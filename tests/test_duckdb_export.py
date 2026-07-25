from pathlib import Path

import duckdb
import pytest

from codeduck import duckdb_export
from codeduck.analyzer_java import JavaAnalyzer
from codeduck.duckdb_export import export_to_duckdb, write_database


def test_exports_classes_methods_and_relations(tmp_path: Path) -> None:
    project_root = tmp_path
    write_java(
        project_root,
        "module-a",
        "main",
        "example.main.UserService",
        """
        package example.main;
        import example.main.Repository;
        import org.springframework.stereotype.Service;
        @Service
        public class UserService extends BaseService implements Api {
            private Repository repository;
            public String handle(int id, java.util.List<String> names) { return null; }
            void internal() {}
        }
        """,
    )
    write_java(project_root, "module-a", "main", "example.main.BaseService", "package example.main; class BaseService {}")
    write_java(project_root, "module-a", "main", "example.main.Api", "package example.main; public interface Api {}")
    write_java(project_root, "module-a", "main", "example.main.Repository", "package example.main; public class Repository {}")
    write_java(
        project_root,
        "module-a",
        "test",
        "example.main.ServiceTest",
        """
        package example.main;
        import org.junit.Test;
        public class ServiceTest {
            @Test public void handles() {}
        }
        """,
    )

    models = list(JavaAnalyzer(project_root, ["module-a"]).analyze_model())
    connection = duckdb.connect(":memory:")
    write_database(connection, "demo-repo", ["module-a"], models)

    assert connection.execute("SELECT name FROM repos").fetchall() == [("demo-repo",)]

    our_tables = (
        "repos",
        "modules",
        "classes",
        "methods",
        "class_dependencies",
        "class_supertypes",
        "class_annotations",
        "method_annotations",
    )
    columns_without_comment = connection.execute(
        "SELECT table_name, column_name FROM duckdb_columns() "
        "WHERE table_name IN ? AND comment IS NULL",
        [list(our_tables)],
    ).fetchall()
    assert columns_without_comment == []
    tables_without_comment = connection.execute(
        "SELECT table_name FROM duckdb_tables() WHERE table_name IN ? AND comment IS NULL",
        [list(our_tables)],
    ).fetchall()
    assert tables_without_comment == []

    assert connection.execute("SELECT module_name FROM modules").fetchall() == [("module-a",)]

    source_types = connection.execute(
        "SELECT class_name, source_type FROM classes ORDER BY class_name"
    ).fetchall()
    assert ("UserService", "prod") in source_types
    assert ("ServiceTest", "test") in source_types

    handle_params = connection.execute(
        "SELECT params, return_type FROM methods m JOIN classes c ON m.class_id = c.class_id "
        "WHERE c.class_name = 'UserService' AND m.method_name = 'handle'"
    ).fetchone()
    assert handle_params == ("int, java.util.List<String>", "String")

    supertypes = connection.execute(
        "SELECT super_fqn, relation_type FROM class_supertypes s JOIN classes c ON s.class_id = c.class_id "
        "WHERE c.class_name = 'UserService' ORDER BY relation_type"
    ).fetchall()
    assert supertypes == [("example.main.BaseService", "extends"), ("example.main.Api", "implements")]

    dependency = connection.execute(
        "SELECT to_fqn, to_class_id IS NOT NULL FROM class_dependencies d JOIN classes c ON d.from_class_id = c.class_id "
        "WHERE c.class_name = 'UserService' AND to_fqn = 'example.main.Repository'"
    ).fetchone()
    assert dependency == ("example.main.Repository", True)

    annotation = connection.execute(
        "SELECT annotation_fqn FROM class_annotations a JOIN classes c ON a.class_id = c.class_id "
        "WHERE c.class_name = 'UserService'"
    ).fetchone()
    assert annotation == ("org.springframework.stereotype.Service",)

    method_annotation = connection.execute(
        "SELECT annotation_fqn FROM method_annotations ma JOIN methods m ON ma.method_id = m.method_id "
        "WHERE m.method_name = 'handles'"
    ).fetchone()
    assert method_annotation == ("org.junit.Test",)


def test_export_checks_existing_output_before_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "model.duckdb"
    output.write_text("existing", encoding="utf-8")

    def fail_if_analyzer_is_created(*args: object, **kwargs: object) -> object:
        raise AssertionError("analyzer must not be created when output already exists")

    monkeypatch.setattr(duckdb_export, "JavaAnalyzer", fail_if_analyzer_is_created)

    with pytest.raises(FileExistsError, match="Файл уже существует:"):
        export_to_duckdb(tmp_path, ["module-a"], output, "demo-repo")


def write_java(project_root: Path, module_name: str, source_dir: str, class_name: str, source: str) -> None:
    package_name, _, simple_name = class_name.rpartition(".")
    source_root = project_root / module_name / "src" / source_dir / "java" / package_name.replace(".", "/")
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / f"{simple_name}.java").write_text(source, encoding="utf-8")
    (project_root / module_name / "pom.xml").write_text("<project />", encoding="utf-8")
