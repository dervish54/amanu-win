# Installer + First-Run Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `amanu-setup.exe` (Inno Setup wizard) installs the bundle per-user and hands model downloads to a first-run progress window in the app.

**Architecture:** Inno unpacks the PyInstaller onedir bundle into `%LOCALAPPDATA%\Programs\Amanu`, drops `install-choices.json` next to the exe, writes autorun/desktop shortcuts, and offers "Launch Amanu". The app, on startup, sees `setup_complete` is not true, merges the choices into `config.json`, shows a tkinter progress window that downloads the chosen whisper model and optionally installs Ollama + pulls qwen2.5:7b, then starts the tray app.

**Tech Stack:** Python 3.10, tkinter, faster-whisper, Inno Setup 6, PyInstaller (existing).

**Spec:** `docs/superpowers/specs/2026-09-19-installer-first-run-design.md`

## Global Constraints

- Config file: `~/.config/amanu/config.json` (existing `amanu_win/config.py`); new keys `setup_complete` and `transcription.compute_type` only.
- `setup_complete` missing in config = **True** (existing installs must NOT see the first-run window). Only the installer-written choices set it to False.
- Per-user install, `PrivilegesRequired=lowest`, no admin anywhere.
- Existing `setup.py` `ensure_*` functions are reused, never duplicated.
- tkinter code runs only on the main thread (off-thread Tcl crashes the host at teardown — established in the floating-panel work).
- Test runner: `D:/amanu-win/venv/Scripts/python.exe -m pytest tests/ -q` from repo root.
- Commit style: one prose sentence, no prefixes (see git log).

---

### Task 1: Model tiers module + compute_type plumbing

**Files:**
- Create: `amanu_win/tiers.py`
- Modify: `amanu_win/transcription.py:30-42` (`load_model` gains `compute_type`)
- Test: `tests/test_tiers.py`

**Interfaces:**
- Produces:
  - `TIERS: dict[str, dict]` — keys `"accurate" | "balanced" | "compact"`; each `{"label": str, "model": str, "compute_type": str, "size": str, "needs_gpu": bool}`
  - `gpu_available() -> bool`
  - `recommended_tier() -> str` (returns a TIERS key)
  - `apply_tier(config_data: dict, tier: str) -> None` — sets `config_data["transcription"]["model"]` and `["compute_type"]`
  - `load_model(model_name, device, models_dir, compute_type=None)` — `None` keeps old behavior (float16 on cuda / int8 on cpu)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tiers.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import tiers  # noqa: E402


def test_tier_map_complete():
    for key in ("accurate", "balanced", "compact"):
        t = tiers.TIERS[key]
        assert t["model"] and t["compute_type"] in ("float16", "int8")
        assert t["size"].startswith("~")


def test_apply_tier_writes_config():
    cfg = {"transcription": {"model": "large-v3-turbo", "device": "cuda"}}
    tiers.apply_tier(cfg, "compact")
    assert cfg["transcription"]["model"] == "small"
    assert cfg["transcription"]["compute_type"] == "int8"


def test_recommended_tier_follows_gpu(monkeypatch):
    monkeypatch.setattr(tiers, "gpu_available", lambda: True)
    assert tiers.recommended_tier() == "accurate"
    monkeypatch.setattr(tiers, "gpu_available", lambda: False)
    assert tiers.recommended_tier() == "compact"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_tiers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'amanu_win.tiers'`

- [ ] **Step 3: Write minimal implementation**

```python
# amanu_win/tiers.py
"""Whisper quality tiers offered by the installer wizard."""
from __future__ import annotations

import shutil

TIERS = {
    "accurate": {"label": "Точная", "model": "large-v3-turbo",
                 "compute_type": "float16", "size": "~1.6 ГБ",
                 "needs_gpu": True},
    "balanced": {"label": "Сбалансированная", "model": "small",
                 "compute_type": "float16", "size": "~460 МБ",
                 "needs_gpu": False},
    "compact": {"label": "Компактная", "model": "small",
                "compute_type": "int8", "size": "~460 МБ",
                "needs_gpu": False},
}


