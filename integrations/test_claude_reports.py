"""Claude report contracts; synthetic observations, no model API requests."""
from copy import deepcopy
import unittest

from acceptance_results import decorate
from report_content import build_report_data
from report_renderer import evidence_records, render_report


def report(cases=None, **changes):
    cases = [{'id': 'baseline', 'title': '原生协议基线', 'status': 'passed', 'module': 'protocol',
              'dimensions': ['protocol'], 'method': '发送规范的原生消息请求', 'expected': '返回非空文本及 stop_reason',
              'observed': '收到 200、end_turn、hello', 'meaning': '本次请求成功', 'next_step': '按请求 ID 对照上游',
              'request_ids': ['baseline-1']}] if cases is None else cases
    value = {'suite': 'claude_acceptance', 'status': 'completed', 'configuration': {
        'base': 'https://fixture.invalid/v1', 'model': 'claude-fixture', 'request_format': 'anthropic',
        'auth': 'anthropic', 'provider': 'aws', 'cache_tokens': 8192, 'stress_requests': 8, 'concurrency': 2},
        'cases': cases, 'summary': {'total': len(cases), 'completed': len(cases)},
        'samples': [{'id': 'baseline-1', 'probe': 'baseline', 'status': 'passed', 'duration_ms': 100,
            'termination': 'eof', 'request': {'method': 'POST', 'url': 'https://fixture.invalid/v1/messages',
                'body': {'model': 'claude-fixture', 'max_tokens': 32, 'messages': [{'role': 'user', 'content': 'hello'}]}},
            'response': {'status': 200, 'body': '{"content":[{"type":"text","text":"hello"}]}',
                'headers': [['request-id', 'up-fixture-1']]},
            'evidence': {'request_ids': [{'header': 'request-id', 'value': 'up-fixture-1'}]},
            'assessments': [{'check': 'baseline', 'status': 'passed', 'detail': 'hello'}]}]}
    value.update(changes)
    return value


