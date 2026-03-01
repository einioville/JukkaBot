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
  - `/chat`: AI chat mode (`on`, `off`) for the current server/channel
  - `/image`: generate an image from text prompt, with optional reference image edit input
  - `/banuser`: ban user from queueing/skipping
  - `/unbanuser`: remove queue/skip ban
  - `/stats`: Tracker stats command (currently disabled until Tracker app approval)
- Per-server queue, history, and moderation state.
- Tracks store who queued them.
- `/play` autocomplete uses trailing debounce (500 ms) and does not cache results.
- Now-playing embed includes:
  - playing/paused status
  - title, author, length, queued-by
  - video image
  - coming-next list (when queue is non-empty)
- Now-playing controls on message:
  - previous, next, pause/resume, shuffle, repeat, stop
  - repeat loops the currently playing track until toggled off
  - previous restarts current track when playback has passed 5 seconds; otherwise it goes to the previous track
- Control interactions edit the existing now-playing message (no extra feedback messages).
- Audio filter presets available:
  - off, hiphop, edm, dance, vocal, pop, rock, trebleboost
- Bass level is configured only through `/bass level:<0..20>` (required argument).
- Filter changes are applied mid-playback by restarting from the current playback position.
- Queue and now-playing cleanup when bot leaves, is disconnected, or is kicked.
- Auto-disconnect after 5 minutes of idle playback state (not playing, not paused, no current track, empty queue).
- Persistent config in project-root `config.json`:
  - banned users per guild
  - active equalizer/filter preset per guild
- Graceful shutdown on `Ctrl+C`: bot closes Discord session cleanly.
- Chat mode:
  - `/chat action:on` enables AI chat in the current guild/channel
  - Reads all channel messages for context/history, but replies only when mentioned
  - Reads supported text attachments only from the mentioned message
  - Reads image attachments from mentioned messages for image understanding
  - Bot replies only when it is mentioned in chat
  - Dynamic memory updates only when the bot is mentioned and message starts with `Muista: ...` (leading bot mention is ignored in this check)
  - Stored facts are reused in later replies for continuity
  - Handles long model replies by splitting them into multiple Discord messages
  - Occasionally sends brainrot GIF links
  - Bot replies in that channel until quiet for 5 minutes, then chat auto-disables
  - `/chat action:off` disables immediately
  - Uses OpenAI web search tools when supported by the selected chat model
- Image generation:
  - `/image prompt:<text>` for text-to-image generation
  - `/image prompt:<text> reference_image:<attachment>` for reference/edit flows
  - Image generation uses a separate image model and a longer timeout (default 120s)

## Project Layout
- `src/jukkabot/`: main bot package
- `src/jukkabot/cogs/music.py`: music command/cog logic
- `src/jukkabot/cogs/chat.py`: AI chat mode command/cog logic
- `src/jukkabot/music_service.py`: YouTube search/stream source resolving
- `src/jukkabot/openai_service.py`: OpenAI API client
- `src/jukkabot/tracker_service.py`: Tracker API client
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
   - `OPENAI_API_KEY` (required for `/chat on`)
   - `OPENAI_MODEL` (optional, default `gpt-4.1-mini`)
   - `OPENAI_IMAGE_MODEL` (optional, default `gpt-image-1`)
   - `OPENAI_TIMEOUT_SECONDS` (optional, default `30`)
   - `OPENAI_IMAGE_TIMEOUT_SECONDS` (optional, default `120`)
   - `CHAT_TEMPERATURE` (optional, default `0.8`)
   - `CHAT_MAX_OUTPUT_TOKENS` (optional, default `220`)
   - `CHAT_IDLE_TIMEOUT_SECONDS` (optional, default `300`)
   - `CHAT_ENABLE_WEB_SEARCH` (optional, default `true`)
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
- If slash commands do not appear, confirm bot invite has `applications.commands` scope and wait for Discord command propagation after restart/sync.
- Chat mode requires Discord Message Content Intent in the developer portal for full channel message processing.
- Chat prompt is loaded from `config.json` key `chat.system_prompt_file` (project-relative file path).
- Prompt files under `resources/prompts/*.txt` are ignored by git; keep your personal prompt there locally.
- OpenAI model parameter support is auto-detected at runtime (unsupported parameters are disabled and retried automatically).
- OpenAI timeouts are retried automatically with short backoff.
- Image API requests use `OPENAI_IMAGE_TIMEOUT_SECONDS` and log using the image model name.
- Chat and image commands currently do not have per-user or per-guild rate limits (recommended for production to control API costs).

### Chat Prompt Config
```json
{
  "chat": {
    "system_prompt_file": "resources/prompts/ragebait_chat_prompt.txt",
    "user_facts": {
      "727518463886753812": {
        "481082798321434635": {
          "name": "ville",
          "facts": ["likes fortnite"]
        }
      }
    }
  }
}
```
