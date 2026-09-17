"""The tray must be told when processing finishes — otherwise the icon
stays amber forever even though everything completed."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config  # noqa: E402
from amanu_win.session import SessionManager  # noqa: E402


def _make_session(tmp_path):
    d = tmp_path / "2026.09.17-0000"
    d.mkdir()
    sf.write(str(d / "audio.wav"),
             np.zeros((48000 * 2, 2), dtype=np.float32), 48000)
    (d / "meta.json").write_text(json.dumps({
        "started_at": "2026-09-17T00:00:00", "stopped_at": "2026-09-17T00:00:02",
        "duration_s": 2.0, "devices": {}, "trigger": "hotkey",
        "processing": {"transcript": "pending", "summary": "pending"},
    }), encoding="utf-8")
    return d


def test_processing_done_fires_state_callback(tmp_path):
    calls = []
    cfg = Config({
        "recordings_dir": str(tmp_path),
        "transcription": {"enabled": False},
        "summary": {"enabled": False},
    })
    sm = SessionManager(cfg, log=lambda m: None,
                        on_state_changed=lambda: calls.append("changed"))
    d = _make_session(tmp_path)
    sm.process(d)
    assert calls, "tray was never notified that processing finished"
    assert not sm.processing.is_set()
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["processing"]["transcript"] == "disabled"
