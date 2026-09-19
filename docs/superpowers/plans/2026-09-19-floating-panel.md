# Floating Panel (SuperWhisper-style) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A semi-transparent draggable floating panel: collapsed to a minimal dark bar showing recording state, expanding on hover into three actions (record on/off, open folder, quit).

**Architecture:** tkinter `Toplevel` (stdlib, zero new heavy deps), borderless + topmost + alpha, with `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` applied via ctypes so the panel never steals focus from the paste target. Own thread (tkinter is single-threaded), state pushed through a queue — same marshalling pattern as the tray icon. Logic lives in a pure `PanelController` (testable headless); the widget is a thin view.

**Tech Stack:** Python 3.10, tkinter, ctypes (already used for clipboard), existing `pystray` tray app.

**Spec:** `SPEC.md` (F1 hotkey, F9 instant state visibility, N2 responsiveness); approved design in chat 2026-09-19.

## Global Constraints

- No new third-party dependencies (tkinter is stdlib; ctypes already used).
- Panel must NEVER take keyboard focus (would break auto-paste into the focused window).
- Recording state visible while collapsed: stripe color gray=idle, green=recording, amber=processing/starting.
- Russian UI labels, same as the tray.
- All state changes go through the panel's queue; the tk thread is the only one touching widgets.
- Tests run against `PanelController` logic, not pixels; one guarded widget smoke test.
- TDD: failing test before every behavior.

---

### Task 1: PanelController — pure state and command logic

**Files:**
- Create: `amanu_win/panel.py`
- Test: `tests/test_panel.py`

**Interfaces:**
- Produces: `PanelController(on_record, on_open_folder, on_quit, get_pos, save_pos)` with:
  - `stripe_for(state: str) -> str` — hex color; state in `{"idle","recording","processing","pending"}`
  - `status_text_for(state: str) -> str`
  - `dispatch(command: str) -> None` — commands `"record"`, `"folder"`, `"quit"`
  - `remember_pos(x: int, y: int) -> None` / `initial_pos() -> tuple[int, int]`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.panel import PanelController  # noqa: E402


def _ctl():
    calls = []
    saved = {}
    ctl = PanelController(
        on_record=lambda: calls.append("record"),
        on_open_folder=lambda: calls.append("folder"),
        on_quit=lambda: calls.append("quit"),
        get_pos=lambda: None,
        save_pos=lambda x, y: saved.update(x=x, y=y),
    )
    return ctl, calls, saved


def test_stripe_colors():
    ctl, _, _ = _ctl()
    assert ctl.stripe_for("idle") == "#8a8a8a"
    assert ctl.stripe_for("recording") == "#2ea043"
    assert ctl.stripe_for("processing") == "#dca014"
    assert ctl.stripe_for("pending") == "#dca014"


def test_dispatch_routes_commands():
    ctl, calls, _ = _ctl()
    ctl.dispatch("record")
    ctl.dispatch("folder")
    ctl.dispatch("quit")
    assert calls == ["record", "folder", "quit"]


def test_dispatch_rejects_unknown():
    ctl, calls, _ = _ctl()
    ctl.dispatch("nonsense")
    assert calls == []


