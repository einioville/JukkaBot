from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from jukkabot.models import Track


@dataclass(slots=True)
class GuildQueue:
    queue: deque[Track] = field(default_factory=deque)
    history: deque[Track] = field(default_factory=lambda: deque(maxlen=50))
    banned_user_ids: set[int] = field(default_factory=set)
    current_track: Track | None = None
    voice_channel_id: int | None = None
    text_channel_id: int | None = None
    now_playing_message_id: int | None = None
    skip_requested: bool = False


class QueueManager:
    def __init__(self) -> None:
        self._guild_queues: dict[int, GuildQueue] = {}

    def get(self, guild_id: int) -> GuildQueue:
        if guild_id not in self._guild_queues:
            self._guild_queues[guild_id] = GuildQueue()
        return self._guild_queues[guild_id]

    def set_voice_channel(self, guild_id: int, channel_id: int) -> None:
        guild_queue = self.get(guild_id)
        guild_queue.voice_channel_id = channel_id

    def set_text_channel(self, guild_id: int, channel_id: int) -> None:
        guild_queue = self.get(guild_id)
        guild_queue.text_channel_id = channel_id

    def add_track(self, guild_id: int, track: Track) -> None:
        self.get(guild_id).queue.append(track)

    def queue_track(
        self,
        guild_id: int,
        track: Track,
        requested_by_user_id: int | None = None,
        requested_by_display_name: str | None = None,
    ) -> None:
        track.requested_by_user_id = requested_by_user_id
        track.requested_by_display_name = requested_by_display_name
        self.add_track(guild_id, track)

    def pop_next(self, guild_id: int) -> Track | None:
        guild_queue = self.get(guild_id)
        if not guild_queue.queue:
            guild_queue.current_track = None
            return None
        next_track = guild_queue.queue.popleft()
        guild_queue.current_track = next_track
        return next_track

    def skip_current(self, guild_id: int) -> Track | None:
        guild_queue = self.get(guild_id)
        if guild_queue.current_track:
            guild_queue.history.append(guild_queue.current_track)
        return self.pop_next(guild_id)

    def finish_current(self, guild_id: int, add_to_history: bool = True) -> Track | None:
        guild_queue = self.get(guild_id)
        track = guild_queue.current_track
        if track is not None and add_to_history:
            guild_queue.history.append(track)
        guild_queue.current_track = None
        return track

    def previous_track(self, guild_id: int) -> Track | None:
        guild_queue = self.get(guild_id)
        if not guild_queue.history:
            return None
        if guild_queue.current_track is not None:
            guild_queue.queue.appendleft(guild_queue.current_track)
        previous = guild_queue.history.pop()
        guild_queue.current_track = previous
        return previous

    def shuffle(self, guild_id: int) -> None:
        guild_queue = self.get(guild_id)
        items = list(guild_queue.queue)
        random.shuffle(items)
        guild_queue.queue = deque(items)

    def clear(self, guild_id: int) -> None:
        guild_queue = self.get(guild_id)
        guild_queue.queue.clear()
        guild_queue.history.clear()
        guild_queue.current_track = None
        guild_queue.voice_channel_id = None
        guild_queue.text_channel_id = None
        guild_queue.now_playing_message_id = None
        guild_queue.skip_requested = False
