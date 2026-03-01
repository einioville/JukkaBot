from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable

import discord
from discord.ext import commands

from jukkabot.config import Settings, load_settings
from jukkabot.music_service import MusicService
from jukkabot.openai_service import DEFAULT_CHAT_SYSTEM_PROMPT, OpenAIService
from jukkabot.queue_manager import QueueManager
from jukkabot.tracker_service import TrackerService

logging.basicConfig(level=logging.INFO)
DEFAULT_CHAT_PROMPT_FILE = "resources/prompts/ragebait_chat_prompt.txt"
DYNAMIC_MEMORY_SECTION_HEADER = "[Dynaaminen muisti]"
EMPTY_DYNAMIC_MEMORY_LINE = "- Ei tallennettuja faktoja."
MAX_DYNAMIC_MEMORY_CHARS = 5000


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
        self.chat_system_prompt_file: str | None = DEFAULT_CHAT_PROMPT_FILE
        self.chat_user_facts_by_guild: dict[int, dict[int, list[str]]] = {}
        self.chat_user_names_by_guild: dict[int, dict[int, str]] = {}
        self.chat_random_gif_urls: list[str] = []
        self.chat_idle_timeout_seconds = settings.chat_idle_timeout_seconds
        self.config_path = Path(__file__).resolve().parents[2] / "config.json"
        self._chat_prompt_sync_task: asyncio.Task[None] | None = None
        self.openai_service: OpenAIService | None = None
        self._load_persistent_config()
        if settings.openai_api_key:
            self.openai_service = OpenAIService(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                image_model=settings.openai_image_model,
                system_prompt=self.chat_system_prompt,
                temperature=settings.chat_temperature,
                max_output_tokens=settings.chat_max_output_tokens,
                timeout_seconds=settings.openai_timeout_seconds,
                image_timeout_seconds=settings.openai_image_timeout_seconds,
                enable_web_search=settings.chat_enable_web_search,
            )

    def _load_persistent_config(self) -> None:
        if not hasattr(self, "chat_random_gif_urls"):
            self.chat_random_gif_urls = []
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
                self.chat_system_prompt_file = system_prompt_file.strip()
            self._load_chat_user_facts(chat.get("user_facts"))
            self._load_chat_random_gif_urls(chat.get("random_gif_urls"))

        if self.chat_system_prompt_file:
            self._sync_dynamic_memory_to_prompt_file()
            prompt_text = self._load_chat_prompt_from_project_file(self.chat_system_prompt_file)
            if prompt_text:
                self.chat_system_prompt = prompt_text
        guilds = payload.get("guilds")
        if isinstance(guilds, dict):
            self.queue_manager.load_persistent_state(guilds)

    def _load_chat_user_facts(self, raw: object) -> None:
        self.chat_user_facts_by_guild.clear()
        self.chat_user_names_by_guild.clear()
        if not isinstance(raw, dict):
            return

        for guild_id_str, guild_payload in raw.items():
            try:
                guild_id = int(guild_id_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(guild_payload, dict):
                continue

            guild_facts: dict[int, list[str]] = {}
            guild_names: dict[int, str] = {}
            for user_id_str, user_payload in guild_payload.items():
                try:
                    user_id = int(user_id_str)
                except (TypeError, ValueError):
                    continue
                if not isinstance(user_payload, dict):
                    continue

                facts_raw = user_payload.get("facts")
                if isinstance(facts_raw, list):
                    cleaned_facts = [
                        str(item).strip()
                        for item in facts_raw
                        if isinstance(item, str) and item.strip()
                    ]
                    if cleaned_facts:
                        guild_facts[user_id] = cleaned_facts[:20]

                name_raw = user_payload.get("name")
                if isinstance(name_raw, str) and name_raw.strip():
                    guild_names[user_id] = name_raw.strip()

            if guild_facts:
                self.chat_user_facts_by_guild[guild_id] = guild_facts
            if guild_names:
                self.chat_user_names_by_guild[guild_id] = guild_names

    def _load_chat_random_gif_urls(self, raw: object) -> None:
        self.chat_random_gif_urls = []
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, str):
                continue
            url = item.strip()
            if not url:
                continue
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            self.chat_random_gif_urls.append(url)

    def add_chat_user_fact(
        self,
        guild_id: int,
        user_id: int,
        display_name: str,
        fact: str,
    ) -> bool:
        cleaned_fact = " ".join(fact.strip().split())
        if not cleaned_fact:
            return False

        guild_facts = self.chat_user_facts_by_guild.setdefault(guild_id, {})
        user_facts = guild_facts.setdefault(user_id, [])
        if any(existing.casefold() == cleaned_fact.casefold() for existing in user_facts):
            return False
        user_facts.append(cleaned_fact)
        if len(user_facts) > 20:
            del user_facts[:-20]

        cleaned_name = display_name.strip()
        if cleaned_name:
            guild_names = self.chat_user_names_by_guild.setdefault(guild_id, {})
            guild_names[user_id] = cleaned_name
        self.request_chat_prompt_sync()
        return True

    def get_chat_user_facts(self, guild_id: int) -> dict[int, list[str]]:
        facts = self.chat_user_facts_by_guild.get(guild_id, {})
        return {user_id: list(items) for user_id, items in facts.items()}

    def get_chat_user_display_name(self, guild_id: int, user_id: int) -> str | None:
        return self.chat_user_names_by_guild.get(guild_id, {}).get(user_id)

    def _resolve_chat_prompt_file_path(self, path_value: str) -> Path | None:
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
        return candidate

    def _load_chat_prompt_from_project_file(self, path_value: str) -> str | None:
        candidate = self._resolve_chat_prompt_file_path(path_value)
        if candidate is None:
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

    def _render_dynamic_memory_lines(self) -> list[str]:
        lines: list[str] = []
        for guild_id in sorted(self.chat_user_facts_by_guild.keys()):
            facts_by_user = self.chat_user_facts_by_guild[guild_id]
            if not facts_by_user:
                continue
            lines.append(f"- Guild {guild_id}:")
            names = self.chat_user_names_by_guild.get(guild_id, {})
            for user_id in sorted(facts_by_user.keys()):
                facts = facts_by_user[user_id]
                if not facts:
                    continue
                display_name = names.get(user_id) or f"user-{user_id}"
                fact_text = "; ".join(facts)
                if len(fact_text) > 300:
                    fact_text = f"{fact_text[:297]}..."
                lines.append(f"  - {display_name}: {fact_text}")
        if not lines:
            return [EMPTY_DYNAMIC_MEMORY_LINE]

        limited_lines: list[str] = []
        total = 0
        for line in lines:
            total += len(line) + 1
            if total > MAX_DYNAMIC_MEMORY_CHARS:
                limited_lines.append("  - ...")
                break
            limited_lines.append(line)
        return limited_lines or [EMPTY_DYNAMIC_MEMORY_LINE]

    def _has_chat_user_facts(self) -> bool:
        for guild_facts in self.chat_user_facts_by_guild.values():
            for facts in guild_facts.values():
                if facts:
                    return True
        return False

    def _replace_prompt_section(
        self,
        prompt_text: str,
        section_header: str,
        replacement_lines: Iterable[str],
    ) -> str:
        lines = prompt_text.splitlines()
        replacement = list(replacement_lines)
        if not replacement:
            replacement = [EMPTY_DYNAMIC_MEMORY_LINE]

        header_index: int | None = None
        header_key = section_header.casefold()
        for index, line in enumerate(lines):
            if line.strip().casefold() == header_key:
                header_index = index
                break

        if header_index is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(section_header)
            lines.extend(replacement)
            return "\n".join(lines).rstrip() + "\n"

        section_start = header_index + 1
        section_end = len(lines)
        for index in range(section_start, len(lines)):
            stripped = lines[index].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section_end = index
                break

        updated = lines[:section_start] + replacement + lines[section_end:]
        return "\n".join(updated).rstrip() + "\n"

    def _sync_dynamic_memory_to_prompt_file(self) -> None:
        prompt_file = getattr(self, "chat_system_prompt_file", None)
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            return
        if not hasattr(self, "config_path"):
            return
        candidate = self._resolve_chat_prompt_file_path(prompt_file)
        if candidate is None or not candidate.exists() or not candidate.is_file():
            return

        try:
            current_text = candidate.read_text(encoding="utf-8")
        except OSError:
            logging.exception("Failed reading chat prompt file for memory sync: %s", candidate)
            return

        has_dynamic_memory_section = any(
            line.strip().casefold() == DYNAMIC_MEMORY_SECTION_HEADER.casefold()
            for line in current_text.splitlines()
        )
        if has_dynamic_memory_section or self._has_chat_user_facts():
            updated_text = self._replace_prompt_section(
                current_text,
                DYNAMIC_MEMORY_SECTION_HEADER,
                self._render_dynamic_memory_lines(),
            )
        else:
            updated_text = current_text
        if updated_text != current_text:
            try:
                candidate.write_text(updated_text, encoding="utf-8")
            except OSError:
                logging.exception("Failed writing chat prompt file memory section: %s", candidate)
                return

        self.chat_system_prompt = updated_text.strip() or DEFAULT_CHAT_SYSTEM_PROMPT
        openai_service = getattr(self, "openai_service", None)
        if openai_service is not None:
            openai_service.system_prompt = self.chat_system_prompt

    async def _sync_dynamic_memory_to_prompt_file_async(self) -> None:
        try:
            await asyncio.to_thread(self._sync_dynamic_memory_to_prompt_file)
        except Exception:
            logging.exception("Async chat prompt sync failed.")

    def request_chat_prompt_sync(self) -> None:
        prompt_file = getattr(self, "chat_system_prompt_file", None)
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        existing_task = getattr(self, "_chat_prompt_sync_task", None)
        if existing_task is not None and not existing_task.done():
            return
        self._chat_prompt_sync_task = loop.create_task(
            self._sync_dynamic_memory_to_prompt_file_async()
        )

    def _save_persistent_config(self) -> None:
        self._sync_dynamic_memory_to_prompt_file()
        if self.openai_service is not None:
            self.chat_system_prompt = (
                self.openai_service.system_prompt.strip() or DEFAULT_CHAT_SYSTEM_PROMPT
            )
        chat_payload: dict[str, object] = {}
        if self.chat_system_prompt_file:
            chat_payload["system_prompt_file"] = self.chat_system_prompt_file
        gif_urls = getattr(self, "chat_random_gif_urls", [])
        if isinstance(gif_urls, list):
            cleaned_gif_urls = [
                url.strip()
                for url in gif_urls
                if isinstance(url, str)
                and url.strip()
                and (
                    url.strip().startswith("http://")
                    or url.strip().startswith("https://")
                )
            ]
            if cleaned_gif_urls:
                chat_payload["random_gif_urls"] = cleaned_gif_urls
        serialized_user_facts: dict[str, dict[str, dict[str, object]]] = {}
        for guild_id, guild_facts in self.chat_user_facts_by_guild.items():
            if not guild_facts:
                continue
            guild_payload: dict[str, dict[str, object]] = {}
            guild_names = self.chat_user_names_by_guild.get(guild_id, {})
            for user_id, facts in guild_facts.items():
                if not facts:
                    continue
                user_payload: dict[str, object] = {
                    "facts": list(facts),
                }
                name = guild_names.get(user_id)
                if isinstance(name, str) and name.strip():
                    user_payload["name"] = name.strip()
                guild_payload[str(user_id)] = user_payload
            if guild_payload:
                serialized_user_facts[str(guild_id)] = guild_payload
        if serialized_user_facts:
            chat_payload["user_facts"] = serialized_user_facts
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
        pending_sync = getattr(self, "_chat_prompt_sync_task", None)
        if pending_sync is not None and not pending_sync.done():
            try:
                await pending_sync
            except Exception:
                logging.exception("Pending chat prompt sync failed during shutdown.")
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
