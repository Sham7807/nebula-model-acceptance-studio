"""Bounded, redacted HTTP evidence for the official synchronous KVV test suite.

Call ``install(key, base, timeout, evidence_path, emit=None)`` once in the pytest
process, then ``set_case_id(nodeid)`` from its test hooks. The returned recorder
has ``summary()`` and ``uninstall()`` methods. JSONL contains request_start and
request_finish records joined by request_id; emit receives compact versions.

The wrapper never pre-consumes a caller-owned streaming response. It records
underlying bytes while httpx/SDK callers consume them, and preserves send's
normal eager-read behavior when stream=False. Transport checks are independent
of official pytest outcomes. A failed HTTP status is not a passed negative test.
The total deadline closes real httpcore sockets, including after headers arrive;
custom transports without interruptible sockets can only stop on their next read.
"""
from __future__ import annotations

import codecs
import contextvars
import json
import re
import socket
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

import httpx

_CASE_ID = contextvars.ContextVar('kvv_case_id', default='')
_INSTALL_LOCK = threading.Lock()
_INSTALLED = None
_CREDENTIAL = re.compile(r'^(authorization|proxy-authorization|x-api-key|x-goog-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie|set-cookie|secret|password)$', re.I)
_SECRET_JSON = re.compile(r'("(?:authorization|proxy-authorization|x-api-key|x-goog-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie|set-cookie|secret|password)"\s*:\s*)"(?:\\.|[^"\\])*"', re.I)
_SENSITIVE_QUERY = re.compile(r'([?&](?:api[_-]?key|key|access_token|refresh_token|secret|password)=)[^&#\s"<>]*', re.I)
_REQUEST_ID_HEADERS = {'request-id', 'x-request-id', 'x-moonshot-request-id', 'x-ms-request-id', 'x-correlation-id', 'trace-id'}


def set_case_id(nodeid):
    """Set the current pytest case for subsequent requests in this context."""
    return _CASE_ID.set(str(nodeid or ''))


def reset_case_id(token):
    _CASE_ID.reset(token)


def _origin(url):
    u = urlsplit(str(url))
    return u.scheme.lower(), (u.hostname or '').lower(), u.port or (443 if u.scheme.lower() == 'https' else 80)


def _interrupt_stream(stream):
    # Closing immediately after shutdown can race a blocked select/poll on
    # macOS. Successful shutdown wakes the reader; its cleanup owns close.
    try:
        sock = stream.get_extra_info('socket') if stream is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                return True
            except OSError:
                pass
        if stream is not None:
            stream.close()
    except Exception:
        pass
    return False


class _SSE:
    def __init__(self):
        self.buffer = ''
        self.first = True
        self.events = self.json_events = self.done = 0
        self.errors, self.malformed, self.models, self.finish_reasons = [], [], [], []
        self.incomplete = self.frame_too_large = False

    def feed(self, text):
        if self.first and text:
            text = text.lstrip('\ufeff')
            self.first = False
        self.buffer += text
        while True:
            match = re.search(r'\r?\n\r?\n|\r\r', self.buffer)
            if not match:
                if len(self.buffer) > 1024 * 1024:
                    self.buffer = ''
                    self.frame_too_large = True
                return
            frame, self.buffer = self.buffer[:match.start()], self.buffer[match.end():]
            data, event_type = [], ''
            for line in re.split(r'\r\n|\n|\r', frame):
                if not line or line.startswith(':'):
                    continue
                field, _, value = line.partition(':')
                value = value[1:] if value.startswith(' ') else value
                if field == 'data':
                    data.append(value)
                elif field == 'event':
                    event_type = value
            if not data:
                if event_type == 'error' and len(self.errors) < 20:
                    self.errors.append('event:error without data')
                continue
            self.events += 1
            raw = '\n'.join(data)
            if raw.strip() == '[DONE]':
                self.done += 1
                continue
            try:
                item = json.loads(raw)
                if not isinstance(item, dict):
                    raise ValueError('SSE data is not a JSON object')
            except (ValueError, TypeError) as exc:
                if len(self.malformed) < 20:
                    self.malformed.append(str(exc))
                continue
            self.json_events += 1
            if (event_type == 'error' or item.get('error') is not None or item.get('type') == 'error') and len(self.errors) < 20:
                self.errors.append(json.dumps(item.get('error', item), ensure_ascii=False)[:4096])
            model = item.get('model')
            if isinstance(model, str) and model not in self.models and len(self.models) < 20:
                self.models.append(model)
            choices = item.get('choices')
            if isinstance(choices, list):
                for choice in choices:
                    reason = choice.get('finish_reason') if isinstance(choice, dict) else None
                    if isinstance(reason, str) and reason and reason not in self.finish_reasons and len(self.finish_reasons) < 20:
                        self.finish_reasons.append(reason)

    def finish(self):
        self.incomplete = bool(self.buffer.strip())
        self.buffer = ''


