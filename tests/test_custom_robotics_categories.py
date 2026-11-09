"""v4.3: Robotics topic recognition and persistent user categories."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
from categories import classify_multiple, classify

ROBOT = {
    'id': 999101, 'full_name': 'test/robot-arm-toolkit',
    'html_url': 'https://github.com/test/robot-arm-toolkit',
    'description': 'ROS 2 robotics software for robot arm control',
    'language': 'Python', 'topics': ['robotics', 'ros2', 'linux'],
    'stargazers_count': 150,
}
OTHER = {
    'id': 999102, 'full_name': 'test/strange-new-project',
    'html_url': 'https://github.com/test/strange-new-project',
    'description': 'An experimental widget with no known category',
    'language': 'Python', 'topics': ['strange-unknown-topic'],
    'stargazers_count': 50,
}

class CustomCategoriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p1 = patch.object(server, 'DATA_DIR', Path(self.tmp.name))
        self.p2 = patch.object(server, 'DB_PATH', Path(self.tmp.name) / 'test.sqlite3')
        self.p1.start(); self.p2.start()
        server.init_db()
    def tearDown(self):
        self.p2.stop(); self.p1.stop(); self.tmp.cleanup()

    def test_robotics_and_multi_system(self):
        self.assertEqual(classify(ROBOT), 'Robotics')
        server.upsert_repo(ROBOT)
        self.assertEqual(server.list_repos(category='Robotics')['total'], 1)
        self.assertEqual(server.list_repos(category='Linux Tools')['total'], 1)
        self.assertEqual(server.stats()['total'], 1)
        self.assertIn('Robotics', server.get_repo(ROBOT['id'])['categories'])

    def test_custom_category_and_manual_tags_survive_restart(self):
        server.upsert_repo(ROBOT)
        server.upsert_repo(OTHER)
        self.assertEqual(classify(OTHER), 'Other')
        cat = server.create_category('3D Printing')
        self.assertEqual(cat['custom'], True)
        self.assertIn('3D Printing', server.valid_category_names())
        server.update_repo(ROBOT['id'], {'category': '3D Printing', 'categories': ['3D Printing', 'Robotics', 'Linux Tools'], 'notes': 'manual test'})
        server.init_db()
        server.upsert_repo({**ROBOT, 'stargazers_count': 200})
        self.assertEqual(server.list_repos(category='3D Printing')['total'], 1)
        self.assertEqual(set(server.get_repo(ROBOT['id'])['categories']), {'3D Printing','Robotics','Linux Tools'})
        self.assertEqual(server.get_repo(ROBOT['id'])['notes'], 'manual test')
        self.assertEqual(server.get_repo(ROBOT['id'])['stars'], 200)
        self.assertEqual(server.list_repos(category='Other')['total'], 1)
        with self.assertRaises(ValueError): server.create_category('3d printing')
        with self.assertRaises(ValueError): server.create_category('Robotics')
        with self.assertRaises(ValueError): server.create_category('All')
        with self.assertRaises(ValueError): server.update_repo(ROBOT['id'], {'categories': []})

    def test_manual_primary_not_overwritten_on_existing_unknown(self):
        server.upsert_repo(OTHER)
        server.create_category('Hardware')
        server.update_repo(OTHER['id'], {'category':'Hardware','categories':['Hardware']})
        server.init_db()
        server.upsert_repo({**OTHER, 'topics': ['robotics']})
        self.assertEqual(server.get_repo(OTHER['id'])['categories'], ['Hardware'])

    def test_existing_unknown_robot_migrated(self):
        server.upsert_repo(ROBOT)
        with server.connect() as db:
            db.execute("UPDATE repos SET category='Other' WHERE id=?", (ROBOT['id'],))
            db.execute('DELETE FROM repo_categories WHERE repo_id=?', (ROBOT['id'],))
            db.execute("INSERT INTO repo_categories(repo_id,category) VALUES (?, 'Other')", (ROBOT['id'],))
        server.init_db()
        self.assertEqual(server.get_repo(ROBOT['id'])['category'], 'Robotics')
        self.assertEqual(server.list_repos(search='Robotics')['total'], 1)

    def test_categories_http_endpoint(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
        base=f'http://127.0.0.1:{httpd.server_port}'
        try:
            req=urllib.request.Request(base+'/api/categories', data=json.dumps({'name':'Smart Home'}).encode(), method='POST', headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=4) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.load(response)['name'], 'Smart Home')
            with urllib.request.urlopen(base+'/api/categories', timeout=4) as response:
                values=json.load(response)
            self.assertTrue(any(c['name']=='Smart Home' and c['custom'] for c in values))
            self.assertTrue(any(c['name']=='Robotics' and c['query'] for c in values))
        finally:
            httpd.shutdown();httpd.server_close();t.join(2)

if __name__ == '__main__': unittest.main()