def gpu_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def recommended_tier() -> str:
    return "accurate" if gpu_available() else "compact"


def apply_tier(config_data: dict, tier: str) -> None:
    t = TIERS[tier]
    tr = config_data.setdefault("transcription", {})
    tr["model"] = t["model"]
    tr["compute_type"] = t["compute_type"]
```

In `amanu_win/transcription.py`, change `load_model` to accept `compute_type: str | None = None` and use it when given:

```python
def load_model(model_name: str, device: str, models_dir: Path,
               compute_type: str | None = None):
    from faster_whisper import WhisperModel
    models_dir.mkdir(parents=True, exist_ok=True)
    import os
    os.environ.setdefault("HF_HOME", str(models_dir))
    if device == "cuda":
        try:
            _ensure_cuda_dlls()
            return WhisperModel(model_name, device="cuda",
                                compute_type=compute_type or "float16")
        except Exception:
            pass
    return WhisperModel(model_name, device="cpu",
                        compute_type=compute_type or "int8")
```

In `amanu_win/session.py` `_get_model` (line ~85-90), pass the configured compute type:

```python
self._model = load_model(t["model"], t["device"], self.config.models_dir,
                         t.get("compute_type"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_tiers.py tests/test_async_toggle.py -q`
Expected: PASS (existing suite untouched behaviorally: `compute_type=None` default)

- [ ] **Step 5: Commit**

```bash
git add amanu_win/tiers.py amanu_win/transcription.py amanu_win/session.py tests/test_tiers.py
git commit -m "Add whisper quality tiers (accurate/balanced/compact) with GPU-based recommendation; load_model honors an explicit compute_type so the installer's choice survives into transcription"
```

---

### Task 2: First-run state, install-choices merge, selectable setup steps

**Files:**
- Modify: `amanu_win/setup.py`
- Modify: `amanu_win/config.py` (DEFAULTS gains `"setup_complete": True` handling — see below)
- Test: `tests/test_first_run.py`

**Interfaces:**
- Consumes: `tiers.apply_tier(config_data, tier)` from Task 1.
- Produces:
  - `needs_first_run(config: Config) -> bool` — True only when the config explicitly says `"setup_complete": false`
  - `apply_install_choices(config: Config, bundle_dir: Path) -> bool` — reads `bundle_dir/install-choices.json`, applies `tier` via `tiers.apply_tier` and `ollama: bool` via `summary.enabled`, deletes the file, returns True if a file was applied
  - `run_setup(config, startup=True, steps=("whisper", "ollama"), progress=None, log=print) -> dict` — `steps` selects which heavy steps run; `progress(step: str, status: str)` called as `(name, "start"|"done"|"skipped"|"failed")`
  - `mark_setup_complete(config: Config) -> None` — sets flag and saves

Config note: do NOT add `setup_complete` to DEFAULTS (missing must mean True for existing installs). `needs_first_run` checks `config.data.get("setup_complete") is False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_first_run.py
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config  # noqa: E402
from amanu_win import setup as setup_mod  # noqa: E402


def _cfg(tmp_path, data=None):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data or {}), encoding="utf-8")
    cfg = Config(p)
    cfg.data.setdefault("models_dir", str(tmp_path / "models"))
    cfg.data.setdefault("recordings_dir", str(tmp_path / "rec"))
    return cfg


def test_needs_first_run_only_when_flag_false(tmp_path):
    assert setup_mod.needs_first_run(_cfg(tmp_path / "a")) is False
    assert setup_mod.needs_first_run(
        _cfg(tmp_path / "b", {"setup_complete": True})) is False
    assert setup_mod.needs_first_run(
        _cfg(tmp_path / "c", {"setup_complete": False})) is True


