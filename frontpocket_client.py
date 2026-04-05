"""
frontpocket_client.py - FrontPocket CLI client. Sends text or commands to
the FrontPocket server over TCP.

Usage examples:
    fp                                   # send clipboard contents
    fp "Hello world"                     # send inline text
    fp --file article.txt                # send contents of a file
    fp --pause
    fp --resume
    fp --next
    fp --back
    fp --voice masha
    fp --speed 1.5
    fp --status
    fp --ping                            # check server is reachable
    fp --interruptwith "Dinner is ready"
    fp --interruptwith alert.txt
    fp --list-voices
    fp --port 5563 --pause               # talk to a non-default instance
    fp --version
"""

import argparse
import os
import platform
import socket
import subprocess
import sys

from frontpocket_shared import (
    BUILTIN_VOICES,
    CMD_BACK, CMD_INTERRUPT, CMD_NEXT, CMD_PAUSE, CMD_PING,
    CMD_RESUME, CMD_SPEED, CMD_STATUS, CMD_VOICE,
    MAX_SPEED, MESSAGE_ENCODING, MIN_SPEED, SOCKET_BUFFER, VERSION,
    get_settings, get_voices, load_config, setup_logging,
)

# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def get_clipboard(quiet: bool) -> str | None:
    """Read clipboard text, supporting X11, Wayland, Windows, and macOS."""
    system = platform.system()

    if system == "Windows":
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            if not quiet:
                print("Error: pyperclip is not installed. Run: pip install pyperclip")
            return None
        except Exception as e:
            if not quiet:
                print(f"Error reading clipboard on Windows: {e}")
            return None

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, check=True
            )
            return result.stdout
        except FileNotFoundError:
            if not quiet:
                print("Error: pbpaste not found.")
            return None
        except subprocess.CalledProcessError as e:
            if not quiet:
                print(f"Error reading macOS clipboard: {e}")
            return None

    # Linux — detect Wayland vs X11
    is_wayland = os.environ.get("WAYLAND_DISPLAY") is not None

    if is_wayland:
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True, text=True, check=True,
            )
            return result.stdout
        except FileNotFoundError:
            if not quiet:
                print("Error: wl-paste not found. Install wl-clipboard.")
            return None
        except subprocess.CalledProcessError as e:
            if not quiet:
                print(f"Error reading Wayland clipboard: {e}")
            return None
    else:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, check=True,
            )
            return result.stdout.strip()
        except FileNotFoundError:
            if not quiet:
                print("Error: xclip not found. Install xclip.")
            return None
        except subprocess.CalledProcessError as e:
            if not quiet:
                print(f"Error reading X11 clipboard: {e}")
            return None


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def read_file(path: str, quiet: bool) -> str | None:
    """Read a text file and return its contents."""
    if not os.path.isfile(path):
        if not quiet:
            print(f"Error: File not found: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        if not quiet:
            print(f"Error reading file {path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Socket send
# ---------------------------------------------------------------------------

def send_message(message: str, host: str, port: int, quiet: bool) -> bool:
    """Send a single message to the server. Returns True on success."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((host, port))
            sock.sendall(message.encode(MESSAGE_ENCODING))
        return True
    except ConnectionRefusedError:
        if not quiet:
            print(f"Error: Could not connect to TTS server at {host}:{port}. Is it running?")
        return False
    except Exception as e:
        if not quiet:
            print(f"Error sending to server: {e}")
        return False


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(settings: dict, voices: dict):
    valid_voices = list(voices.keys())

    parser = argparse.ArgumentParser(
        description="FrontPocket TTS client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--version", action="version", version=f"FrontPocket {VERSION}",
    )

    # Connection
    parser.add_argument(
        "--port", "-P",
        type=int,
        default=settings["port"],
        help=f"Server port (default: {settings['port']})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=settings["host"],
        help=f"Server host (default: {settings['host']})",
    )

    # Output control
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress all client output",
    )

    # Text input (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "text",
        nargs="?",
        default=None,
        help="Text to speak (if omitted, clipboard is used)",
    )
    input_group.add_argument(
        "--file", "-f",
        type=str,
        metavar="PATH",
        help="Path to a text file to speak",
    )

    # Playback commands (mutually exclusive with each other and with text input)
    cmd_group = parser.add_mutually_exclusive_group()
    cmd_group.add_argument("--pause",  action="store_true", help="Pause playback")
    cmd_group.add_argument("--resume", action="store_true", help="Resume playback")
    cmd_group.add_argument("--next",   action="store_true", help="Skip to next chunk")
    cmd_group.add_argument("--back",   action="store_true", help="Go back one chunk")
    cmd_group.add_argument("--status", action="store_true", help="Speak current status")
    cmd_group.add_argument("--ping",   action="store_true", help="Check server is reachable (exit 0 = up)")
    cmd_group.add_argument("--list-voices", action="store_true", help="List configured voices")
    cmd_group.add_argument(
        "--interruptwith", "-i",
        type=str,
        metavar="TEXT_OR_FILE",
        help="Interrupt current playback with this text (or path to a text file)",
    )

    # Settings commands
    parser.add_argument(
        "--voice", "-v",
        type=str,
        metavar="NAME",
        help=f"Change voice. Available: {', '.join(valid_voices)}" if valid_voices else "Change voice",
    )
    parser.add_argument(
        "--speed", "-s",
        type=float,
        metavar="SPEED",
        help=f"Change speed ({MIN_SPEED}–{MAX_SPEED})",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_args(args, settings: dict, voices: dict, quiet: bool) -> bool:
    """Validate speed and voice arguments. Returns False if invalid."""
    if args.speed is not None:
        if not MIN_SPEED <= args.speed <= MAX_SPEED:
            if not quiet:
                print(f"Error: Speed must be between {MIN_SPEED} and {MAX_SPEED}")
            return False

    if args.voice is not None:
        if args.voice.lower() not in voices:
            if not quiet:
                valid = ", ".join(voices.keys())
                print(f"Error: Unknown voice '{args.voice}'. Available: {valid}")
            return False

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config   = load_config()
    settings = get_settings(config)
    voices   = get_voices(config)

    args  = parse_args(settings, voices)
    quiet = args.quiet
    host  = args.host
    port  = args.port

    # Validate before connecting
    if not validate_args(args, settings, voices, quiet):
        sys.exit(1)

    # Build list of messages to send (settings changes may accompany text/commands)
    messages = []
    settings_only = (args.voice is not None or args.speed is not None) and \
                    not any([args.pause, args.resume, args.next, args.back,
                             args.status, args.ping, args.list_voices,
                             args.interruptwith, args.file, args.text])

    # --- Settings changes ---
    if args.voice is not None:
        messages.append(f"{CMD_VOICE} {args.voice.lower()}")

    if args.speed is not None:
        messages.append(f"{CMD_SPEED} {args.speed}")

    # --- Commands ---
    if args.list_voices:
        print("Configured voices:")
        for name, path in voices.items():
            tag = "(built-in)" if path.lower() in BUILTIN_VOICES else path
            print(f"  {name:<12} {tag}")
        sys.exit(0)

    if args.ping:
        if not send_message(CMD_PING, host, port, quiet):
            sys.exit(1)
        if not quiet:
            print(f"FrontPocket server is up on {host}:{port}")
        sys.exit(0)

    elif args.pause:
        messages.append(CMD_PAUSE)

    elif args.resume:
        messages.append(CMD_RESUME)

    elif args.next:
        messages.append(CMD_NEXT)

    elif args.back:
        messages.append(CMD_BACK)

    elif args.status:
        messages.append(CMD_STATUS)

    elif args.interruptwith is not None:
        # Accept a file path or inline text
        interrupt_text = args.interruptwith
        if os.path.isfile(interrupt_text):
            interrupt_text = read_file(interrupt_text, quiet)
            if interrupt_text is None:
                sys.exit(1)
        messages.append(f"{CMD_INTERRUPT} {interrupt_text}")

    # --- Text input (only if no playback command was given and not settings-only) ---
    elif args.file is not None:
        text = read_file(args.file, quiet)
        if text is None:
            sys.exit(1)
        if not quiet:
            print(f"Speaking text from file: {args.file}")
        messages.append(text)

    elif args.text is not None:
        messages.append(args.text)

    elif not settings_only:
        # Default: clipboard
        text = get_clipboard(quiet)
        if text is None:
            sys.exit(1)
        if not text.strip():
            if not quiet:
                print("Error: Clipboard is empty.")
            sys.exit(1)
        if not quiet:
            print("Speaking text from clipboard")
        messages.append(text)

    # --- Send all messages ---
    for message in messages:
        if not send_message(message, host, port, quiet):
            sys.exit(1)


if __name__ == "__main__":
    main()
