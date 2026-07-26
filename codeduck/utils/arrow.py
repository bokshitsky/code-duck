"""Быстрая колоночная вставка строк в DuckDB через Arrow."""

from __future__ import annotations

from typing import Sequence, Tuple

import duckdb
import pyarrow as pa


def insert_arrow(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    columns: Sequence[Tuple[str, object]],
    rows: Sequence[Sequence[object]],
) -> None:
    """Собирает строки в Arrow-таблицу и вставляет одним ``INSERT ... SELECT``.

    DuckDB читает Arrow из памяти напрямую (zero-copy), минуя SQL-слой для каждой
    строки. ``columns`` — пары ``(имя, arrow-тип)`` в порядке колонок таблицы.
    """
    if not rows:
        return
    column_values = list(zip(*rows))
    arrow_array = getattr(pa, "array")
    arrow_table_factory = getattr(pa, "table")
    arrow_table = arrow_table_factory(
        {
            column_name: arrow_array(list(values), type=arrow_type)
            for (column_name, arrow_type), values in zip(columns, column_values)
        }
    )
    connection.register("_insert_view", arrow_table)
    try:
        connection.execute("INSERT INTO %s SELECT * FROM _insert_view" % table_name)
    finally:
        connection.unregister("_insert_view")
