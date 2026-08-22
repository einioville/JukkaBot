from __future__ import annotations

from jukkabot.music_service import MusicService, StreamSource


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


def test_stream_options_target_audio_only_and_keep_dash_formats() -> None:
    # Guards the streaming-quality regression: forcing android/ios clients or
    # skipping DASH makes yt-dlp fall back to the muxed 360p stream (itag 18,
    # AAC) instead of the audio-only Opus stream (itag 251, 48 kHz).
    opts = MusicService._stream_ydl_options()

    extractor_args = opts.get("extractor_args", {})
    youtube_args = extractor_args.get("youtube", {}) if isinstance(extractor_args, dict) else {}
    assert "dash" not in youtube_args.get("skip", [])
    assert "hls" not in youtube_args.get("skip", [])

    fmt = opts["format"]
    assert isinstance(fmt, str) and fmt.startswith("bestaudio")
    assert opts["noplaylist"] is True


def test_stream_headers_prefer_the_selected_format() -> None:
    # The CDN validates headers against the client that minted the URL, so the
    # format's own headers win over the generic top-level ones.
    info = {
        "http_headers": {"User-Agent": "desktop-chrome"},
        "formats": [
            {"url": "https://cdn/other", "http_headers": {"User-Agent": "wrong"}},
            {"url": "https://cdn/audio", "http_headers": {"User-Agent": "android-vr"}},
        ],
    }

    headers = MusicService._stream_headers(info, "https://cdn/audio")

    assert headers == {"User-Agent": "android-vr"}


def test_stream_headers_fall_back_to_top_level_and_drop_blanks() -> None:
    info = {"http_headers": {"User-Agent": "ua", "Accept": "", "Referer": None}}

    headers = MusicService._stream_headers(info, "https://cdn/audio")

    assert headers == {"User-Agent": "ua"}


def test_stream_source_exposes_user_agent_from_headers() -> None:
    assert StreamSource(url="https://cdn/a", headers={"User-Agent": "ua"}).user_agent == "ua"
    assert StreamSource(url="https://cdn/a").user_agent is None
