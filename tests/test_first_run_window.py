"""First-run window lifecycle smoke test, in a subprocess.

Tkinter must never touch the pytest process (off-thread Tcl crashes the
host at teardown) — the panel tests established the subprocess pattern.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import sys, threading, time
sys.path.insert(0, r'%REPO%')
from pathlib import Path
import tempfile, json
from amanu_win.config import Config
from amanu_win import first_run

tmp = Path(tempfile.mkdtemp())
cfgp = tmp / 'config.json'
cfgp.write_text(json.dumps({'setup_complete': False,
    'models_dir': str(tmp / 'm'), 'recordings_dir': str(tmp / 'r')}),
    encoding='utf-8')
cfg = Config(json.loads(cfgp.read_text(encoding='utf-8')))

# stub the heavy work: no downloads in a smoke test
import amanu_win.setup as s
s.run_setup = lambda *a, **k: {'whisper': 'cached'}
s.mark_setup_complete = lambda c: None
import amanu_win.recorder as rec
rec.select_mic_device = lambda prefer=None: None

done = []
t = threading.Thread(target=lambda: (first_run.run_first_run(cfg, tmp),
                                     done.append(1)), daemon=True)
t.start()
deadline = time.monotonic() + 20
w = None
while time.monotonic() < deadline:
    w = first_run._LAST_WINDOW
    if w is not None and w.alive:
        break
    time.sleep(0.1)
assert w is not None and w.alive, 'window never appeared'
print('window alive, rows:', sorted(w._rows))
if w.alive:
    w.close()
    time.sleep(1)
assert not w.alive
print('OK')
# hard exit: the Tcl interpreter belongs to the worker thread, and normal
# interpreter teardown (Tcl exit handler on the main thread) crashes or
# hangs nondeterministically — os._exit skips it
import os
os._exit(0)
"""


def test_first_run_window_lifecycle_subprocess():
    script = SCRIPT.replace("%REPO%", str(REPO).replace("\\", "/"))
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", script],
                       capture_output=True, text=True, timeout=120)
    assert "OK" in r.stdout, r.stdout + r.stderr
