"""First-run state machine: installer choices, selectable setup steps."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config  # noqa: E402
from amanu_win import config as config_mod  # noqa: E402
from amanu_win import setup as setup_mod  # noqa: E402


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    saved = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", saved)

    def make(data=None):
        d = {"models_dir": str(tmp_path / "models"),
             "recordings_dir": str(tmp_path / "rec")}
        d.update(data or {})
        return Config(d)

    make.saved = saved
    return make


def test_needs_first_run_only_when_flag_false(cfg):
    assert setup_mod.needs_first_run(cfg()) is False
    assert setup_mod.needs_first_run(cfg({"setup_complete": True})) is False
    assert setup_mod.needs_first_run(cfg({"setup_complete": False})) is True


def test_apply_install_choices(cfg, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "install-choices.json").write_text(
        json.dumps({"tier": "compact", "ollama": False}), encoding="utf-8")
    c = cfg({"setup_complete": False})
    assert setup_mod.apply_install_choices(c, bundle) is True
    assert c.data["transcription"]["model"] == "small"
    assert c.data["summary"]["enabled"] is False
    assert not (bundle / "install-choices.json").exists()
    assert setup_mod.apply_install_choices(c, bundle) is False


def test_run_setup_steps_and_progress(cfg, monkeypatch):
    monkeypatch.setattr(setup_mod, "ensure_ollama", lambda c: "skipped")
    monkeypatch.setattr(setup_mod, "ensure_model", lambda c: "present")
    monkeypatch.setattr(setup_mod, "preload_whisper", lambda c: True)
    monkeypatch.setattr(setup_mod, "ensure_config", lambda: False)
    monkeypatch.setattr(setup_mod, "ensure_dirs", lambda c: None)
    events = []
    result = setup_mod.run_setup(cfg(), startup=False, steps=("whisper",),
                                 progress=lambda s, st: events.append((s, st)),
                                 log=lambda *a: None)
    assert "ollama" not in result
    assert ("whisper", "start") in events and ("whisper", "done") in events


def test_mark_setup_complete(cfg):
    c = cfg({"setup_complete": False})
    setup_mod.mark_setup_complete(c)
    on_disk = json.loads(cfg.saved.read_text(encoding="utf-8"))
    assert on_disk["setup_complete"] is True


def test_apply_install_choices_marks_setup_incomplete(cfg, tmp_path):
    bundle = tmp_path / "b2"
    bundle.mkdir()
    (bundle / "install-choices.json").write_text(
        json.dumps({"tier": "balanced", "ollama": True}), encoding="utf-8")
    c = cfg()
    assert setup_mod.needs_first_run(c) is False
    setup_mod.apply_install_choices(c, bundle)
    assert c.data["setup_complete"] is False


def test_choices_do_not_override_explicit_user_paths(tmp_path, monkeypatch):
    # regression: the installer's default Documents path overwrote the user's
    # existing D:\Recordings preference on upgrade install
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    bundle = tmp_path / "b3"
    bundle.mkdir()
    (bundle / "install-choices.json").write_text(
        json.dumps({"tier": "balanced", "ollama": True,
                    "recordings_dir": "C:\\Wizard\\Default",
                    "models_dir": "C:\\Wizard\\models"}), encoding="utf-8")
    c = Config({"recordings_dir": "D:\\Recordings"})
    setup_mod.apply_install_choices(c, bundle)
    assert c.data["recordings_dir"] == "D:\\Recordings"
    # models_dir was not explicit -> wizard's choice applies
    assert c.data["models_dir"] == "C:\\Wizard\\models"
    # tier/ollama always apply — the wizard is the explicit act of choosing
    assert c.data["transcription"]["model"] == "small"
