# FrontPocket — Installation Guide

FrontPocket is a low-latency text-to-speech server that pre-loads the TTS model
and streams audio chunk by chunk. It is controlled via a lightweight CLI client
over a TCP socket, and can run as a systemd service.

The instructions below setup a VENV, put everything in the right directories with the correct permissions and setup the SystemD service. That's what's recommended. For those who just want to play with it interactively, it should be possible to clone the project into a folder on your machine, install the requirements and then run ```python3 frontpocket_server.py``` Then from another CLI, you can run  ```python3 frontpocket_client.py``` passing it the parameters. You're suggested to skim the full instructions anyway. 

---

## Requirements

- Linux (tested under Debian). Other distros and MacOS, Windows may work with a little persuasion. Please make a PR with fixes.
- Python 3.10+
- ALSA audio (`libasound2`)
- `rubberband-cli` (for speed adjustment)
- `xclip` (X11) or `wl-clipboard` (Wayland) for clipboard support

---

## 1. Create the system user

FrontPocket runs as a dedicated unprivileged user with access to the audio device.

```bash
sudo useradd -r -s /sbin/nologin -d /opt/FrontPocket frontpocket
sudo usermod -aG audio frontpocket
```

---

## 2. Install system dependencies

```bash
sudo apt install libasound2-dev rubberband-cli xclip
```

For Wayland clipboard support, install `wl-clipboard` instead of or in addition to `xclip`:

```bash
sudo apt install wl-clipboard
```

---

## 3. Create the application directory

```bash
sudo mkdir -p /opt/FrontPocket
sudo git clone https://github.com/yourusername/FrontPocket.git /opt/FrontPocket
sudo chown -R frontpocket:frontpocket /opt/FrontPocket
```

---

## 4. Create the Python virtual environment and install dependencies

```bash
sudo -u frontpocket python3 -m venv /opt/FrontPocket/venv
```

Install CPU-only PyTorch first to avoid downloading large CUDA packages: (If you want CUDA/GPU skip this step)

```bash
sudo -u frontpocket /opt/FrontPocket/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Then install the remaining dependencies:

```bash
sudo -u frontpocket /opt/FrontPocket/venv/bin/pip install -r /opt/FrontPocket/requirements.txt
```

Note: the full path to `pip` is used deliberately — no need to activate the venv
for this step. Calling the binary directly ensures packages install into the
correct venv.

---

## 5. Create directories for config and voices and Hugging Face Token environment variable. 

```bash
sudo mkdir -p /etc/FrontPocket
sudo mkdir -p /var/lib/FrontPocket/voices
sudo mkdir -p /var/lib/FrontPocket/sounds
sudo chown -R frontpocket:frontpocket /var/lib/FrontPocket
```
Place any custom voice embeddings in `/var/lib/FrontPocket/voices/` and any
notification sounds in `/var/lib/FrontPocket/sounds/`.

To use a notification sound before interrupt messages, copy your WAV file and
set the path in `frontpocket.ini`:

```bash
sudo cp /opt/FrontPocket/notification.wav /var/lib/FrontPocket/sounds/
sudo chown frontpocket:frontpocket /var/lib/FrontPocket/sounds/notification.wav
```

```ini
interrupt_sound = /var/lib/FrontPocket/sounds/notification.wav
```

### Hugging Face token

FrontPocket needs a Hugging Face token to download the TTS model on first run.
If you want to use the voice-cloning feature of Pocket-TTS, you'll need to follow
the insuctructions on their project and generate the HF Token. The following steps
allow you to store that token securely and have it referenced by the service.
 
Create a secure environment file that the service will read at startup:

```bash
sudo touch /etc/FrontPocket/environment
sudo chmod 600 /etc/FrontPocket/environment
sudo chown frontpocket:frontpocket /etc/FrontPocket/environment
```

Add your token:

```bash
echo "HF_TOKEN=your_token_here" | sudo tee /etc/FrontPocket/environment
sudo chmod 600 /etc/FrontPocket/environment
```

The service file references this file via `EnvironmentFile=` so the token is
never visible in process listings or world-readable service files.

---

## 6. Install and edit the configuration file

Copy the template from the repo into `/etc/FrontPocket/`, remove the original,
then create a symlink so the server can find it:

```bash
sudo cp /opt/FrontPocket/frontpocket.ini /etc/FrontPocket/frontpocket.ini
sudo rm /opt/FrontPocket/frontpocket.ini
sudo ln -s /etc/FrontPocket/frontpocket.ini /opt/FrontPocket/frontpocket.ini
```

Now edit the config in its canonical location:

```bash
sudo nano /etc/FrontPocket/frontpocket.ini
```

All future edits should be made to `/etc/FrontPocket/frontpocket.ini`. The
symlink in `/opt/FrontPocket/` should never be edited directly.

### Adding custom voices

Copy your `.safetensors` voice embedding files to `/var/lib/FrontPocket/voices/`,
then add entries to the `[voices]` section of `frontpocket.ini`:

```ini
[voices]
alba   = alba
mary  = /var/lib/FrontPocket/voices/mary.safetensors
```

---

## 7. Install the systemd service

```bash
sudo cp /opt/FrontPocket/frontpocket.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable frontpocket
sudo systemctl start frontpocket
```

Check that the service started successfully (NOTE: First start may take several minutes as the model is downloaded from Hugging Face. Subsequent startups should be just a few seconds.):

```bash
sudo systemctl status frontpocket
```

Follow live logs:

```bash
journalctl -u frontpocket -f
```

---

## 8. Make the client available system-wide

Create a simple wrapper script so `fp` works from any terminal without
activating the venv:

```bash
sudo tee /usr/local/bin/fp > /dev/null << 'EOF'
#!/bin/bash
exec /opt/FrontPocket/venv/bin/python3 /opt/FrontPocket/frontpocket_client.py "$@"
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
fp                                   # speak clipboard contents
fp "Hello world"                     # speak inline text
fp --file article.txt                # speak a text file
fp --ping                            # check server is reachable
fp --list-voices                     # show configured voices
fp --pause
fp --resume
fp --next
fp --back
fp --voice maria
fp --speed 1.5
fp --status
fp --interruptwith "Dinner is ready"
fp --version
```

---

## Uninstalling

```bash
sudo systemctl stop frontpocket
sudo systemctl disable frontpocket
sudo rm /etc/systemd/system/frontpocket.service
sudo systemctl daemon-reload
sudo rm -rf /opt/FrontPocket
sudo rm -rf /var/lib/FrontPocket
sudo rm -f /etc/FrontPocket/frontpocket.ini
sudo userdel frontpocket
```

---

## Troubleshooting

**Server won't start / model fails to load**
Check logs: `journalctl -u frontpocket -n 50`

**No audio / ALSA errors**
Ensure the `frontpocket` user is in the `audio` group:
```bash
sudo usermod -aG audio frontpocket
sudo systemctl restart frontpocket
```

**Client can't connect**
Make sure the server is running and the port in `frontpocket.ini` matches on both sides:
```bash
sudo systemctl status frontpocket
fp --status
```

**PortAudio timeout warnings**
These are intermittent ALSA timing warnings and are not fatal. If they occur
frequently, try increasing `RestartSec` in the service file or setting a higher
process priority via `Nice=-5` in the `[Service]` block.