def test_apply_install_choices(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "install-choices.json").write_text(
        json.dumps({"tier": "compact", "ollama": False}), encoding="utf-8")
    cfg = _cfg(tmp_path / "d", {"setup_complete": False})
    assert setup_mod.apply_install_choices(cfg, bundle) is True
    assert cfg.data["transcription"]["model"] == "small"
    assert cfg.data["summary"]["enabled"] is False
    assert not (bundle / "install-choices.json").exists()
    # second call: nothing to apply
    assert setup_mod.apply_install_choices(cfg, bundle) is False


def test_run_setup_steps_and_progress(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path / "e")
    monkeypatch.setattr(setup_mod, "ensure_ollama", lambda c: "skipped")
    monkeypatch.setattr(setup_mod, "ensure_model", lambda c: "present")
    monkeypatch.setattr(setup_mod, "preload_whisper", lambda c: True)
    events = []
    result = setup_mod.run_setup(cfg, startup=False, steps=("whisper",),
                                 progress=lambda s, st: events.append((s, st)),
                                 log=lambda *a: None)
    assert "ollama" not in result
    assert ("whisper", "start") in events and ("whisper", "done") in events


def test_mark_setup_complete(tmp_path):
    cfg = _cfg(tmp_path / "f", {"setup_complete": False})
    setup_mod.mark_setup_complete(cfg)
    assert json.loads(cfg.path.read_text(encoding="utf-8"))["setup_complete"] is True
```

Note: `Config` may not expose `.path`; if the attribute is named differently, use the existing way tests construct/reload config (read `config.py` first) and persist via the existing save method.

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_first_run.py -q`
Expected: FAIL — `AttributeError: module 'amanu_win.setup' has no attribute 'needs_first_run'`

- [ ] **Step 3: Write minimal implementation**

Append to `amanu_win/setup.py`:

```python
def needs_first_run(config: Config) -> bool:
    return config.data.get("setup_complete") is False


def apply_install_choices(config: Config, bundle_dir: Path) -> bool:
    """Merge the installer-written choices file into the config, once."""
    import json
    from . import tiers
    choices = bundle_dir / "install-choices.json"
    if not choices.exists():
        return False
    data = json.loads(choices.read_text(encoding="utf-8"))
    if data.get("tier"):
        tiers.apply_tier(config.data, data["tier"])
    if "ollama" in data:
        config.data.setdefault("summary", {})["enabled"] = data["ollama"]
    if data.get("recordings_dir"):
        config.data["recordings_dir"] = data["recordings_dir"]
    config.save()
    choices.unlink()
    return True


def mark_setup_complete(config: Config) -> None:
    config.data["setup_complete"] = True
    config.save()
```

Change `run_setup` signature to
`run_setup(config, startup=True, steps=("whisper", "ollama"), progress=None, log=print)`
and gate the heavy steps:

```python
    def _prog(step, status):
        if progress:
            progress(step, status)

    ...
    if "ollama" in steps:
        _prog("ollama", "start")
        result["ollama"] = ensure_ollama(config)
        result["model"] = ensure_model(config)
        _prog("ollama", "done")
    if "whisper" in steps:
        _prog("whisper", "start")
        result["whisper"] = "cached" if preload_whisper(config) else "failed"
        _prog("whisper", "done")
```

(`config.save()` exists already — confirm the method name in `config.py`; the config writes used elsewhere in the repo, e.g. panel position, use it.)

