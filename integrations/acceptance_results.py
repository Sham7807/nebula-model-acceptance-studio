"""Acceptance verdicts distinguish observed failures from missing evidence."""

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
    entries = result.get('checks') if result.get('suite') in ('ccmax', 'ccmax_acceptance') else result.get('cases')
    entries = entries or []
    local = [x for x in entries if 'tolerance_boundaries' in x.get('id','')]
    not_applicable = [x for x in entries if x.get('applicable') is False]
    remote = [x for x in entries if x not in local and x not in not_applicable]
    counts = {status: sum(x.get('status') == status for x in remote) for status in ('passed','failed','inconclusive','skipped','not_covered')}
    unknown = sum(x.get('status') not in counts for x in remote)
    transport = result.get('transport') or {}
    transport_bad = [x for x in transport.get('checks',[]) if x.get('status')=='failed']
    untested = max(0, int(result.get('summary',{}).get('total') or 0) - int(result.get('summary',{}).get('completed') or 0))
    if result.get('status')=='cancelled':
        status,label,detail='inconclusive','已取消 · 结论不完整','保留已完成样本，未完成项不计为通过。'
    elif counts['failed'] or transport_bad:
        extra=f"、{len(transport_bad)} 项附加传输检查异常" if transport.get('checks') else ''
        status,label,detail='failed','未满足本轮验收要求',f"发现 {counts['failed']} 项验收失败{extra}；请查看逐项证据。"
    elif result.get('status')!='completed' or counts['inconclusive'] or counts['not_covered'] or unknown or untested or not counts['passed']:
        status,label,detail='inconclusive','证据不足 · 无法确认通过','存在调用错误、未执行项或无法判定项，不能把本次运行视为通过。'
    else:
        status,label,detail='passed','本轮已执行检查通过','仅对本轮采样负责；不代表模型身份认证或长期稳定性保证。'
    result['verdict']={'status':status,'label':label,'detail':detail,'counts':counts,'skipped':counts['skipped'],'not_applicable':len(not_applicable),'untested':untested,'local_checks':len(local),'transport_failures':len(transport_bad),'unclassified':unknown}
    return result
