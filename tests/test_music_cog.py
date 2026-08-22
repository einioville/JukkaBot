from __future__ import annotations

import asyncio
import io
import shlex
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from jukkabot.cogs.music import (
    AUTOCOMPLETE_DEADLINE_SECONDS,
    AUTOCOMPLETE_MIN_SEARCH_SECONDS,
    AUTOCOMPLETE_RESPONSE_MARGIN_SECONDS,
    FFMPEG_STDERR_TAIL_CHARS,
    PLAYBACK_STALL_SECONDS,
    MusicCog,
    StreamPlaybackError,
    _autocomplete_budget,
    _describe_ffmpeg_exit,
    _ffmpeg_header_args,
    _ffmpeg_stderr_tail,
    _format_up_next,
    _loop_status_label,
)
from jukkabot.models import Track
from jukkabot.music_service import StreamSource
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


def test_loop_status_label_reflects_active_mode() -> None:
    state = QueueManager().get(1)
    assert _loop_status_label(state) is None
    state.repeat_current = True
    assert _loop_status_label(state) == "Song Loop On"
    state.repeat_current = False
    state.repeat_queue = True
    assert _loop_status_label(state) == "Queue Loop On"


def test_format_up_next_caps_and_counts_remainder() -> None:
    tracks = [_track(f"t{i}") for i in range(5)]
    rendered = _format_up_next(tracks, max_items=3)
    assert rendered == "1. t0\n2. t1\n3. t2\n+2 more"


def test_format_up_next_unlimited_lists_every_track() -> None:
    tracks = [_track(f"t{i}") for i in range(5)]
    rendered = _format_up_next(tracks, max_items=None)
    assert rendered == "1. t0\n2. t1\n3. t2\n4. t3\n5. t4"


def test_format_up_next_respects_field_char_limit() -> None:
    tracks = [_track("x" * 90) for _ in range(40)]
    rendered = _format_up_next(tracks, max_items=None)
    assert len(rendered) <= 1024
    assert rendered.endswith("more")


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
    cog._stream_retries = {}

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


def test_after_track_finished_appends_current_to_queue_end_when_loop_queue_enabled() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.repeat_queue = True

    cog._playback_started_at = {}
    cog._paused_started_at = {}
    cog._paused_accumulated_seconds = {}
    cog._stream_retries = {}

    played_next: list[int] = []

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del fallback_channel_id
        played_next.append(target_guild.id)

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, None))

    assert state.current_track is None
    assert [track.title for track in state.queue] == ["next", "current"]
    assert list(state.history) == []
    assert played_next == [1]


def test_after_track_finished_keeps_current_in_ring_on_skip_with_loop_queue() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.repeat_queue = True
    state.skip_requested = True

    cog._playback_started_at = {}
    cog._paused_started_at = {}
    cog._paused_accumulated_seconds = {}
    cog._stream_retries = {}

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del target_guild, fallback_channel_id

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, None))

    # Skipping keeps the track in the loop ring: it goes to the back, next plays.
    assert [track.title for track in state.queue] == ["next", "current"]
    assert list(state.history) == []


def test_after_track_finished_restarts_current_on_skip_with_song_loop() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.repeat_current = True
    state.skip_requested = True

    cog._playback_started_at = {}
    cog._paused_started_at = {}
    cog._paused_accumulated_seconds = {}
    cog._stream_retries = {}

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del target_guild, fallback_channel_id

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, None))

    # Skipping a song-looped track restarts it: it stays on the front of the queue.
    assert [track.title for track in state.queue] == ["current", "next"]
    assert list(state.history) == []


def test_after_track_finished_drops_current_on_error_with_loop_queue() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("next"))
    state.repeat_queue = True

    cog._playback_started_at = {}
    cog._paused_started_at = {}
    cog._paused_accumulated_seconds = {}
    cog._stream_retries = {}

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del target_guild, fallback_channel_id

    cog._play_next = _fake_play_next  # type: ignore[assignment]

    asyncio.run(cog._after_track_finished(1, RuntimeError("boom")))

    # A failing track drops out of the ring so it can't loop forever.
    assert [track.title for track in state.queue] == ["next"]


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
    cog._stream_retries = {}
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


