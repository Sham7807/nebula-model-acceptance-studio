'use strict';

// Offline contract tests. Every request is mocked; no provider credentials are used.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { File } = require('node:buffer');

const ENGINE_PATH = path.join(__dirname, 'engine.js');

class MockFileReader {
  readAsDataURL(blob) {
    blob.arrayBuffer().then(buffer => {
      this.result = `data:${blob.type || 'application/octet-stream'};base64,${Buffer.from(buffer).toString('base64')}`;
      this.onload?.({ target: this });
    }, error => { this.error = error; this.onerror?.(error); });
  }
}

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function harness(responses = [], options = {}) {
  const calls = [];
  const queue = [...responses];
  const events = [];
  const sandbox = {
    window: {}, console, URL, URLSearchParams, Blob, File, FormData, FileReader: MockFileReader,
    Headers, Request, Response, TextDecoder, TextEncoder,
    AbortController, AbortSignal, DOMException,
    atob, btoa, performance, structuredClone,
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async (url, init = {}) => {
      calls.push({ url: String(url), ...init });
      assert.ok(queue.length, `Unexpected fetch: ${init.method || 'GET'} ${url}`);
      const response = queue.shift();
      if (typeof response === 'function') return response(url, init);
      if (response instanceof Error) throw response;
      return response;
    },
    ...options.globals,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(ENGINE_PATH, 'utf8'), sandbox, { filename: ENGINE_PATH });
  assert.ok(sandbox.MediaEngine, 'engine exposes MediaEngine');
  return { engine: sandbox.MediaEngine, calls, events, onEvent: event => events.push(event), queue };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function config(preset, changes = {}) {
  return {
    preset,
    base: 'https://relay.example/v1',
    key: 'test-key-local-only',
    model: 'test-model',
    prompt: 'Say hello.',
    ...(preset === 'custom-video' ? { pollPath: '/v1/videos/{id}' } : {}),
    ...changes,
  };
}

function bodyOf(spec) {
  return typeof spec.body === 'string' ? JSON.parse(spec.body) : spec.body;
}

test('all text, image, video and audio profiles are exposed', () => {
  const { engine } = harness();
  const ids = new Set(engine.presets.map(preset => preset.id));
  for (const id of [
    'openai-chat', 'openai-responses', 'anthropic', 'gemini',
    'openai-image', 'openai-image-edit', 'relay-image-json', 'gemini-image',
    'relay-image-json',
    'openai-video', 'relay-video-json', 'doubao-video', 'custom-video',
    'openai-speech', 'openai-audio-chat', 'gemini-speech', 'openai-transcription', 'openai-translation',
  ]) assert.ok(ids.has(id), `Missing profile: ${id}`);
});

test('display URLs reject scriptable protocols, SVG data and embedded credentials', () => {
  const { engine } = harness();
  for (const url of [
    'javascript:alert(1)', 'file:///etc/passwd',
    'data:text/html,<script>alert(1)</script>',
    'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
    'https://name:password@cdn.example/image.png',
  ]) assert.equal(engine.safeUrl(url), null, url);
  assert.equal(engine.safeUrl('https://cdn.example/image.png'), 'https://cdn.example/image.png');
  assert.equal(engine.safeUrl('data:image/png;base64,aGVsbG8='), 'data:image/png;base64,aGVsbG8=');
});

test('HTTP base URL validation rejects credentials and non-web protocols', async () => {
  const { engine, calls } = harness();
  for (const baseUrl of ['file:///tmp', 'javascript:alert(1)', 'https://user:pass@relay.example/v1']) {
    await assert.rejects(engine.build(config('openai-chat', { base: baseUrl })));
  }
  assert.equal(calls.length, 0);
});

test('OpenAI chat joins /v1 once and builds standard Bearer JSON request', async () => {
  const { engine } = harness();
  const request = await engine.build(config('openai-chat'));
  assert.equal(request.url, 'https://relay.example/v1/chat/completions');
  assert.equal(request.method, 'POST');
  const headers = new Headers(request.headers);
  assert.equal(headers.get('Authorization'), 'Bearer test-key-local-only');
  assert.match(headers.get('Content-Type'), /application\/json/);
  const body = bodyOf(request);
  assert.equal(body.model, 'test-model');
  assert.equal(body.messages.at(-1).content, 'Say hello.');
  assert.notEqual(body.stream, true);
});

test('provider base paths remain intact while duplicate version prefixes are removed', async () => {
  const { engine } = harness();
  for (const [baseUrl, expected] of [
    ['https://relay.example', 'https://relay.example/v1/chat/completions'],
    ['https://relay.example/v1/', 'https://relay.example/v1/chat/completions'],
    ['https://relay.example/proxy', 'https://relay.example/proxy/v1/chat/completions'],
    ['https://relay.example/proxy/v1', 'https://relay.example/proxy/v1/chat/completions'],
  ]) {
    const request = await engine.build(config('openai-chat', { base: baseUrl }));
    assert.equal(request.url, expected);
  }
});

test('Responses, Anthropic, and Gemini use their respective JSON payload and authentication', async () => {
  const { engine } = harness();
  const responses = await engine.build(config('openai-responses'));
  assert.equal(new URL(responses.url).pathname, '/v1/responses');
  assert.equal(bodyOf(responses).input, 'Say hello.');

  const anthropic = await engine.build(config('anthropic'));
  assert.equal(new URL(anthropic.url).pathname, '/v1/messages');
  assert.equal(new Headers(anthropic.headers).get('x-api-key'), 'test-key-local-only');
  assert.ok(new Headers(anthropic.headers).get('anthropic-version'));
  assert.equal(bodyOf(anthropic).messages.at(-1).content, 'Say hello.');
  assert.ok(bodyOf(anthropic).max_tokens > 0);

  const gemini = await engine.build(config('gemini', { base: 'https://relay.example' }));
  assert.match(new URL(gemini.url).pathname, /\/models\/test-model:generateContent$/);
  assert.equal(bodyOf(gemini).contents[0].parts[0].text, 'Say hello.');
  assert.equal(new Headers(gemini.headers).get('x-goog-api-key'), 'test-key-local-only');
});

test('image generation builds a JSON request with the prompt and chosen model', async () => {
  const { engine } = harness();
  const generated = await engine.build(config('openai-image'));
  assert.equal(new URL(generated.url).pathname, '/v1/images/generations');
  assert.equal(bodyOf(generated).prompt, 'Say hello.');
  assert.equal(bodyOf(generated).model, 'test-model');
});

test('relay image JSON accepts one or multiple reference images as data URLs', async () => {
  const { engine } = harness();
  const first = new File([new Uint8Array([137, 80, 78, 71])], 'first.png', { type: 'image/png' });
  const second = new File([new Uint8Array([255, 216, 255, 224])], 'second.jpg', { type: 'image/jpeg' });

  const single = await engine.build(config('relay-image-json', { files: [first] }));
  assert.equal(single.url, 'https://relay.example/v1/images/generations');
  assert.equal(single.method, 'POST');
  assert.equal(new Headers(single.headers).get('Content-Type'), 'application/json');
  assert.equal(bodyOf(single).image, 'data:image/png;base64,iVBORw==');

  const multiple = await engine.build(config('relay-image-json', { files: [first, second] }));
  assert.deepEqual(bodyOf(multiple).image, [
    'data:image/png;base64,iVBORw==',
    'data:image/jpeg;base64,/9j/4A==',
  ]);
});

test('relay image JSON preserves an explicitly supplied image URL when no file is uploaded', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('relay-image-json', {
    extra: { image: 'https://cdn.example/reference.png', quality: 'high' },
  }));
  assert.equal(bodyOf(spec).image, 'https://cdn.example/reference.png');
  assert.equal(bodyOf(spec).quality, 'high');
});

