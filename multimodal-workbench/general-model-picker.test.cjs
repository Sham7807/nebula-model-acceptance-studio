'use strict';

// Offline regression for the embedded general detector model picker.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const replies = [
      { status: 503, body: { error: { message: 'temporary upstream failure' } } },
      { status: 200, body: { models: [{ id: 'model-a' }, { id: 'model-b' }, { name: 'kimi-k3' }, { id: 'model-a' }] } },
    ];
    const calls = [];
    await page.route('**/*', async route => {
      const request = route.request();
      if (!request.url().startsWith('https://relay.test/')) return route.continue();
      calls.push({ url: request.url(), authorization: request.headers().authorization });
      const reply = replies.shift() || { status: 200, body: { data: [] } };
      await route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.body) });
    });
    await page.goto(pathToFileURL(path.join(__dirname, 'legacy.html')).href);
    await page.locator('#inBase').fill('https://relay.test/v1');
    await page.locator('#inKey').fill('picker-key');
    await page.locator('#loadGeneralModels').click();
    await page.waitForFunction(() => /获取失败/.test(document.getElementById('generalModelStatus').textContent));
    assert.match(await page.locator('#generalModelStatus').innerText(), /获取失败/);
    await page.locator('#loadGeneralModels').dispatchEvent('click');
    await page.locator('.model-option[data-model="kimi-k3"]').waitFor();
    assert.deepEqual(await page.locator('.model-option').allTextContents(), ['model-a', 'model-b', 'kimi-k3']);
    await page.locator('#generalModelSearch').fill('kimi');
    assert.deepEqual(await page.locator('.model-option').allTextContents(), ['kimi-k3']);
    await page.locator('.model-option[data-model="kimi-k3"]').click();
    assert.equal(await page.locator('#inModel').inputValue(), 'kimi-k3');
    assert.equal(calls.length, 2);
    assert.equal(calls[0].url, 'https://relay.test/v1/models');
    assert.equal(calls[1].authorization, 'Bearer picker-key');
    console.log('general model picker checks passed');
  } finally {
    await browser.close();
  }
})();
