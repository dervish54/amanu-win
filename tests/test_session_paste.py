"""Session auto-paste: once the transcript exists, the user's own speech
is pasted automatically — but only when enabled and non-empty."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import paste  # noqa: E402
from amanu_win.config import Config  # noqa: E402
from amanu_win.session import SessionManager  # noqa: E402

MIC_SEGMENTS = [
    {"start": 0.0, "end": 2.0, "speaker": "Микрофон", "text": "Здравствуйте."},
    {"start": 2.0, "end": 4.0, "speaker": "Собеседник", "text": "Здравствуйте!"},
    {"start": 4.0, "end": 6.0, "speaker": "Микрофон", "text": "Как дела?"},
]


def _session(tmp_path):
    d = tmp_path / "2026.09.17-0000"
    d.mkdir()
    # above the transcription noise gate so the fake model is actually called
    noise = (np.random.default_rng(1).standard_normal((48000 * 2, 2))
             * 0.05).astype(np.float32)
    sf.write(str(d / "audio.wav"), noise, 48000)
    (d / "meta.json").write_text(json.dumps({
        "started_at": "x", "stopped_at": "y", "duration_s": 2.0,
        "devices": {}, "trigger": "hotkey",
        "processing": {"transcript": "pending", "summary": "pending"},
    }), encoding="utf-8")
    return d


def _mk(texts):
    segs = []
    t = 0.0
    for text in texts:
        class S:
            pass
        o = S()
        o.start, o.end, o.text = t, t + 2.0, text
        segs.append(o)
        t += 2.0
    return segs


class _FakeModel:
    """First transcribe call = mic channel, second = far end — mirrors
    what transcribe_channels does."""

    def __init__(self, mic_texts=("Здравствуйте.", "Как дела?"),
                 far_texts=("Здравствуйте!",)):
        self._channels = [_mk(mic_texts), _mk(far_texts)]

    def transcribe(self, mono, language=None, vad_filter=True):
        class I:
            language = "ru"
            language_probability = 0.99
        segs = self._channels.pop(0) if self._channels else []
        return iter(segs), I()


def _config(tmp_path, enabled=True, punctuation=True):
    return Config({
        "recordings_dir": str(tmp_path),
        "transcription": {"enabled": True, "device": "cpu"},
        "summary": {"enabled": False},
        "paste": {"enabled": enabled},
        "punctuation": {"enabled": punctuation},
    })


def test_transcript_done_pastes_own_speech(tmp_path, monkeypatch):
    pasted = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))
    sm = SessionManager(_config(tmp_path), log=lambda m: None)
    sm._model = _FakeModel()
    sm.process(_session(tmp_path))

    assert len(pasted) == 1
    assert "Здравствуйте" in pasted[0]
    assert "Как дела" in pasted[0]
    assert pasted[0].count("Здравствуйте") == 1  # far end excluded


def test_paste_disabled_does_nothing(tmp_path, monkeypatch):
    pasted = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))
    sm = SessionManager(_config(tmp_path, enabled=False), log=lambda m: None)
    sm._model = _FakeModel()
    sm.process(_session(tmp_path))
    assert pasted == []


def test_no_own_speech_nothing_pasted(tmp_path, monkeypatch):
    pasted = []
    monkeypatch.setattr(paste, "paste_text", lambda t: pasted.append(t))

    sm = SessionManager(_config(tmp_path), log=lambda m: None)
    sm._model = _FakeModel(mic_texts=())
    sm.process(_session(tmp_path))
    assert pasted == []


def test_punctuation_applied_before_paste(tmp_path, monkeypatch):
    from amanu_win import punctuate
    monkeypatch.setattr(paste, "paste_text", lambda t: None)
    monkeypatch.setattr(punctuate, "restore_punctuation",
                        lambda text, url, model, speaker_markers=False: text + " [P]")
    sm = SessionManager(_config(tmp_path), log=lambda m: None)
    sm._model = _FakeModel()
    d = _session(tmp_path)
    sm.process(d)
    assert " [P]" in (d / "transcript.md").read_text(encoding="utf-8")


def test_punctuation_disabled_keeps_raw_text(tmp_path, monkeypatch):
    from amanu_win import punctuate
    calls = []
    monkeypatch.setattr(paste, "paste_text", lambda t: calls.append(t))
    monkeypatch.setattr(
        punctuate, "restore_punctuation",
        lambda text, url, model, speaker_markers=False: calls.append(("restored", text)) or text)
    sm = SessionManager(_config(tmp_path, punctuation=False), log=lambda m: None)
    sm._model = _FakeModel()
    d = _session(tmp_path)
    sm.process(d)
    assert not any(isinstance(c, tuple) for c in calls), "restorer ran while disabled"
