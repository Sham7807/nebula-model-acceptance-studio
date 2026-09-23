'use strict';
// Every HTTP request is fulfilled locally. Never sends real credentials or channel requests.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const origin = 'http://workbench.test';
const token = 'offline-history-session';
const errors = [], unexpected = [], passed = [];
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2Z0AAAAASUVORK5CYII=', 'base64');
function item(i) { return { id: 'history-' + i, kind: i === 1 ? 'image' : i === 2 ? 'ccmax' : i === 3 ? 'video' : 'text', source: i === 2 ? 'acceptance' : 'basic', title: '离线测试 ' + i, model: i === 2 ? 'claude-fixture' : 'fixture-model-' + i, base: 'https://relay.invalid/v1', prompt: '检验中文排版、构图和色彩 ' + i, status: i === 2 ? 'failed' : 'passed', created_at: 1789632000 + i, duration_ms: 15100, media_count: i === 1 ? 1 : 0, ...(i === 2 ? { run_id: 'aabbccdd' } : {}) }; }
async function fixture(browser, options = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const state = { requests: [], listQueries: [], login: [], logout: 0, listFail: false, detailFail: false, detail401: false, loginOk: false, holdOld: null, historyEnabled: options.historyEnabled !== false };
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    state.requests.push({ path: url.pathname, method: request.method() });
    if (url.origin !== origin) { unexpected.push(url.href); return route.abort(); }
    const send = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (url.pathname === '/api/auth') return send({ enabled: true, authenticated: false });
    if (url.pathname === '/api/auth/login') { state.login.push(JSON.parse(request.postData())); return send(state.loginOk ? { ok: true } : { error: 'credentials invalid' }, state.loginOk ? 200 : 401); }
    if (url.pathname === '/api/session') return send({ token, ready: true, active: null, latest: null, kvv_revision: '66092cf', ...(state.historyEnabled ? { auth_enabled: true, username: 'admin', history_enabled: true } : {}) });
    if (url.pathname === '/api/auth/logout') { assert.equal(request.headers()['x-workbench-token'], token); state.logout++; return send({ ok: true }); }
    if (url.pathname === '/api/history') {
      assert.equal(request.headers()['x-workbench-token'], token); state.listQueries.push(url.search);
      if (state.listFail) return send({ error: '数据库连接暂时不可用' }, 503);
      if (url.searchParams.get('q') === 'old') await new Promise(resolve => { state.holdOld = resolve; });
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 20), kind = url.searchParams.get('kind'), status = url.searchParams.get('status'), q = url.searchParams.get('q');
      let items = Array.from({ length: 25 }, (_, index) => item(index + 1));
      if (kind) items = items.filter(i => i.kind === kind);
      if (status) items = items.filter(i => i.status === status);
      if (q && q !== 'old' && q !== 'new') items = items.filter(i => (i.model + i.prompt).includes(q));
      if (q === 'old') items = [Object.assign(item(99), { model: 'old-response' })];
      if (q === 'new') items = [Object.assign(item(98), { model: 'new-response' })];
      return send({ items: items.slice(offset, offset + limit), total: items.length, offset, limit, stats: { total: 25, passed: 24, failed: 1, other: 0 } });
    }
    if (url.pathname.endsWith('/media/0')) return route.fulfill({ contentType: 'image/png', body: png });
    if (url.pathname === '/api/runs/aabbccdd/report.html' || url.pathname === '/api/runs/aabbccdd/report.json' || url.pathname === '/api/runs/aabbccdd/evidence.zip' || /\/api\/history\/[^/]+\/report.json/.test(url.pathname)) return route.fulfill({ contentType: 'application/octet-stream', body: '{"fixture":true}' });
    const detail = url.pathname.match(/^\/api\/history\/history-(\d+)$/);
    if (detail) {
      if (state.detail401) return send({ error: '登录过期' }, 401);
      if (state.detailFail) return send({ error: '暂时不可读取记录' }, 503);
      const n = Number(detail[1]), record = item(n), result = { text: n === 1 ? '中文排版测试输出 <img src=x onerror=alert(1)>' : '', config: { size: '1024x1024', prompt: record.prompt }, media_notes: ['一份超大媒体未保存，原始链接仍可使用。'], checks: n === 2 ? [{ id: 'sse', title: '完整收尾', status: 'failed', detail: '缺少 message_stop' }] : [], error: n === 2 ? '收尾检查未通过' : '', raw: { fixture: true } };
      const media = n === 1 ? [{ type: 'image', mime: 'image/png', url: '/api/history/history-1/media/0', stored: true }, { type: 'video', url: 'https://remote.invalid/test.mp4', stored: false }, { type: 'audio', url: 'javascript:alert(1)', stored: false }] : [];
      return send({ ...record, result, media });
    }
    if (url.pathname.startsWith('/api/')) { unexpected.push(url.href); return send({}, 404); }
    const asset = url.pathname === '/' ? 'index.html' : url.pathname === '/login' ? 'login.html' : url.pathname.slice(1);
    if (!/^[a-z0-9.-]+$/.test(asset)) return route.fulfill({ status: 404, body: '' });
    try { return route.fulfill({ contentType: ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(asset)], body: await fs.readFile(path.join(__dirname, asset)) }); } catch (_) { unexpected.push(url.href); return route.fulfill({ status: 404, body: '' }); }
  });
  await page.goto(origin + (options.login ? '/login' : '/'));
  return { page, state };
}
async function noOverflow(page) { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'viewport should not overflow'); }
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  const output = path.join(__dirname, 'qa-history'); await fs.mkdir(output, { recursive: true });
  try {
    {
      const { page, state } = await fixture(browser, { login: true });
      await page.locator('#username').fill('admin'); await page.locator('#password').fill('offline-pass');
      await page.locator('#togglePassword').click(); assert.equal(await page.locator('#password').getAttribute('type'), 'text');
      await page.locator('#togglePassword').click(); assert.equal(await page.locator('#password').getAttribute('type'), 'password');
      await page.locator('#loginSubmit').click(); await page.locator('#loginMessage').filter({ hasText: '账号或密码不正确' }).waitFor();
      assert.equal(await page.locator('#loginSubmit').isEnabled(), true); assert.deepEqual(state.login[0], { username: 'admin', password: 'offline-pass' });
      await page.screenshot({ path: path.join(output, 'login-desktop.png') }); await noOverflow(page);
      await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page); await page.screenshot({ path: path.join(output, 'login-mobile.png') });
      state.loginOk = true; await page.locator('#loginSubmit').click(); await page.waitForURL(origin + '/'); await page.locator('#accountName').filter({ hasText: 'admin' }).waitFor();
      await page.locator('#logoutButton').click(); await page.waitForURL(origin + '/login'); assert.equal(state.logout, 1);
      await page.close(); passed.push('login failure/success, show password, logout, desktop/mobile');
    }
    {
      const { page, state } = await fixture(browser);
      await page.locator('#prompt').fill('未运行的测试草稿'); await page.locator('#historyNavCount').filter({ hasText: '25' }).waitFor();
      await page.locator('#historyTab').click(); await page.locator('.history-record').nth(19).waitFor(); assert.equal(await page.locator('.history-record').count(), 20); assert.equal(await page.locator('#historyTotal').innerText(), '25');
      await noOverflow(page); await page.screenshot({ path: path.join(output, 'history-desktop.png') });
      await page.locator('#historyNext').click(); await page.locator('#historyRange').filter({ hasText: '21–25' }).waitFor(); assert.equal(await page.locator('.history-record').count(), 5);
      await page.locator('#historyPrevious').click(); await page.locator('#historyRange').filter({ hasText: '1–20' }).waitFor();
      await page.locator('#historyKind').selectOption('ccmax'); await page.locator('.history-record').filter({ hasText: 'claude-fixture' }).waitFor(); assert.equal(await page.locator('.history-record').count(), 1);
      await page.locator('.history-record').click(); await page.locator('.history-detail-check').filter({ hasText: '缺少 message_stop' }).waitFor();
      const dl = page.waitForEvent('download'); await page.getByRole('button', { name: '下载详细报告' }).click(); assert.match((await dl).suggestedFilename(), /^测试报告-claude-fixture-\d{8}-\d{6}\.html$/);
      await page.keyboard.press('Escape'); assert.equal(await page.locator('#historyDetail').isVisible(), false);
      await page.locator('#historyKind').selectOption('image'); await page.locator('.history-record').click(); await page.locator('.history-media img').waitFor();
      assert.equal(await page.locator('.history-detail-text img').count(), 0); assert.equal(await page.locator('.history-media video').count(), 0); assert.equal(await page.locator('.history-media .media-load').count(), 1); assert.equal(await page.locator('.history-media a[href^="javascript"]').count(), 0);
      assert.match(await page.locator('#historyDetailBody').innerText(), /超大媒体未保存/); assert.equal(await page.getByText('查看本次测试配置').count(), 1);
      await page.screenshot({ path: path.join(output, 'history-detail-desktop.png') }); await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(page); await page.screenshot({ path: path.join(output, 'history-detail-mobile.png') });
      await page.locator('#historyDetailClose').click(); await noOverflow(page); await page.screenshot({ path: path.join(output, 'history-mobile.png') });
      await page.locator('#workspaceTab').click(); assert.equal(await page.locator('#prompt').inputValue(), '未运行的测试草稿');
      await page.locator('#historyTab').click(); await page.locator('#historyKind').selectOption(''); await page.locator('#historyStatus').selectOption('failed'); await page.locator('.history-record').filter({ hasText: 'claude-fixture' }).waitFor(); assert.equal(await page.locator('.history-record').count(), 1);
      await page.locator('#historyStatus').selectOption(''); await page.locator('#historySearch').fill('不存在的模型'); await page.getByText('没有找到匹配的测试').waitFor();
      await page.locator('#historySearch').fill('old'); await page.waitForFunction(() => document.querySelector('#historyList').getAttribute('aria-busy') === 'true');
      for (let i = 0; i < 50 && !state.holdOld; i++) await new Promise(resolve => setTimeout(resolve, 20)); assert.ok(state.holdOld);
      await page.locator('#historySearch').fill('new'); await page.locator('.history-record').filter({ hasText: 'new-response' }).waitFor(); state.holdOld(); await page.waitForTimeout(100); assert.equal(await page.locator('.history-record').filter({ hasText: 'old-response' }).count(), 0);
      state.listFail = true; await page.locator('#historyRefresh').click(); await page.getByText('数据库连接暂时不可用').waitFor(); state.listFail = false; await page.getByRole('button', { name: '重新连接' }).click(); await page.locator('.history-record').waitFor();
      state.detailFail = true; await page.locator('.history-record').click(); await page.getByText('暂时不可读取记录').waitFor(); state.detailFail = false; await page.getByRole('button', { name: '重试读取' }).click(); await page.getByText('查看本次测试配置').waitFor(); await page.locator('#historyDetailClose').click();
      state.detail401 = true; await page.locator('.history-record').click(); await page.locator('#sessionNotice').filter({ hasText: '登录状态已过期' }).waitFor(); assert.equal(await page.locator('#sessionNotice a').getAttribute('href'), '/login?expired=1');
      await page.close(); passed.push('history paging, filters, stale responses, errors/retry, detail evidence, safe media, downloads, draft preservation, expiry');
    }
    {
      const { page, state } = await fixture(browser, { historyEnabled: false }); await page.locator('#historyTab').click(); await page.getByText('历史记录暂未连接').waitFor(); assert.equal(state.listQueries.length, 0); await page.close(); passed.push('legacy service without history stays compatible');
    }
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); console.log(JSON.stringify({ passed, realRequests: 0, pageErrors: errors }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
