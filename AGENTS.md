# AGENTS.md

## Проект

`codeduck` — пакет с CLI для анализа исходного кода и экспорта модели в DuckDB.

- Основной код: `codeduck/`
- Тесты: `tests/`
- CLI: `codeduck/cli.py`
- Экспорт DuckDB: `codeduck/duckdb_export_java.py`, `codeduck/duckdb_export_python.py`
- Зависимости и настройки: `pyproject.toml`, `uv.lock`

## Разработка

Используй `uv` и запускай команды из корня репозитория:

```bash
uv sync
uv run pytest
uv run codeduck --help
```

Перед завершением изменений запускай `uv run pytest`; тесты стиля проверяют `codeduck/` и `tests/` через настройки `pyproject.toml`.

## Правила

- Не читай содержимое `dist/` и `__pycache__/`.
- Сохраняй совместимость CLI и описанной в `README.md` схемы DuckDB.
- Для новой или изменённой логики добавляй/обновляй тесты в `tests/`.
- Не редактируй вручную `uv.lock` и содержимое кэшей; не меняй артефакты в `dist/`.