def test_position_roundtrip():
    ctl, _, saved = _ctl()
    assert ctl.initial_pos() is None
    ctl.remember_pos(120, 300)
    assert saved == {"x": 120, "y": 300}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_panel.py -q`
Expected: collection error — module `amanu_win.panel` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
"""Floating overlay panel: SuperWhisper-style collapsed bar that expands
on hover. Pure logic lives in PanelController so it is testable headless;
FloatingPanel (Task 2) is the thin tkinter view."""
from __future__ import annotations

STRIPE = {
    "idle": "#8a8a8a",
    "recording": "#2ea043",
    "processing": "#dca014",
    "pending": "#dca014",
}

STATUS_TEXT = {
    "idle": "готов",
    "recording": "идёт запись",
    "processing": "обработка…",
    "pending": "…",
}


class PanelController:
    def __init__(self, on_record, on_open_folder, on_quit, get_pos, save_pos):
        self._handlers = {"record": on_record, "folder": on_open_folder, "quit": on_quit}
        self._get_pos = get_pos
        self._save_pos = save_pos

    def stripe_for(self, state: str) -> str:
        return STRIPE[state]

    def status_text_for(self, state: str) -> str:
        return STATUS_TEXT[state]

    def dispatch(self, command: str) -> None:
        handler = self._handlers.get(command)
        if handler is not None:
            handler()

    def initial_pos(self):
        return self._get_pos()

    def remember_pos(self, x: int, y: int) -> None:
        self._save_pos(x, y)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_panel.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Mikhail/amanu-win
git add amanu_win/panel.py tests/test_panel.py
git commit -m "Add PanelController: state colors, command dispatch, position persistence for the floating panel"
```

---

### Task 2: FloatingPanel — tkinter view with hover expand, drag, no-focus

**Files:**
- Modify: `amanu_win/panel.py` (append the view)
- Test: `tests/test_panel.py` (append smoke test)

**Interfaces:**
- Consumes: `PanelController` from Task 1.
- Produces: `FloatingPanel(controller, config_panel: dict)` with:
  - `start() -> None` — spawns the tk thread
  - `set_state(state: str) -> None` — thread-safe (queue)
  - `close() -> None` — thread-safe
  - `alive -> bool`

- [ ] **Step 1: Write the failing smoke test**

```python
import time

def test_panel_window_lifecycle():
    from amanu_win.panel import FloatingPanel, PanelController
    ctl = PanelController(lambda: None, lambda: None, lambda: None,
                          get_pos=lambda: None, save_pos=lambda x, y: None)
    panel = FloatingPanel(ctl, {})
    panel.start()
    deadline = time.monotonic() + 5
    while not panel.alive and time.monotonic() < deadline:
        time.sleep(0.05)
    assert panel.alive
    panel.set_state("recording")
    panel.set_state("idle")
    panel.close()
    deadline = time.monotonic() + 5
    while panel.alive and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not panel.alive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_panel.py::test_panel_window_lifecycle -q`
Expected: FAIL — `FloatingPanel` does not exist.

- [ ] **Step 3: Write the implementation** (append to `amanu_win/panel.py`)

