'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const script=fs.readFileSync(path.join(__dirname,'client-report.js'),'utf8');
function harness({protocol='file:',fetch=()=>{throw Error('unexpected request');},theme='',ready}={}){
  const root={location:{protocol},fetch,URL,AbortController,setTimeout,clearTimeout,WORKBENCH_REPORT_THEME:theme,WORKBENCH_REPORT_THEME_READY:ready};
  root.window=root;vm.runInNewContext(script,root,{filename:'client-report.js'});return root;
}
function record(){return {kind:'general',model:'fixture-model',result:{config:{model:'fixture-model',key:'private-fixture-key'},total:87,scores:{tools:8.7},mode:'std',logs:[{message:'all assertions saved'}],checks:[
  {id:'tool-pass',name:'标准工具调用',status:'passed',dimensions:['tools'],method:'发送强制 Calculator 调用',expected:'返回 Calculator tool_calls',observed:'已收到工具调用',reason:'函数名与参数匹配',next_step:'增加多轮回填样本',request_ids:['one']},
  {name:'动态工具',status:'skipped',dimensions:['tools'],skip_reason:'本轮未启用'},
  {name:'不适用工具',status:'failed',applicable:false,dimensions:['tools'],skip_reason:'当前协议不适用'},
  {name:'未覆盖工具',status:'not_covered',dimensions:['tools']},
],requests:[{id:'one',status:200,url:'https://relay.test/v1/chat/completions',method:'POST',request_body:{tools:[{name:'Calculator'}]},response_body:{message:'private-fixture-key'},response_headers:{'x-request-id':'provider-first'}},{id:'unlinked',status:503,url:'https://relay.test/v1/chat/completions',response_body:{error:'gateway unavailable'}}]}};}
test('general export expands every assertion, preserves source score and excludes unexecuted checks from score',async()=>{
  const html=await harness().WorkbenchReport.render([record()],[]);
  assert.equal((html.match(/class="check" id=/g)||[]).length,4);
  assert.equal((html.match(/class="score-dimension /g)||[]).length,6);
  assert.match(html,/<div class="score-total">100<small>/);
  assert.match(html,/原始总分：87 \/ 100/);
  for(const detail of ['发送强制 Calculator 调用','返回 Calculator tool_calls','已收到工具调用','函数名与参数匹配','增加多轮回填样本','当前协议不适用','gateway unavailable','provider-first'])assert.ok(html.includes(detail),detail);
  assert.equal((html.match(/class="request" id=/g)||[]).length,2);
  assert.match(html,/未明确关联；仍完整保留本请求/);
  assert.doesNotMatch(html,/private-fixture-key/);
  assert.doesNotMatch(html,/渠道基础测试/);
  assert.match(html,/不参与分母/);
});
test('basic success cannot fabricate tool, cache, max_tokens or performance coverage',async()=>{
  const html=await harness().WorkbenchReport.render([{kind:'text',model:'ordinary',status:'success',config:{prompt:'talk about tools cache max_tokens'},raw:{usage:{prompt_tokens:5,completion_tokens:6,total_tokens:11}},text:'tool cache max_tokens stable'}],[]);
  assert.equal((html.match(/score-dimension not_covered/g)||[]).length,5);
  assert.match(html,/覆盖 1 \/ 6 维/);
  assert.match(html,/尚未覆盖|未覆盖/);
});
test('hosted export uses only report service, sends sanitized observations and returns server template',async()=>{
  const calls=[];
  const root=harness({protocol:'http:',fetch:async(url,options)=>{calls.push({url,options});return url==='/api/session'?new Response(JSON.stringify({token:'session-token'}),{headers:{'content-type':'application/json'}}):new Response('<!doctype html><html><h1>统一服务端报告</h1></html>',{headers:{'content-type':'text/html;charset=utf-8'}});}});
  const html=await root.WorkbenchReport.renderExport([record()],[{message:'report only'}]);
  assert.match(html,/统一服务端报告/);
  assert.deepEqual(calls.map(x=>x.url),['/api/session','/api/reports']);
  assert.equal(calls[1].options.headers['X-Workbench-Token'],'session-token');
  const payload=JSON.parse(calls[1].options.body);
  assert.equal(payload.records[0].result.checks.length,4);
  assert.equal(payload.records[0].result.total,87);
  assert.doesNotMatch(calls[1].options.body,/private-fixture-key/);
});
test('hosted srcdoc export resolves service mode from inherited document base URI',async()=>{
  const calls=[];
  const root=harness({protocol:'about:',fetch:async url=>{calls.push(url);return url==='/api/session'?new Response(JSON.stringify({token:'embedded-token'})):new Response('<!doctype html><html>embedded unified report</html>',{headers:{'content-type':'text/html'}});}});
  root.document={baseURI:'http://127.0.0.1:8877/'};
  const html=await root.WorkbenchReport.renderExport([record()]);
  assert.match(html,/embedded unified report/);assert.deepEqual(calls,['/api/session','/api/reports']);
});
test('service failure uses portable template with all evidence and safely awaited shared theme',async()=>{
  let root;
  const ready=Promise.resolve().then(()=>{root.WORKBENCH_REPORT_THEME='body{color:blue}/* </style><script>bad()</script> */';});
  root=harness({protocol:'http:',ready,fetch:async()=>new Response('unavailable',{status:503})});
  const html=await root.WorkbenchReport.renderExport([record()],[]);
  assert.match(html,/body\{color:blue\}/);
  assert.match(html,/gateway unavailable/);
  assert.doesNotMatch(html,/<\/style><script>bad/);
  assert.match(html,/&lt;|<\\\/style/);
});
test('empty general checks stay inconclusive and arbitrary output is escaped',async()=>{
  const html=await harness().WorkbenchReport.render([{kind:'general',model:'<script>alert(1)</script>',result:{checks:[],total:null,requests:[]}}],[]);
  assert.match(html,/检查证据不完整/);assert.match(html,/不能把空检查列表判为通过/);
  assert.doesNotMatch(html,/<script>alert/);assert.match(html,/&lt;script&gt;alert/);
});
test('skipped-only capability has no score rather than a false 0 or pass',async()=>{
  const r=record();r.result.checks=r.result.checks.slice(1);
  const html=await harness().WorkbenchReport.render([r],[]);
  assert.match(html,/<div class="score-total">—<small>/);
  assert.match(html,/尚无可评分证据/);
  assert.equal((html.match(/score-dimension not_covered/g)||[]).length,6);
});
test('legacy general assertions get the shared descriptions without replacing explicit evidence',async()=>{
  const root=harness();root.GeneralCheckContent=require('./general-check-content.js');
  const r={kind:'general',model:'general-fixture',result:{checks:[{name:'模型列表',status:'passed',observed:'发现三个模型'},{name:'上下文缓存',status:'inconclusive',method:'保存的独立方法优先',expected:'保存的断言优先',observed:'0 → 0 → 0'}]}};
  const html=await root.WorkbenchReport.render([r]);
  assert.match(html,/调用渠道模型列表接口/);assert.match(html,/发现三个模型/);
  assert.match(html,/保存的独立方法优先/);assert.match(html,/保存的断言优先/);
  assert.match(html,/未观测命中不等于模型不支持缓存/);
});
test('derived or inapplicable token totals never display arithmetic verification',async()=>{
  for(const flags of [{total_derived:true},{total_tokens_derived:true},{accounting_applicable:false}]){
    const root=harness();
    const html=await root.WorkbenchReport.render([{kind:'general',model:'old-gpt-usage',result:{checks:[{name:'GPT usage',status:'inconclusive'}],gpt_evaluation:{token_usage:{input:10,output:20,total:30,consistent:true,...flags}}}}]);
    assert.match(html,/未验证渠道上报总量/);assert.doesNotMatch(html,/usage 一致/);
    if(flags.accounting_applicable===false)assert.match(html,/当前协议不适用总量加总校验/);
    else assert.match(html,/总计 30（派生）/);
  }
});
