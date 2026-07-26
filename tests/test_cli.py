from pathlib import Path
from typing import cast

from codeduck.analyzer_java import JavaAnalyzer


def test_resolves_imported_same_package_static_and_fully_qualified_classes(tmp_path: Path) -> None:
    project_root = tmp_path
    write_java(
        project_root,
        'module-a',
        'example.main.ClassA',
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
    write_java(project_root, 'module-a', 'example.main.LocalHelper', 'package example.main; class LocalHelper {}')
    write_java(
        project_root,
        'module-b',
        'example.other.ClassB',
        'package example.other; public interface ClassB {}',
    )
    write_java(
        project_root,
        'module-b',
        'example.other.ClassAnnotation',
        'package example.other; public @interface ClassAnnotation {}',
    )
    write_java(project_root, 'module-b', 'example.other.ClassC', 'package example.other; public class ClassC {}')
    write_java(project_root, 'module-b', 'example.other.ClassD', 'package example.other; public class ClassD {}')

    records = list(JavaAnalyzer(project_root, ['module-a']).analyze())

    class_a = next(record for record in records if record['class_name'] == 'example.main.ClassA')
    assert class_a['depends_on_classes'] == [
        'example.main.LocalHelper',
        'example.other.ClassAnnotation',
        'example.other.ClassB',
        'example.other.ClassC',
        'example.other.ClassD',
    ]
    assert class_a['implements'] == ['example.other.ClassB']
    assert class_a['annotated_by'] == ['example.other.ClassAnnotation']


def test_explicit_import_of_external_class_shadows_project_class_with_same_simple_name(tmp_path: Path) -> None:
    project_root = tmp_path
    write_java(
        project_root,
        'module-a',
        'example.api.Resource',
        """
        package example.api;
        import org.jooq.Param;
        public class Resource {
            private Param<String> code;
        }
        """,
    )
    # Одноимённый класс проекта не должен подхватываться вместо org.jooq.Param.
    write_java(project_root, 'module-b', 'example.other.Param', 'package example.other; public class Param {}')

    records = list(JavaAnalyzer(project_root, ['module-a']).analyze())

    resource = next(record for record in records if record['class_name'] == 'example.api.Resource')
    assert 'example.other.Param' not in cast('list[str]', resource['depends_on_classes'])


def test_records_external_classes_by_fqn_via_explicit_and_wildcard_imports(tmp_path: Path) -> None:
    project_root = tmp_path
    write_java(
        project_root,
        'module-a',
        'example.configs.JerseyConfig',
        """
        package example.configs;
        import org.glassfish.jersey.server.ResourceConfig;
        import example.mappers.LocalMapper;
        import com.example.util.*;
        public class JerseyConfig extends ResourceConfig {
            public JerseyConfig() {
                register(LocalMapper.class);
                register(ExternalHelper.class);
            }
        }
        """,
    )
    write_java(
        project_root,
        'module-a',
        'example.mappers.LocalMapper',
        'package example.mappers; public class LocalMapper {}',
    )

    records = list(JavaAnalyzer(project_root, ['module-a']).analyze())

    config = next(record for record in records if record['class_name'] == 'example.configs.JerseyConfig')
    depends_on = cast('list[str]', config['depends_on_classes'])
    # Внешний суперкласс сохраняется по FQN, хотя такого класса нет среди исходников.
    assert 'org.glassfish.jersey.server.ResourceConfig' in depends_on
    # Внутрипроектный тип по-прежнему резолвится.
    assert 'example.mappers.LocalMapper' in depends_on
    # Одиночный wildcard-импорт даёт FQN для внешнего типа.
    assert 'com.example.util.ExternalHelper' in depends_on


def test_discovers_every_directory_with_pom_xml(tmp_path: Path) -> None:
    project_root = tmp_path
    write_java(project_root, 'module-a', 'example.a.A', 'package example.a; class A {}')
    write_java(project_root, 'parent/child', 'example.b.B', 'package example.b; class B {}')
    write_pom(project_root, 'without-sources')
    write_pom(project_root, 'module-a/target/generated-sources')

    modules = JavaAnalyzer.discover_maven_modules(project_root)

    assert modules == ('module-a', 'module-a/target/generated-sources', 'parent/child', 'without-sources')


def write_java(project_root: Path, module_name: str, class_name: str, source: str) -> None:
    package_name, _, simple_name = class_name.rpartition('.')
    source_root = project_root / module_name / 'src' / 'main' / 'java' / package_name.replace('.', '/')
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / f'{simple_name}.java').write_text(source, encoding='utf-8')
    write_pom(project_root, module_name)


def write_pom(project_root: Path, module_name: str) -> None:
    module_root = project_root / module_name
    module_root.mkdir(parents=True, exist_ok=True)
    (module_root / 'pom.xml').write_text('<project />', encoding='utf-8')
