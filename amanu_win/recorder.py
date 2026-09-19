"""Two-channel audio capture: microphone (left) + system loopback (right).

Each side is recorded into its own mono PCM WAV at the device's *native*
sample rate, written directly from its capture callback. A mono file paced
by its own stream cannot drift: there is no cross-stream pairing, no queue,
no timeouts. At stop, both channels are resampled to 48 kHz and interleaved
into the stereo archive (mic left, far end right) — the same layout the
macOS app writes.

Earlier revision merged both streams chunk-by-chunk in one writer thread;
file time then followed queue availability instead of wall time, which
slowed the audio down and cut the tail off when recording stopped.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import logging

import numpy as np
import sounddevice as sd
import soundfile as sf
import pyaudiowpatch as pyaudio

ARCHIVE_RATE = 48000
DTYPE = "int16"


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
    (MMDevice API, eCapture/eConsole). PortAudio's own hostapi default can
    point at a different endpoint entirely — that mismatch was incident
    2026.09.18-1905 (browser heard the mic, we opened a silent one)."""
    try:
        from comtypes import CLSCTX_ALL, CoCreateInstance
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
        from pycaw.pycaw import IMMDeviceEnumerator, AudioUtilities
        enum = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        # communications first: Chrome and call apps record from the
        # communications default, which tracks newly plugged headsets faster
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
    (config "mic_device") — with many virtual mics installed the Windows
    default is often not the device the user means. Otherwise follows the
    Windows default capture endpoint by name; falls back to the WASAPI
    hostapi default only when the endpoint cannot be read.
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


def _wasapi_default_input_legacy():
    """(device_index, rate) of the WASAPI default input, or None.

    The MME-default input cannot be trusted: Bluetooth hands-free mics are
    exposed there at fake rates (44100) while the device truly runs at
    16000, and frames silently go missing. WASAPI reports and delivers its
    mix format exactly.
    """
    try:
        wasapi = next(
            (i for i, a in enumerate(sd.query_hostapis())
             if "WASAPI" in a["name"].upper()), None)
        if wasapi is None:
            return None
        idx = int(sd.query_hostapis(wasapi)["default_input_device"])
        if idx < 0:
            return None
        dev = sd.query_devices(idx)
        if dev["max_input_channels"] < 1:
            return None
        return idx, int(dev["default_samplerate"])
    except Exception:
        return None


