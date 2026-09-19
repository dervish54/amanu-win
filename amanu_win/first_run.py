"""First-run setup window: downloads models with live progress.

Runs on the main thread (tkinter rule established in panel.py: off-thread
Tcl crashes the host process at teardown); heavy work lives on one worker
thread and reports through a queue.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path

from . import setup as setup_mod
from . import tiers
from .config import Config

BG, FG = "#2b2b2b", "#eeeeee"
_LAST_WINDOW: "FirstRunWindow | None" = None


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
            from .recorder import select_mic_device
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
                if kind == "close":
                    self._root.destroy()
                    return
                if kind == "step":
                    mark = {"start": "…", "done": "✓", "failed": "✗"}[b]
                    if a in self._rows:
                        lbl = self._rows[a]
                        lbl.config(text=f"{mark}  " + lbl.cget("text")[4:])
                elif kind == "finish":
                    tk.Label(self._root,
                             text="Готово! Наведите курсор на панель — там запись.",
                             fg="#2ea043", bg=BG,
                             font=("Segoe UI", 9)).pack(padx=14, pady=4)
                    self._root.after(3000, self.close)
                elif kind == "error":
                    tk.Label(self._root, text=f"Ошибка: {a}",
                             fg="#e06c75", bg=BG, font=("Segoe UI", 8),
                             wraplength=360, justify="left").pack(padx=14,
                                                                  pady=4)
                    self._retry.pack(side="right")
        except queue.Empty:
            pass
        if self.alive:
            self._root.after(80, self._poll)

    def close(self) -> None:
        # destroy must happen in the thread that owns Tcl: a cross-thread
        # destroy while mainloop runs can deadlock the Tk lock
        self._q.put(("close", None, None))


def run_first_run(config: Config, bundle_dir: Path) -> None:
    FirstRunWindow(config, bundle_dir).run()