test('relay image JSON accepts validated URL references through referenceUrls', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('relay-image-json', {
    referenceUrls: ['https://cdn.example/a.png', 'https://cdn.example/b.jpg'],
  }));
  assert.deepEqual(bodyOf(spec).image, ['https://cdn.example/a.png', 'https://cdn.example/b.jpg']);
  await assert.rejects(engine.build(config('relay-image-json', { referenceUrls: ['javascript:alert(1)'] })), /公开.*http|有效.*URL/);
  await assert.rejects(engine.build(config('relay-image-json', { referenceUrls: ['https://user:pass@cdn.example/a.png'] })), /公开.*http|账号密码/);
});

test('relay image JSON rejects missing or invalid reference uploads before fetch', async () => {
  const { engine, calls } = harness();
  await assert.rejects(engine.run(config('relay-image-json')), /参考图|图片|上传/);
  await assert.rejects(engine.run(config('relay-image-json', {
    files: [new File(['not an image'], 'notes.txt', { type: 'text/plain' })],
  })), /参考文件|图片|上传/);
  assert.equal(calls.length, 0);
});

test('relay image JSON provider errors are surfaced without a duplicate submission', async () => {
  const { engine, calls } = harness([
    jsonResponse({ error: { message: 'invalid reference image' } }, 400),
  ]);
  const file = new File([new Uint8Array([137, 80, 78, 71])], 'reference.png', { type: 'image/png' });
  await assert.rejects(engine.run(config('relay-image-json', { files: [file] })), /400.*invalid reference image/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].url, 'https://relay.example/v1/images/generations');
  assert.equal(bodyOf(calls[0]).image, 'data:image/png;base64,iVBORw==');
});

test('speech requests preserve text, selected voice, and output format', async () => {
  const { engine } = harness();
  const request = await engine.build(config('openai-speech', { voice: 'nova', format: 'wav' }));
  assert.equal(new URL(request.url).pathname, '/v1/audio/speech');
  assert.equal(bodyOf(request).input, 'Say hello.');
  assert.equal(bodyOf(request).voice, 'nova');
  assert.equal(bodyOf(request).response_format, 'wav');
});

test('file-dependent image and speech-input profiles fail before fetch if no file is supplied', async () => {
  const { engine, calls } = harness();
  for (const preset of ['openai-image-edit', 'openai-transcription', 'openai-translation']) {
    await assert.rejects(engine.build(config(preset)));
  }
  assert.equal(calls.length, 0);
});

test('Gemini inline images are decoded as renderable media alongside text', () => {
  const { engine } = harness();
  const result = engine.extract({
    candidates: [{ content: { parts: [
      { text: 'Generated a sample image.' },
      { inlineData: { mimeType: 'image/png', data: 'iVBORw0KGgo=' } },
    ] } }],
  }, { kind: 'image' });
  assert.match(result.text, /Generated a sample image/);
  assert.equal(result.media.length, 1);
  assert.equal(result.media[0].type || result.media[0].kind, 'image');
  assert.ok(result.media[0].url.startsWith('data:image/png;base64,'));
});

test('malicious media links never become output previews', () => {
  const { engine } = harness();
  const result = engine.extract({ data: [
    { url: 'javascript:alert(1)' },
    { url: 'https://cdn.example/safe.png' },
    { url: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=' },
  ] }, { kind: 'image' });
  assert.equal(result.media.length, 1);
  assert.equal(result.media[0].url, 'https://cdn.example/safe.png');
});

test('failed POSTs are never automatically retried', async () => {
  const { engine, calls } = harness([
    jsonResponse({ error: { message: 'Rate limit reached' } }, 429),
  ]);
  await assert.rejects(engine.run(config('openai-chat')), /429|Rate limit/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'POST');
});

test('network failure causes one generation attempt only', async () => {
  const { engine, calls } = harness([new TypeError('Failed to fetch')]);
  await assert.rejects(engine.run(config('openai-image')));
  assert.equal(calls.length, 1);
});

test('text extraction supports OpenAI, Responses and Anthropic outputs', () => {
  const { engine } = harness();
  for (const [raw, expected] of [
    [{ choices: [{ message: { content: 'chat result' } }] }, 'chat result'],
    [{ output: [{ type: 'message', content: [{ type: 'output_text', text: 'responses result' }] }] }, 'responses result'],
    [{ content: [{ type: 'text', text: 'anthropic result' }] }, 'anthropic result'],
  ]) assert.match(engine.extract(raw, { kind: 'text' }).text, new RegExp(expected));
});

test('single-run plain text response is returned with request metadata', async () => {
  const { engine, calls } = harness([
    jsonResponse({ choices: [{ message: { content: 'Hello from the mock provider.' } }] }),
  ]);
  const result = await engine.run(config('openai-chat'));
  assert.equal(result.status, 'success');
  assert.match(result.text, /Hello from the mock provider/);
  assert.equal(calls.length, 1);
  assert.ok(result.requests.length > 0);
});

test('image-edit multipart carries file bytes and prompt without forcing Content-Type', async () => {
  const { engine } = harness();
  const file = new File([new Uint8Array([137, 80, 78, 71])], 'reference.png', { type: 'image/png' });
  const spec = await engine.build(config('openai-image-edit', { files: [file] }));
  assert.ok(spec.body instanceof FormData);
  assert.equal(new Headers(spec.headers).has('Content-Type'), false);
  assert.equal(spec.body.get('prompt'), 'Say hello.');
  assert.equal(spec.body.get('model'), 'test-model');
  const submitted = spec.body.get('image') || spec.body.get('image[]');
  assert.ok(submitted instanceof Blob);
  assert.deepEqual([...new Uint8Array(await submitted.arrayBuffer())], [137, 80, 78, 71]);
});

test('audio transcription and translation upload the source file as multipart', async () => {
  const { engine } = harness();
  const file = new File(['audio test bytes'], 'speech.wav', { type: 'audio/wav' });
  for (const [preset, endpoint] of [
    ['openai-transcription', '/v1/audio/transcriptions'],
    ['openai-translation', '/v1/audio/translations'],
  ]) {
    const spec = await engine.build(config(preset, { files: [file], language: 'zh' }));
    assert.equal(new URL(spec.url).pathname, endpoint);
    assert.ok(spec.body instanceof FormData);
    assert.equal(new Headers(spec.headers).has('Content-Type'), false);
    assert.equal(spec.body.get('file').name, 'speech.wav');
    assert.equal(await spec.body.get('file').text(), 'audio test bytes');
    assert.equal(spec.body.get('model'), 'test-model');
  }
});

test('custom API paths cannot send channel credentials to another origin', async () => {
  const { engine, calls } = harness();
  for (const path of ['https://evil.example/collect', '//evil.example/collect']) {
    await assert.rejects(engine.build(config('openai-chat', { path })));
  }
  assert.equal(calls.length, 0);
});

test('JSON parameter overrides are applied without corrupting prototypes', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('openai-image', {
    extra: { quality: 'high', n: 2, model: 'overridden-model' },
  }));
  assert.equal(bodyOf(spec).quality, 'high');
  assert.equal(bodyOf(spec).n, 2);
  assert.equal(bodyOf(spec).model, 'overridden-model');
  const tainted = JSON.parse('{"__proto__":{"injected":true}}');
  try { await engine.build(config('openai-chat', { extra: tainted })); } catch (_) { /* rejection is also safe */ }
  assert.equal({}.injected, undefined);
});

