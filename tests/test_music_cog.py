from __future__ import annotations

import asyncio

from jukkabot.cogs.music import MusicCog
from jukkabot.models import Track
from jukkabot.queue_manager import QueueManager


class _FakeVoiceClient:
    def __init__(self, *, playing: bool = True, paused: bool = False) -> None:
        self._playing = playing
        self._paused = paused
        self.stopped = False

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        self.stopped = True
        self._playing = False
        self._paused = False


class _FakeGuild:
    def __init__(self, guild_id: int, voice_client: _FakeVoiceClient) -> None:
        self.id = guild_id
        self.voice_client = voice_client


def _track(name: str) -> Track:
    return Track(
        title=name,
        url=f"https://example.com/{name}",
        author="tester",
        duration_seconds=60,
    )


def test_previous_restarts_current_track_when_elapsed_is_over_five_seconds() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    cog._pending_seek_seconds = {}
    cog._current_elapsed_seconds = lambda _guild_id: 6.5

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.history.append(_track("old"))
    voice = _FakeVoiceClient(playing=True)
    guild = _FakeGuild(1, voice)

    restarted = asyncio.run(cog._play_previous(guild))  # type: ignore[arg-type]

    assert restarted is True
    assert voice.stopped is True
    assert state.skip_requested is True
    assert state.queue
    assert state.queue[0].title == "current"


def test_previous_uses_history_when_elapsed_is_short() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    cog._pending_seek_seconds = {}
    cog._current_elapsed_seconds = lambda _guild_id: 2.0

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.history.append(_track("old"))
    voice = _FakeVoiceClient(playing=True)
    guild = _FakeGuild(1, voice)

    moved = asyncio.run(cog._play_previous(guild))  # type: ignore[arg-type]

    assert moved is True
    assert voice.stopped is True
    assert state.queue
    assert state.queue[0].title == "old"
    assert state.queue[1].title == "current"
