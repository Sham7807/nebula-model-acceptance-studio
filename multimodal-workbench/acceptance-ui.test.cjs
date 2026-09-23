'use strict';
// Offline acceptance UI regressions: source assets and every local API response are mocked.
// No upstream channel or live verifier is contacted.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const origin = 'http://127.0.0.1:8877';
const artifact = path.resolve(__dirname, '../中转站测试工具-多模态版.html');
const output = path.join(__dirname, 'qa-acceptance');
const token = 'mock-local-session-token';
const errors = [], unexpected = [], passed = [];
let count = 0;
const reportJson = JSON.stringify({ suite: 'ccmax', summary: { passed: 2, failed: 1, skipped: 0, inconclusive: 1 }, cases: [{ id: 'sse-1', status: 'failed', detail: 'Missing message_stop' }] });
const reportHtml = '<!doctype html><html lang="zh-CN"><title>离线验收报告</title><h1>离线验收报告</h1><p>Missing message_stop</p></html>';
// Valid empty ZIP archive: tests download bytes only; the server suite checks evidence contents.
const evidenceZip = Buffer.from('UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==', 'base64');

function running(suite, total = suite === 'kvv11' ? 11 : suite === 'kvvfull' ? 611 : 11) {
  return { id: 'aabb1122', suite, status: 'running', base: 'https://relay.test/v1', model: 'restored-' + suite, completed: 0, total, elapsed: 0, request_count: 0, events: [] };
}
function result(suite, status = 'cancelled') {
  const common = { verdict: { status: status === 'cancelled' ? 'inconclusive' : 'failed', label: status === 'cancelled' ? '检测未完成' : '未满足验收要求', detail: '离线样本中发现未通过项，详见逐项证据。' }, transport: { request_count: 7, checks: [] } };
  if (suite === 'ccmax') return { ...common, suite: 'ccmax_acceptance', status, summary: { passed: 2, failed: 1, skipped: 0, inconclusive: 1 }, checks: [
    { id: 'signature', label: '无效签名探针', status: 'passed', samples: 2, details: [{ sample_id: 'signature-1', status: 'passed', detail: 'Rejected invalid signature' }] },
    { id: 'message-stop', label: 'message_stop 完整收尾', status: 'failed', samples: 1, details: [{ sample_id: 'sse-1', status: 'failed', detail: 'Missing message_stop' }] },
    { id: 'network', label: '签名探针', status: 'inconclusive', samples: 1, details: [{ sample_id: 'signature-2', status: 'inconclusive', detail: 'Mocked timeout' }] },
  ], samples: [{ id: 'sse-1', probe: 'sse', status: 'failed', issues: ['Missing message_stop'] }] };
  return { ...common, suite, status, summary: { passed: 2, failed: 1, skipped: 0, inconclusive: 1 }, cases: [
    { id: 'request-ok', label: '正常请求', status: 'passed' },
    { id: 'sse-1', label: 'SSE 收尾', status: 'failed', detail: 'Missing message_stop' },
    { id: 'network', label: '签名探针', status: 'inconclusive', detail: 'Mocked timeout' },
  ], log: 'Offline fixture: completed evidence preserved.' };
}
async function fixture(browser, options = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
  page.setDefaultTimeout(10000);
  await page.addInitScript(() => {
    window.__deepResizeMessages = 0;
    window.addEventListener('message', event => {
      if (event.data?.type === 'workbench:deep-resize') window.__deepResizeMessages++;
    });
  });
  page.on('pageerror', error => errors.push(error.message));
  const restored = options.active || options.latest;
  const restoredJob = restored ? running(restored, options.total) : null;
  if (options.latest) Object.assign(restoredJob, { status: 'completed', completed: restoredJob.total, elapsed: 90, result: result(restored, 'completed') });
  if (options.configuration && restoredJob?.result) restoredJob.result.configuration = options.configuration;
  const state = { sessionHold: !!options.holdSession, sessionPending: [], sessionOffline: !!options.offline, sessionNoToken: !!options.noToken, kvvRevision: Object.hasOwn(options, 'kvvRevision') ? options.kvvRevision : '66092cf', pollFailuresRemaining: 0, job: restoredJob, calls: [], posts: [], planPosts: [], modelPosts: [], modelReplies: [], modelPending: [], modelHold: false, models: ['ccmax', 'kimi-k3', 'manual-compatible'], cancel: 0, session: 0 };
  const previousJobs = new Map();
  let notifyRestore;const restoreStarted = new Promise(resolve => { notifyRestore = resolve; });
  state.restoreHold = !!options.holdRestore;state.restorePending = [];state.runReads = 0;
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (!/^https?:/.test(url.protocol)) return route.continue();
    count++;
    if (url.origin !== origin) { unexpected.push(req.url()); return route.abort('blockedbyclient'); }
    const send = data => route.fulfill({ headers: { 'content-type': 'application/json' }, body: JSON.stringify(data) });
    if (url.pathname.startsWith('/api/')) {
      const call = { path: url.pathname, method: req.method(), headers: req.headers(), body: req.postData() };
      state.calls.push(call);
      if (url.pathname === '/api/session') {
        state.session++;
        if (state.sessionHold) await new Promise(resolve => state.sessionPending.push(resolve));
        if (state.sessionOffline) return route.abort('connectionrefused');
        return send({ token: state.sessionNoToken ? '' : token, active: state.job?.status === 'running' ? state.job.id : null, latest: state.job?.id || null, kvv_revision: state.kvvRevision, ready: true });
      }
      assert.equal(req.headers()['x-workbench-token'], token, 'API call carries only local session token');
      if (url.pathname === '/api/claude/plan') {
        assert.equal(req.method(),'POST');const body=JSON.parse(req.postData());state.planPosts.push(body);
        assert.equal(Object.hasOwn(body,'key'),false,'request plan never submits API key');
        assert.doesNotMatch(req.postData(),/mock-channel-key/);
        return send({suite:'claude',request_count:6,request_count_is_maximum:true,conditional_requests:1,token_estimate:{cache_prefix_target_tokens:6000,cache_requests:4,cache_total_target_input_tokens:24000,basis:'实际消耗以上游 usage 为准。'},limitations:['来源标签不是官方身份证明。','长前缀 token 为估算，以实测 usage 为准。'],requests:[
          {id:'baseline',module:'protocol',title:'基础 Messages 基线',method:'POST',url:'https://relay.test/v1/messages',body:{model:body.model,max_tokens:32,messages:[{role:'user',content:'<script>window.__previewExecuted=true</script>'}]},notes:'保留响应结构。'},
          {id:'cache-warm',module:'cache',title:'长前缀缓存预热',method:'POST',url:'https://relay.test/v1/messages',repeat:3,conditional:true,body:{model:body.model,system:[{type:'text',text:'prefix '.repeat(300),cache_control:{type:'ephemeral'}}],max_tokens:16,messages:[{role:'user',content:'返回 OK'}]}}
        ]});
      }
      if (url.pathname === '/api/models') {
        assert.equal(req.method(), 'POST');state.modelPosts.push(JSON.parse(req.postData()));
        const reply = state.modelReplies.shift() || { status: 200, data: { models: state.models, total: state.models.length } };
        if (state.modelHold) await new Promise(resolve => state.modelPending.push(resolve));
        return route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.data) });
      }
      if (url.pathname === '/api/runs' && req.method() === 'POST') {
        const body = JSON.parse(req.postData());state.posts.push(body);
        if (state.job) previousJobs.set(state.job.id, state.job);
        state.job = running(body.suite, body.suite === 'ccmax' ? (body.request_format === 'openai' ? 0 : body.signature_samples) + body.sse_samples + 7 : undefined);
        if (options.uniqueRunIds) state.job.id = 'fixture-run-' + state.posts.length;
        return send({ id: state.job.id });
      }
      const runPath = url.pathname.match(/^\/api\/runs\/([^/]+)(?:\/(.+))?$/);
      const routeJob = runPath ? (runPath[1] === state.job?.id ? state.job : previousJobs.get(runPath[1])) : null;
      if (routeJob && runPath[2] === 'cancel') {
        assert.equal(req.method(), 'POST');state.cancel++;
        state.job = { ...state.job, status: 'cancelled', result: result(state.job.suite) };
        return send({ ok: true });
      }
      if (routeJob && runPath[2] === 'report.json') return route.fulfill({ contentType: 'application/json', body: reportJson });
      if (routeJob && runPath[2] === 'report.html') return route.fulfill({ contentType: 'text/html', body: reportHtml });
      if (routeJob && runPath[2] === 'evidence.zip') return route.fulfill({ contentType: 'application/zip', body: evidenceZip });
      if (routeJob && !runPath[2]) {
        state.runReads++;
        if (state.restoreHold && state.runReads === 1) await new Promise(resolve => { state.restorePending.push(resolve);notifyRestore(); });
        if (state.pollFailuresRemaining > 0) { state.pollFailuresRemaining--;return route.abort('connectionrefused'); }
        return send(routeJob);
      }
      unexpected.push(req.url());return route.fulfill({ status: 404, body: '{}' });
    }
    const asset = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(1));
    if (!/^[a-z0-9.-]+$/i.test(asset)) { unexpected.push(req.url()); return route.abort(); }
    const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' }[path.extname(asset)];
    try { return route.fulfill({ contentType: mime || 'application/octet-stream', body: await fs.readFile(path.join(__dirname, asset)) }); }
    catch { unexpected.push(req.url());return route.fulfill({ status: 404, body: 'Not found' }); }
  });
  await page.goto(options.file ? pathToFileURL(artifact).href : origin + '/');
  if (!options.file && !options.holdSession) {
    if (options.offline || options.noToken) await page.waitForFunction(() => document.getElementById('acceptanceService').textContent === '本地验收服务未连接');
    else await page.waitForFunction(() => document.getElementById('acceptanceService').textContent.includes('已连接'));
  }
  if (options.holdRestore) await restoreStarted;
  return { page, state, close: () => page.close() };
}
async function suite(page, name) {
  if (!(await page.locator('#legacyView').isVisible())) await page.locator('#legacyBtn').click();
  await page.locator(`[data-suite="${name}"]`).click();
}
async function assertSuiteIdentity(page, name, status = 'connected', revision = '66092cf') {
  const cc = name === 'ccmax';
  assert.equal(await page.locator('#acceptanceTitle').innerText(), cc ? 'CCMax渠道验收' : 'Kimi Vendor Verifier');
  const description = await page.locator('#acceptanceDescription').innerText();
  const footnote = await page.locator('#acceptanceFootnote').innerText();
  if (cc) {
    assert.match(description, /独立的 Anthropic Messages 检测器/);
    assert.doesNotMatch(description + footnote, /Claude|KVV/);
    assert.equal(await page.locator('[data-suite="ccmax"]').innerText(), 'CCMax 验收');
  } else {
    assert.match(description, /网页.*自动调用已集成.*官方 KVV/);
    assert.match(description, /无需另开项目/);
    assert.match(footnote, /官方/);
    assert.match(footnote, /证据|本地检查/);
  }
  const expected = status === 'file' ? '需要本地验收服务' : status === 'connecting' ? '正在连接本地验收服务' : status === 'offline' ? '本地验收服务未连接' : cc ? '本地验收服务已连接' : '本地服务已连接 · ' + (revision ? '集成 KVV ' + revision : 'KVV 版本未提供');
  assert.equal(await page.locator('#acceptanceService').innerText(), expected);
  if (!cc && status === 'connected') assert.match(await page.locator('#acceptanceService').getAttribute('title'), /仅表示网页已连接本地服务.*任务日志/);
}
async function fill(page, model = 'fixture-model') {
  await page.locator('#acceptanceBase').fill('https://relay.test/v1');
  await page.locator('#acceptanceKey').fill('mock-channel-key');
  await page.locator('#acceptanceModel').fill(model);
}
async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'page has no horizontal overflow');
}
async function waitModelIdle(page) {
  await page.waitForFunction(() => !document.getElementById('acceptanceModels').disabled && !document.getElementById('acceptanceModelHint').textContent.includes('正在获取'));
}
async function screenshot(page, filename) { await page.mouse.move(0, 0); await page.screenshot({ path: path.join(output, filename), fullPage: true, animations: 'disabled' }); }
async function download(page, format, bytes) {
  const pending = page.waitForEvent('download');
  await page.locator(`[data-acceptance-download="${format}"]`).click();
  const value = await pending;
  assert.match(value.suggestedFilename(), /^测试报告-[^/\\:]+-\d{8}-\d{6}(?:-证据)?\.(?:html|json|zip)$/);
  const file = path.join(output, value.suggestedFilename());await value.saveAs(file);
  assert.deepEqual(await fs.readFile(file), Buffer.from(bytes));
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
  try {
    {
      const f = await fixture(browser, { file: true }); const { page, state } = f;
      try {
        await suite(page, 'kimi');
        assert.equal(await page.locator('#acceptanceLocalHelp').isVisible(), true);
        assert.match(await page.locator('#acceptanceLocalHelp').innerText(), /启动验收工作台.command/);
        assert.equal(await page.locator('#acceptanceService').innerText(), '需要本地验收服务');
        await assertSuiteIdentity(page, 'kimi', 'file');
        await suite(page, 'ccmax');await assertSuiteIdentity(page, 'ccmax', 'file');
        await suite(page, 'kimi');await assertSuiteIdentity(page, 'kimi', 'file');
        assert.equal(await page.locator('#acceptanceRun').isDisabled(), true);
        assert.equal(await page.locator('#acceptancePlan li').count(), 11);
        await screenshot(page, 'file-mode-local-help.png');
        await page.locator('[data-suite="general"]').click();
        await page.frameLocator('#legacyFrame').locator('#btnRun').waitFor();
        assert.equal(await page.locator('#legacyFrame').isVisible(), true);
        await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 300)));
        const resizeStart = await page.evaluate(() => window.__deepResizeMessages);
        await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 500)));
        assert.ok((await page.evaluate(() => window.__deepResizeMessages)) - resizeStart < 10,
          'deep iframe resize messages settle instead of triggering an exponentially growing feedback loop');
        await page.locator('#backBtn').click();
        assert.equal(await page.locator('#basicView').isVisible(), true);
        assert.equal(state.calls.length, 0, 'file mode never calls local API automatically');
        passed.push('standalone file mode explains local launcher, disables acceptance and preserves general/basic navigation');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await page.locator('#base').fill('https://relay.test/v1');
        await page.locator('#key').fill('mock-channel-key');
        await page.locator('#model').fill('fixture-model');
        await suite(page, 'kimi');
        assert.equal(await page.locator('#acceptanceBase').inputValue(), 'https://relay.test/v1');
        assert.equal(await page.locator('#acceptanceKey').inputValue(), 'mock-channel-key');
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'fixture-model');
        assert.equal(await page.locator('#acceptancePlan li').count(), 11);
        await page.locator('#acceptanceScope').selectOption('kvvfull');
        assert.equal(await page.locator('#acceptancePlan li').count(), 4);
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /数百次/);
        await screenshot(page, 'desktop-kvv-full.png');
        await page.locator('[data-suite="ccmax"]').click();
        assert.equal(await page.locator('#acceptancePlan li').count(), 12);
        assert.equal(await page.locator('#acceptanceKimiFields').isVisible(), false);
        assert.equal(await page.locator('#acceptanceCcFields').isVisible(), true);
        assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'anthropic');
        await page.locator('#acceptanceAuth').selectOption('bearer');
        assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'bearer');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 11 次/);
        await page.locator('#acceptanceSampling').selectOption('batch');
        assert.equal(await page.locator('#acceptanceSignature').inputValue(), '5');
        assert.equal(await page.locator('#acceptanceSse').inputValue(), '50');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 62 次/);
        await page.locator('#acceptanceSignature').fill('2');await page.locator('#acceptanceSse').fill('7');
        assert.equal(await page.locator('#acceptanceSampling').inputValue(), 'custom');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 16 次/);
        assert.equal(state.posts.length, 0, 'changing plans does not start billable work');
        await screenshot(page, 'desktop-ccmax-plan.png');
        await page.setViewportSize({ width: 390, height: 844 });
        await screenshot(page, 'mobile-ccmax-plan.png');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        passed.push('deep tabs, adopted connection, 11/full plans, CC quick/batch/custom sampling and mobile fit');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await page.locator('#base').fill('https://relay.test/v1');
        await page.locator('#key').fill('mock-channel-key');
        await page.locator('#model').fill('claude-compatible');
        const tabs = await page.locator('.deep-suite-tabs [data-suite]').evaluateAll(nodes => nodes.map(node => node.dataset.suite));
        assert.deepEqual(tabs.slice(0, 5), ['general', 'ccmax', 'claude', 'kimi', 'gpt'], 'Claude tab sits between CCMax and KVV');
        await suite(page, 'claude');
        assert.equal(await page.locator('#acceptanceTitle').innerText(), 'Claude 上游专项验收');
        assert.equal(await page.locator('#acceptanceCcFields').isVisible(), false);
        assert.equal(await page.locator('#acceptanceClaudeFields').isVisible(), true);
        assert.equal(await page.locator('#acceptanceKimiFields').isVisible(), false);
        assert.equal(await page.locator('#claudeProvider').inputValue(), 'auto');
        assert.equal(await page.locator('#claudeFormat').inputValue(), 'anthropic');
        assert.equal(await page.locator('#claudeAuth').inputValue(), 'anthropic');
        assert.equal(await page.locator('#acceptancePlan li').count(), 12);
        assert.match(await page.locator('#acceptancePlan').innerText(), /注入|缓存|压测|签名|透传/);
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /大 token|专业方案/);
        await page.locator('#claudeSampling').selectOption('stress');
        assert.equal(await page.locator('#claudeCacheTokens').inputValue(), '12000');
        assert.equal(await page.locator('#claudeStressRequests').inputValue(), '100');
        assert.equal(await page.locator('#claudeStressConcurrency').inputValue(), '10');
        await page.locator('#claudeFormat').selectOption('openai');
        assert.equal(await page.locator('#claudeAuth').inputValue(), 'bearer');
        assert.equal(await page.locator('#claudeAuth').isDisabled(), true);
        assert.equal(await page.locator('#claudeSignatureGroup').isVisible(), false);
        assert.match(await page.locator('#acceptancePlan').innerText(), /Claude/);
        assert.equal(state.posts.length, 0, 'Claude plan changes do not start billable work');
        await page.locator('#claudeProvider').selectOption('aws');
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        assert.equal(state.modelPosts[0].auth, 'bearer');
        await page.locator('#acceptanceModelMenu').getByRole('button', { name: '清空选择', exact: true }).click();
        await page.locator('#acceptanceModelList label').filter({ hasText: 'ccmax' }).locator('input').check();
        await page.locator('#acceptanceModelList label').filter({ hasText: 'kimi-k3' }).locator('input').check();
        await page.locator('#acceptanceTitle').click();
        await page.setViewportSize({ width: 390, height: 844 });
        await noOverflow(page);
        await screenshot(page, 'mobile-claude-plan.png');
        assert.equal(await page.locator('#claudeStressConcurrency').getAttribute('max'),'20');
        await page.locator('#claudePlanPreview').click();
        await page.locator('#claudePlanDialog').waitFor({state:'visible'});
        assert.equal(state.posts.length,0,'preview makes no billable test submission');
        assert.equal(state.planPosts.length,1);
        assert.equal(state.planPosts[0].request_format,'openai');
        assert.equal(state.planPosts[0].suite,'claude');
        assert.equal(state.planPosts[0].provider,'aws');
        assert.equal(state.planPosts[0].sampling,'stress');
        assert.deepEqual(state.planPosts[0].models,['ccmax','kimi-k3']);
        assert.match(await page.locator('#claudePlanDialog').innerText(),/仅预览.*不向上游/);
        assert.match(await page.locator('#claudePlanSummary').innerText(),/请求数上限.*6.*24000/s);
        assert.match(await page.locator('#claudePlanSummary').innerText(),/包含 1 个条件请求/);
        assert.match(await page.locator('.claude-plan-request').last().innerText(),/条件请求/);
        assert.match(await page.locator('#claudePlanSummary').innerText(),/已选 2 个模型/);
        assert.equal(await page.locator('.claude-plan-module').count(),2);
        await page.locator('.claude-plan-request').first().locator('summary').click();
        assert.match(await page.locator('.claude-plan-request pre').first().innerText(),/<script>/);
        assert.equal(await page.evaluate(()=>window.__previewExecuted),undefined,'preview JSON is text, never HTML');
        assert.equal(await page.locator('#claudePlanDialog script').count(),0);
        assert.equal(await page.locator('#claudePlanDialog').evaluate(node=>node.scrollWidth<=node.clientWidth),true,'mobile request dialog has no horizontal overflow');
        await screenshot(page,'mobile-claude-request-preview.png');
        await page.locator('#claudePlanClose').click();
        assert.equal(await page.locator('#claudePlanDialog').isVisible(),false);
        await page.locator('#claudePlanPreview').click();
        await page.locator('#claudePlanDialog').waitFor({state:'visible'});
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#claudePlanDialog').isVisible(),false,'preview closes with Escape');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('Claude 专项 · 运行中'));
        assert.equal(state.posts.length, 1);
        const request=state.posts[0];
        for(const [key,value] of Object.entries({suite:'claude',provider:'aws',request_format:'openai',auth:'bearer',cache_tokens:12000,stress_requests:100,stress_concurrency:10,concurrency:10,sampling:'stress'}))assert.equal(request[key],value);
        assert.deepEqual(request.models,['ccmax','kimi-k3']);
        assert.deepEqual(request.enabled_modules,['protocol','auth_signature','tools','max_tokens','injection','identity','cache','stress']);
        assert.equal(await page.locator('[data-suite="kimi"]').isDisabled(),true);
        state.job={...state.job,batch:true,models:['ccmax','kimi-k3'],completed:7,total:42,request_count:7,events:undefined,current_run_snapshot:{model:'kimi-k3',status:'running',completed:2,total:20,events:[{type:'progress',phase:'sample_complete',message:'缓存第二次命中',case:{id:'cache-hit',title:'缓存命中',status:'passed',expected:'read tokens > 0',detail:'观察到缓存读取'}}]}};
        await page.waitForFunction(()=>document.getElementById('acceptanceCases').textContent.includes('kimi-k3 · 缓存命中'));
        assert.equal(await page.locator('#acceptanceCount').innerText(),'7 / 42','batch progress remains parent aggregate');
        assert.match(await page.locator('#acceptanceSummary').innerText(),/实际 API 请求 7 次.*当前模型：kimi-k3/);
        assert.match(await page.locator('#acceptanceLog').textContent(),/\[kimi-k3\] 缓存第二次命中/);
        await suite(page,'general');await suite(page,'claude');
        assert.equal(await page.locator('.acceptance-module-card input:enabled').count(),0,'rebuilding active plan keeps modules locked');
        state.job={...state.job,status:'completed',completed:11,result:{...result('claude','completed'),suite:'claude_acceptance'}};
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('Claude 专项 · 测试已完成'));
        assert.equal(await page.locator('#claudeAuth').isDisabled(),true);
        await download(page,'report.html',reportHtml);
        await suite(page,'ccmax');assert.equal(await page.locator('[data-acceptance-download="report.html"]').isDisabled(),true);
        await suite(page,'claude');assert.equal(await page.locator('[data-acceptance-download="report.html"]').isEnabled(),true);
        passed.push('Claude tab/order, provider/auth/native/OpenAI controls, model discovery/batch submission, result routing/downloads, professional plan and mobile fit');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await suite(page, 'ccmax');await fill(page, 'claude-compatible');
        await page.locator('#acceptanceFormat').selectOption('openai');
        assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'bearer');
        assert.equal(await page.locator('#acceptanceAuth').isDisabled(), true);
        assert.equal(await page.locator('#acceptanceSignatureGroup').isVisible(), false);
        assert.equal(await page.locator('#acceptancePlan li').count(), 11);
        assert.match(await page.locator('#acceptancePlan h3').innerText(), /OpenAI 兼容/);
        assert.match(await page.locator('#acceptancePlan').innerText(), /finish_reason.*\[DONE\]/);
        assert.doesNotMatch(await page.locator('#acceptancePlan ol').innerText(), /thinking 签名|message_start|message_stop/);
        assert.match(await page.locator('#acceptanceCcFormatHelp').innerText(), /\/v1\/chat\/completions.*Bearer.*不发送请求、不计入评分/);
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 10 次/);
        await page.locator('#acceptanceSampling').selectOption('batch');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 57 次/);
        await page.locator('#acceptanceFormat').selectOption('anthropic');
        assert.equal(await page.locator('#acceptanceAuth').isEnabled(), true);
        assert.equal(await page.locator('#acceptanceSignatureGroup').isVisible(), true);
        assert.equal(await page.locator('#acceptancePlan li').count(), 12);
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 62 次/);
        assert.match(await page.locator('#acceptanceCcFormatHelp').innerText(), /\/v1\/messages.*Bearer 仅改变鉴权/);
        await page.locator('#acceptanceAuth').selectOption('anthropic');
        await page.locator('#acceptanceFormat').selectOption('openai');
        await page.locator('#acceptanceSse').fill('7');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 14 次/);
        assert.equal(state.posts.length, 0, 'format and sampling selection never starts billable work');
        await screenshot(page, 'desktop-ccmax-openai-plan.png');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 14');
        assert.equal(state.posts.length, 1);
        assert.equal(state.posts[0].suite, 'ccmax');
        assert.equal(state.posts[0].request_format, 'openai');
        assert.equal(state.posts[0].auth, 'bearer');
        assert.equal(state.posts[0].sse_samples, 7);
        assert.equal(await page.locator('#acceptanceFormat').isDisabled(), true);
        const completed = result('ccmax', 'completed');
        completed.checks = [{ id: 'signature', title: '无效 thinking 签名', status: 'skipped', applicable: false, samples: 0, skip_reason: 'OpenAI 兼容协议不适用，不发送签名请求。' }];
        state.job = { ...state.job, status: 'completed', completed: 14, result: completed };
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        const signature = page.locator('.acceptance-case').filter({ hasText: '无效 thinking 签名' });
        assert.match(await signature.locator('b').innerText(), /不适用/);
        assert.doesNotMatch(await signature.locator('b').innerText(), /通过/);
        await signature.locator('summary').click();
        assert.match(await signature.innerText(), /0 样本.*不发送签名请求/s);
        assert.equal(await page.locator('#acceptanceAuth').isDisabled(), true, 'completed OpenAI run keeps Bearer locked');
        await page.setViewportSize({ width: 390, height: 844 });await noOverflow(page);
        await screenshot(page, 'mobile-ccmax-openai-result.png');
        passed.push('CCMax OpenAI selection locks Bearer, omits signature requests, restores native plan and submits compatible format with explicit non-applicable evidence');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await suite(page, 'kimi');await fill(page, 'kimi-compatible');
        await page.locator('#acceptanceThinkMode').selectOption('openai');
        assert.match(await page.locator('#acceptancePlan h3').innerText(), /11 项 OpenAI 兼容预检/);
        assert.equal(await page.locator('#acceptancePlan li').count(), 11);
        for (const item of ['强制工具调用', 'max_tokens=1 限制', '内置样例图片识别', '重复前缀缓存观测', 'Token 计数一致性']) {
          assert.match(await page.locator('#acceptancePlan ol').innerText(), new RegExp(item));
        }
        assert.doesNotMatch(await page.locator('#acceptancePlan ol').innerText(), /Dynamic tools|Prompt Tokens · 基础/);
        assert.match(await page.locator('#acceptanceFormatHelp').innerText(), /固定 Kimi token 基准不参与兼容评分/);
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 11 个兼容测试项/);
        await page.locator('#acceptanceScope').selectOption('kvvfull');
        assert.match(await page.locator('#acceptancePlan h3').innerText(), /OpenAI 全范围兼容验证/);
        assert.deepEqual(await page.locator('#acceptancePlan li span').allTextContents(), [
          '参数与协议 · 标准兼容断言', '工具与 JSON Schema · 兼容矩阵',
          '多模态与能力扩展 · 独立记录支持情况', 'Token / usage / 缓存 · 计数一致性',
        ]);
        assert.match(await page.locator('#acceptanceFootnote').innerText(), /不冒充官方原生验证通过/);
        await screenshot(page, 'desktop-kvv-openai-full-plan.png');
        await page.locator('#acceptanceScope').selectOption('kvv11');
        assert.equal(state.posts.length, 0);
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        assert.equal(state.posts.length, 1);
        for (const [key, value] of Object.entries({ suite: 'kvv11', think_mode: 'openai', request_format: 'openai', thinking: false, auth: 'bearer' })) assert.equal(state.posts[0][key], value);
        state.job = { ...state.job, status: 'completed', completed: 11, result: result('kvv11', 'completed') };
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        await page.locator('#acceptanceScope').selectOption('kvvfull');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('KVV 全套验证 · 运行中'));
        assert.equal(state.posts.length, 2);
        for (const [key, value] of Object.entries({ suite: 'kvvfull', think_mode: 'openai', request_format: 'openai', thinking: false, auth: 'bearer' })) assert.equal(state.posts[1][key], value);
        assert.equal(await page.locator('#acceptancePlan li').count(), 4);
        passed.push('KVV OpenAI quick/full plans cover 11 compatibility checks and four full layers; both submissions disable native thinking and use Bearer');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await suite(page, 'ccmax');await fill(page);
        await page.locator('#acceptanceAuth').selectOption('bearer');
        await page.locator('#acceptanceSignature').fill('2');await page.locator('#acceptanceSse').fill('7');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 16');
        assert.equal(state.posts.length, 1);assert.equal(state.posts[0].suite, 'ccmax');
        assert.equal(state.posts[0].auth, 'bearer');
        assert.equal(state.posts[0].request_format, 'anthropic', 'Bearer selection alone retains native Messages format');
        assert.equal(state.posts[0].signature_samples, 2);assert.equal(state.posts[0].sse_samples, 7);
        assert.match(await page.locator('#acceptanceEta').innerText(), /等待足够样本/);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 0 次/);
        assert.equal(await page.locator('#acceptanceVerdict').isVisible(), false);
        assert.equal(await page.locator('#acceptanceModel').isDisabled(), true);
        assert.equal(await page.locator('[data-suite="kimi"]').isDisabled(), true);
        await page.locator('[data-suite="general"]').click();
        assert.equal(await page.locator('#legacyFrame').isVisible(), true);
        assert.equal(await page.locator('[data-suite="ccmax"]').isEnabled(), true, 'active suite remains reachable after opening general tests');
        await page.locator('[data-suite="ccmax"]').click();
        assert.equal(await page.locator('#acceptancePanel').isVisible(), true);
        state.job = { ...state.job, completed: 4, elapsed: 20, request_count: 5, events: [{ type: 'progress', suite: 'ccmax_acceptance', phase: 'sample_complete', sample_id: 'sse-1', status: 'failed', completed: 4, total: 16, active: 1, message: 'sse-1：failed' }] };
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '4 / 16');
        assert.match(await page.locator('#acceptanceEta').innerText(), /估计剩余约 1 分 0 秒/);
        assert.equal(await page.locator('.acceptance-case.failed').count(), 1);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 5 次/);
        await screenshot(page, 'desktop-ccmax-progress.png');
        await page.locator('#acceptanceStop').click();
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已取消'));
        assert.equal(state.cancel, 1);assert.equal(state.posts.length, 1);
        assert.equal(await page.locator('#acceptanceRun').isEnabled(), true);
        assert.equal(await page.locator('#acceptanceStop').isDisabled(), true);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /通过 2.*未通过 1.*无法判定 1.*实际 API 请求 7 次/);
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /检测未完成/);
        assert.match(await page.locator('#acceptanceVerdict').getAttribute('class'), /inconclusive/);
        const failedDetail = page.locator('.acceptance-case.failed details');
        assert.equal(await failedDetail.count(), 1, 'real CC checks.details retain per-sample diagnosis');
        await failedDetail.locator('summary').click();
        assert.match(await failedDetail.innerText(), /Missing message_stop/);
        await download(page, 'report.html', reportHtml);
        await download(page, 'report.json', reportJson);
        await download(page, 'evidence.zip', evidenceZip);
        assert.equal(page.url(), origin + '/', 'downloads do not navigate the workbench');
        await screenshot(page, 'desktop-ccmax-result.png');
        passed.push('mock CC submission, honest ETA, active-suite navigation, cancellation, retained evidence and 3 actual downloads');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser); const { page, state } = f;
      try {
        await suite(page, 'kimi');await fill(page, 'kimi-k3');
        await page.locator('#acceptanceThinkMode').selectOption('opensource');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        assert.equal(state.posts[0].suite, 'kvv11');assert.equal(state.posts[0].think_mode, 'opensource');
        assert.equal(state.posts[0].request_format, 'native');assert.equal(state.posts[0].thinking, true);
        state.job = { ...state.job, status: 'completed', completed: 11, elapsed: 55, result: result('kvv11', 'completed') };
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator('#acceptanceBar').getAttribute('value'), '100');
        assert.match(await page.locator('#acceptanceEta').innerText(), /已结束/);
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /未满足验收要求/);
        assert.match(await page.locator('#acceptanceVerdict').getAttribute('class'), /failed/);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 7 次/);
        await page.locator('#acceptanceScope').selectOption('kvvfull');
        await page.locator('#acceptanceThinkMode').selectOption('none');
        await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 611');
        assert.equal(await page.locator('#acceptanceVerdict').isVisible(), false, 'starting a new run clears previous verdict');
        assert.equal(state.posts.length, 2);assert.equal(state.posts[1].suite, 'kvvfull');assert.equal(state.posts[1].thinking, false);
        state.job = { ...state.job, status: 'completed', completed: 611, elapsed: 360, result: result('kvvfull', 'completed') };
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '611 / 611');
        await page.setViewportSize({ width: 390, height: 844 });
        await screenshot(page, 'mobile-kvv-result.png');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        passed.push('11/full request selection, thinking mappings, 100% completion and mobile result layout');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { active: 'kvvfull' }); const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 611');
        assert.equal(await page.locator('#legacyView').isVisible(), true);
        assert.equal(await page.locator('#acceptancePanel').isVisible(), true);
        assert.equal(await page.locator('[data-suite="kimi"]').getAttribute('aria-selected'), 'true');
        await assertSuiteIdentity(page, 'kimi');
        assert.equal(await page.locator('#acceptanceScope').inputValue(), 'kvvfull', 'reconnected job restores actual scope');
        assert.equal(await page.locator('#acceptancePlan li').count(), 4);
        assert.equal(state.posts.length, 0, 'reconnect reads state and never resubmits');
        await screenshot(page, 'desktop-reconnected-full.png');
        passed.push('session reconnect restores active full-verifier progress/scope without resubmitting');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser);const { page, state } = f;
      try {
        await suite(page, 'kimi');
        await page.locator('#acceptanceModels').click();
        assert.match(await page.locator('#acceptanceMessage').innerText(), /渠道地址和 API Key/);
        assert.equal(state.modelPosts.length, 0, 'empty connection never requests models');
        await fill(page, 'keep-my-manual-model');
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        assert.deepEqual(state.modelPosts[0], { base: 'https://relay.test/v1', key: 'mock-channel-key', auth: 'bearer' });
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'keep-my-manual-model', 'discovery does not replace current model');
        assert.match(await page.locator('#acceptanceModelHint').innerText(), /已获取 3 个模型/);
        const picker = page.locator('#acceptanceModelPicker');
        assert.equal(await picker.getByRole('checkbox').count(), 3);
        await picker.getByRole('checkbox', { name: 'kimi-k3', exact: true }).click();
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'kimi-k3');
        await picker.getByRole('checkbox', { name: 'ccmax', exact: true }).check();
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'ccmax');
        await picker.getByRole('checkbox', { name: 'kimi-k3', exact: true }).uncheck();
        await page.locator('#acceptanceModel').fill('unlisted/custom-model');
        await page.locator('#acceptanceModel').press('Escape');
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'unlisted/custom-model');
        await suite(page, 'ccmax');await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        assert.equal(state.modelPosts.at(-1).auth, 'anthropic');
        await page.locator('#acceptanceAuth').selectOption('bearer');
        assert.equal(await picker.getByRole('checkbox').count(), 0, 'auth change clears previous catalog');
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);assert.equal(state.modelPosts.at(-1).auth, 'bearer');
        await page.locator('#acceptanceModel').press('Escape');
        await page.setViewportSize({ width: 390, height: 844 });await page.locator('#acceptanceModelToggle').click();await noOverflow(page);
        await screenshot(page, 'mobile-acceptance-model-picker.png');
        await page.locator('#acceptanceModel').press('Escape');
        await page.locator('#acceptanceRun').click();await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        assert.equal(state.posts.length, 1);assert.equal(state.posts[0].model, 'unlisted/custom-model');
        assert.equal(await page.locator('#acceptanceModelToggle').isDisabled(), true);
        assert.equal(await page.locator('#acceptanceModels').isDisabled(), true);
        passed.push('model discovery auth, repeat selection, custom unlisted ID, mobile picker and active controls');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser);const { page, state } = f;
      try {
        await suite(page, 'kimi');await fill(page, 'preserved-manual-id');
        state.modelReplies.push({ status: 502, data: { error: '模拟：上游模型列表不可用' } });
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        assert.match(await page.locator('#acceptanceMessage').innerText(), /上游模型列表不可用/);
        assert.match(await page.locator('#acceptanceModelHint').innerText(), /仍可手动填写/);
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'preserved-manual-id');
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        assert.equal(await page.locator('#acceptanceMessage').isVisible(), false, 'successful retry clears error');
        state.modelHold = true;
        state.modelReplies.push({ status: 200, data: { models: ['stale-model'] } });
        const heldRequest = page.waitForRequest(r => r.url().endsWith('/api/models'));
        await page.locator('#acceptanceModels').click();await heldRequest;
        await page.waitForFunction(() => document.getElementById('acceptanceModels').disabled);
        assert.equal(state.modelPending.length, 1);
        const cancelledRequest = page.waitForEvent('requestfailed', { predicate: r => r.url().endsWith('/api/models') });
        await page.locator('#acceptanceBase').fill('https://new-relay.test/v1');
        assert.match((await cancelledRequest).failure().errorText, /ABORT|CANCEL/i);
        assert.equal(await page.locator('#acceptanceModels').isEnabled(), true);
        state.modelHold = false;state.models = ['new-model-a', 'new-model-b'];
        await page.locator('#acceptanceModels').click();await waitModelIdle(page);
        const picker = page.locator('#acceptanceModelPicker');
        assert.equal(await picker.getByRole('checkbox', { name: 'new-model-a' }).count(), 1);
        state.modelPending.shift()();await page.evaluate(() => new Promise(requestAnimationFrame));
        assert.equal(await picker.getByRole('checkbox', { name: 'stale-model' }).count(), 0, 'late old-channel response cannot replace the new list');
        assert.equal(await picker.getByRole('checkbox', { name: 'new-model-b' }).count(), 1);
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'preserved-manual-id');
        assert.equal(state.posts.length, 0, 'discovery never starts a test job');
        passed.push('model discovery error recovery and stale-response protection across channel changes');
      } finally { for (const resolve of state.modelPending) resolve();await f.close(); }
    }
    for (const restoredSuite of ['ccmax', 'kvv11', 'kvvfull']) {
      const f = await fixture(browser, { latest: restoredSuite });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator('#legacyView').isVisible(), true);
        assert.equal(await page.locator('#acceptancePanel').isVisible(), true);
        assert.equal(await page.locator('#legacyFrame').isVisible(), false);
        await assertSuiteIdentity(page, restoredSuite === 'ccmax' ? 'ccmax' : 'kimi');
        assert.equal(await page.locator('#acceptanceBase').inputValue(), 'https://relay.test/v1');
        assert.equal(await page.locator('#acceptanceModel').inputValue(), 'restored-' + restoredSuite);
        assert.equal(await page.locator('#acceptanceKey').inputValue(), '', 'completed job restore never restores a key');
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /未满足验收要求/);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 7 次/);
        assert.equal(await page.locator('[data-acceptance-download="report.html"]').isEnabled(), true);
        if (restoredSuite !== 'ccmax') assert.equal(await page.locator('#acceptanceScope').inputValue(), restoredSuite);
        await page.reload();await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        await assertSuiteIdentity(page, restoredSuite === 'ccmax' ? 'ccmax' : 'kimi');
        assert.equal(state.session, 4, 'acceptance and history each read the session after both page loads');assert.equal(state.posts.length, 0, 'refresh restores completed evidence without resubmission');
        await screenshot(page, 'desktop-latest-' + restoredSuite + '.png');
      } finally { await f.close(); }
    }
    passed.push('session.latest restores completed CCmax, KVV 11 and full results across refresh without keys or resubmission');
    {
      const f = await fixture(browser, { latest: 'kvvfull' });const { page, state } = f;
      try {
        state.job.result.verdict = { status: 'passed', label: '符合所测契约', detail: '离线用例通过，仅覆盖本次范围。' };
        state.job.result.transport = { request_count: 613, checks: [{ id: 'http-errors', status: 'failed', label: '上游 HTTP 异常', details: ['502 from mock channel'] }, { id: 'all-ok', status: 'passed', label: '不重复展示的通过项' }] };
        await page.reload();await page.waitForFunction(() => document.getElementById('acceptanceVerdict').textContent.includes('符合所测契约'));
        assert.match(await page.locator('#acceptanceVerdict').getAttribute('class'), /passed/);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 613 次/);
        assert.equal(await page.locator('.acceptance-case').filter({ hasText: '上游 HTTP 异常' }).count(), 1);
        assert.equal(await page.locator('.acceptance-case').filter({ hasText: '不重复展示的通过项' }).count(), 0);
        await suite(page, 'general');const frame = page.frameLocator('#legacyFrame');await frame.locator('#btnRun').waitFor();
        await page.waitForFunction(() => parseFloat(document.querySelector('#legacyFrame').style.height) > 900);
        const desktopHeight = (await page.locator('#legacyFrame').boundingBox()).height;
        assert.equal(await frame.locator('body').evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1), true);
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForFunction(() => parseFloat(document.querySelector('#legacyFrame').style.height) > 1800);
        await noOverflow(page);assert.equal(await frame.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await suite(page, 'kimi');assert.equal(await page.locator('#legacyFrame').isVisible(), false);assert.equal(await page.locator('#acceptanceVerdict').isVisible(), true);
        await page.setViewportSize({ width: 1440, height: 1050 });await suite(page, 'general');
        await page.waitForFunction(height => Math.abs(parseFloat(document.querySelector('#legacyFrame').style.height)-height)<2, desktopHeight);
        assert.equal((await page.locator('#legacyFrame').boundingBox()).height, desktopHeight, 'iframe shrinks back after responsive/suite transitions');
        await suite(page, 'kimi');assert.equal(state.posts.length, 0);await screenshot(page, 'desktop-transport-verdict.png');
        passed.push('verdict states, transport request evidence and legacy autoheight across responsive suite switching');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { active: 'kvv11' });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        state.job = { ...state.job, completed: 2, request_count: 3, events: [
          { type: 'progress', case: { id: 'tests/k3_features/test_json.py::case_a', label: '同一个 KVV 节点', status: 'passed', detail: 'call passed' } },
          { type: 'progress', case: { id: 'tests/k3_features/test_json.py::case_b', label: '另一个 KVV 节点', status: 'passed' } },
          { type: 'progress', case: { id: 'tests/k3_features/test_json.py::case_a', label: '同一个 KVV 节点', status: 'failed', detail: 'teardown failed: latest evidence' } },
        ] };
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '2 / 11');
        assert.equal(await page.locator('.acceptance-case').count(), 2, 'call + teardown produce one row per KVV node');
        const updated = page.locator('.acceptance-case').filter({ hasText: '同一个 KVV 节点' });
        assert.equal(await updated.count(), 1);assert.match(await updated.getAttribute('class'), /failed/);
        await updated.locator('summary').click();assert.match(await updated.innerText(), /teardown failed: latest evidence/);
        assert.doesNotMatch(await page.locator('#acceptanceCases').innerText(), /call passed/);
        assert.match(await page.locator('#acceptanceSummary').innerText(), /实际 API 请求 3 次/);
        state.job.events.push({ type: 'progress', case: { id: 'tests/k3_features/test_json.py::case_a', label: '同一个 KVV 节点', status: 'inconclusive', detail: 'latest transport classification' } });
        await page.waitForFunction(() => document.getElementById('acceptanceCases').textContent.includes('无法判定'));
        assert.equal(await page.locator('.acceptance-case').count(), 2);
        assert.equal(await page.locator('.acceptance-case.inconclusive').count(), 1);
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { active: 'ccmax' });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        state.job = { ...state.job, completed: 3, events: [
          { type: 'progress', phase: 'sample_complete', sample_id: 'sse-1', status: 'passed' },
          { type: 'progress', sample: { id: 'tool-1', status: 'passed', detail: 'tool evidence' } },
          { type: 'progress', sample_id: 'sse-2', sample: { status: 'passed', detail: 'second sample' } },
          { type: 'progress', phase: 'sample_complete', sample_id: 'sse-1', status: 'failed' },
        ] };
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '3 / 11');
        assert.equal(await page.locator('.acceptance-case').count(), 3);
        assert.equal(await page.locator('.acceptance-case.failed').filter({ hasText: 'sse-1' }).count(), 1);
        assert.equal(await page.locator('.acceptance-case.passed').filter({ hasText: 'tool-1' }).count(), 1);
        assert.equal(await page.locator('.acceptance-case.passed').filter({ hasText: 'sse-2' }).count(), 1, 'outer sample ID is retained');
      } finally { await f.close(); }
    }
    passed.push('incremental KVV call/teardown and CC sample events deduplicate by ID with latest status/evidence');
    for(const plan of [
      {saved:{sampling:'quick',signature_samples:1,sse_samples:3,cache_tokens:12000,stress_requests:1,stress_concurrency:1},expected:'quick'},
      {saved:{sampling:'stress',signature_samples:2,sse_samples:10,cache_tokens:12000,stress_requests:100,stress_concurrency:10},expected:'stress'},
      {saved:{signature_samples:3,sse_samples:5,cache_tokens:12000,stress_requests:20,concurrency:4},expected:'professional'},
      {saved:{signature_samples:7,sse_samples:9,cache_tokens:22000,stress_requests:13,stress_concurrency:6},expected:'custom'}
    ]){
      const f=await fixture(browser,{latest:'claude',configuration:{provider:'aws',request_format:'anthropic',...plan.saved}});const {page,state}=f;
      try{
        await page.waitForFunction(()=>document.getElementById('acceptanceStage').textContent.includes('Claude 专项 · 测试已完成'));
        assert.equal(await page.locator('#claudeSampling').inputValue(),plan.expected);
        assert.equal(await page.locator('#claudeCacheTokens').inputValue(),String(plan.saved.cache_tokens));
        assert.equal(await page.locator('#claudeStressConcurrency').inputValue(),String(plan.saved.stress_concurrency??plan.saved.concurrency));
        for(const id of ['claudeSignature','claudeSse','claudeCacheTokens','claudeStressRequests','claudeStressConcurrency']){
          await page.locator('#claudeSampling').selectOption('professional');
          await page.locator('#'+id).fill('8');
          assert.equal(await page.locator('#claudeSampling').inputValue(),'custom',id+' edits mark custom plan');
        }
        assert.equal(state.posts.length,0,'restoring or editing plans does not submit upstream work');
      }finally{await f.close();}
    }
    passed.push('Claude saved sampling restores quick/stress and legacy inferred/custom plans; numeric edits mark custom');
    for (const plan of [
      { signature: 5, sse: 50, mode: 'batch', total: 62 },
      { signature: 1, sse: 3, mode: 'quick', total: 11 },
      { signature: 2, sse: 7, mode: 'custom', total: 16 },
    ]) {
      const configuration = { signature_samples: plan.signature, sse_samples: plan.sse, timeout: 85, auth: 'bearer', think_mode: 'opensource', key: 'never-restore-this-fixture-key', unrelated: 'ignored' };
      const f = await fixture(browser, { latest: 'ccmax', total: plan.total, configuration });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator('#acceptanceSignature').inputValue(), String(plan.signature));
        assert.equal(await page.locator('#acceptanceSse').inputValue(), String(plan.sse));
        assert.equal(await page.locator('#acceptanceSampling').inputValue(), plan.mode);
        assert.equal(await page.locator('#acceptanceTimeout').inputValue(), '85');
        assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'bearer');
        assert.equal(await page.locator('#acceptanceThinkMode').inputValue(), 'opensource');
        assert.equal(await page.locator('#acceptanceKey').inputValue(), '', 'only allowlisted non-sensitive fields are restored');
        assert.match(await page.locator('#acceptanceRequestHint').innerText(), new RegExp('计划 ' + plan.total + ' 次'));
        assert.equal(state.posts.length, 0);
        if (plan.mode === 'batch') await screenshot(page, 'desktop-restored-batch-configuration.png');
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { latest: 'kvvfull', configuration: { timeout: 210, think_mode: 'none' } });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator('#acceptanceThinkMode').inputValue(), 'none');
        assert.equal(await page.locator('#acceptanceTimeout').inputValue(), '210');
        assert.equal(await page.locator('#acceptanceScope').inputValue(), 'kvvfull');
        assert.equal(state.posts.length, 0);
      } finally { await f.close(); }
    }
    for (const restoredSuite of ['ccmax', 'kvv11', 'kvvfull']) {
      const cc = restoredSuite === 'ccmax';
      const configuration = { request_format: 'openai', auth: 'bearer', timeout: 95, ...(cc ? { signature_samples: 5, sse_samples: 50 } : { think_mode: 'openai', thinking: false }), key: 'never-restore-this-fixture-key' };
      const f = await fixture(browser, { latest: restoredSuite, ...(cc ? { total: 57 } : {}), configuration });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator('#acceptanceTimeout').inputValue(), '95');
        assert.equal(await page.locator('#acceptanceKey').inputValue(), '', 'OpenAI configuration restoration excludes channel keys');
        if (cc) {
          assert.equal(await page.locator('#acceptanceFormat').inputValue(), 'openai');
          assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'bearer');
          assert.equal(await page.locator('#acceptanceAuth').isDisabled(), true);
          assert.equal(await page.locator('#acceptanceSignatureGroup').isVisible(), false);
          assert.equal(await page.locator('#acceptanceSampling').inputValue(), 'batch');
          assert.equal(await page.locator('#acceptancePlan li').count(), 11);
          assert.match(await page.locator('#acceptanceRequestHint').innerText(), /计划 57 次/);
        } else {
          assert.equal(await page.locator('#acceptanceThinkMode').inputValue(), 'openai');
          assert.equal(await page.locator('#acceptanceScope').inputValue(), restoredSuite);
          assert.equal(await page.locator('#acceptancePlan li').count(), restoredSuite === 'kvv11' ? 11 : 4);
          assert.match(await page.locator('#acceptancePlan h3').innerText(), /OpenAI/);
        }
        await page.reload();
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        assert.equal(await page.locator(cc ? '#acceptanceFormat' : '#acceptanceThinkMode').inputValue(), 'openai');
        assert.equal(await page.locator('#acceptanceKey').inputValue(), '');
        assert.equal(state.posts.length, 0, 'restoring compatible reports never starts an upstream test');
      } finally { await f.close(); }
    }
    passed.push('completed CCMax and KVV quick/full reports restore OpenAI format, compatible plans and non-sensitive configuration across reload');
    {
      const f = await fixture(browser, { active: 'ccmax', total: 57 });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 57');
        assert.equal(state.job.result, undefined);
        assert.equal(await page.locator('#acceptanceSignature').inputValue(), '1');
        assert.equal(await page.locator('#acceptanceSse').inputValue(), '3');
        assert.equal(await page.locator('#acceptanceSampling').inputValue(), 'quick');
        assert.equal(await page.locator('#acceptanceTimeout').inputValue(), '120');
        assert.equal(await page.locator('#acceptanceAuth').inputValue(), 'anthropic');
        assert.equal(await page.locator('#acceptanceThinkMode').inputValue(), 'kimi');
        assert.equal(state.posts.length, 0, 'no configuration is inferred from an active job total');
      } finally { await f.close(); }
    }
    passed.push('completed reports restore actual quick/batch/custom sampling and allowed configuration without secrets; active unknown settings are not inferred');
    {
      const f = await fixture(browser);try {
        assert.match(await f.page.locator('.local-badge').innerText(), /v1\.11/);
      } finally { await f.close(); }
    }
    for (const selectedBeforeConnection of ['ccmax', 'kimi']) {
      const f = await fixture(browser, { holdSession: true });const { page, state } = f;
      try {
        await suite(page, selectedBeforeConnection === 'ccmax' ? 'kimi' : 'ccmax');
        await assertSuiteIdentity(page, selectedBeforeConnection === 'ccmax' ? 'kimi' : 'ccmax', 'connecting');
        await suite(page, selectedBeforeConnection);await assertSuiteIdentity(page, selectedBeforeConnection, 'connecting');
        assert.equal(await page.locator('#acceptanceRun').isDisabled(), true);
        assert.equal(state.sessionPending.length, 2, 'acceptance and history independently check the initial session');
        state.sessionHold = false;state.sessionPending.shift()();
        await page.waitForFunction(() => document.getElementById('acceptanceService').textContent.includes('已连接'));
        await assertSuiteIdentity(page, selectedBeforeConnection);
        for (const next of ['ccmax', 'kimi', 'ccmax', 'kimi']) { await suite(page, next);await assertSuiteIdentity(page, next); }
        await suite(page, 'general');assert.equal(await page.locator('#acceptancePanel').isVisible(), false);
        await suite(page, 'ccmax');await assertSuiteIdentity(page, 'ccmax');
        assert.equal(state.posts.length, 0);assert.equal(state.session, 2, 'suite switching does not create another service connection or run');
        await screenshot(page, 'desktop-ccmax-service-badge.png');
        await suite(page, 'kimi');await screenshot(page, 'desktop-kimi-service-badge.png');
      } finally { for (const resolve of state.sessionPending) resolve();await f.close(); }
    }
    passed.push('suite-specific CCMax title/service badge and Kimi integration explanation survive delayed first connection and repeated tab switching');
    for (const options of [{ offline: true }, { noToken: true }]) {
      const f = await fixture(browser, options);const { page, state } = f;
      try {
        for (const next of ['ccmax', 'kimi', 'ccmax']) {
          await suite(page, next);await assertSuiteIdentity(page, next, 'offline');
          assert.equal(await page.locator('#acceptanceRun').isDisabled(), true);
          assert.equal(await page.locator('#acceptanceLocalHelp').isVisible(), true);
        }
        assert.equal(state.posts.length, 0);
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { kvvRevision: null });const { page, state } = f;
      try {
        await suite(page, 'kimi');await assertSuiteIdentity(page, 'kimi', 'connected', null);
        assert.doesNotMatch(await page.locator('#acceptanceService').innerText(), /undefined|null|已加载/);
        await suite(page, 'ccmax');await assertSuiteIdentity(page, 'ccmax');assert.equal(state.posts.length, 0);
      } finally { await f.close(); }
    }
    passed.push('offline and invalid-session states stay disabled for both suites; missing KVV version is explicit');
    {
      const f = await fixture(browser, { active: 'ccmax' });const { page, state } = f;
      try {
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        await assertSuiteIdentity(page, 'ccmax');
        assert.match(await page.locator('#acceptanceStage').innerText(), /^CCMax ·/);
        state.pollFailuresRemaining = 1;
        await page.waitForFunction(() => document.getElementById('acceptanceService').textContent === '本地验收服务未连接');
        await assertSuiteIdentity(page, 'ccmax', 'offline');
        assert.match(await page.locator('#acceptanceMessage').innerText(), /状态获取失败/);
        await page.waitForFunction(() => document.getElementById('acceptanceService').textContent === '本地验收服务已连接');
        await assertSuiteIdentity(page, 'ccmax');
        assert.equal(state.posts.length, 0);assert.equal(await page.locator('#acceptanceModel').isDisabled(), true);
      } finally { await f.close(); }
    }
    passed.push('running CCMax uses the exact name and updates service badge after mocked disconnect/reconnect without resubmitting');
    for (const restoredSuite of ['ccmax', 'kvvfull']) {
      const f = await fixture(browser, { latest: restoredSuite });const { page, state } = f;
      try {
        const owner = restoredSuite === 'ccmax' ? 'ccmax' : 'kimi', other = owner === 'ccmax' ? 'kimi' : 'ccmax';
        await page.waitForFunction(() => document.getElementById('acceptanceStage').textContent.includes('已完成'));
        await suite(page, other);
        assert.equal(await page.locator('#acceptanceProgress').isVisible(), false, 'another suite has no borrowed progress/result');
        assert.equal(await page.locator('#acceptanceStage').textContent(), '');
        assert.equal(await page.locator('#acceptanceSummary').textContent(), '');
        assert.equal(await page.locator('#acceptanceCases').textContent(), '');
        assert.equal(await page.locator('#acceptanceLog').textContent(), '');
        for (const format of ['report.html', 'report.json', 'evidence.zip']) assert.equal(await page.locator('[data-acceptance-download="' + format + '"]').isDisabled(), true);
        await suite(page, owner);
        assert.equal(await page.locator('#acceptanceProgress').isVisible(), true);
        assert.match(await page.locator('#acceptanceStage').innerText(), owner === 'ccmax' ? /^CCMax/ : /^KVV 全套/);
        await download(page, 'report.json', reportJson);
        assert.equal(state.calls.filter(x => x.path === '/api/runs/aabb1122').length >= 2, true, 'latest restoration retains run ID for the subsequent poll');
        assert.equal(state.posts.length, 0);
      } finally { await f.close(); }
    }
    {
      const f = await fixture(browser, { uniqueRunIds: true });const { page, state } = f;
      try {
        await suite(page, 'ccmax');await fill(page, 'fixture-ccmax');await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        const ccId = state.job.id;
        const ccResult = result('ccmax', 'completed');ccResult.log = 'CCMax cached evidence';ccResult.verdict.label = 'CCMax 专属报告';
        state.job = { ...state.job, status: 'completed', completed: 11, result: ccResult };
        await page.waitForFunction(() => document.getElementById('acceptanceVerdict').textContent.includes('CCMax 专属报告'));
        await suite(page, 'kimi');assert.equal(await page.locator('#acceptanceProgress').isVisible(), false);
        assert.equal(await page.locator('[data-acceptance-download="report.json"]').isDisabled(), true);
        await page.locator('#acceptanceModel').fill('fixture-kimi-k3');await page.locator('#acceptanceRun').click();
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '0 / 11');
        const kimiId = state.job.id;assert.notEqual(kimiId, ccId);
        await suite(page, 'general');assert.equal(await page.locator('#legacyFrame').isVisible(), true);
        const progressed = page.waitForResponse(async response => response.url().endsWith('/api/runs/' + kimiId) && (await response.json()).completed === 3);
        state.job = { ...state.job, completed: 3, elapsed: 15 };
        await progressed;await suite(page, 'kimi');
        await page.waitForFunction(() => document.getElementById('acceptanceCount').textContent === '3 / 11');
        assert.equal(await page.locator('[data-suite="ccmax"]').isDisabled(), true, 'cached completed CC result does not unlock switching while Kimi runs');
        await suite(page, 'general');
        const kimiResult = result('kvv11', 'completed');kimiResult.log = 'Kimi cached evidence';kimiResult.verdict.label = 'Kimi 专属报告';
        state.job = { ...state.job, status: 'completed', completed: 11, result: kimiResult };
        await page.waitForFunction(() => !document.querySelector('[data-suite="ccmax"]').disabled);
        await suite(page, 'ccmax');
        assert.match(await page.locator('#acceptanceStage').innerText(), /^CCMax.*已完成/);
        assert.equal(await page.locator('#acceptanceCount').innerText(), '11 / 11');
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /CCMax 专属报告/);
        assert.equal(await page.locator('#acceptanceLog').textContent(), 'CCMax cached evidence');
        await download(page, 'report.json', reportJson);
        assert.equal(state.calls.filter(x => x.path.endsWith('/report.json')).at(-1).path, '/api/runs/' + ccId + '/report.json', 'CC download uses its cached report ID, not latest Kimi run ID');
        await suite(page, 'kimi');
        assert.match(await page.locator('#acceptanceStage').innerText(), /^KVV 11 项预检.*已完成/);
        assert.equal(await page.locator('#acceptanceCount').innerText(), '11 / 11');
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /Kimi 专属报告/);
        assert.equal(await page.locator('#acceptanceLog').textContent(), 'Kimi cached evidence');
        await download(page, 'report.html', reportHtml);
        assert.equal(state.calls.filter(x => x.path.endsWith('/report.html')).at(-1).path, '/api/runs/' + kimiId + '/report.html');
        await suite(page, 'ccmax');await suite(page, 'general');await suite(page, 'kimi');
        assert.match(await page.locator('#acceptanceVerdict').innerText(), /Kimi 专属报告/);
        assert.equal(state.posts.length, 2, 'all later navigation restores cache without resubmitting either suite');
      } finally { await f.close(); }
    }
    passed.push('per-suite result/report caches hide unknown results, restore original reports, preserve latest reconnection and monitor active jobs through general view');
    for (const restored of [{ latest: 'ccmax' }, { active: 'kvvfull' }]) {
      const f = await fixture(browser, { ...restored, holdRestore: true });const { page, state } = f;
      try {
        await suite(page, restored.latest ? 'kimi' : 'ccmax');
        assert.equal(await page.locator('#acceptanceRun').isDisabled(), true, 'slow report restoration cannot enable a competing start');
        assert.equal(await page.locator('#acceptanceFields').evaluate(fieldset => fieldset.disabled), true);
        assert.equal(await page.locator('#acceptanceModel').isDisabled(), true);
        await page.locator('#acceptanceRun').dispatchEvent('click');
        assert.equal(state.posts.length, 0, 'start handler also guards restoration');
        state.restoreHold = false;state.restorePending.shift()();
        await page.waitForFunction(() => !!document.getElementById('acceptanceStage').textContent);
        const owner = restored.latest ? 'ccmax' : 'kimi';await assertSuiteIdentity(page, owner);
        await page.waitForFunction(expected => document.getElementById('acceptanceRun').disabled === expected, !!restored.active);
        assert.equal(await page.locator('#acceptanceRun').isDisabled(), !!restored.active);
        assert.equal(await page.locator('#acceptanceProgress').isVisible(), true);
        assert.equal(state.posts.length, 0);
      } finally { for (const resolve of state.restorePending) resolve();await f.close(); }
    }
    passed.push('slow latest/active restoration keeps starts disabled and cannot overwrite a competing newly submitted job');
    assert.deepEqual(unexpected, [], 'every request is local and mocked');
    assert.deepEqual(errors, [], 'no browser runtime errors');
    await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify({ passed: true, groups: passed, requests: count, realApiRequests: 0 }, null, 2));
    passed.forEach(value => console.log('PASS: ' + value));
    console.log(`PASS: ${passed.length} acceptance UI groups; ${count} intercepted local requests; no upstream API`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
