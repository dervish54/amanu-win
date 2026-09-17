"""Session lifecycle: one folder per meeting; the folder is the database.

    <recordings_dir>/<YYYY.MM.DD-HHMM>/
    ├── audio.wav        # mic left, system right; removed after transcript unless keep_audio
    ├── transcript.md
    ├── transcript.json  # timed segments + per-channel engine provenance
    ├── summary.md
    └── meta.json        # timing, devices, trigger, processing state
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from .recorder import StereoRecorder
from . import paste
from . import punctuate
from .transcription import load_model, transcribe_channels, write_transcript_json, write_transcript_md
from .summary import ollama_available, summarize


def new_session_dir(recordings_dir: Path) -> Path:
    name = datetime.now().strftime("%Y.%m.%d-%H%M")
    d = recordings_dir / name
    n = 1
    while d.exists():
        d = recordings_dir / f"{name}-{n}"
        n += 1
    d.mkdir(parents=True)
    return d


class SessionManager:
    def __init__(self, config, log=lambda msg: None, on_state_changed=None):
        self.config = config
        self.log = log
        self.on_state_changed = on_state_changed
        self.recorder: StereoRecorder | None = None
        self.session_dir: Path | None = None
        self.processing = threading.Event()
        self.stage = ""
        self._model = None

    def _notify(self) -> None:
        if self.on_state_changed is not None:
            try:
                self.on_state_changed()
            except Exception:
                pass

    # -- recording -------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self.recorder is not None

    def start_recording(self) -> Path:
        if self.recorder:
            raise RuntimeError("already recording")
        self.session_dir = new_session_dir(self.config.recordings_dir)
        self.recorder = StereoRecorder(self.session_dir / "audio.wav")
        self.recorder.start()
        self.log(f"recording → {self.session_dir}")
        return self.session_dir

    def stop_recording(self) -> Path:
        if not self.recorder:
            raise RuntimeError("not recording")
        info = self.recorder.stop()
        d = self.session_dir
        self.recorder = None
        meta = {
            "started_at": datetime.fromtimestamp(info.started_at).isoformat(timespec="seconds"),
            "stopped_at": datetime.fromtimestamp(info.stopped_at).isoformat(timespec="seconds"),
            "duration_s": round(info.stopped_at - info.started_at, 1),
            "devices": {
                "mic": info.mic_device,
                "system": info.system_device,
                "mic_rate": info.mic_rate,
                "system_rate": info.system_rate,
            },
            "trigger": "hotkey",
            "processing": {"transcript": "pending", "summary": "pending"},
        }
        if info.error:
            meta["capture_error"] = info.error
        (d / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        threading.Thread(target=self.process, args=(d,), daemon=True).start()
        return d

    # -- post-processing ----------------------------------------------------------
    def process(self, session_dir: Path) -> None:
        self.processing.set()
        self._notify()
        try:
            self._process_locked(session_dir)
        finally:
            self.stage = ""
            self.processing.clear()
            self._notify()

    def _punctuation_enabled(self) -> bool:
        return bool(self.config.data.get("punctuation", {}).get("enabled", True))

    def _maybe_punctuate_transcript(self, d: Path) -> None:
        """Rewrite transcript.md with restored punctuation/casing (the
        summary below then reads the punctuated version)."""
        if not self._punctuation_enabled():
            return
        md = d / "transcript.md"
        s = self.config.summary
        restored = punctuate.restore_punctuation(
            md.read_text(encoding="utf-8"),
            s["ollama_url"], s["ollama_model"], speaker_markers=True)
        if restored != md.read_text(encoding="utf-8"):
            md.write_text(restored + "\n", encoding="utf-8")
            self.log("transcript punctuation restored")

    def _maybe_paste_own_speech(self, segments: list[dict], tconf: dict) -> None:
        """SuperWhisper-style: the user's own words go straight into the
        focused window (clipboard + Ctrl+V) — no extra keypresses."""
        if not self.config.data.get("paste", {}).get("enabled", True):
            return
        text = paste.own_speech_text(segments, tconf.get("mic_speaker", "Микрофон"))
        if not text.strip():
            return
        if self._punctuation_enabled():
            s = self.config.summary
            text = punctuate.restore_punctuation(text, s["ollama_url"], s["ollama_model"])
        try:
            paste.paste_text(text)
            self.log(f"pasted {len(text)} chars of own speech")
        except Exception as e:
            self.log(f"paste failed: {e}")

    def _process_locked(self, d: Path) -> None:
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        wav = d / "audio.wav"

        try:
            if self.config.transcription.get("enabled", True):
                self.stage = "transcribing"
                self._notify()
                t = self.config.transcription
                if self._model is None:
                    self.log(f"loading model {t['model']} ({t['device']})...")
                    self._model = load_model(t["model"], t["device"], self.config.models_dir)
                segments, prov = transcribe_channels(
                    wav, self._model,
                    language=t.get("language", "auto"),
                    mic_label=t.get("mic_speaker", "Микрофон"),
                    system_label=t.get("system_speaker", "Собеседник"),
                    log=self.log,
                )
                write_transcript_md(segments, d / "transcript.md")
                write_transcript_json(segments, prov, d / "transcript.json")
                meta["processing"]["transcript"] = "done"
                self._maybe_punctuate_transcript(d)
                self._maybe_paste_own_speech(segments, t)
            else:
                meta["processing"]["transcript"] = "disabled"
        except Exception as e:
            meta["processing"]["transcript"] = f"failed: {e}"
            self.log(f"transcription failed: {e}")

        try:
            s = self.config.summary
            if s.get("enabled", True) and (d / "transcript.md").exists():
                self.stage = "summarizing"
                self._notify()
                if ollama_available(s["ollama_url"]):
                    self.log("summarizing via ollama...")
                    text = summarize(
                        (d / "transcript.md").read_text(encoding="utf-8"),
                        s["ollama_url"], s["ollama_model"], s.get("language", "ru"),
                    )
                    (d / "summary.md").write_text(text + "\n", encoding="utf-8")
                    meta["processing"]["summary"] = "done"
                else:
                    meta["processing"]["summary"] = "deferred: ollama unavailable"
                    self.log("ollama not reachable; summary deferred")
            elif not (d / "transcript.md").exists():
                meta["processing"]["summary"] = "skipped: no transcript"
        except Exception as e:
            meta["processing"]["summary"] = f"failed: {e}"
            self.log(f"summary failed: {e}")

        if not self.config.keep_audio and meta["processing"]["transcript"] == "done":
            wav.unlink(missing_ok=True)
            self.log("audio discarded (keep_audio=false)")

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"done → {d}")
