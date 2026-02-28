from jukkabot.models import Track
from jukkabot.queue_manager import QueueManager


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
    manager.add_track(1, make_track("song"))
    manager.pop_next(1)

    manager.clear(1)

    cleared = manager.get(1)
    assert list(cleared.queue) == []
    assert cleared.current_track is None
    assert cleared.active_filter_preset == "edm"
    assert cleared.active_audio_filter == "bass=g=7:f=95:w=0.7,treble=g=4:f=4500:w=0.6"
