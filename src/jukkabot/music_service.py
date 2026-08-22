from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from yt_dlp import YoutubeDL

from jukkabot.models import Track


@dataclass(frozen=True, slots=True)
class StreamSource:
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def user_agent(self) -> str | None:
        return self.headers.get("User-Agent")


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

    @staticmethod
    def _stream_ydl_options() -> dict[str, object]:
        """Options for resolving the actual audio stream to hand to FFmpeg.

        We deliberately do NOT pin ``player_client`` or ``skip`` dash/hls here:
        YouTube's highest-quality audio is the Opus stream (itag 251, webm/opus,
        48 kHz) which is a DASH *adaptive* format and matches Discord's native
        codec. Skipping DASH or forcing the android/ios clients makes yt-dlp fall
        back to the muxed 360p progressive stream (itag 18, AAC 44.1 kHz) and
        pull video bytes we immediately throw away. Letting yt-dlp pick the
        defaults resolves the audio-only Opus stream.
        """
        return {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
            "noplaylist": True,
        }

    @staticmethod
    def _entry_to_track(entry: object) -> Track | None:
        if not isinstance(entry, dict):
            return None
        url = entry.get("url") or entry.get("webpage_url") or entry.get("id") or ""
        if not isinstance(url, str) or not url:
            return None
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        raw_duration = entry.get("duration") or 0
        try:
            duration_seconds = int(raw_duration)
        except (TypeError, ValueError):
            duration_seconds = 0
        return Track(
            title=entry.get("title") or "Unknown title",
            url=url,
            author=entry.get("uploader") or entry.get("channel") or "Unknown author",
            duration_seconds=duration_seconds,
            thumbnail_url=entry.get("thumbnail"),
        )

    def search(self, query: str) -> list[Track]:
        if not query.strip():
            return []

        with YoutubeDL(self._ydl_options) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)

        entries = info.get("entries", []) if info else []
        results: list[Track] = []
        for entry in entries:
            track = self._entry_to_track(entry)
            if track is not None:
                results.append(track)
        return results

    def get_playlist(self, url: str) -> list[Track]:
        if not url.strip():
            return []

        playlist_options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": False,
        }
        with YoutubeDL(playlist_options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            return []

        entries = info.get("entries")
        if not isinstance(entries, list):
            single = self._entry_to_track(info)
            return [single] if single is not None else []

        tracks: list[Track] = []
        for entry in entries:
            track = self._entry_to_track(entry)
            if track is not None:
                tracks.append(track)
        return tracks

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

    @staticmethod
    def _stream_headers(
        info: dict, url: str, fmt: dict | None = None
    ) -> dict[str, str]:
        """Headers FFmpeg has to replay to fetch ``url``.

        A googlevideo URL is bound to the player client that minted it (see the
        ``c=`` query param), and the CDN validates the request headers against
        it. yt-dlp records the matching set on the *format*, so prefer those over
        the top-level ones and forward all of them -- handing FFmpeg a partial or
        mismatched set gets a 403 either immediately or partway through playback.
        """
        source = fmt
        if source is None:
            for candidate in info.get("formats") or []:
                if isinstance(candidate, dict) and candidate.get("url") == url:
                    source = candidate
                    break
        raw = (source or {}).get("http_headers") or info.get("http_headers") or {}
        return {str(k): str(v) for k, v in raw.items() if k and v}

    def get_stream_source(self, video_url: str) -> StreamSource:
        stream_options = self._stream_ydl_options()
        with YoutubeDL(stream_options) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if not info:
            raise RuntimeError("No stream information returned.")

        direct_url = info.get("url")
        if direct_url:
            return StreamSource(
                url=direct_url, headers=self._stream_headers(info, direct_url)
            )

        formats = info.get("formats") or []
        for fmt in reversed(formats):
            candidate = fmt.get("url")
            if candidate and fmt.get("acodec") not in (None, "none"):
                return StreamSource(
                    url=candidate,
                    headers=self._stream_headers(info, candidate, fmt),
                )
        raise RuntimeError("Could not resolve an audio stream URL.")
