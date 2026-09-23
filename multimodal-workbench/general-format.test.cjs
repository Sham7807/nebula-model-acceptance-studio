'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const F=require('./general-format.js');
const config=(format,extra={})=>({base:'https://relay.test/prefix/v1',key:'offline-key',requestFormat:format,...extra});
const payload=(extra={})=>({model:'test-model',messages:[{role:'user',content:'hello'}],max_tokens:100,...extra});
const body=(format,extra={},cfg={})=>F.build(payload(extra),config(format,cfg)).preview;
const tool={type:'function',function:{name:'Calculator',description:'Calculate',parameters:{type:'object',properties:{expr:{type:'string'}},required:['expr']}}};

test('Chat requests stay exact and use the selected auth',()=>{
 const input=payload({tools:[tool],tool_choice:'required',stream:false,n:2});
 const request=F.build(input,config('openai-chat'));
 assert.deepEqual(request.preview,input);assert.deepEqual(JSON.parse(request.body),input);
 assert.equal(request.url,'https://relay.test/prefix/v1/chat/completions');
 assert.equal(request.headers.Authorization,'Bearer offline-key');
 assert.equal(request.format,'openai-chat');assert.deepEqual(request.notApplicable,[]);
 assert.deepEqual(input.tools,[tool],'adapter must not mutate test fixture');
});

test('format profiles join root, explicit API versions and complete API endpoints',()=>{
 for(const [base,format,url] of [
 ['https://relay.test','openai-chat','https://relay.test/v1/chat/completions'],
 ['https://relay.test/api/v1','openai-responses','https://relay.test/api/v1/responses'],
 ['https://relay.test/prefix/v2','openai-chat','https://relay.test/prefix/v2/chat/completions'],
 ['https://relay.test/v1beta/openai','openai-chat','https://relay.test/v1beta/openai/chat/completions'],
 ['https://relay.test/v1/messages','anthropic','https://relay.test/v1/messages'],
 ['https://relay.test','gemini','https://relay.test/v1beta/models/test-model:generateContent'],
 ['https://relay.test/prefix/v1','gemini','https://relay.test/prefix/v1/models/test-model:generateContent'],
 ['https://relay.test/v1beta/models/old:generateContent','gemini','https://relay.test/v1beta/models/test-model:generateContent']])assert.equal(F.build(payload(),config(format,{base})).url,url);
 assert.equal(F.build(payload({model:'vendor/model'}),config('gemini',{base:'https://relay.test'})).url,'https://relay.test/v1beta/models/vendor%2Fmodel:generateContent');
});

test('custom paths are same origin and secrets are kept outside URLs',()=>{
 assert.equal(F.build(payload(),config('openai-chat',{path:'https://relay.test/custom/{model}?mode=test'})).url,'https://relay.test/custom/test-model?mode=test');
 for(const path of ['https://other.test/run','//other.test/run','http://relay.test/run','/v1/run?key=secret','/v1/run#fragment'])assert.throws(()=>F.build(payload(),config('openai-chat',{path})),/同源|密钥|片段/);
 assert.throws(()=>F.build(payload(),config('openai-chat',{base:'https://user:password@relay.test'})),/账号/);
 assert.throws(()=>F.build(payload(),config('openai-chat',{key:'bad\r\nHeader:value'})),/换行/);
});

test('native authentication is separate from the request format',()=>{
 const anthropic=F.build(payload(),config('anthropic'));assert.equal(anthropic.headers['x-api-key'],'offline-key');assert.equal(anthropic.headers['anthropic-version'],'2023-06-01');assert.equal(anthropic.headers.Authorization,undefined);
 const bearer=F.build(payload(),config('anthropic',{auth:'bearer'}));assert.equal(bearer.headers.Authorization,'Bearer offline-key');assert.equal(bearer.headers['anthropic-version'],'2023-06-01');
 const gemini=F.build(payload(),config('gemini'));assert.equal(gemini.headers['x-goog-api-key'],'offline-key');
 assert.equal(F.build(payload(),config('openai-chat',{auth:'none',key:''})).headers.Authorization,undefined);
});

