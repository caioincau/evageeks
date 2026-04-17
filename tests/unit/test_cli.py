# tests/unit/test_cli.py
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT)
    )
    assert result.returncode == 0
    assert "fetch" in result.stdout
    assert "parse" in result.stdout
    assert "ingest" in result.stdout
    assert "serve" in result.stdout
