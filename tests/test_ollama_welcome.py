"""close_ollama_welcome: the winget-installed Ollama launches a Welcome
window that lingers after first-run; we close windows titled 'Ollama'."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import sys, threading, time
sys.path.insert(0, r'%REPO%')
import tkinter as tk
from amanu_win.setup import close_ollama_welcome

root = tk.Tk()
root.title('Ollama')  # stand-in for the real Welcome window
root.geometry('200x100+100+100')
root.update()

time.sleep(0.5)
closed = close_ollama_welcome()
for _ in range(20):  # WM_CLOSE is async — let the stand-in process it
    try:
        root.update()
    except tk.TclError:
        break  # window gone
    time.sleep(0.05)
time.sleep(0.5)
try:
    root.winfo_exists()
    alive = True
except tk.TclError:
    alive = False
print('closed flag:', closed, 'window alive:', alive)
assert closed and not alive, 'welcome window must be closed'
print('OK')
import os
os._exit(0)
"""


def test_close_ollama_welcome():
    script = SCRIPT.replace("%REPO%", str(REPO).replace("\\", "/"))
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", script],
                       capture_output=True, text=True, timeout=60)
    assert "OK" in r.stdout, r.stdout + r.stderr
