#!/usr/bin/env python3
"""Loopback workbench service. Runs only fixed, reviewed test suites."""
import argparse
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit, quote
import kvv_runner
from acceptance_results import decorate
from auth_history import Store, COOKIE_NAME, MAX_BODY

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
WEB = WORKSPACE / 'multimodal-workbench'
REPORTS = Path(os.environ.get('WORKBENCH_REPORTS', str(ROOT / 'reports')))
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.RLock()
SUITES = {'kvv11', 'kvvfull', 'ccmax'}
MAX_BATCH_MODELS = 30

# Capability modules are a presentation and coverage contract shared by the
# KVV, CCMax and future suites.  The runners may still execute their fixed,
# reviewed probes, but disabled modules are explicitly marked not_covered in
# the persisted result so they never inflate the score or verdict.
MODULES = {
    'kvv': {
        'protocol': {'label': '参数与协议契约', 'weight': 40},
        'max_tokens': {'label': 'max_tokens 长度控制', 'weight': 15},
        'tools': {'label': '工具调用与 Schema', 'weight': 15},
        'cache': {'label': '缓存与 Token 计量', 'weight': 15},
        'multimodal': {'label': '多模态输入', 'weight': 15},
        'reliability': {'label': '稳定性与性能', 'weight': 0},
    },
    'ccmax': {
        'protocol': {'label': 'Claude 协议与错误', 'weight': 30},
        'reliability': {'label': '流式稳定性', 'weight': 25},
        'tools': {'label': '工具调用', 'weight': 15},
        'cache': {'label': 'usage 与缓存', 'weight': 15},
        'security': {'label': '安全与一致性', 'weight': 15},
        'max_tokens': {'label': '参数边界', 'weight': 0},
    },
}

def _module_defs(suite):
    return MODULES['ccmax' if suite == 'ccmax' else 'kvv']

def _case_modules(entry, suite):
    """Infer capability dimensions for a raw runner case/check."""
    if not isinstance(entry, dict):
        return set()
    explicit = entry.get('dimensions')
    if isinstance(explicit, (list, tuple, set)) and explicit:
        return {str(x) for x in explicit}
    ident = ' '.join(str(entry.get(k, '')) for k in ('id', 'title', 'label', 'probe', 'category', 'check')).lower()
    out = set()
    if any(x in ident for x in ('tool', 'schema', 'function', 'calculator')): out.add('tools')
    if any(x in ident for x in ('cache', 'usage', 'prompt_token', 'token')): out.add('cache')
    if any(x in ident for x in ('video', 'image', 'multimodal', 'vision')): out.add('multimodal')
    if any(x in ident for x in ('max_token', 'max-completion', 'length')): out.add('max_tokens')
    if any(x in ident for x in ('signature', 'message_', 'error', 'protocol', 'json')): out.add('protocol')
    if any(x in ident for x in ('stream', 'connection', 'reliab', 'sse')): out.add('reliability')
    if any(x in ident for x in ('injection', 'hierarchy', 'fingerprint', 'distill', 'security', 'parameter')): out.add('security')
    if not out:
        out.add('protocol')
    # CCMax advanced security probes are deliberately isolated from protocol.
    if suite == 'ccmax' and any(x in ident for x in ('injection', 'hierarchy', 'fingerprint', 'behavioral')):
        out.discard('protocol'); out.add('security')
    return out

def apply_enabled_modules(result, config):
    """Annotate disabled capability entries as ``not_covered``.

    This runs after the fixed runner so evidence remains available for audit,
    while the report/score can clearly distinguish disabled modules from
    failures and successful checks.
    """
    if not isinstance(result, dict):
        return result
    suite = str(config.get('suite') or result.get('suite') or '')
    defs = _module_defs(suite)
    requested = config.get('enabled_modules')
    enabled = set(requested) if isinstance(requested, list) and requested else set(defs)
    enabled &= set(defs)
    result['enabled_modules'] = sorted(enabled)
    result['module_definitions'] = defs
    if suite == 'batch_acceptance':
        for child in result.get('results') or []:
            child_result = child.get('result') if isinstance(child, dict) else None
            if isinstance(child_result, dict):
                apply_enabled_modules(child_result, config)
        return result
    containers = []
    for key in ('cases', 'checks'):
        value = result.get(key)
        if isinstance(value, list): containers.append(value)
    for entries in containers:
        for entry in entries:
            if not isinstance(entry, dict): continue
            dims = _case_modules(entry, 'ccmax' if suite in ('ccmax','ccmax_acceptance') else 'kvv')
            entry.setdefault('dimensions', sorted(dims))
            disabled = bool(dims) and not (dims & enabled)
            if disabled and entry.get('status') not in ('not_covered', 'skipped'):
                entry['original_status'] = entry.get('status')
                entry['status'] = 'not_covered'
                entry['skip_reason'] = '本轮未启用模块：' + '、'.join(defs[d]['label'] for d in sorted(dims) if d in defs)
                entry['detail'] = entry.get('skip_reason')
                entry['not_covered'] = True
    primary = result.get('checks') if suite in ('ccmax','ccmax_acceptance') else result.get('cases')
    if isinstance(primary, list) and primary:
        summary = dict(result.get('summary') or {})
        for status in ('passed','failed','inconclusive','skipped','not_covered','cancelled'):
            summary[status] = sum(1 for x in primary if isinstance(x, dict) and x.get('status') == status)
        summary['total'] = len(primary)
        summary['completed'] = sum(1 for x in primary if isinstance(x, dict) and x.get('status') not in ('running',))
        result['summary'] = summary
    return result