test('JSON reference image requests accept one or many remote URLs and custom model bodies', async () => {
  const { engine } = harness();
  const images = ['https://images.example/one.jpg', 'https://images.example/two.jpg'];
  const spec = await engine.build(config('relay-image-json', {
    model: 'gemini-3.1-flash-image-preview',
    prompt: 'blend the references',
    extra: { model: 'gemini-3.1-flash-image-preview', aspect_ratio: '16:9', image: images, response_format: 'url', size: '4k' },
  }));
  assert.equal(new URL(spec.url).pathname, '/v1/images/generations');
  assert.deepEqual(bodyOf(spec), { model: 'gemini-3.1-flash-image-preview', prompt: 'blend the references', aspect_ratio: '16:9', image: images, response_format: 'url', size: '4k' });
});

test('binary speech output becomes a playable object URL', async () => {
  const { engine, calls } = harness([
    new Response(new Uint8Array([73, 68, 51, 4, 0, 0]), { headers: { 'Content-Type': 'audio/mpeg' } }),
  ]);
  const result = await engine.run(config('openai-speech', { voice: 'alloy', format: 'mp3' }));
  assert.equal(result.status, 'success');
  assert.equal(result.media.length, 1);
  assert.equal(result.media[0].type || result.media[0].kind, 'audio');
  assert.match(result.media[0].url, /^blob:/);
  assert.equal(calls.length, 1);
  URL.revokeObjectURL(result.media[0].url);
});

test('a completed video URL is exposed as video media', () => {
  const { engine } = harness();
  const result = engine.extract({ id: 'task-1', status: 'succeeded', content: {
    video_url: 'https://cdn.example/output.mp4',
  } }, { kind: 'video' });
  assert.equal(result.media.length, 1);
  assert.equal(result.media[0].type || result.media[0].kind, 'video');
  assert.equal(result.media[0].url, 'https://cdn.example/output.mp4');
});

test('OpenAI video uploads a multipart generation request', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('openai-video', { duration: 8, size: '1280x720' }));
  assert.equal(new URL(spec.url).pathname, '/v1/videos');
  assert.ok(spec.body instanceof FormData);
  assert.equal(spec.body.get('model'), 'test-model');
  assert.equal(spec.body.get('prompt'), 'Say hello.');
  assert.equal(spec.body.get('seconds'), '8');
  assert.equal(spec.body.get('size'), '1280x720');
  assert.equal(new Headers(spec.headers).has('Content-Type'), false);
});

test('Doubao and custom video profiles use JSON with explicit model and content', async () => {
  const { engine } = harness();
  const doubao = await engine.build(config('doubao-video', { base: 'https://relay.example' }));
  assert.equal(new URL(doubao.url).pathname, '/api/v3/contents/generations/tasks');
  assert.equal(bodyOf(doubao).model, 'test-model');
  assert.ok(bodyOf(doubao).content.some(part => part.type === 'text' && part.text.includes('Say hello.')));
  const custom = await engine.build(config('custom-video', { extra: { ratio: '16:9' } }));
  assert.equal(bodyOf(custom).model, 'test-model');
  assert.equal(bodyOf(custom).ratio, '16:9');
});

test('already-aborted signal prevents generation request from being sent', async () => {
  const { engine, calls } = harness();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(engine.run(config('openai-image'), { signal: controller.signal }),
    error => error.name === 'AbortError' || /中止|停止|abort/i.test(error.message));
  assert.equal(calls.length, 0);
});

test('in-flight request cancellation propagates to fetch and does not retry', async () => {
  let fetchStarted;
  const started = new Promise(resolve => { fetchStarted = resolve; });
  const { engine, calls } = harness([(_url, init) => new Promise((_resolve, reject) => {
    assert.ok(init.signal);
    init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    fetchStarted();
  })]);
  const controller = new AbortController();
  const running = engine.run(config('openai-chat'), { signal: controller.signal });
  await started;
  controller.abort();
  await assert.rejects(running, error => error.name === 'AbortError' || /中止|停止|abort/i.test(error.message));
  assert.equal(calls.length, 1);
});

function virtualClock() {
  let now = Date.now();
  return {
    Date: class FakeDate extends Date {
      constructor(...args) { super(...(args.length ? args : [now])); }
      static now() { return now; }
    },
    performance: { now: () => now },
    setTimeout: (callback, delay = 0, ...args) => setTimeout(() => {
      now += Math.max(0, Number(delay) || 0);
      callback(...args);
    }, 1),
    clearTimeout,
  };
}

test('video task polls from queued to completed without resubmitting generation', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'video-task-1', status: 'queued' }),
    jsonResponse({ id: 'video-task-1', status: 'in_progress', progress: 50 }),
    jsonResponse({ id: 'video-task-1', status: 'completed', video_url: 'https://cdn.example/result.mp4' }),
  ], { globals: virtualClock() });
  const result = await engine.run(config('custom-video', { pollInterval: 1, pollTimeout: 30 }));
  assert.equal(result.status, 'success');
  assert.equal(result.taskId, 'video-task-1');
  assert.equal(result.media[0].url, 'https://cdn.example/result.mp4');
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(calls.filter(call => (call.method || 'GET') === 'GET').length, 2);
  assert.ok(calls.slice(1).every(call => call.url.endsWith('/video-task-1')));
});

test('failed video task exposes provider failure and never downloads content', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'video-task-2', status: 'queued' }),
    jsonResponse({ id: 'video-task-2', status: 'failed', error: { message: 'Video generation rejected' } }),
  ], { globals: virtualClock() });
  await assert.rejects(engine.run(config('custom-video', { pollInterval: 1 })), /Video generation rejected|failed|失败/);
  assert.equal(calls.length, 2);
});

test('resume uses only GET and encodes the supplied task ID as one path component', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'id/with?delimiters', status: 'completed', video_url: 'https://cdn.example/resumed.mp4' }),
  ]);
  const result = await engine.resume(config('custom-video'), 'id/with?delimiters');
  assert.equal(result.status, 'success');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method || 'GET', 'GET');
  assert.match(calls[0].url, /id%2Fwith%3Fdelimiters/);
});

test('polling deadline returns pending with task ID so the user can resume safely', async () => {
  const pending = jsonResponse({ id: 'still-working', status: 'in_progress' });
  const { engine, calls } = harness([
    jsonResponse({ id: 'still-working', status: 'queued' }),
    ...Array.from({ length: 30 }, () => () => pending.clone()),
  ], { globals: virtualClock() });
  const result = await engine.run(config('custom-video', { pollInterval: 5, pollTimeout: 5 }));
  assert.equal(result.status, 'pending');
  assert.equal(result.taskId, 'still-working');
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.ok(calls.length < 20, 'the poll deadline is enforced');
});

test('video completion downloads binary content with channel authentication', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'video-binary', status: 'completed' }),
    new Response(new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112]), {
      headers: { 'Content-Type': 'video/mp4' },
    }),
  ]);
  const result = await engine.resume(config('openai-video'), 'video-binary');
  assert.equal(result.status, 'success');
  assert.equal(result.media[0].type || result.media[0].kind, 'video');
  assert.match(result.media[0].url, /^blob:/);
  assert.equal(calls.length, 2);
  assert.ok(calls[1].url.endsWith('/video-binary/content'));
  assert.equal(new Headers(calls[1].headers).get('Authorization'), 'Bearer test-key-local-only');
  URL.revokeObjectURL(result.media[0].url);
});

test('cross-origin polling and content paths are blocked before credentials can leak', async () => {
  const { engine, calls } = harness();
  await assert.rejects(engine.resume(config('custom-video', {
    pollPath: 'https://evil.example/{id}',
  }), 'video-id'));
  assert.equal(calls.length, 0);
});

