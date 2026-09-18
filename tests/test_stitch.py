"""LLM stitcher: fragments with explicit schedule metadata -> whole text.

The model must remove duplicated words at the 2s overlaps and restore
punctuation/casing without rewording. Any failure falls back to the naive
overlap-cut join so the paste always happens.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import stitch  # noqa: E402


def _frag(start, texts):
    segs = []
    t = 0.0  # local to fragment
    for text in texts:
        segs.append({"start": t, "end": t + 2.0, "text": text})
        t += 2.0
    return {"start_s": start, "end_s": start + t, "segments": segs}


FRAGMENTS = [
    _frag(0.0, ["привет это тест", "диктовки с чанками", "часть один два"]),          # 0-6
    _frag(4.0, ["диктовки с чанками", "часть один два", "продолжение второй части"]),  # overlap 2s
]


def test_naive_join_drops_overlap_segments():
    text = stitch.naive_join(FRAGMENTS, overlap_s=2)
    assert "продолжение второй части" in text
    # overlapped words must appear exactly once
    assert text.count("диктовки с чанками") == 1
    assert text.count("часть один два") == 1


def test_naive_join_single_fragment():
    assert stitch.naive_join([FRAGMENTS[0]], overlap_s=2) == \
        "привет это тест диктовки с чанками часть один два"


class _FakeResponse:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _llm(monkeypatch, response_text):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"response": response_text})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_stitch_explains_schedule_to_llm(monkeypatch):
    cap = _llm(monkeypatch, "Привет, это тест диктовки с чанками, часть один два. Продолжение второй части.")
    out = stitch.stitch_fragments(FRAGMENTS, "http://x", "m",
                                  first_s=10, stride_s=8, overlap_s=2)
    prompt = cap["body"]["prompt"]
    # the model must know exactly how the fragments were produced
    assert "8" in prompt and "2" in prompt
    assert "пересека" in prompt or "пересеч" in prompt or "повторя" in prompt
    assert "привет это тест" in prompt
    assert "продолжение второй части" in prompt
    assert out.startswith("Привет")


def test_stitch_falls_back_on_server_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = stitch.stitch_fragments(FRAGMENTS, "http://x", "m",
                                  first_s=10, stride_s=8, overlap_s=2)
    assert out == stitch.naive_join(FRAGMENTS, overlap_s=2)


def test_stitch_falls_back_on_garbage(monkeypatch):
    _llm(monkeypatch, "")
    out = stitch.stitch_fragments(FRAGMENTS, "http://x", "m",
                                  first_s=10, stride_s=8, overlap_s=2)
    assert out == stitch.naive_join(FRAGMENTS, overlap_s=2)


def test_stitch_falls_back_when_first_words_lost(monkeypatch):
    _llm(monkeypatch, "Совершенно другой текст, который потерял начало и все слова.")
    out = stitch.stitch_fragments(FRAGMENTS, "http://x", "m",
                                  first_s=10, stride_s=8, overlap_s=2)
    assert out == stitch.naive_join(FRAGMENTS, overlap_s=2)
