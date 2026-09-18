"""Capture watchdog: a stalled or dead stream must surface in meta.json.

Real incident: a Virtual Desktop microphone opened at 48000, delivered one
second of audio, then stalled for the remaining 41s of a 42s recording —
silently. The archive looked valid; it wasn't.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.recorder import capture_warnings  # noqa: E402


def test_warns_when_stream_stalls_mid_recording():
    # 1s of mic audio over a 42s recording
    warns = capture_warnings(mic_frames=48000, mic_rate=48000,
                             sys_frames=48000 * 42, sys_rate=48000,
                             wall_s=42.0)
    assert any("mic" in w for w in warns)
    assert any("1.0" in w or "1 s" in w for w in warns)


def test_warns_when_loopback_delivers_nothing():
    warns = capture_warnings(mic_frames=48000 * 42, mic_rate=48000,
                             sys_frames=0, sys_rate=48000, wall_s=42.0)
    assert any("loopback" in w or "system" in w for w in warns)


def test_no_warning_on_healthy_streams():
    warns = capture_warnings(mic_frames=int(48000 * 42 * 0.97), mic_rate=48000,
                             sys_frames=int(48000 * 42 * 0.95), sys_rate=48000,
                             wall_s=42.0)
    assert warns == []


def test_no_warning_when_mic_absent_entirely():
    # "unavailable" devices legitimately produce zero frames
    warns = capture_warnings(mic_frames=0, mic_rate=48000,
                             sys_frames=48000 * 42, sys_rate=48000,
                             wall_s=42.0, mic_opened=False)
    assert warns == []
