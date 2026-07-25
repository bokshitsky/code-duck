"""Командная строка :mod:`codeduck`.

Пример::

    uv run codeduck analyze java --maven --all \\
        --project-root ROOT --output model.duckdb --repo-name hh.ru
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Optional, Sequence

import click
import typer

from codeduck.analyzer import JavaDependencyAnalyzer
from codeduck.duckdb_export import export_to_duckdb


app = typer.Typer(
    help="Анализ исходного кода и выгрузка модели в DuckDB.",
    no_args_is_help=True,
    add_completion=False,
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


@analyze_app.command("java", help="Анализ Java-кода (Tree-sitter).")
def analyze_java(
    modules: Annotated[
        list[str],
        typer.Argument(
            help="Имена Maven-модулей относительно корня проекта (игнорируются при --all)."
        ),
    ] = [],
    maven: Annotated[
        bool,
        typer.Option(help="Maven-раскладка исходников (src/main/java, src/test/java)."),
    ] = False,
    all_modules: Annotated[
        bool,
        typer.Option("--all", help="Найти и проанализировать все вложенные Maven-модули project-root."),
    ] = False,
    project_root: Annotated[
        Optional[Path],
        typer.Option(help="Корень Maven-проекта (по умолчанию текущий каталог)."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("-o", "--output", help="Путь к создаваемому файлу базы DuckDB."),
    ] = ...,
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

    if not maven:
        typer.echo("Поддерживается только Maven-раскладка; укажите --maven.", err=True)
        raise typer.Exit(code=2)

    try:
        resolved_project_root = (project_root or Path.cwd()).resolve()
        resolved_repo_name = repo_name or resolved_project_root.name

        if all_modules:
            module_names = JavaDependencyAnalyzer.discover_maven_modules(resolved_project_root)
            if not module_names:
                typer.echo("В project-root не найдено ни одного Maven-модуля с pom.xml.", err=True)
                raise typer.Exit(code=2)
            logger.info("Найдено Maven-модулей: %d", len(module_names))
        else:
            module_names = tuple(modules)
            if not module_names:
                typer.echo("Укажите модули позиционно или используйте --all.", err=True)
                raise typer.Exit(code=2)

        export_to_duckdb(resolved_project_root, module_names, output, resolved_repo_name)
    except (OSError, ValueError) as error:
        typer.echo("Ошибка анализа: %s" % error, err=True)
        raise typer.Exit(code=2) from error


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Запустить CLI; аргумент нужен для программного вызова и тестов."""

    try:
        result = app(
            args=list(arguments) if arguments is not None else None,
            prog_name="codeduck",
            standalone_mode=False,
        )
    except typer.Exit as error:
        return error.exit_code
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
