# codeduck

`codeduck` анализирует Java-код только по дереву разбора Tree-sitter (без `jdeps`,
Maven или компилятора) и раскладывает модель кода по реляционным таблицам DuckDB.

## Анализ Java в DuckDB

Команда `codeduck analyze java` парсит Maven-модули Tree-sitter-резолвером и пишет
модель кода в файл базы DuckDB. Извлекаются классы, методы (с типами параметров),
зависимости, наследование и аннотации. Для целевых модулей индексируются классы
как из `src/main/java` (`source_type = prod`), так и из `src/test/java`
(`source_type = test`).

### Все вложенные модули (`--all`)

С флагом `--all` инструмент сам находит все вложенные Maven-модули под
`--project-root` — модулем считается любой каталог с `pom.xml` на любой глубине.
Имя вложенного модуля — путь относительно корня, например `hh-fixture/server`.

```bash
uv run codeduck analyze java --maven --all \
  --project-root /path/to/hh.ru \
  --output model.duckdb \
  --repo-name hh.ru
```

### Явный список модулей

Вместо `--all` можно перечислить модули позиционно:

```bash
uv run codeduck analyze java --maven hh-core hh-utils webapp-common \
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

## Схема

- `repos(repo_id, name)` — репозиторий (одна строка на запуск).
- `modules(module_name, repo_id)` — проанализированные модули; для вложенных
  модулей `module_name` — путь относительно корня (например, `hh-fixture/server`).
- `classes(class_id, class_name, package_name, fqn, module_name, source_type)` —
  верхнеуровневые классы; `class_name` — простое имя, `fqn` — полное.
  `source_type` ∈ {`prod`, `test`}.
- `methods(method_id, class_id, method_name, params, return_type)` — `params` —
  типы параметров через запятую (varargs как `Тип...`).
- `class_dependencies(from_class_id, to_fqn, to_class_id, to_module_name)` —
  «класс зависит от класса». `to_class_id` заполнен, только если `to_fqn`
  однозначно соответствует одному классу в `classes` (иначе `NULL`).
- `class_supertypes(class_id, super_fqn, super_class_id, relation_type)` —
  наследование; `relation_type` ∈ {`extends`, `implements`}.
- `class_annotations(class_id, annotation_fqn)` и
  `method_annotations(method_id, annotation_fqn)` — аннотации класса и метода
  (имена разрешаются до FQN там, где это возможно).
