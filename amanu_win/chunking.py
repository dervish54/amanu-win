"""Chunk schedule and live accumulation tap for streaming transcription.

Schedule: first chunk 0-10s, then an 8s stride with a 2s overlap, so each
window after the first repeats exactly the last 2s of the previous one.
The tap is fed by the mic capture callback and emits completed chunks as
soon as their audio exists — no waiting for the recording to stop.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FIRST_S = 10.0
STRIDE_S = 8.0
OVERLAP_S = 2.0


def chunk_bounds(duration_s: float,
                 first_s: float = FIRST_S,
                 stride_s: float = STRIDE_S,
                 overlap_s: float = OVERLAP_S) -> list[tuple[float, float]]:
    """[(start_s, end_s)] covering [0, duration_s] per the schedule."""
    bounds: list[tuple[float, float]] = []
    if duration_s <= 0:
        return bounds
    end = min(first_s, duration_s)
    bounds.append((0.0, end))
    # advance by stride (never by end-overlap: float cancellation turned
    # (d-2)+2 < d into an infinite loop on sub-overlap durations)
    stride_s = max(stride_s, 1e-3)
    while bounds[-1][1] < duration_s - 1e-6:
        start = bounds[-1][0] + stride_s
        if start >= duration_s - 1e-6:
            break
        new_end = min(start + first_s, duration_s)
        if new_end <= bounds[-1][1]:
            break  # no new audio beyond current coverage
        bounds.append((start, new_end))
    return bounds


@dataclass
class Chunk:
    start_s: float
    end_s: float
    audio: np.ndarray  # int16, native sample rate


class ChunkTap:
    """Accumulates mic frames and emits chunks per the schedule.

    Feed with native-rate int16 frames from the capture callback; completed
    chunks are delivered to on_chunk as soon as their window is full.
    finish() flushes the final partial window (if it carries >1s of new
    audio beyond the last emitted window) and is idempotent.
    """

    def __init__(self, sample_rate: int, on_chunk,
                 first_s: float = FIRST_S, stride_s: float = STRIDE_S,
                 overlap_s: float = OVERLAP_S):
        self.sr = sample_rate
        self.first_s = first_s
        self.stride_s = stride_s
        self.overlap_s = overlap_s
        self.on_chunk = on_chunk
        self._buf: list[np.ndarray] = []
        self._total = 0
        self._emitted = 0   # number of chunks emitted
        self._finished = False

    @property
    def buffered_s(self) -> float:
        return self._total / self.sr

    def feed(self, frames: np.ndarray) -> None:
        if self._finished:
            return
        self._buf.append(np.asarray(frames).reshape(-1))
        self._total += len(frames)
        self._emit_ready(final=False)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._emit_ready(final=True)

    # -- internals ----------------------------------------------------------------
    def _emit_ready(self, final: bool) -> None:
        bounds = chunk_bounds(self.buffered_s, self.first_s, self.stride_s,
                              self.overlap_s)
        while self._emitted < len(bounds):
            s, e = bounds[self._emitted]
            if not final and e - s < self.first_s - 1e-9:
                break  # mid-recording only full windows; partials wait for finish
            if final and self._emitted > 0:
                prev_end = bounds[self._emitted - 1][1]
                if e - prev_end < 1.0:
                    break  # tail shorter than a second adds nothing usable
            audio = self._slice(s, e)
            self.on_chunk(Chunk(s, e, audio))
            self._emitted += 1

    def _slice(self, start_s: float, end_s: float) -> np.ndarray:
        flat = np.concatenate(self._buf) if self._buf else np.zeros(0, np.int16)
        return flat[int(start_s * self.sr):int(end_s * self.sr)]

import queue
import threading


class LiveMicTranscriber:
    """Transcribes mic chunks the moment they complete, during recording.

    feed() is called from the capture callback thread; transcription runs
    on a worker thread so capture never blocks on the model. finish()
    flushes the tail and waits for the last chunk — cheap, since nearly
    everything was already recognized while the recording was running.
    """

    def __init__(self, sample_rate: int, model_getter, language=None,
                 first_s: float = FIRST_S, stride_s: float = STRIDE_S,
                 overlap_s: float = OVERLAP_S, log=print):
        self._sr = sample_rate
        self._model_getter = model_getter
        self._language = language
        self._log = log
        self.fragments: list[dict] = []
        self._q: queue.Queue = queue.Queue()
        self._tap = ChunkTap(sample_rate, on_chunk=self._q.put,
                             first_s=first_s, stride_s=stride_s,
                             overlap_s=overlap_s)
        self._t = threading.Thread(target=self._run, name="live-transcribe",
                                   daemon=True)
        self._t.start()

    def feed(self, frames: np.ndarray) -> None:
        self._tap.feed(frames)

    def finish(self) -> list[dict]:
        self._tap.finish()
        self._q.put(None)
        self._t.join(timeout=300)
        return self.fragments

    def _run(self) -> None:
        model = None
        while True:
            chunk = self._q.get()
            if chunk is None:
                return
            try:
                if model is None:
                    model = self._model_getter()
                from .transcription import _to_16k
                audio = chunk.audio.astype(np.float32) / 32768.0
                segs, _info = model.transcribe(
                    _to_16k(audio, self._sr), language=self._language,
                    vad_filter=True)
                self.fragments.append({
                    "start_s": chunk.start_s, "end_s": chunk.end_s,
                    "segments": [{"start": round(s.start, 2),
                                  "end": round(s.end, 2),
                                  "text": s.text.strip()} for s in segs],
                })
                self._log(f"chunk {chunk.start_s:.0f}-{chunk.end_s:.0f}s recognized")
            except Exception as e:
                self._log(f"chunk transcription failed: {e}")
