"""Offline end-to-end wiring for selectable request formats and honest reports."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import kvv_runner
import server
from acceptance_results import decorate
from report_content import build_report_data
from report_renderer import render_report
from test_kvv_openai_cases import MockChannel


class FormatProfileTests(unittest.TestCase):
    def test_validation_keeps_protocol_separate_from_auth(self):
        base={'base':'https://fixture.test/v1','key':'fixture-key','model':'fixture-model'}
        cc=server.validate({**base,'suite':'ccmax','request_format':'openai','auth':'anthropic'})
        self.assertEqual(cc['request_format'],'openai')
        self.assertEqual(cc['auth'],'bearer')
        kvv=server.validate({**base,'suite':'kvv11','request_format':'openai'})
        self.assertEqual(kvv['think_mode'],'openai')
        self.assertEqual(kvv['request_format'],'openai')
        self.assertEqual(server.validate({**base,'suite':'ccmax'})['request_format'],'anthropic')
        for extra in ({'request_format':'typo'}, {'request_format':'native','think_mode':'openai'}):
            with self.assertRaises(ValueError):server.validate({**base,'suite':'kvv11',**extra})

    def test_openai_command_never_runs_native_k3_or_token_groundtruth(self):
        for suite in ('kvv11','kvvfull'):
            command=kvv_runner.command({'suite':suite,'model':'kimi-k3','think_mode':'openai'},Path('/tmp/offline'))
            self.assertTrue(any('kvv_openai_cases.py' in arg for arg in command))
            self.assertFalse(any('tests/params' in arg or 'tests/k3_features' in arg or 'tests/prompt_tokens' in arg for arg in command))
            if suite=='kvv11':
                self.assertEqual(sum('kvv_openai_cases.py::' in arg for arg in command),11)
                self.assertNotIn('--think-mode',command)
            else:
                self.assertIn('tests/tool_call_json_schema',command)
                self.assertEqual(command[command.index('--think-mode')+1],'none')

    def test_real_runner_precheck_and_export_preserve_observations(self):
        channel=MockChannel()
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                request=httpx.Request('POST','http://127.0.0.1'+self.path,content=self.rfile.read(int(self.headers['Content-Length'])))
                reply=channel(request)
                self.send_response(reply.status_code)
                for name,value in reply.headers.items():self.send_header(name,value)
                self.end_headers();self.wfile.write(reply.content)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                config={'suite':'kvv11','base':f'http://127.0.0.1:{provider.server_port}/prefix/v1','key':'fixture-private-key','model':'kimi-k3','think_mode':'openai','request_format':'openai','timeout':5}
                result=kvv_runner.run(config,lambda e:None,lambda:False,Path(directory))
                self.assertEqual(result['status'],'completed',result.get('log'))
                self.assertEqual(result['summary']['total'],11)
                self.assertEqual(result['summary']['passed'],11,result.get('log'))
                self.assertEqual(result['transport']['request_count'],12)
                self.assertTrue(all(case.get('title') for case in result['cases']))
                self.assertTrue(any(case.get('observations') for case in result['cases']))
                result['configuration']={k:v for k,v in config.items() if k!='key'}
                decorate(result)
                self.assertEqual(result['verdict']['status'],'passed')
                report=build_report_data(result)
                self.assertIn('OpenAI',report['title'])
                self.assertEqual(len(report['checks']),11)
                self.assertTrue(all(check['metadata']['source']=='workbench-openai-compat' for check in report['checks']))
                self.assertTrue(any('本轮实测' in check['observed'] for check in report['checks']))
                output=render_report(result,Path(directory)).decode()
                self.assertIn('OpenAI Chat Completions',output)
                self.assertIn('不验证 Kimi 原生',output)
                self.assertNotIn('fixture-private-key',output)
                self.assertNotIn('仅影响适用用例',output)
        finally:provider.shutdown();provider.server_close()

    def test_unsupported_compatible_capabilities_are_not_passed(self):
        cases=[{'id':'kvv_openai_cases.py::test_openai_cache_repeat','status':'skipped','detail':'能力未验证：本轮没有缓存命中'}]
        kvv_runner.classify_cases(cases,{})
        self.assertEqual(cases[0]['pytest_status'],'skipped')
        self.assertEqual(cases[0]['status'],'not_covered')
        result={'suite':'kvv11','status':'completed','request_format':'openai','summary':{'total':1,'completed':1},'cases':cases}
        self.assertEqual(decorate(result)['verdict']['status'],'inconclusive')

    def test_native_cc_report_is_not_changed_by_hidden_kvv_setting(self):
        result={'suite':'ccmax_acceptance','status':'completed','configuration':{'request_format':'anthropic','think_mode':'openai'},'checks':[]}
        report=build_report_data(result)
        self.assertEqual(report['title'],'CCMax渠道验收报告')
        self.assertTrue(any('Anthropic Messages' in item for item in report['focus']))

    def test_non_applicable_signature_is_visible_but_not_scored(self):
        checks=[{'id':name,'status':'passed','label':name,'title':name,'applicable':True} for name in ('message_start','message_stop','connection','stream_error','error_format','usage_cache','tool_stream','prompt_injection','instruction_hierarchy','behavioral_consistency','parameter_validation')]
        checks.append({'id':'signature','status':'skipped','applicable':False,'title':'签名校验（不适用）','skip_reason':'Chat Completions 没有原生签名契约'})
        result={'suite':'ccmax_acceptance','status':'completed','configuration':{'request_format':'openai'},'summary':{'total':0,'completed':0},'checks':checks}
        self.assertEqual(decorate(result)['verdict']['not_applicable'],1)
        self.assertEqual(result['verdict']['status'],'passed')
        report=build_report_data(result)
        signature=next(c for c in report['checks'] if c['id']=='signature')
        self.assertFalse(signature['applicable'])
        self.assertIn('没有原生签名契约',signature['observed'])
        protocol=next(d for d in report['score']['dimensions'] if d['id']=='protocol')
        self.assertEqual(protocol['score'],100)
        self.assertNotIn('signature',protocol['check_ids'])


if __name__=='__main__':unittest.main()