class ClaudeReportTests(unittest.TestCase):
    def test_claude_cases_preserve_exact_methods_and_dont_invent_missing_probes(self):
        value = report(); before = deepcopy(value)
        result = build_report_data(value)
        self.assertEqual(value, before)
        self.assertEqual(result['title'], 'Claude 模型专项验收报告')
        self.assertEqual(len(result['checks']), 1)
        check = result['checks'][0]
        self.assertEqual(check['method'], value['cases'][0]['method'])
        self.assertEqual(check['expected'], value['cases'][0]['expected'])
        self.assertEqual(check['request_ids'], ['baseline-1', 'up-fixture-1'])
        self.assertEqual(check['metadata']['module'], 'protocol')
        self.assertIn('不代表已验证来源', '\n'.join(result['scope']))
        self.assertNotIn('官方 pytest', '\n'.join(result['scope']))

    def test_claude_checks_alias_and_verdict_remain_compatible(self):
        value = report()
        value['checks'] = value.pop('cases')
        value['suite'] = 'claude'
        self.assertEqual(decorate(value)['verdict']['status'], 'passed')
        self.assertEqual(len(build_report_data(value)['checks']), 1)

    def test_inapplicable_signature_and_missing_cache_are_not_passes(self):
        cases = [
            {'id': 'cache', 'title': '大 Token 缓存', 'status': 'inconclusive', 'module': 'cache',
             'dimensions': ['cache'], 'observed': '8192-token 前缀重复三次，缓存计数字段缺失'},
            {'id': 'signature', 'title': '签名', 'status': 'skipped', 'applicable': False,
             'module': 'auth_signature', 'dimensions': ['signature'], 'skip_reason': 'OpenAI 协议未定义签名契约'},
            {'id': 'stress', 'title': '并发压测', 'status': 'not_covered', 'module': 'stress', 'dimensions': ['reliability']},
        ]
        value = report(cases, request_format='openai', configuration={**report()['configuration'], 'request_format': 'openai', 'auth': 'bearer'})
        data = build_report_data(value)
        modules = {entry['id']: entry for entry in data['score']['modules']}
        self.assertEqual(sum(entry['weight'] for entry in modules.values()), 100)
        self.assertIsNone(modules['cache']['score'])
        self.assertEqual(modules['cache']['conclusive'], 0)
        self.assertEqual(modules['auth_signature']['covered'], 0)
        self.assertEqual(modules['stress']['covered'], 0)
        self.assertEqual(data['score']['weight_covered'], 0)
        text = '\n'.join(data['scope'] + data['focus'] + data['limitations'])
        self.assertNotIn('KVV', text)
        self.assertNotIn('Kimi', text)
        self.assertIn('签名探针标记为不适用', text)
        self.assertIn('声明', text)

    def test_enabled_module_names_map_to_claude_dimension_names(self):
        cases = [
            {'id': 'prompt_injection', 'status': 'failed', 'module': 'injection', 'dimensions': ['security']},
            {'id': 'vision', 'status': 'passed', 'module': 'tools', 'dimensions': ['multimodal']},
            {'id': 'signature', 'status': 'passed', 'module': 'auth_signature', 'dimensions': ['signature', 'protocol']},
            {'id': 'stress', 'status': 'inconclusive', 'module': 'stress', 'dimensions': ['reliability']},
        ]
        result = build_report_data(report(cases, enabled_modules=['injection','tools','auth_signature','stress']))
        dims = {entry['id']: entry for entry in result['score']['dimensions']}
        self.assertEqual(dims['security']['covered'], 1)
        self.assertEqual(dims['multimodal']['covered'], 1)
        self.assertEqual(dims['signature']['covered'], 1)
        self.assertEqual(dims['reliability']['covered'], 1)
        self.assertEqual(dims['cache']['covered'], 0)
        modules = {entry['id']: entry for entry in result['score']['modules']}
        self.assertEqual(modules['injection']['score'], 0)
        self.assertEqual(modules['auth_signature']['score'], 100)

    def test_render_uses_common_theme_and_exact_request_evidence(self):
        value = report()
        rows = evidence_records(value)
        self.assertEqual(rows[0]['id'], 'baseline-1')
        self.assertEqual(rows[0]['request_body']['max_tokens'], 32)
        html = render_report(value).decode()
        for label in ('Claude 模型专项验收报告', '全项测试结果', '验收模块总览', '能力评分与覆盖明细',
                      '请求明细与证据', 'AWS Bedrock（渠道声明）', '大 Token 缓存', '压测与稳定性'):
            self.assertIn(label, html)
        self.assertIn('href="#request-1"', html)
        self.assertIn('max_tokens', html)
        self.assertNotIn('KVV Schema / 原生版本', html)
        self.assertNotIn('Kimi 原生契约', html)
        self.assertNotIn('pytest 项', html)
        self.assertIn('1 / 1 专项检查', html)

    def test_long_cache_evidence_is_retained_in_full_disclosure(self):
        value = report()
        value['samples'][0]['request']['body']['messages'][0]['content'] = 'prefix-test-' * 2000 + 'CACHE-END-MARKER'
        html = render_report(value).decode()
        self.assertIn('展开完整已采集内容', html)
        self.assertIn('CACHE-END-MARKER', html)

    def test_claude_batch_evidence_ids_remain_model_scoped(self):
        children = [{'model': model, 'result': report(configuration={**report()['configuration'], 'model': model})} for model in ('claude-a', 'claude-b')]
        value = {'suite': 'batch_acceptance', 'status': 'completed', 'configuration': {'suite': 'claude'}, 'results': children}
        data = build_report_data(value)
        self.assertIn('model-1-baseline-1', data['checks'][0]['request_ids'])
        self.assertIn('model-2-baseline-1', data['checks'][1]['request_ids'])
        self.assertNotIn('model-2-baseline-1', data['checks'][0]['request_ids'])
        self.assertEqual([row['id'] for row in evidence_records(value)], ['model-1-baseline-1','model-2-baseline-1'])
        self.assertEqual(sum(m['weight'] for m in data['score']['modules']), 100)
        self.assertIn('auth_signature', {m['id'] for m in data['score']['modules']})

    def test_stress_and_cache_metrics_are_readable_outside_raw_json(self):
        cases = [
            {'id': 'stress', 'status': 'inconclusive', 'module': 'stress', 'dimensions': ['reliability'],
             'observed': '本轮出现一次限流。', 'metrics': {'requested': 20, 'completed': 19, 'concurrency': 4,
                'success_rate': 18 / 19, 'latency_p50_ms': 1200, 'latency_p95_ms': 3400, 'ttfb_p95_ms': 800,
                'http_statuses': {'200': 18, '429': 1}}},
            {'id': 'cache', 'status': 'inconclusive', 'module': 'cache', 'dimensions': ['cache'], 'cache_observations': [
                {'sample_id': 'cache-1', 'input_tokens': 12010, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 12000, 'duration_ms': 5200},
                {'sample_id': 'cache-2', 'input_tokens': 12010, 'cache_read_input_tokens': 12000, 'cache_creation_input_tokens': 0, 'duration_ms': 900},
                {'sample_id': 'cache-4', 'prefix_control': True, 'input_tokens': 12030, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 12020, 'duration_ms': 5000}]},
        ]
        data = build_report_data(report(cases))
        stress, cache = data['checks']
        for text in ('计划 20 次 / 已完成 19 次', '并发 4', '成功率 94.74%', 'P50 1200 ms', 'P95 3400 ms', 'TTFB P95 800 ms', '429 × 1'):
            self.assertIn(text, stress['observed'])
            self.assertIn(text, stress['observed_summary'])
        self.assertIn('缓存创建 12000 Token', cache['observed'])
        self.assertIn('前缀变更负对照', cache['observed'])
        self.assertIn('第 2 轮 12000', cache['observed_summary'])
        self.assertIn('cache-4', cache['request_ids'])

    def test_identity_summary_includes_baseline_models_ids_and_declared_source(self):
        value = report([{'id': 'identity', 'status': 'inconclusive', 'module': 'identity', 'dimensions': ['identity'],
                        'observed': '响应字段不能单独证明来源。'}])
        value['samples'][0]['evidence']['response_model'] = 'claude-returned-version'
        value['samples'][0]['response']['headers'].append(['server', 'fixture-gateway'])
        identity = build_report_data(value)['checks'][0]
        self.assertIn('请求模型 claude-fixture', identity['observed_summary'])
        self.assertIn('响应模型 claude-returned-version', identity['observed_summary'])
        self.assertIn('up-fixture-1', identity['observed_summary'])
        self.assertIn('AWS Bedrock', identity['observed_summary'])
        self.assertIn('未经来源认证', identity['observed_summary'])
        self.assertIn('server=fixture-gateway', identity['observed'])
        self.assertIn('baseline-1', identity['request_ids'])

    def test_unselected_module_remains_not_covered_in_report(self):
        data = build_report_data(report([{'id': 'stress', 'status': 'not_covered', 'module': 'stress', 'applicable': False, 'module_disabled': True}]))
        self.assertEqual(data['checks'][0]['status'], 'not_covered')

    def test_repeated_upstream_header_cannot_expand_exact_sample_links(self):
        value = report()
        # A shared vendor/gateway ID is evidence to display, not a local
        # foreign key that joins all unrelated requests to one assertion.
        second = deepcopy(value['samples'][0]); second['id'] = 'unrelated-2'; second['probe'] = 'tools'
        second['assessments'] = [{'check': 'tools', 'status': 'passed', 'detail': 'unrelated'}]
        value['samples'].append(second)
        html = render_report(value).decode()
        matrix = html.split('id="all-results"', 1)[1].split('</section>', 1)[0]
        self.assertIn('1 次 / 0 次', matrix)
        self.assertNotIn('2 次 / 0 次', matrix)
        check = html.split('id="check-1"', 1)[1].split('</article>', 1)[0]
        self.assertIn('href="#request-1"', check)
        self.assertNotIn('href="#request-2"', check)


if __name__ == '__main__':
    unittest.main()
