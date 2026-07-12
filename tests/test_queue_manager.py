from jukkabot.models import Track
from jukkabot.queue_manager import QueueManager, up_next_tracks


def make_track(name: str) -> Track:
    return Track(title=name, url=f"https://example.com/{name}", author="tester", duration_seconds=60)


def test_queue_is_per_guild() -> None:
    manager = QueueManager()
    manager.add_track(1, make_track("a"))
    manager.add_track(2, make_track("b"))

    assert manager.pop_next(1).title == "a"
    assert manager.pop_next(2).title == "b"


def test_skip_moves_current_to_history() -> None:
    manager = QueueManager()
    manager.add_track(1, make_track("first"))
    manager.add_track(1, make_track("second"))
    assert manager.pop_next(1).title == "first"

    next_track = manager.skip_current(1)

    assert next_track is not None
    assert next_track.title == "second"
    assert manager.get(1).history[-1].title == "first"


def test_previous_restores_last_track() -> None:
    manager = QueueManager()
    manager.add_track(1, make_track("one"))
    manager.add_track(1, make_track("two"))
    assert manager.pop_next(1).title == "one"
    assert manager.skip_current(1).title == "two"

    previous = manager.previous_track(1)

    assert previous is not None
    assert previous.title == "one"
    assert manager.get(1).queue[0].title == "two"


def test_queue_track_stores_requester() -> None:
    manager = QueueManager()
    track = make_track("requested")

    manager.queue_track(
        1,
        track,
        requested_by_user_id=12345,
        requested_by_display_name="ville",
    )

    queued = manager.pop_next(1)
    assert queued is not None
    assert queued.requested_by_user_id == 12345
    assert queued.requested_by_display_name == "ville"


def test_clear_preserves_filter_state_by_default() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.active_filter_preset = "edm"
    state.active_audio_filter = "bass=g=7:f=95:w=0.7,treble=g=4:f=4500:w=0.6"
    state.now_playing_channel_id = 123
    state.repeat_current = True
    manager.add_track(1, make_track("song"))
    manager.pop_next(1)

    manager.clear(1)

    cleared = manager.get(1)
    assert list(cleared.queue) == []
    assert cleared.current_track is None
    assert cleared.active_filter_preset == "edm"
    assert cleared.active_audio_filter == "bass=g=7:f=95:w=0.7,treble=g=4:f=4500:w=0.6"
    assert cleared.now_playing_channel_id is None
    assert cleared.repeat_current is False


def test_remove_at_removes_track_by_index() -> None:
    manager = QueueManager()
    manager.add_track(1, make_track("a"))
    manager.add_track(1, make_track("b"))
    manager.add_track(1, make_track("c"))

    removed = manager.remove_at(1, 1)

    assert removed is not None
    assert removed.title == "b"
    assert [track.title for track in manager.get(1).queue] == ["a", "c"]


def test_remove_at_returns_none_for_out_of_range_index() -> None:
    manager = QueueManager()
    manager.add_track(1, make_track("a"))

    assert manager.remove_at(1, 5) is None
    assert manager.remove_at(1, -1) is None
    assert [track.title for track in manager.get(1).queue] == ["a"]


def test_clear_resets_repeat_queue() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.repeat_queue = True

    manager.clear(1)

    assert manager.get(1).repeat_queue is False


def test_up_next_without_loop_is_the_queue_only() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.current_track = make_track("current")
    state.queue.append(make_track("b"))
    state.queue.append(make_track("c"))

    assert [t.title for t in up_next_tracks(state)] == ["b", "c"]


def test_up_next_song_loop_puts_current_on_top() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.current_track = make_track("current")
    state.queue.append(make_track("b"))
    state.repeat_current = True

    assert [t.title for t in up_next_tracks(state)] == ["current", "b"]


def test_up_next_queue_loop_lists_full_ring_in_play_order() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.current_track = make_track("a")
    state.queue.append(make_track("b"))
    state.queue.append(make_track("c"))
    state.repeat_queue = True

    # After the current "a", the queue plays, then "a" cycles back to the end.
    assert [t.title for t in up_next_tracks(state)] == ["b", "c", "a"]


def test_up_next_queue_loop_with_single_track_repeats_it() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.current_track = make_track("only")
    state.repeat_queue = True

    assert [t.title for t in up_next_tracks(state)] == ["only"]


def test_up_next_with_no_current_track_is_the_queue() -> None:
    manager = QueueManager()
    state = manager.get(1)
    state.queue.append(make_track("b"))
    state.repeat_queue = True

    assert [t.title for t in up_next_tracks(state)] == ["b"]
