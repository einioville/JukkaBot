from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from jukkabot.config import load_settings
from jukkabot.music_service import MusicService
from jukkabot.queue_manager import QueueManager

logging.basicConfig(level=logging.INFO)


class JukkaBot(commands.Bot):
    def __init__(self, admin_user_ids: set[int]) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        intents.guilds = True
        intents.voice_states = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.admin_user_ids = admin_user_ids
        self.queue_manager = QueueManager()
        self.music_service = MusicService()

    async def setup_hook(self) -> None:
        await self.load_extension("jukkabot.cogs.music")
        synced = await self.tree.sync()
        logging.info("Synced %s slash commands.", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logging.info("Connected as %s (%s)", self.user.name, self.user.id)


def run() -> None:
    settings = load_settings()
    bot = JukkaBot(admin_user_ids=settings.admin_user_ids)
    asyncio.run(bot.start(settings.token))
