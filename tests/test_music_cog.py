from __future__ import annotations

import asyncio

from jukkabot.cogs.music import MusicCog
from jukkabot.models import Track
from jukkabot.queue_manager import QueueManager


class _FakeVoiceClient:
    def __init__(
        self,
        *,
        playing: bool = True,
        paused: bool = False,
        connected: bool = True,
        channel: object | None = None,
    ) -> None:
        self._playing = playing
        self._paused = paused
        self._connected = connected
        self.channel = channel
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

    async def disconnect(self, force: bool = False) -> None:
        del force
        self._connected = False
        self.channel = None


class _FakeGuild:
    def __init__(
        self,
        guild_id: int,
        voice_client: _FakeVoiceClient,
        channels: dict[int, object] | None = None,
    ) -> None:
        self.id = guild_id
        self.name = f"guild-{guild_id}"
        self.voice_client = voice_client
        self._channels = channels or {}

    def get_channel(self, channel_id: int) -> object | None:
        return self._channels.get(channel_id)


class _FakeStoredMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.deleted = False
        self.edited = False

    async def delete(self) -> None:
        self.deleted = True

    async def edit(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs
        self.edited = True


class _FakeTextChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: dict[int, _FakeStoredMessage] = {}
        self._next_message_id = 1000

    def seed_message(self, message_id: int) -> _FakeStoredMessage:
        message = _FakeStoredMessage(message_id)
        self.messages[message_id] = message
        return message

    def get_partial_message(self, message_id: int) -> _FakeStoredMessage:
        return self.messages.setdefault(message_id, _FakeStoredMessage(message_id))

    async def send(self, embed=None, view=None) -> _FakeStoredMessage:  # noqa: ANN001
        del embed, view
        self._next_message_id += 1
        message = _FakeStoredMessage(self._next_message_id)
        self.messages[message.id] = message
        return message


class _FakeBot:
    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild
        self.guilds = [guild]
        self.presence_updates: list[object] = []

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        if guild_id == self._guild.id:
            return self._guild
        return None

    async def change_presence(self, *, activity=None) -> None:  # noqa: ANN001
        self.presence_updates.append(activity)


class _FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append((content, ephemeral))


class _FakeUser:
    def __init__(self) -> None:
        self.id = 123
        self.display_name = "tester"
        self.name = "tester"


class _FakeInteraction:
    def __init__(self, guild: _FakeGuild) -> None:
        self.guild = guild
        self.user = _FakeUser()
        self.response = _FakeResponse()


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


def test_previous_fallback_clears_skip_requested_before_manual_advance() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    cog._pending_seek_seconds = {}
    cog._current_elapsed_seconds = lambda _guild_id: 2.0

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.history.append(_track("old"))
    voice = _FakeVoiceClient(playing=False, paused=False)
    guild = _FakeGuild(1, voice)
    skip_state_seen_in_play_next: list[bool] = []

    async def _fake_play_next(_guild: _FakeGuild, fallback_channel_id: int | None = None) -> None:
        del _guild, fallback_channel_id
        skip_state_seen_in_play_next.append(state.skip_requested)

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    moved = asyncio.run(cog._play_previous(guild))  # type: ignore[arg-type]

    assert moved is True
    assert skip_state_seen_in_play_next == [False]
    assert state.skip_requested is False


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


def test_send_now_playing_moves_message_to_new_channel_and_deletes_old() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    state = cog.queue_manager.get(1)
    state.current_track = _track("current")

    old_channel = _FakeTextChannel(11)
    old_message = old_channel.seed_message(900)
    new_channel = _FakeTextChannel(22)
    voice = _FakeVoiceClient(playing=True, paused=False, connected=True)
    guild = _FakeGuild(1, voice, channels={11: old_channel, 22: new_channel})

    state.now_playing_message_id = 900
    state.now_playing_channel_id = 11

    asyncio.run(
        cog._send_now_playing(
            guild, new_channel, _track("new"), edit_existing=True
        )  # type: ignore[arg-type]
    )

    assert old_message.deleted is True
    assert state.now_playing_channel_id == 22
    assert state.now_playing_message_id is not None
    assert state.now_playing_message_id in new_channel.messages


def test_delete_now_playing_prefers_stored_message_channel_over_fallback() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    state = cog.queue_manager.get(1)

    old_channel = _FakeTextChannel(11)
    old_message = old_channel.seed_message(901)
    fallback_channel = _FakeTextChannel(33)
    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice, channels={11: old_channel, 33: fallback_channel})

    state.now_playing_message_id = 901
    state.now_playing_channel_id = 11
    cog._resolve_announce_channel = lambda _guild, _channel_id: fallback_channel  # type: ignore[assignment]

    asyncio.run(cog._delete_now_playing_message(guild, fallback_channel_id=33))  # type: ignore[arg-type]

    assert old_message.deleted is True
    assert state.now_playing_message_id is None
    assert state.now_playing_channel_id is None


def test_after_track_finished_suppresses_auto_advance_when_clear_is_pending() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    cog._playback_started_at = {1: 1.0}
    cog._paused_started_at = {1: 2.0}
    cog._paused_accumulated_seconds = {1: 3.0}
    cog._pending_seek_seconds = {}
    cog._last_presence_text = None

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    bot = _FakeBot(guild)
    cog.bot = bot

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.clear_requested = True

    played_next: list[int] = []

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del fallback_channel_id
        played_next.append(target_guild.id)

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, None))

    assert state.current_track is None
    assert [track.title for track in state.queue] == ["next"]
    assert played_next == []
    assert bot.presence_updates


def test_validate_channel_access_allows_when_voice_client_has_no_channel() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True, channel=None)
    guild = _FakeGuild(1, voice)
    interaction = _FakeInteraction(guild)
    user_channel = object()
    cog._current_voice_channel = lambda _interaction: user_channel  # type: ignore[assignment]

    is_allowed, returned_channel = asyncio.run(
        cog._validate_channel_access(interaction)  # type: ignore[arg-type]
    )

    assert is_allowed is True
    assert returned_channel is user_channel
    assert interaction.response.messages == []


def test_prune_autocomplete_request_state_removes_stale_entries() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog._autocomplete_request_seq = {
        (1, 11): 1,
        (1, 12): 2,
        (2, 21): 3,
    }
    cog._autocomplete_request_seen_at = {
        (1, 11): 1.0,
        (1, 12): 1000.0,
        (2, 21): 1000.0,
    }

    cog._prune_autocomplete_request_state(now=1000.0 + 899.0)

    assert (1, 11) not in cog._autocomplete_request_seq
    assert (1, 11) not in cog._autocomplete_request_seen_at
    assert (1, 12) in cog._autocomplete_request_seq
    assert (2, 21) in cog._autocomplete_request_seq
