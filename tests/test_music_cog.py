from __future__ import annotations

import asyncio

from jukkabot.cogs.music import MusicCog
from jukkabot.models import Track
from jukkabot.queue_manager import QueueManager


class _FakeVoiceClient:
    def __init__(
        self, *, playing: bool = True, paused: bool = False, connected: bool = True
    ) -> None:
        self._playing = playing
        self._paused = paused
        self._connected = connected
        self.stopped = False

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def is_connected(self) -> bool:
        return self._connected

    def stop(self) -> None:
        self.stopped = True
        self._playing = False
        self._paused = False


class _FakeGuild:
    def __init__(self, guild_id: int, voice_client: _FakeVoiceClient) -> None:
        self.id = guild_id
        self.voice_client = voice_client


class _FakeBot:
    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        if guild_id == self._guild.id:
            return self._guild
        return None


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


def test_after_track_finished_requeues_current_when_repeat_is_enabled() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.repeat_current = True

    cog._playback_started_at = {1: 1.0}
    cog._paused_started_at = {1: 2.0}
    cog._paused_accumulated_seconds = {1: 3.0}

    played_next: list[int] = []

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del fallback_channel_id
        played_next.append(target_guild.id)

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, None))

    assert state.current_track is None
    assert state.skip_requested is False
    assert [track.title for track in state.queue] == ["current", "next"]
    assert list(state.history) == []
    assert 1 not in cog._playback_started_at
    assert 1 not in cog._paused_started_at
    assert 1 not in cog._paused_accumulated_seconds
    assert played_next == [1]


def test_play_next_deletes_now_playing_message_when_queue_runs_empty() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)

    deleted_for: list[tuple[int, int | None]] = []
    touched: list[int] = []

    async def _fake_delete_now_playing_message(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        deleted_for.append((target_guild.id, fallback_channel_id))

    async def _fake_cleanup_after_disconnect(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del target_guild, fallback_channel_id
        raise AssertionError("Disconnected cleanup should not run for connected voice")

    cog._delete_now_playing_message = _fake_delete_now_playing_message  # type: ignore[assignment]
    cog._cleanup_after_disconnect = _fake_cleanup_after_disconnect  # type: ignore[assignment]
    cog._resolve_announce_channel = lambda _guild, _channel_id: None  # type: ignore[assignment]
    cog._touch_activity = lambda guild_id: touched.append(guild_id)  # type: ignore[assignment]

    asyncio.run(cog._play_next(guild, fallback_channel_id=123))  # type: ignore[arg-type]

    assert deleted_for == [(1, 123)]
    assert touched == [1]
