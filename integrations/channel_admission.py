"""Offline channel admission and attempt-scoped cost reconciliation.

Scores describe capabilities; this module separately evaluates production gates.
No network call, credential handling, model call, or billing inference occurs here.
Rates supplied by the runner use ratios; policy targets use percentages.
"""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation

POLICY_DEFAULTS = {
    'target_success_rate': 99.5,
    'min_samples': 200,
    'min_observation_seconds': 1800,
    'max_p95_first_content_ms': 10000,
    'max_p95_latency_ms': 120000,
    'required_workloads': ['short', 'long_output', 'stream', 'tools'],
}
PRICE_FIELDS = ('input_per_million', 'output_per_million', 'cache_read_per_million', 'cache_write_per_million')
WORKLOADS = {'short', 'long_output', 'stream', 'tools', 'long_context', 'thinking', 'vision'}


def _obj(value): return value if isinstance(value, dict) else {}
def _rows(value): return value if isinstance(value, list) else []
def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

def _ratio(value):
    return value if _num(value) is not None and value <= 1 else None


def configuration(config=None):
    """Validate a policy, or an outer config containing ``admission_policy``.

    Outer runner config fields are ignored; unknown fields in an explicitly
    supplied policy are rejected to avoid silently accepting misspelled gates.
    Pricing is validated separately by ``pricing_configuration``.
    """
    if config is None: config = {}
    if not isinstance(config, dict): raise ValueError('渠道准入配置必须是对象')
    policy = config.get('admission', config.get('admission_policy', config.get('policy', config)))
    if not isinstance(policy, dict): raise ValueError('准入策略必须是对象')
    if ('admission' in config or 'admission_policy' in config or 'policy' in config) and set(policy) - set(POLICY_DEFAULTS):
        raise ValueError('准入策略包含未知字段：' + ', '.join(sorted(set(policy) - set(POLICY_DEFAULTS))))
    out = {**POLICY_DEFAULTS, 'required_workloads': list(POLICY_DEFAULTS['required_workloads'])}
    bounds = {'target_success_rate': (1, 100), 'min_samples': (1, 1000000),
              'min_observation_seconds': (1, 604800), 'max_p95_first_content_ms': (1, 3600000),
              'max_p95_latency_ms': (1, 7200000)}
    for field, (low, high) in bounds.items():
        value = policy.get(field, out[field])
        if _num(value) is None or not low <= value <= high or (field == 'min_samples' and _integer(value) is None):
            raise ValueError('%s 必须是 %s–%s 的%s' % (field, low, high, '整数' if field == 'min_samples' else '有限数值'))
        out[field] = value
    required = policy.get('required_workloads', out['required_workloads'])
    if not isinstance(required, list) or not required or any(not isinstance(x, str) or x not in WORKLOADS for x in required):
        raise ValueError('required_workloads 必须包含有效业务类型')
    out['required_workloads'] = list(dict.fromkeys(required))
    return out


def pricing_configuration(config=None):
    if config is None: config = {}
    if not isinstance(config, dict): raise ValueError('价格配置必须是对象')
    currency = config.get('currency')
    if currency is not None and currency not in ('CNY', 'USD'): raise ValueError('价格币种必须为 CNY 或 USD')
    if currency is None and any(config.get(field) not in (None, '') for field in PRICE_FIELDS):
        raise ValueError('配置单价时必须明确 CNY 或 USD 币种')
    out = {'currency': currency, 'version': str(config.get('version') or '').strip()[:120]}
    for name in PRICE_FIELDS:
        value = config.get(name)
        if value is None or value == '': out[name] = None; continue
        if _num(value) is None or value > 1000000000: raise ValueError(name + ' 必须是非负有限数值；未配置请留空')
        out[name] = value
    # A comparison must be a workload-matched measured cost, never a token quote.
    for field in ('reference_cost_per_success',):
        value = config.get(field)
        if value is not None and (_num(value) is None or value > 1000000000): raise ValueError(field + ' 必须是非负有限数值')
        out[field] = value
    for field in ('reference_currency', 'reference_workload_fingerprint', 'reference_request_format', 'reference_version'):
        value = config.get(field)
        if value is not None and not isinstance(value, str): raise ValueError(field + ' 必须是文本')
        out[field] = value
    return out


