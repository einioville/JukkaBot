from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from jukkabot.bot import DEFAULT_CHAT_PROMPT_FILE, JukkaBot
from jukkabot.openai_service import DEFAULT_CHAT_SYSTEM_PROMPT
from jukkabot.queue_manager import QueueManager


def test_load_chat_prompt_from_project_file_reads_relative_path() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        prompt_path = test_root / "resources" / "prompts" / "chat_system.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("project prompt", encoding="utf-8")

        text = bot._load_chat_prompt_from_project_file("resources/prompts/chat_system.txt")

        assert text == "project prompt"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_load_chat_prompt_from_project_file_rejects_outside_project() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    bot = JukkaBot.__new__(JukkaBot)
    bot.config_path = test_root / "config.json"

    text = bot._load_chat_prompt_from_project_file("../outside.txt")

    assert text is None


def test_chat_user_fact_store_deduplicates_and_returns_copy() -> None:
    bot = JukkaBot.__new__(JukkaBot)
    bot.chat_user_facts_by_guild = {}
    bot.chat_user_names_by_guild = {}

    assert bot.add_chat_user_fact(1, 10, "ville", "likes fortnite")
    assert not bot.add_chat_user_fact(1, 10, "ville", "likes fortnite")
    assert bot.get_chat_user_display_name(1, 10) == "ville"

    facts = bot.get_chat_user_facts(1)
    assert facts[10] == ["likes fortnite"]

    # Verify caller cannot mutate internal storage accidentally.
    facts[10].append("mutated")
    assert bot.get_chat_user_facts(1)[10] == ["likes fortnite"]


def test_load_chat_user_facts_parses_nested_payload() -> None:
    bot = JukkaBot.__new__(JukkaBot)
    bot.chat_user_facts_by_guild = {}
    bot.chat_user_names_by_guild = {}

    bot._load_chat_user_facts(
        {
            "1": {
                "10": {"name": "ville", "facts": ["likes fortnite", "plays valorant"]},
                "11": {"name": "someone", "facts": []},
            }
        }
    )

    assert bot.get_chat_user_display_name(1, 10) == "ville"
    assert bot.get_chat_user_facts(1)[10] == ["likes fortnite", "plays valorant"]