test('HTTP success without usable model output is not reported as a successful generation', async () => {
  const { engine, calls } = harness([jsonResponse({ message: 'accepted but no output or task ID' })]);
  const result = await engine.run(config('openai-image'));
  assert.equal(result.status, 'unrecognized');
  assert.equal(result.media.length, 0);
  assert.equal(calls.length, 1);
});

test('model discovery preserves provider model IDs', async () => {
  const { engine, calls } = harness([
    jsonResponse({ data: [{ id: 'gpt-test' }, { id: '国产模型/视频-v1' }] }),
  ]);
  const result = await engine.listModels(config('openai-chat'));
  assert.deepEqual(plain(result.models.map(model => model.id)), ['gpt-test', '国产模型/视频-v1']);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method || 'GET', 'GET');
  assert.equal(calls[0].url, 'https://relay.example/v1/models');
  assert.equal(new Headers(calls[0].headers).get('Authorization'), 'Bearer test-key-local-only');
});

test('completed video with external content endpoint never sends authentication there', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'video-safe', status: 'completed' }),
  ]);
  await assert.rejects(engine.resume(config('openai-video', {
    contentPath: 'https://evil.example/{id}/content',
  }), 'video-safe'));
  assert.ok(calls.every(call => new URL(call.url).origin === 'https://relay.example'));
});

test('request metadata does not expose API credentials', async () => {
  const { engine } = harness([
    jsonResponse({ choices: [{ message: { content: 'OK' } }] }),
  ]);
  const result = await engine.run(config('openai-chat'));
  assert.equal(JSON.stringify(result.requests).includes('test-key-local-only'), false);
});

test('safeUrl accepts engine-owned media blobs but rejects arbitrary object URLs', async () => {
  const { engine } = harness([new Response(new Uint8Array([73, 68, 51]), {
    headers: { 'Content-Type': 'audio/mpeg' },
  })]);
  const result = await engine.run(config('openai-speech'));
  const url = result.media[0].url;
  const arbitrary = URL.createObjectURL(new Blob(['audio'], { type: 'audio/wav' }));
  try {
    assert.equal(engine.safeUrl(url, 'audio'), url);
    assert.equal(engine.safeUrl(arbitrary, 'audio'), null);
  } finally {
    URL.revokeObjectURL(url);
    URL.revokeObjectURL(arbitrary);
  }
});

test('Gemini snake_case inline media payloads are also previewable', () => {
  const { engine } = harness();
  const result = engine.extract({ candidates: [{ content: { parts: [
    { inline_data: { mime_type: 'image/jpeg', data: '/9j/2Q==' } },
  ] } }] }, { kind: 'image' });
  assert.equal(result.media.length, 1);
  assert.match(result.media[0].url, /^data:image\/jpeg;base64,/);
});

test('reference images become native Gemini inlineData parts', async () => {
  const { engine } = harness();
  const file = new File([new Uint8Array([137, 80, 78, 71])], 'reference.png', { type: 'image/png' });
  const spec = await engine.build(config('gemini-image', { files: [file], base: 'https://relay.example' }));
  const parts = bodyOf(spec).contents[0].parts;
  assert.ok(parts.some(part => part.text === 'Say hello.'));
  const image = parts.find(part => part.inlineData || part.inline_data);
  assert.ok(image);
  assert.equal((image.inlineData || image.inline_data).mimeType || (image.inlineData || image.inline_data).mime_type, 'image/png');
  assert.equal((image.inlineData || image.inline_data).data, 'iVBORw==');
});

test('pending video polling can be stopped while retaining the already created task ID', async () => {
  let fetchStarted;
  const started = new Promise(resolve => { fetchStarted = resolve; });
  const { engine, calls } = harness([
    jsonResponse({ id: 'cancel-poll-task', status: 'queued' }),
    (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      fetchStarted();
    }),
  ], { globals: virtualClock() });
  const controller = new AbortController();
  const running = engine.run(config('custom-video', { pollInterval: 1 }), { signal: controller.signal });
  await started;
  controller.abort();
  await assert.rejects(running, error => {
    assert.equal(error.taskId, 'cancel-poll-task');
    return error.name === 'AbortError' || /中止|停止|abort/i.test(error.message);
  });
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});

test('a video thumbnail alone is insufficient for video generation success', async () => {
  const { engine } = harness([
    jsonResponse({ id: 'thumbnail-only', status: 'completed', image_url: 'https://cdn.example/thumbnail.png' }),
  ]);
  const result = await engine.resume(config('custom-video'), 'thumbnail-only');
  assert.equal(result.status, 'unrecognized');
});

test('media generation success requires the requested modality', async () => {
  const { engine } = harness([
    new Response(new Uint8Array([73, 68, 51]), { headers: { 'Content-Type': 'audio/mpeg' } }),
  ]);
  const result = await engine.run(config('openai-image'));
  try { assert.equal(result.status, 'unrecognized'); }
  finally { result.media.forEach(item => URL.revokeObjectURL(item.url)); }
});

test('a text refusal on an image endpoint remains visible without reporting image success', async () => {
  const { engine } = harness([
    jsonResponse({ candidates: [{ content: { parts: [{ text: 'This request cannot be generated.' }] } }] }),
  ]);
  const result = await engine.run(config('gemini-image'));
  assert.equal(result.status, 'unrecognized');
  assert.match(result.text, /cannot be generated/);
});

test('OpenAI video completion still fetches video content when metadata contains a thumbnail', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'thumbnail-plus-content', status: 'completed', image_url: 'https://cdn.example/thumbnail.png' }),
    new Response(new Uint8Array([0, 0, 0, 24]), { headers: { 'Content-Type': 'video/mp4' } }),
  ]);
  const result = await engine.resume(config('openai-video'), 'thumbnail-plus-content');
  try {
    assert.equal(result.status, 'success');
    assert.ok(result.media.some(item => item.kind === 'video'));
    assert.equal(calls.length, 2);
  } finally { result.media.filter(item => item.url.startsWith('blob:')).forEach(item => URL.revokeObjectURL(item.url)); }
});

test('PCM speech is wrapped in a RIFF/WAVE container for browser playback', async () => {
  const { engine } = harness([
    new Response(new Uint8Array([0, 0, 255, 127, 0, 128]), { headers: { 'Content-Type': 'application/octet-stream' } }),
  ]);
  const result = await engine.run(config('openai-speech', { format: 'pcm' }));
  try {
    assert.equal(result.status, 'success');
    assert.equal(result.media[0].mime, 'audio/wav');
    const bytes = new Uint8Array(await (await fetch(result.media[0].url)).arrayBuffer());
    assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), 'RIFF');
    assert.equal(new TextDecoder().decode(bytes.slice(8, 12)), 'WAVE');
    assert.equal(bytes.length, 50);
  } finally { URL.revokeObjectURL(result.media[0].url); }
});

test('credentials are omitted and redirects are rejected on provider fetches', async () => {
  const { engine, calls } = harness([jsonResponse({ choices: [{ message: { content: 'OK' } }] })]);
  await engine.run(config('openai-chat'));
  assert.equal(calls[0].credentials, 'omit');
  assert.equal(calls[0].redirect, 'error');
});

