"""Browser exports retain protocol, truthful evidence links and original scores."""
import copy
import unittest
from browser_reports import normalize_browser_report, report_data
from report_renderer import render_report


def payload(checks=None, requests=None, **extra):
    return {'records': [{'kind': 'general', 'model': 'fixture-model', 'base': 'https://relay.test/api/v1',
        'created_at': 1700000000, 'duration_ms': 1200, 'result': {
            'config': {'requestFormat': 'openai-chat', 'auth': 'bearer', 'path': '/chat/completions', 'endpoint': 'https://relay.test/api/v1/chat/completions', 'key': 'private-key', 'timeout': 180000},
            'checks': checks or [], 'requests': requests or [], **extra}}]}


class BrowserReportTests(unittest.TestCase):
    def test_protocol_settings_and_assertion_explanation_survive_normalization(self):
        original = payload([{'id': 'tools', 'name': '工具调用', 'status': 'passed', 'method': '强制工具调用并回填结果',
            'expected': 'tool_calls 含 Calculator', 'observed': '收到 Calculator', 'meaning': '支持当前工具 Schema',
            'next_step': '扩大参数覆盖', 'dimensions': ['tools'], 'module': 'tools'}])
        saved = copy.deepcopy(original)
        result = normalize_browser_report(original)
        self.assertEqual(original, saved, 'normalization does not mutate the saved record')
        config = result['configuration']
        self.assertEqual(config['request_format'], 'openai-chat')
        self.assertEqual(config['requestFormat'], 'openai-chat')
        self.assertEqual(config['auth'], 'bearer')
        self.assertEqual(config['path'], '/chat/completions')
        self.assertEqual(config['timeout'], 180)
        self.assertEqual(config['records'][0]['timeout_ms'], 180000)
        self.assertNotIn('key', config['records'][0])
        check = result['cases'][0]
        for key in ('method','expected','observed','meaning','next_step'):
            self.assertEqual(check[key], saved['records'][0]['result']['checks'][0][key])
        html = render_report(result).decode()
        self.assertIn('OpenAI Chat Completions', html)
        self.assertIn('Authorization: Bearer', html)
        self.assertIn('https://relay.test/api/v1/chat/completions', html)
        self.assertNotIn('private-key', html)
        self.assertIn('扩大参数覆盖', html)

    def test_uncovered_and_inapplicable_checks_never_score_as_passed(self):
        result = normalize_browser_report(payload([
            {'name':'工具调用','status':'passed','applicable':False,'reason':'当前协议不适用'},
            {'name':'视频多模态','status':'not_covered'},
            {'name':'缓存','status':'skipped'},
        ]))
        self.assertEqual([c['status'] for c in result['cases']], ['skipped','not_covered','skipped'])
        self.assertFalse(result['cases'][0]['applicable'])
        self.assertEqual(result['summary']['passed'], 0)
        score = report_data(result)['score']
        self.assertEqual(score['total'], 0)
        self.assertEqual(score['covered_dimensions'], 0)
        html = render_report(result).decode()
        self.assertIn('data-filter="not_covered"', html)
        self.assertIn('未覆盖', html)
        self.assertNotIn('本轮已执行检查通过', html)

    def test_request_links_require_explicit_ids_and_remain_record_scoped(self):
        first = payload([
            {'id':'tool-case','name':'工具调用','status':'passed','request_ids':['r1','missing']},
            {'id':'reverse-case','name':'缓存','status':'failed'},
            {'id':'unlinked','name':'无关联项','status':'inconclusive'},
        ], [
            {'id':'r1','status':200,'request_body':{'model':'fixture-model'},'response_body':{'ok':True}},
            {'request_id':'r2','case_id':'reverse-case','status':200},
            {'id':'r3','status':200},
        ])
        second = payload([{'name':'第二记录','status':'passed','request_id':'r1'}], [{'id':'r1','status':200}])['records'][0]
        first['records'].append(second)
        result = normalize_browser_report(first)
        self.assertEqual([c['request_ids'] for c in result['cases']], [
            ['record-1-request-1'], ['record-1-request-2'], [], ['record-2-request-1']])
        self.assertEqual(result['browser_requests'][0]['case_id'], 'record-1-check-1')
        self.assertEqual(result['browser_requests'][1]['case_id'], 'record-1-check-2')
        self.assertEqual(result['browser_requests'][2]['case_id'], '')
        html = render_report(result).decode()
        first_case = html.split('id="check-1"')[1].split('</article>',1)[0]
        unlinked_case = html.split('id="check-3"')[1].split('</article>',1)[0]
        self.assertIn('href="#request-1"', first_case)
        self.assertNotIn('请求证据：', unlinked_case)
        self.assertNotIn('href="#missing"', html)

    def test_ambiguous_duplicate_request_ids_do_not_create_false_links(self):
        result = normalize_browser_report(payload([
            {'name':'不确定关联','status':'passed','request_id':'r1'},
            {'name':'有模型关联','status':'passed','model':'b','request_id':'r1'},
        ], [{'id':'r1','model':'a','status':200}, {'id':'r1','model':'b','status':200}]))
        self.assertEqual(result['cases'][0]['request_ids'], [])
        self.assertEqual(result['cases'][1]['request_ids'], ['record-1-request-2'])

    def test_original_dimension_batch_and_logs_visible_in_unified_template(self):
        result = normalize_browser_report(payload([{'name':'协议','status':'passed'}], total=87.5, scores={'tools':8.5,'cache':0},
            mode='batch(quick)', batch=[{'model':'batch-a','mini':8.2,'stream':True,'tools':False,'json':True,'conc':'2/3','custom':'keep-me'}],
            logs=[{'t':'12:00:01','msg':'<script>must remain text</script>'}], intelligence=['额外判断']))
        original = result['original_results'][0]
        self.assertEqual(original['scores'], {'tools':8.5,'cache':0})
        self.assertEqual(original['batch'][0]['custom'], 'keep-me')
        html = render_report(result).decode()
        self.assertIn('id="original-results"', html)
        self.assertIn('原始总分：87.5', html)
        self.assertIn('批量模型结果', html)
        self.assertIn('batch-a', html)
        self.assertIn('8.2', html)
        self.assertIn('<td>0</td>', html)
        self.assertIn('<td>否</td>', html)
        self.assertIn('执行日志 · 1 条', html)
        self.assertIn('&lt;script&gt;must remain text&lt;/script&gt;', html)
        self.assertNotIn('<script>must remain text</script>', html)
        self.assertIn('原始评分尺度与上方统一证据评分分别展示', html)

    def test_mixed_protocols_are_shown_per_record_instead_of_claiming_one(self):
        data = payload([{'name':'协议','status':'passed'}])
        other = copy.deepcopy(data['records'][0]);other['model']='claude-fixture'
        other['result']['config'].update(requestFormat='anthropic', auth='anthropic', path='/messages', endpoint='/v1/messages')
        data['records'].append(other)
        result = normalize_browser_report(data)
        self.assertNotIn('request_format', result['configuration'])
        self.assertNotIn('auth', result['configuration'])
        html = render_report(result).decode()
        self.assertIn('OpenAI Chat Completions', html)
        self.assertIn('Anthropic Messages', html)
        self.assertIn('Anthropic · x-api-key', html)
        self.assertIn('/v1/messages', html)


