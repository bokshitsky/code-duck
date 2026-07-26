import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MODULES = ['codeduck', 'tests']


def _run(tool: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', tool, *(['check'] if tool == 'ruff' else []), *MODULES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_codestyle() -> None:
    ruff = _run('ruff')
    assert ruff.returncode == 0, ruff.stdout + ruff.stderr

    mypy = _run('mypy')
    assert mypy.returncode == 0, mypy.stdout + mypy.stderr
