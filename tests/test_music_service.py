from __future__ import annotations

from jukkabot.music_service import MusicService


def test_entry_to_track_builds_track_from_flat_entry() -> None:
    entry = {
        "title": "Song",
        "url": "abc123",
        "uploader": "Artist",
        "duration": 200,
        "thumbnail": "https://img/x.jpg",
    }

    track = MusicService._entry_to_track(entry)

    assert track is not None
    assert track.title == "Song"
    assert track.url == "https://www.youtube.com/watch?v=abc123"
    assert track.author == "Artist"
    assert track.duration_seconds == 200
    assert track.thumbnail_url == "https://img/x.jpg"


def test_entry_to_track_keeps_full_urls_and_falls_back_to_channel() -> None:
    entry = {
        "title": "Song",
        "webpage_url": "https://www.youtube.com/watch?v=xyz",
        "channel": "Channel",
        "duration": "bad",
    }

    track = MusicService._entry_to_track(entry)

    assert track is not None
    assert track.url == "https://www.youtube.com/watch?v=xyz"
    assert track.author == "Channel"
    assert track.duration_seconds == 0


def test_entry_to_track_returns_none_for_invalid_entry() -> None:
    assert MusicService._entry_to_track({}) is None
    assert MusicService._entry_to_track("nope") is None
