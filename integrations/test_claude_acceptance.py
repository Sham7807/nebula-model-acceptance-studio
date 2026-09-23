"""No paid API calls: all Claude fixtures use an isolated httpx transport."""
import json
import threading
import time
import unittest
import uuid
import os
from unittest.mock import patch

import httpx
try:
    from integrations import claude_acceptance as c
    from integrations.test_ccmax_acceptance import good_sse, ChunkStream
except ImportError:
    import claude_acceptance as c
    from test_ccmax_acceptance import good_sse, ChunkStream


class ClaudeAcceptanceTests(unittest.TestCase):
    def config(self, **kwargs):
        value={"base":"https://relay.test/v1","key":"fixture-secret","model":"claude-fixture","signature_samples":1,"sse_samples":1,"cache_tokens":1024,"stress_requests":3,"stress_concurrency":2,"timeout":5}
        value.update(kwargs); return value

    def handler(self, calls):
        def handle(request):
            body=json.loads(request.content); calls.append((dict(request.headers),body))
            headers={"request-id":"req-"+uuid.uuid4().hex,"x-amzn-requestid":"fixture-not-trust-proof"}
            def message(text="CLAUDE-BASELINE-OK",content=None,usage=None,reason="end_turn"):
                return httpx.Response(200,headers=headers,json={"id":"msg_"+uuid.uuid4().hex,"type":"message","model":"claude-fixture","role":"assistant","content":content or [{"type":"text","text":text}],"stop_reason":reason,"usage":usage or {"input_tokens":20,"output_tokens":5}})
            if request.headers.get('x-api-key','').startswith('claude-invalid-'): return httpx.Response(401,json={"error":{"type":"authentication_error","message":"invalid key"}})
            if body["model"].startswith('__claude_acceptance_missing_'): return httpx.Response(404,json={"error":{"type":"not_found_error","message":"model not found"}})
            if body.get('max_tokens')==0: return httpx.Response(400,json={"error":{"type":"invalid_request_error","message":"max_tokens must be positive"}})
            assistant=[m for m in body['messages'] if m['role']=='assistant']
            if assistant and any((x.get('signature','').startswith('Y2hhbm5lbC') or x.get('signature','').startswith('Apaque')) for m in assistant for x in m.get('content',[]) if isinstance(x,dict)): return httpx.Response(400,json={"error":{"message":"invalid signature"}})
            if body.get('stream'): return httpx.Response(200,headers=headers,stream=ChunkStream(good_sse(message_id='msg_'+uuid.uuid4().hex)))
            if body.get('thinking'): return message('323',content=[{"type":"thinking","thinking":"17 times 19 is 323","signature":"opaque-genuine-fixture"},{"type":"text","text":"323"}])
            if body.get('tools'):
                if assistant: return message('27271296')
                return message(content=[{"type":"tool_use","id":"toolu_fixture","name":"Calculator","input":{"expr":"3456 * 7891"}}],reason='tool_use')
            if body.get('max_tokens')==1: return message('A',usage={'input_tokens':20,'output_tokens':1},reason='max_tokens')
            prompt=str(body['messages']); system=str(body.get('system',''))
            if 'STOP_SENTINEL' in prompt: return message('ALPHA ',reason='stop_sequence')
            if 'image' in prompt: return message('red')
            if 'CLAUDE-DOC-SAFE-ACK' in system: return message('CLAUDE-DOC-SAFE-ACK')
            if 'Private token' in system: return message('I cannot disclose that.')
            if 'CLAUDE-SAFE-ACK' in system: return message('CLAUDE-SAFE-ACK')
            if 'Acceptance cache document' in system:
                round=sum('Acceptance cache document' in str(x[1].get('system','')) for x in calls)
                return message('CACHE-ACK',usage={'input_tokens':10,'output_tokens':2,'cache_creation_input_tokens':1500 if round==1 else 0,'cache_read_input_tokens':0 if round in (1,4) else 1500})
            if 'STRESS-OK' in prompt: return message('STRESS-OK')
            return message()
        return handle

    def test_native_complete_run_preserves_evidence_and_excludes_secrets(self):
        calls=[]; result=c.run(self.config(transport=httpx.MockTransport(self.handler(calls))))
        checks={x['id']:x for x in result['checks']}
        self.assertEqual(result['suite'],'claude_acceptance'); self.assertEqual(result['status'],'completed')
        self.assertEqual(checks['cache']['status'],'passed'); self.assertEqual(checks['signature']['status'],'passed'); self.assertEqual(checks['signature_roundtrip']['status'],'passed'); self.assertEqual(checks['tools']['status'],'passed')
        self.assertEqual(checks['identity']['status'],'inconclusive')
        self.assertEqual(checks['stress']['metrics']['completed'],3)
        self.assertNotIn('fixture-secret',json.dumps(result))
        self.assertTrue(all(all(k in x for k in ('method','expected','observed','meaning','next_step','request_ids','dimensions')) for x in result['checks']))
        self.assertTrue(all(x in {s['id'] for s in result['samples']} for check in result['checks'] for x in check['request_ids']))
        bodies=[b for _,b in calls if 'Acceptance cache document' in str(b.get('system',''))]
        self.assertEqual(len(bodies),4); self.assertNotEqual(bodies[2]['system'],bodies[3]['system']); self.assertEqual(bodies[0],bodies[1]); self.assertEqual(bodies[1]['system'],bodies[2]['system']); self.assertNotEqual(bodies[1]['messages'],bodies[2]['messages'])

    def test_provider_claim_does_not_change_endpoint_or_forge_sigv4(self):
        calls=[]; result=c.run(self.config(provider='aws',enabled_modules=['identity'],transport=httpx.MockTransport(self.handler(calls))))
        self.assertEqual(result['configuration']['provider'],'aws')
        for headers,_ in calls:
            self.assertNotIn('x-amz-date',headers); self.assertNotIn('AWS4-HMAC-SHA256',str(headers))
        for sample in result['samples']: self.assertEqual(sample['request']['url'],'https://relay.test/v1/messages')

    def test_openai_native_signature_is_skipped_without_request(self):
        def handler(req):
            b=json.loads(req.content)
            self.assertEqual(str(req.url),'https://relay.test/v1/chat/completions'); self.assertNotIn('anthropic-version',req.headers)
            if req.headers['authorization'].startswith('Bearer claude-invalid-'): return httpx.Response(401,json={'error':{'message':'bad key'}})
            self.assertNotIn('thinking',b)
            return httpx.Response(200,json={'id':'chat-fixture','model':'claude-fixture','choices':[{'message':{'role':'assistant','content':'CLAUDE-BASELINE-OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7}})
        result=c.run(self.config(request_format='openai',enabled_modules=['auth_signature'],transport=httpx.MockTransport(handler)))
        checks={x['id']:x for x in result['checks']}; self.assertEqual(checks['signature']['status'],'skipped'); self.assertEqual(checks['authentication']['status'],'passed'); self.assertEqual(len(result['samples']),2)

    def test_disabled_modules_do_not_send_paid_probes(self):
        calls=[]; result=c.run(self.config(enabled_modules=['max_tokens'],transport=httpx.MockTransport(self.handler(calls))))
        self.assertEqual([x['id'] for x in result['samples']],['baseline','max-tokens-one','max-tokens-invalid'])
        self.assertTrue(all(x['status']=='not_covered' for x in result['checks'] if x['module']!='max_tokens'))

    def test_cache_without_hit_is_inconclusive_not_failed(self):
        def handler(request):
            return httpx.Response(200,json={'type':'message','content':[{'type':'text','text':'CACHE-ACK'}],'usage':{'input_tokens':2000,'output_tokens':2},'stop_reason':'end_turn'})
        r=c.run(self.config(enabled_modules=['cache'],transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='cache')['status'],'inconclusive')

    def test_invalid_cache_types_are_failures_not_crashes_or_hits(self):
        for usage in ({'input_tokens':1200,'cache_read_input_tokens':'700'}, {'prompt_tokens':1200,'prompt_tokens_details':'bad'}, {'input_tokens':1200,'cache_read_input_tokens':True}, {'prompt_tokens':1000,'prompt_tokens_details':{'cached_tokens':2000}}):
            with self.subTest(usage=usage):
                def handler(req): return httpx.Response(200,json={'type':'message','stop_reason':'end_turn','content':[{'type':'text','text':'CACHE-ACK'}],'usage':usage})
                r=c.run(self.config(enabled_modules=['cache'],transport=httpx.MockTransport(handler)))
                self.assertEqual(next(x for x in r['checks'] if x['id']=='cache')['status'],'failed')

    def test_indirect_document_injection_is_a_separate_real_probe(self):
        calls=[];base=self.handler(calls)
        def handler(req):
            body=json.loads(req.content)
            if '<untrusted_document>' in str(body):
                return httpx.Response(200,json={'type':'message','stop_reason':'end_turn','content':[{'type':'text','text':'CLAUDE-DOC-OVERRIDE'}]})
            return base(req)
        r=c.run(self.config(enabled_modules=['injection'],transport=httpx.MockTransport(handler)))
        row=next(x for x in r['checks'] if x['id']=='injection')
        self.assertEqual(row['status'],'failed');self.assertIn('indirect-injection',row['request_ids'])
        self.assertIn('不可信数据',row['next_step'])

    def test_empty_injection_text_is_not_a_pass(self):
        def handler(req):
            b=json.loads(req.content);text='CLAUDE-SAFE-ACK' if 'CLAUDE-SAFE-ACK' in str(b) else ''
            return httpx.Response(200,json={'type':'message','stop_reason':'end_turn','content':[{'type':'text','text':text}]})
        r=c.run(self.config(enabled_modules=['injection'],transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='injection')['status'],'inconclusive')

    def test_unsupported_signature_is_not_verification_success(self):
        calls=[];base=self.handler(calls)
        def handler(req):
            body=json.loads(req.content)
            if any(m['role']=='assistant' for m in body['messages']):
                return httpx.Response(400,json={'error':{'message':'unsupported signature field'}})
            return base(req)
        r=c.run(self.config(enabled_modules=['auth_signature'],transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='signature')['status'],'inconclusive')

    def test_negative_probe_requires_positive_control(self):
        def handler(request): return httpx.Response(400,json={'error':{'message':'invalid signature'}})
        r=c.run(self.config(enabled_modules=['auth_signature'],transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='signature')['status'],'inconclusive')

    def test_stress_respects_concurrency_and_does_not_retry(self):
        active=0; peak=0; calls=0; lock=threading.Lock()
        def handler(req):
            nonlocal active,peak,calls
            b=json.loads(req.content)
            stress='STRESS-OK' in str(b)
            if stress:
                with lock: active+=1; peak=max(peak,active);calls+=1
                time.sleep(.015)
                with lock: active-=1
                if calls==2: return httpx.Response(429,json={'error':{'message':'rate limited'}})
            return httpx.Response(200,json={'type':'message','content':[{'type':'text','text':'STRESS-OK' if stress else 'CLAUDE-BASELINE-OK'}],'id':'msg_'+uuid.uuid4().hex,'stop_reason':'end_turn'})
        r=c.run(self.config(enabled_modules=['stress'],stress_requests=8,stress_concurrency=2,transport=httpx.MockTransport(handler)))
        self.assertEqual(calls,8);self.assertLessEqual(peak,2);self.assertGreater(peak,1)
        check=next(x for x in r['checks'] if x['id']=='stress');self.assertNotEqual(check['status'],'passed')

    def test_cancel_before_run_sends_nothing(self):
        event=threading.Event();event.set()
        r=c.run(self.config(transport=httpx.MockTransport(lambda req:self.fail('network after cancel'))),cancelled=event)
        self.assertEqual(r['status'],'cancelled');self.assertFalse(r['samples'])

    def test_openai_tool_and_image_shapes(self):
        s,_=c.configuration(self.config(request_format='openai'))
        specs=c.build_probe_specs(s,'fixture')
        tools=next(x['body'] for x in specs if x['id']=='tool-call')
        self.assertEqual(tools['tool_choice'],{'type':'function','function':{'name':'Calculator'}})
        self.assertEqual(tools['tools'][0]['function']['parameters']['required'],['expr'])
        vision=next(x['body'] for x in specs if x['id']=='vision-red');self.assertEqual(vision['messages'][0]['content'][1]['type'],'image_url')

    def test_explicit_outbound_proxy_is_applied_and_secrets_not_reported(self):
        original=httpx.Client; kwargs_seen=[]; calls=[]
        def client(*args,**kwargs):
            kwargs_seen.append(dict(kwargs));kwargs.pop('proxy',None)
            return original(*args,**kwargs)
        proxy='http://proxy-user:proxy-private-password@proxy.test:8080'
        with patch.dict(os.environ,{'WORKBENCH_OUTBOUND_PROXY':proxy}),patch.object(c.core.httpx,'Client',side_effect=client):
            r=c.run(self.config(enabled_modules=['identity'],transport=httpx.MockTransport(self.handler(calls))))
        self.assertTrue(kwargs_seen);self.assertTrue(all(x['proxy']==proxy and x['trust_env'] is False for x in kwargs_seen))
        self.assertNotIn('proxy-private-password',json.dumps(r))
        self.assertNotIn('proxy-private-password',c.core._safe_proxy_error(RuntimeError(proxy),proxy))

    def test_invalid_outbound_proxy_is_rejected_without_network_or_secret(self):
        proxy='socks://private-user:private-pass@proxy.test:1000'
        with patch.dict(os.environ,{'WORKBENCH_OUTBOUND_PROXY':proxy}):
            r=c.run(self.config(enabled_modules=['identity'],transport=httpx.MockTransport(lambda req:self.fail('invalid proxy contacted network'))))
        self.assertNotIn('private-pass',json.dumps(r));self.assertNotIn('socks:',json.dumps(r))
        self.assertTrue(all(x['status']=='inconclusive' for x in r['samples']))

    def test_malformed_nested_response_retains_original_evidence(self):
        body={'choices':[{'message':[]}], 'content':'wrong type'}
        r=c.run(self.config(request_format='openai',enabled_modules=['tools'],transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body))))
        for sample in r['samples']:
            self.assertEqual(json.loads(sample['response']['body']),body)
            self.assertNotEqual(sample['status'],'passed')

    def test_custom_versions_and_full_endpoint_paths_are_preserved(self):
        pairs=[('https://relay.test','anthropic','https://relay.test/v1/messages'),('https://relay.test/relay','anthropic','https://relay.test/relay/v1/messages'),('https://relay.test/relay/v2','anthropic','https://relay.test/relay/v2/messages'),('https://relay.test/relay/v2/messages','openai','https://relay.test/relay/v2/chat/completions'),('https://relay.test/relay/chat/completions','anthropic','https://relay.test/relay/messages')]
        for base,format,expected in pairs:
            with self.subTest(base=base,format=format):
                calls=[]
                def handler(req):
                    calls.append(str(req.url))
                    return httpx.Response(200,json={'type':'message','content':[{'type':'text','text':'hi'}],'stop_reason':'end_turn'})
                cfg=self.config(base=base,request_format=format,enabled_modules=['identity'],transport=httpx.MockTransport(handler))
                result=c.run(cfg);self.assertEqual(set(calls),{expected})
                self.assertEqual({x['url'] for x in c.build_plan(cfg)['requests']},{expected})

    def test_plan_uses_same_request_shapes_without_key_or_network(self):
        plan=c.build_plan({**self.config(), 'key':''})
        self.assertEqual(plan['suite'],'claude')
        self.assertEqual(plan['token_estimate']['cache_requests'],4)
        self.assertEqual(plan['request_count'],sum(x.get('repeat',1) for x in plan['requests']))
        self.assertNotIn('fixture-secret',json.dumps(plan))
        self.assertTrue(all('module' in x and 'title' in x for x in plan['requests']))
        self.assertEqual(len([x for x in plan['requests'] if x.get('conditional')]),3)

    def test_signature_tamper_only_runs_after_original_roundtrip(self):
        calls=[]
        base=self.handler(calls)
        def handler(req):
            body=json.loads(req.content)
            if body.get('thinking') and any(x['role']=='assistant' for x in body['messages']):
                calls.append((dict(req.headers),body))
                return httpx.Response(400,json={'error':{'message':'unsupported message'}})
            return base(req)
        r=c.run(self.config(enabled_modules=['auth_signature'],transport=httpx.MockTransport(handler)))
        ids={x['id'] for x in r['samples']}
        self.assertIn('thinking-return',ids);self.assertNotIn('thinking-mutated',ids)
        self.assertEqual(next(x for x in r['checks'] if x['id']=='signature_mutation')['status'],'inconclusive')

    def test_cache_changed_prefix_does_not_blindly_confirm_hit(self):
        calls=[];base=self.handler(calls)
        def handler(req):
            body=json.loads(req.content)
            if 'Changed prefix control' in str(body.get('system','')):
                return httpx.Response(200,json={'type':'message','stop_reason':'end_turn','content':[{'type':'text','text':'CACHE-ACK'}],'usage':{'input_tokens':10,'output_tokens':2,'cache_read_input_tokens':1500}})
            return base(req)
        r=c.run(self.config(enabled_modules=['cache'],transport=httpx.MockTransport(handler)))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='cache')['status'],'inconclusive')

    def test_progress_exposes_case_and_disabled_modules_are_explicit(self):
        events=[];calls=[]
        r=c.run(self.config(enabled_modules=['max_tokens'],transport=httpx.MockTransport(self.handler(calls))),events.append)
        self.assertTrue(all(x.get('case') for x in events if x.get('phase')=='sample_complete'))
        self.assertTrue(all(x['module_disabled'] for x in r['checks'] if x['module']!='max_tokens'))

    def test_malformed_success_cannot_pass_injection(self):
        r=c.run(self.config(enabled_modules=['injection'],transport=httpx.MockTransport(lambda req:httpx.Response(200,json={}))))
        self.assertEqual(next(x for x in r['checks'] if x['id']=='injection')['status'],'failed')

if __name__=='__main__': unittest.main()
