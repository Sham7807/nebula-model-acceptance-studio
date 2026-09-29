"""Evidence-only executive summaries. Export never calls a model."""
from datetime import datetime, timezone, timedelta
import json
import math
import re
from urllib.parse import urlsplit


def obj(value): return value if isinstance(value, dict) else {}
def rows(value): return value if isinstance(value, list) else []
def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None
def token(value): return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _token_from(value, *keys):
    """Read the first explicitly reported non-negative integer token field.

    Providers use both snake_case and camelCase names.  Keep ``None`` distinct
    from a reported zero: a missing cache field is evidence-unavailable, while
    a zero cache field is a valid observation.
    """
    source = obj(value)
    for key in keys:
        candidate = source.get(key)
        if token(candidate) is not None:
            return candidate
    return None


def _first_token(*values):
    """Return the first present token value, retaining a legitimate zero."""
    for value in values:
        if token(value) is not None:
            return value
    return None


def _cache_percent(read, total):
    if token(read) is None or token(total) is None or total <= 0:
        return None
    # Preserve a positive, very small hit rate instead of rounding it to the
    # misleading value 0.  The raw token counts remain the authoritative data.
    value = read / total * 100
    # Keep sub-micro percentages positive as a numeric value; the display
    # formatter renders them as ``<0.01%`` instead of fabricating zero.
    return value if value < 0.01 else round(value, 6)


def _percent_label(value):
    if value is None:
        return '未记录'
    if value > 0 and value < 0.01:
        return '<0.01'
    if value > 0 and value < 1:
        return ('%.6f' % value).rstrip('0').rstrip('.')
    return ('%.6f' % value).rstrip('0').rstrip('.')
def epoch(value):
    if number(value) is not None: return value
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return date.timestamp() if date.tzinfo else None
        except (ValueError, OverflowError): pass
    return None
def time_label(value):
    if value is None: return '未记录'
    try: return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S UTC+08:00')
    except (ValueError, OverflowError, OSError): return '未记录'
def duration_label(value):
    if number(value) is None: return '未记录'
    if value < 1000: return '%g 毫秒' % round(value, 1)
    seconds = value / 1000
    if seconds < 60: return '%g 秒' % round(seconds, 2)
    minutes, second = divmod(round(seconds), 60)
    hours, minute = divmod(minutes, 60)
    return ('%s 小时 ' % hours if hours else '') + '%s 分 %s 秒' % (minute, second)
def percentile(values, p):
    values = sorted(x for x in values if number(x) is not None)
    if not values: return None
    pos = (len(values)-1)*p
    return round(values[math.floor(pos)] + (values[math.ceil(pos)]-values[math.floor(pos)])*(pos-math.floor(pos)), 2)


def requests(result):
    result = obj(result)
    if result.get('suite') == 'batch_acceptance':
        return [dict(r, id='model-%s-%s' % (i, r.get('id') or r.get('request_id'))) for i, child in enumerate(rows(result.get('results')), 1) for r in requests(child.get('result'))]
    matrix = obj(result.get('matrix_validation'))
    source = rows(result.get('samples')) + rows(result.get('browser_requests')) + rows(matrix.get('samples') or matrix.get('evidence_samples')) + rows(obj(result.get('production_validation')).get('samples')) + rows(obj(result.get('transport')).get('requests'))
    seen = set(); output = []
    for index, row in enumerate(source):
        if not isinstance(row, dict): continue
        identity = row.get('id') or row.get('request_id') or 'unidentified-%s' % index
        if identity in seen: continue
        seen.add(identity); output.append(row)
    return output


def build_timing(result, checks=()):
    start, end = epoch(result.get('started_at')), epoch(result.get('finished_at'))
    source = result.get('timing_source') or '完整运行起止时间；并发请求耗时不累加。'
    duration = round((end-start)*1000, 2) if start is not None and end is not None and end >= start else None
    request_rows = requests(result); kind = 'total'
    if duration is None:
        duration = number(result.get('duration_ms'))
        source = '记录的实际运行耗时；并发请求耗时不累加。' if duration is not None else result.get('timing_source') or '未保存完整运行起止时间，不能推算总耗时。'
        if duration is not None and start is not None: end = start+duration/1000
    if duration is None:
        intervals = []
        for r in request_rows:
            began = epoch(r.get('started_at', obj(r.get('extra')).get('started_at')))
            elapsed = number(r.get('duration_ms'))
            if began is not None and elapsed is not None: intervals.append((began, began+elapsed/1000))
        if intervals:
            start = min(x[0] for x in intervals); end = max(x[1] for x in intervals)
            duration = round((end-start)*1000, 2); kind = 'request_window'
            source = '请求观测窗口（非完整测试耗时）：%s/%s 条请求有起止依据；并发耗时不累加。' % (len(intervals), len(request_rows))
    durations = [r.get('duration_ms') for r in request_rows if number(r.get('duration_ms')) is not None]
    stages = []; seen_stage_requests = set()
    matrix_stages = rows(obj(obj(obj(result.get('matrix_validation')).get('metrics')).get('stress')).get('stages'))
    def add_stage(metrics, family, fallback_ids=()):
        identities = frozenset(rows(metrics.get('request_ids')) or fallback_ids)
        if identities and identities in seen_stage_requests: return
        if identities: seen_stage_requests.add(identities)
        stages.append({**metrics, 'source': family,
                       'label': ('参数矩阵' if family == 'matrix_validation' else '原生压测') + ' · 并发 %s' % metrics.get('concurrency', '未记录'),
                       'duration_label': duration_label(metrics.get('duration_ms'))})
    for s in matrix_stages:
        add_stage(s, 'matrix_validation')
    for c in checks:
        metrics = obj(c.get('metrics'))
        if metrics and ('stress' in dimensions(c) or c.get('id') == 'stress'):
            source = 'matrix_validation' if obj(c.get('metadata')).get('source') == 'matrix_validation' else 'native'
            # Matrix aggregate checks repeat the canonical stage metrics.
            # Keep independent native stages even at the same concurrency.
            if source == 'matrix_validation' and any(metrics == s for s in matrix_stages): continue
            add_stage(metrics, source, rows(c.get('request_ids')))
    return {'duration_ms': duration, 'total_ms': duration if kind == 'total' else None, 'kind': kind, 'duration_label': duration_label(duration), 'label': duration_label(duration),
            'started_label': time_label(start), 'finished_label': time_label(end), 'started_at': time_label(start), 'finished_at': time_label(end),
            'source': source, 'request_p50_ms': percentile(durations, .5), 'request_p95_ms': percentile(durations, .95),
            'request_count': len(durations), 'recorded_request_count': len(request_rows), 'stages': stages,
            'latency_note': '请求延迟包含成功与失败样本；只统计记录了有效耗时的请求。'}


