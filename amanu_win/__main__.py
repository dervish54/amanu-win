"""Entry point: `amanu` (tray app), `amanu --doctor`, `amanu --process <folder>`."""
from __future__ import annotations

import sys
from pathlib import Path

from . import config as config_mod
from .config import Config
from .recorder import default_loopback_name, select_mic_device
from .first_run import run_first_run

def run_tray(config: Config) -> None:
    from .app import TrayApp
    TrayApp(config).run()


def start(config: Config, bundle_dir: Path) -> None:
    """Normal startup: consume installer choices, maybe first-run, then tray."""
    from . import setup as setup_mod
    setup_mod.apply_install_choices(config, bundle_dir)
    if setup_mod.needs_first_run(config):
        run_first_run(config, bundle_dir)
    run_tray(config)


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
    if getattr(sys, "frozen", False) and argv:
        # windowed exe has no console; when used as a CLI, borrow the caller's.
        # Rebind via GetStdHandle (not CONOUT$) so output survives redirection.
        import ctypes
        import msvcrt
        import os
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            k32 = ctypes.windll.kernel32
            sys.stdout = os.fdopen(msvcrt.open_osfhandle(
                k32.GetStdHandle(-11), os.O_WRONLY | os.O_TEXT),
                "w", encoding="utf-8", buffering=1)
            sys.stderr = os.fdopen(msvcrt.open_osfhandle(
                k32.GetStdHandle(-12), os.O_WRONLY | os.O_TEXT),
                "w", encoding="utf-8", buffering=1)
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

    # tray entry moved to run_tray(); start() handles first-run gating
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys.executable).parent
    else:
        bundle_dir = Path(__file__).resolve().parent.parent
    start(config, bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