test('credential redaction preserves generation token limits and usage measurements', async () => {
  const { engine } = harness([
    jsonResponse({ choices: [{ message: { content: 'Hello' } }],
      usage: { prompt_tokens: 11, completion_tokens: 6, total_tokens: 17 },
    }),
  ]);
  const c = config('openai-chat', { extra: { max_tokens: 128 } });
  const spec = await engine.build(c);
  assert.equal(spec.preview.max_tokens, 128);
  const result = await engine.run(c);
  assert.equal(result.raw.usage.total_tokens, 17);
  assert.equal(result.raw.usage.prompt_tokens, 11);
  assert.equal(result.raw.usage.completion_tokens, 6);
  assert.equal(result.requests[0].body.max_tokens, 128);
});

test('relay video compatibility builds JSON with string seconds and the channel endpoint', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('relay-video-json', {
    duration: 8, size: '1280x720', model: 'video-model',
  }));
  assert.equal(spec.url, 'https://relay.example/v1/videos');
  assert.equal(spec.method, 'POST');
  assert.equal(typeof spec.body, 'string');
  assert.equal(spec.body instanceof FormData, false);
  assert.equal(new Headers(spec.headers).get('Content-Type'), 'application/json');
  assert.equal(new Headers(spec.headers).get('Authorization'), 'Bearer test-key-local-only');
  assert.deepEqual(bodyOf(spec), {
    model: 'video-model', prompt: 'Say hello.', seconds: '8', size: '1280x720',
  });
  assert.equal(spec.pollUrl, 'https://relay.example/v1/videos/{id}');
});

test('relay JSON video and native OpenAI video remain distinct wire formats', async () => {
  const { engine } = harness();
  const relay = await engine.build(config('relay-video-json', { duration: 4 }));
  const native = await engine.build(config('openai-video', { duration: 4 }));
  assert.equal(typeof relay.body, 'string');
  assert.equal(bodyOf(relay).seconds, '4');
  assert.ok(native.body instanceof FormData);
  assert.equal(native.body.get('seconds'), '4');
  assert.equal(new Headers(native.headers).has('Content-Type'), false);
});

test('relay JSON video polls its default task endpoint and returns playable video output', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'relay-video-task', status: 'queued' }),
    jsonResponse({ id: 'relay-video-task', status: 'in_progress' }),
    jsonResponse({ id: 'relay-video-task', status: 'completed', video_url: 'https://cdn.example/relay-output.mp4' }),
  ], { globals: virtualClock() });
  const result = await engine.run(config('relay-video-json', {
    duration: 6, pollInterval: 1, pollTimeout: 30,
  }));
  assert.equal(result.status, 'success');
  assert.equal(result.taskId, 'relay-video-task');
  assert.equal(result.media[0].kind, 'video');
  assert.equal(result.media[0].url, 'https://cdn.example/relay-output.mp4');
  assert.equal(calls.length, 3);
  assert.equal(calls[0].method, 'POST');
  assert.equal(typeof calls[0].body, 'string');
  assert.equal(JSON.parse(calls[0].body).seconds, '6');
  assert.ok(calls.slice(1).every(call => call.method === 'GET' && call.url === 'https://relay.example/v1/videos/relay-video-task'));
});

test('relay JSON video parsing errors do not trigger format fallbacks or duplicate submissions', async () => {
  const { engine, calls } = harness([
    jsonResponse({ error: { message: 'unmarshal generate request failed: invalid character' } }, 500),
  ]);
  await assert.rejects(engine.run(config('relay-video-json', { duration: 4 })), /500.*unmarshal generate request failed/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].url, 'https://relay.example/v1/videos');
  assert.equal(typeof calls[0].body, 'string');
});

test('relay JSON video does not silently discard selected reference files', async () => {
  const { engine, calls } = harness();
  const file = new File([new Uint8Array([137, 80, 78, 71])], 'reference.png', { type: 'image/png' });
  await assert.rejects(engine.run(config('relay-video-json', { files: [file], duration: 4 })), /图片|文件|上传|参考/);
  assert.equal(calls.length, 0);
});

test('audio chat requests text and audio output through the chat endpoint', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('openai-audio-chat'));
  assert.equal(spec.url, 'https://relay.example/v1/chat/completions');
  assert.equal(spec.method, 'POST');
  assert.equal(new Headers(spec.headers).get('Content-Type'), 'application/json');
  assert.equal(new Headers(spec.headers).get('Authorization'), 'Bearer test-key-local-only');
  const body = bodyOf(spec);
  assert.deepEqual(body.modalities, ['text', 'audio']);
  assert.deepEqual(body.audio, { voice: 'alloy', format: 'wav' });
  assert.equal(body.messages[0].role, 'user');
  assert.equal(body.messages[0].content, 'Say hello.');
});

test('audio chat preserves selected voice and format in its native audio object', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('openai-audio-chat', { voice: 'nova', format: 'mp3' }));
  assert.deepEqual(bodyOf(spec).audio, { voice: 'nova', format: 'mp3' });
  assert.equal(bodyOf(spec).response_format, undefined);
});

test('audio chat sends WAV and MP3 attachments as input_audio base64 parts', async () => {
  const { engine } = harness();
  for (const [name, type, format] of [
    ['voice.mp3', 'audio/mpeg', 'mp3'],
    ['voice.wav', 'audio/wav', 'wav'],
  ]) {
    const file = new File([new Uint8Array([0, 1, 127, 255])], name, { type });
    const spec = await engine.build(config('openai-audio-chat', { files: [file] }));
    assert.equal(typeof spec.body, 'string');
    const parts = bodyOf(spec).messages[0].content;
    assert.ok(parts.some(part => part.type === 'text' && part.text === 'Say hello.'));
    const audio = parts.find(part => part.type === 'input_audio');
    assert.ok(audio);
    assert.deepEqual(audio.input_audio, { data: 'AAF//w==', format });
  }
});

test('audio chat rejects unsupported attachments and multiple files before fetch', async () => {
  const { engine, calls } = harness();
  for (const files of [
    [new File(['x'], 'picture.png', { type: 'image/png' })],
    [new File(['x'], 'voice.ogg', { type: 'audio/ogg' })],
    [new File(['x'], 'first.wav', { type: 'audio/wav' }), new File(['y'], 'second.wav', { type: 'audio/wav' })],
  ]) await assert.rejects(engine.run(config('openai-audio-chat', { files })));
  assert.equal(calls.length, 0);
});

test('Gemini speech requests AUDIO modality and the configured prebuilt voice', async () => {
  const { engine } = harness();
  const spec = await engine.build(config('gemini-speech', { base: 'https://relay.example' }));
  assert.equal(spec.url, 'https://relay.example/v1beta/models/test-model:generateContent');
  assert.equal(new Headers(spec.headers).get('x-goog-api-key'), 'test-key-local-only');
  const body = bodyOf(spec);
  assert.deepEqual(body.generationConfig.responseModalities, ['AUDIO']);
  assert.equal(body.generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName, 'Kore');
  assert.equal(body.contents[0].parts[0].text, 'Say hello.');
  const customized = await engine.build(config('gemini-speech', { voice: 'Puck' }));
  assert.equal(bodyOf(customized).generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName, 'Puck');
});

test('Gemini speech rejects source uploads instead of silently ignoring them', async () => {
  const { engine, calls } = harness();
  const file = new File(['audio'], 'source.wav', { type: 'audio/wav' });
  await assert.rejects(engine.run(config('gemini-speech', { files: [file] })));
  assert.equal(calls.length, 0);
});

