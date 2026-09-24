"""Offline desktop engine integration checks: no paid upstream requests."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'desktop/engine/desktop_service.py'

class Engine:
    def __init__(self, folder, workspace=ROOT, python=sys.executable):
        self.process = subprocess.Popen([str(python), '-B', '-u', str(RUNNER), '--workspace', str(workspace), '--data', str(folder)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not select.select([self.process.stdout], [], [], 20)[0]:
            self.close(); raise RuntimeError('Engine startup timed out')
        line = self.process.stdout.readline()
        if not line:
            # Startup exceptions contain no request credentials.
            raise RuntimeError(self.process.stderr.read().decode()[-2000:])
        self.session = json.loads(line)
    def request(self,path,method='GET',body=None,auth=True,csrf=True):
        headers={}
        if auth: headers['Cookie']=self.session['cookieName']+'='+self.session['cookie']
        if csrf: headers['X-Workbench-Token']=self.session['csrf']
        if body is not None: headers['Content-Type']='application/json'
        req=urllib.request.Request(self.session['url']+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=5) as r: return r.status,r.read()
        except urllib.error.HTTPError as r: return r.code,r.read()
    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait();raise
        self.process.stdout.close();self.process.stderr.close()

class DesktopEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='nebula-engine-test-')
        self.engine=Engine(self.tmp.name)
    def tearDown(self):
        self.engine.close();self.tmp.cleanup()
    def test_authentication_and_csrf(self):
        self.assertEqual(self.engine.request('/api/history',auth=False)[0],401)
        self.assertEqual(self.engine.request('/api/history',csrf=False)[0],403)
        self.assertEqual(self.engine.request('/api/session')[0],200)
        self.assertEqual(self.engine.request('/api/history')[0],200)
        self.assertEqual(self.engine.request('/')[0],200)
    def test_private_storage_and_history_survives_restart(self):
        payload={'client_id':'desktop-qa-fixture','source':'basic','kind':'text','title':'Desktop QA','model':'fixture-only','status':'passed','prompt':'offline fixture','results':[{'text':'OK'}]}
        status,body=self.engine.request('/api/history','POST',payload)
        self.assertEqual(status,200,body[:200])
        old=self.engine.session
        self.engine.close();self.engine=Engine(self.tmp.name)
        status,body=self.engine.request('/api/history?limit=5&offset=0')
        self.assertEqual(status,200)
        page=json.loads(body)
        self.assertEqual(page['stats']['total'],1)
        self.assertEqual(page['items'][0]['model'],'fixture-only')
        self.assertNotEqual(old['cookie'],self.engine.session['cookie'])
        self.assertEqual(os.stat(self.tmp.name).st_mode & 0o777,0o700)
        self.assertEqual(os.stat(Path(self.tmp.name)/'history.sqlite3').st_mode & 0o077,0)
    def test_independent_ports_and_eof_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            other=Engine(folder)
            try:self.assertNotEqual(self.engine.session['url'],other.session['url'])
            finally:other.close()
            self.assertEqual(other.process.returncode,0)
    def test_duplicate_engine_preserves_original_session(self):
        other = Engine(self.tmp.name)
        try:
            self.assertEqual(other.session['type'], 'error')
            self.assertEqual(self.engine.request('/api/history')[0], 200)
        finally: other.close()
    def test_bundled_runtime_and_engine(self):
        resources=ROOT/'desktop/dist/小小宇宙无敌.app/Contents/Resources'
        if not resources.exists():self.skipTest('App not built yet')
        with tempfile.TemporaryDirectory() as folder:
            other=Engine(folder,workspace=resources/'Workbench',python=resources/'Python/bin/python3.12')
            try:
                self.assertEqual(other.request('/api/history')[0],200)
                payload={'records':[{'kind':'general','model':'desktop-report-fixture',
                    'created_at':1700000000,'duration_ms':2000,'result':{
                        'checks':[{'id':'protocol','name':'协议响应','status':'passed','dimensions':['protocol'],'request_ids':['r1']}],
                        'requests':[{'id':'r1','status':200,'duration_ms':1200,
                            'response_body':{'choices':[{'message':{'content':'OK'}}]}}]}}]}
                status,body=other.request('/api/reports','POST',payload)
                self.assertEqual(status,200,body[:200])
                html=body.decode()
                for phrase in ('desktop-report-fixture','验收模块总览','测试总耗时','请求耗时 P50','2 秒'):
                    self.assertIn(phrase,html)
                # A single successful sample must keep the new grade provisional.
                overview=html.split('id="overview"',1)[1].split('id="modules"',1)[0]
                for phrase in ('本轮资源评级','优质资源','暂定 · 证据待完善'):
                    self.assertIn(phrase,overview)
                self.assertLess(html.index('id="overview"'),html.index('id="modules"'))
                self.assertLess(html.index('id="modules"'),html.index('id="all-results"'))
                status,css=other.request('/report-theme.css')
                self.assertEqual(status,200)
                self.assertEqual(css,(ROOT/'multimodal-workbench/report-theme.css').read_bytes())
            finally:other.close()

if __name__=='__main__':unittest.main()
