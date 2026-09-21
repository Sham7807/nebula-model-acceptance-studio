'use strict';

// Every catalog entry point runs against the same mocked service. Any browser
// request to the provider is blocked, reproducing channels without CORS.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const origin = 'http://127.0.0.1:8877';
const key = 'offline-catalog-key';
const token = 'offline-page-token';

function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
async function fixture(browser, bundled = false) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(10000);
  const state = { models: ['kimi-k3', 'claude-fixture', 'image-fixture'], posts: [], external: [], errors: [], plans: [], held: new Set() };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => {
    window.__catalogFetches = [];
    const original = window.fetch;
    window.fetch = function (input, init) {
      if (String(typeof input === 'string' ? input : input.url).endsWith('/api/models')) {
        const row = { aborted: !!init?.signal?.aborted }; window.__catalogFetches.push(row);
        init?.signal?.addEventListener('abort', () => { row.aborted = true; }, { once: true });
      }
      return original.apply(this, arguments);
    };
  });
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!/^https?:$/.test(url.protocol)) return route.continue();
    if (url.origin !== origin) { state.external.push(request.url()); return route.abort('blockedbyclient'); }
    if (url.pathname === '/api/session') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ token, ready: true, history_enabled: false, kvv_revision: 'fixture' }) });
    if (url.pathname === '/api/models') {
      assert.equal(request.method(), 'POST');
      assert.equal(request.headers()['x-workbench-token'], token);
      assert.equal(request.headers().authorization, undefined);
      state.posts.push(JSON.parse(request.postData()));
      const plan = state.plans.shift() || { models: state.models };
      if (plan.gate) { state.held.add(plan); plan.started.resolve(); await plan.gate.promise; state.held.delete(plan); }
      return route.fulfill({ status: plan.status || 200, contentType: 'application/json', body: JSON.stringify(plan.error ? { error: plan.error } : { models: plan.models, total: plan.models.length }) }).catch(() => {});
    }
    const asset = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    assert.match(asset, /^[a-zA-Z0-9_.-]+$/, 'unexpected service request: ' + request.url());
    const mime = asset.endsWith('.js') ? 'text/javascript' : asset.endsWith('.css') ? 'text/css' : 'text/html';
    const filename = bundled && asset === 'index.html' ? path.join(__dirname, '..', '中转站测试工具-多模态版.html') : path.join(__dirname, asset);
    return route.fulfill({ contentType: mime, body: await fs.readFile(filename) });
  });
  await page.goto(origin + '/');
  await page.waitForFunction(() => document.getElementById('acceptanceService').textContent.includes('已连接'));
  await page.locator('#base').fill('https://no-cors-channel.test/v1');
  await page.locator('#key').fill(key);
  return { page, state, close: async () => { for (const plan of state.held) plan.gate.resolve(); await page.close(); } };
}

async function basicLoad(page, expected = 3) {
  await page.locator('#loadModels').click();
  await page.waitForFunction(n => document.querySelectorAll('#modelList .model-option').length === n && document.getElementById('modelHint').textContent.includes('已加载'), expected);
}

(async () => {
  const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
  try {
    for (const bundled of [false, true]) {
      const f = await fixture(browser, bundled), { page, state } = f;
      try {
        for (const kind of ['text', 'image', 'video', 'audio']) {
          await page.locator(`[data-kind="${kind}"]`).click();
          await basicLoad(page);
          assert.deepEqual(await page.locator('#modelList .model-option').evaluateAll(rows => rows.map(row => row.dataset.model)), state.models);
          assert.deepEqual(state.posts.at(-1), { base: 'https://no-cors-channel.test/v1', key, auth: 'bearer' });
        }
        await page.locator('[data-kind="text"]').click();
        await page.locator('#legacyBtn').click();
        const general = page.frameLocator('#legacyFrame');
        await general.locator('#inBase').fill('https://no-cors-channel.test/v1');
        await general.locator('#inKey').fill(key);
        await general.locator('#loadGeneralModels').click();
        await general.locator('.model-option[data-model="kimi-k3"]').waitFor();
        assert.equal(await general.locator('.model-option').count(), 3);
        assert.equal(state.posts.at(-1).auth, 'bearer');
        for (const suite of ['ccmax', 'kimi']) {
          await page.locator(`[data-suite="${suite}"]`).click();
          await page.locator('#acceptanceBase').fill('https://no-cors-channel.test/v1');
          await page.locator('#acceptanceKey').fill(key);
          await page.locator('#acceptanceModels').click();
          await page.waitForFunction(() => document.getElementById('acceptanceModelHint').textContent.includes('已获取 3'));
          const picker = page.locator('.choice-picker').filter({ has: page.locator('#acceptanceModel') });
          assert.equal(await picker.getByRole('option').count(), 3);
          assert.equal(state.posts.at(-1).auth, suite === 'ccmax' ? 'anthropic' : 'bearer');
        }
        assert.equal(state.posts.length, 7, 'four basic modes and all three deep suites use the shared service');
        assert.deepEqual(state.external, [], 'no catalog request escaped to the CORS-blocked channel');
        assert.deepEqual(state.errors, []);
        console.log('PASS: all seven model catalog entry points use service mode (' + (bundled ? 'bundled srcdoc' : 'source assets') + ')');
      } finally { await f.close(); }
    }

    const f = await fixture(browser), { page, state } = f;
    try {
      await basicLoad(page);
      await page.locator('.model-option[data-model="kimi-k3"]').click();
      state.plans.push({ status: 400, error: '获取模型列表失败：HTTP 401。请检查密钥。' });
      await page.locator('#loadModels').click();
      await page.waitForFunction(() => document.getElementById('modelListStatus').textContent.includes('HTTP 401'));
      assert.equal(await page.locator('#model').inputValue(), 'kimi-k3');
      assert.equal(await page.locator('#modelList .model-option').count(), 3, 'failed refresh retains a usable cached catalog');

      const stale = { models: ['stale-cancelled-model'], gate: deferred(), started: deferred() };
      state.plans.push(stale);
      await page.locator('#loadModels').click(); await stale.started.promise;
      await page.locator('#cancelModels').click();
      await page.waitForFunction(() => window.__catalogFetches.at(-1)?.aborted);
      state.plans.push({ models: ['fresh-model'] }); await basicLoad(page, 1);
      stale.gate.resolve();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator('#modelList .model-option').getAttribute('data-model'), 'fresh-model');
      assert.deepEqual(state.external, []);
      assert.deepEqual(state.errors, []);
      console.log('PASS: hosted service errors preserve the catalog and cancelled responses cannot overwrite a new list');
    } finally { await f.close(); }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