def confidence_interval(successes, samples):
    """Two-sided Wilson 95% interval; descriptive, not a long-term SLA."""
    if _integer(samples) is None or samples == 0 or _integer(successes) is None or successes > samples:
        return {'method': 'Wilson', 'level': 0.95, 'n': samples, 'successes': successes,
                'lower_percent': None, 'upper_percent': None, 'note': '样本不足，无法计算区间。'}
    z = 1.959963984540054; p = successes / samples; denominator = 1 + z*z/samples
    center = (p+z*z/(2*samples))/denominator
    half = z*math.sqrt(p*(1-p)/samples+z*z/(4*samples*samples))/denominator
    return {'method': 'Wilson', 'level': 0.95, 'n': samples, 'successes': successes,
            'lower_percent': round(max(0, center-half)*100, 4), 'upper_percent': round(min(1, center+half)*100, 4),
            'note': '95% Wilson 区间仅在近似独立且同分布抽样假设下成立；相关故障、多时段漂移和短测不能据此推断长期 SLA。'}


def _samples(result):
    """Each request attempt is represented once, including failed retries."""
    result = _obj(result)
    sources = [('native', _rows(result.get('samples'))), ('native', _rows(result.get('browser_requests'))),
               ('matrix', _rows(_obj(result.get('matrix_validation')).get('samples'))),
               ('production', _rows(_obj(result.get('production_validation')).get('samples'))),
               ('native', _rows(_obj(result.get('transport')).get('requests')))]
    seen = {}; output = []
    for scope, rows in sources:
        for index, row in enumerate(rows):
            if not isinstance(row, dict): continue
            identity = row.get('id') or row.get('request_id') or '%s-unidentified-%s' % (scope, index+1)
            identity = str(identity)
            if identity in seen:
                # Aliases of one actual request are common. Prefer production
                # attribution when the exact same sample is repeated at the top.
                if scope == 'production': seen[identity].update(scope=scope, sample=row)
                continue
            item = {'request_id': identity, 'scope': scope, 'sample': row}
            output.append(item); seen[identity] = item
    return output


def _normal(sample):
    return (sample.get('traffic_class', sample.get('traffic', 'normal')) == 'normal'
            and not sample.get('expected_negative') and not sample.get('control')
            and sample.get('evidence_category') != 'control')


def _payload(sample):
    value = _obj(sample.get('response')).get('body', sample.get('response_body', sample.get('body') if sample.get('type') == 'request_finish' else None))
    if isinstance(value, str):
        try: value = json.loads(value)
        except (ValueError, TypeError): value = None
    return _obj(value)


def _usage(sample):
    payload = _payload(sample)
    for value in (payload.get('usage'), payload.get('usageMetadata'),
                  _obj(sample.get('observed_fields')).get('usage'),
                  _obj(sample.get('evidence')).get('reported_usage'), sample.get('usage')):
        if isinstance(value, dict) and value: return value
    # SSE usage is cumulative. Merge message_start with final message_delta;
    # never sum successive snapshots of output_tokens.
    snapshots = _obj(sample.get('evidence')).get('usage') or _obj(_obj(sample.get('evidence')).get('sse')).get('usage')
    if isinstance(snapshots, dict): return snapshots
    merged = {}
    for entry in _rows(snapshots):
        if isinstance(entry, dict): merged.update(_obj(entry.get('value')) or _obj(entry.get('usage')))
    if merged: return merged
    # KVV records complete response bodies separately from compact transport
    # metadata. The caller may pass a request_finish record after joining IDs.
    raw = _obj(sample.get('response')).get('body', sample.get('response_body', sample.get('body') if sample.get('type') == 'request_finish' else None))
    if isinstance(raw, str):
        for block in re.split(r'\r?\n\r?\n', raw):
            payload_text = '\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
            if not payload_text: continue
            try: event = json.loads(payload_text)
            except (ValueError, TypeError): continue
            if not isinstance(event, dict): continue
            observed = _obj(event.get('usage')) or _obj(_obj(event.get('message')).get('usage')) or _obj(_obj(event.get('response')).get('usage'))
            merged.update(observed)
    return merged


