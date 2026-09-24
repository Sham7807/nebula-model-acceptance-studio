const test=require('node:test');
const assert=require('node:assert/strict');
const matrix=require('./general-matrix.js');
const formats=require('./general-format.js');
test('quick, standard and full caps are independent cross-products with visible cost',()=>{
  for(const [mode,count]of [['quick',3],['std',12],['full',72]]){
    const rows=matrix.caps(mode);assert.equal(rows.length,count);assert.equal(new Set(rows.map(row=>row.id)).size,count);
    for(const cap of [1,10,20])assert.ok(rows.some(row=>row.cap===cap));
    assert.equal(matrix.plan(mode).cap_output_token_budget,rows.reduce((n,row)=>n+row.cap,0));
  }
  assert.equal(matrix.caps('std').filter(row=>row.stream).length,6);
  assert.equal(matrix.caps('full').filter(row=>row.repeat===2).length,36);
});
test('length judgment never passes missing usage and accepts thinking-only boundary',()=>{
  const value=(count,finish='length')=>({ok:true,status:200,body:{choices:[{message:{content:''},finish_reason:finish}],usage:{completion_tokens:count}}});
  assert.equal(matrix.judgeCap(value(1),{cap:1}).status,'passed');
  assert.equal(matrix.judgeCap(value(undefined),{cap:1}).reason_code,'evidence_missing');
  assert.equal(matrix.judgeCap(value(10),{cap:1}).reason_code,'output_cap_exceeded');
  assert.equal(matrix.judgeCap(value(1,'stop'),{cap:10}).reason_code,'cap_not_exercised');
  assert.equal(matrix.judgeCap({ok:false,status:'timeout',err:'expired'},{cap:1}).reason_code,'transport_error');
  assert.equal(matrix.judgeCap({ok:false,status:400,body:{error:{message:'Unsupported max_tokens'}}},{cap:1}).reason_code,'unsupported_parameter');
  assert.equal(matrix.judgeCap({ok:false,status:429},{cap:1}).reason_code,'rate_limited');
});
test('all four adapters preserve each cap and streaming choice at their native fields',()=>{
  for(const format of ['openai-chat','openai-responses','anthropic','gemini'])for(const row of matrix.caps('std')){
    const req=formats.build({model:'fixture',messages:[{role:'user',content:row.prompt}],max_tokens:row.cap,stream:row.stream},{base:'https://relay.test',key:'fixture',requestFormat:format});
    assert.equal(req.preview.max_tokens??req.preview.max_output_tokens??req.preview.generationConfig?.maxOutputTokens,row.cap);
    if(format!=='gemini')assert.equal(req.preview.stream,row.stream);else assert.equal(req.url.includes('streamGenerateContent'),row.stream);
  }
});
test('explicit cache marker is preserved only for Anthropic, not silently dropped by other mappings',()=>{
  const payload={model:'fixture',messages:[{role:'system',content:[{type:'text',text:'archive',cache_control:{type:'ephemeral'}}]},{role:'user',content:'read'}]};
  const result=formats.build(payload,{base:'https://relay.test',key:'fixture',requestFormat:'anthropic'});
  assert.deepEqual(result.preview.system[0].cache_control,{type:'ephemeral'});
  assert.throws(()=>formats.build(payload,{base:'https://relay.test',key:'fixture',requestFormat:'gemini'}),{code:'not_applicable'});
  assert.throws(()=>formats.build(payload,{base:'https://relay.test',key:'fixture',requestFormat:'openai-responses'}),{code:'not_applicable'});
});
test('load stages are bounded and actual latency percentiles do not conceal errors',()=>{
  assert.deepEqual(matrix.load('std',5),[{concurrency:1,requests:2},{concurrency:2,requests:4},{concurrency:5,requests:10}]);
  assert.equal(matrix.load('full',100).at(-1).concurrency,10);
  const stats=matrix.stats([{good:true,status:200,dt:1,ct:10},{good:false,status:429,dt:2,ct:0},{good:false,status:'timeout',dt:5,ct:0}],6);
  assert.equal(stats.requests,3);assert.equal(stats.passed,1);assert.equal(stats.rate_limited,1);assert.equal(stats.p50_ms,2000);assert.equal(stats.p95_ms,5000);assert.deepEqual(stats.errors,{'429':1,timeout:1});
});
