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
  assert.match(html,/<div class="executive-score"><span>综合验收分<\/span><div><strong>100<\/strong>/);
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
  assert.match(html,/<div class="executive-score"><span>综合验收分<\/span><div><strong>—<\/strong>/);
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
test('unknown capability evidence earns no score and remains separate from coverage',async()=>{
  const r=record();r.result.checks=[{id:'cache-missing',name:'大上下文缓存',status:'inconclusive',dimensions:['cache'],parameters:{target_tokens:12000},scenario_id:'cache-cold',reason_code:'evidence_missing',request_ids:['one']}];
  const html=await harness().WorkbenchReport.render([r]);
  assert.match(html,/<div class="executive-score"><span>综合验收分<\/span><div><strong>—<\/strong>/);
  assert.match(html,/可判定率 0%/);
  assert.match(html,/有限样本/);
  assert.match(html,/返回证据不足/);
  assert.match(html,/target_tokens/);assert.match(html,/12000/);
  assert.doesNotMatch(html,/无法判定=40/);
});
test('capability pass ratio excludes inconclusive evidence but exposes the resolution denominator',async()=>{
  const r=record();r.result.checks=[
    {id:'cap-1',name:'max_tokens=1',status:'passed',dimensions:['max_tokens'],parameters:{max_tokens:1},scenario_id:'enumeration',request_ids:['one']},
    {id:'cap-10',name:'max_tokens=10',status:'failed',dimensions:['max_tokens'],parameters:{max_tokens:10},scenario_id:'enumeration'},
    {id:'cap-20',name:'max_tokens=20',status:'inconclusive',dimensions:['max_tokens'],parameters:{max_tokens:20},scenario_id:'enumeration'},
  ];
  const html=await harness().WorkbenchReport.render([r]);
  assert.match(html,/<div class="executive-score"><span>综合验收分<\/span><div><strong>50<\/strong>/);
  assert.match(html,/参数组合 3 · 关联请求 1 · 可判定率 67%/);
  assert.match(html,/有限样本 · 不代表完整能力/);
});
test('general injection has its own capability dimension and request parameter evidence',async()=>{
  const r=record();r.result.checks=[{id:'injection-canary',name:'合成注入',status:'passed',module:'injection',dimensions:['injection','security'],parameters:{attack:'direct'},request_ids:['one']}];
  const html=await harness().WorkbenchReport.render([r]);
  assert.match(html,/注入与指令隔离/);assert.match(html,/参数组合 1/);assert.match(html,/<dt>attack<\/dt><dd>direct<\/dd>/);
  assert.match(html,/覆盖 1 \/ 7 维/);assert.equal((html.match(/score-dimension not_covered/g)||[]).length,6);
});

