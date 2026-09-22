"""Map saved browser observations to the same report contract as acceptance runs.

This module never sends a request to a model. A successful generation is only a
successful response, not proof of tool execution, cache hits or model identity.
"""
from copy import deepcopy
import json
import time


DIMENSIONS = [('multimodal', '多模态能力'), ('tools', '工具调用'), ('max_tokens', 'max_tokens / 长度控制'),
              ('cache', '缓存与 usage'), ('protocol', '协议与错误'), ('reliability', '稳定性与性能')]
STATUS = {'success': 'passed', 'passed': 'passed', 'error': 'failed', 'failed': 'failed',
          'stopped': 'cancelled', 'cancelled': 'cancelled', 'demo': 'skipped'}
KINDS = {'text': '文本', 'image': '图像', 'video': '视频', 'audio': '音频'}
LIMITS = ['只评价本轮已保存的观察。基础请求返回内容，不代表专项工具调用、缓存命中、max_tokens 契约或媒体内容质量已通过验收。',
          '不同模型的检查和请求分别标注；汇总分是本报告已覆盖检查的表现，不是模型排行榜或官方能力认证。',
          '媒体文件已嵌入时可离线播放；远程媒体仅保留原链接，访问需要网络且链接可能失效。报告导出不会重新调用模型或下载远程媒体。',
          '没有采集的请求正文、响应头或完整流式文本保持“未记录”，不补造证据。演示、取消和未完成结果不会计为通过。']


def _dict(value): return value if isinstance(value, dict) else {}
def _list(value): return value if isinstance(value, list) else []
def _number(value, default=0):
    try: return float(value)
    except (ValueError, TypeError): return default


def dimensions_for(name):
    import re
    name = str(name)
    dimensions = []
    for key, pattern in [('multimodal', r'视觉|多模态|图像|图片|视频|音频|vision'),
                         ('tools', r'工具|tool|function'), ('max_tokens', r'max.?tokens|长度|参数'),
                         ('cache', r'缓存|cache|usage'), ('reliability', r'延迟|吞吐|并发|稳定|流式|stream')]:
        if re.search(pattern, name, re.I): dimensions.append(key)
    return dimensions or ['protocol']


def request_record(raw, identity, model, case_id=''):
    raw = _dict(raw)
    code = raw.get('status') if isinstance(raw.get('status'), int) else raw.get('http_status')
    raw_status = str(raw.get('status') or '').lower()
    status = 'failed' if raw.get('error') or raw_status in ('failed','error') or (isinstance(code, int) and code >= 400) else 'completed' if raw_status in ('success','passed','completed') or code else 'inconclusive'
    return {'id': identity, 'model': raw.get('model') or model, 'case_id': case_id,
            'status': status, 'http_status': code, 'duration_ms': raw.get('duration_ms', raw.get('elapsedMs')),
            'method': raw.get('method'), 'url': raw.get('url'), 'request_body': raw.get('request', raw.get('body')),
            'response_body': raw.get('response', raw.get('raw')), 'response_headers': raw.get('headers'),
            'notes': [raw['error']] if raw.get('error') else [], 'extra': raw, 'assessments': [],
            'observed_fields': {key: raw.get(key) for key in ('model','finish_reason','completion_tokens','prompt_tokens','cached_tokens','tool_calls','usage','media_count') if raw.get(key) is not None}}


