"""Offline discovery tests: provider compatibility, recovery, and diagnostics."""
import os
import unittest
from unittest.mock import patch
import httpx
from channel_discovery import DiscoveryError, fetch_models, model_endpoint, model_endpoints

KEY = 'fixture-discovery-secret'
BASE = 'https://offline-relay.test/prefix/v1/'


class DiscoveryTests(unittest.TestCase):
    def fetch(self, payload=None, *, status=200, raw=None, auth='bearer', base=BASE, handler=None):
        calls = []
        def handle(request):
            calls.append(request)
            if handler:
                return handler(request)
            return httpx.Response(status, content=raw) if raw is not None else httpx.Response(status, json=payload)
        with patch.dict(os.environ, {'WORKBENCH_OUTBOUND_PROXY': ''}):
            value = fetch_models(base, KEY if auth != 'none' else '', auth, transport=httpx.MockTransport(handle))
        return value, calls

    def test_bearer_auth_and_get_path(self):
        value, calls = self.fetch({'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'b'}]})
        self.assertEqual(value['models'], ['a', 'b'])
        self.assertEqual(value['total'], 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(calls[0].url), 'https://offline-relay.test/prefix/v1/models')
        self.assertEqual(calls[0].method, 'GET')
        self.assertEqual(calls[0].headers['authorization'], 'Bearer ' + KEY)
        self.assertNotIn('x-api-key', calls[0].headers)
        self.assertEqual(calls[0].content, b'')
        self.assertLessEqual(calls[0].extensions['timeout']['read'], 8)
        self.assertEqual(value['diagnostics']['code'], 'ok')
        self.assertEqual(value['diagnostics']['auth'], 'bearer')

    def test_anthropic_version_and_auth(self):
        value, calls = self.fetch({'data': [{'id': 'claude-fixture'}]}, auth='anthropic')
        self.assertEqual(value['models'], ['claude-fixture'])
        self.assertEqual(calls[0].headers['x-api-key'], KEY)
        self.assertEqual(calls[0].headers['anthropic-version'], '2023-06-01')
        self.assertNotIn('authorization', calls[0].headers)

    def test_gemini_root_and_names(self):
        value, calls = self.fetch({'models': [{'name': 'models/gemini-fixture'}]}, auth='gemini', base='https://offline-relay.test/prefix')
        self.assertEqual(value['models'], ['gemini-fixture'])
        self.assertEqual(str(calls[0].url), 'https://offline-relay.test/prefix/v1beta/models')
        self.assertEqual(calls[0].headers['x-goog-api-key'], KEY)
        self.assertNotIn('authorization', calls[0].headers)

    def test_native_official_hosts_use_native_list_auth(self):
        for base, auth, header, path in [
            ('https://api.anthropic.com', 'anthropic', 'x-api-key', '/v1/models'),
            ('https://generativelanguage.googleapis.com', 'gemini', 'x-goog-api-key', '/v1beta/models'),
            ('https://generativelanguage.googleapis.com/v1beta/openai', 'bearer', 'authorization', '/v1beta/openai/models'),
        ]:
            with self.subTest(base=base):
                result, calls = self.fetch({'data': ['fixture']}, base=base)
                self.assertEqual(result['diagnostics']['auth'], auth)
                self.assertIn(header, calls[0].headers)
                self.assertEqual(calls[0].url.path, path)

    def test_no_auth_never_sends_key_or_auth_fallback(self):
        result, calls = self.fetch({'data': ['local-model']}, auth='none')
        self.assertEqual(result['models'], ['local-model'])
        for header in ('authorization', 'x-api-key', 'x-goog-api-key'):
            self.assertNotIn(header, calls[0].headers)
        self.assertNotIn(KEY, str(result))

    def test_url_normalization_preserves_prefixes_and_explicit_versions(self):
        for base, auth, expected in [
            ('https://relay.test', 'bearer', '/v1/models'),
            ('https://relay.test/v1/', 'bearer', '/v1/models'),
            ('https://relay.test/prefix/', 'bearer', '/prefix/v1/models'),
            ('https://relay.test/api/v1', 'bearer', '/api/v1/models'),
            ('https://relay.test/prefix/api/v1/', 'bearer', '/prefix/api/v1/models'),
            ('https://relay.test/v2', 'bearer', '/v2/models'),
            ('https://relay.test/v4', 'bearer', '/v4/models'),
            ('https://relay.test/prefix/v1beta', 'bearer', '/prefix/v1beta/models'),
            ('https://relay.test/compatible-mode/v1', 'bearer', '/compatible-mode/v1/models'),
            ('https://relay.test/v1/chat/completions', 'bearer', '/v1/models'),
            ('https://relay.test/v1/responses/', 'bearer', '/v1/models'),
            ('https://relay.test/prefix/v1/messages', 'anthropic', '/prefix/v1/models'),
            ('https://relay.test/v1/models', 'bearer', '/v1/models'),
            ('https://relay.test', 'gemini', '/v1beta/models'),
            ('https://relay.test/v1', 'gemini', '/v1/models'),
            ('https://relay.test/v1beta/openai', 'bearer', '/v1beta/openai/models'),
        ]:
            with self.subTest(base=base, auth=auth):
                result, calls = self.fetch({'data': []}, base=base, auth=auth)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].url.path, expected)

    def test_unversioned_root_falls_back_only_after_path_errors(self):
        def handle(request):
            return httpx.Response(200, json={'data': ['found']}) if request.url.path == '/api/v1/models' else httpx.Response(404, json={'error': 'not found'})
        result, calls = self.fetch(base='https://relay.test', handler=handle)
        self.assertEqual(result['models'], ['found'])
        self.assertEqual([r.url.path for r in calls], ['/v1/models', '/models', '/api/v1/models'])
        self.assertEqual([x['status'] for x in result['diagnostics']['attempts']], [404, 404, 200])

    def test_same_endpoint_auth_fallback_is_reported(self):
        def handle(request):
            if 'authorization' in request.headers:
                return httpx.Response(401, json={'error': 'x-api-key required'})
            return httpx.Response(200, json={'data': ['claude-fixture']})
        result, calls = self.fetch(handler=handle)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].url, calls[1].url)
        self.assertEqual(result['diagnostics']['auth'], 'anthropic')
        self.assertIn('测试请求仍保留', result['diagnostics']['suggestion'])

    def test_validation_happens_before_network(self):
        for base in ('file:///tmp/models', 'ftp://relay.test', 'https://user:password@relay.test', 'https://relay.test?key=secret', 'https://relay.test#fragment', 'https://relay.test:bad', 'not-a-url'):
            with self.subTest(base=base):
                with self.assertRaisesRegex(DiscoveryError, '地址') as ctx:
                    self.fetch(base=base)
                self.assertEqual(ctx.exception.code, 'invalid_config')
                self.assertEqual(ctx.exception.diagnostics['attempts'], [])
        with self.assertRaises(DiscoveryError):
            self.fetch(auth='wrong')
        with self.assertRaises(DiscoveryError):
            fetch_models(BASE, 'bad\nkey', transport=httpx.MockTransport(lambda r: self.fail('must not call provider')))

    def test_envelopes_aliases_and_no_fake_ids(self):
        for payload in ([{'id': 'a'}, {'name': 'b'}], {'models': [' b ', 'a']}, {'data': {'models': [{'model_id': 'a'}, {'model': 'b'}]}}, {'result': {'items': [{'modelId': 'a'}, {'slug': 'b'}]}}):
            with self.subTest(payload=payload):
                result, _ = self.fetch(payload)
                self.assertEqual(result['models'], ['a', 'b'])
        for payload in ({}, {'data': {}}, [None, 1, {}, {'object': 'model'}], {'error': {'message': KEY}}, {'models': [{'id': 123}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(DiscoveryError) as ctx:
                    self.fetch(payload)
                self.assertNotIn(KEY, str(ctx.exception))
                self.assertEqual(ctx.exception.code, 'invalid_catalog')
        result, _ = self.fetch({'data': []})
        self.assertEqual(result['models'], [])
        self.assertEqual(result['diagnostics']['code'], 'empty_catalog')

    def test_invalid_json_is_explicit(self):
        for raw in (b'not json', b'<html>Proxy login</html>', b'{"data":'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(DiscoveryError, 'JSON') as ctx:
                    self.fetch(raw=raw)
                self.assertEqual(ctx.exception.code, 'invalid_json')

    def test_status_diagnostics_are_actionable_and_redacted(self):
        for status, code, limit in [(400, 'upstream_error', 1), (401, 'auth_failed', 2), (403, 'auth_failed', 2), (404, 'models_not_supported', 2), (429, 'rate_limited', 2), (500, 'upstream_error', 2), (503, 'upstream_error', 2)]:
            with self.subTest(status=status):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(status, json={'error': {'message': 'upstream ' + KEY}})
                with self.assertRaisesRegex(DiscoveryError, 'HTTP ' + str(status)) as ctx:
                    self.fetch(handler=handle)
                error = ctx.exception
                self.assertEqual(error.code, code)
                self.assertEqual(error.status, status)
                self.assertEqual(len(calls), limit)
                self.assertNotIn(KEY, str(error))
                self.assertNotIn(KEY, str(error.diagnostics))
                self.assertTrue(error.advice)

    def test_region_error_is_not_misreported_as_key_or_silently_retried(self):
        with self.assertRaises(DiscoveryError) as ctx:
            self.fetch({'error': {'message': 'unsupported_country_region_territory'}}, status=403)
        self.assertEqual(ctx.exception.code, 'region_restricted')
        self.assertEqual(len(ctx.exception.diagnostics['attempts']), 1)

    def test_same_origin_redirect_allowed_but_cross_origin_and_downgrade_blocked(self):
        def handle(request):
            if request.url.path.endswith('/models'):
                return httpx.Response(307, headers={'Location': '/catalog/models/'})
            return httpx.Response(200, json={'data': ['redirect-model']})
        result, calls = self.fetch(handler=handle)
        self.assertEqual(result['models'], ['redirect-model'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(r.headers['authorization'] == 'Bearer ' + KEY for r in calls))
        for target in ('https://other.test/models', 'http://offline-relay.test/models', 'https://user:pass@offline-relay.test/models'):
            with self.subTest(target=target):
                with self.assertRaises(DiscoveryError) as ctx:
                    self.fetch(handler=lambda r: httpx.Response(302, headers={'Location': target}))
                self.assertEqual(ctx.exception.code, 'redirect_blocked')
                self.assertEqual(len(ctx.exception.diagnostics['attempts']), 1)

    def test_transient_network_failure_retries_once_without_leaking_detail(self):
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError('network ' + KEY, request=request)
            return httpx.Response(200, json={'data': ['recovered']})
        result, _ = self.fetch(handler=handle)
        self.assertEqual(result['models'], ['recovered'])
        self.assertEqual(len(calls), 2)
        self.assertNotIn(KEY, str(result))
        for exc, code in [(httpx.ReadTimeout, 'timeout'), (httpx.ConnectError, 'network_error')]:
            def failing(request):
                raise exc(KEY, request=request)
            with self.assertRaises(DiscoveryError) as ctx:
                self.fetch(handler=failing)
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(len(ctx.exception.diagnostics['attempts']), 2)
            self.assertNotIn(KEY, str(ctx.exception))

    def test_tls_failure_does_not_disable_verification_or_retry(self):
        def failing(request):
            raise httpx.ConnectError('certificate verify failed', request=request)
        with self.assertRaises(DiscoveryError) as ctx:
            self.fetch(handler=failing)
        self.assertEqual(ctx.exception.code, 'tls_error')
        self.assertEqual(len(ctx.exception.diagnostics['attempts']), 1)

    def test_gemini_pagination_and_anthropic_cursor_pagination(self):
        for auth, base in [('gemini', 'https://relay.test/v1beta'), ('anthropic', BASE)]:
            def handle(request):
                if request.url.query:
                    return httpx.Response(200, json={'models': [{'name': 'models/b'}]} if auth == 'gemini' else {'data': [{'id': 'b'}], 'has_more': False})
                return httpx.Response(200, json={'models': [{'name': 'models/a'}], 'nextPageToken': 'next'} if auth == 'gemini' else {'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'})
            result, calls = self.fetch(auth=auth, base=base, handler=handle)
            self.assertEqual(result['models'], ['a', 'b'])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result['diagnostics']['pages'], 2)
            self.assertIn('pageToken' if auth == 'gemini' else 'after_id', str(calls[1].url))

    def test_html_catchall_does_not_hide_alternative_catalog_path(self):
        def handle(request):
            if request.url.path == '/api/v1/models':
                return httpx.Response(200, json={'data': ['available']})
            return httpx.Response(200, text='<html>Gateway homepage</html>')
        result, calls = self.fetch(base='https://relay.test', handler=handle)
        self.assertEqual(result['models'], ['available'])
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['diagnostics']['attempts'][0]['code'], 'invalid_json')

    def test_known_groq_prefix_and_long_rate_limit(self):
        result, calls = self.fetch({'data': ['fixture']}, base='https://api.groq.com')
        self.assertEqual(calls[0].url.path, '/openai/v1/models')
        with self.assertRaises(DiscoveryError) as ctx:
            self.fetch(handler=lambda r: httpx.Response(429, headers={'Retry-After': '60'}, json={'error': 'busy'}))
        self.assertEqual(len(ctx.exception.diagnostics['attempts']), 1)
        self.assertEqual(ctx.exception.code, 'rate_limited')

    def test_dns_and_unreachable_routes_have_distinct_diagnostics(self):
        for message, code, attempts in [('Name or service not known', 'dns_error', 2), ('No route to host', 'network_unreachable', 1)]:
            def handle(request):
                raise httpx.ConnectError(message, request=request)
            with self.assertRaises(DiscoveryError) as ctx:
                self.fetch(handler=handle)
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(len(ctx.exception.diagnostics['attempts']), attempts)

    def test_compressed_catalog_is_decoded_exactly_once(self):
        import gzip
        result, calls = self.fetch(handler=lambda r: httpx.Response(200, headers={'content-encoding': 'gzip', 'content-type': 'application/json'}, content=gzip.compress(b'{"data": ["compressed-model"]}')))
        self.assertEqual(result['models'], ['compressed-model'])
        self.assertEqual(len(calls), 1)

    def test_catalog_size_is_bounded(self):
        with self.assertRaises(DiscoveryError) as ctx:
            self.fetch(raw=b'[' + b' ' * (4 * 1024 * 1024) + b']')
        self.assertEqual(ctx.exception.code, 'response_too_large')

    def test_pagination_loop_reports_partial_result(self):
        result, calls = self.fetch({'models': ['a'], 'nextPageToken': 'same'}, auth='gemini')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['models'], ['a'])
        self.assertEqual(result['diagnostics']['code'], 'partial')

    def test_deadline_is_enforced_during_response_read(self):
        clock = [0.0]
        class SlowBody(httpx.SyncByteStream):
            def __iter__(self):
                clock[0] = 30.0
                yield b'{"data": []}'
        with patch('channel_discovery.time.monotonic', lambda: clock[0]):
            with self.assertRaises(DiscoveryError) as ctx:
                self.fetch(handler=lambda r: httpx.Response(200, stream=SlowBody()))
        self.assertEqual(ctx.exception.code, 'timeout')
        self.assertEqual(len(ctx.exception.diagnostics['attempts']), 1)

    def test_explicit_proxy_is_not_loaded_from_ambient_http_proxy(self):
        class Client:
            def __init__(self, **kw): self.kw = kw; captured.append(kw)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def stream(self, method, url, **kwargs):
                from contextlib import contextmanager
                @contextmanager
                def response():
                    yield httpx.Response(200, json={'data': []})
                return response()
        captured = []
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://ambient.test:8888', 'WORKBENCH_OUTBOUND_PROXY': 'http://configured.test:3128'}), patch('channel_discovery.httpx.Client', Client):
            result = fetch_models(BASE, KEY)
        self.assertFalse(captured[0]['trust_env'])
        self.assertEqual(captured[0]['proxy'], 'http://configured.test:3128')
        self.assertTrue(result['diagnostics']['proxy'])
        self.assertNotIn('configured.test', str(result))


if __name__ == '__main__':
    unittest.main()
