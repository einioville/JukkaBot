from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from jukkabot.config import Settings, load_settings
from jukkabot.music_service import MusicService
from jukkabot.openai_service import DEFAULT_CHAT_SYSTEM_PROMPT, OpenAIService
from jukkabot.queue_manager import QueueManager
from jukkabot.tracker_service import TrackerService

logging.basicConfig(level=logging.INFO)


class JukkaBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.voice_states = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.admin_user_ids = settings.admin_user_ids
        self.queue_manager = QueueManager()
        self.music_service = MusicService()
        self.tracker_service = (
            TrackerService(api_key=settings.tracker_api_key)
            if settings.tracker_api_key
            else None
        )
        self.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        self.chat_system_prompt_file: str | None = None
        self.chat_idle_timeout_seconds = settings.chat_idle_timeout_seconds
        self.config_path = Path(__file__).resolve().parents[2] / "config.json"
        self._load_persistent_config()
        self.openai_service = (
            OpenAIService(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                system_prompt=self.chat_system_prompt,
                temperature=settings.chat_temperature,
                max_output_tokens=settings.chat_max_output_tokens,
            )
            if settings.openai_api_key
            else None
        )

    def _load_persistent_config(self) -> None:
        if not self.config_path.exists():
            return
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logging.warning("Could not read persistent config from %s", self.config_path)
            return

        if not isinstance(payload, dict):
            return
        chat = payload.get("chat")
        if isinstance(chat, dict):
            system_prompt_file = chat.get("system_prompt_file")
            if isinstance(system_prompt_file, str) and system_prompt_file.strip():
                prompt_text = self._load_chat_prompt_from_project_file(system_prompt_file.strip())
                if prompt_text:
                    self.chat_system_prompt_file = system_prompt_file.strip()
                    self.chat_system_prompt = prompt_text
            system_prompt = chat.get("system_prompt")
            if (
                isinstance(system_prompt, str)
                and system_prompt.strip()
                and self.chat_system_prompt == DEFAULT_CHAT_SYSTEM_PROMPT
            ):
                self.chat_system_prompt = system_prompt.strip()
        guilds = payload.get("guilds")
        if isinstance(guilds, dict):
            self.queue_manager.load_persistent_state(guilds)

    def _load_chat_prompt_from_project_file(self, path_value: str) -> str | None:
        relative_path = Path(path_value)
        if relative_path.is_absolute():
            logging.warning("Ignoring absolute chat.system_prompt_file path: %s", path_value)
            return None
        project_root = self.config_path.parent.resolve()
        candidate = (project_root / relative_path).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError:
            logging.warning("Ignoring chat.system_prompt_file outside project root: %s", path_value)
            return None
        if not candidate.exists() or not candidate.is_file():
            logging.warning("Chat prompt file does not exist: %s", candidate)
            return None
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            logging.exception("Failed reading chat prompt file: %s", candidate)
            return None
        return text or None

    def _save_persistent_config(self) -> None:
        if self.openai_service is not None:
            self.chat_system_prompt = (
                self.openai_service.system_prompt.strip() or DEFAULT_CHAT_SYSTEM_PROMPT
            )
        chat_payload: dict[str, str] = {
            "system_prompt": self.chat_system_prompt,
        }
        if self.chat_system_prompt_file:
            chat_payload["system_prompt_file"] = self.chat_system_prompt_file
        payload = {
            "chat": chat_payload,
            "guilds": self.queue_manager.to_persistent_state(),
        }
        temp_path = self.config_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp_path.replace(self.config_path)
        except OSError:
            logging.exception("Failed to write persistent config to %s", self.config_path)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def setup_hook(self) -> None:
        await self.load_extension("jukkabot.cogs.music")
        await self.load_extension("jukkabot.cogs.chat")
        synced = await self.tree.sync()
        logging.info("Synced %s slash commands.", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logging.info("Connected as %s (%s)", self.user.name, self.user.id)

    async def close(self) -> None:
        self._save_persistent_config()
        await super().close()


def run() -> None:
    settings = load_settings()
    bot = JukkaBot(settings)

    async def runner() -> None:
        try:
            await bot.start(settings.token)
        finally:
            # Ensure explicit logout/cleanup when process is interrupted (Ctrl+C).
            if not bot.is_closed():
                await bot.close()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        logging.info("Shutdown requested, disconnected from Discord.")
