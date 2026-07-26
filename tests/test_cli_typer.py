from collections.abc import Sequence
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from codeduck.cli import app


def test_java_analysis_is_registered_under_analyze() -> None:
    result = CliRunner().invoke(app, ["analyze", "--help"])

    assert result.exit_code == 0
    assert "java" in result.output


def test_java_analysis_passes_modules_to_exporter(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project_root = tmp_path
    output = project_root / "model.duckdb"
    calls: list[tuple[Path, Sequence[str], Path, str, bool]] = []

    def export_to_duckdb(
        project_root: Path,
        module_names: Sequence[str],
        output_path: Path,
        repo_name: str,
        force: bool = False,
    ) -> None:
        calls.append((project_root, module_names, output_path, repo_name, force))

    monkeypatch.setattr("codeduck.cli.export_to_duckdb", export_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "java",
            "module-a",
            "--project-root",
            str(project_root),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(project_root.resolve(), ("module-a",), output, project_root.name, False)]


def test_java_analysis_discovers_modules_with_maven_all(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project_root = tmp_path
    output = project_root / "model.duckdb"
    calls: list[tuple[Path, Sequence[str], Path, str, bool]] = []

    def discover_maven_modules(project_root: Path) -> tuple[str, ...]:
        assert project_root == tmp_path.resolve()
        return ("module-a", "module-b")

    def export_to_duckdb(
        project_root: Path,
        module_names: Sequence[str],
        output_path: Path,
        repo_name: str,
        force: bool = False,
    ) -> None:
        calls.append((project_root, module_names, output_path, repo_name, force))

    monkeypatch.setattr("codeduck.cli.JavaAnalyzer.discover_maven_modules", discover_maven_modules)
    monkeypatch.setattr("codeduck.cli.export_to_duckdb", export_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "java",
            "--maven-all",
            "--project-root",
            str(project_root),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(project_root.resolve(), ("module-a", "module-b"), output, project_root.name, False)]


def test_java_analysis_does_not_expose_maven_flag() -> None:
    result = CliRunner().invoke(app, ["analyze", "java", "--help"])

    assert result.exit_code == 0, result.output
    assert "--maven-all" in result.output
    assert "--maven " not in result.output


def test_java_analysis_requires_modules_when_maven_all_is_not_used() -> None:
    result = CliRunner().invoke(app, ["analyze", "java", "--output", "model.duckdb"])

    assert result.exit_code == 2
    assert "Укажите модули позиционно или используйте --maven-all." in result.output


def test_java_analysis_prints_errors_in_red() -> None:
    result = CliRunner().invoke(app, ["analyze", "java", "--output", "model.duckdb"], color=True)

    assert result.exit_code == 2
    assert "\x1b[31m" in result.output
    assert "Укажите модули позиционно или используйте --maven-all." in result.output


def test_java_analysis_fails_when_output_exists(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project_root = tmp_path
    output = project_root / "model.duckdb"
    output.write_text("existing", encoding="utf-8")

    def export_to_duckdb(
        project_root: Path,
        module_names: Sequence[str],
        output_path: Path,
        repo_name: str,
        force: bool = False,
    ) -> None:
        assert force is False
        raise FileExistsError(f"Файл уже существует: {output_path}")

    monkeypatch.setattr("codeduck.cli.export_to_duckdb", export_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "java",
            "module-a",
            "--project-root",
            str(project_root),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert "Файл уже существует:" in result.output
    assert output.name in result.output


def test_java_analysis_passes_force_flag_to_exporter(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    project_root = tmp_path
    output = project_root / "model.duckdb"
    calls: list[tuple[Path, Sequence[str], Path, str, bool]] = []

    def export_to_duckdb(
        project_root: Path,
        module_names: Sequence[str],
        output_path: Path,
        repo_name: str,
        force: bool = False,
    ) -> None:
        calls.append((project_root, module_names, output_path, repo_name, force))

    monkeypatch.setattr("codeduck.cli.export_to_duckdb", export_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "java",
            "module-a",
            "--project-root",
            str(project_root),
            "--output",
            str(output),
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(project_root.resolve(), ("module-a",), output, project_root.name, True)]


def test_python_analysis_is_registered_under_analyze() -> None:
    result = CliRunner().invoke(app, ["analyze", "--help"])

    assert result.exit_code == 0
    assert "python" in result.output


def test_python_analysis_passes_packages_to_exporter(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    package_a = tmp_path / "myapp"
    package_b = tmp_path / "otherpkg"
    package_a.mkdir()
    package_b.mkdir()
    output = tmp_path / "model.duckdb"
    calls: list[tuple[Sequence[Path], Path, str, bool]] = []

    def export_python_to_duckdb(
        package_dirs: Sequence[Path],
        output_path: Path,
        repo_name: str,
        force: bool = False,
    ) -> None:
        calls.append((list(package_dirs), output_path, repo_name, force))

    monkeypatch.setattr("codeduck.cli.export_python_to_duckdb", export_python_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "python",
            "-p",
            str(package_a),
            "-p",
            str(package_b),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    # repo_name по умолчанию — имя первого каталога-пакета.
    assert calls == [([package_a.resolve(), package_b.resolve()], output, "myapp", False)]


def test_python_analysis_requires_at_least_one_package() -> None:
    result = CliRunner().invoke(app, ["analyze", "python", "--output", "model.duckdb"])

    assert result.exit_code == 2
    assert "Укажите хотя бы один каталог-пакет" in result.output


def test_python_analysis_rejects_missing_package_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = CliRunner().invoke(
        app,
        ["analyze", "python", "-p", str(missing), "--output", str(tmp_path / "model.duckdb")],
    )

    assert result.exit_code == 2
    assert "не найден или не является каталогом" in result.output
