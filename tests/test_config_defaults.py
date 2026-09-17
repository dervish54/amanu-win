"""Published defaults must be machine-independent.

D:\\Recordings and similar absolute paths belong in the user's local
config.json, never in the shipped defaults.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config, DEFAULTS  # noqa: E402


def test_defaults_are_machine_independent():
    blob = repr(DEFAULTS)
    assert "D:\\" not in blob and "D:/" not in blob
    assert "Mikhail" not in blob
    assert "C:\\" not in blob


def test_defaults_expand_under_home(tmp_path):
    cfg = Config()
    assert cfg.recordings_dir.name == "Recordings"
    assert cfg.recordings_dir.is_absolute()
    assert cfg.models_dir.is_absolute()
    assert "amanu" in str(cfg.models_dir)


def test_user_config_overrides_defaults(tmp_path, monkeypatch):
    import json
    from amanu_win import config as cfgmod
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"recordings_dir": "D:\\Recordings"}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", f)
    cfg = cfgmod.Config.load()
    assert str(cfg.recordings_dir) == r"D:\Recordings"