def raw_check(c): return obj(obj(c.get('raw')).get('check')) or obj(c.get('raw')) or c
def dimensions(c):
    meta = obj(c.get('metadata'))
    return set(rows(meta.get('dimensions')) + rows(c.get('dimensions')) + [meta.get('module'), c.get('module')])
def eligible(c):
    return (not c.get('local_only') and c.get('applicable') is not False and c.get('score_applicable') is not False
            and obj(c.get('metadata')).get('score_applicable') is not False and c.get('evidence_category') not in ('control', 'aggregate', 'observation'))


def _score_units(checks):
    """Expand Claude aggregate injection rows for executive counts."""
    units = []
    for check in checks:
        if not obj(check.get('metadata')).get('score_by_sample'):
            units.append(check)
            continue
        evidence = rows(check.get('evidence_rows'))
        if len(evidence) < 2:
            units.append(check)
            continue
        for row in evidence:
            row = obj(row)
            unit = dict(check)
            unit['status'] = row.get('status', 'inconclusive')
            unit['request_ids'] = rows(row.get('request_ids')) or ([row.get('sample_id')] if row.get('sample_id') else [])
            unit['evidence_rows'] = []
            units.append(unit)
    return units


def item(key, label, checks, conclusion=None, detail=''):
    source_checks = list(checks)
    checks = _score_units(source_checks)
    active = [c for c in checks if eligible(c)]
    passed = sum(c.get('status') == 'passed' for c in active)
    failed = sum(c.get('status') == 'failed' for c in active)
    capability = [c for c in checks if not c.get('local_only') and c.get('applicable') is not False and c.get('evidence_category') not in ('control', 'aggregate', 'observation')]
    pending = sum(c.get('status') in ('inconclusive', 'cancelled') or (c.get('status') in ('passed', 'failed') and not eligible(c)) for c in capability)
    missing = sum(c.get('status') in ('skipped', 'not_covered') for c in capability)
    status = 'failed' if failed else 'inconclusive' if pending or (passed and missing) else 'passed' if passed else 'not_covered'
    denominator = passed + failed
    rate = (passed / denominator * 100) if denominator else None
    if failed:
        status_label, tone = ('部分异常', 'attention') if passed else ('需重点核查', 'risk')
        observation = ('多数检查通过' if passed > failed else '部分检查通过') + ' · %s 项异常' % failed if passed else '%s 项检查与预期不符' % failed
    elif passed:
        status_label, tone = ('部分已验证', 'attention') if pending or missing else ('已验证', 'passed')
        observation = '%s 项检查已验证' % passed
    else:
        status_label, tone = ('待补充证据', 'neutral') if pending else ('本轮未测', 'neutral')
        observation = '证据待补充' if pending else '本轮未覆盖'
    rate_label = ('通过率 %s%%' % _percent_label(rate)) if rate is not None else '通过率未记录'
    conclusion = conclusion or label + '：' + rate_label + '（通过 %s / 异常 %s）' % (passed, failed)
    detail = detail or '已判定 %s 项：%s 项通过、%s 项异常%s%s。' % (passed+failed, passed, failed, '；另 %s 项待确认' % pending if pending else '', '；%s 项未执行' % missing if missing else '')
    evidence = sorted(source_checks, key=lambda c: {'failed':0, 'inconclusive':1, 'passed':2}.get(c.get('status'),3))
    return {'id': key, 'label': label, 'status': status, 'status_label': status_label, 'tone': tone, 'display_tone': tone,
            'counts': {'passed': passed, 'failed': failed, 'pending': pending, 'missing': missing},
            'rate': rate,
            'conclusion': conclusion, 'detail': detail, 'text': conclusion+'。'+detail,
            'check_ids': [c['id'] for c in evidence if c.get('id')], 'request_ids': list(dict.fromkeys(x for c in evidence for x in rows(c.get('request_ids'))))}


_SOURCE_LABELS = {'official_relay': '倾向官转（实测线索）', 'reverse': '倾向逆向（实测线索）', 'unknown': '待判定（官转 / 逆向）'}
_API_CHAT_HOSTS = {
    'api.openai.com', 'api.deepseek.com', 'api.moonshot.cn', 'api.moonshot.ai',
    'api.mistral.ai', 'api.x.ai',
}
_WEB_SESSION_PATH = re.compile(
    r'https://(?:claude\.ai/api/organizations/[^\s/"<>]+/chat_conversations'
    r'|(?:chatgpt\.com|chat\.openai\.com)/backend-api/conversation)(?=[/?\s"<>]|$)', re.I)


def _source_json(value):
    if isinstance(value, str):
        try: value = json.loads(value)
        except (ValueError, TypeError): return {}
    return obj(value)