test('Responses converts input, cap, structured output and tool selection',()=>{
 const result=body('openai-responses',{messages:[{role:'system',content:'policy'},{role:'user',content:'hello'}],tools:[tool],tool_choice:{type:'function',function:{name:'Calculator'}},response_format:{type:'json_schema',json_schema:{name:'answer',strict:true,schema:{type:'object'}}},stream:true,stream_options:{include_usage:true}});
 assert.deepEqual(result.input,[{role:'system',content:[{type:'input_text',text:'policy'}]},{role:'user',content:[{type:'input_text',text:'hello'}]}]);
 assert.equal(result.max_output_tokens,100);assert.equal(result.messages,undefined);assert.equal(result.max_tokens,undefined);assert.equal(result.stream_options,undefined);
 assert.deepEqual(result.tools,[{type:'function',name:'Calculator',description:'Calculate',parameters:tool.function.parameters}]);
 assert.deepEqual(result.tool_choice,{type:'function',name:'Calculator'});assert.deepEqual(result.text.format,{type:'json_schema',name:'answer',strict:true,schema:{type:'object'}});
});

test('Anthropic separates system, maps stops/schema and preserves max_tokens=1',()=>{
 const result=body('anthropic',{max_tokens:1,messages:[{role:'system',content:'policy'},{role:'user',content:'hello'}],tools:[tool],tool_choice:'required',stop:['END'],response_format:{type:'json_schema',json_schema:{name:'answer',schema:{type:'object'}}},stream_options:{include_usage:true}});
 assert.deepEqual(result.system,[{type:'text',text:'policy'}]);assert.deepEqual(result.messages,[{role:'user',content:[{type:'text',text:'hello'}]}]);
 assert.equal(result.max_tokens,1);assert.equal(result.stream_options,undefined);assert.deepEqual(result.stop_sequences,['END']);assert.deepEqual(result.tool_choice,{type:'any'});
 assert.deepEqual(result.tools,[{name:'Calculator',description:'Calculate',input_schema:tool.function.parameters}]);
 assert.deepEqual(result.output_config,{format:{type:'json_schema',schema:{type:'object'}}});
});

test('Gemini maps native roles, tool schema and generation fields',()=>{
 const result=body('gemini',{messages:[{role:'system',content:'policy'},{role:'user',content:'hello'},{role:'assistant',content:'hi'}],max_tokens:1,n:2,temperature:0,top_p:.5,seed:42,logprobs:true,top_logprobs:3,stop:['END'],tools:[tool],tool_choice:'required',response_format:{type:'json_object'},stream:true,stream_options:{include_usage:true}});
 assert.deepEqual(result.systemInstruction,{parts:[{text:'policy'}]});assert.deepEqual(result.contents,[{role:'user',parts:[{text:'hello'}]},{role:'model',parts:[{text:'hi'}]}]);
 assert.deepEqual(result.generationConfig,{maxOutputTokens:1,candidateCount:2,temperature:0,topP:.5,seed:42,responseLogprobs:true,logprobs:3,stopSequences:['END'],responseMimeType:'application/json'});
 assert.deepEqual(result.tools,[{functionDeclarations:[{name:'Calculator',description:'Calculate',parametersJsonSchema:tool.function.parameters}]}]);
 assert.deepEqual(result.toolConfig,{functionCallingConfig:{mode:'ANY'}});assert.equal(result.model,undefined);assert.equal(result.stream,undefined);
 const request=F.build(payload({stream:true}),config('gemini',{base:'https://relay.test'}));assert.equal(request.url,'https://relay.test/v1beta/models/test-model:streamGenerateContent?alt=sse');assert.equal(request.headers.Accept,'text/event-stream');
});

test('all native adapters preserve assistant tool results',()=>{
 const messages=[{role:'user',content:'calculate'},{role:'assistant',content:null,tool_calls:[{id:'call-1',type:'function',function:{name:'Calculator',arguments:'{"expr":"23*47"}'}}]},{role:'tool',tool_call_id:'call-1',content:'{"result":1081}'}];
 const response=body('openai-responses',{messages});assert.equal(response.input[1].type,'function_call');assert.deepEqual(response.input[2],{type:'function_call_output',call_id:'call-1',output:'{"result":1081}'});
 const anthropic=body('anthropic',{messages});assert.equal(anthropic.messages[1].content[0].type,'tool_use');assert.equal(anthropic.messages[2].content[0].tool_use_id,'call-1');
 const gemini=body('gemini',{messages});assert.deepEqual(gemini.contents[1].parts,[{functionCall:{name:'Calculator',args:{expr:'23*47'}}}]);assert.deepEqual(gemini.contents[2].parts,[{functionResponse:{name:'Calculator',response:{result:1081}}}]);
});

