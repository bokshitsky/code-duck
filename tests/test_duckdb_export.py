import tempfile
import unittest
from pathlib import Path

import duckdb

from codeduck.duckdb_export import JavaModelAnalyzer, write_database


class DuckDbExportTest(unittest.TestCase):
    def test_exports_classes_methods_and_relations(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            self._write_java(
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
            self._write_java(project_root, "module-a", "main", "example.main.BaseService", "package example.main; class BaseService {}")
            self._write_java(project_root, "module-a", "main", "example.main.Api", "package example.main; public interface Api {}")
            self._write_java(project_root, "module-a", "main", "example.main.Repository", "package example.main; public class Repository {}")
            self._write_java(
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

            models = list(JavaModelAnalyzer(project_root, ["module-a"]).analyze_model())
            connection = duckdb.connect(":memory:")
            write_database(connection, "demo-repo", ["module-a"], models)

            repos = connection.execute("SELECT name FROM repos").fetchall()
            self.assertEqual([("demo-repo",)], repos)

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
            self.assertEqual([], columns_without_comment)
            tables_without_comment = connection.execute(
                "SELECT table_name FROM duckdb_tables() WHERE table_name IN ? AND comment IS NULL",
                [list(our_tables)],
            ).fetchall()
            self.assertEqual([], tables_without_comment)

            module_rows = connection.execute("SELECT module_name FROM modules").fetchall()
            self.assertEqual([("module-a",)], module_rows)

            source_types = connection.execute(
                "SELECT class_name, source_type FROM classes ORDER BY class_name"
            ).fetchall()
            self.assertIn(("UserService", "prod"), source_types)
            self.assertIn(("ServiceTest", "test"), source_types)

            handle_params = connection.execute(
                "SELECT params, return_type FROM methods m JOIN classes c ON m.class_id = c.class_id "
                "WHERE c.class_name = 'UserService' AND m.method_name = 'handle'"
            ).fetchone()
            self.assertEqual(("int, java.util.List<String>", "String"), handle_params)

            supertypes = connection.execute(
                "SELECT super_fqn, relation_type FROM class_supertypes s JOIN classes c ON s.class_id = c.class_id "
                "WHERE c.class_name = 'UserService' ORDER BY relation_type"
            ).fetchall()
            self.assertEqual(
                [("example.main.BaseService", "extends"), ("example.main.Api", "implements")],
                supertypes,
            )

            dependency = connection.execute(
                "SELECT to_fqn, to_class_id IS NOT NULL FROM class_dependencies d JOIN classes c ON d.from_class_id = c.class_id "
                "WHERE c.class_name = 'UserService' AND to_fqn = 'example.main.Repository'"
            ).fetchone()
            self.assertEqual(("example.main.Repository", True), dependency)

            annotation = connection.execute(
                "SELECT annotation_fqn FROM class_annotations a JOIN classes c ON a.class_id = c.class_id "
                "WHERE c.class_name = 'UserService'"
            ).fetchone()
            self.assertEqual(("org.springframework.stereotype.Service",), annotation)

            method_annotation = connection.execute(
                "SELECT annotation_fqn FROM method_annotations ma JOIN methods m ON ma.method_id = m.method_id "
                "WHERE m.method_name = 'handles'"
            ).fetchone()
            self.assertEqual(("org.junit.Test",), method_annotation)

    @staticmethod
    def _write_java(project_root, module_name, source_dir, class_name, source):
        package_name, _, simple_name = class_name.rpartition(".")
        source_root = project_root / module_name / "src" / source_dir / "java" / package_name.replace(".", "/")
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / (simple_name + ".java")).write_text(source, encoding="utf-8")
        module_root = project_root / module_name
        (module_root / "pom.xml").write_text("<project />", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
