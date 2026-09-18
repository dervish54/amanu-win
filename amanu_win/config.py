"""Configuration: %USERPROFILE%\\.config\\amanu\\config.json, Amanu-compatible keys.

Only values that differ from the defaults are stored, same as the macOS app.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("AMANU_CONFIG", Path.home() / ".config" / "amanu" / "config.json"))

DEFAULTS: dict = {
    "recordings_dir": "~/Recordings",
    "keep_audio": True,
    "hotkey": "ctrl+alt+r",
    "models_dir": "~/.cache/amanu/models",
    "paste": {
        "enabled": True,   # insert own speech into the focused window after transcription
    },
    "punctuation": {
        "enabled": True,   # restore punctuation/casing via local Ollama before paste/summary
    },
    "transcription": {
        "enabled": True,
        "language": "auto",          # auto | ru | en | ...
        "model": "large-v3-turbo",   # faster-whisper model name
        "device": "cuda",            # cuda | cpu
        "mic_speaker": "Микрофон",
        "system_speaker": "Собеседник",
        "streaming": {
            "enabled": True,         # transcribe mic chunks live, stitch via LLM at stop
            "first_s": 10.0,
            "stride_s": 8.0,
            "overlap_s": 2.0,
        },
    },
    "summary": {
        "enabled": True,
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen2.5:7b",
        "language": "ru",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict | None = None):
        self.data = _deep_merge(DEFAULTS, data or {})

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                return cls(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- convenience accessors -------------------------------------------------
    @property
    def recordings_dir(self) -> Path:
        return Path(os.path.expandvars(self.data["recordings_dir"])).expanduser()

    @property
    def keep_audio(self) -> bool:
        return bool(self.data["keep_audio"])

    @property
    def hotkey(self) -> str:
        return self.data["hotkey"]

    @property
    def models_dir(self) -> Path:
        return Path(os.path.expandvars(self.data["models_dir"])).expanduser()

    @property
    def transcription(self) -> dict:
        return self.data["transcription"]

    @property
    def summary(self) -> dict:
        return self.data["summary"]
