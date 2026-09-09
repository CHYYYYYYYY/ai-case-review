from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dgx_api_start_script_loads_untracked_environment_file() -> None:
    script = (ROOT / "scripts" / "start_api_conda.sh").read_text(encoding="utf-8")

    assert "source .env" in script
    assert "set -a" in script
