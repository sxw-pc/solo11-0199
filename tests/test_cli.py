import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", "-m", "cinema", *map(str, args)], cwd=ROOT,
                              check=False, capture_output=True, text=True, encoding="utf-8")

    def test_cli_persists_independent_eligibility_inputs_and_demo_does_not_touch_database(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            path = Path(folder) / "field.sqlite"
            prefix = ("--db", path)
            command = self.cli(*prefix, "init")
            self.assertEqual(command.returncode, 0, command.stderr)
            self.assertEqual(json.loads(command.stdout), {"schema_version": 1})
            for feed in ("cycles", "targets", "roster", "scope"):
                command = self.cli(*prefix, "import", feed, ROOT / "examples" / f"{feed}.csv")
                self.assertEqual(command.returncode, 0, command.stderr)
            command = self.cli(*prefix, "eligibility", "nov26", "ridge-hall", "h-r1",
                               "2026-11-10T10:00:00Z", "2026-11-10T10:30:00Z")
            self.assertEqual(json.loads(command.stdout)["decision"], "eligible")
            command = self.cli(*prefix, "demo")
            self.assertTrue(json.loads(command.stdout)["replayed_all_three_ledgers"])
            command = self.cli(*prefix, "audit")
            self.assertEqual(json.loads(command.stdout)["receipts"], 4)

    def test_uninitialized_cli_fails_without_creating_database(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            path = Path(folder) / "missing.sqlite"
            command = self.cli("--db", path, "coverage", "nov26")
            self.assertEqual(command.returncode, 2)
            self.assertIn("run init", command.stderr)
            self.assertFalse(path.exists())
