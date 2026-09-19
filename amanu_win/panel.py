"""Floating overlay panel: SuperWhisper-style collapsed bar that expands
on hover. Pure logic lives in PanelController so it is testable headless;
FloatingPanel is the thin tkinter view running on its own thread."""
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
        self._handlers = {"record": on_record, "folder": on_open_folder,
                          "quit": on_quit}
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


import queue
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
    """Tkinter must live on the main thread: created on a worker thread,
    Tcl crashes the process at teardown (Tcl_AsyncDelete: async handler
    deleted by the wrong thread). run_forever() blocks the caller; the tray
    meanwhile runs detached via pystray's own thread. set_state/close stay
    thread-safe through the queue."""

    def __init__(self, controller: PanelController, config_panel: dict):
        self.ctl = controller
        self._cfg = config_panel
        self._q: queue.Queue = queue.Queue()
        self.alive = False

    def run_forever(self) -> None:
        self._run()

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
        self._body = body

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
            self._body.pack(fill="both", expand=True)
            self._root.geometry(f"{PANEL_W}x{PANEL_H}+{x}+{y - PANEL_H + BAR_H}")
        else:
            self._body.pack_forget()
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