def _source_sample(raw, fallback_model=''):
    """Read HTTP observations, never request or generated text as error evidence."""
    raw = {**obj(obj(raw).get('extra')), **obj(raw)}
    request = obj(raw.get('request')); response = obj(raw.get('response'))
    # Browser rows carry response_body directly; native rows have response.body.
    body = raw.get('response_body', response.get('body', raw.get('response')))
    if body is None and raw.get('type') == 'request_finish': body = raw.get('body')
    wrapper = _source_json(body)
    if 'body' in wrapper and any(key in wrapper for key in ('status', 'headers', 'body_truncated')):
        response = {**response, **wrapper}; body = wrapper['body']
    payload = _source_json(body)
    request_body = _source_json(raw.get('request_body', request.get('body')))
    if 'body' in request_body and any(key in request_body for key in ('url', 'method', 'headers')):
        request_body = _source_json(request_body['body'])
    status = next((value for value in (raw.get('http_status'), response.get('status'), raw.get('status')) if isinstance(value, int) and not isinstance(value, bool)), None)
    url = str(raw.get('url') or raw.get('endpoint') or request.get('url') or '')
    try:
        parsed = urlsplit(url)
        # Origins containing credentials or explicit insecure ports cannot
        # support a public API-origin inference. Do not include secrets in notes.
        valid_url = parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in (None, 443)
        host, path = (parsed.hostname or '').lower(), parsed.path.rstrip('/')
    except ValueError:
        valid_url = False; host = path = ''
    headers = raw.get('response_headers')
    if headers is None: headers = response.get('headers', {})
    pairs = headers.items() if isinstance(headers, dict) else rows(headers)
    header_names = {str(pair[0]).lower() for pair in pairs if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[1]).strip()}
    end = raw.get('termination')
    complete = (end in (None, '', 'eof')
                and not any(raw.get(k) for k in ('body_truncated', 'truncated'))
                and not any(response.get(k) for k in ('body_truncated', 'truncated'))
                and not obj(raw.get('evidence')).get('truncated'))
    return {'raw': raw, 'id': str(raw.get('id') or raw.get('request_id') or '未命名请求'),
            'model': str(request_body.get('model') or raw.get('model') or raw.get('requested_model') or fallback_model or '未记录模型'),
            'url': url, 'endpoint': url, 'valid_url': valid_url, 'host': host, 'path': path,
            'request_body': request_body, 'payload': payload, 'http_status': status,
            'complete': complete, 'success': complete and not raw.get('infrastructure_error') and status is not None and 200 <= status < 300 and payload.get('error') in (None, {}),
            'headers': header_names, 'probe': raw.get('suite_probe') or raw.get('probe')}


def _native_message(payload):
    usage = obj(payload.get('usage'))
    return (payload.get('type') == 'message' and payload.get('role') == 'assistant'
            and isinstance(payload.get('content'), list) and bool(payload.get('stop_reason'))
            and token(usage.get('input_tokens')) is not None and token(usage.get('output_tokens')) is not None)


def _native_chat(payload):
    usage = obj(payload.get('usage')); choices = rows(payload.get('choices'))
    return (payload.get('object') == 'chat.completion' and bool(choices)
            and isinstance(obj(choices[0]).get('message'), dict) and bool(obj(choices[0]).get('finish_reason'))
            and token(usage.get('prompt_tokens')) is not None and token(usage.get('completion_tokens')) is not None)


def _api_origin(sample):
    if not sample['success'] or not sample['valid_url']: return False
    host, path, payload = sample['host'], sample['path'], sample['payload']
    if host == 'api.anthropic.com' and path == '/v1/messages': return _native_message(payload)
    if host in _API_CHAT_HOSTS and path in ('/v1/chat/completions', '/chat/completions'): return _native_chat(payload)
    if (host == 'dashscope.aliyuncs.com' and path == '/compatible-mode/v1/chat/completions') or (host == 'open.bigmodel.cn' and path == '/api/paas/v4/chat/completions') or (host == 'api.z.ai' and path == '/api/paas/v4/chat/completions'):
        return _native_chat(payload)
    if host == 'api.openai.com' and path == '/v1/responses':
        usage = obj(payload.get('usage'))
        return (payload.get('object') == 'response' and payload.get('status') == 'completed' and isinstance(payload.get('output'), list)
                and token(usage.get('input_tokens')) is not None and token(usage.get('output_tokens')) is not None)
    if host == 'generativelanguage.googleapis.com' and re.fullmatch(r'/v1(?:beta)?/models/[^/]+:generateContent', path):
        usage = obj(payload.get('usageMetadata'))
        return bool(rows(payload.get('candidates'))) and token(usage.get('promptTokenCount')) is not None and token(usage.get('candidatesTokenCount')) is not None
    if re.fullmatch(r'bedrock-runtime\.[a-z]{2}(?:-[a-z0-9]+)+-\d\.amazonaws\.com(?:\.cn)?', host) and re.fullmatch(r'/model/[^/]+/invoke', path):
        return _native_message(payload)
    return False


def _web_error(sample):
    # Only error fields, not assistant content, a user URL, or request bodies.
    # The returned note describes the path family without copying URLs/tokens.
    if not sample['complete'] or sample['http_status'] is None or sample['http_status'] < 400: return False
    error = sample['payload'].get('error')
    if not isinstance(error, dict): return False
    def strings(value, depth=0):
        if depth > 4: return []
        if isinstance(value, str): return [value]
        if not isinstance(value, dict): return []
        return [text for key, child in value.items() if key in ('message', 'detail', 'description', 'url', 'upstream_url', 'endpoint', 'path', 'error', 'cause') for text in strings(child, depth+1)]
    request_text = json.dumps(sample['request_body'], ensure_ascii=False).lower()
    return any(match.group(0).lower() not in request_text for value in strings(error) for match in _WEB_SESSION_PATH.finditer(value))


