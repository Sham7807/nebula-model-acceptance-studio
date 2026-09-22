/* Multimodal API adapter. Browser-only, no dependencies. */
(function (root) {
  'use strict';
  const presets = [
    { id:'openai-chat',label:'OpenAI · Chat Completions',kind:'text',description:'OpenAI 兼容聊天接口，支持图片理解输入',path:'/v1/chat/completions',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'gpt-4o-mini'} },
    { id:'openai-responses',label:'OpenAI · Responses',kind:'text',description:'OpenAI Responses 文本与图片理解接口',path:'/v1/responses',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'gpt-4.1-mini'} },
    { id:'anthropic',label:'Anthropic · Messages',kind:'text',description:'Claude Messages 原生协议',path:'/v1/messages',method:'POST',bodyType:'json',auth:'anthropic',defaults:{model:'claude-sonnet-4-5'} },
    { id:'gemini',label:'Gemini · Generate Content',kind:'text',description:'Gemini 原生内容生成协议',path:'/v1beta/models/{model}:generateContent',method:'POST',bodyType:'json',auth:'gemini',defaults:{model:'gemini-2.5-flash'} },
    { id:'openai-image',label:'OpenAI · 图片生成',kind:'image',description:'OpenAI 兼容图片生成，可返回 URL 或 Base64',path:'/v1/images/generations',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'gpt-image-1',size:'1024x1024'} },
    { id:'openai-image-edit',label:'OpenAI · 图片编辑',kind:'image',description:'上传一张或多张参考图片，通过 multipart 图片编辑接口测试；数量限制由模型决定',path:'/v1/images/edits',method:'POST',bodyType:'multipart',auth:'bearer',defaults:{model:'gpt-image-1',size:'1024x1024'} },
    { id:'relay-image-json',label:'中转站 · 参考图生成（JSON）',kind:'image',description:'通过 image 字段发送单张或多张参考图；适用于支持此 JSON 协议的 Seedream 等渠道，需渠道支持',path:'/v1/images/generations',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'doubao-seedream-4-0-250828',size:'1024x1024'} },
    { id:'gemini-image',label:'Gemini · 图片生成',kind:'image',description:'Gemini 原生图片生成与编辑，返回 inlineData',path:'/v1beta/models/{model}:generateContent',method:'POST',bodyType:'json',auth:'gemini',defaults:{model:'gemini-2.5-flash-image'} },
    { id:'relay-video-json',label:'中转站 · Videos（JSON）',kind:'video',description:'中转站兼容视频任务协议，发送 JSON 并自动查询任务；需渠道支持此协议',path:'/v1/videos',pollPath:'/v1/videos/{id}',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'sora-2',size:'1280x720',duration:'4'} },
    { id:'openai-video',label:'OpenAI 原生 · Videos（multipart）',kind:'video',description:'OpenAI 原生视频任务协议，上传 multipart 表单并获取视频内容',path:'/v1/videos',pollPath:'/v1/videos/{id}',contentPath:'/v1/videos/{id}/content',method:'POST',bodyType:'multipart',auth:'bearer',defaults:{model:'sora-2',size:'1280x720',duration:'4'} },
    { id:'doubao-video',label:'豆包 · 视频生成',kind:'video',description:'火山方舟视频任务接口，支持参考图片',path:'/api/v3/contents/generations/tasks',pollPath:'/api/v3/contents/generations/tasks/{id}',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'doubao-seedance-1-0-lite-t2v'} },
    { id:'custom-video',label:'自定义 · 视频任务',kind:'video',description:'可覆盖请求路径、JSON 字段及任务查询路径',path:'/v1/videos',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:''} },
    { id:'openai-speech',label:'OpenAI · 语音合成（TTS）',kind:'audio',description:'文本转语音，适用于 TTS 模型，直接播放返回音频',path:'/v1/audio/speech',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'tts-1',voice:'alloy',format:'mp3',speed:1} },
    { id:'openai-audio-chat',label:'OpenAI · 音频对话 / 生成',kind:'audio',description:'通过 Chat Completions 生成音频，可选上传一个 WAV 或 MP3 音频进行对话',path:'/v1/chat/completions',method:'POST',bodyType:'json',auth:'bearer',defaults:{model:'gpt-audio',voice:'alloy',format:'wav'} },
    { id:'gemini-speech',label:'Gemini · 语音生成',kind:'audio',description:'Gemini 原生 AUDIO 输出与语音配置，支持 PCM 直接播放',path:'/v1beta/models/{model}:generateContent',method:'POST',bodyType:'json',auth:'gemini',defaults:{model:'gemini-2.5-flash-preview-tts',voice:'Kore',format:'wav'} },
    { id:'openai-transcription',label:'OpenAI · 音频转文字',kind:'audio',description:'上传音频进行转录',path:'/v1/audio/transcriptions',method:'POST',bodyType:'multipart',auth:'bearer',defaults:{model:'whisper-1',format:'json'} },
    { id:'openai-translation',label:'OpenAI · 音频翻译',kind:'audio',description:'上传音频翻译为英文文本',path:'/v1/audio/translations',method:'POST',bodyType:'multipart',auth:'bearer',defaults:{model:'whisper-1',format:'json'} }
  ];
  const ownedUrls = new Set();
  const audioMime = {mp3:'audio/mpeg',wav:'audio/wav',opus:'audio/ogg',aac:'audio/aac',flac:'audio/flac',pcm:'audio/pcm',pcm16:'audio/pcm'};
  const MIME = /^(?:image\/(?:png|jpe?g|webp|gif|avif|bmp)|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+)$/i;
  function profile(config) {
    const id = typeof config.preset === 'object' ? config.preset.id : config.preset;
    const p = presets.find(x=>x.id===id);
    if (!p) throw new Error('请选择有效的接口预设');
    return p;
  }
  function normalize(config) {
    const c = Object.assign({},config);
    c.base = String(c.base || c.baseUrl || '').trim();
    c.key = String(c.key || c.apiKey || '').trim();
    c.model = String(c.model || '').trim();
    c.prompt = String(c.prompt || '');
    c.files = Array.from(c.files || []);
    c.referenceUrls = Array.from(c.referenceUrls || []);
    c.extra = c.extra == null ? {} : c.extra;
    if (!c.extra || Array.isArray(c.extra) || typeof c.extra !== 'object') throw new Error('额外 JSON 必须是对象');
    for (const k of Object.keys(c.extra)) if (['__proto__','prototype','constructor'].includes(k)) throw new Error('额外 JSON 含不支持的字段');
    return c;
  }
  function referenceUrl(value, index) {
    const raw=String(value||'').trim();
    if(!raw) throw new Error('参考图片 URL '+(Number(index)+1)+' 不能为空。');
    let parsed;
    try { parsed=new URL(raw); } catch { throw new Error('参考图片 URL '+(Number(index)+1)+' 不是有效的 URL。'); }
    if(!/^https?:$/.test(parsed.protocol)||parsed.username||parsed.password) throw new Error('参考图片 URL '+(Number(index)+1)+' 必须是公开的 http:// 或 https:// 地址，不能包含账号密码。');
    return parsed.href;
  }
  function baseUrl(base) {
    let u; try { u = new URL(base); } catch { throw new Error('渠道地址必须是完整的 http:// 或 https:// URL'); }
    if (!/^https?:$/.test(u.protocol) || u.username || u.password) throw new Error('渠道地址仅支持 HTTP(S)，不能包含用户名或密码');
    if (u.search || u.hash) throw new Error('渠道地址不能包含查询参数或片段');
    return u;
  }
  function endpoint(base,path,params={}) {
    const b=baseUrl(base);
    let p=String(path||'').trim();
    p=p.replace(/\{(model|id)\}/g,(_,k)=>encodeURIComponent(String(params[k]||'')));
    if (!p) throw new Error('接口路径不能为空');
    if (/[\u0000-\u0020\\]/.test(p)) throw new Error('接口路径含无效字符');
    let u;
    if (/^[a-z][a-z\d+.-]*:/i.test(p) || p.startsWith('//')) u=new URL(p,b);
    else {
      let bp=b.pathname.replace(/\/+$/,'');
      const pp='/'+p.replace(/^\/+/, '');
      const baseVersion=bp.match(/\/(?:v\d+(?:beta)?|api\/v\d+)$/);
      const pathVersion=pp.match(/^\/(?:api\/v\d+|v\d+(?:beta)?)(?=\/|$)/);
      if(baseVersion&&pathVersion&&baseVersion[0]!==pathVersion[0])bp=bp.slice(0,-baseVersion[0].length);
      let suffix=pp;
      const segs=bp.split('/').filter(Boolean);
      for(let n=segs.length;n>0;n--) {
        const overlap='/'+segs.slice(-n).join('/');
        if(pp===overlap || pp.startsWith(overlap+'/')) { suffix=pp.slice(overlap.length); break; }
      }
      u=new URL(b.origin+bp+suffix);
    }
    if(u.origin!==b.origin || !/^https?:$/.test(u.protocol) || u.username || u.password) throw new Error('接口及查询路径必须与渠道地址同源，防止密钥外泄');
    u.hash='';
    return u.href;
  }
  function safeUrl(value,kind) {
    if (typeof value !== 'string' || !value.trim()) return null;
    const str=value.trim();
    if (str.startsWith('blob:')) return ownedUrls.has(str) ? str : null;
    if (str.startsWith('data:')) {
      const match=/^data:([^;,]+);base64,([a-zA-Z0-9+/=\r\n]+)$/.exec(str);
      if (!match || !MIME.test(match[1])) return null;
      if (kind && !match[1].toLowerCase().startsWith(kind+'/')) return null;
      return str;
    }
    try { const u=new URL(str); return /^https?:$/.test(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; }
  }
  function makeObjectUrl(blob) { const u=URL.createObjectURL(blob); ownedUrls.add(u); return u; }
  function bytesBase64(bytes) { let s=''; for(let i=0;i<bytes.length;i+=0x8000) s+=String.fromCharCode.apply(null,bytes.subarray(i,i+0x8000)); return btoa(s); }
  function audioFileFormat(file) {
    const mime=String(file.type||'').toLowerCase().split(';')[0];
    if(['audio/wav','audio/x-wav','audio/wave','audio/vnd.wave'].includes(mime))return 'wav';
    if(['audio/mpeg','audio/mp3'].includes(mime))return 'mp3';
    if(!mime||mime==='application/octet-stream') {const ext=String(file.name||'').toLowerCase().match(/\.(wav|mp3)$/);if(ext)return ext[1];}
    throw new Error('音频对话目前只支持 WAV 或 MP3 文件，请转换后上传。');
  }
  function audioDetails(value) {
    const parts=String(value||'').split(';').map(s=>s.trim());
    const mime=parts.shift().toLowerCase();
    const params={};for(const part of parts){const eq=part.indexOf('=');if(eq>0)params[part.slice(0,eq).toLowerCase()]=part.slice(eq+1).replace(/^"|"$/g,'');}
    return {mime,rate:Number(params.rate||params.samplerate||params.sample_rate)||24000,channels:Number(params.channels)||1};
  }
  function base64Bytes(base64) { const s=atob(base64.replace(/\s/g,'')); const b=new Uint8Array(s.length); for(let i=0;i<s.length;i++) b[i]=s.charCodeAt(i); return b; }
  const imageFileTypes={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif',avif:'image/avif',bmp:'image/bmp',tif:'image/tiff',tiff:'image/tiff',heic:'image/heic',heif:'image/heif',svg:'image/svg+xml'};
  function imageFileMime(file) {
    const mime=String(file.type||'').toLowerCase().split(';')[0];
    if(mime.startsWith('image/'))return mime;
    if(!mime||mime==='application/octet-stream') {
      const ext=String(file.name||'').toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
      if(imageFileTypes[ext])return imageFileTypes[ext];
    }
    throw new Error('参考文件必须是图片：'+String(file.name||'未命名文件'));
  }
  async function fileData(file,mime=file.type||'application/octet-stream') { return {mime,data:bytesBase64(new Uint8Array(await file.arrayBuffer()))}; }

  function wav(bytes,sampleRate=24000,channels=1) {
    const arr=new Uint8Array(44+bytes.byteLength),d=new DataView(arr.buffer);
    const put=(at,s)=>{for(let i=0;i<s.length;i++)arr[at+i]=s.charCodeAt(i);};
    put(0,'RIFF'); d.setUint32(4,36+bytes.byteLength,true); put(8,'WAVE'); put(12,'fmt '); d.setUint32(16,16,true);d.setUint16(20,1,true);d.setUint16(22,channels,true);d.setUint32(24,sampleRate,true);d.setUint32(28,sampleRate*channels*2,true);d.setUint16(32,channels*2,true);d.setUint16(34,16,true);put(36,'data');d.setUint32(40,bytes.byteLength,true);arr.set(bytes,44);return arr;
  }
  function authHeaders(c,p) {
    const auth=c.auth||p.auth||'bearer'; const h={};
    if (!['bearer','anthropic','gemini','none'].includes(auth)) throw new Error('未知鉴权方式');
    if(c.key && auth==='bearer') h.Authorization='Bearer '+c.key;
    if(c.key && auth==='anthropic') h['x-api-key']=c.key;
    if(auth==='anthropic') h['anthropic-version']='2023-06-01';
    if(c.key && auth==='gemini') h['x-goog-api-key']=c.key;
    return h;
  }
  function present(v) { return v!==undefined && v!==null && v!==''; }
  function redact(value,key) {
    if(typeof value==='string') {
      let out=value;
      if(key)for(const needle of [key,encodeURIComponent(key)])if(needle)out=out.split(needle).join('[REDACTED]');
      return out.replace(/([?&](?:api[_-]?key|key|access_token|refresh_token|token|secret|password)=)[^&#\s]*/gi,'$1[REDACTED]');
    }
    if(Array.isArray(value)) return value.map(v=>redact(v,key));
    if(value&&typeof value==='object') { const o={}; for(const [k,v] of Object.entries(value)) o[k]=/^(?:authorization|x-api-key|x-goog-api-key|api[_-]?key|key|access_token|refresh_token|token|secret|password)$/i.test(k)?'[REDACTED]':redact(v,key); return o; }
    return value;
  }
  async function build(config) {
    const c=normalize(config),p=profile(c),headers=authHeaders(c,p);
    if(!c.model) throw new Error('请输入模型 ID');
    const url=endpoint(c.base,c.path||p.path,{model:c.model});
    const pollPath=c.pollPath===undefined?p.pollPath:c.pollPath;
    const contentPath=c.contentPath===undefined?p.contentPath:c.contentPath;
    if(pollPath) endpoint(c.base,pollPath,{id:'__task__',model:c.model});
    if(contentPath) endpoint(c.base,contentPath,{id:'__task__',model:c.model});
    const files=c.files;
    const referenceUrls=c.referenceUrls.map(referenceUrl);
    if(referenceUrls.length&&p.kind!=='image') throw new Error('参考图片 URL 只能用于图像模型接口。');
    const imageInputPresets=['openai-image-edit','relay-image-json','openai-video','doubao-video','gemini-image','gemini','anthropic','openai-chat','openai-responses'];
    if(p.id==='gemini-speech'&&files.length) throw new Error('Gemini 语音生成预设只接收文本；请清除上传文件。');
    if(p.id==='openai-image'&&files.length) throw new Error('当前图片生成预设只接收文本；带参考图请改用图片编辑、中转站参考图生成或 Gemini 图片协议。');
    if(p.id==='openai-speech'&&files.length) throw new Error('语音合成预设只接收文本；请清除上传文件，或选择音频对话 / 转写接口。');
    if(p.id==='custom-video'&&files.length) throw new Error('自定义视频 JSON 预设没有本地文件映射；请清除上传文件，按渠道文档在附加 JSON 中配置参考图。');
    if(p.id==='openai-audio-chat'&&files.length>1) throw new Error('音频对话一次只支持一个 WAV 或 MP3 文件。');
    if(p.id==='openai-audio-chat')for(const f of files)audioFileFormat(f);
    if(p.id==='relay-video-json'&&files.length) throw new Error('中转站 Videos（JSON）预设暂不接收本地参考文件。请清除上传文件；如渠道支持图片 URL，可按渠道文档在附加 JSON 中配置。');
    if(['openai-image-edit','openai-transcription','openai-translation'].includes(p.id)&&!files.length&&!referenceUrls.length) throw new Error('此接口需要先上传文件');
    if(p.id==='openai-image-edit'&&referenceUrls.length) throw new Error('OpenAI 图片编辑的 multipart 接口不接受直接图片 URL；请切换到「中转站 · 参考图生成（JSON）」协议，或先下载 URL 图片后上传。');
    const extraImage=c.extra.image;
    if(referenceUrls.length&&Object.prototype.hasOwnProperty.call(c.extra,'image')) throw new Error('参考图片 URL 与附加 JSON 的 image 字段冲突，请保留一种输入方式。');
    if(p.id==='relay-image-json'&&!files.length&&!referenceUrls.length&&!(typeof extraImage==='string'&&extraImage.trim()||Array.isArray(extraImage)&&extraImage.length&&extraImage.every(v=>typeof v==='string'&&v.trim()))) throw new Error('参考图生成需要上传至少一张图片，或填写至少一个图片 URL。');
    if(['openai-transcription','openai-translation','openai-video'].includes(p.id)&&files.length>1) throw new Error('此接口一次只支持一个上传文件');
    if(imageInputPresets.includes(p.id))for(const file of files)imageFileMime(file);
    const attachmentFields={
      'openai-image-edit':['image','image[]'],'relay-image-json':['image'],
      'openai-video':['input_reference'],'openai-transcription':['file'],'openai-translation':['file'],
      'openai-chat':['messages'],'anthropic':['messages'],'openai-audio-chat':['messages'],
      'openai-responses':['input'],'gemini':['contents'],'gemini-image':['contents'],'doubao-video':['content']
    };
    if(files.length)for(const field of attachmentFields[p.id]||[])if(Object.prototype.hasOwnProperty.call(c.extra,field))throw new Error('附加 JSON 的 '+field+' 与已上传文件冲突；请移除该字段，或清除本地文件后使用自定义请求。');
    const fileContents = ['openai-chat','openai-responses','anthropic','gemini','gemini-image','doubao-video','openai-audio-chat','relay-image-json'].includes(p.id) ? await Promise.all(files.map(file=>fileData(file,p.id==='openai-audio-chat'?audioMime[audioFileFormat(file)]:imageFileMime(file)))) : [];
    let fields={model:c.model};
    switch(p.id) {
      case 'openai-chat': fields.messages=[{role:'user',content:fileContents.length?[{type:'text',text:c.prompt},...fileContents.map(f=>({type:'image_url',image_url:{url:'data:'+f.mime+';base64,'+f.data}}))]:c.prompt}]; break;
      case 'openai-responses': fields.input=fileContents.length?[{role:'user',content:[{type:'input_text',text:c.prompt},...fileContents.map(f=>({type:'input_image',image_url:'data:'+f.mime+';base64,'+f.data}))]}]:c.prompt; break;
      case 'anthropic': fields.max_tokens=1024;fields.messages=[{role:'user',content:fileContents.length?[...fileContents.map(f=>({type:'image',source:{type:'base64',media_type:f.mime,data:f.data}})),{type:'text',text:c.prompt}]:c.prompt}];break;
      case 'gemini': case 'gemini-image': delete fields.model;fields.contents=[{role:'user',parts:[{text:c.prompt},...fileContents.map(f=>({inlineData:{mimeType:f.mime,data:f.data}}))]}];if(p.id==='gemini-image')fields.generationConfig={responseModalities:['TEXT','IMAGE']};break;
      case 'openai-image': case 'openai-image-edit': case 'relay-image-json': fields.prompt=c.prompt;if(present(c.size))fields.size=c.size;if(present(c.format))fields.response_format=c.format;if(p.id==='relay-image-json'&&(fileContents.length||referenceUrls.length)){const images=[...fileContents.map(f=>'data:'+f.mime+';base64,'+f.data),...referenceUrls];fields.image=images.length===1?images[0]:images;}break;
      case 'relay-video-json': case 'openai-video': fields.prompt=c.prompt;if(present(c.size))fields.size=c.size;if(present(c.duration))fields.seconds=String(c.duration);break;
      case 'doubao-video': fields.content=[{type:'text',text:c.prompt},...fileContents.map(f=>({type:'image_url',image_url:{url:'data:'+f.mime+';base64,'+f.data}}))];if(present(c.duration))fields.duration=Number(c.duration);break;
      case 'custom-video': fields.prompt=c.prompt;if(present(c.duration))fields.duration=Number(c.duration);if(present(c.size))fields.size=c.size;break;
      case 'openai-audio-chat': fields.messages=[{role:'user',content:fileContents.length?[{type:'text',text:c.prompt},...fileContents.map((f,i)=>({type:'input_audio',input_audio:{data:f.data,format:audioFileFormat(files[i])}}))]:c.prompt}];fields.modalities=['text','audio'];fields.audio={voice:c.voice||p.defaults.voice,format:c.format==='pcm'?'pcm16':c.format||p.defaults.format};break;
      case 'gemini-speech': delete fields.model;fields.contents=[{role:'user',parts:[{text:c.prompt}]}];fields.generationConfig={responseModalities:['AUDIO'],speechConfig:{voiceConfig:{prebuiltVoiceConfig:{voiceName:c.voice||p.defaults.voice}}}};break;
      case 'openai-speech':fields.input=c.prompt;fields.voice=c.voice||p.defaults.voice;fields.response_format=c.format||p.defaults.format;if(present(c.speed))fields.speed=Number(c.speed);break;
      case 'openai-transcription':case 'openai-translation':if(present(c.prompt))fields.prompt=c.prompt;if(present(c.format))fields.response_format=c.format;if(p.id==='openai-transcription'&&present(c.language))fields.language=c.language;break;
    }
    const resolution=String(c.resolution??'').trim();
    if(['relay-video-json','doubao-video','custom-video'].includes(p.id)&&resolution)fields.resolution=resolution;
    fields=Object.assign(fields,c.extra);
    let body,preview=fields;
    if(p.bodyType==='multipart') {
      body=new FormData();
      for(const [k,v]of Object.entries(fields)) if(present(v)) body.append(k,typeof v==='object'?JSON.stringify(v):String(v));
      const fileField=p.id==='openai-image-edit'?(files.length>1?'image[]':'image'):p.id==='openai-video'?'input_reference':'file';
      for(const f of files){const file=imageInputPresets.includes(p.id)&&f.type!==imageFileMime(f)?new Blob([f],{type:imageFileMime(f)}):f;body.append(fileField,file,f.name||'upload');}
      preview=Object.assign({},fields,{[fileField]:files.map(f=>({name:f.name||'upload',type:f.type||'',size:f.size}))});
    } else { headers['Content-Type']='application/json';body=JSON.stringify(fields); }
    return {url,method:p.method,headers,body,preview:redact(preview,c.key),pollUrl:pollPath?endpoint(c.base,pollPath,{id:'{id}',model:c.model}).replace('%7Bid%7D','{id}'):undefined,contentUrl:contentPath?endpoint(c.base,contentPath,{id:'{id}',model:c.model}).replace('%7Bid%7D','{id}'):undefined,kind:p.kind,format:c.format,key:c.key};
  }
  function extract(raw,options={}) {
    const text=[],media=[],seen=new Set();
    const addText=v=>{if(typeof v==='string'&&v.trim()&&!text.includes(v))text.push(v);};
    const addMedia=(v,kind,mime)=>{const url=safeUrl(v,kind);if(url&&!seen.has(url)){seen.add(url);media.push({kind,url,...(mime?{mime}:{})});}};
    const addBase64=(data,mime,kind)=>{
      const info=audioDetails(mime);mime=info.mime;
      if(typeof data!=='string'||!data||!MIME.test(mime))return;
      if(['audio/pcm','audio/l16'].includes(mime)) {try{const rate=options.sampleRate||info.rate,channels=info.channels;if(!Number.isFinite(rate)||rate<8000||rate>384000||!Number.isInteger(channels)||channels<1||channels>8)return;const b=wav(base64Bytes(data),rate,channels);addMedia('data:audio/wav;base64,'+bytesBase64(b),'audio','audio/wav');}catch{}return;}
      addMedia('data:'+mime+';base64,'+data,kind||mime.split('/')[0],mime);
    };
    function walk(v,depth=0) {
      if(!v||typeof v!=='object'||depth>12)return;
      if(Array.isArray(v)){v.forEach(x=>walk(x,depth+1));return;}
      if(v.inlineData||v.inline_data){const d=v.inlineData||v.inline_data;addBase64(d.data,d.mimeType||d.mime_type||'image/png');}
      if(v.b64_json)addBase64(v.b64_json,v.mime_type||v.mimeType||'image/png','image');
      for(const key of ['video_url','videoUrl','audio_url','audioUrl','image_url','imageUrl']) {
        const val=v[key],kind=key.startsWith('video')?'video':key.startsWith('audio')?'audio':'image';
        addMedia(typeof val==='object'&&val?val.url:val,kind,v.mime_type||v.mimeType);
      }
      if(v.type==='image_generation_call'&&v.result)addBase64(v.result,'image/png','image');
      if(v.type==='output_audio'&&v.data)addBase64(v.data,v.mime_type||audioMime[v.format||options.format]||'audio/wav','audio');
      if(v.audio&&typeof v.audio==='object') { const a=v.audio;if(a.data)addBase64(a.data,a.mime_type||audioMime[a.format||options.format]||'audio/wav','audio');if(a.url)addMedia(a.url,'audio');addText(a.transcript); }
      if(options.kind==='video'&&v.metadata&&typeof v.metadata==='object')addMedia(v.metadata.url,'video');
      if(v.url) {
        let kind = v.type&&/^(image|video|audio)/.test(v.type)?v.type.match(/^(image|video|audio)/)[0]:null;
        if(!kind&&v.mime_type&&MIME.test(v.mime_type))kind=v.mime_type.split('/')[0];
        if(!kind&&v.mimeType&&MIME.test(v.mimeType))kind=v.mimeType.split('/')[0];
        if(!kind&&options.kind&&['image','video','audio'].includes(options.kind)) {
          const isTask=/\/tasks?\/|\/videos\/[^/]+\/?(?:[?#]|$)/i.test(v.url);
          if(!isTask)kind=options.kind;
        }
        if(kind)addMedia(v.url,kind);
      }
      for(const k of ['text','output_text','transcript']) addText(v[k]);
      if(typeof v.content==='string')addText(v.content);
      if(typeof v.message==='string'&&options.kind==='text')addText(v.message);
      for(const key of ['choices','message','delta','content','parts','candidates','data','output','result','results','images','videos','audios','response']) if(v[key]&&typeof v[key]==='object')walk(v[key],depth+1);
    }
    if(typeof raw==='string')addText(raw);else walk(raw);
    for(const t of [...text]) for(const m of t.matchAll(/!\[[^\]]*\]\((https?:\/\/[^\s)]+|data:image\/[a-z0-9.+-]+;base64,[a-zA-Z0-9+/=]+)\)/gi))addMedia(m[1],'image');
    return {text:text.join('\n\n'),media};
  }
  function abortError(message='操作已停止') { const e=new Error(message);e.name='AbortError';return e; }
  function emit(cb,event) {if(typeof cb==='function')try{cb(event);}catch{}}
  let workbenchProxyToken = '', workbenchProxyTokenPromise = null;
  async function proxyToken(force = false) {
    if (!force && workbenchProxyToken) return workbenchProxyToken;
    if (!force && workbenchProxyTokenPromise) return workbenchProxyTokenPromise;
    workbenchProxyTokenPromise = fetch('/api/session', { cache: 'no-store', credentials: 'same-origin' }).then(async response => {
      let data = null; try { data = await response.json(); } catch {}
      if (!response.ok || typeof data?.token !== 'string' || !data.token) throw new Error(data?.error || '工作台会话不可用，请刷新页面或重新登录。');
      workbenchProxyToken = data.token; return workbenchProxyToken;
    }).finally(() => { workbenchProxyTokenPromise = null; });
    return workbenchProxyTokenPromise;
  }
  function canUseWorkbenchProxy(url) {
    try {
      const page = root.location;
      if (!page || !/^https?:$/.test(page.protocol)) return false;
      return new URL(url, page.href).origin !== page.origin;
    } catch { return false; }
  }
  function proxyHeaders(headers) {
    const result={};
    if (!headers) return result;
    if (headers instanceof Headers) headers.forEach((value,key)=>{result[key]=value;});
    else for (const [key,value] of Object.entries(headers)) result[key]=String(value);
    delete result.host; delete result.Host; delete result['content-length']; delete result['Content-Length'];
    return result;
  }
  async function proxyFormData(form) {
    const fields={}, files=[];
    for (const [name,value] of form.entries()) {
      if (typeof File !== 'undefined' && value instanceof File || typeof Blob !== 'undefined' && value instanceof Blob) {
        const bytes=new Uint8Array(await value.arrayBuffer());
        let encoded=''; for (let i=0;i<bytes.length;i+=0x8000) encoded+=String.fromCharCode.apply(null,bytes.subarray(i,i+0x8000));
        files.push({field:name,name:value.name||'upload',type:value.type||'application/octet-stream',data:btoa(encoded)});
      } else if (fields[name]===undefined) fields[name]=String(value);
      else fields[name]=Array.isArray(fields[name])?[...fields[name],String(value)]:[fields[name],String(value)];
    }
    return {fields,files};
  }
  async function workbenchProxy(spec, timeout) {
    const body={url:spec.url,method:spec.method||'GET',headers:proxyHeaders(spec.headers),timeout};
    if (typeof FormData !== 'undefined' && spec.body instanceof FormData) body.form=await proxyFormData(spec.body);
    else if (spec.body!==undefined && spec.body!==null) body.body=typeof spec.body==='string'?spec.body:String(spec.body);
    const token = await proxyToken();
    const response=await fetch('/api/proxy',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify(body),signal:spec.signal,credentials:'same-origin'});
    let envelope; try { envelope=await response.json(); } catch { throw new Error('工作台代理没有返回有效 JSON'); }
    if (!response.ok || envelope?.error) { const error=new Error(envelope?.error||('工作台代理失败（HTTP '+response.status+'）')); error.status=response.status; throw error; }
    const headers=new Headers(envelope.headers||{}), contentType=envelope.content_type||headers.get('content-type')||'';
    let bytes=null, textValue;
    if (typeof envelope.body_base64==='string') { const binary=atob(envelope.body_base64); bytes=new Uint8Array(binary.length); for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i); }
    else textValue=typeof envelope.text==='string'?envelope.text:'';
    return {status:Number(envelope.status)||0,ok:Number(envelope.status)>=200&&Number(envelope.status)<300,url:envelope.url||spec.url,headers,text:async()=>bytes!==null?new TextDecoder().decode(bytes):textValue,blob:async()=>new Blob([bytes!==null?bytes:textValue||''],{type:contentType})};
  }
  async function request(spec,options={}) {
    const requests=options.requests||[],key=spec.key||'',record={method:spec.method||'GET',url:redact(spec.url,key),status:null,durationMs:0};
    if(spec.preview!==undefined)record.body=spec.preview;
    requests.push(record);
    const started=Date.now(),ctrl=new AbortController();
    const timeout=Number(options.timeout===undefined?120:options.timeout);
    let timedOut=false,timer;
    const abort=()=>ctrl.abort(options.signal?.reason);
    if(options.signal?.aborted)ctrl.abort(options.signal.reason);else options.signal?.addEventListener('abort',abort,{once:true});
    if(timeout>0)timer=setTimeout(()=>{timedOut=true;ctrl.abort();},timeout*1000);
    emit(options.onEvent,{type:'request',message:record.method+' '+record.url,request:{...record},timeoutSeconds:Number.isFinite(timeout)&&timeout>0?timeout:null});
    try {
      if(ctrl.signal.aborted)throw abortError();
      const response=canUseWorkbenchProxy(spec.url)
        ? await workbenchProxy({...spec,signal:ctrl.signal},timeout)
        : await fetch(spec.url,{method:record.method,headers:spec.headers,body:spec.body,signal:ctrl.signal,redirect:'error',credentials:'omit'});
      if (canUseWorkbenchProxy(spec.url)) record.transport='workbench-proxy';
      record.status=response.status;
      const contentType=response.headers.get('content-type')||'',binaryAudio=audioDetails(contentType),mime=binaryAudio.mime;
      let raw,media=[],text='';
      const binary=MIME.test(mime)||(response.ok&&spec.kind==='audio'&&['application/octet-stream','binary/octet-stream'].includes(mime))||(response.ok&&spec.kind==='video'&&mime==='application/octet-stream');
      if(binary&&response.ok) {
        let blob=await response.blob(),type=mime,kind=mime.split('/')[0];
        if(!['image','audio','video'].includes(kind)){kind=spec.kind;type=kind==='audio'?(audioMime[spec.format]||'audio/mpeg'):'video/mp4';blob=new Blob([blob],{type});}
        if(['pcm','pcm16'].includes(spec.format)||type==='audio/pcm'||type==='audio/l16') {blob=new Blob([wav(new Uint8Array(await blob.arrayBuffer()),binaryAudio.rate,binaryAudio.channels)],{type:'audio/wav'});type='audio/wav';kind='audio';}
        media=[{kind,url:makeObjectUrl(blob),mime:type,owned:true}];raw={binary:true,mime:type,bytes:blob.size};
      } else {
        const body=await response.text();
        if(body) {try{raw=JSON.parse(body);}catch{raw=body;}}else raw=null;
        if(response.ok){const ex=extract(raw,{kind:spec.kind,format:spec.format});text=ex.text;media=ex.media;}
      }
      record.durationMs=Date.now()-started;
      emit(options.onEvent,{type:'response',message:'HTTP '+response.status,request:{...record},status:response.status});
      const apiError=raw&&typeof raw==='object'?(raw.error||raw.data?.error):null;
      if(!response.ok||apiError) {
        const detail=typeof raw==='string'?raw:typeof apiError==='string'?apiError:apiError?.message||raw?.message||JSON.stringify(apiError||raw);
        const prefix=response.ok?'接口返回错误（HTTP '+response.status+'）':'HTTP '+response.status;
        const err=new Error(prefix+(detail?'：'+redact(detail,key).slice(0,1000):''));err.status=response.status;err.raw=redact(raw,key);err.requests=requests;throw err;
      }
      return {raw: redact(raw,key),media,text,status:response.status,url:response.url||spec.url,headers:response.headers};
    } catch(err) {
      record.durationMs=Date.now()-started;
      if(timedOut){const e=new Error('请求超时（'+timeout+' 秒）');e.name='TimeoutError';e.requests=requests;throw e;}
      if(ctrl.signal.aborted){const e=abortError();e.requests=requests;throw e;}
      err.requests=requests;if(key)err.message=redact(err.message,key);throw err;
    } finally {if(timer)clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
  }
  function taskNodes(raw) {
    const nodes=[],queue=[{value:raw,depth:0}],seen=new Set();
    while(queue.length&&nodes.length<40) {
      const {value,depth}=queue.shift();
      if(!value||typeof value!=='object'||Array.isArray(value)||seen.has(value))continue;
      seen.add(value);nodes.push(value);
      if(depth<5)for(const key of ['data','result','output','metadata'])if(value[key]&&typeof value[key]==='object')queue.push({value:value[key],depth:depth+1});
    }
    return nodes;
  }
  function firstTaskField(nodes,keys,parse) {
    for(const value of nodes)for(const key of keys)if(Object.prototype.hasOwnProperty.call(value,key)) {
      const found=parse(value[key]);if(found!==null&&found!==undefined)return found;
    }
    return null;
  }
  function telemetryNumber(value,percent=false) {
    if(typeof value==='number')return Number.isFinite(value)?value:null;
    if(typeof value!=='string')return null;
    const str=value.trim();if(!str)return null;
    const pattern=percent?/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*%?$/:/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
    if(!pattern.test(str))return null;
    const number=Number(str.replace(/\s*%$/,''));return Number.isFinite(number)?number:null;
  }
  function taskProgress(raw) {
    const nodes=taskNodes(raw);
    const id=firstTaskField(nodes,['task_id','taskId','id'],v=>(typeof v==='string'&&v.trim())||(typeof v==='number'&&Number.isFinite(v))?String(v):null);
    const status=firstTaskField(nodes,['status','task_status','taskStatus','state'],v=>typeof v==='string'&&v.trim()?v.trim().toLowerCase():null)||'';
    let progressPercent=firstTaskField(nodes,['progress_percent','progress_percentage','progressPercent','percentage','percent','progress'],v=>{const n=telemetryNumber(v,true);return n!==null&&n>=0&&n<=100?n:null;});
    if(progressPercent===null)progressPercent=firstTaskField(nodes,['progress_ratio','progressRatio'],v=>{const n=telemetryNumber(v);return n!==null&&n>=0&&n<=1?n*100:null;});
    const remainingSeconds=firstTaskField(nodes,['estimated_remaining_seconds','remaining_seconds','eta_seconds','estimated_time_remaining_seconds','time_remaining_seconds','estimatedRemainingSeconds','remainingSeconds','etaSeconds','estimatedTimeRemainingSeconds','timeRemainingSeconds'],v=>{const n=telemetryNumber(v);return n!==null&&n>=0?n:null;});
    const queuePosition=firstTaskField(nodes,['queue_position','queuePosition','position_in_queue','positionInQueue'],v=>{const n=telemetryNumber(v);return n!==null&&Number.isInteger(n)&&n>=0?n:null;});
    return {taskId:id,status,progressPercent,remainingSeconds,queuePosition};
  }
  function taskId(raw) {const id=taskProgress(raw).taskId;return id===null?undefined:id;}
  function taskStatus(raw) {return taskProgress(raw).status;}
  function taskStage(status) {
    if(['queued','pending','submitted','created','waiting'].includes(status))return 'queued';
    return 'processing';
  }
  function progressEvent(onEvent,raw,stage,extra={}) {
    const value=taskProgress(raw);
    emit(onEvent,{type:'progress',...value,stage,pollDeadline:null,nextPollAt:null,pollCount:0,source:'provider',...extra});
  }
  const completed=new Set(['completed','succeeded','success','done','finished','complete']);
  const failed=new Set(['failed','failure','error','cancelled','canceled','expired','rejected']);
  const pending=new Set(['queued','pending','running','processing','in_progress','in-progress','submitted','created','starting','waiting']);
  function delay(ms,signal) {return new Promise((resolve,reject)=>{if(signal?.aborted){reject(abortError());return;}const done=()=>{signal?.removeEventListener('abort',stop);resolve();};const timer=setTimeout(done,ms);const stop=()=>{clearTimeout(timer);signal?.removeEventListener('abort',stop);reject(abortError());};signal?.addEventListener('abort',stop,{once:true});});}
  function result(status,response,requests,start,id) {return {status,text:response.text||'',media:response.media||[],raw:response.raw,requests,...(id!==undefined?{taskId:String(id)}:{}),elapsedMs:Date.now()-start};}
  async function poll(c,p,id,first,options,requests,start) {
    const path=c.pollPath===undefined?p.pollPath:c.pollPath;
    const contentPath=c.contentPath===undefined?p.contentPath:c.contentPath;
    let response=first,last=response?.raw,pollCount=0;
    const deadline=Date.now()+Math.max(0,Number(c.pollTimeout===undefined?300:c.pollTimeout))*1000;
    const interval=Math.max(0,Number(c.pollInterval===undefined?3:c.pollInterval))*1000;
    const timeout=Number(c.timeout===undefined?120:c.timeout);
    const telemetry=(stage,nextPollAt=null,raw=last)=>progressEvent(options.onEvent,raw,stage,{taskId:String(id),pollDeadline:deadline,nextPollAt,pollCount});
    const successful=(value)=>{telemetry('complete');return result('success',value,requests,start,id);};
    emit(options.onEvent,{type:'poll',taskId:String(id),status:taskStatus(last),message:first?'任务 '+id+' 已创建':'正在继续查询任务 '+id});
    telemetry(first?taskStage(taskStatus(last)):'polling');
    try {
      for(;;) {
        const status=taskStatus(response?.raw);
        if(failed.has(status)) {const e=new Error('视频任务失败：'+(response.raw?.error?.message||response.raw?.error?.code||response.raw?.message||status));e.raw=response.raw;e.taskId=String(id);e.requests=requests;throw e;}
        if(response?.media?.some(m=>m.kind===p.kind)&&(!status||completed.has(status)))return successful(response);
        if(completed.has(status)) {
          if(contentPath) {
            telemetry('downloading');
            const content=await request({url:endpoint(c.base,contentPath,{id,model:c.model}),method:'GET',headers:authHeaders(c,p),kind:'video',key:c.key},{...options,timeout,requests});
            if(content.media.some(m=>m.kind===p.kind))return successful(content);
            return result('unrecognized',content,requests,start,id);
          }
          return response?.media?.some(m=>m.kind===p.kind)?successful(response):result('unrecognized',response,requests,start,id);
        }
        if(!path||Date.now()>=deadline)return result('pending',response||{raw:{id},text:'',media:[]},requests,start,id);
        if(response) {
          const delayMs=Math.min(interval,Math.max(0,deadline-Date.now()));
          telemetry('waiting',Math.min(deadline,Date.now()+delayMs));
          await delay(delayMs,options.signal);
        }
        if(Date.now()>=deadline)return result('pending',response||{raw:{id},text:'',media:[]},requests,start,id);
        pollCount++;telemetry('polling');
        response=await request({url:endpoint(c.base,path,{id,model:c.model}),method:'GET',headers:authHeaders(c,p),kind:p.kind,key:c.key},{...options,timeout:Math.min(timeout,Math.max(.001,(deadline-Date.now())/1000)),requests});
        last=response.raw;
        emit(options.onEvent,{type:'poll',taskId:String(id),status:taskStatus(last),message:'任务 '+id+' · '+(taskStatus(last)||'状态未知')});
        telemetry(taskStage(taskStatus(last)));
      }
    } catch(err) {err.taskId=String(id);err.requests=requests;if(err.raw===undefined)err.raw=last;if(err.name==='TimeoutError'&&Date.now()>=deadline)return result('pending',response||{raw:last,text:'',media:[]},requests,start,id);throw err;}
  }
  async function run(config,options={}) {
    const c=normalize(config),p=profile(c),requests=[],start=Date.now();
    try {
      const spec=await build(c);
      progressEvent(options.onEvent,null,'submitting',{source:'client'});
      const response=await request(spec,{...options,timeout:c.timeout,requests});
      const id=taskId(response.raw),status=taskStatus(response.raw);
      if(p.kind==='video'&&id!==undefined)return await poll(c,p,id,response,options,requests,start);
      if(failed.has(status)){const e=new Error('任务失败：'+status);e.raw=response.raw;throw e;}
      const expectsMedia=p.kind==='image'||p.kind==='video'||['openai-speech','openai-audio-chat','gemini-speech'].includes(p.id);
      if((expectsMedia?response.media.some(m=>m.kind===p.kind):response.media.length)||(!expectsMedia&&response.text)) {progressEvent(options.onEvent,response.raw,'complete');return result('success',response,requests,start);}
      if(response.status===202||pending.has(status)){progressEvent(options.onEvent,response.raw,taskStage(status));return result('pending',response,requests,start,id);}
      return result('unrecognized',response,requests,start);
    } catch(err){err.requests=requests;err.elapsedMs=Date.now()-start;throw err;}
  }
  async function resume(config,id,options={}) {
    const c=normalize(config),p=profile(c),requests=[],start=Date.now();
    if(!present(id))throw new Error('请输入任务 ID');
    const path=c.pollPath===undefined?p.pollPath:c.pollPath;
    if(!path)throw new Error('请填写查询路径，使用 {id} 作为任务 ID 占位符');
    endpoint(c.base,path,{id,model:c.model});
    const cp=c.contentPath===undefined?p.contentPath:c.contentPath;if(cp)endpoint(c.base,cp,{id,model:c.model});
    try{return await poll(c,p,id,null,options,requests,start);}catch(e){e.elapsedMs=Date.now()-start;throw e;}
  }
  async function listModels(config,options={}) {
    const c=normalize(config),p=profile(c),requests=[];
    const path=(c.auth||p.auth)==='gemini'?'/v1beta/models':'/v1/models';
    const r=await request({url:endpoint(c.base,path),method:'GET',headers:authHeaders(c,p),key:c.key},{...options,timeout:c.timeout,requests});
    const values=Array.isArray(r.raw)?r.raw:Array.isArray(r.raw?.data)?r.raw.data:Array.isArray(r.raw?.models)?r.raw.models:null;
    if(values===null)throw Object.assign(new Error('模型列表响应格式无法识别：应为数组，或包含 data / models 数组的对象'),{requests,raw:r.raw});
    return {models:values.map(x=>typeof x==='string'?{id:x}:{...x,id:String(x.id||x.name||'').replace(/^models\//,'')}).filter(x=>x.id),raw:r.raw,requests};
  }
  root.MediaEngine={presets,request,run,resume,listModels,extract,build,safeUrl,taskProgress};
})(typeof window!=='undefined'?window:globalThis);
