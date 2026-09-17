"""Setup command: one idempotent step from fresh checkout to working app.

Everything heavy (models, Ollama) is downloaded here; nothing heavy is
shipped in the repo.
"""
import json
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import setup as setup_mod  # noqa: E402
from amanu_win.config import Config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return Config({
        "recordings_dir": str(tmp_path / "rec"),
        "models_dir": str(tmp_path / "models"),
    })


def test_ensure_config_creates_minimal_file(tmp_path, monkeypatch):
    from amanu_win import config as cfgmod
    target = tmp_path / "cfg" / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", target)
    created = setup_mod.ensure_config()
    assert created is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {}  # only overrides belong in the user file
    assert setup_mod.ensure_config() is False  # idempotent


def test_ensure_dirs_creates_storage(cfg):
    setup_mod.ensure_dirs(cfg)
    assert cfg.recordings_dir.exists()
    assert cfg.models_dir.exists()


def test_ollama_missing_installs_via_winget(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(setup_mod, "ollama_available", lambda url, timeout=2.0: False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0, stdout=b"", stderr=b""))
    result = setup_mod.ensure_ollama(cfg)
    assert any("winget" in " ".join(map(str, c)) for c in calls)
    assert result in ("installed", "installed-not-running")


def test_ollama_present_skips_install(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(setup_mod, "ollama_available", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0))
    result = setup_mod.ensure_ollama(cfg)
    assert result == "ready"
    assert not any("winget" in " ".join(map(str, c)) for c in calls)


def test_model_pull_skipped_when_present(cfg, monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        out = b"NAME                      ID\nqwen2.5:7b                deadbeef\n" if "list" in args else b""
        return subprocess.CompletedProcess(args, 0, stdout=out)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert setup_mod.ensure_model(cfg) == "present"
    assert not any("pull" in a for a in calls)

    def fake_run_missing(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout=b"NAME ID\n")

    monkeypatch.setattr(subprocess, "run", fake_run_missing)
    assert setup_mod.ensure_model(cfg) == "pulled"


def test_preload_whisper_calls_loader(cfg, monkeypatch):
    loaded = []
    from amanu_win import transcription
    monkeypatch.setattr(transcription, "load_model",
                        lambda name, device, models_dir: loaded.append(name) or object())
    assert setup_mod.preload_whisper(cfg) is True
    assert loaded == [cfg.transcription["model"]]


def test_register_startup_writes_lnk(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess(a[0], 0))
    lnk = setup_mod.register_startup(tmp_path / "startup")
    assert lnk.name == "Amanu.lnk"
    assert any("powershell" in " ".join(map(str, c[0])) for c in calls)
    # idempotent: existing shortcut is not rewritten
    lnk.touch()
    calls.clear()
    setup_mod.register_startup(tmp_path / "startup")
    assert not calls


def test_run_setup_aggregates(cfg, monkeypatch, tmp_path):
    from amanu_win import config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(setup_mod, "ensure_ollama", lambda c: "ready")
    monkeypatch.setattr(setup_mod, "ensure_model", lambda c: "present")
    monkeypatch.setattr(setup_mod, "preload_whisper", lambda c: True)
    monkeypatch.setattr(setup_mod, "register_startup", lambda p: Path("x.lnk"))
    result = setup_mod.run_setup(cfg)
    assert result["config"] == "created"
    assert result["ollama"] == "ready"
    assert result["startup"] == "registered"

    result2 = setup_mod.run_setup(cfg, startup=False)
    assert result2["config"] == "exists"
    assert result2["startup"] == "skipped"