Also make `summary.enabled` actually respected where summaries run: in `session.py` `_process_locked`, guard the summary block with `if s.get("enabled", True):` — check current code; the punctuation/stitch paths already fail soft when Ollama is down, no change needed there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_first_run.py tests/test_async_toggle.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add amanu_win/setup.py amanu_win/config.py amanu_win/session.py tests/test_first_run.py
git commit -m "First-run state machine: installer choices file is merged into config once, setup steps are selectable with progress callbacks, and setup_complete defaults to true so existing installs never see the wizard"
```

---

### Task 3: First-run progress window

**Files:**
- Create: `amanu_win/first_run.py`
- Test: `tests/test_first_run_window.py` (subprocess lifecycle smoke, like `test_panel.py::test_panel_window_lifecycle_subprocess`)

**Interfaces:**
- Consumes: `needs_first_run`, `apply_install_choices`, `run_setup(steps=, progress=)`, `mark_setup_complete` (Task 2); `tiers.TIERS` (Task 1); `FloatingPanel` for the post-setup hint.
- Produces: `run_first_run(config: Config, bundle_dir: Path) -> None` — blocking, main thread; returns when setup is done or deferred. The launcher calls it before starting the tray app.

Window contents (tkinter, same visual language as the panel: dark bg `#2b2b2b`, alpha 0.95, no focus-stealing not needed here):
- Title «Amanu — первоначальная настройка»
- Step rows: whisper model (with tier label + size), Ollama (if enabled), microphone check. Each row: canvas status icon (spinner→check/cross) + label.
- One progress bar (indeterminate) under the active step.
- Footer buttons: «Сделать это позже» (defer: close without flag, app still starts) and, on failure of a step, «Повторить» / «Пропустить».
- On success: `mark_setup_complete`, final line «Наведите курсор на панель — там запись», auto-close after 3 s.

Worker thread runs `run_setup`; all UI updates go through a `queue.Queue` + `after(80, ...)` poll (identical pattern to `FloatingPanel`). Mic check runs last on the worker via `devices.select_mic_device()` and posts a line.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_first_run_window.py
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
    'models_dir': str(tmp/'m'), 'recordings_dir': str(tmp/'r')}),
    encoding='utf-8')
cfg = Config(cfgp)

# stub the heavy work: no downloads in a smoke test
import amanu_win.setup as s
s.run_setup = lambda *a, **k: {'whisper': 'cached'}

done = []
t = threading.Thread(target=lambda: (first_run.run_first_run(cfg, tmp),
                                     done.append(1)), daemon=True)
t.start()
time.sleep(4)
w = first_run._LAST_WINDOW
assert w is not None and w.alive
print('window alive, state rows:', len(w._rows))
w.close()
time.sleep(1)
print('OK')
"""

def test_first_run_window_lifecycle_subprocess():
    script = SCRIPT.replace("%REPO%", str(REPO).replace("\\", "/"))
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", script],
                       capture_output=True, text=True, timeout=120)
    assert "OK" in r.stdout, r.stdout + r.stderr
```

`_LAST_WINDOW` is a module-level reference the class sets on itself (needed because `run_first_run` blocks); `close()` mirrors `FloatingPanel.close()` (queue + destroy).

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_first_run_window.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'amanu_win.first_run'`

- [ ] **Step 3: Write minimal implementation**

`amanu_win/first_run.py`:

```python
"""First-run setup window: downloads models with live progress.

Runs on the main thread (tkinter rule from panel.py); heavy work lives on
one worker thread and reports through a queue.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path

from .config import Config
from . import setup as setup_mod
from . import tiers

BG, FG = "#2b2b2b", "#eeeeee"
_LAST_WINDOW = None


