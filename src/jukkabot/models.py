from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Track:
    title: str
    url: str
    author: str
    duration_seconds: int
    thumbnail_url: str | None = None
    requested_by_user_id: int | None = None
    requested_by_display_name: str | None = None

    @property
    def duration_label(self) -> str:
        minutes, seconds = divmod(self.duration_seconds, 60)
        return f"{minutes}:{seconds:02d}"
