# JukkaBot

> Note: This is a personal learning project for experimenting with Discord bot development.

A Discord music bot that streams audio from YouTube, built with Python, `discord.py`, `yt-dlp`, and FFmpeg. All playback state (queue, history, moderation, active filter) is tracked per server.

## Features
- Slash commands:
  - `/join`: join your voice channel
  - `/play`: search and queue a track, or queue a whole playlist from a playlist URL (capped at 100 tracks)
  - `/skip`: skip current track
  - `/pause`: pause/resume playback
  - `/queue`: show the current track and everything queued (ephemeral)
  - `/loop`: set loop mode (`off`, `track`, `queue`)
  - `/seek`: jump to a position in the current track (`90` or `1:30`)
  - `/filter`: apply audio filter preset (autocomplete)
  - `/bass`: apply bass boost filter with level control (`0..20`)
  - `/remove`: remove a track from the queue (autocomplete lists queued tracks)
  - `/clear`: clear queue and delete now-playing message
  - `/leave`: disconnect and clear queue
  - `/banuser`: ban user from queueing/skipping
  - `/unbanuser`: remove queue/skip ban
- Per-server queue, history, and moderation state.
- Tracks store who queued them.
- `/play` autocomplete uses trailing debounce (500 ms) and does not cache results.
- Autocomplete selections for `/play` carry the exact track URL value so selected suggestions resolve to the intended track.
- Now-playing embed includes:
  - playing/paused status
  - title, author, length, queued-by
  - video image
  - coming-next list (when queue is non-empty)
- Now-playing controls on message:
  - repeat/loop, previous, pause/resume, next, stop
  - pause/resume button swaps emoji by state (`⏸️` while playing, `▶️` while paused)
  - shuffle logic exists in code but is not shown in now-playing controls
  - the loop button cycles three states: off (grey `🔁`), track (green `🔂`, loops the current track), queue (blurple `🔁`, loops the whole queue)
  - `/loop` sets the same three modes directly; queue-loop re-queues each track after it finishes naturally (skipped/errored tracks are not re-queued)
  - previous restarts current track when playback has passed 5 seconds; otherwise it goes to the previous track
  - if music commands are used from another channel, now-playing moves to that channel and old message is deleted
- Control interactions edit the existing now-playing message (no extra feedback messages).
- Audio filter presets available:
  - off, hiphop, edm, dance, vocal, pop, rock, trebleboost
- Bass level is configured only through `/bass level:<0..20>` (required argument).
- Filter changes are applied mid-playback by restarting from the current playback position.
- Queue and now-playing cleanup when bot leaves, is disconnected, or is kicked.
- Auto-disconnect after 5 minutes of idle playback state (not playing, not paused, no current track, empty queue).
- Bot member-list presence is updated dynamically:
  - idle: `Vitun Pellet`
  - during playback: `Playing: <track title>`
- Persistent config in project-root `config.json`:
  - banned users per guild
  - active equalizer/filter preset per guild
- Graceful shutdown on `Ctrl+C`: bot closes Discord session cleanly.

## Project Layout
- `src/jukkabot/bot.py`: `JukkaBot` client, entrypoint (`run`), and `config.json` persistence
- `src/jukkabot/config.py`: loads `.env` settings (`DISCORD_BOT_TOKEN`, `ADMIN_USER_IDS`)
- `src/jukkabot/queue_manager.py`: per-guild queue/history/moderation state (`QueueManager`, `GuildQueue`)
- `src/jukkabot/music_service.py`: YouTube search, playlist, and stream-source resolving via `yt-dlp`
- `src/jukkabot/models.py`: the `Track` dataclass
- `src/jukkabot/cogs/music.py`: all slash commands, now-playing UI, and playback logic
- `tests/`: pytest suite

## Setup
This project is managed with [uv](https://docs.astral.sh/uv/).

1. Install dependencies (creates/updates the project virtual environment from `uv.lock`):
   ```powershell
   uv sync
   ```
2. Install FFmpeg and ensure `ffmpeg` is available on `PATH`.
3. Configure `.env`:
   - `DISCORD_BOT_TOKEN` (required)
   - `ADMIN_USER_IDS` (optional, comma-separated user IDs; invalid entries are ignored)
4. Run:
   ```powershell
   uv run jukka
   ```
   (equivalent to `uv run python -m jukkabot`)

The Python version is pinned via `.python-version` (3.14); `uv` will fetch it automatically if needed.

## Testing
- Run all tests:
  ```powershell
  uv run pytest
  ```

## Notes
- If slash commands do not appear, confirm bot invite has `applications.commands` scope and wait for Discord command propagation after restart/sync.
