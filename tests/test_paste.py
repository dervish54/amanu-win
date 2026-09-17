"""Auto-paste own speech (SuperWhisper-style): after transcription, the
user's own words go to the clipboard and are pasted into the focused
window — no extra keypresses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import paste  # noqa: E402
from amanu_win.paste import own_speech_text  # noqa: E402

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "speaker": "Микрофон", "text": "Привет, это тест."},
    {"start": 2.0, "end": 5.0, "speaker": "Собеседник", "text": "Да, слышно."},
    {"start": 5.0, "end": 7.0, "speaker": "Микрофон", "text": "Отлично, спасибо."},
]


def test_own_speech_keeps_only_mic_segments():
    text = own_speech_text(SEGMENTS, mic_label="Микрофон")
    assert "Привет" in text
    assert "Отлично" in text
    assert "Слышно" not in text
    assert "слышно" not in text.lower()


def test_own_speech_empty_when_no_mic_speech():
    far_only = [s for s in SEGMENTS if s["speaker"] != "Микрофон"]
    assert own_speech_text(far_only, mic_label="Микрофон") == ""


def test_paste_text_sets_clipboard_and_sends_ctrl_v(monkeypatch):
    sent = []
    clipboard = {}
    monkeypatch.setattr(paste, "set_clipboard_text",
                        lambda t: clipboard.update(text=t))
    monkeypatch.setattr(paste.keyboard, "send",
                        lambda combo: sent.append(combo))

    paste.paste_text("hello world")

    assert clipboard.get("text") == "hello world"
    assert sent == ["ctrl+v"]


def test_set_clipboard_text_roundtrip():
    # touches the real clipboard; the previous content is restored
    original = paste.get_clipboard_text()
    try:
        paste.set_clipboard_text("проверка unicode 123")
        assert paste.get_clipboard_text() == "проверка unicode 123"
    finally:
        paste.set_clipboard_text(original or "")
