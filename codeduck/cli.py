"""Единая точка входа codeduck: анализ кода и выгрузка модели в DuckDB.

Пример:

    uv run codeduck analyze java --maven --all \\
        --project-root ROOT --output model.duckdb --repo-name hh.ru
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from codeduck.analyzer import JavaDependencyAnalyzer
from codeduck.duckdb_export import export_to_duckdb


def _analyze_java(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("codeduck.cli")

    if not args.maven:
        print("Поддерживается только Maven-раскладка; укажите --maven.", file=sys.stderr)
        return 2

    try:
        project_root = args.project_root.resolve()
        repo_name = args.repo_name or project_root.name

        if args.all:
            module_names = JavaDependencyAnalyzer.discover_maven_modules(project_root)
            if not module_names:
                print("В project-root не найдено ни одного Maven-модуля с src/main/java.", file=sys.stderr)
                return 2
            logger.info("Найдено Maven-модулей: %d", len(module_names))
        else:
            module_names = tuple(args.modules)
            if not module_names:
                print("Укажите модули позиционно или используйте --all.", file=sys.stderr)
                return 2

        export_to_duckdb(project_root, module_names, args.output, repo_name)
    except (OSError, ValueError) as error:
        print("Ошибка анализа: %s" % error, file=sys.stderr)
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeduck",
        description="Анализ исходного кода и выгрузка модели в DuckDB.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser("analyze", help="Проанализировать исходный код")
    languages = analyze.add_subparsers(dest="language", required=True)

    java = languages.add_parser("java", help="Анализ Java-кода (Tree-sitter)")
    java.add_argument(
        "modules",
        nargs="*",
        help="Имена Maven-модулей относительно корня проекта (игнорируются при --all)",
    )
    java.add_argument(
        "--maven",
        action="store_true",
        help="Maven-раскладка исходников (src/main/java, src/test/java)",
    )
    java.add_argument(
        "--all",
        action="store_true",
        help="Найти и проанализировать все вложенные Maven-модули project-root",
    )
    java.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Корень Maven-проекта (по умолчанию текущий каталог)",
    )
    java.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Путь к создаваемому файлу базы DuckDB",
    )
    java.add_argument(
        "--repo-name",
        default=None,
        help="Имя репозитория (по умолчанию имя каталога project-root)",
    )
    java.add_argument(
        "--log-level",
        default="INFO",
        help="Уровень логирования (по умолчанию INFO; тайминги этапов — на INFO)",
    )
    java.set_defaults(handler=_analyze_java)
    return parser


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
