'use strict';
// Offline end-to-end checks for reusable choices, prompt scenarios and reference-image workflows.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const indexUrl = pathToFileURL(path.join(__dirname, 'index.html')).href;
const pngBase64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8qkAAAAASUVORK5CYII=';
const headers = { 'content-type': 'application/json', 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' };
const errors = [], unexpected = [], checks = [];
let requestCount = 0;

function reference(name, marker) {
  return { name, mimeType: 'image/png', buffer: Buffer.concat([Buffer.from(pngBase64, 'base64'), Buffer.from(marker)]) };
}
const referenceA = reference('reference-a.png', 'REFERENCE_A');
const referenceB = reference('reference-b.png', 'REFERENCE_B');
const referenceC = reference('reference-c.png', 'REFERENCE_C');
const dataUrl = file => `data:${file.mimeType};base64,${file.buffer.toString('base64')}`;

async function setup(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(10000);
  const calls = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request();
    if (!/^https?:/.test(request.url())) return route.continue();
    requestCount++;
    const url = new URL(request.url());
    if (url.hostname !== 'relay.test') { unexpected.push(request.url()); return route.abort('blockedbyclient'); }
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    const call = { method: request.method(), path: url.pathname, headers: request.headers(), body: request.postData() };
    calls.push(call);
    if (url.pathname === '/v1/images/generations' || url.pathname === '/v1/images/edits') return route.fulfill({ headers, body: JSON.stringify({ data: [{ b64_json: pngBase64 }] }) });
    if (/\/v1beta\/models\/[^/]+:generateContent$/.test(url.pathname)) return route.fulfill({ headers, body: JSON.stringify({ candidates: [{ content: { parts: [{ inlineData: { mimeType: 'image/png', data: pngBase64 } }] } }] }) });
    unexpected.push(request.url());
    return route.fulfill({ status: 400, headers, body: '{"error":{"message":"Unexpected workflow request"}}' });
  });
  await page.goto(indexUrl);
  await page.locator('#base').fill('https://relay.test/v1');
  await page.locator('#key').fill('workflow-offline-key');
  await page.locator('#model').fill('workflow-model');
  return { page, calls, close: () => page.close() };
}
async function preview(page) {
  await page.locator('#previewBtn').click();
  await page.locator('#previewDialog').waitFor({ state: 'visible' });
  const value = JSON.parse(await page.locator('#requestPreview').innerText());
  await page.locator('#closePreview').click();
  return value;
}
async function waitForResult(page, count) {
  await page.waitForFunction(expected => document.querySelectorAll('.result-card').length === expected && !document.getElementById('runBtn').disabled, count);
}
async function scenarioPrompt(page, category, id) {
  return page.evaluate(({ category, id }) => window.PromptLibrary[category].find(item => item.id === id)?.prompt, { category, id });
}
async function dimensions(page) {
  return page.evaluate(() => Object.fromEntries(['size', 'resolution'].map(id => [id, document.getElementById(id).value])));
}

