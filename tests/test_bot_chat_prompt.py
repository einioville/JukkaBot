from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from jukkabot.bot import JukkaBot


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