QUEUE_META = REPORTS / '.jobs.json'
AUTH_STORE = Store(os.environ.get('WORKBENCH_DB', str(ROOT / 'workbench.sqlite3')), os.environ.get('WORKBENCH_AUTH_FILE') or None)


class WorkbenchServer(ThreadingHTTPServer):
    """Keep a burst of browser requests queued while worker threads start.

    ``TCPServer`` defaults ``request_queue_size`` to 5.  A browser commonly
    opens several API, script, stylesheet, and media connections together;
    under that default the kernel rejects a short burst before the threaded
    handler can accept it, which surfaces as intermittent gateway 502s.
    """
    request_queue_size = 256
    allow_reuse_address = True

def clean(value, key=''):
    if isinstance(value, dict):
        return {k: ('[已隐藏]' if re.fullmatch(r'(?i)(key|api[_-]?key|authorization|x-api-key|x-goog-api-key)', k) else clean(v,key)) for k,v in value.items()}
    if isinstance(value, list): return [clean(v,key) for v in value]
    if isinstance(value, tuple): return [clean(v,key) for v in value]
    if isinstance(value, str):
        if key: value = value.replace(key, '[已隐藏]')
        return re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[已隐藏]', value)
    return value

def normalized_base(raw):
    u = urlsplit(str(raw).strip())
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('渠道地址必须是完整 HTTP(S) 地址，不能带账号、查询参数或片段。')
    path = u.path.rstrip('/')
    if not path.endswith('/v1'): path += '/v1'
    return urlunsplit((u.scheme,u.netloc,path,'',''))

def validate(data):
    if not isinstance(data,dict) or data.get('suite') not in SUITES: raise ValueError('请选择有效的验收套件')
    raw_models=data.get('models')
    if raw_models is not None:
        if not isinstance(raw_models,list) or not raw_models: raise ValueError('模型列表不能为空')
        models=[]
        for value in raw_models:
            value=str(value).strip()
            if value and value not in models: models.append(value)
        if not models: raise ValueError('模型列表不能为空')
        if len(models)>MAX_BATCH_MODELS: raise ValueError(f'一次最多测试 {MAX_BATCH_MODELS} 个模型')
    else:
        models=[]
    model=str(data.get('model','')).strip() or (models[0] if models else '')
    c = {'suite':data['suite'], 'base':normalized_base(data.get('base','')), 'key':str(data.get('key','')).strip(), 'model':model, 'models':models}
    if not c['key'] or not c['model']: raise ValueError('请填写 API Key 和渠道模型 ID')
    if any('\n' in c[k] or '\r' in c[k] for k in ('key','model')): raise ValueError('密钥和模型名不能包含换行')
    for name, default, low, high in [('timeout',120,5,600),('signature_samples',1,1,20),('sse_samples',3,1,200),('concurrency',2,1,10)]:
        try: n = int(data.get(name,default))
        except (ValueError,TypeError): raise ValueError(name+' 必须为整数')
        if not low <= n <= high: raise ValueError(f'{name} 超出范围 {low}–{high}')
        c[name] = n
    c['think_mode'] = data.get('think_mode','openai' if data.get('request_format')=='openai' and c['suite']!='ccmax' else 'kimi')
    if c['think_mode'] not in ('kimi','opensource','none','openai'): raise ValueError('thinking 格式无效')
    c['request_format'] = data.get('request_format', 'anthropic' if c['suite']=='ccmax' else 'openai' if c['think_mode']=='openai' else 'native')
    if c['request_format'] not in (('anthropic','openai') if c['suite']=='ccmax' else ('native','openai')): raise ValueError('请求格式不适用于当前套件')
    if c['suite']!='ccmax' and (c['request_format']=='openai') != (c['think_mode']=='openai'): raise ValueError('KVV 请求格式与 thinking 配置不一致')
    c['auth'] = data.get('auth','anthropic')
    if c['auth'] not in ('anthropic','bearer'): raise ValueError('CCmax 鉴权方式无效')
    if c['request_format']=='openai': c['auth']='bearer'
    c['thinking'] = bool(data.get('thinking',True))
    advanced = data.get('advanced', True if c['suite'] == 'ccmax' else False)
    if not isinstance(advanced, bool): raise ValueError('高级 CCMax 探针开关必须为布尔值')
    c['advanced'] = advanced
    # Module selection is optional for backwards compatibility.  An omitted
    # selection means every reviewed module for the selected suite; invalid
    # names are rejected before any provider request is made.
    available = _module_defs(c['suite'])
    raw_modules = data.get('enabled_modules')
    if raw_modules is None:
        c['enabled_modules'] = sorted(available)
    else:
        if not isinstance(raw_modules, list):
            raise ValueError('enabled_modules 必须是数组')
        selected_modules = []
        for value in raw_modules:
            value = str(value).strip()
            if value and value not in selected_modules:
                selected_modules.append(value)
        unknown = [value for value in selected_modules if value not in available]
        if unknown:
            raise ValueError('存在无效检测模块：' + '、'.join(unknown))
        if not selected_modules:
            raise ValueError('至少启用一个检测模块')
        c['enabled_modules'] = selected_modules
    return c

def _persist_jobs():
    """Persist only restart-safe job metadata; never write channel keys."""
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        with LOCK:
            payload=[]
            for job in JOBS.values():
                if not job.get('batch'): continue
                payload.append({'id':job['id'],'batch':True,'suite':job.get('suite'),'models':job.get('models',[]),
                    'base':job.get('base',''),'status':job.get('status'),'started_at':job.get('started_at'),
                    'completed':job.get('completed',0),'total':job.get('total'),'children':[
                        {k:v for k,v in child.items() if k in ('id','model','status','completed','total','summary','run_id')}
                        for child in job.get('children',[])]})
        tmp=QUEUE_META.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8');tmp.replace(QUEUE_META)
    except Exception:
        pass

