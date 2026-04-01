"""
frontpocket_server.py - FrontPocket TTS server. Loads the model once, then
listens on a TCP socket for text to speak or commands to control playback.

Usage:
    python frontpocket_server.py [--log-level DEBUG|INFO|ERROR] [--version]
"""

import argparse
import os
import queue
import re
import socket
import threading
import time

import numpy as np
import pysbd
import pyrubberband as rb
import scipy.io.wavfile
import sounddevice as sd
from pocket_tts import TTSModel

from frontpocket_shared import (
    BUILTIN_VOICES,
    CMD_ALIASES, CMD_BACK, CMD_INTERRUPT, CMD_NEXT, CMD_PAUSE,
    CMD_PING, CMD_PREFIX, CMD_RESUME, CMD_SPEED, CMD_STATUS, CMD_VOICE,
    MAX_SPEED, MESSAGE_ENCODING, MIN_SPEED, SOCKET_BUFFER, VERSION,
    get_settings, get_voices, load_config, setup_logging,
)

# Sentinel value for chunk["audio"] when generation permanently failed.
# Distinct from None ("not yet generated") so the playback thread can skip immediately.
AUDIO_SKIP = "SKIP"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ServerState:
    """All mutable server state, protected by a single lock."""

    def __init__(self, settings: dict, voices: dict):
        self.lock = threading.Lock()

        # Config
        self.host             = settings["host"]
        self.port             = settings["port"]
        self.language         = settings["language"]
        self.lookahead        = settings["lookahead_chunks"]
        self.max_chunk_secs   = settings["max_chunk_duration"]
        self.interrupt_pause  = settings["interrupt_pause"]
        self.interrupt_sound  = settings["interrupt_sound"]   # path to WAV or ""
        self.debug_dir        = settings["debug_dir"]         # path or ""
        self.voices           = voices                          # {name: path_or_builtin}

        # Playback settings
        self.voice_name       = settings["default_voice"]
        self.speed            = settings["default_speed"]

        # Playback state
        self.status           = "idle"          # "idle" | "playing" | "paused"
        self.is_interrupting  = False

        # Chunk management
        # chunks: list of {"text": str, "audio": np.ndarray | None}
        self.chunks           = []
        self.chunk_index      = 0              # currently playing chunk
        self.sample_offset    = 0             # sample position within current chunk

        # Signals between threads
        self.skip_event            = threading.Event()   # set to interrupt current sd.play
        self.new_text_event        = threading.Event()   # set when chunks list is replaced
        self.pregen_event          = threading.Event()   # set to wake pre-gen thread
        self.settings_changed_event = threading.Event()  # set on voice/speed change to abort pregen loop

        # Model (set after load)
        self.tts_model        = None
        self.sample_rate      = None
        self.voice_state      = None            # current loaded voice


# ---------------------------------------------------------------------------
# Voice loading
# ---------------------------------------------------------------------------

def load_voice(state: ServerState, voice_name: str, log) -> bool:
    """Load a voice by short name. Returns True on success."""
    path = state.voices.get(voice_name.lower())
    if path is None:
        log.info("Invalid voice: %s", voice_name)
        return False
    try:
        state.voice_state = state.tts_model.get_state_for_audio_prompt(path)
        state.voice_name  = voice_name.lower()
        log.debug("Loaded voice: %s (%s)", voice_name, path)
        return True
    except Exception as e:
        log.info("Failed to load voice %s: %s", voice_name, e)
        return False


# ---------------------------------------------------------------------------
# Chunk text cleaning
# ---------------------------------------------------------------------------

_TRAIL_STRIP = re.compile(r'[\s.,\-\u2013\u2014\u201c\u201d"\']+$')
# Also strip leading quotes
_LEAD_STRIP  = re.compile(r'^[\s\u201c\u201d"\']+')

