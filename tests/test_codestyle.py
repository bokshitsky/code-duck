from pathlib import Path

from pystolint.api import check
from pystolint.tools import Tool

ROOT = Path(__file__).parent.parent
MODULES = ['codeduck', 'tests']


def test_codestyle() -> None:
    result = check(MODULES, local_toml_path_provided=f'{ROOT}/pyproject.toml', tools=[Tool.RUFF, Tool.MYPY])

    assert len(result.items) == 0, '\n'.join(str(item) for item in result.items)
    assert len(result.errors) == 0, '\n'.join(error for error in result.errors)
