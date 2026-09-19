"""Floating overlay panel: SuperWhisper-style collapsed bar that expands
on hover. Pure logic lives in PanelController so it is testable headless;
FloatingPanel is the thin tkinter view.

Tkinter must live on the main thread: created on a worker thread, Tcl
crashes the host process at teardown (Tcl_AsyncDelete: async handler
deleted by the wrong thread). run_forever() blocks the caller; the tray
loop meanwhile runs detached via pystray's own thread.
"""
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


def bounds_contain(geom, pointer) -> bool:
    """True if the pointer is inside window bounds. Widget-level <Leave>
    propagates to the toplevel via bindtags, so hover state must be decided
    geometrically — otherwise moving from the bar into the menu body
    collapses the menu."""
    x, y, w, h = geom
    px, py = pointer
    return x <= px < x + w and y <= py < y + h


def expand_frames(from_wh, to_wh, steps: int = 6) -> list[tuple[int, int]]:
    """Two-phase geometry animation. Expanding: horizontal first, then
    vertical; collapsing: the exact reverse."""
    (w0, h0), (w1, h1) = from_wh, to_wh
    expanding = (w1, h1) > (w0, h0)
    phases = [((w0, h0), (w1, h0)), ((w1, h0), (w1, h1))] if expanding \
        else [((w0, h0), (w0, h1)), ((w0, h1), (w1, h1))]
    frames = []
    for (wa, ha), (wb, hb) in phases:
        for i in range(1, steps + 1):
            w = round(wa + (wb - wa) * i / steps)
            h = round(ha + (hb - ha) * i / steps)
            frames.append((w, h))
    return frames


import queue
import tkinter as tk

BAR_W, BAR_H = 64, 16
PANEL_W, PANEL_H = 232, 148
BG = "#2b2b2b"
FG = "#eeeeee"
ALPHA = 0.75
ANIM_MS = 16

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


def _draw_icon(canvas: tk.Canvas, kind: str, color: str) -> None:
    """Vector icons, drawn — emoji fonts render inconsistently and blurry."""
    canvas.delete("all")
    if kind == "record":
        canvas.create_oval(4, 4, 18, 18, outline=color, width=2)
        canvas.create_oval(8, 8, 14, 14, fill=color, outline="")
    elif kind == "folder":
        canvas.create_polygon(3, 6, 9, 6, 11, 9, 19, 9, 19, 18, 3, 18,
                              outline=color, fill="", width=2,
                              joinstyle="round")
    elif kind == "quit":
        canvas.create_line(5, 5, 17, 17, fill=color, width=2, capstyle="round")
        canvas.create_line(17, 5, 5, 17, fill=color, width=2, capstyle="round")


class FloatingPanel:
    def __init__(self, controller: PanelController, config_panel: dict):
        self.ctl = controller
        self._cfg = config_panel
        self._q: queue.Queue = queue.Queue()
        self.alive = False
        self._expanded = False
        self._animating = False
        self._collapse_job = None
        self._drag = None
        self._icons: dict[str, tk.Canvas] = {}

    def run_forever(self) -> None:
        self._run()

    def set_state(self, state: str) -> None:
        self._q.put(("state", state))

    def close(self) -> None:
        self._q.put(("close", None))

    # -- window ------------------------------------------------------------------
    def _run(self) -> None:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", ALPHA)
        pos = self.ctl.initial_pos()
        x, y = pos if pos else (root.winfo_screenwidth() - BAR_W - 40, 60)
        self._root = root
        self._state = "idle"

        bar = tk.Frame(root, bg=BG, width=BAR_W, height=BAR_H)
        bar.pack(fill="both", expand=True)
        bar.pack_propagate(False)
        stripe = tk.Frame(bar, height=4, bg=STRIPE["idle"])
        stripe.pack(side="bottom", fill="x")
        self._stripe = stripe

        body = tk.Frame(root, bg=BG)
        self._status = tk.Label(body, text="", fg="#bbb", bg=BG,
                                font=("Segoe UI", 8))
        self._status.pack(fill="x", padx=8, pady=(4, 2), anchor="w")
        for cmd, label in (("record", "Запись"), ("folder", "Папка"),
                           ("quit", "Выйти из приложения")):
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", padx=4, pady=1)
            cv = tk.Canvas(row, width=22, height=22, bg=BG,
                           highlightthickness=0)
            cv.pack(side="left")
            _draw_icon(cv, cmd, FG)
            self._icons[cmd] = cv
            btn = tk.Label(row, text=label, fg=FG, bg=BG,
                           font=("Segoe UI", 9), anchor="w")
            btn.pack(side="left", fill="x", expand=True, padx=4)
            for w in (row, cv, btn):
                w.bind("<Button-1>", lambda e, c=cmd: self.ctl.dispatch(c))
                w.bind("<Enter>", lambda e, r=row: r.config(bg="#3d3d3d"))
                w.bind("<Leave>", lambda e, r=row: r.config(bg=BG))
        self._body = body

        root.geometry(f"{BAR_W}x{BAR_H}+{x}+{y}")
        root.bind("<Enter>", lambda e: self._hover(True))
        root.bind("<Leave>", lambda e: self._hover(False))
        for w in (bar, stripe):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        root.update_idletasks()
        _make_noactivate(root)
        self.alive = True
        root.after(80, self._poll)
        root.mainloop()
        self.alive = False

    # -- state ---------------------------------------------------------------------
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

    # -- hover / animation -----------------------------------------------------------
    def _hover(self, inside: bool) -> None:
        if inside:
            if self._collapse_job is not None:
                self._root.after_cancel(self._collapse_job)
                self._collapse_job = None
            if not self._expanded and not self._animating:
                self._animate_to((PANEL_W, PANEL_H), expand=True)
        else:
            # collapse only if the pointer truly left the window: widget-level
            # <Leave> events propagate here when the cursor moves from the bar
            # into the menu body, and must not collapse anything
            self._collapse_job = self._root.after(150, self._maybe_collapse)

    def _maybe_collapse(self) -> None:
        self._collapse_job = None
        root = self._root
        geom = (root.winfo_x(), root.winfo_y(),
                root.winfo_width(), root.winfo_height())
        if not bounds_contain(geom, root.winfo_pointerxy()):
            self._animate_to((BAR_W, BAR_H), expand=False)

    def _animate_to(self, target, expand: bool) -> None:
        self._expanded = expand
        self._animating = True
        cur = (self._root.winfo_width(), self._root.winfo_height())
        frames = expand_frames(cur, target, steps=6)
        x, y = self._root.winfo_x(), self._root.winfo_y()

        def step(i=0):
            if i >= len(frames):
                self._animating = False
                if expand:
                    self._body.pack(fill="both", expand=True)
                else:
                    self._body.pack_forget()
                return
            w, h = frames[i]
            self._root.geometry(f"{w}x{h}+{x}+{y}")
            if expand and i == len(frames) // 2:
                # body appears once the window is wide enough to hold it
                self._body.pack(fill="both", expand=True)
            self._root.after(ANIM_MS, lambda: step(i + 1))

        if not expand:
            self._body.pack_forget()
        step()

    # -- drag ---------------------------------------------------------------------
    def _drag_start(self, e):
        self._drag = (e.x_root - self._root.winfo_x(), e.y_root - self._root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self._root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, e):
        if self._drag:
            self.ctl.remember_pos(self._root.winfo_x(), self._root.winfo_y())
            self._drag = None
