# JukkaBot Prototype

Prototype Discord music bot based on the current `AGENTS.md` requirements.

## Implemented
- Slash commands: `/join`, `/play`, `/skip`, `/pause`, `/leave`, `/banuser`, `/unbanuser`
- Per-server queue and history state
- Per-server banned-user list for queueing/skipping
- Tracks store who queued them
- "Now Playing" embed that removes the previous message before posting a new one
- "Now Playing" controls: previous, next, pause/resume, shuffle
- Auto-disconnect after 5 minutes when only bots remain in voice
- YouTube search via `yt-dlp` and direct audio streaming to voice channels using FFmpeg

## Project Layout
- `src/jukkabot/`: bot code
- `src/jukkabot/cogs/music.py`: slash command behavior
- `tests/`: queue manager tests

## Quick Start
1. Create and activate a virtual environment.
2. Install dependencies:
   ```powershell
   pip install -e . pytest
   ```
3. Install FFmpeg and ensure `ffmpeg` is available on `PATH`.
4. Create `.env` from `.env.example` and set your bot token/admin IDs.
5. Run the bot:
   ```powershell
   python -m jukkabot
   ```
6. Run tests:
   ```powershell
   pytest
   ```

## Notes
- Reaction-button controls (pause/previous/shuffle/loop) are not implemented yet.
