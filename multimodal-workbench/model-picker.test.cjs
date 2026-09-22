'use strict';

// Offline browser regressions for model catalog selection, refresh and cancellation.
// Source index.html is intentional: packaging is verified by the existing UI suite.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const pageUrl = pathToFileURL(path.join(__dirname, 'index.html')).href;
const modelIds = ['model-alpha', 'model-beta', 'model-gamma'];
const headers = { 'content-type': 'application/json', 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' };
const errors = [];
const unexpected = [];
const checks = [];
let requestCount = 0;

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function catalog(ids = modelIds) { return { status: 200, body: { data: ids.map(id => ({ id })) } }; }
function pendingCatalog(ids) { return { ...catalog(ids), gate: deferred(), started: deferred() }; }

async function setup(browser) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.setDefaultTimeout(10000);
  const plans = [];
  const calls = [];
  const unfinished = new Set();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__modelFetches = [];
    const nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      const url = typeof input === 'string' ? input : input.url;
      if (/\/models(?:\?|$)/.test(url)) {
        const record = { url, aborted: !!init?.signal?.aborted };
        window.__modelFetches.push(record);
        init?.signal?.addEventListener('abort', () => { record.aborted = true; }, { once: true });
      }
      return nativeFetch.apply(this, arguments);
    };
  });
  await page.route('**/*', async route => {
    const request = route.request();
    if (!/^https?:/.test(request.url())) return route.continue();
    requestCount++;
    const url = new URL(request.url());
    if (!['relay.test', 'other-relay.test'].includes(url.hostname) || url.pathname !== '/v1/models') {
      unexpected.push(request.url());
      return route.abort('blockedbyclient');
    }
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    const plan = plans.shift();
    calls.push({ url: request.url(), method: request.method(), authorization: request.headers().authorization });
    if (!plan) {
      unexpected.push('Unplanned model fetch: ' + request.url());
      return route.fulfill({ status: 500, headers, body: JSON.stringify({ error: { message: 'Unexpected test request' } }) });
    }
    if (plan.gate) {
      unfinished.add(plan);
      plan.started.resolve();
      await plan.gate.promise;
      unfinished.delete(plan);
    }
    await route.fulfill({ status: plan.status, headers, body: JSON.stringify(plan.body) }).catch(() => {});
  });
  await page.goto(pageUrl);
  await page.locator('#modelToggle').waitFor();
  await page.locator('#base').fill('https://relay.test/v1');
  await page.locator('#key').fill('model-picker-key');
  return { page, plans, calls, close: async () => {
    for (const plan of unfinished) plan.gate.resolve();
    await page.close();
  } };
}
async function optionIds(page) {
  return page.locator('#modelList .model-option').evaluateAll(elements => elements.map(element => element.dataset.model));
}
async function waitForIds(page, expected) {
  await page.waitForFunction(ids => JSON.stringify([...document.querySelectorAll('#modelList .model-option')].map(element => element.dataset.model)) === JSON.stringify(ids), expected);
}
async function load(fixture, plan = catalog()) {
  fixture.plans.push(plan);
  await fixture.page.locator('#loadModels').click();
  if (plan.gate) await plan.started.promise;
  else if (plan.status === 200) {
    await fixture.page.waitForFunction(() => !/正在获取/.test(document.getElementById('modelListStatus').textContent));
    await waitForIds(fixture.page, plan.body.data.map(model => model.id));
  }
  else await fixture.page.waitForFunction(() => /失败|错误|HTTP|unavailable/i.test(document.getElementById('modelListStatus').textContent));
}
async function drainBrowser(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function assertLastFetchAborted(page) {
  await page.waitForFunction(() => window.__modelFetches.at(-1)?.aborted === true);
}

(async () => {
  const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
  try {
    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        assert.equal(await page.locator('#model').getAttribute('list'), null, 'custom picker replaces native datalist filtering');
        await load(fixture);
        assert.equal(await page.locator('#modelMenu').isVisible(), true);
        assert.match(await page.locator('#modelListStatus').innerText(), /3/);
        await page.locator('.model-option[data-model="model-alpha"]').click();
        assert.equal(await page.locator('#model').inputValue(), 'model-alpha');
        assert.equal(await page.locator('#modelMenu').isVisible(), true, 'checkbox selection leaves menu open');
        assert.deepEqual(await optionIds(page), modelIds, 'the arrow shows B/C even after A is selected');
        await page.locator('#modelSearch').fill('beta');
        await waitForIds(page, ['model-beta']);
        assert.equal(await page.locator('#model').inputValue(), 'model-alpha', 'catalog search does not change the selected model');
        await page.locator('.model-option[data-model="model-beta"]').click();
        assert.equal(await page.locator('#model').inputValue(), 'model-beta');
        assert.equal(await page.locator('#batch').inputValue(), 'model-alpha,model-beta');
        await page.locator('#modelToggle').click();
        assert.equal(await page.locator('#modelSearch').inputValue(), '');
        assert.deepEqual(await optionIds(page), modelIds);
        await page.locator('#modelSearch').fill('no-such-model');
        await waitForIds(page, []);
        assert.match(await page.locator('#modelListStatus').innerText(), /没有|无|未找到|0/);
        await page.locator('#modelSearch').press('Escape');
        assert.equal(await page.locator('#modelMenu').isVisible(), false);
        await page.locator('#model').fill('my/custom-model-id');
        assert.equal(await page.locator('#model').inputValue(), 'my/custom-model-id');
        await page.locator('#modelClear').click();
        assert.equal(await page.locator('#model').inputValue(), '');
        assert.equal(await page.locator('#modelMenu').isVisible(), true);
        assert.deepEqual(await optionIds(page), modelIds);
        await page.locator('#modelSearch').fill('gamma');
        await waitForIds(page, ['model-gamma']);
        await page.locator('#modelSearch').press('ArrowDown');
        await page.keyboard.press('Enter');
        assert.equal(await page.locator('#model').inputValue(), 'model-gamma');
        assert.equal(await page.locator('#modelMenu').isVisible(), true);
        await page.locator('#model').fill('model-');
        assert.equal(await page.locator('#modelMenu').isVisible(), true);
        await page.locator('.advanced summary').click();
        assert.equal(await page.locator('.advanced').evaluate(element => element.open), true, 'closing the picker must not swallow the outside summary click');
        assert.equal(await page.locator('#modelMenu').isVisible(), false);
        await page.locator('#modelToggle').click();
        await page.locator('#modelSearch').press('Escape');
        await page.waitForFunction(() => document.getElementById('modelMenu').hidden);
        assert.equal(fixture.calls.length, 1, 'selection, search and manual editing never refetch or generate');
        checks.push('full list after selection, independent search, custom ID, clear and keyboard navigation');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        await load(fixture);
        await page.locator('.model-option[data-model="model-alpha"]').click();
        await load(fixture, { status: 503, body: { error: { message: 'Catalog temporarily unavailable' } } });
        assert.equal(await page.locator('#loadModels').isEnabled(), true);
        assert.equal(await page.locator('#model').inputValue(), 'model-alpha');
        assert.deepEqual(await optionIds(page), modelIds, 'same-channel failure retains the last good catalog');
        assert.match(await page.locator('#modelListStatus').innerText(), /失败|错误|HTTP|unavailable/i);
        await load(fixture, catalog(['model-beta', 'model-delta']));
        assert.deepEqual(await optionIds(page), ['model-beta', 'model-delta']);
        assert.equal(await page.locator('#model').inputValue(), '', 'catalog refresh removes retired catalog models');
        assert.equal(fixture.calls.length, 3);
        checks.push('failed refresh keeps previous models and a later refresh recovers');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        const stale = pendingCatalog(['stale-cancelled-model']);
        await load(fixture, stale);
        assert.match(await page.locator('#modelListStatus').innerText(), /加载|获取|请求|正在/);
        assert.equal(await page.locator('#cancelModels').isVisible(), true);
        assert.equal(await page.locator('#loadModels').isEnabled(), true, 'refresh remains available while discovery is pending');
        await page.locator('#cancelModels').click();
        await assertLastFetchAborted(page);
        await load(fixture, catalog(['fresh-after-cancel']));
        stale.gate.resolve();
        await drainBrowser(page);
        assert.deepEqual(await optionIds(page), ['fresh-after-cancel']);
        assert.equal(fixture.calls.length, 2);
        checks.push('cancel then reload aborts the old request and blocks stale results');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        const stale = pendingCatalog(['stale-restarted-model']);
        await load(fixture, stale);
        fixture.plans.push(catalog(['fresh-after-restart']));
        await page.locator('#loadModels').click();
        await waitForIds(page, ['fresh-after-restart']);
        assert.equal(await page.evaluate(() => window.__modelFetches[0].aborted), true);
        stale.gate.resolve();
        await drainBrowser(page);
        assert.deepEqual(await optionIds(page), ['fresh-after-restart']);
        assert.equal(fixture.calls.length, 2);
        checks.push('refresh during loading replaces the pending request without a stale overwrite');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        await load(fixture);
        const oldChannel = pendingCatalog(['wrong-channel-model']);
        await load(fixture, oldChannel);
        await page.locator('#base').fill('https://other-relay.test/v1');
        await assertLastFetchAborted(page);
        await waitForIds(page, []);
        oldChannel.gate.resolve();
        await drainBrowser(page);
        assert.deepEqual(await optionIds(page), []);
        const stale = pendingCatalog(['wrong-key-model']);
        await load(fixture, stale);
        await page.locator('#key').fill('new-model-picker-key');
        await assertLastFetchAborted(page);
        await waitForIds(page, []);
        stale.gate.resolve();
        await drainBrowser(page);
        assert.deepEqual(await optionIds(page), []);
        await load(fixture, catalog(['right-key-model']));
        assert.equal(fixture.calls.at(-1).url, 'https://other-relay.test/v1/models');
        assert.equal(fixture.calls.at(-1).authorization, 'Bearer new-model-picker-key');
        assert.deepEqual(await optionIds(page), ['right-key-model']);
        await page.locator('#key').fill('third-key');
        await waitForIds(page, []);
        checks.push('channel/key edits invalidate model lists and in-flight discovery');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser);
      const { page } = fixture;
      try {
        await load(fixture, catalog([]));
        assert.equal(await page.locator('#modelMenu').isVisible(), true);
        assert.deepEqual(await optionIds(page), []);
        assert.match(await page.locator('#modelListStatus').innerText(), /没有|无|空|0|未返回/);
        assert.equal(await page.locator('#loadModels').isEnabled(), true);
        await load(fixture, catalog(['restored-model']));
        fixture.plans.push({ status: 200, body: { unexpected: [{ id: 'unverified-model' }] } });
        await page.locator('#loadModels').click();
        await page.waitForFunction(() => /失败|错误|无法识别/.test(document.getElementById('modelListStatus').textContent));
        assert.deepEqual(await optionIds(page), ['restored-model']);
        checks.push('valid empty catalogs remain retryable and malformed responses preserve the prior list');
      } finally { await fixture.close(); }
    }

    assert.deepEqual(unexpected, [], 'all provider traffic must be explicitly mocked');
    assert.deepEqual(errors, [], 'no browser runtime errors');
    for (const check of checks) console.log('PASS: ' + check);
    console.log(`PASS: ${checks.length} model picker groups; ${requestCount} intercepted HTTP requests; no real network`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
