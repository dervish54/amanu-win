"""Static checks on the Inno script; behavioral proof is the manual install."""
import json
from pathlib import Path

ISS = Path(__file__).resolve().parent.parent / "installer" / "amanu.iss"


def test_iss_exists_with_required_sections():
    text = ISS.read_text(encoding="utf-8-sig")
    for section in ("[Setup]", "[Files]", "[Tasks]", "[Registry]", "[Code]"):
        assert section in text
    assert "PrivilegesRequired=lowest" in text
    assert "install-choices.json" in text


def test_choices_json_shape_written_by_iss():
    text = ISS.read_text(encoding="utf-8-sig")
    for key in ('"tier"', '"ollama"', '"recordings_dir"'):
        assert key in text
    for tier in ("accurate", "balanced", "compact"):
        assert tier in text
