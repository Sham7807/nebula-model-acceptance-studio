"""Local pytest adapter: structured progress, guarded rejection results, bounded clients."""
import json
import os
from pathlib import Path
import pytest

EVENT_FILE = os.environ.get('WORKBENCH_EVENTS')
RECORDER = None
TRANSPORT_WRITTEN = False

def event(data):
    if EVENT_FILE:
        with open(EVENT_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')


def pytest_collection_finish(session):
    event({'type': 'collected', 'total': len(session.items)})
    for module in {item.module for item in session.items}:
        original = getattr(module, '_make_request', None)
        if original and module.__name__.endswith('test_params'):
            def checked(*args, _original=original, **kwargs):
                success, message = _original(*args, **kwargs)
                if not success and not message.startswith('Rejected(400):'):
                    raise RuntimeError('无法判定参数约束：' + message)
                extra = kwargs.get('extra_params') or (args[4] if len(args) > 4 else {})
                if not success and extra and not any(name in message.lower() for name in extra):
                    raise RuntimeError('无法判定参数约束：400 未指向被测参数；' + message)
                return success, message
            module._make_request = checked


def pytest_runtest_logreport(report):
    if report.when == 'call' or (report.when == 'setup' and report.outcome != 'passed') or (report.when == 'teardown' and report.outcome == 'failed'):
        data={'type': 'case', 'id': report.nodeid, 'status': report.outcome,
               'phase': report.when, 'duration': report.duration, 'detail': str(report.longrepr) if report.longrepr else ''}
        observations=dict(getattr(report,'user_properties',[]) or []).get('workbench_observations')
        if observations:
            data['observations']=RECORDER.redact(observations) if RECORDER else observations
        event(data)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    outcome = yield
    if fixturedef.argname in ('client', 'hclient') and outcome.excinfo is None:
        obj = outcome.get_result()
        # Stream fixtures wrap both the SDK and raw httpx clients. Update each underlying
        # client as well, since assigning an attribute on the wrapper does not forward it.
        seen = set()
        while obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            if hasattr(obj, 'max_retries'):
                obj.max_retries = 0
            if hasattr(obj, 'timeout'):
                import httpx
                obj.timeout = httpx.Timeout(float(os.environ.get('WORKBENCH_REQUEST_TIMEOUT', '120')))
            obj = vars(obj).get('_client') if hasattr(obj, '__dict__') else None


def pytest_configure(config):
    global RECORDER
    if not EVENT_FILE: return
    import kvv_transport
    RECORDER=kvv_transport.install(os.environ.get('KIMI_API_KEY',''),os.environ['KIMI_BASE_URL'],
        float(os.environ.get('WORKBENCH_REQUEST_TIMEOUT','120')),Path(EVENT_FILE).parent/'requests.jsonl',event)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    import kvv_transport
    token=kvv_transport.set_case_id(item.nodeid)
    try: yield
    finally: kvv_transport.reset_case_id(token)


def save_transport():
    global TRANSPORT_WRITTEN
    if RECORDER and not TRANSPORT_WRITTEN:
        RECORDER.uninstall()
        summary=RECORDER.summary()
        (Path(EVENT_FILE).parent/'transport-summary.json').write_text(json.dumps(summary,ensure_ascii=False),encoding='utf-8')
        event({'type':'transport_summary','request_count':summary['request_count'],'completed_requests':summary['completed_requests']})
        TRANSPORT_WRITTEN=True


def pytest_sessionfinish(session, exitstatus):
    save_transport()


def pytest_unconfigure(config):
    save_transport()
