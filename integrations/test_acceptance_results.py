"""Offline verdict regressions; no provider requests or credentials are used."""
import copy
import unittest
from acceptance_results import decorate

LOCAL_IDS = [
    'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_token_tolerance_boundaries[18-True]',
    'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_token_tolerance_boundaries[17-False]',
    'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_token_tolerance_boundaries[22-True]',
    'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_token_tolerance_boundaries[23-False]',
]


def entry(status='passed', identity='remote-case'):
    return {'id': identity, 'status': status}


def result(cases=None, **changes):
    cases = [entry()] if cases is None else cases
    value = {'suite': 'kvv11', 'status': 'completed', 'cases': cases,
             'summary': {'total': len(cases), 'completed': len(cases)}}
    value.update(changes)
    return value


class VerdictTests(unittest.TestCase):
    def verdict(self, value):
        return decorate(copy.deepcopy(value))['verdict']

    def test_four_local_tolerance_passes_cannot_prove_channel_success(self):
        local = [entry(identity=name) for name in LOCAL_IDS]
        verdict = self.verdict(result(local))
        self.assertEqual(verdict['status'], 'inconclusive')
        self.assertEqual(verdict['local_checks'], 4)
        self.assertEqual(verdict['counts']['passed'], 0)

    def test_local_checks_are_excluded_from_remote_pass_fail_counts(self):
        local = [entry(identity=name) for name in LOCAL_IDS]
        cases = local + [entry(identity='remote-a'), entry(identity='remote-b')]
        verdict = self.verdict(result(cases))
        self.assertEqual(verdict['status'], 'passed')
        self.assertEqual(verdict['counts']['passed'], 2)
        self.assertEqual(verdict['local_checks'], 4)
        unavailable = self.verdict(result(local + [entry('inconclusive', 'HTTP-401')]))
        self.assertEqual(unavailable['status'], 'inconclusive')
        self.assertEqual(unavailable['counts']['passed'], 0)

    def test_explicit_failure_wins_over_missing_evidence_or_execution_error(self):
        cases = [entry(), entry('failed', 'mismatch'), entry('inconclusive', 'timeout')]
        for status in ('completed', 'error', 'running', None, 'unknown'):
            with self.subTest(status=status):
                verdict = self.verdict(result(cases, status=status, summary={'total': 8, 'completed': 3}))
                self.assertEqual(verdict['status'], 'failed')
                self.assertEqual(verdict['counts']['failed'], 1)
                self.assertEqual(verdict['untested'], 5)

    def test_failed_independent_transport_check_prevents_success(self):
        value = result(transport={'checks': [entry('passed', 'headers'), entry('failed', 'sse_done')]})
        verdict = self.verdict(value)
        self.assertEqual(verdict['status'], 'failed')
        self.assertEqual(verdict['transport_failures'], 1)

    def test_cancelled_priority_preserves_observed_failures_without_complete_verdict(self):
        for cases in ([entry()], [entry('failed')]):
            value = result(cases, status='cancelled')
            verdict = self.verdict(value)
            self.assertEqual(verdict['status'], 'inconclusive')
            self.assertIn('取消', verdict['label'])
            self.assertEqual(verdict['counts']['failed'], sum(c['status'] == 'failed' for c in cases))

    def test_error_or_missing_execution_status_cannot_be_passed(self):
        for status in ('error', 'running', 'queued', '', None, 'unexpected'):
            with self.subTest(status=status):
                self.assertEqual(self.verdict(result(status=status))['status'], 'inconclusive')
        missing = result(); missing.pop('status')
        self.assertEqual(self.verdict(missing)['status'], 'inconclusive')

    def test_unexecuted_cases_or_no_remote_evidence_cannot_pass(self):
        self.assertEqual(self.verdict(result(summary={'total': 11, 'completed': 1}))['status'], 'inconclusive')
        self.assertEqual(self.verdict(result([], summary={'total': 11, 'completed': 11}))['status'], 'inconclusive')
        self.assertEqual(self.verdict(result([entry('skipped')]))['status'], 'inconclusive')

    def test_skipped_official_cases_are_counted_separately_from_executed_passes(self):
        verdict = self.verdict(result([entry(), entry('skipped', 'official-skip')], suite='kvvfull'))
        self.assertEqual(verdict['status'], 'passed')
        self.assertEqual(verdict['counts']['passed'], 1)
        self.assertEqual(verdict['skipped'], 1)
        self.assertIn('已执行', verdict['label'])

    def test_not_covered_or_unknown_check_status_is_missing_evidence(self):
        for status in ('not_covered', 'cancelled', 'unknown', None):
            with self.subTest(status=status):
                verdict = self.verdict(result([entry(), entry(status, 'uncovered')]))
                self.assertEqual(verdict['status'], 'inconclusive')

    def test_ccmax_verdict_uses_aggregate_checks_not_sample_count(self):
        value = {'suite': 'ccmax_acceptance', 'status': 'completed',
                 'summary': {'total': 6, 'completed': 6},
                 'checks': [entry(identity='check-%s' % i) for i in range(8)],
                 'samples': [{'status': 'passed', 'assessments': [entry('not_covered', 'ordinary-sse-tool')]}],
                 'cases': [entry('failed', 'irrelevant-kvv-key')]}
        verdict = self.verdict(value)
        self.assertEqual(verdict['status'], 'passed')
        self.assertEqual(verdict['counts']['passed'], 8)
        value['checks'][0]['status'] = 'not_covered'
        self.assertEqual(self.verdict(value)['status'], 'inconclusive')

    def test_partial_usage_capture_does_not_override_an_official_assertion(self):
        value = result(transport={'checks': [entry('inconclusive', 'caller-stopped-stream')]})
        self.assertEqual(self.verdict(value)['status'], 'passed')
        value['cases'][0]['status'] = 'failed'
        self.assertEqual(self.verdict(value)['status'], 'failed')

    def test_decoration_preserves_raw_official_and_transport_evidence(self):
        value = result([{'id': 'remote', 'status': 'inconclusive', 'pytest_status': 'failed', 'detail': 'HTTP 401'}])
        old = copy.deepcopy(value)
        self.assertIs(decorate(value), value)
        self.assertEqual({k: v for k, v in value.items() if k != 'verdict'}, old)

    def test_blocked_measurement_does_not_count_as_capability_failure(self):
        for code, category in [('rate_limited', 'infrastructure'), ('http_error', 'infrastructure'),
                               ('budget_exhausted', None), ('model_parameter_incompatible', None)]:
            blocked = dict(entry('failed', 'blocked'), reason_code=code, evidence_category=category)
            value = result([entry(), blocked]); before = copy.deepcopy(value)
            verdict = decorate(value)['verdict']
            self.assertEqual(verdict['status'], 'inconclusive')
            self.assertEqual(verdict['counts']['failed'], 0)
            self.assertEqual(verdict['counts']['inconclusive'], 1)
            self.assertEqual(value['cases'], before['cases'])

    def test_aggregate_stress_rows_do_not_duplicate_independent_failures(self):
        value = result([entry('failed', 'load-1'), dict(entry('failed', 'stage-1'), evidence_category='aggregate'),
                        dict(entry('passed', 'positive-control'), evidence_category='control')])
        verdict = self.verdict(value)
        self.assertEqual(verdict['counts']['failed'], 1)
        self.assertEqual(verdict['counts']['passed'], 0)
        self.assertEqual(verdict['ancillary'], 2)

    def test_mixed_blocked_and_true_failure_keeps_failure(self):
        row = dict(entry('failed'), reason_code='rate_limited',
                   reason_codes=['rate_limited','assertion_failed'])
        self.assertEqual(self.verdict(result([row]))['status'], 'failed')

    def test_legacy_aggregate_only_duplicate_id_finding_is_preserved(self):
        value = result([entry(), dict(entry('failed', 'stage-1'), evidence_category='aggregate',
                                     metrics={'duplicate_response_ids':['duplicated-id']})])
        self.assertEqual(self.verdict(value)['status'], 'failed')

    def test_not_applicable_cells_are_handled_without_false_unfinished_count(self):
        value = result([entry()], native_summary={'total':1, 'completed':1}, matrix_validation={
            'cases':[dict(entry('not_covered', 'native-video'), applicable=False, reason_code='protocol_not_applicable')],
            'summary':{'total':1, 'completed':0}})
        self.assertEqual(self.verdict(value)['status'], 'passed')
        self.assertEqual(self.verdict(value)['untested'], 0)

    def test_old_upstream_prompt_observations_cannot_fail_claude_capabilities(self):
        value = result(suite='claude_acceptance', checks=[entry(), entry('failed', 'prompt_exfiltration')],
                       summary={'total':2,'completed':2})
        verdict = self.verdict(value)
        self.assertEqual(verdict['status'], 'passed')
        self.assertEqual(verdict['observational'], 1)


if __name__ == '__main__': unittest.main()
