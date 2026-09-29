"""Offline provenance inference: wire evidence, boundaries, and false positives."""
from copy import deepcopy
import json
import unittest

from report_brief import resource_source
from browser_reports import normalize_browser_report


def message():
    return {'type':'message','role':'assistant','content':[{'type':'text','text':'OK'}],
            'stop_reason':'end_turn','usage':{'input_tokens':12,'output_tokens':2}}


def sample(identity='one', url='https://relay.test/v1/messages', payload=None, status=200, model='claude-fixture'):
    return {'id':identity, 'request':{'url':url,'body':{'model':model,'messages':[{'role':'user','content':'hi'}]}},
            'response':{'status':status,'body':json.dumps(message() if payload is None else payload),'headers':[]},'termination':'eof'}


def report(samples, **config):
    return {'configuration':{'model':'claude-fixture', **config}, 'samples':samples}


def signature_chain():
    original=sample('original'); original['suite_probe']='thinking'
    body=message(); body['content']=[{'type':'thinking','thinking':'short thought','signature':'test-signature'},{'type':'text','text':'323'}]
    original['response']['body']=json.dumps(body)
    original['response']['headers']=[['anthropic-ratelimit-requests-limit','100']]
    positive=sample('positive'); positive['suite_probe']='thinking_return'
    positive['request']['body']['messages'].append({'role':'assistant','content':deepcopy(body['content'])})
    negative=deepcopy(positive); negative['id']='negative'; negative['suite_probe']='signature_mutation'
    negative['request']['body']['messages'][-1]['content'][0]['signature']='Xest-signature'
    negative['response']={'status':400,'headers':[],'body':json.dumps({'error':{'type':'invalid_request_error','message':'Invalid thinking signature'}})}
    return [original,positive,negative]


