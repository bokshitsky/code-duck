"""Быстрая колоночная вставка строк в DuckDB через Arrow."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa

if TYPE_CHECKING:
    from collections.abc import Sequence

    import duckdb


def insert_arrow(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: Sequence[tuple[str, object]],
    rows: Sequence[Sequence[object]],
) -> None:
    """
    Собирает строки в Arrow-таблицу и вставляет одним ``INSERT ... SELECT``.

    DuckDB читает Arrow из памяти напрямую (zero-copy), минуя SQL-слой для каждой
    строки. ``columns`` — пары ``(имя, arrow-тип)`` в порядке колонок таблицы.
    """
    if not rows:
        return
    column_values = list(zip(*rows, strict=False))
    arrow_array = pa.array  # type: ignore[attr-defined]
    arrow_table_factory = pa.table  # type: ignore[attr-defined]
    arrow_table = arrow_table_factory({
        column_name: arrow_array(list(values), type=arrow_type)
        for (column_name, arrow_type), values in zip(columns, column_values, strict=False)
    })
    connection.register('_insert_view', arrow_table)
    try:
        # table_name формируется кодом из описаний колонок, а не из пользовательского ввода.
        connection.execute(f'INSERT INTO {table_name} SELECT * FROM _insert_view')  # noqa: S608
    finally:
        connection.unregister('_insert_view')
