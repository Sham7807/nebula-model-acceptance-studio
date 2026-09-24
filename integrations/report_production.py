"""Shared offline presentation of production admission and reconciled costs."""
import html
import json

WORKLOAD_LABELS={'short':'短业务响应','long_output':'长输出','long_context':'长上下文','stream':'长流式响应','thinking':'推理响应','vision':'图像理解','tools':'多轮工具','cancellation':'主动取消','custom':'业务回放'}
def esc(value): return html.escape(str(value if value is not None else '未记录'),quote=True)
def obj(value): return value if isinstance(value,dict) else {}
def number(value,suffix=''):
    return ('%g'%round(value,4))+suffix if isinstance(value,(int,float)) and not isinstance(value,bool) else '未记录'
def rate(value): return number(value*100,'%') if isinstance(value,(int,float)) and not isinstance(value,bool) else '未记录'
def money(value,currency): return format(value,'.10g')+' '+str(currency or '币种未记录') if isinstance(value,(int,float)) and not isinstance(value,bool) else '待核算'
def table(headers,rows):
    return '<div class="results-scroll"><table class="results-table production-table"><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+cell+'</td>' for cell in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def badge(state):
    label,tone={'passed':('满足条件','passed'),'failed':('需修复','risk'),'inconclusive':('证据待补','neutral')}.get(state,('未覆盖','neutral'))
    return '<span class="badge '+tone+'">'+label+'</span>'
def metrics(items):
    return '<div class="production-kpis">'+''.join('<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>' for label,value in items)+'</div>'
