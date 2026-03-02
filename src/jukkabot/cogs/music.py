from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

from jukkabot.models import Track
from jukkabot.music_service import MusicService
from jukkabot.queue_manager import QueueManager
from jukkabot.tracker_service import (
    TRACKER_GAME_NAMES,
    TrackerApiError,
    TrackerService,
)

logger = logging.getLogger(__name__)
TRACKER_STATS_ENABLED = False

FILTER_PRESETS: dict[str, tuple[str, str | None]] = {
    "off": ("Off", None),
    "hiphop": ("Hip Hop", "bass=g=8:f=95:w=0.8,treble=g=2:f=3500:w=0.7"),
    "edm": ("EDM", "bass=g=7:f=95:w=0.7,treble=g=4:f=4500:w=0.6"),
    "dance": (
        "Dance",
        "bass=g=6:f=120:w=0.8,equalizer=f=1000:t=q:w=1:g=2,treble=g=3:f=5000:w=0.6",
    ),
    "vocal": (
        "Vocal",
        "highpass=f=120,lowpass=f=12000,equalizer=f=2500:t=q:w=1.2:g=4,"
        "equalizer=f=5500:t=q:w=1.4:g=3",
    ),
    "pop": ("Pop", "bass=g=4:f=120:w=0.7,treble=g=3:f=4500:w=0.6"),
    "rock": ("Rock", "bass=g=5:f=120:w=0.7,treble=g=4:f=5000:w=0.7"),
    "trebleboost": ("Treble Boost", "treble=g=6:f=5000:w=0.8"),
}
SHOW_SHUFFLE_BUTTON = False


