# CLAUDE.md

Guidance for working in this repository.

## What this is

JukkaBot is a Discord music bot that streams audio from YouTube. Python + `discord.py` (with `[voice]`), `yt-dlp` for extraction/search, and FFmpeg for the audio pipeline. It is a personal learning project.

## Commands

Managed with [uv](https://docs.astral.sh/uv/). Python is pinned to 3.14 via `.python-version` (`requires-python >= 3.11`).

```powershell
uv sync            # install/update the venv from uv.lock
uv run jukka       # run the bot (equivalent: uv run python -m jukkabot)
uv run pytest      # run the full test suite
uv run pytest tests/test_queue_manager.py::test_name   # single test
```

FFmpeg must be installed and on `PATH`. The bot needs `.env` with `DISCORD_BOT_TOKEN` (required) and `ADMIN_USER_IDS` (optional, comma-separated).

## Architecture

- `src/jukkabot/bot.py` — `JukkaBot(commands.Bot)`, the `run()` entrypoint, and `config.json` load/save. Loads the `jukkabot.cogs.music` extension and syncs the command tree in `setup_hook`. Saves persistent state in `close()` (so Ctrl+C / disconnect persists cleanly).
- `src/jukkabot/config.py` — `load_settings()` reads `.env` into a frozen `Settings` dataclass.
- `src/jukkabot/queue_manager.py` — `QueueManager` owns a `dict[guild_id -> GuildQueue]`. `GuildQueue` holds queue, history (maxlen 50), banned users, current track, active filter, now-playing message pointers, and the `skip_requested` / `repeat_current` / `repeat_queue` / `clear_requested` flags. Pure data/state logic, no Discord calls — this is the easy-to-unit-test layer.
- `src/jukkabot/music_service.py` — `MusicService` wraps `yt-dlp`: `search`, `get_playlist`, `get_track`, `get_stream_source`. Runs synchronously; callers offload it with `asyncio.to_thread`.
- `src/jukkabot/models.py` — the `Track` dataclass (`duration_label` property formats mm:ss).
- `src/jukkabot/cogs/music.py` — everything Discord-facing (~1900 lines): all slash commands, the `NowPlayingControls` button view, the `MusicCog` playback engine, presence sync, and the idle-disconnect task. Most feature work happens here.

## Conventions and gotchas

- **All state is per guild.** Never introduce global playback state; go through `QueueManager.get(guild_id)`.
- **Voice access rule:** if the bot is not in voice, commands are allowed; if it is connected, the user must be in the same voice channel. See `_validate_channel_access`.
- **Silent interactions:** commands ack with `_ack_silent` and finish with `_finalize_silent` / `_send_followup_and_finalize` to avoid feedback spam. Playback state is communicated through a single now-playing embed that gets *edited in place*, not new messages.
- **Now-playing message moves channels:** if a music command is used in a different text channel, the now-playing message is re-sent there and the old one deleted.
- **Loop modes are mutually exclusive:** `repeat_current` and `repeat_queue` are never both true. `/loop` and the loop button both cycle off → track → queue → off. Queue-loop re-queues a track only after it finishes *naturally* (skips/errors are not re-queued).
- **`clear_requested` guard:** `/clear` is a hard stop — pending `_after_track_finished` callbacks must check this flag and not auto-advance.
- **Filter changes mid-playback** restart the stream from the current elapsed position (`_restart_current_with_active_filter`). Presets live in `FILTER_PRESETS`; `/bass` builds a dynamic bass-boost filter from a required `level` (0..20).
- **Previous button:** restarts the current track if elapsed > 5s, otherwise steps back in history.
- **Idle auto-disconnect** after 5 minutes of fully-idle state (not playing, not paused, no current track, empty queue).
- **Persistence:** only banned users + active filter/equalizer are written to root `config.json` (atomic temp-file replace). `config.json` is gitignored and auto-created; do not commit it.
- **Playlist cap:** `MAX_PLAYLIST_TRACKS = 100`. Playlist detection (`_is_playlist_url`) matches a `list=` query param or a `/playlist` path.

## Testing

Tests are pytest under `tests/`. The state/service layers (`queue_manager`, `music_service`, `config`) are unit-testable directly; `test_music_cog.py` exercises cog behavior with mocked Discord objects. Prefer adding logic to the testable `QueueManager`/`MusicService` layers rather than burying it in the cog when practical.

## Notes

- If slash commands don't appear, confirm the bot invite has the `applications.commands` scope and allow time for Discord to propagate after a restart/sync.
