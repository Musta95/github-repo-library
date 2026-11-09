"""Regression: GitHub descriptions can mention OSes without OS topic tags."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from categories import classify_multiple, platforms_from_description

ANYPS5 = {
    "id": 98275611,
    "full_name": "boykopovar/AnyPS5",
    "html_url": "https://github.com/boykopovar/AnyPS5",
    "description": "Tool for automatic PS5 executables porting to Linux and Windows",
    "language": "C++",
    "stargazers_count": 51000,
    "topics": ["anyps5", "dynamic-library", "game-porting", "ps5", "vulkan"],
}


class DescriptionPlatformsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.p1 = patch.object(server, "DATA_DIR", self.data)
        self.p2 = patch.object(server, "DB_PATH", self.data / "repos.sqlite3")
        self.p1.start()
        self.p2.start()
        server.init_db()

    def tearDown(self):
        self.p2.stop()
        self.p1.stop()
        self.tmp.cleanup()

    def test_description_with_two_oses(self):
        self.assertEqual(platforms_from_description(ANYPS5["description"]),
                         {"Windows Tools", "Linux Tools"})
        found = set(classify_multiple(ANYPS5, "Windows Tools"))
        self.assertIn("Linux Tools", found)
        self.assertIn("Windows Tools", found)
        self.assertNotIn("macOS Tools", found)

    def test_platform_topics_still_work_without_description(self):
        result = set(classify_multiple({"topics": ["windows", "macos", "linux"],
                                        "description": "A command-line tool"}))
        self.assertEqual(result & {"Windows Tools", "macOS Tools", "Linux Tools"},
                         {"Windows Tools", "macOS Tools", "Linux Tools"})

    def test_only_cross_platform_topic_is_not_enough(self):
        self.assertFalse(platforms_from_description("A cross-platform developer utility"))
        self.assertFalse(set(classify_multiple({"topics": ["cross-platform"],
                                                   "description": "A cross-platform utility"})) &
                         {"Windows Tools", "macOS Tools", "Linux Tools"})

    def test_existing_repo_migrated_from_saved_description(self):
        server.upsert_repo(ANYPS5, "Windows Tools")
        with server.connect() as db:
            # Simulate the old version that assigned only a single OS category.
            db.execute("DELETE FROM repo_categories WHERE repo_id=?", (ANYPS5["id"],))
            db.execute("INSERT INTO repo_categories(repo_id,category) VALUES (?,?)",
                       (ANYPS5["id"], "Windows Tools"))
        server.init_db()
        self.assertEqual(server.list_repos(category="Linux Tools")["total"], 1)
        self.assertEqual(server.list_repos(category="Windows Tools")["total"], 1)
        self.assertEqual(server.list_repos(category="macOS Tools")["total"], 0)
        self.assertEqual(server.stats()["total"], 1)

    def test_manual_choice_remains_after_migration_and_reimport(self):
        server.upsert_repo(ANYPS5)
        server.update_repo(ANYPS5["id"], {"category": "Developer Tools",
            "categories": ["Developer Tools", "Windows Tools"],
            "notes": "Reviewed personally", "favorite": True})
        server.init_db()
        server.upsert_repo({**ANYPS5, "stargazers_count": 53000})
        repo = server.get_repo(ANYPS5["id"])
        self.assertEqual(set(repo["categories"]), {"Developer Tools", "Windows Tools"})
        self.assertEqual(repo["notes"], "Reviewed personally")
        self.assertTrue(repo["favorite"])
        self.assertEqual(repo["stars"], 53000)
        self.assertEqual(server.list_repos(category="Linux Tools")["total"], 0)


if __name__ == "__main__":
    unittest.main()