def test_save_config_uses_prompt_file_and_omits_inline_prompt() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.openai_service = None
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {}
        bot.chat_user_names_by_guild = {}
        bot.chat_random_gif_urls = []
        bot.queue_manager = QueueManager()
        bot.config_path.parent.mkdir(parents=True, exist_ok=True)

        bot._save_persistent_config()

        payload = json.loads(bot.config_path.read_text(encoding="utf-8"))
        chat = payload["chat"]
        assert chat["system_prompt_file"] == DEFAULT_CHAT_PROMPT_FILE
        assert "system_prompt" not in chat
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_load_config_uses_default_prompt_file_when_not_explicit() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.queue_manager = QueueManager()
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {}
        bot.chat_user_names_by_guild = {}
        bot.chat_random_gif_urls = []

        prompt_path = test_root / DEFAULT_CHAT_PROMPT_FILE
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("rage prompt from file", encoding="utf-8")
        bot.config_path.write_text(
            json.dumps({"chat": {}, "guilds": {}}),
            encoding="utf-8",
        )

        bot._load_persistent_config()

        assert bot.chat_system_prompt_file == DEFAULT_CHAT_PROMPT_FILE
        assert bot.chat_system_prompt == "rage prompt from file"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_sync_dynamic_memory_updates_prompt_section() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_user_facts_by_guild = {1: {10: ["likes fortnite"]}}
        bot.chat_user_names_by_guild = {1: {10: "ville"}}
        bot.chat_random_gif_urls = []
        bot.openai_service = None

        prompt_path = test_root / DEFAULT_CHAT_PROMPT_FILE
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(
            "base prompt\n\n[Dynaaminen muisti]\n- old value\n\n[Other]\nkeep",
            encoding="utf-8",
        )

        bot._sync_dynamic_memory_to_prompt_file()

        updated = prompt_path.read_text(encoding="utf-8")
        assert "- old value" not in updated
        assert "- Guild 1:" in updated
        assert "ville: likes fortnite" in updated
        assert "[Other]\nkeep" in updated
        assert "ville: likes fortnite" in bot.chat_system_prompt
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_sync_dynamic_memory_appends_section_when_missing_and_facts_exist() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_user_facts_by_guild = {1: {10: ["likes fortnite"]}}
        bot.chat_user_names_by_guild = {1: {10: "ville"}}
        bot.chat_random_gif_urls = []
        bot.openai_service = None

        prompt_path = test_root / DEFAULT_CHAT_PROMPT_FILE
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("base prompt", encoding="utf-8")

        bot._sync_dynamic_memory_to_prompt_file()

        updated = prompt_path.read_text(encoding="utf-8")
        assert "[Dynaaminen muisti]" in updated
        assert "ville: likes fortnite" in updated
        assert updated.startswith("base prompt")
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_load_config_reads_chat_random_gif_urls() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.queue_manager = QueueManager()
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {}
        bot.chat_user_names_by_guild = {}
        bot.chat_random_gif_urls = []

        prompt_path = test_root / DEFAULT_CHAT_PROMPT_FILE
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("prompt", encoding="utf-8")
        bot.config_path.parent.mkdir(parents=True, exist_ok=True)
        bot.config_path.write_text(
            json.dumps(
                {
                    "chat": {
                        "random_gif_urls": [
                            "https://example.com/a.gif",
                            "https://example.com/b.gif",
                        ]
                    },
                    "guilds": {},
                }
            ),
            encoding="utf-8",
        )

        bot._load_persistent_config()

        assert bot.chat_random_gif_urls == [
            "https://example.com/a.gif",
            "https://example.com/b.gif",
        ]
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_save_config_persists_chat_random_gif_urls() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.openai_service = None
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {}
        bot.chat_user_names_by_guild = {}
        bot.chat_random_gif_urls = [
            "https://example.com/a.gif",
            "https://example.com/b.gif",
        ]
        bot.queue_manager = QueueManager()
        bot.config_path.parent.mkdir(parents=True, exist_ok=True)

        bot._save_persistent_config()

        payload = json.loads(bot.config_path.read_text(encoding="utf-8"))
        assert payload["chat"]["random_gif_urls"] == [
            "https://example.com/a.gif",
            "https://example.com/b.gif",
        ]
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_save_config_omits_user_facts_payload() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.openai_service = None
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {1: {10: ["likes fortnite"]}}
        bot.chat_user_names_by_guild = {1: {10: "ville"}}
        bot.chat_random_gif_urls = []
        bot.queue_manager = QueueManager()
        bot.config_path.parent.mkdir(parents=True, exist_ok=True)

        bot._save_persistent_config()

        payload = json.loads(bot.config_path.read_text(encoding="utf-8"))
        chat = payload.get("chat", {})
        assert "user_facts" not in chat
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_load_config_ignores_user_facts_payload() -> None:
    test_root = Path(".tmp_test_prompt") / f"case_{uuid4().hex}"
    try:
        bot = JukkaBot.__new__(JukkaBot)
        bot.config_path = test_root / "config.json"
        bot.queue_manager = QueueManager()
        bot.chat_system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
        bot.chat_system_prompt_file = DEFAULT_CHAT_PROMPT_FILE
        bot.chat_user_facts_by_guild = {}
        bot.chat_user_names_by_guild = {}
        bot.chat_random_gif_urls = []

        prompt_path = test_root / DEFAULT_CHAT_PROMPT_FILE
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("prompt", encoding="utf-8")
        bot.config_path.parent.mkdir(parents=True, exist_ok=True)
        bot.config_path.write_text(
            json.dumps(
                {
                    "chat": {
                        "user_facts": {
                            "1": {
                                "10": {
                                    "name": "ville",
                                    "facts": ["likes fortnite"],
                                }
                            }
                        }
                    },
                    "guilds": {},
                }
            ),
            encoding="utf-8",
        )

        bot._load_persistent_config()

        assert bot.chat_user_facts_by_guild == {}
        assert bot.chat_user_names_by_guild == {}
    finally:
        shutil.rmtree(test_root, ignore_errors=True)
