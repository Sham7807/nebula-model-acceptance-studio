"""Standalone, readable acceptance reports; no network requests or external assets."""
from collections import Counter
import copy
import html
import json
import math
from pathlib import Path
import re
import statistics

from acceptance_results import decorate
from report_content import build_report_data

STATUS = {'passed':'通过', 'failed':'未通过', 'inconclusive':'无法判定', 'skipped':'已跳过',
          'not_covered':'未覆盖', 'cancelled':'已取消', 'completed':'已完成', 'error':'运行错误', 'running':'运行中'}
TERMINATIONS = {'eof':'响应已正常结束', 'done':'收到 [DONE]', 'connection_grace_exceeded':'收尾事件后响应仍未结束',
                'timeout':'请求超时', 'total_timeout':'超过总时限', 'idle_timeout':'读取等待超时',
                'network_error':'网络中断', 'recorder_closed':'用例提前停止读取，记录器关闭',
                'client_closed':'调用方提前结束读取', 'cancelled':'用户取消', 'evidence_limit':'达到证据采集上限'}
SENSITIVE = re.compile(r'^(api[_-]?key|key|authorization|proxy-authorization|x-api-key|x-goog-api-key|cookie|set-cookie|access[_-]?token|refresh[_-]?token|secret|password)$', re.I)
PREVIEW_LIMIT = 16000


def redact(value):
    if isinstance(value, dict):
        return {k: '[已隐藏]' if SENSITIVE.fullmatch(str(k)) else redact(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0],str) and SENSITIVE.fullmatch(value[0]):
            return [value[0], '[已隐藏]']
        return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[已隐藏]', value)
        value=re.sub(r'sk-[A-Za-z0-9_-]{20,}', '[已隐藏]', value)
        value=re.sub(r'(?i)([?&](?:api[_-]?key|access_token|key)=)[^&#\s"<>]*', r'\1[已隐藏]', value)
        return re.sub(r'(?i)("(?:api[_-]?key|authorization|x-api-key|password|secret)"\s*:\s*)"(?:\\.|[^"\\])*"',r'\1"[已隐藏]"',value)
    return value


def esc(value):
    return html.escape(str(value if value is not None else '—'), quote=True)


def prose(value):
    if isinstance(value,(list,tuple)): value='\n'.join(str(x) for x in value)
    return esc(value or '未记录').replace('\n','<br>')


def badge(status):
    style=status if status in STATUS else 'inconclusive'
    return '<span class="badge '+style+'">'+esc(STATUS.get(status,status or '未记录'))+'</span>'


def seconds(ms):
    if not isinstance(ms,(float,int)) or not math.isfinite(ms): return '—'
    return f'{ms/1000:.2f} 秒'


def pretty(value):
    if isinstance(value,str):
        try: value=json.loads(value)
        except (ValueError,TypeError): pass
    value=redact(value)
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)


def raw_block(label, value):
    if value is None: return '<p class="muted">'+esc(label)+'：本次记录未包含该证据。</p>'
    text=pretty(value)
    note=f'<p class="muted">摘要展示前 {PREVIEW_LIMIT:,} 字符；下方可展开本次已采集的完整内容（{len(text):,} 字符）。采集时已截断的数据无法由报告恢复。</p>' if len(text)>PREVIEW_LIMIT else ''
    complete='<details class="raw full-evidence"><summary>展开完整已采集内容</summary><pre>'+esc(text)+'</pre></details>' if len(text)>PREVIEW_LIMIT else ''
    return '<details class="raw"><summary>'+esc(label)+'</summary>'+note+'<pre>'+esc(text[:PREVIEW_LIMIT])+'</pre>'+complete+'</details>'


def evidence_records(result, directory=None):
    """Join raw KVV observations with the corrected, derived transport summary."""
    records=[]
    if result.get('suite') == 'batch_acceptance':
        parent = Path(directory).parent if directory else None
        for index, item in enumerate(result.get('results') or []):
            child = item.get('result') if isinstance(item, dict) else None
            if not isinstance(child, dict):
                continue
            child_dir = parent / str(item.get('run_id')) if parent and item.get('run_id') else None
            for row in evidence_records(child, child_dir):
                row = dict(row)
                row['id'] = 'model-%s-%s' % (index + 1, row.get('id') or 'request')
                row['model'] = item.get('model') or (child.get('configuration') or {}).get('model')
                records.append(row)
        return records
    if result.get('suite') == 'browser_report':
        return result.get('browser_requests') or []
    if result.get('suite') in ('ccmax','ccmax_acceptance','claude','claude_acceptance'):
        for sample in result.get('samples',[]):
            response=sample.get('response') or {};evidence=sample.get('evidence') or {};request=sample.get('request') or {}
            records.append({'id':sample.get('id',''), 'case_id':sample.get('probe',''), 'status':sample.get('status'),
                'http_status':response.get('status'), 'duration_ms':sample.get('duration_ms'), 'first_byte_ms':evidence.get('first_byte_ms'),
                'termination':sample.get('termination'), 'method':request.get('method'), 'url':request.get('url'),
                'upstream_ids':evidence.get('request_ids',[]), 'request_body':request.get('body'), 'response_body':response.get('body'),
                'response_headers':response.get('headers'), 'notes':sample.get('issues',[]), 'extra':evidence,
                'assessments':sample.get('assessments',[])})
        return records
    raw={}
    if directory:
        path=Path(directory)/'requests.jsonl'
        if path.is_file():
            with path.open(encoding='utf-8',errors='replace') as source:
                for line in source:
                    try: event=json.loads(line)
                    except ValueError: continue
                    if not isinstance(event,dict) or not event.get('request_id'): continue
                    if event.get('type') in ('request_start','request_finish'):
                        raw.setdefault(event['request_id'],{})[event['type']]=event
    case_map={c.get('id'):c for c in result.get('cases',[])}
    for compact in (result.get('transport') or {}).get('requests',[]):
        parts=raw.get(compact.get('request_id'),{});start=parts.get('request_start',{});end=parts.get('request_finish',{})
        case=case_map.get(compact.get('case_id'),{})
        records.append({'id':compact.get('request_id',''), 'case_id':compact.get('case_id',''),
            'status':case.get('status',compact.get('status')), 'transport_status':compact.get('status'),
            'http_status':compact.get('http_status'), 'duration_ms':compact.get('duration_ms'),
            'termination':compact.get('termination'), 'method':compact.get('method'), 'url':compact.get('url'),
            'upstream_ids':compact.get('upstream_request_ids',[]), 'request_body':start.get('body'),
            'response_body':end.get('body'), 'response_headers':end.get('response_headers'),
            'notes':[end['error']] if end.get('error') else [], 'extra':{'请求记录':compact,'SSE 观测':end.get('sse'),
                '请求内容被截断':start.get('body_truncated'), '响应内容被截断':end.get('body_truncated')},
            'assessments':end.get('checks',[])})
    # Preserve requests that had started but were still in-flight at cancellation.
    known={r['id'] for r in records}
    for identity,parts in raw.items():
        if identity in known:continue
        start=parts.get('request_start',{})
        records.append({'id':identity,'case_id':start.get('case_id',''),'status':'inconclusive','http_status':None,
            'termination':'未采集到完成记录','request_body':start.get('body'),'method':start.get('method'),'url':start.get('url'),
            'notes':['已记录请求开始；缺少完成证据，不能计为通过。']})
    return records


