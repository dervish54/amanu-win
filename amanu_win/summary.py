"""Local summaries through Ollama (localhost), Amanu's local-first path.

If Ollama is not installed or reachable, the summary is marked deferred in
meta.json instead of being dropped — same policy as the macOS app for work
that cannot run right now.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

PROMPT_RU = """Ты — ассистент, который готовит итоги встреч. Ниже полная расшифровка встречи с пометками говорящих.

Напиши подробное резюме на русском языке со следующими разделами:
## Тема
## Ключевые моменты
## Решения
## Действия (кто и что делает)
## Открытые вопросы

Расшифровка:
{transcript}
"""

PROMPT_EN = """You summarize meetings. Below is a full transcript with speaker labels.

Write a detailed summary with these sections:
## Topic
## Key points
## Decisions
## Action items (who does what)
## Open questions

Transcript:
{transcript}
"""


def ollama_available(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(base_url + "/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def summarize(transcript_md: str, base_url: str, model: str, language: str) -> str:
    prompt = (PROMPT_RU if language == "ru" else PROMPT_EN).format(transcript=transcript_md)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["response"].strip()
