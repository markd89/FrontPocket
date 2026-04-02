# FrontPocket — Installation Guide

FrontPocket is a low-latency text-to-speech server that pre-loads the TTS model
and streams audio chunk by chunk. It is controlled via a lightweight CLI client
over a TCP socket, and can run as a systemd service.

---

## Requirements

- Linux (Debian/Ubuntu recommended)
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
sudo git clone https://github.com/markd89/FrontPocket.git /opt/FrontPocket
sudo chown -R frontpocket:frontpocket /opt/FrontPocket
```

---

## 4. Create the Python virtual environment

```bash
sudo -u frontpocket python3 -m venv /opt/FrontPocket/venv
sudo -u frontpocket /opt/FrontPocket/venv/bin/pip install -e /opt/FrontPocket
```

This installs all dependencies and puts the `fp` and `frontpocket-server` commands
on the venv's PATH via `pyproject.toml`.

---

## 5. Create directories for config and voices

```bash
sudo mkdir -p /etc/FrontPocket
sudo mkdir -p /var/lib/FrontPocket/voices
sudo chown -R frontpocket:frontpocket /var/lib/FrontPocket
```

---

## 6. Install and edit the configuration file

```bash
sudo cp /opt/FrontPocket/frontpocket.ini /etc/FrontPocket/frontpocket.ini
sudo nano /etc/FrontPocket/frontpocket.ini
```

The server looks for `frontpocket.ini` next to `frontpocket_server.py` first, then in the current
working directory. To use `/etc/FrontPocket/frontpocket.ini`, create a symlink:

```bash
sudo ln -s /etc/FrontPocket/frontpocket.ini /opt/FrontPocket/frontpocket.ini
```

### Adding custom voices

Copy your `.safetensors` voice embedding files to `/var/lib/FrontPocket/voices/`,
then add entries to the `[voices]` section of `frontpocket.ini`:

```ini
[voices]
alba   = alba
maria  = /var/lib/FrontPocket/voices/maria.safetensors
```

---

## 7. Install the systemd service

```bash
sudo cp /opt/FrontPocket/frontpocket.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable frontpocket
sudo systemctl start frontpocket
```

Check that the service started successfully (model loading takes up to 60 seconds):

```bash
sudo systemctl status frontpocket
```

Follow live logs:

```bash
journalctl -u frontpocket -f
```

---

## 8. Using the client

After installing via `pip install -e`, the `fp` command is available inside the
venv. To use it system-wide without activating the venv, symlink it:

```bash
sudo ln -s /opt/FrontPocket/venv/bin/fp /usr/local/bin/fp
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
fp --voice masha
fp --speed 1.5
fp --status
fp --interruptwith "Dinner is ready"
fp --version
```

---

## Upgrading

```bash
cd /opt/FrontPocket
sudo -u frontpocket git pull
sudo -u frontpocket /opt/FrontPocket/venv/bin/pip install -r requirements.txt
sudo systemctl restart frontpocket
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
