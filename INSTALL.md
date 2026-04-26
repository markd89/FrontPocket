# FrontPocket — Installation Guide & Basic Usage

FrontPocket is a low-latency text-to-speech server that pre-loads the TTS model
and streams audio chunk by chunk. It is controlled via a lightweight CLI client
over a TCP socket, and runs as a systemd user service in your desktop session.

The instructions below set up a venv, put everything in the right directories,
and configure the systemd user service. That's the recommended path. If you just
want to try it interactively first, clone the repo, install the requirements, and
run `python3 frontpocket_server.py` directly. From another terminal, run
`python3 frontpocket_client.py` with parameters. Either way, skim the full
instructions — they contain useful context.

---

## Requirements

- Linux (tested under Debian). Other distros may work with minor adjustments.
  MacOS and Windows are untested. PRs welcome.
- Python 3.10+
- ALSA audio (`libasound2`)
- `rubberband-cli` (for speed adjustment)
- `xclip` (X11) or `wl-clipboard` (Wayland) for clipboard support
- A desktop session (the service uses your audio session directly)
- python3-venv (Debian and Ubuntu need to install this. Arch & Fedora package it with python3)
- PyQt6 for Toolbar GUI


## 0. "Easy" Installation with frontpocket_installer.sh

- New simplified installation for FrontPocket v1.4+
- Starting in FrontPocket v1.4 [frontpocket_installer.sh](frontpocket_installer.sh) is provided. This script simplifies installation on Linux environments. Tested on Debian.
- Download the script, chmod +x frontpocket_installer.sh, ./frontpocket_installer.sh
- The script will download the project and perform the same install steps as the manual install. This needs more testing, especially on non-Debian systems. A log file is created in ~/FrontPocket which may help with troubleshooting and issue reporting.

We still recommend reviewing the steps below so that you know what the install script is doing. Also starting at Step 7, there are some usage information.

## 1. Install system dependencies

```bash
sudo apt install libasound2-dev rubberband-cli xclip python3-venv python3-pyqt6
```

For Wayland clipboard support, install `wl-clipboard` instead of or in addition to `xclip`:

```bash
sudo apt install wl-clipboard
```

---

## 2. Create the application directory

Clone the repo into your home directory:

```bash
git clone https://github.com/markd89/FrontPocket.git ~/FrontPocket
```

---

## 3. Create the Python virtual environment and install dependencies

```bash
python3 -m venv ~/FrontPocket/venv
```

Install CPU-only PyTorch first to avoid downloading large CUDA packages (skip
this step if you want CUDA/GPU support):

```bash
~/FrontPocket/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Then install the remaining dependencies:

```bash
~/FrontPocket/venv/bin/pip install -r ~/FrontPocket/requirements.txt
```

The full path to `pip` is used deliberately — no need to activate the venv.
Calling the binary directly ensures packages install into the correct venv.

---

## 4. Create directories for config, voices, and sounds

```bash
mkdir -p ~/.config/FrontPocket
mkdir -p ~/FrontPocket/voices
mkdir -p ~/FrontPocket/sounds
```

Place any custom voice embeddings in `~/FrontPocket/voices/` and any
notification sounds in `~/FrontPocket/sounds/`. 
We provide a sample notification.wav with the package.

To use a notification sound before interrupt messages, copy your WAV file and
set the path in `frontpocket.ini`:

```bash
cp ~/FrontPocket/notification.wav ~/FrontPocket/sounds/
```

```ini
interrupt_sound = ~/FrontPocket/sounds/notification.wav
```

### Hugging Face token

FrontPocket needs a Hugging Face token to download the TTS model on first run.
If you want to use the voice-cloning feature of Pocket-TTS, follow their
instructions to generate an HF token. The following steps store it securely
for the service to read at startup.

Create a private environment file:

```bash
touch ~/.config/FrontPocket/environment
chmod 600 ~/.config/FrontPocket/environment
```

Add your token:

```bash
echo "HF_TOKEN=your_token_here" >> ~/.config/FrontPocket/environment
```

---

## 5. Install and edit the configuration file

Copy the template into your config directory and symlink it so the server can
find it:

```bash
cp ~/FrontPocket/frontpocket.ini ~/.config/FrontPocket/frontpocket.ini
rm ~/FrontPocket/frontpocket.ini
ln -s ~/.config/FrontPocket/frontpocket.ini ~/FrontPocket/frontpocket.ini
```

Edit the config in its canonical location:

```bash
nano ~/.config/FrontPocket/frontpocket.ini
```

All future edits should be made to `~/.config/FrontPocket/frontpocket.ini`.
The symlink in `~/FrontPocket/` should never be edited directly.

### Adding custom voices

Copy your `.safetensors` voice embedding files to `~/FrontPocket/voices/`, then
add entries to the `[voices]` section of `frontpocket.ini`:

```ini
[voices]
alba  = alba
mary  = ~/FrontPocket/voices/mary.safetensors
```

---

## 6. Install the systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp ~/FrontPocket/frontpocket.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable frontpocket
systemctl --user start frontpocket
```

