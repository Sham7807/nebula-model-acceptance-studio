"""Bounded, GET-only model discovery shared by every workbench panel."""
import os
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx

_AUTH = {'bearer', 'anthropic', 'gemini', 'none'}
_VERSION_PATH = re.compile(r'/v\d+(?:beta\d*)?(?:/openai)?$', re.I)
_REQUEST_SUFFIX = re.compile(r'/(?:chat/completions|responses|completions|messages)$', re.I)
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_REQUESTS = 12
_DEADLINE = 24.0


class DiscoveryError(ValueError):
    """Actionable, redacted error which the service can return to the browser."""
    def __init__(self, message, diagnostics, status=None):
        super().__init__(message)
        self.diagnostics = diagnostics
        self.status = status
        self.code = diagnostics.get('code', 'discovery_failed')
        self.advice = diagnostics.get('suggestion', '')
        self.retryable = bool(diagnostics.get('retryable'))


def _validated_url(base):
    raw = str(base).strip()
    try:
        url = urlsplit(raw)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or re.search(r'[\x00-\x20\\]', raw)):
            raise ValueError()
        _ = url.port
    except (TypeError, ValueError):
        raise ValueError('渠道地址必须是完整 HTTP(S) 地址，不能带账号、查询参数、片段或空白字符。')
    return url


def model_endpoints(base, auth='bearer'):
    """Keep explicit API prefixes/versions; infer versions only when omitted."""
    if auth not in _AUTH:
        raise ValueError('模型列表鉴权方式无效')
    url = _validated_url(base)
    path = _REQUEST_SUFFIX.sub('', url.path.rstrip('/'))
    if path.endswith('/models'):
        paths = [path]
    elif _VERSION_PATH.search(path):
        paths = [path + '/models']
    else:
        version = '/v1beta' if auth == 'gemini' else '/v1'
        paths = [path + version + '/models', path + '/models']
        if not path and url.hostname == 'api.groq.com':
            paths.insert(0, '/openai/v1/models')
        elif not path and url.hostname in ('openrouter.ai', 'api.together.xyz'):
            paths.insert(0, '/api/v1/models' if url.hostname == 'openrouter.ai' else '/v1/models')
        # /api/v1 is common on OpenRouter and some domestic gateways.  Preserve
        # explicit /api prefixes; never turn /api/v1 into /v1.
        if not path.endswith('/api'):
            paths.append(path + '/api/v1/models')
    return list(dict.fromkeys(urlunsplit((url.scheme, url.netloc, path, '', '')) for path in paths))


def model_endpoint(base, auth='bearer'):
    return model_endpoints(base, auth)[0]


def _headers(key, auth):
    headers = {'Accept': 'application/json'}
    if auth == 'bearer':
        headers['Authorization'] = 'Bearer ' + key
    elif auth == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    elif auth == 'gemini':
        headers['x-goog-api-key'] = key
    return headers


