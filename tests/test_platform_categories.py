import tempfile
import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from categories import CATEGORIES, classify_multiple

PLEXO = {
    'id': 202699,
    'full_name': 'anmolkapil/plexo',
    'html_url': 'https://github.com/anmolkapil/plexo',
    'description': 'Speed up downloads by combining multiple network connections in parallel',
    'language': 'TypeScript',
    'stargazers_count': 5600,
    'topics': ['bandwidth-aggregation', 'cross-platform', 'desktop-app', 'linux', 'macos', 'windows'],
    'pushed_at': '2026-11-09T00:00:00Z',
}

class PlatformCategoriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch1=patch.object(server,'DATA_DIR',Path(self.tmp.name))
        self.patch2=patch.object(server,'DB_PATH',Path(self.tmp.name)/'repos.sqlite3')
        self.patch1.start();self.patch2.start()
        server.init_db()

    def tearDown(self):
        self.patch2.stop();self.patch1.stop();self.tmp.cleanup()

    def test_platforms_are_distinct_and_plexo_in_all(self):
        for cat in ('Windows Tools','macOS Tools','Linux Tools'):
            self.assertIn(cat, CATEGORIES)
        self.assertEqual(set(classify_multiple(PLEXO)) & {'Windows Tools','macOS Tools','Linux Tools'},
                         {'Windows Tools','macOS Tools','Linux Tools'})
        server.upsert_repo(PLEXO)
        for cat in ('Windows Tools','macOS Tools','Linux Tools'):
            self.assertEqual(server.list_repos(category=cat)['total'],1)
        self.assertEqual(server.stats()['total'],1)

    def test_manual_categories_persist_after_reimport(self):
        server.upsert_repo(PLEXO)
        server.update_repo(PLEXO['id'],{'category':'Developer Tools',
            'categories':['Developer Tools','Windows Tools','Linux Tools'],
            'notes':'Manual note from Instagram','favorite':True})
        server.upsert_repo(PLEXO)
        server.init_db()
        result=server.get_repo(PLEXO['id'])
        self.assertEqual(set(result['categories']),{'Developer Tools','Windows Tools','Linux Tools'})
        self.assertEqual(result['category'],'Developer Tools')
        self.assertEqual(result['notes'],'Manual note from Instagram')
        self.assertTrue(result['favorite'])
        self.assertEqual(server.list_repos(category='macOS Tools')['total'],0)

    def test_search_and_pagination(self):
        for num in range(92):
            server.upsert_repo({**PLEXO, 'id': 500000 + num, 'full_name': f'example/plexo-tool-{num}',
                                'html_url': f'https://github.com/example/plexo-tool-{num}', 'stargazers_count':num})
        result=server.list_repos(page=2)
        self.assertEqual((result['total'], result['page'], len(result['items'])),(92,2,40))
        result=server.list_repos(page=3)
        self.assertEqual((result['page'],len(result['items'])),(3,12))
        result=server.list_repos(page=100)
        self.assertEqual((result['page'],len(result['items'])),(3,12))
        result=server.list_repos(search='plexo')
        self.assertEqual(result['total'],92)
        result=server.list_repos(search='tool-50')
        self.assertEqual(result['total'],1)
        result=server.list_repos(search='plexo',category='Linux Tools',page=2)
        self.assertEqual((result['total'],len(result['items'])),(92,40))


    def test_http_search_and_page_2_return_valid_json(self):
        for num in range(48):
            server.upsert_repo({**PLEXO,'id':700000+num,'full_name':f'example/plexo-{num}',
                'html_url':f'https://github.com/example/plexo-{num}'})
        httpd=ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True)
        thread.start()
        try:
            base=f'http://127.0.0.1:{httpd.server_port}'
            def get(params):
                with urllib.request.urlopen(base+'/api/repos?'+urllib.parse.urlencode(params),timeout=4) as r:
                    self.assertEqual(r.status,200)
                    return json.load(r)
            second=get({'page':2,'category':'Linux Tools'})
            self.assertEqual((second['total'],second['page'],len(second['items'])),(48,2,8))
            result=get({'q':'plexo-22','category':'macOS Tools'})
            self.assertEqual(result['total'],1)
            self.assertEqual(result['items'][0]['full_name'],'example/plexo-22')
        finally:
            httpd.shutdown();httpd.server_close();thread.join(2)

    def test_migrate_old_category_without_losing_repo(self):
        server.upsert_repo(PLEXO)
        with server.connect() as db:
            db.execute('DELETE FROM repo_categories')
        server.init_db()
        self.assertGreater(server.list_repos(category=server.get_repo(PLEXO['id'])['category'])['total'],0)

if __name__=='__main__': unittest.main()
