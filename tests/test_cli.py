from pathlib import Path

from codeduck.analyzer_java import JavaAnalyzer


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
