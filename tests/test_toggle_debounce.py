"""Regression tests for the auto-repeat hotkey bug.

Holding Ctrl+Alt+R makes Windows auto-repeat the keydown many times a
second; every repeat must be ignored — one deliberate press = one toggle.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.app import TrayApp  # noqa: E402
from amanu_win.config import Config  # noqa: E402


@pytest.fixture
def tray(tmp_path):
    cfg = Config({
        "recordings_dir": str(tmp_path / "rec"),
        "transcription": {"enabled": False},
        "summary": {"enabled": False},
    })
    app = TrayApp(cfg)
    yield app
    if app.sessions.is_recording:
        app.sessions.stop_recording()


def test_auto_repeat_does_not_retoggle(tray):
    # holding the hotkey: every auto-repeat trigger must be a no-op
    tray.toggle()
    assert tray.sessions.is_recording
    for _ in range(8):
        tray.toggle()
        time.sleep(0.05)
        assert tray.sessions.is_recording, "auto-repeat must not stop the recording"


def test_deliberate_second_toggle_after_window_stops(tray):
    tray.toggle()
    assert tray.sessions.is_recording
    time.sleep(0.8)
    tray.toggle()
    assert not tray.sessions.is_recording, "a deliberate press after the debounce window must stop"
