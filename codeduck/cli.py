"""Командная строка :mod:`codeduck`.

Пример::

    uv run codeduck analyze java --maven-all \\
        --project-root ROOT --output model.duckdb --repo-name hh.ru
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Optional, Sequence

import typer

from codeduck.analyzer import JavaDependencyAnalyzer
from codeduck.duckdb_export import export_to_duckdb


app = typer.Typer(
    help="Анализ исходного кода и выгрузка модели в DuckDB.",
    no_args_is_help=True,
    add_completion=True,
    context_settings={'help_option_names': ['-h', '--help']},
)
"""Корневое приложение.

Новые независимые команды добавляются сюда через ``app.command`` или
``app.add_typer``. Языковые анализаторы живут в группе ``analyze``.
"""

analyze_app = typer.Typer(
    help="Проанализировать исходный код.",
    no_args_is_help=True,
)
app.add_typer(analyze_app, name="analyze")


def fail(message: str, code: int = 1) -> None:
    """Печатает пользовательскую ошибку красным цветом и завершает команду."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


@analyze_app.command("java", help="Анализ Java-кода (Tree-sitter).")
def analyze_java(
    modules: Annotated[
        list[str],
        typer.Argument(
            help="Имена Maven-модулей относительно корня проекта (игнорируются при --maven-all)."
        ),
    ] = [],
    maven_all: Annotated[
        bool,
        typer.Option("--maven-all", help="Найти и проанализировать все вложенные Maven-модули project-root."),
    ] = False,
    project_root: Annotated[
        Optional[Path],
        typer.Option(help="Корень Maven-проекта (по умолчанию текущий каталог)."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Путь к создаваемому файлу базы DuckDB."),
    ] = ...,
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Пересоздать файл базы, если он уже существует."),
    ] = False,
    repo_name: Annotated[
        Optional[str],
        typer.Option(help="Имя репозитория (по умолчанию имя каталога project-root)."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option(help="Уровень логирования (по умолчанию INFO; тайминги этапов — на INFO)."),
    ] = "INFO",
) -> None:
    """Выгрузить модель Java-модулей Maven в DuckDB."""

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("codeduck.cli")

    resolved_project_root = (project_root or Path.cwd()).resolve()
    resolved_repo_name = repo_name or resolved_project_root.name

    if maven_all:
        module_names = JavaDependencyAnalyzer.discover_maven_modules(resolved_project_root)
        if not module_names:
            fail("В project-root не найдено ни одного Maven-модуля с pom.xml.", code=2)
        logger.info("Найдено Maven-модулей: %d", len(module_names))
    else:
        module_names = tuple(modules)
        if not module_names:
            fail("Укажите модули позиционно или используйте --maven-all.", code=2)
    try:
        export_to_duckdb(
            resolved_project_root,
            module_names,
            output,
            resolved_repo_name,
            force=force,
        )
    except FileExistsError as error:
        fail(str(error))
    except OSError as error:
        fail(str(error))

def main(arguments: Sequence[str] | None = None) -> int:
    try:
        app(
            args=list(arguments) if arguments is not None else None,
            prog_name="codeduck",
            standalone_mode=False,
        )
    except typer.Exit as error:
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
