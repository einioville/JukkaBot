# JukkaBot

> Note: This is my training project. The goal is to learn using Codex as a development tool.

Discord music bot project using Python, `discord.py`, `yt-dlp`, and FFmpeg.

## Features
- Slash commands:
  - `/join`: join your voice channel
  - `/play`: search and queue a track
  - `/skip`: skip current track
  - `/pause`: pause/resume playback
  - `/filter`: apply audio filter preset (autocomplete)
  - `/bass`: apply bass boost filter with level control (`0..20`)
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
  - repeat, previous, pause/resume, next, stop
  - pause/resume button swaps emoji by state (`⏸️` while playing, `▶️` while paused)
  - shuffle logic exists in code but is not shown in now-playing controls
  - repeat loops the currently playing track until toggled off
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
- `src/jukkabot/`: main bot package
- `src/jukkabot/cogs/music.py`: music command/cog logic
- `src/jukkabot/music_service.py`: YouTube search/stream source resolving
- `tests/`: tests

## Setup
1. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   pip install -e . pytest
   ```
3. Install FFmpeg and ensure `ffmpeg` is available on `PATH`.
4. Configure `.env`:
   - `DISCORD_BOT_TOKEN` (required)
   - `ADMIN_USER_IDS` (optional, comma-separated user IDs; invalid entries are ignored)
5. Run:
   ```powershell
   python -m jukkabot
   ```

## Testing
- Run all tests:
  ```powershell
  pytest
  ```

## Notes
- If slash commands do not appear, confirm bot invite has `applications.commands` scope and wait for Discord command propagation after restart/sync.
