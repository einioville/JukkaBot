from __future__ import annotations

from dataclasses import dataclass

from yt_dlp import YoutubeDL

from jukkabot.models import Track


@dataclass(frozen=True, slots=True)
class StreamSource:
    url: str
    user_agent: str | None = None


class MusicService:
    def __init__(self) -> None:
        self._ydl_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "default_search": "ytsearch5",
            "noplaylist": True,
        }

    def search(self, query: str) -> list[Track]:
        if not query.strip():
            return []

        with YoutubeDL(self._ydl_options) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)

        entries = info.get("entries", []) if info else []
        results: list[Track] = []
        for entry in entries:
            url = entry.get("url") or ""
            if not url:
                continue
            if not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            results.append(
                Track(
                    title=entry.get("title") or "Unknown title",
                    url=url,
                    author=entry.get("uploader") or "Unknown author",
                    duration_seconds=int(entry.get("duration") or 0),
                    thumbnail_url=entry.get("thumbnail"),
                )
            )
        return results

    def get_track(self, video_url: str) -> Track:
        if not video_url.strip():
            raise RuntimeError("Track URL is empty.")

        info_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        with YoutubeDL(info_options) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if not info:
            raise RuntimeError("No track information returned.")

        if isinstance(info, dict):
            entries = info.get("entries")
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict):
                    info = first

        if not isinstance(info, dict):
            raise RuntimeError("Invalid track information.")

        resolved_url = (
            info.get("webpage_url")
            or info.get("original_url")
            or info.get("url")
            or video_url
        )
        if not isinstance(resolved_url, str) or not resolved_url.strip():
            resolved_url = video_url
        if not resolved_url.startswith("http"):
            resolved_url = f"https://www.youtube.com/watch?v={resolved_url}"

        raw_duration = info.get("duration") or 0
        try:
            duration_seconds = int(raw_duration)
        except (TypeError, ValueError):
            duration_seconds = 0

        return Track(
            title=info.get("title") or "Unknown title",
            url=resolved_url,
            author=info.get("uploader") or info.get("channel") or "Unknown author",
            duration_seconds=duration_seconds,
            thumbnail_url=info.get("thumbnail"),
        )

    def get_stream_source(self, video_url: str) -> StreamSource:
        stream_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "noplaylist": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "tv"],
                    "skip": ["hls", "dash"],
                }
            },
        }
        with YoutubeDL(stream_options) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if not info:
            raise RuntimeError("No stream information returned.")

        headers = info.get("http_headers") or {}
        user_agent = headers.get("User-Agent")
        direct_url = info.get("url")
        if direct_url:
            return StreamSource(url=direct_url, user_agent=user_agent)

        formats = info.get("formats") or []
        for fmt in reversed(formats):
            candidate = fmt.get("url")
            if candidate and fmt.get("acodec") not in (None, "none"):
                fmt_headers = fmt.get("http_headers") or headers
                return StreamSource(
                    url=candidate,
                    user_agent=fmt_headers.get("User-Agent"),
                )
        raise RuntimeError("Could not resolve an audio stream URL.")