def render(result,data,links):
    if result.get('suite')=='batch_acceptance':
        rows=[]
        for child in data.get('model_admissions') or []:
            a=obj(child.get('admission')); r=obj(a.get('reliability')); c=obj(a.get('cost'))
            rows.append([esc(child.get('model')),esc(a.get('label')),esc(rate(r.get('first_attempt_success_rate'))),esc(r.get('normal_tasks')),esc(c.get('label'))])
        hero='<section class="admission-panel" id="admission"><span class="index">ADMISSION / 模型独立准入</span><h2>逐模型判断接入条件</h2><p>能力分、负载范围和费用均按独立模型判读，不以合并成功率证明单个模型稳定。</p>'+table(['模型','接入建议','首试成功率','正常业务任务','成本证据'],rows)+'</section>'
        return hero,''
    a=obj(data.get('admission')); r=obj(a.get('reliability')); envelope=obj(a.get('validated_envelope'))
    tone={'approved':'passed','limited':'attention','retest':'neutral','rejected':'risk'}.get(a.get('status'),'neutral')
    reasons=a.get('reasons') or []
    hero='<section class="admission-panel '+tone+'" id="admission"><div class="section-head"><div><span class="index">ADMISSION / 独立接入决策</span><h2>'+esc(a.get('label'))+'</h2></div><a href="#production">查看准入条件 ↗</a></div><p>能力评分与生产接入分别评估。关键业务异常不能被其他项目的高分抵消。</p><ul>'+''.join('<li>'+esc(x)+'</li>' for x in reasons[:3])+'</ul>'+metrics([('首试业务成功率',rate(r.get('first_attempt_success_rate'))),('重试后最终成功率',rate(r.get('eventual_success_rate'))),('正常业务任务',r.get('normal_tasks') if r.get('normal_tasks') is not None else '未记录'),('实际观察时窗',number(r.get('observation_seconds'),' 秒'))])+'</section>'
    gates=[]
    for g in a.get('gates') or []:
        presentation='<span class="badge neutral">观测边界</span>' if g.get('blocking') is False else badge(g.get('status'))
        gates.append([esc(g.get('label')),presentation,esc(g.get('observed')),esc(g.get('required'))+'<small>'+esc(g.get('detail') or '')+'</small>',links(g.get('request_ids') or [])])
    production=obj(result.get('production_validation')); pm=obj(production.get('metrics')); pc=obj(production.get('configuration'))
    work=[]
    for name,w in obj(envelope.get('workloads')).items():
        w=obj(w); n=w.get('tasks',w.get('normal_tasks')); good=w.get('business_successful',w.get('successful'))
        work.append([esc(WORKLOAD_LABELS.get(name,name)),esc(n),esc(good),esc(rate(good/n if isinstance(n,(int,float)) and n and isinstance(good,(int,float)) else None)),esc(number(w.get('latency_p95_ms'),' ms'))])
    confidence=obj(r.get('confidence'))
    confidence_text=('业务成功率 95% Wilson 区间：'+number(confidence.get('lower_percent'),'%')+'–'+number(confidence.get('upper_percent'),'%')+'。'+str(confidence.get('note') or '')) if confidence.get('lower_percent') is not None else '样本不足，无法计算成功率区间。'
    windows=[]
    for i,w in enumerate(r.get('time_buckets') or []):
        w=obj(w)
        n=w.get('tasks',w.get('normal_tasks',w.get('count')));first=w.get('first_attempt_successful')
        windows.append([esc(w.get('label') or '距开始 '+number(w.get('offset_seconds'),' 秒')),esc(n),esc(w.get('business_successful',w.get('successful'))),esc(rate(w.get('first_attempt_success_rate',first/n if isinstance(first,(int,float)) and n else None))),esc(number(w.get('latency_p95_ms'),' ms'))])
    recovery=obj(pm.get('recovery')) or {k:pm.get(k) for k in ('retry_attempts','tasks_retried','recovered_tasks','retry_sample_ids')}
    cancel=obj(pm.get('cancellation')) or obj(obj(pm.get('workloads')).get('cancellation'))
    detail='<section class="section" id="production"><div class="section-head"><div><span class="index">PRODUCTION / 业务稳定性</span><h2>准入条件与已验证运行范围</h2><p>'+esc(production.get('reason') or '真实业务任务、请求尝试与负向控制分开统计；不足的证据保持待补充。')+'</p></div></div>'+metrics([('响应协议完整率',rate(r.get('protocol_success_rate'))),('完整业务任务成功率',rate(r.get('business_success_rate'))),('有效内容首到 P95',number(r.get('first_content_p95_ms'),' ms')),('完整任务耗时 P95',number(r.get('latency_p95_ms'),' ms'))])+'<p class="production-note">'+esc(confidence_text)+'</p>'+table(['准入条件','判断','实际观察','要求与解释','证据'],gates)
    detail+='<div class="production-envelope"><b>本轮范围</b><span>协议 '+esc(envelope.get('request_format'))+'</span><span>实测最大并发 '+esc(pm.get('max_concurrency'))+'</span><span>生产请求预算 '+esc(pc.get('max_requests'))+'</span><span>生产持续时间配置 '+esc(number(pc.get('duration_seconds'),' 秒'))+'</span></div><p class="production-note">'+esc(envelope.get('detail'))+' 请求预算包含多轮和重试；估算 Token 预算不等于供应商扣费上限。</p>'
    if work: detail+='<h3>不同业务负载的成功表现</h3>'+table(['业务类型','完整任务数','成功任务数','业务成功率','完成耗时 P95'],work)
    if windows: detail+='<details class="production-detail"><summary>分时段表现 · '+str(len(windows))+' 个观察窗口</summary>'+table(['窗口','任务数','业务成功','首试成功率','耗时 P95'],windows)+'</details>'
    detail+='<details class="production-detail"><summary>重试、取消与停止原因</summary><p>只允许未输出有效内容、没有工具副作用的可恢复错误有限重试；首试失败保留。客户端关闭连接只证明本地取消，不证明上游立即停止生成或停止计费。</p><pre>'+esc(json.dumps({'stop_reason':production.get('stop_reason',pm.get('stop_reason')),'recovery':recovery,'cancellation':cancel},ensure_ascii=False,indent=2))+'</pre></details></section>'
    cost=obj(a.get('cost')); currency=cost.get('currency') or ''; business=obj(cost.get('production'))
    coverage=obj(cost.get('actual_coverage')); estimate_coverage=obj(cost.get('estimated_coverage'))
    detail+='<section class="section" id="cost"><div class="section-head"><div><span class="index">COST / 尝试级成本核算</span><h2>'+esc(cost.get('label'))+'</h2><p>报价版本：'+esc(cost.get('price_version'))+'。按每次请求的 usage 与报价估算，导入上游账单后单独展示实扣金额。</p></div></div>'+metrics([('全部请求估算成本',money(cost.get('estimated_total'),currency)),('全部请求账单金额',money(cost.get('actual_total'),currency)),('生产成功任务估算成本',money(business.get('estimated_cost_per_success'),currency)),('生产成功任务实扣成本',money(business.get('actual_cost_per_success'),currency))])+'<p class="production-note">usage 估算覆盖 '+esc(estimate_coverage.get('covered'))+'/'+esc(estimate_coverage.get('total'))+'；账单覆盖 '+esc(coverage.get('covered'))+'/'+esc(coverage.get('total'))+'。'+esc(business.get('detail'))+'</p><p>'+esc(obj(cost.get('comparison')).get('detail'))+'</p>'
    ledger=[]
    for row in cost.get('rows') or []:
        units=obj(row.get('units'))
        ledger.append([links([row.get('request_id')]),esc(row.get('scope'))+' / '+esc(row.get('traffic_class')),esc(units.get('input'))+' / '+esc(units.get('output')),esc(units.get('cache_read'))+' / '+esc(units.get('cache_write')),esc(money(row.get('estimated_amount'),currency)),esc(money(row.get('actual_amount'),currency)),esc('; '.join(row.get('missing') or []))])
    if ledger: detail+='<details class="production-detail"><summary>逐请求成本与缺失证据 · '+str(len(ledger))+' 次尝试</summary>'+table(['请求','范围 / 性质','输入 / 输出 Token','缓存读 / 写 Token','估算费用','账单金额','待核查'],ledger)+'</details>'
    detail+='<ul class="scope-list">'+''.join('<li>'+esc(x)+'</li>' for x in cost.get('limitations') or [])+'</ul></section>'
    return hero,detail