class _ContentDecoder:
    def __init__(self, encoding):
        self.encoding = (encoding or 'identity').strip().lower()
        self.error = None
        self.decoder = None
        try:
            if self.encoding in ('', 'identity'):
                pass
            elif self.encoding == 'gzip':
                self.decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
            elif self.encoding == 'deflate':
                self.decoder = zlib.decompressobj()
            elif self.encoding == 'br':
                import brotli
                self.decoder = brotli.Decompressor()
            elif self.encoding == 'zstd':
                import zstandard
                self.decoder = zstandard.ZstdDecompressor().decompressobj()
            else:
                self.error = 'unsupported content encoding: ' + self.encoding
        except ImportError:
            self.error = 'decoder unavailable: ' + self.encoding

    def decode(self, data):
        if self.error:
            return b''
        try:
            if self.decoder is None:
                return data
            return self.decoder.process(data) if self.encoding == 'br' else self.decoder.decompress(data)
        except Exception as exc:
            self.error = 'content decoding failed: ' + type(exc).__name__
            return b''

    def finish(self):
        if self.error or self.decoder is None or self.encoding == 'br':
            return b''
        try:
            return self.decoder.flush()
        except Exception as exc:
            self.error = 'content decoding failed: ' + type(exc).__name__
            return b''


