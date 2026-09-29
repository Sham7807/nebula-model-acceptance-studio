"""Offline recorder checks. Only an ephemeral loopback HTTP server is used."""
import gzip
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx
import kvv_transport as transport


def frame(data):
    return ('data: ' + (data if isinstance(data, str) else json.dumps(data)) + '\n\n').encode()

GOOD = frame({'model': 'fixture-k3', 'choices': [{'delta': {'content': 'OK'}, 'finish_reason': None}]}) + frame({'model': 'fixture-k3', 'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + frame('[DONE]')
JSON = json.dumps({'model': 'fixture-k3', 'choices': [{'message': {'content': 'OK'}}]}).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    ports = []
    def log_message(self, *args): pass
    def handle(self):
        try: super().handle()
        except (ConnectionResetError, BrokenPipeError): pass
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        mode = data.get('mode', 'json')
        self.ports.append(self.client_address[1])
        status, content_type, body = 200, 'application/json', JSON
        if mode in ('sse', 'gzip', 'missing_done', 'missing_finish', 'stream_error', 'malformed', 'stall', 'done_close', 'split', 'usage_early_stop'):
            content_type, body = 'text/event-stream', GOOD
        if mode == 'usage_early_stop': body = frame({'model': 'fixture-k3', 'choices': [], 'usage': {'prompt_tokens': 123, 'completion_tokens': 1}})
        elif mode == 'missing_done': body = GOOD.rsplit(frame('[DONE]'), 1)[0]
        elif mode == 'missing_finish': body = frame({'choices': [{'delta': {'content': 'OK'}, 'finish_reason': None}]}) + frame('[DONE]')
        elif mode == 'stream_error': body = frame({'error': {'type': 'upstream_error', 'message': 'fixture failure'}}) + frame('[DONE]')
        elif mode == 'malformed': body = GOOD.replace(b'"stop"', b'BROKEN')
        elif mode == 'secret': body = json.dumps({'model': 'fixture-k3', 'message': 'fixture-secret+/ used', 'api_key': 'another-sensitive-key'}).encode()
        elif mode == 'large': body = json.dumps({'model': 'fixture-k3', 'data': 'x' * 10000}).encode()
        elif mode == 'reject': status, body = int(data.get('status', 401)), b'{"error":{"message":"offline rejection"}}'
        elif mode == 'slow_json': time.sleep(.20)
        if mode == 'gzip': body = gzip.compress(body)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body) + (10000 if mode in ('stall', 'done_close') else 0)))
        self.send_header('x-request-id', 'upstream-fixture-id')
        self.send_header('set-cookie', 'private-cookie=value')
        if mode == 'gzip': self.send_header('Content-Encoding', 'gzip')
        if mode == 'slow_headers':
            self.wfile.write(b'HTTP/1.1 200 OK\r\n')
            self.wfile.flush()
            try:
                for i in range(12):
                    self.wfile.write(('X-Trickle-%s: yes\r\n' % i).encode());self.wfile.flush();time.sleep(.05)
            except (OSError, BrokenPipeError): return
        self.end_headers()
        try:
            if mode == 'stall':
                self.wfile.write(frame({'choices': [{'delta': {'content': 'partial'}, 'finish_reason': None}]})); self.wfile.flush(); time.sleep(2)
            elif mode == 'done_close':
                self.wfile.write(body); self.wfile.flush(); time.sleep(1)
            elif mode == 'split':
                for index in range(0, len(body), 7): self.wfile.write(body[index:index + 7]); self.wfile.flush()
            else: self.wfile.write(body); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError): pass


class RecorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%s/v1' % cls.server.server_port
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / 'evidence.jsonl'
        self.events = []; self.recorder = None; transport.set_case_id('test-fixture[nostream]')
    def tearDown(self):
        if self.recorder: self.recorder.uninstall()
        transport.set_case_id(''); self.temp.cleanup()
    def install(self, timeout=2, **limits):
        self.recorder = transport.install('fixture-secret+/', self.base, timeout, self.path, self.events.append, **limits)
        return self.recorder
    def entries(self): return [json.loads(line) for line in self.path.read_text().splitlines()]
    def last(self): return self.entries()[-1]

    def test_nonstream_semantics_redaction_ids_and_counts(self):
        rec = self.install()
        with httpx.Client(trust_env=False) as client:
            response = client.post(self.base + '/chat/completions', headers={'Authorization': 'Bearer fixture-secret+/'}, json={'model': 'fixture-alias', 'mode': 'secret'})
            self.assertIn('fixture-secret+/', response.text)
        text = self.path.read_text()
        for secret in ('fixture-secret+/', 'another-sensitive-key', 'private-cookie=value'): self.assertNotIn(secret, text)
        self.assertEqual(self.last()['status'], 'passed')
        self.assertEqual(self.last()['upstream_request_ids'][0]['value'], 'upstream-fixture-id')
        self.assertEqual(self.last()['response_models'], ['fixture-k3'])
        summary = rec.summary()
        self.assertEqual(summary['request_count'], 1); self.assertEqual(summary['completed_requests'], 1); self.assertEqual(summary['wire_requests'], 1)
        self.assertEqual(summary['requests'][0]['requested_model'], 'fixture-alias')
        self.assertEqual(summary['checks'][0]['case_id'], 'test-fixture[nostream]')
        self.assertEqual([e['type'] for e in self.events], ['request_start', 'request_finish'])
        self.assertNotIn('body', self.events[-1])

    def test_stream_not_preconsumed_and_done_close_is_complete(self):
        rec = self.install(timeout=.30); start = time.monotonic()
        with httpx.Client(trust_env=False) as client:
            with client.stream('POST', self.base + '/chat/completions', json={'model': 'fixture', 'mode': 'done_close', 'stream': True}) as response:
                self.assertFalse(response.is_stream_consumed); self.assertEqual(rec.summary()['completed_requests'], 0)
                content = b''
                for chunk in response.iter_bytes():
                    content += chunk
                    if b'[DONE]' in content: break
        self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(self.last()['termination'], 'done'); self.assertEqual(self.last()['status'], 'passed')
        time.sleep(.35); self.assertEqual(rec.summary()['completed_requests'], 1)

    def test_normal_split_and_compressed_sse_preserve_bytes(self):
        self.install()
        with httpx.Client(trust_env=False) as client:
            for mode in ('sse', 'split', 'gzip'):
                with client.stream('POST', self.base + '/chat/completions', json={'model': 'fixture', 'mode': mode, 'stream': True}) as response:
                    self.assertEqual(b''.join(response.iter_bytes()), GOOD)
                self.assertEqual(self.last()['status'], 'passed', mode); self.assertEqual(self.last()['sse']['done_count'], 1)
                self.assertEqual(self.last()['response_models'], ['fixture-k3'])
        self.assertEqual(self.recorder.summary()['request_count'], 3)

    def test_stream_failures_do_not_change_response(self):
        self.install()
        expected = {'json': 'sse_content_type', 'missing_done': 'sse_done', 'missing_finish': 'sse_finish_reason', 'stream_error': 'sse_error', 'malformed': 'sse_frames'}
        with httpx.Client(trust_env=False) as client:
            for mode, check_id in expected.items():
                response = client.post(self.base + '/chat/completions', json={'model': 'fixture', 'mode': mode, 'stream': True})
                self.assertTrue(response.content); last = self.last(); self.assertEqual(last['status'], 'failed', mode)
                self.assertEqual(next(c['status'] for c in last['checks'] if c['id'] == check_id), 'failed')

    def test_infrastructure_errors_are_not_negative_success(self):
        self.install()
        with httpx.Client(trust_env=False) as client:
            for status in (400, 401, 403, 429, 500):
                response = client.post(self.base + '/chat/completions', json={'mode': 'reject', 'status': status})
                self.assertEqual(response.status_code, status); self.assertEqual(self.last()['status'], 'inconclusive')
                self.assertEqual(self.last()['infrastructure_error'], status != 400)
        self.assertEqual(self.recorder.summary()['request_count'], 5)

    def test_total_deadline_interrupts_socket_retains_partial(self):
        rec = self.install(timeout=.20); start = time.monotonic()
        with httpx.Client(trust_env=False) as client:
            with self.assertRaises(httpx.ReadTimeout):
                with client.stream('POST', self.base + '/chat/completions', json={'mode': 'stall', 'stream': True}) as response: list(response.iter_bytes())
        self.assertLess(time.monotonic() - start, 1); self.assertEqual(self.last()['termination'], 'total_timeout')
        self.assertTrue(self.last()['infrastructure_error']); self.assertIn('partial', self.last()['body'])
        self.assertEqual(rec.summary()['completed_requests'], 1); self.assertEqual(rec.summary()['active_requests'], 0)

    def test_deadline_closes_response_on_reading_thread_without_context_manager(self):
        self.install(timeout=.20)
        with httpx.Client(trust_env=False) as client:
            response = client.send(client.build_request('POST', self.base + '/chat/completions', json={'mode': 'stall', 'stream': True}), stream=True)
            sock = response.extensions['network_stream'].get_extra_info('socket')
            start = time.monotonic()
            with self.assertRaises(httpx.ReadTimeout): list(response.iter_bytes())
            self.assertLess(time.monotonic() - start, 1)
            self.assertTrue(response.is_closed)
            self.assertEqual(sock.fileno(), -1)
        self.assertEqual(self.last()['termination'], 'total_timeout')

    def test_old_deadline_cannot_close_reused_connection(self):
        self.install(timeout=.30); Handler.ports.clear()
        with httpx.Client(trust_env=False) as client:
            client.post(self.base + '/chat/completions', json={'mode': 'json'}); time.sleep(.15)
            self.assertEqual(client.post(self.base + '/chat/completions', json={'mode': 'slow_json'}).status_code, 200)
        self.assertEqual(len(set(Handler.ports)), 1); self.assertEqual(self.recorder.summary()['passed'], 2)

    def test_reused_connection_header_trickle_obeys_total_deadline(self):
        self.install(timeout=.20)
        with httpx.Client(trust_env=False) as client:
            client.post(self.base + '/chat/completions', json={'mode': 'json'})
            started = time.monotonic()
            with self.assertRaises(httpx.ReadTimeout):
                client.post(self.base + '/chat/completions', json={'mode': 'slow_headers'})
            self.assertLess(time.monotonic() - started, .50)
        self.assertEqual(self.last()['termination'], 'total_timeout')

    def test_observed_stream_error_remains_failed_when_caller_closes_early(self):
        self.install()
        with httpx.Client(trust_env=False) as client:
            with client.stream('POST', self.base + '/chat/completions', json={'mode': 'stream_error', 'stream': True}) as response:
                for chunk in response.iter_bytes():
                    if b'error' in chunk: break
        self.assertEqual(self.last()['status'], 'failed')
        self.assertEqual(next(c['status'] for c in self.last()['checks'] if c['id'] == 'sse_error'), 'failed')

    def test_timeout_race_classification_and_preservation_of_short_idle_timeout(self):
        self.install(timeout=.20)
        for elapsed, expected in ((.25, 'total_timeout'), (.01, 'idle_timeout')):
            def fail(request, delay=elapsed):
                time.sleep(delay)
                raise httpx.ReadTimeout('fixture timeout', request=request)
            with httpx.Client(transport=httpx.MockTransport(fail)) as client:
                with self.assertRaises(httpx.ReadTimeout):
                    client.post(self.base + '/chat/completions', json={'model': 'fixture'})
            self.assertEqual(self.last()['termination'], expected)
            self.assertTrue(self.last()['infrastructure_error'])

    def test_reusing_request_restores_original_trace_and_timeouts(self):
        self.install(timeout=.20)
        calls = []
        trace = lambda name, info: calls.append(name)
        with httpx.Client(trust_env=False) as client:
            request = client.build_request('POST', self.base + '/chat/completions', json={'mode': 'json'}, extensions={'trace': trace})
            original_timeout = dict(request.extensions['timeout'])
            for i in range(2):
                self.assertEqual(client.send(request).status_code, 200)
                self.assertIs(request.extensions['trace'], trace)
                self.assertEqual(request.extensions['timeout'], original_timeout)
        self.assertEqual(self.recorder.summary()['passed'], 2)
        self.assertTrue(calls)

    def test_usage_early_stop_session_cleanup_does_not_hide_assertion_failure(self):
        rec = self.install()
        with httpx.Client(trust_env=False) as client:
            request = client.build_request('POST', self.base + '/chat/completions', json={'model': 'fixture', 'mode': 'usage_early_stop', 'stream': True})
            response = client.send(request, stream=True)
            chunks = response.iter_bytes()
            data = next(chunks)
            usage = json.loads(data.decode().removeprefix('data: ').strip())['usage']
            self.assertEqual(usage['prompt_tokens'], 123)
            self.assertNotEqual(usage['prompt_tokens'], 100, 'fixture has a concrete token-count mismatch')
            self.assertEqual(rec.summary()['completed_requests'], 0)
            rec.uninstall()
            self.assertTrue(response.is_closed, 'paused SDK iterators must still release their response during cleanup')
        row = rec.summary()['requests'][0]
        self.assertEqual(row['http_status'], 200)
        self.assertEqual(row['termination'], 'recorder_closed')
        self.assertFalse(row['infrastructure_error'], 'session cleanup must not convert a token assertion failure to inconclusive')
        self.assertEqual(row['status'], 'inconclusive', 'unconsumed SSE is not claimed complete')
        self.assertEqual(self.last()['sse']['done_count'], 0)
        self.assertEqual(next(c['status'] for c in self.last()['checks'] if c['id'] == 'sse_done'), 'inconclusive')

    def test_usage_early_stop_caller_close_is_not_infrastructure_failure(self):
        rec = self.install()
        with httpx.Client(trust_env=False) as client:
            with client.stream('POST', self.base + '/chat/completions', json={'mode': 'usage_early_stop', 'stream': True}) as response:
                chunks = response.iter_bytes()
                self.assertIn(b'prompt_tokens', next(chunks))
        row = rec.summary()['requests'][0]
        self.assertEqual(row['termination'], 'client_closed')
        self.assertFalse(row['infrastructure_error'])
        self.assertEqual(row['status'], 'inconclusive')

    def test_evidence_caps_preserve_response_and_restore_send(self):
        original = httpx.Client.send; rec = self.install(max_body_bytes=128, max_run_bytes=512)
        with httpx.Client(trust_env=False) as client:
            response = client.post(self.base + '/chat/completions', json={'mode': 'large'}); self.assertEqual(len(response.json()['data']), 10000)
        self.assertTrue(self.last()['body_truncated']); self.assertLess(len(self.path.read_bytes()), 5000)
        rec.uninstall(); self.assertIs(httpx.Client.send, original)

    def test_eager_mock_and_other_origin_bypass(self):
        rec = self.install()
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'model': 'mock', 'ok': True}))) as client:
            self.assertEqual(client.post(self.base + '/chat/completions', json={'stream': False}).json()['model'], 'mock')
            self.assertEqual(client.get('https://unrelated.test/').status_code, 200)
        self.assertEqual(rec.summary()['request_count'], 1); self.assertEqual(rec.summary()['completed_requests'], 1)
        self.assertEqual(self.last()['status'], 'passed'); self.assertEqual(self.last()['response_models'], ['mock'])

if __name__ == '__main__': unittest.main()
