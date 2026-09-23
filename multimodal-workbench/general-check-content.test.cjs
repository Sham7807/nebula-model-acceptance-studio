'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {describe}=require('./general-check-content.js');
test('core descriptions match implemented assertions without overstating capability',()=>{
  assert.match(describe('Function Calling').method,/tool_choice=auto/);
  assert.match(describe('Function Calling').meaning,/未执行/);
  assert.match(describe('并行工具调用').method,/tool_choice=auto/);
  assert.match(describe('动态工具加载').method,/只有第二次/);
  assert.match(describe('logprobs').expected,/非null/);
  assert.match(describe('GPT usage').expected,/派生total不参与/);
  assert.match(describe('max_tokens=1 边界值').expected,/不超过1/);
  assert.match(describe('max_tokens=1e8').method,/99999999/);
  assert.match(describe('视频输入（video_url）').expected,/未.*真实性/);
  assert.match(describe('JSON Schema 结构化输出').expected,/未完整遍历/);
  assert.match(describe('隐藏 system prompt 探测').meaning,/不能证明/);
  assert.match(describe('缓存').method,/两次/);
  assert.match(describe('上下文缓存 ×3').method,/三次/);
});
test('status advice and fallback keep missing evidence explicit',()=>{
  assert.match(describe('流式输出','failed').next_step,/关联请求/);
  assert.match(describe('流式输出','passed').next_step,/回归基线/);
  assert.match(describe('流式输出','not_covered').next_step,/不参与分数/);
  assert.match(describe('unknown custom case').expected,/未保存独立预期/);
  const first=describe('工具调用');first.dimensions.push('fake');
  assert.deepEqual(describe('工具调用').dimensions,['tools']);
});
