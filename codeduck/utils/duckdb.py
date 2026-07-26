"""Утилиты для работы с DuckDB."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def connect(path: str | Path, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """
    Открывает DuckDB-соединение и гарантированно закрывает его.

    Yields:
        Открытое соединение DuckDB.

    """
    connection = duckdb.connect(str(path), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()
