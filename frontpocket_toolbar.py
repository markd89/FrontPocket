#!/usr/bin/env python3
"""
frontpocket_toolbar.py - Floating PyQt6 toolbar for FrontPocket TTS.

Reads voices and appearance from /etc/FrontPocket/frontpocket.ini.
Saves per-user state (voice, speed, position) to ~/.config/frontpocket/toolbar.ini.

Usage:
    python3 frontpocket_toolbar.py
"""

import configparser
import os
import subprocess
import sys

from PyQt6.QtCore import (Qt, QPropertyAnimation, QEasingCurve, QSize, QTimer)

# Qt's maximum widget dimension — used to release fixed size constraints
QWIDGETSIZE_MAX = 16777215
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout,
                              QPushButton, QComboBox, QLabel, QMessageBox)

from frontpocket_shared import BUILTIN_VOICES, VERSION, find_config

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_state_path() -> str:
    """Return path to frontpocket_state.ini, next to frontpocket.ini."""
    return os.path.join(os.path.dirname(find_config()), "frontpocket_state.ini")


def load_fp_config() -> configparser.ConfigParser:
    """Load the main FrontPocket config using the same search logic as the server."""
    config = configparser.ConfigParser()
    config.read(find_config())
    return config


def load_state() -> configparser.ConfigParser:
    """Load toolbar state from frontpocket_state.ini."""
    state = configparser.ConfigParser()
    state_path = get_state_path()
    if os.path.exists(state_path):
        state.read(state_path)
    if not state.has_section("CurrentSettings"):
        state.add_section("CurrentSettings")
    return state


def save_state(state: configparser.ConfigParser):
    """Save toolbar state to frontpocket_state.ini."""
    try:
        with open(get_state_path(), "w") as f:
            state.write(f)
    except Exception as e:
        print(f"Warning: could not save state: {e}")


def get_voices(fp_config: configparser.ConfigParser) -> list[str]:
    """Return list of voice short names from [voices] section."""
    if not fp_config.has_section("voices"):
        return []
    return list(fp_config["voices"].keys())


def get_speed_choices(fp_config: configparser.ConfigParser) -> list[str]:
    """Return list of speed choices from [Toolbar] section."""
    raw = fp_config.get("Toolbar", "speed_choices", fallback="1.0,1.1,1.2,1.4,1.6,1.8,2.0,0.75")
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_speed_defaults(fp_config: configparser.ConfigParser) -> dict:
    """Return {voice: speed} from [SpeedDefaults] section."""
    if not fp_config.has_section("SpeedDefaults"):
        return {}
    return dict(fp_config["SpeedDefaults"])


def validate_position(x: int, y: int) -> bool:
    """Return True if (x, y) falls within any connected screen."""
    for screen in QApplication.screens():
        if screen.geometry().contains(x, y):
            return True
    return False


BUTTON_STYLE = """
    QPushButton {
        border: 1px solid #666;
        border-radius: 4px;
        background-color: #333;
        color: white;
        font-size: 16px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #555;
        border: 1px solid #888;
    }
    QPushButton:pressed {
        background-color: #222;
        border: 1px solid #999;
    }
"""

EXPANDED_STYLE = """
    QWidget {
        background-color: #2b2b2b;
        border: 1px solid #666;
        border-top: none;
    }
    QLabel {
        color: white;
        font-weight: bold;
        padding: 5px;
    }
    QComboBox {
        background-color: #404040;
        color: white;
        border: 1px solid #666;
        padding: 5px;
        min-width: 120px;
    }
    QComboBox::drop-down { border: none; }
    QPushButton {
        background-color: #404040;
        color: white;
        border: 1px solid #666;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: bold;
        min-width: 50px;
    }
    QPushButton:hover { background-color: #555; }
    QPushButton:pressed { background-color: #222; }
"""


# ---------------------------------------------------------------------------
# Toolbar widget
# ---------------------------------------------------------------------------