def _source_signature_chain(samples):
    """Require the actual linked positive/negative bodies, not check labels."""
    positives = [s for s in samples if s['probe'] == 'thinking_return' and s['success'] and _native_message(s['payload'])]
    negatives = [s for s in samples if s['probe'] == 'signature_mutation' and s['complete'] and s['http_status'] in (400, 422)]
    for original in samples:
        if original['probe'] != 'thinking' or not original['success'] or not _native_message(original['payload']): continue
        signed = any(isinstance(block, dict) and block.get('type') == 'thinking' and isinstance(block.get('signature'), str) and block['signature'] for block in original['payload']['content'])
        if not signed or not _vendor_header(original): continue
        for positive in positives:
            if positive['endpoint'] != original['endpoint'] or positive['model'] != original['model']: continue
            messages = rows(positive['request_body'].get('messages'))
            if not any(obj(m).get('role') == 'assistant' and obj(m).get('content') == original['payload']['content'] for m in messages): continue
            for negative in negatives:
                if negative['endpoint'] != original['endpoint'] or negative['model'] != original['model']: continue
                error = json.dumps(obj(negative['payload'].get('error')), ensure_ascii=False)
                if not (re.search(r'signature|签名', error, re.I) and re.search(r'invalid|mismatch|verification|verify|failed|not valid|incorrect|无效|校验|验证|不匹配', error, re.I)) or re.search(r'unsupported|unknown field|not supported|unrecognized|不支持|未知字段', error, re.I): continue
                # Compare every request field. A valid negative changes exactly
                # one character in a thinking signature and nothing else.
                left = json.loads(json.dumps(positive['request_body']))
                right = json.loads(json.dumps(negative['request_body']))
                differences = []
                def compare(a, b, path=()):
                    if type(a) is not type(b): differences.append((path, a, b)); return
                    if isinstance(a, dict):
                        if a.keys() != b.keys(): differences.append((path, a, b)); return
                        for key in a: compare(a[key], b[key], path+(key,))
                    elif isinstance(a, list):
                        if len(a) != len(b): differences.append((path, a, b)); return
                        for i, (x, y) in enumerate(zip(a, b)): compare(x, y, path+(i,))
                    elif a != b: differences.append((path, a, b))
                compare(left, right)
                if len(differences) != 1: continue
                path, a, b = differences[0]
                if path and path[-1] == 'signature' and isinstance(a, str) and isinstance(b, str) and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
                    return [original['id'], positive['id'], negative['id']]
    return []


def _vendor_header(sample):
    headers = sample['headers']
    return bool(headers.intersection({'anthropic-request-id', 'anthropic-organization-id', 'x-amzn-bedrock-invocation-latency'})) or any(h.startswith('anthropic-ratelimit-') or h.startswith('x-amzn-bedrock-') for h in headers)


def _resource_source_group(samples):
    evidence, missing = [], []
    api = [s for s in samples if _api_origin(s)]
    reverse = [s for s in samples if _web_error(s)]
    signature = _source_signature_chain(samples)
    if api: evidence.append('标准供应商 API 域名取得完整原生响应及用量字段（请求 %s）；按本报告的两类口径归入官转 / API 资源。' % '、'.join(s['id'] for s in api[:4]))
    if signature: evidence.append('同一模型、同一端点完成真实 thinking 签名原样回传与单字符篡改拒绝，原始响应同时包含供应商专有头及原生用量结构（请求 %s）。' % '、'.join(signature))
    if reverse: evidence.append('结构化错误暴露网页会话专用接口路径，存在网页会话转换的线索（请求 %s）；错误信息可由中间层改写。' % '、'.join(s['id'] for s in reverse[:4]))
    official = bool(api or signature)
    classification = 'unknown' if official and reverse else 'official_relay' if official else 'reverse' if reverse else 'unknown'
    if samples and all(s['model'] == '未记录模型' for s in samples):
        classification = 'unknown'
        missing.append('请求和配置均未记录模型，无法把链路线索关联到具体被测模型。')
    if official and reverse: missing.append('API 资源与网页会话线索互相冲突；需按本轮 Request ID 核对是否混用或回退上游。')
    if not api and not reverse and not signature:
        successful = sum(s['success'] for s in samples)
        evidence.append('已检查 %s 条 HTTP 记录，其中 %s 条完整成功；本轮未出现可区分官转 / 逆向的来源线索。' % (len(samples), successful) if samples else '本轮未保存可用于来源分析的 HTTP 请求和响应。')
        missing.append('未取得供应商 API 域名上的完整原生响应，或同一链路的签名正负对照 + 专有头 + 原生用量组合。')
        missing.append('已保存的结构化错误未暴露网页会话专用路径；没有此类错误不代表没有逆向。' if samples else '缺少结构化响应错误记录，无法检查网页会话链路线索。')
    if not signature:
        returned = [s for s in samples if s['probe'] == 'thinking_return' and s['success'] and _native_message(s['payload'])]
        accepted = [s for s in samples if s['probe'] == 'signature_mutation' and s['success']]
        originals = [s for s in samples if s['probe'] == 'thinking' and s['success']]
        if returned: evidence.append('原样签名回传请求成功（请求 %s）；往返成功不能单独证明来源。' % '、'.join(s['id'] for s in returned[:4]))
        if accepted: evidence.append('篡改签名仍收到 HTTP %s（请求 %s）；可能被删除、重写或忽略，属于签名校验异常，不能单独证明逆向。' % (' / '.join(str(code) for code in sorted({s['http_status'] for s in accepted})), '、'.join(s['id'] for s in accepted[:4])))
        if originals and not any(_vendor_header(s) for s in originals): missing.append('签名原始响应未保留供应商专有头；只有中转通用头或未记录响应头，不满足组合判断依据。')
    missing.append('若需确认而非倾向判断，需用本轮 Request ID 对应的上游调用日志或计费记录核对。')
    return {'classification': classification, 'kind': 'inferred' if classification != 'unknown' else 'unknown', 'label': _SOURCE_LABELS[classification],
            'note': '按可观察的 API / 网页会话链路线索判断；属于倾向判断，不是来源认证。' if classification != 'unknown' else '本轮证据不足或相互冲突，无法可靠二选一；能力通过、缓存命中、模型自述及普通错误不作为来源结论。',
            'evidence': evidence, 'missing_evidence': missing}