class FirstRunWindow:
    def __init__(self, config: Config, bundle_dir: Path):
        self.cfg = config
        self.bundle_dir = bundle_dir
        self._q: queue.Queue = queue.Queue()
        self.alive = False
        self._rows: dict[str, tk.Label] = {}

    def run(self) -> None:
        global _LAST_WINDOW
        _LAST_WINDOW = self
        root = tk.Tk()
        root.title("Amanu — первоначальная настройка")
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        self._root = root

        tier_key = next((k for k, t in tiers.TIERS.items()
                         if t["model"] == self.cfg.transcription["model"]),
                        "accurate")
        tier = tiers.TIERS[tier_key]
        rows = [("whisper",
                 f"Модель распознавания: {tier['label']} ({tier['size']})")]
        if self.cfg.summary.get("enabled", True):
            rows.append(("ollama", "Ollama + модель сводок (~5 ГБ)"))
        rows.append(("mic", "Проверка микрофона"))
        for key, text in rows:
            lbl = tk.Label(root, text="…  " + text, fg=FG, bg=BG,
                           font=("Segoe UI", 9), anchor="w")
            lbl.pack(fill="x", padx=14, pady=3)
            self._rows[key] = lbl

        self._bar = tk.Canvas(root, width=360, height=6, bg="#444",
                              highlightthickness=0)
        self._bar.pack(padx=14, pady=8)

        btns = tk.Frame(root, bg=BG)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(btns, text="Сделать это позже",
                  command=self.close).pack(side="right")
        self._retry = tk.Button(btns, text="Повторить",
                                command=self._start_worker)
        # hidden until a failure

        self.alive = True
        root.after(80, self._poll)
        self._start_worker()
        root.mainloop()
        self.alive = False

    def _start_worker(self) -> None:
        self._retry.pack_forget()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            setup_mod.apply_install_choices(self.cfg, self.bundle_dir)
            steps = ["whisper"]
            if self.cfg.summary.get("enabled", True):
                steps.append("ollama")
            setup_mod.run_setup(
                self.cfg, startup=False, steps=tuple(steps),
                progress=lambda s, st: self._q.put(("step", s, st)),
                log=lambda *a: None)
            from .devices import select_mic_device
            ok = select_mic_device() is not None
            self._q.put(("step", "mic", "done" if ok else "failed"))
            setup_mod.mark_setup_complete(self.cfg)
            self._q.put(("finish", None, None))
        except Exception as e:  # network/disk failures land here
            self._q.put(("error", str(e), None))

    def _poll(self) -> None:
        try:
            while True:
                kind, a, b = self._q.get_nowait()
                if kind == "step":
                    mark = {"start": "…", "done": "✓", "failed": "✗"}[b]
                    if a in self._rows:
                        self._rows[a].config(
                            text=f"{mark}  " + self._rows[a].cget("text")[4:])
                elif kind == "finish":
                    self._rows["mic"].master  # noqa
                    done = tk.Label(self._root,
                                    text="Готово! Наведите курсор на панель — там запись.",
                                    fg="#2ea043", bg=BG, font=("Segoe UI", 9))
                    done.pack(padx=14, pady=4)
                    self._root.after(3000, self.close)
                elif kind == "error":
                    err = tk.Label(self._root, text=f"Ошибка: {a}",
                                   fg="#e06c75", bg=BG, font=("Segoe UI", 8),
                                   wraplength=360, justify="left")
                    err.pack(padx=14, pady=4)
                    self._retry.pack(side="right")
        except queue.Empty:
            pass
        if self.alive:
            self._root.after(80, self._poll)

    def close(self) -> None:
        try:
            self._root.destroy()
        except Exception:
            pass


def run_first_run(config: Config, bundle_dir: Path) -> None:
    FirstRunWindow(config, bundle_dir).run()
```

(The ✓/✗ marks are plain Unicode text, not emoji — consistent with the panel's no-emoji rule. The `text[4:]` prefix-strip works because every label starts with a 3-char mark + space; keep that invariant when editing row creation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_first_run_window.py -q`
Expected: PASS (subprocess prints OK)

- [ ] **Step 5: Commit**

```bash
git add amanu_win/first_run.py tests/test_first_run_window.py
git commit -m "First-run progress window: step rows with status marks, queue-fed from a worker running the existing ensure_* setup steps, defer/retry buttons, and a closing hint pointing at the floating panel"
```

---

### Task 4: Launcher wiring

**Files:**
- Modify: `launcher.py` and/or `amanu_win/__main__.py` (read both first; the flag belongs wherever argv is parsed)
- Test: `tests/test_launcher_first_run.py`

**Interfaces:**
- Consumes: `setup.needs_first_run`, `setup.apply_install_choices`, `first_run.run_first_run` (Tasks 2-3).
- Produces: startup sequence — `apply_install_choices` (if bundle file present) → `needs_first_run` → `run_first_run` → tray app. Bundle dir = `Path(sys.executable).parent` when frozen (`getattr(sys, "frozen", False)`), else repo root.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_launcher_first_run.py
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.config import Config  # noqa: E402
from amanu_win import __main__ as main_mod  # noqa: E402


