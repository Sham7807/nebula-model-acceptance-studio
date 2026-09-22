"""Offline integration checks. All API traffic stays on an ephemeral loopback server."""
import json
import io
import zipfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
import kvv_runner
import server

class RejectProvider(BaseHTTPRequestHandler):
    count=0
    status=401
    def log_message(self,*args):pass
    def do_POST(self):
        type(self).count+=1
        length=int(self.headers.get('Content-Length',0));self.rfile.read(length)
        payload=json.dumps({'error':{'type':'authentication_error','message':'offline key rejected'}}).encode()
        self.send_response(self.status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)

class ServiceTests(unittest.TestCase):
    def test_model_discovery_service_auth_validation_and_errors(self):
        from auth_history import Store, hash_password
        import channel_discovery
        with tempfile.TemporaryDirectory() as temp:
            auth_file=Path(temp)/'auth.json'
            auth_file.write_text(json.dumps({'username':'fixture','password_hash':hash_password('fixture-password')}))
            store=Store(Path(temp)/'history.sqlite3',auth_file)
            with patch.object(server,'AUTH_STORE',store), patch.dict('os.environ',{'WORKBENCH_COOKIE_SECURE':'0'}):
                srv=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                threading.Thread(target=srv.serve_forever,daemon=True).start()
                try:
                    url=f'http://127.0.0.1:{srv.server_port}'
                    with httpx.Client(trust_env=False) as client:
                        payload={'base':'https://relay.test/v1','key':'fixture-secret','auth':'bearer'}
                        self.assertEqual(client.post(url+'/api/models',json=payload).status_code,401)
                        self.assertEqual(client.post(url+'/api/auth/login',json={'username':'fixture','password':'fixture-password'}).status_code,200)
                        token=client.get(url+'/api/session').json()['token']
                        headers={'X-Workbench-Token':token}
                        self.assertEqual(client.post(url+'/api/models',json=payload).status_code,403)
                        self.assertEqual(client.post(url+'/api/models',headers={**headers,'Origin':'https://untrusted.test'},json=payload).status_code,403)
                        for body in [[], 'invalid', {'base':'file:///tmp/models','key':'fixture-secret'}, {**payload,'key':'bad\nheader'}]:
                            response=client.post(url+'/api/models',headers=headers,json=body)
                            self.assertEqual(response.status_code,400)
                            self.assertNotIn('fixture-secret',response.text)
                        for exception, expected in [(httpx.ReadTimeout('fixture-secret'), '超时'), (httpx.ConnectError('fixture-secret'), '无法连接'), (ValueError('upstream fixture-secret'), '[已隐藏]')]:
                            with patch.object(channel_discovery,'fetch_models',side_effect=exception):
                                response=client.post(url+'/api/models',headers=headers,json=payload)
                            self.assertEqual(response.status_code,400)
                            self.assertIn(expected,response.json()['error'])
                            self.assertNotIn('fixture-secret',response.text)
                        for auth in ['bearer','anthropic','gemini','none']:
                            body={**payload,'auth':auth,'key':'' if auth=='none' else 'fixture-secret'}
                            with patch.object(channel_discovery,'fetch_models',return_value={'models':['fixture-model'],'total':1}) as fetch:
                                response=client.post(url+'/api/models',headers=headers,json=body)
                            self.assertEqual(response.status_code,200)
                            self.assertEqual(response.json(),{'models':['fixture-model'],'total':1})
                            fetch.assert_called_once_with(body['base'],body['key'],auth)
                finally:srv.shutdown();srv.server_close()

    def test_proxy_forwards_json_and_multipart_without_cors(self):
        class Provider(BaseHTTPRequestHandler):
            requests=[]
            def log_message(self,*args): pass
            def do_POST(self):
                length=int(self.headers.get('Content-Length','0')); body=self.rfile.read(length)
                type(self).requests.append((self.headers.get('Content-Type',''), body))
                payload=json.dumps({'choices':[{'message':{'content':'proxy-ok'}}]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider); threading.Thread(target=provider.serve_forever,daemon=True).start()
        service=ThreadingHTTPServer(('127.0.0.1',0),server.Handler); threading.Thread(target=service.serve_forever,daemon=True).start()
        try:
            with httpx.Client(trust_env=False) as client:
                base=f'http://127.0.0.1:{service.server_port}'; token=client.get(base+'/api/session').json()['token']; headers={'X-Workbench-Token':token}
                target=f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
                response=client.post(base+'/api/proxy',headers=headers,json={'url':target,'method':'POST','headers':{'Authorization':'Bearer [hidden]','Content-Type':'application/json'},'body':'{"hello":"world"}'})
                self.assertEqual(response.status_code,200); self.assertEqual(response.json()['status'],200); self.assertIn('proxy-ok',response.json()['text'])
                self.assertEqual(len(Provider.requests),1); self.assertIn(b'hello',Provider.requests[0][1])
                response=client.post(base+'/api/proxy',headers=headers,json={'url':target,'method':'POST','form':{'fields':{'model':'fixture'},'files':[{'field':'file','name':'a.bin','type':'application/octet-stream','data':'AAE='}]}})
                self.assertEqual(response.status_code,200); self.assertEqual(len(Provider.requests),2); self.assertIn(b'a.bin',Provider.requests[1][1]); self.assertIn(b'\x00\x01',Provider.requests[1][1])
        finally: provider.shutdown(); provider.server_close(); service.shutdown(); service.server_close()

    def test_normalization_validation(self):
        self.assertEqual(server.normalized_base('https://relay.test/prefix/'),'https://relay.test/prefix/v1')
        for value in ['file:///tmp/foo','https://key@relay.test','https://relay.test?api_key=x']:
            with self.assertRaises(ValueError): server.normalized_base(value)
        with self.assertRaises(ValueError):server.validate({'suite':'unknown'})
        self.assertNotIn('secret',json.dumps(server.clean({'authorization':'Bearer secret','detail':'secret'},'secret')))

    def test_local_service_origin_and_credentials(self):
        srv=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        try:
            url=f'http://127.0.0.1:{srv.server_port}'
            with httpx.Client(trust_env=False) as client:
                self.assertEqual(client.get(url+'/').status_code,200)
                token=client.get(url+'/api/session').json()['token']
                self.assertEqual(client.post(url+'/api/runs',json={}).status_code,403)
                self.assertEqual(client.post(url+'/api/runs',headers={'X-Workbench-Token':token,'Origin':'https://untrusted.test'},json={}).status_code,403)
                self.assertEqual(client.post(url+'/api/runs',headers={'X-Workbench-Token':token},json={}).status_code,400)
                self.assertEqual(client.get(url+'/../integrations/server.py').status_code,404)
                self.assertEqual(client.get(url+'/',headers={'Host':'untrusted.test'}).status_code,403)
        finally:srv.shutdown();srv.server_close()

    def test_official_precheck_and_no_false_pass_on_auth(self):
        RejectProvider.count=0
        RejectProvider.status=401
        srv=ThreadingHTTPServer(('127.0.0.1',0),RejectProvider)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        config={'suite':'kvv11','key':'offline-key','model':'offline-model','base':f'http://127.0.0.1:{srv.server_port}/v1','timeout':5,'think_mode':'kimi'}
        events=[]
        try:
            with tempfile.TemporaryDirectory() as temp:
                result=kvv_runner.run(config,events.append,lambda:False,Path(temp))
            self.assertEqual(result['summary']['total'],11)
            self.assertEqual(result['summary']['completed'],11)
            self.assertEqual(result['summary']['passed'],0)
            self.assertEqual(result['summary']['inconclusive'],11)
            self.assertTrue(all(c['pytest_status']=='failed' for c in result['cases']))
            self.assertEqual(result['transport']['request_count'],11)
            rejection_cases=[c for c in result['cases'] if 'wrong_param_rejected' in c['id']]
            self.assertEqual(len(rejection_cases),2)
            self.assertTrue(all('无法判定参数约束' in c['detail'] for c in rejection_cases))
            self.assertEqual(RejectProvider.count,11,'automatic retries must be disabled')
            self.assertTrue(any(e['total']==11 for e in events))
        finally:srv.shutdown();srv.server_close()

    def test_full_official_suite_with_mock_rate_limits(self):
        RejectProvider.count=0
        RejectProvider.status=429
        srv=ThreadingHTTPServer(('127.0.0.1',0),RejectProvider)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        config={'suite':'kvvfull','key':'offline-key','model':'offline','base':f'http://127.0.0.1:{srv.server_port}/v1','timeout':5}
        try:
            with tempfile.TemporaryDirectory() as temp:
                result=kvv_runner.run(config,lambda e:None,lambda:False,Path(temp))
            self.assertEqual(result['summary']['total'],611)
            self.assertEqual(result['summary']['completed'],611)
            # The four local tolerance checks do not make API calls. No 429 response may pass.
            passed=[c for c in result['cases'] if c['status']=='passed']
            self.assertEqual(len(passed),4)
            self.assertTrue(all('tolerance_boundaries' in c['id'] for c in passed))
            self.assertLess(RejectProvider.count,620,'SDK and flaky-marker retries must be disabled')
        finally:srv.shutdown();srv.server_close()

    def test_http_job_evidence_download(self):
        provider=ThreadingHTTPServer(('127.0.0.1',0),RejectProvider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        service=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=service.serve_forever,daemon=True).start()
        old_reports=server.REPORTS
        try:
            with tempfile.TemporaryDirectory() as temp, httpx.Client(trust_env=False) as client:
                server.REPORTS=Path(temp)
                url=f'http://127.0.0.1:{service.server_port}'
                token=client.get(url+'/api/session').json()['token']
                headers={'X-Workbench-Token':token}
                config={'suite':'ccmax','key':'offline-secret-marker','model':'offline','base':f'http://127.0.0.1:{provider.server_port}/v1','timeout':5,'signature_samples':1,'sse_samples':1}
                response=client.post(url+'/api/runs',headers=headers,json=config)
                self.assertEqual(response.status_code,202)
                run=response.json()['id']
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    result=client.get(url+'/api/runs/'+run,headers=headers).json()
                    if result['status']!='running':break
                    time.sleep(.03)
                self.assertEqual(result['status'],'completed')
                self.assertEqual(result['completed'],9)
                self.assertEqual(len(result['result']['checks']),12)
                for artifact in ['report.html','report.json','evidence.zip']:
                    response=client.get(url+'/api/runs/'+run+'/'+artifact,headers=headers)
                    self.assertEqual(response.status_code,200)
                    self.assertIn('attachment',response.headers['content-disposition'])
                    if artifact.endswith('.zip'):
                        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                            self.assertIn('report.html',z.namelist())
                            self.assertEqual(z.namelist().count('report.html'),1)
                            self.assertIn('逐项验收说明'.encode(),z.read('report.html'))
                            for name in z.namelist():self.assertNotIn(b'offline-secret-marker',z.read(name))
                    else:self.assertNotIn('offline-secret-marker',response.text)
        finally:
            server.REPORTS=old_reports
            provider.shutdown();provider.server_close();service.shutdown();service.server_close()
            server.JOBS.clear()

    def test_cancel_official_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'suite':'kvv11','key':'offline-key','model':'offline','base':'http://127.0.0.1:9/v1','timeout':5}
            start=time.monotonic()
            result=kvv_runner.run(config,lambda e:None,lambda:True,Path(temp))
            self.assertEqual(result['status'],'cancelled')
            self.assertLess(time.monotonic()-start,5)

if __name__=='__main__':unittest.main()
