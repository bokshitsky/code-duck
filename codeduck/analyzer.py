"""Анализ зависимостей Java-классов по дереву разбора Tree-sitter."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, Mapping, Optional, Sequence, Set, Tuple

import tree_sitter_java
from tree_sitter import Language, Node, Parser

TYPE_DECLARATION_TYPES = {
    "annotation_type_declaration",
    "class_declaration",
    "enum_declaration",
    "interface_declaration",
    "record_declaration",
}
TYPE_REFERENCE_TYPES = {
    "scoped_type_identifier",
    "type_identifier",
}
ANNOTATION_TYPES = {
    "annotation",
    "marker_annotation",
}


@dataclass(frozen=True)
class JavaSource:
    """Распарсенный Java-файл и информация, нужная для резолвинга имён."""

    module_name: str
    path: Path
    source: bytes
    tree: object
    package_name: str
    explicit_imports: Mapping[str, str]
    wildcard_imports: Tuple[str, ...]
    static_imports: Tuple[str, ...]


@dataclass(frozen=True)
class ClassDeclaration:
    """Декларация верхнеуровневого Java-класса."""

    module_name: str
    class_name: str
    source: JavaSource
    node: Node


class JavaDependencyAnalyzer:
    """Строит связи между классами, объявленными в переданных Maven-модулях."""

    def __init__(self, project_root: Path, module_names: Sequence[str]) -> None:
        self.project_root = project_root
        self.module_names = tuple(module_names)
        self.parser = Parser(Language(tree_sitter_java.language()))

    def analyze(self) -> Iterator[Mapping[str, object]]:
        sources = list(self._read_sources(self._project_modules()))
        all_declarations = list(self._find_declarations(sources))
        declarations = [
            declaration
            for declaration in all_declarations
            if declaration.module_name in self.module_names
        ]
        known_classes = {declaration.class_name for declaration in all_declarations}
        classes_by_simple_name = self._group_by_simple_name(known_classes)
        for declaration in sorted(declarations, key=lambda item: (item.module_name, item.class_name)):
            dependencies = self._dependencies_for(
                declaration,
                known_classes,
                classes_by_simple_name,
            )
            implemented_interfaces = self._implemented_interfaces(
                declaration,
                known_classes,
                classes_by_simple_name,
            )
            annotations = self._class_annotations(
                declaration,
                known_classes,
                classes_by_simple_name,
            )
            yield {
                "module_name": declaration.module_name,
                "class_name": declaration.class_name,
                "depends_on_classes": sorted(dependencies),
                "implements": sorted(implemented_interfaces),
                "annotated_by": sorted(annotations),
            }

    def _project_modules(self) -> Tuple[str, ...]:
        """Возвращает модули с Java-исходниками для полного индекса проекта."""
        discovered_modules = self.discover_maven_modules(self.project_root)
        missing_modules = set(self.module_names).difference(discovered_modules)
        if missing_modules:
            missing = ", ".join(sorted(missing_modules))
            raise ValueError("В модулях нет каталога src/main/java: %s" % missing)
        return discovered_modules

    @staticmethod
    def discover_maven_modules(project_root: Path) -> Tuple[str, ...]:
        """Находит все вложенные Maven-модули с Java-исходниками.

        Модулем считается каталог с `src/main/java` на любой глубине проекта.
        Имя модуля — его путь относительно корня в posix-виде (например,
        `hh-fixture/server`). Каталоги внутри `target` пропускаются.
        """
        modules = set()
        for source_root in project_root.rglob("src/main/java"):
            if not source_root.is_dir():
                continue
            relative_source_root = source_root.relative_to(project_root)
            if "target" in relative_source_root.parts:
                continue
            module_dir = source_root.parents[2].relative_to(project_root)
            modules.add(module_dir.as_posix())
        return tuple(sorted(modules))

    def _read_sources(self, module_names: Iterable[str]) -> Iterator[JavaSource]:
        for module_name in module_names:
            source_root = self.project_root / module_name / "src" / "main" / "java"
            for path in sorted(source_root.rglob("*.java")):
                source = path.read_bytes()
                tree = self.parser.parse(source)
                package_name = self._package_name(tree.root_node, source)
                explicit_imports, wildcard_imports, static_imports = self._imports(tree.root_node, source)
                yield JavaSource(
                    module_name=module_name,
                    path=path,
                    source=source,
                    tree=tree,
                    package_name=package_name,
                    explicit_imports=explicit_imports,
                    wildcard_imports=wildcard_imports,
                    static_imports=static_imports,
                )

    def _find_declarations(self, sources: Iterable[JavaSource]) -> Iterator[ClassDeclaration]:
        for source in sources:
            for child in source.tree.root_node.named_children:
                if child.type not in TYPE_DECLARATION_TYPES:
                    continue
                name = child.child_by_field_name("name")
                if name is None:
                    continue
                simple_name = self._text(name, source.source)
                class_name = self._qualified_name(source.package_name, simple_name)
                yield ClassDeclaration(source.module_name, class_name, source, child)

    def _dependencies_for(
        self,
        declaration: ClassDeclaration,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Set[str]:
        used_names = self._used_type_names(declaration)
        dependencies = set()

        for imported_class in declaration.source.static_imports:
            dependency = self._resolve_name(
                imported_class,
                declaration.source,
                known_classes,
                classes_by_simple_name,
            )
            if dependency is not None:
                dependencies.add(dependency)

        for name in used_names:
            dependency = self._resolve_name(
                name,
                declaration.source,
                known_classes,
                classes_by_simple_name,
            )
            if dependency is not None:
                dependencies.add(dependency)

        dependencies.discard(declaration.class_name)
        return dependencies

    def _implemented_interfaces(
        self,
        declaration: ClassDeclaration,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Set[str]:
        if declaration.node.type not in {"class_declaration", "enum_declaration", "record_declaration"}:
            return set()

        interface_nodes = [
            child
            for child in declaration.node.named_children
            if child.type in {"implements_interfaces", "super_interfaces"}
        ]
        implemented_interfaces = set()
        for interface_node in interface_nodes:
            for node in self._walk(interface_node):
                if node.type not in TYPE_REFERENCE_TYPES:
                    continue
                dependency = self._resolve_name(
                    self._text(node, declaration.source.source),
                    declaration.source,
                    known_classes,
                    classes_by_simple_name,
                )
                if dependency is not None:
                    implemented_interfaces.add(dependency)
        implemented_interfaces.discard(declaration.class_name)
        return implemented_interfaces

    def _class_annotations(
        self,
        declaration: ClassDeclaration,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Set[str]:
        annotations = set()
        for child in declaration.node.named_children:
            if child.type != "modifiers":
                continue
            for annotation in child.named_children:
                if annotation.type not in ANNOTATION_TYPES:
                    continue
                annotation_name = self._annotation_name(annotation, declaration.source.source)
                annotations.add(
                    self._resolve_annotation_name(
                        annotation_name,
                        declaration.source,
                        known_classes,
                        classes_by_simple_name,
                    )
                )
        return annotations

    @staticmethod
    def _annotation_name(annotation: Node, source: bytes) -> str:
        text = JavaDependencyAnalyzer._text(annotation, source).strip()
        return text.removeprefix("@").partition("(")[0].strip()

    def _resolve_annotation_name(
        self,
        annotation_name: str,
        source: JavaSource,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> str:
        resolved_name = self._resolve_name(
            annotation_name,
            source,
            known_classes,
            classes_by_simple_name,
        )
        if resolved_name is not None:
            return resolved_name
        if "." in annotation_name:
            return annotation_name

        imported_name = source.explicit_imports.get(annotation_name)
        if imported_name is not None:
            return imported_name
        if len(source.wildcard_imports) == 1:
            return self._qualified_name(source.wildcard_imports[0], annotation_name)
        return annotation_name

    def _used_type_names(self, declaration: ClassDeclaration) -> Set[str]:
        names = set()
        declared_names = set()

        for node in self._walk(declaration.node):
            if node.type in TYPE_DECLARATION_TYPES:
                name = node.child_by_field_name("name")
                if name is not None:
                    declared_names.add(self._text(name, declaration.source.source))

            if node.type in TYPE_REFERENCE_TYPES:
                names.add(self._text(node, declaration.source.source))

            if node.type == "field_access":
                names.update(self._class_prefixes(node, declaration.source.source))

            # Tree-sitter представляет квалификатор статического вызова Foo.call()
            # обычным identifier, поэтому берём идентификаторы с заглавной буквы.
            if node.type == "identifier":
                identifier = self._text(node, declaration.source.source)
                if identifier[:1].isupper():
                    names.add(identifier)

        names.difference_update(declared_names)
        return names

    @staticmethod
    def _class_prefixes(node: Node, source: bytes) -> Set[str]:
        """Находит префиксы FQN в выражениях вида ru.hh.Class.CONSTANT."""
        text = JavaDependencyAnalyzer._text(node, source)
        if any(character in text for character in "()[] <>+-*/=?!:\"'"):
            return set()
        parts = text.split(".")
        if not all(part.isidentifier() for part in parts):
            return set()
        return {".".join(parts[:index]) for index in range(1, len(parts))}

    @staticmethod
    def _resolve_name(
        name: str,
        source: JavaSource,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Optional[str]:
        if name in known_classes:
            return name

        simple_name = name.rsplit(".", 1)[-1]
        if "." in name:
            # Для ссылки Outer.Inner достаточно зависимости от доступного внешнего класса.
            first_name = name.split(".", 1)[0]
            imported = source.explicit_imports.get(first_name)
            if imported in known_classes:
                return imported
            return None

        imported = source.explicit_imports.get(simple_name)
        if imported is not None:
            # Явный single-type import полностью перекрывает простое имя (правила Java),
            # поэтому эвристики по package и уникальному имени здесь применять нельзя:
            # для `import org.jooq.Param` имя `Param` не должно резолвиться в свой класс проекта.
            return imported if imported in known_classes else None

        package_candidate = JavaDependencyAnalyzer._qualified_name(source.package_name, simple_name)
        if package_candidate in known_classes:
            return package_candidate

        wildcard_candidates = {
            JavaDependencyAnalyzer._qualified_name(package_name, simple_name)
            for package_name in source.wildcard_imports
        }.intersection(known_classes)
        if len(wildcard_candidates) == 1:
            return next(iter(wildcard_candidates))

        # Импорт может отсутствовать для класса из того же package. Если имя уникально
        # среди анализируемых исходников, его также можно разрешить безопасно.
        candidates = classes_by_simple_name.get(simple_name, set())
        if len(candidates) == 1:
            return next(iter(candidates))
        return None

    @staticmethod
    def _group_by_simple_name(class_names: Iterable[str]) -> Mapping[str, Set[str]]:
        result: DefaultDict[str, Set[str]] = defaultdict(set)
        for class_name in class_names:
            result[class_name.rsplit(".", 1)[-1]].add(class_name)
        return result

    @staticmethod
    def _package_name(root: Node, source: bytes) -> str:
        for child in root.named_children:
            if child.type == "package_declaration":
                text = JavaDependencyAnalyzer._text(child, source)
                return text.removeprefix("package").removesuffix(";").strip()
        return ""

    @staticmethod
    def _imports(root: Node, source: bytes) -> Tuple[Mapping[str, str], Tuple[str, ...], Tuple[str, ...]]:
        explicit_imports = {}
        wildcard_imports = []
        static_imports = []
        for child in root.named_children:
            if child.type != "import_declaration":
                continue
            text = JavaDependencyAnalyzer._text(child, source).removesuffix(";").strip()
            is_static = text.startswith("import static ")
            imported_name = text.removeprefix("import static ").removeprefix("import ").strip()
            if is_static:
                owner, _, _ = imported_name.rpartition(".")
                if owner:
                    static_imports.append(owner)
            elif imported_name.endswith(".*"):
                wildcard_imports.append(imported_name[:-2])
            else:
                explicit_imports[imported_name.rsplit(".", 1)[-1]] = imported_name
        return explicit_imports, tuple(wildcard_imports), tuple(static_imports)

    @staticmethod
    def _walk(node: Node) -> Iterator[Node]:
        yield node
        for child in node.named_children:
            yield from JavaDependencyAnalyzer._walk(child)

    @staticmethod
    def _text(node: Node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8")

    @staticmethod
    def _qualified_name(package_name: str, simple_name: str) -> str:
        return "%s.%s" % (package_name, simple_name) if package_name else simple_name