class FrontPocketToolbar(QWidget):

    def __init__(self):
        super().__init__()

        # Load configs
        self.fp_config = load_fp_config()
        self.state     = load_state()

        # Toolbar settings from frontpocket.ini [Toolbar]
        t = self.fp_config
        self.fp_command     = t.get("Toolbar", "fp_command",     fallback="fp")
        self.button_size    = int(t.get("Toolbar", "button_size",  fallback="30"))
        self.confirm_quit   = t.getboolean("Toolbar", "confirm_quit", fallback=True)
        self.animate        = t.getboolean("Toolbar", "animation",    fallback=True)
        self.record_command = t.get("Toolbar", "record", fallback="").strip()
        self.always_send_voice_speed = t.getboolean("Toolbar", "always_send_voice_speed", fallback=True)

        # Voice/speed data
        self.voices        = get_voices(self.fp_config)
        self.speed_choices = get_speed_choices(self.fp_config)
        self.speed_defaults = get_speed_defaults(self.fp_config)

        # Current applied settings — from state file, fall back to ini defaults
        default_voice = self.fp_config.get("settings", "default_voice", fallback="")
        default_speed = self.fp_config.get("settings", "default_speed", fallback="")
        self.current_voice = self.state.get("CurrentSettings", "voice", fallback="") or default_voice
        self.current_speed = self.state.get("CurrentSettings", "speed", fallback="") or default_speed

        # Pending (selected in dropdown but not yet applied)
        self.pending_voice = self.current_voice
        self.pending_speed = self.current_speed

        # Playback state: "playing" | "paused" | "stopped"
        self.play_state = "stopped"

        # Expansion state
        self.expanded        = False
        self.expanded_widget = None
        self.animation       = None

        self._build_ui()
        self._send_initial_settings()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setWindowTitle("FrontPocket")

        opacity = float(self.fp_config.get("Toolbar", "window_opacity", fallback="0.9"))
        self.setWindowOpacity(opacity)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(0)

        # Initializing label (shown briefly on startup if sending settings)
        self.init_label = QLabel("Initializing...")
        self.init_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.init_label.setStyleSheet("""
            QLabel {
                background-color: #2b2b2b;
                color: white;
                padding: 2px;
                font-size: 10px;
            }
        """)
        self.init_label.hide()
        self.main_layout.addWidget(self.init_label)

        # Toolbar button row
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(1)

        buttons = []
        if self.record_command:
            buttons.append(("record", "⏺", "Record"))
        buttons += [
            ("rewind",       "⏮", "Back"),
            ("play",         "▶", "Play / Resume"),
            ("pause",        "⏸", "Pause"),
            ("stop",         "⏹", "Stop"),
            ("fast_forward", "⏭", "Next"),
        ]

        self._pause_buttons = []   # refs to pause + stop buttons for tooltip updates

        for key, symbol, tooltip in buttons:
            btn = QPushButton(symbol)
            btn.setFixedSize(QSize(self.button_size, self.button_size))
            btn.setToolTip(tooltip)
            btn.setStyleSheet(BUTTON_STYLE)
            btn.clicked.connect(lambda checked, k=key: self._on_button(k))
            self._setup_drag(btn)
            toolbar_layout.addWidget(btn)
            if key in ("pause", "stop"):
                self._pause_buttons.append(btn)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar_layout)
        self.main_layout.addWidget(toolbar_widget)

        self.setLayout(self.main_layout)
        self.adjustSize()
        self.setFixedWidth(self.width())

        # Position — restore saved, validate, fall back to ini default
        saved_x = self.state.getint("CurrentSettings", "x", fallback=-1)
        saved_y = self.state.getint("CurrentSettings", "y", fallback=-1)
        default_x = int(self.fp_config.get("Toolbar", "initial_x", fallback="800"))
        default_y = int(self.fp_config.get("Toolbar", "initial_y", fallback="30"))

        if saved_x >= 0 and saved_y >= 0 and validate_position(saved_x, saved_y):
            self.move(saved_x, saved_y)
        else:
            self.move(default_x, default_y)

        # Drag state
        self.draggable    = False
        self.drag_started = False
        self.offset       = None
        self.press_pos    = None

        # Ctrl+Q to quit
        qs = QShortcut(QKeySequence("Ctrl+Q"), self)
        qs.activated.connect(self._confirm_quit)

    def _build_expanded_widget(self) -> QWidget:
        """Build the voice/speed/OK/Cancel/Quit panel."""
        w = QWidget()
        w.setStyleSheet(EXPANDED_STYLE)
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        # Voice row
        if self.voices:
            row = QHBoxLayout()
            lbl = QLabel("Voice:")
            lbl.setFixedWidth(60)
            self.voice_combo = QComboBox()
            self.voice_combo.addItems(self.voices)
            if self.current_voice in self.voices:
                self.voice_combo.setCurrentText(self.current_voice)
                self.pending_voice = self.current_voice
            self.voice_combo.currentTextChanged.connect(self._on_voice_changed)
            row.addWidget(lbl)
            row.addWidget(self.voice_combo)
            layout.addLayout(row)

        # Speed row
        if self.speed_choices:
            row = QHBoxLayout()
            lbl = QLabel("Speed:")
            lbl.setFixedWidth(60)
            self.speed_combo = QComboBox()
            self.speed_combo.addItems(self.speed_choices)
            if self.current_speed in self.speed_choices:
                self.speed_combo.setCurrentText(self.current_speed)
                self.pending_speed = self.current_speed
            self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
            row.addWidget(lbl)
            row.addWidget(self.speed_combo)
            layout.addLayout(row)

        # OK / Cancel / Quit buttons — stretch to fill panel width
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        for label, slot in [("OK", self._collapse), ("Cancel", self._cancel), ("Quit", self._confirm_quit)]:
            b = QPushButton(label)
            b.setFixedHeight(self.button_size)
            b.setMinimumWidth(60)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        w.setLayout(layout)
        return w

    # -----------------------------------------------------------------------
    # Startup — send saved voice/speed to server
    # -----------------------------------------------------------------------

    def _send_initial_settings(self):
        """Send saved voice and speed to the server on startup."""
        commands = []
        if self.current_voice:
            commands.append(f"{self.fp_command} --voice {self.current_voice}")
        if self.current_speed:
            commands.append(f"{self.fp_command} --speed {self.current_speed}")

        if not commands:
            return

        self.init_label.show()
        self.adjustSize()
        self.setFixedSize(self.size())

        for cmd in commands:
            try:
                subprocess.Popen(cmd, shell=True)
            except Exception as e:
                print(f"Error sending initial setting: {e}")

        # Hide the initializing label after a short moment
        QTimer.singleShot(500, self._hide_init_label)

    def _hide_init_label(self):
        self.init_label.hide()
        self.adjustSize()
        self.setFixedSize(self.size())

    # -----------------------------------------------------------------------
    # Button handler
    # -----------------------------------------------------------------------

    def _on_button(self, key: str):
        if key == "play":
            if self.play_state == "paused":
                # Resume from where we left off
                self._run("--resume")
                self.play_state = "playing"
                print("Toolbar: Resume")
            else:
                # Idle or stopped — start new clipboard text
                if self.always_send_voice_speed:
                    if self.current_voice:
                        self._run(f"--voice {self.current_voice}")
                    if self.current_speed:
                        self._run(f"--speed {self.current_speed}")
                self._run()
                self.play_state = "playing"
                print("Toolbar: Play (clipboard)")

        elif key == "pause":
            if self.play_state == "playing":
                self._run("--pause")
                self.play_state = "paused"
                print("Toolbar: Pause")
            elif self.play_state == "paused":
                # Pause toggles to resume
                self._run("--resume")
                self.play_state = "playing"
                print("Toolbar: Resume (via pause)")
            # stopped — pause does nothing
            self._update_pause_tooltip()

        elif key == "stop":
            if self.play_state in ("playing", "paused"):
                self._run("--pause")
                self.play_state = "stopped"
                print("Toolbar: Stop")
            # already stopped — nothing to do
            self._update_pause_tooltip()

        elif key == "rewind":
            self._run("--back")
            self.play_state = "playing"
            print("Toolbar: Back")

        elif key == "fast_forward":
            self._run("--next")
            self.play_state = "playing"
            print("Toolbar: Next")

        elif key == "record":
            try:
                subprocess.Popen(self.record_command, shell=True)
                print("Toolbar: Record")
            except Exception as e:
                print(f"Toolbar: Error running record command: {e}")

        elif key in ("pause", "stop"):
            if self.play_state == "paused":
                # Already paused — toggle to resume
                self._run("--resume")
                self.play_state = "playing"
                print("Toolbar: Resume (via pause/stop toggle)")
            else:
                self._run("--pause")
                self.play_state = "paused"
                print("Toolbar: Pause")
            # Update tooltip to reflect new state
            self._update_pause_tooltip()

        elif key == "rewind":
            self._run("--back")
            self.play_state = "playing"
            print("Toolbar: Back")

        elif key == "fast_forward":
            self._run("--next")
            self.play_state = "playing"
            print("Toolbar: Next")

        elif key == "record":
            try:
                subprocess.Popen(self.record_command, shell=True)
                print("Toolbar: Record")
            except Exception as e:
                print(f"Toolbar: Error running record command: {e}")

    def _run(self, *args):
        """Run fp with optional arguments."""
        cmd = " ".join([self.fp_command] + list(args))
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            print(f"Error running command '{cmd}': {e}")

    def _update_pause_tooltip(self):
        """Update pause/stop button tooltips to reflect current play state."""
        if self.play_state == "paused":
            pause_tip = "Resume"
            stop_tip  = "Stop"
        elif self.play_state == "stopped":
            pause_tip = "Pause"
            stop_tip  = "Stopped"
        else:
            pause_tip = "Pause"
            stop_tip  = "Stop"
        for btn in self._pause_buttons:
            if btn.text() == "⏸":
                btn.setToolTip(pause_tip)
            elif btn.text() == "⏹":
                btn.setToolTip(stop_tip)

    # -----------------------------------------------------------------------
    # Dropdown handlers
    # -----------------------------------------------------------------------

    def _on_voice_changed(self, choice: str):
        self.pending_voice = choice
        # Auto-set speed default for this voice if configured
        default_speed = self.speed_defaults.get(choice.lower())
        if default_speed and hasattr(self, "speed_combo"):
            idx = self.speed_combo.findText(default_speed)
            if idx >= 0:
                self.speed_combo.setCurrentIndex(idx)
                self.pending_speed = default_speed

    def _on_speed_changed(self, choice: str):
        self.pending_speed = choice

    # -----------------------------------------------------------------------
    # Expansion panel
    # -----------------------------------------------------------------------

    def _expand(self):
        if self.expanded:
            return
        self.expanded = True
        self.pending_voice = self.current_voice
        self.pending_speed = self.current_speed
        self.expanded_widget = self._build_expanded_widget()
        self.main_layout.addWidget(self.expanded_widget)

        # Release fixed size so the window can grow to fit the panel
        self.setMinimumWidth(0)
        self.setMaximumWidth(QWIDGETSIZE_MAX)
        self.setMaximumHeight(QWIDGETSIZE_MAX)

        if self.animate:
            self.expanded_widget.setMaximumHeight(0)
            anim = QPropertyAnimation(self.expanded_widget, b"maximumHeight")
            anim.setDuration(200)
            anim.setStartValue(0)
            anim.setEndValue(130)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            # Lock size only after animation finishes
            anim.finished.connect(self._lock_expanded_size)
            anim.start()
            self.animation = anim
        else:
            self.expanded_widget.setMaximumHeight(QWIDGETSIZE_MAX)
            self.adjustSize()
            self.setFixedSize(self.size())

    def _lock_expanded_size(self):
        """Called after expand animation completes to lock the window size."""
        self.expanded_widget.setMaximumHeight(QWIDGETSIZE_MAX)
        self.adjustSize()
        self.setFixedSize(self.size())

    def _collapse(self):
        """OK — apply changes and collapse."""
        if not self.expanded or not self.expanded_widget:
            return
        self._apply_pending()
        self._animate_collapse()

    def _cancel(self):
        """Cancel — discard changes and collapse."""
        self.pending_voice = self.current_voice
        self.pending_speed = self.current_speed
        if hasattr(self, "voice_combo") and self.current_voice:
            self.voice_combo.setCurrentText(self.current_voice)
        if hasattr(self, "speed_combo") and self.current_speed:
            self.speed_combo.setCurrentText(self.current_speed)
        self._animate_collapse()

    def _animate_collapse(self):
        if not self.expanded or not self.expanded_widget:
            return
        # Release fixed size so animation can shrink the window
        self.setMaximumHeight(QWIDGETSIZE_MAX)
        if self.animate:
            anim = QPropertyAnimation(self.expanded_widget, b"maximumHeight")
            anim.setDuration(200)
            anim.setStartValue(self.expanded_widget.height())
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.finished.connect(self._remove_expanded)
            anim.start()
            self.animation = anim
        else:
            self._remove_expanded()

    def _remove_expanded(self):
        if self.expanded_widget:
            self.main_layout.removeWidget(self.expanded_widget)
            self.expanded_widget.deleteLater()
            self.expanded_widget = None
        self.expanded = False
        # Reset all size constraints so adjustSize calculates true minimum
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        self.adjustSize()
        self.setFixedSize(self.size())

    def _apply_pending(self):
        """Send any changed voice/speed to the server and save state."""
        if self.pending_voice and self.pending_voice != self.current_voice:
            self._run(f"--voice {self.pending_voice}")
            self.current_voice = self.pending_voice
            print(f"Toolbar: Voice changed to {self.current_voice}")

        if self.pending_speed and self.pending_speed != self.current_speed:
            self._run(f"--speed {self.pending_speed}")
            self.current_speed = self.pending_speed
            print(f"Toolbar: Speed changed to {self.current_speed}")

        self._save_state()

    # -----------------------------------------------------------------------
    # State persistence
    # -----------------------------------------------------------------------

    def _save_state(self):
        self.state.set("CurrentSettings", "voice", self.current_voice or "")
        self.state.set("CurrentSettings", "speed", self.current_speed or "")
        self.state.set("CurrentSettings", "x",     str(self.x()))
        self.state.set("CurrentSettings", "y",     str(self.y()))
        save_state(self.state)

    # -----------------------------------------------------------------------
    # Quit
    # -----------------------------------------------------------------------

    def _confirm_quit(self):
        if self.confirm_quit:
            reply = QMessageBox.question(
                self, "Quit FrontPocket Toolbar", "Quit the toolbar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._save_state()
        QApplication.quit()

    # -----------------------------------------------------------------------
    # Drag support
    # -----------------------------------------------------------------------

    def _setup_drag(self, widget):
        original_press   = widget.mousePressEvent
        original_move    = widget.mouseMoveEvent
        original_release = widget.mouseReleaseEvent

        def on_press(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.draggable    = True
                self.drag_started = False
                self.offset       = event.globalPosition().toPoint() - self.pos()
                self.press_pos    = event.globalPosition().toPoint()
            original_press(event)

        def on_move(event):
            if self.draggable and self.offset is not None:
                cur = event.globalPosition().toPoint()
                if not self.drag_started:
                    if (cur - self.press_pos).manhattanLength() > 5:
                        self.drag_started = True
                if self.drag_started:
                    self.move(cur - self.offset)
                    return
            original_move(event)

        def on_release(event):
            if event.button() == Qt.MouseButton.LeftButton:
                was_dragging      = self.drag_started
                self.draggable    = False
                self.drag_started = False
                self.offset       = None
                if not was_dragging:
                    original_release(event)
            else:
                original_release(event)

        widget.mousePressEvent   = on_press
        widget.mouseMoveEvent    = on_move
        widget.mouseReleaseEvent = on_release

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.draggable    = True
            self.drag_started = False
            self.offset       = event.position().toPoint()
            self.press_pos    = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.draggable and self.offset is not None:
            cur = event.globalPosition().toPoint()
            if not self.drag_started:
                if (cur - self.press_pos).manhattanLength() > 5:
                    self.drag_started = True
            if self.drag_started:
                self.move(cur - self.offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.draggable    = False
            self.drag_started = False
            self.offset       = None

    def contextMenuEvent(self, event):
        """Right-click toggles the expanded panel."""
        if self.expanded:
            self._cancel()
        else:
            self._expand()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    toolbar = FrontPocketToolbar()
    toolbar.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