def test_build_queue_embed_lists_current_and_queued_tracks() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    state.queue.append(_track("first"))
    state.queue.append(_track("second"))

    embed = cog._build_queue_embed(state)

    assert embed.fields[0].name == "Now Playing"
    assert "current" in embed.fields[0].value
    assert "first" in embed.description
    assert "second" in embed.description
    assert embed.footer.text is not None
    assert "2 in queue" in embed.footer.text


def test_build_queue_embed_reports_empty_queue() -> None:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    state = cog.queue_manager.get(1)

    embed = cog._build_queue_embed(state)

    assert embed.description == "Queue is empty."


def test_is_playlist_url_detects_list_param_and_playlist_path() -> None:
    assert (
        MusicCog._is_playlist_url("https://www.youtube.com/watch?v=abc&list=PL123") is True
    )
    assert MusicCog._is_playlist_url("https://www.youtube.com/playlist?list=PL123") is True
    assert MusicCog._is_playlist_url("https://youtu.be/abc") is False
    assert MusicCog._is_playlist_url("just a search query") is False


def test_parse_timestamp_accepts_seconds_and_colon_forms() -> None:
    assert MusicCog._parse_timestamp("90") == 90
    assert MusicCog._parse_timestamp("1:30") == 90
    assert MusicCog._parse_timestamp("1:02:03") == 3723
    assert MusicCog._parse_timestamp("0") == 0


def test_parse_timestamp_rejects_invalid_values() -> None:
    assert MusicCog._parse_timestamp("") is None
    assert MusicCog._parse_timestamp("abc") is None
    assert MusicCog._parse_timestamp("-5") is None
    assert MusicCog._parse_timestamp("1:2:3:4") is None


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


def test_describe_ffmpeg_exit_decodes_http_error_tags() -> None:
    # FFmpeg exits with a negated AVERROR; 3436169992 is AVERROR_HTTP_FORBIDDEN.
    assert _describe_ffmpeg_exit(3436169992) == "HTTP 403 from the media host"
    assert _describe_ffmpeg_exit(3419392776) == "HTTP 404 from the media host"


def test_describe_ffmpeg_exit_falls_back_to_the_raw_code() -> None:
    assert _describe_ffmpeg_exit(1) == "exit code 1"
    assert _describe_ffmpeg_exit(4294967274) == "exit code 4294967274"


def test_ffmpeg_header_args_replay_yt_dlp_headers() -> None:
    stream = StreamSource(
        url="https://cdn/audio",
        headers={
            "User-Agent": "Chrome/1 (Windows NT 10.0; Win64)",
            "Accept": "*/*",
            "Range": "bytes=0-",
        },
    )

    args = shlex.split(_ffmpeg_header_args(stream))

    # User-Agent gets its dedicated flag, Range is left to FFmpeg, and the rest
    # is replayed verbatim as CRLF-delimited header lines.
    assert args[:2] == ["-user_agent", "Chrome/1 (Windows NT 10.0; Win64)"]
    assert args[2] == "-headers"
    assert args[3] == "Accept: */*\r\n"


def test_ffmpeg_header_args_are_empty_without_headers() -> None:
    assert _ffmpeg_header_args(StreamSource(url="https://cdn/audio")) == ""


def _cog_for_after_track(*, queued: list[str]) -> tuple[MusicCog, object]:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()

    voice = _FakeVoiceClient(playing=False, paused=False, connected=True)
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)

    state = cog.queue_manager.get(1)
    state.current_track = _track("current")
    for title in queued:
        state.queue.append(_track(title))

    cog._playback_started_at = {}
    cog._paused_started_at = {}
    cog._paused_accumulated_seconds = {}
    cog._stream_retries = {}

    async def _fake_play_next(
        target_guild: _FakeGuild, fallback_channel_id: int | None = None
    ) -> None:
        del target_guild, fallback_channel_id

    cog._play_next = _fake_play_next  # type: ignore[assignment]
    return cog, state


