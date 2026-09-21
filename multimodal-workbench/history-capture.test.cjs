'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=__dirname,files=name=>fs.readFileSync(path.join(root,name),'utf8');
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8qkAAAAASUVORK5CYII=';
(async()=>{
const browser=await chromium.launch({...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}),headless:true});
const errors=[],unexpected=[],saved=[],modelCalls=[];let failSave=false,historyEnabled=true,failSession=false;
const page=await browser.newPage({viewport:{width:1400,height:1100}});page.on('pageerror',e=>errors.push(e.message));
await page.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url());
 if(url.hostname==='history.test'){
  if(url.pathname==='/api/session')return route.fulfill({status:failSession?503:200,contentType:'application/json',body:JSON.stringify(failSession?{error:'暂时离线'}:{token:'history-test-csrf',history_enabled:historyEnabled})});
  if(url.pathname==='/api/models'){
   assert.equal(request.headers()['x-workbench-token'],'history-test-csrf');
   assert.match(JSON.parse(request.postData()).base,/^https:\/\/relay\.test(?:\/v1)?$/);
   return route.fulfill({contentType:'application/json',body:JSON.stringify({models:['text-test'],total:1})});
  }
  if(url.pathname==='/api/history'&&request.method()==='POST'){
   assert.equal(request.headers()['x-workbench-token'],'history-test-csrf');const payload=JSON.parse(request.postData());saved.push(payload);
   return route.fulfill({status:failSave?503:200,contentType:'application/json',body:JSON.stringify(failSave?{error:'稍后重试'}:{id:payload.client_id})});
  }
  if(url.pathname==='/'){
   let html=files('index.html');if(!html.includes('src="history-capture.js"'))html=html.replace('<script src="app.js">','<script src="history-capture.js"></script><script src="app.js">');
   return route.fulfill({contentType:'text/html',body:html});
  }
  const file=url.pathname.slice(1);if(!file.includes('/')&&fs.existsSync(path.join(root,file)))return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html',body:files(file)});
  return route.fulfill({status:404,body:'missing'});
 }
 if(url.hostname==='relay.test'){
  const headers={'access-control-allow-origin':'*','access-control-allow-headers':'*','content-type':'application/json'};
  if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
  modelCalls.push({path:url.pathname,method:request.method()});
  if(url.pathname==='/v1/models')return route.fulfill({headers,body:JSON.stringify({data:[{id:'text-test'}]})});
  if(url.pathname==='/v1/failure')return route.fulfill({status:400,headers,body:JSON.stringify({error:{message:'test-secret-api-key is invalid'}})});
  if(url.pathname==='/v1/slow'){await new Promise(r=>setTimeout(r,900));return route.fulfill({headers,body:JSON.stringify({choices:[{message:{content:'done'}}]})}).catch(()=>{});}
  if(url.pathname==='/v1/chat/completions'){
   const payload=JSON.parse(request.postData());
   if(payload.stream)return route.fulfill({headers:{...headers,'content-type':'text/event-stream'},body:'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'});
   return route.fulfill({headers,body:JSON.stringify({choices:[{message:{content:'test-secret-api-key 391'},finish_reason:'stop'}],usage:{prompt_tokens:5,completion_tokens:5}})});
  }
  if(url.pathname==='/v1/images/generations')return route.fulfill({headers,body:JSON.stringify({data:[{b64_json:png}]})});
  if(url.pathname==='/v1/audio/speech')return route.fulfill({headers:{...headers,'content-type':'audio/wav'},body:Buffer.from('RIFF'+'.'.repeat(80))});
  return route.fulfill({status:400,headers,body:'{"error":{"message":"unsupported"}}'});
 }
 unexpected.push(request.url());return route.abort();
});
await page.goto('http://history.test/');
await page.locator('#base').fill('https://relay.test/v1');await page.locator('#key').fill('test-secret-api-key');await page.locator('#model').fill('text-test');
await page.locator('#loadModels').click();await page.waitForFunction(()=>document.getElementById('modelHint').textContent.includes('1'));
await page.locator('#previewBtn').click();await page.locator('#closePreview').click();assert.equal(saved.length,0,'list/preview do not create history');
async function resultCount(n){await page.waitForFunction(count=>document.querySelectorAll('.result-card').length===count&&!document.getElementById('runBtn').disabled,n);}
async function saveCount(n){await page.waitForFunction(count=>document.querySelectorAll('.history-save-status.is-saved').length===count,n);}
await page.locator('#runBtn').click();await resultCount(1);await saveCount(1);assert.equal(saved[0].kind,'text');assert.equal(saved[0].status,'passed');assert.ok(!JSON.stringify(saved[0]).includes('test-secret-api-key'));assert.ok(saved[0].prompt.includes('17'));
await page.locator('[data-kind=image]').click();await page.locator('#model').fill('image-test');await page.locator('#runBtn').click();await resultCount(2);await saveCount(2);assert.equal(saved[1].media[0].b64,png);assert.equal(saved[1].media[0].type,'image');
await page.locator('[data-kind=audio]').click();await page.locator('#model').fill('tts-test');await page.locator('#runBtn').click();await resultCount(3);await saveCount(3);assert.equal(saved[2].media[0].type,'audio');assert.ok(saved[2].media[0].b64);
await page.locator('[data-kind=text]').click();await page.locator('.advanced summary').click();await page.locator('#path').fill('/v1/failure');await page.locator('#runBtn').click();await resultCount(4);await saveCount(4);assert.equal(saved[3].status,'failed');assert.ok(!JSON.stringify(saved[3]).includes('test-secret-api-key'));
await page.locator('#path').fill('/v1/slow');await page.locator('#runBtn').click();await page.waitForFunction(()=>document.getElementById('runBtn').disabled);await page.locator('#stopBtn').click();await resultCount(5);await saveCount(5);assert.equal(saved[4].status,'cancelled');
failSave=true;await page.locator('#path').fill('/v1/chat/completions');await page.locator('#runBtn').click();await resultCount(6);await page.locator('.history-save-status.is-error button').waitFor();const beforeRetry=modelCalls.length;failSave=false;await page.locator('.history-save-status.is-error button').click();await saveCount(6);assert.equal(modelCalls.length,beforeRetry,'retry must not run model again');assert.equal(saved[5].client_id,saved[6].client_id);
// Stored video result advances using one identity; no network media download is attempted.
await page.evaluate(async()=>{
 const config={base:'https://relay.test/v1',model:'video-test',preset:'relay-video-json',key:'test-secret-api-key',prompt:'video prompt'};
 const pending={id:'pending-example',kind:'video',status:'pending',model:'video-test',preset:'video',config:{base:config.base},createdAt:new Date().toISOString(),media:[],taskId:'vid-1',elapsedMs:12,requests:[]};
 await saveBasicHistory(pending,config,Date.now());
 const done={...pending,id:'complete-example',status:'success',media:[{kind:'video',url:'https://cdn.invalid/video.mp4'}]};await saveBasicHistory(done,config,Date.now(),'vid-1');
});
const videos=saved.filter(r=>r.kind==='video');assert.equal(videos.at(-1).status,'passed');assert.equal(new Set(videos.map(r=>r.client_id)).size,1);assert.equal(videos.at(-1).media[0].url,'https://cdn.invalid/video.mp4');
// Oversized/active media are omitted explicitly; remote links are retained without GET.
const prepared=await page.evaluate(async()=>{
 const blob=URL.createObjectURL(new Blob([new Uint8Array(16*1024*1024+1)],{type:'image/png'}));
 const output=await HistoryCapture.prepare({client_id:'limit',kind:'image',result:{},media:[{type:'image',url:blob},{type:'image',url:'data:image/svg+xml;base64,PHN2Zz4='},{type:'image',url:'https://cdn.invalid/test.png?key=test-secret-api-key'}]},'test-secret-api-key');URL.revokeObjectURL(blob);return output;
});assert.equal(prepared.payload.media.length,0);assert.equal(prepared.notes.length,3);
// iframe cannot be impersonated by an unrelated message source.
await page.evaluate(()=>window.postMessage({type:'workbench:general-history',record:{client_id:'forged',kind:'general',source:'general'}},location.origin));await page.waitForTimeout(30);assert.ok(!saved.some(r=>r.client_id==='forged'));
await page.locator('#legacyBtn').click();const frame=page.frameLocator('#legacyFrame');await frame.locator('#inBase').fill('https://relay.test');await frame.locator('#inKey').fill('test-secret-api-key');await frame.locator('#inModel').fill('text-test');await frame.locator('input[value=quick]').check();await frame.locator('#btnRun').click();await page.locator('#generalHistorySaveStatus.is-saved').waitFor({timeout:20000});const general=saved.at(-1);assert.equal(general.kind,'general');assert.ok(general.result.checks.length);assert.ok(general.result.requests.length);assert.ok(!JSON.stringify(general).includes('test-secret-api-key'));
// Clearing results while a blob is being serialized must not revoke it too early.
await page.evaluate(async()=>{
 const blob=URL.createObjectURL(new Blob(['media-content'],{type:'audio/wav'}));const save=HistoryCapture.record({client_id:'blob-clear',kind:'audio',source:'basic',status:'passed',result:{},media:[{type:'audio',url:blob}]});HistoryCapture.releaseMedia(blob);await save;
});assert.equal(saved.at(-1).client_id,'blob-clear');assert.equal(saved.at(-1).media[0].b64,Buffer.from('media-content').toString('base64'));
// Cancellation of a general run preserves the request begun before interruption.
await frame.locator('#btnRun').evaluate(button=>{button.click();document.getElementById('btnStop').click();});await page.waitForFunction(()=>document.getElementById('generalHistorySaveStatus')?.classList.contains('is-saved'));
await page.waitForTimeout(30);assert.equal(saved.at(-1).status,'cancelled');assert.equal(saved.at(-1).kind,'general');
assert.deepEqual(unexpected,[],'no media fetch / external requests');assert.deepEqual(errors,[]);
// Old services without history_enabled never receive new POSTs.
const old=await browser.newPage();await old.route('**/*',route=>route.fulfill({contentType:'application/json',body:'{"token":"old"}'}));await old.goto('http://old.test/');await old.addScriptTag({content:files('history-capture.js')});let oldPosts=0;old.on('request',r=>{if(r.method()==='POST')oldPosts++;});await old.evaluate(()=>HistoryCapture.record({client_id:'old',kind:'text',result:{},media:[]}));assert.equal(oldPosts,0);
// Session failure leaves retry affordance, and retry saves without invoking the model.
const retryPage=await browser.newPage();let sessionFail=true,retryPosts=0;
await retryPage.route('**/*',route=>{const url=new URL(route.request().url());if(url.pathname==='/api/session')return route.fulfill({status:sessionFail?503:200,contentType:'application/json',body:JSON.stringify(sessionFail?{error:'offline'}:{token:'retry',history_enabled:true})});if(url.pathname==='/api/history'){retryPosts++;return route.fulfill({contentType:'application/json',body:'{"id":"retry-history"}'});}return route.fulfill({contentType:'text/html',body:'<div id="state"></div>'});});
await retryPage.goto('http://retry.test/');await retryPage.addScriptTag({content:files('history-capture.js')});await retryPage.evaluate(()=>HistoryCapture.record({client_id:'retry',kind:'text',result:{},media:[]},{container:document.getElementById('state')}));await retryPage.locator('#state.is-error button').waitFor();assert.equal(retryPosts,0);sessionFail=false;await retryPage.locator('#state button').click();await retryPage.locator('#state.is-saved').waitFor();assert.equal(retryPosts,1);
// Acceptance jobs are saved by the service: retry only the persistence endpoint.
const acceptancePage=await browser.newPage();let acceptanceSaved=false,acceptanceRetries=0,unexpectedAcceptancePosts=0;
await acceptancePage.route('**/*',route=>{
 const request=route.request(),url=new URL(request.url());
 if(url.pathname==='/api/session')return route.fulfill({contentType:'application/json',body:JSON.stringify({token:'acceptance',latest:'history-acceptance',history_enabled:true})});
 if(url.pathname==='/api/runs/history-acceptance/history'){acceptanceSaved=true;acceptanceRetries++;return route.fulfill({contentType:'application/json',body:'{"history_saved":true,"history_id":"stored-acceptance"}'});}
 if(url.pathname==='/api/runs/history-acceptance')return route.fulfill({contentType:'application/json',body:JSON.stringify({id:'history-acceptance',suite:'ccmax',status:'completed',model:'claude-test',base:'https://relay.test/v1',history_saved:acceptanceSaved,history_id:acceptanceSaved?'stored-acceptance':undefined,history_error:acceptanceSaved?undefined:'offline',result:{summary:{passed:1},checks:[]}})});
 if(request.method()==='POST')unexpectedAcceptancePosts++;
 const asset=url.pathname==='/'?'index.html':url.pathname.slice(1);if(!asset.includes('/')&&fs.existsSync(path.join(root,asset)))return route.fulfill({contentType:asset.endsWith('.js')?'application/javascript':asset.endsWith('.css')?'text/css':'text/html',body:files(asset)});return route.fulfill({status:404,contentType:'application/json',body:'{}'});
});
await acceptancePage.goto('http://acceptance.test/');await acceptancePage.locator('#acceptanceHistorySaveStatus.is-error button').waitFor();await acceptancePage.locator('#acceptanceHistorySaveStatus button').click();await acceptancePage.locator('#acceptanceHistorySaveStatus.is-saved').waitFor();assert.equal(acceptanceRetries,1);assert.equal(unexpectedAcceptancePosts,0);
// File mode never attempts a history network request.
const file=await browser.newPage();await file.goto('file://'+path.join(root,'legacy.html'));await file.addScriptTag({content:files('history-capture.js')});let fileCalls=0;file.on('request',()=>fileCalls++);const fileState=await file.evaluate(async()=>{const el=document.createElement('div');document.body.append(el);await HistoryCapture.record({client_id:'local',kind:'text',result:{},media:[]},{container:el});return el.textContent;});assert.equal(fileCalls,0);assert.ok(fileState.includes('当前页面'));
await browser.close();console.log('PASS: 17 capture groups; no real model requests; '+saved.length+' mocked history saves');
})().catch(error=>{console.error(error);process.exitCode=1;});
