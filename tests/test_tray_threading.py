"""Tray icon mutations are only legal on the message-loop thread.

A state change arriving from the keyboard-hook or processing thread must be
marshalled to the loop thread via a posted message, never applied directly —
a foreign-thread mutation can raise WinError 1402 and kill the hook thread.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import app  # noqa: E402
from amanu_win.app import TrayApp  # noqa: E402
from amanu_win.config import Config  # noqa: E402


@pytest.fixture
def tray(tmp_path):
    a = TrayApp(Config({"recordings_dir": str(tmp_path / "rec")}))
    return a


def test_state_change_marshalled_when_loop_running(tray, monkeypatch):
    posted = []
    monkeypatch.setattr(
        app.pwin32, "PostMessage",
        lambda hwnd, msg, w, l: posted.append((hwnd, msg)))
    tray.icon._hwnd = 12345  # pretend the message loop is up
    direct = []
    monkeypatch.setattr(tray, "_refresh", lambda: direct.append(1))

    tray._schedule_refresh()

    assert posted, "must post a message to the loop thread"
    assert posted[0][1] == tray.icon._msg_refresh
    assert not direct, "must not mutate the icon from a foreign thread"


def test_state_change_direct_when_no_loop(tray, monkeypatch):
    tray.icon._hwnd = None  # loop not started yet (tests, early startup)
    direct = []
    monkeypatch.setattr(tray, "_refresh", lambda: direct.append(1))

    tray._schedule_refresh()

    assert len(direct) == 1, "without a running loop, fall back to direct"