def clean_chunk_text(text: str) -> str:
    """Strip leading/trailing punctuation that causes TTS artifacts."""
    text = _LEAD_STRIP.sub("", text)
    text = _TRAIL_STRIP.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, language: str, max_secs: int, sample_rate: int,
               state: ServerState, log) -> list:
    """
    Split text into sentence-level chunks using pysbd.
    Returns list of {"text": str, "audio": None}.
    Very long sentences are split further on comma boundaries.
    """
    segmenter = pysbd.Segmenter(language=language, clean=False)
    sentences = segmenter.segment(text.strip())

    chunks = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # Rough character-based heuristic for over-long sentences:
        # ~20 chars/sec of speech is a reasonable upper bound.
        max_chars = max_secs * 20
        if len(sentence) <= max_chars:
            cleaned = clean_chunk_text(sentence)
            if cleaned:
                chunks.append({"text": cleaned, "audio": None, "voice": None, "speed": None})
        else:
            # Split on commas
            parts = [p.strip() for p in sentence.split(",") if p.strip()]
            current = ""
            for part in parts:
                if len(current) + len(part) + 2 <= max_chars:
                    current = (current + ", " + part).lstrip(", ")
                else:
                    if current:
                        cleaned = clean_chunk_text(current)
                        if cleaned:
                            chunks.append({"text": cleaned, "audio": None, "voice": None, "speed": None})
                    current = part
            if current:
                cleaned = clean_chunk_text(current)
                if cleaned:
                    chunks.append({"text": cleaned, "audio": None, "voice": None, "speed": None})

    log.debug("Chunked into %d pieces", len(chunks))
    return chunks


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

def generate_chunk_audio(state: ServerState, chunk_index: int, log) -> bool:
    """
    Generate audio for chunks[chunk_index] if not already generated with
    the current voice and speed. Returns True on success.
    Marks chunk["audio"] = AUDIO_SKIP on permanent failure so the playback
    thread does not stall waiting for it.
    """
    with state.lock:
        if chunk_index >= len(state.chunks):
            return False
        chunk      = state.chunks[chunk_index]
        text       = chunk["text"]
        voice_name = state.voice_name
        speed      = state.speed
        # Already done with current settings?
        if (chunk["audio"] is not None and
                chunk["audio"] is not AUDIO_SKIP and
                chunk["voice"] == voice_name and
                chunk["speed"] == speed):
            return True
        # Permanently failed — don't retry
        if chunk["audio"] is AUDIO_SKIP:
            return False
        vs        = state.voice_state
        model     = state.tts_model
        sr        = state.sample_rate
        debug_dir = state.debug_dir

    if not text:
        log.info("Chunk %d is empty after cleaning, marking skip", chunk_index)
        with state.lock:
            if chunk_index < len(state.chunks):
                state.chunks[chunk_index]["audio"] = AUDIO_SKIP
        return False

    log.info("Generating chunk %d (voice=%s speed=%.2f)%s",
             chunk_index, voice_name, speed,
             f": {repr(text)}" if log.isEnabledFor(10) else "")

    # Write debug chunk file if debug_dir is configured
    if debug_dir and log.isEnabledFor(10):
        try:
            os.makedirs(debug_dir, exist_ok=True)
            chunk_path = os.path.join(debug_dir, f"chunk_{chunk_index:03d}.txt")
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            log.debug("Could not write debug chunk file: %s", e)

    try:
        audio = model.generate_audio(vs, text).numpy()
        if speed != 1.0:
            audio = rb.time_stretch(audio, sr, rate=speed)
        audio = audio.astype(np.float32)
    except Exception as e:
        log.info("Error generating chunk %d: %s", chunk_index, e)
        with state.lock:
            if chunk_index < len(state.chunks) and state.chunks[chunk_index]["text"] == text:
                state.chunks[chunk_index]["audio"] = AUDIO_SKIP
        return False

    with state.lock:
        # Only store if the chunk still belongs to this text and settings haven't changed
        if (chunk_index < len(state.chunks) and
                state.chunks[chunk_index]["text"] == text and
                state.voice_name == voice_name and
                state.speed == speed):
            state.chunks[chunk_index]["audio"] = audio
            state.chunks[chunk_index]["voice"] = voice_name
            state.chunks[chunk_index]["speed"] = speed
            return True
    return False


# ---------------------------------------------------------------------------
# Pre-generation thread
# ---------------------------------------------------------------------------

def pregen_worker(state: ServerState, log):
    """
    Background thread: keeps lookahead_chunks ahead of current playback
    position pre-generated.
    """
    while True:
        state.pregen_event.wait()
        state.pregen_event.clear()
        state.settings_changed_event.clear()  # clear any stale abort signal before starting

        try:
            with state.lock:
                idx       = state.chunk_index
                lookahead = state.lookahead
                total     = len(state.chunks)

            for i in range(idx, min(idx + lookahead + 1, total)):
                # Abort immediately if voice/speed changed or new text arrived
                if state.settings_changed_event.is_set():
                    log.debug("Pregen aborting at chunk %d due to settings change", i)
                    break
                with state.lock:
                    if state.new_text_event.is_set():
                        break
                    if i >= len(state.chunks):
                        break
                    c = state.chunks[i]
                    # Done = has audio with current voice+speed (or permanently skipped)
                    already_done = (
                        c["audio"] is AUDIO_SKIP or
                        (c["audio"] is not None and
                         c["voice"] == state.voice_name and
                         c["speed"] == state.speed)
                    )
                if not already_done:
                    generate_chunk_audio(state, i, log)

        except Exception as e:
            log.info("Pre-gen worker error: %s", e)


