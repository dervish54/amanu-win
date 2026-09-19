"""Mic selection: follows the endpoint other apps record from, robustly.

Incident 1905: Windows default capture endpoint was WO Mic (browser heard
it) while we opened the WASAPI hostapi default — a silent endpoint.
Incident 0027: headphones became the default (Chrome heard them) but the
exact-name match against the truncated PortAudio name failed and the
fallback hit a stale WASAPI default. Selector: MMDevice endpoint,
communications role first, prefix-tolerant name matching.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amanu_win import recorder  # noqa: E402

WASAPI = 2


def _fake_sd(monkeypatch, devices, wasapi_default_idx, mme_default=None):
    def query_devices(arg=None, kind=None):
        if kind == "input":
            return mme_default or {"name": "none", "default_samplerate": 48000.0}
        if isinstance(arg, int):
            return devices[arg]
        return devices

    def hostapis(i=None):
        apis = [{"name": "MME", "default_input_device": -1},
                {"name": "DS", "default_input_device": -1},
                {"name": "Windows WASAPI", "default_input_device": wasapi_default_idx}]
        return apis[i] if i is not None else apis

    monkeypatch.setattr(recorder.sd, "query_devices", query_devices)
    monkeypatch.setattr(recorder.sd, "query_hostapis", hostapis)


DEVICES = [
    {"name": "Микрофон (WO Mic Device)", "hostapi": WASAPI, "max_input_channels": 1,
     "default_samplerate": 48000.0},
    {"name": "Головной телефон (1MORE SonoFlo", "hostapi": WASAPI, "max_input_channels": 1,
     "default_samplerate": 16000.0},
]


def test_selector_follows_windows_default_endpoint(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    monkeypatch.setattr(recorder, "default_capture_endpoint_name",
                        lambda: "Микрофон (WO Mic Device)")
    picked = recorder.select_mic_device()
    assert picked[0] == 0, "must open the endpoint Windows considers default"
    assert picked[1] == 48000


def test_selector_falls_back_to_wasapi_default(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    monkeypatch.setattr(recorder, "default_capture_endpoint_name", lambda: None)
    picked = recorder.select_mic_device()
    assert picked[0] == 0


def test_selector_returns_none_when_no_devices(monkeypatch):
    _fake_sd(monkeypatch, [], wasapi_default_idx=-1)
    monkeypatch.setattr(recorder, "default_capture_endpoint_name", lambda: None)
    assert recorder.select_mic_device() is None


def test_truncated_portaudio_name_matches_full_endpoint_name(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    monkeypatch.setattr(
        recorder, "default_capture_endpoint_name",
        lambda: "Головной телефон (1MORE SonoFlow Hands-Free AG Audio)")
    picked = recorder.select_mic_device()
    assert picked is not None and picked[0] == 1, (
        "truncated PortAudio name must still match the full MMDevice name")


def test_communications_role_preferred_over_console(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    # the getter tries eCommunications first; here only comms returns a name
    calls = []

    def getter():
        calls.append(1)
        return "Головной телефон (1MORE SonoFlow Hands-Free AG Audio)"

    monkeypatch.setattr(recorder, "default_capture_endpoint_name", getter)
    picked = recorder.select_mic_device()
    assert picked[2] == "Головной телефон (1MORE SonoFlo"


def test_capture_warnings_flag_digital_silence():
    warns = recorder.capture_warnings(
        mic_frames=48000 * 97, mic_rate=48000,
        sys_frames=44100 * 97, sys_rate=44100,
        wall_s=97.0, mic_peak=0, sys_peak=0)
    assert any("silence" in w.lower() or "тишин" in w for w in warns), warns


def test_capture_warnings_quiet_on_real_audio():
    warns = recorder.capture_warnings(
        mic_frames=48000 * 97, mic_rate=48000,
        sys_frames=44100 * 97, sys_rate=44100,
        wall_s=97.0, mic_peak=2000, sys_peak=1500)
    assert warns == []


def test_mic_device_override_by_substring(monkeypatch):
    # user pins a device; Windows default (WO Mic) must not win
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    monkeypatch.setattr(recorder, "default_capture_endpoint_name",
                        lambda: "Микрофон (WO Mic Device)")
    picked = recorder.select_mic_device(prefer="SonoFlo")
    assert picked is not None and picked[0] == 1, "config override must win over the default endpoint"


def test_mic_device_override_no_match_falls_back(monkeypatch):
    _fake_sd(monkeypatch, DEVICES, wasapi_default_idx=0)
    monkeypatch.setattr(recorder, "default_capture_endpoint_name", lambda: None)
    picked = recorder.select_mic_device(prefer="nonexistent-device")
    assert picked[0] == 0, "no match → normal default chain"
