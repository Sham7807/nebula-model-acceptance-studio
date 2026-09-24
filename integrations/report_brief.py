"""Evidence-only executive summaries. Export never calls a model."""
from datetime import datetime, timezone, timedelta
import json
import math


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
    if failed:
        status_label, tone = ('部分异常', 'attention') if passed else ('需重点核查', 'risk')
        observation = ('多数检查通过' if passed > failed else '部分检查通过') + ' · %s 项异常' % failed if passed else '%s 项检查与预期不符' % failed
    elif passed:
        status_label, tone = ('部分已验证', 'attention') if pending or missing else ('已验证', 'passed')
        observation = '%s 项检查已验证' % passed
    else:
        status_label, tone = ('待补充证据', 'neutral') if pending else ('本轮未测', 'neutral')
        observation = '证据待补充' if pending else '本轮未覆盖'
    conclusion = conclusion or label + '：' + observation
    detail = detail or '已判定 %s 项：%s 项通过、%s 项异常%s%s。' % (passed+failed, passed, failed, '；另 %s 项待确认' % pending if pending else '', '；%s 项未执行' % missing if missing else '')
    evidence = sorted(source_checks, key=lambda c: {'failed':0, 'inconclusive':1, 'passed':2}.get(c.get('status'),3))
    return {'id': key, 'label': label, 'status': status, 'status_label': status_label, 'tone': tone, 'display_tone': tone,
            'counts': {'passed': passed, 'failed': failed, 'pending': pending, 'missing': missing},
            'conclusion': conclusion, 'detail': detail, 'text': conclusion+'。'+detail,
            'check_ids': [c['id'] for c in evidence if c.get('id')], 'request_ids': list(dict.fromkeys(x for c in evidence for x in rows(c.get('request_ids'))))}


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


def build_summary(result, checks, score):
    selected = lambda *keys: [c for c in checks if dimensions(c).intersection(keys) and not c.get('local_only') and c.get('applicable') is not False]
    basic = item('basic', '基础协议', selected('protocol'))
    tools = item('tools_media', '工具与多模态', selected('tools', 'multimodal'))
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
    security = [c for c in selected('security', 'injection') if any(x in ' '.join(str(v or '') for v in (c.get('id'), c.get('source_id'), raw_check(c).get('id'), c.get('title'))).lower() for x in ('injection', 'hierarchy', 'canary', '注入', '指令层级', '金丝雀')) or 'injection' in dimensions(c)]
    injection = item('injection', '指令隔离', security)
    if injection['status'] == 'failed': injection['conclusion'] = '指令隔离存在风险'
    injection['detail'] += ' 合成注入用例不能证明上游暗加提示词；本轮未验证上游是否添加提示词。'
    presentation(injection, '隔离风险待核查' if injection['counts']['failed'] else injection['status_label'], injection['tone'])
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
    items = [basic, tools, limit, injection, cache, pressure]
    # Batch conclusions must not pretend pooled requests describe one model.
    models = rows(obj(result.get('configuration')).get('models'))
    batch = result.get('suite') == 'batch_acceptance' or len(models) > 1
    if batch:
        items = [item('batch', '多模型结果', checks, '各模型独立判读', '本报告包含多个模型，失败和缓存率请按对应模型的检查证据阅读；不以跨模型汇总证明单个模型能力。')]
    headline_items = sorted(items, key=lambda x: (0 if x['status'] == 'failed' else 1 if x.get('cache') else 2 if x['id'] == 'basic' else 3))
    headline = '；'.join(x['conclusion'] for x in headline_items if x['status'] != 'not_covered')
    headline = '；'.join(headline.split('；')[:4]) or '本轮缺少可判定证据'
    overall = score.get('weighted_total', score.get('total'))
    grade = resource_grade(score)
    if batch:
        grade['provisional'] = grade['level'] != 'unknown'
        grade['note'] = '多模型汇总参考，不能代表每个模型的资源等级；请按模型单独判读。' + grade['note']
    return {'headline': headline, 'status': 'failed' if any(c.get('status') == 'failed' and eligible(c) for c in checks) else obj(score.get('evidence')).get('status', 'inconclusive'),
            'detail': '先看本轮结论、模块得分和证据可判定率，再对照失败请求。完整测试清单位于报告末尾。',
            'items': items, 'score': overall, 'overall_score': overall, 'resource_grade': grade, 'resolution_percent': obj(score.get('evidence')).get('resolution_percent'), 'timing': timing, 'duration': timing}