class _Request:
    def __init__(self, owner, request, number, client):
        self.owner, self.request, self.client = owner, request, client
        self.id = 'request-%06d' % number
        self.case_id = _CASE_ID.get()
        self.started = time.monotonic()
        self.response = self.network_stream = None
        self.status = None
        self.reason = None
        self.error = None
        self.finished = False
        self.reading = False
        self.bytes = self.decoded_bytes = self.wire_requests = 0
        self.body, self.body_length = [], 0
        self.truncated = False
        self.requested_stream = None
        self.request_model = None
        self.response_headers = []
        self.decoder = None
        self.utf8 = codecs.getincrementaldecoder('utf-8')('replace')
        self.sse = _SSE()
        self.lock = threading.RLock()
        self.done = threading.Event()
        self._prepare()
        self.monitor = threading.Thread(target=self._watch, name='kvv-http-deadline', daemon=True)
        self.monitor.start()

    def _prepare(self):
        try:
            content = self.request.content
        except httpx.RequestNotRead:
            content = None
        body = None
        if content is not None:
            try:
                item = json.loads(content)
                if isinstance(item, dict):
                    self.requested_stream = bool(item.get('stream', False))
                    self.request_model = item.get('model')
            except (ValueError, UnicodeDecodeError):
                pass
            body = content[:self.owner.max_body_bytes].decode('utf-8', errors='replace')
        headers = {name: value for name, value in self.request.headers.items() if name.lower() in {'content-type', 'accept'}}
        self.owner._record({'type': 'request_start', 'request_id': self.id, 'case_id': self.case_id,
                            'method': self.request.method, 'url': str(self.request.url), 'headers': headers,
                            'requested_model': self.request_model, 'requested_stream': self.requested_stream,
                            'body': body, 'body_bytes': len(content) if content is not None else None,
                            'body_truncated': content is not None and len(content) > self.owner.max_body_bytes},
                           compact_exclude={'body', 'headers'})

    def trace(self, name, info):
        if name.endswith(('connect_tcp.complete', 'connect_unix_socket.complete', 'start_tls.complete')):
            self.network_stream = info.get('return_value')
            if self.reason:
                _interrupt_stream(self.network_stream)
        if name.endswith('send_request_headers.started'):
            with self.lock:
                self.wire_requests += 1
                self._capture_reused_connection()

    def _capture_reused_connection(self):
        # httpcore emits no connect event when reusing a pooled HTTP/1.1 socket.
        # At send_request_headers.started its connection is already ACTIVE.
        # Inspect only an unambiguous connection for this origin; never choose an
        # idle/different-origin connection or a shared HTTP/2 connection.
        if self.network_stream is not None:
            return
        transports = [getattr(self.client, '_transport', None)]
        transports += [item for item in getattr(self.client, '_mounts', {}).values() if item is not None]
        streams = []
        wanted = (self.request.url.scheme.encode(), self.request.url.host.encode(), self.request.url.port or (443 if self.request.url.scheme == 'https' else 80))
        for candidate in transports:
            pool = getattr(candidate, '_pool', None)
            for connection in getattr(pool, 'connections', ()):
                origin = getattr(connection, '_origin', None)
                actual = (getattr(origin, 'scheme', None), getattr(origin, 'host', None), getattr(origin, 'port', None))
                underlying = getattr(connection, '_connection', None)
                if actual != wanted or type(underlying).__name__ != 'HTTP11Connection':
                    continue
                try:
                    if connection.is_idle() or connection.is_closed():
                        continue
                except Exception:
                    continue
                stream = getattr(underlying, '_network_stream', None)
                if stream is not None and all(stream is not known for known in streams):
                    streams.append(stream)
        if len(streams) == 1:
            self.network_stream = streams[0]

    def _watch(self):
        remaining = self.owner.timeout - (time.monotonic() - self.started)
        if self.done.wait(max(0, remaining)):
            return
        self.interrupt('total_timeout')

    def interrupt(self, reason):
        with self.lock:
            if self.finished:
                return
            self.reason = self.reason or reason
            response, stream = self.response, self.network_stream
            # Keep completion/socket release serialized with shutdown. Otherwise a
            # near-deadline watchdog could close a socket already returned to its pool.
            if response is not None:
                if not _interrupt_stream(response.extensions.get('network_stream') or stream) or not self.reading:
                    try:
                        response.close()
                    except Exception:
                        pass
            else:
                _interrupt_stream(stream)

    def attach(self, response):
        self.response, self.status = response, response.status_code
        self.network_stream = response.extensions.get('network_stream') or self.network_stream
        self.response_headers = list(response.headers.multi_items())
        self.decoder = _ContentDecoder(response.headers.get('content-encoding', ''))
        if response.is_stream_consumed and not self.reason:
            self.decoder = _ContentDecoder('')
            self.receive(response.content)
            self.complete()
            return
        response.stream = _RecordingStream(response.stream, self)
        if self.reason:
            self.interrupt(self.reason)
            raise self.timeout_error()

    def timeout_error(self):
        return httpx.ReadTimeout('KVV request exceeded the %.3g second total deadline' % self.owner.timeout, request=self.request)

    def receive(self, data):
        with self.lock:
            if self.finished:
                return
            self.bytes += len(data)
            self._decoded(self.decoder.decode(data))

    def _decoded(self, data):
        self.decoded_bytes += len(data)
        text = self.utf8.decode(data)
        available = max(0, self.owner.max_body_bytes - self.body_length)
        encoded = text.encode('utf-8')
        saved = encoded[:available].decode('utf-8', errors='ignore')
        if saved:
            self.body.append(saved)
            self.body_length += len(saved.encode('utf-8'))
        self.truncated |= len(encoded) > available
        # Inspect all small SSE frames even after the raw-evidence cap is reached.
        if self.requested_stream:
            self.sse.feed(text)

    def complete(self, reason='eof', error=None):
        with self.lock:
            if self.finished:
                return
            self.finished = True
            self.done.set()
            if self.decoder:
                self._decoded(self.decoder.finish())
            tail = self.utf8.decode(b'', final=True)
            if tail:
                self.body.append(tail[:max(0, self.owner.max_body_bytes - self.body_length)])
                if self.requested_stream:
                    self.sse.feed(tail)
            self.sse.finish()
            if reason == 'network_error' and isinstance(error, httpx.TimeoutException):
                reason = 'total_timeout' if time.monotonic() - self.started >= self.owner.timeout else 'idle_timeout'
            self.reason = self.reason or reason
            self.error = error
            body = ''.join(self.body)
            checks = self._checks(body)
            verdict = 'failed' if any(c['status'] == 'failed' for c in checks) else 'inconclusive' if any(c['status'] == 'inconclusive' for c in checks) else 'passed'
            models = list(self.sse.models)
            if not self.requested_stream and not self.truncated:
                try:
                    model = json.loads(body).get('model')
                    if isinstance(model, str):
                        models.append(model)
                except (ValueError, AttributeError):
                    pass
            # A caller/session closing an incompletely consumed response does not
            # demonstrate an infrastructure failure. In particular, token tests may
            # stop at usage then fail a concrete assertion; keep that failure intact.
            record = {'type': 'request_finish', 'request_id': self.id, 'case_id': self.case_id,
                      'http_status': self.status, 'duration_ms': round((time.monotonic() - self.started) * 1000),
                      'termination': self.reason, 'status': verdict, 'wire_requests': self.wire_requests,
                      'requested_model': self.request_model, 'requested_stream': self.requested_stream,
                      'method': self.request.method, 'url': str(self.request.url),
                      'infrastructure_error': self.status in (401, 403, 408, 429) or self.status is not None and self.status >= 500 or self.reason in ('total_timeout', 'idle_timeout', 'network_error'),
                      'response_headers': self.response_headers,
                      'upstream_request_ids': [{ 'header': n, 'value': v } for n, v in self.response_headers if n.lower() in _REQUEST_ID_HEADERS],
                      'response_models': models, 'body': body, 'body_bytes': self.bytes,
                      'decoded_bytes': self.decoded_bytes, 'body_truncated': self.truncated,
                      'checks': checks, 'error': str(error) if error else None,
                      'sse': {'events': self.sse.events, 'json_events': self.sse.json_events,
                              'done_count': self.sse.done, 'finish_reasons': self.sse.finish_reasons,
                              'errors': self.sse.errors, 'malformed': self.sse.malformed}}
            self.body = []
        self.owner._finish(self, record)

    def _checks(self, body):
        def check(name, status, detail):
            return {'id': name, 'status': status, 'detail': detail}
        complete = self.reason in ('eof', 'done')
        checks = [] if complete else [check('transport_complete', 'failed' if self.reason == 'total_timeout' else 'inconclusive', self.reason)]
        if self.status is None or not 200 <= self.status < 300:
            return checks + [check('http_response', 'inconclusive', 'HTTP %s; official cases judge rejection semantics' % self.status)]
        if self.decoder and self.decoder.error:
            return checks + [check('response_decode', 'inconclusive', self.decoder.error)]
        if self.requested_stream:
            content_type = (self.response.headers.get('content-type') or '').split(';')[0].strip().lower()
            incomplete_status = 'failed' if complete else 'inconclusive'
            frames_bad = self.sse.malformed or self.sse.frame_too_large
            frames_status = 'failed' if frames_bad else 'passed' if complete and self.sse.json_events and not self.sse.incomplete else incomplete_status
            checks += [check('sse_content_type', 'passed' if content_type == 'text/event-stream' else 'failed', content_type or 'missing Content-Type'),
                       check('sse_frames', frames_status, 'JSON frames=%s; malformed=%s; incomplete=%s; oversized=%s' % (self.sse.json_events, len(self.sse.malformed), self.sse.incomplete, self.sse.frame_too_large)),
                       check('sse_done', 'passed' if self.sse.done == 1 else incomplete_status, '[DONE] count=%s' % self.sse.done),
                       check('sse_error', 'failed' if self.sse.errors else 'passed' if complete else 'inconclusive', 'stream errors=%s' % len(self.sse.errors))]
            if self.request.url.path.rstrip('/').endswith('/chat/completions'):
                checks.append(check('sse_finish_reason', 'passed' if self.sse.finish_reasons else incomplete_status, ', '.join(self.sse.finish_reasons) or 'missing finish_reason'))
            return checks
        if not complete:
            return checks
        if self.truncated:
            return [check('json_body', 'inconclusive', 'Response exceeded the evidence limit; official parser still receives all bytes')]
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('JSON response is not an object')
        except (ValueError, TypeError):
            return [check('json_body', 'failed', 'Successful non-stream response is not a JSON object')]
        return [check('json_body', 'failed' if value.get('error') is not None else 'passed', 'HTTP success with error envelope' if value.get('error') is not None else 'JSON object received')]


