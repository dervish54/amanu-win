"""LLM stitching of overlapping transcript fragments.

Fragments arrive from chunk transcription knowing exactly how they were
produced (first window 0-10s, then 8s stride with 2s overlap). The model
removes the duplicated words at the overlaps and restores
punctuation/casing without rewording. Any LLM failure falls back to the
naive word-boundary join, so the result — and therefore the paste — is
always available.
"""
from __future__ import annotations

import re

from .punctuate import _generate

WORD_RE = re.compile(r"[^\w']+|\s+", re.UNICODE)


def naive_join(fragments: list[dict], overlap_s: float) -> str:
    """Join fragments cutting duplicated words at each overlap boundary."""
    acc: list[str] = []
    for i, frag in enumerate(fragments):
        segs = frag.get("segments", [])
        if i > 0:
            # drop segments lying entirely inside the overlap region
            segs = [s for s in segs if s.get("end", 0) > overlap_s]
        words = []
        for s in segs:
            words.extend(w for w in WORD_RE.split(s.get("text", "")) if w)
        if acc and words:
            words = _dedup_boundary(acc, words)
        acc.extend(words)
    return " ".join(acc)


def _norm(words: list[str]) -> list[str]:
    return [w.lower() for w in words]


def _dedup_boundary(acc_words: list[str], new_words: list[str]) -> list[str]:
    a, b = _norm(acc_words), _norm(new_words)
    max_k = min(len(a), len(b), 40)
    for k in range(max_k, 0, -1):
        if a[-k:] == b[:k]:
            return new_words[k:]
    return new_words


PROMPT_RU = """Это фрагменты расшифровки устной речи. Они получены так: первый фрагмент — первые {first_s:.0f} с записи, каждый следующий начинается на {stride_s:.0f} с позже предыдущего, то есть первые {overlap_s:.0f} с каждого фрагмента повторяют конец предыдущего фрагмента.

Собери из фрагментов единый цельный текст:
- убери слова, повторяющиеся из-за пересечений (каждое слово должно войти ровно один раз);
- не перефразируй и не исправляй слова, не добавляй ничего от себя;
- расставь пунктуацию и заглавные буквы по правилам языка текста;
- верни только собранный текст, без комментариев, нумерации и кавычек.

Фрагменты:
{fragments_text}
"""


def stitch_fragments(fragments: list[dict], base_url: str, model: str,
                     first_s: float, stride_s: float, overlap_s: float) -> str:
    """Whole text from overlapping fragments; naive join on any failure."""
    fallback = naive_join(fragments, overlap_s)
    if not fallback:
        return fallback

    lines = []
    for i, f in enumerate(fragments, 1):
        text = " ".join(s.get("text", "") for s in f.get("segments", []))
        lines.append(f"[{i}] {f['start_s']:.0f}-{f['end_s']:.0f} с: {text}")
    prompt = PROMPT_RU.format(
        first_s=first_s, stride_s=stride_s, overlap_s=overlap_s,
        fragments_text="\n".join(lines))
    try:
        result = _generate(base_url, model, prompt)
    except Exception:
        return fallback

    # fail-safe: the model must have kept the beginning of the recording
    fb_words = _norm([w for w in WORD_RE.split(fallback) if w])
    first_words = fb_words[:3]
    res_words = set(_norm([w for w in WORD_RE.split(result) if w]))
    if (not result
            or not (0.3 * len(fallback) <= len(result) <= 2.5 * len(fallback))
            or not all(w in res_words for w in first_words)):
        return fallback
    return result.strip()