def test_after_track_finished_retries_a_dead_stream_once() -> None:
    cog, state = _cog_for_after_track(queued=["next"])

    asyncio.run(cog._after_track_finished(1, StreamPlaybackError("403")))

    # Re-resolving mints a fresh URL, so the track goes back to the front rather
    # than being dropped, and it stays out of history until it actually plays.
    assert [track.title for track in state.queue] == ["current", "next"]
    assert list(state.history) == []

    # Second failure exhausts the budget: the track is dropped so the queue moves on.
    state.current_track = state.queue.popleft()
    asyncio.run(cog._after_track_finished(1, StreamPlaybackError("403")))

    assert [track.title for track in state.queue] == ["next"]
    assert [track.title for track in state.history] == ["current"]
    assert cog._stream_retries == {}


def test_after_track_finished_does_not_retry_a_skipped_track() -> None:
    cog, state = _cog_for_after_track(queued=["next"])
    # Stopping the player kills FFmpeg too, which must not read as a failure.
    state.skip_requested = True

    asyncio.run(cog._after_track_finished(1, StreamPlaybackError("killed")))

    assert [track.title for track in state.queue] == ["next"]
    assert cog._stream_retries == {}


def test_after_track_finished_clears_the_retry_budget_on_a_clean_finish() -> None:
    cog, state = _cog_for_after_track(queued=["next"])
    cog._stream_retries = {1: ("https://youtu.be/current", 1)}

    asyncio.run(cog._after_track_finished(1, None))

    assert cog._stream_retries == {}
    assert [track.title for track in state.history] == ["current"]


