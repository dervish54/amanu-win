"""Local transcription with faster-whisper.

The stereo archive is split into its two channels and each is transcribed
separately, giving free speaker attribution: left = mic, right = the far
end of the call. Segments from both channels are merged on one timeline.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def _ensure_cuda_dlls() -> None:
    """ctranslate2 needs CUDA libs on PATH; pip nvidia-* wheels ship them."""
    import sys, os, glob

    roots = [Path(p) for p in (getattr(sys, "_MEIPASS", None), *sys.path) if p]
    for root in roots:
        for pat in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/*/bin"):
            for d in glob.glob(str(root / pat)):
                if d not in os.environ["PATH"].split(os.pathsep):
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def load_model(model_name: str, device: str, models_dir: Path):
    from faster_whisper import WhisperModel

    models_dir.mkdir(parents=True, exist_ok=True)
    import os
    os.environ.setdefault("HF_HOME", str(models_dir))
    if device == "cuda":
        try:
            _ensure_cuda_dlls()
            return WhisperModel(model_name, device="cuda", compute_type="float16")
        except Exception:
            pass  # fall through to CPU: slower, but never blocks a meeting
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _to_16k(mono: np.ndarray, sr: int) -> np.ndarray:
    """faster-whisper treats a numpy input as already 16 kHz — feeding it 48 kHz
    audio stretches time exactly 3x and produces looping hallucinations."""
    if sr == 16000:
        return mono
    n = int(len(mono) * 16000 / sr)
    t_src = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    t_dst = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(t_dst, t_src, mono).astype(np.float32)


MIN_CHANNEL_SEC = 1.0   # shorter than this: nothing useful to transcribe
NOISE_GATE_RMS = 0.006  # below this the "speech" is hiss — whisper hallucinates on it


def transcribe_channels(
    wav_path: Path,
    model,
    language: str | None,
    mic_label: str,
    system_label: str,
    log=lambda msg: None,
):
    """Returns (segments, info_dict). segments: [{start,end,speaker,text}] sorted."""
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    channels = [(mic_label, audio[:, 0]), (system_label, audio[:, 1])]
    segments: list[dict] = []
    prov = {}
    lang = language if language and language != "auto" else None
    for label, mono in channels:
        if len(mono) < MIN_CHANNEL_SEC * sr:
            log(f"{label}: too short ({len(mono)/sr:.1f}s), skipped")
            continue
        rms = float(np.sqrt(np.mean(mono ** 2)))
        if rms < NOISE_GATE_RMS:
            log(f"{label}: below noise gate (RMS {rms:.4f}), skipped")
            continue
        t0 = time.time()
        segs, info = model.transcribe(_to_16k(mono, sr), language=lang, vad_filter=True)
        segs = list(segs)
        prov[label] = {
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration_after_vad": round(info.duration_after_vad, 2) if hasattr(info, "duration_after_vad") else None,
            "elapsed_s": round(time.time() - t0, 1),
        }
        log(f"{label}: {len(segs)} segments ({prov[label]['elapsed_s']}s)")
        for s in segs:
            segments.append(
                {"start": round(s.start, 2), "end": round(s.end, 2), "speaker": label, "text": s.text.strip()}
            )
    segments.sort(key=lambda s: (s["start"], s["end"]))
    return segments, prov


def write_transcript_md(segments: list[dict], path: Path) -> None:
    lines = []
    cur = None
    for s in segments:
        if s["speaker"] != cur:
            lines.append(f"\n**{s['speaker']}:**")
            cur = s["speaker"]
        lines.append(s["text"])
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def write_transcript_json(segments: list[dict], provenance: dict, path: Path) -> None:
    payload = {"engine": "faster-whisper", "channels": provenance, "segments": segments}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
