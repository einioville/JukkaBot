from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from jukkabot.config import load_settings
from jukkabot.music_service import MusicService
from jukkabot.queue_manager import QueueManager
from jukkabot.tracker_service import TrackerService

logging.basicConfig(level=logging.INFO)


class JukkaBot(commands.Bot):
    def __init__(self, admin_user_ids: set[int], tracker_api_key: str | None) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        intents.guilds = True
        intents.voice_states = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.admin_user_ids = admin_user_ids
        self.queue_manager = QueueManager()
        self.music_service = MusicService()
        self.tracker_service = (
            TrackerService(api_key=tracker_api_key) if tracker_api_key else None
        )
        self.config_path = Path(__file__).resolve().parents[2] / "config.json"
        self._load_persistent_config()

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
        guilds = payload.get("guilds")
        if isinstance(guilds, dict):
            self.queue_manager.load_persistent_state(guilds)

    def _save_persistent_config(self) -> None:
        payload = {
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
    bot = JukkaBot(
        admin_user_ids=settings.admin_user_ids,
        tracker_api_key=settings.tracker_api_key,
    )

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
