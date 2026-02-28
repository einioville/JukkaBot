# JukkaBot

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
  - `/stats`: Tracker stats command (currently disabled until Tracker app approval)
- Per-server queue, history, and moderation state.
- Tracks store who queued them.
- Now-playing embed includes:
  - playing/paused status
  - title, author, length, queued-by
  - video image
  - coming-next list (when queue is non-empty)
- Now-playing controls on message:
  - previous, next, pause/resume, shuffle
- Audio filter presets available:
  - off, bassboost, hiphop, edm, dance, vocal, pop, rock, trebleboost
- Queue and now-playing cleanup when bot leaves or gets disconnected.
- Auto-disconnect after 5 minutes of idle playback state (not playing, not paused, no current track, empty queue).

## Project Layout
- `src/jukkabot/`: main bot package
- `src/jukkabot/cogs/music.py`: music command/cog logic
- `src/jukkabot/music_service.py`: YouTube search/stream source resolving
- `src/jukkabot/tracker_service.py`: Tracker API client
- `tests/`: tests

## Setup
1. Create and activate a virtual environment.
2. Install dependencies:
   ```powershell
   pip install -e . pytest
   ```
3. Install FFmpeg and ensure `ffmpeg` is available on `PATH`.
4. Configure `.env`:
   - `DISCORD_BOT_TOKEN` (required)
   - `ADMIN_USER_IDS` (optional, comma-separated user IDs)
   - `TRACKER_API_KEY` or `TRN_API_KEY` (optional while `/stats` is disabled)
5. Run:
   ```powershell
   python -m jukkabot
   ```

## Testing
- Run all tests:
  ```powershell
  pytest
  ```
- Tracker live tests are opt-in:
  - Set `TRACKER_LIVE_TESTS=1`
  - Ensure Tracker API key is configured

## Notes
- `/stats` is intentionally disabled in code (`TRACKER_STATS_ENABLED = False`) until Tracker approves API access for the app.
