from __future__ import annotations

import asyncio
import logging

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

    async def setup_hook(self) -> None:
        await self.load_extension("jukkabot.cogs.music")
        synced = await self.tree.sync()
        logging.info("Synced %s slash commands.", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logging.info("Connected as %s (%s)", self.user.name, self.user.id)


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
