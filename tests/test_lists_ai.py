import base64
import json
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server

SAMPLE = {
    "id": 222001, "full_name": "sample/website-template",
    "html_url": "https://github.com/sample/website-template",
    "description": "Frontend UI components for web design",
    "language": "TypeScript", "stargazers_count": 350,
    "forks_count": 18, "topics": ["web-design"],
    "license": {"spdx_id": "MIT"}, "pushed_at": "2026-11-09T09:00:00Z"
}

class ListsAndAITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data = server.DATA_DIR
        self.original_path = server.DB_PATH
        self.original_key = server.AI_API_KEY
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = Path(self.tmp.name) / 'repos.sqlite3'
        server.AI_API_KEY = 'test-key-not-secret'
        server.init_db()
        server.upsert_repo(SAMPLE)

    def tearDown(self):
        server.DB_PATH = self.original_path
        server.DATA_DIR = self.original_data
        server.AI_API_KEY = self.original_key
        self.tmp.cleanup()

    def test_list_membership_persistent_and_multiple_lists(self):
        one=server.create_list('Web Design Ideas','For the next website')
        two=server.create_list('Read Later')
        self.assertEqual(one['name'], 'Web Design Ideas')
        server.set_list_membership(one['id'],SAMPLE['id'])
        server.set_list_membership(two['id'],SAMPLE['id'])
        self.assertEqual(server.list_repos(list_id=one['id'])['total'],1)
        self.assertEqual(server.list_repos(list_id=two['id'])['total'],1)
        self.assertEqual(len(server.get_repo(SAMPLE['id'])['list_ids']),2)
        self.assertEqual(server.get_lists()[0]['repo_count'],1)
        # Simulate re-start and reimport; manual category, notes and membership remain.
        server.update_repo(SAMPLE['id'], {'category':'Developer Tools','notes':'From Instagram'})
        server.init_db()
        server.upsert_repo({**SAMPLE, 'stargazers_count': 999})
        result=server.get_repo(SAMPLE['id'])
        self.assertEqual(result['category'],'Developer Tools')
        self.assertEqual(result['notes'],'From Instagram')
        self.assertEqual(result['stars'],999)
        self.assertEqual(result['list_ids'],[one['id'],two['id']])
        # Delete collection, not repository.
        self.assertTrue(server.delete_list(one['id']))
        self.assertEqual(server.list_repos(list_id=two['id'])['total'],1)
        self.assertEqual(server.stats()['total'],1)
        server.remove_repo(SAMPLE['id'])
        self.assertEqual(server.get_list(two['id'])['repo_ids'],[])

    def test_list_rename_and_duplicate_validation(self):
        a=server.create_list('KI Ideen')
        server.update_list(a['id'], {'name':'AI Ideas','description':'use later'})
        self.assertEqual(server.get_list(a['id'])['description'],'use later')
        with self.assertRaises(ValueError):server.create_list('ai ideas')
        with self.assertRaises(ValueError):server.set_list_membership(9999, SAMPLE['id'])
        with self.assertRaises(ValueError):server.set_list_membership(a['id'],999999)

    def test_readme_and_safe_clone_access(self):
        data=server.repository_access(SAMPLE['id'])
        self.assertEqual(data['clone_url'],'https://github.com/sample/website-template.git')
        self.assertEqual(data['clone_command'],'git clone --depth 1 https://github.com/sample/website-template.git')
        readme={'encoding':'base64','content':base64.b64encode(b'# Hello\nThis is an example').decode('ascii')}
        with patch.object(server,'github_get',return_value=readme) as github:
            obj=server.repository_readme(SAMPLE['id'])
        self.assertIn('# Hello', obj['readme'])
        self.assertFalse(obj['truncated'])
        github.assert_called_once_with('/repos/sample/website-template/readme')

    def test_http_api_for_lists_and_ai_key(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{httpd.server_port}'
        def request(path, method='GET', data=None, key=None):
            headers={'Content-Type':'application/json'}
            if key: headers['X-API-Key']=key
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
            with urllib.request.urlopen(req,timeout=5) as resp:
                return json.load(resp)
        try:
            newlist=request('/api/lists','POST',{'name':'Von Instagram'})
            self.assertEqual(newlist['repo_ids'],[])
            imported={**SAMPLE, 'id': 222002, 'full_name':'sample/another-project','html_url':'https://github.com/sample/another-project'}
            with patch.object(server,'github_get',return_value=imported):
                saved=request('/api/import-url','POST',{'url':'https://github.com/sample/another-project/tree/main','list_id':newlist['id']})
            self.assertEqual(saved['list_id'],newlist['id'])
            self.assertEqual(request('/api/repos?list_id='+str(newlist['id']))['total'],1)
            with self.assertRaises(urllib.error.HTTPError) as err:
                request('/api/ai/search?q=web')
            self.assertEqual(err.exception.code,401)
            data=request('/api/ai/search?q=web',key=server.AI_API_KEY)
            self.assertGreaterEqual(data['total'],1)
            self.assertEqual(request('/api/ai/repos/'+str(saved['id']),key=server.AI_API_KEY)['github_url'],'https://github.com/sample/another-project')
            request('/api/lists/'+str(newlist['id'])+'/repos/'+str(saved['id']),method='DELETE')
            self.assertEqual(request('/api/repos?list_id='+str(newlist['id']))['total'],0)
            request('/api/lists/'+str(newlist['id']),method='DELETE')
            self.assertEqual(request('/api/repos')['total'],2)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

if __name__ == '__main__': unittest.main()
