"""Shotgun start: all active mics record the first seconds, the one with a
live signal wins the archive.

Requirements from incidents 1905/0027/1247: no start delay, no lost first
words, automatic choice even when the Windows default endpoint is a dead
virtual driver. Decision rule: a device with peak >= LIVE_PEAK_FLOOR is
alive (real ADC noise floor); the default endpoint wins ties.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import recorder as rec_mod  # noqa: E402
from amanu_win.recorder import StereoRecorder  # noqa: E402


class FakeStream:
    """sounddevice InputStream stand-in: tests push frames manually."""

    def __init__(self, device, samplerate, channels, dtype, callback, blocksize=0):
        self.device = device
        self.samplerate = samplerate
        self.callback = callback
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        pass

    def push(self, frames):
        data = np.asarray(frames, dtype=np.int16).reshape(-1, 1)
        self.callback(data, len(data), None, None)


@pytest.fixture
def fake_audio(monkeypatch):
    """Fake device landscape: index 10 = dead default, 20 = live alternate."""
    streams = []

    def fake_input_stream(**kw):
        s = FakeStream(**kw)
        streams.append(s)
        return s

    monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)
    return streams


def _feed(stream, seconds, rate, peak):
    n = int(seconds * rate)
    if peak == 0:
        frames = np.zeros(n, dtype=np.int16)
    else:
        rng = np.random.default_rng(0)
        frames = (rng.standard_normal(n) * peak).astype(np.int16)
    stream.push(frames)


def test_dead_default_switches_to_live_alternate(tmp_path, fake_audio, monkeypatch):
    monkeypatch.setattr(rec_mod, "_active_capture_devices",
                        lambda prefer=None: [
                            (10, 48000, "Микрофон (WO Mic Device)"),
                            (20, 16000, "Головной телефон (SonoFlow)"),
                        ])
    monkeypatch.setattr(rec_mod, "select_mic_device", lambda prefer=None: (10, 48000, "Микрофон (WO Mic Device)"))
    monkeypatch.setattr(rec_mod, "PROBE_S", 0.2)

    out = tmp_path / "audio.wav"
    rec = StereoRecorder(out)
    rec.start()
    assert len(fake_audio) == 2, "shotgun must open all candidates"
    _feed(fake_audio[0], 0.5, 48000, peak=0)     # dead default
    _feed(fake_audio[1], 0.5, 16000, peak=500)   # live alternate
    time.sleep(0.5)  # decision timer fires
    rec.stop()

    data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    assert float(np.abs(data[:, 0]).max()) > 1e-4, "archive must hold the live device, not the dead default"
    assert rec.info.mic_device == "Головной телефон (SonoFlow)"
    assert rec.info.mic_rate == 16000
    assert getattr(rec.info, "mic_switched_from", None) == "Микрофон (WO Mic Device)"


def test_live_default_kept_alternates_closed(tmp_path, fake_audio, monkeypatch):
    monkeypatch.setattr(rec_mod, "_active_capture_devices",
                        lambda prefer=None: [(10, 48000, "WO Mic"), (20, 16000, "SonoFlow")])
    monkeypatch.setattr(rec_mod, "select_mic_device", lambda prefer=None: (10, 48000, "WO Mic"))
    monkeypatch.setattr(rec_mod, "PROBE_S", 0.2)

    rec = StereoRecorder(tmp_path / "audio.wav")
    rec.start()
    _feed(fake_audio[0], 0.5, 48000, peak=400)
    _feed(fake_audio[1], 0.5, 16000, peak=600)
    time.sleep(0.5)
    rec.stop()
    assert rec.info.mic_device == "WO Mic"
    assert not getattr(rec.info, "mic_switched_from", None)


def test_all_dead_keeps_default_and_warns(tmp_path, fake_audio, monkeypatch):
    monkeypatch.setattr(rec_mod, "_active_capture_devices",
                        lambda prefer=None: [(10, 48000, "WO Mic"), (20, 16000, "SonoFlow")])
    monkeypatch.setattr(rec_mod, "select_mic_device", lambda prefer=None: (10, 48000, "WO Mic"))
    monkeypatch.setattr(rec_mod, "PROBE_S", 0.2)
    rec = StereoRecorder(tmp_path / "audio.wav")
    rec.start()
    _feed(fake_audio[0], 0.5, 48000, peak=0)
    _feed(fake_audio[1], 0.5, 16000, peak=0)
    time.sleep(0.5)
    info = rec.stop()
    assert info.mic_device == "WO Mic"
    assert any("silence" in w.lower() or "dead" in w.lower() for w in (info.warnings or []))


def test_pinned_mic_skips_shotgun(tmp_path, fake_audio, monkeypatch):
    monkeypatch.setattr(rec_mod, "_active_capture_devices",
                        lambda prefer=None: [(10, 48000, "WO Mic"), (20, 16000, "SonoFlow")])
    monkeypatch.setattr(rec_mod, "select_mic_device",
                        lambda prefer=None: (20, 16000, "SonoFlow") if prefer else (10, 48000, "WO Mic"))
    rec = StereoRecorder(tmp_path / "audio.wav", mic_device="SonoFlow")
    rec.start()
    assert len(fake_audio) == 1, "pinned mic must not open alternates"
    assert fake_audio[0].device == 20
    rec.stop()
