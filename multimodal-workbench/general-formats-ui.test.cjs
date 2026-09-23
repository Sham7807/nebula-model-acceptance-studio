'use strict';
// Offline browser contract: a real local workbench with temporary storage;
// all channel/model requests are intercepted. The real report API is exercised.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..');
const key='offline-general-format-key';
const formats=['openai-chat','openai-responses','anthropic','gemini'];
const labels={'openai-chat':'OpenAI Chat Completions','openai-responses':'OpenAI Responses',anthropic:'Anthropic Messages',gemini:'Gemini'};
const model='fixture-model';
const sse=values=>values.map(value=>'data: '+(typeof value==='string'?value:JSON.stringify(value))+'\n\n').join('');
function nativeResponse(request){
 const url=new URL(request.url),p=request.body;
 const format=url.pathname.includes(':')?'gemini':url.pathname.endsWith('/messages')?'anthropic':url.pathname.endsWith('/responses')?'openai-responses':'openai-chat';
 const text=JSON.stringify(p);const cap=p.max_tokens??p.max_output_tokens??p.generationConfig?.maxOutputTokens;
 const isJson=p.response_format||p.text?.format||p.output_config?.format||p.generationConfig?.responseMimeType;
 const responseModel=p.model||decodeURIComponent(url.pathname.match(/\/models\/([^/:]+):/)?.[1]||model);
 const output=isJson?' {"name":"张三","age":30}':text.includes('秋天')?'秋天是一幅金色的画。':text.includes('ABCD')?'ABCD':'你好，我是 fixture-model。';
 const tool=!!(p.tools?.length)&&!isJson;
 const capped=cap===1||cap===30;
 const input=10,out=capped?cap:5;
 const usage={prompt_tokens:input,completion_tokens:out,total_tokens:input+out,prompt_tokens_details:{cached_tokens:5}};
 let json,stream;
 if(format==='openai-chat'){
  json={id:'chat-fixture',model:responseModel,choices:Array.from({length:p.n||1},(_,index)=>({index,message:{role:'assistant',content:tool?null:output,...(tool?{tool_calls:[{id:'c',type:'function',function:{name:'get_weather',arguments:'{"city":"北京"}'}}]}:{})},finish_reason:capped?'length':tool?'tool_calls':'stop',...(p.logprobs?{logprobs:{content:[]}}:{})})),usage};
  stream=sse([{choices:[{delta:{content:'1'}}]},{choices:[{delta:{content:'2'},finish_reason:'stop'}],usage},'[DONE]']);
 } else if(format==='openai-responses'){
  json={id:'response-fixture',object:'response',model:responseModel,status:capped?'incomplete':'completed',...(capped?{incomplete_details:{reason:'max_output_tokens'}}:{}),output:tool?[{type:'function_call',call_id:'c',name:'get_weather',arguments:'{"city":"北京"}'}]:[{type:'message',content:[{type:'output_text',text:output}]}],usage:{input_tokens:input,output_tokens:out,total_tokens:input+out,input_tokens_details:{cached_tokens:5}}};
  stream=sse([{type:'response.output_text.delta',delta:'1'},{type:'response.output_text.delta',delta:'2'},{type:'response.completed',response:{...json,status:'completed'}}]);
 } else if(format==='anthropic'){
  json={id:'message-fixture',type:'message',model:responseModel,role:'assistant',content:tool?[{type:'tool_use',id:'c',name:'get_weather',input:{city:'北京'}}]:[{type:'text',text:output}],stop_reason:capped?'max_tokens':tool?'tool_use':'end_turn',usage:{input_tokens:5,cache_read_input_tokens:5,output_tokens:out}};
  stream=sse([{type:'message_start',message:{usage:json.usage}},{type:'content_block_delta',index:0,delta:{type:'text_delta',text:'1'}},{type:'content_block_delta',index:0,delta:{type:'text_delta',text:'2'}},{type:'message_delta',delta:{stop_reason:'end_turn'},usage:{output_tokens:out}},{type:'message_stop'}]);
 } else {
  json={modelVersion:responseModel,candidates:Array.from({length:p.generationConfig?.candidateCount||1},(_,index)=>({index,content:{role:'model',parts:tool?[{functionCall:{name:'get_weather',args:{city:'北京'}}}]:[{text:output}]},finishReason:capped?'MAX_TOKENS':'STOP',...(p.generationConfig?.responseLogprobs?{logprobsResult:{topCandidates:[]}}:{})})),usageMetadata:{promptTokenCount:input,candidatesTokenCount:out,totalTokenCount:input+out,cachedContentTokenCount:5}};
  stream=sse([{candidates:[{content:{parts:[{text:'1'}]}}]},{...json,candidates:[{content:{parts:[{text:'2'}]},finishReason:'STOP'}]}]);
 }
 return {format,json,stream};
}
async function launchService(temp){
 const child=spawn(path.join(root,'Kimi-Vendor-Verifier/.venv/bin/python'),['integrations/server.py','--port','0'],{cwd:root,env:{...process.env,WORKBENCH_DB:path.join(temp,'workbench.sqlite3'),WORKBENCH_REPORTS:path.join(temp,'reports'),WORKBENCH_AUTH_FILE:''},stdio:['ignore','pipe','pipe']});
 let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
 const deadline=Date.now()+20000;
 while(Date.now()<deadline){const match=output.match(/http:\/\/127\.0\.0\.1:(\d+)/);if(match)return {child,origin:'http://127.0.0.1:'+match[1]};if(child.exitCode!==null)throw new Error('Local service stopped: '+output);await new Promise(resolve=>setTimeout(resolve,50));}
 child.kill();throw new Error('Service failed to start: '+output);
}
async function fixture(browser,origin,variant){
 const page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true});page.setDefaultTimeout(12000);
 const state={calls:[],models:[],reports:[],errors:[],unexpected:[]};
 page.on('pageerror',error=>state.errors.push(error.message));
 page.on('dialog',async dialog=>{state.errors.push('dialog: '+dialog.message());await dialog.dismiss();});
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(!/^https?:$/.test(url.protocol))return route.continue();
  if(url.origin!==origin){
   if(variant!=='file'||url.origin!=='https://relay.test'){state.unexpected.push(url.href);return route.abort();}
   if(url.pathname.endsWith('/models'))return route.fulfill({contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify({data:[{id:model}]})});
   if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'*'}});
   const call={url:url.href,headers:request.headers(),body:JSON.parse(request.postData())};state.calls.push(call);const response=nativeResponse(call),stream=call.body.stream||url.pathname.includes('streamGenerateContent');
   return route.fulfill({contentType:stream?'text/event-stream':'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:stream?response.stream:JSON.stringify(response.json)});
  }
  if(url.pathname==='/api/models'){state.models.push(JSON.parse(request.postData()));return route.fulfill({contentType:'application/json',body:JSON.stringify({models:[model],total:1})});}
  if(url.pathname==='/api/proxy'){
   const requestBody=JSON.parse(request.postData());assert.ok(request.headers()['x-workbench-token']);const call={...requestBody,body:JSON.parse(requestBody.body)};state.calls.push(call);const response=nativeResponse(call);
   if(requestBody.stream)return route.fulfill({contentType:'text/event-stream',body:response.stream});
   return route.fulfill({contentType:'application/json',body:JSON.stringify({status:200,headers:{'content-type':'application/json'},text:JSON.stringify(response.json)})});
  }
  if(url.pathname==='/api/reports')state.reports.push(JSON.parse(request.postData()));
  if(variant==='bundle'&&url.pathname==='/')return route.fulfill({contentType:'text/html',body:await fs.readFile(path.join(root,'中转站测试工具-多模态版.html'))});
  return route.continue();
 });
 if(variant==='file')await page.goto(pathToFileURL(path.join(__dirname,'legacy.html')).href);
 else{await page.goto(origin+'/');await page.locator('#legacyBtn').click();await page.frameLocator('#legacyFrame').locator('#inFormat').waitFor();}
 const surface=variant==='file'?page:page.frameLocator('#legacyFrame');
 const frame=variant==='file'?page:page.frames().find(frame=>frame!==page.mainFrame());
 return {page,surface,frame,state};
}
async function configure(f,format){
 await f.surface.locator('#inFormat').selectOption(format);await f.surface.locator('#inBase').fill('https://relay.test/prefix/v1');await f.surface.locator('#inKey').fill(key);await f.surface.locator('#inModel').fill(model);
 await f.surface.locator('input[name="mode"][value="quick"]').check();
}
async function runQuick(f,format){
 await configure(f,format);const modelCount=f.state.models.length;
 await f.surface.locator('#loadGeneralModels').click();await f.surface.locator('.model-option[data-model="'+model+'"]').waitFor();await f.frame.evaluate(()=>closeGeneralModelPicker());
 if(f.page.url().startsWith('http')){assert.equal(f.state.models.length,modelCount+1);assert.equal(f.state.models.at(-1).auth,format==='anthropic'?'anthropic':format==='gemini'?'gemini':'bearer');}
 const start=f.state.calls.length;
 await f.surface.locator('#btnRun').click();await f.frame.waitForFunction(()=>!RUNNING&&S.tEnd!==null,{},{timeout:20000});
 const result=await f.frame.evaluate(()=>({total:S.total,checks:S.details,requests:S.requests,logs:S.logs,outcome:S.outcome,config:S.config}));
 assert.ok(result.checks.length>=4,format+' completed checks');assert.ok(Number.isFinite(result.total),format+' has numeric total');assert.ok(result.requests.length>=4);assert.equal(result.config.requestFormat,format);
 assert.ok(!result.logs.some(row=>row.msg.includes('流程中断')),format+' did not abort');
 const calls=f.state.calls.slice(start);assert.ok(calls.length>=3);const first=calls[0];
 if(format==='anthropic'){assert.equal(first.headers['x-api-key'],key);assert.ok(first.body.messages);assert.ok(result.checks.some(check=>check.name==='JSON Mode'&&check.status==='skipped'&&check.applicable===false));}
 if(format==='gemini'){assert.equal(first.headers['x-goog-api-key'],key);assert.ok(first.body.contents);assert.equal(first.body.messages,undefined);}
 if(format==='openai-responses'){assert.ok(first.body.input);assert.equal(first.body.max_tokens,undefined);}
 assert.ok(result.checks.some(check=>check.name.includes('流式')&&check.cls==='ok'),format+' native SSE decoded');
 assert.ok(result.checks.some(check=>check.name.includes('Function Calling')&&check.cls==='ok'),format+' native tool decoded');
 assert.ok(!JSON.stringify(result).includes(key),'saved result redacts API key');
 return result;
}
async function report(f,format,temp,hosted=true){
 const count=f.state.reports.length;const downloadPromise=f.page.waitForEvent('download');await f.surface.locator('#btnDl').click();let download;try{download=await downloadPromise;}catch(error){console.error('REPORT DEBUG',format,{reports:f.state.reports.length,errors:f.state.errors,logs:await f.surface.locator('#log').innerText()});throw error;}
 assert.match(download.suggestedFilename(),/^测试报告-fixture-model-\d{8}-\d{6}\.html$/);
 const filename=path.join(temp,(hosted?'hosted':'offline')+'-'+format+'.html');await download.saveAs(filename);const html=await fs.readFile(filename,'utf8');
 if(hosted){assert.equal(f.state.reports.length,count+1);const payload=f.state.reports.at(-1).records[0].result;assert.equal(payload.config.request_format,format);assert.ok(payload.checks.some(check=>check.request_ids?.length));assert.ok(payload.requests.length);assert.match(html,/id="check-1"/);assert.match(html,/id="request-1"/);assert.ok(html.includes(labels[format]));}
 assert.ok(!html.includes(key));assert.ok(!html.includes('中转站模型通道测试报告'));assert.ok(!html.includes('linear-gradient(135deg,#4f46e5,#7c3aed)'));assert.match(html,/<!doctype html>|<!DOCTYPE html>/);
 return filename;
}

