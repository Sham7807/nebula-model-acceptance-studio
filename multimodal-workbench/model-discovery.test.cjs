'use strict';

// Offline transport contract tests. The hosted workbench must never depend on
// the upstream channel allowing browser CORS; standalone HTML stays explicit.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = () => fs.readFileSync(path.join(__dirname, 'model-discovery.js'), 'utf8');
const secret = 'fixture-channel-secret';
const config = overrides => ({ base: 'https://no-cors-channel.test/v1', key: secret, auth: 'bearer', ...overrides });
const plain = value => JSON.parse(JSON.stringify(value));
function json(body, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }); }
function harness(replies, protocol = 'https:') {
  const calls = [];
  const root = {
    location: new URL(protocol === 'file:' ? 'file:///offline/workbench.html' : 'https://workbench.test/'),
    URL, URLSearchParams, Headers, AbortController, DOMException, TypeError, setTimeout, clearTimeout,
    fetch: async (input, init = {}) => {
      const url = typeof input === 'string' ? input : input.url;
      calls.push({ url, init });
      assert.ok(replies.length, 'unplanned request: ' + url);
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return typeof reply === 'function' ? reply(input, init) : reply;
    },
  };
  root.window = root; root.globalThis = root;
  vm.runInNewContext(source(), root, { filename: 'model-discovery.js' });
  return { list: root.ModelDiscovery.list, calls, root };
}

test('hosted discovery sends credentials only to the same-origin service', async () => {
  const h = harness([json({ token: 'page-csrf-token' }), json({ models: ['kimi-k3', 'claude-fixture'], total: 2 })]);
  const result = await h.list(config());
  assert.deepEqual(plain(result.models), ['kimi-k3', 'claude-fixture']);
  assert.equal(result.transport, 'service');
  assert.equal(h.calls.length, 2);
  assert.equal(new URL(h.calls[0].url, h.root.location).pathname, '/api/session');
  const call = h.calls[1];
  const url = new URL(call.url, h.root.location);
  assert.equal(url.origin, 'https://workbench.test');
  assert.equal(url.pathname, '/api/models');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.credentials, 'same-origin');
  assert.equal(call.init.cache, 'no-store');
  assert.deepEqual(JSON.parse(call.init.body), config());
  const headers = new Headers(call.init.headers);
  assert.equal(headers.get('X-Workbench-Token'), 'page-csrf-token');
  assert.equal(headers.get('Authorization'), null);
  assert.equal(headers.get('x-api-key'), null);
  assert.equal(headers.get('x-goog-api-key'), null);
  assert.ok(h.calls.every(c => !c.url.includes(secret)), 'key never appears in a URL');
});

test('embedded srcdoc uses its inherited hosted origin for model discovery', async () => {
  const h = harness([json({ token: 'page-token' }), json({ models: ['embedded-model'] })]);
  h.root.location = { protocol: 'about:', href: 'about:srcdoc' };
  h.root.document = { baseURI: 'https://workbench.test/index.html' };
  const result = await h.list(config());
  assert.equal(result.transport, 'service');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/session', '/api/models']);
});

test('all supported auth modes use the same hosted discovery endpoint', async () => {
  for (const auth of ['bearer', 'anthropic', 'gemini', 'none']) {
    const h = harness([json({ token: 'token' }), json({ models: [], total: 0 })]);
    const c = config({ auth, key: auth === 'none' ? '' : secret });
    const result = await h.list(c);
    assert.deepEqual(plain(result.models), []);
    assert.equal(JSON.parse(h.calls.at(-1).init.body).auth, auth);
    assert.equal(new URL(h.calls.at(-1).url, h.root.location).pathname, '/api/models');
  }
});

test('a fresh attempt reacquires a session token after the server restarts', async () => {
  const h = harness([
    json({ token: 'old-token' }), json({ error: '会话已过期' }, 403),
    json({ token: 'new-token' }), json({ models: ['recovered-model'] }),
  ]);
  await assert.rejects(h.list(config()), error => error.status === 403);
  const recovered = await h.list(config());
  assert.deepEqual(plain(recovered.models), ['recovered-model']);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/session', '/api/models', '/api/session', '/api/models']);
  assert.equal(new Headers(h.calls[1].init.headers).get('X-Workbench-Token'), 'old-token');
  assert.equal(new Headers(h.calls[3].init.headers).get('X-Workbench-Token'), 'new-token');
});