def run_job(job,c):
    directory = REPORTS / job['id']; directory.mkdir(parents=True, exist_ok=True)
    key = c['key']
    def emit(event):
        event = clean(event,key)
        with LOCK:
            job['events'].append(event)
            if len(job['events']) > 2000: job['events'] = job['events'][-2000:]
            if event.get('type')=='request_start': job['request_count']=job.get('request_count',0)+1
            if event.get('request_count') is not None: job['request_count']=event['request_count']
            if event.get('total') is not None: job['total'] = event['total']
            if event.get('completed') is not None: job['completed'] = event['completed']
        parent_emit=job.get('parent_emit')
        if parent_emit:
            parent_emit(event)
    try:
        if c['suite']=='ccmax':
            from ccmax_acceptance import run
            result = run(c,emit,job['cancel'].is_set)
        else: result = kvv_runner.run(c,emit,job['cancel'].is_set,directory)
        result = clean(result,key)
        # Mark disabled modules after the runner has produced its reviewed
        # evidence.  This keeps raw request evidence intact while preventing
        # unselected dimensions from being scored as passing.
        apply_enabled_modules(result, c)
        result['configuration'] = clean({**result.get('configuration',{}), **{k:v for k,v in c.items() if k!='key'}})
    except Exception as exc:
        result = {'suite':c['suite'],'status':'error','error':clean(str(exc),key),'summary':{}}
    finally:
        # Raw pytest artifacts can include echoed vendor errors. Persist only redacted text.
        for path in directory.iterdir():
            if path.is_file(): path.write_text(clean(path.read_text(errors='replace'),key),encoding='utf-8')
    result['started_at']=job['started_at'];result['finished_at']=time.time();result['run_id']=job['id']
    decorate(result)
    (directory/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        (directory/'report.html').write_bytes(report_html(result,directory))
    except Exception:
        result['report_export_error']='HTML 报告生成失败；测试结果已保存，可先下载 JSON 与证据包。'
        (directory/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with LOCK:
        job['result']=result; job['status']=result['status']; job['finished_at']=time.time()
        job['summary']=result.get('summary',{})
        if job['summary'].get('total') is not None: job['total']=job['summary']['total']
        job['completed']=job['summary'].get('completed',job['completed'])
    try:
        saved = AUTH_STORE.save_acceptance(result)
        job['history_id'] = saved.get('id'); job['history_saved'] = True
    except Exception as exc:
        job['history_saved'] = False
        job['history_error'] = str(exc)
    c['key']=''

def run_batch(parent, config):
    """Run selected models serially. Each child has its own report/history.

    The parent is only an orchestration record: it never sends a provider request
    and its persisted metadata never contains the channel key.
    """
    models=list(config.get('models') or [config.get('model','')])
    results=[]
    try:
        for index, model in enumerate(models):
            with LOCK:
                if parent['cancel'].is_set():
                    break
                child={'id':uuid.uuid4().hex,'suite':config['suite'],'model':model,'base':config['base'],
                    'status':'running','started_at':time.time(),'total':11 if config['suite']=='kvv11' else None,
                    'completed':0,'events':[],'cancel':parent['cancel'],'batch_child':True}
                parent['children'][index].update({'id':child['id'],'run_id':child['id'],'status':'running'})
                JOBS[child['id']]=child
                parent['current_run']=child['id']
            _persist_jobs()
            # Copy only progress into the parent; the child owns complete evidence.
            def relay(event, child=child, child_index=index, child_model=model):
                with LOCK:
                    parent['events'].append({'model':child_model,**event})
                    if len(parent['events'])>2000: parent['events']=parent['events'][-2000:]
                    parent['completed']=sum(int(x.get('completed') or 0) for x in parent['children'])
                    parent['total']=sum(int(x.get('total') or 0) for x in parent['children']) or None
                    parent['children'][child_index].update({'completed':child.get('completed',0),'total':child.get('total'),'summary':child.get('summary',{})})
            child_config={**config,'model':model,'models':[]}
            child['parent_emit']=relay
            run_job(child,child_config)
            with LOCK:
                child_result=child.get('result') or {'suite':config['suite'],'status':child.get('status','error'),'run_id':child['id'],'configuration':{'suite':config['suite'],'model':model,'base':config['base']}}
                verdict_status=(child_result.get('verdict') or {}).get('status')
                if verdict_status not in ('passed','failed','inconclusive','cancelled','skipped','not_covered'):
                    verdict_status='failed' if child.get('status') in ('error','failed') or child_result.get('status')=='error' else 'cancelled' if child.get('status')=='cancelled' else 'inconclusive'
                parent['children'][index].update({'status':verdict_status,'completed':child.get('completed',0),'total':child.get('total'),'summary':child.get('summary',{})})
                results.append({'model':model,'run_id':child['id'],'status':verdict_status,'result':child_result})
                parent['current_run']=None
            _persist_jobs()
        # Models not started after cancellation are explicitly retained.
        with LOCK:
            for item in parent['children'][len(results):]:
                if item.get('status')=='pending': item['status']='not_run'
            cancelled=parent['cancel'].is_set()
            completed=len(results)
            counts={k:0 for k in ('passed','failed','inconclusive','cancelled','not_run')}
            for item in parent['children']:
                status=item.get('status','not_run');
                if status in counts: counts[status]+=1
            if counts['failed']: verdict={'status':'failed','label':'至少一个模型未通过','detail':f"{counts['failed']} 个模型存在失败项；请展开各模型报告查看证据。"}
            elif cancelled or counts['not_run'] or counts['inconclusive'] or completed<len(models): verdict={'status':'inconclusive','label':'批量测试未完成或证据不足','detail':'已保留已完成模型结果，未启动模型不会计为通过。'}
            elif counts['passed']==len(models): verdict={'status':'passed','label':'所选模型均通过本轮检查','detail':'结论仅对本轮各模型独立测试负责。'}
            else: verdict={'status':'inconclusive','label':'批量结果无法确认全部通过','detail':'部分模型没有可用的完整通过证据。'}
            status='cancelled' if cancelled else ('completed' if completed==len(models) else 'inconclusive')
            summary={'total':len(models),'completed':completed,'passed':counts['passed'],'failed':counts['failed'],'inconclusive':counts['inconclusive'],'cancelled':counts['cancelled'],'not_run':counts['not_run']}
            safe_config={k:v for k,v in config.items() if k!='key'};safe_config['model']=f'多模型对比（{len(models)}）';safe_config['models']=models
            result={'suite':'batch_acceptance','status':status,'configuration':safe_config,'results':results,'models':models,'summary':summary,
                    'enabled_modules': list(config.get('enabled_modules') or []),
                    'module_definitions': _module_defs(config.get('suite')),
                    'verdict':verdict,'run_id':parent['id'],'started_at':parent['started_at'],'finished_at':time.time(),'log':'批量模型按顺序独立执行；未自动重试。'}
            parent['result']=result;parent['status']=status;parent['summary']=summary;parent['completed']=completed;parent['total']=len(models);parent['finished_at']=result['finished_at']
        directory=REPORTS/parent['id'];directory.mkdir(parents=True,exist_ok=True)
        (directory/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        try:(directory/'report.html').write_bytes(report_html(result,directory))
        except Exception as exc: parent['history_error']='HTML 报告生成失败：'+str(exc)
        try:
            saved=AUTH_STORE.save_acceptance(result);parent['history_id']=saved.get('id');parent['history_saved']=True
        except Exception as exc: parent['history_saved']=False;parent['history_error']=str(exc)
    finally:
        config['key']='';_persist_jobs()

def run_dispatch(job,c):
    if job.get('batch'): return run_batch(job,c)
    return run_job(job,c)

def snapshot(job):
    if job.get('batch'):
        data={k:v for k,v in job.items() if k not in ('cancel','result','events','children','models')}
        data.update({'batch':True,'models':job.get('models',[]),'children':job.get('children',[]),'current_run':job.get('current_run')})
        if job.get('current_run') and JOBS.get(job['current_run']): data['current_run_snapshot']=snapshot(JOBS[job['current_run']])
        data['elapsed']=round((job.get('finished_at') or time.time())-job['started_at'],1);data['result']=job.get('result')
        return data
    return {k:v for k,v in job.items() if k not in ('cancel','result','parent_emit')} | {'elapsed': round((job.get('finished_at') or time.time())-job['started_at'],1), 'result':job.get('result')}

def report_download_name(result, kind='html'):
    result = result if isinstance(result, dict) else {}
    configuration = result.get('configuration') if isinstance(result.get('configuration'), dict) else {}
    model = str(configuration.get('model') or result.get('model') or '未命名模型').strip()
    model = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '-', model)
    model = re.sub(r'\s+', ' ', model)[:80].strip() or '未命名模型'
    raw = result.get('finished_at') or result.get('started_at') or time.time()
    try: stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(float(raw) if float(raw) < 1e12 else float(raw) / 1000))
    except (TypeError, ValueError, OverflowError): stamp = time.strftime('%Y%m%d-%H%M%S')
    base = '测试报告-%s-%s' % (model, stamp)
    if kind == 'evidence.zip': return base + '-证据.zip'
    return base + '.' + str(kind).rsplit('.', 1)[-1]

def report_html(result, directory=None):
    from report_renderer import render_report
    return render_report(result, directory)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def cookie_token(self):
        raw = self.headers.get('Cookie', '')
        for part in raw.split(';'):
            key, sep, value = part.strip().partition('=')
            if sep and key == COOKIE_NAME:
                return value
        return ''
    def set_session_cookie(self, token):
        secure = os.environ.get('WORKBENCH_COOKIE_SECURE', '1') != '0'
        value = f'{COOKIE_NAME}={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict'
        if secure: value += '; Secure'
        self.send_header('Set-Cookie', value)
    def clear_session_cookie(self):
        self.send_header('Set-Cookie', f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict')
    def guard(self,auth=False,token_required=True):
        expected=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected: self.send_json(403,{'error':'仅允许本地工作台访问'}); return False
        origin=self.headers.get('Origin')
        if origin and origin != 'http://'+expected: self.send_json(403,{'error':'请求来源不匹配，请从本地工作台打开'}); return False
        if auth:
            if AUTH_STORE.enabled and not AUTH_STORE.identity(self.cookie_token()):
                # Browser pages are redirected; API clients receive a neutral 401.
                if self.path.startswith('/api/'):
                    self.send_json(401, {'error':'请先登录'}); return False
                self.send_response(302); self.send_header('Location','/login'); self.end_headers(); return False
            if token_required and not secrets.compare_digest(self.headers.get('X-Workbench-Token',''),TOKEN): self.send_json(403,{'error':'会话已过期，请刷新页面'}); return False
        return True
    def send_bytes(self,status,data,mime,filename=None):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        if filename:
            fallback = re.sub(r'[^A-Za-z0-9._-]+', '_', str(filename)) or 'download'
            self.send_header('Content-Disposition', "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, quote(str(filename), safe='')))
        self.end_headers();self.wfile.write(data)
    def send_json(self,status,data): self.send_bytes(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/login','/login.html','/login.css','/login.js','/api/auth'):
            if not self.guard(auth=False): return
            if path == '/api/auth':
                identity = AUTH_STORE.identity(self.cookie_token()) if AUTH_STORE.enabled else 'local'
                data = {'enabled': AUTH_STORE.enabled, 'authenticated': bool(identity)}
                if identity: data['username'] = identity
                return self.send_json(200, data)
            target = WEB / ('login.html' if path in ('/login','/login.html') else path[1:])
            if not target.is_file(): return self.send_json(404, {'error':'Not found'})
            return self.send_bytes(200,target.read_bytes(),(mimetypes.guess_type(target.name)[0] or 'text/plain')+'; charset=utf-8')
        if path == '/api/session':
            # This endpoint authenticates the browser and issues the per-page token.
            if not self.guard(auth=True, token_required=False): return
            with LOCK: active=next((j['id'] for j in JOBS.values() if j['status']=='running'),None)
            return self.send_json(200,{'token':TOKEN,'active':active,'latest':next(reversed(JOBS),None),'kvv_revision':'66092cf','ready':True,'auth_enabled':AUTH_STORE.enabled,'username':AUTH_STORE.identity(self.cookie_token()) if AUTH_STORE.enabled else None,'history_enabled':True})
        media_asset = bool(re.fullmatch(r'/api/history/[^/]+/media/\d+', path))
        protected = path.startswith('/api/') and path != '/api/auth'
        if (protected and path != '/api/session'):
            # Native <img>/<video>/<audio> requests cannot attach the page
            # token; the HttpOnly session cookie still protects stored media.
            if not self.guard(auth=True, token_required=not media_asset):return
        elif AUTH_STORE.enabled:
            # HTML/CSS/JS navigation is authorized by the login cookie. The
            # per-page token is required only for API mutations and history.
            if not self.guard(auth=True, token_required=False):return
        elif not self.guard(auth=False):
            return
        if path == '/api/history':
            try:
                query = urlsplit(self.path).query
                from urllib.parse import parse_qs
                q = parse_qs(query)
                data = AUTH_STORE.listing(kind=q.get('kind',[''])[0], status=q.get('status',[''])[0], q=q.get('q',[''])[0], offset=int(q.get('offset',['0'])[0]), limit=int(q.get('limit',['20'])[0]))
                return self.send_json(200, data)
            except Exception as exc: return self.send_json(400, {'error':str(exc)})
        if path.startswith('/api/history/'):
            parts = path.split('/')
            identity = parts[3] if len(parts)>3 else ''
            if len(parts) == 4:
                record = AUTH_STORE.detail(identity)
                return self.send_json(200, record) if record else self.send_json(404, {'error':'历史记录不存在'})
            if len(parts) == 6 and parts[4] == 'media':
                try: media = AUTH_STORE.media(identity, int(parts[5]))
                except ValueError: media = None
                if not media: return self.send_json(404, {'error':'媒体不存在'})
                blob = media['data']; start, end = 0, len(blob)-1
                range_header = self.headers.get('Range','')
                if range_header.startswith('bytes='):
                    try:
                        spec = range_header[6:].split(',',1)[0]; left,right = spec.split('-',1)
                        if left: start=int(left); end=int(right) if right else len(blob)-1
                        else: start=max(0,len(blob)-int(right)); end=len(blob)-1
                        if start<0 or end>=len(blob) or start>end: raise ValueError()
                    except ValueError: return self.send_json(416, {'error':'Range 无效'})
                    chunk=blob[start:end+1]; self.send_response(206); self.send_header('Content-Range',f'bytes {start}-{end}/{len(blob)}')
                else: chunk=blob; self.send_response(200)
                self.send_header('Content-Type',media['mime']); self.send_header('Content-Length',str(len(chunk))); self.send_header('Accept-Ranges','bytes'); self.send_header('Cache-Control','private, no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(chunk); return
            if len(parts) == 5 and parts[4] == 'report.json':
                record = AUTH_STORE.detail(identity)
                if not record: return self.send_json(404, {'error':'历史记录不存在'})
                return self.send_bytes(200,json.dumps(record,ensure_ascii=False,indent=2).encode(),'application/json',report_download_name(record, 'json'))
            if len(parts) == 5 and parts[4] == 'report.html':
                record = AUTH_STORE.detail(identity)
                if not record: return self.send_json(404, {'error':'历史记录不存在'})
                if record.get('run_id'):
                    result=record['result'];directory=REPORTS/record['run_id']
                else:
                    import base64
                    from browser_reports import normalize_browser_report
                    for item in record.get('media',[]):
                        if item.get('stored'):
                            # Inline only previously stored local bytes; do not fetch remote URLs.
                            index=int(item['url'].rsplit('/',1)[-1]);media=AUTH_STORE.media(identity,index)
                            if media:item['url']='data:'+media['mime']+';base64,'+base64.b64encode(media['data']).decode('ascii')
                    result=normalize_browser_report(record);directory=None
                return self.send_bytes(200,report_html(result,directory),'text/html; charset=utf-8',report_download_name(record,'html'))
            return self.send_json(404, {'error':'历史资源不存在'})
        if path.startswith('/api/runs/'):
            if not self.guard(auth=True):return
            parts=path.split('/');job=JOBS.get(parts[3])
            if not job:return self.send_json(404,{'error':'任务不存在'})
            if len(parts)==4:
                with LOCK:data=snapshot(job)
                return self.send_json(200,data)
            result=job.get('result')
            if not result:return self.send_json(409,{'error':'任务尚未完成'})
            if parts[4]=='report.html':return self.send_bytes(200,report_html(result,REPORTS/job['id']),'text/html; charset=utf-8',report_download_name(result, 'html'))
            if parts[4]=='report.json':return self.send_bytes(200,json.dumps(result,ensure_ascii=False,indent=2).encode(),'application/json',report_download_name(result, 'json'))
            if parts[4]=='evidence.zip':
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
                    root_dir=REPORTS/job['id']
                    for f in root_dir.rglob('*'):
                        if f.is_file() and f.name!='report.html':archive.write(f,f.relative_to(root_dir))
                    for child in job.get('result', {}).get('results', []) if isinstance(job.get('result'), dict) else []:
                        child_dir=REPORTS/str(child.get('run_id',''))
                        if not child_dir.is_dir(): continue
                        for f in child_dir.rglob('*'):
                            if f.is_file() and f.name!='report.html':
                                safe_model=re.sub(r'[^A-Za-z0-9._-]+','_',str(child.get('model','model')))[:80] or 'model'
                                archive.write(f,Path('models')/safe_model/f.relative_to(child_dir))
                    archive.writestr('report.html',report_html(result,REPORTS/job['id']))
                return self.send_bytes(200,data.getvalue(),'application/zip',report_download_name(result, 'evidence.zip'))
            return self.send_json(404,{'error':'产物不存在'})
        if path=='/' or path=='/index.html': target=WEB/'index.html'
        elif re.fullmatch(r'/[a-zA-Z0-9_-]+\.(js|css|html)',path): target=WEB/path[1:]
        else:return self.send_json(404,{'error':'Not found'})
        if not target.is_file():return self.send_json(404,{'error':'Not found'})
        return self.send_bytes(200,target.read_bytes(),(mimetypes.guess_type(target.name)[0] or 'text/plain')+'; charset=utf-8')
    def do_POST(self):
        path=urlsplit(self.path).path
        if path == '/api/auth/login':
            if not self.guard(auth=False): return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 65536: raise ValueError('请求长度无效')
                data=json.loads(self.rfile.read(length)); token,error=AUTH_STORE.login(data.get('username'),data.get('password'),self.client_address[0],self.cookie_token())
                if error == 'rate_limited': return self.send_json(429, {'error':'登录尝试过于频繁，请稍后再试'})
                if not token: return self.send_json(401, {'error':'用户名或密码错误'})
                self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.set_session_cookie(token); self.send_header('Content-Length','0'); self.end_headers(); return
            except Exception as exc: return self.send_json(400, {'error':str(exc)})
        if not self.guard(auth=True):return
        if path == '/api/auth/logout':
            AUTH_STORE.logout(self.cookie_token()); self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.clear_session_cookie(); self.send_header('Content-Length','0'); self.end_headers(); return
        if path == '/api/history':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > MAX_BODY: raise ValueError('历史记录请求过大')
                data=json.loads(self.rfile.read(length)); saved=AUTH_STORE.save(data)
                return self.send_json(200, saved)
            except Exception as exc: return self.send_json(400, {'error':str(exc)})
        if path == '/api/reports':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get_content_type()!='application/json' or not 0<length<=MAX_BODY: raise ValueError('报告请求格式无效或内容过大')
                from browser_reports import normalize_browser_report
                result=normalize_browser_report(json.loads(self.rfile.read(length)))
                return self.send_bytes(200,report_html(result),'text/html; charset=utf-8',report_download_name(result,'html'))
            except (ValueError,TypeError,KeyError) as exc:
                return self.send_json(400, {'error':'报告内容不完整，请刷新后重试：'+str(exc)})
        history_match = re.fullmatch(r'/api/runs/([a-f0-9]+)/history', path)
        if history_match:
            job = JOBS.get(history_match.group(1))
            if not job or not job.get('result'): return self.send_json(404, {'error':'任务报告不存在'})
            try:
                saved = AUTH_STORE.save_acceptance(job['result'])
                job['history_id'] = saved.get('id'); job['history_saved'] = True; job.pop('history_error', None)
                return self.send_json(200, saved)
            except Exception as exc:
                job['history_saved'] = False; job['history_error'] = str(exc)
                return self.send_json(500, {'error':'历史记录保存失败，请稍后重试'})
        if re.fullmatch(r'/api/runs/[a-f0-9]+/cancel',path):
            job=JOBS.get(path.split('/')[3])
            if not job:return self.send_json(404,{'error':'任务不存在'})
            job['cancel'].set();return self.send_json(200,{'status':'stopping'})
        if path=='/api/proxy':
            # Browser clients on a deployed workbench cannot call arbitrary
            # relay origins directly when the relay does not enable CORS.
            # Execute the already-built request server-side and return a small
            # response envelope so the browser can keep the same parser and
            # diagnostics as direct requests.
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get_content_type()!='application/json' or not 0<length<=50*1024*1024:
                    raise ValueError('代理请求格式无效或内容过大')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict): raise ValueError('代理请求必须是 JSON 对象')
                target=str(payload.get('url','')).strip(); method=str(payload.get('method','POST')).upper()
                parsed=urlsplit(target)
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                    raise ValueError('代理目标必须是完整的 HTTP(S) 地址')
                if method not in {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'}:
                    raise ValueError('代理请求方法不受支持')
                headers=payload.get('headers') or {}
                if not isinstance(headers,dict): raise ValueError('代理请求头格式无效')
                # Never allow a caller to pin the upstream host or ask httpx
                # to follow redirects.  Authentication is intentionally passed
                # through because the browser already supplied it to this
                # authenticated workbench request.
                upstream_headers={str(k):str(v) for k,v in headers.items() if str(k).lower() not in ('host','content-length','connection')}
                timeout=float(payload.get('timeout',120) or 120); timeout=max(.5,min(timeout,600))
                body=None; files=None; data=None
                form=payload.get('form')
                if isinstance(form,dict):
                    fields=form.get('fields') or {}
                    if not isinstance(fields,dict): raise ValueError('代理表单字段格式无效')
                    data={str(k):str(v) for k,v in fields.items()}
                    files=[]
                    for item in form.get('files') or []:
                        if not isinstance(item,dict): continue
                        name=str(item.get('field','file')); filename=str(item.get('name','upload')); mime=str(item.get('type','application/octet-stream'))
                        encoded=item.get('data','')
                        import base64
                        raw=base64.b64decode(encoded,validate=True)
                        files.append((name,(filename,raw,mime)))
                    # httpx builds the boundary and content type for multipart.
                    upstream_headers.pop('Content-Type',None); upstream_headers.pop('content-type',None)
                elif payload.get('body') is not None:
                    body=str(payload.get('body'))
                import base64, httpx
                with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False) as client:
                    response=client.request(method,target,headers=upstream_headers,content=body,data=data,files=files)
                ctype=response.headers.get('content-type','application/octet-stream')
                is_text=('text/' in ctype.lower() or 'json' in ctype.lower() or 'javascript' in ctype.lower() or 'xml' in ctype.lower() or 'event-stream' in ctype.lower())
                envelope={'status':response.status_code,'url':str(response.url),'content_type':ctype,'headers':{k:v for k,v in response.headers.items() if k.lower() in ('content-type','x-request-id','request-id','retry-after','content-length')}}
                if is_text:
                    envelope['text']=response.text
                else:
                    envelope['body_base64']=base64.b64encode(response.content).decode('ascii')
                return self.send_json(200,envelope)
            except Exception as exc:
                from httpx import TimeoutException, RequestError
                message='代理请求超时，请检查渠道连通性后重试。' if isinstance(exc,TimeoutException) else '服务器无法连接渠道，请检查渠道地址、TLS 证书和网络设置。' if isinstance(exc,RequestError) else str(exc)
                return self.send_json(400,{'error':clean(message,locals().get('headers',{}).get('Authorization',''))})
        if path=='/api/models':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get_content_type()!='application/json' or not 0<length<65536:raise ValueError('请求格式无效')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError('请求必须为 JSON 对象')
                base=str(data.get('base','')).strip();key=str(data.get('key','')).strip();auth=data.get('auth','bearer')
                from channel_discovery import fetch_models
                return self.send_json(200,fetch_models(base,key,auth))
            except Exception as exc:
                from httpx import TimeoutException, RequestError
                message='获取模型列表超时，请检查渠道连通性后重试。' if isinstance(exc,TimeoutException) else '服务器无法连接渠道，请检查渠道地址、TLS 证书和网络设置。' if isinstance(exc,RequestError) else str(exc)
                return self.send_json(400,{'error':clean(message,locals().get('key',''))})
        if path!='/api/runs':return self.send_json(404,{'error':'Not found'})
        try:
            if self.headers.get_content_type()!='application/json': raise ValueError('请求必须为 JSON')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<65536:raise ValueError('请求长度无效')
            payload=json.loads(self.rfile.read(length)); c=validate(payload)
            request_id=str(payload.get('client_request_id','')).strip()
            with LOCK:
                if request_id:
                    for existing in JOBS.values():
                        if existing.get('client_request_id')==request_id:
                            return self.send_json(202,{'id':existing['id'],'duplicate':True})
                if any(j['status']=='running' for j in JOBS.values()):return self.send_json(409,{'error':'已有验收任务运行中，请完成或取消后再开始'})
                models=c.get('models') or [c['model']]; batch=len(models)>1
                job={'id':uuid.uuid4().hex,'suite':c['suite'],'model':c['model'] if not batch else f'多模型对比（{len(models)}）','base':c['base'],'status':'running','started_at':time.time(),'total':(11 if c['suite']=='kvv11' else None) if not batch else len(models),'completed':0,'events':[],'cancel':threading.Event(),'client_request_id':request_id}
                if batch:
                    job.update({'batch':True,'models':models,'children':[{'model':m,'status':'pending','completed':0,'total':11 if c['suite']=='kvv11' else None,'summary':{}} for m in models],'current_run':None})
                JOBS[job['id']]=job
            _persist_jobs();threading.Thread(target=run_dispatch,args=(job,c),daemon=True).start()
            return self.send_json(202,{'id':job['id']})
        except (ValueError,TypeError) as exc:return self.send_json(400,{'error':str(exc)})

def restore_reports():
    if not REPORTS.exists(): return
    for path in sorted(REPORTS.glob('*/report.json'), key=lambda p:p.stat().st_mtime):
        try:
            result=json.loads(path.read_text())
            identity=path.parent.name
            if not re.fullmatch('[a-f0-9]+',identity):continue
            suite=result.get('configuration',{}).get('suite') or result.get('suite')
            if suite=='ccmax_acceptance':suite='ccmax'
            config=result.get('configuration',{});summary=result.get('summary',{})
            job={'id':identity,'suite':suite,'model':config.get('model',''),'base':config.get('base',''),
                'status':result.get('status','error'),'started_at':result.get('started_at',path.stat().st_mtime),
                'finished_at':result.get('finished_at',path.stat().st_mtime),'total':summary.get('total'),
                'completed':summary.get('completed',0),'summary':summary,'events':[],'result':result,'cancel':threading.Event()}
            if result.get('suite') == 'batch_acceptance':
                job.update({'batch':True,'models':result.get('models') or [x.get('model','') for x in result.get('results',[])],
                    'children':[{'id':x.get('run_id'),'run_id':x.get('run_id'),'model':x.get('model',''),'status':x.get('status','inconclusive'),'completed':(x.get('result') or {}).get('summary',{}).get('completed',0),'total':(x.get('result') or {}).get('summary',{}).get('total'),'summary':(x.get('result') or {}).get('summary',{})} for x in result.get('results',[])], 'current_run':None})
            JOBS[identity]=job
            # Backfill the durable history index for reports created before SQLite history.
            try:
                AUTH_STORE.save_acceptance(result, only_missing=True)
            except Exception:
                pass
        except (ValueError,OSError):continue
    # A process can stop between child requests before the parent report is
    # written. Restore only the safe queue metadata and mark it inconclusive;
    # never resume provider calls automatically.
    if QUEUE_META.is_file():
        try:
            queued = json.loads(QUEUE_META.read_text(encoding='utf-8'))
            for item in queued if isinstance(queued, list) else []:
                identity = item.get('id') if isinstance(item, dict) else ''
                if not re.fullmatch('[a-f0-9]+', str(identity)) or identity in JOBS or not item.get('batch'):
                    continue
                models = [str(x) for x in item.get('models', [])]
                children = []
                for child in item.get('children', []):
                    row = dict(child) if isinstance(child, dict) else {}
                    if row.get('status') in ('running','pending'): row['status'] = 'not_run'
                    children.append(row)
                JOBS[identity] = {'id':identity,'suite':item.get('suite'),'model':'多模型对比（%s）' % len(models),'base':item.get('base',''),
                    'status':'inconclusive','started_at':item.get('started_at') or time.time(),'finished_at':time.time(),
                    'total':item.get('total') or len(models),'completed':item.get('completed',0),'summary':{'total':len(models),'completed':0,'not_run':len(models)},
                    'events':[],'cancel':threading.Event(),'batch':True,'models':models,'children':children,'current_run':None}
        except (ValueError, OSError, TypeError):
            pass
    # Editing a derived report must not make an older run the latest run.
    ordered=sorted(JOBS.items(),key=lambda item:item[1]['started_at'])
    JOBS.clear();JOBS.update(ordered)
    for job in JOBS.values():
        try:
            saved = AUTH_STORE.save_acceptance(job.get('result') or {}, only_missing=True)
            job['history_id'] = saved.get('id'); job['history_saved'] = True
        except Exception as exc:
            job['history_saved'] = False; job['history_error'] = str(exc)
    # A queue metadata file means the service stopped while a batch was active.
    # Never resume provider calls after restart: expose an explicit incomplete
    # parent so the UI can show the already-finished child summaries safely.
    if QUEUE_META.is_file():
        try:
            queued=json.loads(QUEUE_META.read_text(encoding='utf-8'))
            for meta in queued if isinstance(queued,list) else []:
                identity=meta.get('id','')
                if not meta.get('batch') or identity in JOBS or not re.fullmatch('[a-f0-9]+',str(identity)): continue
                children=meta.get('children') if isinstance(meta.get('children'),list) else []
                children=[dict(x) for x in children]
                for child in children:
                    if child.get('status') in ('running','pending'): child['status']='not_run'
                models=[str(x) for x in (meta.get('models') or [])]
                done=sum(x.get('status') not in ('not_run','pending') for x in children)
                result={'suite':'batch_acceptance','status':'inconclusive','configuration':{'suite':meta.get('suite'),'model':f'多模型对比（{len(models)}）','models':models,'base':meta.get('base','')},
                    'models':models,'results':[{'model':x.get('model',''),'run_id':x.get('run_id'),'status':x.get('status','not_run')} for x in children],
                    'summary':{'total':len(models),'completed':done,'passed':sum(x.get('status')=='passed' for x in children),'failed':sum(x.get('status')=='failed' for x in children),'inconclusive':sum(x.get('status')=='inconclusive' for x in children),'not_run':sum(x.get('status')=='not_run' for x in children)},
                    'verdict':{'status':'inconclusive','label':'服务重启导致批量测试未完成','detail':'已完成的子模型结果保留；未开始的模型不会自动重试或计费。'},'run_id':identity,'started_at':meta.get('started_at',time.time()),'finished_at':time.time(),
                    'log':'服务在批量任务完成前重启，未自动恢复请求。'}
                directory=REPORTS/identity;directory.mkdir(parents=True,exist_ok=True)
                (directory/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                try:(directory/'report.html').write_bytes(report_html(result,directory))
                except Exception:pass
                JOBS[identity]={'id':identity,'batch':True,'suite':meta.get('suite'),'model':f'多模型对比（{len(models)}）','base':meta.get('base',''),'models':models,'children':children,'current_run':None,'status':'inconclusive','started_at':result['started_at'],'finished_at':result['finished_at'],'total':len(models),'completed':done,'summary':result['summary'],'events':[],'result':result,'cancel':threading.Event(),'history_saved':False}
                try:
                    saved=AUTH_STORE.save_acceptance(result,only_missing=True);JOBS[identity]['history_id']=saved.get('id');JOBS[identity]['history_saved']=True
                except Exception as exc:JOBS[identity]['history_error']=str(exc)
        except (OSError,ValueError,TypeError):
            pass

def main():
    restore_reports()
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8877);parser.add_argument('--open',action='store_true');args=parser.parse_args()
    server=WorkbenchServer(('127.0.0.1',args.port),Handler)
    print(f'工作台已启动：http://127.0.0.1:{server.server_port}',flush=True)
    if args.open:
        import webbrowser;webbrowser.open(f'http://127.0.0.1:{server.server_port}/')
    try:server.serve_forever()
    except KeyboardInterrupt:
        for job in JOBS.values():job['cancel'].set()
        time.sleep(1)
    finally:server.server_close()
if __name__=='__main__':main()
