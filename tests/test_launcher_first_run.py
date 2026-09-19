"""Startup sequence: installer choices -> first-run gate -> tray."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config  # noqa: E402
from amanu_win import __main__ as main_mod  # noqa: E402


def _cfg(tmp_path, data=None):
    d = {"models_dir": str(tmp_path / "m"),
         "recordings_dir": str(tmp_path / "r")}
    d.update(data or {})
    return Config(d)


def test_startup_invokes_first_run_when_flag_false(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "run_first_run",
                        lambda cfg, bundle: calls.append("first_run"))
    monkeypatch.setattr(main_mod, "run_tray",
                        lambda cfg: calls.append("tray"))
    main_mod.start(_cfg(tmp_path, {"setup_complete": False}),
                   bundle_dir=tmp_path)
    assert calls == ["first_run", "tray"]


def test_startup_skips_first_run_for_existing_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "run_first_run",
                        lambda cfg, bundle: calls.append("first_run"))
    monkeypatch.setattr(main_mod, "run_tray",
                        lambda cfg: calls.append("tray"))
    main_mod.start(_cfg(tmp_path), bundle_dir=tmp_path)
    assert calls == ["tray"]
