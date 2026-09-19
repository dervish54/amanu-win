"""Whisper quality tiers offered by the installer wizard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import tiers  # noqa: E402


def test_tier_map_complete():
    for key in ("accurate", "balanced", "compact"):
        t = tiers.TIERS[key]
        assert t["model"] and t["compute_type"] in ("float16", "int8")
        assert t["size"].startswith("~")


def test_apply_tier_writes_config():
    cfg = {"transcription": {"model": "large-v3-turbo", "device": "cuda"}}
    tiers.apply_tier(cfg, "compact")
    assert cfg["transcription"]["model"] == "small"
    assert cfg["transcription"]["compute_type"] == "int8"


def test_recommended_tier_follows_gpu(monkeypatch):
    monkeypatch.setattr(tiers, "gpu_available", lambda: True)
    assert tiers.recommended_tier() == "accurate"
    monkeypatch.setattr(tiers, "gpu_available", lambda: False)
    assert tiers.recommended_tier() == "compact"
