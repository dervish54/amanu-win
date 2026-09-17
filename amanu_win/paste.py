"""Auto-paste the user's own words into the focused window.

SuperWhisper-style: once transcription finishes, the recognized text of
the mic channel goes to the clipboard and Ctrl+V is sent to whatever
window has focus. Keystroke emulation cannot type Cyrillic reliably, so
the text travels as CF_UNICODETEXT clipboard data — exact for any
language, in any application.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import keyboard

GMEM_MOVEABLE = 0x0002
CF_UNICODETEXT = 13

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit handles are truncated to c_int unless signatures are declared —
# a high GlobalAlloc address then fails GlobalLock in the field while
# unit tests pass by address-space luck
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def own_speech_text(segments: list[dict], mic_label: str) -> str:
    """The user's own utterances joined for insertion (far end excluded)."""
    return "\n".join(
        s["text"] for s in segments if s.get("speaker") == mic_label and s.get("text")
    )


def set_clipboard_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    user32.OpenClipboard(None)
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            raise RuntimeError("GlobalLock failed")
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise RuntimeError("SetClipboardData failed")
        h = None  # ownership transferred to the clipboard
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str | None:
    """Current clipboard text, or None if it doesn't hold text."""
    user32.OpenClipboard(None)
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        text = ctypes.wstring_at(p) if p else None
        kernel32.GlobalUnlock(h)
        return text
    finally:
        user32.CloseClipboard()


def paste_text(text: str) -> None:
    """Clipboard + Ctrl+V into the focused window."""
    set_clipboard_text(text)
    keyboard.send("ctrl+v")