```python
import queue
import threading
import tkinter as tk

BAR_W, BAR_H = 64, 16
PANEL_W, PANEL_H = 220, 132
BG = "#2b2b2b"
ALPHA = 0.72

# never steal focus: paste must land in the user's app
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20


def _make_noactivate(root: tk.Tk) -> None:
    import ctypes
    hwnd = int(root.wm_frame(), 16)
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


class FloatingPanel:
    def __init__(self, controller: PanelController, config_panel: dict):
        self.ctl = controller
        self._cfg = config_panel
        self._q: queue.Queue = queue.Queue()
        self.alive = False
        self._t = threading.Thread(target=self._run, name="panel", daemon=True)

    def start(self) -> None:
        self._t.start()

    def set_state(self, state: str) -> None:
        self._q.put(("state", state))

    def close(self) -> None:
        self._q.put(("close", None))

    def _run(self) -> None:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", ALPHA)
        pos = self.ctl.initial_pos()
        x, y = pos if pos else (root.winfo_screenwidth() - BAR_W - 40, 60)
        self._root = root
        self._expanded = False
        self._state = "idle"

        bar = tk.Frame(root, bg=BG, width=BAR_W, height=BAR_H)
        bar.pack()
        stripe = tk.Frame(bar, height=4)
        stripe.pack(side="bottom", fill="x")
        self._stripe = stripe

        body = tk.Frame(root, bg=BG)
        self._status = tk.Label(body, text="", fg="#ddd", bg=BG)
        self._status.pack(fill="x", pady=(4, 2))
        for cmd, label in (("record", "⏺ Запись"), ("folder", "📁 Папка"),
                           ("quit", "✕ Выйти из приложения")):
            tk.Button(body, text=label, anchor="w", relief="flat",
                      bg=BG, fg="#eee", activebackground="#3d3d3d",
                      command=lambda c=cmd: self.ctl.dispatch(c),
                      ).pack(fill="x", padx=2)

        root.geometry(f"{BAR_W}x{BAR_H}+{x}+{y}")
        root.bind("<Enter>", lambda e: self._expand(True))
        root.bind("<Leave>", lambda e: self._expand(False))
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        bar.bind("<ButtonRelease-1>", self._drag_end)
        self._drag = None

        root.update_idletasks()
        _make_noactivate(root)
        self.alive = True
        root.after(80, self._poll)
        root.mainloop()
        self.alive = False

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "close":
                    self._root.destroy()
                    return
                if kind == "state":
                    self._state = payload
                    self._stripe.config(bg=self.ctl.stripe_for(payload))
                    self._status.config(text=self.ctl.status_text_for(payload))
        except queue.Empty:
            pass
        self._root.after(80, self._poll)

    def _expand(self, expand: bool) -> None:
        if expand == self._expanded:
            return
        self._expanded = expand
        x, y = self._root.winfo_x(), self._root.winfo_y()
        if expand:
            self._root.geometry(f"{PANEL_W}x{PANEL_H}+{x}+{y - PANEL_H + BAR_H}")
        else:
            self._root.geometry(f"{BAR_W}x{BAR_H}+{x}+{y + PANEL_H - BAR_H}")

    def _drag_start(self, e):
        self._drag = (e.x_root - self._root.winfo_x(), e.y_root - self._root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self._root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, e):
        if self._drag:
            self.ctl.remember_pos(self._root.winfo_x(), self._root.winfo_y())
            self._drag = None
```

- [ ] **Step 4: Run the smoke test**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_panel.py -q`
Expected: 5 passed (window appears briefly on screen — expected on a desktop).

- [ ] **Step 5: Commit**

```bash
git add amanu_win/panel.py tests/test_panel.py
git commit -m "Add FloatingPanel: collapsible no-focus overlay bar with hover expand, drag, and three actions"
```

---

### Task 3: Wire the panel into TrayApp

**Files:**
- Modify: `amanu_win/app.py`
- Modify: `amanu_win/config.py`
- Test: `tests/test_async_toggle.py` (append wiring test)

**Interfaces:**
- Consumes: `FloatingPanel`, `PanelController` (Task 2); `SessionManager.capture_warnings` (existing).
- Produces: `TrayApp._panel_state() -> str`; panel actions call the existing `toggle()` (worker queue), `_open_recordings()`, and a new `quit()`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_async_toggle.py`)

```python
def test_panel_state_mapping(app):
    from amanu_win.app import TrayApp
    assert app._panel_state() == "idle"
    app.sessions.recorder = object()  # fake: recording
    assert app._panel_state() == "recording"
    app.sessions.recorder = None
    app.sessions.processing.set()
    assert app._panel_state() == "processing"
    app.sessions.processing.clear()
    app._pending = "start"
    assert app._panel_state() == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_async_toggle.py::test_panel_state_mapping -q`
Expected: FAIL — `_panel_state` does not exist.

- [ ] **Step 3: Implement the wiring**

In `amanu_win/config.py` DEFAULTS add:

```python
    "panel": {
        "enabled": True,
        "x": None,
        "y": None,
    },
```

In `amanu_win/app.py` — imports and construction:

```python
from .panel import FloatingPanel, PanelController
```

In `TrayApp.__init__`, after `self.icon = ...`:

```python
        pcfg = self.config.data.get("panel", {})
        self.panel = None
        if pcfg.get("enabled", True):
            ctl = PanelController(
                on_record=lambda: self._work_q.put("toggle"),
                on_open_folder=self._open_recordings,
                on_quit=self.quit,
                get_pos=lambda: (pcfg.get("x"), pcfg.get("y"))
                                if pcfg.get("x") is not None else None,
                save_pos=self._save_panel_pos,
            )
            self.panel = FloatingPanel(ctl, pcfg)
```