# One stylesheet is embedded in service and portable reports.
STYLE = (Path(__file__).resolve().parent.parent / 'multimodal-workbench' / 'report-theme.css').read_text(encoding='utf-8')


def media_html(items):
    from urllib.parse import urlsplit
    output=[]
    for item in items or []:
        if not isinstance(item,dict): continue
        kind=item.get('type') or item.get('kind');url=str(item.get('url') or '')
        if kind not in ('image','video','audio'):continue
        parsed=urlsplit(url)
        safe_remote=parsed.scheme in ('https','http') and parsed.netloc and not parsed.username and not parsed.password and redact(url)==url
        safe_data=bool(re.fullmatch(r'data:(?:image/(?:png|jpeg|webp|gif|avif)|video/(?:mp4|webm|quicktime)|audio/(?:mpeg|mp3|mp4|wav|wave|x-wav|ogg|webm|flac|x-flac|aac));base64,[A-Za-z0-9+/=\r\n]+',url))
        if not (safe_remote or safe_data): continue
        tag='img' if kind=='image' else kind
        attrs=' alt="生成图片" loading="lazy" referrerpolicy="no-referrer"' if tag=='img' else ' controls preload="none"'
        output.append('<figure><'+tag+' src="'+esc(url)+'"'+attrs+'>'+('' if tag=='img' else '</'+tag+'>')+'<figcaption>' + ('已嵌入本报告 · 可离线查看' if safe_data else '<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">打开媒体 ↗</a> · 远程链接需联网')+'</figcaption></figure>')
    return '<div class="media">'+''.join(output)+'</div>'

SCRIPT = r'''
(()=>{'use strict';let filter='all';const search=document.getElementById('check-search');const cards=[...document.querySelectorAll('.check')];
function apply(){const q=search.value.trim().toLowerCase();let count=0;for(const card of cards){const ok=(filter==='all'||card.dataset.status===filter)&&(q===''||card.textContent.toLowerCase().includes(q));card.hidden=!ok;if(ok)count++;}document.getElementById('visible-count').textContent='显示 '+count+' / '+cards.length+' 项';}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});apply();}));search.addEventListener('input',apply);
document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',()=>{const target=document.getElementById(a.getAttribute('href').slice(1));if(!target)return;if(target.matches('details'))target.open=true;if(target.matches('.check')&&target.hidden){filter='all';search.value='';document.querySelector('[data-filter="all"]').click();} }));
document.getElementById('print-report').addEventListener('click',()=>window.print());let closed=[];window.addEventListener('beforeprint',()=>{closed=[...document.querySelectorAll('details:not([open])')];closed.forEach(d=>d.open=true);});window.addEventListener('afterprint',()=>closed.forEach(d=>d.open=false));apply();})();
'''


def _browser_originals(result):
    """Show saved scores/logs/batch summaries without replacing evidence scores."""
    originals = result.get('original_results') or []
    if not originals:
        return ''
    labels = {'idn':'身份自述', 'stream':'流式输出', 'vision':'多模态能力', 'tools':'工具调用与动态加载', 'json':'JSON 协议', 'cache':'上下文缓存', 'max_tokens':'长度控制', 'batch':'批量快评', 'proto':'协议兼容性', 'params':'参数支持度', 'auth':'身份与一致性观察', 'longctx':'长上下文检索', 'reason':'推理与数学', 'content':'内容与语言能力', 'latency':'延迟与吞吐', 'conc':'稳定性与并发', 'err':'错误处理', 'robust':'工程健壮性', 'gpt':'GPT HTML 生成与 Token 一致性'}
    def saved_value(value):
        if value is None: return '未记录'
        if isinstance(value,bool): return '是' if value else '否'
        return esc(value)
    cards = []
    for index, source in enumerate(originals, 1):
        scores = source.get('scores') if isinstance(source.get('scores'), dict) else {}
        score_rows = ''.join('<tr><td>'+esc(labels.get(name, name))+'</td><td>'+saved_value(value)+'</td></tr>' for name,value in scores.items())
        batch = source.get('batch') if isinstance(source.get('batch'), list) else []
        batch_columns = [('model','模型'), ('mini','原始快评分'), ('stream','流式'), ('tools','工具'), ('json','JSON'), ('conc','并发')]
        batch_rows = ''.join('<tr>'+''.join('<td>'+saved_value(row.get(key))+'</td>' for key,label in batch_columns)+'</tr>' for row in batch if isinstance(row,dict))
        batch_html = '<h3>批量模型结果</h3><table class="compact-table"><thead><tr>'+''.join('<th>'+label+'</th>' for key,label in batch_columns)+'</tr></thead><tbody>'+batch_rows+'</tbody></table>'+raw_block('批量原始结果',batch) if batch else ''
        logs = source.get('logs')
        log_lines = []
        for line in logs if isinstance(logs,list) else []:
            if isinstance(line,dict):
                log_lines.append('['+str(line.get('t') or line.get('time') or '')+'] '+str(line.get('msg') or line.get('message') or line.get('text') or ''))
            else: log_lines.append(str(line))
        # Preserve the full already-bounded input in expandable logs: the HTML
        # is the requested deliverable, so do not point users to a missing ZIP.
        log_html = '<details class="raw"><summary>执行日志 · '+str(len(log_lines))+' 条</summary><pre>'+esc('\n'.join(log_lines))+'</pre></details>' if log_lines else '<p class="muted">执行日志未记录。</p>'
        total = source.get('total')
        cards.append('<article class="panel" style="margin-top:12px"><h3>'+esc(source.get('model') or '未记录模型')+'</h3><p class="muted">模式：'+esc(source.get('mode') or '未记录')+' · 原始总分：'+esc(total if total is not None else '未记录')+(' / 100' if total is not None else '')+'</p>'
            +('<table class="compact-table"><thead><tr><th>原始维度</th><th>页面采集分值 / 10</th></tr></thead><tbody>'+score_rows+'</tbody></table>' if score_rows else '<p class="muted">原维度分未记录。</p>')
            +batch_html+log_html+raw_block('补充观察',source.get('intelligence'))+'</article>')
    return '<section id="original-results" class="section"><div class="section-head"><div><span class="index">ORIGINAL OBSERVATIONS</span><h2>原始评分、批量结果与执行日志</h2><p>保留检测页面当时的分值与日志；原始评分尺度与上方统一证据评分分别展示，不互相替代。</p></div></div>'+''.join(cards)+'</section>'


