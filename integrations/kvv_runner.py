"""Run the official Kimi Vendor Verifier, with no credentials on the command line."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT / 'Kimi-Vendor-Verifier'
K3_EXTENSIONS = [
 'tests/k3_features/test_workbench_capabilities.py::test_k3_dynamic_tool_in_system_calculator',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_top_level_tool_calculator',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_dynamic_tool_required',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_dynamic_and_top_level_tools_coexist',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_max_tokens_one_is_enforced',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_video_url_multimodal',
 'tests/k3_features/test_workbench_capabilities.py::test_k3_prompt_cache_repeatability',
]
PRECHECK = [
 'tests/params/test_params.py::test_no_param_succeeds[non-thinking]',
 'tests/params/test_params.py::test_no_param_succeeds[thinking]',
 'tests/params/test_params.py::test_wrong_param_rejected[non-thinking-temperature=1.1]',
 'tests/params/test_params.py::test_wrong_param_rejected[thinking-temperature=1.1]',
 'tests/tool_call_json_schema/test_tool_call_json_schema.py::test_tool_call_schema_matches_case_schema[TestAdditionalProperties:1:non-stream]',
 'tests/tool_call_json_schema/test_tool_call_json_schema.py::test_tool_call_schema_matches_case_schema[TestAdditionalProperties:1:stream]',
 'tests/k3_features/test_dynamic_tools.py::test_dynamic_tool_in_system_callable[nostream]',
 'tests/k3_features/test_response_format.py::test_json_object[nostream]',
 'tests/k3_features/test_tool_choice.py::test_tool_choice_required_forces_call[nostream]',
 'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[assistant_hello]',
 'tests/prompt_tokens/test_prompt_tokens.py::test_prompt_tokens_match_groundtruth[k3_tool_required]',
]
FULL = ['tests/params', 'tests/tool_call_json_schema', 'tests/k3_features', 'tests/prompt_tokens']

def _is_k3(config):
    model = str(config.get('model') or '').strip().lower()
    return model in {'kimi-k3', 'kimi_k3', 'kimi/k3'} or model.endswith('/kimi-k3')

def command(config, directory, collect=False):
    args = [sys.executable, '-m', 'pytest', '-p', 'kvv_progress']
    if config.get('think_mode') == 'openai':
        from kvv_openai_cases import PRECHECK as OPENAI_PRECHECK
        cases = str(ROOT / 'kvv_openai_cases.py')
        args += [cases+'::'+name for name in OPENAI_PRECHECK] if config['suite']=='kvv11' else [cases, 'tests/tool_call_json_schema']
        if config['suite']=='kvvfull':
            args += ['--think-mode','none','--tool-json-report='+str(directory/'schema.json')]
        args += ['--reruns','0','--force-reruns','0','-o','addopts=','-q']
        args += ['--collect-only'] if collect else ['--junitxml='+str(directory/'results.xml')]
        return args
    selected = PRECHECK if config['suite'] == 'kvv11' else FULL
    # The 11-item verifier remains stable for other model IDs.  When the
    # selected model is Kimi-K3, append the workbench capability probes so the
    # report includes the requested dynamic-tools, max_tokens, video and cache
    # evidence without pretending those K3-only contracts apply to every model.
    k3 = config['suite'] in ('kvv11', 'kvvfull') and _is_k3(config)
    # The extension file lives under tests/k3_features and would otherwise be
    # picked up by the full directory suite for every model. Ignore it during
    # directory collection, then append its explicit nodes only for Kimi-K3.
    args += selected
    if config['suite'] == 'kvvfull':
        args += ['--ignore=tests/k3_features/test_workbench_capabilities.py']
    if k3:
        args += K3_EXTENSIONS
    args += ['--think-mode', config.get('think_mode', 'kimi'), '--reruns', '0', '--force-reruns', '0', '-o', 'addopts=', '-q',
             '--tool-json-report=' + str(directory / 'schema.json')]
    if config.get('thinking', True): args += ['--thinking']
    if collect: args += ['--collect-only']
    else: args += ['--junitxml=' + str(directory / 'results.xml')]
    return args

def environment(config, directory):
    env = os.environ.copy()
    env.update(KIMI_API_KEY=config['key'], KIMI_BASE_URL=config['base'], MODEL_NAME=config['model'],
               PYTHONPATH=str(ROOT), PYTHONUNBUFFERED='1', WORKBENCH_EVENTS=str(directory / 'events.jsonl'),
               WORKBENCH_REQUEST_TIMEOUT=str(config.get('timeout', 120)), PYTEST_DISABLE_PLUGIN_AUTOLOAD='0')
    # PYTEST_DISABLE_PLUGIN_AUTOLOAD treats any nonempty value as true; required plugins are explicit.
    env.pop('PYTEST_DISABLE_PLUGIN_AUTOLOAD', None)
    return env

def merge_case(cases, event):
    """One row per pytest node, retaining failures from call and teardown."""
    previous = next((case for case in cases if case['id'] == event['id']), None)
    if previous is None:
        cases.append(dict(event))
        return cases[-1]
    phases = previous.setdefault('phases', [dict(previous)])
    phases.append(dict(event))
    previous['duration'] = previous.get('duration', 0) + event.get('duration', 0)
    if event['status'] == 'failed':
        previous['status'] = 'failed'
        previous['detail'] = '\n'.join(x for x in (previous.get('detail', ''), event.get('detail', '')) if x)
        previous['phase'] = event.get('phase')
    return previous

def classify_cases(cases, transport):
    """Keep official assertions separate from unavailable channel evidence."""
    for case in cases:
        case['pytest_status'] = case.get('pytest_status', case['status'])
        case['status'] = case['pytest_status']
        if 'kvv_openai_cases.py' in case['id'] and case['pytest_status']=='skipped':
            case['status']='not_covered'
        prefix = '渠道调用失败，无法据此判断模型能力或参数契约。\n'
        case['detail'] = case.get('detail', '').removeprefix(prefix)
        requests = [r for r in transport.get('requests', []) if r.get('case_id') == case['id']]
        case['request_count'] = len(requests)
        case['request_ids'] = [r.get('request_id') for r in requests]
        if (case['status'] == 'failed' and case.get('phase') != 'teardown' and requests
                and requests[-1].get('infrastructure_error')
                and not (requests[-1].get('termination') in ('client_closed', 'recorder_closed')
                         and 200 <= (requests[-1].get('http_status') or 0) < 300)):
            case['status'] = 'inconclusive'
            case['detail'] = prefix + case.get('detail', '')
    return cases

def run(config, emit, cancelled, directory):
    directory = Path(directory)
    event_path = directory / 'events.jsonl'
    stdout_path = directory / 'pytest.log'
    openai = config.get('think_mode') == 'openai'
    if openai:
        from kvv_openai_cases import OPENAI_CASE_SPECS
    extensions = not openai and config['suite'] in ('kvv11', 'kvvfull') and _is_k3(config)
    cases, total = [], (11 + len(K3_EXTENSIONS) if extensions else 11) if config['suite'] == 'kvv11' else None
    with stdout_path.open('w', encoding='utf-8') as output:
        process = subprocess.Popen(command(config, directory), cwd=REPO, env=environment(config, directory),
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        offset = 0
        def drain():
            nonlocal offset, total
            if not event_path.exists(): return
            with event_path.open(encoding='utf-8') as events:
                events.seek(offset)
                while True:
                    start = events.tell(); line = events.readline()
                    if not line or not line.endswith('\n'): offset = start; break
                    offset = events.tell()
                    try: event = json.loads(line)
                    except ValueError: continue
                    if event['type'] in ('request_start','request_finish','transport_summary'):
                        emit({**event,'total':total,'completed':len(cases)});continue
                    if event['type'] == 'collected': total = event['total']
                    elif event['type'] == 'case':
                        if openai:
                            spec=OPENAI_CASE_SPECS.get(event['id'].split('::')[-1].split('[',1)[0],{})
                            if spec:event['title']=spec['title']
                        event = merge_case(cases, event)
                    emit({'type': 'progress', 'total': total, 'completed': len(cases), 'case': event})
        while process.poll() is None:
            drain()
            if cancelled():
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
                break
            time.sleep(.25)
        drain()
    transport = load_transport(directory)
    classify_cases(cases, transport)
    return {'transport':transport,'suite': config['suite'], 'status': 'cancelled' if cancelled() else ('completed' if process.returncode in (0,1) else 'error'),
            'exit_code': process.returncode, 'source': ('Workbench OpenAI compatibility + KVV Schema' if config['suite']=='kvvfull' else 'Workbench OpenAI compatibility') if openai else 'MoonshotAI/Kimi-Vendor-Verifier',
            'request_format': 'openai' if openai else 'native',
            'compatibility': {'profile':'openai-chat-completions', 'scope':['参数与协议','工具与 JSON Schema','多模态及能力扩展','usage / token 与缓存'],
                'native_not_applicable':['Kimi system.tools 动态加载语义：使用标准顶层工具覆盖对应工作流，不视为原生动态加载通过。','Kimi thinking / keep 与思考字段优先级：OpenAI 模式使用 reasoning_effort 独立能力探针。','Kimi 固定 tokenizer 数值基准：改为 usage 计数一致性与缓存观测，不作原生数值匹配结论。'],
                'official_schema_matrix': config['suite']=='kvvfull'} if openai else None,
            'extensions': ['K3 渠道扩展能力：动态工具、max_tokens=1、video_url、多请求缓存 usage'] if extensions else [],
            'revision': '66092cf', 'summary': {'total': total, 'completed': len(cases),
                'passed': sum(c['status']=='passed' for c in cases), 'failed': sum(c['status']=='failed' for c in cases),
                'skipped': sum(c['status']=='skipped' for c in cases), 'not_covered':sum(c['status']=='not_covered' for c in cases), 'inconclusive':sum(c['status']=='inconclusive' for c in cases)},
            'cases': cases, 'log': stdout_path.read_text(errors='replace')[-100000:]}


def load_transport(directory):
    path=Path(directory)/'transport-summary.json'
    if path.exists():
        try:return json.loads(path.read_text())
        except (ValueError,OSError):pass
    path=Path(directory)/'requests.jsonl'
    started=set();requests=[];checks=[]
    if path.exists():
        for line in path.read_text(errors='replace').splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            if r.get('type')=='request_start':started.add(r.get('request_id'))
            if r.get('type')=='request_finish':
                requests.append({k:v for k,v in r.items() if k not in ('body','sse','response_headers','checks')})
                checks.extend({**c,'request_id':r.get('request_id'),'case_id':r.get('case_id')} for c in r.get('checks',[]))
    return {'request_count':len(started),'completed_requests':len(requests),'requests':requests,'checks':checks}