test('failed service/session requests never fall back to the cross-origin channel', async () => {
  for (const replies of [
    [new TypeError('Failed to fetch')],
    [json({ token: 'token' }), new TypeError('Failed to fetch')],
    [json({ error: '请先登录' }, 401)],
    [json({ token: 'token' }), json({ error: '上游 HTTP 403' }, 400)],
    [json({})],
  ]) {
    const h = harness(replies);
    await assert.rejects(h.list(config()));
    assert.ok(h.calls.every(call => new URL(call.url, h.root.location).origin === 'https://workbench.test'));
    assert.ok(h.calls.length <= 2, 'failure is not silently retried against a different transport');
  }
});

test('cancelling during model discovery aborts the service request', async () => {
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const h = harness([json({ token: 'token' }), (_url, init) => new Promise((resolve, reject) => {
    started();
    if (init.signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  })]);
  const controller = new AbortController();
  const result = h.list(config(), { signal: controller.signal });
  await ready; controller.abort();
  await assert.rejects(result, error => error.name === 'AbortError');
  assert.equal(h.calls.at(-1).init.signal.aborted, true);
  assert.equal(h.calls.length, 2);
});

test('standalone HTML normalizes root/prefix/version without requesting a local service', async () => {
  const cases = [
    ['https://relay.test', 'bearer', '/v1/models'],
    ['https://relay.test/v1/', 'bearer', '/v1/models'],
    ['https://relay.test/prefix/v1', 'bearer', '/prefix/v1/models'],
    ['https://relay.test/prefix/v1beta', 'bearer', '/prefix/v1/models'],
    ['https://relay.test/prefix', 'gemini', '/prefix/v1beta/models'],
    ['https://relay.test/prefix/v1/', 'gemini', '/prefix/v1beta/models'],
    ['https://relay.test/prefix/v1beta/', 'gemini', '/prefix/v1beta/models'],
  ];
  for (const [base, auth, expected] of cases) {
    const h = harness([json(auth === 'gemini' ? { models: [{ name: 'models/gemini-fixture' }] } : { data: [{ id: 'fixture' }] })], 'file:');
    const result = await h.list(config({ base, auth }));
    assert.equal(result.transport, 'direct');
    assert.equal(h.calls.length, 1);
    assert.equal(new URL(h.calls[0].url).pathname, expected);
    assert.equal(h.calls[0].init.credentials, 'omit');
    assert.equal(h.calls[0].init.redirect, 'error');
    assert.deepEqual(plain(result.models), [auth === 'gemini' ? 'gemini-fixture' : 'fixture']);
  }
});

test('standalone auth selects exactly the provider header requested', async () => {
  for (const auth of ['bearer', 'anthropic', 'gemini', 'none']) {
    const h = harness([json({ data: [] })], 'file:');
    await h.list(config({ auth, key: auth === 'none' ? '' : secret }));
    const headers = new Headers(h.calls[0].init.headers);
    assert.equal(headers.get('Authorization'), auth === 'bearer' ? 'Bearer ' + secret : null);
    assert.equal(headers.get('x-api-key'), auth === 'anthropic' ? secret : null);
    assert.equal(headers.get('x-goog-api-key'), auth === 'gemini' ? secret : null);
    if (auth === 'anthropic') assert.equal(headers.get('anthropic-version'), '2023-06-01');
  }
});

test('standalone CORS/network failure explains that service mode can retrieve the catalog', async () => {
  const h = harness([new TypeError('Failed to fetch')], 'file:');
  await assert.rejects(h.list(config()), /CORS|跨域|服务/);
  assert.equal(h.calls.length, 1);
});

test('malformed catalog responses remain errors instead of empty successes', async () => {
  for (const payload of [{ unexpected: [] }, { error: { message: secret } }, { models: 'bad' }, { data: {} }, [null, 23, {}]]) {
    const h = harness([json(payload)], 'file:');
    await assert.rejects(h.list(config()), error => {
      assert.ok(!error.message.includes(secret));
      return /模型|响应|JSON/.test(error.message);
    });
  }
});

test('validation rejects unsafe addresses before sending any key', async () => {
  for (const base of ['file:///tmp/models', 'https://user:pass@relay.test', 'https://relay.test?key=secret', 'https://relay.test#fragment', 'invalid']) {
    for (const protocol of ['file:', 'https:']) {
      const h = harness([], protocol);
      await assert.rejects(h.list(config({ base })));
      assert.equal(h.calls.length, 0);
    }
  }
});
