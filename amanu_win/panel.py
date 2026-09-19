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
