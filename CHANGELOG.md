# Changelog

All notable changes to FrontPocket will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.3.0] — 2026-04-21

### Change toolbar button behavior. Make Pause, Play, Stop work as intuitively expected.
- Pause to pause playback and put us in a paused state.
- When in a paused state, either Pause or Play will resume.
- Stop to stop playback and put us in a stopped state.
- While in a stopped state, Play will initiate playback from the beginning of the current clipboarded text.

### Change server audio playback to use sd.OutputStream in place of sd.play and sd.stop
- Resolves resource leak with multiple streams as different chunks are played.

### Fixed duplicated Step #2 in INSTALL.md

---

## [1.2.0] — 2026-04-12

### Discussion: Changing from Systemd Service with a limited permissions user to User Service
- Testing of 1.1.0 showed that the previous method of running as a system service using a dedicated limited permissions user was flawed. In that scenario, the limited user needed access to the audio of user 1000. There are ways to do that but they added complexity and were not going to work consistently across diverse environments. Put another way, it's not enough for it to work for me, it needs to work for you as well and with minimal hassle. Running as a user service inherently has access to your audio device. Code should have the minimal security needed to do it's job and by dropping the limited permissions user we tradeoff some of that. It's a local TTS Server. For those less compomising on security, it is possible to run it with Firejail and should be possible with Docker.
- Moving from to a user service meant that it no longer made sense to store the project under /opt/FrontPocket so it's moved under ~/FrontPocket.
- README.md, INSTALL.md, frontpocket.service were updated to accomodate these changes.
- Server updated to allow ~/FrontPocket rather than full path specification for location of voices and sounds.

### Toolbar
- Always send Speed and Voice when we start playing. Resolves condition where if the server restarted after toolbar is already running which can result in the server playing in a different voice than shown in the toolbar. New ini option under [SpeedDefaults] always_send_voice_speed = false can disable this behavior.

- Fix locations searched for frontpocket.ini so that we're consistent with locations searched by server and client. 


## [1.1.0] — 2026-04-05

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