def extract_usage(sample):
    """Read usage from stored evidence (including joined KVV finish records)."""
    return dict(_usage(sample))


def normalize_usage(sample, request_format=None):
    if (_num(sample.get('wire_requests')) or 0) > 1:
        return None, '记录包含多次底层发送，仅末次 usage 不足以估算全部尝试费用'
    if sample.get('body_truncated') or _obj(sample.get('evidence')).get('truncated') or sample.get('termination') in ('cancelled', 'client_cancel_probe'):
        return None, '响应截断或主动取消，实际完成用量需账单核实'
    usage = _usage(sample)
    if not usage: return None, '缺少实际 usage；失败、取消或负对照也不能推定免费'
    url = str(_obj(sample.get('request')).get('url') or sample.get('url') or '')
    if 'prompt_tokens' in usage:
        protocol = 'openai'; total = usage.get('prompt_tokens'); output = usage.get('completion_tokens')
        detail = usage.get('prompt_tokens_details')
        if detail is not None and not isinstance(detail, dict): return None, 'prompt_tokens_details 格式无效'
        cached = _obj(detail).get('cached_tokens', 0); write = 0
    elif 'promptTokenCount' in usage:
        protocol = 'gemini'; total = usage.get('promptTokenCount'); output = usage.get('candidatesTokenCount')
        # Thought tokens are separately billed output on Gemini.
        thoughts = usage.get('thoughtsTokenCount', 0)
        if _integer(thoughts) is None: return None, 'Gemini thoughtsTokenCount 无效'
        if _integer(output) is not None: output += thoughts
        cached = usage.get('cachedContentTokenCount', 0); write = 0
    elif 'cache_read_input_tokens' in usage or 'cache_creation_input_tokens' in usage or '/messages' in url or request_format == 'anthropic':
        protocol = 'anthropic'; normal = usage.get('input_tokens'); output = usage.get('output_tokens')
        cached = usage.get('cache_read_input_tokens', 0); write = usage.get('cache_creation_input_tokens', 0)
        if any(_integer(x) is None for x in (normal, cached, write, output)): return None, 'Anthropic usage 含缺失或无效计数'
        total = normal + cached + write
    elif 'input_tokens' in usage:
        protocol = 'responses'; total = usage.get('input_tokens'); output = usage.get('output_tokens')
        detail = usage.get('input_tokens_details')
        if detail is not None and not isinstance(detail, dict): return None, 'input_tokens_details 格式无效'
        cached = _obj(detail).get('cached_tokens', 0); write = 0
    else: return None, '未知 usage 计数格式'
    if any(_integer(x) is None for x in (total, output, cached, write)) or cached + write > total:
        return None, 'Token 计数必须为非负整数，缓存计数不得超过总输入'
    return {'protocol': protocol, 'input': total-cached-write, 'output': output,
            'cache_read': cached, 'cache_write': write, 'total_input': total,
            'total_output': output, 'unit': 'token'}, None


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)): raise ValueError('账单金额必须为非负有限数值')
    try: number = Decimal(str(value))
    except InvalidOperation: raise ValueError('账单金额必须为非负有限数值')
    if not number.is_finite() or number < 0 or number > Decimal('1000000000000'): raise ValueError('账单金额必须为非负有限数值')
    return number


def validate_ledger(result, ledger):
    if not isinstance(ledger, list): raise ValueError('账单 rows 必须是数组')
    known = {r['request_id'] for r in _samples(result)}
    seen = set(); output = []; currency = None
    for row in ledger:
        if not isinstance(row, dict): raise ValueError('每行账单必须是对象')
        identity = row.get('request_id')
        if not isinstance(identity, str) or identity not in known: raise ValueError('账单含未知 request_id')
        if identity in seen: raise ValueError('账单 request_id 重复：' + identity)
        if row.get('currency') not in ('CNY', 'USD'): raise ValueError('账单币种必须为 CNY 或 USD')
        if currency is not None and row['currency'] != currency: raise ValueError('同一账单不允许混合币种，请先按真实汇率外部对账')
        currency = row['currency']; seen.add(identity)
        note = row.get('note', '')
        if not isinstance(note, str) or len(note) > 2000: raise ValueError('账单备注必须是 2000 字以内文本')
        provider = row.get('provider_request_id')
        if provider is not None and (not isinstance(provider, str) or len(provider) > 500): raise ValueError('上游请求 ID 格式无效')
        output.append({'request_id': identity, 'amount': float(_amount(row.get('amount'))),
                       'currency': currency, 'note': note, **({'provider_request_id': provider} if provider else {})})
    return output


