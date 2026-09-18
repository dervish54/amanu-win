"""Async toggle: the keyboard hook must return instantly.

Toggle work (device open/close, joins, merge) can take seconds on flaky
audio drivers; the hook callback must just enqueue it. The tray shows an
intermediate "starting/stopping" title while the worker runs.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import app as app_mod  # noqa: E402
from amanu_win.config import Config  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(app_mod.pystray.Icon, "run", lambda self: None)
    monkeypatch.setattr(app_mod.keyboard, "on_press_key", lambda k, cb: None)
    monkeypatch.setattr(app_mod.keyboard, "on_release_key", lambda k, cb: None)
    cfg = Config({"transcription": {"enabled": False}, "summary": {"enabled": False},
                  "paste": {"enabled": False}, "punctuation": {"enabled": False}})
    a = app_mod.TrayApp(cfg)
    yield a
    a._work_q.put(None)


def test_hook_returns_fast_with_slow_toggle(app, monkeypatch):
    started = []

    def slow():
        time.sleep(3)
        started.append(1)

    monkeypatch.setattr(app, "toggle", slow)
    t0 = time.monotonic()
    app._on_hk_press(None)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"hook blocked {elapsed:.1f}s on toggle work"
    deadline = time.monotonic() + 6
    while not started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started == [1], "worker never ran the toggle"


def test_pending_state_shown_immediately(app, monkeypatch):
    done = []
    monkeypatch.setattr(app, "toggle", lambda: (time.sleep(0.4), done.append(1)))
    app._on_hk_press(None)
    assert app._pending is not None, "no intermediate state while worker runs"
    deadline = time.monotonic() + 3
    while not done and time.monotonic() < deadline:
        time.sleep(0.05)
    deadline = time.monotonic() + 3
    while app._pending is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert app._pending is None
