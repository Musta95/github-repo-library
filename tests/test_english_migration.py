"""Regression tests for translating existing German built-in category labels."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from categories import DEFAULT_CATEGORY, CATEGORIES

SAMPLE = {
    "id": 940001,
    "full_name": "test/migrated-robot-arm",
    "html_url": "https://github.com/test/migrated-robot-arm",
    "description": "ROS 2 robotics project for Linux",
    "language": "Python",
    "topics": ["robotics", "linux"],
    "stargazers_count": 12,
}
UNKNOWN = {
    "id": 940002,
    "full_name": "test/unknown-project",
    "html_url": "https://github.com/test/unknown-project",
    "description": "Something not covered by built-in categories",
    "topics": ["unmapped-topic"],
}


class EnglishMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = patch.object(server, "DATA_DIR", Path(self.tmp.name))
        self.b = patch.object(server, "DB_PATH", Path(self.tmp.name) / "migration.sqlite3")
        self.a.start()
        self.b.start()
        server.init_db()

    def tearDown(self):
        self.b.stop()
        self.a.stop()
        self.tmp.cleanup()

    def test_legacy_manual_categories_and_personal_data_survive(self):
        server.upsert_repo(SAMPLE)
        saved_list = server.create_list("My custom list", "keep original list content")
        server.set_list_membership(saved_list["id"], SAMPLE["id"])
        server.update_repo(SAMPLE["id"], {
            "category": "Robotics", "categories": ["Robotics", "Linux Tools", "Other"],
            "notes": "Do not translate my notes", "favorite": True,
        })
        # Simulate database rows written by v4.3, where these categories
        # were still stored using their original German built-in labels.
        with server.connect() as db:
            db.execute("UPDATE repos SET category='Robotik' WHERE id=?", (SAMPLE["id"],))
            db.execute("UPDATE repo_categories SET category='Robotik' WHERE repo_id=? AND category='Robotics'", (SAMPLE["id"],))
            db.execute("UPDATE repo_categories SET category='Sonstiges' WHERE repo_id=? AND category='Other'", (SAMPLE["id"],))
        server.init_db()
        server.init_db()  # migration is safe to repeat
        record = server.get_repo(SAMPLE["id"])
        self.assertEqual(record["category"], "Robotics")
        self.assertEqual(set(record["categories"]), {"Robotics", "Linux Tools", "Other"})
        self.assertTrue(record["categories_manual"])
        self.assertTrue(record["favorite"])
        self.assertEqual(record["notes"], "Do not translate my notes")
        self.assertEqual(record["list_ids"], [saved_list["id"]])
        self.assertEqual(server.get_list(saved_list["id"])["description"], "keep original list content")
        self.assertEqual(server.list_repos(search="Robotics")["total"], 1)
        self.assertEqual(server.list_repos(category="Robotics")["total"], 1)
        server.upsert_repo(SAMPLE)  # automatic reimport must preserve manual choices
        self.assertEqual(set(server.get_repo(SAMPLE["id"])["categories"]), {"Robotics", "Linux Tools", "Other"})

    def test_legacy_other_is_migrated_without_duplicate_tags(self):
        server.upsert_repo(UNKNOWN)
        with server.connect() as db:
            db.execute("UPDATE repos SET category='Sonstiges' WHERE id=?", (UNKNOWN["id"],))
            db.execute("UPDATE repo_categories SET category='Sonstiges' WHERE repo_id=?", (UNKNOWN["id"],))
            # Build a mixed English/German row, as could happen after a partial upgrade.
            db.execute("INSERT OR IGNORE INTO repo_categories(repo_id,category) VALUES (?, 'Other')", (UNKNOWN["id"],))
        server.init_db()
        record = server.get_repo(UNKNOWN["id"])
        self.assertEqual(record["category"], "Other")
        self.assertEqual(record["categories"], ["Other"])
        self.assertEqual(server.list_repos(category="Other")["total"], 1)

    def test_new_categories_are_english(self):
        self.assertEqual(DEFAULT_CATEGORY, "Other")
        self.assertIn("Web Design", CATEGORIES)
        self.assertIn("AI & Machine Learning", CATEGORIES)
        self.assertIn("Robotics", CATEGORIES)
        self.assertNotIn("Robotik", CATEGORIES)


if __name__ == "__main__":
    unittest.main()
