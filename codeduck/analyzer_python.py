"""Анализ Python-кода по дереву разбора Tree-sitter.

Строит модель пакетов, модулей, классов, функций/методов, импортов и вызовов для
переданных каталогов-пакетов. Каждый каталог ``-p`` трактуется как корневой пакет:
его имя становится верхним элементом dotted-имени модуля.

Резолвинг вызовов — best-effort: у каждого вызова всегда есть сырое выражение, а
``resolved_target`` заполняется по импортам и локальным определениям, где это
однозначно, иначе остаётся ``None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from codeduck.utils.measure import log_duration

logger = logging.getLogger("codeduck.analyzer_python")


@dataclass(frozen=True)
class PackageModel:
    """Пакет — каталог с исходниками; ``name`` dotted, ``path`` posix-относительный."""

    name: str
    path: str


@dataclass(frozen=True)
class ImportModel:
    """Импорт модуля: ``kind`` ∈ {import, from}; ``alias`` — связанное имя или None."""

    kind: str
    imported_name: str
    alias: Optional[str]


@dataclass(frozen=True)
class ClassModel:
    """Объявление класса на любой глубине вложенности."""

    name: str
    qualified_name: str
    bases: Tuple[str, ...]
    decorators: Tuple[str, ...]
    lineno: int


@dataclass(frozen=True)
class FunctionModel:
    """Функция или метод; ``owner_class`` — qualified_name класса-владельца или None."""

    name: str
    qualified_name: str
    owner_class: Optional[str]
    params: str
    returns: Optional[str]
    decorators: Tuple[str, ...]
    is_method: bool
    is_async: bool
    lineno: int


@dataclass(frozen=True)
class CallModel:
    """Вызов внутри модуля; ``caller`` — обрамляющая функция/метод или None."""

    callee_expr: str
    callee_name: Optional[str]
    resolved_target: Optional[str]
    caller: Optional[str]
    lineno: int


@dataclass
class ModuleModel:
    """Модуль (`.py`) со всем, что из него извлечено."""

    name: str
    path: str
    package_name: str
    imports: List[ImportModel] = field(default_factory=list)
    classes: List[ClassModel] = field(default_factory=list)
    functions: List[FunctionModel] = field(default_factory=list)
    calls: List[CallModel] = field(default_factory=list)


@dataclass
class _RawCall:
    """Промежуточный вызов до резолва: хранит разобранные части и контекст self."""

    callee_expr: str
    callee_name: Optional[str]
    parts: Optional[Tuple[str, ...]]
    caller: Optional[str]
    class_qname: Optional[str]
    lineno: int


class PythonAnalyzer:
    """Строит модель пакетов и модулей из переданных каталогов-пакетов."""

    def __init__(self, package_dirs: Sequence[Path]) -> None:
        self.package_dirs = tuple(Path(directory).resolve() for directory in package_dirs)
        self.parser = Parser(Language(tree_sitter_python.language()))

    def analyze(self) -> Tuple[List[PackageModel], List[ModuleModel]]:
        packages: Dict[str, PackageModel] = {}
        modules: List[ModuleModel] = []
        with log_duration("Чтение и парсинг исходников (tree-sitter)"):
            for package_dir in self.package_dirs:
                root_parent = package_dir.parent
                for path in sorted(package_dir.rglob("*.py")):
                    modules.append(self._read_module(path, root_parent, packages))
        logger.info("Пакетов: %d, модулей: %d", len(packages), len(modules))
        ordered_packages = [packages[name] for name in sorted(packages)]
        modules.sort(key=lambda module: module.name)
        return ordered_packages, modules

    def _read_module(
        self,
        path: Path,
        root_parent: Path,
        packages: Dict[str, PackageModel],
    ) -> ModuleModel:
        relative = path.relative_to(root_parent)
        package_path = relative.parent
        package_name = package_path.as_posix().replace("/", ".")
        self._register_packages(package_path, packages)

        stem = path.stem
        module_name = package_name if stem == "__init__" else self._join(package_name, stem)

        source = path.read_bytes()
        tree = self.parser.parse(source)
        root = tree.root_node

        imports = self._imports(root, source)
        alias_map = self._alias_map(imports)

        module = ModuleModel(
            name=module_name,
            path=relative.as_posix(),
            package_name=package_name,
            imports=imports,
        )
        raw_calls: List[_RawCall] = []
        for child in root.named_children:
            self._visit(child, source, module_name, None, None, False, module, raw_calls)

        defs_map = self._defs_map(module)
        for raw in raw_calls:
            module.calls.append(
                CallModel(
                    callee_expr=raw.callee_expr,
                    callee_name=raw.callee_name,
                    resolved_target=self._resolve(raw, alias_map, defs_map),
                    caller=raw.caller,
                    lineno=raw.lineno,
                )
            )
        return module

    @staticmethod
    def _register_packages(package_path: Path, packages: Dict[str, PackageModel]) -> None:
        """Регистрирует пакет и всех его предков (namespace-пакеты включительно)."""
        parts = package_path.parts
        for depth in range(1, len(parts) + 1):
            prefix = Path(*parts[:depth])
            name = prefix.as_posix().replace("/", ".")
            if name and name not in packages:
                packages[name] = PackageModel(name=name, path=prefix.as_posix())

    def _visit(
        self,
        node: Node,
        source: bytes,
        prefix: str,
        class_qname: Optional[str],
        func_qname: Optional[str],
        parent_is_class: bool,
        module: ModuleModel,
        raw_calls: List[_RawCall],
    ) -> None:
        node_type = node.type
        if node_type == "class_definition":
            self._visit_class(node, source, prefix, func_qname, module, raw_calls)
            return
        if node_type == "function_definition":
            self._visit_function(
                node, source, prefix, class_qname, parent_is_class, module, raw_calls
            )
            return
        if node_type == "call":
            raw_calls.append(self._raw_call(node, source, func_qname, class_qname))
        for child in node.named_children:
            self._visit(child, source, prefix, class_qname, func_qname, parent_is_class, module, raw_calls)

    def _visit_class(
        self,
        node: Node,
        source: bytes,
        prefix: str,
        func_qname: Optional[str],
        module: ModuleModel,
        raw_calls: List[_RawCall],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node, source)
        qualified_name = self._join(prefix, name)
        module.classes.append(
            ClassModel(
                name=name,
                qualified_name=qualified_name,
                bases=self._bases(node, source),
                decorators=self._decorators(node, source),
                lineno=node.start_point[0] + 1,
            )
        )
        body = node.child_by_field_name("body")
        if body is None:
            return
        for child in body.named_children:
            self._visit(child, source, qualified_name, qualified_name, func_qname, True, module, raw_calls)

    def _visit_function(
        self,
        node: Node,
        source: bytes,
        prefix: str,
        class_qname: Optional[str],
        parent_is_class: bool,
        module: ModuleModel,
        raw_calls: List[_RawCall],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node, source)
        qualified_name = self._join(prefix, name)
        return_node = node.child_by_field_name("return_type")
        module.functions.append(
            FunctionModel(
                name=name,
                qualified_name=qualified_name,
                owner_class=class_qname if parent_is_class else None,
                params=self._params(node, source),
                returns=self._text(return_node, source) if return_node is not None else None,
                decorators=self._decorators(node, source),
                is_method=parent_is_class,
                is_async=any(child.type == "async" for child in node.children),
                lineno=node.start_point[0] + 1,
            )
        )
        body = node.child_by_field_name("body")
        if body is None:
            return
        # Внутри тела функции self по-прежнему указывает на класс метода,
        # поэтому class_qname прокидываем дальше, но parent_is_class сбрасываем.
        for child in body.named_children:
            self._visit(child, source, qualified_name, class_qname, qualified_name, False, module, raw_calls)

    def _raw_call(
        self,
        node: Node,
        source: bytes,
        func_qname: Optional[str],
        class_qname: Optional[str],
    ) -> _RawCall:
        function_node = node.child_by_field_name("function")
        callee_expr = self._text(function_node, source) if function_node is not None else ""
        parts = self._dotted_parts(function_node, source) if function_node is not None else None
        callee_name = self._callee_name(function_node, source, parts)
        return _RawCall(
            callee_expr=callee_expr,
            callee_name=callee_name,
            parts=parts,
            caller=func_qname,
            class_qname=class_qname,
            lineno=node.start_point[0] + 1,
        )

    @staticmethod
    def _callee_name(
        function_node: Optional[Node],
        source: bytes,
        parts: Optional[Tuple[str, ...]],
    ) -> Optional[str]:
        if parts:
            return parts[-1]
        if function_node is None:
            return None
        if function_node.type == "attribute":
            attribute = function_node.child_by_field_name("attribute")
            return PythonAnalyzer._text(attribute, source) if attribute is not None else None
        if function_node.type == "identifier":
            return PythonAnalyzer._text(function_node, source)
        return None

    @staticmethod
    def _dotted_parts(node: Node, source: bytes) -> Optional[Tuple[str, ...]]:
        """Разбирает цепочку identifier/attribute в список сегментов, иначе None."""
        if node.type == "identifier":
            return (PythonAnalyzer._text(node, source),)
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            attribute = node.child_by_field_name("attribute")
            if obj is None or attribute is None:
                return None
            left = PythonAnalyzer._dotted_parts(obj, source)
            if left is None:
                return None
            return left + (PythonAnalyzer._text(attribute, source),)
        return None

    @staticmethod
    def _resolve(
        raw: _RawCall,
        alias_map: Dict[str, str],
        defs_map: Dict[str, str],
    ) -> Optional[str]:
        parts = raw.parts
        if not parts:
            return None
        head = parts[0]
        rest = parts[1:]
        if head == "self" and raw.class_qname is not None:
            return ".".join((raw.class_qname, *rest)) if rest else raw.class_qname
        if head in alias_map:
            base = alias_map[head]
            return ".".join((base, *rest)) if rest else base
        if head in defs_map:
            base = defs_map[head]
            return ".".join((base, *rest)) if rest else base
        return None

    def _imports(self, root: Node, source: bytes) -> List[ImportModel]:
        imports: List[ImportModel] = []
        for node in self._walk(root):
            if node.type == "import_statement":
                imports.extend(self._plain_imports(node, source))
            elif node.type == "import_from_statement":
                imports.extend(self._from_imports(node, source))
        return imports

    def _plain_imports(self, node: Node, source: bytes) -> Iterator[ImportModel]:
        for child in node.named_children:
            if child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    yield ImportModel(
                        kind="import",
                        imported_name=self._text(name_node, source),
                        alias=self._text(alias_node, source) if alias_node is not None else None,
                    )
            elif child.type == "dotted_name":
                yield ImportModel(kind="import", imported_name=self._text(child, source), alias=None)

    def _from_imports(self, node: Node, source: bytes) -> Iterator[ImportModel]:
        module_node = node.child_by_field_name("module_name")
        module_name = self._text(module_node, source) if module_node is not None else ""
        for index in range(node.child_count):
            if node.field_name_for_child(index) != "name":
                continue
            child = node.children[index]
            if child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    original = self._text(name_node, source)
                    yield ImportModel(
                        kind="from",
                        imported_name=self._join(module_name, original),
                        alias=self._text(alias_node, source) if alias_node is not None else None,
                    )
            elif child.type == "dotted_name":
                original = self._text(child, source)
                yield ImportModel(kind="from", imported_name=self._join(module_name, original), alias=None)

    @staticmethod
    def _alias_map(imports: Sequence[ImportModel]) -> Dict[str, str]:
        """Строит связанное имя → цель для резолва вызовов."""
        alias_map: Dict[str, str] = {}
        for entry in imports:
            if entry.alias is not None:
                alias_map[entry.alias] = entry.imported_name
            elif entry.kind == "from":
                # `from a.b import c` связывает простое имя c.
                alias_map[entry.imported_name.rsplit(".", 1)[-1]] = entry.imported_name
            else:
                # `import a.b` связывает верхний сегмент a.
                head = entry.imported_name.split(".", 1)[0]
                alias_map.setdefault(head, head)
        return alias_map

    @staticmethod
    def _defs_map(module: ModuleModel) -> Dict[str, str]:
        """Простое имя класса/функции → qualified_name (последнее объявление выигрывает)."""
        defs_map: Dict[str, str] = {}
        for class_model in module.classes:
            defs_map[class_model.name] = class_model.qualified_name
        for function_model in module.functions:
            defs_map[function_model.name] = function_model.qualified_name
        return defs_map

    def _bases(self, node: Node, source: bytes) -> Tuple[str, ...]:
        argument_list = next(
            (child for child in node.named_children if child.type == "argument_list"),
            None,
        )
        if argument_list is None:
            return ()
        return tuple(self._text(child, source) for child in argument_list.named_children)

    def _decorators(self, node: Node, source: bytes) -> Tuple[str, ...]:
        parent = node.parent
        if parent is None or parent.type != "decorated_definition":
            return ()
        decorators = []
        for child in parent.named_children:
            if child.type == "decorator":
                decorators.append(self._text(child, source).removeprefix("@").strip())
        return tuple(decorators)

    def _params(self, node: Node, source: bytes) -> str:
        parameters = node.child_by_field_name("parameters")
        if parameters is None:
            return ""
        return ", ".join(self._text(child, source) for child in parameters.named_children)

    @staticmethod
    def _walk(node: Node) -> Iterator[Node]:
        yield node
        for child in node.named_children:
            yield from PythonAnalyzer._walk(child)

    @staticmethod
    def _text(node: Node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8")

    @staticmethod
    def _join(prefix: str, name: str) -> str:
        return "%s.%s" % (prefix, name) if prefix else name
