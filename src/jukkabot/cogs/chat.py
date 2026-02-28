from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from jukkabot.openai_service import OpenAIService, OpenAIServiceError

logger = logging.getLogger(__name__)
TEXT_ATTACHMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
}
MAX_ATTACHMENT_BYTES = 256 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 3
MAX_ATTACHMENT_TEXT_CHARS = 3500
MAX_MESSAGE_CONTEXT_CHARS = 8000


@dataclass(slots=True)
class ChatSession:
    channel_id: int
    last_human_message_at: datetime
    history: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=30))


class ChatCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        openai_service: OpenAIService | None,
        idle_timeout_seconds: int,
    ) -> None:
        self.bot = bot
        self.openai_service = openai_service
        self.idle_timeout = timedelta(seconds=max(60, idle_timeout_seconds))
        self.sessions: dict[int, ChatSession] = {}
        self._reply_locks: dict[int, asyncio.Lock] = {}
        self.chat_idle_watchdog.start()

    def cog_unload(self) -> None:
        self.chat_idle_watchdog.cancel()

    def _supported_text_channel(
        self, channel: discord.abc.GuildChannel | discord.Thread | None
    ) -> discord.TextChannel | discord.Thread | None:
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _disable_session(
        self, guild_id: int, announce_in_channel: bool = False
    ) -> None:
        session = self.sessions.pop(guild_id, None)
        self._reply_locks.pop(guild_id, None)
        if session is None or not announce_in_channel:
            return

        channel = self.bot.get_channel(session.channel_id)
        target_channel = self._supported_text_channel(channel)
        if target_channel is None:
            return
        try:
            await target_channel.send("Chat mode turned off due to 5 minutes of inactivity.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @app_commands.command(name="chat", description="Manage AI chat for this channel.")
    @app_commands.describe(action="Enable, disable, or inspect chat mode.")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="status", value="status"),
        ]
    )
    async def chat(self, interaction: discord.Interaction, action: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        channel = self._supported_text_channel(interaction.channel)
        if channel is None:
            await interaction.response.send_message(
                "This command can only be used in a text channel or thread.",
                ephemeral=True,
            )
            return

        selected_action = action.strip().casefold()
        now = datetime.now(UTC)
        if selected_action == "status":
            session = self.sessions.get(guild.id)
            if session is None:
                await interaction.response.send_message(
                    "Chat mode is currently off in this server.",
                    ephemeral=True,
                )
                return

            remaining = int(
                max(
                    0.0,
                    (
                        self.idle_timeout
                        - (now - session.last_human_message_at)
                    ).total_seconds(),
                )
            )
            await interaction.response.send_message(
                f"Chat mode is on in <#{session.channel_id}>. "
                f"Auto-off in {remaining}s if the channel stays quiet.",
                ephemeral=True,
            )
            return

        if selected_action == "off":
            if guild.id not in self.sessions:
                await interaction.response.send_message(
                    "Chat mode is already off.",
                    ephemeral=True,
                )
                return
            await self._disable_session(guild.id, announce_in_channel=False)
            await interaction.response.send_message(
                "Chat mode turned off for this server.",
                ephemeral=True,
            )
            return

        if selected_action != "on":
            await interaction.response.send_message(
                "Invalid action. Use on, off, or status.",
                ephemeral=True,
            )
            return

        if self.openai_service is None:
            await interaction.response.send_message(
                "Chat mode is unavailable because OPENAI_API_KEY is not configured.",
                ephemeral=True,
            )
            return

        self.sessions[guild.id] = ChatSession(
            channel_id=channel.id,
            last_human_message_at=now,
        )
        await interaction.response.send_message(
            f"Chat mode turned on in {channel.mention}. I will auto-disable after 5 minutes of quiet.",
            ephemeral=True,
        )

    def _is_supported_text_attachment(self, attachment: discord.Attachment) -> bool:
        content_type = (attachment.content_type or "").casefold()
        if content_type.startswith("text/"):
            return True
        suffix = Path(attachment.filename).suffix.casefold()
        return suffix in TEXT_ATTACHMENT_EXTENSIONS

    async def _build_user_prompt(self, message: discord.Message) -> str:
        parts: list[str] = []
        base_text = message.content.strip()
        if base_text:
            parts.append(base_text)

        for attachment in message.attachments[:MAX_ATTACHMENTS_PER_MESSAGE]:
            if attachment.size > MAX_ATTACHMENT_BYTES:
                parts.append(
                    f"[Attachment omitted: {attachment.filename} is larger than "
                    f"{MAX_ATTACHMENT_BYTES // 1024}KB]"
                )
                continue
            if not self._is_supported_text_attachment(attachment):
                parts.append(
                    f"[Attachment omitted: {attachment.filename} is not a supported text file]"
                )
                continue

            try:
                data = await attachment.read()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                parts.append(f"[Attachment omitted: failed to read {attachment.filename}]")
                continue

            text = data.decode("utf-8", errors="replace").strip()
            if not text:
                parts.append(f"[Attachment note: {attachment.filename} is empty]")
                continue
            if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
                text = f"{text[:MAX_ATTACHMENT_TEXT_CHARS]}..."

            parts.append(f"[Attachment: {attachment.filename}]\n{text}")

        combined = "\n\n".join(parts).strip()
        if len(combined) > MAX_MESSAGE_CONTEXT_CHARS:
            combined = f"{combined[:MAX_MESSAGE_CONTEXT_CHARS]}..."
        return combined

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if self.openai_service is None:
            return

        session = self.sessions.get(message.guild.id)
        if session is None or message.channel.id != session.channel_id:
            return

        session.last_human_message_at = datetime.now(UTC)
        prompt = await self._build_user_prompt(message)
        if not prompt:
            return

        lock = self._reply_locks.setdefault(message.guild.id, asyncio.Lock())
        async with lock:
            active_session = self.sessions.get(message.guild.id)
            if active_session is None or active_session.channel_id != message.channel.id:
                return

            author_name = (
                message.author.display_name
                if isinstance(message.author, discord.Member)
                else message.author.name
            )
            active_session.history.append(
                {"role": "user", "content": f"{author_name}: {prompt}"}
            )
            history = list(active_session.history)
            try:
                async with message.channel.typing():
                    response_text = await asyncio.to_thread(
                        self.openai_service.generate_reply,
                        history,
                    )
            except OpenAIServiceError as exc:
                logger.warning("Chat reply failed in guild %s: %s", message.guild.id, exc)
                return
            except Exception:
                logger.exception("Unexpected chat reply failure in guild %s", message.guild.id)
                return

            trimmed = response_text.strip()
            if not trimmed:
                return
            if len(trimmed) > 1900:
                trimmed = f"{trimmed[:1897]}..."

            active_session.history.append({"role": "assistant", "content": trimmed})
            try:
                await message.reply(
                    trimmed,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @tasks.loop(seconds=30)
    async def chat_idle_watchdog(self) -> None:
        now = datetime.now(UTC)
        expired_guild_ids = [
            guild_id
            for guild_id, session in self.sessions.items()
            if now - session.last_human_message_at >= self.idle_timeout
        ]
        for guild_id in expired_guild_ids:
            await self._disable_session(guild_id, announce_in_channel=True)

    @chat_idle_watchdog.before_loop
    async def before_chat_idle_watchdog(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self._disable_session(guild.id, announce_in_channel=False)


async def setup(bot: commands.Bot) -> None:
    openai_service = getattr(bot, "openai_service", None)
    idle_timeout = int(getattr(bot, "chat_idle_timeout_seconds", 300))
    await bot.add_cog(ChatCog(bot, openai_service, idle_timeout))
