"""
frontpocket_shared.py - Shared constants, config loading, and command definitions
for FrontPocket server and client.
"""

import configparser
import logging
import os
import sys

VERSION = "1.4.0"

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "frontpocket.ini"

def find_config() -> str:
    """Locate frontpocket.ini: first next to this file, then in cwd."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME),
        os.path.join(os.getcwd(), CONFIG_FILENAME),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    print(f"Error: Could not find {CONFIG_FILENAME}", file=sys.stderr)
    sys.exit(1)


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(find_config())
    return config


# ---------------------------------------------------------------------------
# Defaults (used if frontpocket.ini is missing a value)
# ---------------------------------------------------------------------------

DEFAULT_HOST            = "127.0.0.1"
DEFAULT_LOG_LEVEL       = "INFO"
DEFAULT_INTERRUPT_SOUND = ""
DEFAULT_DEBUG_DIR       = ""
DEFAULT_SENTENCE_GAP_MS = 75

# Voice names that are built in to the TTS engine and need no file path.
BUILTIN_VOICES = {
    "alba", "marius", "javert", "jean",
    "fantine", "cosette", "eponine", "azelma",
}
DEFAULT_PORT            = 5562
DEFAULT_VOICE           = "alba"
DEFAULT_SPEED           = 1.0
DEFAULT_LANGUAGE        = "en"
DEFAULT_INTERRUPT_PAUSE = 1.0
DEFAULT_LOOKAHEAD       = 3
DEFAULT_MAX_CHUNK_SECS  = 15

# ---------------------------------------------------------------------------
# Helpers to pull typed values from config
# ---------------------------------------------------------------------------

def get_settings(config: configparser.ConfigParser) -> dict:
    s = config["settings"] if "settings" in config else {}
    return {
        "host":             s.get("host",              DEFAULT_HOST),
        "port":             int(s.get("port",          DEFAULT_PORT)),
        "default_voice":    s.get("default_voice",     DEFAULT_VOICE),
        "default_speed":    float(s.get("default_speed", DEFAULT_SPEED)),
        "language":         s.get("language",          DEFAULT_LANGUAGE),
        "interrupt_pause":  float(s.get("interrupt_pause", DEFAULT_INTERRUPT_PAUSE)),
        "lookahead_chunks": int(s.get("lookahead_chunks",  DEFAULT_LOOKAHEAD)),
        "max_chunk_duration": int(s.get("max_chunk_duration", DEFAULT_MAX_CHUNK_SECS)),
        "log_level":          s.get("log_level", DEFAULT_LOG_LEVEL).upper(),
        "interrupt_sound":    s.get("interrupt_sound", DEFAULT_INTERRUPT_SOUND),
        "debug_dir":          s.get("debug_dir", DEFAULT_DEBUG_DIR),
        "sentence_gap_ms":    int(s.get("sentence_gap_ms", DEFAULT_SENTENCE_GAP_MS)),
    }


def get_voices(config: configparser.ConfigParser) -> dict:
    """Return {shortname: path_or_builtin} from [voices] section."""
    if "voices" not in config:
        return {}
    return dict(config["voices"])


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

VALID_LOG_LEVELS = {"DEBUG", "INFO", "ERROR"}

def setup_logging(level_str: str = DEFAULT_LOG_LEVEL) -> logging.Logger:
    """
    Initialise and return the root 'tts' logger.

    level_str: "DEBUG", "INFO", or "ERROR" (case-insensitive).
    Call once at startup; subsequent calls to logging.getLogger('tts')
    return the same instance.
    """
    level_str = level_str.upper()
    if level_str not in VALID_LOG_LEVELS:
        level_str = DEFAULT_LOG_LEVEL

    level = getattr(logging, level_str)
    logger = logging.getLogger("tts")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger


# ---------------------------------------------------------------------------
# Command constants
# ---------------------------------------------------------------------------

# Prefix that distinguishes a command from plain text
CMD_PREFIX = "!"

# Canonical command strings (what the client sends over the socket)
CMD_PAUSE         = "!pause"
CMD_RESUME        = "!resume"
CMD_NEXT          = "!next"
CMD_BACK          = "!back"
CMD_STATUS        = "!status"
CMD_VOICE         = "!voice"
CMD_SPEED         = "!speed"
CMD_INTERRUPT     = "!interruptwith"
CMD_PING          = "!ping"
CMD_RELOAD        = "!reload"

# Short aliases (client maps these before sending the canonical form)
CMD_ALIASES = {
    "!p": CMD_PAUSE,
    "!r": CMD_RESUME,
    "!n": CMD_NEXT,
    "!b": CMD_BACK,
    "!i": CMD_INTERRUPT,
}

# Commands that carry no argument
SIMPLE_COMMANDS = {CMD_PAUSE, CMD_RESUME, CMD_NEXT, CMD_BACK, CMD_STATUS}

# Speed limits
MIN_SPEED = 0.5
MAX_SPEED = 3.0

# Socket / protocol
MESSAGE_ENCODING = "utf-8"
SOCKET_BUFFER    = 65536   # bytes per recv call