def ledger_template(result):
    currency = pricing_configuration(_obj(result.get('configuration')).get('pricing'))['currency']
    return [{'request_id': row['request_id'], 'amount': None, 'currency': currency, 'note': '',
             'scope': row['scope']} for row in _samples(result)]


def estimate_cost(result, ledger=None):
    config = _obj(result.get('configuration')); production = _obj(result.get('production_validation'))
    pricing = pricing_configuration(config.get('pricing', _obj(production.get('configuration')).get('pricing')))
    attempts = _samples(result)
    if ledger is None:
        stored = result.get('billing', result.get('cost_ledger'))
        ledger = stored.get('rows') if isinstance(stored, dict) else stored
    ledger_rows = validate_ledger(result, ledger) if ledger is not None else []
    ledger_map = {r['request_id']: r for r in ledger_rows}
    if ledger_rows and pricing['currency'] is not None and ledger_rows[0]['currency'] != pricing['currency']:
        raise ValueError('账单币种与报价币种不一致，不进行隐式汇率换算')
    currency = ledger_rows[0]['currency'] if ledger_rows else pricing['currency']
    rows = []; missing_usage = []; missing_price = []; missing_ledger = []
    for entry in attempts:
        identity, sample = entry['request_id'], entry['sample']
        units, error = normalize_usage(sample, config.get('request_format'))
        estimated = None; missing = []
        if units is None: missing_usage.append(identity); missing.append(error)
        else:
            missing_fields = [key+'_per_million' for key in ('input', 'output', 'cache_read', 'cache_write') if units[key] and pricing[key+'_per_million'] is None]
            if all(pricing[field] is None for field in PRICE_FIELDS): missing_fields = list(PRICE_FIELDS)
            if missing_fields: missing_price.append(identity); missing.append('缺少报价：'+', '.join(missing_fields))
            else:
                value = sum(Decimal(units[key])*Decimal(str(pricing[key+'_per_million'] or 0))/Decimal(1000000) for key in ('input', 'output', 'cache_read', 'cache_write'))
                estimated = float(value)
        actual = ledger_map.get(identity, {}).get('amount')
        if actual is None: missing_ledger.append(identity)
        rows.append({'request_id': identity, 'scope': entry['scope'], 'task_id': sample.get('task_id'),
                     'traffic_class': 'normal' if _normal(sample) else 'control', 'attempt': sample.get('attempt'),
                     'units': units, 'estimated_amount': estimated, 'actual_amount': actual,
                     'missing': missing, 'discrepancy': float(Decimal(str(actual))-Decimal(str(estimated))) if actual is not None and estimated is not None else None})
    count = len(rows); estimate_count = sum(r['estimated_amount'] is not None for r in rows); actual_count = len(ledger_rows)
    def complete_sum(items, field):
        return float(sum(Decimal(str(r[field])) for r in items)) if items and all(r[field] is not None for r in items) else None
    estimated_total = complete_sum(rows, 'estimated_amount'); actual_total = complete_sum(rows, 'actual_amount')
    prod_rows = [r for r in rows if r['scope'] == 'production' and r['traffic_class'] == 'normal']
    metrics = _obj(production.get('metrics')); successful = _integer(metrics.get('business_successful'))
    n = _integer(metrics.get('normal_tasks'))
    if successful is None or n is None or successful > n: successful = None
    prod_estimate = complete_sum(prod_rows, 'estimated_amount'); prod_actual = complete_sum(prod_rows, 'actual_amount')
    task_ids = {r['task_id'] for r in prod_rows if isinstance(r['task_id'], str) and r['task_id']}
    task_coverage_complete = bool(n and len(task_ids) == n and all(r['task_id'] for r in prod_rows))
    actual_per_success = float(Decimal(str(prod_actual))/Decimal(successful)) if task_coverage_complete and prod_actual is not None and successful else None
    estimated_per_success = float(Decimal(str(prod_estimate))/Decimal(successful)) if task_coverage_complete and prod_estimate is not None and successful else None
    comparison = {'status': 'unverified', 'detail': '未提供同币种、同协议、同业务配比的参考成功任务实测成本，不推断价格优势。'}
    fingerprint = metrics.get('workload_fingerprint') or production.get('workload_fingerprint')
    reference = pricing.get('reference_cost_per_success')
    comparable = (actual_per_success is not None and reference is not None and reference > 0 and fingerprint
                  and pricing.get('reference_workload_fingerprint') == fingerprint
                  and pricing.get('reference_currency') == currency
                  and pricing.get('reference_request_format') == config.get('request_format'))
    if comparable:
        saving = round((reference-actual_per_success)/reference*100, 2)
        comparison = {'status': 'lower' if saving > 0 else 'higher' if saving < 0 else 'equal',
                      'saving_percent': saving, 'reference_cost_per_success': reference,
                      'detail': '同协议、同业务指纹、同币种参考下的成功任务成本差异；依赖导入账单及参考数据真实性。'}
    status = 'actual' if count and actual_count == count else 'estimated' if count and estimate_count == count else 'partial' if actual_count or estimate_count else 'unverified'
    return {'status': status, 'label': {'actual': '已导入完整账单', 'estimated': '按实际 usage 估算 · 待账单核对', 'partial': '成本证据不完整', 'unverified': '成本待核算'}[status],
            'currency': currency, 'price_version': pricing['version'] or '未记录报价版本',
            'pricing': pricing, 'request_count': count, 'estimated_total': estimated_total, 'actual_total': actual_total,
            'estimated_partial_total': float(sum(Decimal(str(r['estimated_amount'] or 0)) for r in rows)) if estimate_count else None,
            'actual_partial_total': float(sum(Decimal(str(r['actual_amount'] or 0)) for r in rows)) if actual_count else None,
            'estimated_coverage': {'covered': estimate_count, 'total': count}, 'actual_coverage': {'covered': actual_count, 'total': count},
            'missing_usage_ids': missing_usage, 'missing_price_ids': missing_price, 'missing_ledger_ids': missing_ledger,
            'eligible_success_tasks': successful, 'production': {'request_count': len(prod_rows), 'normal_tasks': n, 'recorded_tasks': len(task_ids), 'task_coverage_complete': task_coverage_complete,
                'estimated_total': prod_estimate, 'actual_total': prod_actual,
                'estimated_cost_per_success': estimated_per_success, 'actual_cost_per_success': actual_per_success,
                'missing_ledger_ids': [r['request_id'] for r in prod_rows if r['actual_amount'] is None],
                'detail': '生产正常业务的全部实际尝试（含失败与重试）费用 ÷ 合格完成任务数；不混入原生和矩阵诊断探针、取消或恢复控制。'},
            'rows': rows, 'comparison': comparison,
            'discrepancy_total': float(Decimal(str(actual_total))-Decimal(str(estimated_total))) if actual_total is not None and estimated_total is not None else None,
            'limitations': ['导入账单是用户提供的扣费证据；未直接访问上游账务系统。', 'usage 报告不是实际扣费证明；缺失 usage 或单价不按免费处理。', '并发请求分别计费，失败、重试和控制探针均纳入总成本；已知取消不证明上游停止计费。']}



