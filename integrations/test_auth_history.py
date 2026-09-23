import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_history import Store, hash_password, verify_password


class HistoryStoreTests(unittest.TestCase):
    def test_claude_acceptance_keeps_distinct_history_kind_and_rejects_browser_spoof(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'history.sqlite3')
            result = {'suite':'claude_acceptance','run_id':'a'*32,'status':'completed',
                      'configuration':{'suite':'claude','model':'claude-fixture','base':'https://relay.test/v1'},
                      'verdict':{'status':'inconclusive'},'cases':[]}
            saved = store.save_acceptance(result)
            self.assertEqual(store.listing(kind='claude')['total'], 1)
            record=store.detail(saved['id'])
            self.assertEqual(record['kind'], 'claude')
            self.assertEqual(record['title'], 'Claude 上游验收')
            with self.assertRaises(ValueError):
                store.save({'kind':'claude','source':'basic','client_id':'spoof','result':{}})

    def test_connection_context_closes_connection(self):
        """The per-operation connection must not remain open after ``with``."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'history.sqlite3')
            with store.connection() as db:
                db.execute('SELECT 1')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')

    def test_password_hash_and_session_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth.json'
            db = Path(directory) / 'history.sqlite3'
            import json
            auth.write_text(json.dumps({'username': 'admin', 'password_hash': hash_password('correct horse')}))
            first = Store(db, auth)
            token, error = first.login('admin', 'correct horse', '127.0.0.1')
            self.assertIsNone(error)
            self.assertTrue(first.identity(token))
            second = Store(db, auth)
            self.assertEqual(second.identity(token), 'admin')
            self.assertFalse(verify_password('wrong', first.auth['password_hash']))

    def test_upsert_search_pagination_and_redacted_media(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'history.sqlite3')
            payload = {'client_id': 'client-1', 'kind': 'image', 'source': 'basic', 'title': 'Poster',
                       'model': 'image-model', 'base': 'https://relay.test/v1', 'prompt': 'hello',
                       'status': 'passed', 'result': {'authorization': 'secret', 'usage': {'max_tokens': 5}},
                       'media': [{'type': 'image', 'mime': 'image/png',
                                  'b64': base64.b64encode(b'png-bytes').decode()}]}
            first = store.save(payload)
            second = store.save({**payload, 'status': 'failed', 'title': 'Poster v2'})
            self.assertEqual(first['id'], second['id'])
            listing = store.listing(q='v2', limit=1)
            self.assertEqual(listing['total'], 1)
            record = store.detail(first['id'])
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['media'][0]['bytes'], 9)
            self.assertEqual(record['result']['authorization'], '[已隐藏]')
            self.assertEqual(record['result']['usage']['max_tokens'], 5)


if __name__ == '__main__':
    unittest.main()
