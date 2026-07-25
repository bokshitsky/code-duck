"""Анализ Java-кода по дереву разбора Tree-sitter.

Строит модель классов, методов, зависимостей, наследования и аннотаций для
целевых Maven-модулей, а также умеет отдавать плоские записи зависимостей.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Tree

from codeduck.utils.measure import log_duration

logger = logging.getLogger("codeduck.analyzer_java")

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
METHOD_TYPES = {
    "compact_constructor_declaration",
    "constructor_declaration",
    "method_declaration",
}


@dataclass(frozen=True)
class JavaSource:
    """Распарсенный Java-файл и информация, нужная для резолвинга имён."""

    module_name: str
    path: Path
    source: bytes
    tree: Tree
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


@dataclass(frozen=True)
class MethodModel:
    """Метод класса: имя, типы параметров и тип возвращаемого значения."""

    name: str
    params: str
    return_type: Optional[str]
    annotations: Tuple[str, ...]


@dataclass(frozen=True)
class ClassModel:
    """Верхнеуровневый класс со всем, что о нём известно."""

    module_name: str
    source_type: str
    fqn: str
    package_name: str
    simple_name: str
    methods: Tuple[MethodModel, ...]
    dependencies: Tuple[str, ...]
    supertypes: Tuple[Tuple[str, str], ...]
    annotations: Tuple[str, ...]


class JavaAnalyzer:
    """Строит связи и модель классов, объявленных в переданных Maven-модулях."""

    def __init__(self, project_root: Path, module_names: Sequence[str]) -> None:
        self.project_root = project_root
        self.module_names = tuple(module_names)
        self.parser = Parser(Language(tree_sitter_java.language()))
        self._source_type_by_path: dict[Path, str] = {}

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

    def analyze_model(self) -> List[ClassModel]:
        with log_duration("Чтение и парсинг исходников (tree-sitter)"):
            sources = list(self._read_all_sources())
        logger.info("Файлов распарсено: %d", len(sources))

        with log_duration("Поиск деклараций классов"):
            all_declarations = list(self._find_declarations(sources))
        target_modules = set(self.module_names)
        declarations = [
            declaration
            for declaration in all_declarations
            if declaration.module_name in target_modules
        ]
        known_classes = {declaration.class_name for declaration in all_declarations}
        classes_by_simple_name = self._group_by_simple_name(known_classes)

        models = []
        with log_duration("Анализ модели (зависимости/методы/наследование/аннотации)"):
            for declaration in sorted(declarations, key=lambda item: (item.module_name, item.class_name)):
                source_type = self._source_type_by_path[declaration.source.path]
                package_name = declaration.source.package_name
                simple_name = declaration.class_name.rsplit(".", 1)[-1]
                models.append(
                    ClassModel(
                        module_name=declaration.module_name,
                        source_type=source_type,
                        fqn=declaration.class_name,
                        package_name=package_name,
                        simple_name=simple_name,
                        methods=tuple(self._methods(declaration, known_classes, classes_by_simple_name)),
                        dependencies=tuple(
                            sorted(self._dependencies_for(declaration, known_classes, classes_by_simple_name))
                        ),
                        supertypes=tuple(self._supertypes(declaration, known_classes, classes_by_simple_name)),
                        annotations=tuple(
                            sorted(self._class_annotations(declaration, known_classes, classes_by_simple_name))
                        ),
                    )
                )
        logger.info("Классов в модели: %d", len(models))
        return models

    def _project_modules(self) -> Tuple[str, ...]:
        """Возвращает Maven-модули для полного индекса проекта."""
        discovered_modules = self.discover_maven_modules(self.project_root)
        missing_modules = set(self.module_names).difference(discovered_modules)
        if missing_modules:
            missing = ", ".join(sorted(missing_modules))
            raise ValueError("В модулях нет pom.xml: %s" % missing)
        return discovered_modules

    @staticmethod
    def discover_maven_modules(project_root: Path) -> Tuple[str, ...]:
        """Находит все Maven-модули.

        Модулем считается каталог с `pom.xml` на любой глубине проекта.
        Имя модуля — его путь относительно корня в posix-виде (например,
        `hh-fixture/server`).
        """
        modules = set()
        for pom_file in project_root.rglob("pom.xml"):
            if pom_file.is_file():
                module_dir = pom_file.parent.relative_to(project_root)
                modules.add(module_dir.as_posix())
        return tuple(sorted(modules))

    def _read_sources(self, module_names: Iterable[str]) -> Iterator[JavaSource]:
        for module_name in module_names:
            yield from self._read_module_sources(module_name, "main", "prod")

    def _read_all_sources(self) -> Iterator[JavaSource]:
        target_modules = set(self.module_names)
        for module_name in self._project_modules():
            yield from self._read_module_sources(module_name, "main", "prod")
            if module_name in target_modules:
                yield from self._read_module_sources(module_name, "test", "test")

    def _read_module_sources(self, module_name: str, source_dir: str, source_type: str) -> Iterator[JavaSource]:
        source_root = self.project_root / module_name / "src" / source_dir / "java"
        if not source_root.is_dir():
            return
        for path in sorted(source_root.rglob("*.java")):
            source = path.read_bytes()
            tree = self.parser.parse(source)
            package_name = self._package_name(tree.root_node, source)
            explicit_imports, wildcard_imports, static_imports = self._imports(tree.root_node, source)
            self._source_type_by_path[path] = source_type
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
        return self._node_annotations(
            declaration.node,
            declaration.source,
            known_classes,
            classes_by_simple_name,
        )

    def _node_annotations(
        self,
        node: Node,
        source: JavaSource,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Set[str]:
        annotations = set()
        for child in node.named_children:
            if child.type != "modifiers":
                continue
            for annotation in child.named_children:
                if annotation.type not in ANNOTATION_TYPES:
                    continue
                annotation_name = self._annotation_name(annotation, source.source)
                annotations.add(
                    self._resolve_annotation_name(
                        annotation_name,
                        source,
                        known_classes,
                        classes_by_simple_name,
                    )
                )
        return annotations

    @staticmethod
    def _annotation_name(annotation: Node, source: bytes) -> str:
        text = JavaAnalyzer._text(annotation, source).strip()
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

    def _methods(
        self,
        declaration: ClassDeclaration,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> Iterator[MethodModel]:
        body = declaration.node.child_by_field_name("body")
        if body is None:
            return
        source = declaration.source.source
        for method_node in self._method_nodes(body):
            name_node = method_node.child_by_field_name("name")
            if name_node is None:
                continue
            return_type_node = method_node.child_by_field_name("type")
            annotations = self._node_annotations(
                method_node,
                declaration.source,
                known_classes,
                classes_by_simple_name,
            )
            yield MethodModel(
                name=self._text(name_node, source),
                params=self._method_params(method_node, source),
                return_type=self._text(return_type_node, source) if return_type_node is not None else None,
                annotations=tuple(sorted(annotations)),
            )

    def _method_nodes(self, body: Node) -> Iterator[Node]:
        for child in body.named_children:
            if child.type in METHOD_TYPES:
                yield child
            elif child.type == "enum_body_declarations":
                for nested in child.named_children:
                    if nested.type in METHOD_TYPES:
                        yield nested

    def _method_params(self, method_node: Node, source: bytes) -> str:
        params_node = method_node.child_by_field_name("parameters")
        if params_node is None:
            return ""
        param_types = []
        for parameter in params_node.named_children:
            if parameter.type == "formal_parameter":
                type_node = parameter.child_by_field_name("type")
                if type_node is not None:
                    param_types.append(self._text(type_node, source))
            elif parameter.type == "spread_parameter":
                type_node = next(
                    (child for child in parameter.named_children if child.type not in {"modifiers", "variable_declarator"}),
                    None,
                )
                if type_node is not None:
                    param_types.append(self._text(type_node, source) + "...")
        return ", ".join(param_types)

    def _supertypes(
        self,
        declaration: ClassDeclaration,
        known_classes: Set[str],
        classes_by_simple_name: Mapping[str, Set[str]],
    ) -> List[Tuple[str, str]]:
        result = []
        implemented = self._implemented_interfaces(declaration, known_classes, classes_by_simple_name)
        for interface_fqn in sorted(implemented):
            result.append((interface_fqn, "implements"))

        extends_containers = []
        superclass = declaration.node.child_by_field_name("superclass")
        if superclass is not None:
            extends_containers.append(superclass)
        extends_containers.extend(
            child
            for child in declaration.node.named_children
            if child.type == "extends_interfaces"
        )

        extended = set()
        for container in extends_containers:
            for node in self._walk(container):
                if node.type not in TYPE_REFERENCE_TYPES:
                    continue
                resolved = self._resolve_name(
                    self._text(node, declaration.source.source),
                    declaration.source,
                    known_classes,
                    classes_by_simple_name,
                )
                if resolved is not None and resolved != declaration.class_name:
                    extended.add(resolved)
        for super_fqn in sorted(extended):
            result.append((super_fqn, "extends"))
        return result

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
        text = JavaAnalyzer._text(node, source)
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

        package_candidate = JavaAnalyzer._qualified_name(source.package_name, simple_name)
        if package_candidate in known_classes:
            return package_candidate

        wildcard_candidates = {
            JavaAnalyzer._qualified_name(package_name, simple_name)
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
                text = JavaAnalyzer._text(child, source)
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
            text = JavaAnalyzer._text(child, source).removesuffix(";").strip()
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
            yield from JavaAnalyzer._walk(child)

    @staticmethod
    def _text(node: Node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8")

    @staticmethod
    def _qualified_name(package_name: str, simple_name: str) -> str:
        return "%s.%s" % (package_name, simple_name) if package_name else simple_name
