from __future__ import annotations

import asyncio
import logging
import random
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
MAX_PARTICIPANTS_IN_MEMORY = 10
GIF_CHANCE = 0.2
GIF_COOLDOWN_SECONDS = 60
MAX_MESSAGES_WITHOUT_BOT_REPLY = 6
BRAINROT_GIF_URLS = (
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcW0zbnNoOTI4Y2V3YjQ3azJmdGRob3YxY2MyYjBicW5mZG9kN3RzYiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7aD2saalBwwftBIY/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExdjYwbnk1Y2Y5dTFrc2h5aXJhM3A2eG1sYjBvYjFkdzJnbnhwMWhqMCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/l4FGpP4lxGGgK5CBW/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExd3NpN3Vmd2wyZzVnYTRoN2V0OG8xN2NnZmxjOGM5N2E4YXF5aXVkNiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/13CoXDiaCcCoyk/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMWF4Mjh3Y2ptMDRvMWNuY2xha2RmOHMyc3A2MHhqeGZ2M3V2a2dubiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/11mwI67GLeMvgA/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMWN3NXU4Y2hwN3N6eDZ4bW1wajk2NnljcGc3M3djY3llMjI2M3cybiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/xT0xeJpnrWC4XWblEk/giphy.gif",
)
REPLY_TRIGGER_TOKENS = (
    "bot",
    "jukka",
    "what",
    "why",
    "how",
    "bruh",
    "bro",
    "wtf",
    "lol",
    "lmao",
)


@dataclass(slots=True)
class ParticipantState:
    display_name: str
    message_count: int = 0
    cooked_count: int = 0
    last_message: str = ""


@dataclass(slots=True)
class ChatSession:
    channel_id: int
    last_human_message_at: datetime
    history: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=30))
    participants: dict[int, ParticipantState] = field(default_factory=dict)
    messages_since_last_reply: int = 0
    last_bot_reply_at: datetime | None = None
    last_gif_sent_at: datetime | None = None


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
    async def chat(self, interaction: discord.Interaction, action: str = "on") -> None:
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

    def _build_participant_memory(self, session: ChatSession) -> str:
        if not session.participants:
            return ""
        ranked = sorted(
            session.participants.values(),
            key=lambda participant: (
                participant.message_count,
                participant.cooked_count,
            ),
            reverse=True,
        )[:MAX_PARTICIPANTS_IN_MEMORY]
        lines: list[str] = []
        for participant in ranked:
            last_message = participant.last_message.replace("\n", " ").strip()
            if len(last_message) > 120:
                last_message = f"{last_message[:117]}..."
            lines.append(
                f"- {participant.display_name}: messages={participant.message_count}, "
                f"cooked={participant.cooked_count}, last=\"{last_message}\""
            )
        return "Conversation participants and memory:\n" + "\n".join(lines)

    def _should_reply(
        self,
        message: discord.Message,
        session: ChatSession,
        prompt: str,
        now: datetime,
    ) -> bool:
        if self.bot.user and any(user.id == self.bot.user.id for user in message.mentions):
            return True
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            referenced = message.reference.resolved
            if referenced.author.id == self.bot.user.id:
                return True

        if session.messages_since_last_reply >= MAX_MESSAGES_WITHOUT_BOT_REPLY:
            return True

        chance = 0.15
        lowered = prompt.casefold()
        if "?" in prompt:
            chance += 0.12
        if any(token in lowered for token in REPLY_TRIGGER_TOKENS):
            chance += 0.12
        if session.messages_since_last_reply >= 3:
            chance += 0.18
        if session.last_bot_reply_at is None:
            chance += 0.2
        else:
            silence = (now - session.last_bot_reply_at).total_seconds()
            if silence >= 60:
                chance += 0.1
            if silence >= 180:
                chance += 0.2
        return random.random() < min(0.9, chance)

    def _should_send_gif(self, session: ChatSession, now: datetime) -> bool:
        if session.last_gif_sent_at is not None:
            elapsed = (now - session.last_gif_sent_at).total_seconds()
            if elapsed < GIF_COOLDOWN_SECONDS:
                return False
        return random.random() < GIF_CHANCE

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
        user_display_name = (
            message.author.display_name
            if isinstance(message.author, discord.Member)
            else message.author.name
        )
        participant = session.participants.get(message.author.id)
        if participant is None:
            participant = ParticipantState(display_name=user_display_name)
            session.participants[message.author.id] = participant
        else:
            participant.display_name = user_display_name
        participant.message_count += 1
        participant.last_message = prompt[:300]
        session.messages_since_last_reply += 1

        now = datetime.now(UTC)
        if not self._should_reply(message, session, prompt, now):
            return

        lock = self._reply_locks.setdefault(message.guild.id, asyncio.Lock())
        async with lock:
            active_session = self.sessions.get(message.guild.id)
            if active_session is None or active_session.channel_id != message.channel.id:
                return

            active_session.history.append(
                {"role": "user", "content": f"{user_display_name}: {prompt}"}
            )
            history = list(active_session.history)
            memory_block = self._build_participant_memory(active_session)
            if memory_block:
                history = [{"role": "user", "content": memory_block}, *history]
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
            else:
                active_session.messages_since_last_reply = 0
                active_session.last_bot_reply_at = datetime.now(UTC)
                target = active_session.participants.get(message.author.id)
                if target is not None:
                    target.cooked_count += 1
                if self._should_send_gif(active_session, active_session.last_bot_reply_at):
                    try:
                        await message.channel.send(
                            random.choice(BRAINROT_GIF_URLS),
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    else:
                        active_session.last_gif_sent_at = datetime.now(UTC)

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
