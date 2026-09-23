'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
test('GPT downloads and history share detailed general records through unified export',async()=>{
  const nodes={},downloads=[],exports=[],saved=[];
  const element=id=>({id,value:'',textContent:'',hidden:false,disabled:false,listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},replaceChildren(){},click(){downloads.push(this.download);},querySelector(){return {srcdoc:''};}});
  const doc={getElementById(id){return nodes[id]||=(element(id));},createElement:element};
  let count=0;
  const root={document:doc,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},AbortController,Blob,setTimeout:()=>0,clearTimeout(){},location:{protocol:'https:'},MediaEngine:{build:async c=>c,request:async()=>{count++;return {status:200,text:'<html><body><svg><path id="pelican-bicycle"/></svg></body></html>',raw:{usage:{prompt_tokens:10,completion_tokens:20,total_tokens:30}},requests:[{id:'observed-one',status:200,response_body:{usage:{prompt_tokens:10,completion_tokens:20,total_tokens:30}}}]};}},WorkbenchReport:{renderExport:async(records,logs)=>{exports.push({records,logs});return '<!doctype html><html>report</html>';}},HistoryCapture:{record:r=>saved.push(r)},ModelDiscovery:{}};
  root.window=root;vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gpt-suite.js'),'utf8'),root);
  doc.getElementById('gptBase').value='https://relay.test';doc.getElementById('gptKey').value='mock-key';doc.getElementById('gptModel').value='gpt-fixture';
  await nodes.gptRun.listeners.click();
  assert.equal(count,1);
  assert.equal(saved.length,1);
  assert.equal(saved[0].result.checks.length,8);
  assert.equal(saved[0].result.requests.length,1);
  await nodes.gptDownload.onclick();
  assert.equal(count,1,'export must never regenerate');
  assert.equal(exports.length,1);
  const r=exports[0].records[0];assert.equal(r.kind,'general');assert.equal(r.result.mode,'gpt');assert.equal(r.result.checks.length,8);
  for(const check of r.result.checks){assert.ok(check.method);assert.ok(check.expected);assert.ok(check.observed!==undefined);assert.ok(check.meaning);assert.ok(check.next_step);assert.equal(check.request_ids[0],'observed-one');}
  const usage=r.result.checks.find(x=>x.id==='gpt-usage');assert.equal(usage.status,'passed');assert.match(usage.method,/不将输入和输出数量相等/);
  assert.equal(r.result.checks.find(x=>x.id==='gpt-animation_detected').status,'inconclusive','missing heuristic cannot alone prove a model failure');
  assert.match(downloads[0],/^测试报告-gpt-fixture-\d{8}-\d{6}\.html$/);
});