def normalize_browser_report(payload):
    if not isinstance(payload, dict): raise ValueError('报告内容必须为 JSON 对象')
    if payload.get('suite') == 'browser_report':
        result = deepcopy(payload)
        if not isinstance(result.get('cases'), list) or not isinstance(result.get('browser_requests'), list):
            raise ValueError('报告缺少检查或请求记录')
        if len(result['cases']) > 5000 or len(result['browser_requests']) > 10000: raise ValueError('报告条目过多')
        for case in result['cases']:
            if not isinstance(case, dict): raise ValueError('检查项格式无效')
        for row in result['browser_requests']:
            if not isinstance(row, dict): raise ValueError('请求记录格式无效')
        return result
    records = payload.get('records')
    if records is None and payload.get('kind'): records = [payload]
    if not isinstance(records, list) or not records or len(records) > 500: raise ValueError('请提供 1 至 500 条测试记录')
    cases, requests, models, bases, originals = [], [], [], [], []
    started, finished = [], []
    general = False
    for index, record in enumerate(records):
        if not isinstance(record, dict): raise ValueError('测试记录格式无效')
        result = _dict(record.get('result')) or record
        kind = record.get('kind') or 'text'; general = general or kind == 'general'
        config = _dict(result.get('config'))
        model = str(record.get('model') or config.get('model') or '未记录模型')
        base = str(record.get('base') or config.get('base') or '未记录')
        if model not in models: models.append(model)
        if base not in bases: bases.append(base)
        created = _number(record.get('created_at'), time.time())
        started.append(created); finished.append(created + _number(record.get('duration_ms', result.get('elapsedMs'))) / 1000)
        prefix = 'record-%s' % (index + 1)
        record_requests = []
        for ri, raw in enumerate(_list(result.get('requests'))):
            identity = '%s-request-%s' % (prefix, ri + 1)
            requests.append(request_record(raw, identity, model, prefix if kind != 'general' else ''))
            record_requests.append(identity)
        if kind == 'general':
            originals.append({'model': model, 'total': result.get('total'), 'scores': result.get('scores'), 'batch': result.get('batch'), 'logs': result.get('logs')})
            for ci, check in enumerate(_list(result.get('checks'))):
                if not isinstance(check, dict): continue
                name = str(check.get('name') or check.get('title') or '通用检查')
                status = check.get('status') if check.get('status') in ('passed', 'failed', 'inconclusive', 'cancelled', 'skipped') else 'inconclusive'
                check_model = check.get('model') or (model if not _list(result.get('batch')) else '旧记录未保存逐项模型')
                fields = {key: check.get(key) for key in ('model','finish_reason','completion_tokens','prompt_tokens','cached_tokens','tool_calls','http_status','duration_ms') if check.get(key) is not None}
                observed = check.get('result', check.get('observed'))
                if fields:
                    observed = '%s\n实测字段：%s' % (observed or '已记录检查结果', json.dumps(fields, ensure_ascii=False, default=str))
                cases.append({'id': '%s-check-%s' % (prefix, ci+1), 'title': '%s · %s' % (check_model, name), 'model': check_model,
                    'status': status, 'method': '运行通用检测内置“%s”用例，并保留本次返回的判定。' % name,
                    'expected': check.get('expected') or '符合该内置用例的输出与协议断言；旧记录未保存独立预期值，详见原始判定。',
                    'observed': observed, 'meaning': check.get('judge') or '依据保存的原始判定；单项不能推导所有能力。',
                    'next_step': '优先按模型和请求地址核对失败项，再重跑该专项。' if status != 'passed' else '此结论仅适用于当前样本；可增加输入、并发和重复测试。',
                    'metadata': {'dimensions': dimensions_for(name), 'module': dimensions_for(name)[0]}, 'raw': check, 'request_ids': []})
        else:
            status = STATUS.get(record.get('status') or result.get('status'), 'inconclusive')
            title = result.get('preset') or (KINDS.get(kind, kind) + '基础测试')
            observed = result.get('error') or result.get('text') or result.get('output') or ('收到 %s 个媒体结果。' % len(_list(record.get('media'))) if record.get('media') else '未保存可展示内容，请查看原始响应。')
            cases.append({'id': prefix, 'title': '%s · %s' % (model, title), 'model': model, 'status': status,
                'method': '使用所选协议与用户输入发起一次%s请求；本项检查是否收到可展示结果。' % KINDS.get(kind, kind),
                'expected': '返回与所选接口协议匹配的文本或媒体结果；生成、编辑和理解能力以实际请求体为准。',
                'observed': observed, 'meaning': '返回成功仅表示本次接口响应可解析，不自动证明生成质量或工具、缓存、长度控制专项通过。',
                'next_step': result.get('diagnostic') or ('核对错误正文、模型名、端点和鉴权；需要时联系渠道方按请求 ID 排查。' if status == 'failed' else '结合输出复核质量，并使用通用检测或专项验收验证工具、缓存与长度控制。'),
                'metadata': {'dimensions': dimensions_for(title), 'module': dimensions_for(title)[0]}, 'request_ids': record_requests,
                'raw': result, 'media': _list(record.get('media'))})
    if not cases:
        cases = [{'id': 'missing-evidence', 'title': '检查证据不完整', 'status': 'inconclusive', 'model': '、'.join(models),
                  'method': '读取已保存的测试记录。', 'expected': '至少保留一项实际检查结果。', 'observed': '没有可用于逐项判定的记录。',
                  'meaning': '不把空记录视为通过。', 'next_step': '重新运行检测并确认历史保存完成。', 'metadata': {'dimensions': []}}]
    counts = {key: sum(1 for c in cases if c.get('status') == key) for key in ('passed','failed','inconclusive','cancelled','skipped')}
    overall = 'failed' if counts['failed'] else 'inconclusive' if counts['inconclusive'] or counts['cancelled'] or counts['skipped'] else 'passed'
    return {'suite': 'browser_report', 'report_kind': 'general' if general else 'basic', 'status': 'completed',
            'run_id': payload.get('run_id') or 'browser-export', 'configuration': {'model': '、'.join(models), 'models': models, 'base': '、'.join(bases)},
            'started_at': min(started), 'finished_at': max(finished), 'cases': cases, 'browser_requests': requests,
            'summary': {'total': len(cases), 'completed': len(cases), **counts},
            'verdict': {'status': overall, 'label': '存在失败或证据不足' if overall != 'passed' else '本轮已执行检查通过',
                        'detail': '基础请求结果只代表本轮实际观察；专项能力需查看逐项覆盖。' if overall == 'passed' else '请按模型和请求证据查看失败、取消或无法判定项。'},
            'original_results': originals,
            'transport': {'request_count': len(requests)}}


def report_data(result):
    from report_content import _report_score, _findings
    checks = result['cases']
    general = result.get('report_kind') == 'general'
    return {'title': '通用深度检测报告' if general else '多模态基础测试报告',
            'engine': '通用检测内置用例' if general else '工作台基础接口观察', 'checks': checks,
            'scope': ['本报告仅汇总实际保存的 %s 个检查结果和 %s 条 HTTP 观察。' % (len(checks), len(result['browser_requests'])),
                      '每项标题与请求证据保留所属模型；多模型汇总不混用某一个模型的结果。'],
            'focus': ['通用检测关注协议、多模态理解、工具、参数、缓存及稳定性；实际覆盖以逐项记录为准。' if general else '基础测试关注接口连通、请求参数、文本或媒体返回及错误诊断。'],
            'limitations': LIMITS, 'findings': _findings(checks, result), 'score': _report_score(checks, result)}
