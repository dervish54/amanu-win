"""Floating panel: controller logic is headless-testable; the tkinter
view is a thin shell."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.panel import PanelController  # noqa: E402


def _ctl():
    calls = []
    saved = {}
    ctl = PanelController(
        on_record=lambda: calls.append("record"),
        on_open_folder=lambda: calls.append("folder"),
        on_quit=lambda: calls.append("quit"),
        get_pos=lambda: None,
        save_pos=lambda x, y: saved.update(x=x, y=y),
    )
    return ctl, calls, saved


def test_stripe_colors():
    ctl, _, _ = _ctl()
    assert ctl.stripe_for("idle") == "#8a8a8a"
    assert ctl.stripe_for("recording") == "#2ea043"
    assert ctl.stripe_for("processing") == "#dca014"
    assert ctl.stripe_for("pending") == "#dca014"


def test_dispatch_routes_commands():
    ctl, calls, _ = _ctl()
    ctl.dispatch("record")
    ctl.dispatch("folder")
    ctl.dispatch("quit")
    assert calls == ["record", "folder", "quit"]


def test_dispatch_rejects_unknown():
    ctl, calls, _ = _ctl()
    ctl.dispatch("nonsense")
    assert calls == []


def test_position_roundtrip():
    ctl, _, saved = _ctl()
    assert ctl.initial_pos() is None
    ctl.remember_pos(120, 300)
    assert saved == {"x": 120, "y": 300}