if __name__ == '__main__':
    unittest.main()

class UnifiedThemeTests(unittest.TestCase):
    def test_shared_theme_matrix_and_counts_never_invent_request_failures(self):
        from pathlib import Path
        from report_renderer import STYLE
        self.assertEqual(STYLE, (Path(__file__).resolve().parent.parent/'multimodal-workbench/report-theme.css').read_text())
        result = normalize_browser_report(payload([
            {'id':'tool','name':'工具字段','status':'failed','request_ids':['one'],'method':'检查工具回填','expected':'字段完整','observed':'字段缺失'},
            {'id':'unknown','name':'本地断言','status':'failed'},
        ], [{'id':'one','status':200,'response':{'text':'incomplete'}}]))
        html = render_report(result).decode()
        table=html.split('<table class="results-table">')[1].split('</table>')[0]
        self.assertIn('检查工具回填',table)
        self.assertIn('字段完整',table)
        self.assertIn('字段缺失',table)
        self.assertIn('1 次 / 0 次',table, 'failed assertion cannot invent a failed HTTP observation')
        self.assertIn('未记录关联',table)
        self.assertIn('linear-gradient(135deg,#1c3b5a', html)
        self.assertIn('报告目录',html)
        self.assertIn('#1e242c',html)
        self.assertNotIn('模型是真的', html)
        self.assertNotIn('高危问题', html)

    def test_uncovered_checks_are_visible_but_excluded_from_score_denominator(self):
        result = normalize_browser_report(payload([
            {'name':'工具通过','status':'passed','dimensions':['tools']},
            {'name':'工具无法判定','status':'inconclusive','dimensions':['tools']},
            {'name':'工具未覆盖','status':'not_covered','dimensions':['tools']},
            {'name':'工具已跳过','status':'skipped','dimensions':['tools']},
            {'name':'工具不适用','status':'failed','applicable':False,'dimensions':['tools']},
        ]))
        score = report_data(result)['score']
        dimension = next(row for row in score['dimensions'] if row['id']=='tools')
        module = next(row for row in score['modules'] if row['id']=='tools')
        self.assertEqual(dimension['score'],70)
        self.assertEqual(dimension['covered'],2)
        self.assertEqual(module['score'],70)
        self.assertEqual(dimension['counts']['skipped'],1)
        self.assertEqual(dimension['counts']['not_covered'],1)