function overview(html){return html.split('<section id="overview"')[1].split('<section id="modules"')[0];}
function check(id,dimensions,status='passed',extra={}){return {id,name:id,dimensions,status,...extra};}
function summaryRecord(checks,requests=[],extra={}){return {kind:'general',model:'summary-fixture',...extra,result:{checks,requests}};}
test('conclusion and weighted modules lead report, full matrix is the final section',async()=>{
  const html=await harness().WorkbenchReport.render([record()]);
  const order=['overview','modules','score','scope','findings','checks','requests','original-results','all-results'].map(id=>html.indexOf(`id="${id}"`));
  assert.ok(order.every((value,index)=>value>=0&&(index===0||value>order[index-1])));
  const tail=html.slice(html.indexOf('id="all-results"'));
  assert.equal((tail.match(/<section/g)||[]).length,0);
  assert.ok(tail.includes('<footer>'));
  assert.equal((html.match(/class="executive-score"/g)||[]).length,1);
  assert.doesNotMatch(html,/<div class="score-total">|各可评分维度等权汇总/);
});
test('single headline score uses browser module weights instead of a second dimension average',async()=>{
  const r=summaryRecord([check('protocol',['protocol'],'failed'),check('tools',['tools'])]);
  const html=await harness().WorkbenchReport.render([r]);
  assert.match(overview(html),/<strong>43<\/strong>/);
  assert.match(html,/可评分权重 35% \/ 100%/);
  assert.match(overview(html),/基础能力需重点核查/);
});
test('illegal max_tokens control failure cannot imply a legal cap was ignored',async()=>{
  const r=summaryRecord([check('max_tokens=0',['max_tokens'],'failed',{parameters:{max_tokens:0}}),check('max_tokens=10',['max_tokens'],'passed',{parameters:{max_tokens:10}}),check('isolation',['security'],'failed')]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/非法限长参数校验存在异常，不能据此判定合法限长失效/);
  assert.match(html,/指令隔离存在异常/);
  assert.match(html,/不能据此认定上游私自添加提示词/);
  assert.doesNotMatch(html,/限长出现超限|长度控制未遵守|疑似偷偷/);
});
test('verified output cap excess produces a direct conclusion and links to evidence',async()=>{
  const r=summaryRecord([check('base',['protocol']),check('cap',['max_tokens'],'failed',{parameters:{max_tokens:10},reason_code:'output_cap_exceeded'})]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/基础接口可用；部分限长样本出现超限/);
  assert.match(html,/长度控制未遵守请求/);
  assert.match(html,/href="#record-1-check-2"/);
});
test('cache headline counts only explicit warm response usage, deduplicates and reports missing fields',async()=>{
  const warm=(id,request)=>check(id,['cache'],'passed',{parameters:{round:'warm_1'},request_ids:[request]});
  const r=summaryRecord([warm('a','warm'),warm('a-second-assertion','warm'),warm('b','missing'),check('cold',['cache'],'passed',{parameters:{round:'cold'},request_ids:['cold']})],[
    {id:'warm',status:200,response:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:800}}}},
    {id:'missing',status:200,request:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:1000}}},response:{usage:{prompt_tokens:1000}}},
    {id:'cold',status:200,response:{usage:{prompt_tokens:100000,prompt_tokens_details:{cached_tokens:0}}}},
  ]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/已计量暖请求缓存 Token 命中率 80%/);
  assert.match(html,/读取 800 \/ 完整输入 1,000 Token/);
  assert.match(html,/命中请求 1\/1，字段完整 1\/2 个暖请求/);
});
test('native cache denominator adds read and creation, missing creation stays unknown',async()=>{
  const r=summaryRecord([check('warm',['cache'],'passed',{parameters:{round:'warm_2'},request_ids:['warm']})],[{id:'warm',status:200,response:{usage:{input_tokens:200,cache_read_input_tokens:800}}}]);
  let html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/缓存命中率未知/);assert.doesNotMatch(html,/命中率 80%/);
  r.result.requests[0].response.usage.cache_creation_input_tokens=0;
  html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/缓存 Token 命中率 80%/);assert.match(html,/完整输入 1,000 Token/);
});
test('cache score does not fabricate a hit rate or use usage from failed requests',async()=>{
  const r=summaryRecord([check('warm',['cache'],'passed',{parameters:{round:'warm'},request_ids:['warm']})],[{id:'warm',status:500,response:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:800}}}}]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/缓存命中率未知/);assert.doesNotMatch(html,/缓存 Token 命中率 80%/);
});
test('elapsed report time uses wall clock intervals, not sum of overlapping request durations',async()=>{
  const start=1700000000;
  const records=[summaryRecord([check('first',['protocol'])],[{id:'1',status:200,duration_ms:10000}],{created_at:start,duration_ms:10000}),summaryRecord([check('second',['protocol'])],[{id:'2',status:200,duration_ms:15000}],{created_at:start+5,duration_ms:15000})];
  const html=overview(await harness().WorkbenchReport.render(records));
  assert.match(html,/>测试总耗时<\/span><strong>20 秒<\/strong>/);
  assert.match(html,/P50 <b>12.5 秒<\/b>/);assert.match(html,/P95 <b>14.8 秒<\/b>/);assert.match(html,/已保存耗时 <b>2\/2 个请求/);
  assert.doesNotMatch(html,/<strong>25 秒<\/strong>/);
});
test('missing run timestamps show a labelled request window and never invent full test time',async()=>{
  const r=summaryRecord([check('baseline',['protocol'])],[{id:'1',status:200,started_at:1700000000,duration_ms:2000},{id:'2',status:200,started_at:1700000001,duration_ms:2000}]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/请求观测窗口（非完整测试耗时）/);assert.match(html,/<strong>3 秒<\/strong>/);
  assert.doesNotMatch(html,/>测试总耗时<\/span>/);
});
test('Gemini cache uses original usageMetadata denominator',async()=>{
  const r=summaryRecord([check('warm',['cache'],'passed',{parameters:{round:'warm_1'},request_ids:['warm']})],[{id:'warm',status:200,response:{usageMetadata:{promptTokenCount:1000,cachedContentTokenCount:800,candidatesTokenCount:5}}}]);
  assert.match(overview(await harness().WorkbenchReport.render([r])),/缓存 Token 命中率 80%/);
});
test('a zero-cap assertion never becomes a legal-cap excess claim even if a legacy reason code says excess',async()=>{
  const r=summaryRecord([check('zero',['max_tokens'],'failed',{parameters:{max_tokens:0},reason_code:'output_cap_exceeded'})]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/非法限长参数校验存在异常/);assert.doesNotMatch(html,/合法上限样本输出超限|限长出现超限/);
});
test('portable score uses the same even rounding as the server at 62.5',async()=>{
  const checks=Array.from({length:8},(_,i)=>check('tool-'+i,['tools'],i<5?'passed':'failed'));
  const html=await harness().WorkbenchReport.render([summaryRecord(checks)]);
  assert.match(overview(html),/<strong>62<\/strong>/);
  assert.doesNotMatch(overview(html),/<strong>63<\/strong>/);
});
test('warm variant takes precedence over numeric matrix round and noninteger usage is rejected',async()=>{
  const r=summaryRecord([check('warm',['cache'],'passed',{parameters:{variant:'warm',round:2},request_ids:['warm']})],[{id:'warm',status:200,response:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:800}}}}]);
  assert.match(overview(await harness().WorkbenchReport.render([r])),/缓存 Token 命中率 80%/);
  r.result.requests[0].response.usage.prompt_tokens_details.cached_tokens=800.5;
  assert.match(overview(await harness().WorkbenchReport.render([r])),/缓存命中率未知/);
});
test('multiple model summaries never pool cache hits into one model capability claim',async()=>{
  const make=(model,cached)=>({...summaryRecord([check('warm',['cache'],'passed',{parameters:{round:'warm'},request_ids:['warm']})],[{id:'warm',status:200,response:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:cached}}}}]),model});
  const html=overview(await harness().WorkbenchReport.render([make('first',800),make('second',0)]));
  assert.match(html,/多模型报告：各模型独立判读/);assert.match(html,/跨模型汇总不能证明单个模型能力/);
  assert.doesNotMatch(html,/暖请求缓存 Token 命中率 40%/);
});