Add methods to `TrayApp`:

```python
    def _panel_state(self) -> str:
        if self._pending:
            return "pending"
        if self.sessions.is_recording:
            return "recording"
        if self.sessions.processing.is_set():
            return "processing"
        return "idle"

    def _save_panel_pos(self, x: int, y: int) -> None:
        self.config.data.setdefault("panel", {}).update({"x": x, "y": y})
        self.config.save()

    def quit(self) -> None:
        if self.panel is not None:
            self.panel.close()
        self.icon.stop()
```

At the end of `_refresh()`:

```python
        if self.panel is not None:
            self.panel.set_state(self._panel_state())
```

In `run()`: before `self.icon.run()` add `if self.panel is not None: self.panel.start()`.

- [ ] **Step 4: Run tests**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/ -q`
Expected: all pass (79).

- [ ] **Step 5: Commit**

```bash
git add amanu_win/app.py amanu_win/config.py tests/test_async_toggle.py
git commit -m "Wire the floating panel into the tray app: state stripe mirrors recording/processing, actions reuse the toggle worker, position persists to config"
```

---

### Task 4: Visual verification, build, deploy

**Files:** none (verification only)

- [ ] **Step 1: Screenshot both states**

```python
# D:/amanu-win/test_panel_visual.py — throwaway
import sys, time
sys.path.insert(0, r"C:/Users/Mikhail/amanu-win")
from PIL import ImageGrab
from amanu_win.panel import FloatingPanel, PanelController

ctl = PanelController(lambda: None, lambda: None, lambda: None,
                      get_pos=lambda: None, save_pos=lambda x, y: None)
p = FloatingPanel(ctl, {})
p.start()
time.sleep(2)
ImageGrab.grab().save(r"D:/amanu-win/panel-collapsed.png")
# expand programmatically for the shot
p._q.put(("state", "recording"))
import ctypes
time.sleep(1)
ImageGrab.grab().save(r"D:/amanu-win/panel-recording.png")
p.close()
print("shots saved")
```

Run: `"D:/amanu-win/venv/Scripts/python.exe" D:/amanu-win/test_panel_visual.py`
Expected: two PNGs; read them back and eyeball: collapsed dark bar; green stripe when recording.

- [ ] **Step 2: Rebuild the exe and restart the app**

```bash
python -c "import subprocess; subprocess.run(['taskkill','/F','/IM','amanu.exe'],capture_output=True)"
cd C:/Users/Mikhail/amanu-win
"D:/amanu-win/venv/Scripts/python.exe" scripts/build_exe.py --venv-python "D:/amanu-win/venv/Scripts/python.exe" --out "D:/amanu-win"
```
Then start `D:\amanu-win\amanu\amanu.exe` detached.

- [ ] **Step 3: Manual check with the user**

Collapsed bar visible, draggable; hover expands to three buttons; record button starts/stops; folder opens D:\Recordings; quit exits; the bar turns green while recording; Ctrl+V still lands in the target app (panel never takes focus).

- [ ] **Step 4: Commit and push**

```bash
git add -A
git commit -m "Ship the floating panel: screenshots verified both states, no-focus behavior preserved"
git push
```

---

## Self-Review Notes

- Spec coverage: F9 (instant state visibility) → Tasks 1–3 + stripe; N2 (responsiveness) → queue-pushed state, no work on UI thread; no-focus constraint → `_make_noactivate` (Task 2) + manual check (Task 4 step 3).
- Placeholder scan: no TBDs; every code step has content.
- Type consistency: `PanelController(stripe_for/status_text_for/dispatch/remember_pos/initial_pos)` used identically in Tasks 1–3; `FloatingPanel(start/set_state/close/alive)` consumed in Task 3 exactly as produced in Task 2.