class _FakeAutocompleteResponse:
    """Stands in for ``InteractionResponse`` for the autocomplete tests."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.calls: list[list[object]] = []
        self._response_type: object | None = None

    async def autocomplete(self, choices) -> None:  # noqa: ANN001
        self.calls.append(list(choices))
        if self.fails:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10062, "message": "Unknown interaction"},
            )
        self._response_type = discord.InteractionResponseType.autocomplete_result

    def is_done(self) -> bool:
        return self._response_type is not None


class _FakeAutocompleteInteraction:
    def __init__(self, *, age_seconds: float = 0.0, fails: bool = False) -> None:
        self.guild_id = 1
        self.user = _FakeUser()
        self.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        self.response = _FakeAutocompleteResponse(fails=fails)


def _autocomplete_cog(search) -> MusicCog:  # noqa: ANN001
    cog = MusicCog.__new__(MusicCog)
    cog._autocomplete_request_seq = {}
    cog._autocomplete_request_seen_at = {}
    cog.music_service = SimpleNamespace(search=search)
    return cog


def test_autocomplete_budget_shrinks_as_the_interaction_ages() -> None:
    usable = AUTOCOMPLETE_DEADLINE_SECONDS - AUTOCOMPLETE_RESPONSE_MARGIN_SECONDS

    fresh = _autocomplete_budget(_FakeAutocompleteInteraction())
    assert fresh == pytest.approx(usable, abs=0.05)

    aged = _autocomplete_budget(_FakeAutocompleteInteraction(age_seconds=1.0))
    assert aged == pytest.approx(usable - 1.0, abs=0.05)


def test_autocomplete_budget_clamps_a_skewed_clock() -> None:
    usable = AUTOCOMPLETE_DEADLINE_SECONDS - AUTOCOMPLETE_RESPONSE_MARGIN_SECONDS
    # Local clock behind Discord's must not hand out more than the real window.
    assert _autocomplete_budget(_FakeAutocompleteInteraction(age_seconds=-30.0)) == usable
    # And an expired interaction bottoms out at zero rather than going negative.
    assert _autocomplete_budget(_FakeAutocompleteInteraction(age_seconds=30.0)) == 0.0


def test_play_autocomplete_answers_the_interaction_itself() -> None:
    cog = _autocomplete_cog(lambda query: [_track("hit")])
    interaction = _FakeAutocompleteInteraction()

    returned = asyncio.run(cog.play_autocomplete(interaction, "query"))  # type: ignore[arg-type]

    # Answering here is what stops discord.py from answering a second time.
    assert interaction.response.is_done() is True
    assert [choice.name for choice in interaction.response.calls[0]] == ["hit - tester"]
    # discord.py only re-sends what we return, so it has to be empty.
    assert returned == []


def test_play_autocomplete_swallows_an_expired_interaction() -> None:
    cog = _autocomplete_cog(lambda query: [_track("hit")])
    interaction = _FakeAutocompleteInteraction(fails=True)

    returned = asyncio.run(cog.play_autocomplete(interaction, "query"))  # type: ignore[arg-type]

    assert returned == []
    # Marked done by hand, or discord.py repeats the call we watched 404.
    assert interaction.response.is_done() is True


def test_play_autocomplete_skips_the_search_when_the_window_is_spent() -> None:
    searched: list[str] = []

    def search(query: str) -> list[Track]:
        searched.append(query)
        return [_track("hit")]

    cog = _autocomplete_cog(search)
    # Enough budget left to answer, but not enough to finish a ~1.2s search.
    interaction = _FakeAutocompleteInteraction(age_seconds=1.5)
    assert 0 < _autocomplete_budget(interaction) < AUTOCOMPLETE_MIN_SEARCH_SECONDS

    asyncio.run(cog.play_autocomplete(interaction, "query"))  # type: ignore[arg-type]

    assert searched == []
    assert interaction.response.calls == [[]]


def test_play_autocomplete_gives_up_on_a_search_that_outruns_the_window() -> None:
    def slow_search(query: str) -> list[Track]:
        time.sleep(5)
        return [_track("hit")]

    cog = _autocomplete_cog(slow_search)
    # Aged so the remaining budget is short enough to keep the test quick.
    interaction = _FakeAutocompleteInteraction(age_seconds=1.0)
    started = time.monotonic()

    asyncio.run(cog.play_autocomplete(interaction, "query"))  # type: ignore[arg-type]

    assert time.monotonic() - started < AUTOCOMPLETE_DEADLINE_SECONDS
    assert interaction.response.calls == [[]]


def test_play_autocomplete_drops_a_query_a_newer_keystroke_replaced() -> None:
    cog = _autocomplete_cog(lambda query: [_track("hit")])

    async def scenario() -> list[object]:
        stale = _FakeAutocompleteInteraction()
        task = asyncio.ensure_future(cog.play_autocomplete(stale, "quer"))  # type: ignore[arg-type]
        await asyncio.sleep(0)
        # A newer keystroke from the same user bumps the sequence number.
        await cog.play_autocomplete(_FakeAutocompleteInteraction(), "query")  # type: ignore[arg-type]
        await task
        return stale.response.calls

    assert asyncio.run(scenario()) == [[]]


class _FakeFFmpegProcess:
    """Stand-in for FFmpeg's Popen: alive until killed, then a non-zero exit."""

    def __init__(self, *, returncode: int | None = None) -> None:
        self._returncode = returncode
        self.kills = 0

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self.kills += 1
        self._returncode = 1


class _FakeAudioPlayer:
    """Stand-in for discord.py's AudioPlayer thread."""

    def __init__(self, *, loops: int = 0, alive: bool = True, process=None) -> None:  # noqa: ANN001
        self.loops = loops
        self._alive = alive
        self.source = SimpleNamespace(_process=process)

    def is_alive(self) -> bool:
        return self._alive


def _watchdog_cog(
    voice: _FakeVoiceClient, player: _FakeAudioPlayer | None
) -> tuple[MusicCog, _FakeGuild]:
    cog = MusicCog.__new__(MusicCog)
    cog.queue_manager = QueueManager()
    guild = _FakeGuild(1, voice)
    cog.bot = _FakeBot(guild)
    voice._player = player  # type: ignore[attr-defined]
    cog._playback_progress = {}
    cog._stream_stderr = {}
    cog.queue_manager.get(1).current_track = _track("stuck")
    return cog, guild