test('mixed capability result uses amber and exact counts without rewriting failed assertions',async()=>{
  const checks=Array.from({length:9},(_,i)=>check('base-'+i,['protocol'],i<8?'passed':'failed'));
  const html=await harness().WorkbenchReport.render([summaryRecord(checks)]),brief=overview(html);
  assert.match(brief,/class="brief-item attention" data-status="failed"/);
  assert.match(brief,/class="badge attention" data-status="failed">部分异常/);
  assert.match(brief,/多数检查通过 · 1 项异常。通过 8\/9/);
  assert.doesNotMatch(brief,/>未通过</);
  assert.match(html,/data-status="failed">本项异常/);
  assert.match(html,/本项计分 0 \/ 100/);
  assert.match(brief,/<strong>89<\/strong>/);
});
test('all anomalous samples remain a prominent scoped risk rather than a pass',async()=>{
  const html=overview(await harness().WorkbenchReport.render([summaryRecord([check('tool',['tools'],'failed')])]));
  assert.match(html,/class="brief-item risk" data-status="failed"/);
  assert.match(html,/需重点核查/);
  assert.match(html,/1\/1 项已判定检查出现异常/);
  assert.match(html,/<strong>0<\/strong>/);
});
test('zero observed cache reuse is an amber observation and remains distinct from missing measurement',async()=>{
  const r=summaryRecord([check('warm',['cache'],'passed',{parameters:{round:'warm_1'},request_ids:['warm']})],[{id:'warm',status:200,response:{usage:{prompt_tokens:1000,prompt_tokens_details:{cached_tokens:0}}}}]);
  const html=overview(await harness().WorkbenchReport.render([r]));
  assert.match(html,/class="brief-item attention" data-status="inconclusive"/);
  assert.match(html,/>本轮未观察到复用<\/span>/);
  assert.match(html,/缓存 Token 命中率 0%（读取 0 \/ 完整输入 1,000 Token）/);
  assert.doesNotMatch(html,/>复用证据待补齐<\/span>/);
  assert.match(html,/<strong>100<\/strong>/); // measured reuse is not the assertion score
});
function gradeFixture(percent,{unknown=0,requestCount=10,moduleKeys=['protocol','multimodal','tools','max_tokens','cache','reliability','security']}={}){
  const requests=Array.from({length:requestCount},(_,i)=>({id:'sample-'+i,status:200}));
  const checks=moduleKeys.flatMap(module=>Array.from({length:100+unknown},(_,i)=>check(`${module}-${i}`,[module],i>=100?'inconclusive':i<percent?'passed':'failed',{request_ids:requestCount?['sample-'+(i%requestCount)]:[]})));
  return summaryRecord(checks,requests);
}
test('resource grade thresholds follow the unchanged weighted score at 69, 70, 89 and 90',async()=>{
  for(const [score,level,label] of [[69,'low','低等级资源'],[70,'medium','中等资源'],[89,'medium','中等资源'],[90,'high','优质资源']]){
    const html=overview(await harness().WorkbenchReport.render([gradeFixture(score)]));
    assert.match(html,new RegExp(`<strong>${score}</strong>`));
    assert.match(html,new RegExp(`class="resource-grade grade-${level}"><span class="grade-label">本轮资源评级</span><b class="grade-status">${label}</b>`));
    assert.match(html,/>本轮资源评级<\/span>/);
    assert.doesNotMatch(html,/>暂定 · 证据待完善<\/small>/);
    assert.match(html,/评级仅反映本轮已测渠道表现/);
  }
});
test('small sample, low coverage and unresolved evidence qualify the grade without changing it',async()=>{
  for(const [options,reason] of [[{requestCount:9},/关联能力请求 9 个/],[{moduleKeys:['protocol']},/可评分权重 20%/],[{unknown:30},/证据可判定率 77%/]]){
    const html=overview(await harness().WorkbenchReport.render([gradeFixture(90,options)]));
    assert.match(html,/<strong>90<\/strong>/);
    assert.match(html,/class="resource-grade grade-high"/);
    assert.match(html,/>暂定 · 证据待完善<\/small>/);
    assert.match(html,reason);
  }
});
test('unscorable evidence is pending assessment, never a fabricated zero grade',async()=>{
  const html=overview(await harness().WorkbenchReport.render([summaryRecord([check('unknown',['protocol'],'inconclusive')])]));
  assert.match(html,/<strong>—<\/strong>/);
  assert.match(html,/class="resource-grade grade-unknown"><span class="grade-label">本轮资源评级<\/span><b class="grade-status">待评估<\/b>/);
  assert.match(html,/尚无可评分证据，暂不评级/);
});
test('resource grade sits before the headline and multimodel ratings remain provisional even with sufficient evidence',async()=>{
  const first={...gradeFixture(90),model:'first'},second={...gradeFixture(90),model:'second'};
  const html=overview(await harness().WorkbenchReport.render([first,second]));
  assert.match(html,/<div class="executive-verdict"><span class="index">VERDICT \/ 本轮结论<\/span><div class="resource-grade grade-high">/);
  assert.ok(html.indexOf('class="resource-grade')<html.indexOf('<h2>'));
  assert.doesNotMatch(html.split('<div class="executive-score">')[1],/class="resource-grade/);
  assert.match(html,/<small>暂定 · 证据待完善<\/small>/);
  assert.match(html,/多模型汇总评级仅供参考，不能代表单个模型/);
  assert.match(html,/<details class="grade-criteria"><summary>查看评级标准与适用范围<\/summary>/);
});