def reconcile(result, billing):
    """Validate an imported attempt ledger; return the value to persist as billing."""
    if not isinstance(billing, dict): raise ValueError('billing 必须是对象')
    note = billing.get('note', '')
    if not isinstance(note, str) or len(note) > 4000: raise ValueError('账单说明必须是 4000 字以内文本')
    normalized = validate_ledger(result, billing.get('rows'))
    # Validate against declared prices; absent prices never invent a currency.
    estimate_cost(result, normalized)
    return {'rows': normalized, 'note': note, 'source': 'operator_ledger',
            'currency': normalized[0]['currency'] if normalized else None}

def _gate(ident, label, status, observed, required, detail='', ids=None):
    return {'id': ident, 'label': label, 'status': status, 'observed': observed, 'required': required,
            'detail': detail, 'request_ids': list(dict.fromkeys(ids or []))}


def evaluate(result, ledger=None):
    """Return admission without modifying the input result or capability score."""
    config = _obj(result.get('configuration')); production = _obj(result.get('production_validation'))
    prod_config = _obj(production.get('configuration'))
    policy = configuration({'admission': config.get('admission', config.get('admission_policy', prod_config.get('admission', prod_config.get('admission_policy', {}))))})
    metrics = _obj(production.get('metrics')); gates = []
    checks = (_rows(result.get('checks')) or _rows(result.get('cases'))) + (_rows(production.get('checks')) or _rows(production.get('cases')))
    auth = [c for c in checks if isinstance(c, dict) and c.get('id') in ('authentication', 'invalid-auth', 'production-authentication')]
    attempts = _samples(result)
    baselines = [r for r in attempts if (r['scope'] == 'native' and (r['request_id'] == 'baseline' or r['sample'].get('probe') == 'baseline')) or r['request_id'] == 'production-baseline']
    baseline_checks = [c for c in checks if c.get('id') == 'production-baseline']
    credential_errors = [r for r in attempts if _normal(r['sample']) and (r in baselines or r['scope'] == 'production')
                         and _obj(r['sample'].get('response')).get('status') in (401, 403)]
    auth_failed = any(c.get('status') == 'failed' for c in auth) or bool(credential_errors)
    auth_passed = any(c.get('status') == 'passed' for c in auth) and not auth_failed
    gates.append(_gate('authentication', '鉴权与账户隔离', 'failed' if auth_failed else 'passed' if auth_passed else 'inconclusive',
                       '鉴权出现异常' if auth_failed else '有效/无效凭据对照已验证' if auth_passed else '未完成有效/无效凭据对照',
                       '有效凭据成功，无效凭据被拒绝', '合成无效凭据的预期 401/403 不计入正常业务失败率。',
                       [x for c in auth for x in _rows(c.get('request_ids'))] + [r['request_id'] for r in credential_errors]))
    baseline_status = 'failed' if any(r['sample'].get('status') == 'failed' for r in baselines) or any(c.get('status') == 'failed' for c in baseline_checks) else 'passed' if any(r['sample'].get('status') == 'passed' for r in baselines) or any(c.get('status') == 'passed' for c in baseline_checks) else 'inconclusive'
    gates.append(_gate('baseline', '有效业务基线', baseline_status, '已验证' if baseline_status == 'passed' else '基线异常' if baseline_status == 'failed' else '有效基线证据不足', '有效凭据可完成基础请求', ids=[r['request_id'] for r in baselines] + [rid for c in baseline_checks for rid in _rows(c.get('request_ids'))]))
    complete = production.get('status') == 'completed' and result.get('status') == 'completed'
    gates.append(_gate('complete', '生产场景执行完整性', 'passed' if complete else 'inconclusive', production.get('status') or '未执行', '生产场景完整执行，取消/中断/未启动不算完成'))
    n = _integer(metrics.get('normal_tasks')); successful = _integer(metrics.get('business_successful'))
    rates = {}; invalid = []
    if n is None or successful is None or successful > (n or 0): invalid.append('正常任务数或成功任务数缺失/无效')
    for name in ('first_attempt_success_rate', 'eventual_success_rate', 'protocol_success_rate', 'business_success_rate'):
        value = _ratio(metrics.get(name))
        if value is None: invalid.append(name + ' 缺失/无效')
        rates[name] = value
    if n and successful is not None and rates['business_success_rate'] is not None and abs(successful/n-rates['business_success_rate']) > max(0.0001, 0.5/n):
        invalid.append('业务成功率与样本计数不一致')
    gates.append(_gate('metrics', '统计证据口径', 'inconclusive' if invalid else 'passed', '；'.join(invalid) if invalid else '业务任务与协议/首试/最终成功率分别记录',
                       '仅正常任务进入业务成功率；预期负对照独立记录'))
    gates.append(_gate('samples', '正常业务样本量', 'passed' if n is not None and n >= policy['min_samples'] else 'inconclusive',
                       n if n is not None else '未记录', '至少 %s 个正常业务任务' % policy['min_samples']))
    observation = _num(metrics.get('observation_seconds'))
    populated = [b for b in _rows(metrics.get('time_buckets')) if isinstance(b, dict) and (_num(b.get('tasks', b.get('normal_tasks', b.get('count')))) or 0) > 0]
    window_ok = observation is not None and observation >= policy['min_observation_seconds'] and len(populated) >= 2
    gates.append(_gate('observation', '持续观察时窗', 'passed' if window_ok else 'inconclusive',
                       '%s 秒' % observation if observation is not None else '未记录', '至少 %s 秒真实生产场景观察，并覆盖至少 2 个有业务样本的时段' % policy['min_observation_seconds'],
                       '记录 %s 个有业务样本的时段；原生探针与排队前空闲时间不充当持续运行证据。' % len(populated)))
    threshold = policy['target_success_rate']/100
    for ident, label in (('first_attempt_success_rate', '首试业务成功率'), ('protocol_success_rate', '响应协议完整率'), ('business_success_rate', '完整业务任务成功率')):
        rate = rates[ident]
        state = 'inconclusive' if invalid or rate is None or not n else 'passed' if rate >= threshold else 'failed'
        gates.append(_gate(ident, label, state, '%g%%' % round(rate*100, 4) if rate is not None else '未记录', '≥ %g%%' % policy['target_success_rate'],
                           '重试后的最终成功率另列，不能掩盖首试错误。'))
    task_latency = _num(metrics.get('task_latency_p95_ms'))
    latency_value = task_latency if task_latency is not None else _num(metrics.get('latency_p95_ms'))
    for field, policy_key, label in (('first_content_p95_ms', 'max_p95_first_content_ms', '有效内容首到 P95'), ('latency_p95_ms', 'max_p95_latency_ms', '任务完成延迟 P95')):
        value = latency_value if field == 'latency_p95_ms' else _num(metrics.get(field)); target = policy[policy_key]
        gates.append(_gate(field, label, 'inconclusive' if value is None else 'passed' if value <= target else 'failed',
                           '%g ms' % value if value is not None else '未记录', '≤ %g ms' % target,
                           '首个 HTTP 字节、thinking 元数据与心跳不等同客户收到有效内容。' if field == 'first_content_p95_ms' else '完成延迟应覆盖真实请求/任务，超时与失败单独计数。'))
    workloads = _obj(metrics.get('workloads')); workload_status = 'passed'; evidence = []
    for name in policy['required_workloads']:
        work = _obj(workloads.get(name)); total = _integer(work.get('tasks', work.get('normal_tasks')))
        good = _integer(work.get('business_successful', work.get('successful')))
        if not total or good is None or good > total:
            if workload_status != 'failed': workload_status = 'inconclusive'
            evidence.append(name+' 未完整验证')
        elif good/total < threshold:
            workload_status = 'failed'; evidence.append('%s %s/%s' % (name, good, total))
        else: evidence.append('%s %s/%s' % (name, good, total))
    gates.append(_gate('workloads', '必需业务场景', workload_status, '；'.join(evidence), ', '.join(policy['required_workloads']),
                       '整体高分或高成功率不能抵消某个必需业务场景异常。'))
    # Recovery and cancellation are observed separately. No naturally
    # occurring fault is not a successful recovery experiment.
    recovery = _obj(metrics.get('recovery')); cancellation = _obj(metrics.get('cancellation'))
    if recovery:
        retried = _integer(recovery.get('tasks_retried')); recovered = _integer(recovery.get('recovered_tasks'))
        observed_retry = bool(retried and recovered is not None and recovered <= retried)
        recovery_state = 'passed' if observed_retry and recovered == retried else 'failed' if observed_retry else 'inconclusive'
        gate = _gate('recovery', '暂态故障恢复', recovery_state,
                     '%s/%s 个重试任务恢复' % (recovered, retried) if observed_retry else '本轮未获得可判定的自然故障恢复样本',
                     '观察到允许重试的暂态故障时应完成业务任务',
                     '未发生故障不算恢复已验证；不等同降载恢复、幂等性或重复扣费验证。', _rows(recovery.get('sample_ids')))
        gate['blocking'] = observed_retry
        gates.append(gate)
    if cancellation:
        cancel_samples = [r for r in attempts if r['scope'] == 'production' and r['sample'].get('workload') == 'cancellation']
        failed_close = [r for r in cancel_samples if _obj(r['sample'].get('evidence')).get('client_response_closed') is False
                        and ((_num(_obj(r['sample'].get('evidence')).get('content_event_count')) or 0) > 0
                             or _num(_obj(r['sample'].get('evidence')).get('first_content_ms')) is not None)]
        attempted = _integer(cancellation.get('attempted')); closed = _integer(cancellation.get('client_closed'))
        all_closed = bool(attempted and closed == attempted)
        gate = _gate('cancellation', '客户端取消释放', 'failed' if failed_close else 'passed' if all_closed else 'inconclusive',
                     '有效内容到达后本地响应未关闭' if failed_close else '%s/%s 次本地取消释放已观察' % (closed, attempted) if attempted else '未观察到有效的客户端取消窗口',
                     '收到有效内容后取消，客户端响应应释放',
                     '只验证客户端连接释放，不证明上游停止生成或停止计费；未等到有效内容不误判取消失败。',
                     [r['request_id'] for r in failed_close] or _rows(cancellation.get('sample_ids')))
        gate['blocking'] = bool(failed_close)
        gates.append(gate)
    normal_attempts = [r for r in attempts if r['scope'] == 'production' and _normal(r['sample'])]
    ordered_evidence = sorted(normal_attempts, key=lambda r: r['sample'].get('status') == 'passed')
    for gate in gates:
        if not gate['request_ids'] and gate['id'] not in ('authentication', 'baseline'):
            gate['request_ids'] = [r['request_id'] for r in ordered_evidence[:20]]
    hard = {'authentication', 'baseline', 'first_attempt_success_rate', 'protocol_success_rate', 'business_success_rate', 'workloads'}
    failures = [g for g in gates if g['status'] == 'failed']; unknown = [g for g in gates if g['status'] == 'inconclusive' and g.get('blocking') is not False]
    if any(g['id'] in hard for g in failures): status = 'rejected'
    elif failures: status = 'limited'
    elif unknown: status = 'limited' if n and successful and auth_passed and baseline_status == 'passed' and complete and not invalid else 'retest'
    else: status = 'approved'
    labels = {'approved': '建议接入 · 限本轮已验证范围', 'limited': '限量试运行 · 补齐准入条件', 'retest': '待复测 · 证据不足', 'rejected': '暂不接入 · 关键条件需修复'}
    reason_rows = failures + unknown
    confidence = confidence_interval(successful, n)
    reliability = {**rates, 'normal_tasks': n, 'business_successful': successful, 'confidence': confidence,
                   'observation_seconds': observation, 'duration_seconds': _num(metrics.get('duration_seconds')),
                   'first_content_p95_ms': _num(metrics.get('first_content_p95_ms')), 'latency_p95_ms': latency_value, 'attempt_latency_p95_ms': _num(metrics.get('latency_p95_ms')),
                   'latency_population': '完整业务任务（含多轮与重试）' if task_latency is not None else '旧记录的 HTTP 尝试延迟；未记录完整业务任务延迟',
                   'time_buckets': _rows(metrics.get('time_buckets')), 'counting_note': '正常业务按完整任务计数；多轮工具请求和自动重试分别留存尝试证据，预期负对照不混入业务成功率。'}
    return {'status': status, 'label': labels[status], 'gates': gates, 'policy': policy,
            'observations': ['%s：%s' % (g['label'], g['observed']) for g in gates if g['status'] == 'inconclusive' and g.get('blocking') is False],
            'reasons': ['%s：%s' % (g['label'], g['observed']) for g in reason_rows] or ['本轮已配置的准入条件均满足；不替代上线后的持续监测。'],
            'validated_envelope': {'model': config.get('model'), 'request_format': config.get('request_format'),
                'normal_tasks': n, 'observation_seconds': observation, 'workloads': workloads,
                'concurrency': metrics.get('max_concurrency', prod_config.get('concurrency')),
                'limits': metrics.get('validated_limits', {}), 'detail': '仅描述实际已测业务与负载；计划上限、来源声明和综合评分不构成长期 SLA。'},
            'reliability': reliability, 'cost': estimate_cost(result, ledger)}
