"""Panel hover + animation mechanics.

Incident: the expanded menu collapsed the moment the cursor left the
collapsed bar, because a child widget's <Leave> propagates to the toplevel
via bindtags. Collapse must be decided by pointer-vs-window-bounds, not by
widget-level events. Expand/collapse animate in two phases: horizontal
first, then vertical.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.panel import bounds_contain, expand_frames  # noqa: E402


def test_bounds_contain():
    assert bounds_contain((100, 100, 220, 132), (150, 150)) is True
    assert bounds_contain((100, 100, 220, 132), (50, 150)) is False
    assert bounds_contain((100, 100, 220, 132), (150, 50)) is False
    # boundary pixels count as inside
    assert bounds_contain((100, 100, 220, 132), (100, 100)) is True
    assert bounds_contain((100, 100, 220, 132), (319, 231)) is True
    assert bounds_contain((100, 100, 220, 132), (320, 231)) is False


def test_expand_frames_horizontal_then_vertical():
    frames = expand_frames((64, 16), (220, 132), steps=4)
    # phase 1: only width changes
    phase1 = frames[:4]
    assert all(h == 16 for w, h in phase1)
    assert phase1[-1][0] == 220
    # phase 2: only height changes
    phase2 = frames[4:]
    assert all(w == 220 for w, h in phase2)
    assert phase2[-1] == (220, 132)


def test_collapse_frames_reverse():
    frames = expand_frames((220, 132), (64, 16), steps=4)
    # collapsing animates vertical first, then horizontal
    phase1 = frames[:4]
    assert all(w == 220 for w, h in phase1)
    phase2 = frames[4:]
    assert all(h == 16 for w, h in phase2)
    assert frames[-1] == (64, 16)
