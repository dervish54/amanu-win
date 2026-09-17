"""Punctuation restoration through the local Ollama model.

Whisper's Russian output (and turbo models generally) loses punctuation
and capitalization. The original Amanu solves this at the engine level
(GigaAM for Russian, Parakeet for English); here the transcript is
post-processed by the already-installed local LLM under a strict
no-rewording prompt. Any failure returns the original text unchanged.
"""
import json
import urllib.request

PROMPT_PLAIN_RU = """Текст распознан речевой моделью и потерял пунктуацию и заглавные буквы.
Верни тот же самый текст с расставленной пунктуацией и заглавными буквами.
Не перефразируй, не исправляй и не добавляй слова, не меняй порядок слов.
Если текст на английском — расставь пунктуацию по правилам английского языка.
Верни только текст, без комментариев и кавычек.

Текст:
{text}
"""

PROMPT_MARKED_RU = """Текст — расшифровка встречи с пометками говорящих, распознанная речевой моделью.
Верни тот же текст с расставленной пунктуацией и заглавными буквами.
Не перефразируй, не исправляй и не добавляй слова.
Строки-заголовки вида **Имя:** сохрани в точности, не меняя их.
Верни только текст расшифровки, без комментариев.

Текст:
{text}
"""


def _generate(base_url: str, model: str, prompt: str, timeout: float = 300.0) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1},
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["response"].strip()


def restore_punctuation(text: str, base_url: str, model: str,
                        speaker_markers: bool = False) -> str:
    """Punctuated text; returns the input unchanged on any failure."""
    text = (text or "").strip()
    if not text:
        return text
    prompt = (PROMPT_MARKED_RU if speaker_markers else PROMPT_PLAIN_RU).format(text=text)
    try:
        result = _generate(base_url, model, prompt)
    except Exception:
        return text
    # fail-safe validation: never trade a transcript for garbage
    if not result or not (0.3 * len(text) <= len(result) <= 2.5 * len(text)):
        return text
    if speaker_markers and "**" not in result:
        return text
    return result
