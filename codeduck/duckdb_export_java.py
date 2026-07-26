"""Экспорт модели Java-кода модулей в базу DuckDB.

Использует :mod:`codeduck.analyzer_java` для построения модели классов, методов,
наследования и аннотаций и раскладывает её по реляционным таблицам.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Sequence

import duckdb
import pyarrow as pa

from codeduck.analyzer_java import ClassModel, JavaAnalyzer
from codeduck.utils.arrow import insert_arrow
from codeduck.utils.duckdb import connect as connect_duckdb
from codeduck.utils.measure import log_duration

SCHEMA_STATEMENTS = (
    "CREATE TABLE repos ("
    "  repo_id INTEGER PRIMARY KEY,"
    "  name TEXT NOT NULL UNIQUE"
    ")",
    "CREATE TABLE java_modules ("
    "  module_name TEXT PRIMARY KEY,"
    "  repo_id INTEGER NOT NULL REFERENCES repos(repo_id)"
    ")",
    "CREATE TABLE java_classes ("
    "  module_name TEXT NOT NULL REFERENCES java_modules(module_name),"
    "  source_type TEXT NOT NULL,"
    "  class_id INTEGER PRIMARY KEY,"
    "  class_name TEXT NOT NULL,"
    "  package_name TEXT NOT NULL,"
    "  fqn TEXT NOT NULL"
    ")",
    "CREATE TABLE java_methods ("
    "  method_id INTEGER PRIMARY KEY,"
    "  class_id INTEGER NOT NULL REFERENCES java_classes(class_id),"
    "  class_fqn TEXT NOT NULL,"
    "  method_name TEXT NOT NULL,"
    "  params TEXT NOT NULL,"
    "  return_type TEXT"
    ")",
    "CREATE TABLE java_class_dependencies ("
    "  from_class_id INTEGER NOT NULL REFERENCES java_classes(class_id),"
    "  from_fqn TEXT NOT NULL,"
    "  relation_type TEXT NOT NULL,"
    "  to_fqn TEXT NOT NULL"
    ")",
    "CREATE TABLE java_method_annotations ("
    "  method_id INTEGER NOT NULL REFERENCES java_methods(method_id),"
    "  annotation_fqn TEXT NOT NULL"
    ")",
)

COMMENT_STATEMENTS = (
    "COMMENT ON TABLE repos IS 'Репозиторий, из которого собрана модель (одна строка на запуск)'",
    "COMMENT ON COLUMN repos.repo_id IS 'Суррогатный идентификатор репозитория'",
    "COMMENT ON COLUMN repos.name IS 'Имя репозитория (по умолчанию имя каталога project-root)'",
    "COMMENT ON TABLE java_modules IS 'Целевые Maven-модули, переданные при запуске экспорта'",
    "COMMENT ON COLUMN java_modules.module_name IS 'Имя модуля относительно корня проекта; первичный ключ'",
    "COMMENT ON COLUMN java_modules.repo_id IS 'Ссылка на repos.repo_id'",
    "COMMENT ON TABLE java_classes IS 'Верхнеуровневые Java-типы (class/interface/enum/record/annotation) целевых модулей'",
    "COMMENT ON COLUMN java_classes.module_name IS 'Модуль, в котором объявлен тип; ссылка на java_modules.module_name'",
    "COMMENT ON COLUMN java_classes.source_type IS 'Каталог исходников: prod (src/main/java) или test (src/test/java)'",
    "COMMENT ON COLUMN java_classes.class_id IS 'Суррогатный идентификатор класса; на него ссылаются остальные таблицы'",
    "COMMENT ON COLUMN java_classes.class_name IS 'Простое имя типа без пакета, например UserService'",
    "COMMENT ON COLUMN java_classes.package_name IS 'Имя пакета без имени типа, например ru.hh.model'",
    "COMMENT ON COLUMN java_classes.fqn IS 'Полное имя типа package_name.class_name; может повторяться в prod и test'",
    "COMMENT ON TABLE java_methods IS 'Методы и конструкторы, объявленные непосредственно в теле класса'",
    "COMMENT ON COLUMN java_methods.method_id IS 'Суррогатный идентификатор метода'",
    "COMMENT ON COLUMN java_methods.class_id IS 'Класс-владелец метода; ссылка на java_classes.class_id'",
    "COMMENT ON COLUMN java_methods.class_fqn IS 'Полное имя класса-владельца метода; денормализованная копия java_classes.fqn'",
    "COMMENT ON COLUMN java_methods.method_name IS 'Имя метода; для конструктора совпадает с именем класса'",
    "COMMENT ON COLUMN java_methods.params IS 'Типы параметров через запятую в порядке объявления; varargs как Тип...; пустая строка при отсутствии параметров'",
    "COMMENT ON COLUMN java_methods.return_type IS 'Тип возвращаемого значения (void для процедур); NULL для конструктора'",
    "COMMENT ON TABLE java_class_dependencies IS 'Связи класса с другими типами по их FQN: использование, наследование и аннотации'",
    "COMMENT ON COLUMN java_class_dependencies.from_class_id IS 'Класс-источник связи; ссылка на java_classes.class_id'",
    "COMMENT ON COLUMN java_class_dependencies.from_fqn IS 'Полное имя класса-источника связи; денормализованная копия java_classes.fqn'",
    "COMMENT ON COLUMN java_class_dependencies.relation_type IS 'Тип связи: annotated_by (аннотация класса), extends (наследование), implements (реализация интерфейса), uses (использование типа в коде)'",
    "COMMENT ON COLUMN java_class_dependencies.to_fqn IS 'Полное имя типа, с которым связан класс (аннотация, суперкласс, интерфейс или используемый тип)'",
    "COMMENT ON TABLE java_method_annotations IS 'Аннотации, которыми помечена декларация метода'",
    "COMMENT ON COLUMN java_method_annotations.method_id IS 'Аннотированный метод; ссылка на java_methods.method_id'",
    "COMMENT ON COLUMN java_method_annotations.annotation_fqn IS 'Имя аннотации, разрешённое до FQN там, где это возможно'",
)

DROP_STATEMENTS = tuple(
    "DROP TABLE IF EXISTS %s" % table_name
    for table_name in (
        "java_method_annotations",
        "java_class_dependencies",
        "java_methods",
        "java_classes",
        "java_modules",
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

    # Отдельно замеряем подготовку строк (Python) и саму вставку (DuckDB),
    # чтобы понять, где именно узкое место.
    with log_duration("Подготовка строк для вставки"):
        module_rows = [[module_name, 1] for module_name in sorted(set(module_names))]
        class_rows = []
        for class_id, model in enumerate(class_models, start=1):
            class_rows.append(
                [model.module_name, model.source_type, class_id, model.simple_name, model.package_name, model.fqn]
            )

        method_rows = []
        method_annotation_rows = []
        dependency_rows = []
        method_id = 0
        for class_id, model in enumerate(class_models, start=1):
            for method in model.methods:
                method_id += 1
                method_rows.append([method_id, class_id, model.fqn, method.name, method.params, method.return_type])
                for annotation_fqn in method.annotations:
                    method_annotation_rows.append([method_id, annotation_fqn])
            for annotation_fqn in model.annotations:
                dependency_rows.append([class_id, model.fqn, "annotated_by", annotation_fqn])
            for super_fqn, relation_type in model.supertypes:
                dependency_rows.append([class_id, model.fqn, relation_type, super_fqn])
            for dependency_fqn in model.dependencies:
                dependency_rows.append([class_id, model.fqn, "uses", dependency_fqn])

    insert = partial(insert_arrow, connection)

    text = pa.string()  # type: ignore[attr-defined]
    integer = pa.int32()  # type: ignore[attr-defined]
    with log_duration("Вставка данных в DuckDB"):
        insert("repos", [("repo_id", integer), ("name", text)], [[1, repo_name]])
        insert("java_modules", [("module_name", text), ("repo_id", integer)], module_rows)
        insert(
            "java_classes",
            [
                ("module_name", text),
                ("source_type", text),
                ("class_id", integer),
                ("class_name", text),
                ("package_name", text),
                ("fqn", text),
            ],
            class_rows,
        )
        insert(
            "java_methods",
            [
                ("method_id", integer),
                ("class_id", integer),
                ("class_fqn", text),
                ("method_name", text),
                ("params", text),
                ("return_type", text),
            ],
            method_rows,
        )
        insert(
            "java_method_annotations", [("method_id", integer), ("annotation_fqn", text)], method_annotation_rows
        )
        insert(
            "java_class_dependencies",
            [
                ("from_class_id", integer),
                ("from_fqn", text),
                ("relation_type", text),
                ("to_fqn", text),
            ],
            dependency_rows,
        )


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
