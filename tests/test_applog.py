"""File logging + crash capture: detached exe must leave a log behind."""
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import applog  # noqa: E402


def test_log_file_created_and_written(tmp_path):
    log_path = applog.setup_logging(tmp_path)
    logging.getLogger("test").info("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_path.read_text(encoding="utf-8")
    assert "logging started" in text
    assert "hello log" in text
    logging.getLogger().handlers.clear()


def test_thread_exception_lands_in_log(tmp_path):
    log_path = applog.setup_logging(tmp_path)
    err = threading.Event()

    def die():
        raise ValueError("boom in thread")

    t = threading.Thread(target=die, name="test-crash")
    t.start()
    t.join()
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_path.read_text(encoding="utf-8")
    assert "boom in thread" in text
    assert "test-crash" in text
    logging.getLogger().handlers.clear()
