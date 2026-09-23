/* Native request/response adapters for the general acceptance detector. */
(function (root) {
  'use strict';
  const profiles = [
    {id:'openai-chat',label:'OpenAI Chat Completions',path:'/v1/chat/completions',auth:'bearer'},
    {id:'openai-responses',label:'OpenAI Responses',path:'/v1/responses',auth:'bearer'},
    {id:'anthropic',label:'Anthropic Messages',path:'/v1/messages',auth:'anthropic'},
    {id:'gemini',label:'Gemini GenerateContent',path:'/v1beta/models/{model}:generateContent',auth:'gemini'}
  ];
  const aliases = {openai:'openai-chat',chat:'openai-chat',responses:'openai-responses','anthropic-messages':'anthropic','gemini-generateContent':'gemini'};
  const own = (object,key) => Object.prototype.hasOwnProperty.call(object||{},key);
  const clone = value => value===undefined?undefined:JSON.parse(JSON.stringify(value));
  function profile(value) {
    const id=typeof value==='object' ? value?.requestFormat||value?.format||value?.preset||'openai-chat' : value||'openai-chat';
    const result=profiles.find(item=>item.id===(aliases[id]||id));
    if(!result)throw new Error('不支持的通用检测请求格式：'+id);
    return result;
  }
  class NotApplicableError extends Error {
    constructor(format,items) {
      super(profile(format).label+' 不适用：'+items.map(item=>item.field+'（'+item.reason+'）').join('；'));
      this.name='NotApplicableError';this.code='not_applicable';this.format=profile(format).id;this.notApplicable=items;this.fields=items.map(item=>item.field);
    }
  }
  function baseUrl(value) {
    let url;try{url=new URL(String(value||'').trim());}catch{throw new Error('渠道地址必须是完整的 HTTP / HTTPS URL。');}
    if(!/^https?:$/.test(url.protocol)||url.username||url.password||url.search||url.hash||/[\u0000-\u0020\\]/.test(String(value)))throw new Error('渠道地址不能包含账号、查询参数、片段或空白字符。');
    return url;
  }
  function endpoint(base,path,model,stream=false,format='openai-chat') {
    const b=baseUrl(base),p=profile(format);
    let requested=String(path||p.path).trim().replace(/\{model\}/g,encodeURIComponent(String(model||'').replace(/^models\//,'')));
    if(/[\u0000-\u0020\\]/.test(requested))throw new Error('接口路径含无效字符。');
    if(p.id==='gemini'&&stream)requested=requested.replace(/:generateContent(?=\?|$)/,':streamGenerateContent');
    let url;
    if(/^[a-z][a-z\d+.-]*:/i.test(requested)||requested.startsWith('//'))url=new URL(requested,b);
    else {
      let prefix=b.pathname.replace(/\/+$/,'').replace(/\/(?:chat\/completions|responses|messages|completions)$/,'').replace(/\/models\/[^/]+:(?:streamGenerateContent|generateContent)$/,'');
      let suffix='/'+requested.replace(/^\/+/,''),version=/\/(v\d+(?:beta\d*)?)(\/openai)?$/i.exec(prefix);
      // A versioned Base URL is explicit. Keep /v2 and /v1beta/openai,
      // rather than silently redirecting tests to another API version.
      if(version&&(!path||path===p.path))suffix=suffix.replace(/^\/v\d+(?:beta\d*)?(?=\/|$)/i,'');
      let tail=suffix;const parts=prefix.split('/').filter(Boolean);
      for(let n=parts.length;n>0;n--){const overlap='/'+parts.slice(-n).join('/');if(suffix===overlap||suffix.startsWith(overlap+'/')){tail=suffix.slice(overlap.length);break;}}
      url=new URL(b.origin+prefix+tail);
    }
    if(url.origin!==b.origin||url.username||url.password||!/^https?:$/.test(url.protocol))throw new Error('接口路径必须与渠道地址同源，避免密钥外泄。');
    if(url.hash)throw new Error('接口路径不能包含片段。');
    for(const key of url.searchParams.keys())if(/^(?:key|api[_-]?key|token|access_token|secret|password)$/i.test(key))throw new Error('请通过鉴权字段发送密钥，不要写入接口 URL。');
    if(p.id==='gemini'&&stream)url.searchParams.set('alt','sse');
    return url.href;
  }
  function authHeaders(config,p=profile(config)) {
    const auth=config.auth||p.auth,key=String(config.key||'').trim(),headers={'Content-Type':'application/json'};
    if(!['bearer','anthropic','gemini','none'].includes(auth))throw new Error('不支持的鉴权方式。');
    if(/[\r\n]/.test(key))throw new Error('API Key 不能包含换行。');
    if(auth!=='none'&&!key)throw new Error('请输入 API Key，或选择不使用鉴权。');
    if(auth==='bearer')headers.Authorization='Bearer '+key;
    if(auth==='anthropic')headers['x-api-key']=key;
    if(auth==='gemini')headers['x-goog-api-key']=key;
    if(p.id==='anthropic'||auth==='anthropic'){
      headers['anthropic-version']='2023-06-01';
      headers['anthropic-dangerous-direct-browser-access']='true';
    }
    return headers;
  }
  function plain(value){return typeof value==='string'?value:Array.isArray(value)?value.map(item=>typeof item==='string'?item:item?.text||'').join(''):value==null?'':String(value);}
  function mimeFor(url,kind) {
    const ext=String(url).split(/[?#]/)[0].match(/\.([a-z0-9]+)$/i)?.[1]?.toLowerCase();
    return ({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif',mp4:'video/mp4',webm:kind==='video'?'video/webm':'audio/webm',mov:'video/quicktime',mp3:'audio/mpeg',wav:'audio/wav'})[ext]||(kind==='video'?'video/mp4':'image/jpeg');
  }
  function mediaPart(url,kind,format,reject,field) {
    const data=/^data:([^;,]+);base64,([A-Za-z0-9+/=\r\n]+)$/.exec(String(url||''));
    if(!data){try{const parsed=new URL(url);if(!/^https?:$/.test(parsed.protocol)||parsed.username||parsed.password)throw new Error();}catch{reject(field,'媒体地址必须为 HTTP(S) URL 或 Base64 Data URL');return null;}}
    if(format==='openai-responses'){
      if(kind!=='image'){reject(field,'Responses 没有与 video_url 等价的通用视频输入字段');return null;}
      return {type:'input_image',image_url:url};
    }
    if(format==='anthropic'){
      if(kind!=='image'){reject(field,'Messages 原生协议不接收 video_url');return null;}
      return {type:'image',source:data?{type:'base64',media_type:data[1],data:data[2]}:{type:'url',url}};
    }
    return data?{inlineData:{mimeType:data[1],data:data[2]}}:{fileData:{mimeType:mimeFor(url,kind),fileUri:url}};
  }
  function contentParts(value,format,reject,field,role='user') {
    const rows=typeof value==='string'?[{type:'text',text:value}]:Array.isArray(value)?value:value==null?[]:[{type:'text',text:String(value)}];
    return rows.map((item,index)=>{
      if(typeof item==='string')item={type:'text',text:item};
      if(item?.type==='text')return format==='gemini'?{text:String(item.text||'')}:{type:format==='openai-responses'?(role==='assistant'?'output_text':'input_text'):'text',text:String(item.text||'')};
      if(item?.type==='image_url'||item?.type==='video_url'){
        const kind=item.type==='image_url'?'image':'video',source=item[item.type],part=mediaPart(typeof source==='string'?source:source?.url,kind,format,reject,field+'['+index+']');
        if(part&&source?.detail){if(format==='openai-responses')part.detail=source.detail;else reject(field+'['+index+'].detail','当前协议没有已验证的图片 detail 映射');}
        return part;
      }
      reject(field+'['+index+']','不支持的内容类型 '+(item?.type||'unknown'));return null;
    }).filter(Boolean);
  }
  function functionTools(tools,format,reject) {
    if(!Array.isArray(tools)){reject('tools','工具定义必须为数组');return [];}
    return tools.map((tool,index)=>{
      if(tool?.type!=='function'||!tool.function?.name){reject('tools['+index+']','本检测适配器仅支持 function 工具');return null;}
      const f=tool.function,parameters=clone(f.parameters||{type:'object',properties:{}});
      for(const key of Object.keys(f))if(!['name','description','parameters','strict'].includes(key))reject('tools['+index+'].function.'+key,'当前协议没有已验证的等价工具字段');
      if(format==='gemini'&&own(f,'strict'))reject('tools['+index+'].function.strict','Gemini 函数声明没有 strict 开关');
      if(format==='openai-responses')return {type:'function',name:f.name,...(f.description?{description:f.description}:{}),parameters,...(own(f,'strict')?{strict:f.strict}:{})};
      if(format==='anthropic')return {name:f.name,...(f.description?{description:f.description}:{}),input_schema:parameters,...(own(f,'strict')?{strict:f.strict}:{})};
      return {name:f.name,...(f.description?{description:f.description}:{}),parametersJsonSchema:parameters};
    }).filter(Boolean);
  }
  function toolChoice(value,format,reject) {
    const name=value?.function?.name;
    if(format==='openai-responses')return name?{type:'function',name}:value;
    if(format==='anthropic'){
      if(name)return {type:'tool',name};
      if(['auto','required','none'].includes(value))return {type:value==='required'?'any':value};
    } else if(format==='gemini'){
      if(name)return {functionCallingConfig:{mode:'ANY',allowedFunctionNames:[name]}};
      if(['auto','required','none'].includes(value))return {functionCallingConfig:{mode:({auto:'AUTO',required:'ANY',none:'NONE'})[value]}};
    }
    reject('tool_choice','无法映射该工具选择形式');return undefined;
  }
  function build(payload,config={}) {
    if(!payload||typeof payload!=='object'||Array.isArray(payload))throw new Error('检测请求体必须是 JSON 对象。');
    const p=profile(config),input=clone(payload),notApplicable=[],seen=new Set();
    const reject=(field,reason)=>{if(!seen.has(field)){seen.add(field);notApplicable.push({field,reason});}};
    const source=Array.isArray(input.messages)?input.messages:[];
    if(!Array.isArray(input.messages))throw new Error('检测请求必须包含 messages 数组。');
    let body;
    if(p.id==='openai-chat')body=input;
    else {
      const known=new Set(['model','messages','stream','max_tokens','max_completion_tokens','temperature','top_p','tools','tool_choice','response_format','stream_options']);
      const allowed=p.id==='openai-responses'?['parallel_tool_calls','reasoning','metadata','store','user','service_tier','truncation']:p.id==='anthropic'?['stop','top_k','metadata','service_tier','thinking']:['stop','top_k','seed','n','presence_penalty','frequency_penalty','logprobs','top_logprobs','safetySettings','cachedContent'];
      allowed.forEach(key=>known.add(key));
      for(const key of Object.keys(input))if(!known.has(key))reject(key,'当前协议没有已验证的等价字段');
      for(const [index,message] of source.entries()){
        if(!message||typeof message!=='object'||Array.isArray(message)){reject('messages['+index+']','消息必须为对象');continue;}
        if(own(message,'tools'))reject('messages['+index+'].tools','动态 system tools 属于 Chat 扩展，当前原生协议没有等价语义');
        for(const key of Object.keys(message))if(!['role','content','tools','tool_calls','tool_call_id'].includes(key)&&!(p.id==='gemini'&&message.role==='tool'&&key==='name'))reject('messages['+index+'].'+key,'当前协议没有已验证的等价消息字段');
      }
      if(source.some(message=>!message||typeof message!=='object'||Array.isArray(message)))throw new NotApplicableError(p.id,notApplicable);
      if(own(input,'max_tokens')&&own(input,'max_completion_tokens'))reject('max_tokens / max_completion_tokens','两个输出上限字段不可同时映射到同一原生字段');
      if(input.stream_options&&Object.keys(input.stream_options).some(key=>key!=='include_usage'))reject('stream_options','只支持 include_usage 的协议等价处理');
      const cap=own(input,'max_completion_tokens')?input.max_completion_tokens:input.max_tokens;
      if(p.id==='openai-responses'){
        body={model:input.model,input:[]};
        for(const [index,message] of source.entries()){
          if(message.role==='tool'){body.input.push({type:'function_call_output',call_id:message.tool_call_id,output:plain(message.content)});continue;}
          if(!['system','developer','user','assistant'].includes(message.role)){reject('messages['+index+'].role','无法映射该消息角色');continue;}
          const parts=contentParts(message.content,p.id,reject,'messages['+index+'].content',message.role);
          if(parts.length||!message.tool_calls?.length)body.input.push({role:message.role,content:parts});
          for(const call of message.tool_calls||[])body.input.push({type:'function_call',call_id:call.id,name:call.function?.name,arguments:call.function?.arguments||'{}'});
        }
        if(cap!==undefined)body.max_output_tokens=cap;
        for(const key of ['stream','temperature','top_p',...allowed])if(own(input,key))body[key]=input[key];
        if(own(input,'tools'))body.tools=functionTools(input.tools,p.id,reject);
        if(own(input,'tool_choice'))body.tool_choice=toolChoice(input.tool_choice,p.id,reject);
        if(input.response_format){const format=input.response_format;body.text={format:format.type==='json_schema'?{type:'json_schema',...clone(format.json_schema||{})}:clone(format)};}
      } else if(p.id==='anthropic'){
        body={model:input.model,max_tokens:cap===undefined?1024:cap,messages:[]};
        const systems=[];
        for(const [index,message] of source.entries()){
          if(['system','developer'].includes(message.role)){systems.push(...contentParts(message.content,p.id,reject,'messages['+index+'].content'));continue;}
          if(message.role==='tool'){body.messages.push({role:'user',content:[{type:'tool_result',tool_use_id:message.tool_call_id,content:plain(message.content)}]});continue;}
          if(!['user','assistant'].includes(message.role)){reject('messages['+index+'].role','无法映射该消息角色');continue;}
          const parts=contentParts(message.content,p.id,reject,'messages['+index+'].content');
          for(const call of message.tool_calls||[]){let args;try{args=JSON.parse(call.function?.arguments||'{}');}catch{reject('messages['+index+'].tool_calls','工具参数不是有效 JSON');continue;}parts.push({type:'tool_use',id:call.id,name:call.function?.name,input:args});}
          body.messages.push({role:message.role,content:parts});
        }
        if(systems.length)body.system=systems;
        for(const key of ['stream','temperature','top_p','top_k','metadata','service_tier','thinking'])if(own(input,key))body[key]=input[key];
        if(own(input,'stop'))body.stop_sequences=Array.isArray(input.stop)?input.stop:[input.stop];
        if(own(input,'tools'))body.tools=functionTools(input.tools,p.id,reject);
        if(own(input,'tool_choice'))body.tool_choice=toolChoice(input.tool_choice,p.id,reject);
        if(input.response_format){
          if(input.response_format.type==='json_schema')body.output_config={format:{type:'json_schema',schema:clone(input.response_format.json_schema?.schema||{})}};
          else if(input.response_format.type!=='text')reject('response_format','Messages 不提供与 json_object 等价的独立 JSON Mode；可使用 JSON Schema');
        }
      } else {
        body={contents:[]};const systems=[],calls=new Map();
        for(const message of source)for(const call of message.tool_calls||[])calls.set(call.id,call.function?.name);
        for(const [index,message] of source.entries()){
          if(['system','developer'].includes(message.role)){systems.push(...contentParts(message.content,p.id,reject,'messages['+index+'].content'));continue;}
          if(message.role==='tool'){
            const name=message.name||calls.get(message.tool_call_id);if(!name){reject('messages['+index+']','Gemini 工具结果需要函数名或对应 tool_call_id');continue;}
            let response;try{response=JSON.parse(plain(message.content));}catch{response={result:plain(message.content)};}
            if(!response||typeof response!=='object'||Array.isArray(response))response={result:response};
            body.contents.push({role:'user',parts:[{functionResponse:{name,response}}]});continue;
          }
          if(!['user','assistant'].includes(message.role)){reject('messages['+index+'].role','无法映射该消息角色');continue;}
          const parts=contentParts(message.content,p.id,reject,'messages['+index+'].content');
          for(const call of message.tool_calls||[]){let args;try{args=JSON.parse(call.function?.arguments||'{}');}catch{reject('messages['+index+'].tool_calls','工具参数不是有效 JSON');continue;}parts.push({functionCall:{name:call.function?.name,args}});}
          body.contents.push({role:message.role==='assistant'?'model':'user',parts});
        }
        if(systems.length)body.systemInstruction={parts:systems};
        const generationConfig={},map={max_tokens:'maxOutputTokens',max_completion_tokens:'maxOutputTokens',temperature:'temperature',top_p:'topP',top_k:'topK',seed:'seed',n:'candidateCount',presence_penalty:'presencePenalty',frequency_penalty:'frequencyPenalty',logprobs:'responseLogprobs',top_logprobs:'logprobs'};
        for(const [key,target]of Object.entries(map))if(own(input,key))generationConfig[target]=input[key];
        if(own(input,'stop'))generationConfig.stopSequences=Array.isArray(input.stop)?input.stop:[input.stop];
        if(input.response_format){
          if(input.response_format.type==='json_object'||input.response_format.type==='json_schema')generationConfig.responseMimeType='application/json';
          if(input.response_format.type==='json_schema')generationConfig.responseJsonSchema=clone(input.response_format.json_schema?.schema||{});
          if(!['json_object','json_schema','text'].includes(input.response_format.type))reject('response_format','无法映射该输出格式');
        }
        if(Object.keys(generationConfig).length)body.generationConfig=generationConfig;
        if(own(input,'tools'))body.tools=[{functionDeclarations:functionTools(input.tools,p.id,reject)}];
        if(own(input,'tool_choice'))body.toolConfig=toolChoice(input.tool_choice,p.id,reject);
        for(const key of ['safetySettings','cachedContent'])if(own(input,key))body[key]=input[key];
      }
    }
    if(notApplicable.length&&!config.allowUnsupported)throw new NotApplicableError(p.id,notApplicable);
    const model=input.model||config.model,url=endpoint(config.base,config.path,model,!!input.stream,p.id),headers=authHeaders(config,p);
    if(input.stream)headers.Accept='text/event-stream';
    return {url,method:'POST',headers,body:JSON.stringify(body),preview:body,format:p.id,auth:config.auth||p.auth,notApplicable};
  }
  function finite(value){return typeof value==='number'&&Number.isFinite(value)?value:undefined;}
  function sum(...values){return values.every(value=>finite(value)!==undefined)?values.reduce((a,b)=>a+b,0):undefined;}
  function normalizedUsage(raw,format) {
    if(!raw||typeof raw!=='object')return undefined;
    const id=profile(format).id;if(id==='openai-chat')return clone(raw);
    const out={raw_usage:clone(raw)},set=(key,value)=>{if(value!==undefined)out[key]=value;};
    if(id==='openai-responses'){
      set('prompt_tokens',finite(raw.input_tokens));set('completion_tokens',finite(raw.output_tokens));set('total_tokens',finite(raw.total_tokens));
      if(raw.input_tokens_details)out.prompt_tokens_details=clone(raw.input_tokens_details);
      if(raw.output_tokens_details)out.completion_tokens_details=clone(raw.output_tokens_details);
    }else if(id==='anthropic'){
      const input=finite(raw.input_tokens),read=finite(raw.cache_read_input_tokens),create=finite(raw.cache_creation_input_tokens);
      set('prompt_tokens',input===undefined?undefined:input+(read||0)+(create||0));set('completion_tokens',finite(raw.output_tokens));
      set('total_tokens',sum(out.prompt_tokens,out.completion_tokens));if(out.total_tokens!==undefined)out.total_tokens_derived=true;
      if((read||0)+(create||0)>0)out.prompt_tokens_derived=true;
      if(read!==undefined||create!==undefined)out.prompt_tokens_details={...(read!==undefined?{cached_tokens:read,cache_read_input_tokens:read}:{}),...(create!==undefined?{cache_creation_input_tokens:create}:{})};
    }else{
      set('prompt_tokens',finite(raw.promptTokenCount));const output=finite(raw.candidatesTokenCount),thoughts=finite(raw.thoughtsTokenCount);
      set('completion_tokens',output===undefined?undefined:output+(thoughts||0));set('total_tokens',finite(raw.totalTokenCount));
      if(thoughts!==undefined)out.completion_tokens_details={reasoning_tokens:thoughts,visible_output_tokens:output};
      if(finite(raw.cachedContentTokenCount)!==undefined)out.prompt_tokens_details={cached_tokens:raw.cachedContentTokenCount};
    }
    return out;
  }
  function finishReason(value) {return ({end_turn:'stop',stop_sequence:'stop',max_tokens:'length',tool_use:'tool_calls',STOP:'stop',MAX_TOKENS:'length',SAFETY:'content_filter',RECITATION:'content_filter',BLOCKLIST:'content_filter',PROHIBITED_CONTENT:'content_filter'})[value]||value||null;}
  function normalize(raw,format='openai-chat') {
    const p=profile(format);if(!raw||typeof raw!=='object')return raw;
    if(p.id==='openai-chat')return raw;
    if(raw.error)return raw;
    const out={...raw,choices:[],native_format:p.id},calls=[];let text='',reasoning='';
    if(p.id==='anthropic'){
      for(const block of Array.isArray(raw.content)?raw.content:[]){if(block.type==='text')text+=block.text||'';if(block.type==='thinking')reasoning+=block.thinking||'';if(block.type==='tool_use')calls.push({id:block.id,type:'function',function:{name:block.name,arguments:JSON.stringify(block.input||{})}});}
      if(Array.isArray(raw.content))out.choices=[{index:0,message:{role:'assistant',content:text,...(reasoning?{reasoning_content:reasoning}:{}),...(calls.length?{tool_calls:calls}:{})},finish_reason:finishReason(raw.stop_reason)}];
      const value=normalizedUsage(raw.usage,p.id);if(value)out.usage=value;
    } else if(p.id==='openai-responses'){
      for(const item of Array.isArray(raw.output)?raw.output:[]){
        if(item.type==='message')for(const block of Array.isArray(item.content)?item.content:[]){if(block.type==='output_text')text+=block.text||'';if(block.type==='refusal')text+=block.refusal||'';}
        if(item.type==='reasoning')for(const block of Array.isArray(item.summary)?item.summary:[])reasoning+=block.text||'';
        if(item.type==='function_call')calls.push({id:item.call_id||item.id,type:'function',function:{name:item.name,arguments:item.arguments||'{}'}});
      }
      if(!text&&typeof raw.output_text==='string')text=raw.output_text;
      if(Array.isArray(raw.output)||raw.object==='response'||typeof raw.output_text==='string')out.choices=[{index:0,message:{role:'assistant',content:text,...(reasoning?{reasoning_content:reasoning}:{}),...(calls.length?{tool_calls:calls}:{})},finish_reason:raw.status==='incomplete'?finishReason(raw.incomplete_details?.reason==='max_output_tokens'?'length':raw.incomplete_details?.reason):calls.length?'tool_calls':raw.status==='completed'?'stop':null}];
      const value=normalizedUsage(raw.usage,p.id);if(value)out.usage=value;
    } else {
      out.model=raw.modelVersion||raw.model;
      out.choices=(Array.isArray(raw.candidates)?raw.candidates:[]).map((candidate,index)=>{
        let text='',reasoning='';const calls=[];
        for(const [i,part]of (Array.isArray(candidate.content?.parts)?candidate.content.parts:[]).entries()){
          if(part.text){if(part.thought)reasoning+=part.text;else text+=part.text;}
          if(part.functionCall)calls.push({id:part.functionCall.id||'gemini-call-'+index+'-'+i,type:'function',function:{name:part.functionCall.name,arguments:JSON.stringify(part.functionCall.args||{})}});
        }
        return {index:candidate.index??index,message:{role:'assistant',content:text,...(reasoning?{reasoning_content:reasoning}:{}),...(calls.length?{tool_calls:calls}:{})},finish_reason:calls.length?'tool_calls':finishReason(candidate.finishReason),...(candidate.logprobsResult?{logprobs:candidate.logprobsResult}:{})};
      });
      const value=normalizedUsage(raw.usageMetadata,p.id);if(value)out.usage=value;
    }
    return out;
  }
  function streamEvent(raw,format='openai-chat',eventName='') {
    const p=profile(format);
    if(raw==='[DONE]')return {choices:[],done:true,text:'',raw};
    if(typeof raw==='string'){try{raw=JSON.parse(raw);}catch{return {choices:[],text:'',malformed:true,raw};}}
    if(!raw||typeof raw!=='object')return {choices:[],text:'',malformed:true,raw};
    if(raw.error||raw.type==='error')return {choices:[],text:'',error:raw.error||raw,raw};
    let choices=[],usage,done=false;
    if(p.id==='openai-chat'){choices=raw.choices||[];usage=raw.usage;}
    else if(p.id==='gemini'){const value=normalize(raw,p.id);choices=(value.choices||[]).map(choice=>({...choice,delta:choice.message}));usage=value.usage;done=choices.some(choice=>!!choice.finish_reason);}
    else if(p.id==='anthropic'){
      const type=raw.type||eventName;
      if(type==='content_block_delta'){
        const d=raw.delta||{},delta=d.type==='text_delta'?{content:d.text||''}:d.type==='thinking_delta'?{reasoning_content:d.thinking||''}:d.type==='input_json_delta'?{tool_calls:[{index:raw.index||0,function:{arguments:d.partial_json||''}}]}:{};
        choices=[{index:0,delta,finish_reason:null}];
      }
      if(type==='content_block_start'&&raw.content_block?.type==='tool_use')choices=[{index:0,delta:{tool_calls:[{index:raw.index||0,id:raw.content_block.id,type:'function',function:{name:raw.content_block.name,arguments:''}}]},finish_reason:null}];
      if(type==='message_delta')choices=[{index:0,delta:{},finish_reason:finishReason(raw.delta?.stop_reason)}];
      usage=normalizedUsage(raw.usage||raw.message?.usage,p.id);done=type==='message_stop';
    }else{
      const type=raw.type||eventName;
      if(type==='response.output_text.delta')choices=[{index:raw.output_index||0,delta:{content:raw.delta||''},finish_reason:null}];
      if(type==='response.reasoning_summary_text.delta')choices=[{index:raw.output_index||0,delta:{reasoning_content:raw.delta||''},finish_reason:null}];
      if(type==='response.output_item.added'&&raw.item?.type==='function_call')choices=[{index:0,delta:{tool_calls:[{index:raw.output_index||0,id:raw.item.call_id||raw.item.id,type:'function',function:{name:raw.item.name,arguments:raw.item.arguments||''}}]},finish_reason:null}];
      if(type==='response.function_call_arguments.delta')choices=[{index:0,delta:{tool_calls:[{index:raw.output_index||0,function:{arguments:raw.delta||''}}]},finish_reason:null}];
      if(['response.completed','response.incomplete','response.failed'].includes(type)){
        const value=normalize(raw.response||{},p.id);choices=(value.choices||[]).map(choice=>({index:choice.index,delta:{},finish_reason:choice.finish_reason}));usage=value.usage;done=true;
        if(type==='response.failed')return {choices,text:'',usage,done,error:raw.response?.error||{message:'Responses 返回 failed 状态'},raw};
      }
    }
    const first=choices[0],delta=first?.delta||{},text=choices.map(choice=>plain(choice.delta?.content)).join('');
    return {choices,text,delta,reasoning:delta.reasoning_content||'',toolCalls:delta.tool_calls||[],usage,finishReason:first?.finish_reason||null,done,raw};
  }
  function createStreamParser(format='openai-chat') {
    let buffer='';
    const consume=final=>{
      const events=[];let match;
      while((match=/\r?\n\r?\n/.exec(buffer))){const block=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);consumeBlock(block,events);}
      if(final&&buffer){consumeBlock(buffer,events);buffer='';}return events;
    };
    function consumeBlock(block,events){let eventName='',data=[];for(const line of block.split(/\r?\n/)){if(line.startsWith('event:'))eventName=line.slice(6).trim();else if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));}if(data.length)events.push(streamEvent(data.join('\n'),format,eventName));}
    return {push(chunk){buffer+=String(chunk);return consume(false);},finish(){return consume(true);}};
  }
  function parseSSE(text,format='openai-chat'){const parser=createStreamParser(format);return [...parser.push(text),...parser.finish()];}
  root.GeneralFormat={profiles,profile,build,endpoint,authHeaders,normalize,normalizedUsage,streamEvent,createStreamParser,parseSSE,NotApplicableError};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.GeneralFormat;
})(typeof window!=='undefined'?window:globalThis);