class ResourceSourceTests(unittest.TestCase):
    def test_two_categories_keep_legacy_official_claim_separate(self):
        for value in ('official','official_relay','reverse'):
            result=resource_source(report([],resource_source=value))
            self.assertEqual(result['classification'],'unknown')
            self.assertEqual(result['kind'],'unknown')
            self.assertIn('渠道声明',result['operator_label'])
            self.assertNotIn('官方',result['label'])
            self.assertTrue(result['missing_evidence'])

    def test_real_api_origin_and_native_response_support_api_resource_tendency(self):
        result=resource_source(report([sample(url='https://api.anthropic.com/v1/messages')]))
        self.assertEqual(result['classification'],'official_relay')
        self.assertEqual(result['label'],'倾向官转（实测线索）')
        self.assertIn('完整原生响应',result['evidence'][0])

    def test_multiple_providers_use_matching_native_response_shapes(self):
        chat={'object':'chat.completion','choices':[{'message':{'role':'assistant','content':'OK'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':8,'completion_tokens':2}}
        responses={'object':'response','status':'completed','output':[], 'usage':{'input_tokens':8,'output_tokens':2}}
        google={'candidates':[{'content':{'parts':[{'text':'OK'}]}}], 'usageMetadata':{'promptTokenCount':8,'candidatesTokenCount':2}}
        for url,body in [('https://api.deepseek.com/chat/completions',chat),('https://api.moonshot.cn/v1/chat/completions',chat),('https://api.openai.com/v1/responses',responses),('https://generativelanguage.googleapis.com/v1beta/models/gemini-fixture:generateContent',google),('https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.fixture/invoke',message())]:
            with self.subTest(url=url):
                self.assertEqual(resource_source(report([sample(url=url,payload=body)]))['classification'],'official_relay')

    def test_api_name_spoofing_and_incomplete_native_bodies_do_not_classify(self):
        for url in ('https://api.anthropic.com.evil.test/v1/messages','http://api.anthropic.com/v1/messages','https://relay.test/api.anthropic.com/v1/messages','https://user@api.anthropic.com/v1/messages','https://api.anthropic.com:8443/v1/messages','https://api.anthropic.com/v1/models'):
            with self.subTest(url=url): self.assertEqual(resource_source(report([sample(url=url)]))['classification'],'unknown')
        for body in ({'usage':{'input_tokens':1,'output_tokens':1}}, {'model':'claude','choices':[]}, {'type':'message','content':[]}):
            self.assertEqual(resource_source(report([sample(url='https://api.anthropic.com/v1/messages',payload=body)]))['classification'],'unknown')
        for field,value in [('termination','total_timeout'),('body_truncated',True),('infrastructure_error',True)]:
            item=sample(url='https://api.anthropic.com/v1/messages');item[field]=value
            self.assertEqual(resource_source(report([item]))['classification'],'unknown')

    def test_web_session_path_is_only_a_narrow_structured_error_signal(self):
        for url in ('https://claude.ai/api/organizations/org-test/chat_conversations/conversation-test','https://chatgpt.com/backend-api/conversation','https://chat.openai.com/backend-api/conversation/one'):
            item=sample(payload={'error':{'message':'upstream failed '+url}},status=502)
            result=resource_source(report([item]))
            self.assertEqual(result['classification'],'reverse')
            self.assertEqual(result['label'],'倾向逆向（实测线索）')
            self.assertNotIn(url,' '.join(result['evidence']))

    def test_generated_text_requests_and_generic_errors_are_not_source_evidence(self):
        url='https://chatgpt.com/backend-api/conversation'
        body=message();body['content'][0]['text']=url
        candidates=[sample(payload=body),sample(payload={'error':{'message':url}},status=200),sample(payload={'message':url},status=502),sample(payload={'error':{'message':'timeout rate limit tool not supported invalid max_tokens'}},status=400),sample(payload={'error':{'message':'https://chatgpt.com.evil.test/backend-api/conversation'}},status=502)]
        echoed=sample(payload={'error':{'message':'rejected URL '+url}},status=400);echoed['request']['body']['messages'][0]['content']=url;candidates.append(echoed)
        for item in candidates:
            with self.subTest(response=item['response']): self.assertEqual(resource_source(report([item]))['classification'],'unknown')

    def test_signature_chain_requires_same_model_same_endpoint_and_real_negative_control(self):
        self.assertEqual(resource_source(report(signature_chain()))['classification'],'official_relay')
        for mutation in ('header','usage','model','endpoint','positive-content','extra-change','generic-error','missing-negative'):
            chain=signature_chain()
            if mutation=='header': chain[0]['response']['headers']=[['x-request-id','arbitrary']]
            elif mutation=='usage':
                payload=json.loads(chain[0]['response']['body']);payload.pop('usage');chain[0]['response']['body']=json.dumps(payload)
            elif mutation=='model': chain[2]['request']['body']['model']='different-model'
            elif mutation=='endpoint': chain[2]['request']['url']='https://other.test/v1/messages'
            elif mutation=='positive-content': chain[1]['request']['body']['messages'][-1]['content'][0]['thinking']='changed'
            elif mutation=='extra-change': chain[2]['request']['body']['max_tokens']=8
            elif mutation=='generic-error': chain[2]['response']['body']=json.dumps({'error':{'message':'Unknown field signature is not supported'}})
            elif mutation=='missing-negative': chain.pop()
            with self.subTest(mutation=mutation): self.assertEqual(resource_source(report(chain))['classification'],'unknown')

    def test_invalid_model_control_does_not_become_a_second_resource(self):
        control=sample('invalid-model',payload={'error':{'message':'invalid model'}},status=404,model='__missing_model')
        self.assertEqual(resource_source(report([sample(url='https://api.anthropic.com/v1/messages'),control]))['classification'],'official_relay')

    def test_conflicting_evidence_stays_pending(self):
        values=[sample(url='https://api.anthropic.com/v1/messages'),sample('web',payload={'error':{'message':'failed https://chatgpt.com/backend-api/conversation'}},status=502)]
        result=resource_source(report(values))
        self.assertEqual(result['classification'],'unknown')
        self.assertTrue(any('互相冲突' in item for item in result['missing_evidence']))

    def test_browser_normalization_retains_source_inference_and_record_boundaries(self):
        def record(items,claim=None):
            return {'kind':'general','model':'claude-fixture','result':{'config':{'model':'claude-fixture','resource_source':claim},'checks':[],'requests':items}}
        chain=signature_chain()
        for row in chain:
            row.update(url=row['request']['url'],request_body=row['request']['body'],response_body=json.loads(row['response']['body']),response_headers=row['response']['headers'],http_status=row['response']['status'])
        normalized=normalize_browser_report({'records':[record(chain)]})
        self.assertEqual(resource_source(normalized)['classification'],'official_relay')
        separated=normalize_browser_report({'records':[record(chain[:1],'official_relay'),record(chain[1:])]})
        result=resource_source(separated)
        self.assertEqual(result['classification'],'unknown')
        self.assertIn('部分记录未声明',result['operator_label'])

    def test_batch_does_not_lend_one_models_evidence_to_another(self):
        source={'suite':'batch_acceptance','results':[{'model':'a','result':report([sample(url='https://api.anthropic.com/v1/messages')],model='a')},{'model':'b','result':report([],model='b')}]}
        answer=resource_source(source)
        self.assertEqual(answer['classification'],'unknown')
        self.assertEqual(len(answer['models']),2)

    def test_plain_requests_container_and_missing_vs_no_signal_are_distinct(self):
        item=sample(url='https://api.anthropic.com/v1/messages')
        result=resource_source({'config':{'model':'claude-fixture'},'requests':[item]})
        self.assertEqual(result['classification'],'official_relay')
        no_signal=resource_source(report([sample()]))
        no_request=resource_source(report([]))
        self.assertIn('已检查 1 条',no_signal['evidence'][0])
        self.assertIn('未保存',no_request['evidence'][0])

    def test_nested_browser_wrapper_http_status_and_raw_signature_evidence(self):
        chain=signature_chain()
        normalized=normalize_browser_report({'records':[{'kind':'general','model':'claude-fixture','result':{'config':{'model':'claude-fixture'},'requests':chain}}]})
        self.assertEqual(resource_source(normalized)['classification'],'official_relay')
        wrapped={'model':'claude-fixture','url':'https://api.anthropic.com/v1/messages', 'http_status':None,
                 'response_body':{'status':200,'body':json.dumps(message()),'headers':[]}}
        self.assertEqual(resource_source({'requests':[wrapped]})['classification'],'official_relay')

    def test_missing_model_and_response_truncation_cannot_establish_source(self):
        missing=sample(url='https://api.anthropic.com/v1/messages');missing['request']['body'].pop('model')
        result=resource_source({'samples':[missing]})
        self.assertEqual(result['classification'],'unknown')
        self.assertTrue(any('未记录模型' in text for text in result['missing_evidence']))
        for location in ('response','evidence'):
            item=sample(url='https://api.anthropic.com/v1/messages');item.setdefault(location,{})['body_truncated' if location=='response' else 'truncated']=True
            self.assertEqual(resource_source(report([item]))['classification'],'unknown')
        item=sample(url='https://api.anthropic.com/v1/messages');body=message();body['error']={'message':'upstream is broken'};item['response']['body']=json.dumps(body)
        self.assertEqual(resource_source(report([item]))['classification'],'unknown')

    def test_http_error_is_observable_even_when_transport_marks_it_infrastructure(self):
        item=sample(payload={'error':{'message':'failed https://chatgpt.com/backend-api/conversation'}},status=502)
        item['infrastructure_error']=True
        self.assertEqual(resource_source(report([item]))['classification'],'reverse')

    def test_accepted_mutated_signature_explains_anomaly_without_calling_it_reverse(self):
        chain=signature_chain()
        chain[0]['response']['headers']=[['x-request-id','gateway-trace'],['x-oneapi-request-id','local-trace']]
        chain[2]['response']={'status':200,'headers':[],'body':json.dumps(message())}
        answer=resource_source(report(chain))
        self.assertEqual(answer['classification'],'unknown')
        self.assertTrue(any('原样签名回传请求成功' in text for text in answer['evidence']))
        self.assertTrue(any('篡改签名仍收到 HTTP 200' in text for text in answer['evidence']))
        self.assertTrue(any('未保留供应商专有头' in text for text in answer['missing_evidence']))


if __name__=='__main__': unittest.main()