async function runStandard(f,format,temp){
 await configure(f,format);await f.surface.locator('input[name="mode"][value="std"]').check();
 await f.surface.locator('#btnRun').click();await f.frame.waitForFunction(()=>!RUNNING&&S.tEnd!==null,{},{timeout:25000});
 const result=await f.frame.evaluate(()=>({checks:S.details,requests:S.requests,logs:S.logs,total:S.total,mode:S.mode}));
 assert.equal(result.mode,'std');assert.ok(Number.isFinite(result.total));assert.ok(!result.logs.some(row=>row.msg.includes('流程中断')),format+' standard flow must complete');
 for(const pattern of [/视觉输入/,/视频输入/,/max_tokens=1/,/上下文缓存/])assert.ok(result.checks.some(row=>pattern.test(row.name)),format+' covers '+pattern);
 if(['anthropic','openai-responses'].includes(format))assert.ok(result.checks.some(row=>/视频输入/.test(row.name)&&row.status==='skipped'));
 for(const check of result.checks.filter(row=>row.applicable!==false&&!row.local_only))assert.ok(check.request_ids.length,format+' '+check.name+' references requests');
 await report(f,format,temp);const downloaded=f.state.reports.at(-1).records[0].result;
 for(const pattern of [/视觉输入/,/视频输入/,/max_tokens=1/,/上下文缓存/])assert.ok(downloaded.checks.some(row=>pattern.test(row.name)),format+' exports '+pattern);
 console.log('PASS: '+format+' standard flow with '+result.checks.length+' checks / '+result.requests.length+' recorded requests');
}
async function runBatch(f,temp){
 await configure(f,'anthropic');await f.frame.evaluate(()=>{$('inBatch').value='fixture-model,fixture-other';});
 await f.surface.locator('#btnRun').click();await f.frame.waitForFunction(()=>!RUNNING&&S.tEnd!==null,{},{timeout:20000});
 const result=await f.frame.evaluate(()=>({checks:S.details,requests:S.requests,logs:S.logs,batch:S.batch}));
 assert.equal(result.batch.length,2);assert.ok(!result.logs.some(row=>/测试异常|流程中断/.test(row.msg)));
 for(const id of ['fixture-model','fixture-other']){
  const checks=result.checks.filter(check=>check.model===id);assert.ok(checks.length>=7);assert.ok(checks.some(row=>row.name==='JSON Mode'&&row.status==='skipped'));
  for(const check of checks.filter(row=>row.applicable!==false)){
   assert.ok(check.request_ids.length,check.name+' saves request IDs');
   for(const requestId of check.request_ids)assert.equal(result.requests.find(row=>row.id===requestId).model,id,'links retain each model');
  }
 }
 const downloadPromise=f.page.waitForEvent('download');await f.surface.locator('#btnDl').click();const download=await downloadPromise;const filename=path.join(temp,'batch.html');await download.saveAs(filename);const html=await fs.readFile(filename,'utf8');
 assert.ok(html.includes('fixture-model'));assert.ok(html.includes('fixture-other'));assert.ok(html.includes('批量'));assert.ok(!html.includes(key));
 console.log('PASS: two-model Anthropic batch preserves per-model checks, skipped probes and request IDs');
}

