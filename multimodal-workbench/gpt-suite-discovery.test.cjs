'use strict';

// The GPT panel must use the same-origin model-discovery service while the
// workbench is hosted. This prevents channels without browser CORS headers
// from breaking the standalone GPT suite.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(id) {
  const listeners = {};
  return {
    id, value: '', textContent: '', hidden: false, disabled: false,
    children: [], className: '',
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    async dispatch(type) { for (const fn of listeners[type] || []) await fn({ type, target: this }); },
    replaceChildren(...children) { this.children = children; },
  };
}

function harness(list) {
  const ids = ['gptBase','gptKey','gptModel','gptModelList','gptModelHint','gptModels','gptRun','gptStop','gptMessage','gptProgress','gptProgressText','gptSummary','gptDownload','gptResult','gptRaw','gptRawWrap','gptPreviewWrap'];
  const nodes = Object.fromEntries(ids.map(id => [id, element(id)]));
  const calls = [];
  const root = {
    document: {
      getElementById(id) { return nodes[id]; },
      createElement(tag) { const node = element(tag); node.tagName = tag.toUpperCase(); return node; },
    },
    URL, AbortController, setTimeout, clearTimeout,
    location: { protocol: 'https:' },
    ModelDiscovery: { list: async (config, options) => { calls.push({ config, options }); return list(); } },
    MediaEngine: {}, WorkbenchReport: {}, HistoryCapture: {},
  };
  root.window = root;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'gpt-suite.js'), 'utf8'), root, { filename: 'gpt-suite.js' });
  return { nodes, calls };
}

test('GPT model picker uses hosted discovery service and fills the datalist', async () => {
  const h = harness(() => ({ models: [{ id: 'gpt-fixture' }, { id: 'claude-fixture' }, { id: 'gpt-fixture' }] }));
  h.nodes.gptBase.value = 'https://relay.example/v1';
  h.nodes.gptKey.value = 'fixture-secret';
  await h.nodes.gptModels.dispatch('click');
  assert.equal(h.calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].config)), { base: 'https://relay.example/v1', key: 'fixture-secret', auth: 'bearer' });
  assert.equal(h.calls[0].options.timeout, 30);
  assert.equal(h.nodes.gptModelList.children.length, 2);
  assert.deepEqual(h.nodes.gptModelList.children.map(x => x.value), ['gpt-fixture', 'claude-fixture']);
  assert.match(h.nodes.gptModelHint.textContent, /已获取 2 个模型/);
  assert.equal(h.nodes.gptModel.value, 'gpt-fixture');
});

test('changing GPT credentials cancels stale discovery and clears the catalog', async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const ids = ['stale-model'];
  const h = harness(() => pending);
  h.nodes.gptBase.value = 'https://relay.example/v1';
  h.nodes.gptKey.value = 'fixture-secret';
  const request = h.nodes.gptModels.dispatch('click');
  await new Promise(done => setImmediate(done));
  assert.equal(h.nodes.gptModels.disabled, true);
  h.nodes.gptKey.value = 'changed-secret';
  await h.nodes.gptKey.dispatch('input');
  assert.equal(h.nodes.gptModels.disabled, false);
  assert.equal(h.nodes.gptModelList.children.length, 0);
  resolve({ models: ids });
  await request;
  assert.equal(h.nodes.gptModelList.children.length, 0, 'stale result must not overwrite changed credentials');
});
