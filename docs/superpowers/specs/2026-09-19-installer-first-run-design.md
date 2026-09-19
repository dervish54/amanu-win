# Installer + first-run setup — design (2026-09-19)

## Goal

A Windows user downloads `amanu-setup.exe` from the releases page and goes
from zero to a working recorder with the fewest possible decisions: a
standard installation wizard, then one progress window that fetches
everything the bundle deliberately does not ship.

## Architecture

Two actors with separate responsibilities.

**Inno Setup** does the classic installer work it does perfectly:
wizard pages (welcome → install directory with disk-space explanation →
model tier → checkboxes → finish), unpacking the PyInstaller onedir bundle,
"Programs and Features" entry, uninstaller. It writes the user's choices
into `config.json` and offers "Launch Amanu".

**The application** does the heavy lifting. On `--first-run` it detects the
unfinished setup and shows a progress window (styled like the floating
panel) that downloads the chosen whisper model, optionally installs Ollama
and pulls the LLM, verifies the microphone, then marks setup complete.
Downloading gigabytes inside Inno is strictly worse: no resume, opaque
progress, a network failure kills the whole install.

```
amanu-setup.exe (Inno)
  └─ wizard: folder | model (3 tiers) | ☐ Ollama | ☐ desktop icon | ☑ autorun
  └─ unpacks bundle → %LOCALAPPDATA%\Programs\Amanu  (per-user, no admin)
  └─ writes choices into config.json → "Launch Amanu"
        └─ first run: progress window
             ├─ whisper model of chosen tier → models_dir (HF cache)
             ├─ [if checked] winget install Ollama.Ollama + ollama pull qwen2.5:7b
             ├─ microphone: select_mic_device() → "found ✓"
             └─ setup_complete flag → normal app run
```

Rationale for per-user install without admin: least friction, no UAC.

## Components and model tiers

- **App bundle** (mandatory, ~300 MB): exe + Python + faster-whisper + CUDA DLLs.
- **Whisper model tiers** (radio buttons; GPU auto-detected via nvidia-smi /
  CUDA DLL presence, recommended tier highlighted):
  | Tier | Model | Size | For |
  |---|---|---|---|
  | Точная | `large-v3-turbo` fp16 | ~1.6 GB | NVIDIA GPU |
  | Сбалансированная | `small` fp16 | ~460 MB | weak GPU / strong CPU |
  | Компактная | `small` + int8 quantization | ~460 MB, fast load | CPU-only |
  Exact HF repos are pinned at implementation; if a trusted pre-quantized
  int8 turbo build (~800 MB) verifies, it becomes the middle tier.
- **Ollama + qwen2.5:7b** (optional, checked by default, ~5 GB): summaries,
  punctuation, stitching. Unchecked → transcript and paste work fully,
  degradation is soft (already in code). Can be enabled later from the tray.
- **Desktop icon** (unchecked default), **autorun** (checked default — the
  app is resident by design).
- **Recordings folder**: default `Documents\Amanu Recordings`, changeable on
  the directory page. Models live in `%LOCALAPPDATA%\amanu\models`; the
  wizard shows total required space up front.

Onboarding conveniences:
1. "Launch after install" — standard final checkbox.
2. Progress window offers "Do this later" — models download in background on
   first recording; the app is already in the tray.
3. Microphone auto-detected at first run, shown as "microphone: found ✓".
4. Recognition language stays "auto" — no extra wizard page.
5. After completion, a one-line hint near the panel ("hover here — recording"),
   fades after 10 seconds.

## Data flow

```
Inno wizard → %LOCALAPPDATA%\Programs\Amanu\ (bundle)
            → config.json: transcription.model = <tier>,
                           summary.enabled = <Ollama checkbox>
            → registry Run key / desktop shortcut — Inno itself
            → finish: amanu.exe --first-run

amanu.exe --first-run → reads config.json → progress window:
   whisper model → models_dir (HF cache)            [1.6 GB | 460 MB]
   [☐] winget install Ollama.Ollama → ollama pull qwen2.5:7b   [~5 GB]
   mic check line
   → setup_complete flag → normal run
```

Idempotent: the progress window reuses the existing `ensure_*` functions
from `setup.py`; an interrupted setup simply continues on the next launch
(HF cache resumes partial downloads, `ollama pull` resumes too).

## Error handling

No error kills the installation; each gives clear text plus Retry / Skip:

- **Network failure during model download** → Retry (resumes in place) or
  "Do this later" (flag stays unset, download happens on first recording).
- **Low disk space** → the Inno wizard checks free space before starting
  (bundle + chosen tier + Ollama); the progress window re-checks before
  each download.
- **winget missing/fails** (older Windows) → Ollama step marked "skipped",
  tray gets an "Install summaries" item later; the app is fully functional.
- **CUDA DLLs fail** → silent CPU fallback (existing behavior).
- **No microphone** → "microphone not found — check connection" line; setup
  completes, recording simply will not start.

Uninstaller removes the bundle and asks about models/recordings instead of
deleting silently.

## Testing

- Unit/integration (headless): tier→(repo, compute_type) mapping, config
  generation from wizard choices, resume logic (interrupted setup continues),
  every error branch of the progress window (mocked downloads), GPU
  detection (mocked nvidia-smi).
- Installer: scripted check — built `amanu-setup.exe` installs into a
  sandbox folder, config.json contains chosen options, uninstaller cleans
  the bundle (and asks about models/recordings).
- Manual passes on a clean Windows machine/VM: full install, install
  without Ollama, install with a network break.

## Standing decisions this design respects

- Single PyInstaller onedir bundle remains the build artifact; Inno wraps
  it, it does not replace `scripts/build_exe.py`.
- No new IPC, no new config formats: choices land in the existing
  `config.json` with existing keys plus `setup_complete`.
- Everything reuses `setup.py`'s `ensure_*` functions rather than duplicating
  download/install logic in a second place.