test('audio chat base64 MP3 and WAV outputs preserve MIME and transcript', async () => {
  for (const [format, mime, data] of [
    ['mp3', 'audio/mpeg', 'SUQzBAAA'],
    ['wav', 'audio/wav', 'UklGRgQAAABXQVZF'],
  ]) {
    const { engine, calls } = harness([
      jsonResponse({ choices: [{ message: { content: null, audio: {
        data, transcript: 'Hello from audio chat.',
      } } }] }),
    ]);
    const result = await engine.run(config('openai-audio-chat', { format }));
    assert.equal(result.status, 'success');
    assert.equal(result.media.length, 1);
    assert.equal(result.media[0].kind, 'audio');
    assert.equal(result.media[0].mime, mime);
    assert.equal(result.media[0].url, `data:${mime};base64,${data}`);
    assert.match(result.text, /Hello from audio chat/);
    assert.equal(calls.length, 1);
  }
});

function pcmHeader(media) {
  assert.equal(media.kind, 'audio');
  assert.equal(media.mime, 'audio/wav');
  const data = Buffer.from(media.url.split(',')[1], 'base64');
  assert.equal(data.toString('ascii', 0, 4), 'RIFF');
  assert.equal(data.toString('ascii', 8, 12), 'WAVE');
  return data;
}

test('Gemini PCM inlineData MIME parameters determine the browser WAV sample rate', async () => {
  const source = Buffer.from([0, 0, 255, 127, 0, 128]);
  const { engine } = harness([
    jsonResponse({ candidates: [{ content: { parts: [{ inlineData: {
      mimeType: 'audio/L16;codec=pcm;rate=16000', data: source.toString('base64'),
    } }] } }] }),
  ]);
  const result = await engine.run(config('gemini-speech'));
  assert.equal(result.status, 'success');
  assert.equal(result.media.length, 1);
  const data = pcmHeader(result.media[0]);
  assert.equal(data.readUInt32LE(24), 16000);
  assert.equal(data.readUInt16LE(22), 1);
  assert.equal(data.readUInt16LE(34), 16);
  assert.deepEqual(data.subarray(44), source);
});

test('PCM extraction accepts snake_case MIME fields and defaults to 24 kHz', () => {
  const { engine } = harness();
  const result = engine.extract({ candidates: [{ content: { parts: [{ inline_data: {
    mime_type: 'audio/pcm', data: 'AAD/fwCA',
  } }] } }] }, { kind: 'audio' });
  assert.equal(result.media.length, 1);
  const data = pcmHeader(result.media[0]);
  assert.equal(data.readUInt32LE(24), 24000);
});

test('audio chat PCM16 response format is wrapped as playable WAV', async () => {
  const { engine } = harness([
    jsonResponse({ choices: [{ message: { audio: { data: 'AAD/fwCA', transcript: 'PCM output.' } } }] }),
  ]);
  const result = await engine.run(config('openai-audio-chat', { format: 'pcm16' }));
  assert.equal(result.status, 'success');
  assert.equal(result.media.length, 1);
  const data = pcmHeader(result.media[0]);
  assert.equal(data.readUInt32LE(24), 24000);
  assert.equal(data.length, 50);
});

test('audio endpoints returning only text preserve the response without reporting audio success', async () => {
  for (const [preset, raw] of [
    ['openai-speech', { text: 'This is not speech output.' }],
    ['openai-audio-chat', { choices: [{ message: { content: 'No audio returned.' } }] }],
    ['gemini-speech', { candidates: [{ content: { parts: [{ text: 'No audio available.' }] } }] }],
  ]) {
    const { engine, calls } = harness([jsonResponse(raw)]);
    const result = await engine.run(config(preset));
    assert.equal(result.status, 'unrecognized', preset);
    assert.equal(result.media.length, 0, preset);
    assert.ok(result.text.length > 0, preset);
    assert.equal(calls.length, 1);
  }
});

test('audio chat and Gemini speech failures never trigger a second paid submission', async () => {
  for (const preset of ['openai-audio-chat', 'gemini-speech']) {
    const { engine, calls } = harness([
      jsonResponse({ error: { message: 'Unsupported audio generation model' } }, 400),
    ]);
    await assert.rejects(engine.run(config(preset)), /400.*Unsupported audio generation model/);
    assert.equal(calls.length, 1, preset);
    assert.equal(calls[0].method, 'POST');
  }
});

test('Gemini PCM MIME channel count produces a valid stereo WAV header', () => {
  const { engine } = harness();
  const result = engine.extract({ candidates: [{ content: { parts: [{ inlineData: {
    mimeType: 'audio/L16;rate=48000;channels=2', data: 'AAD/fwCAAQA=',
  } }] } }] }, { kind: 'audio' });
  assert.equal(result.media.length, 1);
  const data = pcmHeader(result.media[0]);
  assert.equal(data.readUInt32LE(24), 48000);
  assert.equal(data.readUInt16LE(22), 2);
  assert.equal(data.readUInt32LE(28), 192000);
  assert.equal(data.readUInt16LE(32), 4);
});

test('relay video task completion accepts its metadata.url output field', async () => {
  const { engine, calls } = harness([
    jsonResponse({ id: 'metadata-video', status: 'completed', metadata: { url: 'https://cdn.example/metadata-video.mp4' } }),
  ]);
  const result = await engine.resume(config('relay-video-json'), 'metadata-video');
  assert.equal(result.status, 'success');
  assert.equal(result.media[0].kind, 'video');
  assert.equal(result.media[0].url, 'https://cdn.example/metadata-video.mp4');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'GET');
});

test('nested task IDs and status fields survive relay polling', async () => {
  const { engine, calls } = harness([
    jsonResponse({ data: { task_id: 'nested-video', task_status: 'QUEUED' } }),
    jsonResponse({ output: { task_id: 'nested-video', task_status: 'SUCCEEDED', video_url: 'https://cdn.example/nested-video.mp4' } }),
  ], { globals: virtualClock() });
  const result = await engine.run(config('relay-video-json', { pollInterval: 1 }));
  assert.equal(result.status, 'success');
  assert.equal(result.taskId, 'nested-video');
  assert.equal(result.media[0].url, 'https://cdn.example/nested-video.mp4');
  assert.equal(calls.length, 2);
});

test('HTTP 200 error objects cannot masquerade as successful audio responses', async () => {
  for (const preset of ['openai-speech', 'openai-audio-chat', 'gemini-speech']) {
    const { engine, calls } = harness([
      jsonResponse({ error: { message: 'Audio generation is unavailable', code: 'unsupported_model' },
        choices: [{ message: { audio: { data: 'SUQzBAAA', format: 'mp3' } } }],
      }),
    ]);
    await assert.rejects(engine.run(config(preset)), error => {
      assert.match(error.message, /Audio generation is unavailable/);
      assert.ok(error.raw?.error);
      return true;
    });
    assert.equal(calls.length, 1);
  }
});

test('binary PCM16 output is wrapped in WAV before it reaches the audio player', async () => {
  const { engine } = harness([
    new Response(new Uint8Array([0, 0, 255, 127]), { headers: { 'Content-Type': 'application/octet-stream' } }),
  ]);
  const result = await engine.run(config('openai-audio-chat', { format: 'pcm16' }));
  try {
    assert.equal(result.status, 'success');
    assert.equal(result.media[0].mime, 'audio/wav');
    const bytes = Buffer.from(await (await fetch(result.media[0].url)).arrayBuffer());
    assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
    assert.equal(bytes.readUInt32LE(24), 24000);
    assert.equal(bytes.length, 48);
  } finally { URL.revokeObjectURL(result.media[0].url); }
});

test('the PCM UI choice uses pcm16 for Audio Chat and preserves pcm for Speech', async () => {
  const { engine } = harness();
  const audioChat = await engine.build(config('openai-audio-chat', { format: 'pcm' }));
  const speech = await engine.build(config('openai-speech', { format: 'pcm' }));
  assert.equal(bodyOf(audioChat).audio.format, 'pcm16');
  assert.equal(bodyOf(audioChat).response_format, undefined);
  assert.equal(bodyOf(speech).response_format, 'pcm');
  assert.equal(bodyOf(speech).audio, undefined);
});