# ---------------------------------------------------------------------------
# Playback thread
# ---------------------------------------------------------------------------

def playback_worker(state: ServerState, log):
    """
    Background thread: plays chunks sequentially, respecting pause,
    next, back, and new-text signals.
    """
    while True:
        # Wait until there is something to play
        while True:
            with state.lock:
                has_work = (state.status == "playing"
                            and state.chunk_index < len(state.chunks))
            if has_work:
                break
            time.sleep(0.05)

        try:
            with state.lock:
                idx    = state.chunk_index
                total  = len(state.chunks)
                offset = state.sample_offset

            if idx >= total:
                with state.lock:
                    state.status = "idle"
                continue

            # Ensure audio is ready and matches current voice+speed settings
            with state.lock:
                audio      = state.chunks[idx]["audio"]
                cur_voice  = state.voice_name
                cur_speed  = state.speed
                if (audio is not None and
                        audio is not AUDIO_SKIP and
                        (state.chunks[idx]["voice"] != cur_voice or
                         state.chunks[idx]["speed"] != cur_speed)):
                    # Stale audio from old voice/speed — treat as not yet generated
                    state.chunks[idx]["audio"] = None
                    state.chunks[idx]["voice"] = None
                    state.chunks[idx]["speed"] = None
                    audio = None

            # Wait for audio — generation is exclusively the pregen thread's job
            if audio is None:
                log.debug("Waiting for chunk %d to be generated", idx)
                state.pregen_event.set()
                wait_start = time.time()
                while audio is None:
                    time.sleep(0.05)
                    with state.lock:
                        if idx >= len(state.chunks):
                            break
                        audio = state.chunks[idx]["audio"]
                        if state.status != "playing":
                            break
                        # Also break out if audio arrived but is stale (settings changed again)
                        if (audio is not None and
                                audio is not AUDIO_SKIP and
                                (state.chunks[idx]["voice"] != state.voice_name or
                                 state.chunks[idx]["speed"] != state.speed)):
                            audio = None
                            state.chunks[idx]["audio"] = None
                            state.chunks[idx]["voice"] = None
                            state.chunks[idx]["speed"] = None
                    if time.time() - wait_start > 30:
                        log.info("Timed out waiting for chunk %d", idx)
                        break

            if audio is None or audio is AUDIO_SKIP:
                log.info("Skipping chunk %d", idx)
                with state.lock:
                    state.chunk_index += 1
                    if state.chunk_index >= len(state.chunks):
                        state.status = "idle"
                        log.info("Playback complete")
                continue

            # Trim to resume offset
            play_audio = audio[offset:]
            if len(play_audio) == 0:
                with state.lock:
                    state.chunk_index += 1
                    state.sample_offset = 0
                continue

            log.info("Playing chunk %d of %d (voice=%s speed=%.2f)",
                     idx + 1, total, state.voice_name, state.speed)

            state.skip_event.clear()

            finished_naturally = False
            start_time         = time.time()
            sr                 = state.sample_rate

            # Brief stop + settle before starting new stream to avoid ALSA timeout
            sd.stop()
            time.sleep(0.05)
            sd.play(play_audio, samplerate=sr)

            # Poll while playing
            while sd.get_stream().active:
                # Check for pause
                with state.lock:
                    current_status = state.status

                if current_status == "paused" or state.skip_event.is_set():
                    sd.stop()
                    break

                time.sleep(0.05)
            else:
                finished_naturally = True

            # Update sample offset
            elapsed_samples = int((time.time() - start_time) * sr) + offset

            with state.lock:
                if finished_naturally:
                    state.chunk_index  += 1
                    state.sample_offset = 0
                    if state.chunk_index >= len(state.chunks):
                        state.status = "idle"
                        log.info("Playback complete")
                elif state.skip_event.is_set():
                    # next/back already updated chunk_index
                    state.sample_offset = 0
                elif state.status == "paused":
                    state.sample_offset = min(elapsed_samples, len(audio) - 1)

            # Wake pre-gen thread
            state.pregen_event.set()

        except Exception as e:
            log.info("Playback worker error: %s", e)
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------

