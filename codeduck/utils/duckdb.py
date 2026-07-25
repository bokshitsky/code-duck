"""Утилиты для работы с DuckDB."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


@contextmanager
def connect(path: str | Path, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Открывает DuckDB-соединение и гарантированно закрывает его."""
    connection = duckdb.connect(str(path), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()
