import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from codeduck.cli import app


class CodeduckCliTest(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_java_analysis_is_registered_under_analyze(self):
        result = self.runner.invoke(app, ["analyze", "--help"])

        self.assertEqual(0, result.exit_code)
        self.assertIn("java", result.output)

    @patch("codeduck.cli.export_to_duckdb")
    def test_java_analysis_passes_modules_to_exporter(self, export):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            output = project_root / "model.duckdb"

            result = self.runner.invoke(
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

        self.assertEqual(0, result.exit_code, result.output)
        export.assert_called_once_with(project_root.resolve(), ("module-a",), output, project_root.name)

    def test_java_analysis_requires_maven_layout(self):
        result = self.runner.invoke(app, ["analyze", "java", "module-a", "--output", "model.duckdb"])

        self.assertEqual(2, result.exit_code)
        self.assertIn("укажите --maven", result.output)
