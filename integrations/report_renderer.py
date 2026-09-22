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
    note=f'<p class="muted">此处展示前 {PREVIEW_LIMIT:,} 字符；其余内容请查看 JSON 结果或 ZIP 中的原始采集记录。</p>' if len(text)>PREVIEW_LIMIT else ''
    return '<details class="raw"><summary>'+esc(label)+'</summary>'+note+'<pre>'+esc(text[:PREVIEW_LIMIT])+'</pre></details>'


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
    if result.get('suite') in ('ccmax','ccmax_acceptance'):
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


STYLE = r'''
:root{--ink:#233d32;--muted:#6e7e73;--line:#dce5dc;--paper:#f4f7f2;--green:#28664e;--red:#ac4b3d;--amber:#936b22}.score-panel{margin-top:18px;background:white;border:1px solid var(--line);border-radius:16px;padding:20px}.score-head{display:flex;align-items:center;justify-content:space-between;gap:15px}.score-total{font-size:32px;font-weight:650;color:var(--green);font-variant-numeric:tabular-nums}.score-total small{font-size:12px;font-weight:500}.score-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.score-dimension{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfa}.score-dimension-head{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px}.score-dimension b{font-size:18px}.score-dimension .bar{height:5px;border-radius:5px;background:#e6ece4;margin:8px 0;overflow:hidden}.score-dimension .bar i{display:block;height:100%;background:#5f916d}.score-dimension.failed .bar i{background:#bb6453}.score-dimension.inconclusive .bar i{background:#c1994b}.score-dimension.not_covered{opacity:.75}.score-note{font-size:10px;color:var(--muted);margin-top:12px}.recommendations{margin:14px 0 0;padding-left:18px;font-size:11px;color:var(--muted)}.recommendations li+li{margin-top:5px}.module-panel{margin-top:18px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}.module-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px}.module-total{display:flex;align-items:center;gap:10px}.module-total strong{font-size:30px;color:var(--green)}.module-total small{font-size:12px;color:var(--muted)}.module-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:15px}.module-card{position:relative;border:1px solid var(--line);border-radius:12px;padding:14px 12px;background:#fbfcfa;min-height:148px}.module-card:before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;border-radius:12px 0 0 12px;background:#8aa993}.module-card.failed:before{background:#c45d50}.module-card.inconclusive:before{background:#c19a4d}.module-card.not_covered:before{background:#cbd4c9}.module-card h3{font-size:14px;margin:0 0 3px}.module-weight{font:11px ui-monospace,monospace;color:var(--muted)}.module-score{display:flex;align-items:baseline;gap:3px;margin-top:8px}.module-score strong{font-size:24px}.module-score small{font-size:11px;color:var(--muted)}.module-desc{font-size:11px;color:var(--muted);line-height:1.5;margin-top:6px}.module-meta{display:flex;justify-content:space-between;gap:6px;margin-top:9px;font-size:10px;color:var(--muted)}.module-bar{height:5px;background:#e7ece5;border-radius:4px;overflow:hidden;margin-top:9px}.module-bar i{display:block;height:100%;background:#68a17b}.module-card.failed .module-bar i{background:#c45d50}.module-card.inconclusive .module-bar i{background:#c19a4d}.module-card.not_covered .module-bar i{background:#cbd4c9}.module-table{width:100%;border-collapse:collapse;margin-top:15px;font-size:11px}.module-table th,.module-table td{text-align:left;padding:9px;border-bottom:1px solid var(--line);vertical-align:top}.module-table th{color:var(--muted);font-weight:500}.module-table .weight{text-align:right;font-variant-numeric:tabular-nums}.matrix-table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:12px}.matrix-table th,.matrix-table td{text-align:left;padding:14px 12px;border-bottom:1px solid var(--line);vertical-align:top}.matrix-table th{background:#f5f7f4;color:#56675b;font-weight:650}.matrix-table tr:last-child td{border-bottom:0}.matrix-table .status-cell{width:34px;text-align:center;font-size:19px}.matrix-table .case-title{font-weight:650;color:#24362b}.matrix-table .case-title small{display:block;color:var(--amber);font-weight:600;font-size:11px;margin-top:4px}.matrix-table .expected{color:#4e5f53}.matrix-table .actual{color:#33443a}.matrix-table tr.row-failed td{background:#fff9f7}.matrix-table tr.row-inconclusive td{background:#fffdf5}.check-matrix{margin-top:12px}.check-matrix .details-wrap{margin-top:9px}.check-matrix .raw{border-top:0;padding-top:0}.check-matrix .raw summary{font-size:11px}@media(max-width:900px){.module-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.score-grid{grid-template-columns:1fr 1fr}.module-grid{grid-template-columns:1fr}.module-head{align-items:flex-start;flex-direction:column}}
.focus-box{margin-top:16px;padding:14px 16px;border:1px solid #dce9df;border-radius:10px;background:#f7fbf6}.focus-box h3{font-size:13px;color:var(--green);margin-bottom:5px}.focus-box .scope-list{font-size:12px}
.score-evidence{margin-top:6px;font-size:10px;color:var(--muted);line-height:1.55;overflow-wrap:anywhere}.score-evidence a{text-decoration:underline}.score-evidence-empty{color:#8b6f35}.score-status{display:inline-block;margin-top:7px;font-size:10px;color:var(--muted)}.score-more{display:inline;margin-left:4px}.score-more summary{display:inline;color:var(--green);cursor:pointer}.score-dimension.not_covered .bar{background:#edf0eb}.score-dimension.not_covered .bar i{background:#cbd4c9}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:90px}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--green);text-decoration:none}button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #83b699;outline-offset:3px}main{max-width:1200px;margin:auto;padding:32px 28px 60px}h1,h2,h3,p{margin:0}h1{font-size:32px;letter-spacing:-.8px;line-height:1.3;overflow-wrap:anywhere}h2{font-size:21px;line-height:1.4}h3{font-size:16px;line-height:1.5}small,.muted{color:var(--muted)}.eyebrow{font-size:10px;letter-spacing:2px;font-weight:600}.masthead{display:flex;align-items:center;justify-content:space-between;padding:0 0 18px;gap:16px}.brand{font-weight:650;font-size:16px}.brand:before{content:'宇';display:inline-grid;place-items:center;width:32px;height:32px;color:white;background:var(--ink);border-radius:10px;margin-right:10px}.cover{background:var(--ink);color:#f8fbf7;border-radius:20px;padding:32px}.cover .eyebrow{color:#b6cbb8}.cover-top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}.cover p{color:#c6d5c9;margin-top:10px}.cover code{background:#ffffff12;color:inherit}.run-id{font:11px ui-monospace,monospace;opacity:.75;overflow-wrap:anywhere}.button{background:white;border:1px solid var(--line);border-radius:8px;padding:8px 13px;color:var(--green);display:inline-block}.cover .button{border-color:#637a69;background:transparent;color:#eef5ed}.nav{display:flex;gap:20px;flex-wrap:wrap;background:#f4f7f2ed;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:17px 0;position:sticky;top:0;z-index:2;font-size:12px}.nav a{white-space:nowrap}.overview{padding:24px 0}.verdict-line{display:flex;align-items:flex-start;justify-content:space-between;gap:15px;margin-bottom:16px}.verdict-line p{color:var(--muted);margin-top:6px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{padding:18px 20px;background:white;border:1px solid var(--line);border-radius:12px}.metric b{font-size:30px;display:block;line-height:1.3;font-variant-numeric:tabular-nums}.metric span{font-size:12px;color:var(--muted)}.metric small{display:block;margin-top:4px;font-size:10px}.distribution{display:flex;height:7px;border-radius:10px;overflow:hidden;background:#e5ebe3;margin-top:18px}.distribution span{min-width:0}.distribution .passed{background:#418062}.distribution .failed{background:#bb6453}.distribution .inconclusive,.distribution .not_covered{background:#c1994b}.distribution .skipped{background:#b8c1b5}.legend{font-size:11px;color:var(--muted);margin-top:7px}.section{margin-top:26px}.section-head{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:14px}.section-head p{font-size:12px;color:var(--muted);margin-top:4px}.index{font:11px ui-monospace,monospace;color:#799477;letter-spacing:1px;display:block;margin-bottom:4px}.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:22px}.grid-two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.key-value{display:grid;grid-template-columns:130px minmax(0,1fr);gap:8px 14px;margin:0}.key-value dt{color:var(--muted);font-size:12px}.key-value dd{margin:0;overflow-wrap:anywhere}.scope-list{padding-left:19px;margin:0}.scope-list li+li{margin-top:7px}.badge{display:inline-block;white-space:nowrap;font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;background:#eef1ed;color:#627363}.badge.passed,.badge.completed{color:#28664e;background:#e9f3e9}.badge.failed,.badge.error{color:#a0483c;background:#faebe6}.badge.inconclusive,.badge.not_covered,.badge.cancelled{color:#86611d;background:#faf1da}.badge.skipped{color:#687568;background:#edf0ea}.findings{display:grid;grid-template-columns:1fr 1fr;gap:14px}.finding{padding:20px;background:white;border:1px solid var(--line);border-left:3px solid #be6a58;border-radius:12px}.finding.inconclusive{border-left-color:#c19a4d}.finding h3{margin:10px 0}.finding p{margin-top:9px}.finding .field-label{font-size:11px;color:var(--muted);display:block;margin-bottom:2px}.finding .evidence-links{border-top:1px solid var(--line);margin-top:14px;padding-top:10px;font-size:11px}.evidence-links a{display:inline-block;margin:3px 8px 3px 0;border-bottom:1px solid #dce5dc}.filters{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:12px 0}.filters button{border:1px solid var(--line);background:white;color:var(--muted);border-radius:7px;padding:6px 11px;font-size:11px}.filters button.active{background:var(--green);color:white;border-color:var(--green)}.filters input{flex:1;min-width:180px;border:1px solid var(--line);border-radius:7px;padding:7px 10px;background:white;outline:none}.check{border:1px solid var(--line);background:white;border-radius:13px;padding:21px;margin-top:12px}.check-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.case-id{margin-top:5px}.case-id summary{cursor:pointer;list-style:none;font:11px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--muted)}.case-id summary::-webkit-details-marker{display:none}.case-id summary:before{content:'技术用例 ID · 点击查看';color:#859384}.case-id[open] summary:before{content:'技术用例 ID · 收起'}.case-id code{display:block;margin-top:3px;font:10px/1.6 ui-monospace,monospace;color:#859384;overflow-wrap:anywhere;background:#f4f7f1}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}.comparison>div{padding:14px 16px;background:#f7f9f4;border:1px solid #e7ede3;border-radius:9px;min-width:0}.comparison .actual{background:#fbfcfa}.field-label{color:var(--muted);font-size:11px;font-weight:600}.check p{overflow-wrap:anywhere}.method{margin-top:13px}.method .field-label{margin-right:8px}.interpretation{margin-top:13px;display:grid;grid-template-columns:1fr 1fr;gap:14px;font-size:12px}.interpretation p{margin-top:4px}.raw{margin-top:12px;border-top:1px solid #e8ede5;padding-top:10px}.raw summary,.request>summary{cursor:pointer;font-size:12px;color:var(--green)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;background:#f4f7f1;border:1px solid #e0e8dc;border-radius:8px;padding:14px;font:11px/1.75 ui-monospace,SFMono-Regular,monospace;color:#374d3b}code{font:11px/1.7 ui-monospace,monospace;overflow-wrap:anywhere;background:#f0f4ed;padding:2px 5px;border-radius:4px}.request{border:1px solid var(--line);border-radius:11px;background:white;margin:10px 0;padding:15px 18px}.request>summary{display:flex;align-items:center;gap:12px;list-style:none}.request>summary:before{content:'+';color:#7f9579;font-size:17px;width:12px}.request[open]>summary:before{content:'−'}.request-id{font:12px ui-monospace,monospace;min-width:90px}.request-summary{color:var(--muted);font-size:11px;flex:1}.request>summary .badge{margin-left:auto}.request-body{border-top:1px solid var(--line);padding-top:16px;margin-top:14px}.request .key-value{grid-template-columns:125px minmax(0,1fr);font-size:12px}.compact-table{width:100%;border-collapse:collapse;font-size:11px;margin-top:12px}.compact-table td,.compact-table th{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:9px 8px;overflow-wrap:anywhere}.compact-table th{color:var(--muted);font-weight:500}.chart{display:flex;align-items:flex-end;gap:3px;height:92px;margin-top:18px;padding-top:12px;overflow:hidden}.chart a{flex:1;min-width:2px;background:#82aa8c;border-radius:3px 3px 0 0}.chart a.failed{background:#bc7769}.chart a.inconclusive{background:#c3a160}.chart-note{font-size:10px;color:var(--muted);margin-top:5px}.perf-stats{display:flex;flex-wrap:wrap;gap:24px}.perf-stats b{font-size:20px;display:block}.perf-stats span{font-size:11px;color:var(--muted)}.transport-list{display:grid;gap:10px;margin-top:12px}.transport-item{padding:12px 15px;background:#fafbf8;border:1px solid var(--line);border-radius:8px;font-size:12px}.transport-item p{margin-top:6px}.empty{border:1px dashed var(--line);border-radius:12px;padding:22px;color:var(--muted)}footer{display:flex;justify-content:space-between;gap:16px;margin-top:26px;border-top:1px solid var(--line);padding-top:18px;font-size:11px;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:700px){main{padding:18px 14px 35px}.masthead .eyebrow{display:none}.cover{padding:23px 20px;border-radius:14px}h1{font-size:25px}.metrics{grid-template-columns:1fr 1fr;gap:8px}.metric{padding:14px}.metric b{font-size:25px}.grid-two,.findings,.comparison,.interpretation{grid-template-columns:1fr}.nav{gap:14px;padding:13px 0}.key-value,.request .key-value{grid-template-columns:90px minmax(0,1fr)}.panel,.check{padding:17px}.section-head{align-items:flex-start}.request{padding:13px}.request>summary{flex-wrap:wrap;gap:8px}.request-summary{flex-basis:48%;min-width:110px}.request-id{min-width:90px}.cover-top{align-items:flex-start}.run-id{font-size:9px}.filters input{width:100%;flex-basis:100%}footer{display:block}}
@media print{body{background:white;font-size:10px}main{max-width:none;padding:0}.no-print,.nav,.filters{display:none!important}.cover{background:white;color:var(--ink);border:1px solid var(--line);padding:20px}.cover p,.cover .eyebrow{color:var(--muted)}.cover code{color:var(--ink)}.metrics{grid-template-columns:repeat(4,1fr)}.check[hidden]{display:block!important}.check,.finding,.panel,.request{break-inside:avoid}.findings{display:block}.finding{margin-bottom:10px}.comparison,.interpretation{grid-template-columns:1fr 1fr}pre{max-height:none;overflow:visible;font-size:8px}.raw>summary{font-size:10px}.chart{height:55px}.section{margin-top:18px}h1{font-size:24px}}
'''