Check that the service started successfully. The first start may take several
minutes while the model downloads from Hugging Face. Subsequent startups should
take only a few seconds:

```bash
systemctl --user status frontpocket
```

Follow live logs:

```bash
journalctl --user -u frontpocket -f
```

### Optional: start on boot before login

By default the user service only runs while you are logged in. If you want
FrontPocket to start at boot even without an active desktop session, enable
user lingering:

```bash
loginctl enable-linger $USER
```

Note: audio will still require your PulseAudio or PipeWire session to be
running. Lingering is most useful if you have a persistent audio session
(e.g. a headless setup with a virtual sink).

---

## 7. Make the client available system-wide

Create a simple wrapper script so `fp` works from any terminal without
activating the venv:

```bash
sudo tee /usr/local/bin/fp > /dev/null << 'EOF'
#!/bin/bash
exec ~/FrontPocket/venv/bin/python3 ~/FrontPocket/frontpocket_client.py "$@"
EOF
sudo chmod +x /usr/local/bin/fp
```

Verify it works:

```bash
fp --ping
fp --version
fp --list-voices
```

Then use it from anywhere:

```bash
fp                                    # speak clipboard contents
fp "Hello world"                      # speak inline text
fp --file article.txt                 # speak a text file
fp --ping                             # check server is reachable
fp --list-voices                      # show configured voices
fp --pause
fp --resume
fp --next
fp --back
fp --voice mary
fp --speed 1.5
fp --status
fp --interruptwith "Dinner is ready"
fp --version
```

---

## 8. Use the toolbar

Start the toolbar with:

```bash
~/FrontPocket/venv/bin/python3 ~/FrontPocket/frontpocket_toolbar.py
```

Speed, Voice, and Quit are on the right-click menu. Pause toggles between
pause and resume based on current state.

To speak something new, copy it to the clipboard then press Play. This works
whether the server is idle or currently speaking — in the latter case it stops
the current text and starts the new one.

---

## 9. Fun and Notifications

While you are speaking some nice long piece of text, try:

```bash
fp --interruptwith "Dinner is ready"

```

Did it make you laugh?

Anyway, the idea behind the interruptwith feature is that maybe you want to get a spoken alert when something happens on your system. Maybe something compiles or an error happens or something else. This let's you get a spoken alert prefixed with the notification sound (configurable, of course) and then the TTS resumes where it left off.


---

## Uninstalling

```bash
systemctl --user stop frontpocket
systemctl --user disable frontpocket
rm ~/.config/systemd/user/frontpocket.service
systemctl --user daemon-reload
rm -rf ~/FrontPocket
rm -rf ~/.config/FrontPocket
sudo rm -f /usr/local/bin/fp
```

---

## Troubleshooting

**Server won't start / model fails to load**
Check logs:
```bash
journalctl --user -u frontpocket -n 50
```

**No audio**
The service runs as your user and uses your desktop audio session directly.
Make sure your desktop session is active and audio works for other apps. If
you're running under Wayland with PipeWire, confirm PipeWire is running:
```bash
systemctl --user status pipewire pipewire-pulse
```

**Client can't connect**
Make sure the server is running and the port in `frontpocket.ini` matches on
both sides:
```bash
systemctl --user status frontpocket
fp --ping
```

**HF_TOKEN not being picked up / unauthenticated requests warning**
systemd's `EnvironmentFile` requires strict `KEY=value` format. Check the file:
```bash
cat -A ~/.config/FrontPocket/environment
```
Lines must end with `$` only. Common problems: quotes around the value
(`HF_TOKEN="abc"` should be `HF_TOKEN=abc`), spaces around `=`, or Windows
line endings (`^M$`). Fix the file then restart the service:
```bash
systemctl --user restart frontpocket
```

**PortAudio timeout warnings**
These are intermittent ALSA timing warnings and are not fatal. If they occur
frequently, try increasing `RestartSec` in the service file or setting a higher
process priority via `Nice=-5` in the `[Service]` block.