class _RecordingStream(httpx.SyncByteStream):
    def __init__(self, stream, record):
        self.stream, self.record = stream, record
        self.closed = False
        self.lock = threading.Lock()

    def __iter__(self):
        try:
            iterator = iter(self.stream)
            while True:
                with self.record.lock:
                    if self.record.reason == 'total_timeout':
                        raise self.record.timeout_error()
                    self.record.reading = True
                try:
                    data = next(iterator)
                except StopIteration:
                    break
                finally:
                    with self.record.lock:
                        self.record.reading = False
                if self.record.reason == 'total_timeout':
                    raise self.record.timeout_error()
                self.record.receive(data)
                yield data
            if self.record.reason == 'total_timeout':
                raise self.record.timeout_error()
            self.record.complete()
        except Exception as exc:
            self.record.complete('network_error', exc)
            # Socket shutdown only wakes the reader. Finish descriptor release
            # here on that reader, even if the caller used iter_bytes directly
            # without a response context manager.
            if self.record.response is not None:
                try:
                    self.record.response.close()
                except Exception:
                    pass
            if self.record.reason == 'total_timeout' and not isinstance(exc, httpx.ReadTimeout):
                raise self.record.timeout_error() from exc
            raise

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        # [DONE] is the stream's completion marker. SDKs commonly stop consuming
        # immediately at it; explicit close at that boundary is not truncation.
        reason = 'done' if self.record.requested_stream and self.record.sse.done == 1 else 'client_closed'
        self.record.complete(reason)
        self.stream.close()


