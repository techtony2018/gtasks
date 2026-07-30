import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_board_and_status_editor_are_first_class_navigation(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="board"', html)
        self.assertIn('data-count="board"', html)
        self.assertIn('id="task-status-select"', html)
        self.assertIn('id="task-status-save"', html)
        for status in (
            "planned",
            "active",
            "waiting",
            "blocked",
            "completed",
            "cancelled",
        ):
            self.assertIn(f'<option value="{status}">', html)

        self.assertIn("function renderBoard()", javascript)
        self.assertIn("/status`", javascript)
        self.assertIn("renderBoard()", javascript)


if __name__ == "__main__":
    unittest.main()
