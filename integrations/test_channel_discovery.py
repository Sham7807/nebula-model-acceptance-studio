"""Offline model discovery tests using httpx.MockTransport only."""
import unittest
import httpx
from channel_discovery import fetch_models

KEY = 'fixture-discovery-secret'
BASE = 'https://offline-relay.test/prefix/v1/'


class DiscoveryTests(unittest.TestCase):
    def fetch(self, payload=None, *, status=200, raw=None, auth='bearer', response_headers=None):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(status, headers=response_headers, content=raw) if raw is not None else httpx.Response(status, headers=response_headers, json=payload)
        value = fetch_models(BASE, KEY, auth, transport=httpx.MockTransport(handle))
        return value, calls

    def test_bearer_auth_and_get_path_are_correct(self):
        value, calls = self.fetch({'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'b'}]})
        self.assertEqual(value, {'models': ['a', 'b'], 'total': 2})
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(str(request.url), 'https://offline-relay.test/prefix/v1/models')
        self.assertEqual(request.headers['authorization'], 'Bearer ' + KEY)
        self.assertNotIn('x-api-key', request.headers)
        self.assertEqual(request.content, b'')
        self.assertEqual(request.extensions['timeout']['read'], 25)

    def test_anthropic_auth_uses_its_version_header(self):
        value, calls = self.fetch({'data': [{'id': 'claude-fixture'}]}, auth='anthropic')
        self.assertEqual(value['models'], ['claude-fixture'])
        self.assertEqual(calls[0].headers['x-api-key'], KEY)
        self.assertEqual(calls[0].headers['anthropic-version'], '2023-06-01')
        self.assertNotIn('authorization', calls[0].headers)

    def test_gemini_catalog_uses_native_path_header_and_model_names(self):
        value, calls = self.fetch({'models': [{'name': 'models/gemini-fixture'}]}, auth='gemini')
        self.assertEqual(value['models'], ['gemini-fixture'])
        self.assertEqual(str(calls[0].url), 'https://offline-relay.test/prefix/v1beta/models')
        self.assertEqual(calls[0].headers['x-goog-api-key'], KEY)
        self.assertNotIn('authorization', calls[0].headers)
        self.assertNotIn('x-api-key', calls[0].headers)

    def test_unauthenticated_catalog_does_not_forward_a_key(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={'data': [{'id': 'local-model'}]})
        value = fetch_models('https://offline-relay.test', '', 'none', transport=httpx.MockTransport(handle))
        self.assertEqual(value['models'], ['local-model'])
        self.assertEqual(str(calls[0].url), 'https://offline-relay.test/v1/models')
        for header in ('authorization', 'x-api-key', 'x-goog-api-key'):
            self.assertNotIn(header, calls[0].headers)

    def test_root_prefix_and_protocol_version_are_normalized_once(self):
        cases = [
            ('https://offline-relay.test', 'bearer', '/v1/models'),
            ('https://offline-relay.test/v1/', 'bearer', '/v1/models'),
            ('https://offline-relay.test/prefix/', 'bearer', '/prefix/v1/models'),
            ('https://offline-relay.test/prefix/v1', 'bearer', '/prefix/v1/models'),
            ('https://offline-relay.test/prefix/v1beta', 'bearer', '/prefix/v1/models'),
            ('https://offline-relay.test', 'gemini', '/v1beta/models'),
            ('https://offline-relay.test/prefix/v1/', 'gemini', '/prefix/v1beta/models'),
            ('https://offline-relay.test/prefix/v1beta/', 'gemini', '/prefix/v1beta/models'),
        ]
        for base, auth, expected in cases:
            with self.subTest(base=base, auth=auth):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(200, json={'data': []})
                fetch_models(base, KEY, auth, transport=httpx.MockTransport(handle))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].url.path, expected)

    def test_invalid_addresses_fail_before_the_network(self):
        cases = ('file:///tmp/models', 'ftp://offline-relay.test',
                 'https://user:password@offline-relay.test',
                 'https://offline-relay.test?key=secret',
                 'https://offline-relay.test#fragment', 'not-a-url')
        for base in cases:
            with self.subTest(base=base):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(200, json={'data': []})
                with self.assertRaises(ValueError):
                    fetch_models(base, KEY, transport=httpx.MockTransport(handle))
                self.assertEqual(calls, [])

    def test_supported_envelopes_and_names_preserve_model_ids(self):
        for payload in ([{'id': 'a'}, {'name': 'b'}], {'models': [{'name': 'a'}, {'id': 'b'}]}):
            with self.subTest(payload=payload):
                value, _ = self.fetch(payload)
                self.assertEqual(value, {'models': ['a', 'b'], 'total': 2})

    def test_string_rows_and_mixed_valid_entries_are_supported(self):
        for payload in (['b', 'a', 'a'], {'data': ['b', {'id': 'a'}, {'name': 'c'}]}, {'models': [' b ', 'a', 'b']}):
            with self.subTest(payload=payload):
                value, _ = self.fetch(payload)
                expected = ['a', 'b', 'c'] if isinstance(payload, dict) and 'data' in payload else ['a', 'b']
                self.assertEqual(value['models'], expected)
                self.assertEqual(value['total'], len(expected))

    def test_empty_recognized_catalog_is_a_success(self):
        for payload in ([], {'data': []}, {'models': []}):
            with self.subTest(payload=payload):
                value, calls = self.fetch(payload)
                self.assertEqual(value, {'models': [], 'total': 0})
                self.assertEqual(len(calls), 1)

    def test_unrecognized_or_error_envelopes_do_not_masquerade_as_empty_catalogs(self):
        payloads = ({}, {'unexpected': []}, {'error': {'message': KEY}}, None, False, 23, 'not a list', {'data': {}}, {'data': None}, {'models': 'a'})
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, '结构|错误|模型列表') as caught:
                    self.fetch(payload)
                self.assertNotIn(KEY, str(caught.exception))

    def test_invalid_ids_are_not_stringified_into_fake_models(self):
        for payload in ({'data': [{'id': 123}]}, {'models': [{'id': {'nested': 'a'}}]}, [None, 1, '', '  ', {}]):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError): self.fetch(payload)
        value, _ = self.fetch({'data': [{'id': ['invalid']}, {'id': 'real-model'}, None, '', {'name': 'named-model'}]})
        self.assertEqual(value['models'], ['named-model', 'real-model'])

    def test_malformed_json_and_html_are_explicit_errors(self):
        for raw in (b'not json', b'<html>Proxy login</html>', b'{"data":'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, 'JSON'): self.fetch(raw=raw)

    def test_http_errors_are_not_retried_and_do_not_echo_secrets(self):
        for status in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(status, json={'error': {'message': KEY}})
                with self.assertRaisesRegex(ValueError, 'HTTP ' + str(status)) as caught:
                    fetch_models(BASE, KEY, transport=httpx.MockTransport(handle))
                self.assertEqual(len(calls), 1)
                self.assertNotIn(KEY, str(caught.exception))

    def test_redirect_is_not_followed_to_another_origin(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(302, headers={'Location': 'https://other-origin.test/models'})
        with self.assertRaisesRegex(ValueError, 'HTTP 302'):
            fetch_models(BASE, KEY, transport=httpx.MockTransport(handle))
        self.assertEqual(len(calls), 1)

    def test_network_and_timeout_errors_propagate_without_retry(self):
        for error in (httpx.ConnectError, httpx.ReadTimeout):
            with self.subTest(error=error):
                calls = []
                def handle(request):
                    calls.append(request)
                    raise error('offline fixture', request=request)
                with self.assertRaises(error): fetch_models(BASE, KEY, transport=httpx.MockTransport(handle))
                self.assertEqual(len(calls), 1)

    def test_unknown_auth_is_rejected_before_a_request(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={'data': []})
        with self.assertRaises(ValueError): fetch_models(BASE, KEY, 'typo-auth', transport=httpx.MockTransport(handle))
        self.assertEqual(calls, [])


if __name__ == '__main__': unittest.main()
