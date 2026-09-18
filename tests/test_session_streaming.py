"""Streaming transcription seam in SessionManager: when fragments were
produced live during the recording, the paste comes from the LLM stitch
(and the separate punctuation pass must not run again); without fragments
the classic whole-file path is used.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import paste, punctuate, stitch  # noqa: E402
from amanu_win.config import Config  # noqa: E402
from amanu_win.session import SessionManager  # noqa: E402

FRAGMENTS = [
    {"start_s": 0.0, "end_s": 10.0,
     "segments": [{"start": 0.0, "end": 2.0, "text": "привет это тест"}]},
    {"start_s": 8.0, "end_s": 18.0,
     "segments": [{"start": 0.0, "end": 2.0, "text": "привет это тест"},
                  {"start": 2.0, "end": 4.0, "text": "дальше по тексту"}]},
]


def _session(tmp_path):
    d = tmp_path / "2026.09.17-0000"
    d.mkdir()
    noise = (np.random.default_rng(1).standard_normal((48000 * 2, 2))
             * 0.05).astype(np.float32)
    sf.write(str(d / "audio.wav"), noise, 48000)
    (d / "meta.json").write_text(json.dumps({
        "started_at": "x", "stopped_at": "y", "duration_s": 2.0,
        "devices": {}, "trigger": "hotkey",
        "processing": {"transcript": "pending", "summary": "pending"},
    }), encoding="utf-8")
    return d


def _config(tmp_path, streaming=True):
    return Config({
        "recordings_dir": str(tmp_path),
        "transcription": {"enabled": True, "device": "cpu",
                          "streaming": {"enabled": streaming}},
        "summary": {"enabled": False},
        "punctuation": {"enabled": True},
        "paste": {"enabled": True},
    })


class _FarModel:
    """Fake whisper for the far-end channel."""
    def __init__(self):
        self.calls = 0

    def transcribe(self, mono, language=None, vad_filter=True):
        self.calls += 1
        class I:
            language = "ru"
            language_probability = 0.99
        class S:
            start, end, text = 0.0, 2.0, "Ответ собеседника."
        return iter([S()]), I()


def test_fragments_paste_stitched_without_second_llm_pass(tmp_path, monkeypatch):
    pasted = []
    punct_calls = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))
    monkeypatch.setattr(stitch, "stitch_fragments",
                        lambda *a, **kw: "СШИТЫЙ текст.")
    monkeypatch.setattr(punctuate, "restore_punctuation",
                        lambda *a, **kw: punct_calls.append(1) or "!!")
    sm = SessionManager(_config(tmp_path), log=lambda m: None)
    sm._model = _FarModel()
    d = _session(tmp_path)
    sm.process(d, fragments=FRAGMENTS)

    assert pasted == ["СШИТЫЙ текст."]
    assert punct_calls == [], "streaming output is already punctuated by the stitch"
    assert sm._model.calls == 1, "only the far end is transcribed after stop"
    md = (d / "transcript.md").read_text(encoding="utf-8")
    assert "Ответ собеседника" in md
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["streaming"]["fragments"] == 2


def test_empty_fragments_fall_back_to_classic_path(tmp_path, monkeypatch):
    pasted = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))
    sm = SessionManager(_config(tmp_path), log=lambda m: None)
    sm._model = _FarModel()
    d = _session(tmp_path)
    sm.process(d, fragments=[])
    # classic path: both channels transcribed (fake returns one segment per call)
    assert sm._model.calls == 2
    assert pasted  # own speech from the classic path was pasted


def test_streaming_disabled_ignores_fragments(tmp_path, monkeypatch):
    pasted = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))
    monkeypatch.setattr(stitch, "stitch_fragments",
                        lambda *a, **kw: "СШИТЫЙ текст.")
    sm = SessionManager(_config(tmp_path, streaming=False), log=lambda m: None)
    sm._model = _FarModel()
    d = _session(tmp_path)
    sm.process(d, fragments=FRAGMENTS)
    assert sm._model.calls == 2, "classic path despite fragments"
    assert "СШИТЫЙ" not in (pasted[0] if pasted else "")
