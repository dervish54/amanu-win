"""Entry point: `amanu` (tray app), `amanu --doctor`, `amanu --process <folder>`."""
from __future__ import annotations

import sys
from pathlib import Path

from . import config as config_mod
from .config import Config
from .recorder import default_loopback_name, select_mic_device


def doctor(config: Config) -> int:
    print("Amanu-Win doctor")
    print(f"  config:          {__import__('amanu_win.config', fromlist=['CONFIG_PATH']).CONFIG_PATH}")
    print(f"  recordings_dir:  {config.recordings_dir} (exists: {config.recordings_dir.exists()})")
    print(f"  models_dir:      {config.models_dir}")
    picked = select_mic_device()
    mic_desc = f"{picked[2]} @ {picked[1]}Hz" if picked else "none"
    print(f"  mic (endpoint):  {mic_desc}")
    print(f"  system loopback: {default_loopback_name()}")

    from .summary import ollama_available
    print(f"  ollama:          {'reachable' if ollama_available(config.summary['ollama_url']) else 'NOT reachable'} ({config.summary['ollama_url']})")

    try:
        from .transcription import load_model
        m = load_model(config.transcription["model"], config.transcription["device"], config.models_dir)
        print(f"  faster-whisper:  OK ({config.transcription['device']})")
        del m
    except Exception as e:
        print(f"  faster-whisper:  FAILED ({e})")
        return 1
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .applog import setup_logging
    setup_logging(config_mod.CONFIG_PATH.parent)
    config = Config.load()

    if argv and argv[0] == "--doctor":
        return doctor(config)
    if argv and argv[0] == "--setup":
        from .setup import run_setup
        run_setup(config, startup="--no-startup" not in argv)
        return 0
    if argv and argv[0] == "--process":
        from .session import SessionManager
        sm = SessionManager(config, log=lambda m: print(f"[amanu] {m}"))
        sm.process(Path(argv[1]))
        return 0

    from .app import TrayApp
    TrayApp(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
