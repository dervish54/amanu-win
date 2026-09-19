"""Session lifecycle: one folder per meeting; the folder is the database.

    <recordings_dir>/<YYYY.MM.DD-HHMM>/
    ├── audio.wav        # mic left, system right; removed after transcript unless keep_audio
    ├── transcript.md
    ├── transcript.json  # timed segments + per-channel engine provenance
    ├── summary.md
    └── meta.json        # timing, devices, trigger, processing state

With streaming enabled, the mic channel is transcribed live in overlapping
chunks while the recording runs; at stop only the tail is left, then the
LLM stitches the fragments into the punctuated whole text that gets pasted.
The far end is always transcribed from the archive after the stop.

stop() never waits on transcription: live fragment finalization happens on
the processing thread, because the caller of stop() is the keyboard hook —
blocking it would freeze the global hotkey.
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
from . import stitch
from .chunking import LiveMicTranscriber
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
        self._live: LiveMicTranscriber | None = None

    def _tap_feed(self, frames, rate) -> None:
        if self._live is None:
            stream_cfg = self._streaming_config()
            lang = self.config.transcription.get("language", "auto")
            self._live = LiveMicTranscriber(
                sample_rate=rate,
                model_getter=self._get_model,
                language=None if lang == "auto" else lang,
                first_s=stream_cfg.get("first_s", 10.0),
                stride_s=stream_cfg.get("stride_s", 8.0),
                overlap_s=stream_cfg.get("overlap_s", 2.0),
                log=self.log,
            )
        self._live.feed(frames)

    def _on_capture_warning(self, msg: str) -> None:
        self.capture_warnings.append(msg)
        self._notify()

    def _notify(self) -> None:
        if self.on_state_changed is not None:
            try:
                self.on_state_changed()
            except Exception:
                pass

    def _get_model(self):
        if self._model is None:
            t = self.config.transcription
            self.log(f"loading model {t['model']} ({t['device']})...")
            self._model = load_model(t["model"], t["device"], self.config.models_dir)
        return self._model

    def _streaming_config(self) -> dict:
        return self.config.transcription.get("streaming", {})

    def _streaming_enabled(self) -> bool:
        return (self.config.transcription.get("enabled", True)
                and self._streaming_config().get("enabled", True))

    # -- recording -------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self.recorder is not None

    def start_recording(self) -> Path:
        if self.recorder:
            raise RuntimeError("already recording")
        self.session_dir = new_session_dir(self.config.recordings_dir)
        self.capture_warnings: list[str] = []
        self.recorder = StereoRecorder(
            self.session_dir / "audio.wav",
            mic_device=self.config.data.get("mic_device"),
            on_warning=self._on_capture_warning)
        if self._streaming_enabled():
            # the live transcriber is created on the first frames: with the
            # shotgun start the winning mic's rate is unknown until the probe
            self.recorder.mic_tap = self._tap_feed
        self.recorder.start()
        self.log(f"recording → {self.session_dir}")
        return self.session_dir

    def stop_recording(self) -> Path:
        if not self.recorder:
            raise RuntimeError("not recording")
        info = self.recorder.stop()
        live = self._live
        self._live = None
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
        if info.warnings:
            meta["capture_warnings"] = info.warnings
            self.log("capture warnings: " + "; ".join(info.warnings))
        (d / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # live.finish() can take many seconds (model load + tail chunk) — it
        # belongs to the processing thread, never to the caller of stop()
        threading.Thread(target=self.process, args=(d,),
                         kwargs={"live": live}, daemon=True).start()
        return d

    # -- post-processing ----------------------------------------------------------
    def process(self, session_dir: Path, fragments: list[dict] | None = None,
                live: "LiveMicTranscriber | None" = None) -> None:
        self.processing.set()
        self._notify()
        try:
            if live is not None:
                self.stage = "finishing chunks"
                self._notify()
                fragments = live.finish()
            self._process_locked(session_dir, fragments)
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

    def _paste_text(self, text: str) -> None:
        """Clipboard + Ctrl+V into the focused window."""
        if not text.strip():
            return
        if not self.config.data.get("paste", {}).get("enabled", True):
            return
        try:
            paste.paste_text(text)
            self.log(f"pasted {len(text)} chars of own speech")
        except Exception as e:
            self.log(f"paste failed: {e}")

    def _maybe_paste_own_speech(self, segments: list[dict], tconf: dict) -> None:
        """Classic path: own words from the whole-file transcript, punctuated."""
        text = paste.own_speech_text(segments, tconf.get("mic_speaker", "Микрофон"))
        if text.strip() and self._punctuation_enabled():
            s = self.config.summary
            text = punctuate.restore_punctuation(text, s["ollama_url"], s["ollama_model"])
        self._paste_text(text)

    def _process_locked(self, d: Path, fragments: list[dict] | None = None) -> None:
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        wav = d / "audio.wav"
        t = self.config.transcription
        s = self.config.summary
        stream_cfg = self._streaming_config()
        streaming = bool(fragments) and stream_cfg.get("enabled", True)

        try:
            if t.get("enabled", True):
                self.stage = "transcribing"
                self._notify()
                mic_label = t.get("mic_speaker", "Микрофон")
                if streaming:
                    # mic was transcribed live; only the far end is left
                    segments, prov = transcribe_channels(
                        wav, self._get_model(),
                        language=t.get("language", "auto"),
                        mic_label=mic_label,
                        system_label=t.get("system_speaker", "Собеседник"),
                        log=self.log, include_mic=False,
                    )
                    meta["streaming"] = {"fragments": len(fragments)}
                    stitched = stitch.stitch_fragments(
                        fragments, s["ollama_url"], s["ollama_model"],
                        first_s=stream_cfg.get("first_s", 10.0),
                        stride_s=stream_cfg.get("stride_s", 8.0),
                        overlap_s=stream_cfg.get("overlap_s", 2.0))
                    mic_block = (
                        [{"start": fragments[0]["start_s"],
                          "end": fragments[-1]["end_s"],
                          "speaker": mic_label, "text": stitched}]
                        if stitched else [])
                    all_segs = sorted(mic_block + segments,
                                      key=lambda x: (x["start"], x["end"]))
                    write_transcript_md(all_segs, d / "transcript.md")
                    payload = {
                        "engine": "faster-whisper",
                        "mic": {"mode": "streaming",
                                "schedule": {k: stream_cfg.get(k) for k in
                                             ("first_s", "stride_s", "overlap_s")},
                                "fragments": fragments},
                        "channels": prov,
                        "segments": all_segs,
                    }
                    (d / "transcript.json").write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    meta["processing"]["transcript"] = "done"
                    # the stitch already punctuated: no separate LLM pass
                    self._paste_text(stitched)
                else:
                    segments, prov = transcribe_channels(
                        wav, self._get_model(),
                        language=t.get("language", "auto"),
                        mic_label=mic_label,
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
