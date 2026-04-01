# FrontPocket
FrontPocket provides a front-end to Kyutai Labs Pocket TTS, including the ability to read text from the clipboard, from a file and passed on the CLI. Features include the ability to pause, resume, move back and forward in the spoken text and change playback speed.

It is a low-latency, daemon-based text-to-speech system, developed and tested under Linux. FrontPocket loads the TTS model once at startup and streams audio sentence by sentence, so there is minimal delay between sending text and hearing it spoken.

It is designed to be always running in the background as a systemd service, controlled by a lightweight CLI client. Of course, you can also run frontpocket_server.py from the CLI to use it in interactive mode. I'd suggest doing that and then running as a service once you're satisfied it is dialed-in.

Note: Developed/tested under Debian Linux. MacOS/Windows "should" work. Please test and provide a PR for any needed fixes.

Inspiration for this project comes from kokorodoki which provides a similar featureset for kokoro TTS. https://github.com/eel-brah/kokorodoki

Much thanks to the very smart people at Kyutai Labs for their beautiful model and helpful reference code. https://github.com/kyutai-labs/pocket-tts Their stuff is where the <b>real magic</b> happens.


---

## Features

- **Low latency** — model is pre-loaded; audio begins within seconds of sending text
- **Chunk-ahead generation** — the next several sentences are generated in the background while the current one plays
- **Multiple voices** — switch voices on the fly; built-in voices and custom `.safetensors` embeddings supported
- **Speed control** — pitch-preserved speed adjustment via pyrubberband
- **Pause / resume** — resume from exactly where you paused, even after changing voice or speed
- **Skip forward / back** — move through sentences instantly; previously played sentences are cached
- **Interrupt** — inject an urgent TTS message mid-playback, then resume automatically
- **Clipboard-first** — default input is the system clipboard; also accepts inline text and text files
- **systemd ready** — runs as a proper system service with automatic restart on failure
- **Multilingual** — sentence segmentation supports English, German, French, Spanish, Italian, Russian, Polish, and more

---

## How It Works

FrontPocket has two components:

| Component | File | Role |
|---|---|---|
| Server | `frontpocket_server.py` | Loads the model, listens on a TCP socket, plays audio |
| Client | `frontpocket_client.py` | Sends text or commands to the server |

The server and client communicate over a local TCP socket (default port `5562`). The client is fire-and-forget — it sends a message and exits immediately.

---

## Installation

See [INSTALL.md](INSTALL.md) for full setup instructions including systemd service configuration.

**Quick summary:**
```bash
sudo useradd -r -s /sbin/nologin frontpocket
sudo usermod -aG audio frontpocket
sudo git clone https://github.com/markd89/FrontPocket.git /opt/FrontPocket
sudo -u frontpocket python3 -m venv /opt/FrontPocket/venv
sudo -u frontpocket /opt/FrontPocket/venv/bin/pip install -r /opt/FrontPocket/requirements.txt
sudo cp /opt/FrontPocket/frontpocket.service /etc/systemd/system/
sudo systemctl enable --now frontpocket
```

---

## Client Usage

```
fp [text] [options]
```

### Input

| Command | Description |
|---|---|
| `fp` | Speak clipboard contents (default) |
| `fp "Some text"` | Speak inline text |
| `fp --file article.txt` | Speak contents of a text file |

### Playback Control

| Command | Short | Description |
|---|---|---|
| `fp --pause` | | Pause playback |
| `fp --resume` | | Resume from where you paused |
| `fp --next` | | Skip to next sentence |
| `fp --back` | | Go back one sentence |

### Settings

| Command | Description |
|---|---|
| `fp --voice masha` | Change voice (takes effect immediately) |
| `fp --speed 1.5` | Change speed (0.5–3.0, takes effect immediately) |

### Interrupts & Status

| Command | Description |
|---|---|
| `fp --interruptwith "text"` | Pause, speak the text, resume |
| `fp --interruptwith alert.txt` | Same, but read text from a file |
| `fp --status` | Speak current voice, speed, and playback state |

### Other Options

| Option | Description |
|---|---|
| `--ping` | Check server is reachable (exit 0 = up, exit 1 = down) |
| `--list-voices` | Print all voices configured in `frontpocket.ini` |
| `--version` | Print FrontPocket version and exit |
| `--port PORT` | Connect to a non-default server port |
| `--host HOST` | Connect to a non-default server host |
| `--quiet` | Suppress all client output |

---

## Configuration

FrontPocket is configured via `frontpocket.ini`. The server looks for it next to `frontpocket_server.py`, then in the current working directory. When installed as a service, symlink `/etc/FrontPocket/frontpocket.ini` into `/opt/FrontPocket/`.

### Key settings

```ini
[settings]
default_voice = alba
default_speed = 1.0
port = 5562
lookahead_chunks = 5
language = en
log_level = INFO
interrupt_sound = /var/lib/FrontPocket/sounds/notification.wav

[voices]
alba  = alba
mary = /var/lib/FrontPocket/voices/mary.safetensors
```

See the fully commented `frontpocket.ini` for all available options.

---

## Voices

FrontPocket uses [pocket_tts](https://github.com/kyutai-labs/pocket-tts) for TTS.

**Built-in voices** are referenced by name in `frontpocket.ini`:
```ini
alba = alba
```

**Custom voices** use `.safetensors` embedding files:
```ini
masha = /var/lib/FrontPocket/voices/mary.safetensors
```

**Hugging Face voices** can be referenced directly:
```ini
expresso = hf://kyutai/tts-voices/expresso/ex01-ex02_default_001_channel2_198s.wav
```

---

## Commands Reference (Server Protocol)

Text sent to the server without a `!` prefix is spoken. Commands are prefixed with `!`:

| Command | Alias | Description |
|---|---|---|
| `!pause` | `!p` | Pause playback |
| `!resume` | `!r` | Resume playback |
| `!next` | `!n` | Skip to next chunk |
| `!back` | `!b` | Go back one chunk |
| `!voice <name>` | | Change voice |
| `!speed <value>` | | Change speed |
| `!interruptwith <text>` | `!i` | Interrupt with text, then resume |
| `!status` | | Speak current voice, speed, and state |
| `!ping` | | No-op — used to verify server is reachable |

---

## Logging

| Level | What you see |
|---|---|
| `ERROR` | Fatal errors only |
| `INFO` | Chunk generation, playback, commands received (default) |
| `DEBUG` | Everything above plus chunk text, socket messages, and per-chunk `.txt` files in `debug_dir` |

Set `log_level` in `frontpocket.ini` or override at launch:
```bash
python3 frontpocket_server.py --log-level DEBUG
```

When running as a service, view logs with:
```bash
journalctl -u frontpocket -f
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `pocket_tts` | TTS engine |
| `pysbd` | Sentence boundary detection |
| `pyrubberband` | Pitch-preserved speed adjustment |
| `sounddevice` | Audio playback |
| `numpy` | Audio array handling |
| `scipy` | WAV file reading (interrupt sound) |
| `rubberband-cli` | System package required by pyrubberband |
| `pyperclip` | Windows clipboard support (optional) |

---

## License

MIT
