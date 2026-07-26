import textwrap
from pathlib import Path

from codeduck.analyzer_python import ModuleModel, PythonAnalyzer


def build_sample_package(root: Path) -> Path:
    package_dir = root / 'myapp'
    write_py(
        package_dir / '__init__.py',
        '',
    )
    write_py(
        package_dir / 'repo.py',
        """
        class Repository:
            def get(self, x):
                return x
        """,
    )
    write_py(
        package_dir / 'service.py',
        """
        from myapp.repo import Repository
        import os.path as osp

        class Base:
            pass

        @final
        class UserService(Base):
            async def handle(self, user_id: int) -> str:
                return self.load(user_id)

            def load(self, user_id):
                return Repository().get(user_id)

        def helper(a, b=2):
            def inner():
                helper(1)
            return inner
        """,
    )
    return package_dir


def test_collects_packages_and_module_names(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)

    packages, modules = PythonAnalyzer([package_dir]).analyze()

    assert [(package.name, package.path) for package in packages] == [('myapp', 'myapp')]
    assert sorted(module.name for module in modules) == ['myapp', 'myapp.repo', 'myapp.service']
    init_module = by_name(modules, 'myapp')
    assert init_module.path == 'myapp/__init__.py'
    assert init_module.package_name == 'myapp'


def test_classes_include_bases_decorators_and_qualified_names(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)

    _, modules = PythonAnalyzer([package_dir]).analyze()

    service = by_name(modules, 'myapp.service')
    class_by_name = {model.qualified_name: model for model in service.classes}
    assert set(class_by_name) == {'myapp.service.Base', 'myapp.service.UserService'}
    user_service = class_by_name['myapp.service.UserService']
    assert user_service.bases == ('Base',)
    assert user_service.decorators == ('final',)


def test_functions_track_methods_async_params_and_nesting(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)

    _, modules = PythonAnalyzer([package_dir]).analyze()

    service = by_name(modules, 'myapp.service')
    functions = {model.qualified_name: model for model in service.functions}

    handle = functions['myapp.service.UserService.handle']
    assert handle.is_method is True
    assert handle.is_async is True
    assert handle.params == 'self, user_id: int'
    assert handle.returns == 'str'
    assert handle.owner_class == 'myapp.service.UserService'

    helper = functions['myapp.service.helper']
    assert helper.is_method is False
    assert helper.owner_class is None
    # Вложенная в функцию функция сохраняется с квалифицированным именем.
    assert 'myapp.service.helper.inner' in functions
    assert functions['myapp.service.helper.inner'].is_method is False


def test_calls_are_resolved_best_effort(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)

    _, modules = PythonAnalyzer([package_dir]).analyze()

    service = by_name(modules, 'myapp.service')
    resolved = {call.callee_expr: call for call in service.calls}

    # self.method резолвится в метод текущего класса.
    self_call = resolved['self.load']
    assert self_call.resolved_target == 'myapp.service.UserService.load'
    assert self_call.caller == 'myapp.service.UserService.handle'

    # Имя из from-import резолвится в его FQN.
    assert resolved['Repository'].resolved_target == 'myapp.repo.Repository'

    # Локальная функция резолвится в её qualified_name; вызов из вложенной функции.
    helper_call = resolved['helper']
    assert helper_call.resolved_target == 'myapp.service.helper'
    assert helper_call.caller == 'myapp.service.helper.inner'

    # Цепочка через вызов не резолвится, но имя и выражение сохранены.
    chained = resolved['Repository().get']
    assert chained.resolved_target is None
    assert chained.callee_name == 'get'


def test_imports_capture_kind_name_and_alias(tmp_path: Path) -> None:
    package_dir = build_sample_package(tmp_path)

    _, modules = PythonAnalyzer([package_dir]).analyze()

    service = by_name(modules, 'myapp.service')
    imports = {(entry.kind, entry.imported_name): entry for entry in service.imports}
    assert imports['from', 'myapp.repo.Repository'].alias is None
    assert imports['import', 'os.path'].alias == 'osp'


def by_name(modules: list[ModuleModel], name: str) -> ModuleModel:
    return next(module for module in modules if module.name == name)


def write_py(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding='utf-8')
