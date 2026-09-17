"""Guards: tiny/empty/noise-only channels must not crash transcription
and must not be sent to the model."""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win.transcription import transcribe_channels  # noqa: E402


def _wav(path, seconds, sr=48000, signal="noise"):
    n = int(seconds * sr)
    if signal == "noise":
        data = (np.random.default_rng(1).standard_normal(n) * 0.001).astype(np.float32)
    elif signal == "silence":
        data = np.zeros(n, dtype=np.float32)
    else:
        t = np.linspace(0, seconds, n, endpoint=False)
        data = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([data, data], axis=1)
    sf.write(str(path), stereo, sr)


def test_tiny_file_does_not_raise(tmp_path):
    wav = tmp_path / "tiny.wav"
    _wav(wav, 0.5)
    # model=None: the guards must fire before the model is ever touched
    segments, prov = transcribe_channels(
        wav, None, "en", "Mic", "Far", log=lambda m: None)
    assert segments == []
    assert set(prov) == set()


def test_noise_only_channel_is_skipped(tmp_path):
    wav = tmp_path / "noise.wav"
    _wav(wav, 5, signal="noise")
    segments, prov = transcribe_channels(
        wav, None, "en", "Mic", "Far", log=lambda m: None)
    assert segments == []
