"""Cross-suite matrix scoring and exact evidence joins; offline fixtures only."""
from copy import deepcopy
import unittest
from acceptance_results import decorate
from report_content import build_report_data, _report_score, _explain_check
from report_renderer import render_report, evidence_records


def sample(identity, probe='cap', status=200):
    return {'id': identity, 'probe': probe, 'status': 'passed' if status == 200 else 'failed',
            'duration_ms': 125, 'termination': 'eof',
            'request': {'method':'POST','url':'https://fixture.invalid/v1/messages',
                        'body': {'max_tokens':10,'messages':[{'role':'user','content':'fixture'}]}},
            'response': {'status':status,'body':'{"fixture":true}', 'headers':[['x-request-id','shared-upstream']]},
            'evidence': {'request_ids':[{'header':'x-request-id','value':'shared-upstream'}]}}


def case(identity, status='passed', module='max_tokens', **changes):
    value = {'id':identity,'title':'长度参数矩阵 '+identity,'status':status,'module':module,
             'dimensions':['max_tokens'],'scenario_id':'length-numeric','repetition':1,'parameters':{'max_tokens':10,'stream':False},
             'method':'发送长枚举任务并检查截断','expected':'不超10 tokens且以长度结束',
             'observed':'completion_tokens=10; stop_reason=max_tokens','meaning':'本轮限制生效',
             'next_step':'扩大任务类型后复测','request_ids':[identity], 'sample_ids':[identity], 'reason_code':'assertion_passed'}
    value.update(changes); return value


def report(suite):
    return {'suite':suite,'status':'completed','configuration':{'model':'fixture-claude','suite':suite},
            'enabled_modules':['protocol'], 'checks':[],'cases':[],'samples':[],
            'native_summary':{'total':0,'completed':0}, 'summary':{'total':2,'completed':2},
            'transport':{'requests':[], 'request_count':2},
            'matrix_validation': {'cases':[case('matrix-length-10'),case('matrix-length-20', 'inconclusive',parameters={'max_tokens':20,'stream':False}, observed='HTTP 429 quota exceeded',reason_code='rate_limited')],
                                  'samples':[sample('matrix-length-10'),sample('matrix-length-20',status=429)],
                                  'summary':{'total':2,'completed':2},'plan':{'max_tokens':[1,10,20]},'metrics':{}}}


