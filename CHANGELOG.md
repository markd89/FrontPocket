# Changelog

All notable changes to FrontPocket will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] — 2026-03-31

Initial public release.

### Added
- All built-in TTS voices pre-configured in `frontpocket.ini`: alba, marius,
  javert, jean, fantine, cosette, eponine, azelma
- `--ping` client command — verifies server is reachable (exit 0 = up)
- `--list-voices` client command — prints all voices from `frontpocket.ini`
- `--version` flag on both server and client
- Voice path validation at server startup — warns on missing `.safetensors`
  files before attempting to load them
- `pyproject.toml` — installs `fp` and `frontpocket-server` console scripts
- Project renamed to FrontPocket; files renamed `frontpocket_server.py`,
  `frontpocket_client.py`, `frontpocket_shared.py`, `frontpocket.ini`

### Server
- TCP socket server with pre-loaded TTS model for low-latency playback
- Sentence-level chunking via pysbd with configurable max chunk duration
- Chunk-ahead pre-generation (configurable lookahead window)
- Lookbehind cache — previously played chunks retained for instant `!back`
- Pause and resume with sample-accurate position tracking
- Voice and speed change mid-playback — stale audio detected by voice/speed
  tagging; pregen aborts and restarts with new settings immediately
- `!interruptwith` — pauses, plays optional alert WAV, speaks interrupt text,
  resumes automatically; second interrupt while one is active is ignored
- `!status` — speaks current voice, speed, and playback state
- `!next` / `!back` — instant chunk navigation, cuts off current chunk
- Chunk text cleaning — strips trailing periods, hyphens, and quote characters
  that cause TTS engine artifacts
- Empty chunk detection — chunks that clean to empty are marked SKIP and never
  sent to the TTS engine
- AUDIO_SKIP sentinel — permanently failed chunks are skipped instantly rather
  than causing a 30-second stall
- ALSA PortAudio race mitigation — brief settle before each `sd.play()` call
- Three log levels: ERROR, INFO, DEBUG
- DEBUG mode writes per-chunk `.txt` files to a configurable directory
- systemd service support with automatic restart on failure
- INI-based configuration with full comments

### Client
- Default input: system clipboard (X11 via xclip, Wayland via wl-paste,
  Windows via pyperclip, macOS via pbpaste)
- Inline text via positional argument
- Text file input via `--file`
- `--voice` and `--speed` are settings-only — do not trigger clipboard read
- `--interruptwith` accepts inline text or a file path
- `--quiet` suppresses all client output
- `--port` / `--host` override for non-default server instances
- Client-side voice validation against `frontpocket.ini` before connecting
- Fire-and-forget design — client exits immediately after sending