test('model discovery rejects unknown envelopes while retaining response evidence', async () => {
  const raw = { unexpected: [{ id: 'not-a-model-list' }] };
  const { engine, calls } = harness([jsonResponse(raw)]);
  await assert.rejects(engine.listModels(config('openai-chat')), error => {
    assert.match(error.message, /模型列表响应格式无法识别/);
    assert.deepEqual(plain(error.raw), raw);
    assert.equal(error.requests.length, 1);
    assert.equal(error.requests[0].status, 200);
    return true;
  });
  assert.equal(calls.length, 1);
});

test('model discovery accepts legitimate empty arrays in each supported envelope', async () => {
  for (const raw of [[], { data: [] }, { models: [] }]) {
    const { engine, calls } = harness([jsonResponse(raw)]);
    const result = await engine.listModels(config('openai-chat'));
    assert.deepEqual(plain(result.models), []);
    assert.deepEqual(plain(result.raw), raw);
    assert.equal(result.requests.length, 1);
    assert.equal(calls.length, 1);
  }
});

test('supplier progress parses percentages and nested metadata without guessing ratios', () => {
  const { engine } = harness();
  for (const [raw, expected] of [
    [{ progress: '45%' }, 45],
    [{ data: { progress: '62.5' } }, 62.5],
    [{ output: { metadata: { progress_percent: 78 } } }, 78],
    [{ result: { progress_ratio: 0.35 } }, 35],
    [{ progress: 0.5 }, 0.5],
    [{ progress: 0 }, 0],
  ]) assert.equal(engine.taskProgress(raw).progressPercent, expected, JSON.stringify(raw));
});

test('unknown or invalid supplier telemetry remains unknown instead of becoming fake progress', () => {
  const { engine } = harness();
  for (const raw of [
    {}, { progress: null }, { progress: '' }, { progress: true },
    { progress: 'working' }, { progress: '-2%' }, { progress: 101 },
    { progress_ratio: 1.5 }, { progress: Infinity },
  ]) assert.equal(engine.taskProgress(raw).progressPercent, null, JSON.stringify(raw));
  const unknown = engine.taskProgress({ id: 'pending-task', status: 'queued' });
  assert.equal(unknown.progressPercent, null);
  assert.equal(unknown.remainingSeconds, null);
  assert.equal(unknown.queuePosition, null);
});

test('supplier remaining seconds and queue position are parsed only from valid values', () => {
  const { engine } = harness();
  const telemetry = engine.taskProgress({ data: { task_id: 'progress-task', task_status: 'PROCESSING',
    remaining_seconds: '42.5', queue_position: '3', progress: '10%',
  } });
  assert.equal(telemetry.taskId, 'progress-task');
  assert.equal(telemetry.status, 'processing');
  assert.equal(telemetry.remainingSeconds, 42.5);
  assert.equal(telemetry.queuePosition, 3);
  assert.equal(telemetry.progressPercent, 10);
  for (const value of [-1, '', true, 'soon', Infinity]) {
    assert.equal(engine.taskProgress({ remaining_seconds: value }).remainingSeconds, null);
    assert.equal(engine.taskProgress({ queue_position: value }).queuePosition, null);
  }
  assert.equal(engine.taskProgress({ queue_position: 1.5 }).queuePosition, null);
  assert.equal(engine.taskProgress({ remaining_seconds: 0, queue_position: 0 }).remainingSeconds, 0);
  assert.equal(engine.taskProgress({ remaining_seconds: 0, queue_position: 0 }).queuePosition, 0);
});

test('video progress events describe submission, real provider progress, polling and completion', async () => {
  const { engine, calls, events, onEvent } = harness([
    jsonResponse({ id: 'progress-video', status: 'queued', queue_position: 2 }),
    jsonResponse({ id: 'progress-video', status: 'in_progress', progress: '40%', remaining_seconds: 12 }),
    jsonResponse({ id: 'progress-video', status: 'completed', progress: '100%', video_url: 'https://cdn.example/progress.mp4' }),
  ], { globals: virtualClock() });
  const result = await engine.run(config('relay-video-json', { pollInterval: 1, pollTimeout: 30, timeout: 7 }), { onEvent });
  assert.equal(result.status, 'success');
  const progress = events.filter(event => event.type === 'progress');
  assert.equal(progress[0].stage, 'submitting');
  assert.equal(progress[0].progressPercent, null);
  assert.ok(progress.some(event => event.stage === 'queued' && event.queuePosition === 2 && event.taskId === 'progress-video'));
  assert.ok(progress.some(event => event.stage === 'processing' && event.progressPercent === 40 && event.remainingSeconds === 12));
  assert.ok(progress.some(event => event.stage === 'polling' && event.pollCount >= 1));
  const scheduled = progress.filter(event => Number.isFinite(event.nextPollAt));
  assert.ok(scheduled.length > 0, 'poll events expose the next scheduled query');
  assert.ok(scheduled.every(event => Number.isFinite(event.pollDeadline) && event.nextPollAt <= event.pollDeadline));
  assert.equal(progress.at(-1).stage, 'complete');
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(events.find(event => event.type === 'request').timeoutSeconds, 7);
});

test('video content retrieval emits downloading before complete', async () => {
  const { engine, events, onEvent, calls } = harness([
    jsonResponse({ id: 'download-progress', status: 'completed' }),
    new Response(new Uint8Array([0, 0, 0, 24]), { headers: { 'Content-Type': 'video/mp4' } }),
  ]);
  const result = await engine.resume(config('openai-video'), 'download-progress', { onEvent });
  try {
    const progress = events.filter(event => event.type === 'progress');
    const downloading = progress.findIndex(event => event.stage === 'downloading');
    const complete = progress.findIndex(event => event.stage === 'complete');
    assert.ok(downloading >= 0 && complete > downloading);
    assert.equal(calls.filter(call => call.method === 'POST').length, 0);
  } finally { result.media.filter(media => media.url.startsWith('blob:')).forEach(media => URL.revokeObjectURL(media.url)); }
});

test('waiting deadline telemetry never invents provider percent or resubmits generation', async () => {
  const { engine, events, onEvent, calls } = harness([
    jsonResponse({ id: 'unknown-progress', status: 'queued' }),
  ], { globals: virtualClock() });
  const result = await engine.run(config('relay-video-json', { pollInterval: 5, pollTimeout: 5 }), { onEvent });
  assert.equal(result.status, 'pending');
  const progress = events.filter(event => event.type === 'progress');
  assert.ok(progress.some(event => event.stage === 'waiting'));
  assert.ok(progress.every(event => event.progressPercent === null));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(progress.some(event => event.stage === 'complete'), false);
});

test('stopping a progress-enabled video run retains task metadata and performs only one POST', async () => {
  const controller = new AbortController();
  const { engine, calls, events } = harness([
    jsonResponse({ id: 'stop-progress', status: 'queued', progress: '15%' }),
  ], { globals: virtualClock() });
  await assert.rejects(engine.run(config('relay-video-json', { pollInterval: 5 }), {
    signal: controller.signal,
    onEvent: event => {
      events.push(event);
      if (event.type === 'progress' && event.stage === 'waiting') controller.abort();
    },
  }), error => {
    assert.equal(error.taskId, 'stop-progress');
    return error.name === 'AbortError';
  });
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.ok(events.some(event => event.type === 'progress' && event.progressPercent === 15));
  assert.equal(events.some(event => event.type === 'progress' && event.stage === 'complete'), false);
});