class FullEvidenceTests(unittest.TestCase):
    def test_long_saved_evidence_remains_in_html_with_redaction_and_escaping(self):
        from report_renderer import raw_block, PREVIEW_LIMIT
        tail = '<script>TAIL_EVIDENCE_MARKER</script> Bearer private-token'
        html = raw_block('长响应', 'x'*(PREVIEW_LIMIT+1)+tail)
        self.assertIn('展开完整已采集内容', html)
        self.assertIn('TAIL_EVIDENCE_MARKER', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('private-token', html)
        self.assertIn('采集时已截断的数据无法由报告恢复', html)


class NativeRequestFieldsTests(unittest.TestCase):
    def test_duration_ms_and_native_usage_are_preserved_without_guessing_tokens(self):
        from browser_reports import request_record
        row = request_record({'durationMs': 1234, 'status': 200, 'response': {'usage': {'input_tokens': 4, 'output_tokens': 3, 'cache_read_input_tokens': 0}}}, 'r1', 'claude')
        self.assertEqual(row['duration_ms'], 1234)
        self.assertEqual(row['observed_fields']['input_tokens'], 4)
        self.assertEqual(row['observed_fields']['cache_read_input_tokens'], 0)
        self.assertNotIn('total_tokens', row['observed_fields'])
        gemini = request_record({'duration_ms': 5, 'durationMs': 100, 'response': {'usageMetadata': {'promptTokenCount': 9, 'candidatesTokenCount': 2, 'thoughtsTokenCount': 3, 'totalTokenCount': 14}}}, 'r2', 'gemini')
        self.assertEqual(gemini['duration_ms'], 5)
        self.assertEqual(gemini['observed_fields']['thoughtsTokenCount'], 3)
        self.assertEqual(gemini['observed_fields']['totalTokenCount'], 14)
        self.assertNotIn('completion_tokens', gemini['observed_fields'])
