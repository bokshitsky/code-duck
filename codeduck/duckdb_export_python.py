"""
Экспорт модели Python-кода в базу DuckDB.

Использует :mod:`codeduck.analyzer_python` для построения модели пакетов, модулей,
классов, функций/методов, импортов и вызовов и раскладывает её по реляционным
таблицам ``python_*``.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pyarrow as pa

from codeduck.analyzer_python import ModuleModel, PackageModel, PythonAnalyzer
from codeduck.utils.arrow import insert_arrow
from codeduck.utils.duckdb import connect as connect_duckdb
from codeduck.utils.measure import log_duration

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import duckdb

SCHEMA_STATEMENTS = (
    'CREATE TABLE repos (  repo_id INTEGER PRIMARY KEY,  name TEXT NOT NULL UNIQUE)',
    ('CREATE TABLE python_packages ('
    '  package_id INTEGER PRIMARY KEY,'
    '  repo_id INTEGER NOT NULL REFERENCES repos(repo_id),'
    '  name TEXT NOT NULL,'
    '  path TEXT NOT NULL'
    ')'),
    ('CREATE TABLE python_modules ('
    '  module_id INTEGER PRIMARY KEY,'
    '  package_id INTEGER NOT NULL REFERENCES python_packages(package_id),'
    '  repo_id INTEGER NOT NULL REFERENCES repos(repo_id),'
    '  name TEXT NOT NULL,'
    '  path TEXT NOT NULL'
    ')'),
    ('CREATE TABLE python_imports ('
    '  import_id INTEGER PRIMARY KEY,'
    '  module_id INTEGER NOT NULL REFERENCES python_modules(module_id),'
    '  kind TEXT NOT NULL,'
    '  imported_name TEXT NOT NULL,'
    '  alias TEXT'
    ')'),
    ('CREATE TABLE python_classes ('
    '  class_id INTEGER PRIMARY KEY,'
    '  module_id INTEGER NOT NULL REFERENCES python_modules(module_id),'
    '  name TEXT NOT NULL,'
    '  qualified_name TEXT NOT NULL,'
    '  bases TEXT NOT NULL,'
    '  decorators TEXT NOT NULL,'
    '  lineno INTEGER NOT NULL'
    ')'),
    ('CREATE TABLE python_functions ('
    '  function_id INTEGER PRIMARY KEY,'
    '  module_id INTEGER NOT NULL REFERENCES python_modules(module_id),'
    '  class_id INTEGER REFERENCES python_classes(class_id),'
    '  name TEXT NOT NULL,'
    '  qualified_name TEXT NOT NULL,'
    '  params TEXT NOT NULL,'
    '  returns TEXT,'
    '  decorators TEXT NOT NULL,'
    '  is_method BOOLEAN NOT NULL,'
    '  is_async BOOLEAN NOT NULL,'
    '  lineno INTEGER NOT NULL'
    ')'),
    ('CREATE TABLE python_calls ('
    '  call_id INTEGER PRIMARY KEY,'
    '  module_id INTEGER NOT NULL REFERENCES python_modules(module_id),'
    '  caller_function_id INTEGER REFERENCES python_functions(function_id),'
    '  callee_expr TEXT NOT NULL,'
    '  callee_name TEXT,'
    '  resolved_target TEXT,'
    '  lineno INTEGER NOT NULL'
    ')'),
)

COMMENT_STATEMENTS = (
    "COMMENT ON TABLE repos IS 'Репозиторий, из которого собрана модель (одна строка на запуск)'",
    "COMMENT ON COLUMN repos.repo_id IS 'Суррогатный идентификатор репозитория'",
    "COMMENT ON COLUMN repos.name IS 'Имя репозитория (по умолчанию имя первого каталога-пакета)'",
    "COMMENT ON TABLE python_packages IS 'Пакеты (каталоги) целевых деревьев, включая namespace-предков'",
    "COMMENT ON COLUMN python_packages.package_id IS 'Суррогатный идентификатор пакета'",
    "COMMENT ON COLUMN python_packages.repo_id IS 'Ссылка на repos.repo_id'",
    "COMMENT ON COLUMN python_packages.name IS 'Dotted-имя пакета, например myapp.sub'",
    "COMMENT ON COLUMN python_packages.path IS 'Путь пакета в posix относительно родителя корневого каталога, например myapp/sub'",  # noqa: E501
    "COMMENT ON TABLE python_modules IS 'Модули (.py-файлы) целевых пакетов; __init__.py представляет сам пакет'",
    "COMMENT ON COLUMN python_modules.module_id IS 'Суррогатный идентификатор модуля; на него ссылаются остальные таблицы'",  # noqa: E501
    "COMMENT ON COLUMN python_modules.package_id IS 'Пакет-владелец (каталог файла); ссылка на python_packages.package_id'",  # noqa: E501
    "COMMENT ON COLUMN python_modules.repo_id IS 'Ссылка на repos.repo_id'",
    "COMMENT ON COLUMN python_modules.name IS 'Dotted-имя модуля, например myapp.sub.mod'",
    "COMMENT ON COLUMN python_modules.path IS 'Путь файла в posix относительно родителя корневого каталога, например myapp/sub/mod.py'",  # noqa: E501
    "COMMENT ON TABLE python_imports IS 'Импорты, встречающиеся в модуле (на любой глубине)'",
    "COMMENT ON COLUMN python_imports.import_id IS 'Суррогатный идентификатор импорта'",
    "COMMENT ON COLUMN python_imports.module_id IS 'Модуль с импортом; ссылка на python_modules.module_id'",
    "COMMENT ON COLUMN python_imports.kind IS 'Форма импорта: import (import a.b) или from (from a.b import c)'",
    "COMMENT ON COLUMN python_imports.imported_name IS 'Полное импортируемое имя; для from — module.name, для относительных начинается с точки'",  # noqa: E501
    "COMMENT ON COLUMN python_imports.alias IS 'Связанное имя при наличии as; NULL, если алиас не задан'",
    "COMMENT ON TABLE python_classes IS 'Объявления классов на любой глубине вложенности'",
    "COMMENT ON COLUMN python_classes.class_id IS 'Суррогатный идентификатор класса'",
    "COMMENT ON COLUMN python_classes.module_id IS 'Модуль объявления; ссылка на python_modules.module_id'",
    "COMMENT ON COLUMN python_classes.name IS 'Простое имя класса без квалификатора'",
    "COMMENT ON COLUMN python_classes.qualified_name IS 'Имя с учётом вложенности: module.Outer.Inner'",
    "COMMENT ON COLUMN python_classes.bases IS 'Базовые классы через запятую как в исходнике (сырой текст); пустая строка при отсутствии'",  # noqa: E501
    "COMMENT ON COLUMN python_classes.decorators IS 'Декораторы класса через запятую без ведущего @; пустая строка при отсутствии'",  # noqa: E501
    "COMMENT ON COLUMN python_classes.lineno IS 'Номер строки объявления (с 1)'",
    "COMMENT ON TABLE python_functions IS 'Функции и методы на любой глубине вложенности'",
    "COMMENT ON COLUMN python_functions.function_id IS 'Суррогатный идентификатор функции'",
    "COMMENT ON COLUMN python_functions.module_id IS 'Модуль объявления; ссылка на python_modules.module_id'",
    "COMMENT ON COLUMN python_functions.class_id IS 'Класс-владелец для метода; ссылка на python_classes.class_id; NULL для функции уровня модуля'",  # noqa: E501
    "COMMENT ON COLUMN python_functions.name IS 'Простое имя функции/метода'",
    "COMMENT ON COLUMN python_functions.qualified_name IS 'Имя с учётом вложенности: module.Class.method или module.func.nested'",  # noqa: E501
    "COMMENT ON COLUMN python_functions.params IS 'Параметры через запятую как в исходнике (с аннотациями и значениями по умолчанию, включая *args/**kwargs)'",  # noqa: E501
    "COMMENT ON COLUMN python_functions.returns IS 'Аннотация возвращаемого значения (текст после ->); NULL, если не указана'",  # noqa: E501
    "COMMENT ON COLUMN python_functions.decorators IS 'Декораторы через запятую без ведущего @; пустая строка при отсутствии'",  # noqa: E501
    "COMMENT ON COLUMN python_functions.is_method IS 'TRUE, если объявлена непосредственно в теле класса'",
    "COMMENT ON COLUMN python_functions.is_async IS 'TRUE для async def'",
    "COMMENT ON COLUMN python_functions.lineno IS 'Номер строки объявления (с 1)'",
    "COMMENT ON TABLE python_calls IS 'Вызовы внутри модулей: сырое выражение и best-effort резолв цели'",
    "COMMENT ON COLUMN python_calls.call_id IS 'Суррогатный идентификатор вызова'",
    "COMMENT ON COLUMN python_calls.module_id IS 'Модуль с вызовом; ссылка на python_modules.module_id'",
    "COMMENT ON COLUMN python_calls.caller_function_id IS 'Обрамляющая функция/метод; ссылка на python_functions.function_id; NULL для кода уровня модуля'",  # noqa: E501
    "COMMENT ON COLUMN python_calls.callee_expr IS 'Сырое выражение вызываемого (текст перед скобками), например self.repo.save'",  # noqa: E501
    "COMMENT ON COLUMN python_calls.callee_name IS 'Последний сегмент вызова (имя функции/атрибута); NULL, если не выделяется'",  # noqa: E501
    "COMMENT ON COLUMN python_calls.resolved_target IS 'Разрешённая цель (FQN) по импортам/локальным определениям/self; NULL, если однозначно не определено'",  # noqa: E501
    "COMMENT ON COLUMN python_calls.lineno IS 'Номер строки вызова (с 1)'",
)

DROP_STATEMENTS = tuple(
    f'DROP TABLE IF EXISTS {table_name}'
    for table_name in (
        'python_calls',
        'python_functions',
        'python_classes',
        'python_imports',
        'python_modules',
        'python_packages',
        'repos',
    )
)


def write_database(
    connection: duckdb.DuckDBPyConnection,
    repo_name: str,
    package_models: Sequence[PackageModel],
    module_models: Sequence[ModuleModel],
) -> None:
    """Создаёт схему и заполняет таблицы моделью Python-кода."""
    with log_duration('Создание схемы и комментариев'):
        for statement in DROP_STATEMENTS:
            connection.execute(statement)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        for statement in COMMENT_STATEMENTS:
            connection.execute(statement)

    with log_duration('Подготовка строк для вставки'):
        package_rows: list[list[object]] = []
        package_id_by_name: dict[str, int] = {}
        for package_id, package in enumerate(package_models, start=1):
            package_id_by_name[package.name] = package_id
            package_rows.append([package_id, 1, package.name, package.path])

        module_rows: list[list[object]] = []
        import_rows: list[list[object]] = []
        class_rows: list[list[object]] = []
        function_rows: list[list[object]] = []
        call_rows: list[list[object]] = []
        class_id = 0
        function_id = 0
        import_id = 0
        call_id = 0
        for module_id, module in enumerate(module_models, start=1):
            module_rows.append([module_id, package_id_by_name[module.package_name], 1, module.name, module.path])
            for import_model in module.imports:
                import_id += 1
                import_rows.append([
                    import_id,
                    module_id,
                    import_model.kind,
                    import_model.imported_name,
                    import_model.alias,
                ])

            class_id_by_qname: dict[str, int] = {}
            for class_model in module.classes:
                class_id += 1
                class_id_by_qname[class_model.qualified_name] = class_id
                class_rows.append([
                    class_id,
                    module_id,
                    class_model.name,
                    class_model.qualified_name,
                    ', '.join(class_model.bases),
                    ', '.join(class_model.decorators),
                    class_model.lineno,
                ])

            function_id_by_qname: dict[str, int] = {}
            for function_model in module.functions:
                function_id += 1
                function_id_by_qname[function_model.qualified_name] = function_id
                owner_class_id = (
                    class_id_by_qname.get(function_model.owner_class)
                    if function_model.owner_class is not None
                    else None
                )
                function_rows.append([
                    function_id,
                    module_id,
                    owner_class_id,
                    function_model.name,
                    function_model.qualified_name,
                    function_model.params,
                    function_model.returns,
                    ', '.join(function_model.decorators),
                    function_model.is_method,
                    function_model.is_async,
                    function_model.lineno,
                ])

            for call_model in module.calls:
                call_id += 1
                caller_id = function_id_by_qname.get(call_model.caller) if call_model.caller is not None else None
                call_rows.append([
                    call_id,
                    module_id,
                    caller_id,
                    call_model.callee_expr,
                    call_model.callee_name,
                    call_model.resolved_target,
                    call_model.lineno,
                ])

    insert = partial(insert_arrow, connection)
    text = pa.string()  # type: ignore[attr-defined]
    integer = pa.int32()  # type: ignore[attr-defined]
    boolean = pa.bool_()  # type: ignore[attr-defined]
    with log_duration('Вставка данных в DuckDB'):
        insert('repos', [('repo_id', integer), ('name', text)], [[1, repo_name]])
        insert(
            'python_packages',
            [('package_id', integer), ('repo_id', integer), ('name', text), ('path', text)],
            package_rows,
        )
        insert(
            'python_modules',
            [
                ('module_id', integer),
                ('package_id', integer),
                ('repo_id', integer),
                ('name', text),
                ('path', text),
            ],
            module_rows,
        )
        insert(
            'python_imports',
            [
                ('import_id', integer),
                ('module_id', integer),
                ('kind', text),
                ('imported_name', text),
                ('alias', text),
            ],
            import_rows,
        )
        insert(
            'python_classes',
            [
                ('class_id', integer),
                ('module_id', integer),
                ('name', text),
                ('qualified_name', text),
                ('bases', text),
                ('decorators', text),
                ('lineno', integer),
            ],
            class_rows,
        )
        insert(
            'python_functions',
            [
                ('function_id', integer),
                ('module_id', integer),
                ('class_id', integer),
                ('name', text),
                ('qualified_name', text),
                ('params', text),
                ('returns', text),
                ('decorators', text),
                ('is_method', boolean),
                ('is_async', boolean),
                ('lineno', integer),
            ],
            function_rows,
        )
        insert(
            'python_calls',
            [
                ('call_id', integer),
                ('module_id', integer),
                ('caller_function_id', integer),
                ('callee_expr', text),
                ('callee_name', text),
                ('resolved_target', text),
                ('lineno', integer),
            ],
            call_rows,
        )


def export_to_duckdb(
    package_dirs: Sequence[Path],
    output: Path,
    repo_name: str,
    force: bool = False,
) -> None:
    """
    Анализирует Python-пакеты и пишет модель кода в файл базы DuckDB.

    Если ``force=True``, существующий файл базы будет удалён и создан заново.
    Иначе при существующем файле выбрасывается ошибка.

    Raises:
        FileExistsError: Если файл базы уже существует и ``force=False``.

    """
    with log_duration('ИТОГО'):
        if output.exists():
            if not force:
                raise FileExistsError(f'Файл уже существует: {output}')
            output.unlink()
        analyzer = PythonAnalyzer(package_dirs)
        with log_duration('Анализ исходников (всего)'):
            package_models, module_models = analyzer.analyze()
        output.parent.mkdir(parents=True, exist_ok=True)
        with connect_duckdb(output) as connection, log_duration('Запись в базу (всего)'):
            write_database(connection, repo_name, package_models, module_models)


# Псевдонимы под стиль java-модуля, чтобы имена не пересекались при импорте.
write_python_database = write_database
export_python_to_duckdb = export_to_duckdb
