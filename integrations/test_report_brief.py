"""Offline regression for executive conclusions and observed timing."""
from copy import deepcopy
import unittest
from report_brief import build_summary, build_timing, cache_summary, usage_pair, resource_grade
from report_content import _report_score, _score_stats, build_report_data
from browser_reports import normalize_browser_report


def check(identity, dimension, status='passed', **extra):
    return {'id': identity, 'status': status, 'metadata': {'dimensions': [dimension]}, **extra}


class BriefTests(unittest.TestCase):
    def test_one_overall_score_uses_module_weights(self):
        checks = [check('p','protocol'), check('t','tools','failed')]
        score = _report_score(checks, {'suite': 'browser_report'})
        self.assertEqual(score['total'], score['weighted_total'])
        self.assertNotEqual(score['total'], score['dimension_average'])

    def test_browser_stress_alias_contributes_to_reliability_module(self):
        c=check('load','reliability',module='stress')
        score=_report_score([c],{'suite':'browser_report'})
        module=next(x for x in score['modules'] if x['id']=='reliability')
        self.assertEqual(module['score'],100)

    def test_control_uncertainty_does_not_label_passed_capability_unknown(self):
        stats = _score_stats([check('ok','tools'), check('control','tools','inconclusive',evidence_category='control')])
        self.assertEqual((stats['status'],stats['score'],stats['scored_passed'],stats['conclusive']), ('passed',100,1,1))
        self.assertEqual(stats['observation_count'],1)

    def test_actual_overshoot_distinct_from_invalid_parameter_rejection(self):
        invalid = check('invalid-cap-0','max_tokens','failed',parameters={'max_tokens':0})
        over = check('cap-10','max_tokens','failed',parameters={'max_tokens':10},raw={'check':{'measurements':{'output_tokens':28}}})
        for values, phrase in [([invalid], '限长检查有 1 项需核查'), ([invalid,over], '观察到输出超限（1 项）')]:
            summary = build_summary({},values,{})
            cap = next(x for x in summary['items'] if x['id']=='max_tokens')
            self.assertEqual(cap['conclusion'],phrase)
        self.assertIn('上限 10 → 返回 28',cap['detail'])

    def test_injection_does_not_claim_upstream_prompt_modification(self):
        c = check('matrix-injection-canary','security','failed')
        summary=build_summary({},[c],{})
        self.assertIn('指令隔离存在风险',summary['headline'])
        injection=next(x for x in summary['items'] if x['id']=='injection')
        self.assertIn('未验证上游是否添加提示词',injection['text'])

    def test_browser_normalized_ids_preserve_injection_evidence(self):
        result=normalize_browser_report({'records':[{'kind':'general','model':'fixture','result':{'checks':[{'id':'instruction_hierarchy','name':'指令层级','dimensions':['security'],'status':'failed'}]}}]})
        self.assertIn('指令隔离存在风险',build_report_data(result)['executive_summary']['headline'])

    def test_zero_cap_is_not_overshoot_even_with_legacy_reason_code(self):
        c=check('invalid-cap-0','max_tokens','failed',parameters={'max_tokens':0},reason_code='output_cap_exceeded')
        self.assertNotIn('观察到输出超限',build_summary({},[c],{})['headline'])

    def test_cache_weighted_token_ratio_excludes_cold_and_changed_prefix(self):
        rounds = [{'variant':variant,'request_id':variant,'status':'passed','cache_read_tokens':read,'total_input_tokens':total} for variant,read,total in [('cold',0,50000),('warm',8000,10000),('suffix_changed',16000,20000),('prefix_changed',0,60000)]]
        result={'matrix_validation':{'metrics':{'cache':{'rounds':rounds}}}}
        before=deepcopy(result)
        summary=cache_summary(result,[check('cache','cache')])
        self.assertEqual(summary['cache']['percent'],80)
        self.assertEqual(summary['cache']['warm_requests'],2)
        self.assertEqual(result,before)

    def test_missing_negative_boolean_or_excess_cache_usage_is_not_zero(self):
        for read,total in [(None,100),(True,100),(-1,100),(101,100),(0,None)]:
            result={'matrix_validation':{'metrics':{'cache':{'rounds':[{'variant':'warm','status':'passed','cache_read_tokens':read,'total_input_tokens':total}]}}}}
            summary=cache_summary(result,[check('cache','cache')])
            self.assertNotIn('cache',summary)
            self.assertEqual(summary['status'],'inconclusive')

    def test_native_claude_denominator_already_normalized(self):
        c=check('cache','cache',cache_observations=[{'sample_id':'cache-2','input_tokens':10000,'cache_read_input_tokens':8000,'cache_creation_input_tokens':1000}])
        self.assertEqual(cache_summary({},[c])['cache']['percent'],80)
        self.assertEqual(usage_pair({'input_tokens':1000,'cache_read_input_tokens':8000,'cache_creation_input_tokens':1000}),(8000,10000))
        self.assertEqual(usage_pair({'input_tokens':10000,'input_tokens_details':{'cached_tokens':8000}}),(8000,10000))

    def test_matrix_failure_cannot_supply_a_successful_warm_ratio(self):
        result={'matrix_validation':{'metrics':{'cache':{'rounds':[{'request_id':'warm','variant':'warm','status':'failed','cache_read_tokens':80,'total_input_tokens':100}]}},'samples':[{'id':'warm','response':{'status':500}}]}}
        self.assertNotIn('cache',cache_summary(result,[check('cache','cache')]))

    def test_browser_usage_requires_exact_association_and_successful_http(self):
        c=check('cache','cache',parameters={'round':'warm_1'},request_ids=['request-1'])
        r={'id':'request-1','http_status':200,'response_body':{'usage':{'prompt_tokens':10000,'prompt_tokens_details':{'cached_tokens':8000}}}}
        result={'browser_requests':[r]}
        self.assertEqual(cache_summary(result,[c])['cache']['percent'],80)
        c['request_ids']=['other']
        self.assertNotIn('cache',cache_summary(result,[c]))
        c['request_ids']=['request-1']; r['http_status']=500
        self.assertNotIn('cache',cache_summary(result,[c]))

    def test_parallel_latency_is_not_summed_as_total_runtime(self):
        result={'started_at':1700000000,'finished_at':1700000002,'samples':[{'id':'one','duration_ms':1800},{'id':'two','duration_ms':1700}]}
        timing=build_timing(result)
        self.assertEqual(timing['duration_ms'],2000)
        self.assertEqual((timing['request_p50_ms'],timing['request_p95_ms']),(1750,1795))
        self.assertIn('UTC+08:00',timing['started_at'])

    def test_missing_runtime_not_fabricated_from_export_time(self):
        result=normalize_browser_report({'records':[{'kind':'general','model':'fixture','result':{'checks':[{'name':'protocol','status':'passed'}]}}]})
        self.assertIsNone(result['started_at'])
        timing=build_timing(result)
        self.assertIsNone(timing['duration_ms'])
        self.assertEqual(timing['label'],'未记录')

    def test_browser_total_and_request_window_are_distinguished(self):
        record={'kind':'general','model':'fixture','created_at':1700000000,'duration_ms':2000,'result':{'checks':[{'name':'protocol','status':'passed'}], 'requests':[{'id':'a','started_at':1700000000,'duration_ms':1000}]}}
        result=normalize_browser_report({'records':[record]})
        self.assertEqual(build_report_data(result)['executive_summary']['duration']['total_ms'],2000)
        del record['duration_ms']
        result=normalize_browser_report({'records':[record]})
        timing=build_timing(result)
        self.assertEqual(timing['kind'],'request_window')
        self.assertIsNone(timing['total_ms'])
        self.assertIn('非完整测试耗时',timing['source'])

    def test_pressure_stage_elapsed_and_missing_are_explicit(self):
        result={'matrix_validation':{'metrics':{'stress':{'stages':[{'concurrency':4,'duration_ms':1500},{'concurrency':8}]}}}}
        stages=build_timing(result)['stages']
        self.assertEqual([s['duration_label'] for s in stages],['1.5 秒','未记录'])

    def test_batch_does_not_attribute_mixed_results_to_one_model(self):
        summary=build_summary({'suite':'batch_acceptance'},[check('one','protocol'),check('two','tools','failed')],{})
        self.assertEqual(len(summary['items']),1)
        self.assertEqual(summary['headline'],'各模型独立判读')

    def test_executed_unscorable_check_is_pending_not_uncovered(self):
        c=check('cap','max_tokens','inconclusive',score_applicable=False)
        summary=build_summary({},[c],{})
        limit=next(x for x in summary['items'] if x['id']=='max_tokens')
        self.assertEqual(limit['status'],'inconclusive')
        self.assertIn('1 项待确认',limit['detail'])

    def test_observed_zero_cache_is_visible_but_not_capability_failure(self):
        c=check('cache','cache','inconclusive',score_applicable=False)
        result={'matrix_validation':{'metrics':{'cache':{'rounds':[{'variant':'warm','status':'passed','cache_read_tokens':0,'total_input_tokens':100}]}}}}
        summary=build_summary(result,[c],{})
        cache=next(x for x in summary['items'] if x['id']=='cache')
        self.assertEqual(cache['status'],'inconclusive')
        self.assertEqual(cache['cache']['percent'],0)
        self.assertIn('复用率 0%',summary['headline'])

    def test_mixed_results_keep_anomalies_but_do_not_dismiss_entire_capability(self):
        checks=[check('p%s'%i,'protocol') for i in range(8)]+[check('anomaly','protocol','failed')]+[check('unknown%s'%i,'protocol','inconclusive') for i in range(2)]
        score=_report_score(checks,{'suite':'browser_report'})
        before=deepcopy(score)
        summary=build_summary({},checks,score)
        basic=next(x for x in summary['items'] if x['id']=='basic')
        self.assertEqual((basic['status'],basic['status_label'],basic['tone']),('failed','部分异常','attention'))
        self.assertIn('多数检查通过 · 1 项异常',basic['conclusion'])
        self.assertIn('8 项通过、1 项异常；另 2 项待确认',basic['detail'])
        self.assertEqual(score,before)

    def test_grade_thresholds_match_single_score_and_unknown_never_gets_a_grade(self):
        for total,label in [(100,'优质资源'),(90,'优质资源'),(89,'中等资源'),(70,'中等资源'),(69,'低等级资源'),(0,'低等级资源'),(None,'待评估')]:
            score={'weighted_total':total,'weight_covered':90,'weight_total':100,'evidence':{'resolution_percent':100,'sample_count':12,'scored_failed':1}}
            grade=resource_grade(score)
            self.assertEqual(grade['label'],label)
            self.assertFalse(grade['provisional'])
            if total is not None:self.assertIn('仍有 1 项异常',grade['note'])

    def test_high_score_with_limited_evidence_is_provisional(self):
        score={'weighted_total':100,'weight_covered':20,'weight_total':100,'evidence':{'resolution_percent':50,'sample_count':1}}
        grade=resource_grade(score)
        self.assertEqual(grade['label'],'优质资源')
        self.assertTrue(grade['provisional'])
        self.assertIn('可评分模块权重不足',grade['note'])
        self.assertIn('能力请求不足',grade['note'])

    def test_pressure_stages_deduplicate_exact_evidence_not_concurrency(self):
        matrix={'concurrency':4,'planned':8,'completed':8,'success_rate':.875,'request_ids':['m1','m2'],'duration_ms':2000}
        native={**matrix,'request_ids':['n1','n2'],'success_rate':1,'duration_ms':1000}
        result={'matrix_validation':{'metrics':{'stress':{'stages':[matrix]}}}}
        checks=[check('matrix-stress-stage-4','stress','failed',metrics=deepcopy(matrix),evidence_category='aggregate'),check('stress','stress',metrics=native)]
        checks[0]['metadata']['source']='matrix_validation'
        summary=build_summary(result,checks,{})
        stages=summary['timing']['stages']
        self.assertEqual(len(stages),2)
        self.assertEqual([s['source'] for s in stages],['matrix_validation','native'])
        pressure=next(x for x in summary['items'] if x['id']=='pressure')
        self.assertIn('16/16',pressure['detail'])
        self.assertIn('87.5%–100%',pressure['detail'])
        self.assertLess(len(pressure['text']),180)
        self.assertEqual(pressure['tone'],'attention')

    def test_matrix_stage_fallback_keeps_its_source(self):
        c=check('matrix-stress-stage-4','stress',metrics={'concurrency':4,'duration_ms':2000})
        c['metadata']['source']='matrix_validation'
        self.assertEqual(build_timing({},[c])['stages'][0]['label'],'参数矩阵 · 并发 4')

    def test_cancelled_pressure_partial_completion_never_claims_full_validation(self):
        s={'concurrency':2,'planned':100,'completed':1,'success_rate':1,'duration_ms':3000}
        summary=build_summary({'matrix_validation':{'metrics':{'stress':{'stages':[s]}}}},[],{})
        pressure=next(x for x in summary['items'] if x['id']=='pressure')
        self.assertIn('1/100',pressure['detail'])
        self.assertEqual(pressure['status_label'],'负载未测完')

    def test_zero_cache_is_scoped_observation_even_if_other_cache_assertions_failed(self):
        result={'matrix_validation':{'metrics':{'cache':{'rounds':[{'variant':'warm','status':'passed','cache_read_tokens':0,'total_input_tokens':100}]}}}}
        value=cache_summary(result,[check('cache-fields','cache','failed')])
        self.assertEqual((value['status'],value['tone'],value['status_label']),('failed','attention','未观察到复用'))
        self.assertIn('1 项缓存检查异常',value['detail'])
        self.assertIn('不代表模型不支持缓存',value['detail'])


if __name__=='__main__': unittest.main()
