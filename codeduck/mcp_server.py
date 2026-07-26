"""
Локальный MCP-сервер над базами DuckDB, собранными :mod:`codeduck`.

Поднимает stdio-сервер :mod:`fastmcp` с двумя инструментами. Путь к файлу базы
передаётся параметром каждого инструмента, поэтому один сервер обслуживает любую
базу:

``get_instructions``
    Читает структуру базы напрямую из DuckDB (таблицы, колонки, типы и
    комментарии ``COMMENT ON``) и возвращает её описанием.
``query``
    Выполняет произвольный SQL-запрос к базе в режиме только для чтения и
    возвращает колонки и строки результата.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from codeduck.utils.duckdb import connect

if TYPE_CHECKING:
    from collections.abc import Iterator

    import duckdb


@contextmanager
def open_database(database: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """
    Открывает базу только для чтения, понятно сообщая об отсутствии файла.

    Yields:
        Открытое соединение с базой в режиме только для чтения.

    Raises:
        ValueError: Если файл базы не существует.

    """
    if not Path(database).exists():
        raise ValueError(f'Файл базы не найден: {database}')
    with connect(database, read_only=True) as connection:
        yield connection


def fetch_instructions(connection: duckdb.DuckDBPyConnection) -> str:
    """
    Собирает описание структуры базы из системных представлений DuckDB.

    Использует ``duckdb_tables()`` и ``duckdb_columns()``, где хранятся заданные
    через ``COMMENT ON`` описания таблиц и колонок.

    Returns:
        Текстовое описание таблиц, колонок, их типов и комментариев.

    """
    table_rows = connection.execute(
        'SELECT table_name, comment FROM duckdb_tables() WHERE NOT internal ORDER BY table_name'
    ).fetchall()

    column_rows = connection.execute(
        'SELECT table_name, column_name, data_type, comment FROM duckdb_columns() '
        'WHERE NOT internal ORDER BY table_name, column_index'
    ).fetchall()

    if not table_rows:
        return 'База не содержит таблиц.'

    columns_by_table: dict[str, list[tuple[str, str, str | None]]] = {}
    for table_name, column_name, data_type, comment in column_rows:
        columns_by_table.setdefault(table_name, []).append((column_name, data_type, comment))

    lines: list[str] = ['Структура базы DuckDB.', '']
    for table_name, table_comment in table_rows:
        header = f'Таблица {table_name}'
        if table_comment:
            header += f' — {table_comment}'
        lines.append(header)
        for column_name, data_type, comment in columns_by_table.get(table_name, []):
            description = f' — {comment}' if comment else ''
            lines.append(f'  - {column_name} ({data_type}){description}')
        lines.append('')

    return '\n'.join(lines).rstrip()


def run_query(connection: duckdb.DuckDBPyConnection, sql: str) -> dict[str, object]:
    """
    Выполняет SQL-запрос и возвращает колонки и строки результата.

    Returns:
        Словарь с ключами ``columns``, ``rows`` и ``row_count``.

    """
    cursor = connection.execute(sql)
    columns = [description[0] for description in cursor.description] if cursor.description else []
    rows = [list(row) for row in cursor.fetchall()]
    return {'columns': columns, 'rows': rows, 'row_count': len(rows)}


def build_server() -> FastMCP[None]:
    """
    Создаёт MCP-сервер; путь к базе передаётся параметром каждого инструмента.

    Returns:
        Настроенный экземпляр :class:`FastMCP` с зарегистрированными инструментами.

    """
    server: FastMCP[None] = FastMCP('codeduck')

    @server.tool
    def get_instructions(database: str) -> str:
        """
        Вернуть структуру базы: список таблиц, колонок, их типы и описания.

        ``database`` — путь к файлу базы DuckDB, собранной командой
        ``codeduck analyze``.

        Данные читаются прямо из базы (комментарии ``COMMENT ON``), поэтому
        всегда отражают актуальную схему. Вызывайте этот инструмент первым,
        чтобы понять, какие таблицы и поля доступны, прежде чем писать запрос.

        Returns:
            Текстовое описание структуры базы.

        """
        with open_database(database) as connection:
            return fetch_instructions(connection)

    @server.tool
    def query(database: str, sql: str) -> dict[str, object]:
        """
        Выполнить SQL-запрос (только чтение) к базе и вернуть результат.

        ``database`` — путь к файлу базы DuckDB; ``sql`` — текст запроса.

        Перед составлением запроса лучше сначала вызвать ``get_instructions``
        для этой же базы, чтобы узнать доступные таблицы, поля и их назначение.

        Возвращает объект с ключами ``columns`` (имена колонок), ``rows``
        (строки результата) и ``row_count`` (число строк).

        Returns:
            Объект с ключами ``columns``, ``rows`` и ``row_count``.

        """
        with open_database(database) as connection:
            return run_query(connection, sql)

    return server


def run_server() -> None:
    """Запускает локальный stdio MCP-сервер."""
    build_server().run()
