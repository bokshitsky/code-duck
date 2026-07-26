r"""
Командная строка :mod:`codeduck`.

Пример::

    uv run codeduck analyze java --maven-all \\
        --project-root ROOT --output model.duckdb --repo-name hh.ru
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import typer

from codeduck.analyzer_java import JavaAnalyzer
from codeduck.duckdb_export_java import export_java_to_duckdb
from codeduck.duckdb_export_python import export_python_to_duckdb

if TYPE_CHECKING:
    from collections.abc import Sequence

app = typer.Typer(
    help='Анализ исходного кода и выгрузка модели в DuckDB.',
    no_args_is_help=True,
    add_completion=True,
    context_settings={'help_option_names': ['-h', '--help']},
)
"""Корневое приложение.

Новые независимые команды добавляются сюда через ``app.command`` или
``app.add_typer``. Языковые анализаторы живут в группе ``analyze``.
"""

analyze_app = typer.Typer(
    help='Проанализировать исходный код.',
    no_args_is_help=True,
)
app.add_typer(analyze_app, name='analyze')


def fail(message: str, code: int = 1) -> None:
    """
    Печатает пользовательскую ошибку красным цветом и завершает команду.

    Raises:
        typer.Exit: Всегда, чтобы прервать выполнение команды указанным кодом.

    """
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


@analyze_app.command('java', help='Анализ Java-кода.')
def analyze_java(
    modules: Annotated[
        list[str] | None,
        typer.Argument(help='Имена Maven-модулей относительно корня проекта (игнорируются при --maven-all).'),
    ] = None,
    maven_all: Annotated[
        bool,
        typer.Option('--maven-all', help='Найти и проанализировать все вложенные Maven-модули project-root.'),
    ] = False,
    project_root: Annotated[
        Path | None,
        typer.Option(help='Корень Maven-проекта (по умолчанию текущий каталог).'),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option('-o', '--output', help='Путь к создаваемому файлу базы DuckDB.'),
    ] = None,
    force: Annotated[
        bool,
        typer.Option('-f', '--force', help='Пересоздать файл базы, если он уже существует.'),
    ] = False,
    repo_name: Annotated[
        str | None,
        typer.Option(help='Имя репозитория (по умолчанию имя каталога project-root).'),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option(help='Уровень логирования (по умолчанию INFO; тайминги этапов — на INFO).'),
    ] = 'INFO',
) -> None:
    """Выгрузить модель Java-модулей Maven в DuckDB."""
    if modules is None:
        modules = []
    logging.basicConfig(
        level=log_level.upper(),
        format='%(asctime)s %(levelname)s %(message)s',
        stream=sys.stderr,
    )
    logger = logging.getLogger('codeduck.cli')

    resolved_project_root = (project_root or Path.cwd()).resolve()
    resolved_repo_name = repo_name or resolved_project_root.name

    # Единственное место, где обнаруживаются Maven-модули: при --maven-all берём
    # все модули проекта, иначе анализируем только переданные.
    if maven_all:
        module_names = JavaAnalyzer.discover_maven_modules(resolved_project_root)
        if not module_names:
            fail('В project-root не найдено ни одного Maven-модуля с pom.xml.', code=2)
        logger.info('Найдено Maven-модулей: %d', len(module_names))
    else:
        module_names = tuple(modules)
        if not module_names:
            fail('Укажите модули позиционно или используйте --maven-all.', code=2)
    if output is None:
        fail('Укажите --output.', code=2)
    resolved_output = cast('Path', output)
    try:
        export_java_to_duckdb(
            resolved_project_root,
            module_names,
            resolved_output,
            resolved_repo_name,
            force=force,
        )
    except FileExistsError as error:
        fail(str(error))
    except OSError as error:
        fail(str(error))


@analyze_app.command('python', help='Анализ Python-кода.')
def analyze_python(
    packages: Annotated[
        list[Path] | None,
        typer.Option(
            '-p',
            '--package',
            help='Каталог-пакет для анализа; имя каталога становится корневым пакетом. Можно указать несколько раз.',
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option('-o', '--output', help='Путь к создаваемому файлу базы DuckDB.'),
    ] = None,
    force: Annotated[
        bool,
        typer.Option('-f', '--force', help='Пересоздать файл базы, если он уже существует.'),
    ] = False,
    repo_name: Annotated[
        str | None,
        typer.Option('--repo-name', '--repo_name', help='Имя репозитория (по умолчанию имя первого каталога-пакета).'),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option(help='Уровень логирования (по умолчанию INFO; тайминги этапов — на INFO).'),
    ] = 'INFO',
) -> None:
    """Выгрузить модель Python-пакетов в DuckDB."""
    if packages is None:
        packages = []
    logging.basicConfig(
        level=log_level.upper(),
        format='%(asctime)s %(levelname)s %(message)s',
        stream=sys.stderr,
    )

    if not packages:
        fail('Укажите хотя бы один каталог-пакет через -p/--package.', code=2)
    resolved_packages = [package.resolve() for package in packages]
    for package in resolved_packages:
        if not package.is_dir():
            fail(f'Каталог-пакет не найден или не является каталогом: {package}', code=2)
    if output is None:
        fail('Укажите --output.', code=2)
    resolved_output = cast('Path', output)
    resolved_repo_name = repo_name or resolved_packages[0].name

    try:
        export_python_to_duckdb(
            resolved_packages,
            resolved_output,
            resolved_repo_name,
            force=force,
        )
    except FileExistsError as error:
        fail(str(error))
    except OSError as error:
        fail(str(error))


@app.command('mcp', help='Запустить локальный MCP-сервер (stdio) над базами DuckDB.')
def mcp() -> None:
    """
    Поднять локальный MCP-сервер с инструментами query и get_instructions.

    Путь к файлу базы передаётся параметром самих инструментов, поэтому один
    сервер обслуживает любую базу.
    """
    # Ленивый импорт: не тянем fastmcp, пока не вызвана команда mcp.
    from codeduck.mcp_server import run_server  # noqa: PLC0415

    run_server()


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        result = app(
            args=list(arguments) if arguments is not None else None,
            prog_name='codeduck',
            standalone_mode=False,
        )
    except typer.Exit as error:
        return error.exit_code
    # При standalone_mode=False click возвращает код выхода вместо sys.exit.
    return result if isinstance(result, int) else 0


if __name__ == '__main__':
    raise SystemExit(main())
