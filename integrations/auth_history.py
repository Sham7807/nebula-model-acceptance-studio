"""Single-admin authentication and private, durable SQLite test history.

WORKBENCH_AUTH_FILE is an optional JSON file containing username/password_hash.
WORKBENCH_DB selects the database; media bytes live transactionally in the same DB.
WORKBENCH_COOKIE_SECURE=0 is only intended for loopback HTTP development/tests.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SESSION_AGE = 7 * 24 * 3600
COOKIE_NAME = 'workbench_session'
MAX_MEDIA = 16 * 1024 * 1024
MAX_MEDIA_TOTAL = 32 * 1024 * 1024
MAX_BODY = 48 * 1024 * 1024
MEDIA_MIMES = {
    'image': {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/avif', 'image/bmp'},
    'audio': {'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/ogg', 'audio/webm', 'audio/mp4', 'audio/aac', 'audio/flac', 'audio/x-flac'},
    'video': {'video/mp4', 'video/webm', 'video/ogg', 'video/quicktime', 'video/x-matroska'},
}
KINDS = {'text', 'image', 'video', 'audio', 'general', 'ccmax', 'kimi'}
STATUSES = {'passed', 'failed', 'cancelled', 'pending', 'inconclusive'}
SECRET_FIELD = re.compile(r'(?i)^(?:key|api[_-]?key|authorization|proxy-authorization|x-api-key|x-goog-api-key|password|passwd|password_hash|access[_-]?token|refresh[_-]?token|session[_-]?token|cookie|set-cookie|secret|client_secret|token)$')


def hash_password(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise ValueError('密码长度无效')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + salt.hex() + '$' + digest.hex()


def verify_password(password, stored):
    try:
        algorithm, n, r, p, salt, expected = stored.split('$')
        if algorithm != 'scrypt' or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        if not isinstance(password, str) or len(password) > 1024:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def redact(value, depth=0):
    """Remove credentials while preserving usage and max_tokens evidence."""
    if depth > 64:
        return '[嵌套内容过深，已省略]'
    if isinstance(value, dict):
        return {str(k): '[已隐藏]' if SECRET_FIELD.fullmatch(str(k)) else redact(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)(Bearer\s+)[^\s"<>]+', r'\1[已隐藏]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[已隐藏]', value)
        value = re.sub(r'(?i)([?&](?:api[_-]?key|key|token|access_token|signature|sig)=)[^&#\s"<>]+', r'\1[已隐藏]', value)
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def safe_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        # Signed media URLs are useful but credentials must never be retained.
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not SECRET_FIELD.fullmatch(k)]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))
    except ValueError:
        return None


def limited(value, budget=2 * 1024 * 1024):
    notes = []
    remaining = [budget]
    def visit(item, depth=0):
        if depth > 20 or remaining[0] <= 0:
            notes.append('部分字段超出历史记录容量，已省略')
            return '[已省略]'
        if isinstance(item, str):
            cap = min(200000, remaining[0])
            remaining[0] -= min(len(item), cap)
            if len(item) > cap:
                notes.append('部分文本字段已截断')
                return item[:cap] + '\n[内容过长，历史记录已截断]'
            return item
        if isinstance(item, dict):
            output = {}
            for index, (key, val) in enumerate(item.items()):
                if index >= 512:
                    notes.append('部分对象字段已省略'); break
                output[str(key)[:200]] = visit(val, depth + 1)
            return output
        if isinstance(item, (list, tuple)):
            if len(item) > 2000: notes.append('部分列表项目已省略')
            return [visit(val, depth + 1) for val in item[:2000]]
        return item
    result = visit(redact(value))
    return result, sorted(set(notes))


class Store:
    def __init__(self, database, auth_file=None):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.auth = None
        if auth_file:
            auth = json.loads(Path(auth_file).read_text(encoding='utf-8'))
            if not isinstance(auth, dict) or not isinstance(auth.get('username'), str) or not auth['username'] or not re.fullmatch(r'scrypt\$16384\$8\$1\$[a-f0-9]{32}\$[a-f0-9]{64}', auth.get('password_hash', '')):
                raise ValueError('工作台认证配置无效')
            self.auth = auth
        self.lock = threading.RLock()
        self.attempts = {}
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, credential_version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, client_id TEXT NOT NULL,
                    kind TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL, model TEXT NOT NULL,
                    base TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, duration_ms REAL NOT NULL, run_id TEXT,
                    result_json TEXT NOT NULL, media_notes_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(owner, client_id));
                CREATE INDEX IF NOT EXISTS history_owner_created ON history(owner, created_at DESC);
                CREATE TABLE IF NOT EXISTS media (
                    history_id TEXT NOT NULL REFERENCES history(id) ON DELETE CASCADE,
                    idx INTEGER NOT NULL, type TEXT NOT NULL, mime TEXT NOT NULL,
                    url TEXT, data BLOB, PRIMARY KEY(history_id, idx));
            ''')
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (time.time(),))
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        """Yield a short-lived SQLite connection and always close it.

        ``sqlite3.Connection`` implements a transaction context manager, but
        its ``__exit__`` method only commits/rolls back; it does *not* close
        the connection.  The history store opens a connection per operation,
        so relying on ``with sqlite3.connect(...)`` leaves file descriptors
        around until garbage collection and eventually exhausts the process
        limit under sustained traffic.  Keep transaction semantics while
        making the connection lifetime explicit and deterministic.
        """
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=15000')
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()

    @property
    def enabled(self):
        return bool(self.auth)

    @property
    def owner(self):
        return self.auth['username'] if self.auth else 'local'

    @property
    def credential_version(self):
        return hashlib.sha256((self.auth['username'] + '\0' + self.auth['password_hash']).encode()).hexdigest() if self.auth else ''

    def identity(self, token):
        if not self.enabled:
            return 'local'
        if not token or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute('SELECT username FROM sessions WHERE token_hash=? AND expires_at>? AND credential_version=?', (digest, time.time(), self.credential_version)).fetchone()
        return row['username'] if row else None

    def login(self, username, password, address, old_token=None):
        now = time.time()
        # Per-client and global windows cap expensive password hashing under load.
        buckets = [str(address), '*global*']
        with self.lock:
            for bucket in buckets:
                self.attempts[bucket] = [t for t in self.attempts.get(bucket, []) if t > now - 300]
            if len(self.attempts[buckets[0]]) >= 8 or len(self.attempts['*global*']) >= 80:
                return None, 'rate_limited'
            for bucket in buckets:
                self.attempts[bucket].append(now)
        if not self.enabled:
            return None, 'disabled'
        valid_password = verify_password(password, self.auth['password_hash'])
        if not isinstance(username, str) or not hmac.compare_digest(username, self.auth['username']) or not valid_password:
            return None, 'invalid'
        token = secrets.token_urlsafe(48)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            if old_token:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(old_token.encode()).hexdigest(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), self.owner, now, now + SESSION_AGE, self.credential_version))
        with self.lock:
            self.attempts.pop(str(address), None)
        return token, None

    def logout(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256((token or '').encode()).hexdigest(),))

    def save(self, data, owner=None, acceptance=False, only_missing=False):
        if not isinstance(data, dict): raise ValueError('历史记录必须为对象')
        owner = owner or self.owner
        kind = data.get('kind')
        source = data.get('source')
        if kind not in KINDS or (acceptance and source != 'acceptance') or (not acceptance and (source not in ('basic', 'general') or kind in ('ccmax', 'kimi'))):
            raise ValueError('历史记录类型无效')
        status = data.get('status', 'inconclusive')
        if status not in STATUSES: raise ValueError('历史记录状态无效')
        client_id = data.get('client_id')
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 200: raise ValueError('历史记录 client_id 无效')
        def small(name, length):
            value = data.get(name, '')
            if not isinstance(value, str): raise ValueError(name + ' 必须为文本')
            return redact(value[:length])
        title = small('title', 240) or {'text':'文本测试','image':'图像测试','video':'视频测试','audio':'音频测试','general':'通用检测','ccmax':'CCMax渠道验收','kimi':'Kimi KVV验证'}[kind]
        model = small('model', 500)
        base = safe_url(small('base', 2048)) or ''
        prompt = small('prompt', 100000)
        notes = []
        if len(data.get('prompt', '')) > 100000: notes.append('测试提示词超出长度限制，已截断')
        try:
            created = float(data.get('created_at', time.time()))
            duration = float(data.get('duration_ms', 0) or 0)
            if not math.isfinite(created) or created < 0 or not math.isfinite(duration) or not 0 <= duration <= 365 * 24 * 3600000: raise ValueError()
        except (ValueError, TypeError): raise ValueError('历史记录时间无效')
        raw_result = data.get('result', {})
        if not isinstance(raw_result, dict): raise ValueError('result 必须为对象')
        if acceptance:
            result = redact(raw_result)  # Do not truncate the 611-case official report.
        else:
            result, truncated = limited(raw_result)
            notes.extend(truncated)
        media = data.get('media', [])
        if not isinstance(media, list): raise ValueError('media 必须为列表')
        if len(media) > 32: notes.append('媒体超过32项，其余已省略')
        stored_media = []
        total = 0
        for index, item in enumerate(media[:32]):
            if not isinstance(item, dict): notes.append(f'第{index+1}项媒体格式无效，已省略'); continue
            media_type = item.get('type')
            mime = str(item.get('mime', '')).lower().split(';')[0].strip()
            if media_type not in MEDIA_MIMES or mime not in MEDIA_MIMES[media_type]:
                notes.append(f'第{index+1}项媒体类型不支持安全预览，已省略'); continue
            blob = None
            url = None
            if item.get('b64'):
                try:
                    encoded = item['b64']
                    if not isinstance(encoded, str) or len(encoded) > ((MAX_MEDIA + 2) // 3) * 4: raise ValueError()
                    blob = base64.b64decode(encoded, validate=True)
                    if not blob or len(blob) > MAX_MEDIA or total + len(blob) > MAX_MEDIA_TOTAL: raise ValueError()
                    total += len(blob)
                except (ValueError, TypeError):
                    notes.append(f'第{index+1}项媒体过大或编码无效，已省略'); continue
            elif item.get('url'):
                url = safe_url(item['url'])
                if not url: notes.append(f'第{index+1}项媒体链接不安全或无效，已省略'); continue
            else:
                notes.append(f'第{index+1}项媒体没有内容，已省略'); continue
            stored_media.append((media_type, mime, url, blob))
        if notes:
            existing = result.get('media_notes', [])
            result['media_notes'] = (existing if isinstance(existing, list) else [str(existing)]) + sorted(set(notes))
        run_id = data.get('run_id') if acceptance else None
        with self.lock, self.connection() as db:
            existing = db.execute('SELECT id, created_at FROM history WHERE owner=? AND client_id=?', (owner, client_id)).fetchone()
            if existing and only_missing:
                return {'id':existing['id'], 'history_saved':True}
            identity = existing['id'] if existing else uuid.uuid4().hex
            if existing: created = existing['created_at']
            db.execute('''INSERT INTO history (id,owner,client_id,kind,source,title,model,base,prompt,status,created_at,duration_ms,run_id,result_json,media_notes_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,client_id) DO UPDATE SET
                kind=excluded.kind,source=excluded.source,title=excluded.title,model=excluded.model,base=excluded.base,
                prompt=excluded.prompt,status=excluded.status,duration_ms=excluded.duration_ms,run_id=excluded.run_id,
                result_json=excluded.result_json,media_notes_json=excluded.media_notes_json''',
                (identity,owner,client_id,kind,source,title,model,base,prompt,status,created,duration,run_id,json.dumps(result,ensure_ascii=False),json.dumps(sorted(set(notes)),ensure_ascii=False)))
            db.execute('DELETE FROM media WHERE history_id=?', (identity,))
            db.executemany('INSERT INTO media VALUES (?,?,?,?,?,?)', [(identity,i,*item) for i,item in enumerate(stored_media)])
        return {'id':identity, 'history_saved':True, 'media_notes':sorted(set(notes))}

    def save_acceptance(self, result, only_missing=False):
        config = result.get('configuration') or {}
        run_id = result.get('run_id')
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]+', run_id): raise ValueError('报告缺少有效运行编号')
        ccmax = config.get('suite') == 'ccmax' or result.get('suite') == 'ccmax_acceptance'
        status = result.get('verdict', {}).get('status', 'inconclusive')
        if result.get('status') == 'cancelled': status = 'cancelled'
        finished = result.get('finished_at') or time.time()
        started = result.get('started_at') or finished
        return self.save({'client_id':'acceptance:' + run_id,'kind':'ccmax' if ccmax else 'kimi','source':'acceptance',
            'title':'CCMax渠道验收' if ccmax else ('Kimi KVV 11项预检' if config.get('suite') == 'kvv11' else 'Kimi KVV全套验证'),
            'model':config.get('model', ''),'base':config.get('base', ''),'prompt':'','status':status,'created_at':started,
            'duration_ms':max(0, (finished - started) * 1000),'result':result,'run_id':run_id}, acceptance=True, only_missing=only_missing)

    def summary(self, row):
        data = {key:row[key] for key in ('id','kind','source','title','model','base','status','created_at','duration_ms','run_id')}
        data['prompt'] = row['prompt'][:200]
        data['media_count'] = row['media_count']
        return data

    def listing(self, owner=None, kind='', status='', q='', offset=0, limit=20):
        owner = owner or self.owner
        clauses = ['h.owner=?']; args = [owner]
        if kind:
            if kind not in KINDS: raise ValueError('类型筛选无效')
            clauses.append('h.kind=?'); args.append(kind)
        if status == 'other':
            clauses.append("h.status NOT IN ('passed','failed')")
        elif status:
            if status not in STATUSES: raise ValueError('状态筛选无效')
            clauses.append('h.status=?'); args.append(status)
        if q:
            query = '%' + str(q)[:200].replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
            clauses.append("(h.title LIKE ? ESCAPE '\\' OR h.model LIKE ? ESCAPE '\\' OR h.prompt LIKE ? ESCAPE '\\' OR h.base LIKE ? ESCAPE '\\')")
            args.extend([query]*4)
        where = ' AND '.join(clauses)
        limit = min(100, max(1, int(limit))); offset = max(0, int(offset))
        with self.connection() as db:
            total = db.execute('SELECT count(*) FROM history h WHERE ' + where, args).fetchone()[0]
            stats = dict(db.execute("SELECT count(*) AS total,coalesce(sum(status='passed'),0) AS passed,coalesce(sum(status='failed'),0) AS failed,coalesce(sum(status NOT IN ('passed','failed')),0) AS other FROM history WHERE owner=?", (owner,)).fetchone())
            rows = db.execute('SELECT h.*, (SELECT count(*) FROM media m WHERE m.history_id=h.id) AS media_count FROM history h WHERE ' + where + ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
        return {'items':[self.summary(row) for row in rows], 'total':total,'stats':stats,'limit':limit,'offset':offset}

    def detail(self, identity, owner=None):
        with self.connection() as db:
            row = db.execute('SELECT h.*, (SELECT count(*) FROM media WHERE history_id=h.id) AS media_count FROM history h WHERE id=? AND owner=?', (identity, owner or self.owner)).fetchone()
            if not row: return None
            media = db.execute('SELECT idx,type,mime,url,length(data) AS bytes FROM media WHERE history_id=? ORDER BY idx', (identity,)).fetchall()
        data = self.summary(row)
        data['prompt'] = row['prompt']
        data['result'] = json.loads(row['result_json'])
        data['media_notes'] = json.loads(row['media_notes_json'])
        data['media'] = [{'type':item['type'],'mime':item['mime'],'url':f'/api/history/{identity}/media/{item["idx"]}' if item['bytes'] is not None else item['url'],'stored':item['bytes'] is not None,'bytes':item['bytes']} for item in media]
        return data

    def media(self, identity, index, owner=None):
        with self.connection() as db:
            row = db.execute('SELECT m.type,m.mime,m.data FROM media m JOIN history h ON h.id=m.history_id WHERE h.id=? AND h.owner=? AND m.idx=?', (identity,owner or self.owner,index)).fetchone()
        return dict(row) if row and row['data'] is not None else None
