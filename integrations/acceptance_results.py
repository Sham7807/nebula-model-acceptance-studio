"""Acceptance verdicts distinguish observed failures from missing evidence."""

# These reasons describe a blocked measurement, not an observed capability
# violation. Keep raw statuses untouched (including imported older reports).
BLOCKED_REASONS = frozenset((
    'transport_error', 'authentication_error', 'rate_limited', 'budget_exhausted',
    'evidence_missing', 'usage_missing', 'prerequisite_failed', 'cap_not_exercised',
    'cache_not_observed', 'cache_context_too_small', 'cache_scale_not_reached',
    'cache_scale_insufficient', 'model_parameter_incompatible', 'unsupported_capability',
))
BLOCKED_CATEGORIES = frozenset(('authentication', 'rate_limit', 'timeout', 'infrastructure', 'insufficient_evidence'))


def measurement_status(entry):
    """Return a verdict status without changing the saved assertion."""
    metadata = entry.get('metadata') or {}
    status = entry.get('status')
    reason = entry.get('reason_code') or metadata.get('reason_code')
    category = entry.get('evidence_category') or metadata.get('evidence_category')
    if status == 'failed' and (reason in ('assertion_failed', 'duplicate_response_ids')
                               or 'assertion_failed' in (entry.get('reason_codes') or [])):
        return status
    if status == 'failed' and (reason in BLOCKED_REASONS or category in BLOCKED_CATEGORIES):
        return 'inconclusive'
    return status

def decorate(result):
    if result.get('suite') == 'batch_acceptance':
        children = result.get('results') or []
        statuses = [((child.get('result') or {}).get('verdict') or {}).get('status') for child in children if isinstance(child, dict)]
        if result.get('status') == 'cancelled' or any(s in ('failed', 'error') for s in statuses):
            status, label, detail = ('failed' if any(s in ('failed', 'error') for s in statuses) else 'inconclusive', '批量测试存在未通过模型' if any(s in ('failed', 'error') for s in statuses) else '批量测试未完成', '已保留每个模型的独立子报告；未启动项不会计为通过。')
        elif result.get('status') == 'completed' and children and all(s == 'passed' for s in statuses):
            status, label, detail = 'passed', '所选模型均通过本轮检查', '结论仅对本轮各模型独立测试负责。'
        else:
            status, label, detail = 'inconclusive', '批量结果无法确认全部通过', '部分模型没有完整通过证据，请展开各模型报告。'
        result['verdict'] = {'status': status, 'label': label, 'detail': detail, 'models': len(children)}
        return result
    # ``ccmax`` is the UI suite name; completed runs are persisted as
    # ``ccmax_acceptance``.  Both must use the CCMax check collection so a
    # standalone render cannot accidentally count its checks as KVV cases.
    entries = (result.get('checks') or result.get('cases')) if result.get('suite') in ('claude', 'claude_acceptance') else result.get('checks') if result.get('suite') in ('ccmax', 'ccmax_acceptance') else result.get('cases')
    matrix = result.get('matrix_validation') or {}
    entries = list(entries or []) + list(matrix.get('cases') or [])
    local = [x for x in entries if 'tolerance_boundaries' in x.get('id','')]
    not_applicable = [x for x in entries if x.get('applicable') is False or x.get('reason_code') == 'protocol_not_applicable']
    not_selected = [x for x in entries if x.get('module_disabled') is True]
    def observational(entry):
        metadata = entry.get('metadata') or {}
        dimensions = entry.get('dimensions') or metadata.get('dimensions') or []
        upstream = result.get('suite') in ('claude', 'claude_acceptance') and entry.get('id') in ('prompt_exfiltration', 'prompt_sidechannel', 'token_accounting')
        return upstream or entry.get('evidence_category') == 'observation' or dimensions == ['identity'] or entry.get('id') in ('identity','authenticity','model_identity')
    observations = [x for x in entries if observational(x)]
    ancillary = [x for x in entries if x.get('evidence_category') in ('control', 'aggregate')]
    # Older matrices recorded duplicate-ID violations only in the aggregate;
    # preserve that independent finding until there is a dedicated assertion.
    has_id_assertion = any(x.get('reason_code') == 'duplicate_response_ids' for x in entries)
    legacy_id_failures = [x for x in ancillary if not has_id_assertion and x.get('status') == 'failed' and (x.get('metrics') or {}).get('duplicate_response_ids')]
    remote = [x for x in entries if x not in local and x not in not_applicable and x not in not_selected and x not in observations and (x not in ancillary or x in legacy_id_failures)]
    counts = {status: sum(measurement_status(x) == status for x in remote) for status in ('passed','failed','inconclusive','skipped','not_covered')}
    unknown = sum(measurement_status(x) not in counts for x in remote)
    transport = result.get('transport') or {}
    transport_bad = [x for x in transport.get('checks',[]) if x.get('status')=='failed']
    summary = result.get('native_summary') or result.get('summary') or {}
    untested = max(0, int(summary.get('total') or 0) - int(summary.get('completed') or 0))
    if result.get('native_summary') and matrix:
        matrix_summary = matrix.get('summary') or {}
        handled_no_send = sum(x in not_applicable or x in not_selected for x in (matrix.get('cases') or []))
        untested += int(matrix_summary['unexecuted_cases']) if 'unexecuted_cases' in matrix_summary else max(0, int(matrix_summary.get('total') or 0) - int(matrix_summary.get('completed') or 0) - handled_no_send)
    if result.get('status')=='cancelled':
        status,label,detail='inconclusive','已取消 · 结论不完整','保留已完成样本，未完成项不计为通过。'
    elif counts['failed'] or transport_bad:
        extra=f"、{len(transport_bad)} 项附加传输检查异常" if transport.get('checks') else ''
        status,label,detail='failed','未满足本轮验收要求',f"发现 {counts['failed']} 项验收失败{extra}；请查看逐项证据。"
    elif result.get('status')!='completed' or counts['inconclusive'] or counts['not_covered'] or unknown or untested or not counts['passed']:
        status,label,detail='inconclusive','证据不足 · 无法确认通过','存在调用错误、未执行项或无法判定项，不能把本次运行视为通过。'
    else:
        status,label,detail='passed','本轮已执行检查通过','仅对本轮采样负责；不代表模型身份认证或长期稳定性保证。'
    result['verdict']={'status':status,'label':label,'detail':detail,'counts':counts,'skipped':counts['skipped'],'not_applicable':len(not_applicable),'not_selected':len(not_selected),'untested':untested,'local_checks':len(local),'transport_failures':len(transport_bad),'unclassified':unknown,'observational':len(observations),'ancillary':len(ancillary)}
    return result
