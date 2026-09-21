"""mdstats 测试（unittest）。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from mdstats import stats

SAMPLE = Path(__file__).parent / "sample.md"


class StatsTest(unittest.TestCase):
    def test_line_count(self):
        lines, _ = stats(str(SAMPLE))
        self.assertEqual(lines, 4)

    def test_word_count(self):
        _, words = stats(str(SAMPLE))
        self.assertEqual(words, 5)

    def test_first_word_not_bom_contaminated(self):
        with open(SAMPLE, encoding="utf-8-sig") as f:
            text = f.read()
        first = text.split()[0]
        self.assertNotIn("\ufeff", first)


class CliTest(unittest.TestCase):
    def test_text_output(self):
        out = subprocess.run(
            [sys.executable, "mdstats.py", str(SAMPLE)],
            capture_output=True, text=True, cwd=Path(__file__).parent,
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("4 行", out.stdout)

    def test_json_output(self):
        out = subprocess.run(
            [sys.executable, "mdstats.py", str(SAMPLE), "--json"],
            capture_output=True, text=True, cwd=Path(__file__).parent,
        )
        self.assertEqual(out.returncode, 0)
        data = json.loads(out.stdout)
        self.assertEqual(data["lines"], 4)
        self.assertEqual(data["words"], 5)

    def test_lines_only_text_output(self):
        out = subprocess.run(
            [sys.executable, "mdstats.py", str(SAMPLE), "--lines-only"],
            capture_output=True, text=True, cwd=Path(__file__).parent,
        )
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "4 行")

    def test_lines_only_json_output(self):
        out = subprocess.run(
            [sys.executable, "mdstats.py", str(SAMPLE), "--json", "--lines-only"],
            capture_output=True, text=True, cwd=Path(__file__).parent,
        )
        self.assertEqual(out.returncode, 0)
        data = json.loads(out.stdout)
        self.assertEqual(data, {"file": str(SAMPLE), "lines": 4})


if __name__ == "__main__":
    unittest.main()
