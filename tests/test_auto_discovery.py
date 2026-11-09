import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import server

SAMPLE = {
    'id': 88001,
    'full_name': 'example/new-design-project',
    'html_url': 'https://github.com/example/new-design-project',
    'description': 'UI kit for web design',
    'language': 'TypeScript',
    'stargazers_count': 456,
    'forks_count': 12,
    'topics': ['web-design'],
    'license': {'spdx_id': 'MIT'},
    'pushed_at': '2026-11-09T09:00:00Z',
}

class DailyDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir, self.old_db = server.DATA_DIR, server.DB_PATH
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = server.DATA_DIR / 'repos.sqlite3'
        server.init_db()

    def tearDown(self):
        server.DATA_DIR, server.DB_PATH = self.old_dir, self.old_db
        self.tmp.cleanup()

    def test_daily_scan_runs_once_per_day_and_preserves_edit_fields(self):
        calls = []
        def fake_get(path, params):
            calls.append((path, params))
            return {'items': [SAMPLE]}
        with patch.object(server, 'CATEGORIES', {'Web Design': {'query': 'topic:web-design stars:>50'}}):
            self.assertTrue(server.auto_discover_once('2026-11-09', fake_get))
            status = server.auto_discover_status()['last_run']
            self.assertEqual(status['last_day'], '2026-11-09')
            self.assertEqual((status['added'], status['processed'], status['categories_done']), (1,1,1))
            self.assertEqual(status['result'], 'success')
            self.assertEqual(calls[0][1]['sort'], 'updated')
            self.assertEqual(server.list_repos(sort='synced')['total'], 1)
            server.update_repo(SAMPLE['id'], {'favorite': True, 'notes':'Saved from Instagram', 'category':'Other'})
            self.assertFalse(server.auto_discover_once('2026-11-09', fake_get))
            self.assertEqual(len(calls), 1)
            self.assertTrue(server.auto_discover_once('2026-11-10', fake_get))
        data = server.get_repo(SAMPLE['id'])
        self.assertEqual(data['category'], 'Other')
        self.assertEqual(data['notes'], 'Saved from Instagram')
        self.assertTrue(data['favorite'])
        self.assertEqual(server.stats()['total'], 1)

    def test_error_stops_run_does_not_retry_same_day(self):
        def error(*args):
            raise server.GithubError('Rate limit reached')
        with patch.object(server, 'CATEGORIES', {'Web Design': {'query':'topic:web-design'}}):
            self.assertTrue(server.auto_discover_once('2026-11-09', error))
            status = server.auto_discover_status()['last_run']
            self.assertEqual(status['result'],'partial')
            self.assertIn('Rate limit',status['error'])
            self.assertFalse(server.auto_discover_once('2026-11-09', error))

    def test_local_timezone_exists(self):
        self.assertEqual(datetime(2026,11,9,3,0,tzinfo=ZoneInfo('Europe/Berlin')).utcoffset().total_seconds(),3600)

if __name__=='__main__':
    unittest.main()
