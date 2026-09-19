"""Two-channel audio capture: microphone (left) + system loopback (right).

Microphone leg uses a shotgun start: every ACTIVE capture endpoint records
the first PROBE_S seconds, and the archive keeps the one with a live
signal — a dead virtual driver (WO Mic, Virtual Desktop) emits synthetic
zeros (peak 0-1), while a real ADC always has a noise floor. The user
starts speaking immediately; no start delay, no lost words. A pinned
`mic_device` config skips the shotgun and opens exactly that device.

Each side is written to its own mono PCM WAV at the device's native rate,
directly from its capture callback; at stop both tracks are resampled to
48 kHz and interleaved into the stereo archive (mic left, far end right).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import pyaudiowpatch as pyaudio

ARCHIVE_RATE = 48000
DTYPE = "int16"
LIVE_PEAK_FLOOR = 10   # int16 peak above this = live device (ADC noise floor)
PROBE_S = 2.5          # shotgun decision window
EARLY_WATCHDOG_S = 5.0
EARLY_MIN_DELIVERED_S = 2.0
SILENCE_PEAK = 3       # int16; below this the device is emitting digital zeros


@dataclass
class CaptureInfo:
    mic_device: str = "none"
    system_device: str = "none"
    mic_rate: int = 0
    system_rate: int = 0
    started_at: float = 0.0
    stopped_at: float = 0.0
    error: str | None = None
    warnings: list = None
    mic_switched_from: str | None = None


def default_mic_name() -> str | None:
    try:
        d = sd.query_devices(kind="input")
        return f"{d['name']} ({int(sd.default.device[0])})"
    except Exception:
        return None


def default_loopback_name() -> str | None:
    p = pyaudio.PyAudio()
    try:
        return p.get_default_wasapi_loopback()["name"]
    except Exception:
        return None
    finally:
        p.terminate()


def _resample(mono: np.ndarray, src_rate: int, dst_rate: int = ARCHIVE_RATE) -> np.ndarray:
    if src_rate == dst_rate or len(mono) == 0:
        return mono.astype(np.float32)
    n = int(round(len(mono) * dst_rate / src_rate))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    t_src = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    t_dst = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(t_dst, t_src, mono).astype(np.float32)


def default_capture_endpoint_name() -> str | None:
    """The endpoint other apps record from: Windows default capture device
    (MMDevice API). Communications role first — Chrome and call apps record
    from it, and it tracks newly plugged headsets faster. PortAudio's own
    hostapi default can point at a different endpoint entirely (incident
    2026.09.18-1905)."""
    try:
        from comtypes import CLSCTX_ALL, CoCreateInstance
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
        from pycaw.pycaw import IMMDeviceEnumerator, AudioUtilities
        enum = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        for role in (ERole.eCommunications, ERole.eConsole):
            try:
                d = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, role.value)
                return AudioUtilities.CreateDevice(d).FriendlyName
            except Exception:
                continue
    except Exception:
        pass
    return None


def _names_match(portaudio_name: str, endpoint_name: str) -> bool:
    """PortAudio truncates long WASAPI device names; a truncated prefix of
    the full MMDevice FriendlyName is still the same endpoint."""
    a = portaudio_name.strip().lower()
    b = endpoint_name.strip().lower()
    return bool(a) and bool(b) and (a == b or b.startswith(a) or a.startswith(b))


def select_mic_device(prefer: str | None = None):
    """(index, rate, name) of the WASAPI endpoint to open, or None.

    prefer: case-insensitive substring pinning a specific device
    (config "mic_device"). A pin that matches NOTHING returns None — never
    a silent fallback: 2026-09-19 the SonoFlow HFP endpoint vanished in
    A2DP mode, the fallback opened a dead virtual cable, and 80 s of
    digital silence got recorded. Bluetooth endpoints appear and disappear
    with the profile switch; callers wanting resilience use
    select_mic_device_with_retry.
    """
    try:
        wasapi = next((i for i, a in enumerate(sd.query_hostapis())
                       if "WASAPI" in a["name"].upper()), None)
        if wasapi is None:
            return None
        devices = sd.query_devices()
        if prefer:
            needle = prefer.strip().lower()
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi and d["max_input_channels"] > 0
                        and needle in d["name"].lower()):
                    return i, int(d["default_samplerate"]), d["name"]
            return None
        ep = default_capture_endpoint_name()
        if ep:
            for i, d in enumerate(devices):
                if (d["hostapi"] == wasapi and d["max_input_channels"] > 0
                        and _names_match(d["name"], ep)):
                    return i, int(d["default_samplerate"]), d["name"]
        idx = int(sd.query_hostapis(wasapi)["default_input_device"])
        if idx >= 0:
            d = devices[idx]
            if d["max_input_channels"] > 0:
                return idx, int(d["default_samplerate"]), d["name"]
    except Exception:
        return None
    return None


def select_mic_device_with_retry(prefer: str, tries: int = 4,
                                 delay_s: float = 0.75):
    """Retry pin selection, kicking the Bluetooth profile once on the first
    miss: the pinned HFP capture endpoint exists in MMDevice but is invisible
    to PortAudio until someone activates it — that is what Chrome and the
    Settings mic test do, and why they 'work' while enumeration shows
    nothing. The kick connects hands-free; the endpoint then appears."""
    for attempt in range(tries):
        pick = select_mic_device(prefer=prefer)
        if pick is not None:
            return pick
        if attempt == 0:
            kick_hf_endpoint(prefer)
        if attempt < tries - 1:
            time.sleep(delay_s)
    return None


def kick_hf_endpoint(needle: str, hold_s: float = 0.6) -> bool:
    """Activate the named capture endpoint via MMDevice and run its stream
    briefly, forcing Windows to connect the Bluetooth hands-free profile.
    True when a matching endpoint was found and activated."""
    import warnings
    warnings.filterwarnings("ignore")
    try:
        from comtypes import CLSCTX_ALL, CoCreateInstance
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow
        from pycaw.pycaw import IMMDeviceEnumerator, AudioUtilities
        from pycaw.api.audioclient import IAudioClient

        n = needle.strip().lower()
        enum = CoCreateInstance(CLSID_MMDeviceEnumerator,
                                IMMDeviceEnumerator, CLSCTX_ALL)
        coll = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, 15)
        for i in range(coll.GetCount()):
            d = coll.Item(i)
            try:
                name = AudioUtilities.CreateDevice(d).FriendlyName
            except Exception:
                continue  # NOTPRESENT endpoints expose no property store
            if name and n in name.lower():
                from comtypes import cast, POINTER
                ac = cast(d.Activate(IAudioClient._iid_, CLSCTX_ALL, None),
                          POINTER(IAudioClient))
                fmt = ac.GetMixFormat()
                ac.Initialize(0, 0, 0, 0, fmt, None)  # shared mode
                ac.Start()
                time.sleep(hold_s)
                ac.Stop()
                return True
    except Exception:
        logging.getLogger(__name__).exception("HF endpoint kick failed")
    return False

def _active_capture_devices():
    """[(index, rate, name)] of WASAPI inputs Windows lists as ACTIVE, or
    None when the enumeration cannot be read (caller then opens just the
    default endpoint)."""
    try:
        from comtypes import CLSCTX_ALL, CoCreateInstance
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow
        from pycaw.pycaw import IMMDeviceEnumerator, AudioUtilities
        enum = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        coll = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, 1)  # ACTIVE
        active = [AudioUtilities.CreateDevice(coll.Item(i)).FriendlyName
                  for i in range(coll.GetCount())]
    except Exception:
        return None
    wasapi = next((i for i, a in enumerate(sd.query_hostapis())
                   if "WASAPI" in a["name"].upper()), None)
    if wasapi is None:
        return None
    out = []
    for i, d in enumerate(sd.query_devices()):
        if (d["hostapi"] == wasapi and d["max_input_channels"] > 0
                and any(_names_match(d["name"], a) for a in active)):
            out.append((i, int(d["default_samplerate"]), d["name"]))
    return out


class _MicLeg:
    """One open capture stream: own mono file, frame/peak counters, and a
    ring buffer so the winner's first seconds can be replayed into the
    live-transcription tap after the decision."""

    def __init__(self, recorder, index: int, rate: int, name: str):
        self.recorder = recorder
        self.index, self.rate, self.name = index, rate, name
        self.frames = 0
        self.peak = 0
        self.ring: list[np.ndarray] = []
        self.sf = sf.SoundFile(
            str(recorder._tmp(f".mic{index}")), mode="w",
            samplerate=rate, channels=1, subtype="PCM_16")
        self.stream = None

    @property
    def file(self) -> Path:
        return Path(self.sf.name)

    def open(self) -> None:
        self.stream = sd.InputStream(
            device=self.index, samplerate=self.rate, channels=1,
            dtype=DTYPE, callback=self._cb, blocksize=4096)
        self.stream.start()

    def _cb(self, indata, frames, time_info, status):
        r = self.recorder
        if r._first_mic_frame_at is None:
            r._first_mic_frame_at = time.time()
            logging.getLogger(__name__).info(
                "first mic frame %.2fs after recording start",
                r._first_mic_frame_at - r.info.started_at)
        try:
            self.sf.write(indata)
            self.frames += len(indata)
            self.peak = max(self.peak, int(np.abs(indata.astype(np.int32)).max()))
        except Exception as e:
            self.recorder.error = f"mic write failed: {e}"
            return
        r = self.recorder
        if r._mic_active is self and r.mic_tap is not None:
            try:
                r.mic_tap(indata.copy(), self.rate)
            except Exception:
                pass  # a tap bug must never disturb the archive write
        elif r._mic_active is None:
            self.ring.append(indata.copy())

    def close(self) -> None:
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        try:
            self.sf.close()
        except Exception:
            pass


class StereoRecorder:
    def __init__(self, out_path: Path, mic_tap=None, mic_device=None,
                 on_warning=None):
        self.out_path = out_path
        # mic_tap(frames, rate): live chunk transcription feed
        self.mic_tap = mic_tap
        self._mic_device_pref = mic_device
        self.on_warning = on_warning
        self.info = CaptureInfo(
            mic_device=default_mic_name() or "unavailable",
            system_device=default_loopback_name() or "unavailable",
        )
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._mic_legs: list[_MicLeg] = []
        self._mic_active: _MicLeg | None = None
        self._mic_file: Path | None = None
        self._mic_decision_timer: threading.Timer | None = None
        self._sf_sys = None
        self._sys_frames = 0
        self._sys_peak = 0
        self._early_warnings: list[str] = []
        self._first_mic_frame_at: float | None = None
        self.error: str | None = None

    # -- public API ------------------------------------------------------------
    def start(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.info.started_at = time.time()
        # loopback init (PyAudio + WASAPI open) takes a few hundred ms on
        # its own thread; start it first so it overlaps the mic opens
        self._start_loopback()
        self._start_mic()
        threading.Thread(target=self._early_watchdog, name="capture-watchdog",
                         daemon=True).start()

    def stop(self) -> CaptureInfo:
        self._stop.set()
        if self._mic_active is None and self._mic_legs:
            self._decide_mic()  # recording shorter than the probe window
        for leg in self._mic_legs:
            leg.close()
        if self._mic_decision_timer is not None:
            self._mic_decision_timer.cancel()
        for t in self._threads:
            t.join(timeout=5)
        self.info.stopped_at = time.time()
        self.info.error = self.error
        mic_frames = self._mic_active.frames if self._mic_active else 0
        mic_peak = self._mic_active.peak if self._mic_active else 0
        mic_opened = self._mic_active is not None
        self.info.warnings = self._early_warnings + capture_warnings(
            mic_frames=mic_frames, mic_rate=self.info.mic_rate or ARCHIVE_RATE,
            sys_frames=self._sys_frames, sys_rate=self.info.system_rate or ARCHIVE_RATE,
            wall_s=self.info.stopped_at - self.info.started_at,
            mic_opened=mic_opened,
            sys_opened=self._sf_sys is not None,
            mic_peak=mic_peak, sys_peak=self._sys_peak)
        self._merge()
        return self.info

    # -- mic: shotgun ------------------------------------------------------------
    def _start_mic(self):
        pick = (select_mic_device_with_retry(self._mic_device_pref)
                if self._mic_device_pref else select_mic_device())
        active = None if self._mic_device_pref else _active_capture_devices()

        if pick and active and len(active) > 1:
            # shotgun: default endpoint first, then every other active input
            specs = [pick] + [a for a in active if a[0] != pick[0]]
            for index, rate, name in specs:
                leg = _MicLeg(self, index, rate, name)
                try:
                    leg.open()
                except Exception as e:
                    logging.getLogger(__name__).warning(
                        "mic leg %r failed to open: %s", name, e)
                    try:
                        leg.sf.close()
                        leg.file.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                self._mic_legs.append(leg)
                logging.getLogger(__name__).info(
                    "mic leg opened: %r @%s", name, rate)
            if not self._mic_legs:
                self.info.mic_device = "unavailable (all legs failed to open)"
                return
            self.info.mic_rate = self._mic_legs[0].rate
            self._mic_decision_timer = threading.Timer(PROBE_S, self._decide_mic)
            self._mic_decision_timer.daemon = True
            self._mic_decision_timer.start()
            logging.getLogger(__name__).info(
                "shotgun: %d mic candidates, decision in %.1fs",
                len(self._mic_legs), PROBE_S)
            return

        # single-device path (pinned, one active input, or enumeration unreadable)
        if pick is None:
            self.info.mic_device = (f"unavailable (pinned mic "
                                    f"'{self._mic_device_pref}' not found)"
                                    if self._mic_device_pref
                                    else "unavailable (no capture endpoint)")
            return
        index, rate, name = pick
        leg = _MicLeg(self, index, rate, name)
        try:
            leg.open()
        except Exception:
            # WASAPI endpoint can refuse (exclusive-mode hold, profile
            # switch); fall back to the PortAudio default rather than
            # losing the microphone entirely
            try:
                leg = _MicLeg(self, None, rate, name)
                leg.open()
            except Exception as e:
                self.info.mic_device = f"unavailable ({e})"
                try:
                    leg.sf.close()
                    leg.file.unlink(missing_ok=True)
                except Exception:
                    pass
                return
        self._mic_legs = [leg]
        self._mic_active = leg
        self._mic_file = leg.file
        self.info.mic_device = name
        self.info.mic_rate = rate
        logging.getLogger(__name__).info(
            "opening mic stream: endpoint=%r rate=%s", name, rate)

    def _decide_mic(self):
        if self._mic_active is not None or not self._mic_legs:
            return
        legs = self._mic_legs
        primary = legs[0]
        if primary.peak >= LIVE_PEAK_FLOOR:
            winner = primary
        else:
            best = max(legs[1:], key=lambda l: l.peak, default=None)
            if best is not None and best.peak >= LIVE_PEAK_FLOOR:
                winner = best
                self.info.mic_switched_from = primary.name
                logging.getLogger(__name__).info(
                    "mic failover: %r silent -> switching to %r",
                    primary.name, best.name)
            else:
                winner = primary
                msg = ("all capture endpoints are silent (synthetic zeros) — "
                       "no live microphone found")
                logging.getLogger(__name__).warning(msg)
                self._early_warnings.append(msg)
                if self.on_warning is not None:
                    try:
                        self.on_warning(msg)
                    except Exception:
                        pass
        for leg in legs:
            if leg is winner:
                continue
            leg.close()
            leg.file.unlink(missing_ok=True)
        self._mic_active = winner
        self._mic_file = winner.file
        self.info.mic_device = winner.name
        self.info.mic_rate = winner.rate
        # replay the winner's buffered first seconds into the live tap
        if self.mic_tap is not None:
            for fr in winner.ring:
                try:
                    self.mic_tap(fr, winner.rate)
                except Exception:
                    break
        winner.ring = []

    def _early_watchdog(self) -> None:
        time.sleep(EARLY_WATCHDOG_S)
        if self._stop.is_set():
            return
        active = self._mic_active
        msg = early_capture_warning(
            active.frames if active else 0,
            self.info.mic_rate or ARCHIVE_RATE,
            EARLY_WATCHDOG_S, mic_opened=active is not None,
            mic_peak=active.peak if active else 0)
        if msg:
            logging.getLogger(__name__).warning(msg)
            self._early_warnings.append(msg)
            if self.on_warning is not None:
                try:
                    self.on_warning(msg)
                except Exception:
                    pass

    # -- loopback ------------------------------------------------------------------
    def _start_loopback(self):
        def run():
            p = pyaudio.PyAudio()
            try:
                dev = p.get_default_wasapi_loopback()
            except OSError:
                self.info.system_device = "unavailable (no WASAPI loopback device)"
                p.terminate()
                return
            rate = int(dev["defaultSampleRate"])
            self.info.system_rate = rate
            sf_sys = sf.SoundFile(
                str(self._tmp(".sys")), mode="w", samplerate=rate,
                channels=1, subtype="PCM_16",
            )
            self._sf_sys = sf_sys

            def cb(in_data, frame_count, time_info, status_flags):
                try:
                    data = np.frombuffer(in_data, dtype=np.int16)
                    sf_sys.write(data)
                    self._sys_frames += len(data)
                    self._sys_peak = max(self._sys_peak, int(np.abs(data.astype(np.int32)).max()))
                except Exception as e:
                    self.error = f"loopback write failed: {e}"
                return None, pyaudio.paContinue

            try:
                stream = p.open(
                    format=pyaudio.paInt16, channels=1, rate=rate,
                    input=True, input_device_index=dev["index"],
                    frames_per_buffer=4096, stream_callback=cb,
                )
            except Exception as e:
                self.info.system_device = f"unavailable ({e})"
                sf_sys.close()
                self._sf_sys = None
                p.terminate()
                return

            stream.start_stream()
            logging.getLogger(__name__).info("loopback stream started (rate=%s)", rate)
            while not self._stop.is_set() and stream.is_active():
                time.sleep(0.1)
            stream.stop_stream()
            stream.close()
            sf_sys.close()
            p.terminate()

        t = threading.Thread(target=run, name="loopback", daemon=True)
        t.start()
        self._threads.append(t)

    # -- merge ---------------------------------------------------------------------
    def _tmp(self, suffix: str) -> Path:
        return self.out_path.with_name(self.out_path.stem + suffix + ".wav")

    def _read_file(self, p: Path) -> tuple[np.ndarray, int]:
        if p is None or not p.exists():
            return np.zeros(0, dtype=np.float32), ARCHIVE_RATE
        try:
            data, rate = sf.read(str(p), dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1)
            return data.astype(np.float32), rate
        except Exception:
            return np.zeros(0, dtype=np.float32), ARCHIVE_RATE
        finally:
            p.unlink(missing_ok=True)

    def _merge(self):
        """Resample both mono tracks to the archive rate and interleave.

        Both streams start within ~100 ms of each other; for v1 the tracks
        are aligned at their first sample, same convention as the initial
        macOS implementation.
        """
        try:
            mic, mic_rate = self._read_file(self._mic_file)
            sys_l, sys_rate = self._read_file(self._tmp(".sys"))
            left = _resample(mic, mic_rate)
            right = _resample(sys_l, sys_rate)
            n = max(len(left), len(right))
            if n == 0:
                n = ARCHIVE_RATE  # both sides dead: still write a valid file
            if len(left) < n:
                left = np.concatenate([left, np.zeros(n - len(left), dtype=np.float32)])
            if len(right) < n:
                right = np.concatenate([right, np.zeros(n - len(right), dtype=np.float32)])
            interleaved = np.empty((n, 2), dtype=np.float32)
            interleaved[:, 0] = left[:n]
            interleaved[:, 1] = right[:n]
            sf.write(str(self.out_path), interleaved, ARCHIVE_RATE, subtype="PCM_16")
            logging.getLogger(__name__).info(
                "merged archive: mic %r (%s frames @%s), sys %s frames @%s",
                self.info.mic_device, len(mic), mic_rate, len(sys_l), sys_rate)
        except Exception as e:
            self.error = f"merge failed: {e}"


def capture_warnings(mic_frames: int, mic_rate: int,
                     sys_frames: int, sys_rate: int,
                     wall_s: float,
                     mic_opened: bool = True,
                     sys_opened: bool = True,
                     mic_peak: int | None = None,
                     sys_peak: int | None = None) -> list[str]:
    """Flag streams that stalled or delivered synthetic silence.

    A stream that dies mid-recording (virtual mic without its host app)
    leaves an archive that looks valid but is mostly silence; a synthetic-
    silence driver (WO Mic) delivers zeros on schedule. Surface both in
    meta.json instead of discovering them by ear.
    """
    warns: list[str] = []
    if wall_s <= 0:
        return warns
    mic_s = mic_frames / max(mic_rate, 1)
    sys_s = sys_frames / max(sys_rate, 1)
    if mic_opened and mic_s < 0.5 * wall_s:
        warns.append(f"mic delivered only {mic_s:.1f}s of {wall_s:.0f}s recording — check the microphone device")
    if sys_opened and sys_s < 0.5 * wall_s:
        warns.append(f"system loopback delivered only {sys_s:.1f}s of {wall_s:.0f}s recording")
    # frame counters alone miss drivers that emit synthetic silence:
    # frames arrive on schedule, but every sample is zero
    if mic_opened and mic_peak is not None and mic_s >= 0.5 * wall_s and mic_peak < SILENCE_PEAK:
        warns.append("mic delivered only digital silence — the device is not actually streaming audio")
    if sys_opened and sys_peak is not None and sys_s >= 0.5 * wall_s and sys_peak < SILENCE_PEAK:
        warns.append("system loopback delivered only digital silence")
    return warns


def early_capture_warning(mic_frames: int, mic_rate: int, elapsed_s: float,
                          mic_opened: bool = True, mic_peak: int | None = None) -> str | None:
    """Real-time form of the stall check: surfaces a dead mic while the
    recording is still running, not after the fact."""
    if not mic_opened or elapsed_s < EARLY_WATCHDOG_S:
        return None
    delivered = mic_frames / max(mic_rate, 1)
    if delivered < EARLY_MIN_DELIVERED_S:
        return (f"mic silent after {elapsed_s:.0f}s of recording "
                f"({delivered:.1f}s delivered) — check the microphone device")
    # synthetic-silence drivers (WO Mic) stream zeros on schedule
    if mic_peak is not None and mic_peak < SILENCE_PEAK:
        return (f"mic delivered only digital silence after {elapsed_s:.0f}s — "
                "the device is not actually streaming audio")
    return None
