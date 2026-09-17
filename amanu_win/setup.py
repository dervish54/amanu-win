"""One-command setup: `python -m amanu_win --setup`.

Idempotent. Downloads everything the repo deliberately does not ship:
the Ollama server, the summary model, and the whisper model.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config as config_mod
from .config import Config
from .summary import ollama_available

WINGET_OLLAMA_ID = "Ollama.Ollama"

def ensure_config() -> bool:
    """Create a minimal config file (overrides only); True if created."""
    if config_mod.CONFIG_PATH.exists():
        return False
    config_mod.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_PATH.write_text("{}\n", encoding="utf-8")
    return True


def ensure_dirs(config: Config) -> None:
    config.recordings_dir.mkdir(parents=True, exist_ok=True)
    config.models_dir.mkdir(parents=True, exist_ok=True)


def ensure_ollama(config: Config) -> str:
    """'ready' | 'installed' | 'installed-not-running' | 'skipped: no winget'."""
    if ollama_available(config.summary["ollama_url"]):
        return "ready"
    winget = subprocess.run(
        ["winget", "--version"], capture_output=True, timeout=30)
    if winget.returncode != 0:
        return "skipped: no winget"
    subprocess.run(
        ["winget", "install", "-e", "--id", WINGET_OLLAMA_ID,
         "--accept-source-agreements", "--accept-package-agreements"],
        timeout=1200)
    return "installed" if ollama_available(config.summary["ollama_url"], timeout=5) \
        else "installed-not-running"


def ensure_model(config: Config) -> str:
    """'present' | 'pulled' — pulls the configured summary model if absent."""
    model = config.summary["ollama_model"]
    listed = subprocess.run(
        ["ollama", "list"], capture_output=True, timeout=30)
    if model in listed.stdout.decode("utf-8", errors="replace"):
        return "present"
    subprocess.run(["ollama", "pull", model], timeout=3600)
    return "pulled"


def preload_whisper(config: Config) -> bool:
    """Warm the whisper cache now instead of during the first meeting."""
    from . import transcription
    transcription.load_model(
        config.transcription["model"], config.transcription["device"],
        config.models_dir)
    return True


def register_startup(startup_dir: Path, target: Path | None = None) -> Path:
    """Create Amanu.lnk in the given startup folder; existing one is kept."""
    lnk = startup_dir / "Amanu.lnk"
    if lnk.exists():
        return lnk
    startup_dir.mkdir(parents=True, exist_ok=True)
    if target is None:
        target = Path(sys_exe_dir()) / "amanu.exe"  # caller passes for exe installs
    ps = (
        "$s = New-Object -ComObject WScript.Shell;"
        f"$lnk = $s.CreateShortcut('{lnk}');"
        f"$lnk.TargetPath = '{target}';"
        f"$lnk.Description = 'Amanu meeting recorder';"
        "$lnk.Save();"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=60)
    return lnk


def sys_exe_dir() -> str:
    import sys
    return str(Path(sys.executable).parent)


def run_setup(config: Config, startup: bool = True, log=print) -> dict:
    result = {}
    result["config"] = "created" if ensure_config() else "exists"
    ensure_dirs(config)
    result["dirs"] = "ok"
    result["ollama"] = ensure_ollama(config)
    result["model"] = ensure_model(config)
    result["whisper"] = "cached" if preload_whisper(config) else "failed"

    if startup:
        from os import environ
        startup_dir = Path(environ.get(
            "APPDATA", str(Path.home() / "AppData/Roaming"))) \
            / r"Microsoft\Windows\Start Menu\Programs\Startup"
        lnk = register_startup(startup_dir)
        result["startup"] = "registered" if lnk else "failed"
    else:
        result["startup"] = "skipped"

    for k, v in result.items():
        log(f"  {k:10s}: {v}")
    return result
