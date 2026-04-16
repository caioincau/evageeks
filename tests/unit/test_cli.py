# tests/unit/test_cli.py
import subprocess
import sys


def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True, text=True,
        cwd="/Users/caio.incau/evageeks"
    )
    assert result.returncode == 0
    assert "fetch" in result.stdout
    assert "parse" in result.stdout
    assert "ingest" in result.stdout
    assert "serve" in result.stdout
