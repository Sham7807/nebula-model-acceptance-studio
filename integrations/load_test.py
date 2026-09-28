"""Bounded API load-test engine used by the desktop workbench.

The engine is deliberately small and deterministic: it only executes the URL
provided by the authenticated local workbench, caps request volume/concurrency,
and exposes cancellation and percentile metrics. It never retries a request,
which keeps the observed error rate honest and avoids accidental traffic
amplification.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_REQUESTS = 20_000
MAX_DURATION = 600
MAX_CONCURRENCY = 100
MAX_BODY = 2 * 1024 * 1024
MAX_HEADERS = 64
MAX_HEADER_VALUE = 16 * 1024
MAX_SAMPLES = 250

# OpenAI-compatible gateways use several spellings for the usage object.  The
# normalizer below deliberately accepts the common OpenAI, Anthropic and
# Gemini forms without trying to estimate tokens when a provider omits usage.
# A missing usage field is reported as "unavailable" in the final report; it
# must never be rendered as zero because that would falsely imply no tokens
# were consumed.
_TOKEN_KEYS = {
    'input': ('prompt_tokens', 'input_tokens', 'promptTokenCount', 'inputTokenCount', 'input'),
    'output': ('completion_tokens', 'output_tokens', 'candidatesTokenCount', 'outputTokenCount', 'output'),
    'total': ('total_tokens', 'totalTokens', 'total'),
    'cached': ('cached_tokens', 'cache_read_input_tokens', 'cache_read_tokens', 'cachedContentTokenCount'),
}


def _non_negative_int(value):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def extract_usage(payload):
    """Extract explicit token usage from a JSON response.

    ``None`` means the provider did not return a usable usage object.  The
    function intentionally does not derive tokens from character counts.
    """
    if not isinstance(payload, dict):
        return None
    candidates = []
    for key in ('usage', 'token_usage', 'tokenUsage', 'usageMetadata'):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    # Some gateways put usage under the first choice/message envelope.
    for key in ('message', 'response'):
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get('usage'), dict):
            candidates.append(value['usage'])
    if not candidates:
        return None
    usage = {}
    for output_name, keys in _TOKEN_KEYS.items():
        value = None
        for source in candidates:
            for key in keys:
                value = _non_negative_int(source.get(key))
                if value is not None:
                    break
            if value is not None:
                break
        if value is not None:
            usage[output_name] = value
    # A usage object without any known numeric field is not useful evidence.
    if not usage:
        return None
    if 'total' not in usage and 'input' in usage and 'output' in usage:
        usage['total'] = usage['input'] + usage['output']
        usage['total_derived'] = True
    usage['reported'] = True
    return usage


def extract_stream_usage(content):
    """Read usage from an OpenAI/Anthropic SSE response when available.

    Providers normally emit usage in the final ``data:`` event.  We inspect
    only JSON event payloads and retain the last complete usage object; stream
    text itself is never treated as a token estimate.
    """
    if not content:
        return None
    try:
        text = content.decode('utf-8', errors='replace') if isinstance(content, (bytes, bytearray)) else str(content)
    except Exception:
        return None
    found = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('data:'):
            line = line[5:].strip()
        if not line or line == '[DONE]':
            continue
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        usage = extract_usage(value)
        if usage is not None:
            found = usage
    return found


def _endpoint_from_base(base, *, request_format='openai'):
    """Turn a provider base URL into a request endpoint.

    Explicit terminal paths always win.  For an ordinary OpenAI-compatible
    base URL, append ``/v1/chat/completions`` (or ``/chat/completions`` when a
    version path is already present).  Native Anthropic/Gemini callers can
    still pass ``url`` explicitly and retain their protocol.
    """
    value = _validate_url(base)
    parsed = urlsplit(value)
    path = parsed.path.rstrip('/')
    if re.search(r'/(?:chat/completions|responses|messages|completions)$', path, re.I):
        return value
    if request_format == 'anthropic':
        suffix = '/messages'
    elif request_format == 'gemini':
        suffix = '/models'
    elif re.search(r'/v\d+(?:beta\d*)?(?:/openai)?$', path, re.I):
        suffix = '/chat/completions'
    else:
        suffix = '/v1/chat/completions'
    return urlunsplit((parsed.scheme, parsed.netloc, path + suffix, '', ''))


def _number(value, name, low, high, *, integer=True, default=None):
    if value is None and default is not None:
        value = default
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须为{'整数' if integer else '数字'}")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} 必须为{'整数' if integer else '数字'}")
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{name} 超出范围 {low}–{high}")
    return number


def _validate_url(raw):
    value = str(raw or '').strip()
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.fragment or
            re.search(r'[\x00-\x20\\]', value)):
        raise ValueError('压测地址必须是完整 HTTP(S) 地址，不能包含账号、密码、片段或控制字符。')
    # urlsplit may defer invalid ports until access.
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError('压测地址端口无效。')
    return value


def resolve_target(raw):
    """Resolve the target host for a preflight display; no HTTP request is made."""
    value = _validate_url(raw)
    parsed = urlsplit(value)
    try:
        records = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return {'url': value, 'hostname': parsed.hostname, 'addresses': [], 'error': str(exc)[:240]}
    addresses = sorted({record[4][0] for record in records if record and record[4]})
    return {'url': value, 'hostname': parsed.hostname, 'port': parsed.port or (443 if parsed.scheme == 'https' else 80), 'addresses': addresses}


def validate_config(data):
    """Validate and normalize a user supplied load-test configuration."""
    if not isinstance(data, dict):
        raise ValueError('压测配置必须是 JSON 对象。')
    # The desktop flow is intentionally simple: Base URL + API key, followed
    # by model selection.  Keep the old ``url``/``headers`` contract for
    # backwards compatibility while accepting the new friendly aliases.
    request_format = str(data.get('request_format') or data.get('format') or 'openai').lower().strip()
    if request_format in ('openai-compatible', 'openai_compatible', 'chat'):
        request_format = 'openai'
    if request_format not in {'openai', 'anthropic', 'gemini', 'custom'}:
        raise ValueError('请求格式必须是 openai、anthropic、gemini 或 custom。')
    raw_url = data.get('url') or data.get('endpoint')
    raw_base = data.get('base_url') or data.get('base')
    url = _validate_url(raw_url) if raw_url else _endpoint_from_base(raw_base, request_format=request_format)
    method = str(data.get('method', 'POST')).upper().strip()
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
        raise ValueError('请求方法不受支持。')

    headers = data.get('headers') or {}
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise ValueError(f'请求头必须是对象且最多 {MAX_HEADERS} 项。')
    normalized_headers = {}
    for key, value in headers.items():
        key = str(key).strip()
        if not key or re.search(r'[\r\n]', key) or re.search(r'[\r\n]', str(value)):
            raise ValueError('请求头名称和值不能包含换行。')
        if key.lower() in {'host', 'content-length', 'connection', 'transfer-encoding'}:
            raise ValueError(f'不允许自定义 {key} 请求头。')
        text = str(value)
        if len(text) > MAX_HEADER_VALUE:
            raise ValueError(f'请求头 {key} 过长。')
        normalized_headers[key] = text

    # Inject the API key only after validation, and only when the caller did
    # not already provide an explicit authentication header.  The secret is
    # kept in memory for this run and removed by the server worker finally
    # block; redacted_config never persists it.
    api_key = str(data.get('api_key') or data.get('key') or '').strip()
    if any(c in api_key for c in ('\r', '\n')):
        raise ValueError('API Key 不能包含换行。')
    auth = str(data.get('auth') or 'bearer').lower().strip()
    if auth not in {'bearer', 'anthropic', 'gemini', 'none'}:
        raise ValueError('鉴权方式必须是 bearer、anthropic、gemini 或 none。')
    header_names = {key.lower() for key in normalized_headers}
    if api_key and auth != 'none' and not header_names.intersection({'authorization', 'x-api-key', 'x-goog-api-key'}):
        if auth == 'anthropic':
            normalized_headers['x-api-key'] = api_key
            normalized_headers.setdefault('anthropic-version', '2023-06-01')
        elif auth == 'gemini':
            normalized_headers['x-goog-api-key'] = api_key
        else:
            normalized_headers['Authorization'] = 'Bearer ' + api_key

    body = data.get('body', None)
    model = str(data.get('model') or '').strip()
    if not model and isinstance(body, dict):
        model = str(body.get('model') or '').strip()
    # For OpenAI-compatible traffic, model can live alongside the config or
    # inside the body.  Supplying it at the top level makes the UI usable
    # immediately after model discovery without hand-editing JSON.
    if body is None and method not in {'GET', 'HEAD', 'OPTIONS'} and model:
        body = {'model': model, 'messages': [{'role': 'user', 'content': 'ping'}]}
    elif isinstance(body, dict) and model and not body.get('model'):
        body = {**body, 'model': model}
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        if not any(key.lower() == 'content-type' for key in normalized_headers):
            normalized_headers['Content-Type'] = 'application/json'
    elif body is not None:
        body = str(body)
    if body is not None and len(body.encode('utf-8')) > MAX_BODY:
        raise ValueError(f'请求体不能超过 {MAX_BODY // 1024 // 1024} MB。')
    if method in {'GET', 'HEAD', 'OPTIONS'} and body:
        # Keep this a warning-free deterministic API: a body on these methods is
        # legal in HTTP but is rejected by many gateways and makes comparisons
        # confusing.
        raise ValueError(f'{method} 请求不应携带请求体。')

    mode = str(data.get('mode', 'requests')).lower()
    if mode not in {'requests', 'duration'}:
        raise ValueError('运行模式必须是 requests 或 duration。')
    total = _number(data.get('total_requests'), 'total_requests', 1, MAX_REQUESTS, default=100, integer=True)
    duration = _number(data.get('duration_seconds'), 'duration_seconds', 1, MAX_DURATION, default=30, integer=True)
    concurrency = _number(data.get('concurrency'), 'concurrency', 1, MAX_CONCURRENCY, default=4, integer=True)
    ramp_up = _number(data.get('ramp_up_milliseconds'), 'ramp_up_milliseconds', 0, 60_000, default=0, integer=True)
    rate_limit = _number(data.get('rate_limit'), 'rate_limit', 0, 2000, default=0, integer=False)
    timeout = _number(data.get('timeout_seconds'), 'timeout_seconds', 0.5, 120, default=30, integer=False)
    expected = data.get('expected_statuses', None)
    if expected is None:
        expected = list(range(200, 400))
    if not isinstance(expected, list) or not expected or len(expected) > 600:
        raise ValueError('expected_statuses 必须是非空数组。')
    try:
        expected = sorted(set(_number(item, 'expected_statuses', 100, 599, integer=True) for item in expected))
    except ValueError:
        raise
    if mode == 'duration' and duration < 1:
        raise ValueError('持续时间必须至少 1 秒。')
    return {
        'url': url, 'method': method, 'headers': normalized_headers,
        'body': body, 'mode': mode, 'total_requests': total,
        'duration_seconds': duration, 'concurrency': concurrency,
        'ramp_up_milliseconds': ramp_up,
        'rate_limit': rate_limit, 'timeout_seconds': timeout,
        'expected_statuses': expected,
        'base_url': str(raw_base or url).strip(),
        'request_format': request_format,
        'model': model,
        # A label is useful in the UI but must never be used in a request.
        'name': str(data.get('name', '')).strip()[:80],
    }


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile
    lower = int(math.floor(rank)); upper = int(math.ceil(rank))
    if lower == upper:
        return round(ordered[lower], 2)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 2)


def redacted_config(config):
    headers = {}
    for key, value in config.get('headers', {}).items():
        low = key.lower()
        headers[key] = '[已隐藏]' if low in {'authorization', 'proxy-authorization', 'x-api-key', 'api-key', 'x-goog-api-key'} else value
    result = {key: value for key, value in config.items() if key not in {'body', 'headers'}}
    # Query strings are valid for API endpoints, but frequently contain
    # access tokens. Keep parameter names while masking all values on both
    # the request URL and the user-facing Base URL field.
    from urllib.parse import parse_qsl, urlencode
    for field in ('url', 'base_url'):
        parsed = urlsplit(str(result.get(field, '')))
        if parsed.query:
            result[field] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                        urlencode([(key, '[已隐藏]') for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]),
                                        parsed.fragment))
    result['headers'] = headers
    if config.get('body') is not None:
        result['body_bytes'] = len(str(config['body']).encode('utf-8'))
        result['body_present'] = True
    return result


# Kept as an internal alias for callers from older desktop builds.
_redacted_config = redacted_config


def _request_once(config):
    started = time.perf_counter()
    try:
        # A fresh client per request keeps worker usage isolated and avoids
        # sharing mutable transport state across threads.
        with httpx.Client(timeout=config['timeout_seconds'], follow_redirects=False, trust_env=False) as client:
            response = client.request(config['method'], config['url'], headers=config['headers'], content=config.get('body'))
        elapsed = (time.perf_counter() - started) * 1000
        usage = None
        content_type = response.headers.get('content-type', '')
        if 'json' in content_type.lower() or response.content[:1] in (b'{', b'['):
            try:
                usage = extract_usage(response.json())
            except (ValueError, TypeError):
                usage = None
        if usage is None and ('event-stream' in content_type.lower() or b'data:' in response.content[:512]):
            usage = extract_stream_usage(response.content)
        return {'ok': response.status_code in config['expected_statuses'], 'status': response.status_code,
                'latency_ms': round(elapsed, 2), 'bytes': len(response.content), 'error_type': None,
                'usage': usage}
    except httpx.TimeoutException as exc:
        return {'ok': False, 'status': None, 'latency_ms': round((time.perf_counter() - started) * 1000, 2), 'bytes': 0,
                'error_type': 'timeout', 'error': str(exc)[:240], 'usage': None}
    except httpx.RequestError as exc:
        return {'ok': False, 'status': None, 'latency_ms': round((time.perf_counter() - started) * 1000, 2), 'bytes': 0,
                'error_type': 'network_error', 'error': str(exc)[:240], 'usage': None}
    except Exception as exc:  # classify parser/transport faults without leaking credentials
        return {'ok': False, 'status': None, 'latency_ms': round((time.perf_counter() - started) * 1000, 2), 'bytes': 0,
                'error_type': 'client_error', 'error': str(exc)[:240], 'usage': None}


def run(config, emit=None, cancelled=None):
    """Execute a bounded run and return a report dictionary.

    ``emit`` receives progress events and ``cancelled`` is a zero-argument
    callable. The function never retries requests and returns partial metrics
    when cancelled.
    """
    config = validate_config(config)
    emit = emit or (lambda event: None)
    cancelled = cancelled or (lambda: False)
    started_wall = time.time(); started = time.perf_counter()
    stop = threading.Event()
    results = []
    results_lock = threading.Lock()
    schedule_lock = threading.Lock()
    next_slot = [started]
    target_total = config['total_requests'] if config['mode'] == 'requests' else None
    deadline = started + config['duration_seconds'] if config['mode'] == 'duration' else None

    def wants_work(index):
        if cancelled() or stop.is_set(): return False
        if target_total is not None: return index < target_total
        return time.perf_counter() < deadline

    def worker(worker_index):
        local_index = worker_index
        if worker_index and config['ramp_up_milliseconds']:
            if stop.wait(worker_index * config['ramp_up_milliseconds'] / 1000):
                return
        while wants_work(local_index):
            if config['rate_limit']:
                interval = 1.0 / config['rate_limit']
                with schedule_lock:
                    now = time.perf_counter(); due = max(now, next_slot[0]); next_slot[0] = due + interval
                if due > now and stop.wait(due - now): return
            if not wants_work(local_index): return
            row = _request_once(config)
            with results_lock:
                results.append(row)
                completed = len(results)
            emit({'type': 'request_finish', 'completed': completed,
                  'total': target_total, 'request_count': completed,
                  'latency_ms': row['latency_ms'], 'status': row.get('status'),
                  'ok': row['ok'], 'usage': row.get('usage'), 'message': '请求完成'})
            local_index += config['concurrency']

    with ThreadPoolExecutor(max_workers=config['concurrency'], thread_name_prefix='load-test') as pool:
        futures = [pool.submit(worker, index) for index in range(config['concurrency'])]
        while futures:
            if cancelled():
                stop.set()
            done = [future for future in futures if future.done()]
            if done:
                futures = [future for future in futures if not future.done()]
                for future in done:
                    try: future.result()
                    except Exception as exc:
                        with results_lock: results.append({'ok': False, 'status': None, 'latency_ms': 0, 'bytes': 0, 'error_type': 'worker_error', 'error': str(exc)[:240]})
            if not futures: break
            if deadline is not None and time.perf_counter() >= deadline: stop.set()
            if target_total is not None and len(results) >= target_total: stop.set()
            time.sleep(0.01)
    finished = time.time(); elapsed = max(time.perf_counter() - started, 0.000001)
    completed = len(results)
    successful = sum(1 for row in results if row.get('ok'))
    failed = completed - successful
    statuses = Counter(str(row['status']) if row.get('status') is not None else 'transport_error' for row in results)
    errors = Counter(row.get('error_type') or ('http_status' if row.get('status') is not None and not row.get('ok') else '') for row in results)
    errors.pop('', None)
    latencies = [float(row.get('latency_ms') or 0) for row in results]
    total_bytes = sum(int(row.get('bytes') or 0) for row in results)
    samples = []
    for row in results[:MAX_SAMPLES]:
        sample = {key: row.get(key) for key in ('ok', 'status', 'latency_ms', 'bytes', 'error_type', 'error') if row.get(key) is not None}
        if row.get('usage') is not None:
            sample['usage'] = row['usage']
        samples.append(sample)
    was_cancelled = bool(cancelled())
    status = 'cancelled' if was_cancelled else 'completed'
    if completed == 0 and not was_cancelled and config['mode'] == 'requests': status = 'error'
    token_totals = Counter()
    usage_requests = 0
    for row in results:
        usage = row.get('usage')
        if not isinstance(usage, dict):
            continue
        usage_requests += 1
        for key in ('input', 'output', 'total', 'cached'):
            value = _non_negative_int(usage.get(key))
            if value is not None:
                token_totals[key] += value
    rate_factor = 60 / elapsed
    token_report = {
        'input': token_totals.get('input') if 'input' in token_totals else None,
        'output': token_totals.get('output') if 'output' in token_totals else None,
        'total': token_totals.get('total') if 'total' in token_totals else None,
        'cached': token_totals.get('cached') if 'cached' in token_totals else None,
        'usage_requests': usage_requests,
        'usage_missing_requests': completed - usage_requests,
    }
    if token_report['total'] is None and token_report['input'] is not None and token_report['output'] is not None:
        token_report['total'] = token_report['input'] + token_report['output']
        token_report['total_derived'] = True
    token_report['input_per_minute'] = round(token_report['input'] * rate_factor, 2) if token_report['input'] is not None else None
    token_report['output_per_minute'] = round(token_report['output'] * rate_factor, 2) if token_report['output'] is not None else None
    token_report['tokens_per_minute'] = round(token_report['total'] * rate_factor, 2) if token_report['total'] is not None else None
    token_report['cached_per_minute'] = round(token_report['cached'] * rate_factor, 2) if token_report['cached'] is not None else None
    result = {
        'suite': 'api_stress', 'status': status,
        'configuration': _redacted_config(config),
        'started_at': started_wall, 'finished_at': finished,
        'summary': {
            'total': target_total if target_total is not None else completed,
            'completed': completed, 'successful': successful, 'failed': failed,
            'cancelled': was_cancelled, 'error_rate': round((failed / completed * 100) if completed else 0, 2),
            'elapsed_seconds': round(elapsed, 3),
            'requests_per_second': round(completed / elapsed, 3),
            # RPM/TPM are rates over the whole completed window.  Keep both
            # names so the desktop and HTML report can use their preferred
            # labels without recomputing from rounded values.
            'rpm': round(completed / elapsed * 60, 2),
            'tpm': token_report['tokens_per_minute'],
            'input_tpm': token_report['input_per_minute'],
            'output_tpm': token_report['output_per_minute'],
            'bytes_per_second': round(total_bytes / elapsed, 1),
            'status_codes': dict(statuses), 'errors': dict(errors),
            'token_usage': token_report,
        },
        'latency_ms': {
            'min': round(min(latencies), 2) if latencies else None,
            'avg': round(sum(latencies) / len(latencies), 2) if latencies else None,
            'p50': _percentile(latencies, .50), 'p90': _percentile(latencies, .90),
            'p95': _percentile(latencies, .95), 'p99': _percentile(latencies, .99),
            'max': round(max(latencies), 2) if latencies else None,
        },
        'samples': samples,
        'limitations': [
            '本工具只反映本轮单客户端、受控并发下的观测结果，不代表长期容量上限。',
            '请求不会自动重试；失败率按原始请求结果统计。',
            '样本明细最多保留 250 条，完整汇总按状态码、错误类型和延迟分位数保留。',
        ],
    }
    emit({'type': 'complete', 'completed': completed, 'total': target_total,
          'request_count': completed, 'status': status, 'message': '压测完成'})
    return result
