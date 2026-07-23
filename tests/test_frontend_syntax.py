"""Fail the suite if frontend JavaScript cannot parse.

This catches the class of bug that left HEAD dead after an extra brace
was committed in frontend/index.js (channels never loaded because init never ran).
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_JS = REPO_ROOT / "frontend" / "index.js"
HISTORY_STATE_JS = REPO_ROOT / "frontend" / "history_state.js"
HISTORY_STATE_TEST = REPO_ROOT / "tests" / "frontend_history_state.test.js"


class TestFrontendSyntax(unittest.TestCase):
    def test_index_js_parses(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for frontend syntax checks")
        self.assertTrue(INDEX_JS.is_file(), f"missing {INDEX_JS}")
        result = subprocess.run(
            [node, "--check", str(INDEX_JS)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                "frontend/index.js failed node --check\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )

    def test_history_state_behaviors(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for frontend history tests")
        self.assertTrue(HISTORY_STATE_JS.is_file(), f"missing {HISTORY_STATE_JS}")
        self.assertTrue(HISTORY_STATE_TEST.is_file(), f"missing {HISTORY_STATE_TEST}")
        result = subprocess.run(
            [node, "--test", str(HISTORY_STATE_TEST)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                "frontend history-state tests failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
