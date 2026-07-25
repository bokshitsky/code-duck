# codeduck

`codeduck` анализирует код и собирает информацию о нем в [DuckDB](https://duckdb.org/).

## Java

Команда `codeduck analyze java` парсит Maven-модули.
Извлекаются классы, методы (с типами параметров), зависимости, наследование и аннотации.
Для целевых модулей индексируются классы  как из `src/main/java` (`source_type = prod`), так и из `src/test/java` (`source_type = test`).

### Все вложенные Maven-модули (`--maven-all`)

С флагом `--maven-all` инструмент сам находит все вложенные Maven-модули под`--project-root` — модулем считается любой каталог с `pom.xml` на любой глубине.
Имя вложенного модуля — путь относительно корня, например `hh-fixture/server`.

```bash
uv run codeduck analyze java --maven-all \
  --project-root /path/to/hh.ru \
  --output model.duckdb \
  --repo-name hh.ru
```

### Явный список модулей

Вместо `--maven-all` можно перечислить модули позиционно:

```bash
uv run codeduck analyze java hh-core hh-utils webapp-common \
  --project-root /path/to/hh.ru \
  --output model.duckdb \
  --repo-name hh.ru
```

Если файл базы уже существует, он пересоздаётся. `--repo-name` по умолчанию —
имя каталога `--project-root`.

Имена в зависимостях, суперклассах и аннотациях резолвятся по явным и
wildcard-import'ам, классам из текущего package и уникальным простым именам в
проиндексированных исходниках. Поэтому связь находится и для класса без import,
если его можно однозначно определить по исходному коду.

## MCP-сервер (`codeduck mcp`)

Команда `codeduck mcp` поднимает локальный MCP-сервер (транспорт stdio, на базе
[FastMCP](https://github.com/jlowin/fastmcp)). Сервер запускается локально; путь
к файлу базы DuckDB передаётся параметром `database` самих инструментов, поэтому
один сервер обслуживает любую базу и открывает её в режиме только для чтения.

```bash
uv run codeduck mcp
```

Доступны два инструмента:

- `get_instructions(database)` — читает структуру базы прямо из DuckDB (таблицы,
  колонки, их типы и комментарии `COMMENT ON`) и возвращает описание. Полезно
  вызвать первым, чтобы понять, какие таблицы и поля доступны.
- `query(database, sql)` — выполняет SQL-запрос (только чтение) и возвращает
  `columns`, `rows` и `row_count`. Перед составлением запроса рекомендуется
  вызвать `get_instructions` для этой же базы.

## Схема

- `repos(repo_id, name)` — репозиторий (одна строка на запуск).
- `java_modules(module_name, repo_id)` — проанализированные модули; для вложенных
  модулей `module_name` — путь относительно корня (например, `hh-fixture/server`).
- `java_classes(module_name, source_type, class_id, class_name, package_name, fqn)` —
  верхнеуровневые классы; `class_name` — простое имя, `fqn` — полное.
  `source_type` ∈ {`prod`, `test`}.
- `java_methods(method_id, class_id, method_name, params, return_type)` — `params` —
  типы параметров через запятую (varargs как `Тип...`).
- `java_class_dependencies(from_class_id, relation_type, to_fqn)` — связи класса с
  другими типами по их `to_fqn`. `relation_type` ∈ {`annotated_by` (аннотация класса),
  `extends` (наследование), `implements` (реализация интерфейса), `uses` (использование
  типа в коде)}.
- `java_method_annotations(method_id, annotation_fqn)` — аннотации метода
  (имена разрешаются до FQN там, где это возможно).
