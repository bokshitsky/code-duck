from pathlib import Path

import duckdb
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from codeduck.mcp_server import build_server, fetch_instructions, run_query


def _sample_database(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute('CREATE TABLE items (item_id INTEGER, title TEXT)')
        connection.execute("COMMENT ON TABLE items IS 'Демонстрационные элементы'")
        connection.execute("COMMENT ON COLUMN items.item_id IS 'Идентификатор элемента'")
        connection.execute("COMMENT ON COLUMN items.title IS 'Название элемента'")
        connection.execute("INSERT INTO items VALUES (1, 'first'), (2, 'second')")


def test_fetch_instructions_reads_comments_from_database(tmp_path: Path) -> None:
    database = tmp_path / 'model.duckdb'
    _sample_database(database)

    with duckdb.connect(str(database), read_only=True) as connection:
        instructions = fetch_instructions(connection)

    assert 'Таблица items — Демонстрационные элементы' in instructions
    assert 'item_id (INTEGER) — Идентификатор элемента' in instructions
    assert 'title (VARCHAR) — Название элемента' in instructions


def test_run_query_returns_columns_and_rows(tmp_path: Path) -> None:
    database = tmp_path / 'model.duckdb'
    _sample_database(database)

    with duckdb.connect(str(database), read_only=True) as connection:
        result = run_query(connection, 'SELECT item_id, title FROM items ORDER BY item_id')

    assert result == {
        'columns': ['item_id', 'title'],
        'rows': [[1, 'first'], [2, 'second']],
        'row_count': 2,
    }


async def test_tools_take_database_as_a_parameter(tmp_path: Path) -> None:
    database = tmp_path / 'model.duckdb'
    _sample_database(database)

    async with Client(build_server()) as client:
        instructions = await client.call_tool('get_instructions', {'database': str(database)})
        result = await client.call_tool(
            'query',
            {'database': str(database), 'sql': 'SELECT item_id FROM items ORDER BY item_id'},
        )

    assert 'Таблица items — Демонстрационные элементы' in instructions.data
    assert result.data == {'columns': ['item_id'], 'rows': [[1], [2]], 'row_count': 2}


async def test_query_tool_hints_get_instructions_in_its_description() -> None:
    server = build_server()

    query_tool = await server.get_tool('query')
    get_instructions_tool = await server.get_tool('get_instructions')

    assert query_tool is not None
    assert get_instructions_tool is not None
    assert 'get_instructions' in (query_tool.description or '')


async def test_tool_reports_missing_database(tmp_path: Path) -> None:
    async with Client(build_server()) as client:
        with pytest.raises(ToolError, match='Файл базы не найден'):
            await client.call_tool('get_instructions', {'database': str(tmp_path / 'missing.duckdb')})
