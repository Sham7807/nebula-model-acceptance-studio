"""Private desktop engine: OS-assigned loopback port, ephemeral authenticated session.

The web project is imported unchanged. Credentials travel only over an inherited
pipe to the parent app; neither the CLI nor a predictable port grants access.
Closing stdin shuts down pending jobs and the service, including on parent death.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.data.mkdir(parents=True, exist_ok=True)
    os.chmod(args.data, 0o700)
    # Separate installations cannot invalidate each other's live session/database.
    lock = (args.data / '.engine.lock').open('a')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'type': 'error', 'message': '另一个渠道测试系统窗口正在使用本机数据库。请先退出已有应用，再重新打开。'}), flush=True)
        return
    os.environ.update(WORKBENCH_DB=str(args.data / 'history.sqlite3'),
                      WORKBENCH_REPORTS=str(args.data / 'Reports'),
                      WORKBENCH_COOKIE_SECURE='0', PYTHONDONTWRITEBYTECODE='1',
                      PYTEST_ADDOPTS='-p no:cacheprovider')
    os.environ.pop('WORKBENCH_AUTH_FILE', None)
    sys.path.insert(0, str(args.workspace / 'integrations'))
    import server
    from auth_history import COOKIE_NAME, hash_password
    # A fresh credential version invalidates earlier desktop sessions; the stable
    # owner retains history across launches without storing a login password.
    server.AUTH_STORE.auth = {'username': 'desktop', 'password_hash': hash_password(secrets.token_urlsafe(48))}
    cookie = secrets.token_urlsafe(48)
    now = time.time()
    with server.AUTH_STORE.connection() as db:
        db.execute('DELETE FROM sessions')
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                   (hashlib.sha256(cookie.encode()).hexdigest(), 'desktop', now,
                    now + 7 * 86400, server.AUTH_STORE.credential_version))
    server.restore_reports()
    httpd = server.WorkbenchServer(('127.0.0.1', 0), server.Handler)
    httpd.daemon_threads = True
    stopping = threading.Event()

    def stop(*_):
        if stopping.is_set():
            return
        stopping.set()
        with server.LOCK:
            for job in server.JOBS.values():
                job['cancel'].set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    def watch_parent():
        # Any shutdown line or EOF is terminal. No command execution interface.
        sys.stdin.buffer.readline()
        stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=watch_parent, daemon=True).start()

    def renew_session():
        while not stopping.wait(3600):
            with server.AUTH_STORE.connection() as db:
                db.execute('UPDATE sessions SET expires_at=? WHERE token_hash=?',
                           (time.time() + 7 * 86400, hashlib.sha256(cookie.encode()).hexdigest()))

    threading.Thread(target=renew_session, daemon=True).start()
    print(json.dumps({'type': 'ready', 'url': f'http://127.0.0.1:{httpd.server_port}',
                      'cookieName': COOKIE_NAME, 'cookie': cookie,
                      'csrf': server.TOKEN, 'pid': os.getpid()}), flush=True)
    try:
        httpd.serve_forever(poll_interval=0.15)
    finally:
        stop()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with server.LOCK:
                active = any(j.get('status') == 'running' for j in server.JOBS.values())
            if not active:
                break
            time.sleep(0.1)
        server.AUTH_STORE.logout(cookie)
        httpd.server_close()


if __name__ == '__main__':
    main()
