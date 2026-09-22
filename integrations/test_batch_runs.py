"""Offline batch orchestration regressions; no provider requests are made."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class Store:
    def __init__(self): self.saved=[]
    def save_acceptance(self, result, **kwargs):
        self.saved.append(result)
        return {'id':'history-'+result['run_id']}


class BatchRunTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store()
        self.patches=[patch.object(server,'REPORTS',Path(self.temp.name)),
                      patch.object(server,'QUEUE_META',Path(self.temp.name)/'.jobs.json'),
                      patch.object(server,'AUTH_STORE',self.store),
                      patch.object(server,'JOBS',{}),
                      patch.object(server,'report_html',return_value=b'<html>offline report</html>')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def config(self, models):
        return server.validate({'suite':'kvv11','base':'https://fixture.test','key':'batch-private-key',
                                'models':models,'timeout':5})

    def parent(self, models):
        job={'id':'a'*32,'suite':'kvv11','model':f'多模型对比（{len(models)}）','base':'https://fixture.test/v1',
             'batch':True,'models':models,'status':'running','started_at':time.time(),'total':len(models),
             'completed':0,'events':[],'cancel':threading.Event(),'current_run':None,
             'children':[{'model':m,'status':'pending','completed':0,'total':11,'summary':{}} for m in models]}
        server.JOBS[job['id']]=job
        return job

    @staticmethod
    def result(model):
        status='failed' if model=='bad' else 'passed'
        return {'suite':'kvv11','status':'completed','summary':{'total':1,'completed':1,'passed':int(status=='passed'),
               'failed':int(status=='failed')},'cases':[{'id':'offline-basic','status':status}]}

    def test_selected_models_validate_deduplicate_and_limit(self):
        self.assertEqual(self.config(['a','a',' b '])['models'],['a','b'])
        self.assertEqual(len(self.config([str(i) for i in range(30)])['models']),30)
        for models in [[],[str(i) for i in range(31)],'a,b']:
            with self.assertRaises(ValueError): self.config(models)

    def test_serial_children_independent_history_reports_and_key_redaction(self):
        parent=self.parent(['good','bad']);config=self.config(parent['models']);calls=[]
        def runner(c,emit,cancelled,directory):
            self.assertEqual(c['key'],'batch-private-key')
            calls.append(c['model']);emit({'completed':1,'total':1})
            value=self.result(c['model']);value['log']='echo batch-private-key'
            return value
        with patch.object(server.kvv_runner,'run',side_effect=runner): server.run_batch(parent,config)
        self.assertEqual(calls,['good','bad']);self.assertEqual(config['key'],'')
        self.assertEqual(len(self.store.saved),3)
        self.assertEqual(parent['summary']['passed'],1);self.assertEqual(parent['summary']['failed'],1)
        self.assertEqual(parent['result']['verdict']['status'],'failed')
        for child in parent['children']:
            self.assertTrue((server.REPORTS/child['id']/'report.json').exists())
        for path in server.REPORTS.rglob('*'):
            if path.is_file():self.assertNotIn('batch-private-key',path.read_text())
        self.assertNotIn('batch-private-key',json.dumps(server.snapshot(parent)))

    def test_cancel_keeps_completed_child_and_never_starts_later_models(self):
        parent=self.parent(['one','two','three']);calls=[]
        def runner(c,emit,cancelled,directory):
            calls.append(c['model']);parent['cancel'].set()
            return self.result(c['model'])
        with patch.object(server.kvv_runner,'run',side_effect=runner): server.run_batch(parent,self.config(parent['models']))
        self.assertEqual(calls,['one']);self.assertEqual(parent['status'],'cancelled')
        self.assertEqual([x['status'] for x in parent['children']],['passed','not_run','not_run'])
        self.assertEqual(parent['summary']['not_run'],2)
        self.assertEqual(parent['result']['verdict']['status'],'inconclusive')

    def test_restart_marks_queue_incomplete_without_running_provider(self):
        parent=self.parent(['one','two']);parent['children'][0]['status']='running';server._persist_jobs()
        server.JOBS.clear()
        with patch.object(server.kvv_runner,'run') as runner:
            server.restore_reports();runner.assert_not_called()
        restored=server.JOBS[parent['id']]
        self.assertEqual(restored['status'],'inconclusive')
        self.assertEqual([x['status'] for x in restored['children']],['not_run','not_run'])
        self.assertNotIn('batch-private-key',json.dumps(server.snapshot(restored)))


if __name__=='__main__': unittest.main()