test('vision adapters transform both data images and public image URLs',()=>{
 const messages=[{role:'user',content:[{type:'image_url',image_url:{url:'data:image/png;base64,AAAA'}},{type:'image_url',image_url:{url:'https://image.test/a.jpg'}},{type:'text',text:'what color?'}]}];
 const anthropic=body('anthropic',{messages});assert.deepEqual(anthropic.messages[0].content[0],{type:'image',source:{type:'base64',media_type:'image/png',data:'AAAA'}});assert.equal(anthropic.messages[0].content[1].source.type,'url');
 const responses=body('openai-responses',{messages});assert.equal(responses.input[0].content[0].type,'input_image');
 const gemini=body('gemini',{messages});assert.deepEqual(gemini.contents[0].parts[0],{inlineData:{mimeType:'image/png',data:'AAAA'}});assert.equal(gemini.contents[0].parts[1].fileData.fileUri,'https://image.test/a.jpg');
});

test('video maps only to Gemini and otherwise gives explicit not_applicable',()=>{
 const messages=[{role:'user',content:[{type:'video_url',video_url:{url:'https://video.test/a.mp4'}},{type:'text',text:'describe'}]}];
 assert.deepEqual(body('gemini',{messages}).contents[0].parts[0],{fileData:{mimeType:'video/mp4',fileUri:'https://video.test/a.mp4'}});
 for(const format of ['anthropic','openai-responses'])assert.throws(()=>body(format,{messages}),error=>error.code==='not_applicable'&&error.fields.includes('messages[0].content[0]'));
});

test('unmapped parameters and dynamic tools are never silently passed',()=>{
 for(const [format,extra,field]of [['anthropic',{n:2},'n'],['anthropic',{response_format:{type:'json_object'}},'response_format'],['openai-responses',{stop:['END']},'stop'],['gemini',{parallel_tool_calls:true},'parallel_tool_calls']])assert.throws(()=>body(format,extra),error=>error.code==='not_applicable'&&error.fields.includes(field));
 for(const format of ['anthropic','openai-responses','gemini'])assert.throws(()=>body(format,{messages:[{role:'system',tools:[tool]}]}),error=>error.code==='not_applicable'&&error.fields.includes('messages[0].tools'));
 const request=F.build(payload({n:2}),config('anthropic',{allowUnsupported:true}));assert.equal(request.preview.n,undefined);assert.equal(request.notApplicable[0].field,'n');
 assert.throws(()=>body('anthropic',{stream_options:{unknown:true}}),F.NotApplicableError);
});

test('Responses normalization retains raw evidence and usage fields',()=>{
 const native={id:'r1',object:'response',model:'actual-model',status:'completed',output:[{type:'message',content:[{type:'output_text',text:'hello'}]},{type:'function_call',call_id:'c1',name:'Calculator',arguments:'{"expr":"1+1"}'}],usage:{input_tokens:11,output_tokens:4,total_tokens:15,input_tokens_details:{cached_tokens:5}}};
 const result=F.normalize(native,'openai-responses');assert.equal(result.choices[0].message.content,'hello');assert.equal(result.choices[0].message.tool_calls[0].function.name,'Calculator');assert.equal(result.choices[0].finish_reason,'tool_calls');assert.equal(result.usage.prompt_tokens,11);assert.equal(result.usage.prompt_tokens_details.cached_tokens,5);assert.deepEqual(result.output,native.output);assert.equal(native.choices,undefined);
 assert.equal(F.normalize({...native,status:'incomplete',incomplete_details:{reason:'max_output_tokens'}},'openai-responses').choices[0].finish_reason,'length');
});

test('Anthropic normalizes cache-inclusive input and labels calculated totals',()=>{
 const result=F.normalize({content:[{type:'text',text:'hello'},{type:'thinking',thinking:'private'},{type:'tool_use',id:'c',name:'Calculator',input:{expr:'2+2'}}],stop_reason:'tool_use',usage:{input_tokens:11,cache_read_input_tokens:20,cache_creation_input_tokens:30,output_tokens:4}},'anthropic');
 assert.equal(result.choices[0].message.content,'hello');assert.equal(result.choices[0].message.reasoning_content,'private');assert.equal(result.usage.prompt_tokens,61);assert.equal(result.usage.completion_tokens,4);assert.equal(result.usage.total_tokens,65);assert.equal(result.usage.total_tokens_derived,true);assert.equal(result.usage.prompt_tokens_details.cached_tokens,20);assert.equal(result.usage.raw_usage.input_tokens,11);
});