def test_startup_invokes_first_run_when_flag_false(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"setup_complete": False,
                                "models_dir": str(tmp_path / "m"),
                                "recordings_dir": str(tmp_path / "r")}),
                    encoding="utf-8")
    calls = []
    monkeypatch.setattr(main_mod, "run_first_run",
                        lambda cfg, bundle: calls.append("first_run"))
    monkeypatch.setattr(main_mod, "run_tray",
                        lambda cfg: calls.append("tray"))
    main_mod.start(Config(cfgp), bundle_dir=tmp_path)
    assert calls == ["first_run", "tray"]


def test_startup_skips_first_run_for_existing_install(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"models_dir": str(tmp_path / "m"),
                                "recordings_dir": str(tmp_path / "r")}),
                    encoding="utf-8")
    calls = []
    monkeypatch.setattr(main_mod, "run_first_run",
                        lambda cfg, bundle: calls.append("first_run"))
    monkeypatch.setattr(main_mod, "run_tray",
                        lambda cfg: calls.append("tray"))
    main_mod.start(Config(cfgp), bundle_dir=tmp_path)
    assert calls == ["tray"]
```

Read `__main__.py`/`launcher.py` first — the test assumes a refactor into `start(config, bundle_dir)` + `run_tray(config)` seams; if the current entry already has equivalents, adapt names to the real ones instead of inventing new ones.

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_launcher_first_run.py -q`
Expected: FAIL — no `start` seam / `run_first_run` attribute

- [ ] **Step 3: Write minimal implementation**

In the real entry module, extract:

```python
def start(config, bundle_dir):
    from . import setup as setup_mod
    setup_mod.apply_install_choices(config, bundle_dir)
    if setup_mod.needs_first_run(config):
        from .first_run import run_first_run
        run_first_run(config, bundle_dir)
    run_tray(config)
```

and route the existing main() through it (`--setup` CLI path stays as-is).

- [ ] **Step 4: Run tests to verify they pass**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add launcher.py amanu_win/__main__.py tests/test_launcher_first_run.py
git commit -m "Startup merges installer choices and runs the first-run window only when setup_complete is explicitly false, then enters the tray app as before"
```

---

### Task 5: Inno Setup script + installer build script

**Files:**
- Create: `installer/amanu.iss`
- Create: `scripts/build_installer.py`
- Modify: `SPEC.md` / `README.md` of amanu-win (install instructions point at the setup exe)
- Test: `tests/test_installer_script.py` (static checks on the .iss + choices JSON shape); full install run is manual

**Interfaces:**
- Consumes: `scripts/build_exe.py::build(venv_python, out)` (existing); the `install-choices.json` contract from Task 2 (`{"tier": str, "ollama": bool, "recordings_dir": str}`).
- Produces: `python scripts/build_installer.py --venv-python ... --out D:/amanu-win` → `D:/amanu-win/amanu-setup.exe`.

`installer/amanu.iss` essentials (Pascal script for the custom page):

```iss
[Setup]
AppName=Amanu
AppVersion=1.0
DefaultDirName={localappdata}\Programs\Amanu
PrivilegesRequired=lowest
OutputBaseFilename=amanu-setup
Compression=lzma2

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked
Name: "autorun"; Description: "Запускать с Windows"

