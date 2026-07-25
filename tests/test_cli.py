import tempfile
import unittest
from pathlib import Path

from codeduck.analyzer import JavaDependencyAnalyzer


class JavaDependencyAnalyzerTest(unittest.TestCase):
    def test_resolves_imported_same_package_static_and_fully_qualified_classes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            self._write_java(
                project_root,
                "module-a",
                "example.main.ClassA",
                """
                package example.main;
                import example.other.ClassB;
                import example.other.ClassAnnotation;
                import static example.other.ClassD.VALUE;
                @ClassAnnotation
                public class ClassA extends LocalHelper implements ClassB {
                    private ClassB classB;
                    private example.other.ClassC classC;
                    void run() { ClassD.use(); }
                }
                """,
            )
            self._write_java(project_root, "module-a", "example.main.LocalHelper", "package example.main; class LocalHelper {}")
            self._write_java(
                project_root,
                "module-b",
                "example.other.ClassB",
                "package example.other; public interface ClassB {}",
            )
            self._write_java(
                project_root,
                "module-b",
                "example.other.ClassAnnotation",
                "package example.other; public @interface ClassAnnotation {}",
            )
            self._write_java(project_root, "module-b", "example.other.ClassC", "package example.other; public class ClassC {}")
            self._write_java(project_root, "module-b", "example.other.ClassD", "package example.other; public class ClassD {}")

            records = list(JavaDependencyAnalyzer(project_root, ["module-a"]).analyze())

        class_a = next(record for record in records if record["class_name"] == "example.main.ClassA")
        self.assertEqual(
            [
                "example.main.LocalHelper",
                "example.other.ClassAnnotation",
                "example.other.ClassB",
                "example.other.ClassC",
                "example.other.ClassD",
            ],
            class_a["depends_on_classes"],
        )
        self.assertEqual(["example.other.ClassB"], class_a["implements"])
        self.assertEqual(["example.other.ClassAnnotation"], class_a["annotated_by"])

    def test_explicit_import_of_external_class_shadows_project_class_with_same_simple_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            self._write_java(
                project_root,
                "module-a",
                "example.api.Resource",
                """
                package example.api;
                import org.jooq.Param;
                public class Resource {
                    private Param<String> code;
                }
                """,
            )
            # Одноимённый класс проекта не должен подхватываться вместо org.jooq.Param.
            self._write_java(project_root, "module-b", "example.other.Param", "package example.other; public class Param {}")

            records = list(JavaDependencyAnalyzer(project_root, ["module-a"]).analyze())

        resource = next(record for record in records if record["class_name"] == "example.api.Resource")
        self.assertNotIn("example.other.Param", resource["depends_on_classes"])

    def test_discovers_every_directory_with_pom_xml(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            self._write_java(project_root, "module-a", "example.a.A", "package example.a; class A {}")
            self._write_java(project_root, "parent/child", "example.b.B", "package example.b; class B {}")
            self._write_pom(project_root, "without-sources")
            self._write_pom(project_root, "module-a/target/generated-sources")

            modules = JavaDependencyAnalyzer.discover_maven_modules(project_root)

        self.assertEqual(
            ("module-a", "module-a/target/generated-sources", "parent/child", "without-sources"),
            modules,
        )

    @staticmethod
    def _write_java(project_root, module_name, class_name, source):
        package_name, _, simple_name = class_name.rpartition(".")
        source_root = project_root / module_name / "src" / "main" / "java" / package_name.replace(".", "/")
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / (simple_name + ".java")).write_text(source, encoding="utf-8")
        JavaDependencyAnalyzerTest._write_pom(project_root, module_name)

    @staticmethod
    def _write_pom(project_root, module_name):
        module_root = project_root / module_name
        module_root.mkdir(parents=True, exist_ok=True)
        (module_root / "pom.xml").write_text("<project />", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
