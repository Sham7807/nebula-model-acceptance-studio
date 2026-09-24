"""Cross-suite matrix dispatch, redaction and preview. No provider traffic."""
import copy
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import acceptance_matrix
import ccmax_acceptance
import claude_acceptance
import server
from auth_history import Store


class MatrixServiceTests(unittest.TestCase):
    def config(self, suite, **values):
        return server.validate({'suite':suite,'base':'https://fixture.invalid/v1',
            'model':'fixture-model','key':'fixture-private-key','matrix_profile':'standard',
            'matrix_modules':['max_tokens'],**values})

    def test_configuration_rejects_unknown_matrix_profiles_and_dimensions(self):
        for values in ({'matrix_profile':'huge'},{'matrix_modules':[]},
                       {'matrix_modules':['max_tokens','typo']},{'matrix_modules':'tools'}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                self.config('ccmax',**values)
        self.assertEqual(self.config('ccmax',matrix_modules=['tools','tools'])['matrix_modules'],['tools'])

    def exercise(self, suite, matrix_error=False, cancel=False, off=False):
        config=self.config(suite,matrix_profile='off' if off else 'standard')
        native_case={'id':'native-fixture','module':'protocol','dimensions':['protocol'],
                     'title':'原专项响应','status':'passed','request_ids':['native-fixture']}
        native={'suite':suite,'status':'completed','cases':[native_case],
                'samples':[{'id':'native-fixture','response':{'body':'fixture-private-key'}}],
                'summary':{'total':1,'completed':1,'passed':1},'transport':{'request_count':1}}
        matrix={'cases':[{'id':'matrix-cap-10','module':'max_tokens','dimensions':['max_tokens'],
                         'title':'10 Token 截断','status':'failed','reason_code':'assertion_failed',
                         'parameters':{'max_tokens':10},'request_ids':['matrix-cap-10']}],
                'samples':[{'id':'matrix-cap-10','response':{'body':'fixture-private-key'}}],
                'summary':{'total':1,'completed':1,'failed':1,'request_count':1}}
        with tempfile.TemporaryDirectory() as temporary,ExitStack() as stack:
            root=Path(temporary)
            job={'id':'f'*32,'suite':suite,'model':config['model'],'status':'running',
                 'started_at':time.time(),'completed':0,'events':[],'cancel':threading.Event()}
            stack.enter_context(patch.object(server,'REPORTS',root/'reports'))
            stack.enter_context(patch.object(server,'AUTH_STORE',Store(root/'history.sqlite3')))
            stack.enter_context(patch.object(server,'report_html',return_value=b'<html>fixture</html>'))
            def base_runner(c,emit,cancelled,*args):
                emit({'type':'progress','completed':1,'total':1,'request_count':1})
                if cancel:job['cancel'].set()
                return copy.deepcopy(native)
            target=claude_acceptance if suite=='claude' else ccmax_acceptance if suite=='ccmax' else server.kvv_runner
            stack.enter_context(patch.object(target,'run',side_effect=base_runner))
            def matrix_runner(c,emit,cancelled):
                self.assertEqual(c['key'],'fixture-private-key')
                self.assertEqual(c['matrix_modules'],['max_tokens'])
                if matrix_error:raise RuntimeError('fixture-private-key execution fault')
                if cancelled():return {'cases':[],'samples':[],'status':'cancelled','summary':{'request_count':0}}
                emit({'type':'progress','completed':1,'total':1,'request_count':1,'message':'matrix done'})
                return copy.deepcopy(matrix)
            runner=stack.enter_context(patch.object(acceptance_matrix,'run',side_effect=matrix_runner,create=True))
            server.run_job(job,config)
            self.assertEqual(config['key'],'')
            stored=(root/'reports'/job['id']/'report.json').read_text()
            self.assertNotIn('fixture-private-key',stored)
            self.assertTrue(job['history_saved'])
            result=job['result']
            self.assertEqual(result['cases'][0]['id'],'native-fixture')
            if off:
                runner.assert_not_called();self.assertNotIn('matrix_validation',result)
            else:
                runner.assert_called_once()
                self.assertEqual(result['native_summary']['passed'],1)
                self.assertEqual(result['configuration']['matrix_profile'],'standard')
                if cancel:
                    self.assertEqual(result['status'],'cancelled')
                    self.assertEqual(result['transport']['request_count'],1)
                elif matrix_error:
                    self.assertEqual(result['matrix_validation']['cases'][0]['reason_code'],'execution_error')
                    self.assertEqual(result['transport']['request_count'],1)
                else:
                    self.assertEqual(result['summary']['failed'],1)
                    self.assertEqual(result['transport']['request_count'],2)
                    self.assertEqual(result['verdict']['status'],'failed')
                    self.assertEqual(job['events'][-1]['completed'],2)
                    self.assertEqual(job['events'][-1]['total'],2)
                    self.assertEqual(job['events'][-1]['request_count'],2)

    def test_all_suites_preserve_native_and_matrix_evidence(self):
        for suite in ('ccmax','claude','kvv11','kvvfull'):
            with self.subTest(suite=suite):self.exercise(suite)

    def test_matrix_failure_does_not_destroy_native_result(self):self.exercise('claude',matrix_error=True)
    def test_cancellation_is_shared_and_preserves_completed_evidence(self):self.exercise('kvv11',cancel=True)
    def test_legacy_api_without_matrix_does_not_add_calls(self):self.exercise('ccmax',off=True)

    def test_real_keyless_previews_for_all_suite_protocols(self):
        for suite,fmt in (('ccmax','anthropic'),('claude','anthropic'),('kvv11','native'),
                          ('kvvfull','native'),('kvv11','openai'),('ccmax','openai')):
            with self.subTest(suite=suite,fmt=fmt):
                cfg=self.config(suite,request_format=fmt,think_mode='openai' if fmt=='openai' else 'kimi')
                cfg['key']=''
                plan=server.acceptance_plan(cfg)
                caps=[x for x in plan['requests'] if x['id'].startswith('matrix-cap-')]
                self.assertEqual(len(caps),12)
                self.assertEqual({x['body']['max_tokens'] for x in caps},{1,10,20})
                self.assertTrue(plan['matrix_only'])
                expected='/messages' if fmt=='anthropic' else '/chat/completions'
                self.assertTrue(all(x['url'].endswith(expected) for x in caps))
                self.assertNotIn('fixture-private-key',json.dumps(plan))

    def test_full_endpoints_and_explicit_versions_are_shared_by_suites(self):
        for suite in ('ccmax','claude','kvv11','kvvfull'):
            for base in ('https://fixture.invalid/relay/v2','https://fixture.invalid/relay/v2/messages',
                         'https://fixture.invalid/relay/v2/chat/completions'):
                with self.subTest(suite=suite,base=base):
                    cfg=self.config(suite,base=base)
                    plan=server.acceptance_plan(cfg)
                    suffix='messages' if suite in ('ccmax','claude') else 'chat/completions'
                    self.assertEqual({r['url'] for r in plan['requests']},{'https://fixture.invalid/relay/v2/'+suffix})
                    if suite=='ccmax':
                        self.assertEqual(ccmax_acceptance._endpoint(cfg['base'],cfg['request_format']),next(iter(plan['requests']))['url'])
                    if suite.startswith('kvv'):
                        self.assertEqual(cfg['base'],'https://fixture.invalid/relay/v2')
            for endpoint in ('messages','chat/completions'):
                cfg=self.config(suite,base='https://fixture.invalid/relay/'+endpoint)
                plan=server.acceptance_plan(cfg)
                suffix='messages' if suite in ('ccmax','claude') else 'chat/completions'
                self.assertEqual({r['url'] for r in plan['requests']},{'https://fixture.invalid/relay/'+suffix})


if __name__=='__main__':unittest.main()