class StereoRecorder:
    def __init__(self, out_path: Path, mic_tap=None, mic_device=None,
                 on_warning=None):
        self.out_path = out_path
        # mic_tap: optional callable fed every mic frame batch (int16, native
        # rate) for live chunk transcription; assigned before start()
        self.mic_tap = mic_tap
        picked = select_mic_device(prefer=mic_device)
        self._mic_pick = picked  # resolved once so tap and stream agree
        self.info = CaptureInfo(
            mic_device=default_mic_name() or "unavailable",
            system_device=default_loopback_name() or "unavailable",
        )
        if picked:
            self.info.mic_rate = picked[1]
            self.info.mic_device = picked[2]  # the endpoint actually opened
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stream = None
        self._sf_mic = None
        self._sf_sys = None
        self._mic_frames = 0
        self._sys_frames = 0
        self._early_warnings: list[str] = []
        self._mic_peak = 0
        self._sys_peak = 0
        self.on_warning = on_warning
        self.error: str | None = None

    # -- public API ------------------------------------------------------------
    def start(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.info.started_at = time.time()
        # loopback init (PyAudio + WASAPI open) takes a few hundred ms on
        # its own thread; start it first so it overlaps the mic stream open
        self._start_loopback()
        self._start_mic()
        threading.Thread(target=self._early_watchdog, name="capture-watchdog",
                         daemon=True).start()

    def stop(self) -> CaptureInfo:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=5)
        self.info.stopped_at = time.time()
        self.info.error = self.error
        self.info.warnings = self._early_warnings + capture_warnings(
            mic_frames=self._mic_frames, mic_rate=self.info.mic_rate or ARCHIVE_RATE,
            sys_frames=self._sys_frames, sys_rate=self.info.system_rate or ARCHIVE_RATE,
            wall_s=self.info.stopped_at - self.info.started_at,
            mic_opened=self._stream is not None,
            sys_opened=self._sf_sys is not None,
            mic_peak=self._mic_peak, sys_peak=self._sys_peak)
        # close the capture-side files first: on Windows a file cannot be
        # read/removed while a SoundFile still holds it open
        for s in (self._sf_mic, self._sf_sys):
            try:
                if s is not None and not s.closed:
                    s.close()
            except Exception:
                pass
        self._merge()
        return self.info

    # -- capture sides -----------------------------------------------------------
    def _start_mic(self):
        try:
            dev = sd.query_devices(kind="input")
            rate = int(dev["default_samplerate"])
        except Exception:
            rate = ARCHIVE_RATE
        device = None
        if self._mic_pick:
            device, rate = self._mic_pick[0], self._mic_pick[1]
        logging.getLogger(__name__).info(
            "opening mic stream: endpoint=%r rate=%s", self.info.mic_device, rate)
        self._sf_mic = sf.SoundFile(
            str(self._tmp(".mic")), mode="w", samplerate=rate,
            channels=1, subtype="PCM_16",
        )
        self.info.mic_rate = rate

        def cb(indata, frames, time_info, status):
            try:
                self._sf_mic.write(indata)
                self._mic_frames += len(indata)
                self._mic_peak = max(self._mic_peak, int(np.abs(indata.astype(np.int32)).max()))
            except Exception as e:
                self.error = f"mic write failed: {e}"
            if self.mic_tap is not None:
                try:
                    self.mic_tap(indata.copy())
                except Exception:
                    pass  # a tap bug must never disturb the archive write
            return


        try:
            self._stream = sd.InputStream(
                device=device, samplerate=rate, channels=1,
                dtype=DTYPE, callback=cb, blocksize=4096,
            )
            self._stream.start()
            logging.getLogger(__name__).info("mic stream started")
        except Exception:
            # WASAPI endpoint can refuse (exclusive-mode hold, profile
            # switch); fall back to the PortAudio default rather than
            # losing the microphone entirely
            try:
                self._stream = sd.InputStream(
                    samplerate=rate, channels=1, dtype=DTYPE, callback=cb
                )
                self._stream.start()
            except Exception as e:
                self.info.mic_device = f"unavailable ({e})"
                self._stream = None

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

    def _read_tmp(self, suffix: str) -> tuple[np.ndarray, int]:
        p = self._tmp(suffix)
        if not p.exists():
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

    def _early_watchdog(self) -> None:
        time.sleep(EARLY_WATCHDOG_S)
        if self._stop.is_set():
            return
        msg = early_capture_warning(
            self._mic_frames, self.info.mic_rate or ARCHIVE_RATE,
            EARLY_WATCHDOG_S, mic_opened=self._stream is not None,
            mic_peak=self._mic_peak)
        if msg:
            logging.getLogger(__name__).warning(msg)
            self._early_warnings.append(msg)
            if self.on_warning is not None:
                try:
                    self.on_warning(msg)
                except Exception:
                    pass

    def _merge(self):
        logging.getLogger(__name__).info(
            "merging tracks: mic %s frames @%s, sys %s frames @%s",
            self._mic_frames, self.info.mic_rate, self._sys_frames, self.info.system_rate)
        """Resample both mono tracks to the archive rate and interleave.

        Both streams start within ~100 ms of each other; for v1 the tracks
        are aligned at their first sample, same convention as the initial
        macOS implementation.
        """
        try:
            mic, mic_rate = self._read_tmp(".mic")
            sys_l, sys_rate = self._read_tmp(".sys")
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
        except Exception as e:
            self.error = f"merge failed: {e}"
        finally:
            for s in (self._sf_mic, self._sf_sys):
                try:
                    if s is not None and not s.closed:
                        s.close()
                except Exception:
                    pass


SILENCE_PEAK = 3  # int16; below this the device is emitting digital zeros


def capture_warnings(mic_frames: int, mic_rate: int,
                     sys_frames: int, sys_rate: int,
                     wall_s: float,
                     mic_opened: bool = True,
                     sys_opened: bool = True,
                     mic_peak: int | None = None,
                     sys_peak: int | None = None) -> list[str]:
    """Flag streams that stalled: opened but delivered <50% of wall time.

    A stream that dies mid-recording (e.g. a virtual mic that needs its
    host app) otherwise leaves an archive that looks valid but is mostly
    silence; surface it in meta.json instead of discovering it by ear.
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
    # frame counters alone miss drivers that emit synthetic silence (WO Mic):
    # frames arrive on schedule, but every sample is zero
    if mic_opened and mic_peak is not None and mic_s >= 0.5 * wall_s and mic_peak < SILENCE_PEAK:
        warns.append("mic delivered only digital silence — the device is not actually streaming audio")
    if sys_opened and sys_peak is not None and sys_s >= 0.5 * wall_s and sys_peak < SILENCE_PEAK:
        warns.append("system loopback delivered only digital silence")
    return warns

EARLY_WATCHDOG_S = 5.0
EARLY_MIN_DELIVERED_S = 2.0


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