test('task progress traversal is bounded, cycle-safe and ignores unrelated timing fields', () => {
  const { engine } = harness();
  const raw = { duration: 99, elapsed: 40, metadata: { progressPercent: '12.5%', estimatedRemainingSeconds: '3.5', positionInQueue: '0' } };
  raw.data = raw;
  const progress = engine.taskProgress(raw);
  assert.equal(progress.progressPercent, 12.5);
  assert.equal(progress.remainingSeconds, 3.5);
  assert.equal(progress.queuePosition, 0);
  const unrelated = engine.taskProgress({ duration: 100, elapsed: 75, time: 20, progress_text: 'Almost finished' });
  assert.equal(unrelated.progressPercent, null);
  assert.equal(unrelated.remainingSeconds, null);
});

test('JSON video protocols send an explicit trimmed resolution and omit blank values', async () => {
  const { engine } = harness();
  for (const preset of ['relay-video-json', 'doubao-video', 'custom-video']) {
    const spec = await engine.build(config(preset, { resolution: '  1080p  ' }));
    assert.equal(bodyOf(spec).resolution, '1080p', preset);
    assert.equal(bodyOf(await engine.build(config(preset))).resolution, undefined, preset);
    assert.equal(bodyOf(await engine.build(config(preset, { resolution: '   ' }))).resolution, undefined, preset);
  }
});

test('native OpenAI video and non-video protocols do not inherit the resolution field', async () => {
  const { engine } = harness();
  const native = await engine.build(config('openai-video', { resolution: '1080p', size: '1280x720' }));
  assert.ok(native.body instanceof FormData);
  assert.equal(native.body.get('size'), '1280x720');
  assert.equal(native.body.has('resolution'), false);
  for (const preset of ['openai-image', 'openai-chat', 'openai-speech']) {
    const spec = await engine.build(config(preset, { resolution: '1080p' }));
    assert.equal(bodyOf(spec).resolution, undefined, preset);
  }
});

test('extra video JSON parameters override resolution without deriving it from size', async () => {
  const { engine } = harness();
  for (const preset of ['relay-video-json', 'doubao-video', 'custom-video']) {
    const spec = await engine.build(config(preset, {
      size: '1280x720', resolution: '720p', extra: { resolution: 'provider-specific-resolution' },
    }));
    assert.equal(bodyOf(spec).resolution, 'provider-specific-resolution', preset);
    const sizeOnly = await engine.build(config(preset, { size: '1280x720' }));
    assert.equal(bodyOf(sizeOnly).resolution, undefined, 'a pixel size is not silently converted to a provider resolution');
  }
});

function imageFiles() {
  return [
    new File([new Uint8Array([137, 80, 78, 71, 1])], 'first.png', { type: 'image/png' }),
    new File([new Uint8Array([255, 216, 255, 2])], 'second.jpg', { type: 'image/jpeg' }),
    new File([new Uint8Array([82, 73, 70, 70, 3])], 'third.webp', { type: 'image/webp' }),
  ];
}

async function expectedImageData(file) {
  return `data:${file.type};base64,${Buffer.from(await file.arrayBuffer()).toString('base64')}`;
}

test('relay image JSON uses one image string or an ordered array of multiple reference images', async () => {
  const { engine } = harness();
  const files = imageFiles();
  const single = await engine.build(config('relay-image-json', { files: [files[0]] }));
  assert.equal(single.url, 'https://relay.example/v1/images/generations');
  assert.equal(new Headers(single.headers).get('Content-Type'), 'application/json');
  assert.equal(bodyOf(single).image, await expectedImageData(files[0]));
  const reordered = [files[2], files[0], files[1]];
  const multiple = await engine.build(config('relay-image-json', { files: reordered }));
  assert.deepEqual(bodyOf(multiple).image, await Promise.all(reordered.map(expectedImageData)));
  assert.equal(bodyOf(multiple).model, 'test-model');
  assert.equal(bodyOf(multiple).prompt, 'Say hello.');
});

test('OpenAI image edit chooses image for one file and image[] for ordered multiple files', async () => {
  const { engine } = harness();
  const files = imageFiles();
  const single = await engine.build(config('openai-image-edit', { files: [files[0]] }));
  assert.ok(single.body instanceof FormData);
  assert.equal(single.body.getAll('image').length, 1);
  assert.equal(single.body.getAll('image[]').length, 0);
  assert.equal(single.body.get('image').name, 'first.png');
  const reordered = [files[1], files[2], files[0]];
  const multiple = await engine.build(config('openai-image-edit', { files: reordered }));
  assert.equal(new Headers(multiple.headers).has('Content-Type'), false);
  assert.equal(multiple.body.getAll('image').length, 0);
  const uploaded = multiple.body.getAll('image[]');
  assert.deepEqual(uploaded.map(file => file.name), reordered.map(file => file.name));
  for (let i = 0; i < uploaded.length; i++) {
    assert.deepEqual(Buffer.from(await uploaded[i].arrayBuffer()), Buffer.from(await reordered[i].arrayBuffer()));
  }
});

test('Gemini image generation keeps every reference image in the selected order', async () => {
  const { engine } = harness();
  const files = imageFiles();
  const reordered = [files[2], files[1], files[0]];
  const spec = await engine.build(config('gemini-image', { files: reordered, base: 'https://relay.example' }));
  const parts = bodyOf(spec).contents[0].parts;
  assert.equal(parts[0].text, 'Say hello.');
  const media = parts.filter(part => part.inlineData).map(part => part.inlineData);
  assert.equal(media.length, 3);
  assert.deepEqual(media.map(item => item.mimeType), reordered.map(file => file.type));
  assert.deepEqual(media.map(item => item.data), await Promise.all(reordered.map(async file => Buffer.from(await file.arrayBuffer()).toString('base64'))));
});

test('protocols without file mappings reject uploads instead of silently discarding them', async () => {
  const { engine, calls } = harness();
  const file = imageFiles()[0];
  for (const preset of ['openai-image', 'openai-speech', 'gemini-speech', 'custom-video', 'relay-video-json']) {
    await assert.rejects(engine.run(config(preset, { files: [file] })), /上传|参考图|文件|文本|图片/);
  }
  assert.equal(calls.length, 0);
});

test('uploaded image data cannot be replaced silently by conflicting extra JSON', async () => {
  const { engine, calls } = harness();
  const files = imageFiles().slice(0, 2);
  for (const [preset, extra] of [
    ['relay-image-json', { image: 'https://cdn.example/replacement.png' }],
    ['openai-image-edit', { image: 'https://cdn.example/replacement.png' }],
    ['openai-image-edit', { 'image[]': ['replacement'] }],
    ['gemini-image', { contents: [{ parts: [{ text: 'replacement' }] }] }],
    ['openai-chat', { messages: [{ role: 'user', content: 'replacement' }] }],
    ['openai-responses', { input: 'replacement' }],
  ]) await assert.rejects(engine.run(config(preset, { files, extra })), /冲突/);
  assert.equal(calls.length, 0);
});

test('relay image reference requires input but permits explicit URL-only extra configuration', async () => {
  const { engine, calls } = harness();
  for (const extra of [{}, { image: '' }, { image: [] }]) {
    await assert.rejects(engine.build(config('relay-image-json', { extra })), /参考图|图片/);
  }
  const urls = ['https://cdn.example/a.png', 'https://cdn.example/b.png'];
  const spec = await engine.build(config('relay-image-json', { extra: { image: urls } }));
  assert.deepEqual(bodyOf(spec).image, urls);
  assert.equal(calls.length, 0);
});