# Shared with the portable HTML export.  Keep all reports on one visual system.
STYLE += r'''
.gpt-panel{margin-top:18px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}.gpt-grid{display:grid;grid-template-columns:1fr;gap:14px}.gpt-card{border:1px solid var(--line);border-radius:12px;background:#fbfcfa;padding:16px}.gpt-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.gpt-card-head h3{margin:3px 0}.gpt-note{font-size:11px;color:var(--muted);margin-top:10px}.gpt-card .compact-table{margin-top:13px}.gpt-card .compact-table th{width:100px}.gpt-card .raw{margin-top:12px}@media(max-width:700px){.gpt-card-head{display:block}.gpt-card-head .badge{margin-top:8px}}
.media{display:flex;flex-wrap:wrap;gap:13px;margin-top:14px}.media:empty{display:none}.media figure{margin:0;flex:1 1 280px;padding:10px;border:1px solid var(--line);border-radius:10px;background:#fbfcfa}.media img,.media video{display:block;width:100%;max-height:520px;object-fit:contain;border-radius:7px;background:#f0f3eb}.media audio{width:100%}.media figcaption{font-size:11px;margin-top:8px}.model-label{font:11px ui-monospace,monospace;color:var(--muted);margin-top:6px}
'''


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


def render_report(result, directory=None):
    result=redact(copy.deepcopy(result));decorate(result)
    data=build_report_data(result);cc=result.get('suite') in ('ccmax','ccmax_acceptance');browser=result.get('suite')=='browser_report'
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
        for identity in dict.fromkeys(ids or []):
            matches=evidence_aliases.get(identity)
            if matches:
                for anchor,label in matches:
                    if anchor in seen:continue
                    seen.add(anchor);items.append('<a href="#'+anchor+'" title="'+esc(identity)+'">'+esc(label)+'</a>')
            elif identity:items.append('<code>'+esc(identity)+'</code>')
        if len(items)>10:
            return ' '.join(items[:8])+'<details class="more-evidence"><summary>展开其余 '+str(len(items)-8)+' 个样本</summary>'+' '.join(items[8:])+'</details>'
        return ' '.join(items)
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
        if consistent is None and isinstance(inp, (int,float)) and isinstance(out, (int,float)) and isinstance(total_tokens, (int,float)):
            consistent = (inp + out == total_tokens)
        signals = item.get('signals')
        signal_text = json.dumps(redact(signals), ensure_ascii=False, indent=2) if isinstance(signals, (dict,list)) else gpt_value(signals)
        generated = item.get('html') or item.get('svg') or item.get('output') or item.get('html_preview') or item.get('source')
        generated_block = raw_block('HTML / SVG 生成结果（转义展示）', generated) if generated else '<p class="muted">没有保存生成的 HTML/SVG 正文；请展开请求证据查看响应原文。</p>'
        gpt_cards.append('<article class="gpt-card"><div class="gpt-card-head"><div><span class="index">GPT / QUALITY CHECK</span><h3>'+esc(item.get('model') or '未记录模型')+'</h3><p class="muted">提示词：'+esc(item.get('prompt') or '生成 HTML，内容是 SVG 绘制鹈鹕骑自行车 2D 动画')+'</p></div>'+badge('passed' if item.get('verdict') in (True,'passed','通过') else 'failed' if item.get('verdict') in (False,'failed','失败') else 'inconclusive')+'</div><table class="compact-table"><tr><th>HTML 输出</th><td>'+gpt_badge(item.get('html_detected'))+'</td><th>SVG 输出</th><td>'+gpt_badge(item.get('svg_detected'))+'</td></tr><tr><th>动画特征</th><td>'+gpt_badge(item.get('animation_detected'))+'</td><th>HTML 可解析</th><td>'+gpt_badge(item.get('html_valid'))+'</td></tr><tr><th>输入 tokens</th><td>'+esc(gpt_value(inp))+'</td><th>输出 tokens</th><td>'+esc(gpt_value(out))+'</td></tr><tr><th>总 tokens</th><td>'+esc(gpt_value(total_tokens))+'</td><th>输入 + 输出 = 总数</th><td>'+gpt_badge(consistent)+'</td></tr></table><p class="gpt-note">Token 一致性只核对本次响应 usage 字段的算术关系，不代表 tokenizer、计费或模型身份准确。</p><details class="raw"><summary>检测信号与生成效果证据</summary><pre>'+esc(signal_text)+'</pre></details>'+generated_block+'</article>')
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
        ('执行进度',f'{summary.get("completed",0)} / {summary.get("total","未记录")} '+('请求样本' if cc else '观察项' if browser else 'pytest 项')),
        ('单次超时',str(config['timeout'])+' 秒' if config.get('timeout') is not None else '未记录')]
    openai=result.get('request_format')=='openai' or config.get('request_format')=='openai' or (not cc and config.get('think_mode')=='openai')
    runtime_info.append(('请求格式','按各项实际请求体与端点记录' if browser else 'OpenAI Chat Completions · /v1/chat/completions' if openai else 'Anthropic Messages · /v1/messages' if cc else 'Kimi 原生契约 · Chat Completions'))
    if cc:
        runtime_info.extend([('采样设置',('签名不适用 · ' if openai else f'签名 {config.get("signature_samples","未记录")} 次 · ')+f'普通 SSE {config.get("sse_samples","未记录")} 次 · 工具与非法模型各 1 次'),
            ('并发 / 鉴权',str(config.get('concurrency','未记录'))+' / '+('x-api-key' if config.get('auth')=='anthropic' else config.get('auth','未记录')))])
    elif not browser:
        runtime_info.extend([('KVV Schema / 原生版本',result.get('revision','未记录')),('格式范围','全部四个检测层面使用兼容断言，原生专项另行说明' if openai else str(config.get('think_mode','未记录'))+'；原生 K3 专项保留官方字段')])
    info='<dl class="key-value">'+''.join('<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>' for k,v in runtime_info)+'</dl>'
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
    result_json=raw_block('查看报告原始 JSON（已脱敏）',result)
    title=data.get('title') or '渠道验收报告';model=config.get('model') or '未记录模型'
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>'+esc(model+' · '+title)+'</title><style>'+STYLE+'</style></head><body><main>'
        +'<div class="masthead"><span class="brand">小小宇宙无敌</span><span class="eyebrow">CHANNEL ACCEPTANCE REPORT</span><button class="button no-print" id="print-report">打印 / 保存 PDF</button></div>'
        +'<header class="cover"><div class="cover-top"><span class="eyebrow">'+esc(title)+'</span>'+badge(result.get('status'))+'</div><h1>'+esc(model)+'</h1><p>'+esc(config.get('base') or '渠道地址未记录')+'</p><p class="run-id">RUN / '+esc(result.get('run_id') or '未记录')+'</p></header>'
        +'<nav class="nav"><a href="#overview">结论总览</a><a href="#modules">验收模块</a><a href="#score">能力评分</a>'+('<a href="#gpt-quality">GPT 质量</a>' if gpt_html else '')+'<a href="#setup">范围与配置</a><a href="#findings">发现的问题</a><a href="#checks">逐项检查</a><a href="#requests">请求证据</a><a href="#limits">判读说明</a></nav>'
        +'<section id="overview" class="overview"><div class="verdict-line"><div><h2>'+esc(verdict.get('label',''))+'</h2><p>'+esc(verdict.get('detail',''))+'</p></div>'+badge(verdict.get('status'))+'</div><div class="metrics">'+metrics+'</div><div class="distribution" aria-hidden="true">'+distribution+'</div><p class="legend">本报告列出 '+str(len(checks))+' 个验收项 / 用例；'+('请求样本：通过 '+str(summary.get('passed',0))+'，未通过 '+str(summary.get('failed',0))+'，无法判定 '+str(summary.get('inconclusive',0))+'。' if cc else '浏览器检查结果与实际 HTTP 请求数分别统计。' if browser else '官方用例、附加传输检查与真实请求数分别统计。')+'</p></section>'+score_html+gpt_html
        +'<section id="setup" class="section"><div class="section-head"><div><span class="index">01 / SCOPE</span><h2>这次测了什么</h2></div></div><div class="grid-two"><div class="panel">'+info+'</div><div class="panel">'+scopes+'</div></div></section>'
        +'<section id="findings" class="section"><div class="section-head"><div><span class="index">02 / FINDINGS</span><h2>问题与影响</h2><p>依据本轮已保存的响应和断言整理；建议用于核对链路，不代替上游日志。</p></div></div>'+finding_html+'</section>'
        +'<section id="checks" class="section"><div class="section-head"><div><span class="index">03 / CHECKS</span><h2>逐项验收说明</h2></div><small id="visible-count"></small></div><div class="filters no-print">'
        +''.join('<button type="button" data-filter="'+state+'" class="'+('active' if state=='all' else '')+'" aria-pressed="'+str(state=='all').lower()+'">'+label+'</button>' for state,label in [('all','全部'),('failed','未通过'),('inconclusive','无法判定'),('passed','通过'),('skipped','已跳过')])
        +'<input id="check-search" type="search" aria-label="搜索检查项" placeholder="搜索项目、参数或问题…"></div>'+''.join(check_html)+'</section>'+wire_html
        +'<section id="requests" class="section"><div class="section-head"><div><span class="index">04 / EVIDENCE</span><h2>请求明细与证据</h2><p>展开样本可查看请求内容、原始响应和链路 Request ID。</p></div></div>'+perf+(''.join(request_html) if request_html else '<div class="empty">没有可展示的请求记录。请查看执行日志与 JSON 结果。</div>')+'</section>'
        +'<section id="limits" class="section"><div class="section-head"><div><span class="index">05 / READING NOTES</span><h2>如何使用这份报告</h2></div></div><div class="panel"><ul class="scope-list">'+''.join('<li>'+prose(x)+'</li>' for x in limitations)+'</ul>'+result_json+'</div>'+note_html+'</section>'
        +'<footer><span>小小宇宙无敌 · '+esc(title)+'</span><span>独立 HTML · 无外部字体或脚本依赖 · 凭据已隐藏</span></footer></main><script>'+SCRIPT+'</script></body></html>').encode('utf-8')