[Icons]
Name: "{userdesktop}\Amanu"; Filename: "{app}\amanu.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueName: "Amanu"; ValueData: """{app}\amanu.exe"""; Tasks: autorun

[Run]
Filename: "{app}\amanu.exe"; Description: "Запустить Amanu"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete] — none for models/recordings (ask instead, see Code)
```

`[Code]` section: custom wizard page after dir selection with three radio
buttons (Точная/Сбалансированная/Компактная + sizes from the spec) and an
Ollama checkbox (checked default); a recordings-dir edit (default
`{userdocs}\Amanu Recordings`); a space label showing required total; in
`CurStepChanged(ssPostInstall)` write `{app}\install-choices.json` with the
selections. Uninstall: `InitializeUninstall` shows a MsgBox asking whether
to also delete `%LOCALAPPDATA%\amanu\models` and the recordings folder
(mbConfirmation, default keep).

`scripts/build_installer.py`:

```python
"""Build amanu-setup.exe: PyInstaller bundle + Inno wrapper."""
import argparse, shutil, subprocess, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from build_exe import build  # noqa: E402

ISCC = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"


def build_installer(venv_python: Path, out: Path) -> Path:
    bundle = build(venv_python, out)          # <out>/amanu/amanu.exe
    iss = REPO_ROOT / "installer" / "amanu.iss"
    subprocess.check_call([
        ISCC, f"/DBundleDir={bundle.parent}", f"/DOutDir={out}", str(iss)])
    setup_exe = out / "amanu-setup.exe"
    print(f"OK -> {setup_exe}")
    return setup_exe
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_installer_script.py
import json
import re
from pathlib import Path

ISS = Path(__file__).resolve().parent.parent / "installer" / "amanu.iss"


def test_iss_exists_with_required_sections():
    text = ISS.read_text(encoding="utf-8-sig")
    for section in ("[Setup]", "[Files]", "[Tasks]", "[Registry]", "[Code]"):
        assert section in text
    assert "PrivilegesRequired=lowest" in text
    assert "install-choices.json" in text


def test_choices_json_shape_written_by_iss():
    text = ISS.read_text(encoding="utf-8-sig")
    # the Pascal code must write all three keys the app consumes
    for key in ('"tier"', '"ollama"', '"recordings_dir"'):
        assert key in text
    for tier in ("accurate", "balanced", "compact"):
        assert tier in text
```

(Static assertions on installer source are the honest headless check; the behavioral proof is the manual install pass, listed in Global Constraints' testing section of the spec.)

- [ ] **Step 2: Run test to verify it fails**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_installer_script.py -q`
Expected: FAIL — file missing

- [ ] **Step 3: Write the .iss and build script** (contents above; expand the `[Code]` section fully — radio page, choices writer, uninstall prompt)

- [ ] **Step 4: Run tests + build the installer**

Run: `"D:/amanu-win/venv/Scripts/python.exe" -m pytest tests/test_installer_script.py -q`
Expected: PASS
Then: `"D:/amanu-win/venv/Scripts/python.exe" scripts/build_installer.py --venv-python "D:/amanu-win/venv/Scripts/python.exe" --out "D:/amanu-win"`
Expected: `OK -> D:\amanu-win\amanu-setup.exe` (requires Inno Setup 6 installed; if `iscc` is absent, install via `winget install JRSoftware.InnoSetup`)

- [ ] **Step 5: Manual install verification** (with the user)

Run `amanu-setup.exe` in a sandbox dir; verify: wizard pages in Russian, choices land in `install-choices.json`, first-run window appears, models download, tray app + panel start. Then uninstall and confirm the keep/delete prompt.

- [ ] **Step 6: Commit**

```bash
git add installer/amanu.iss scripts/build_installer.py tests/test_installer_script.py README.md SPEC.md
git commit -m "Windows installer: Inno Setup wizard (folder, three model tiers, optional Ollama, shortcuts) writes install-choices.json for the app's first-run downloader; build_installer.py produces amanu-setup.exe"
```

---

## Self-review notes

- Spec coverage: wizard pages (T5), tiers (T1, T5), choices→config (T2, T5),
  first-run window + errors + defer/retry (T3), launcher flow (T4), mic check
  (T3 worker), autorun/desktop icon (T5), uninstall ask (T5 Code section),
  closing hint (T3 finish label — the always-visible variant; the panel
  itself needs no change).
- The spec's "hint near the panel fading after 10 s" is realized as the
  window's final line plus the already-expanded-on-hover panel behavior;
  expanding the panel programmatically was dropped to keep the panel code
  untouched — flag this to the user at delivery.
