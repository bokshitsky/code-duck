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

## Python

Команда `codeduck analyze python` анализирует переданные каталоги-пакеты.
Каждый `-p/--package` трактуется как корневой пакет: имя каталога становится
верхним элементом dotted-имени модуля (`-p /src/myapp` → `myapp/sub/mod.py` =
`myapp.sub.mod`). Рекурсивно берутся все `.py`; `__init__.py` представляет сам
пакет.

Извлекаются пакеты, модули, классы (в т.ч. вложенные), функции и методы (с
параметрами, аннотацией возврата, декораторами, признаками метода/`async`),
импорты и вызовы. Для вызовов всегда сохраняется сырое выражение, а
`resolved_target` заполняется best-effort — по импортам, локальным определениям и
`self` — иначе остаётся `NULL`.

```bash
uv run codeduck analyze python \
  -p /path/to/myapp -p /path/to/otherpkg \
  --output model.duckdb \
  --repo-name my-repo
```

Можно указать несколько `-p`. Если файл базы уже существует, используйте `-f`,
чтобы пересоздать его. `--repo-name` (можно и `--repo_name`) по умолчанию — имя
первого каталога-пакета.

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
- `java_methods(method_id, class_id, class_fqn, method_name, params, return_type)` — `params` —
  типы параметров через запятую (varargs как `Тип...`). `class_fqn` — денормализованная
  копия `java_classes.fqn` класса-владельца.
- `java_class_dependencies(from_class_id, from_fqn, relation_type, to_fqn)` — связи класса с
  другими типами по их `to_fqn`. `from_fqn` — денормализованная копия `java_classes.fqn`
  класса-источника. `relation_type` ∈ {`annotated_by` (аннотация класса),
  `extends` (наследование), `implements` (реализация интерфейса), `uses` (использование
  типа в коде)}.
- `java_method_annotations(method_id, annotation_fqn)` — аннотации метода
  (имена разрешаются до FQN там, где это возможно).

Таблицы Python (`codeduck analyze python`):

- `python_packages(package_id, repo_id, name, path)` — пакеты (каталоги), включая
  namespace-предков; `name` dotted, `path` posix.
- `python_modules(module_id, package_id, repo_id, name, path)` — модули (`.py`);
  `__init__.py` представляет сам пакет.
- `python_imports(import_id, module_id, kind, imported_name, alias)` — импорты
  модуля; `kind` ∈ {`import`, `from`}, `alias` — связанное имя при `as` (иначе NULL).
- `python_classes(class_id, module_id, name, qualified_name, bases, decorators, lineno)` —
  классы любой вложенности; `bases`/`decorators` — сырой текст через запятую.
- `python_functions(function_id, module_id, class_id, name, qualified_name, params, returns, decorators, is_method, is_async, lineno)` —
  функции и методы; `class_id` — класс-владелец метода (NULL для функции модуля).
- `python_calls(call_id, module_id, caller_function_id, callee_expr, callee_name, resolved_target, lineno)` —
  вызовы; `caller_function_id` — обрамляющая функция (NULL для кода уровня модуля),
  `resolved_target` — best-effort FQN цели (NULL, если не определено однозначно).