def _redact(value, key):
    text = str(value or '')
    if isinstance(key, str) and key:
        text = text.replace(key, '[已隐藏]')
    text = re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)([?&](?:key|api_key|token)=)[^&\s"<>]+', r'\1[已隐藏]', text)
    return text[:350]


def _provider_error(response, key):
    try:
        data = response.json()
    except ValueError:
        return ''
    if not isinstance(data, dict):
        return ''
    error = data.get('error')
    if isinstance(error, dict):
        value = error.get('message') or error.get('type') or error.get('code')
    else:
        value = error if isinstance(error, str) else data.get('message') or data.get('detail')
    return _redact(value, key) if isinstance(value, (str, int)) else ''


def _rows(data, depth=0):
    if isinstance(data, dict) and data.get('error') is not None:
        raise ValueError('模型列表接口返回错误')
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and depth < 4:
        for key in ('data', 'models', 'items', 'result'):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                try:
                    return _rows(value, depth + 1)
                except ValueError:
                    continue
    raise ValueError('模型列表返回结构无法识别：需要 data / models 数组')


def _ids(data, auth):
    rows, values = _rows(data), set()
    for row in rows:
        options = (row,) if isinstance(row, str) else tuple(row.get(k) for k in ('id', 'name', 'model', 'model_id', 'modelId', 'slug')) if isinstance(row, dict) else ()
        value = next((v.strip() for v in options if isinstance(v, str) and v.strip()), '')
        if auth == 'gemini':
            value = re.sub(r'^models/', '', value)
        if value:
            values.add(value)
    if rows and not values:
        raise ValueError('模型列表没有可识别的模型 ID')
    return values


def _next_page(data, endpoint, auth):
    if not isinstance(data, dict):
        return None
    params = {}
    if auth == 'gemini' and isinstance(data.get('nextPageToken'), str) and data['nextPageToken']:
        params = {'pageToken': data['nextPageToken']}
    elif data.get('has_more') is True:
        cursor = data.get('last_id')
        if not cursor:
            entries = _rows(data)
            cursor = entries[-1].get('id') if entries and isinstance(entries[-1], dict) else None
        if isinstance(cursor, str) and cursor:
            params = {'after_id': cursor}
    if not params:
        return None
    url = urlsplit(endpoint)
    return urlunsplit((url.scheme, url.netloc, url.path, urlencode(params), ''))


def _same_origin(a, b):
    a, b = urlsplit(a), urlsplit(b)
    try:
        return (a.scheme, a.hostname, a.port or (443 if a.scheme == 'https' else 80)) == (b.scheme, b.hostname, b.port or (443 if b.scheme == 'https' else 80))
    except ValueError:
        return False


def fetch_models(base, key, auth='bearer', transport=None):
    started = time.monotonic()
    attempts = []
    diag = {'endpoint': '', 'auth': auth, 'attempts': attempts, 'code': '', 'suggestion': '', 'elapsed_ms': 0,
            'retryable': False, 'proxy': bool(os.environ.get('WORKBENCH_OUTBOUND_PROXY', '').strip())}

    def fail(code, message, suggestion, status=None, retryable=False):
        diag.update(code=code, suggestion=suggestion, retryable=retryable, elapsed_ms=round((time.monotonic() - started) * 1000))
        raise DiscoveryError(_redact(message, key) + '。' + suggestion, dict(diag), status)

    try:
        endpoints = model_endpoints(base, auth)
    except ValueError as exc:
        fail('invalid_config', str(exc), '请检查渠道地址和鉴权方式。')
    if not isinstance(key, str) or (auth != 'none' and not key.strip()) or any(c in key for c in ('\n', '\r')):
        fail('invalid_key', '请输入有效密钥', '请重新复制 API Key，或选择不使用鉴权。')
    key = key.strip()
    host = urlsplit(base).hostname
    primary_auth = auth
    # Recognise native public endpoints even when a panel defaults to OpenAI.
    # A user-supplied OpenAI compatibility prefix remains explicit and wins.
    if auth != 'none' and host == 'generativelanguage.googleapis.com' and '/openai' not in urlsplit(base).path:
        primary_auth = 'gemini'
        endpoints = model_endpoints(base, primary_auth)
    elif auth != 'none' and host == 'api.anthropic.com':
        primary_auth = 'anthropic'
    auths = [primary_auth]
    if primary_auth in ('bearer', 'anthropic'):
        auths.append('anthropic' if primary_auth == 'bearer' else 'bearer')
    elif primary_auth == 'gemini':
        auths.append('bearer')

    proxy = os.environ.get('WORKBENCH_OUTBOUND_PROXY', '').strip() or None
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.query or parsed.fragment or re.search(r'[\x00-\x20\\]', proxy):
                raise ValueError()
            _ = parsed.port
        except ValueError:
            fail('proxy_config', '服务器出站代理配置无效', '请管理员检查 WORKBENCH_OUTBOUND_PROXY，使用完整 HTTP(S) 代理地址。')

    def request(client, endpoint, selected_auth):
        current, redirects, retries = endpoint, set(), 0
        while True:
            remaining = _DEADLINE - (time.monotonic() - started)
            diag.update(endpoint=_redact(current, key), auth=selected_auth)
            if remaining <= .05 or len(attempts) >= _MAX_REQUESTS:
                fail('timeout', '获取模型列表达到时间或请求上限', '请重试；若持续失败，请检查服务器到渠道的网络或配置出站代理。', retryable=True)
            entry = {'url': _redact(current, key), 'auth': selected_auth, 'status': None, 'code': '', 'message': ''}
            attempts.append(entry)
            try:
                # HTTPX timeouts are per socket phase, not a total deadline.
                # Keep each phase short, bound catalog bytes, and check elapsed
                # time while reading so a slow/huge response cannot run forever.
                timeout = min(5.0, remaining / 4)
                with client.stream('GET', current, headers=_headers(key, selected_auth), timeout=timeout) as upstream:
                    chunks, size = [], 0
                    for chunk in upstream.iter_bytes():
                        size += len(chunk)
                        if size > 4 * 1024 * 1024:
                            fail('response_too_large', '模型列表超过 4 MB', '请检查是否填写了正确模型列表接口，或联系渠道调整分页。', upstream.status_code)
                        if time.monotonic() - started >= _DEADLINE:
                            fail('timeout', '读取模型列表超时', '请检查服务器出站网络或稍后重试。', retryable=True)
                        chunks.append(chunk)
                    # iter_bytes() has already decompressed gzip/deflate.
                    # Retaining content-encoding would make Response decode
                    # these bytes a second time and fail for real providers.
                    decoded_headers = {name: value for name, value in upstream.headers.items() if name.lower() not in ('content-encoding', 'content-length')}
                    response = httpx.Response(upstream.status_code, headers=decoded_headers, content=b''.join(chunks))
                entry['status'] = response.status_code
                entry['code'] = 'ok' if response.is_success else 'http_' + str(response.status_code)
                entry['message'] = _provider_error(response, key)
            except httpx.RequestError as exc:
                detail = str(exc).lower()
                code = ('timeout' if isinstance(exc, httpx.TimeoutException) else 'proxy_error' if isinstance(exc, httpx.ProxyError) else
                        'tls_error' if any(v in detail for v in ('certificate', 'ssl:', 'tls')) else
                        'dns_error' if any(v in detail for v in ('name or service not known', 'nodename nor servname', 'name resolution', 'getaddrinfo')) else
                        'network_unreachable' if any(v in detail for v in ('no route to host', 'network is unreachable', 'network unreachable')) else 'network_error')
                entry.update(code=code, message={'timeout': '服务器请求渠道超时', 'proxy_error': '服务器无法连接出站代理', 'tls_error': '渠道 TLS 证书校验失败', 'dns_error': '服务器无法解析渠道域名', 'network_unreachable': '服务器没有到渠道的可用网络路由', 'network_error': '服务器无法连接渠道'}[code])
                if retries == 0 and code in ('timeout', 'network_error', 'dns_error'):
                    retries += 1
                    continue
                advice = '请检查渠道证书和系统时间；不会自动关闭 TLS 校验。' if code == 'tls_error' else '请检查服务器出站网络、DNS 或代理配置；本机能打开不代表服务器能访问该渠道。'
                fail(code, entry['message'], advice, retryable=code != 'tls_error')
            if response.status_code in _REDIRECTS:
                location = response.headers.get('location')
                target = urljoin(current, location or '')
                target_url = urlsplit(target)
                if not location or not _same_origin(current, target) or target_url.username or target_url.password:
                    fail('redirect_blocked', '模型列表接口返回 HTTP ' + str(response.status_code) + ' 跳转', '请直接填写渠道最终 API 地址；为保护密钥，不会跟随跨域或降级跳转。', response.status_code)
                if target in redirects or len(redirects) >= 3:
                    fail('redirect_loop', '模型列表接口重复跳转', '请填写最终 API 地址，检查网关重定向规则。', response.status_code)
                redirects.add(target)
                current = target
                continue
            if response.status_code in (408, 425, 429, 500, 502, 503, 504) and retries == 0:
                retry_after = response.headers.get('retry-after', '')
                delay = float(retry_after) if re.fullmatch(r'\d+(?:\.\d+)?', retry_after) else 0.15
                # Honour short waits only; leave long rate limits to the user.
                if (not retry_after or re.fullmatch(r'\d+(?:\.\d+)?', retry_after)) and delay <= 1 and time.monotonic() - started + delay < _DEADLINE - 1:
                    retries += 1
                    time.sleep(delay)
                    continue
            return response, current

    try:
        with httpx.Client(follow_redirects=False, trust_env=False, transport=transport, proxy=proxy) as client:
            last, invalid_response = None, None
            for selected_auth in auths:
                for endpoint_index, endpoint in enumerate(endpoints):
                    response, actual_endpoint = request(client, endpoint, selected_auth)
                    last = response
                    detail = _provider_error(response, key)
                    if response.status_code == 403 and re.search(r'country|region|location|unsupported_country|地区|地域', detail, re.I):
                        fail('region_restricted', '渠道拒绝服务器所在地区（HTTP 403）' + ('：' + detail if detail else ''), '请使用该服务支持地区的服务器或渠道；工作台无法取消上游地区限制。', 403)
                    if response.is_success:
                        try:
                            data = response.json()
                        except ValueError:
                            invalid_response = ('invalid_json', '模型列表接口没有返回 JSON', '请确认填写 API 地址而非网站首页；检查反向代理或登录拦截页。')
                            attempts[-1]['code'] = 'invalid_json'
                            if endpoint_index < len(endpoints) - 1:
                                continue
                            fail(*invalid_response, status=response.status_code)
                        try:
                            values = _ids(data, selected_auth)
                        except ValueError as exc:
                            invalid_response = ('invalid_catalog', str(exc), '请确认渠道提供模型列表接口；未开放列表的渠道仍可手动填写模型 ID。')
                            attempts[-1]['code'] = 'invalid_catalog'
                            if endpoint_index < len(endpoints) - 1:
                                continue
                            fail(*invalid_response, status=response.status_code)
                        seen_pages, page_count, current = set(), 0, actual_endpoint
                        while True:
                            page_count += 1
                            next_page = _next_page(data, actual_endpoint, selected_auth)
                            if not next_page:
                                break
                            if next_page in seen_pages or page_count >= 5 or len(attempts) >= _MAX_REQUESTS:
                                diag['partial'] = True
                                break
                            seen_pages.add(next_page)
                            response, current = request(client, next_page, selected_auth)
                            if not response.is_success:
                                fail('pagination_failed', '获取模型列表后续分页失败（HTTP ' + str(response.status_code) + '）', '请重试，避免把不完整的模型列表误认为全部模型。', response.status_code, retryable=True)
                            try:
                                data = response.json()
                                values.update(_ids(data, selected_auth))
                            except ValueError:
                                fail('pagination_failed', '模型列表后续分页格式无效', '请检查渠道分页返回结构。', response.status_code)
                        suggestion = '已达到分页上限，当前展示已返回的模型；未列出的模型可手动填写。' if diag.get('partial') else '模型列表已获取。' if values else '渠道返回空列表；请检查密钥模型权限，也可手动填写模型 ID。'
                        if selected_auth != auth:
                            suggestion += ' 列表使用 ' + selected_auth + ' 鉴权兼容获取，测试请求仍保留所选协议。'
                        diag.update(endpoint=_redact(actual_endpoint, key), auth=selected_auth, code='partial' if diag.get('partial') else 'ok' if values else 'empty_catalog', suggestion=suggestion, retryable=False,
                                    elapsed_ms=round((time.monotonic() - started) * 1000), pages=page_count)
                        models = sorted(values)
                        return {'models': models, 'total': len(models), 'diagnostics': dict(diag)}
                    if response.status_code in (401, 403):
                        # Same endpoint, alternate provider header only.  Never
                        # try a different origin or include keys in URLs.
                        break
                    if response.status_code not in (404, 405):
                        break
                if last.status_code not in (401, 403, 404, 405):
                    break
    except DiscoveryError:
        raise
    except (ValueError, TypeError, ImportError) as exc:
        fail('proxy_config' if proxy else 'network_config', '服务器网络客户端配置异常', '请管理员检查出站代理和 HTTP 客户端依赖。')

    status, detail = last.status_code, _provider_error(last, key)
    if status in (401, 403):
        code, suggestion = 'auth_failed', '请核对 API Key、模型列表权限和渠道鉴权方式；列表权限可能与推理权限不同。'
    elif status in (404, 405):
        code, suggestion = 'models_not_supported', '已检查兼容路径；该地址可能未开放模型列表。请核对 Base URL，或手动填写渠道给出的模型 ID。'
    elif status == 429:
        code, suggestion = 'rate_limited', '渠道限制请求频率或额度，请稍后重试并检查余额。'
    else:
        code, suggestion = 'upstream_error', '请检查渠道服务状态；若仅国外渠道失败，请核对服务器出站网络和地区支持。'
    fail(code, '获取模型列表失败：HTTP ' + str(status) + ('，' + detail if detail else ''), suggestion, status, retryable=status == 429 or status >= 500)
