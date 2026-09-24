"""Pure fixtures: admission and costs must not make model requests."""
from copy import deepcopy
import math
import unittest

import channel_admission as admission


def sample(identity, task=None, **kw):
    row = {'id': identity, 'status': 'passed', 'task_id': task, 'traffic_class': 'normal', 'attempt': 1,
           'request': {'url': 'https://fixture.invalid/v1/messages'},
           'response': {'status': 200, 'body': {'usage': {'input_tokens': 100, 'output_tokens': 10,
                 'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 300}}}}
    row.update(kw)
    return row


def report(n=200):
    names = ['short', 'long_output', 'stream', 'tools']
    counts = {name: sum(i % 4 == ix for i in range(n)) for ix, name in enumerate(names)}
    return {'suite': 'claude_acceptance', 'status': 'completed', 'configuration': {
                'model': 'claude-fixture', 'request_format': 'anthropic',
                'pricing': {'currency': 'CNY', 'version': 'fixture-1', 'input_per_million': 10,
                            'output_per_million': 50, 'cache_read_per_million': 1, 'cache_write_per_million': 20}},
            'checks': [{'id': 'authentication', 'status': 'passed', 'request_ids': ['invalid-auth']}],
            'score': {'total': 100},
            'samples': [sample('baseline'), sample('invalid-auth', traffic_class='control', response={'status': 401, 'body': {}})],
            'production_validation': {'status': 'completed', 'configuration': {'concurrency': 4},
                'samples': [sample('prod-%d' % i, 'task-%d' % i, workload=names[i % 4]) for i in range(n)],
                'metrics': {'normal_tasks': n, 'business_successful': n, 'first_attempt_successful': n,
                    'first_attempt_success_rate': 1, 'eventual_success_rate': 1, 'protocol_success_rate': 1,
                    'business_success_rate': 1, 'observation_seconds': 1801, 'duration_seconds': 1801,
                    'first_content_p95_ms': 800, 'latency_p95_ms': 5000, 'max_concurrency': 4,
                    'workload_fingerprint': 'same-workload-123',
                    'workloads': {name: {'tasks': count, 'business_successful': count} for name, count in counts.items()},
                    'time_buckets': [{'tasks': n//2}, {'tasks': n-n//2}]}}}


def ledger(value, amount=1):
    return [{'request_id': item['request_id'], 'amount': amount, 'currency': 'CNY'} for item in admission.ledger_template(value)]


class AdmissionTests(unittest.TestCase):
    def test_complete_gates_can_approve_but_not_prove_sla(self):
        value = report(); before = deepcopy(value)
        got = admission.evaluate(value)
        self.assertEqual(got['status'], 'approved')
        self.assertEqual(value, before)
        self.assertTrue(all(g['status'] == 'passed' for g in got['gates']))
        self.assertLess(got['reliability']['confidence']['lower_percent'], 99.5)
        self.assertIn('长期 SLA', got['reliability']['confidence']['note'])
        self.assertEqual(got['validated_envelope']['concurrency'], 4)

    def test_high_score_cannot_cover_failed_authentication(self):
        value = report(); value['checks'][0]['status'] = 'failed'
        self.assertEqual(admission.evaluate(value)['status'], 'rejected')
        self.assertEqual(value['score']['total'], 100)

    def test_expected_401_control_is_not_normal_auth_failure(self):
        got = admission.evaluate(report())
        auth = next(g for g in got['gates'] if g['id'] == 'authentication')
        self.assertEqual(auth['status'], 'passed')
        self.assertEqual(got['reliability']['business_success_rate'], 1)

    def test_valid_key_401_overrides_nominal_success_metrics(self):
        value = report(); value['production_validation']['samples'][0]['response']['status'] = 401
        self.assertEqual(admission.evaluate(value)['status'], 'rejected')

    def test_unknown_native_auth_never_approves(self):
        value = report(); value['checks'][0]['status'] = 'inconclusive'
        self.assertEqual(admission.evaluate(value)['status'], 'retest')

    def test_high_score_without_production_evidence_requires_retest(self):
        value = report(); del value['production_validation']
        value['checks'].append({'id': 'stress', 'status': 'inconclusive'})
        self.assertEqual(admission.evaluate(value)['status'], 'retest')

    def test_short_or_single_window_is_provisional(self):
        for mutate in ('duration', 'buckets'):
            value = report()
            if mutate == 'duration': value['production_validation']['metrics']['observation_seconds'] = 30
            else: value['production_validation']['metrics']['time_buckets'] = [{'tasks': 200}]
            with self.subTest(mutate=mutate):
                self.assertEqual(admission.evaluate(value)['status'], 'limited')

    def test_partial_small_sample_does_not_claim_approved(self):
        got = admission.evaluate(report(20))
        self.assertEqual(got['status'], 'limited')
        self.assertEqual(got['reliability']['normal_tasks'], 20)
        self.assertLess(got['reliability']['confidence']['lower_percent'], 85)

    def test_required_workload_failure_is_not_hidden_by_other_workloads(self):
        value = report(); value['production_validation']['metrics']['workloads']['tools']['business_successful'] = 0
        self.assertEqual(admission.evaluate(value)['status'], 'rejected')

    def test_missing_workload_requires_more_evidence(self):
        value = report(); del value['production_validation']['metrics']['workloads']['tools']
        self.assertEqual(admission.evaluate(value)['status'], 'limited')

    def test_retry_success_does_not_hide_initial_errors(self):
        value = report(); value['production_validation']['metrics']['first_attempt_success_rate'] = 0.9
        self.assertEqual(admission.evaluate(value)['status'], 'rejected')

    def test_latency_breach_limits_instead_of_capability_downgrade(self):
        value = report(); value['production_validation']['metrics']['latency_p95_ms'] = 150000
        self.assertEqual(admission.evaluate(value)['status'], 'limited')

    def test_cancelled_run_cannot_approve(self):
        value = report(); value['status'] = 'cancelled'
        self.assertEqual(admission.evaluate(value)['status'], 'retest')

    def test_malformed_rates_are_not_zero_or_success(self):
        for number in (True, -1, math.nan, 99.5):
            value = report(); value['production_validation']['metrics']['business_success_rate'] = number
            with self.subTest(number=number): self.assertEqual(admission.evaluate(value)['status'], 'retest')

    def test_custom_policy_alias_and_validation(self):
        value = report(20); value['configuration']['admission'] = {'min_samples': 20, 'min_observation_seconds': 10}
        self.assertEqual(admission.evaluate(value)['status'], 'approved')
        for policy in ({'min_samples': True}, {'target_success_rate': math.nan}, {'required_workloads': []}, {'typo': 1}):
            with self.subTest(policy=policy), self.assertRaises(ValueError): admission.configuration({'admission': policy})

    def test_shared_production_auth_controls_enable_other_suites(self):
        value = report(); value['suite'] = 'kvv11'; value['samples'] = []; value['checks'] = []
        value['production_validation']['cases'] = [
            {'id': 'production-baseline', 'status': 'passed', 'request_ids': ['production-baseline']},
            {'id': 'production-authentication', 'status': 'passed', 'request_ids': ['production-invalid-auth']}]
        value['production_validation']['samples'] += [sample('production-baseline', traffic_class='control'),
            sample('production-invalid-auth', traffic_class='control', response={'status': 401, 'body': {}})]
        self.assertEqual(admission.evaluate(value)['status'], 'approved')

    def test_multiround_task_latency_is_not_short_attempt_latency(self):
        value = report(); value['production_validation']['metrics']['task_latency_p95_ms'] = 130000
        got = admission.evaluate(value)
        self.assertEqual(got['status'], 'limited')
        self.assertEqual(got['reliability']['attempt_latency_p95_ms'], 5000)
        self.assertEqual(got['reliability']['latency_p95_ms'], 130000)

    def test_actual_client_close_failure_limits_channel(self):
        value = report(); p = value['production_validation']
        p['samples'].append(sample('cancel-observed', 'cancel-1', workload='cancellation', traffic_class='control',
                                  evidence={'first_content_ms': 0, 'client_response_closed': False}))
        p['metrics']['cancellation'] = {'attempted': 1, 'client_closed': 0, 'sample_ids': ['cancel-observed']}
        got = admission.evaluate(value)
        self.assertEqual(got['status'], 'limited')
        gate = next(g for g in got['gates'] if g['id'] == 'cancellation')
        self.assertEqual(gate['status'], 'failed'); self.assertEqual(gate['request_ids'], ['cancel-observed'])

    def test_no_cancel_opportunity_or_fault_is_not_fake_pass_or_block(self):
        value = report(); p = value['production_validation']
        p['samples'].append(sample('cancel-no-content', 'cancel-1', workload='cancellation', traffic_class='control',
                                  evidence={'first_content_ms': None, 'client_response_closed': False}))
        p['metrics']['cancellation'] = {'attempted': 1, 'client_closed': 0, 'sample_ids': ['cancel-no-content']}
        p['metrics']['recovery'] = {'tasks_retried': 0, 'recovered_tasks': 0}
        got = admission.evaluate(value)
        self.assertEqual(got['status'], 'approved')
        self.assertTrue(all(g['status'] == 'inconclusive' for g in got['gates'] if g['id'] in ('recovery', 'cancellation')))
        self.assertEqual(len(got['observations']), 2)

    def test_observed_recovery_exhaustion_not_hidden_by_large_denominator(self):
        value = report(); value['production_validation']['metrics']['recovery'] = {'tasks_retried': 1, 'recovered_tasks': 0}
        self.assertEqual(admission.evaluate(value)['status'], 'limited')
        value['production_validation']['metrics']['recovery']['recovered_tasks'] = 1
        self.assertEqual(admission.evaluate(value)['status'], 'approved')

    def test_wilson_zero_success_and_unknown_cases(self):
        self.assertEqual(admission.confidence_interval(0, 100)['lower_percent'], 0)
        self.assertLess(admission.confidence_interval(0, 100)['upper_percent'], 4)
        self.assertIsNone(admission.confidence_interval(0, 0)['upper_percent'])


class CostTests(unittest.TestCase):
    def test_anthropic_non_cached_input_does_not_double_count(self):
        value = report(4); got = admission.estimate_cost(value)
        row = next(x for x in got['rows'] if x['request_id'] == 'baseline')
        self.assertEqual(row['units']['total_input'], 600)
        self.assertEqual(row['units']['input'], 100)
        self.assertAlmostEqual(row['estimated_amount'], .0077)
        self.assertIsNone(got['estimated_total'])  # failed auth has no billing usage
        self.assertIn('invalid-auth', got['missing_usage_ids'])

    def test_sse_cumulative_snapshots_are_not_summed(self):
        row = sample('stream', response={'status': 200, 'body': 'data: SSE'}, evidence={'sse': {'usage': [
            {'value': {'input_tokens': 100, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 0, 'output_tokens': 0}},
            {'value': {'output_tokens': 10}}, {'value': {'output_tokens': 20}}]}})
        usage, error = admission.normalize_usage(row, 'anthropic')
        self.assertIsNone(error)
        self.assertEqual(usage['total_input'], 1100)
        self.assertEqual(usage['output'], 20)

    def test_chat_and_responses_cache_already_in_input(self):
        variants = [({'prompt_tokens': 1000, 'completion_tokens': 30, 'prompt_tokens_details': {'cached_tokens': 800}}, 'chat/completions'),
                    ({'input_tokens': 1000, 'output_tokens': 30, 'input_tokens_details': {'cached_tokens': 800}}, 'responses')]
        for raw, path in variants:
            row = sample('call', request={'url': 'https://fixture.invalid/v1/'+path}, response={'body': {'usage': raw}})
            units, error = admission.normalize_usage(row, 'openai')
            self.assertIsNone(error); self.assertEqual(units['input'], 200); self.assertEqual(units['cache_read'], 800)

    def test_missing_prices_not_free_and_explicit_zero_ledger_valid(self):
        value = report(4); value['configuration'].pop('pricing')
        self.assertIsNone(admission.estimate_cost(value)['estimated_total'])
        got = admission.estimate_cost(value, ledger(value, 0))
        self.assertEqual(got['status'], 'actual'); self.assertEqual(got['actual_total'], 0)
        self.assertEqual(got['production']['actual_cost_per_success'], 0)

    def test_failed_retry_cost_included_probe_cost_not_in_business_cost(self):
        value = report(4)
        retry = deepcopy(value['production_validation']['samples'][0]); retry['id'] = 'retry-0'; retry['attempt'] = 2
        value['production_validation']['samples'].append(retry)
        got = admission.estimate_cost(value, ledger(value))
        self.assertEqual(got['request_count'], 7)
        self.assertEqual(got['actual_total'], 7)
        self.assertEqual(got['production']['actual_total'], 5)
        self.assertEqual(got['production']['actual_cost_per_success'], 1.25)

    def test_missing_attempt_ledger_blocks_cost_per_success(self):
        value = report(4); all_rows = ledger(value)
        got = admission.estimate_cost(value, all_rows[:-1])
        self.assertIsNone(got['actual_total']); self.assertIsNone(got['production']['actual_cost_per_success'])
        self.assertEqual(got['actual_partial_total'], 5)

    def test_partial_sample_evidence_does_not_divide_by_unobserved_tasks(self):
        value = report(4); value['production_validation']['samples'].pop()
        got = admission.estimate_cost(value, ledger(value))
        self.assertFalse(got['production']['task_coverage_complete'])
        self.assertIsNone(got['production']['actual_cost_per_success'])

    def test_duplicate_evidence_alias_charged_once(self):
        value = report(4); value['samples'].append(deepcopy(value['production_validation']['samples'][0]))
        got = admission.estimate_cost(value, ledger(value))
        self.assertEqual(got['request_count'], 6)
        self.assertEqual(got['production']['request_count'], 4)

    def test_control_and_cancel_cost_retained_in_total_excluded_business(self):
        value = report(4); value['production_validation']['samples'].append(sample('cancel', 'cancel-task', traffic_class='control'))
        got = admission.estimate_cost(value, ledger(value))
        self.assertEqual(got['actual_total'], 7); self.assertEqual(got['production']['actual_cost_per_success'], 1)

    def test_bad_ledgers_rejected(self):
        value = report(4)
        invalids = [ledger(value)+[ledger(value)[0]], [{'request_id': 'unknown', 'amount': 1, 'currency': 'CNY'}],
                    [{'request_id': 'baseline', 'amount': 1, 'currency': 'USD'}]]
        invalids += [[{'request_id': 'baseline', 'amount': amount, 'currency': 'CNY'}] for amount in (-1, math.nan, math.inf, True, None)]
        mixed = ledger(value); mixed[-1]['currency'] = 'USD'; invalids.append(mixed)
        for rows in invalids:
            with self.subTest(rows=rows[:1]), self.assertRaises(ValueError): admission.reconcile(value, {'rows': rows})

    def test_no_declared_quote_currency_does_not_invent_mismatch(self):
        value = report(4); value['configuration'].pop('pricing')
        rows = ledger(value); [r.update(currency='USD') for r in rows]
        got = admission.estimate_cost(value, rows)
        self.assertEqual(got['currency'], 'USD'); self.assertEqual(got['actual_total'], 6)

    def test_actual_ledger_roundtrip_preserved_not_mutated(self):
        value = report(4); before = deepcopy(value); rows = ledger(value)
        normalized = admission.reconcile(value, {'rows': rows, 'note': 'fixture billing'})
        self.assertEqual(value, before)
        value['billing'] = normalized
        self.assertEqual(admission.evaluate(value)['cost']['actual_total'], 6)

    def test_unknown_usage_and_invalid_cache_are_not_fabricated(self):
        for usage in ({'input_tokens': True, 'output_tokens': 1}, {'prompt_tokens': 1, 'completion_tokens': 2, 'prompt_tokens_details': {'cached_tokens': 10}}):
            units, error = admission.normalize_usage(sample('bad', response={'body': {'usage': usage}}))
            self.assertIsNone(units); self.assertTrue(error)

    def test_reference_comparison_requires_matched_scope_and_actual_ledger(self):
        value = report(4); p = value['configuration']['pricing']
        p.update(reference_cost_per_success=2, reference_currency='CNY', reference_workload_fingerprint='same-workload-123', reference_request_format='anthropic')
        self.assertEqual(admission.estimate_cost(value)['comparison']['status'], 'unverified')
        got = admission.estimate_cost(value, ledger(value))
        self.assertEqual(got['comparison']['status'], 'lower'); self.assertEqual(got['comparison']['saving_percent'], 50)
        p['reference_workload_fingerprint'] = 'different'
        self.assertEqual(admission.estimate_cost(value, ledger(value))['comparison']['status'], 'unverified')

    def test_kvv_joined_finish_body_usage_and_sse_are_readable(self):
        body = 'data: {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}\n\ndata: [DONE]\n\n'
        row = {'type': 'request_finish', 'body': body, 'url': 'https://fixture.invalid/chat/completions'}
        self.assertEqual(admission.extract_usage(row)['completion_tokens'], 20)
        units, error = admission.normalize_usage(row, 'openai')
        self.assertIsNone(error); self.assertEqual(units['input'], 100)
        row['body'] = '{"usage":{"prompt_tokens":10,"completion_tokens":2}}'
        self.assertEqual(admission.extract_usage(row)['prompt_tokens'], 10)

    def test_cancelled_truncated_and_wire_retry_usage_require_bill(self):
        for kw in ({'termination': 'client_cancel_probe'}, {'body_truncated': True}, {'wire_requests': 2}):
            with self.subTest(kw=kw):
                units, error = admission.normalize_usage(sample('partial', **kw))
                self.assertIsNone(units); self.assertTrue(error)

    def test_tiny_real_cost_is_not_rounded_to_free(self):
        value = report(4); got = admission.estimate_cost(value, ledger(value, .00000000001))
        self.assertGreater(got['actual_total'], 0)
        self.assertGreater(got['production']['actual_cost_per_success'], 0)

    def test_template_blank_is_not_a_zero_invoice(self):
        value = report(4); rows = admission.ledger_template(value)
        self.assertTrue(all(r['amount'] is None for r in rows))
        with self.assertRaises(ValueError): admission.reconcile(value, {'rows': rows})


if __name__ == '__main__': unittest.main()
