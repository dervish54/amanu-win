"""File logging + crash capture.

The app usually runs detached with no console, so every lifecycle event
goes to ~/.config/amanu/amanu-win.log. faulthandler plus sys/threading
exception hooks make crashes — including native (driver/PortAudio) ones —
leave a traceback in that file.
"""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "amanu-win.log"


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_NAME

    handler = RotatingFileHandler(str(log_path), maxBytes=2_000_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    crash = open(log_path, "a", encoding="utf-8")
    faulthandler.enable(file=crash)

    def _sys_hook(exc_type, exc, tb):
        logging.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args):
        logging.critical(
            f"uncaught exception in thread {args.thread.name}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook

    logging.getLogger(__name__).info("logging started -> %s", log_path)
    return log_path