def handle_command(raw: str, state: ServerState, log):
    """Dispatch a ! command received from the client."""

    # Resolve aliases
    parts   = raw.strip().split(None, 1)
    keyword = parts[0].lower()
    arg     = parts[1] if len(parts) > 1 else ""
    keyword = CMD_ALIASES.get(keyword, keyword)

    if keyword == CMD_PAUSE:
        log.info("Pause command received")
        with state.lock:
            if state.status == "playing":
                state.status = "paused"

    elif keyword == CMD_RESUME:
        log.info("Resume command received")
        with state.lock:
            if state.status == "paused":
                # If voice/speed changed, discard audio for current+ahead chunks
                # (they were already invalidated by handle_voice/handle_speed)
                state.status = "playing"
        state.pregen_event.set()

    elif keyword == CMD_NEXT:
        log.info("Next command received")
        with state.lock:
            if state.chunk_index + 1 < len(state.chunks):
                state.chunk_index  += 1
                state.sample_offset = 0
                state.skip_event.set()
                state.status = "playing"
        state.pregen_event.set()

    elif keyword == CMD_BACK:
        log.info("Back command received")
        with state.lock:
            if state.chunk_index > 0:
                state.chunk_index  -= 1
                state.sample_offset = 0
                state.skip_event.set()
                state.status = "playing"
        state.pregen_event.set()

    elif keyword == CMD_VOICE:
        if not arg:
            log.info("!voice requires a name argument")
            return
        with state.lock:
            old_voice = state.voice_name
        ok = load_voice(state, arg.strip(), log)
        if ok:
            log.info("Voice changed to: %s", arg.strip())
            invalidate_from_current(state, log)
        else:
            # load_voice already logged the error
            pass

    elif keyword == CMD_SPEED:
        try:
            new_speed = float(arg.strip())
        except ValueError:
            log.info("Invalid speed value: %s", arg)
            return
        if not MIN_SPEED <= new_speed <= MAX_SPEED:
            log.info("Speed out of range (%.1f-%.1f): %s", MIN_SPEED, MAX_SPEED, arg)
            return
        with state.lock:
            state.speed = new_speed
        log.info("Speed changed to: %.2f", new_speed)
        invalidate_from_current(state, log)

    elif keyword == CMD_STATUS:
        log.info("Status command received")
        with state.lock:
            voice   = state.voice_name
            speed   = state.speed
            status  = state.status
            is_int  = state.is_interrupting
        text = f"Voice: {voice}. Speed: {speed:.1f}. Status: {status}."
        if is_int:
            return  # already mid-interrupt, ignore
        _do_interrupt(text, state, log)

    elif keyword == CMD_INTERRUPT:
        if not arg:
            log.info("!interruptwith requires text")
            return
        with state.lock:
            if state.is_interrupting:
                log.debug("Ignoring interrupt — already interrupting")
                return
        log.info("Interrupt command received")
        _do_interrupt(arg.strip(), state, log)

    elif keyword == CMD_PING:
        log.debug("Ping received")

    else:
        log.info("Unknown command: %s", keyword)


def invalidate_from_current(state: ServerState, log):
    """
    Stop current playback and wake the pregen thread after a voice/speed change.
    Stale audio is detected by voice/speed tag mismatch rather than wiping arrays,
    so we avoid races between the command handler and pregen threads.
    """
    with state.lock:
        state.sample_offset = 0
        state.skip_event.set()
        state.settings_changed_event.set()
    state.pregen_event.set()


def _play_wav_file(path: str, log):
    """Play a WAV file synchronously. Silently skips if file missing or unreadable."""
    try:
        sr, data = scipy.io.wavfile.read(path)
        # Normalise to float32 [-1, 1]
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32)
        # Mix to mono if stereo
        if data.ndim == 2:
            data = data.mean(axis=1)
        sd.stop()
        time.sleep(0.05)
        sd.play(data, samplerate=sr)
        sd.wait()
    except Exception as e:
        log.debug("Could not play interrupt sound %s: %s", path, e)


def _do_interrupt(text: str, state: ServerState, log):
    """Pause, play alert tone, speak interruption text, resume."""
    with state.lock:
        was_playing = state.status == "playing"
        state.is_interrupting = True
        if was_playing:
            state.status = "paused"
            state.skip_event.set()

    pause_secs    = state.interrupt_pause
    interrupt_sound = state.interrupt_sound
    time.sleep(pause_secs)

    # Play alert tone if configured
    if interrupt_sound:
        _play_wav_file(interrupt_sound, log)

    # Generate and play interruption inline (blocking)
    try:
        with state.lock:
            vs    = state.voice_state
            model = state.tts_model
            speed = state.speed
            sr    = state.sample_rate

        audio = model.generate_audio(vs, text).numpy()
        if speed != 1.0:
            audio = rb.time_stretch(audio, sr, rate=speed)
        audio = audio.astype(np.float32)
        sd.stop()
        time.sleep(0.05)
        sd.play(audio, samplerate=sr)
        sd.wait()
    except Exception as e:
        log.info("Interrupt playback error: %s", e)

    time.sleep(pause_secs)

    with state.lock:
        state.is_interrupting = False
        if was_playing:
            state.status = "playing"
    if was_playing:
        state.pregen_event.set()