class NowPlayingControls(discord.ui.View):
    def __init__(self, cog: "MusicCog", guild_id: int) -> None:
        super().__init__(timeout=3600)
        self.cog = cog
        self.guild_id = guild_id
        if not SHOW_SHUFFLE_BUTTON:
            self.remove_item(self.shuffle_button)
        self._sync_repeat_button_style()
        self._sync_pause_button_style()

    def _sync_repeat_button_style(self) -> None:
        state = self.cog.queue_manager.get(self.guild_id)
        self.repeat_button.style = (
            discord.ButtonStyle.success
            if state.repeat_current
            else discord.ButtonStyle.secondary
        )

    def _sync_pause_button_style(self) -> None:
        bot = getattr(self.cog, "bot", None)
        get_guild = getattr(bot, "get_guild", None)
        guild = get_guild(self.guild_id) if callable(get_guild) else None
        voice = getattr(guild, "voice_client", None) if guild is not None else None
        self.pause_button.style = (
            discord.ButtonStyle.success
            if voice is not None and voice.is_paused()
            else discord.ButtonStyle.secondary
        )

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

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def previous_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        logger.info(
            "%s pressed previous in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        if await self.cog._play_previous(guild):
            self.cog._touch_activity(guild.id)
            return

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        logger.info(
            "%s pressed next/skip in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        if await self.cog._skip_track(guild):
            self.cog._touch_activity(guild.id)
            return

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        logger.info(
            "%s pressed pause/resume in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        await self.cog._toggle_pause(guild)
        self._sync_pause_button_style()
        await self.cog._refresh_now_playing(
            guild, interaction.channel_id, edit_existing=True
        )
        self.cog._touch_activity(guild.id)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
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

        logger.info(
            "%s pressed shuffle in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        self.cog.queue_manager.shuffle(guild.id)
        current_channel = self.cog._resolve_announce_channel(guild, interaction.channel_id)
        if state.current_track is not None:
            await self.cog._send_now_playing(
                guild, current_channel, state.current_track, edit_existing=True
            )
        self.cog._touch_activity(guild.id)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def repeat_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return

        state = self.cog.queue_manager.get(guild.id)
        state.repeat_current = not state.repeat_current
        logger.info(
            "%s set repeat=%s in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            state.repeat_current,
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        self._sync_repeat_button_style()
        await self.cog._refresh_now_playing(
            guild, interaction.channel_id, edit_existing=True
        )
        self.cog._touch_activity(guild.id)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.secondary, row=0)
    async def stop_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        guild = await self._validate(interaction)
        if guild is None:
            return
        logger.info(
            "%s pressed stop in channel '%s' at guild '%s'.",
            self.cog._user_name(interaction.user),
            self.cog._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self.cog._guild_name(guild),
        )
        await self.cog._leave_voice(guild, interaction.channel_id)


class MusicCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        queue_manager: QueueManager,
        music_service: MusicService,
        tracker_service: TrackerService | None,
        admin_user_ids: set[int],
    ) -> None:
        self.bot = bot
        self.queue_manager = queue_manager
        self.music_service = music_service
        self.tracker_service = tracker_service
        self.admin_user_ids = admin_user_ids
        self.last_active_by_guild: dict[int, datetime] = {}
        self._autocomplete_request_seq: dict[tuple[int, int], int] = {}
        self._playback_started_at: dict[int, float] = {}
        self._paused_started_at: dict[int, float] = {}
        self._paused_accumulated_seconds: dict[int, float] = {}
        self._pending_seek_seconds: dict[int, float] = {}
        self.idle_disconnect.start()

    def cog_unload(self) -> None:
        self.idle_disconnect.cancel()

    def _touch_activity(self, guild_id: int) -> None:
        self.last_active_by_guild[guild_id] = datetime.now(UTC)

    @staticmethod
    def _guild_name(guild: object | None) -> str:
        if guild is None:
            return "Unknown guild"
        name = getattr(guild, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return "Unknown guild"

    @staticmethod
    def _channel_name(channel: object | None, *, fallback: str = "Unknown channel") -> str:
        if channel is None:
            return fallback
        name = getattr(channel, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback

    @staticmethod
    def _user_name(user: object | None) -> str:
        if user is None:
            return "Unknown user"
        display_name = getattr(user, "display_name", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
        username = getattr(user, "name", None)
        if isinstance(username, str) and username.strip():
            return username.strip()
        return "Unknown user"

    @staticmethod
    def _track_name(track: Track | None) -> str:
        if track is None or not track.title.strip():
            return "Unknown track"
        return track.title

    async def _ack_silent(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()

    async def _finalize_silent(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _send_followup_and_finalize(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await interaction.followup.send(content, ephemeral=ephemeral)
        await self._finalize_silent(interaction)

    def _set_filter_preset(self, guild_id: int, preset_key: str) -> bool:
        preset = FILTER_PRESETS.get(preset_key)
        if preset is None:
            return False
        state = self.queue_manager.get(guild_id)
        state.active_filter_preset = preset_key
        state.active_audio_filter = preset[1]
        return True

    def _set_playback_clock(self, guild_id: int, initial_elapsed_seconds: float = 0.0) -> None:
        elapsed = max(0.0, initial_elapsed_seconds)
        self._playback_started_at[guild_id] = time.monotonic() - elapsed
        self._paused_started_at.pop(guild_id, None)
        self._paused_accumulated_seconds[guild_id] = 0.0

    def _clear_playback_clock(self, guild_id: int) -> None:
        self._playback_started_at.pop(guild_id, None)
        self._paused_started_at.pop(guild_id, None)
        self._paused_accumulated_seconds.pop(guild_id, None)
        self._pending_seek_seconds.pop(guild_id, None)

    def _mark_paused(self, guild_id: int) -> None:
        if guild_id in self._playback_started_at and guild_id not in self._paused_started_at:
            self._paused_started_at[guild_id] = time.monotonic()

    def _mark_resumed(self, guild_id: int) -> None:
        paused_started = self._paused_started_at.pop(guild_id, None)
        if paused_started is None:
            return
        self._paused_accumulated_seconds[guild_id] = (
            self._paused_accumulated_seconds.get(guild_id, 0.0)
            + (time.monotonic() - paused_started)
        )

    def _current_elapsed_seconds(self, guild_id: int) -> float:
        started_at = self._playback_started_at.get(guild_id)
        if started_at is None:
            return 0.0
        paused_total = self._paused_accumulated_seconds.get(guild_id, 0.0)
        paused_started = self._paused_started_at.get(guild_id)
        if paused_started is not None:
            paused_total += time.monotonic() - paused_started
        return max(0.0, time.monotonic() - started_at - paused_total)

    async def _restart_current_with_active_filter(self, guild: discord.Guild) -> None:
        state = self.queue_manager.get(guild.id)
        voice = guild.voice_client
        if state.current_track is None or voice is None:
            return

        elapsed = self._current_elapsed_seconds(guild.id)
        if elapsed > 0.5:
            self._pending_seek_seconds[guild.id] = elapsed
        else:
            self._pending_seek_seconds.pop(guild.id, None)
        logger.info(
            "Applying filter '%s' by restarting '%s' in channel '%s' at guild '%s'.",
            state.active_filter_preset,
            self._track_name(state.current_track),
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
        state.queue.appendleft(state.current_track)
        state.skip_requested = True
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            return

        self.queue_manager.finish_current(guild.id, add_to_history=False)
        state.skip_requested = False
        await self._play_next(guild)

    def _clear_guild_voice_state(self, guild: discord.Guild) -> None:
        guild_id = guild.id
        state = self.queue_manager.get(guild_id)
        queued_count = len(state.queue)
        history_count = len(state.history)
        self.queue_manager.clear(guild_id)
        self.last_active_by_guild.pop(guild_id, None)
        self._clear_playback_clock(guild_id)
        logger.info(
            "Cleared media state in guild '%s' (queue items: %s, history items: %s).",
            self._guild_name(guild),
            queued_count,
            history_count,
        )

    async def _delete_now_playing_message(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> None:
        state = self.queue_manager.get(guild.id)
        if state.now_playing_message_id is None:
            return

        channel: discord.TextChannel | discord.Thread | None = None
        if state.now_playing_channel_id is not None:
            stored_channel = guild.get_channel(state.now_playing_channel_id)
            if stored_channel is not None and callable(
                getattr(stored_channel, "get_partial_message", None)
            ):
                channel = stored_channel
        if channel is None:
            channel = self._resolve_announce_channel(guild, fallback_channel_id)
        if channel is None:
            state.now_playing_message_id = None
            state.now_playing_channel_id = None
            return

        old_message = channel.get_partial_message(state.now_playing_message_id)
        try:
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        finally:
            state.now_playing_message_id = None
            state.now_playing_channel_id = None

    async def _cleanup_after_disconnect(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> None:
        logger.info(
            "Running disconnect cleanup for guild '%s'.",
            self._guild_name(guild),
        )
        await self._delete_now_playing_message(guild, fallback_channel_id)
        self._clear_guild_voice_state(guild)

    async def _leave_voice(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> bool:
        voice = guild.voice_client
        if voice is None:
            return False
        logger.info(
            "Leaving channel '%s' at guild '%s'.",
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
        await voice.disconnect(force=True)
        await self._cleanup_after_disconnect(guild, fallback_channel_id)
        return True

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

        logger.info(
            "Denied voice command from %s in guild '%s': bot in '%s', user in '%s'.",
            self._user_name(interaction.user),
            self._guild_name(guild),
            self._channel_name(bot_voice.channel, fallback="Unknown voice channel"),
            self._channel_name(user_channel, fallback="No voice channel"),
        )
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

        controls = NowPlayingControls(self, guild.id)
        previous_channel: discord.TextChannel | discord.Thread | None = None
        if state.now_playing_channel_id is not None:
            stored_channel = guild.get_channel(state.now_playing_channel_id)
            if stored_channel is not None and callable(
                getattr(stored_channel, "get_partial_message", None)
            ):
                previous_channel = stored_channel
        if previous_channel is None:
            previous_channel = channel

        if (
            edit_existing
            and state.now_playing_message_id
            and previous_channel is not None
            and previous_channel.id == channel.id
        ):
            old_message = previous_channel.get_partial_message(state.now_playing_message_id)
            try:
                await old_message.edit(embed=embed, view=controls)
                state.now_playing_channel_id = channel.id
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if state.now_playing_message_id is not None:
            if previous_channel is None:
                state.now_playing_message_id = None
                state.now_playing_channel_id = None
            else:
                old_message = previous_channel.get_partial_message(state.now_playing_message_id)
                try:
                    await old_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                finally:
                    state.now_playing_message_id = None
                    state.now_playing_channel_id = None

        message = await channel.send(embed=embed, view=controls)
        state.now_playing_message_id = message.id
        state.now_playing_channel_id = channel.id

    async def _refresh_now_playing(
        self,
        guild: discord.Guild,
        fallback_channel_id: int | None = None,
        edit_existing: bool = False,
    ) -> None:
        state = self.queue_manager.get(guild.id)
        if state.current_track is None:
            await self._delete_now_playing_message(guild, fallback_channel_id)
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

        logger.info(
            "Skipping '%s' in channel '%s' at guild '%s'.",
            self._track_name(state.current_track),
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
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

        # Spotify-like behavior: restart current track if we've progressed enough.
        if state.current_track is not None and self._current_elapsed_seconds(guild.id) > 5.0:
            logger.info(
                "Restarting current track '%s' in channel '%s' at guild '%s'.",
                self._track_name(state.current_track),
                self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
                self._guild_name(guild),
            )
            state.queue.appendleft(state.current_track)
            state.skip_requested = True
            self._pending_seek_seconds.pop(guild.id, None)
            if voice.is_playing() or voice.is_paused():
                voice.stop()
                return True
            self.queue_manager.finish_current(guild.id, add_to_history=False)
            state.skip_requested = False
            await self._play_next(guild)
            return True

        if not state.history:
            return False

        previous_title = self._track_name(state.history[-1])
        logger.info(
            "Moving to previous track '%s' in channel '%s' at guild '%s'.",
            previous_title,
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
        previous = state.history.pop()
        if state.current_track is not None:
            state.queue.appendleft(state.current_track)
        state.queue.appendleft(previous)
        state.skip_requested = True
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            return True
        state.skip_requested = False
        await self._play_next(guild)
        return True

    async def _toggle_pause(self, guild: discord.Guild) -> str:
        voice = guild.voice_client
        if voice is None:
            return "Bot is not connected to voice."
        if voice.is_playing():
            voice.pause()
            self._mark_paused(guild.id)
            logger.info(
                "Paused playback in channel '%s' at guild '%s'.",
                self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
                self._guild_name(guild),
            )
            return "Paused."
        if voice.is_paused():
            voice.resume()
            self._mark_resumed(guild.id)
            logger.info(
                "Resumed playback in channel '%s' at guild '%s'.",
                self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
                self._guild_name(guild),
            )
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
        state = self.queue_manager.get(guild.id)
        if state.active_audio_filter:
            ffmpeg_options = f'{ffmpeg_options} -af "{state.active_audio_filter}"'
        seek_seconds = max(0.0, self._pending_seek_seconds.pop(guild.id, 0.0))
        if seek_seconds > 0.0:
            ffmpeg_before = f"-ss {seek_seconds:.3f} {ffmpeg_before}"
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
                logger.exception(
                    "Failed to process playback callback in channel '%s' at guild '%s'.",
                    self._channel_name(
                        getattr(guild.voice_client, "channel", None),
                        fallback="Unknown voice channel",
                    ),
                    self._guild_name(guild),
                )

        voice.play(source, after=after_playback)
        self._set_playback_clock(guild.id, initial_elapsed_seconds=seek_seconds)
        self._touch_activity(guild.id)
        logger.info(
            "Started playback of '%s' in channel '%s' at guild '%s'.",
            self._track_name(track),
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
        await self._send_now_playing(guild, announce_channel, track)

    async def _after_track_finished(
        self, guild_id: int, error: Exception | None
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        state = self.queue_manager.get(guild_id)
        finished_track = state.current_track
        channel_name = self._channel_name(
            getattr(guild.voice_client, "channel", None), fallback="Unknown voice channel"
        )
        guild_name = self._guild_name(guild)
        if error:
            logger.error(
                "Playback error for '%s' in channel '%s' at guild '%s': %s",
                self._track_name(finished_track),
                channel_name,
                guild_name,
                error,
            )

        should_repeat = (
            state.repeat_current
            and not state.skip_requested
            and state.current_track is not None
        )
        if should_repeat and state.current_track is not None:
            state.queue.appendleft(state.current_track)
        was_skip = state.skip_requested
        self.queue_manager.finish_current(
            guild_id, add_to_history=not state.skip_requested and not should_repeat
        )
        state.skip_requested = False
        self._playback_started_at.pop(guild_id, None)
        self._paused_started_at.pop(guild_id, None)
        self._paused_accumulated_seconds.pop(guild_id, None)
        if finished_track is not None:
            if should_repeat:
                reason = "repeat"
            elif error:
                reason = "error"
            elif was_skip:
                reason = "skip"
            else:
                reason = "finished"
            logger.info(
                "Playback ended (%s) for '%s' in channel '%s' at guild '%s'.",
                reason,
                self._track_name(finished_track),
                channel_name,
                guild_name,
            )

        await self._play_next(guild)

    async def _play_next(
        self, guild: discord.Guild, fallback_channel_id: int | None = None
    ) -> None:
        voice = guild.voice_client
        if voice is None:
            return
        if not voice.is_connected():
            logger.info(
                "Voice client was disconnected in guild '%s'; cleaning media state.",
                self._guild_name(guild),
            )
            await self._cleanup_after_disconnect(guild, fallback_channel_id)
            return
        if voice.is_playing() or voice.is_paused():
            return

        channel = self._resolve_announce_channel(guild, fallback_channel_id)
        while True:
            if not voice.is_connected():
                logger.info(
                    "Voice client disconnected during playback loop in guild '%s'.",
                    self._guild_name(guild),
                )
                await self._cleanup_after_disconnect(guild, fallback_channel_id)
                return

            track = self.queue_manager.pop_next(guild.id)
            if track is None:
                await self._delete_now_playing_message(guild, fallback_channel_id)
                self._touch_activity(guild.id)
                logger.info(
                    "Playback ended at channel '%s' at server '%s' (queue is empty).",
                    self._channel_name(
                        getattr(voice, "channel", None), fallback="Unknown voice channel"
                    ),
                    self._guild_name(guild),
                )
                return

            try:
                await self._play_track(guild, voice, track, channel)
                return
            except Exception as exc:
                logger.exception(
                    "Failed to start '%s' in channel '%s' at guild '%s'.",
                    self._track_name(track),
                    self._channel_name(
                        getattr(voice, "channel", None), fallback="Unknown voice channel"
                    ),
                    self._guild_name(guild),
                )
                self.queue_manager.finish_current(guild.id, add_to_history=False)
                if channel is not None:
                    await channel.send(f"Failed to play **{track.title}**: {exc}")
                if not voice.is_connected():
                    await self._cleanup_after_disconnect(guild, fallback_channel_id)
                    return

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
                logger.info(
                    "Disconnected from channel '%s' at guild '%s' after idle timeout.",
                    self._channel_name(voice.channel, fallback="Unknown voice channel"),
                    self._guild_name(guild),
                )
                await voice.disconnect(force=True)
                await self._cleanup_after_disconnect(guild)

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
            logger.info(
                "Bot disconnected from channel '%s' at guild '%s'. Clearing media state.",
                self._channel_name(before.channel, fallback="Unknown voice channel"),
                self._guild_name(member.guild),
            )
            await self._cleanup_after_disconnect(member.guild)

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
        requester_name = self._user_name(interaction.user)

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
            await self._ack_silent(interaction)
            logger.info(
                "%s requested /join while bot was already in channel '%s' at guild '%s'.",
                requester_name,
                self._channel_name(user_channel, fallback="Unknown voice channel"),
                self._guild_name(guild),
            )
            await self._finalize_silent(interaction)
            return

        if bot_voice and bot_voice.channel.id != user_channel.id:
            humans_in_bot_channel = [m for m in bot_voice.channel.members if not m.bot]
            if humans_in_bot_channel:
                logger.info(
                    "%s tried moving bot to '%s' at guild '%s', but bot is active in '%s'.",
                    requester_name,
                    self._channel_name(user_channel, fallback="Unknown voice channel"),
                    self._guild_name(guild),
                    self._channel_name(bot_voice.channel, fallback="Unknown voice channel"),
                )
                await interaction.response.send_message(
                    "Bot is active in another channel and cannot switch yet.",
                    ephemeral=True,
                )
                return
            await self._ack_silent(interaction)
            await bot_voice.move_to(user_channel)
            self.queue_manager.set_voice_channel(guild.id, user_channel.id)
            self._touch_activity(guild.id)
            logger.info(
                "%s moved bot to channel '%s' at guild '%s'.",
                requester_name,
                self._channel_name(user_channel, fallback="Unknown voice channel"),
                self._guild_name(guild),
            )
            await self._finalize_silent(interaction)
            return

        try:
            await self._ack_silent(interaction)
            await user_channel.connect()
        except discord.DiscordException as exc:
            logger.warning(
                "Failed to join channel '%s' at guild '%s': %s",
                self._channel_name(user_channel, fallback="Unknown voice channel"),
                self._guild_name(guild),
                exc,
            )
            await self._send_followup_and_finalize(
                interaction,
                f"Could not join voice channel: {exc}",
                ephemeral=True,
            )
            return

        self.queue_manager.set_voice_channel(guild.id, user_channel.id)
        self._touch_activity(guild.id)
        logger.info(
            "Joined channel '%s' at guild '%s' via /join by %s.",
            self._channel_name(user_channel, fallback="Unknown voice channel"),
            self._guild_name(guild),
            requester_name,
        )
        await self._finalize_silent(interaction)

    async def play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        request_key = (interaction.guild_id or 0, interaction.user.id)
        request_id = self._autocomplete_request_seq.get(request_key, 0) + 1
        self._autocomplete_request_seq[request_key] = request_id

        query = current.strip()
        if len(query) < 2:
            return []

        # Trailing debounce: only search if no newer query from this user arrived
        # during the 500ms cooldown.
        await asyncio.sleep(0.5)
        if self._autocomplete_request_seq.get(request_key) != request_id:
            return []

        try:
            tracks = await asyncio.to_thread(self.music_service.search, query)
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

    async def filter_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        del interaction
        query = current.strip().casefold()
        choices: list[app_commands.Choice[str]] = []
        for key, (label, _) in FILTER_PRESETS.items():
            if query and query not in key and query not in label.casefold():
                continue
            choices.append(app_commands.Choice(name=label, value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="filter", description="Apply an audio filter preset.")
    @app_commands.autocomplete(preset=filter_autocomplete)
    async def filter(self, interaction: discord.Interaction, preset: str) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        preset_key = preset.strip().casefold()
        if preset_key not in FILTER_PRESETS:
            await interaction.response.send_message(
                "Invalid filter preset.", ephemeral=True
            )
            return

        await self._ack_silent(interaction)
        self._set_filter_preset(guild.id, preset_key)
        self._touch_activity(guild.id)
        preset_label = FILTER_PRESETS[preset_key][0]
        logger.info(
            "%s set filter to '%s' in channel '%s' at guild '%s'.",
            self._user_name(interaction.user),
            preset_label,
            self._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self._guild_name(guild),
        )
        await self._restart_current_with_active_filter(guild)
        await self._refresh_now_playing(guild, interaction.channel_id, edit_existing=True)
        await self._finalize_silent(interaction)

    @app_commands.command(name="bass", description="Apply bass boost filter.")
    async def bass(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 20],
    ) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        await self._ack_silent(interaction)
        state = self.queue_manager.get(guild.id)
        state.active_filter_preset = f"bass({level})"
        if level <= 0:
            state.active_audio_filter = None
        else:
            state.active_audio_filter = f"bass=g={int(level)}:f=110:w=0.8"
        self._touch_activity(guild.id)
        logger.info(
            "%s set bass level to %s in channel '%s' at guild '%s'.",
            self._user_name(interaction.user),
            int(level),
            self._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self._guild_name(guild),
        )
        await self._restart_current_with_active_filter(guild)
        await self._refresh_now_playing(guild, interaction.channel_id, edit_existing=True)
        await self._finalize_silent(interaction)

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

        await self._ack_silent(interaction)
        if interaction.channel_id is not None:
            self.queue_manager.set_text_channel(guild.id, interaction.channel_id)

        bot_voice = guild.voice_client
        if bot_voice is None:
            try:
                bot_voice = await user_channel.connect()
                self.queue_manager.set_voice_channel(guild.id, user_channel.id)
                logger.info(
                    "Auto-joined channel '%s' at guild '%s' for /play by %s.",
                    self._channel_name(user_channel, fallback="Unknown voice channel"),
                    self._guild_name(guild),
                    self._user_name(interaction.user),
                )
            except discord.DiscordException as exc:
                logger.warning(
                    "Auto-join failed for /play in channel '%s' at guild '%s': %s",
                    self._channel_name(user_channel, fallback="Unknown voice channel"),
                    self._guild_name(guild),
                    exc,
                )
                await self._send_followup_and_finalize(
                    interaction,
                    f"Could not join voice channel: {exc}",
                    ephemeral=True,
                )
                return

        try:
            tracks = await asyncio.to_thread(self.music_service.search, query)
        except Exception as exc:
            await self._send_followup_and_finalize(
                interaction,
                f"Search failed: {exc}",
                ephemeral=True,
            )
            return

        if not tracks:
            await self._send_followup_and_finalize(
                interaction,
                "No results found.",
                ephemeral=True,
            )
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
        logger.info(
            "%s queued '%s' in channel '%s' at guild '%s' (queue size now %s).",
            requester_name,
            self._track_name(track),
            self._channel_name(user_channel, fallback="Unknown voice channel"),
            self._guild_name(guild),
            len(state.queue),
        )
        self._touch_activity(guild.id)
        if state.current_track is None:
            await self._start_if_needed(guild, interaction.channel_id)
        else:
            await self._refresh_now_playing(
                guild, interaction.channel_id, edit_existing=True
            )
        await self._finalize_silent(interaction)

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
            await interaction.response.send_message(
                "Nothing is currently playing.",
                ephemeral=True,
            )
            return

        await self._ack_silent(interaction)
        logger.info(
            "%s requested /skip for '%s' in channel '%s' at guild '%s'.",
            self._user_name(interaction.user),
            self._track_name(state.current_track),
            self._channel_name(getattr(voice, "channel", None), fallback="Unknown voice channel"),
            self._guild_name(guild),
        )
        skipped = await self._skip_track(guild)
        self._touch_activity(guild.id)
        if not skipped:
            await self._send_followup_and_finalize(
                interaction,
                "Nothing is currently playing.",
                ephemeral=True,
            )
            return
        await self._finalize_silent(interaction)

    @app_commands.command(name="pause", description="Pause or resume playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        await self._ack_silent(interaction)
        logger.info(
            "%s requested /pause in channel '%s' at guild '%s'.",
            self._user_name(interaction.user),
            self._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self._guild_name(guild),
        )
        await self._toggle_pause(guild)
        await self._refresh_now_playing(
            guild, interaction.channel_id, edit_existing=True
        )
        self._touch_activity(guild.id)
        await self._finalize_silent(interaction)

    @app_commands.command(name="clear", description="Clear queue and remove now-playing.")
    async def clear(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        await self._ack_silent(interaction)
        voice = guild.voice_client
        state = self.queue_manager.get(guild.id)
        if voice is not None and (voice.is_playing() or voice.is_paused()):
            state.skip_requested = True
            voice.stop()

        await self._delete_now_playing_message(guild, interaction.channel_id)
        current_track_name = self._track_name(state.current_track)
        self.queue_manager.clear(guild.id)
        self._touch_activity(guild.id)
        logger.info(
            "%s cleared playback state in guild '%s' (previous track: '%s').",
            self._user_name(interaction.user),
            self._guild_name(guild),
            current_track_name,
        )
        await self._finalize_silent(interaction)

    @app_commands.command(name="leave", description="Disconnect the bot from voice.")
    async def leave(self, interaction: discord.Interaction) -> None:
        is_allowed, _ = await self._validate_channel_access(interaction)
        if not is_allowed:
            return

        guild = interaction.guild
        if guild is None:
            return

        await self._ack_silent(interaction)
        logger.info(
            "%s requested /leave in channel '%s' at guild '%s'.",
            self._user_name(interaction.user),
            self._channel_name(
                getattr(guild.voice_client, "channel", None),
                fallback="Unknown voice channel",
            ),
            self._guild_name(guild),
        )
        if not await self._leave_voice(guild, interaction.channel_id):
            await self._send_followup_and_finalize(
                interaction,
                "I am not connected to a voice channel.",
                ephemeral=True,
            )
            return
        await self._finalize_silent(interaction)

    async def stats_game_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        del interaction
        query = current.strip().casefold()
        if query:
            matches = [name for name in TRACKER_GAME_NAMES if query in name.casefold()]
        else:
            matches = list(TRACKER_GAME_NAMES)
        return [app_commands.Choice(name=name, value=name) for name in matches[:25]]

    @app_commands.command(
        name="stats",
        description="Fetch player stats from Tracker Network.",
    )
    @app_commands.autocomplete(game=stats_game_autocomplete)
    async def stats(
        self,
        interaction: discord.Interaction,
        game: str,
        account_name: str,
    ) -> None:
        if not TRACKER_STATS_ENABLED:
            await interaction.response.send_message(
                "Stats feature is temporarily disabled until Tracker app approval.",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        if not account_name.strip():
            await interaction.response.send_message(
                "Account name is required.", ephemeral=True
            )
            return

        selected_game = next(
            (
                name
                for name in TRACKER_GAME_NAMES
                if name.casefold() == game.strip().casefold()
            ),
            None,
        )
        if selected_game is None:
            await interaction.response.send_message(
                "Game must be selected from the supported Tracker list.",
                ephemeral=True,
            )
            return
        if self.tracker_service is None:
            await interaction.response.send_message(
                "Tracker API key is not configured.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            profile = await asyncio.to_thread(
                self.tracker_service.fetch_profile_stats,
                selected_game,
                account_name,
            )
        except TrackerApiError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            logger.exception("Tracker stats lookup failed: %s", exc)
            await interaction.followup.send(
                "Failed to fetch stats from Tracker API.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{profile.game_name} Stats",
            description=f"Player: **{profile.platform_user}**  \nPlatform: **{profile.platform}**",
            color=discord.Color(0x1DB954),
        )
        count = 0
        for stat_name, stat_value in profile.stats.items():
            embed.add_field(
                name=str(stat_name)[:256],
                value=str(stat_value)[:1024],
                inline=True,
            )
            count += 1
            if count >= 25:
                break
        await interaction.followup.send(embed=embed)

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

        await self._ack_silent(interaction)
        state = self.queue_manager.get(guild.id)
        state.banned_user_ids.add(user.id)
        await self._finalize_silent(interaction)

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

        await self._ack_silent(interaction)
        state = self.queue_manager.get(guild.id)
        state.banned_user_ids.discard(user.id)
        await self._finalize_silent(interaction)


async def setup(bot: commands.Bot) -> None:
    queue_manager = bot.queue_manager  # type: ignore[attr-defined]
    music_service = bot.music_service  # type: ignore[attr-defined]
    tracker_service = bot.tracker_service  # type: ignore[attr-defined]
    admin_user_ids = bot.admin_user_ids  # type: ignore[attr-defined]
    await bot.add_cog(
        MusicCog(bot, queue_manager, music_service, tracker_service, admin_user_ids)
    )
