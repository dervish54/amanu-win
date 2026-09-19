"""Whisper quality tiers offered by the installer wizard.

The tier choice lands in config.json (transcription.model +
transcription.compute_type); transcription.load_model honors both.
"""
from __future__ import annotations

import shutil

TIERS = {
    "accurate": {"label": "Точная", "model": "large-v3-turbo",
                 "compute_type": "float16", "size": "~1.6 ГБ",
                 "needs_gpu": True},
    "balanced": {"label": "Сбалансированная", "model": "small",
                 "compute_type": "float16", "size": "~460 МБ",
                 "needs_gpu": False},
    "compact": {"label": "Компактная", "model": "small",
                "compute_type": "int8", "size": "~460 МБ",
                "needs_gpu": False},
}


def gpu_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def recommended_tier() -> str:
    return "accurate" if gpu_available() else "compact"


def apply_tier(config_data: dict, tier: str) -> None:
    t = TIERS[tier]
    tr = config_data.setdefault("transcription", {})
    tr["model"] = t["model"]
    tr["compute_type"] = t["compute_type"]
