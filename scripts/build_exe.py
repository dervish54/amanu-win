"""Build a portable amanu.exe bundle with PyInstaller (onedir, GPU-enabled).

Run from the repo root with the virtualenv that has requirements.txt
installed:

    python scripts/build_exe.py
    python scripts/build_exe.py --venv-python .venv/Scripts/python.exe --out D:/build

Output: <out>/amanu/amanu.exe (+ _internal/). The bundle is portable:
on machines without an NVIDIA GPU, transcription falls back to CPU.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build(venv_python: Path, out: Path, name: str = "amanu") -> Path:
    cmd = [
        str(venv_python), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--name", name,
        "--distpath", str(out),
        "--workpath", str(out / "build"),
        "--specpath", str(out / "build"),
        "--paths", str(REPO_ROOT),
        "--collect-binaries", "ctranslate2",
        "--collect-binaries", "sounddevice",
        "--collect-binaries", "soundfile",
        "--collect-binaries", "pyaudiowpatch",
        "--collect-binaries", "nvidia.cublas",
        "--collect-binaries", "nvidia.cudnn",
        "--collect-submodules", "faster_whisper",
        "--collect-data", "faster_whisper",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "amanu_win",
        "--collect-submodules", "pycaw",
        "--collect-submodules", "comtypes",
        str(REPO_ROOT / "launcher.py"),
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd)
    exe = out / name / f"{name}.exe"
    print(f"OK -> {exe}")
    return exe


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--venv-python", default=sys.executable,
                   help="python of the venv that has requirements.txt installed "
                        "(default: the python running this script)")
    p.add_argument("--out", default=str(REPO_ROOT / "dist"),
                   help="output directory for the bundle (default: ./dist)")
    p.add_argument("--name", default="amanu")
    args = p.parse_args()
    build(Path(args.venv_python), Path(args.out), args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
