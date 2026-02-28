from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

from jukkabot.models import Track
from jukkabot.music_service import MusicService
from jukkabot.queue_manager import QueueManager

logger = logging.getLogger(__name__)


class NowPlayingControls(discord.ui.View):
    def __init__(self, cog: "MusicCog", guild_id: int) -> None:
        super().__init__(timeout=3600)
        self.cog = cog
        self.guild_id = guild_id

    async def _validate(self, interaction: discord.Interaction) -> discord.Guild | None:
        guild = interaction.guild or self.cog.bot.get_guild(self.guild_id)
        if guild is None:
            return None

        voice = guild.voice_client
        if voice is None or voice.channel is None:
            return None

        user_channel = self.cog._current_voice_channel(interaction)
        if user_channel is None or user_channel.id != voice.channel.id:
            return None

        return guild

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        if await self.cog._play_previous(guild):
            self.cog._touch_activity(guild.id)
            return

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        if await self.cog._skip_track(guild):
            self.cog._touch_activity(guild.id)
            return

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        await self.cog._toggle_pause(guild)
        await self.cog._refresh_now_playing(
            guild, interaction.channel_id, edit_existing=True
        )
        self.cog._touch_activity(guild.id)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        state = self.cog.queue_manager.get(guild.id)
        if not state.queue:
            return

        self.cog.queue_manager.shuffle(guild.id)
        current_channel = self.cog._resolve_announce_channel(guild, interaction.channel_id)
        if state.current_track is not None:
            await self.cog._send_now_playing(
                guild, current_channel, state.current_track, edit_existing=True
            )
        self.cog._touch_activity(guild.id)


class MusicCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        queue_manager: QueueManager,
        music_service: MusicService,
        admin_user_ids: set[int],
    ) -> None:
        self.bot = bot
        self.queue_manager = queue_manager
        self.music_service = music_service
        self.admin_user_ids = admin_user_ids
        self.last_active_by_guild: dict[int, datetime] = {}
        self.autocomplete_cache_ttl = timedelta(seconds=8)
        self._autocomplete_cache: dict[tuple[int, str], tuple[datetime, list[Track]]] = {}
        self.idle_disconnect.start()

    def cog_unload(self) -> None:
        self.idle_disconnect.cancel()

    def _touch_activity(self, guild_id: int) -> None:
        self.last_active_by_guild[guild_id] = datetime.now(UTC)

    def _clear_guild_voice_state(self, guild_id: int) -> None:
        self.queue_manager.clear(guild_id)
        self.last_active_by_guild.pop(guild_id, None)

    def _cache_autocomplete_results(
        self, cache_key: tuple[int, str], tracks: list[Track]
    ) -> None:
        now = datetime.now(UTC)
        self._autocomplete_cache[cache_key] = (now, tracks)
        if len(self._autocomplete_cache) <= 300:
            return

        stale_before = now - self.autocomplete_cache_ttl
        self._autocomplete_cache = {
            key: value
            for key, value in self._autocomplete_cache.items()
            if value[0] >= stale_before
        }
        if len(self._autocomplete_cache) <= 300:
            return

        newest = sorted(
            self._autocomplete_cache.items(),
            key=lambda item: item[1][0],
            reverse=True,
        )[:300]
        self._autocomplete_cache = dict(newest)

    def _youtube_thumbnail(self, track: Track) -> str | None:
        if track.thumbnail_url:
            return track.thumbnail_url

        parsed = urlparse(track.url)
        video_id: str | None = None
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            video_id = qs.get("v", [None])[0]
            if video_id is None and parsed.path.startswith("/shorts/"):
                video_id = parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
        elif "youtu.be" in parsed.netloc:
            video_id = parsed.path.lstrip("/").split("/", 1)[0] or None

        if not video_id:
            return None
        return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

    def _current_voice_channel(
        self, interaction: discord.Interaction
    ) -> discord.VoiceChannel | None:
        user = interaction.user
        if isinstance(user, discord.Member) and user.voice:
            if isinstance(user.voice.channel, discord.VoiceChannel):
                return user.voice.channel
        return None

    def _resolve_announce_channel(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> discord.TextChannel | discord.Thread | None:
        if fallback_channel_id is not None:
            channel = guild.get_channel(fallback_channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel

        state = self.queue_manager.get(guild.id)
        if state.text_channel_id is not None:
            channel = guild.get_channel(state.text_channel_id)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel
        return None

    async def _validate_channel_access(
        self, interaction: discord.Interaction
    ) -> tuple[bool, discord.VoiceChannel | None]:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return False, None

        user_channel = self._current_voice_channel(interaction)
        bot_voice = guild.voice_client
        if bot_voice is None:
            return True, user_channel

        if user_channel is not None and bot_voice.channel.id == user_channel.id:
            return True, user_channel

        await interaction.response.send_message(
            "Join the same voice channel as the bot to use this command.",
            ephemeral=True,
        )
        return False, user_channel

    async def _send_now_playing(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | discord.Thread | None,
        track: Track,
        edit_existing: bool = False,
    ) -> None:
        if channel is None:
            return

        state = self.queue_manager.get(guild.id)
        voice = guild.voice_client
        status = "Paused" if voice is not None and voice.is_paused() else "Playing"
        embed = discord.Embed(
            title=f"JukkaBot - {status}",
            color=discord.Color(0x1DB954),
        )
        embed.add_field(name="Name", value=f"[{track.title}]({track.url})", inline=False)
        embed.add_field(name="Author", value=track.author, inline=True)
        embed.add_field(name="Length", value=track.duration_label, inline=True)
        requested_by = track.requested_by_display_name or "Unknown"
        embed.add_field(name="Queued By", value=requested_by, inline=True)
        if state.queue:
            next_count = min(3, len(state.queue))
            upcoming = "\n".join(
                f"{index + 1}. {state.queue[index].title}" for index in range(next_count)
            )
            if len(state.queue) > next_count:
                upcoming = f"{upcoming}\n+{len(state.queue) - next_count} more"
            embed.add_field(name="Coming Next", value=upcoming, inline=False)
        image_url = self._youtube_thumbnail(track)
        if image_url:
            embed.set_image(url=image_url)

        if edit_existing and state.now_playing_message_id:
            old_message = channel.get_partial_message(state.now_playing_message_id)
            try:
                await old_message.edit(embed=embed)
                return
            except (discord.NotFound, discord.Forbidden):
                pass

        if state.now_playing_message_id:
            old_message = channel.get_partial_message(state.now_playing_message_id)
            try:
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        controls = NowPlayingControls(self, guild.id)
        message = await channel.send(embed=embed, view=controls)
        state.now_playing_message_id = message.id

    async def _refresh_now_playing(
        self,
        guild: discord.Guild,
        fallback_channel_id: int | None = None,
        edit_existing: bool = False,
    ) -> None:
        state = self.queue_manager.get(guild.id)
        if state.current_track is None:
            return
        channel = self._resolve_announce_channel(guild, fallback_channel_id)
        await self._send_now_playing(
            guild, channel, state.current_track, edit_existing=edit_existing
        )

    async def _skip_track(self, guild: discord.Guild) -> bool:
        state = self.queue_manager.get(guild.id)
        voice = guild.voice_client
        if voice is None or state.current_track is None:
            return False

        state.skip_requested = True
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            return True

        # Fallback path: voice client can be connected but no active player.
        # Mark current as skipped and advance to the next queued track.
        self.queue_manager.finish_current(guild.id, add_to_history=False)
        state.skip_requested = False
        await self._play_next(guild)
        return True

    async def _play_previous(self, guild: discord.Guild) -> bool:
        state = self.queue_manager.get(guild.id)
        voice = guild.voice_client
        if voice is None:
            return False
        if not state.history:
            return False

        previous = state.history.pop()
        if state.current_track is not None:
            state.queue.appendleft(state.current_track)
        state.queue.appendleft(previous)
        state.skip_requested = True
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            return True
        await self._play_next(guild)
        return True

    async def _toggle_pause(self, guild: discord.Guild) -> str:
        voice = guild.voice_client
        if voice is None:
            return "Bot is not connected to voice."
        if voice.is_playing():
            voice.pause()
            return "Paused."
        if voice.is_paused():
            voice.resume()
            return "Resumed."
        return "Nothing is currently playing."

    async def _play_track(
        self,
        guild: discord.Guild,
        voice: discord.VoiceClient,
        track: Track,
        announce_channel: discord.TextChannel | discord.Thread | None,
    ) -> None:
        stream = await asyncio.to_thread(self.music_service.get_stream_source, track.url)
        ffmpeg_before = (
            "-nostdin -reconnect 1 -reconnect_streamed 1 "
            "-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx "
            "-reconnect_delay_max 5"
        )
        ffmpeg_options = "-vn -loglevel warning"
        if stream.user_agent:
            ffmpeg_before = f'{ffmpeg_before} -user_agent "{stream.user_agent}"'
        source = discord.FFmpegPCMAudio(
            stream.url,
            before_options=ffmpeg_before,
            options=ffmpeg_options,
        )

        def after_playback(error: Exception | None) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._after_track_finished(guild.id, error), self.bot.loop
            )
            try:
                future.result()
            except Exception:
                logger.exception("Failed to process playback callback for guild %s", guild.id)

        voice.play(source, after=after_playback)
        self._touch_activity(guild.id)
        await self._send_now_playing(guild, announce_channel, track)

    async def _after_track_finished(
        self, guild_id: int, error: Exception | None
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        if error:
            logger.error("Playback error in guild %s: %s", guild_id, error)

        state = self.queue_manager.get(guild_id)
        self.queue_manager.finish_current(guild_id, add_to_history=not state.skip_requested)
        state.skip_requested = False

        await self._play_next(guild)

    async def _play_next(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> None:
        voice = guild.voice_client
        if voice is None:
            return
        if not voice.is_connected():
            self._clear_guild_voice_state(guild.id)
            return
        if voice.is_playing() or voice.is_paused():
            return

        channel = self._resolve_announce_channel(guild, fallback_channel_id)
        while True:
            track = self.queue_manager.pop_next(guild.id)
            if track is None:
                self._touch_activity(guild.id)
                return

            try:
                await self._play_track(guild, voice, track, channel)
                return
            except Exception as exc:
                logger.exception("Failed to start track in guild %s", guild.id)
                self.queue_manager.finish_current(guild.id, add_to_history=False)
                if channel is not None:
                    await channel.send(f"Failed to play **{track.title}**: {exc}")

    async def _start_if_needed(
        self, guild: discord.Guild, channel_id: int | None = None
    ) -> None:
        state = self.queue_manager.get(guild.id)
        if state.current_track is not None:
            return
        await self._play_next(guild, channel_id)

    @tasks.loop(seconds=60)
    async def idle_disconnect(self) -> None:
        now = datetime.now(UTC)
        for guild in self.bot.guilds:
            voice = guild.voice_client
            if voice is None or voice.channel is None:
                continue

            state = self.queue_manager.get(guild.id)
            is_idle = (
                not voice.is_playing()
                and not voice.is_paused()
                and state.current_track is None
                and not state.queue
            )
            if not is_idle:
                continue
            last_active = self.last_active_by_guild.get(guild.id, now)
            if now - last_active >= timedelta(minutes=5):
                await voice.disconnect(force=True)
                self._clear_guild_voice_state(guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # If the bot gets disconnected from voice (manual disconnect, kick, channel removal),
        # clear per-guild playback state and queue immediately.
        if self.bot.user is None or member.id != self.bot.user.id:
            return
        if member.guild is None:
            return
        if before.channel is not None and after.channel is None:
            self._clear_guild_voice_state(member.guild.id)

    @idle_disconnect.before_loop
    async def before_idle_disconnect(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="join", description="Join your current voice channel.")
    async def join(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        user_channel = self._current_voice_channel(interaction)
        if user_channel is None:
            await interaction.response.send_message(
                "You must be in a voice channel first.", ephemeral=True
            )
            return

        if interaction.channel_id is not None:
            self.queue_manager.set_text_channel(guild.id, interaction.channel_id)

        bot_voice = guild.voice_client
        if bot_voice and bot_voice.channel.id == user_channel.id:
            await interaction.response.send_message("Already in your voice channel.")
            return

        if bot_voice and bot_voice.channel.id != user_channel.id:
            humans_in_bot_channel = [m for m in bot_voice.channel.members if not m.bot]
            if humans_in_bot_channel:
                await interaction.response.send_message(
                    "Bot is active in another channel and cannot switch yet.",
                    ephemeral=True,
                )
                return
            await bot_voice.move_to(user_channel)
            self.queue_manager.set_voice_channel(guild.id, user_channel.id)
            self._touch_activity(guild.id)
            await interaction.response.send_message(
                f"Moved to {user_channel.mention} (previous channel was empty)."
            )
            return

        try:
            await user_channel.connect()
        except discord.DiscordException as exc:
            await interaction.response.send_message(
                f"Could not join voice channel: {exc}", ephemeral=True
            )
            return

        self.queue_manager.set_voice_channel(guild.id, user_channel.id)
        self._touch_activity(guild.id)
        await interaction.response.send_message(f"Joined {user_channel.mention}.")

    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = current.strip()
        if len(query) < 2:
            return []

        cache_key = (interaction.guild_id or 0, query.casefold())
        now = datetime.now(UTC)
        cache_item = self._autocomplete_cache.get(cache_key)
        try:
            if cache_item is not None and now - cache_item[0] <= self.autocomplete_cache_ttl:
                tracks = cache_item[1]
            else:
                tracks = await asyncio.to_thread(self.music_service.search, query)
                self._cache_autocomplete_results(cache_key, tracks)
        except Exception:
            return []

        choices: list[app_commands.Choice[str]] = []
        seen_values: set[str] = set()
        for track in tracks:
            # Discord choice limits: max 25 choices, and name/value <= 100 chars.
            value = track.title.strip()[:100]
            if not value or value in seen_values:
                continue
            label = f"{track.title[:60]} - {track.author[:30]}".strip()[:100]
            choices.append(app_commands.Choice(name=label, value=value))
            seen_values.add(value)
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="play", description="Search and queue a track.")
    @app_commands.autocomplete(query=play_autocomplete)
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        is_allowed, user_channel = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        if user_channel is None:
            await interaction.response.send_message(
                "You must be in a voice channel to play music.", ephemeral=True
            )
            return

        state = self.queue_manager.get(guild.id)
        if interaction.user.id in state.banned_user_ids:
            await interaction.response.send_message(
                "You are banned from queueing songs in this server.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        if interaction.channel_id is not None:
            self.queue_manager.set_text_channel(guild.id, interaction.channel_id)

        bot_voice = guild.voice_client
        if bot_voice is None:
            try:
                bot_voice = await user_channel.connect()
                self.queue_manager.set_voice_channel(guild.id, user_channel.id)
            except discord.DiscordException as exc:
                await interaction.followup.send(
                    f"Could not join voice channel: {exc}", ephemeral=True
                )
                return

        try:
            tracks = await asyncio.to_thread(self.music_service.search, query)
        except Exception as exc:
            await interaction.followup.send(f"Search failed: {exc}", ephemeral=True)
            return

        if not tracks:
            await interaction.followup.send("No results found.")
            return

        track = tracks[0]
        requester_name = (
            interaction.user.display_name
            if isinstance(interaction.user, discord.Member)
            else interaction.user.name
        )
        self.queue_manager.queue_track(
            guild.id,
            track,
            requested_by_user_id=interaction.user.id,
            requested_by_display_name=requester_name,
        )
        self._touch_activity(guild.id)
        await interaction.followup.send(f"Queued: **{track.title}**")

        if state.current_track is not None:
            announce_channel = self._resolve_announce_channel(guild, interaction.channel_id)
            await self._send_now_playing(guild, announce_channel, state.current_track)

        await self._start_if_needed(guild, interaction.channel_id)

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return
        state = self.queue_manager.get(guild.id)
        if interaction.user.id in state.banned_user_ids:
            await interaction.response.send_message(
                "You are banned from skipping songs in this server.", ephemeral=True
            )
            return

        voice = guild.voice_client
        if voice is None or state.current_track is None:
            await interaction.response.send_message("Nothing is currently playing.")
            return

        skipped = await self._skip_track(guild)
        self._touch_activity(guild.id)
        if skipped:
            await interaction.response.send_message("Skipped.")
            return
        await interaction.response.send_message("Nothing is currently playing.")

    @app_commands.command(name="pause", description="Pause or resume playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        response = await self._toggle_pause(guild)
        await self._refresh_now_playing(guild, interaction.channel_id)
        self._touch_activity(guild.id)
        await interaction.response.send_message(response)

    @app_commands.command(name="leave", description="Disconnect the bot from voice.")
    async def leave(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        voice = guild.voice_client
        if voice is None:
            await interaction.response.send_message(
                "I am not connected to a voice channel.", ephemeral=True
            )
            return

        await voice.disconnect(force=True)
        self._clear_guild_voice_state(guild.id)
        await interaction.response.send_message("Left the voice channel and cleared the queue.")

    @app_commands.command(name="banuser", description="Ban a user from queue and skip.")
    async def banuser(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        is_admin = interaction.user.id in self.admin_user_ids
        has_perm = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )
        if not (is_admin or has_perm):
            await interaction.response.send_message(
                "Only configured owners/admins can use this command.", ephemeral=True
            )
            return

        state = self.queue_manager.get(guild.id)
        state.banned_user_ids.add(user.id)
        await interaction.response.send_message(
            f"{user.mention} is now banned from queueing/skipping."
        )

    @app_commands.command(name="unbanuser", description="Unban a user from queue and skip.")
    async def unbanuser(self, interaction: discord.Interaction, user: discord.Member) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        is_admin = interaction.user.id in self.admin_user_ids
        has_perm = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )
        if not (is_admin or has_perm):
            await interaction.response.send_message(
                "Only configured owners/admins can use this command.", ephemeral=True
            )
            return

        state = self.queue_manager.get(guild.id)
        state.banned_user_ids.discard(user.id)
        await interaction.response.send_message(f"{user.mention} is now unbanned.")


async def setup(bot: commands.Bot) -> None:
    queue_manager = bot.queue_manager  # type: ignore[attr-defined]
    music_service = bot.music_service  # type: ignore[attr-defined]
    admin_user_ids = bot.admin_user_ids  # type: ignore[attr-defined]
    await bot.add_cog(MusicCog(bot, queue_manager, music_service, admin_user_ids))
