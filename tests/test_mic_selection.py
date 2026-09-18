"""Mic selection must follow the endpoint other apps record from.

Incident 2026.09.18-1905: Windows default capture endpoint was the WO Mic
(browser recorded it fine) while we opened the WASAPI hostapi default — a
different, silent endpoint. The selector must consult the MMDevice API and
match by name, falling back to the WASAPI hostapi default only when the
endpoint can't be read.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import recorder  # noqa: E402

WASAPI = 2  # hostapi index in the fakes below


def _fake_sd(monkeypatch, devices, wasapi_default_idx, mme_default):
    def query_devices(arg=None, kind=None):
        if kind == "input":
            return mme_default
        if isinstance(arg, int):
            return devices[arg]
        return devices

    monkeypatch.setattr(recorder.sd, "query_devices", query_devices)
    monkeypatch.setattr(
        recorder.sd, "query_hostapis",
        lambda i=None: (
            {"name": "Windows WASAPI", "default_input_device": wasapi_default_idx}
            if i == WASAPI or i is None else {"name": f"api{i}", "default_input_device": -1}
        ) if i is not None else [{"name": "MME"}, {"name": "DS"}, {"name": "Windows WASAPI", "default_input_device": wasapi_default_idx}])
    # hostapi enumeration: find WASAPI
    def hostapis_all(i=None):
        if i is None:
            return [{"name": "MME"}, {"name": "DS"}, {"name": "Windows WASAPI", "default_input_device": wasapi_default_idx}]
        return [{"name": "MME"}, {"name": "DS"}, {"name": "Windows WASAPI", "default_input_device": wasapi_default_idx}][i]

    monkeypatch.setattr(recorder.sd, "query_hostapis", hostapis_all)


DEVICES = [
    {"name": "Гарнитура BT", "hostapi": WASAPI, "max_input_channels": 1,
     "default_samplerate": 16000.0},
    {"name": "Микрофон (WO Mic Device)", "hostapi": WASAPI, "max_input_channels": 1,
     "default_samplerate": 48000.0},
]


def test_selector_follows_windows_default_endpoint(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0,
             mme_default={"name": "Микрофон (WO Mic Device)", "default_samplerate": 48000.0})
    monkeypatch.setattr(recorder, "default_capture_endpoint_name",
                        lambda: "Микрофон (WO Mic Device)")
    picked = recorder.select_mic_device()
    assert picked[0] == 1, "must open the endpoint Windows considers default, not the WASAPI list default"
    assert picked[1] == 48000
    assert picked[2] == "Микрофон (WO Mic Device)"


def test_selector_falls_back_to_wasapi_default(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0,
             mme_default={"name": "Гарнитура BT", "default_samplerate": 16000.0})
    monkeypatch.setattr(recorder, "default_capture_endpoint_name", lambda: None)
    picked = recorder.select_mic_device()
    assert picked[0] == 0  # WASAPI hostapi default


def test_selector_returns_none_when_no_devices(monkeypatch):
    _fake_sd(monkeypatch, [], wasapi_default_idx=-1,
             mme_default={"name": "none", "default_samplerate": 48000.0})
    monkeypatch.setattr(recorder, "default_capture_endpoint_name", lambda: None)
    assert recorder.select_mic_device() is None
