import json
import tempfile
import unittest
from pathlib import Path

from gtasks.release import bump_patch_release, next_patch_version


class ReleaseCommandTests(unittest.TestCase):
    def test_next_version_always_increments_only_patch(self) -> None:
        self.assertEqual(next_patch_version("V0.0.1"), "V0.0.2")
        self.assertEqual(next_patch_version("V2.7.9"), "V2.7.10")

    def test_bump_appends_one_user_facing_entry_and_updates_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "releases.json"
            catalog.write_text(
                json.dumps(
                    {
                        "current_version": "V0.0.1",
                        "releases": [
                            {
                                "version": "V0.0.1",
                                "date": "2026-07-30",
                                "title": "Initial baseline",
                                "summary": "Initial verified behavior.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            version = bump_patch_release(
                catalog,
                title="  Next actions  ",
                summary="  Added verified editing.  ",
                release_date="2026-07-30",
            )

            payload = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertEqual(version, "V0.0.2")
            self.assertEqual(payload["current_version"], "V0.0.2")
            self.assertEqual(
                payload["releases"][-1],
                {
                    "version": "V0.0.2",
                    "date": "2026-07-30",
                    "title": "Next actions",
                    "summary": "Added verified editing.",
                },
            )

    def test_bump_refuses_catalog_drift_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "releases.json"
            catalog.write_text(
                json.dumps(
                    {
                        "current_version": "V0.0.2",
                        "releases": [{"version": "V0.0.1"}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match"):
                bump_patch_release(
                    catalog,
                    title="Change",
                    summary="Changed behavior.",
                    release_date="2026-07-30",
                )


if __name__ == "__main__":
    unittest.main()
