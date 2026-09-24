"""Parallel desktop jobs via real HTTP; runners are local, controlled fixtures."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import server
from auth_history import Store


class ParallelRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.store = Store(folder / 'history.sqlite3')
        self.releases = {name: threading.Event() for name in ('one', 'two', 'three', 'four', 'five')}
        self.calls = []
        self.patches = [patch.object(server, 'AUTH_STORE', self.store),
                        patch.object(server, 'JOBS', {}),
                        patch.object(server, 'REPORTS', folder / 'reports'),
                        patch.object(server, 'QUEUE_META', folder / 'reports/.jobs.json'),
                        patch.object(server, 'MAX_CONCURRENT_RUNS', 4),
                        patch.object(server.kvv_runner, 'run', side_effect=self.runner),
                        patch.object(server, 'report_html', return_value=b'<html>local fixture</html>')]
        for p in self.patches: p.start()
        self.srv = server.WorkbenchServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.client = httpx.Client(base_url=f'http://127.0.0.1:{self.srv.server_port}', trust_env=False, timeout=5)
        self.client.headers['X-Workbench-Token'] = self.client.get('/api/session').json()['token']

    def tearDown(self):
        for release in self.releases.values(): release.set()
        deadline = time.monotonic() + 5
        while any(j['status'] == 'running' or not j.get('history_saved') for j in server.JOBS.values() if not j.get('batch_child')) and time.monotonic() < deadline:
            time.sleep(.02)
        self.client.close(); self.srv.shutdown(); self.srv.server_close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def runner(self, config, emit, cancelled, directory):
        model = config['model']
        self.calls.append((model, config['base'], config['key'], str(directory)))
        emit({'total': 2, 'completed': 1})
        deadline = time.monotonic() + 10
        while not self.releases[model].wait(.02) and not cancelled() and time.monotonic() < deadline: pass
        return {'suite': 'kvv11', 'status': 'cancelled' if cancelled() else 'completed',
                'summary': {'total': 2, 'completed': 1, 'passed': 1},
                'cases': [{'id': 'fixture', 'status': 'passed', 'detail': model}]}

    def post(self, model, **extra):
        return self.client.post('/api/runs', json={'suite': 'kvv11', 'base': f'https://{model}.fixture.test',
            'key': 'fixture-key-' + model, 'model': model, 'timeout': 5, 'matrix_profile': 'off',
            'client_request_id': 'parallel-' + model, **extra})

    def wait_saved(self, identity):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = self.client.get('/api/runs/' + identity).json()
            if data.get('history_saved'): return data
            time.sleep(.025)
        self.fail('Result was not saved')

    def test_parallel_isolation_limit_dedup_individual_cancel_and_reports(self):
        ids = []
        for name in ('one', 'two', 'three', 'four'):
            response = self.post(name)
            self.assertEqual(response.status_code, 202, response.text)
            ids.append(response.json()['id'])
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(self.post('five').status_code, 409)
        repeat = self.post('one')
        self.assertEqual(repeat.status_code, 202)
        self.assertEqual(repeat.json(), {'id': ids[0], 'duplicate': True})
        snapshots = [self.client.get('/api/runs/' + i).json() for i in ids]
        self.assertTrue(all(job['status'] == 'running' for job in snapshots))
        self.assertEqual([j['model'] for j in snapshots], ['one', 'two', 'three', 'four'])
        self.assertNotIn('fixture-key-', json.dumps(snapshots))
        self.assertEqual(self.client.post('/api/runs/' + ids[0] + '/cancel', json={}).status_code, 200)
        stopped = self.wait_saved(ids[0]); self.assertEqual(stopped['status'], 'cancelled')
        self.assertTrue(all(self.client.get('/api/runs/' + i).json()['status'] == 'running' for i in ids[1:]))
        for name in ('two', 'three', 'four'): self.releases[name].set()
        for identity in ids[1:]: self.assertEqual(self.wait_saved(identity)['status'], 'completed')
        response = self.post('five'); self.assertEqual(response.status_code, 202)
        self.releases['five'].set(); self.wait_saved(response.json()['id'])
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(len(set(row[3] for row in self.calls)), 5)
        for model, base, key, directory in self.calls:
            self.assertEqual(base, f'https://{model}.fixture.test/v1')
            self.assertEqual(key, 'fixture-key-' + model)
            self.assertNotIn(key, (Path(directory) / 'report.json').read_text())
        page = self.client.get('/api/history').json()
        self.assertEqual(page['stats']['total'], 5)

    def test_batch_child_uses_parent_slot_and_serial_default_is_retained(self):
        first = self.post('one', models=['one', 'two']).json()['id']
        deadline = time.monotonic() + 2
        while len(server.JOBS) < 2 and time.monotonic() < deadline: time.sleep(.02)
        self.assertEqual(len(server.JOBS), 2)
        for name in ('three', 'four', 'five'): self.assertEqual(self.post(name).status_code, 202)
        self.assertEqual(self.post('two').status_code, 409)
        self.client.post('/api/runs/' + first + '/cancel', json={})
        result = self.wait_saved(first)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual([row['model'] for row in result['result']['results']], ['one'])
        with patch.object(server, 'MAX_CONCURRENT_RUNS', 1): self.assertEqual(self.post('two').status_code, 409)


if __name__ == '__main__': unittest.main()
