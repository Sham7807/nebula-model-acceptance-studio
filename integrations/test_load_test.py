"""Offline checks for the bounded API load-test engine."""
import threading
import json
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

import httpx
import load_test
import server


class LoadTestEngineTests(unittest.TestCase):
    def test_validate_requires_safe_target_and_caps(self):
        with self.assertRaises(ValueError):
            load_test.validate_config({'url': 'file:///etc/hosts'})
        with self.assertRaises(ValueError):
            load_test.validate_config({'url': 'https://relay.test', 'concurrency': 101})
        with self.assertRaises(ValueError):
            load_test.validate_config({'url': 'https://relay.test', 'method': 'GET', 'body': 'x'})
        config = load_test.validate_config({'url': 'https://relay.test/path?token=secret', 'method': 'POST',
                                            'body': {'hello': 'world'}, 'total_requests': 3,
                                            'concurrency': 2})
        self.assertEqual(config['headers']['Content-Type'], 'application/json')
        self.assertEqual(config['total_requests'], 3)

    def test_percentiles_and_success_metrics_are_deterministic(self):
        calls = {'count': 0}
        def fixture(config):
            calls['count'] += 1
            return {'ok': calls['count'] != 2, 'status': 200 if calls['count'] != 2 else 503,
                    'latency_ms': float(calls['count'] * 10), 'bytes': 3,
                    'error_type': None if calls['count'] != 2 else 'http_status'}
        events = []
        with patch.object(load_test, '_request_once', side_effect=fixture):
            result = load_test.run({'url': 'https://relay.test', 'method': 'POST', 'body': '{}',
                                    'total_requests': 5, 'concurrency': 2}, events.append)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['summary']['completed'], 5)
        self.assertEqual(result['summary']['successful'], 4)
        self.assertEqual(result['summary']['failed'], 1)
        self.assertEqual(result['summary']['status_codes']['200'], 4)
        self.assertEqual(result['latency_ms']['p50'], 30.0)
        self.assertTrue(any(event['type'] == 'complete' for event in events))

    def test_cancellation_returns_partial_evidence_without_retry(self):
        cancel = threading.Event()
        calls = {'count': 0}
        def fixture(config):
            calls['count'] += 1
            if calls['count'] == 2:
                cancel.set()
            return {'ok': True, 'status': 200, 'latency_ms': 1, 'bytes': 1, 'error_type': None}
        with patch.object(load_test, '_request_once', side_effect=fixture):
            result = load_test.run({'url': 'https://relay.test', 'total_requests': 100, 'concurrency': 2}, cancelled=cancel.is_set)
        self.assertEqual(result['status'], 'cancelled')
        self.assertLess(result['summary']['completed'], 100)
        self.assertGreater(result['summary']['completed'], 0)

    def test_loopback_api_run_resolve_and_cancel(self):
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                time.sleep(0.15)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')
            def log_message(self, *_):
                pass
        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        service = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        with tempfile.TemporaryDirectory() as folder:
            old_reports = server.REPORTS
            server.REPORTS = __import__('pathlib').Path(folder)
            threading.Thread(target=provider.serve_forever, daemon=True).start()
            threading.Thread(target=service.serve_forever, daemon=True).start()
            try:
                base = f'http://127.0.0.1:{service.server_port}'
                target = f'http://127.0.0.1:{provider.server_port}/chat?token=secret'
                with httpx.Client(trust_env=False, timeout=5) as client:
                    token = client.get(base + '/api/session').json()['token']
                    headers = {'X-Workbench-Token': token}
                    resolved = client.post(base + '/api/load-tests/resolve', headers=headers, json={'url': target})
                    self.assertEqual(resolved.status_code, 200)
                    self.assertIn('127.0.0.1', resolved.json()['addresses'])
                    denied = client.post(base + '/api/load-tests', headers=headers, json={'url': target})
                    self.assertEqual(denied.status_code, 400)
                    started = client.post(base + '/api/load-tests', headers=headers, json={
                        'url': target, 'method': 'POST', 'body': '{}', 'total_requests': 2,
                        'concurrency': 1, 'acknowledge_risk': True})
                    self.assertEqual(started.status_code, 202, started.text)
                    identity = started.json()['id']
                    running_snapshot = client.get(base + '/api/load-tests/' + identity, headers=headers).json()
                    self.assertNotIn('secret', json.dumps(running_snapshot, ensure_ascii=False))
                    for _ in range(30):
                        snapshot = client.get(base + '/api/load-tests/' + identity, headers=headers).json()
                        if snapshot.get('status') != 'running':
                            break
                        time.sleep(0.03)
                    self.assertEqual(snapshot['status'], 'completed')
                    self.assertEqual(snapshot['result']['summary']['completed'], 2)
                    self.assertTrue((server.REPORTS / identity / 'report.json').is_file())
                    downloaded = client.get(base + '/api/load-tests/' + identity + '/report.json', headers=headers)
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.json()['run_id'], identity)
                    second = client.post(base + '/api/load-tests', headers=headers, json={
                        'url': target, 'method': 'POST', 'body': '{}', 'total_requests': 100,
                        'concurrency': 1, 'acknowledge_risk': True})
                    identity2 = second.json()['id']
                    self.assertEqual(client.post(base + '/api/load-tests/' + identity2 + '/cancel', headers=headers).status_code, 200)
            finally:
                server.REPORTS = old_reports
                provider.shutdown(); provider.server_close(); service.shutdown(); service.server_close()


if __name__ == '__main__':
    unittest.main()
