"""Hotkey regression: stop must be instant, and holding must not re-toggle.

1. stop_recording must return immediately even when live chunk
   transcription is still finalizing (the worker join belongs to the
   processing thread, not the keyboard hook).
2. The hotkey must be edge-triggered on release, because Windows key
   auto-repeat emits key-downs while held.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import session as session_mod  # noqa: E402
from amanu_win.config import Config  # noqa: E402


class _FakeInfo:
    started_at = time.time() - 3
    stopped_at = time.time()
    mic_device = "fake"
    system_device = "fake"
    mic_rate = 48000
    system_rate = 48000
    error = None
    warnings = []

class _FakeRecorder:
    def __init__(self, out_path, mic_tap=None, **kw):
        self.info = _FakeInfo()
        self.mic_tap = mic_tap

    def start(self):
        pass

    def stop(self):
        return _FakeInfo()


class _SlowLive:
    """finish() blocks like a real one waiting on model load."""

    def __init__(self):
        self.finish_called = 0

    def feed(self, frames):
        pass

    def finish(self):
        self.finish_called += 1
        time.sleep(5)  # simulates whisper load + tail chunk
        return []


@pytest.fixture
def cfg(tmp_path):
    return Config({
        "recordings_dir": str(tmp_path),
        "transcription": {"enabled": False},
        "summary": {"enabled": False},
        "paste": {"enabled": False},
        "punctuation": {"enabled": False},
    })


def test_stop_returns_fast_even_while_live_finalize_blocks(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "StereoRecorder", _FakeRecorder)
    live = _SlowLive()
    monkeypatch.setattr(session_mod, "LiveMicTranscriber", lambda **kw: live)
    cfg.transcription["streaming"] = {"enabled": True}

    sm = session_mod.SessionManager(cfg, log=lambda m: None)
    sm.start_recording()
    t0 = time.monotonic()
    d = sm.stop_recording()
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"stop blocked {elapsed:.1f}s on live finalize — hotkey freezes"
    # finalize happens in the processing thread, not on the caller
    sm.processing.wait(timeout=0.5)  # thread started
    assert d.exists()


def test_hotkey_edge_triggered(monkeypatch):
    import amanu_win.app as app_mod
    hooks = {}
    monkeypatch.setattr(app_mod.keyboard, "on_press_key",
                        lambda key, cb: hooks.setdefault("press", cb))
    monkeypatch.setattr(app_mod.keyboard, "on_release_key",
                        lambda key, cb: hooks.setdefault("release", cb))
    monkeypatch.setattr(app_mod.keyboard, "is_pressed", lambda k: True)
    monkeypatch.setattr(app_mod.pystray.Icon, "run", lambda self: None)
    cfg = Config({"transcription": {"enabled": False}, "summary": {"enabled": False},
                  "paste": {"enabled": False}, "punctuation": {"enabled": False}, "panel": {"enabled": False}})
    app = app_mod.TrayApp(cfg)
    toggles = []
    monkeypatch.setattr(app, "toggle", lambda: toggles.append(1))
    import time as _time
    app.run()
    deadline = _time.monotonic() + 5

    def wait_n(n):
        nonlocal deadline
        while len(toggles) < n and _time.monotonic() < deadline:
            _time.sleep(0.02)

    hooks["press"](None)   # physical key-down
    hooks["press"](None)   # Windows auto-repeat while held
    hooks["press"](None)
    wait_n(1)
    _time.sleep(0.3)  # give repeats a chance to slip through
    assert toggles == [1], "auto-repeat key-downs must not re-toggle"

    hooks["release"](None)  # key released
    hooks["press"](None)    # deliberate second press
    wait_n(2)
    assert toggles == [1, 1]
