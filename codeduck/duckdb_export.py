"""Экспорт модели Java-кода модулей в базу DuckDB.

Использует :mod:`codeduck.analyzer_java` для построения модели классов, методов,
наследования и аннотаций и раскладывает её по реляционным таблицам.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Optional, Sequence, Set, Tuple

import duckdb
import pyarrow as pa

from codeduck.analyzer_java import ClassModel, JavaAnalyzer
from codeduck.utils.duckdb import connect as connect_duckdb
from codeduck.utils.measure import log_duration

INSERT_BATCH_SIZE = 2000

SCHEMA_STATEMENTS = (
    "CREATE TABLE repos ("
    "  repo_id INTEGER PRIMARY KEY,"
    "  name TEXT NOT NULL UNIQUE"
    ")",
    "CREATE TABLE modules ("
    "  module_name TEXT PRIMARY KEY,"
    "  repo_id INTEGER NOT NULL REFERENCES repos(repo_id)"
    ")",
    "CREATE TABLE classes ("
    "  class_id INTEGER PRIMARY KEY,"
    "  class_name TEXT NOT NULL,"
    "  package_name TEXT NOT NULL,"
    "  fqn TEXT NOT NULL,"
    "  module_name TEXT NOT NULL REFERENCES modules(module_name),"
    "  source_type TEXT NOT NULL"
    ")",
    "CREATE TABLE methods ("
    "  method_id INTEGER PRIMARY KEY,"
    "  class_id INTEGER NOT NULL REFERENCES classes(class_id),"
    "  method_name TEXT NOT NULL,"
    "  params TEXT NOT NULL,"
    "  return_type TEXT"
    ")",
    "CREATE TABLE class_dependencies ("
    "  from_class_id INTEGER NOT NULL REFERENCES classes(class_id),"
    "  to_fqn TEXT NOT NULL,"
    "  to_class_id INTEGER,"
    "  to_module_name TEXT"
    ")",
    "CREATE TABLE class_supertypes ("
    "  class_id INTEGER NOT NULL REFERENCES classes(class_id),"
    "  super_fqn TEXT NOT NULL,"
    "  super_class_id INTEGER,"
    "  relation_type TEXT NOT NULL"
    ")",
    "CREATE TABLE class_annotations ("
    "  class_id INTEGER NOT NULL REFERENCES classes(class_id),"
    "  annotation_fqn TEXT NOT NULL"
    ")",
    "CREATE TABLE method_annotations ("
    "  method_id INTEGER NOT NULL REFERENCES methods(method_id),"
    "  annotation_fqn TEXT NOT NULL"
    ")",
)

COMMENT_STATEMENTS = (
    "COMMENT ON TABLE repos IS 'Репозиторий, из которого собрана модель (одна строка на запуск)'",
    "COMMENT ON COLUMN repos.repo_id IS 'Суррогатный идентификатор репозитория'",
    "COMMENT ON COLUMN repos.name IS 'Имя репозитория (по умолчанию имя каталога project-root)'",
    "COMMENT ON TABLE modules IS 'Целевые Maven-модули, переданные при запуске экспорта'",
    "COMMENT ON COLUMN modules.module_name IS 'Имя модуля относительно корня проекта; первичный ключ'",
    "COMMENT ON COLUMN modules.repo_id IS 'Ссылка на repos.repo_id'",
    "COMMENT ON TABLE classes IS 'Верхнеуровневые Java-типы (class/interface/enum/record/annotation) целевых модулей'",
    "COMMENT ON COLUMN classes.class_id IS 'Суррогатный идентификатор класса; на него ссылаются остальные таблицы'",
    "COMMENT ON COLUMN classes.class_name IS 'Простое имя типа без пакета, например UserService'",
    "COMMENT ON COLUMN classes.package_name IS 'Имя пакета без имени типа, например ru.hh.model'",
    "COMMENT ON COLUMN classes.fqn IS 'Полное имя типа package_name.class_name; может повторяться в prod и test'",
    "COMMENT ON COLUMN classes.module_name IS 'Модуль, в котором объявлен тип; ссылка на modules.module_name'",
    "COMMENT ON COLUMN classes.source_type IS 'Каталог исходников: prod (src/main/java) или test (src/test/java)'",
    "COMMENT ON TABLE methods IS 'Методы и конструкторы, объявленные непосредственно в теле класса'",
    "COMMENT ON COLUMN methods.method_id IS 'Суррогатный идентификатор метода'",
    "COMMENT ON COLUMN methods.class_id IS 'Класс-владелец метода; ссылка на classes.class_id'",
    "COMMENT ON COLUMN methods.method_name IS 'Имя метода; для конструктора совпадает с именем класса'",
    "COMMENT ON COLUMN methods.params IS 'Типы параметров через запятую в порядке объявления; varargs как Тип...; пустая строка при отсутствии параметров'",
    "COMMENT ON COLUMN methods.return_type IS 'Тип возвращаемого значения (void для процедур); NULL для конструктора'",
    "COMMENT ON TABLE class_dependencies IS 'Связь «класс зависит от класса» по использованным в коде типам'",
    "COMMENT ON COLUMN class_dependencies.from_class_id IS 'Зависящий класс; ссылка на classes.class_id'",
    "COMMENT ON COLUMN class_dependencies.to_fqn IS 'Полное имя класса, от которого есть зависимость'",
    "COMMENT ON COLUMN class_dependencies.to_class_id IS 'classes.class_id для to_fqn, если он однозначно определён среди classes; иначе NULL (внешний или неоднозначный класс)'",
    "COMMENT ON COLUMN class_dependencies.to_module_name IS 'Зарезервировано; сейчас всегда NULL'",
    "COMMENT ON TABLE class_supertypes IS 'Наследование: суперклассы и реализуемые интерфейсы'",
    "COMMENT ON COLUMN class_supertypes.class_id IS 'Класс-наследник; ссылка на classes.class_id'",
    "COMMENT ON COLUMN class_supertypes.super_fqn IS 'Полное имя суперкласса или интерфейса'",
    "COMMENT ON COLUMN class_supertypes.super_class_id IS 'classes.class_id для super_fqn, если он однозначно определён; иначе NULL'",
    "COMMENT ON COLUMN class_supertypes.relation_type IS 'Тип связи: extends (наследование класса/интерфейса) или implements (реализация интерфейса)'",
    "COMMENT ON TABLE class_annotations IS 'Аннотации, которыми помечена декларация класса'",
    "COMMENT ON COLUMN class_annotations.class_id IS 'Аннотированный класс; ссылка на classes.class_id'",
    "COMMENT ON COLUMN class_annotations.annotation_fqn IS 'Имя аннотации, разрешённое до FQN там, где это возможно'",
    "COMMENT ON TABLE method_annotations IS 'Аннотации, которыми помечена декларация метода'",
    "COMMENT ON COLUMN method_annotations.method_id IS 'Аннотированный метод; ссылка на methods.method_id'",
    "COMMENT ON COLUMN method_annotations.annotation_fqn IS 'Имя аннотации, разрешённое до FQN там, где это возможно'",
)

DROP_STATEMENTS = tuple(
    "DROP TABLE IF EXISTS %s" % table_name
    for table_name in (
        "method_annotations",
        "class_annotations",
        "class_supertypes",
        "class_dependencies",
        "methods",
        "classes",
        "modules",
        "repos",
    )
)


def write_database(
    connection: duckdb.DuckDBPyConnection,
    repo_name: str,
    module_names: Sequence[str],
    class_models: Sequence[ClassModel],
) -> None:
    """Создаёт схему и заполняет таблицы моделью классов."""
    with log_duration("Создание схемы и комментариев"):
        for statement in DROP_STATEMENTS:
            connection.execute(statement)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        for statement in COMMENT_STATEMENTS:
            connection.execute(statement)

    class_id_by_fqn: DefaultDict[str, Set[int]] = defaultdict(set)

    def unique_class_id(fqn: str) -> Optional[int]:
        candidates = class_id_by_fqn.get(fqn)
        return next(iter(candidates)) if candidates is not None and len(candidates) == 1 else None

    # Отдельно замеряем подготовку строк (Python) и саму вставку (DuckDB),
    # чтобы понять, где именно узкое место.
    with log_duration("Подготовка строк для вставки"):
        module_rows = [[module_name, 1] for module_name in sorted(set(module_names))]
        class_rows = []
        for class_id, model in enumerate(class_models, start=1):
            class_rows.append([class_id, model.simple_name, model.package_name, model.fqn, model.module_name, model.source_type])
            class_id_by_fqn[model.fqn].add(class_id)

        method_rows = []
        method_annotation_rows = []
        dependency_rows = []
        supertype_rows = []
        class_annotation_rows = []
        method_id = 0
        for class_id, model in enumerate(class_models, start=1):
            for method in model.methods:
                method_id += 1
                method_rows.append([method_id, class_id, method.name, method.params, method.return_type])
                for annotation_fqn in method.annotations:
                    method_annotation_rows.append([method_id, annotation_fqn])
            for dependency_fqn in model.dependencies:
                dependency_rows.append([class_id, dependency_fqn, unique_class_id(dependency_fqn), None])
            for super_fqn, relation_type in model.supertypes:
                supertype_rows.append([class_id, super_fqn, unique_class_id(super_fqn), relation_type])
            for annotation_fqn in model.annotations:
                class_annotation_rows.append([class_id, annotation_fqn])

    def insert_arrow(table: str, columns: Sequence[Tuple[str, pa.DataType]], rows: Sequence[Sequence[object]]) -> None:
        # Собираем данные в колоночную Arrow-таблицу и вставляем одним INSERT ... SELECT.
        # DuckDB читает Arrow из памяти напрямую (zero-copy), минуя SQL-слой для каждой строки.
        if not rows:
            return
        column_values = list(zip(*rows))
        arrow_table = pa.table(
            {
                column_name: pa.array(list(values), type=arrow_type)
                for (column_name, arrow_type), values in zip(columns, column_values)
            }
        )
        connection.register("_insert_view", arrow_table)
        try:
            connection.execute("INSERT INTO %s SELECT * FROM _insert_view" % table)
        finally:
            connection.unregister("_insert_view")

    text = pa.string()
    integer = pa.int32()
    with log_duration("Вставка данных в DuckDB"):
        insert_arrow("repos", [("repo_id", integer), ("name", text)], [[1, repo_name]])
        insert_arrow("modules", [("module_name", text), ("repo_id", integer)], module_rows)
        insert_arrow(
            "classes",
            [
                ("class_id", integer),
                ("class_name", text),
                ("package_name", text),
                ("fqn", text),
                ("module_name", text),
                ("source_type", text),
            ],
            class_rows,
        )
        insert_arrow(
            "methods",
            [
                ("method_id", integer),
                ("class_id", integer),
                ("method_name", text),
                ("params", text),
                ("return_type", text),
            ],
            method_rows,
        )
        insert_arrow("method_annotations", [("method_id", integer), ("annotation_fqn", text)], method_annotation_rows)
        insert_arrow(
            "class_dependencies",
            [
                ("from_class_id", integer),
                ("to_fqn", text),
                ("to_class_id", integer),
                ("to_module_name", text),
            ],
            dependency_rows,
        )
        insert_arrow(
            "class_supertypes",
            [
                ("class_id", integer),
                ("super_fqn", text),
                ("super_class_id", integer),
                ("relation_type", text),
            ],
            supertype_rows,
        )
        insert_arrow("class_annotations", [("class_id", integer), ("annotation_fqn", text)], class_annotation_rows)


def export_to_duckdb(
    project_root: Path,
    module_names: Sequence[str],
    output: Path,
    repo_name: str,
    force: bool = False,
) -> None:
    """Анализирует модули и пишет модель кода в файл базы DuckDB.

    Если ``force=True``, существующий файл базы будет удалён и создан заново.
    Иначе при существующем файле выбрасывается ошибка.
    """
    with log_duration("ИТОГО"):
        if output.exists():
            if not force:
                raise FileExistsError(f"Файл уже существует: {output}")
            output.unlink()
        analyzer = JavaAnalyzer(project_root, module_names)
        with log_duration("Анализ исходников (всего)"):
            class_models = analyzer.analyze_model()
        output.parent.mkdir(parents=True, exist_ok=True)
        with connect_duckdb(output) as connection:
            with log_duration("Запись в базу (всего)"):
                write_database(connection, repo_name, module_names, class_models)
