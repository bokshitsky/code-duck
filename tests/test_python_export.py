import textwrap
from pathlib import Path

import duckdb
import pytest

from codeduck import duckdb_export_python
from codeduck.analyzer_python import PythonAnalyzer
from codeduck.duckdb_export_python import export_to_duckdb, write_database

PYTHON_TABLES = (
    'repos',
    'python_packages',
    'python_modules',
    'python_imports',
    'python_classes',
    'python_functions',
    'python_calls',
)


def build_sample_package(root: Path) -> Path:
    package_dir = root / 'myapp'
    write_py(package_dir / '__init__.py', '')
    write_py(
        package_dir / 'repo.py',
        """
        class Repository:
            def get(self, x):
                return x
        """,
    )
    write_py(
        package_dir / 'service.py',
        """
        from myapp.repo import Repository

        class UserService:
            async def handle(self, user_id: int) -> str:
                return self.load(user_id)

            def load(self, user_id):
                return Repository().get(user_id)
        """,
    )
    return package_dir


def test_exports_packages_modules_classes_functions_and_calls(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)
    packages, modules = PythonAnalyzer([package_dir]).analyze()

    connection = duckdb.connect(':memory:')
    write_database(connection, 'demo-repo', packages, modules)

    assert connection.execute('SELECT name FROM repos').fetchall() == [('demo-repo',)]

    # Каждая наша таблица и каждая её колонка снабжены комментарием.
    columns_without_comment = connection.execute(
        'SELECT table_name, column_name FROM duckdb_columns() WHERE table_name IN ? AND comment IS NULL',
        [list(PYTHON_TABLES)],
    ).fetchall()
    assert columns_without_comment == []
    tables_without_comment = connection.execute(
        'SELECT table_name FROM duckdb_tables() WHERE table_name IN ? AND comment IS NULL',
        [list(PYTHON_TABLES)],
    ).fetchall()
    assert tables_without_comment == []

    assert connection.execute('SELECT name FROM python_packages').fetchall() == [('myapp',)]

    # Модуль ссылается на свой пакет.
    module_package = connection.execute(
        'SELECT p.name FROM python_modules m JOIN python_packages p ON m.package_id = p.package_id '
        "WHERE m.name = 'myapp.service'"
    ).fetchone()
    assert module_package == ('myapp',)

    handle = connection.execute(
        'SELECT params, returns, is_method, is_async FROM python_functions WHERE qualified_name = ?',
        ['myapp.service.UserService.handle'],
    ).fetchone()
    assert handle == ('self, user_id: int', 'str', True, True)

    # Метод ссылается на класс-владелец.
    owner = connection.execute(
        'SELECT c.qualified_name FROM python_functions f JOIN python_classes c ON f.class_id = c.class_id '
        "WHERE f.qualified_name = 'myapp.service.UserService.handle'"
    ).fetchone()
    assert owner == ('myapp.service.UserService',)

    # Вызов self.load резолвится и знает свою вызывающую функцию.
    self_call = connection.execute(
        'SELECT f.qualified_name, ca.resolved_target FROM python_calls ca '
        'JOIN python_functions f ON ca.caller_function_id = f.function_id '
        "WHERE ca.callee_expr = 'self.load'"
    ).fetchone()
    assert self_call == ('myapp.service.UserService.handle', 'myapp.service.UserService.load')

    repository_target = connection.execute(
        "SELECT resolved_target FROM python_calls WHERE callee_expr = 'Repository'"
    ).fetchone()
    assert repository_target == ('myapp.repo.Repository',)


def test_export_checks_existing_output_before_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / 'model.duckdb'
    output.write_text('existing', encoding='utf-8')

    def fail_if_analyzer_is_created(*args: object, **kwargs: object) -> object:
        msg = 'analyzer must not be created when output already exists'
        raise AssertionError(msg)

    monkeypatch.setattr(duckdb_export_python, 'PythonAnalyzer', fail_if_analyzer_is_created)

    with pytest.raises(FileExistsError, match='Файл уже существует:'):
        export_to_duckdb([tmp_path], output, 'demo-repo')


def write_py(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding='utf-8')
