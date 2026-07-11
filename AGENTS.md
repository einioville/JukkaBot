# Description
- Target: Discord bot that plays music from YouTube.
- Language: Python.

# Core Rules
- Use slash (`/`) commands.
- All state must be per guild/server.
- Access rule for voice commands:
1. If bot is not connected to voice, allow command.
2. If bot is connected, user must be in the same voice channel.

# Commands (Current State)
- `/join`: join user voice channel; do not switch if bot is active with humans in another channel.
- `/play`: search + queue track, auto-join user channel if needed.
  - Autocomplete uses trailing debounce (500ms after user stops typing).
  - No autocomplete result caching.
  - Autocomplete selections resolve by exact track URL value.
  - A playlist URL (any URL with a `list=` query param or a `/playlist` path) queues all entries, capped at 100 tracks.
- `/skip`: skip current track.
- `/pause`: toggle pause/resume.
- `/queue`: show the current track and queued tracks (ephemeral, read-only).
- `/loop`: set loop mode (`off`, `track`, `queue`).
- `/clear`: clear queue/history/current track and delete now-playing message.
  - Clear is a hard-stop operation: pending playback callbacks must not auto-advance to the next track after `/clear`.
- `/leave`: disconnect from voice and clear queue/history/current state.
- `/filter`: apply preset audio filter (`off`, `hiphop`, `edm`, `dance`, `vocal`, `pop`, `rock`, `trebleboost`).
- `/bass`: apply bass boost with required `level` option (`0..20`).
- `/banuser`, `/unbanuser`: queue/skip moderation (owner/admin only).

# Queue and Playback
- Queue is per guild.
- Each queued track stores who queued it (user id + display name).
- Previous tracks are kept in history.
- Filter changes should apply during playback by restarting stream near current elapsed position.

# Now Playing Message
- Old now-playing message is removed when a new track starts.
- Embed content:
  - Title: `JukkaBot - Playing` or `JukkaBot - Paused`
  - Name, author, length, queued by
  - Video thumbnail image
  - `Coming Next` section only when queue has items
- Button controls (same style):
  - Repeat, previous, pause/resume, next, stop
  - Pause/resume button icon is state-based (`⏸️` while playing, `▶️` while paused).
  - Shuffle logic exists but its now-playing button is hidden.
  - Loop button cycles three states: off -> track -> queue -> off.
    - Off: grey, `🔁`.
    - Track: green (Spotify green), `🔂`; current track loops until changed.
    - Queue: blurple/`primary` (closest button style to purple), `🔁`; each track re-queues to the back after it finishes naturally (skips/errors are not re-queued).
    - `/loop mode:<off|track|queue>` sets the same modes; `repeat_current` and `repeat_queue` are mutually exclusive.
  - Previous behavior:
    - If current track elapsed time > 5s, restart current track from beginning.
    - Otherwise move to previous track and put current track back at the front of queue.
- If music commands are used from a different text channel, move now-playing message there and delete the old one.
- Button actions should update/edit existing now-playing message and avoid extra feedback spam.

# Automation
- Leave voice after 5 minutes idle playback state:
  - not playing
  - not paused
  - no current track
  - empty queue
- If bot is kicked/disconnected/leaves, clear queue state and delete now-playing message.
- Bot presence text:
  - Idle text: `Vitun Pellet`
  - Active playback text: `Playing: <track title>`

# Persistence
- Persist minimal per-guild config to root `config.json` on shutdown:
  - banned users
  - active equalizer/filter
- `config.json` is gitignored.

# Architecture
- Code lives under `src/`.
- Keep queue logic, service integrations, and command/cog logic separated.

# Other Instructions
- Always commit changes made.