def test_watchdog_kills_ffmpeg_when_frames_stop_advancing() -> None:
    process = _FakeFFmpegProcess()
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=True, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    # First observation only records the frame count; the stall is measured from
    # there, so nothing is killed until the count sits still past the threshold.
    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    assert process.kills == 0

    asyncio.run(
        cog._check_playback_progress(guild, PLAYBACK_STALL_SECONDS + 1.0)  # type: ignore[arg-type]
    )
    assert process.kills == 1


def test_watchdog_leaves_advancing_playback_alone() -> None:
    process = _FakeFFmpegProcess()
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=True, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    now = 0.0
    for _ in range(10):
        asyncio.run(cog._check_playback_progress(guild, now))  # type: ignore[arg-type]
        player.loops += 750  # 15s of 20ms frames
        now += PLAYBACK_STALL_SECONDS

    assert process.kills == 0


def test_watchdog_fires_after_a_skip_left_the_thread_wedged() -> None:
    """The regression: voice.stop() clears is_playing() but cannot free the thread.

    A stall that follows a skip reads as neither playing nor paused, so the
    watchdog must key off the live player thread rather than is_playing().
    """
    process = _FakeFFmpegProcess()
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=False, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    asyncio.run(
        cog._check_playback_progress(guild, PLAYBACK_STALL_SECONDS + 1.0)  # type: ignore[arg-type]
    )

    assert process.kills == 1


def test_watchdog_ignores_a_paused_player() -> None:
    process = _FakeFFmpegProcess()
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=False, paused=True)
    cog, guild = _watchdog_cog(voice, player)

    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    asyncio.run(
        cog._check_playback_progress(guild, PLAYBACK_STALL_SECONDS + 1.0)  # type: ignore[arg-type]
    )

    assert process.kills == 0
    # No window is carried across a pause, so resuming starts fresh.
    assert 1 not in cog._playback_progress


def test_watchdog_ignores_ffmpeg_that_already_exited() -> None:
    # stdout hits EOF on the next read, so the thread frees itself.
    process = _FakeFFmpegProcess(returncode=0)
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=True, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    asyncio.run(
        cog._check_playback_progress(guild, PLAYBACK_STALL_SECONDS + 1.0)  # type: ignore[arg-type]
    )

    assert process.kills == 0


def test_watchdog_ignores_a_finished_player_thread() -> None:
    process = _FakeFFmpegProcess()
    player = _FakeAudioPlayer(loops=5000, alive=False, process=process)
    voice = _FakeVoiceClient(playing=True, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    asyncio.run(
        cog._check_playback_progress(guild, PLAYBACK_STALL_SECONDS + 1.0)  # type: ignore[arg-type]
    )

    assert process.kills == 0


def test_watchdog_restarts_its_window_after_a_kill() -> None:
    """A kill that doesn't land gets another full window, not an error a tick."""
    process = _FakeFFmpegProcess()
    process.kill = lambda: None  # type: ignore[method-assign]
    player = _FakeAudioPlayer(loops=5000, process=process)
    voice = _FakeVoiceClient(playing=True, paused=False)
    cog, guild = _watchdog_cog(voice, player)

    asyncio.run(cog._check_playback_progress(guild, 0.0))  # type: ignore[arg-type]
    stalled_at = PLAYBACK_STALL_SECONDS + 1.0
    asyncio.run(cog._check_playback_progress(guild, stalled_at))  # type: ignore[arg-type]

    assert cog._playback_progress[1] == (5000, stalled_at)


def test_ffmpeg_stderr_tail_collapses_whitespace_and_keeps_the_end() -> None:
    assert _ffmpeg_stderr_tail(None) == ""
    assert _ffmpeg_stderr_tail(io.BytesIO(b"")) == ""
    assert _ffmpeg_stderr_tail(io.BytesIO(b"  http:  \n  403 forbidden\n")) == (
        "http: 403 forbidden"
    )
    long = io.BytesIO(b"x" * (FFMPEG_STDERR_TAIL_CHARS + 50) + b"tail")
    assert _ffmpeg_stderr_tail(long).endswith("tail")
    assert len(_ffmpeg_stderr_tail(long)) == FFMPEG_STDERR_TAIL_CHARS