class Recorder:
    def __init__(self, key, base, timeout, evidence_path, emit=None, max_body_bytes=2 * 1024 * 1024, max_run_bytes=64 * 1024 * 1024):
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 3600:
            raise ValueError('timeout must be a positive bounded number of seconds')
        self.origin = _origin(base)
        if self.origin[0] not in ('http', 'https') or not self.origin[1]:
            raise ValueError('base must be an HTTP(S) URL')
        self.key = str(key or '')
        self.max_body_bytes, self.max_run_bytes = int(max_body_bytes), int(max_run_bytes)
        if self.max_body_bytes < 1 or self.max_run_bytes < 1:
            raise ValueError('evidence limits must be positive')
        self.path = Path(evidence_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.emit = emit if callable(emit) else lambda data: None
        self.lock = threading.RLock()
        self.active = {}
        self.requests, self.checks = [], []
        self.started = self.completed = self.wire_requests = self.evidence_bytes = 0
        self.failed = self.inconclusive = self.passed = 0
        self.installed = False
        self.original_send = None
        self.wrapper = None

    def redact(self, value):
        if isinstance(value, dict):
            return {k: '[已隐藏]' if _CREDENTIAL.match(str(k)) else self.redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            if len(value) == 2 and isinstance(value[0], str) and _CREDENTIAL.match(value[0]):
                return [value[0], '[已隐藏]']
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            if self.key:
                for key in {self.key, quote(self.key, safe=''), quote_plus(self.key)}:
                    value = value.replace(key, '[已隐藏]')
            value = _SECRET_JSON.sub(r'\1"[已隐藏]"', value)
            value = _SENSITIVE_QUERY.sub(r'\1[已隐藏]', value)
            return re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[已隐藏]', value)
        return value

    def _record(self, data, compact_exclude=()):
        data = self.redact(data)
        data.setdefault('id', data.get('request_id'))
        data.setdefault('completed', self.completed)
        data.setdefault('total', None)
        with self.lock:
            raw = (json.dumps(data, ensure_ascii=False) + '\n').encode('utf-8')
            if self.evidence_bytes + len(raw) > self.max_run_bytes:
                data['body'] = '[证据正文达到整轮大小上限]'
                data['body_truncated'] = True
                raw = (json.dumps(data, ensure_ascii=False) + '\n').encode('utf-8')
            # Keep bounded metadata after the body budget is exhausted (up to 5,000 requests).
            if self.started <= 5000:
                with self.path.open('ab') as output:
                    output.write(raw)
                self.evidence_bytes += len(raw)
        try:
            self.emit({k: v for k, v in data.items() if k not in set(compact_exclude) | {'body', 'response_headers', 'sse'}})
        except Exception:
            pass

    def _finish(self, record, data):
        with self.lock:
            self.active.pop(record.id, None)
            self.completed += 1
            self.wire_requests += record.wire_requests
            setattr(self, data['status'], getattr(self, data['status']) + 1)
            compact = {k: data[k] for k in ('request_id', 'case_id', 'http_status', 'duration_ms', 'termination', 'status', 'wire_requests', 'upstream_request_ids', 'response_models', 'requested_model', 'requested_stream', 'method', 'url', 'infrastructure_error')}
            compact['id'] = data['request_id']
            if len(self.requests) < 5000:
                self.requests.append(self.redact(compact))
                labels = {'transport_complete':'请求完整性', 'response_decode':'响应解码', 'sse_content_type':'SSE 内容类型', 'sse_frames':'SSE 帧结构', 'sse_done':'SSE 完成标记', 'sse_error':'流中错误', 'sse_finish_reason':'流式完成原因', 'json_body':'JSON 响应结构', 'http_response':'HTTP 响应状态'}
                self.checks.extend(self.redact([{**check, 'label': labels.get(check['id'], check['id']), 'request_id': record.id, 'case_id': record.case_id} for check in data['checks']]))
        self._record(data)

    def summary(self):
        with self.lock:
            return {'request_count': self.started, 'completed_requests': self.completed,
                    'requests_started': self.started, 'requests_completed': self.completed,
                    'requests': list(self.requests), 'checks': list(self.checks),
                    'wire_requests': self.wire_requests, 'active_requests': len(self.active),
                    'passed': self.passed, 'failed': self.failed, 'inconclusive': self.inconclusive,
                    'evidence_bytes': self.evidence_bytes}

    def send(self, client, request, *args, **kwargs):
        if _origin(request.url) != self.origin:
            return self.original_send(client, request, *args, **kwargs)
        with self.lock:
            self.started += 1
            number = self.started
        record = _Request(self, request, number, client)
        with self.lock:
            self.active[record.id] = record
        had_trace = 'trace' in request.extensions
        previous_trace = request.extensions.get('trace')
        def trace(name, info):
            record.trace(name, info)
            if previous_trace:
                previous_trace(name, info)
        request.extensions['trace'] = trace
        had_timeout = 'timeout' in request.extensions
        old_timeout = request.extensions.get('timeout', {})
        request.extensions['timeout'] = {name: min(float(value), self.timeout) if value is not None else self.timeout for name, value in old_timeout.items()} or {name: self.timeout for name in ('connect', 'read', 'write', 'pool')}
        caller_stream = kwargs.get('stream', False)
        kwargs['stream'] = True
        try:
            if record.reason == 'total_timeout':
                raise record.timeout_error()
            response = self.original_send(client, request, *args, **kwargs)
            record.attach(response)
            if not caller_stream:
                response.read()
            return response
        except Exception as exc:
            record.complete('network_error', exc)
            if record.reason == 'total_timeout' and not isinstance(exc, httpx.ReadTimeout):
                raise record.timeout_error() from exc
            raise
        finally:
            # Do not leave a closed request's trace callback attached to a reusable
            # httpx.Request; its old watchdog state must never touch a future socket.
            if had_trace: request.extensions['trace'] = previous_trace
            else: request.extensions.pop('trace', None)
            if had_timeout: request.extensions['timeout'] = old_timeout
            else: request.extensions.pop('timeout', None)

    def uninstall(self):
        global _INSTALLED
        with _INSTALL_LOCK:
            if self.installed and httpx.Client.send is self.wrapper:
                httpx.Client.send = self.original_send
            self.installed = False
            if _INSTALLED is self:
                _INSTALLED = None
        for record in list(self.active.values()):
            record.interrupt('recorder_closed')
            record.complete('recorder_closed')
        self.key = ''


def install(key, base, timeout, evidence_path, emit=None, **limits):
    """Install a process-local synchronous httpx recorder; return its handle."""
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED is not None:
            raise RuntimeError('A KVV transport recorder is already installed')
        recorder = Recorder(key, base, timeout, evidence_path, emit, **limits)
        recorder.original_send = httpx.Client.send
        def send(client, request, *args, **kwargs):
            return recorder.send(client, request, *args, **kwargs)
        recorder.wrapper = send
        httpx.Client.send = send
        recorder.installed = True
        _INSTALLED = recorder
        return recorder
