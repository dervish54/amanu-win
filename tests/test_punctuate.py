"""Punctuation restoration via the local Ollama model.

Whisper loses punctuation and capitalization on Russian; the restorer
re-punctuates under a strict no-rewording prompt and fails safe to the
original text.
"""
import io
import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import punctuate  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture_request(monkeypatch, response_text):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"response": response_text})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_restore_sends_text_with_low_temperature(monkeypatch):
    cap = _capture_request(monkeypatch, "Привет, мир!")
    out = punctuate.restore_punctuation(
        "привет мир", "http://localhost:11434", "qwen2.5:7b")
    assert out == "Привет, мир!"
    assert "привет мир" in cap["body"]["prompt"]
    assert cap["body"]["options"]["temperature"] < 0.3
    assert cap["body"]["model"] == "qwen2.5:7b"


def test_restore_returns_original_when_server_fails(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no server")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = punctuate.restore_punctuation("какой то текст", "http://x", "m")
    assert out == "какой то текст"


def test_restore_rejects_garbage(monkeypatch):
    cap = _capture_request(monkeypatch, "")  # empty model output
    out = punctuate.restore_punctuation("нормальный текст здесь", "http://x", "m")
    assert out == "нормальный текст здесь"


def test_restore_rejects_wrong_length(monkeypatch):
    _capture_request(monkeypatch, "x" * 1000)  # far too long
    out = punctuate.restore_punctuation("коротко", "http://x", "m")
    assert out == "коротко"


def test_marked_variant_requires_speaker_markers(monkeypatch):
    _capture_request(monkeypatch, "просто текст без меток")
    out = punctuate.restore_punctuation(
        "**Микрофон:** привет", "http://x", "m", speaker_markers=True)
    assert out == "**Микрофон:** привет"

    cap = _capture_request(monkeypatch, "**Микрофон:** Привет, как дела?")
    out = punctuate.restore_punctuation(
        "**Микрофон:** привет как дела", "http://x", "m", speaker_markers=True)
    assert out == "**Микрофон:** Привет, как дела?"
    assert "сохрани" in cap["body"]["prompt"]