(async () => {
  const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
  try {
    {
      const fixture = await setup(browser); const { page, calls } = fixture;
      try {
        await page.locator('[data-kind="video"]').click();
        await page.locator('#model').fill('workflow-video');
        for (const [id, values] of [['size', ['1024x1024', '1536x1024', '1024x1024']], ['resolution', ['480p', '1080p', '720p', '480p']]]) {
          const picker = page.locator('.choice-picker').filter({ has: page.locator(`#${id}`) });
          assert.equal(await page.locator(`#${id}`).getAttribute('list'), null);
          for (const value of values) {
            await picker.locator('.choice-toggle').click();
            assert.ok(await picker.locator('.choice-option:visible').count() >= 5);
            await picker.locator(`.choice-option[data-value="${value}"]`).click();
            assert.equal(await page.locator(`#${id}`).inputValue(), value);
          }
          await picker.locator('.choice-clear').click();
          assert.equal(await page.locator(`#${id}`).inputValue(), '');
          await page.locator(`#${id}`).fill('provider-custom-value');
          await page.locator(`#${id}`).press('Escape');
          assert.equal(await page.locator(`#${id}`).inputValue(), 'provider-custom-value');
        }
        await page.locator('#resolution').focus();
        await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
        assert.equal(await page.locator('#resolution').inputValue(), '720p');
        await page.locator('#prompt').fill('Preserve this video prompt.');
        await page.locator('#size').fill('1280x720');
        await page.locator('.advanced summary').click();
        await page.locator('#extra').fill('{"seed":42}');
        await page.locator('#duration').fill('8');
        await page.locator('#preset').selectOption('doubao-video');
        assert.equal(await page.locator('#prompt').inputValue(), 'Preserve this video prompt.');
        assert.equal(await page.locator('#size').inputValue(), '1280x720');
        assert.equal(await page.locator('#resolution').inputValue(), '720p');
        assert.equal(await page.locator('#duration').inputValue(), '8');
        assert.equal(await page.locator('#extra').inputValue(), '{"seed":42}');
        await page.locator('[data-kind="image"]').click();
        assert.equal(await page.locator('#size').inputValue(), '', 'new image draft does not inherit video dimensions');
        assert.equal(await page.locator('#extra').inputValue(), '{}');
        await page.locator('[data-kind="video"]').click();
        assert.equal(await page.locator('#preset').inputValue(), 'doubao-video');
        assert.equal(await page.locator('#resolution').inputValue(), '720p');
        assert.equal(await page.locator('#extra').inputValue(), '{"seed":42}');
        assert.equal(calls.length, 0, 'choice changes do not submit requests');
        checks.push('repeated arrow/keyboard choices, clear/custom values, protocol preservation and isolated modality drafts');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser); const { page, calls } = fixture;
      try {
        const scenario = page.locator('#promptScenario');
        const prompt = page.locator('#prompt');
        const drafts = new Map();
        assert.equal(await scenario.count(), 1);
        assert.equal(await page.locator('#applyPrompt').count(), 0, 'scenario selection needs no second apply button');
        const cases = [
          { kind: 'text', id: 'reasoning', preset: 'openai-chat', alternative: 'anthropic', requestPrompt: body => body.messages[0].content },
          { kind: 'image', id: 'photo', preset: 'openai-image', alternative: 'gemini-image', requestPrompt: body => body.prompt },
          { kind: 'video', id: 'motion', preset: 'relay-video-json', alternative: 'doubao-video', requestPrompt: body => body.prompt },
          { kind: 'audio', id: 'neutral', preset: 'openai-speech', alternative: 'gemini-speech', requestPrompt: body => body.input },
        ];
        for (const item of cases) {
          await page.locator(`[data-kind="${item.kind}"]`).click();
          await page.locator('#preset').selectOption(item.preset);
          await page.locator('#model').fill(`workflow-${item.kind}`);
          if (['image', 'video'].includes(item.kind)) await page.locator('#size').fill('1536x1024');
          if (item.kind === 'video') await page.locator('#resolution').fill('1080p');
          const before = await dimensions(page);
          const expected = await scenarioPrompt(page, item.kind, item.id);
          assert.equal(typeof expected, 'string');
          assert.ok(expected.length > 0, `${item.kind} scenario has usable text`);
          await prompt.fill(`用户手写的 ${item.kind} 草稿`);
          assert.equal(await scenario.inputValue(), '', 'editing switches to the custom prompt option');
          assert.match(await scenario.locator('option[value=""]').innerText(), /自定义/);
          await scenario.selectOption(item.id);
          assert.equal(await prompt.inputValue(), expected, `${item.kind} selection immediately applies the entire prompt`);
          assert.equal(await scenario.inputValue(), item.id);
          assert.deepEqual(await dimensions(page), before, 'scenario selection preserves size and resolution');
          assert.equal(item.requestPrompt((await preview(page)).body), expected, `${item.kind} preview uses the chosen prompt`);

          await prompt.fill(expected + '\n用户继续修改，不应被自动覆盖。');
          assert.equal(await scenario.inputValue(), '');
          await scenario.selectOption(item.id);
          assert.equal(await prompt.inputValue(), expected, 'the same scenario can be selected again after editing');
          await prompt.fill('临时自定义文本');
          await prompt.fill(expected);
          assert.equal(await scenario.inputValue(), item.id, 'an exact manual match synchronizes the scenario selection');

          const draft = `保留 ${item.kind} 自定义草稿\n第二行包含 "引号"、中文和 42。`;
          await prompt.fill(draft);
          await scenario.selectOption('');
          assert.equal(await prompt.inputValue(), draft, 'choosing custom does not erase the draft');
          await page.locator('#model').fill(`workflow-${item.kind}-another`);
          await page.locator('#preset').selectOption(item.alternative);
          assert.equal(await prompt.inputValue(), draft, 'model/protocol changes preserve manual text');
          assert.equal(await scenario.inputValue(), '');
          assert.deepEqual(await dimensions(page), before);
          drafts.set(item.kind, { prompt: draft, dimensions: before });
          assert.equal(calls.length, 0, 'choosing, editing and previewing scenarios sends no API request');
        }
        for (const [kind, draft] of drafts) {
          await page.locator(`[data-kind="${kind}"]`).click();
          assert.equal(await prompt.inputValue(), draft.prompt, 'switching modalities restores each manual draft');
          assert.equal(await scenario.inputValue(), '');
          assert.deepEqual(await dimensions(page), draft.dimensions);
        }

        await page.locator('[data-kind="image"]').click();
        await page.locator('#preset').selectOption('openai-image');
        // Switching the protocol invalidates the model catalog/context; enter
        // the provider model again before previewing the new request.
        await page.locator('#model').fill('workflow-image-poster');
        const poster = await scenarioPrompt(page, 'image', 'live-sale-poster');
        assert.equal(typeof poster, 'string', 'the live sale poster scenario exists');
        assert.ok(poster.length > 500 && poster.split('\n').length >= 5, 'poster remains a complete multiline test prompt');
        const beforePoster = await dimensions(page);
        await scenario.selectOption('live-sale-poster');
        assert.equal(await prompt.inputValue(), poster, 'long poster text is copied without shortening or reformatting');
        assert.equal((await preview(page)).body.prompt, poster, 'request preview preserves every line of the complete poster');
        assert.deepEqual(await dimensions(page), beforePoster);
        assert.equal(calls.length, 0);
        checks.push('instant scenarios in all four modalities, editable/reselectable text, full poster preview and preserved manual drafts without API calls');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser); const { page, calls } = fixture;
      try {
        await page.locator('[data-kind="image"]').click();
        await page.locator('#model').fill('workflow-image');
        const mode = page.locator('#imageMode'); assert.equal(await mode.count(), 1);
        const options = await mode.locator('option').evaluateAll(items => items.map(option => option.value));
        assert.ok(options.includes('generation') && options.includes('edit') && options.includes('reference'));
        const fileInput = page.locator('#files');
        await mode.selectOption('edit');
        assert.equal(await page.locator('#preset').inputValue(), 'openai-image-edit');
        assert.match(await page.locator('#fileLabel').innerText(), /原始图片|图片/);
        await fileInput.setInputFiles([referenceA, referenceB]);
        const thumbs = page.locator('#inputPreview img');
        assert.equal(await thumbs.count(), 2);
        const controls = page.locator('#inputPreview button');
        assert.ok(await controls.count() >= 4, 'multi-reference thumbnails expose order/removal controls');
        await page.locator('[data-action="move-down"]').first().click();
        await page.locator('[data-action="remove-reference"]').last().click();
        assert.equal(await page.locator('#inputPreview img').count(), 1);
        await mode.selectOption('reference');
        assert.equal(await page.locator('#preset').inputValue(), 'relay-image-json');
        await fileInput.setInputFiles(referenceC);
        assert.equal(await page.locator('#inputPreview img').count(), 2, 'incremental append preserves existing reference');
        const request = await preview(page);
        const image = request.body.image;
        assert.ok(typeof image === 'string' || Array.isArray(image));
        const values = Array.isArray(image) ? image : [image];
        assert.equal(values.length, 2);
        assert.deepEqual(values, [dataUrl(referenceB), dataUrl(referenceC)], 'request order follows thumbnails after move/remove/append');
        await fileInput.setInputFiles(referenceC);
        assert.equal(await thumbs.count(), 2, 'adding the same reference does not duplicate it');
        await page.locator('#runBtn').click(); await waitForResult(page, 1);
        assert.deepEqual(JSON.parse(calls.at(-1).body).image, [dataUrl(referenceB), dataUrl(referenceC)]);
        assert.equal(await page.locator('.media-item img').count(), 1);
        assert.equal(calls.length, 1);
        checks.push('image mode routes generation/edit/reference and preserves ordered multi-image input');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser); const { page, calls } = fixture;
      try {
        await page.locator('[data-kind="image"]').click();
        await page.locator('#model').fill('workflow-image');
        const scenarios = () => page.locator('#promptScenario option').evaluateAll(items => items.map(item => item.value));
        const generationScenarios = await scenarios();
        for (const id of ['', 'photo', 'poster', 'count', 'watercolor', 'product', 'live-sale-poster']) assert.ok(generationScenarios.includes(id));
        assert.ok(!generationScenarios.includes('multi-compose') && !generationScenarios.includes('edit-background'));
        await page.locator('#prompt').fill('My manually written image prompt.');
        await page.locator('#imageMode').selectOption('edit');
        const editScenarios = await scenarios();
        for (const id of ['', 'edit-background', 'edit-style']) assert.ok(editScenarios.includes(id));
        assert.ok(!editScenarios.includes('photo') && !editScenarios.includes('multi-compose'));
        assert.equal(await page.locator('#prompt').inputValue(), 'My manually written image prompt.');
        assert.equal(await page.locator('#promptScenario').inputValue(), '');
        await page.locator('#files').setInputFiles([referenceA, referenceB]);
        const multipart = await preview(page);
        assert.deepEqual(multipart.body['image[]'].map(file => file.name), ['reference-a.png', 'reference-b.png']);
        await page.locator('#imageMode').selectOption('reference');
        assert.ok((await scenarios()).includes('multi-compose'));
        await page.locator('#promptScenario').selectOption('multi-compose');
        const compositionPrompt = await scenarioPrompt(page, 'image', 'multi-compose');
        assert.equal(await page.locator('#prompt').inputValue(), compositionPrompt);
        await page.locator('#imageMode').selectOption('generation');
        assert.equal(await page.locator('#promptScenario').inputValue(), '', 'a scenario outside the current mode becomes custom without overwriting text');
        assert.equal(await page.locator('#parkedFiles').isVisible(), true);
        assert.match(await page.locator('#parkedFiles').innerText(), /已暂存 2 个附件/);
        const generation = await preview(page);
        assert.equal(generation.body.image, undefined, 'parked reference images are not sent to text-to-image');
        await page.locator('[data-kind="video"]').click();
        await page.locator('[data-kind="image"]').click();
        assert.equal(await page.locator('#imageMode').inputValue(), 'generation');
        assert.equal(await page.locator('#prompt').inputValue(), compositionPrompt);
        await page.locator('#imageMode').selectOption('reference');
        assert.equal(await page.locator('#promptScenario').inputValue(), 'multi-compose');
        assert.equal(await page.locator('#inputPreview img').count(), 2, 'parked image draft returns intact');
        await page.locator('#preset').selectOption('gemini-image');
        await page.locator('#model').fill('workflow-image');
        assert.equal(await page.locator('#imageMode').inputValue(), 'reference');
        const gemini = await preview(page);
        assert.deepEqual(gemini.body.contents[0].parts.slice(1).map(part => part.inlineData.data), [referenceA.buffer.toString('base64'), referenceB.buffer.toString('base64')]);
        await page.locator('#imageMode').selectOption('generation');
        assert.equal(await page.locator('#preset').inputValue(), 'gemini-image', 'task switches retain explicitly chosen Gemini protocol');
        assert.equal((await preview(page)).body.contents[0].parts.length, 1);
        await page.locator('#imageMode').selectOption('reference');
        await page.locator('#clearFilesBtn').click();
        assert.equal(await page.locator('#inputPreview img').count(), 0);
        assert.match(await page.locator('#scenarioRequirement').innerText(), /至少上传 2 张/);
        assert.equal(await page.locator('#model').inputValue(), 'workflow-image');
        assert.equal(await page.locator('#prompt').inputValue(), compositionPrompt);
        assert.equal(calls.length, 0);
        checks.push('mode-specific scenarios, parked files, multipart/Gemini ordering, draft recovery and attachment-only clearing');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser); const { page, calls } = fixture;
      try {
        await page.locator('[data-kind="audio"]').click();
        await page.locator('#prompt').fill('');
        await page.locator('#model').fill('custom-audio-model');
        assert.equal(await page.locator('#prompt').inputValue(), '');
        await page.locator('#preset').selectOption('openai-transcription');
        assert.equal(await page.locator('#prompt').inputValue(), '');
        await page.locator('#promptScenario').selectOption('meeting');
        const prompt = await page.locator('#prompt').inputValue();
        assert.equal(prompt, await scenarioPrompt(page, 'transcription', 'meeting'));
        await page.locator('#preset').selectOption('openai-speech');
        await page.locator('#model').fill('custom-tts');
        assert.equal(await page.locator('#prompt').inputValue(), prompt);
        await page.locator('[data-kind="text"]').click();
        await page.locator('#files').setInputFiles(referenceA);
        await page.locator('#files').setInputFiles(referenceB);
        assert.equal(await page.locator('#inputPreview img').count(), 2, 'text vision references also append');
        assert.equal(calls.length, 0);
        checks.push('empty/custom audio prompts survive model/protocol edits and text vision references append');
      } finally { await fixture.close(); }
    }

    {
      const fixture = await setup(browser); const { page } = fixture;
      try {
        const out = path.join(__dirname, 'qa-workflows'); await fs.mkdir(out, { recursive: true });
        await page.screenshot({ path: path.join(out, 'desktop-text.png'), fullPage: true });
        await page.locator('[data-kind="image"]').click();
        await page.screenshot({ path: path.join(out, 'desktop-generation.png'), fullPage: true });
        await page.locator('#imageMode').selectOption('edit');
        await page.locator('#files').setInputFiles([referenceA, referenceB]);
        await page.screenshot({ path: path.join(out, 'desktop-edit.png'), fullPage: true });
        await page.locator('#imageMode').selectOption('reference');
        await page.screenshot({ path: path.join(out, 'desktop-reference.png'), fullPage: true });
        await page.locator('[data-kind="video"]').click();
        await page.locator('.choice-picker').filter({ has: page.locator('#resolution') }).locator('.choice-toggle').click();
        await page.screenshot({ path: path.join(out, 'desktop-video-choices.png'), fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator('[data-kind="text"]').click();
        await page.screenshot({ path: path.join(out, 'mobile-text.png'), fullPage: true });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile layout fits viewport');
        await page.locator('[data-kind="image"]').click();
        await page.screenshot({ path: path.join(out, 'mobile-reference.png'), fullPage: true });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        checks.push('desktop/mobile screenshots and no horizontal overflow');
      } finally { await fixture.close(); }
    }

    assert.deepEqual(unexpected, [], 'all provider traffic is mocked');
    assert.deepEqual(errors, [], 'no browser runtime errors');
    for (const check of checks) console.log('PASS: ' + check);
    console.log(`PASS: ${checks.length} workflow UI groups; ${requestCount} intercepted HTTP requests; no real network`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