def resource_source(result):
    """Infer API versus web-session resources without treating claims as tests."""
    result = obj(result); config = obj(result.get('configuration') or result.get('config'))
    configurations = [obj(c) for c in rows(config.get('records'))] or [config]
    values = {str(c.get('resource_source') or '').strip().lower() for c in configurations} - {'', 'unknown'}
    values = {'official_relay' if value == 'official' else value for value in values}
    providers = {str(c.get('provider') or '').strip().lower() for c in configurations} - {'', 'auto'}
    operator_label = '渠道未声明来源'
    unclaimed = any(str(c.get('resource_source') or '').strip().lower() in ('', 'unknown') and str(c.get('provider') or '').strip().lower() in ('', 'auto') for c in configurations)
    if len(values) > 1: operator_label = '渠道声明不一致（官转 / 逆向）'
    elif values: operator_label = '渠道声明：' + ('官转' if 'official_relay' in values else '逆向') + '（未作为实测结论）'
    elif providers: operator_label = '渠道声明：官转（%s，未作为实测结论）' % ' / '.join({'anthropic':'Anthropic API', 'aws':'AWS Bedrock'}.get(p, p) for p in sorted(providers))
    if unclaimed and (values or providers): operator_label += '；部分记录未声明'
    # Child runs and browser records must not lend one model's signature or
    # headers to another. Endpoints are additionally matched inside the chain.
    if result.get('suite') == 'batch_acceptance':
        children = [(str(obj(obj(child.get('result')).get('configuration')).get('model') or child.get('model') or '未记录模型'), resource_source(child.get('result'))) for child in rows(result.get('results')) if isinstance(child, dict)]
    else:
        raw_samples = requests(result)
        seen = {id(row) for row in raw_samples}
        identities = {row.get('id') or row.get('request_id') for row in raw_samples} - {None, ''}
        for row in rows(result.get('requests')):
            if not isinstance(row, dict) or id(row) in seen: continue
            identity = row.get('id') or row.get('request_id')
            if identity and identity in identities: continue
            raw_samples.append(row); seen.add(id(row))
            if identity: identities.add(identity)
        grouped = {}; group_labels = {}; record_configs = rows(config.get('records'))
        for row in raw_samples:
            record_match = re.match(r'^record-(\d+)-request-', str(row.get('id') or ''))
            record_index = int(record_match.group(1))-1 if record_match else None
            group_config = obj(record_configs[record_index]) if record_index is not None and 0 <= record_index < len(record_configs) else config
            sample = _source_sample(row, group_config.get('model'))
            model = str(group_config.get('model') or sample['model'])
            # A deliberately invalid-model request is a control for the same
            # tested resource, not a new resource lacking successful samples.
            if not record_configs and len(rows(config.get('models'))) > 1:
                model = sample['model'] if sample['model'] in config['models'] else '未关联到被测模型'
            key = (record_index, model)
            grouped.setdefault(key, []).append(sample); group_labels[key] = model
        # Include configured but unobserved models in a multi-model report.
        if record_configs:
            for index, cfg in enumerate(record_configs):
                model = str(obj(cfg).get('model') or '未记录模型'); key = (index, model)
                grouped.setdefault(key, []); group_labels[key] = model
        else:
            for model in rows(config.get('models')):
                if isinstance(model, str):
                    key = (None, model); grouped.setdefault(key, []); group_labels[key] = model
        children = [(group_labels[key], _resource_source_group(samples)) for key, samples in grouped.items()]
    if not children: answer = _resource_source_group([])
    elif len(children) == 1: answer = dict(children[0][1])
    else:
        classes = {child['classification'] for _, child in children}
        classification = next(iter(classes)) if len(classes) == 1 else 'unknown'
        answer = {'classification': classification, 'kind': 'inferred' if classification != 'unknown' else 'unknown', 'label': _SOURCE_LABELS[classification],
                  'note': '逐模型分析后汇总；任一模型待判定或不同模型来源倾向不一致时，不替全部模型二选一。',
                  'evidence': [model+'：'+child['label']+'；'+' '.join(child['evidence']) for model, child in children],
                  'missing_evidence': list(dict.fromkeys(value for _, child in children for value in child['missing_evidence']))}
        answer['models'] = [{'model':model, 'classification':child['classification'], 'label':child['label']} for model, child in children]
    answer.update(operator_label=operator_label, operator_value=next(iter(values)) if len(values) == 1 else 'unknown', provider=next(iter(providers)) if len(providers) == 1 else None)
    return answer


def presentation(value, label, tone):
    value.update(status_label=label, tone=tone, display_tone=tone)
    value['text'] = value['conclusion'] + '。' + value['detail']
    return value


def resource_grade(score):
    """Report a score band, without changing scoring or certifying provenance."""
    total = score.get('weighted_total', score.get('total'))
    valid = number(total) is not None and total <= 100
    level = 'high' if valid and total >= 90 else 'medium' if valid and total >= 70 else 'low' if valid else 'unknown'
    label = {'high': '优质资源', 'medium': '中等资源', 'low': '低等级资源', 'unknown': '待评估'}[level]
    evidence = obj(score.get('evidence'))
    resolution = number(evidence.get('resolution_percent'))
    sample_count = number(evidence.get('sample_count')) or 0
    weight_total = number(score.get('weight_total')) or 100
    coverage = (number(score.get('weight_covered')) or 0) / weight_total * 100
    reasons = []
    if resolution is None or resolution < 80: reasons.append('证据可判定率不足 80%')
    if coverage < 70: reasons.append('可评分模块权重不足 70%')
    if sample_count < 10: reasons.append('明确关联的能力请求不足 10 条')
    provisional = bool(valid and reasons)
    anomalies = evidence.get('scored_failed', 0)
    note = ('本轮综合分 %g/100，仅评价已测范围。' % total if valid else '本轮没有可计算的综合分，补齐证据后再评级。')
    if provisional: note += '暂定原因：' + '；'.join(reasons) + '。'
    if anomalies: note += '仍有 %s 项异常，请结合具体条件与证据判断。' % anomalies
    return {'label': label, 'level': level, 'provisional': provisional, 'note': note,
            'criteria': '优质资源：90–100 分；中等资源：70–89 分；低等级资源：低于 70 分。缺少综合分时待评估。可判定率至少 80%、可评分模块权重至少 70%、至少 10 条明确关联能力请求时才取消“暂定”标记；评级仍只适用于本轮已测范围，不代表来源认证或长期保证。'}


