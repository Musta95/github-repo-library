import os
import tempfile
import unittest
from unittest.mock import patch

import server
from categories import CATEGORIES, classify

SAMPLE = {
    "id": 100001,
    "full_name": "example/frida-kit",
    "html_url": "https://github.com/example/frida-kit",
    "description": "Reverse engineering tools for debugging binaries",
    "language": "Python",
    "stargazers_count": 1234,
    "forks_count": 42,
    "topics": ["reverse-engineering", "binary-analysis"],
    "license": {"spdx_id": "MIT"},
    "pushed_at": "2026-11-08T10:00:00Z"
}

class RepoLibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data = server.DATA_DIR
        server.DATA_DIR = __import__('pathlib').Path(self.temp.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.init_db()

    def tearDown(self):
        server.DATA_DIR = self.original_data
        server.DB_PATH = server.DATA_DIR / "repos.sqlite3"
        self.temp.cleanup()

    def test_import_and_fulltext_search(self):
        self.assertEqual(classify(SAMPLE), "Reverse Engineering")
        server.upsert_repo(SAMPLE)
        result = server.list_repos('revers eng')
        self.assertEqual(result['total'], 1)
        r = result['items'][0]
        self.assertEqual(r['category'], "Reverse Engineering")
        self.assertEqual(r['stars'], 1234)
        self.assertEqual(server.list_repos('unrelated')['total'], 0)

    def test_no_duplicate_and_preserve_edited_fields(self):
        server.upsert_repo(SAMPLE)
        server.update_repo(100001, {"notes":"For IT projects", "favorite":True, "category":"Developer Tools"})
        new = {**SAMPLE, "stargazers_count": 2000}
        server.upsert_repo(new, 'Reverse Engineering')
        res = server.list_repos('IT-Proj')
        self.assertEqual(res['total'], 1)
        row = res['items'][0]
        self.assertEqual(row['category'], 'Developer Tools')
        self.assertTrue(row['favorite'])
        self.assertEqual(row['notes'], 'For IT projects')
        self.assertEqual(row['stars'], 2000)
        self.assertEqual(server.stats()['total'],1)
        server.remove_repo(100001)
        self.assertEqual(server.stats()['total'],0)

    def test_url_validation(self):
        self.assertEqual(server.parse_repo_url('https://github.com/example/frida-kit/tree/main'), ('example','frida-kit'))
        self.assertEqual(server.parse_repo_url('https://github.com/example/frida-kit.git'), ('example','frida-kit'))
        for url in ['http://github.com/a/b', 'https://example.org/a/b', 'https://github.com/a', 'file:///etc/passwd']:
            with self.assertRaises(ValueError): server.parse_repo_url(url)

    def test_categories(self):
        self.assertIn('Web Design',CATEGORIES)
        self.assertIn('Reverse Engineering',CATEGORIES)
        self.assertEqual(classify({"description":"unknown", "topics":[],"full_name":"x/y"}), 'Other')

if __name__ == '__main__': unittest.main()
