"""Offline integration of admission, production evidence and durable billing."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import server
from auth_history import Store
from report_content import build_report_data
from report_renderer import render_report, evidence_records
from test_channel_admission import report, ledger


class ProductionServiceTests(unittest.TestCase):
    def test_config_and_preview_share_budget_with_no_provider_calls(self):
        for suite in ('claude','ccmax','kvv11'):
            config=server.validate({'suite':suite,'base':'https://fixture.invalid/v1','key':'fixture','model':'model',
                                    'production':{'profile':'screening'},'admission':{'min_samples':300}})
            self.assertTrue(config['production']['enabled'])
            self.assertEqual(config['admission']['min_samples'],300)
            plan=server.acceptance_plan(config,include_native=suite=='claude')
            self.assertEqual(plan['production_request_count'],60)
            self.assertTrue(plan['production']['enabled'])
            self.assertGreaterEqual(plan['request_count'],60)
            self.assertNotIn('fixture-private-key',json.dumps(plan))
        for field in ({'pricing':{'input_per_million':-1}},{'production':{'profile':'custom','max_requests':10001}},{'admission':{'target_success_rate':101}}):
            with self.assertRaises(ValueError): server.validate({'suite':'claude','base':'https://fixture.invalid','key':'fixture','model':'model',**field})

    def test_bad_native_baseline_stops_additional_spend(self):
        config=server.validate({'suite':'claude','base':'https://fixture.invalid','key':'fixture','model':'model','production':{'profile':'screening'}})
        result={'samples':[{'id':'baseline','status':'failed'}]}
        job={'cancel':threading.Event()}
        with patch('channel_production.run') as run:
            server.attach_production(result,config,job,lambda e:None)
            run.assert_not_called()
        self.assertEqual(result['production_validation']['status'],'blocked')

    def test_progress_and_request_counts_do_not_reset_between_phases(self):
        result={'samples':[{'id':'baseline','status':'passed'}],'transport':{'request_count':12},'summary':{'request_count':12}}
        events=[]
        def run(config,emit,cancelled):
            emit({'type':'request_start','completed':0,'total':2})
            emit({'type':'progress','completed':1,'total':2,'request_count':1})
            return {'status':'completed','samples':[{'id':'prod'}]}
        with patch('channel_production.run',side_effect=run):
            server.attach_production(result,{'suite':'claude','key':'fixture','production':{'enabled':True}},{'cancel':threading.Event(),'completed':5},events.append)
        self.assertEqual(events[-1]['request_count'],13)
        self.assertEqual(events[-1]['completed'],6)
        self.assertEqual(result['transport']['request_count'],13)

    def test_report_production_evidence_links_and_independent_decision(self):
        value=report(4); value['production_validation']['cases']=[{'id':'production-stream','title':'stream','status':'failed','sample_ids':['prod-0'],'dimensions':['reliability']}]
        data=build_report_data(value)
        self.assertNotEqual(data['admission']['status'],'approved')
        self.assertFalse(next(c for c in data['checks'] if c['id']=='production-stream')['score_applicable'])
        ids=[r['id'] for r in evidence_records(value)]
        self.assertEqual(ids.count('prod-0'),1)
        text=render_report(value).decode()
        self.assertIn('id="admission"',text)
        self.assertIn('id="cost"',text)
        self.assertLess(text.index('id="production"'),text.index('id="all-results"'))
        batch={'suite':'batch_acceptance','status':'completed','results':[{'model':'one','result':value},{'model':'two','result':copy.deepcopy(value)}]}
        self.assertIn('model-2-prod-0',[r['id'] for r in evidence_records(batch)])
        self.assertEqual(len(build_report_data(batch)['model_admissions']),2)

    def test_billing_auth_validation_history_and_report_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'history.sqlite3')
            result=report(4); result.update(run_id='abcd1234',started_at=time.time()-10,finished_at=time.time())
            jobs={'abcd1234':{'id':'abcd1234','status':'completed','result':result,'started_at':result['started_at']}}
            with patch.object(server,'AUTH_STORE',store),patch.object(server,'REPORTS',Path(tmp)/'reports'),patch.object(server,'JOBS',jobs):
                saved=store.save_acceptance(result)
                service=server.WorkbenchServer(('127.0.0.1',0),server.Handler)
                worker=threading.Thread(target=service.serve_forever,daemon=True);worker.start()
                try:
                    base=f'http://127.0.0.1:{service.server_port}'
                    with httpx.Client(base_url=base,trust_env=False) as client:
                        url='/api/runs/abcd1234/billing'; body={'billing':{'rows':ledger(result,amount=0.00000001)}}
                        self.assertEqual(client.post(url,json=body).status_code,403)
                        headers={'X-Workbench-Token':server.TOKEN}
                        preview={'suite':'claude','base':'https://fixture.invalid/v1','model':'model','production':{'profile':'screening','workloads':['custom'],
                                  'custom_cases':[{'name':'long-business','body':{'messages':[{'role':'user','content':'x'*70000}]},'expected_text':'OK'}]}}
                        self.assertEqual(client.post('/api/claude/plan',headers=headers,json=preview).status_code,200)
                        template=client.get('/api/runs/abcd1234/cost-template',headers=headers).json()
                        self.assertIsNone(template['billing']['rows'][0]['amount'])
                        bad=copy.deepcopy(body);bad['billing']['rows'][0]['request_id']='foreign'
                        self.assertEqual(client.post(url,headers=headers,json=bad).status_code,400)
                        response=client.post(url,headers=headers,json=body)
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(response.json()['cost']['status'],'actual')
                        stored=store.detail(saved['id'])['result']
                        self.assertEqual(stored['billing']['rows'][0]['amount'],0.00000001)
                        disk=json.loads((Path(tmp)/'reports/abcd1234/report.json').read_text())
                        self.assertEqual(disk['billing'],stored['billing'])
                        html=client.get('/api/history/'+saved['id']+'/report.html',headers=headers).text
                        self.assertIn('1e-08 CNY',html)
                        self.assertEqual(client.post('/api/history/'+saved['id']+'/billing',headers=headers,json=body).status_code,200)
                finally: service.shutdown();service.server_close();worker.join()

if __name__=='__main__':unittest.main()