# ---------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------

def handle_text(text: str, source: str, state: ServerState, log):
    """Replace current queue with new text and begin playback."""
    log.info("Speaking text from %s", source)

    sd.stop()

    chunks = chunk_text(
        text,
        language=state.language,
        max_secs=state.max_chunk_secs,
        sample_rate=state.sample_rate,
        state=state,
        log=log,
    )

    if not chunks:
        log.info("No speakable text found")
        return

    with state.lock:
        state.chunks        = chunks
        state.chunk_index   = 0
        state.sample_offset = 0
        state.status        = "playing"
        state.new_text_event.set()
        state.new_text_event.clear()
        state.skip_event.set()   # interrupt any current sd.play

    # Trigger pre-gen immediately so chunk 1+ are ready before chunk 0 finishes
    state.pregen_event.set()


# ---------------------------------------------------------------------------
# Socket listener
# ---------------------------------------------------------------------------

def handle_connection(conn, addr, state: ServerState, log):
    """Handle one client connection."""
    try:
        data = b""
        while True:
            chunk = conn.recv(SOCKET_BUFFER)
            if not chunk:
                break
            data += chunk
        message = data.decode(MESSAGE_ENCODING).strip()
        if not message:
            return

        log.debug("Received from %s: %r", addr, message[:120])

        if message.startswith(CMD_PREFIX):
            handle_command(message, state, log)
        else:
            handle_text(message, "client", state, log)

    except Exception as e:
        log.info("Connection error from %s: %s", addr, e)
    finally:
        conn.close()


def run_server(state: ServerState, log):
    """Main TCP accept loop."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((state.host, state.port))
        srv.listen(5)
        log.info("Listening on %s:%d", state.host, state.port)

        while True:
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=handle_connection,
                    args=(conn, addr, state, log),
                    daemon=True,
                )
                t.start()
            except Exception as e:
                log.info("Accept error: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="FrontPocket TTS server")
    parser.add_argument(
        "--version", action="version", version=f"FrontPocket {VERSION}",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "ERROR"],
        help="Override log level from frontpocket.ini",
    )
    return parser.parse_args()


def main():
    args    = parse_args()
    config  = load_config()
    settings = get_settings(config)
    voices   = get_voices(config)

    log_level = args.log_level or settings["log_level"]
    log = setup_logging(log_level)

    log.info("FrontPocket %s starting", VERSION)

    # Validate voice paths — warn on missing files, skip built-ins
    for name, path in voices.items():
        if path.lower() in BUILTIN_VOICES or not path.startswith("/"):
            continue  # built-in or HuggingFace reference, no file to check
        if not os.path.isfile(path):
            log.info("Warning: voice '%s' file not found: %s", name, path)

    # Clear stale debug chunk files
    if settings["debug_dir"] and log.isEnabledFor(10):
        debug_dir = settings["debug_dir"]
        try:
            os.makedirs(debug_dir, exist_ok=True)
            for f in os.listdir(debug_dir):
                if f.startswith("chunk_") and f.endswith(".txt"):
                    os.remove(os.path.join(debug_dir, f))
            log.debug("Cleared debug chunk files in %s", debug_dir)
        except Exception as e:
            log.debug("Could not clear debug dir: %s", e)

    log.info("Loading TTS model...")
    tts_model = TTSModel.load_model()
    log.info("Model loaded")

    state             = ServerState(settings, voices)
    state.tts_model   = tts_model
    state.sample_rate = tts_model.sample_rate

    # Load default voice
    if not load_voice(state, state.voice_name, log):
        log.info("Default voice '%s' not found in config — check frontpocket.ini [voices]",
                 state.voice_name)
        return

    # Start background threads
    threading.Thread(target=pregen_worker,   args=(state, log), daemon=True).start()
    threading.Thread(target=playback_worker, args=(state, log), daemon=True).start()

    log.info("Server ready (voice=%s, speed=%.1f)", state.voice_name, state.speed)

    try:
        run_server(state, log)
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    main()
