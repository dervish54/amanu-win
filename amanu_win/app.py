"""Tray application + global hotkey.

Red circle = idle, green = recording. Left-click toggles recording too,
so the app is fully usable without the keyboard.
"""
from __future__ import annotations

import io
import threading
import time
import tkinter as tk

import keyboard
import pystray
from PIL import Image, ImageDraw
from pystray._util import win32 as pwin32

from .session import SessionManager

IDLE = (196, 43, 43)      # red
RECORDING = (46, 160, 67) # green
BUSY = (220, 160, 20)     # amber while post-processing


def _icon_image(color) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color + (255,))
    return img


class _TrayIcon(pystray.Icon):
    """pystray allows icon mutation only on the message-loop thread.

    State changes arrive from the keyboard-hook and processing threads; they
    are marshalled through a registered window message, whose handler runs
    on the loop thread (wndproc dispatches any message via the instance's
    ``_message_handlers`` mapping).
    """

    def __init__(self, *args, on_refresh=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_refresh = on_refresh
        self._msg_refresh = pwin32.RegisterWindowMessage("Amanu.TrayRefresh")
        self._message_handlers[self._msg_refresh] = self._handle_refresh

    def _handle_refresh(self, wparam, lparam):
        if self._on_refresh is not None:
            self._on_refresh()
        return 0


class TrayApp:
    def __init__(self, config):
        self.config = config
        self.sessions = SessionManager(config, log=self._log,
                                       on_state_changed=self._schedule_refresh)
        self.icon = _TrayIcon(
            "amanu",
            icon=_icon_image(IDLE),
            title="Amanu (idle) — Ctrl+Alt+R to record",
            menu=self._build_menu(),
            on_refresh=self._refresh,
        )
        self._toggle_lock = threading.Lock()
        self._last_toggle = 0.0

    def _log(self, msg: str) -> None:
        # Windows consoles are often cp1251; unencodable chars must never
        # kill the processing thread
        try:
            print(f"[amanu] {msg}", flush=True)
        except UnicodeEncodeError:
            print(f"[amanu] {msg.encode('ascii', 'replace').decode('ascii')}", flush=True)

    # -- state ---------------------------------------------------------------
    def _refresh(self) -> None:
        if self.sessions.is_recording:
            color, title = RECORDING, "Amanu — RECORDING (Ctrl+Alt+R to stop)"
        elif self.sessions.processing.is_set():
            color, title = BUSY, f"Amanu — {self.sessions.stage or 'processing'}…"
        else:
            color, title = IDLE, "Amanu (idle) — Ctrl+Alt+R to record"
        self.icon.icon = _icon_image(color)
        self.icon.title = title
        self.icon.menu = self._build_menu()

    def _build_menu(self):
        if self.sessions.is_recording:
            first = pystray.MenuItem("■ Stop recording", lambda: self.toggle())
        else:
            first = pystray.MenuItem("● Start recording", lambda: self.toggle())
        return pystray.Menu(
            first,
            pystray.MenuItem("Open recordings folder", self._open_recordings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.icon.stop()),
        )

    # -- actions ----------------------------------------------------------------
    def _schedule_refresh(self) -> None:
        """Thread-safe icon update: post to the message loop, or apply
        directly when the loop is not running yet."""
        hwnd = self.icon._hwnd
        if hwnd:
            pwin32.PostMessage(hwnd, self.icon._msg_refresh, 0, 0)
        else:
            self._refresh()

    def toggle(self) -> None:
        # holding the hotkey makes Windows auto-repeat the keydown; without a
        # debounce window that storm toggles start/stop many times a second
        with self._toggle_lock:
            now = time.monotonic()
            if now - self._last_toggle < 0.6:
                return
            self._last_toggle = now
            try:
                if self.sessions.is_recording:
                    d = self.sessions.stop_recording()
                    self._log(f"stopped; processing {d.name}")
                else:
                    self.sessions.start_recording()
            except Exception as e:
                self._log(f"error: {e}")
            finally:
                # never let an exception escape into the keyboard lib's
                # event thread — that kills the global hook
                try:
                    self._schedule_refresh()
                except Exception as e:
                    self._log(f"refresh error: {e}")

    def _open_recordings(self) -> None:
        import os
        os.startfile(self.config.recordings_dir)

    # -- run ---------------------------------------------------------------------
    def run(self) -> None:
        keyboard.add_hotkey(self.config.hotkey, self.toggle)
        self.icon.run()