def usage_pair(usage):
    """Return (read, total) with protocol-specific accounting, never missing=0."""
    u = obj(usage)
    details = obj(u.get('prompt_tokens_details'))
    input_details = obj(u.get('input_tokens_details'))
    # Anthropic Messages reports input_tokens separately from cache reads and
    # writes.  Its complete denominator is the sum of all three fields; do not
    # silently treat an omitted creation field as zero.
    read = _token_from(u, 'cache_read_input_tokens', 'cache_read_tokens',
                       'prompt_cache_read_tokens', 'prompt_cache_hit_tokens')
    creation = _token_from(u, 'cache_creation_input_tokens', 'cache_creation_tokens',
                           'prompt_cache_creation_tokens')
    base = _token_from(u, 'input_tokens', 'inputTokens')
    if read is not None or creation is not None:
        return read, (base + read + creation if base is not None and read is not None and creation is not None else None)

    # OpenAI Chat/Responses, Gemini and compatible gateways expose cached
    # tokens inside a details object or under camelCase names.  Their reported
    # prompt/input token count already includes the cached portion.
    read = _first_token(
        _token_from(details, 'cached_tokens', 'cache_read_tokens', 'cachedTokens'),
        _token_from(input_details, 'cached_tokens', 'cache_read_tokens', 'cachedTokens'),
        _token_from(u, 'cached_tokens', 'cachedTokens', 'cachedContentTokenCount',
                    'cache_read_tokens', 'prompt_cache_read_tokens', 'prompt_cache_hit_tokens'))
    base = _first_token(
        _token_from(u, 'prompt_tokens', 'promptTokenCount', 'prompt_tokens_count',
                    'input_tokens', 'inputTokens'),
        _token_from(details, 'prompt_tokens', 'input_tokens'))
    return read, base


