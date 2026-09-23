"""Claude service orchestration checks; never contact a model provider."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import server
from auth_history import Store, hash_password


class ClaudeServiceTests(unittest.TestCase):
    def payload(self, **values):
        return {'suite': 'claude', 'base': 'https://fixture.invalid/relay/v2/messages',
                'key': 'fixture-private-key', 'model': 'claude-fixture', **values}

    def test_configuration_preserves_endpoint_and_enforces_bounds(self):
        config = server.validate(self.payload(provider='aws'))
        self.assertEqual(config['base'], self.payload()['base'])
        self.assertEqual(config['request_format'], 'anthropic')
        self.assertEqual(config['cache_tokens'], 12000)
        self.assertEqual(config['stress_requests'], 20)
        self.assertEqual(config['stress_concurrency'], 4)
        self.assertEqual(set(config['enabled_modules']), set(server.MODULES['claude']))
        config = server.validate(self.payload(request_format='openai', auth='anthropic',
                                             stress_concurrency=20, enabled_modules=['tools']))
        self.assertEqual(config['auth'], 'bearer')
        self.assertEqual(config['concurrency'], 20)
        self.assertEqual(config['enabled_modules'], ['tools'])
        for value in ({'provider': 'unverified-provider'}, {'auth': 'aws_sigv4'},
                      {'cache_tokens': 1023}, {'cache_tokens': 100001},
                      {'stress_requests': 201}, {'stress_requests': True},
                      {'stress_concurrency': 21}, {'stress_concurrency': 1.5},
                      {'signature_samples': 1.5}, {'enabled_modules': []},
                      {'enabled_modules': ['invented']}, {'base': 'https://fixture.invalid?key=x'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.validate(self.payload(**value))
        self.assertEqual(server.validate(self.payload(key=''), require_key=False)['key'], '')
        with self.assertRaises(ValueError): server.validate(self.payload(key=''))

    def test_secondary_dimensions_do_not_disable_selected_module(self):
        cases = [{'id': 'image', 'status': 'passed', 'module': 'tools', 'dimensions': ['multimodal']},
                 {'id': 'signature', 'status': 'failed', 'module': 'auth_signature', 'dimensions': ['signature']}]
        result = {'suite': 'claude_acceptance', 'cases': cases, 'checks': cases.copy()}
        server.apply_enabled_modules(result, {'suite': 'claude', 'enabled_modules': ['tools']})
        self.assertEqual(cases[0]['status'], 'passed')
        self.assertEqual(cases[1]['status'], 'not_covered')
        self.assertEqual(result['summary']['passed'], 1)
        self.assertEqual(result['summary']['failed'], 0)
        result['status']='completed'
        self.assertEqual(server.decorate(result)['verdict']['status'],'passed')
        self.assertEqual(result['verdict']['not_selected'],1)

    def test_preview_requires_session_and_csrf_but_never_requires_channel_key(self):
        import claude_acceptance
        with tempfile.TemporaryDirectory() as temporary:
            auth_file = Path(temporary) / 'auth.json'
            auth_file.write_text(json.dumps({'username': 'fixture', 'password_hash': hash_password('fixture-password')}))
            store = Store(Path(temporary) / 'history.sqlite3', auth_file)
            with patch.object(server, 'AUTH_STORE', store), patch.dict('os.environ', {'WORKBENCH_COOKIE_SECURE': '0'}):
                service = server.WorkbenchServer(('127.0.0.1', 0), server.Handler)
                threading.Thread(target=service.serve_forever, daemon=True).start()
                try:
                    url = f'http://127.0.0.1:{service.server_port}'
                    with httpx.Client(trust_env=False) as client:
                        body = self.payload(key='')
                        self.assertEqual(client.post(url + '/api/claude/plan', json=body).status_code, 401)
                        self.assertEqual(client.post(url + '/api/auth/login', json={'username': 'fixture', 'password': 'fixture-password'}).status_code, 200)
                        token = client.get(url + '/api/session').json()['token']
                        self.assertEqual(client.post(url + '/api/claude/plan', json=body).status_code, 403)
                        headers = {'X-Workbench-Token': token}
                        with patch.object(claude_acceptance, 'build_plan', create=True,
                                          return_value={'suite': 'claude', 'request_count': 1, 'requests': []}) as build:
                            response = client.post(url + '/api/claude/plan', headers=headers, json=body)
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(build.call_args.args[0]['key'], '')
                            response = client.post(url + '/api/claude/plan', headers=headers,
                                                   json=self.payload(key='fixture-private-key'))
                            self.assertEqual(response.status_code, 200)
                            self.assertNotIn('fixture-private-key', response.text)
                            self.assertEqual(build.call_args.args[0]['key'], '')
                        for bad in [[], self.payload(suite='ccmax'), self.payload(cache_tokens=1)]:
                            self.assertEqual(client.post(url + '/api/claude/plan', headers=headers, json=bad).status_code, 400)
                finally:
                    service.shutdown(); service.server_close()

    def test_claude_dispatch_persists_redacted_result_and_restores_distinct_suite(self):
        import claude_acceptance
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = Store(directory / 'history.sqlite3')
            config = server.validate(self.payload(enabled_modules=['protocol']))
            job = {'id': 'd' * 32, 'suite': 'claude', 'model': config['model'], 'base': config['base'],
                   'status': 'running', 'started_at': time.time(), 'completed': 0, 'total': 0, 'events': [], 'cancel': threading.Event()}
            cases = [{'id': 'fixture', 'title': 'Synthetic protocol', 'module': 'protocol', 'dimensions': ['protocol'], 'status': 'passed'}]
            result = {'suite': 'claude_acceptance', 'status': 'completed', 'cases': cases, 'checks': cases,
                      'summary': {'total': 1, 'completed': 1, 'passed': 1}, 'log': 'fixture-private-key'}
            with patch.object(server, 'REPORTS', directory / 'reports'), patch.object(server, 'QUEUE_META', directory / '.jobs.json'), \
                 patch.object(server, 'AUTH_STORE', store), patch.object(server, 'JOBS', {job['id']: job}), \
                 patch.object(server, 'report_html', return_value=b'<html>offline report</html>'), \
                 patch.object(claude_acceptance, 'run', return_value=result, create=True) as runner, \
                 patch.object(server.kvv_runner, 'run') as kvv:
                server.run_job(job, config)
                runner.assert_called_once(); kvv.assert_not_called()
                self.assertEqual(config['key'], '')
                self.assertEqual(job['status'], 'completed')
                self.assertTrue(job['history_saved'])
                stored = (server.REPORTS / job['id'] / 'report.json').read_text()
                self.assertNotIn('fixture-private-key', stored)
                server.JOBS.clear(); server.restore_reports()
                self.assertEqual(server.JOBS[job['id']]['suite'], 'claude')


if __name__ == '__main__': unittest.main()
