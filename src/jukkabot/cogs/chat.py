from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
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
MAX_IMAGE_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_IMAGE_ATTACHMENTS_PER_MESSAGE = 3
MAX_MESSAGE_CONTEXT_CHARS = 8000
MAX_MEMORY_CONTEXT_CHARS = 3500
MAX_PARTICIPANTS_IN_MEMORY = 10
GIF_CHANCE = 0.2
GIF_COOLDOWN_SECONDS = 60
MAX_REPLY_CHUNKS = 6
MAX_DISCORD_MESSAGE_CHARS = 1900
BRAINROT_GIF_URLS = (
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcW0zbnNoOTI4Y2V3YjQ3azJmdGRob3YxY2MyYjBicW5mZG9kN3RzYiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7aD2saalBwwftBIY/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExdjYwbnk1Y2Y5dTFrc2h5aXJhM3A2eG1sYjBvYjFkdzJnbnhwMWhqMCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/l4FGpP4lxGGgK5CBW/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExd3NpN3Vmd2wyZzVnYTRoN2V0OG8xN2NnZmxjOGM5N2E4YXF5aXVkNiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/13CoXDiaCcCoyk/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMWF4Mjh3Y2ptMDRvMWNuY2xha2RmOHMyc3A2MHhqeGZ2M3V2a2dubiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/11mwI67GLeMvgA/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMWN3NXU4Y2hwN3N6eDZ4bW1wajk2NnljcGc3M3djY3llMjI2M3cybiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/xT0xeJpnrWC4XWblEk/giphy.gif",
)
REMEMBER_COMMAND_PATTERN = re.compile(
    r"^\s*muista\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
IMAGE_ATTACHMENT_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
SUPPORTED_IMAGE_MIME_TYPES = set(IMAGE_ATTACHMENT_MIME_BY_SUFFIX.values())


@dataclass(slots=True)
class ParticipantState:
    display_name: str
    message_count: int = 0
    cooked_count: int = 0
    last_message: str = ""


@dataclass(slots=True)
class ChatSession:
    guild_id: int
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
    @app_commands.describe(action="Enable or disable chat mode.")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
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
                "Invalid action. Use on or off.",
                ephemeral=True,
            )
            return

        if self.openai_service is None:
            await interaction.response.send_message(
                "Chat mode is unavailable because OPENAI_API_KEY is not configured.",
                ephemeral=True,
            )
            return

        existing = self.sessions.get(guild.id)
        if existing is not None and existing.channel_id == channel.id:
            await interaction.response.send_message(
                "Chat mode is already on in this channel.",
                ephemeral=True,
            )
            return

        self.sessions[guild.id] = ChatSession(
            guild_id=guild.id,
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

    def _resolve_image_mime_type(self, attachment: discord.Attachment) -> str | None:
        content_type = (attachment.content_type or "").split(";", 1)[0].strip().casefold()
        if content_type in SUPPORTED_IMAGE_MIME_TYPES:
            return content_type
        suffix = Path(attachment.filename).suffix.casefold()
        return IMAGE_ATTACHMENT_MIME_BY_SUFFIX.get(suffix)

    def _is_supported_image_attachment(self, attachment: discord.Attachment) -> bool:
        return self._resolve_image_mime_type(attachment) is not None

    async def _build_image_input(
        self, attachment: discord.Attachment
    ) -> tuple[dict[str, str] | None, str | None]:
        mime_type = self._resolve_image_mime_type(attachment)
        if mime_type is None:
            return None, (
                f"[Attachment omitted: {attachment.filename} is not a supported image file]"
            )
        if attachment.size > MAX_IMAGE_ATTACHMENT_BYTES:
            return None, (
                f"[Image omitted: {attachment.filename} is larger than "
                f"{MAX_IMAGE_ATTACHMENT_BYTES // (1024 * 1024)}MB]"
            )

        try:
            data = await attachment.read()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None, f"[Image omitted: failed to read {attachment.filename}]"

        if not data:
            return None, f"[Image omitted: {attachment.filename} is empty]"
        if len(data) > MAX_IMAGE_ATTACHMENT_BYTES:
            return None, (
                f"[Image omitted: {attachment.filename} is larger than "
                f"{MAX_IMAGE_ATTACHMENT_BYTES // (1024 * 1024)}MB]"
            )

        encoded = base64.b64encode(data).decode("ascii")
        return {"mime_type": mime_type, "data_base64": encoded}, None

    async def _build_user_prompt(
        self,
        message: discord.Message,
        *,
        include_attachments: bool,
        image_inputs_out: list[dict[str, str]] | None = None,
    ) -> str:
        parts: list[str] = []
        base_text = message.content.strip()
        if base_text:
            parts.append(base_text)

        if include_attachments:
            for attachment in message.attachments[:MAX_ATTACHMENTS_PER_MESSAGE]:
                if not self._is_supported_text_attachment(attachment):
                    if (
                        image_inputs_out is not None
                        and self._is_supported_image_attachment(attachment)
                    ):
                        if len(image_inputs_out) >= MAX_IMAGE_ATTACHMENTS_PER_MESSAGE:
                            parts.append(
                                f"[Image omitted: more than {MAX_IMAGE_ATTACHMENTS_PER_MESSAGE} "
                                "images are not supported]"
                            )
                            continue
                        image_input, image_note = await self._build_image_input(attachment)
                        if image_note:
                            parts.append(image_note)
                            continue
                        if image_input is not None:
                            image_inputs_out.append(image_input)
                            parts.append(f"[Image attached: {attachment.filename}]")
                            continue
                    parts.append(
                        f"[Attachment omitted: {attachment.filename} is not a supported text file]"
                    )
                    continue
                if attachment.size > MAX_ATTACHMENT_BYTES:
                    parts.append(
                        f"[Attachment omitted: {attachment.filename} is larger than "
                        f"{MAX_ATTACHMENT_BYTES // 1024}KB]"
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
        sections: list[str] = []
        if session.participants:
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
            sections.append("Conversation participants and memory:\n" + "\n".join(lines))

        get_facts = getattr(self.bot, "get_chat_user_facts", None)
        get_name = getattr(self.bot, "get_chat_user_display_name", None)
        if callable(get_facts):
            facts_by_user = get_facts(session.guild_id)
            if facts_by_user:
                fact_lines: list[str] = []
                sorted_users = sorted(
                    facts_by_user.items(),
                    key=lambda pair: len(pair[1]),
                    reverse=True,
                )[:MAX_PARTICIPANTS_IN_MEMORY]
                for user_id, facts in sorted_users:
                    if not facts:
                        continue
                    display_name = (
                        get_name(session.guild_id, user_id)
                        if callable(get_name)
                        else None
                    ) or f"user-{user_id}"
                    preview = "; ".join(facts[:3])
                    if len(preview) > 240:
                        preview = f"{preview[:237]}..."
                    fact_lines.append(f"- {display_name}: {preview}")
                if fact_lines:
                    sections.append("Remembered user facts:\n" + "\n".join(fact_lines))
        combined = "\n\n".join(section for section in sections if section).strip()
        if len(combined) > MAX_MEMORY_CONTEXT_CHARS:
            combined = f"{combined[:MAX_MEMORY_CONTEXT_CHARS]}..."
        return combined

    def _extract_memory_fact_payload(self, content: str) -> str | None:
        normalized = self._strip_leading_bot_mention(content)
        match = REMEMBER_COMMAND_PATTERN.match(normalized)
        if not match:
            return None
        payload = match.group(1).strip()
        return payload or None

    def _resolve_target_user_for_fact(
        self,
        message: discord.Message,
        session: ChatSession,
        payload: str,
    ) -> tuple[int, str, str]:
        if message.mentions:
            mention = next((user for user in message.mentions if not user.bot), None)
            if mention is not None:
                fact_text = re.sub(r"<@!?\d+>", "", payload).strip(" ,:-")
                display_name = (
                    mention.display_name
                    if isinstance(mention, discord.Member)
                    else mention.name
                )
                if fact_text:
                    return mention.id, display_name, fact_text

        token, _, remainder = payload.partition(" ")
        token = token.strip(" ,:;.!?")
        if token and remainder.strip():
            token_cf = token.casefold()
            for user_id, participant in session.participants.items():
                participant_cf = participant.display_name.casefold()
                if participant_cf == token_cf or participant_cf.startswith(token_cf):
                    return user_id, participant.display_name, remainder.strip()
            if isinstance(message.guild, discord.Guild):
                for member in message.guild.members:
                    if (
                        member.display_name.casefold() == token_cf
                        or member.name.casefold() == token_cf
                    ):
                        return member.id, member.display_name, remainder.strip()

        author_name = (
            message.author.display_name
            if isinstance(message.author, discord.Member)
            else message.author.name
        )
        return message.author.id, author_name, payload

    def _remember_user_fact(self, message: discord.Message, session: ChatSession) -> bool:
        payload = self._extract_memory_fact_payload(message.content)
        if not payload:
            return False

        target_id, target_name, fact_text = self._resolve_target_user_for_fact(
            message,
            session,
            payload,
        )
        add_fact = getattr(self.bot, "add_chat_user_fact", None)
        if not callable(add_fact):
            return False

        added = bool(add_fact(message.guild.id, target_id, target_name, fact_text))
        if added:
            session.history.append(
                {
                    "role": "assistant",
                    "content": f"Memory stored: {target_name} -> {fact_text}",
                }
            )
        return added

    def _strip_leading_bot_mention(self, content: str) -> str:
        bot = getattr(self, "bot", None)
        bot_user = getattr(bot, "user", None)
        if bot_user is None:
            return content
        stripped = content.lstrip()
        mention_plain = f"<@{bot_user.id}>"
        mention_nick = f"<@!{bot_user.id}>"
        if stripped.startswith(mention_plain):
            return stripped[len(mention_plain) :].lstrip(" \t,:;-")
        if stripped.startswith(mention_nick):
            return stripped[len(mention_nick) :].lstrip(" \t,:;-")
        return content

    def _is_bot_mentioned(self, message: discord.Message) -> bool:
        if self.bot.user is None:
            return False
        return any(user.id == self.bot.user.id for user in message.mentions)

    def _should_send_gif(self, session: ChatSession, now: datetime) -> bool:
        if session.last_gif_sent_at is not None:
            elapsed = (now - session.last_gif_sent_at).total_seconds()
            if elapsed < GIF_COOLDOWN_SECONDS:
                return False
        return random.random() < GIF_CHANCE

    def _split_discord_chunks(self, text: str, limit: int = MAX_DISCORD_MESSAGE_CHARS) -> list[str]:
        remaining = text.strip()
        if not remaining:
            return []
        chunks: list[str] = []
        while len(remaining) > limit:
            split_index = remaining.rfind("\n", 0, limit)
            if split_index < int(limit * 0.6):
                split_index = remaining.rfind(" ", 0, limit)
            if split_index <= 0:
                split_index = limit
            chunk = remaining[:split_index].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_index:].lstrip()
            if len(chunks) >= MAX_REPLY_CHUNKS:
                break

        if remaining and len(chunks) < MAX_REPLY_CHUNKS:
            chunks.append(remaining)
        elif remaining and chunks:
            chunks[-1] = f"{chunks[-1]}\n..."
        return chunks

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
        is_mentioned = self._is_bot_mentioned(message)
        image_inputs: list[dict[str, str]] = []
        prompt = await self._build_user_prompt(
            message,
            include_attachments=is_mentioned,
            image_inputs_out=image_inputs if is_mentioned else None,
        )
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
        history_entry: dict[str, str] = {"role": "user", "content": f"{user_display_name}: {prompt}"}
        session.history.append(history_entry)
        if not is_mentioned:
            return
        self._remember_user_fact(message, session)

        lock = self._reply_locks.setdefault(message.guild.id, asyncio.Lock())
        async with lock:
            active_session = self.sessions.get(message.guild.id)
            if active_session is None or active_session.channel_id != message.channel.id:
                return

            history = list(active_session.history)
            if image_inputs:
                for index in range(len(history) - 1, -1, -1):
                    if history[index] is history_entry:
                        history[index] = {
                            "role": "user",
                            "content": history_entry["content"],
                            "images": image_inputs,
                        }
                        break
            memory_block = self._build_participant_memory(active_session)
            if memory_block:
                history = [{"role": "user", "content": memory_block}, *history]
            try:
                async with message.channel.typing():
                    response_text = await asyncio.to_thread(
                        self.openai_service.generate_reply,
                        history,
                        use_web_search=True,
                    )
            except OpenAIServiceError as exc:
                logger.warning("Chat reply failed in guild %s: %s", message.guild.id, exc)
                return
            except Exception:
                logger.exception("Unexpected chat reply failure in guild %s", message.guild.id)
                return

            response_clean = response_text.strip()
            if not response_clean:
                return
            history_response = (
                response_clean
                if len(response_clean) <= 4000
                else f"{response_clean[:3997]}..."
            )
            active_session.history.append({"role": "assistant", "content": history_response})
            chunks = self._split_discord_chunks(response_clean)
            if not chunks:
                return

            try:
                await message.reply(
                    chunks[0],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                for extra_chunk in chunks[1:]:
                    await message.channel.send(
                        extra_chunk,
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