def cache_summary(result, checks):
    pairs = {}; request_map = {r.get('id') or r.get('request_id'): r for r in requests(result)}
    def put_pair(identity, pair):
        """Keep the strongest evidence for a request identity.

        A normalized check may repeat a request with a sparse fallback record.
        Such a fallback must never overwrite a positive cache read with zero or
        an unknown value.
        """
        if not identity:
            return
        pair = tuple(pair or (None, None))
        old = pairs.get(identity)
        def rank(value):
            read, total = value
            if token(read) is not None and token(total) is not None and total > 0:
                return 3 if read > 0 else 2
            return 1 if token(read) is not None or token(total) is not None else 0
        if old is None or rank(pair) > rank(old):
            pairs[identity] = pair
    def successful(identity, fallback_status):
        r = obj(request_map.get(identity))
        code = r.get('http_status', obj(r.get('response')).get('status'))
        if fallback_status in ('failed', 'cancelled'): return False
        return 200 <= code < 300 if isinstance(code, int) and not isinstance(code, bool) else fallback_status == 'passed'
    warm = lambda value: str(value or '').lower() in ('warm', 'warm_1', 'warm_2', 'suffix_changed', 'suffix', 'warm1', 'warm2')
    cache_rounds = rows(obj(obj(obj(result.get('matrix_validation')).get('metrics')).get('cache')).get('rounds'))
    for i, r in enumerate(cache_rounds):
        if warm(r.get('variant') or r.get('round')):
            identity = r.get('request_id') or 'matrix-%s' % i
            put_pair(identity, (r.get('cache_read_tokens'), r.get('total_input_tokens')) if successful(identity, r.get('status')) else (None, None))
    for c in checks:
        for r in rows(c.get('cache_observations')):
            identity = r.get('sample_id', '')
            if identity in ('cache-2', 'cache-3', 'cache-2warm', 'cache-3suffix') and not r.get('prefix_control'):
                # A Claude aggregate can be inconclusive because the changed
                # prefix control is unresolved while cache-2/3 still carry
                # valid per-round usage. Preserve that row-level evidence.
                mapped = obj(request_map.get(identity))
                state = r.get('status') if 'status' in r else mapped.get('status')
                if state is None:
                    state = 'passed' if any(key in r for key in ('cache_read_input_tokens', 'cache_read_tokens', 'input_tokens', 'prompt_tokens', 'usage', 'reported_usage', 'usageMetadata')) else c.get('status')
                direct_read = _first_token(r.get('cache_read_input_tokens'), r.get('cache_read_tokens'), r.get('prompt_cache_read_tokens'))
                direct_base = _first_token(r.get('input_tokens'), r.get('inputTokens'), r.get('prompt_tokens'), r.get('promptTokenCount'))
                direct_create = _first_token(r.get('cache_creation_input_tokens'), r.get('cache_creation_tokens'), r.get('prompt_cache_creation_tokens'))
                # Claude observations may expose input_tokens=0 while the
                # reusable prefix is reported in cache_read/create fields.
                # Rebuild the complete native denominator from all three
                # counters; do not discard a valid positive read as zero.
                native_direct = any(key in r for key in ('cache_read_input_tokens', 'cache_creation_input_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
                reported_total = _first_token(r.get('total_input_tokens'))
                if reported_total is not None:
                    # Claude acceptance aggregation already stores the complete
                    # denominator here; prefer it over the fresh input field.
                    direct_total = reported_total
                elif native_direct and direct_base == 0 and direct_read is not None and direct_create is not None:
                    # Some Anthropic relays report no fresh input on a warm
                    # request. Reconstruct the denominator from cache fields
                    # so a positive read is never discarded as zero.
                    direct_total = direct_base + direct_read + direct_create
                else:
                    # Legacy observation fixtures may expose input_tokens as
                    # an already-normalized denominator.
                    direct_total = direct_base
                direct = (direct_read, direct_total)
                nested = r.get('usage') or r.get('reported_usage') or r.get('usageMetadata')
                pair = usage_pair(nested) if nested else direct
                put_pair(identity, pair if successful(identity, state) else (None, None))
        params = obj(c.get('parameters')) or obj(raw_check(c).get('parameters'))
        if not warm(params.get('variant') or params.get('round')): continue
        for identity in rows(c.get('request_ids')):
            if identity in pairs: continue
            r = obj(request_map.get(identity)); observed = obj(r.get('observed_fields'))
            body = r.get('response_body', obj(r.get('response')).get('body'))
            if isinstance(body, str):
                try: body = json.loads(body)
                except ValueError: body = {}
            usage = observed.get('usage') or observed.get('usageMetadata') or obj(body).get('usage') or obj(body).get('usageMetadata')
            put_pair(identity, usage_pair(usage or observed) if successful(identity, r.get('status')) else (None, None))
    valid = [(read, total) for read, total in pairs.values() if token(read) is not None and token(total) is not None and total > 0 and read <= total]
    if not valid:
        summary = item('cache', '缓存复用', checks, '缓存复用未证实', '有效暖请求计数 %s/%s；usage 字段检查不等于缓存复用。字段缺失不按零命中计算。' % (len(valid), len(pairs)))
        if summary['status'] == 'passed': summary['status'] = 'inconclusive'
        return presentation(summary, '计量待核查' if summary['counts']['failed'] else '待补充计量', 'attention' if summary['counts']['failed'] else 'neutral')
    read, total = sum(x[0] for x in valid), sum(x[1] for x in valid)
    percent = _cache_percent(read, total)
    conclusion = '暖请求缓存 Token 复用率 %s%%' % _percent_label(percent)
    detail = '缓存读取 %s / 总输入 %s Token；%s/%s 个暖请求计量有效。排除冷请求与改前缀对照，比例依据渠道上报。' % (read, total, len(valid), len(pairs))
    result_item = item('cache', '缓存复用', checks, conclusion, detail)
    if result_item['status'] == 'not_covered' or (result_item['status'] == 'passed' and (read == 0 or len(valid) < len(pairs))): result_item['status'] = 'inconclusive'
    result_item['cache'] = {'read_tokens': read, 'total_input_tokens': total, 'percent': percent, 'valid_warm_requests': len(valid), 'warm_requests': len(pairs)}
    failures = result_item['counts']['failed']
    if failures: result_item['detail'] += ' 另有 %s 项缓存检查异常待核查。' % failures
    if not read:
        result_item['conclusion'] = '本轮未观察到缓存复用（Token 复用率 0%）'
        result_item['detail'] += ' 本轮零复用不代表模型不支持缓存。'
        return presentation(result_item, '未观察到复用', 'attention')
    return presentation(result_item, '有复用 · 部分异常' if failures else '部分样本已复用' if len(valid) < len(pairs) else '已观察到复用', 'attention' if failures or len(valid) < len(pairs) else 'passed')


def injection_defense_summary(checks):
    """Keep synthetic adversarial prompts separate from upstream-prompt probes.

    A leaked test canary demonstrates a failed protection assertion, but does
    not establish that the relay added an undisclosed system prompt. Token
    accounting and hidden-prompt side channels answer a different question.
    """
    synthetic = []
    for check in checks:
        if not dimensions(check).intersection(('security', 'injection')):
            continue
        if check.get('local_only') or check.get('applicable') is False:
            continue
        raw = raw_check(check)
        identity = ' '.join(str(value or '') for value in (
            check.get('id'), check.get('source_id'), raw.get('id'),
            check.get('title'), raw.get('probe'))).lower()
        if any(value in identity for value in ('prompt_exfiltration', 'prompt_sidechannel', 'token_accounting',
                'reference_exfiltration', 'reference_sidechannel', '上游', '侧信道', 'token 线性')):
            continue
        if any(value in identity for value in ('injection', 'hierarchy', 'canary', '注入', '指令层级', '金丝雀', '越权')):
            synthetic.append(check)
    summary = item('injection', '抗注入防护', synthetic)
    if summary['counts']['failed']:
        summary['conclusion'] = '抗注入防护：检查存在异常，需核对证据'
        label, tone = '检查存在异常', 'attention'
    elif summary['counts']['pending'] or summary['counts']['missing']:
        summary['conclusion'] = '抗注入防护：证据不足，不能确认'
        label, tone = '证据不足', 'neutral'
    elif summary['counts']['passed']:
        summary['conclusion'] = '抗注入防护：本轮未观察到泄露或越权'
        label, tone = '本轮未见异常', 'passed'
    else:
        summary['conclusion'] = '抗注入防护：本轮未测试'
        label, tone = '本轮未测', 'neutral'
    summary['detail'] = '通过 %s 项、异常 %s 项、待判定 %s 项、未执行 %s 项。' % tuple(
        summary['counts'][key] for key in ('passed', 'failed', 'pending', 'missing'))
    summary['detail'] += ' 检查合成系统提示词泄露、越权覆盖与不可信内容影响；传输或解析错误不能当作泄露，只有完整响应中的具体泄露或越权内容才是防护异常证据。这些结果不能证明上游是否暗加提示词。'
    return presentation(summary, label, tone)


def upstream_prompt_summary(result, checks):
    """Present hidden upstream prompts as evidence, never a pass percentage."""
    assessment = obj(result.get('upstream_prompt_assessment'))
    if assessment.get('verdict') not in ('suspected', 'no_signal', 'inconclusive', 'not_tested'):
        try:
            from .prompt_audit import build_upstream_prompt_assessment
        except ImportError:
            from prompt_audit import build_upstream_prompt_assessment
        assessment = build_upstream_prompt_assessment(result)
    verdict = assessment.get('verdict', 'not_tested')
    label, status, tone = {
        'suspected': ('疑似加词', 'inconclusive', 'attention'),
        'no_signal': ('未发现加词迹象', 'passed', 'passed'),
        'inconclusive': ('证据不足', 'inconclusive', 'neutral'),
        'not_tested': ('本轮未测', 'not_covered', 'neutral'),
    }[verdict]
    relevant = [check for check in checks if 'upstream_prompt' in dimensions(check) or any(
        value in str(check.get('id') or '') for value in ('prompt_exfiltration', 'prompt_sidechannel', 'token_accounting'))]
    detail = str(assessment.get('detail') or '按未添加本地 system 的请求观察上游是否额外加入提示词；黑盒输出不能单独证明上游内部配置。')
    counts = obj(assessment.get('counts'))
    if counts:
        detail += ' 疑似线索 %s 项、未见线索 %s 项、待判定 %s 项、对照 %s 项。' % tuple(
            counts.get(key, 0) for key in ('candidate', 'clear', 'inconclusive', 'controls'))
    return presentation({
        'id': 'upstream_prompt', 'label': '上游加词检测', 'status': status,
        'conclusion': '上游加词检测：' + label, 'detail': detail,
        'rate': None, 'assessment': assessment,
        'check_ids': [check['id'] for check in relevant if check.get('id')],
        'request_ids': rows(assessment.get('request_ids')),
    }, label, tone)


def build_summary(result, checks, score):
    selected = lambda *keys: [c for c in checks if dimensions(c).intersection(keys) and not c.get('local_only') and c.get('applicable') is not False]
    basic = item('basic', '基础协议', selected('protocol'))
    tools = item('tools', '工具调用', selected('tools'))
    media = item('multimodal', '多模态输入', selected('multimodal'))
    caps = selected('max_tokens'); exceeded = []
    for c in caps:
        if not eligible(c) or c.get('status') != 'failed': continue
        raw = raw_check(c); params = obj(c.get('parameters')) or obj(raw.get('parameters'))
        cap = params.get('max_tokens'); output = obj(raw.get('measurements')).get('output_tokens', raw.get('completion_tokens'))
        if (number(cap) is not None and cap > 0 and number(output) is not None and output > cap) or ((cap is None or (number(cap) is not None and cap > 0)) and (c.get('reason_code') == 'output_cap_exceeded' or raw.get('reason_code') == 'output_cap_exceeded')):
            exceeded.append((c, cap, output))
    limit = item('max_tokens', '输出限长', caps)
    if exceeded:
        limit = item('max_tokens', '输出限长', caps, '观察到输出超限（%s 项）' % len(exceeded), '；'.join('上限 %s → 返回 %s Token' % (cap, out) if cap is not None and out is not None else '请求上限被超过，详见关联原始判定' for _, cap, out in exceeded[:3]))
        presentation(limit, '部分参数超限' if limit['counts']['passed'] else '观察到超限', 'attention' if limit['counts']['passed'] else 'risk')
    elif limit['status'] == 'failed':
        limit = item('max_tokens', '输出限长', caps, '限长检查有 %s 项需核查' % limit['counts']['failed'], '请按异常参数核对；非法 0/-1 上限被接受不等于所有正常截断失效。')
        presentation(limit, '参数待核查', 'attention')
    injection = injection_defense_summary(checks)
    upstream_prompt = upstream_prompt_summary(result, checks)
    cache = cache_summary(result, selected('cache'))
    pressure = item('pressure', '压测与稳定性', selected('stress', 'reliability'))
    timing = build_timing(result, checks)
    if timing['stages']:
        stages = timing['stages']
        counted = [s for s in stages if token(s.get('completed')) is not None and token(s.get('planned', s.get('requested'))) is not None]
        completed = sum(s['completed'] for s in counted); planned = sum(s.get('planned', s.get('requested')) for s in counted)
        rates = [(s, s['success_rate']) for s in stages if number(s.get('success_rate')) is not None and s['success_rate'] <= 1 and (number(s.get('completed')) or 0) > 0]
        text = '%s 个独立负载阶段' % len(stages)
        if counted: text += '，已记录完成 %s/%s 次请求' % (completed, planned)
        if rates:
            low, high = min(v for _, v in rates), max(v for _, v in rates)
            text += '；已完成请求成功率 %s' % ('%g%%' % (low*100) if low == high else '%g%%–%g%%' % (low*100, high*100))
            if low < 1:
                weakest = next(s for s, value in rates if value == low)
                text += '，%s 最低' % weakest['label']
                pressure['conclusion'] = '部分负载阶段有异常' if high > 0 else '已记录负载请求需重点核查'
                pressure['status_label'] = '负载有波动' if high > 0 else '负载异常'
                pressure['tone'] = 'attention' if high > 0 else 'risk'
            elif planned > completed:
                pressure['conclusion'] = '已完成负载请求表现正常，计划尚未完成'
                pressure['status_label'], pressure['tone'] = '负载未测完', 'attention'
            elif pressure['counts']['failed']:
                pressure['conclusion'] = '已记录负载请求表现正常，另有稳定性检查异常'
                pressure['status_label'], pressure['tone'] = '部分检查异常', 'attention'
            elif len(rates) == len(stages):
                pressure['conclusion'] = '已测负载阶段表现正常'
        pressure['detail'] = text + '。各阶段耗时见上方，详细指标见下方证据；短时样本不代表长期 SLA。'
        presentation(pressure, pressure['status_label'], pressure['tone'])
    items = [upstream_prompt, basic, tools, media, limit, injection, cache, pressure]
    # Batch conclusions must not pretend pooled requests describe one model.
    models = rows(obj(result.get('configuration')).get('models'))
    batch = result.get('suite') == 'batch_acceptance' or len(models) > 1
    if batch:
        items = [item('batch', '多模型结果', checks, '各模型独立判读', '本报告包含多个模型，失败和缓存率请按对应模型的检查证据阅读；不以跨模型汇总证明单个模型能力。')]
    headline_items = sorted(items, key=lambda x: (0 if x['id'] == 'upstream_prompt' and x['status'] != 'not_covered' else 1 if x['status'] == 'failed' else 2 if x.get('cache') else 3 if x['id'] == 'basic' else 4))
    headline = '；'.join(x['conclusion'] for x in headline_items if x['status'] != 'not_covered')
    headline = '；'.join(headline.split('；')[:5]) or '本轮缺少可判定证据'
    overall = score.get('weighted_total', score.get('total'))
    grade = resource_grade(score)
    if batch:
        grade['provisional'] = grade['level'] != 'unknown'
        grade['note'] = '多模型汇总参考，不能代表每个模型的资源等级；请按模型单独判读。' + grade['note']
    return {'headline': headline, 'status': 'failed' if any(c.get('status') == 'failed' and eligible(c) for c in checks) else obj(score.get('evidence')).get('status', 'inconclusive'),
            'detail': '通过率按已判定项计算；待判定、未覆盖和不适用不会伪装成失败或通过。完整请求证据位于报告后半段。',
            'items': items, 'score': overall, 'overall_score': overall, 'resource_grade': grade, 'resource_source': resource_source(result), 'resolution_percent': obj(score.get('evidence')).get('resolution_percent'), 'timing': timing, 'duration': timing}