test('Gemini normalizes multiple candidates, tool calls and thinking tokens',()=>{
 const result=F.normalize({modelVersion:'actual-model',candidates:[{index:0,content:{parts:[{thought:true,text:'thought'},{text:'answer'},{functionCall:{name:'Calculator',args:{expr:'2+2'}}}]},finishReason:'STOP'},{index:1,content:{parts:[{text:'other'}]},finishReason:'MAX_TOKENS'}],usageMetadata:{promptTokenCount:10,candidatesTokenCount:2,thoughtsTokenCount:3,totalTokenCount:15,cachedContentTokenCount:7}},'gemini');
 assert.equal(result.choices.length,2);assert.equal(result.choices[0].message.content,'answer');assert.equal(result.choices[0].message.reasoning_content,'thought');assert.equal(result.choices[0].message.tool_calls[0].function.arguments,'{"expr":"2+2"}');assert.equal(result.choices[1].finish_reason,'length');assert.equal(result.usage.completion_tokens,5);assert.equal(result.usage.total_tokens,15);assert.equal(result.usage.prompt_tokens_details.cached_tokens,7);assert.equal(result.model,'actual-model');
});

test('missing native usage stays missing and malformed payloads stay detectable',()=>{
 for(const format of ['anthropic','gemini','openai-responses']){
  const result=F.normalize({unexpected:'shape'},format);assert.deepEqual(result.choices,[]);assert.equal(result.usage,undefined);assert.equal(result.model,undefined);
  assert.deepEqual(F.normalize({error:{message:'denied'}},format),{error:{message:'denied'}});
 }
 assert.equal(F.normalizedUsage({input_tokens:10},'openai-responses').completion_tokens,undefined);
 assert.equal(F.normalizedUsage({input_tokens:10},'anthropic').total_tokens,undefined);
});

test('SSE parser handles partial chunks, CRLF, complete events and DONE',()=>{
 const parser=F.createStreamParser('openai-chat');assert.deepEqual(parser.push('data: {"choices":[{"delta":{"content":"he'),[]);
 const events=parser.push('llo"}}]}\r\n\r\ndata: [DONE]\r\n\r\n');assert.equal(events.length,2);assert.equal(events[0].text,'hello');assert.equal(events[1].done,true);assert.deepEqual(parser.finish(),[]);
 assert.equal(F.parseSSE('data: {"choices":[{"delta":{"content":"tail"}}]}','openai-chat')[0].text,'tail');
});

test('SSE native deltas expose normalized text, tool arguments and usage',()=>{
 const anth=F.streamEvent({type:'content_block_delta',index:1,delta:{type:'text_delta',text:'hello'}},'anthropic');assert.equal(anth.text,'hello');
 const toolDelta=F.streamEvent({type:'content_block_delta',index:1,delta:{type:'input_json_delta',partial_json:'{"expr":'}},'anthropic');assert.equal(toolDelta.toolCalls[0].function.arguments,'{"expr":');
 assert.equal(F.streamEvent({type:'message_stop'},'anthropic').done,true);
 const resp=F.streamEvent({type:'response.output_text.delta',delta:'response'},'openai-responses');assert.equal(resp.text,'response');
 const completed=F.streamEvent({type:'response.completed',response:{status:'completed',output:[],usage:{input_tokens:1,output_tokens:2,total_tokens:3}}},'openai-responses');assert.equal(completed.done,true);assert.equal(completed.usage.total_tokens,3);
 const gem=F.streamEvent({candidates:[{content:{parts:[{text:'gemini'}]},finishReason:'STOP'}],usageMetadata:{promptTokenCount:1,candidatesTokenCount:2,totalTokenCount:3}},'gemini');assert.equal(gem.text,'gemini');assert.equal(gem.done,true);assert.equal(gem.usage.prompt_tokens,1);
});

test('SSE failures and invalid JSON are not accepted as text success',()=>{
 assert.equal(F.streamEvent('not-json','anthropic').malformed,true);
 assert.equal(F.streamEvent({type:'error',error:{message:'overloaded'}},'anthropic').error.message,'overloaded');
 assert.equal(F.streamEvent({type:'response.failed',response:{error:{message:'failed'}}},'openai-responses').error.message,'failed');
});

test('nested fields without an equivalent never disappear silently',()=>{
 for(const [format,extra,field]of [
  ['gemini',{tools:[{type:'function',function:{...tool.function,strict:true}}]},'tools[0].function.strict'],
  ['anthropic',{messages:[{role:'user',name:'named-user',content:'hello'}]},'messages[0].name'],
  ['gemini',{messages:[{role:'user',content:[{type:'image_url',image_url:{url:'https://image.test/a.png',detail:'high'}}]}]},'messages[0].content[0].detail'],
  ['openai-responses',{max_tokens:100,max_completion_tokens:200},'max_tokens / max_completion_tokens']
 ])assert.throws(()=>body(format,extra),error=>error.code==='not_applicable'&&error.fields.includes(field));
 for(const format of ['anthropic','gemini','openai-responses'])assert.doesNotThrow(()=>F.normalize({content:{invalid:true},candidates:{invalid:true},output:{invalid:true}},format));
});
