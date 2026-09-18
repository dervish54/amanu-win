"""Chunk schedule and tap: first chunk 0-10s, then 8s stride, 2s overlap."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.chunking import ChunkTap, chunk_bounds  # noqa: E402


def test_schedule_first_ten_then_stride_with_overlap():
    assert chunk_bounds(10) == [(0.0, 10.0)]
    assert chunk_bounds(18) == [(0.0, 10.0), (8.0, 18.0)]
    assert chunk_bounds(26) == [(0.0, 10.0), (8.0, 18.0), (16.0, 26.0)]
    # tail shorter than a full window is still emitted
    assert chunk_bounds(10.5) == [(0.0, 10.0), (8.0, 10.5)]
    assert chunk_bounds(5) == [(0.0, 5.0)]
    assert chunk_bounds(0) == []


def test_schedule_matches_overlap_invariant():
    # every window after the first overlaps the previous by exactly overlap_s
    for s, e in chunk_bounds(50):
        pass
    bounds = chunk_bounds(50)
    for (s0, e0), (s1, e1) in zip(bounds, bounds[1:]):
        assert e0 - s1 == 2.0


def test_tap_emits_completed_chunks_as_audio_arrives():
    emitted = []
    tap = ChunkTap(sample_rate=16000, first_s=10, stride_s=8, overlap_s=2,
                   on_chunk=emitted.append)
    # feed 12s worth: chunk (0,10) completes
    tap.feed(np.zeros(16000 * 12, dtype=np.int16))
    assert [(c.start_s, c.end_s) for c in emitted] == [(0.0, 10.0)]
    assert len(emitted[0].audio) == 16000 * 10

    # feed 8 more seconds: chunk (8,18) completes
    tap.feed(np.zeros(16000 * 8, dtype=np.int16))
    assert [(c.start_s, c.end_s) for c in emitted] == [(0.0, 10.0), (8.0, 18.0)]


def test_tap_finish_flushes_tail():
    emitted = []
    tap = ChunkTap(sample_rate=16000, first_s=10, stride_s=8, overlap_s=2,
                   on_chunk=emitted.append)
    tap.feed(np.zeros(16000 * 15, dtype=np.int16))  # only (0,10) complete
    tap.finish()
    bounds = [(c.start_s, c.end_s) for c in emitted]
    assert bounds == [(0.0, 10.0), (8.0, 15.0)]
    # finishing twice must not double-emit
    tap.finish()
    assert [(c.start_s, c.end_s) for c in emitted] == bounds


def test_short_recording_terminates():
    # incident 2026.09.18-1905: buffered 0.234s -> 0.234 - 2 + 2 == 0.234
    # by float cancellation made the loop spin until MemoryError
    import threading
    result = {}

    def run():
        result["bounds"] = chunk_bounds(0.234)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert "bounds" in result, "chunk_bounds hung on a sub-overlap duration"
    assert result["bounds"] == [(0.0, 0.234)]


def test_short_recording_tap_finish_terminates():
    emitted = []
    tap = ChunkTap(sample_rate=48000, on_chunk=emitted.append)
    tap.feed(np.zeros(11232, dtype=np.int16))  # exactly the incident frames
    tap.finish()
    assert [(c.start_s, c.end_s) for c in emitted] == [(0.0, 11232 / 48000)]
