import unittest

from gtasks import __version__
from gtasks.releases import CURRENT_RELEASE, RELEASES


class ReleaseCatalogTests(unittest.TestCase):
    def test_initial_release_is_exactly_v0_0_1(self) -> None:
        self.assertEqual(RELEASES[0]["version"], "V0.0.1")

    def test_runtime_version_is_the_latest_catalog_entry(self) -> None:
        self.assertEqual(CURRENT_RELEASE["version"], RELEASES[-1]["version"])
        self.assertEqual(__version__, CURRENT_RELEASE["version"])

    def test_every_release_has_user_facing_history_fields(self) -> None:
        versions: set[str] = set()
        for release in RELEASES:
            self.assertRegex(release["version"], r"^V\d+\.\d+\.\d+$")
            self.assertRegex(release["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(release["title"].strip())
            self.assertTrue(release["summary"].strip())
            self.assertNotIn("commit", release["summary"].lower())
            self.assertNotIn(release["version"], versions)
            versions.add(release["version"])


if __name__ == "__main__":
    unittest.main()
