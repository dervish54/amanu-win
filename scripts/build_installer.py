"""Build amanu-setup.exe: PyInstaller bundle wrapped by Inno Setup.

Run from the repo root with the virtualenv that has requirements.txt:

    python scripts/build_installer.py
    python scripts/build_installer.py --venv-python .venv/Scripts/python.exe --out D:/build

Requires Inno Setup 6 (`winget install JRSoftware.InnoSetup`).
Output: <out>/amanu-setup.exe (plus the raw <out>/amanu/ bundle).
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from build_exe import build  # noqa: E402

def find_iscc() -> str:
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc is None:
        candidates = [
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path.home() / r"AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        ]
        iscc = next((str(c) for c in candidates if c.exists()), None)
    if iscc is None:
        raise SystemExit("Inno Setup 6 not found: winget install JRSoftware.InnoSetup")
    return iscc



def build_installer(venv_python: Path, out: Path) -> Path:
    bundle = build(venv_python, out)  # <out>/amanu/amanu.exe
    iss = REPO_ROOT / "installer" / "amanu.iss"
    subprocess.check_call([
        find_iscc(),
        f"/DBundleDir={bundle.parent}",
        f"/DOutDir={out}",
        str(iss)])
    setup_exe = out / "amanu-setup.exe"
    print(f"OK -> {setup_exe}")
    return setup_exe


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--venv-python", default=sys.executable)
    p.add_argument("--out", default=str(REPO_ROOT / "dist"))
    args = p.parse_args()
    build_installer(Path(args.venv_python), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
