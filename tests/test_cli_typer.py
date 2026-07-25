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
    calls: list[tuple[Path, Sequence[str], Path, str]] = []

    def export_to_duckdb(project_root: Path, module_names: Sequence[str], output_path: Path, repo_name: str) -> None:
        calls.append((project_root, module_names, output_path, repo_name))

    monkeypatch.setattr("codeduck.cli.export_to_duckdb", export_to_duckdb)
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "java",
            "module-a",
            "--maven",
            "--project-root",
            str(project_root),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(project_root.resolve(), ("module-a",), output, project_root.name)]


def test_java_analysis_requires_maven_layout() -> None:
    result = CliRunner().invoke(app, ["analyze", "java", "module-a", "--output", "model.duckdb"])

    assert result.exit_code == 2
    assert "укажите --maven" in result.output