class MatrixReportTests(unittest.TestCase):
    def test_matrix_cases_and_exact_samples_are_supported_by_each_suite(self):
        for suite in ('claude_acceptance','ccmax_acceptance','kvv11','kvvfull'):
            with self.subTest(suite=suite):
                value=report(suite); before=deepcopy(value)
                data=build_report_data(value)
                rows=[r for r in data['checks'] if r['id'].startswith('matrix-')]
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[0]['parameters']['max_tokens'],10)
                self.assertEqual(rows[1]['evidence_category'],'rate_limit')
                length=next(d for d in data['score']['dimensions'] if d['id']=='max_tokens')
                self.assertEqual(length['score'],100)
                self.assertEqual(length['covered'],2)
                self.assertEqual(length['conclusive'],1)
                self.assertEqual(length['resolution_percent'],50)
                self.assertEqual(length['scenario_count'],1)
                self.assertEqual(length['parameter_count'],2)
                self.assertEqual(length['sample_count'],2)
                self.assertTrue(length['small_sample'])
                self.assertEqual(value,before)
                html=render_report(value).decode()
                self.assertIn('有限样本',html)
                self.assertIn('可判定率 50%',html)
                self.assertIn('参数矩阵与压测执行明细',html)
                self.assertNotIn('无法判定=40%',html)
                check_anchor = next(i for i,r in enumerate(data['checks'],1) if r['id']=='matrix-length-10')
                check_html=html.split('id="check-%s"'%check_anchor,1)[1].split('</article>',1)[0]
                self.assertIn('href="#request-1"',check_html)
                self.assertNotIn('href="#request-2"',check_html)
                self.assertEqual(len(evidence_records(value)),2)

    def test_no_conclusive_evidence_has_no_numerical_score(self):
        value=report('claude_acceptance')
        value['matrix_validation']['cases']=[case('matrix-one','inconclusive',reason_code='evidence_missing')]
        data=build_report_data(value)
        self.assertIsNone(data['score']['total'])
        self.assertIsNone(data['score']['weighted_total'])
        self.assertIn('<div class="score-total">—',render_report(value).decode())

    def test_identity_observation_is_not_failure_or_free_points(self):
        value=report('claude_acceptance')
        value['enabled_modules']=['protocol','identity']
        value['cases']=[case('identity','inconclusive',module='identity',dimensions=['identity'],observed='模型字段只是渠道声明')]
        value['matrix_validation']['cases']=[case('matrix-one')]
        self.assertEqual(decorate(value)['verdict']['status'],'passed')
        self.assertEqual(value['verdict']['observational'],1)
        data=build_report_data(value)
        identity=next(d for d in data['score']['dimensions'] if d['id']=='identity')
        self.assertIsNone(identity['score'])
        self.assertTrue(identity['observational'])
        self.assertEqual(data['score']['total'],100)

    def test_matrix_failures_affect_verdict_and_preserve_native_summary_units(self):
        value=report('ccmax_acceptance')
        value['checks']=[{'id':'stream','status':'passed'}]
        value['native_summary']={'total':20,'completed':20}
        value['summary']={'total':22,'completed':22}
        value['matrix_validation']['cases']=[case('matrix-one','failed',reason_code='assertion_failed')]
        value['matrix_validation']['summary']={'total':1,'completed':1}
        verdict=decorate(value)['verdict']
        self.assertEqual(verdict['status'],'failed')
        self.assertEqual(verdict['counts']['failed'],1)
        self.assertEqual(verdict['untested'],0)

    def test_reasons_distinguish_transport_unsupported_and_assertion_failures(self):
        for observation, code, expected in [('HTTP 401 invalid api key','http_error','authentication'),
                ('HTTP 429 quota exceeded','rate_limited','rate_limit'),
                ('HTTP 200 missing usage','evidence_missing','insufficient_evidence'),
                ('model does not support video','unsupported_parameter','unsupported'),
                ('actual tokens=22 exceeds cap20','assertion_failed','capability_failure')]:
            row=_explain_check(case('matrix-one','failed', observed=observation,reason_code=code))
            self.assertEqual(row['evidence_category'],expected)
            self.assertEqual(row['status'],'failed','presentation must preserve original assertion')

    def test_saved_request_count_does_not_count_reused_provider_headers(self):
        value=report('claude_acceptance')
        one=value['matrix_validation']['cases'][0]
        one['sample_ids']=[];one['request_ids']=['matrix-length-10','shared-upstream']
        value['matrix_validation']['cases']=[one]
        stats=next(d for d in build_report_data(value)['score']['dimensions'] if d['id']=='max_tokens')
        self.assertEqual(stats['sample_count'],1)

    def test_performance_and_cache_metrics_are_readable_outside_raw_details(self):
        value=report('kvvfull')
        value['matrix_validation']['metrics']={
            'stress': {'stages':[{'concurrency':4,'planned':20,'completed':20,'successful':19,'success_rate':.95,
                'http_successful':20,'http_success_rate':1,'semantic_failures':0,'budget_exhausted':1,
                'rate_limited':1,'server_errors':0,'transport_errors':0,'p50_ms':1200,'p95_ms':2300,
                'first_byte_p50_ms':300,'throughput_rps':3.14,'request_ids':['matrix-length-10']}]},
            'cache': {'rounds':[{'target_tokens':12000,'round':2,'variant':'warm-repeat','input_tokens':30,
                'output_tokens':16,'cache_read_tokens':12000,'cache_creation_tokens':0,'total_input_tokens':12030,
                'duration_ms':1500,'prefix_sha256':'abc0123456789abcdef','prefix_chars':48000,
                'status':'passed','request_id':'matrix-length-10'}],'estimate_note':'目标仅为估算，实测以 usage 为准'}}
        html=render_report(value).decode()
        panel=html.split('id="parameter-matrix"',1)[1].split('<details class="raw">',1)[0]
        for text in ('阶梯负载','95.00%','3.14','12000','warm-repeat','abc0123456789abc','429 / 5xx / 连接异常','HTTP 成功 / 比例','语义与协议通过 / 比例','断言不符 / 预算耗尽','阶段耗时与吞吐量'):
            self.assertIn(text,panel)
        self.assertIn('href="#request-1"',panel)

    def test_infrastructure_failure_preserves_status_but_cannot_score_capability(self):
        value=report('claude_acceptance')
        value['matrix_validation']['cases']=[case('matrix-length-10','inconclusive',score_applicable=False,
            observed='HTTP 503 upstream unavailable',reason_code='http_error')]
        data=build_report_data(value)
        self.assertIsNone(data['score']['total'])
        self.assertEqual(data['checks'][0]['evidence_category'],'infrastructure')


    def test_ambiguous_legacy_provider_id_cannot_invent_local_sample_links(self):
        value=report('claude_acceptance')
        value['matrix_validation']['cases']=[case('legacy-one',request_ids=['shared-upstream'],sample_ids=[])]
        html=render_report(value).decode()
        table=html.split('id="all-results"',1)[1].split('</section>',1)[0]
        self.assertIn('未记录关联',table)
        self.assertNotIn('2 次 /',table)
        check_html=html.split('id="check-1"',1)[1].split('</article>',1)[0]
        self.assertIn('上游 ID 被复用，无法唯一关联',check_html)
        self.assertNotIn('href="#request-',check_html)


    def test_native_reason_and_actual_parameters_remain_visible(self):
        value=report('claude_acceptance'); value.pop('matrix_validation')
        value['checks']=[case('ordinary','inconclusive',module='protocol',dimensions=['protocol'],reason_code='budget_exhausted',
                             observed='输出预算耗尽且无可见正文',request_ids=['native-1'],sample_ids=['native-1'])]
        value['samples']=[sample('native-1')]
        value['samples'][0]['assessments']=[{'check':'ordinary','status':'inconclusive','reason_code':'budget_exhausted','detail':'仅返回 thinking'}]
        value['samples'][0]['request']['body']['max_tokens']=512
        data=build_report_data(value)
        self.assertEqual(data['checks'][0]['evidence_category'],'insufficient_evidence')
        self.assertIsNone(data['score']['total'])
        self.assertIn('截断专项必须保持原设定上限',data['checks'][0]['next_step'])
        html=render_report(value).decode()
        check_html=html.split('id="check-1"',1)[1].split('</article>',1)[0]
        self.assertIn('逐请求参数与样本判定',check_html)
        self.assertIn('<b>max_tokens</b> = 512',check_html)
        self.assertIn('budget_exhausted',check_html)

    def test_general_injection_is_distinct_from_protocol(self):
        from browser_reports import normalize_browser_report, report_data
        payload={'records':[{'kind':'general','model':'fixture','result':{'checks':[
            {'name':'合成金丝雀','status':'passed','dimensions':['injection','security'],'module':'injection','parameters':{'attack':'direct'}}]}}]}
        data=report_data(normalize_browser_report(payload))
        dimensions={d['id']:d for d in data['score']['dimensions']}
        self.assertEqual(dimensions['security']['score'],100)
        self.assertEqual(dimensions['protocol']['covered'],0)
        modules={m['id']:m for m in data['score']['modules']}
        self.assertEqual(modules['security']['score'],100)
        self.assertEqual(sum(m['weight'] for m in modules.values()),100)


    def test_controls_and_aggregates_do_not_dilute_resolution_but_unknown_capability_does(self):
        value=report('claude_acceptance')
        value['matrix_validation']['cases']=[case('pass'),case('control',score_applicable=False,evidence_category='control'),
             case('aggregate',score_applicable=False,evidence_category='aggregate')]
        score=build_report_data(value)['score']
        length=next(d for d in score['dimensions'] if d['id']=='max_tokens')
        self.assertEqual(length['resolution_percent'],100)
        self.assertEqual(length['capability_observed'],1)
        self.assertEqual(length['observation_count'],2)
        value['matrix_validation']['cases'].append(case('unknown','inconclusive',score_applicable=False,reason_code='usage_missing'))
        length=next(d for d in build_report_data(value)['score']['dimensions'] if d['id']=='max_tokens')
        self.assertEqual(length['resolution_percent'],50)
        self.assertEqual(length['score'],100)


    def test_matrix_batch_links_cannot_join_another_model(self):
        children=[{'model':'model-a','result':report('kvv11')},{'model':'model-b','result':report('kvv11')}]
        batch={'suite':'batch_acceptance','status':'completed','results':children}
        rows=build_report_data(batch)['checks']
        self.assertEqual(rows[0]['request_ids'],['model-1-matrix-length-10'])
        self.assertEqual(rows[2]['request_ids'],['model-2-matrix-length-10'])
        self.assertEqual(len(evidence_records(batch)),4)


if __name__=='__main__': unittest.main()
