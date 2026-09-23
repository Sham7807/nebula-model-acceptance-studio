"""Offline export contracts: readable evidence, safe markup, and honest counts."""
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from report_renderer import render_report, evidence_records


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__();self.scripts=0;self.event_attributes=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.scripts+=1
        self.event_attributes.extend(a for a,v in attrs if a.startswith('on'))


class ReportTests(unittest.TestCase):
    def test_vendor_text_is_escaped_and_credentials_redacted(self):
        hostile='</pre><script>alert(1)</script><img onerror="alert(2)">'
        result={'suite':'ccmax_acceptance','status':'completed','configuration':{'model':hostile,'key':'sk-'+'A'*30},
            'summary':{'total':1,'completed':1,'failed':1},'checks':[{'id':'signature','status':'failed'}],
            'samples':[{'id':'signature-1','probe':'signature','status':'failed','response':{'status':200,'body':hostile,'headers':[['Set-Cookie','private-session']]},
                        'request':{'body':{'api_key':'sk-'+'A'*30}},'assessments':[{'check':'signature','status':'failed','detail':hostile}]}]}
        report=render_report(result).decode();parser=Scripts();parser.feed(report)
        self.assertEqual(parser.scripts,1,'only the fixed report UI script executes')
        self.assertEqual(parser.event_attributes,[])
        self.assertNotIn('sk-'+'A'*30,report);self.assertNotIn('private-session',report)
        self.assertIn('&lt;script&gt;',report)
        self.assertIn('预期行为',report);self.assertIn('实际结果',report)
        self.assertNotIn('KVV 全套',report,'CCMax report scope must not claim it runs KVV')

    def test_kvv_raw_request_response_join_and_case_status_preserved(self):
        case='tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[assistant_hello]'
        result={'suite':'kvv11','status':'completed','summary':{'total':1,'completed':1,'failed':1},
            'cases':[{'id':case,'status':'failed','pytest_status':'failed','detail':'got 86, expected [99, 102]'}],
            'transport':{'request_count':1,'requests':[{'request_id':'r1','case_id':case,'http_status':200,'termination':'recorder_closed','status':'inconclusive','infrastructure_error':False}]}}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'requests.jsonl'
            path.write_text('\n'.join(json.dumps(x) for x in [
                {'type':'request_start','request_id':'r1','case_id':case,'body':'{"messages":[{"role":"user","content":"sample-prompt"}]}'},
                {'type':'request_finish','request_id':'r1','body':'{"usage":{"prompt_tokens":86}}','infrastructure_error':True}]))
            records=evidence_records(result,temp);report=render_report(result,temp).decode()
        self.assertEqual(records[0]['status'],'failed')
        self.assertFalse(records[0]['extra']['请求记录']['infrastructure_error'])
        self.assertIn('sample-prompt',report);self.assertIn('prompt_tokens',report)
        self.assertIn('id="request-1"',report);self.assertIn('href="#request-1"',report)
        self.assertIn('recorder_closed',report)

    def test_missing_raw_evidence_and_cancelled_run_are_explicit(self):
        result={'suite':'kvv11','status':'cancelled','summary':{'total':11,'completed':0},'cases':[],
            'transport':{'request_count':1,'requests':[{'request_id':'r1','case_id':'unknown','http_status':None,'status':'inconclusive'}]}}
        report=render_report(result).decode()
        self.assertIn('已取消',report);self.assertIn('本次记录未包含该证据',report)
        self.assertIn('0 / 11 pytest 项',report)
        self.assertNotIn('本轮已执行检查通过',report)

    def test_started_request_without_finish_is_preserved(self):
        result={'suite':'kvv11','status':'cancelled','cases':[],'transport':{'requests':[]}}
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'requests.jsonl').write_text(json.dumps({'type':'request_start','request_id':'r1','case_id':'c1','body':'sample'}))
            rows=evidence_records(result,temp)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'inconclusive')
        self.assertIn('缺少完成证据',rows[0]['notes'][0])


if __name__=='__main__':unittest.main()

class GPTReportTests(unittest.TestCase):
    def test_gpt_quality_and_token_panel_is_rendered_from_saved_evidence(self):
        payload = {'records': [{'kind': 'general', 'model': 'gpt-test', 'base': 'https://relay.invalid/v1',
            'result': {'checks': [{'name': 'GPT · HTML/SVG 降智与 Token 一致性', 'status': 'passed', 'result': '已生成'}],
                       'raw': {'gpt_evaluation': {'prompt': '生成html，内容是svg绘制鹈鹕骑自行车2D动画',
                           'html_detected': True, 'svg_detected': True, 'animation_detected': True, 'html_valid': True,
                           'token_usage': {'input': 10, 'output': 50, 'total': 60, 'consistent': True},
                           'signals': ['svg', 'requestAnimationFrame'], 'verdict': 'passed'}}}}]}
        from browser_reports import normalize_browser_report
        html = render_report(normalize_browser_report(payload)).decode()
        self.assertIn('GPT 生成质量与 Token 一致性', html)
        self.assertIn('输入 + 输出 = 总数', html)
        self.assertIn('requestAnimationFrame', html)
        self.assertIn('gpt-quality', html)

    def test_gpt_missing_tokens_are_explicitly_unrecorded(self):
        payload = {'records': [{'kind': 'general', 'model': 'gpt-test', 'result': {
            'checks': [{'name': 'GPT 专项', 'status': 'inconclusive'}],
            'raw': {'gpt_evaluation': {'html_detected': True, 'svg_detected': False}}}}]}
        from browser_reports import normalize_browser_report
        html = render_report(normalize_browser_report(payload)).decode()
        self.assertIn('输入 tokens</th><td>未记录', html)
        self.assertIn('SVG 输出</th><td><span class="badge failed">未通过', html)

    def test_gpt_derived_or_inapplicable_total_is_not_verified(self):
        from browser_reports import normalize_browser_report
        for flag in ({'total_derived': True}, {'total_tokens_derived': True}, {'accounting_applicable': False}):
            for consistent in (None, True):
                with self.subTest(flag=flag, consistent=consistent):
                    payload = {'records': [{'kind': 'general', 'model': 'old-gpt-usage', 'result': {
                        'checks': [{'name': 'GPT usage', 'status': 'inconclusive'}],
                        'gpt_evaluation': {'token_usage': {'input': 10, 'output': 20, 'total': 30, 'consistent': consistent, **flag}}}}]}
                    html = render_report(normalize_browser_report(payload)).decode()
                    self.assertIn('未验证渠道上报总量', html)
                    self.assertNotIn('输入 + 输出 = 总数</th><td><span class="badge passed">', html)
                    if flag.get('accounting_applicable') is False:
                        self.assertIn('不适用 · 未验证总量', html)
                    else:
                        self.assertIn('总 tokens</th><td>30（派生）', html)
                        self.assertIn('派生值 · 未验证总量', html)