def _format_name(value):
    return {'openai':'OpenAI Chat Completions', 'openai-chat':'OpenAI Chat Completions', 'openai-responses':'OpenAI Responses', 'responses':'OpenAI Responses', 'anthropic':'Anthropic Messages', 'gemini':'Gemini 原生'}.get(str(value), str(value) if value else '未记录')


def _auth_name(value):
    return {'bearer':'Authorization: Bearer', 'anthropic':'Anthropic · x-api-key', 'gemini':'Gemini · x-goog-api-key', 'none':'不使用鉴权'}.get(str(value), str(value) if value else '未记录')


def render_report(result, directory=None):
    result=redact(copy.deepcopy(result));decorate(result)
    data=build_report_data(result);cc=result.get('suite') in ('ccmax','ccmax_acceptance');claude=result.get('suite') in ('claude','claude_acceptance');browser=result.get('suite')=='browser_report'
    checks=data.get('checks',[]);requests=redact(evidence_records(result,directory));config=result.get('configuration') or {}
    status_counts=Counter(c.get('status') for c in checks)
    local_count=sum('tolerance_boundaries' in str(c.get('id','')) for c in checks)
    check_anchors={c.get('id'):f'check-{i+1}' for i,c in enumerate(checks)}
    request_anchors={r['id']:f'request-{i+1}' for i,r in enumerate(requests)}
    evidence_aliases={}
    for r in requests:
        target=(request_anchors[r['id']],r['id'])
        evidence_aliases.setdefault(r['id'],[]).append(target)
        for identity in r.get('upstream_ids',[]):
            value=identity.get('value') if isinstance(identity,dict) else str(identity)
            if value:evidence_aliases.setdefault(value,[]).append(target)
    def links(ids):
        items=[];seen=set()
        identities=list(dict.fromkeys(ids or []))
        # Local IDs are authoritative. Providers can reuse an upstream header
        # across many HTTP requests; do not let that alias broaden an explicit
        # sample relationship to every request sharing the header.
        exact=[identity for identity in identities if identity in request_anchors]
        for identity in exact or identities:
            if identity in request_anchors:
                anchor=request_anchors[identity]
                if anchor not in seen:
                    seen.add(anchor);items.append('<a href="#'+anchor+'">'+esc(identity)+'</a>')
                continue
            matches=evidence_aliases.get(identity)
            if matches:
                for anchor,label in matches:
                    if anchor in seen:continue
                    seen.add(anchor);items.append('<a href="#'+anchor+'" title="'+esc(identity)+'">'+esc(label)+'</a>')
            elif identity:items.append('<code>'+esc(identity)+'</code>')
        if len(items)>10:
            return ' '.join(items[:8])+'<details class="more-evidence"><summary>展开其余 '+str(len(items)-8)+' 个样本</summary>'+' '.join(items[8:])+'</details>'
        return ' '.join(items)
    def excerpt(value, limit=210):
        if isinstance(value, (dict, list, tuple)):
            text = pretty(value)
        else:
            text = str(value if value is not None else '未记录')
        if len(text) <= limit:
            return prose(text)
        return prose(text[:limit])+'…<details class="result-excerpt"><summary>展开完整摘要</summary><div>'+prose(text)+'</div></details>'

    # Count only real saved requests joined by IDs.  A failed assertion is not
    # automatically a failed HTTP request; negative-parameter probes may
    # intentionally receive 4xx responses and still pass their assertion.
    request_by_id={row['id']: row for row in requests}
    result_rows=[]
    for index, check in enumerate(checks, 1):
        matched={}
        identities=check.get('request_ids') or []
        exact=[identity for identity in identities if identity in request_by_id]
        for identity in exact or identities:
            if identity in request_by_id:
                matched[identity]=request_by_id[identity]
                continue
            for anchor, identity_id in evidence_aliases.get(identity, []):
                matched[identity_id]=request_by_id[identity_id]
        failed=sum(row.get('status') in ('failed','error') for row in matched.values())
        evidence=('<span class="result-stat">'+str(len(matched))+' 次 / '+str(failed)+' 次</span>' if matched else '<span class="result-unlinked">未记录关联</span>')
        result_rows.append('<tr><td><span class="result-number">'+f'{index:02d}'+'</span><a class="result-name" href="#check-'+str(index)+'">'+esc(check.get('title') or check.get('id'))+'</a></td><td>'+excerpt(check.get('method'))+'</td><td><div class="result-observation"><b>预期</b><br>'+excerpt(check.get('expected'))+'</div><div class="result-observation"><b>实际</b><br>'+excerpt(check.get('observed_summary') or check.get('observed'))+'</div></td><td>'+evidence+'<small>请求 / 标为失败</small></td><td class="result-status">'+badge(check.get('status'))+('</td></tr>'))
    all_results_html='<section id="all-results" class="all-results"><div class="section-head"><div><span class="index">RESULTS / COMPLETE MATRIX</span><h2>全项测试结果（'+str(len(checks))+' 项）</h2></div></div><p class="all-results-note">请求数来自已保存且与该项明确关联的证据；同一请求可支撑多项检查，不能逐行相加当作总请求数。“标为失败”保留请求记录的状态，参数拒绝等负向用例请结合预期判读。</p><div class="results-scroll"><table class="results-table"><thead><tr><th>测试项</th><th>测试方法</th><th>预期 / 实际结果</th><th>证据次数</th><th>检查状态</th></tr></thead><tbody>'+''.join(result_rows)+'</tbody></table></div></section>'
    toc_items=[('overview','01 · 结论总览'),('all-results','02 · 全项测试结果'),('modules','03 · 验收模块'),('score','04 · 能力评分'),('setup','05 · 范围与配置'),('findings','06 · 问题、影响与建议'),('checks','07 · 逐项验收说明'),('requests','08 · 请求明细与证据')]
    if result.get('original_results'): toc_items.append(('original-results','09 · 原始评分、批量与日志'))
    toc_items.append(('limits','判读说明与原始数据'))
    toc_html='<section class="report-toc" aria-label="报告目录"><h2>报告目录</h2><div class="toc-grid">'+''.join('<a href="#'+target+'">'+label+'</a>' for target,label in toc_items)+'</div></section>'
    summary=result.get('summary') or {};verdict=result.get('verdict') or {}
    score=data.get('score') or {}
    score_dims=score.get('dimensions') or []
    check_titles={c.get('id'): c.get('title') or c.get('id') for c in checks}
    status_labels={'passed':'已覆盖 / 通过','failed':'存在失败','inconclusive':'无法判定','not_covered':'本轮未覆盖','skipped':'含跳过项','cancelled':'已取消'}
    count_labels={'passed':'通过','failed':'失败','inconclusive':'无法判定','skipped':'跳过','not_covered':'未覆盖','cancelled':'取消'}
    def score_evidence(d):
        counts=d.get('counts') or {};covered=int(d.get('covered') or 0);status=d.get('status','not_covered')
        count_text='、'.join('%s %s' % ({'total':'共'}.get(k,count_labels.get(k,k)),v) for k,v in counts.items() if v and k != 'total')
        summary='覆盖 %s 项%s' % (covered, (' · '+count_text) if count_text else '')
        if not covered:
            return '<small>'+esc(summary)+'</small><div class="score-evidence score-evidence-empty">本轮未覆盖此能力，不代表模型不支持；请运行包含该项目的专项测试。</div>'
        links=[]
        for check_id in d.get('check_ids') or []:
            anchor=check_anchors.get(check_id)
            title=check_titles.get(check_id) or check_id
            if anchor: links.append('<a href="#'+anchor+'" title="技术用例：'+esc(check_id)+'">'+esc(title)+'</a>')
        if len(links)>4:
            evidence='<div class="score-evidence">评分依据：'+ '、'.join(links[:4]) + '<details class="score-more"><summary>查看其余 '+str(len(links)-4)+' 项</summary>'+ '、'.join(links[4:]) + '</details></div>'
        elif links:
            evidence='<div class="score-evidence">评分依据：'+ '、'.join(links) + '</div>'
        else:
            evidence='<div class="score-evidence">本轮有结果，但没有可跳转的技术用例。</div>'
        return '<small>'+esc(summary)+'</small>'+evidence
    def score_card(d):
        status=d.get('status','not_covered');covered=int(d.get('covered') or 0);value='—' if status=='not_covered' else str(d.get('score',0));width=0 if status=='not_covered' else max(0,min(100,int(d.get('score',0))))
        return '<article class="score-dimension '+esc(status)+'"><div class="score-dimension-head"><span>'+esc(d.get('label'))+'</span><b>'+value+'<small>'+('' if value=='—' else '/100')+'</small></b></div><div class="bar"><i style="width:'+str(width)+'%"></i></div><div class="score-status">'+esc(status_labels.get(status,status))+'</div>'+score_evidence(d)+'</article>'
    score_cards=''.join(score_card(d) for d in score_dims)
    score_total_note='已覆盖 %s/%s 个维度' % (score.get('covered_dimensions',0),score.get('dimension_count',len(score_dims)))
    module_cards=[]
    for module in (score.get('modules') or []):
        module_status=module.get('status','not_covered');covered=int(module.get('covered') or 0)
        module_value='—' if module_status=='not_covered' else str(module.get('score',0))
        module_width=0 if module_status=='not_covered' else max(0,min(100,int(module.get('score',0))))
        counts=module.get('counts') or {}
        count_text='通过 %s · 失败 %s · 无法判定 %s' % (counts.get('passed',0), counts.get('failed',0), counts.get('inconclusive',0))
        module_cards.append('<article class="module-card '+esc(module_status)+'"><div class="module-weight">权重 '+esc(module.get('weight',0))+'%</div><h3>'+esc(module.get('label'))+'</h3><div class="module-score"><strong>'+esc(module_value)+'</strong><small>/100</small></div><div class="module-bar"><i style="width:'+str(module_width)+'%"></i></div><p class="module-desc">'+prose(module.get('description'))+'</p><div class="module-meta"><span>'+esc('覆盖 '+str(covered)+' 项')+'</span><span>'+esc(count_text)+'</span></div></article>')
    module_total=score.get('weighted_total',score.get('total',0));module_covered=score.get('weight_covered',0);module_weight_total=score.get('weight_total',0)
    module_html='<section class="module-panel" id="modules"><div class="module-head"><div><span class="index">MODULES / WEIGHTED SCORE</span><h2>验收模块总览</h2><p class="score-note">各套件使用统一模块结构；模块分数按权重计算，未覆盖模块不计入加权总分。</p></div><div class="module-total"><strong>'+esc(module_total)+'</strong><small>/ 100 · 已覆盖权重 '+esc(module_covered)+'% / '+esc(module_weight_total)+'%</small></div></div><div class="module-grid">'+''.join(module_cards)+'</div><table class="module-table"><thead><tr><th>模块</th><th>权重</th><th>本轮结论</th><th>计分说明</th></tr></thead><tbody>'+''.join('<tr><td><b>'+esc(m.get('label'))+'</b><br><small>'+prose(m.get('description'))+'</small></td><td class="weight">'+esc(m.get('weight'))+'%</td><td>'+badge(m.get('status'))+'<br><small>覆盖 '+esc(m.get('covered',0))+' 项 · 得分 '+esc('—' if m.get('status')=='not_covered' else m.get('score',0))+'</small></td><td><small>'+esc((m.get('counts') or {}).get('passed',0))+' 通过 / '+esc((m.get('counts') or {}).get('failed',0))+' 失败 / '+esc((m.get('counts') or {}).get('inconclusive',0))+' 无法判定</small></td></tr>' for m in (score.get('modules') or []))+'</tbody></table></section>'
    score_html=module_html+'<section class="score-panel" id="score"><div class="score-head"><div><span class="index">SCORE / DIMENSIONS</span><h2>能力评分与覆盖明细</h2><p class="score-note">'+esc(score.get('method',''))+' · '+esc(score_total_note)+'</p></div><div class="score-total">'+esc(score.get('total',0))+'<small> / 100</small></div></div><div class="score-grid">'+score_cards+'</div><ul class="recommendations">'+''.join('<li>'+prose(x)+'</li>' for x in (score.get('recommendations') or []))+'</ul></section>'
    # Optional GPT degradation / HTML-SVG generation panel.  It is populated
    # only when the browser client persisted raw.gpt_evaluation; absent fields
    # stay explicitly unrecorded and are never inferred from a normal response.
    gpt_rows = data.get('gpt_evaluations') or []
    def gpt_value(value):
        if value is None or value == '': return '未记录'
        if isinstance(value, bool): return '是' if value else '否'
        return str(value)
    def gpt_badge(value):
        if value is True: return '<span class="badge passed">通过</span>'
        if value is False: return '<span class="badge failed">未通过</span>'
        return '<span class="badge inconclusive">未记录</span>'
    gpt_cards=[]
    for item in gpt_rows:
        item = item if isinstance(item, dict) else {}
        usage = item.get('token_usage') if isinstance(item.get('token_usage'), dict) else {}
        inp = usage.get('input', usage.get('prompt_tokens'))
        out = usage.get('output', usage.get('completion_tokens'))
        total_tokens = usage.get('total', usage.get('total_tokens'))
        consistent = usage.get('consistent')
        total_derived = any(source.get(key) is True for source in (usage,item) for key in ('total_derived','total_tokens_derived'))
        accounting_applicable = all(source.get('accounting_applicable') is not False for source in (usage,item))
        if total_derived or not accounting_applicable:
            consistent = None
        elif consistent is None and isinstance(inp, (int,float)) and isinstance(out, (int,float)) and isinstance(total_tokens, (int,float)):
            consistent = (inp + out == total_tokens)
        accounting_badge = '<span class="badge inconclusive">派生值 · 未验证总量</span>' if total_derived else '<span class="badge skipped">不适用 · 未验证总量</span>' if not accounting_applicable else gpt_badge(consistent)
        accounting_note = '总量为本地派生值，未验证渠道上报总量；不能用输入和输出之和再次证明渠道总量一致。' if total_derived else '当前协议不适用总量加总校验，未验证渠道上报总量。' if not accounting_applicable else 'Token 一致性只核对本次响应 usage 字段的算术关系，不代表 tokenizer、计费或模型身份准确。'
        signals = item.get('signals')
        signal_text = json.dumps(redact(signals), ensure_ascii=False, indent=2) if isinstance(signals, (dict,list)) else gpt_value(signals)
        generated = item.get('html') or item.get('svg') or item.get('output') or item.get('html_preview') or item.get('source')
        generated_block = raw_block('HTML / SVG 生成结果（转义展示）', generated) if generated else '<p class="muted">没有保存生成的 HTML/SVG 正文；请展开请求证据查看响应原文。</p>'
        gpt_cards.append('<article class="gpt-card"><div class="gpt-card-head"><div><span class="index">GPT / QUALITY CHECK</span><h3>'+esc(item.get('model') or '未记录模型')+'</h3><p class="muted">提示词：'+esc(item.get('prompt') or '生成 HTML，内容是 SVG 绘制鹈鹕骑自行车 2D 动画')+'</p></div>'+badge('passed' if item.get('verdict') in (True,'passed','通过') else 'failed' if item.get('verdict') in (False,'failed','失败') else 'inconclusive')+'</div><table class="compact-table"><tr><th>HTML 输出</th><td>'+gpt_badge(item.get('html_detected'))+'</td><th>SVG 输出</th><td>'+gpt_badge(item.get('svg_detected'))+'</td></tr><tr><th>动画特征</th><td>'+gpt_badge(item.get('animation_detected'))+'</td><th>HTML 可解析</th><td>'+gpt_badge(item.get('html_valid'))+'</td></tr><tr><th>输入 tokens</th><td>'+esc(gpt_value(inp))+'</td><th>输出 tokens</th><td>'+esc(gpt_value(out))+'</td></tr><tr><th>总 tokens</th><td>'+esc(gpt_value(total_tokens))+('（派生）' if total_derived else '')+'</td><th>输入 + 输出 = 总数</th><td>'+accounting_badge+'</td></tr></table><p class="gpt-note">'+esc(accounting_note)+'</p><details class="raw"><summary>检测信号与生成效果证据</summary><pre>'+esc(signal_text)+'</pre></details>'+generated_block+'</article>')
    gpt_html='<section class="gpt-panel" id="gpt-quality"><div class="section-head"><div><span class="index">GPT / HTML · SVG · TOKEN</span><h2>GPT 生成质量与 Token 一致性</h2><p>针对“SVG 绘制鹈鹕骑自行车 2D 动画”提示词的可观察验收；不把一次生成等同于长期模型质量。</p></div></div><div class="gpt-grid">'+''.join(gpt_cards)+'</div></section>' if gpt_cards else ''
    request_count=(result.get('transport') or {}).get('request_count',len(requests))
    elapsed=(result.get('finished_at') or 0)-(result.get('started_at') or 0)
    elapsed_text=seconds(elapsed*1000) if elapsed>0 else '未记录'
    metric_items=[('请求样本' if cc else '实际 API 请求',request_count,'请求数与验收项数分别统计'),
        ('检查通过',status_counts['passed'],'含 '+str(local_count)+' 项本地检查' if local_count else '只统计本次已完成检查'),
        ('检查未通过',status_counts['failed'],'明确观察到不符合预期的结果'),
        ('无法判定 / 未覆盖',status_counts['inconclusive']+status_counts['not_covered'],f'另有 {status_counts["skipped"]} 项跳过')]
    metrics=''.join('<div class="metric"><span>'+esc(label)+'</span><b>'+esc(value)+'</b><small>'+esc(note)+'</small></div>' for label,value,note in metric_items)
    total=max(1,len(checks));distribution=''.join('<span class="'+s+'" style="width:'+str(n/total*100)+'%"></span>' for s,n in status_counts.items() if s in STATUS)
    runtime_info=[('渠道地址',config.get('base') or '未记录'),('模型 ID',config.get('model') or '未记录'),('检测程序',data.get('engine') or '未记录'),
        ('运行状态',STATUS.get(result.get('status'),result.get('status','未记录'))),('本轮耗时',elapsed_text),
        ('执行进度',f'{summary.get("completed",0)} / {summary.get("total","未记录")} '+('请求样本' if cc else '专项检查' if claude else '观察项' if browser else 'pytest 项')),
        ('单次超时',str(config['timeout'])+' 秒' if config.get('timeout') is not None else '未记录')]
    openai=result.get('request_format')=='openai' or config.get('request_format')=='openai' or (not cc and not claude and config.get('think_mode')=='openai')
    runtime_info.append(('请求格式',_format_name(config.get('request_format') or config.get('requestFormat')) if browser else 'OpenAI Chat Completions · /v1/chat/completions' if openai else 'Anthropic Messages · /v1/messages' if cc or claude else 'Kimi 原生契约 · Chat Completions'))
    if browser:
        runtime_info.extend([('鉴权方式',_auth_name(config.get('auth'))), ('请求端点',config.get('endpoint') or config.get('path') or '按各项请求证据记录')])
    if cc:
        runtime_info.extend([('采样设置',('签名不适用 · ' if openai else f'签名 {config.get("signature_samples","未记录")} 次 · ')+f'普通 SSE {config.get("sse_samples","未记录")} 次 · 工具与非法模型各 1 次'),
            ('并发 / 鉴权',str(config.get('concurrency','未记录'))+' / '+('x-api-key' if config.get('auth')=='anthropic' else config.get('auth','未记录')))])
    elif claude:
        provider_names = {'auto': '未指定 / 自动观察', 'anthropic': 'Anthropic 官方（渠道声明）', 'aws': 'AWS Bedrock（渠道声明）'}
        runtime_info.extend([('上游来源声明',provider_names.get(config.get('provider'), config.get('provider') or '未指定')),
            ('来源判读','来源为配置提示，未对官方资源、AWS 账号或模型权重进行认证'),
            ('缓存目标规模',str(config.get('cache_tokens','未记录'))+' tokens（目标值，实测以 usage 为准）'),
            ('压测样本 / 并发',str(config.get('stress_requests','未记录'))+' / '+str(config.get('concurrency','未记录'))),
            ('鉴权方式',_auth_name(config.get('auth')))])
    elif not browser:
        runtime_info.extend([('KVV Schema / 原生版本',result.get('revision','未记录')),('格式范围','全部四个检测层面使用兼容断言，原生专项另行说明' if openai else str(config.get('think_mode','未记录'))+'；原生 K3 专项保留官方字段')])
    info='<dl class="key-value">'+''.join('<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>' for k,v in runtime_info)+'</dl>'
    if browser and len(config.get('records') or []) > 1:
        columns=[('model','模型'),('request_format','请求协议'),('auth','鉴权'),('endpoint','端点')]
        rows=[]
        for row in config['records']:
            values=[row.get('model'), _format_name(row.get('request_format') or row.get('requestFormat')), _auth_name(row.get('auth')), row.get('endpoint') or row.get('path') or '未记录']
            rows.append('<tr>'+''.join('<td>'+esc(value)+'</td>' for value in values)+'</tr>')
        info+='<table class="compact-table"><thead><tr>'+''.join('<th>'+label+'</th>' for key,label in columns)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
    scopes='<ul class="scope-list">'+''.join('<li>'+prose(x)+'</li>' for x in data.get('scope',[]))+'</ul>'
    focus_items=data.get('focus') or []
    if focus_items:
        scopes += '<div class="focus-box"><h3>本套件测试重点</h3><ul class="scope-list">'+''.join('<li>'+prose(x)+'</li>' for x in focus_items)+'</ul></div>'
    findings=[]
    for f in data.get('findings',[]):
        finding_status=f.get('status','failed');anchor=check_anchors.get(f.get('check_id'))
        findings.append('<article class="finding '+('inconclusive' if finding_status!='failed' else '')+'">'+badge(finding_status)+'<h3>'+esc(f.get('title','问题摘要'))+'</h3>'
            +'<p><span class="field-label">实际观察</span>'+prose(f.get('observation_summary') or f.get('observation'))+'</p><p><span class="field-label">影响</span>'+prose(f.get('impact'))+'</p>'
            +'<p><span class="field-label">建议排查</span>'+prose(f.get('recommendation'))+'</p><div class="evidence-links">'
            +('<a href="#'+anchor+'">查看检查项 →</a>' if anchor else '')+links(f.get('evidence_ids',[]))+'</div></article>')
    finding_html='<div class="findings">'+''.join(findings)+'</div>' if findings else '<div class="empty">本轮未记录明确失败项。请同时核对无法判定、跳过、未覆盖和执行进度，不能仅凭这一行认定全部通过。</div>'
    check_html=[]
    for i,c in enumerate(checks):
        row_status=c.get('status','inconclusive');symbol={'passed':'✓','failed':'✕','inconclusive':'!','skipped':'–','not_covered':'·','cancelled':'–'}.get(row_status,'!')
        matrix='<div class="check-matrix"><table class="matrix-table"><thead><tr><th class="status-cell">状态</th><th>检测项</th><th>预期行为</th><th>实际结果</th></tr></thead><tbody><tr class="row-'+esc(row_status)+'"><td class="status-cell" aria-label="'+esc(STATUS.get(row_status,row_status))+'">'+symbol+'</td><td><div class="case-title">'+esc(c.get('title') or c.get('id'))+'</div><small>'+badge(row_status)+'</small></td><td class="expected">'+prose(c.get('expected'))+'</td><td class="actual">'+prose(c.get('observed_summary') or c.get('observed'))+'</td></tr></tbody></table></div>'
        check_html.append('<article class="check" id="check-'+str(i+1)+'" data-status="'+esc(row_status)+'"><div class="check-head"><div><h3>'+f'{i+1:02d} · '+esc(c.get('title') or c.get('id'))+'</h3><details class="case-id"><summary></summary><code>'+esc(c.get('id',''))+'</code></details></div>'+badge(row_status)+'</div>'
            +'<p class="method"><span class="field-label">怎么测</span>'+prose(c.get('method'))+'</p>'+matrix
            +'<div class="interpretation"><div><span class="field-label">结果说明</span><p>'+prose(c.get('meaning'))+'</p></div><div><span class="field-label">下一步建议</span><p>'+prose(c.get('next_step'))+'</p></div></div>'
            +('<div class="evidence-links">请求证据：'+links(c.get('request_ids',[]))+'</div>' if c.get('request_ids') else '')+media_html(c.get('media'))+(raw_block('逐样本结果明细',c.get('observed')) if c.get('observed_summary') else '')+raw_block('原始判定与断言',c.get('raw'))+'</article>')
    durations=[r.get('duration_ms') for r in requests if isinstance(r.get('duration_ms'),(int,float)) and math.isfinite(r['duration_ms'])]
    if durations:
        ordered=sorted(durations);p95=ordered[max(0,math.ceil(len(ordered)*.95)-1)];maximum=max(durations)
        bars=''.join('<a class="'+('failed' if r.get('status')=='failed' else 'inconclusive' if r.get('status')!='passed' else '')+'" href="#'+request_anchors[r['id']]+'" style="height:'+str(max(3,round(r.get('duration_ms',0)/max(1,maximum)*76)))+'px" title="'+esc(r['id']+' · '+seconds(r.get('duration_ms')))+'" aria-label="'+esc(r['id']+' 请求耗时 '+seconds(r.get('duration_ms')))+'"></a>' for r in requests if isinstance(r.get('duration_ms'),(int,float)))
        perf='<div class="panel"><div class="perf-stats">'+''.join('<div><span>'+label+'</span><b>'+seconds(value)+'</b></div>' for label,value in [('中位请求耗时',statistics.median(durations)),('P95 请求耗时',p95),('最慢请求',maximum)])+'</div><div class="chart">'+bars+'</div><p class="chart-note">每根柱对应一个已记录耗时的请求，点击可展开证据。耗时包含响应等待；取消或提前结束的请求可能不完整，不等于模型纯生成速度。</p></div>'
    else:perf='<div class="empty">本次没有可用的请求耗时记录。</div>'
    request_html=[]
    for r in requests:
        upstream=r.get('upstream_ids') or []
        ids='\n'.join(str(x.get('header','Request ID'))+': '+str(x.get('value','')) if isinstance(x,dict) else str(x) for x in upstream) or '上游未提供 / 本次未记录'
        kv=[('所属用例 / 探针',r.get('case_id')),('请求地址',str(r.get('method') or '')+' '+str(r.get('url') or '未记录')),
            ('HTTP 状态',r.get('http_status')),('请求耗时',seconds(r.get('duration_ms'))),('结束方式',TERMINATIONS.get(r.get('termination'),r.get('termination'))),('上游 Request ID',ids)]
        if r.get('model'):kv.insert(0,('所属模型',r['model']))
        if r.get('first_byte_ms') is not None:kv.append(('首字节耗时',seconds(r['first_byte_ms'])))
        if r.get('transport_status'):kv.append(('传输独立判定',STATUS.get(r['transport_status'],r['transport_status'])))
        facts='<dl class="key-value">'+''.join('<dt>'+esc(k)+'</dt><dd>'+prose(v)+'</dd>' for k,v in kv)+'</dl>'
        rows=''.join('<tr><td>'+esc(a.get('label') or a.get('check') or a.get('id'))+'</td><td>'+badge(a.get('status'))+'</td><td>'+prose(a.get('detail') or a.get('details'))+'</td></tr>' for a in r.get('assessments',[]))
        assessments='<table class="compact-table"><tr><th>观测项</th><th>结果</th><th>证据说明</th></tr>'+rows+'</table>' if rows else ''
        request_html.append('<details class="request" id="'+request_anchors[r['id']]+'"><summary><b class="request-id">'+esc(r['id'])+'</b><span class="request-summary">HTTP '+esc(r.get('http_status'))+' · '+seconds(r.get('duration_ms'))+'</span>'+badge(r.get('status'))+'</summary><div class="request-body">'+facts
            +('<p class="method">'+prose(r['notes'])+'</p>' if r.get('notes') else '')+assessments+raw_block('请求内容（已脱敏）',r.get('request_body'))+raw_block('响应正文 / SSE（已脱敏）',r.get('response_body'))
            +raw_block('响应头与链路标识',r.get('response_headers'))+raw_block('其他采集信息',r.get('extra'))+'</div></details>')
    wire=(result.get('transport') or {}).get('checks',[])
    wire_counts=Counter(x.get('status') for x in wire)
    wire_items=''.join('<div class="transport-item">'+badge(x.get('status'))+' <b>'+esc(x.get('label') or x.get('id'))+'</b> · '+links([x.get('request_id','')])+'<p>'+prose(x.get('detail') or x.get('details'))+'</p></div>' for x in wire if x.get('status')!='passed')
    wire_html='<section class="section"><div class="section-head"><div><span class="index">TRANSPORT</span><h2>附加传输检查</h2><p>与官方用例结果分开统计：通过 '+str(wire_counts['passed'])+'，失败 '+str(wire_counts['failed'])+'，无法判定 '+str(wire_counts['inconclusive'])+'。</p></div></div><div class="transport-list">'+wire_items+'</div></section>' if wire else ''
    limitations=list(data.get('limitations',[]))+['HTTP 成功不等于验收通过；报告中的模型名称为请求或响应字段，不能单独作为真实模型身份证明。',
        '原始字段在本 HTML 中按段展示，超长字段会明确标注缩略。被截断或未读完的响应不声称完整。']
    notes=result.get('classification_notes') or []
    note_html='<div class="panel" style="margin-top:14px"><h3>结果归类说明</h3><p>'+prose(notes)+'</p></div>' if notes else ''
    original_html=_browser_originals(result) if browser else ''
    result_json=raw_block('查看报告原始 JSON（已脱敏）',result)
    title=data.get('title') or '渠道验收报告';model=config.get('model') or '未记录模型'
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>'+esc(model+' · '+title)+'</title><style>'+STYLE+'</style></head><body><main>'
        +'<div class="masthead"><span class="brand">小小宇宙无敌</span><span class="eyebrow">CHANNEL ACCEPTANCE REPORT</span><button class="button no-print" id="print-report">打印 / 保存 PDF</button></div>'
        +'<header class="cover"><div class="cover-top"><span class="eyebrow">'+esc(title)+'</span>'+badge(result.get('status'))+'</div><h1>'+esc(model)+'</h1><p>'+esc(config.get('base') or '渠道地址未记录')+'</p><p class="run-id">RUN / '+esc(result.get('run_id') or '未记录')+'</p><div class="cover-meta"><span>'+esc(data.get('engine') or '渠道验收')+'</span><span>'+str(len(checks))+' 项检查</span><span>'+str(request_count)+' 次已记录请求</span><span>依据本轮实测 · 脱敏证据</span></div></header>'
        +'<nav class="nav"><a href="#overview">结论总览</a><a href="#all-results">全项结果</a><a href="#modules">验收模块</a><a href="#score">能力评分</a>'+('<a href="#gpt-quality">GPT 质量</a>' if gpt_html else '')+'<a href="#setup">范围与配置</a><a href="#findings">发现的问题</a><a href="#checks">逐项检查</a><a href="#requests">请求证据</a>'+('<a href="#original-results">原始评分与日志</a>' if original_html else '')+'<a href="#limits">判读说明</a></nav>'
        +'<section id="overview" class="overview"><div class="verdict-line"><div><h2>'+esc(verdict.get('label',''))+'</h2><p>'+esc(verdict.get('detail',''))+'</p></div>'+badge(verdict.get('status'))+'</div><div class="metrics">'+metrics+'</div><div class="distribution" aria-hidden="true">'+distribution+'</div><p class="legend">本报告列出 '+str(len(checks))+' 个验收项 / 用例；'+('请求样本：通过 '+str(summary.get('passed',0))+'，未通过 '+str(summary.get('failed',0))+'，无法判定 '+str(summary.get('inconclusive',0))+'。' if cc else 'Claude 专项检查与真实请求数分别统计；上游来源仅为渠道声明。' if claude else '浏览器检查结果与实际 HTTP 请求数分别统计。' if browser else '官方用例、附加传输检查与真实请求数分别统计。')+'</p></section>'+toc_html+all_results_html+score_html+gpt_html
        +'<section id="setup" class="section"><div class="section-head"><div><span class="index">01 / SCOPE</span><h2>这次测了什么</h2></div></div><div class="grid-two"><div class="panel">'+info+'</div><div class="panel">'+scopes+'</div></div></section>'
        +'<section id="findings" class="section"><div class="section-head"><div><span class="index">02 / FINDINGS</span><h2>问题与影响</h2><p>依据本轮已保存的响应和断言整理；建议用于核对链路，不代替上游日志。</p></div></div>'+finding_html+'</section>'
        +'<section id="checks" class="section"><div class="section-head"><div><span class="index">03 / CHECKS</span><h2>逐项验收说明</h2></div><small id="visible-count"></small></div><div class="filters no-print">'
        +''.join('<button type="button" data-filter="'+state+'" class="'+('active' if state=='all' else '')+'" aria-pressed="'+str(state=='all').lower()+'">'+label+'</button>' for state,label in [('all','全部'),('failed','未通过'),('inconclusive','无法判定'),('passed','通过'),('skipped','已跳过'),('not_covered','未覆盖')])
        +'<input id="check-search" type="search" aria-label="搜索检查项" placeholder="搜索项目、参数或问题…"></div>'+''.join(check_html)+'</section>'+wire_html
        +'<section id="requests" class="section"><div class="section-head"><div><span class="index">04 / EVIDENCE</span><h2>请求明细与证据</h2><p>展开样本可查看请求内容、原始响应和链路 Request ID。</p></div></div>'+perf+(''.join(request_html) if request_html else '<div class="empty">没有可展示的请求记录。请查看执行日志与 JSON 结果。</div>')+'</section>'
        +original_html+'<section id="limits" class="section"><div class="section-head"><div><span class="index">05 / READING NOTES</span><h2>如何使用这份报告</h2></div></div><div class="panel"><ul class="scope-list">'+''.join('<li>'+prose(x)+'</li>' for x in limitations)+'</ul>'+result_json+'</div>'+note_html+'</section>'
        +'<footer><span>小小宇宙无敌 · '+esc(title)+'</span><span>独立 HTML · 无外部字体或脚本依赖 · 凭据已隐藏</span></footer></main><script>'+SCRIPT+'</script></body></html>').encode('utf-8')
