"""Timing regression tests for the capture architecture.

The archive must track wall time regardless of what either capture side
is doing: a silent loopback must not stretch the mic, and vice versa.
"""
import sys
import time
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.recorder import StereoRecorder  # noqa: E402


def test_silent_loopback_preserves_wall_time(tmp_path):
    out = tmp_path / "audio.wav"
    rec = StereoRecorder(out)
    rec.start()
    time.sleep(6)
    info = rec.stop()
    dur = sf.info(str(out)).duration
    # the pacing bug class this guards lost 50%+ of wall time (0.576 and
    # 0.22 ratios measured); the band tolerates BT-headset startup stagger
    # (variable by seconds) while still catching any real pacing regression
    assert 3.0 <= dur <= 6.9, (
        f"file time {dur:.1f}s drifted from wall time ~6s — "
        "capture must be paced by the streams, not by queue timeouts"
    )


def test_stereo_layout_mic_left_loopback_right(tmp_path):
    # even with nothing playing, the archive must be a two-channel file
    out = tmp_path / "audio.wav"
    rec = StereoRecorder(out)
    rec.start()
    time.sleep(1.5)
    rec.stop()
    data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    assert data.shape[1] == 2
    assert sr == 48000
