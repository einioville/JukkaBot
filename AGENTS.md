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
- `/skip`: skip current track.
- `/pause`: toggle pause/resume.
- `/clear`: clear queue/history/current track and delete now-playing message.
- `/leave`: disconnect from voice and clear queue/history/current state.
- `/chat`: chat mode for current channel (`on`, `off`, `status`).
  - One active chat channel per guild.
  - Auto-disable after 5 minutes of quiet.
- `/filter`: apply preset audio filter (`off`, `hiphop`, `edm`, `dance`, `vocal`, `pop`, `rock`, `trebleboost`).
- `/bass`: apply bass boost with required `level` option (`0..20`).
- `/banuser`, `/unbanuser`: queue/skip moderation (owner/admin only).
- `/stats`: Tracker Network stats command exists but is disabled until Tracker app approval.

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
  - Previous, next, pause/resume, shuffle, stop
- Button actions should update/edit existing now-playing message and avoid extra feedback spam.

# Automation
- Leave voice after 5 minutes idle playback state:
  - not playing
  - not paused
  - no current track
  - empty queue
- If bot is kicked/disconnected/leaves, clear queue state and delete now-playing message.

# Persistence
- Persist minimal per-guild config to root `config.json` on shutdown:
  - banned users
  - active equalizer/filter
- `config.json` is gitignored.

# AI Chat Config
- Chat mode uses OpenAI API.
- Configure via `.env`:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `CHAT_TEMPERATURE`
  - `CHAT_MAX_OUTPUT_TOKENS`
  - `CHAT_IDLE_TIMEOUT_SECONDS`
- Configure prompt via `config.json`:
  - `chat.system_prompt`

# Architecture
- Code lives under `src/`.
- Keep queue logic, service integrations, and command/cog logic separated.

# Other Instructions
- Always commit changes made.