(async()=>{
 const temp=await fs.mkdtemp(path.join(os.tmpdir(),'general-formats-ui-'));let service,browser;
 try{
  service=await launchService(temp);browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
  for(const variant of ['source','bundle']){
   const f=await fixture(browser,service.origin,variant);
   try{
    for(const format of formats){await runQuick(f,format);await report(f,format,temp);}
    if(variant==='source'){
      for(const format of formats)await runStandard(f,format,temp);await runBatch(f,temp);
      await configure(f,'anthropic');const accounting=await f.frame.evaluate(async()=>{STOP=false;S.details=[];const result=await tGptGeneration();const identity=await tIdentity();return {result,identity,check:S.details.find(row=>row.id==='gpt_usage_token_accounting'),evaluation:S.gptEvaluation};});
      assert.equal(accounting.result.usageDerived,true);assert.equal(accounting.result.usageConsistent,false);assert.equal(accounting.check.status,'skipped');assert.equal(accounting.check.applicable,false);assert.match(accounting.check.result,/适配器计算/);assert.equal(accounting.evaluation.token_usage.consistent,null);assert.equal(accounting.identity.usageOk,true);assert.equal(accounting.identity.usageDerived,true);
      console.log('PASS: Anthropic derived totals cannot masquerade as independently verified GPT accounting');
    }
    await configure(f,'anthropic');const params=await f.frame.evaluate(async()=>{STOP=false;return await tParams();});assert.equal(params.maxOne,true);assert.equal(params.n2,null);assert.equal(params.pen,null);assert.equal(params.seed,null);assert.equal(params.logprobs,null);
    const vision=await f.frame.evaluate(()=>{const payload={model:cfg().model,messages:[{role:'user',content:[{type:'image_url',image_url:{url:makeContentPng(7)}},{type:'text',text:'read'}]}],max_tokens:100};return GeneralFormat.build(payload,cfg()).preview;});assert.equal(vision.messages[0].content[0].type,'image');
    await configure(f,'gemini');const native=await f.frame.evaluate(()=>GeneralFormat.build({model:cfg().model,max_tokens:300,messages:[{role:'user',content:[{type:'video_url',video_url:{url:'https://video.test/sample.mp4'}},{type:'text',text:'describe'}]}]},cfg()).preview);assert.equal(native.contents[0].parts[0].fileData.mimeType,'video/mp4');
    assert.deepEqual(f.state.unexpected,[]);assert.deepEqual(f.state.errors,[]);
    await runQuick(f,'gemini');await f.page.screenshot({path:path.join(__dirname,'qa-general-formats-'+variant+'.png'),fullPage:true});
    await f.page.setViewportSize({width:390,height:844});assert.ok(await f.frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'general format controls fit mobile');
    console.log('PASS: '+variant+' embedded four-format quick runs, native parameters/media and unified real report API');
   }finally{await f.page.close();}
  }
  const f=await fixture(browser,service.origin,'file');
  try{for(const format of formats)await runQuick(f,format);await report(f,'gemini',temp,false);assert.deepEqual(f.state.errors,[]);assert.deepEqual(f.state.unexpected,[]);console.log('PASS: standalone file four-format quick runs and portable report');}finally{await f.page.close();}
 }finally{if(browser)await browser.close();if(service?.child&&service.child.exitCode===null){const exited=new Promise(resolve=>service.child.once('exit',resolve));service.child.kill('SIGTERM');await exited;}await fs.rm(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
