'use strict';
const $=id=>document.getElementById(id);
const kinds={text:{name:'文本',icon:'Aa',prompt:'用中文简短介绍你能完成的任务，并回答：17 × 23 等于多少？'},image:{name:'图像',icon:'▧',prompt:'一只橘猫坐在窗边的木桌上，旁边是一杯咖啡，晨间柔和的自然光，干净的画面，细节清晰。'},video:{name:'视频',icon:'▷',prompt:'镜头缓慢推进，一只橘猫坐在窗边，转头看向镜头，晨间柔和光线，画面稳定。'},audio:{name:'音频',icon:'≋',prompt:'你好，欢迎使用多模态渠道测试台。这是一段语音合成测试，请检查发音、停顿和声音是否自然。'}};
let lastPresetId=null,modelAdvice=null;
let kind='text',records=[],logs=[],controller=null,busy=false,timer=null,inputUrls=[],drafts={},fileDrafts={},referenceFiles=[],legacyLoaded=false;
let progressState=null;
const secrets=new Set(),localMediaUrls=new Set(),historyTasks=new Map();
const draftIds=['preset','model','prompt','size','duration','resolution','voice','format','speed','language','path','auth','timeout','pollInterval','pollPath','contentPath','pollTimeout','extra','batch','imageMode','imageUrls'];
const initialFieldValues=Object.fromEntries(draftIds.map(id=>[id,$(id).value]));
const E=window.MediaEngine;
let modelCatalog=[],modelCatalogLoaded=false,modelFetchState='idle',modelFetchError='',modelFetchEpoch=0,modelFetchController=null;
const modelSelection=window.ModelMultiselect.attach($('model'),{batchInput:$('batch'),label:'渠道模型',ids:{wrapper:'modelPicker',menu:'modelMenu',search:'modelSearch',list:'modelList',status:'modelListStatus',toggle:'modelToggle',clear:'modelClear',all:'modelShowAll'},onChange:()=>{$('allowMismatch').checked=false;updateFields();}});
function discoveryConfig(){return {base:$('base').value.trim(),key:$('key').value.trim(),auth:$('auth').value,preset:$('preset').value,timeout:Math.min(30,Math.max(5,Number($('timeout').value)||30))};}
function discoveryIdentity(c=discoveryConfig()){return JSON.stringify([c.base,c.key,c.auth,c.auth==='gemini'?'gemini':'openai']);}
let modelContextIdentity='';
function syncModelContext(){
  const next=discoveryIdentity();if(next===modelContextIdentity)return;
  const changed=!!modelContextIdentity;
  modelContextIdentity=next;modelFetchEpoch++;modelFetchController?.abort();modelFetchController=null;
  modelCatalog=[];modelCatalogLoaded=false;modelFetchState='idle';modelFetchError='';
  if(changed)modelSelection.clear({emit:false});
  modelSelection.setOptions([],{reconcile:false});
  closeModelMenu();updateModelFetchUI();renderModelOptions();
}
function updateModelFetchUI(){
  const loading=modelFetchState==='loading';$('loadModels').textContent=loading?'重新获取 ↻':modelCatalogLoaded?'刷新模型列表 ↻':'获取模型列表 ↓';
  $('loadModels').disabled=busy;$('cancelModels').hidden=!loading;$('modelPicker').setAttribute('aria-busy',String(loading));
  let hint='获取列表后可勾选多个模型；自定义映射可以手动添加。';
  if(modelCatalogLoaded)hint=`已加载 ${modelCatalog.length} 个模型；下箭头可重复展开，多选后按顺序测试。`;
  if(loading)hint='正在获取模型列表（最多 30 秒）… 可取消，当前选择仍保留。';
  else if(modelFetchState==='error')hint='获取失败：'+modelFetchError+(modelCatalogLoaded?'。已保留上次列表和选择，可刷新重试。':'。可重新获取或手动输入模型 ID。');
  else if(modelFetchState==='canceled')hint='已取消获取。'+(modelCatalogLoaded?'已保留上次列表和选择。':'可以重新获取或手动填写模型。');
  $('modelHint').textContent=scrub(hint);
}
function renderModelOptions(){
  let status='';
  if(modelFetchState==='loading')status='正在获取…'+(modelCatalogLoaded?' · 下面暂显示上次列表':'');
  else if(modelFetchState==='error')status='获取失败：'+modelFetchError+(modelCatalogLoaded?' · 保留上次列表':'');
  else if(modelFetchState==='canceled')status='已取消获取'+(modelCatalogLoaded?' · 保留上次列表':'');
  else if(!modelCatalogLoaded)status='填写地址和密钥后获取列表；下箭头不会自动发请求。';
  modelSelection.setStatus(scrub(status));
}
function openModelMenu({query='',focusSearch=false}={}){
  if(busy)return;syncModelContext();modelSelection.search.value=query;modelSelection.open({focus:focusSearch});renderModelOptions();
}
function closeModelMenu(){modelSelection.close();}
function cancelModelFetch(){
  if(!modelFetchController)return;modelFetchEpoch++;modelFetchController.abort();modelFetchController=null;modelFetchState='canceled';modelFetchError='';updateModelFetchUI();renderModelOptions();
}

const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function scrub(value,truncate=false){
  const sensitive=/^(authorization|x-api-key|x-goog-api-key|api[_-]?key|access_token|refresh_token|secret|key)$/i;
  function visit(v,k='',seen=new WeakSet()){
    if(sensitive.test(k))return '[已隐藏]';
    if(typeof v==='string'){
      for(const s of secrets)if(s){v=v.split(s).join('[已隐藏]');v=v.split(encodeURIComponent(s)).join('[已隐藏]');}
      v=v.replace(/([?&](?:api[_-]?key|key|access_token|refresh_token|secret)=)[^&#\s"<>]*/gi,'$1[已隐藏]');
      v=v.replace(/(Bearer\s+)[^\s"<>]+/gi,'$1[已隐藏]');
      if(truncate&&v.length>16000)return v.slice(0,2000)+`\n… [省略 ${v.length-2000} 个字符]`;
      return v;
    }
    if(v&&typeof v==='object'){
      if(seen.has(v))return '[重复引用]';seen.add(v);
      if(Array.isArray(v))return v.map(x=>visit(x,'',seen));
      const r={};for(const [key,val]of Object.entries(v))r[key]=visit(val,key,seen);return r;
    }
    return v;
  }
  return visit(value);
}
function notify(message,error=false){$('notice').hidden=!message;$('notice').className='notice'+(error?' error':'');$('notice').textContent=scrub(message);}
function log(message,type='info'){
  if(!logs.length)$('logs').replaceChildren();
  const row={time:new Date().toLocaleTimeString('zh-CN',{hour12:false}),message:scrub(String(message)),type};logs.push(row);
  const p=node('p',type);p.append(node('time','',row.time),document.createTextNode(row.message));$('logs').append(p);$('logs').scrollTop=$('logs').scrollHeight;$('logCount').textContent=logs.length+' 条';
}
function selectedPreset(){return E.presets.find(p=>p.id===$('preset').value)||E.presets[0];}
function fillPresets(){
  $('preset').replaceChildren();
  for(const p of E.presets.filter(p=>p.kind===kind))$('preset').add(new Option(p.label,p.id));
}
function saveDraft(){drafts[kind]=Object.fromEntries(draftIds.map(id=>[id,$(id).value]));fileDrafts[kind]=[...referenceFiles];}
function syncFileInput(){
  if(typeof DataTransfer==='undefined')return;
  try{const transfer=new DataTransfer();referenceFiles.forEach(file=>transfer.items.add(file));$('files').files=transfer.files;}catch{ /* referenceFiles remains the source of truth when FileList assignment is unavailable. */ }
}
function fileKey(file){return [file.name,file.size,file.type].join('|');}
function acceptsReferenceFiles(){return !['openai-speech','gemini-speech','openai-image','relay-video-json','custom-video'].includes($('preset').value)&&!(kind==='image'&&$('imageMode').value==='generation');}
function activeReferenceFiles(){return acceptsReferenceFiles()?[...referenceFiles]:[];}
function clearFiles(){referenceFiles=[];$('files').value='';renderInputPreview();}
function sampleSvg(kind,index=1){
  const palettes=[['#f8eee8','#cf5b4d','#f4c95d'],['#eaf3ed','#4f8b72','#7fa9d8'],['#f2edf8','#8a5ca8','#e69a56']];
  const [bg,accent,accent2]=palettes[(index-1)%palettes.length];
  const title=kind==='multi'?`参考图 ${index} · ${index===1?'主体':'场景'}`:'视觉理解示例';
  const body=kind==='multi'&&index===1
    ? `<rect x=190 y=155 width=220 height=210 rx=18 fill="${accent}"/><circle cx=300 cy=210 r=42 fill="${accent2}"/><rect x=245 y=270 width=110 height=55 rx=8 fill="#fff"/><text x=300 y=306 text-anchor="middle" font-size=24 fill="#233d32">A-17</text>`
    : kind==='multi'
      ? `<rect x=0 y=150 width=640 height=210 fill="${accent}"/><circle cx=112 cy=190 r=44 fill="${accent2}"/><path d="M0 430 L190 250 360 430Z" fill="#d7e7ef"/><path d="M330 430 L500 210 640 430Z" fill="#b9d2c0"/>`
      : `<circle cx=180 cy=250 r=78 fill="#e85d55"/><rect x=285 y=172 width=145 height=145 rx=18 fill="#4e83c4"/><path d="M500 320 L570 180 640 320Z" fill="#f0c54f"/><text x=320 y=380 text-anchor="middle" font-size=30 fill="#233d32">7</text>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect width="640" height="480" rx="22" fill="${bg}"/><text x="32" y="52" font-family="sans-serif" font-size="22" font-weight="600" fill="#233d32">${title}</text>${body}<text x="32" y="445" font-family="sans-serif" font-size="16" fill="#6e7e73">内置示例 · 可直接用于测试</text></svg>`;
}
async function samplePngFile(kind,index){
  const svgBlob=new Blob([sampleSvg(kind,index)],{type:'image/svg+xml'}),url=URL.createObjectURL(svgBlob);
  try{
    const image=await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('示例图片渲染失败'));img.src=url;});
    const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0,640,480);
    const png=await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('示例图片编码失败')),'image/png'));
    return new File([png],`示例参考图-${index}.png`,{type:'image/png'});
  }finally{URL.revokeObjectURL(url);}
}
async function loadSampleReferences(count=1){
  if(busy||!acceptsReferenceFiles())return;
  const total=Math.max(1,Number(count)||1),files=[];
  try{for(let i=1;i<=total;i++)files.push(await samplePngFile(total>1?'multi':'vision',i));}
  catch(error){notify(`示例图片加载失败：${error.message||'未知错误'}`,true);return;}
  referenceFiles=files;renderInputPreview();syncFileInput();notify(`已加载 ${total} 张内置 PNG 示例图片，可直接开始测试。`);
}
function restoreFiles(files=[]){referenceFiles=[...files];renderInputPreview();syncFileInput();}
function renderInputPreview(){
  inputUrls.forEach(URL.revokeObjectURL);inputUrls=[];$('inputPreview').replaceChildren();
  referenceFiles.forEach((file,index)=>{
    const row=node('div','reference-item');row.dataset.index=String(index);
    const url=URL.createObjectURL(file);inputUrls.push(url);
    if(file.type.startsWith('image/')||/\.(png|jpe?g|webp|gif|avif|bmp)$/i.test(file.name)){
      const img=node('img');img.src=url;img.alt=file.name;row.append(img);
      const info=node('div','reference-info');info.append(node('b','',`${index+1}. ${file.name}`),node('small','',file.size<1024*1024?`${Math.max(1,Math.round(file.size/1024))} KB`:`${(file.size/1024/1024).toFixed(2)} MB`));row.append(info);
      const controls=node('div','reference-controls');
      const up=node('button','reference-action','↑');up.type='button';up.title='上移';up.setAttribute('aria-label','上移 '+file.name);up.dataset.action='move-up';up.disabled=index===0;
      const down=node('button','reference-action','↓');down.type='button';down.title='下移';down.setAttribute('aria-label','下移 '+file.name);down.dataset.action='move-down';down.disabled=index===referenceFiles.length-1;
      const remove=node('button','reference-action remove','×');remove.type='button';remove.title='移除';remove.setAttribute('aria-label','移除 '+file.name);remove.dataset.action='remove-reference';controls.append(up,down,remove);row.append(controls);
    }else{
      const audio=node('audio');audio.controls=true;audio.src=url;row.append(audio,node('small','',file.name));
      const remove=node('button','reference-action remove','移除');remove.type='button';remove.dataset.action='remove-reference';row.append(remove);
    }
    $('inputPreview').append(row);
  });
  $('fileSummary').textContent=referenceFiles.length?`已添加 ${referenceFiles.length} 个文件`:'';
  $('clearFilesBtn').disabled=!referenceFiles.length;
  const parked=referenceFiles.length&&!acceptsReferenceFiles();$('parkedFiles').hidden=!parked;
  $('parkedFiles').textContent=parked?`已暂存 ${referenceFiles.length} 个附件。当前任务不发送这些附件；切回支持参考文件的任务后可继续编辑或清空。`:'';
  updateScenarioRequirement();
}
// Extract provider usage in a protocol-neutral way.  OpenAI-compatible
// gateways may call these fields prompt_tokens/completion_tokens while other
// providers use input_tokens/output_tokens or nested token_usage objects.
function extractUsage(raw){
  const found=[];const seen=new WeakSet();
  const number=v=>{const n=Number(v);return Number.isFinite(n)&&n>=0?n:null;};
  const walk=(value,depth=0)=>{
    if(!value||typeof value!=='object'||depth>10||seen.has(value))return;
    seen.add(value);
    if(Array.isArray(value)){value.forEach(item=>walk(item,depth+1));return;}
    for(const [key,val] of Object.entries(value)){
      if((key==='usage'||key==='token_usage'||key==='tokenUsage')&&val&&typeof val==='object'){
        const input=number(val.prompt_tokens??val.promptTokens??val.input_tokens??val.inputTokens??val.input);
        const output=number(val.completion_tokens??val.completionTokens??val.output_tokens??val.outputTokens??val.output);
        const total=number(val.total_tokens??val.totalTokens??val.total);
        const cached=number(val.prompt_tokens_details?.cached_tokens??val.input_tokens_details?.cached_tokens??val.cached_tokens??val.cachedTokens);
        if(input!==null||output!==null||total!==null)found.push({input,output,total,cached,source:key});
      }
      walk(val,depth+1);
    }
  };
  walk(raw);
  return found.find(item=>item.input!==null||item.output!==null||item.total!==null)||null;
}
function renderTokenUsage(r){
  const usage=extractUsage(r.raw),tokenScenario=r.scenarioId==='gpt-html-animation'||/鹈鹕骑自行车|SVG绘制.*2D动画/i.test(String(r.config?.prompt||''));
  if(!usage&&!tokenScenario)return null;
  const row=node('div','token-usage');row.append(node('span','token-kicker','TOKEN USAGE'));
  const value=(v)=>v===null||v===undefined?'—':Number(v).toLocaleString('en-US');
  if(!usage){row.append(node('span','token-stat','输入 —'),node('span','token-stat','输出 —'),node('span','token-stat','总计 —'),node('span','token-consistency pending','渠道未返回 usage，无法核对 token 一致性'));return row;}
  row.append(node('span','token-stat','输入 '+value(usage.input)),node('span','token-stat','输出 '+value(usage.output)),node('span','token-stat','总计 '+value(usage.total)));
  if(usage.cached!==null&&usage.cached!==undefined)row.append(node('span','token-stat','缓存 '+value(usage.cached)));
  const hasParts=usage.input!==null&&usage.output!==null,computed=hasParts?usage.input+usage.output:null;
  const consistent=usage.total===null||computed===null?null:usage.total===computed;
  const note=consistent===null?'未提供完整 token 字段':consistent?'输入 + 输出 = 总计，usage 一致':'输入 + 输出 ≠ 总计，请核对渠道 usage 定义';
  const status=node('span','token-consistency '+(consistent===true?'pass':consistent===false?'fail':'pending'),note);row.append(status);return row;
}
function htmlSource(text){
  let source=String(text||'').trim();if(!source)return null;
  source=source.replace(/^```(?:html)?\s*/i,'').replace(/\s*```$/,'').trim();
  if(!/(?:<!doctype\s+html|<html\b|<svg\b|<body\b)/i.test(source))return null;
  return source;
}
function renderHtmlPreview(r){
  const prompt=String(r.config?.prompt||'');
  if(r.scenarioId!=='gpt-html-animation'&&!/鹈鹕骑自行车|SVG绘制.*2D动画/i.test(prompt))return null;
  const source=htmlSource(r.text);if(!source)return null;
  const details=document.createElement('details');details.className='generated-html-preview';
  const summary=document.createElement('summary');summary.textContent='预览生成的 HTML / SVG 动画（沙箱）';details.append(summary);
  const note=node('p','preview-note','仅用于查看模型输出效果；预览运行在隔离 iframe 中，不会再次发起渠道请求。');details.append(note);
  const frame=document.createElement('iframe');frame.className='html-preview-frame';frame.title='模型生成的 HTML / SVG 动画预览';frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');frame.srcdoc=source;details.append(frame);return details;
}
function renderGptAnimationAssessment(r){
  const prompt=String(r.config?.prompt||'');if(r.scenarioId!=='gpt-html-animation'&&!/鹈鹕骑自行车|SVG绘制.*2D动画/i.test(prompt))return null;
  const output=String(r.text||'').trim(),clean=output.replace(/^```(?:html)?\s*/i,'').replace(/\s*```$/,'').trim();
  const checks=[
    ['HTML 文档',/(?:<!doctype\s+html|<html\b|<body\b)/i.test(clean)],
    ['SVG 绘制',/<svg\b/i.test(clean)],
    ['动画效果',/(?:<animate\b|<animateTransform\b|@keyframes\b|animation(?:-name|-duration)?\s*:|requestAnimationFrame\s*\(|setInterval\s*\()/i.test(clean)],
    ['未使用 Markdown 围栏',!/^```|```$/m.test(output)],
    ['未出现拒答',!/抱歉|我不能|无法完成|不能帮助|拒绝/i.test(clean)]
  ];
  const box=node('div','gpt-assessment');box.append(node('b','gpt-assessment-title','GPT 专项判读 · HTML/SVG 降智检查'));
  const list=node('div','gpt-assessment-list');checks.forEach(([label,ok])=>{const row=node('div','gpt-assessment-row');row.append(node('span','',label),node('span',ok?'pass':'fail',ok?'通过':'需核对'));list.append(row);});box.append(list);
  const passed=checks.filter(([,ok])=>ok).length;box.append(node('p','gpt-assessment-note',`启发式结果：${passed}/${checks.length} 项符合预期。请结合下方沙箱预览人工确认鹈鹕、自行车、车轮和连续运动是否真的绘制出来。`));return box;
}
function buildGptEvaluation(r,c){
  const prompt=String(c?.prompt||'');if(!/鹈鹕骑自行车|SVG绘制.*2D动画/i.test(prompt))return null;
  const output=String(r.text||'').trim(),clean=output.replace(/^```(?:html)?\s*/i,'').replace(/\s*```$/,'').trim();
  const hasHtml=/(?:<!doctype\s+html|<html\b|<body\b)/i.test(clean),hasSvg=/<svg\b/i.test(clean),hasAnimation=/(?:<animate\b|<animateTransform\b|@keyframes\b|animation(?:-name|-duration)?\s*:|requestAnimationFrame\s*\(|setInterval\s*\()/i.test(clean),hasBird=/(?:鹈鹕|pelican)/i.test(clean),hasBike=/(?:自行车|单车|bicycle|bike)/i.test(clean),noFence=!/^```|```$/m.test(output),notRefusal=!/抱歉|我不能|无法完成|不能帮助|拒绝/i.test(clean);
  const usage=extractUsage(r.raw);let consistent=null;if(usage?.input!==null&&usage?.output!==null&&usage?.total!==null)consistent=usage.input+usage.output===usage.total;
  const checks={hasHtml,hasSvg,hasBird,hasBike,hasAnimation,noFence,notRefusal};
  return {prompt,html_detected:hasHtml,svg_detected:hasSvg,animation_detected:hasAnimation,html_valid:hasHtml&&hasSvg,token_usage:usage?{input:usage.input,output:usage.output,total:usage.total,cached:usage.cached,consistent}:null,signals:checks,source:clean.slice(0,16000),verdict:hasHtml&&hasSvg&&hasBird&&hasBike&&hasAnimation&&noFence&&notRefusal&&consistent!==false?'passed':'failed'};
}
function updateScenarioRequirement(){
  const min=Number(currentPromptScenario()?.requiresImages||0),imageCount=acceptsReferenceFiles()?referenceFiles.filter(file=>file.type.startsWith('image/')||/\.(png|jpe?g|webp|gif|avif|bmp)$/i.test(file.name)).length:0;
  $('scenarioRequirement').textContent=min&&imageCount<min?`此场景建议至少上传 ${min} 张图片，当前参与请求 ${imageCount} 张。`:'';
}
function promptCategory(){return kind==='audio'&&/transcription|translation/.test($('preset').value)?'transcription':kind;}
function promptScenarios(){return (window.PromptLibrary?.[promptCategory()]||[]).filter(item=>(!item.presets||item.presets.includes($('preset').value))&&(kind!=='image'||(item.modes||['generation']).includes($('imageMode').value)));}
function currentPromptScenario(){return promptScenarios().find(item=>item.id===$('promptScenario').value)||null;}
function updateScenarioDetails(){const item=currentPromptScenario();$('scenarioGoal').textContent=item?.goal||'可自由编辑提示词，也可选择一个测试场景立即填入。';updateScenarioRequirement();}
function updatePromptScenarios(){
  const items=promptScenarios();$('promptScenario').replaceChildren(new Option('自定义提示词',''));
  items.forEach(item=>$('promptScenario').add(new Option(item.name,item.id)));
  $('promptScenario').value=items.find(item=>item.prompt===$('prompt').value)?.id||'';
  updateScenarioDetails();
}
function applyPromptScenario(){
  if(busy)return;
  const item=currentPromptScenario();if(!item){updateScenarioDetails();return;}
  const prompt=$('prompt');prompt.value=item.prompt;updateFields();
  prompt.style.height='auto';prompt.style.height=Math.min(320,Math.max(140,prompt.scrollHeight))+'px';prompt.scrollTop=0;
  notify('已填入「'+item.name+'」测试提示词，可继续编辑。');
}
function handleFilesChange(){
  if(busy)return;
  const incoming=[...$('files').files];if(!incoming.length)return;
  if($('files').multiple){const seen=new Set(referenceFiles.map(fileKey));referenceFiles.push(...incoming.filter(file=>{const key=fileKey(file);if(seen.has(key))return false;seen.add(key);return true;}));}
  else referenceFiles=incoming.slice(0,1);
  renderInputPreview();syncFileInput();
}
function reorderReference(index,delta){const next=index+delta;if(next<0||next>=referenceFiles.length)return;const moved=referenceFiles.splice(index,1)[0];referenceFiles.splice(next,0,moved);renderInputPreview();syncFileInput();}
function removeReference(index){referenceFiles.splice(index,1);renderInputPreview();syncFileInput();}
function updateImageMode(){
  if(busy||kind!=='image')return;const mode=$('imageMode').value;
  const id=mode==='edit'?'openai-image-edit':mode==='reference'?'relay-image-json':'openai-image';
  if($('preset').value!=='gemini-image'&&$('preset').value!==id){$('preset').value=id;applyPreset();}
  updateFields();
}
function setTextMode(mode){
  if(busy)return;
  const deep=kind==='text'&&mode==='deep';closeModelMenu();
  if(deep&&!legacyLoaded){if(window.LEGACY_HTML)$('legacyFrame').srcdoc=window.LEGACY_HTML;else $('legacyFrame').src='legacy.html';legacyLoaded=true;}
  $('textModes').hidden=kind!=='text';$('textKindCard').classList.toggle('active',kind==='text');$('basicView').hidden=deep;$('legacyView').hidden=!deep;
  $('basicBtn').classList.toggle('active',!deep);$('basicBtn').setAttribute('aria-selected',String(!deep));
  $('legacyBtn').classList.toggle('active',deep);$('legacyBtn').setAttribute('aria-selected',String(deep));
}
function switchKind(next){
  if(busy)return;if(next===kind){setTextMode('basic');return;}
  closeModelMenu();window.ChoicePickers?.closeAll();saveDraft();$('allowMismatch').checked=false;kind=next;fillPresets();clearFiles();
  if(drafts[kind])for(const[id,v]of Object.entries(drafts[kind]))$(id).value=v;
  else{for(const[id,v]of Object.entries(initialFieldValues))if(id!=='preset')$(id).value=v;$('prompt').value=kinds[kind].prompt;applyPreset();}
  lastPresetId=$('preset').value;
  if(fileDrafts[kind])restoreFiles(fileDrafts[kind]);
  document.querySelectorAll('.kind-tab[data-kind]').forEach(b=>{b.classList.toggle('active',b.dataset.kind===kind);b.setAttribute('aria-selected',b.dataset.kind===kind?'true':'false');});
  modelSelection.syncFromInputs();setTextMode('basic');updateFields();renderInputPreview();notify('');
}
function applyPreset(){
  window.ChoicePickers?.closeAll();
  const p=selectedPreset();$('path').value=p.path||'';$('pollPath').value=p.pollPath||'';$('contentPath').value=p.contentPath||'';
  $('auth').value=p.auth||'bearer';
  if(['openai-speech','openai-audio-chat','gemini-speech'].includes(p.id)){$('voice').value=p.defaults?.voice||'alloy';$('format').value=p.defaults?.format||'wav';}
  if(p.id!==lastPresetId)$('allowMismatch').checked=false;lastPresetId=p.id;
  if(kind==='image'&&p.id!=='gemini-image')$('imageMode').value=p.id==='openai-image-edit'?'edit':p.id==='relay-image-json'?'reference':'generation';
  updateFields();renderInputPreview();
}
function updateFields(){
  const p=selectedPreset();$('presetDescription').textContent=p.description||'';
  $('model').placeholder=p.defaults?.model?'例如 '+p.defaults.model:'填写渠道实际使用的模型 ID';
  const speech=p.id==='openai-speech',audioChat=p.id==='openai-audio-chat',geminiSpeech=p.id==='gemini-speech',transcribe=/transcription|translation/.test(p.id),edit=p.id==='openai-image-edit';
  const options={size:(kind==='image'&&p.id!=='gemini-image')||kind==='video',duration:kind==='video',resolution:['relay-video-json','doubao-video','custom-video'].includes(p.id),voice:speech||audioChat||geminiSpeech,format:speech||audioChat,speed:speech,language:p.id==='openai-transcription'};
  $('imageModeGroup').hidden=kind!=='image';
  $('imageModeHint').textContent=p.id==='gemini-image'?'Gemini 原生协议支持文生图、图片编辑及多图参考；已保留当前任务。':'切换任务会选择对应的常用协议；也可以在下方选择渠道实际支持的协议。';
  document.querySelectorAll('[data-option]').forEach(el=>el.hidden=!options[el.dataset.option]);
  $('resolutionHint').textContent=/seedance/i.test($('model').value)?'部分 Seedance 渠道要求此项；请选择渠道支持的值，如 720p 或 1080p。':'独立于画面尺寸；可填写渠道支持的自定义值。';
  document.querySelectorAll('.video-only').forEach(el=>el.hidden=kind!=='video');
  $('fileGroup').hidden=!acceptsReferenceFiles();
  const urlMode=kind==='image';$('imageUrlGroup').hidden=!urlMode;
  $('imageUrlGroup').querySelector('label').firstChild.textContent=p.id==='relay-image-json'?'参考图片 URL ':'图片 URL（JSON 参考图协议） ';
  $('imageUrlGroup').querySelector('small').innerHTML=p.id==='relay-image-json'?'每行一个公开的 http(s) 图片地址；会按顺序写入请求体的 <code>image</code> 字段，可与本地上传图片混合使用。':'当前协议主要使用本地上传；如渠道支持 URL 参考图，请切换到「中转站 · 参考图生成（JSON）」后再粘贴地址。';
  const supportsImages=['openai-image-edit','relay-image-json','gemini-image','openai-chat','openai-responses','anthropic','gemini','doubao-video'].includes(p.id);
  $('files').accept=audioChat?'.wav,.mp3,audio/wav,audio/mpeg':transcribe?'audio/*,video/mp4,video/webm':'image/*';$('files').multiple=kind==='text'||supportsImages;
  $('fileLabel').textContent=audioChat?'音频输入（可选）':transcribe?'上传音频（必选)':edit?'原始图片（可多张）':kind==='video'?'参考图片（可选）':p.id==='relay-image-json'?'参考图片（可多图，也可用 URL）':'参考图片（可选）';
  $('fileHint').textContent=audioChat?'支持上传 WAV / MP3，也可只输入文本，测试模型的音频回答。':transcribe?'选择待转写 / 翻译的音频；文件大小限制由渠道决定。':supportsImages?'可继续添加图片；缩略图下方可调整顺序或移除。':'文件随本次请求上传至你配置的渠道。';
  $('promptLabel').textContent=(speech||geminiSpeech)?'朗读文本':transcribe?'转写提示词（可选）':'测试提示词';
  const count=($('batch').value.trim()?$('batch').value.split(/[,，\s]+/).filter(Boolean):[$('model').value.trim()].filter(Boolean)).length||1;$('runHint').textContent=`将向当前渠道提交 ${count} 次${transcribe?'识别':'生成'}请求，按渠道规则计费。`;
  const notes={ 'relay-video-json':'中转站常用 JSON 请求。原生表单不兼容时可选此项；查询地址仍以渠道文档为准。', 'openai-video':'此选项发送 multipart 表单。若出现 unmarshal / invalid character 错误，可切换中转站 Videos（JSON）。', 'doubao-video':'仅适用于暴露火山方舟原生任务接口的渠道；404 通常表示该渠道没有提供这个路径。', 'openai-speech':'仅用于兼容 /v1/audio/speech 的语音合成模型。gpt-audio / audio-preview 请选音频对话；Gemini TTS 请选原生语音生成。', 'openai-audio-chat':'使用 Chat Completions 的音频输入 / 输出协议，需要渠道透传 modalities 与 audio 字段。普通文本模型不能因此变成音频模型。', 'gemini-speech':'使用 Gemini 原生 TTS 协议，音色如 Kore、Puck、Aoede。PCM 返回会转换为可播放的 WAV。', 'openai-transcription':'只做录音转文字，需要先上传录音。模型 ID 应是渠道提供的转写模型。', 'openai-translation':'上传音频并翻译为英文文本；不是任意语言翻译或语音合成。' };
  $('protocolNote').textContent=notes[p.id]||'';$('protocolNote').hidden=!notes[p.id];
  $('voiceLabel').textContent=geminiSpeech?'Gemini 音色':'音色';
  for(const option of $('format').options)option.disabled=audioChat&&!['wav','mp3','opus','flac','pcm'].includes(option.value);
  updatePromptScenarios();syncModelContext();updateModelAdvice();updateCoverage();renderInputPreview();
}
function recommendModel(model){
  const id=String(model||'').toLowerCase();
  if(/seedream/.test(id))return {kind:'image',preset:'openai-image',note:'Seedream 通常是图像生成模型，视频系列叫 Seedance',name:'图像生成'};
  if(/seedance|(?:^|[-/])sora(?:[-/]|$)|(?:^|[-/])veo(?:[-/\d]|$)|kling.*(?:video|v\d)|wan\d.*(?:t2v|i2v)/.test(id))return {kind:'video',preset:'relay-video-json',note:'这个名称通常对应视频生成模型，具体协议仍以渠道文档为准',name:'视频生成'};
  if(/gemini.*(?:tts|text-to-speech)/.test(id))return {kind:'audio',preset:'gemini-speech',note:'Gemini TTS 使用原生语音生成协议和专用音色',name:'Gemini 语音生成',strict:true};
  if(/(?:gpt.*(?:audio|realtime)|audio-preview)/.test(id))return /realtime/.test(id)?{kind:'audio',preset:'openai-audio-chat',note:'Realtime 通常需要实时 WebSocket / WebRTC 协议；本页音频对话仅适用于渠道同时提供 Chat Completions 映射的模型',name:'音频对话',strict:true,always:true}:{kind:'audio',preset:'openai-audio-chat',note:'这个名称通常使用 Chat Completions 音频对话接口，而不是 Speech 接口',name:'音频对话',strict:true};
  if(/whisper|transcrib/.test(id))return {kind:'audio',preset:'openai-transcription',alternatives:/whisper/.test(id)?['openai-translation']:[],note:'这是转写模型名称，通常需要上传音频并调用转写 / 翻译接口',name:'音频转文字',strict:true};
  if(/(?:^|[-/])tts(?:[-/]|$)|(?:text-to-speech)/.test(id))return {kind:'audio',preset:'openai-speech',note:'这个名称通常对应语音合成模型',name:'语音合成'};
  if(/gpt-image|dall-e|flux|stable-diffusion|imagen/.test(id))return {kind:'image',preset:'openai-image',note:'这个名称通常对应图像生成模型',name:'图像生成'};
  return null;
}
function isMismatch(advice){return !!advice&&(advice.kind!==kind||advice.always||advice.strict&&advice.preset!==$('preset').value&&!advice.alternatives?.includes($('preset').value));}
function updateModelAdvice(){
  const models=($('batch').value.trim()?$('batch').value.split(/[,，\s]+/):[$('model').value]).filter(Boolean);
  const item=models.map(model=>({model,advice:recommendModel(model)})).find(x=>isMismatch(x.advice));modelAdvice=item?{...item.advice,model:item.model}:null;
  $('modelAdvice').hidden=!item;if(!item)return;
  $('modelAdviceText').textContent=item.model+'：'+item.advice.note+'。提示依据模型名称推测，不会自动改模型或发请求。';
  $('applyModelAdvice').textContent='改用'+item.advice.name+' →';
}
function applyRecommendation(advice,model){
  if(busy)return;const savedModel=model||advice.model||$('model').value,oldPrompt=$('prompt').value,oldKind=kind;
  switchKind(advice.kind);$('preset').value=advice.preset;applyPreset();$('model').value=savedModel;$('batch').value='';modelSelection.syncFromInputs();
  if(oldKind===kind)$('prompt').value=oldPrompt;updateFields();notify('已调整测试配置，尚未发送请求。请检查提示词、模型和音色后开始测试。');$('model').scrollIntoView({behavior:'smooth',block:'center'});
}
function updateCoverage(){
  const generation=kind==='image'?'图像':kind==='video'?'视频':kind==='audio'?'音频 / 转写':'文本';
  $('coverageTitle').textContent=generation+' · 本次检测范围';
  $('coverageItems').textContent='HTTP 状态 · 请求耗时 · 输出结构'+(kind==='video'?' · 异步任务状态':'')+(kind!=='text'?' · 媒体加载 / 播放':' · 回答内容');
  $('coverageLimit').textContent='未测：模型真实身份、计费准确性、长期稳定性与内容质量。流式、参数、上下文和并发等专项检查，请在「文本模型」中选择「深度检测」。';
}
function diagnoseResult(r){
  let message='',advice=null,guidance=[];
  const error=String(r.error||'').trim();
  const requests=Array.isArray(r.requests)?r.requests:[];
  const lastRequest=requests.length?requests[requests.length-1]:null;
  const httpStatus=Number(lastRequest?.status);
  const hasHttpStatus=Number.isInteger(httpStatus)&&httpStatus>0;
  const lower=error.toLowerCase();
  const networkFailure=/failed to fetch|networkerror|load failed|cors|跨域|dns|econnrefused|enotfound|socket hang up|connection reset|network request failed/i.test(error)
    || (!!requests.length&&requests.every(item=>item?.status===null||item?.status===undefined));
  const mixed=location.protocol==='https:'&&/^http:/i.test(String(r.config?.base||''));
  if(networkFailure){
    message='错误类型：浏览器网络请求失败（Failed to fetch）。';
    const evidence=requests.length
      ? `证据：已记录 ${requests.length} 次请求，但没有收到可读的 HTTP 状态码。`
      : '证据：浏览器在收到 HTTP 响应前中断了请求。';
    guidance=[
      evidence,
      '可能原因：'+(mixed
        ?'当前工作台使用 HTTPS，而渠道地址使用 HTTP，浏览器按混合内容策略拦截了请求。'
        :'渠道没有返回可读的 HTTP 响应；常见原因是 CORS 未放行、OPTIONS 预检失败、域名 / DNS / TLS 不可达、端口被防火墙拦截，或提交路径被网关拒绝。'),
      '解决方法：先在同一浏览器打开渠道地址确认网络可达；让渠道允许当前工作台 Origin 的 POST 和 OPTIONS，并在响应中开放 Authorization、Content-Type；统一使用 HTTPS，核对 Base URL、提交路径、鉴权方式和模型 ID。渠道不支持浏览器跨域时，请改用服务端代理或同源网关。'
    ];
  }else if(/timeout|timed out|超时/i.test(lower)||r.error?.name==='TimeoutError'){
    message='错误类型：请求超过了本页设置的超时时间。';
    guidance=[
      requests.length?`证据：最近一次请求已等待，已记录 ${requests.length} 次请求。`:'证据：未收到完整响应。',
      '可能原因：渠道排队、上游生成耗时过长，或网络连接在等待期间中断。视频等异步任务还可能已经提交成功，只是查询超时。',
      '解决方法：先查看请求日志和任务 ID；确认渠道服务状态后适当增大“单次请求超时”或“最多等待视频”，再使用“继续查询”而不是重复生成。'
    ];
  }else if(hasHttpStatus&&httpStatus>=400&&!/UpstreamError|upstream call failed|fail_to_fetch_task/i.test(error)){
    const statusHint=httpStatus===401||httpStatus===403
      ?'鉴权失败：API Key 无效、已过期，或鉴权方式 / 请求来源不被渠道接受。'
      :httpStatus===404
        ?'地址或模型不存在：提交路径、Base URL 版本、查询路径或模型 ID 可能不匹配。'
        :httpStatus===408||httpStatus===429
          ?'渠道暂时无法及时处理：可能是请求超时、限流、余额 / 配额不足或上游排队。'
          :httpStatus===413||httpStatus===415||httpStatus===422
            ?'请求体不符合渠道约束：可能是字段、格式、文件大小、图片数量或模型能力不支持。'
            :httpStatus>=500
              ?'渠道或上游服务发生错误，通常需要结合渠道日志和 Request ID 排查。'
              :'渠道明确拒绝了请求，需要按返回的错误字段核对协议和参数。';
    message=`错误类型：渠道返回 HTTP ${httpStatus}。`;
    guidance=[
      `证据：最近一次请求收到 HTTP ${httpStatus}${error?'，页面已保留返回的错误详情。':'。'} `,
      '可能原因：'+statusHint,
      '解决方法：展开“查看原始响应与请求记录”，按 error.message / code 修正；重点核对模型 ID、接口路径、鉴权方式、请求体字段和文件格式。401/403 先更新密钥，429 先降低频率并检查配额，5xx 需要联系渠道方核对上游日志。'
    ];
  }
  if(r.kind==='video'&&/resolution/i.test(error)&&/missing|required|缺少|必填/i.test(error)){
    const box=node('div','diagnostic');box.append(node('p','diagnostic-title','错误类型：渠道拒绝了请求，缺少视频分辨率 resolution。'),node('p','','画面尺寸 size 不能代替此字段。请在左侧「视频分辨率」选择渠道支持的值（例如 720p、1080p），再手动开始测试。'));
    if(kind==='video'&&['relay-video-json','doubao-video','custom-video'].includes($('preset').value)){
      const btn=node('button','text-button','填写视频分辨率 →');btn.type='button';btn.dataset.action='configure-resolution';
      btn.addEventListener('click',()=>{if(busy)return;if(kind!=='video'||!['relay-video-json','doubao-video','custom-video'].includes($('preset').value)){notify('请在视频模型中选择对应的 JSON 协议，再填写视频分辨率。');return;}$('resolution').scrollIntoView({behavior:'smooth',block:'center'});$('resolution').focus({preventScroll:true});notify('请按渠道文档选择视频分辨率。尚未重新提交请求。');});box.append(btn);
    }
    return box;
  }else if(/UpstreamError|upstream call failed|fail_to_fetch_task/i.test(error)){
    const id=error.match(/Request ID\s*:\s*([a-zA-Z0-9_-]+)/i)?.[1];
    message='错误类型：请求已到达中转站，但中转站调用上游失败。';
    guidance=[
      '证据：'+(id?'响应包含 Request ID '+id+'，可用于定位本次上游调用。':'响应未提供 Request ID，请使用测试时间和模型 ID 查询渠道日志。'),
      '可能原因：上游服务异常、模型映射错误、渠道余额 / 配额不足，或请求参数被上游拒绝。',
      '解决方法：按'+(id?' Request ID '+id:'请求时间')+'查询中转站 / 上游日志，核对服务状态、模型映射与参数。'+(r.taskId?'已有任务 ID，可核对任务状态后继续查询。':'本次响应未提供任务 ID；先确认是否已产生计费，再决定是否重新生成。')
    ];
  }else if(r.kind==='video'&&/unmarshal|invalid character|json.*(?:parse|decode)/i.test(error)&&r.config?.preset==='openai-video'){
    message='渠道可能在按 JSON 解码 multipart 表单。可换用「中转站 · Videos（JSON）」；模型是否支持视频仍需单独确认。';advice={kind:'video',preset:'relay-video-json',name:'中转站 JSON 视频协议'};
  }else if(r.kind==='video'&&/404|Invalid URL/i.test(error)&&r.config?.preset==='doubao-video'){
    message='该渠道没有提供火山方舟原生任务路径。可改用中转站 JSON 协议，并按渠道文档核对提交与查询路径。';advice={kind:'video',preset:'relay-video-json',name:'中转站 JSON 视频协议'};
  }else if(!message&&r.kind==='audio'){
    const suggested=recommendModel(r.model);if(suggested&&suggested.kind==='audio'&&suggested.preset!==r.config?.preset){message=suggested.note;advice=suggested;}
    else if(/voice|speaker|音色/i.test(error))message='渠道拒绝了音色参数。不同模型支持的音色不同，请填写该模型文档中的 voice / 音色名称。';
    else if(/404|Invalid URL|not found/i.test(error))message='当前音频路径或模型不存在。语音合成、转写、音频对话和 Gemini TTS 使用不同接口，请按渠道文档选择协议。';
    else message='音频接口、音色和返回格式因模型而异。请核对模型类型；下方原始响应保留了渠道错误详情。';
  }
  if(r.kind==='image'&&/400|401|403|404|422|invalid|unsupported|model|parameter|image/i.test(error)){
    message='错误类型：图片接口或参数被渠道拒绝。';
    guidance=['可能原因：模型 ID、接口路径、鉴权方式或请求体字段与渠道协议不匹配；图生图还可能是 image URL 过期、渠道无法抓取外链、图片格式 / 数量 / 尺寸不符合限制。','解决方法：展开“查看原始响应与请求记录”，先按 HTTP 状态和 error.message 修正；图生图请选择“中转站 · 参考图生成（JSON）”，把每个 URL 放入“参考图片 URL”（每行一个），或在附加 JSON 中完整填写 image 数组，并确认模型文档要求的 aspect_ratio、size、response_format 和提交路径。'];
  }
  if(!message&&error){
    message='错误类型：请求失败，暂时无法自动归类。';
    guidance=[
      '可能原因：渠道返回了无法识别的错误，或浏览器 / 网关在请求过程中中断了连接。',
      '解决方法：展开“查看原始响应与请求记录”，根据 HTTP 状态、error.message、请求路径和模型 ID 排查；修正配置后再重试。'
    ];
  }
  if(!message)return null;const box=node('div','diagnostic');box.append(node('p','diagnostic-title',message));guidance.forEach(item=>box.append(node('p','',item)));
  if(advice){const btn=node('button','text-button','载入'+advice.name);btn.addEventListener('click',()=>applyRecommendation(advice,r.model));box.append(btn);}
  return box;
}
function renderCoverage(body,r){
  if(r.status==='demo')return;
  const box=node('details','coverage-checks'),summary=node('summary'),summaryStatus=node('span','coverage-summary');
  summary.append(node('span','','检测记录'),summaryStatus);box.append(summary);box.open=r.status!=='success';
  const updateSummary=()=>{
    const rows=[...box.querySelectorAll('.coverage-row')],passed=rows.filter(row=>row.querySelector('.pass')).length,failed=rows.some(row=>row.querySelector('.fail'));
    summaryStatus.textContent=`${passed} / ${rows.length} 项已确认`+(failed?' · 存在异常':' · 展开详情');
    if(failed)box.open=true;
  };
  const requests=r.requests||[],httpOK=requests.length&&requests.every(x=>Number(x.status)>=200&&Number(x.status)<300);
  const checks=[['接口响应',requests.length?(httpOK?'已收到 HTTP 成功响应':'存在错误 / 中断'):'未发出请求',httpOK?'pass':'pending'],['输出内容',r.status==='success'?'已识别与测试类型匹配的输出':'尚未获得有效输出',r.status==='success'?'pass':'pending']];
  if(r.kind==='video')checks.push(['异步任务',r.taskId?(r.status==='success'?'任务已返回成品':r.status==='pending'?'仍在等待':r.status==='error'?'任务查询 / 生成失败':'任务未确认完成'):'未返回任务 ID',r.taskId&&r.status==='success'?'pass':'pending']);
  for(const[label,value,cls]of checks){const row=node('div','coverage-row');row.append(node('span','',label),node('span',cls,value));box.append(row);}
  for(const media of body.querySelectorAll('.media-item img,.media-item audio,.media-item video')){
    const image=media.tagName==='IMG',row=node('div','coverage-row'),value=node('span','pending','等待加载');row.append(node('span','',image?'图片解码':media.tagName==='VIDEO'?'视频加载':'音频加载'),value);box.append(row);
    const ok=()=>{value.textContent=image?'可显示':'媒体已加载；播放质量需试听 / 观看';value.className='pass';updateSummary();};const fail=()=>{value.textContent='浏览器无法加载 / 解码';value.className='fail';updateSummary();};media.addEventListener(image?'load':'loadeddata',ok);media.addEventListener('error',fail);if(image&&media.complete&&media.naturalWidth||!image&&media.readyState>=2)ok();else if(media.error)fail();
  }
  box.append(node('small','','仅确认实际完成的检测项。未测：模型真实身份、输出质量、计费与长期性能。播放成功不等于语义正确。'));updateSummary();body.append(box);
}
function getModels(requireModel=true){
  const list=$('batch').value.trim()?$('batch').value.split(/[,，\s]+/).filter(Boolean):[$('model').value.trim()].filter(Boolean);
  const unique=[...new Set(list)];if(requireModel&&!unique.length)throw new Error('请填写模型名称，或在高级参数中填写批量模型。');
  if(unique.length>30)throw new Error('每批最多 30 个模型，请分批测试。');return unique;
}
function getConfig(model){
  const key=$('key').value.trim();if(key)secrets.add(key);
  let extra;try{extra=JSON.parse($('extra').value.trim()||'{}');}catch{throw new Error('附加参数不是有效 JSON，请检查逗号和引号。');}
  if(!extra||typeof extra!=='object'||Array.isArray(extra))throw new Error('附加参数必须是 JSON 对象。');
  if(Object.prototype.hasOwnProperty.call(extra,'model')&&(typeof extra.model!=='string'||!extra.model.trim()))throw new Error('附加 JSON 中的 model 必须是非空字符串。');
  const referenceUrls=$('imageUrls').value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
  if(referenceUrls.length){
    if($('preset').value!=='relay-image-json')throw new Error('图片 URL 需要选择「中转站 · 参考图生成（JSON）」协议；OpenAI multipart 图片编辑请先下载后上传。');
    if(Object.prototype.hasOwnProperty.call(extra,'image'))throw new Error('已填写参考图片 URL，请移除附加 JSON 中的 image 字段，避免重复发送。');
    for(const [index,value] of referenceUrls.entries()){let parsed;try{parsed=new URL(value);}catch{throw new Error(`第 ${index+1} 个参考图片 URL 无效。`);}if(!/^https?:$/.test(parsed.protocol)||parsed.username||parsed.password)throw new Error(`第 ${index+1} 个参考图片 URL 必须是公开的 http(s) 地址。`);referenceUrls[index]=parsed.href;}
  }
  if(!model)throw new Error('请填写模型名称。');
  if(!$('base').value.trim())throw new Error('请填写渠道地址。');
  if($('auth').value!=='none'&&!key)throw new Error('请填写 API Key。');
  const number=(id,min,max)=>{const v=Number($(id).value);if(!Number.isFinite(v)||v<min||v>max)throw new Error(`${$(id).previousElementSibling?.textContent||id}应在 ${min}–${max} 之间。`);return v;};
  return {base:$('base').value.trim(),key,model,preset:$('preset').value,path:$('path').value.trim(),auth:$('auth').value,prompt:$('prompt').value,extra,referenceUrls,files:activeReferenceFiles(),timeout:number('timeout',5,3600),pollInterval:kind==='video'?number('pollInterval',1,120):5,pollTimeout:kind==='video'?number('pollTimeout',5,7200):600,pollPath:$('pollPath').value.trim(),contentPath:$('contentPath').value.trim(),voice:$('voice').value.trim(),format:['openai-speech','openai-audio-chat'].includes($('preset').value)?$('format').value:undefined,speed:$('preset').value==='openai-speech'?number('speed',.25,4):1,size:$('size').value.trim(),resolution:['relay-video-json','doubao-video','custom-video'].includes($('preset').value)?$('resolution').value.trim():undefined,duration:kind==='video'?number('duration',1,120):4,language:$('language').value.trim(),fetchMedia:true};
}
function safeSnapshot(c){const {key,files,...rest}=c;return scrub({...rest,files:files.map(f=>({name:f.name,type:f.type,size:f.size}))});}
function durationLabel(seconds){
  const value=Math.max(0,Math.ceil(Number(seconds)||0));
  if(value<60)return value+' 秒';
  if(value<3600)return Math.floor(value/60)+' 分 '+value%60+' 秒';
  return Math.floor(value/3600)+' 小时 '+Math.floor(value%3600/60)+' 分';
}
function resetProgress(){
  progressState=null;$('progressDashboard').hidden=true;$('elapsed').textContent='';$('runningLabel').textContent='准备请求';$('runningDetail').textContent='正在检查配置…';
}
function beginProgress(c,index,total,resumeId){
  progressState={startedAt:Date.now(),requestDeadline:null,pollDeadline:null,nextPollAt:null,pollCount:0,taskId:resumeId||null,stage:resumeId?'polling':'submitting',status:'',percent:null,etaAt:null,etaOrigin:null,observations:[],index,total,resuming:!!resumeId,lastUpdate:null,inRequest:false,requestTimeout:c.timeout};
  $('progressDashboard').hidden=false;renderProgress();
}
function recordProgress(e){
  const s=progressState;if(!s)return;const now=Date.now();
  if(e.type==='request'){
    s.inRequest=true;s.requestDeadline=now+(Number(e.timeoutSeconds)||s.requestTimeout)*1000;s.nextPollAt=null;
  }else if(e.type==='response'){
    s.inRequest=false;s.requestDeadline=null;s.lastUpdate=now;
  }else if(e.type==='progress'){
    s.stage=e.stage||s.stage;if(e.status!==undefined)s.status=e.status;
    if(e.taskId)s.taskId=e.taskId;
    if(Number.isFinite(e.pollDeadline))s.pollDeadline=e.pollDeadline;
    if(e.nextPollAt!==undefined)s.nextPollAt=e.nextPollAt;
    if(Number.isFinite(e.pollCount))s.pollCount=e.pollCount;
    if(e.queuePosition!==undefined)s.queuePosition=e.queuePosition;
    const telemetry=['queued','processing','complete'].includes(e.stage);
    if(telemetry){
      s.lastUpdate=now;
      if(Number.isFinite(e.progressPercent)){
        const percent=Math.max(0,Math.min(100,e.progressPercent));
        if(s.percent!==null&&percent<s.percent)s.observations=[];
        s.percent=percent;
        if(s.observations.at(-1)?.percent!==percent){s.observations.push({percent,time:now});s.observations=s.observations.slice(-8);}
      }else s.percent=null;
      s.etaAt=Number.isFinite(e.remainingSeconds)?now+e.remainingSeconds*1000:null;
      s.etaOrigin=s.etaAt!==null?'channel':null;
      if(!s.etaOrigin&&s.percent!==null&&s.percent<100&&e.stage==='processing'){
        const first=s.observations[0],last=s.observations.at(-1);
        if(first&&last&&last.time-first.time>=3000&&last.percent-first.percent>=5&&last.percent>=10){
          s.etaAt=now+(100-last.percent)*(last.time-first.time)/(last.percent-first.percent);s.etaOrigin='trend';
        }
      }
    }
  }
  renderProgress();
}
function renderProgress(){
  const s=progressState;if(!s)return;const now=Date.now(),elapsed=(now-s.startedAt)/1000;
  $('elapsed').textContent='已用 '+durationLabel(elapsed);
  const queued=/^(queued|pending|submitted|created|starting|waiting)$/.test(s.status)||s.stage==='queued';
  const complete=s.stage==='complete',downloading=s.stage==='downloading';
  const phase=complete?'complete':downloading?'download':queued?'queue':s.taskId?(s.status?'generate':'query'):'submit';
  $('progressStage').textContent=complete?'已取得生成结果':downloading?'正在获取媒体文件':queued?'任务排队中':phase==='generate'?'正在生成内容':phase==='query'?'正在查询已有任务':'请求已提交，等待渠道处理';
  if(queued&&Number.isFinite(s.queuePosition))$('progressStage').textContent+=' · 队列位置 '+s.queuePosition;
  const known=s.percent!==null;
  $('progressPercent').textContent=known?s.percent.toFixed(s.percent%1?1:0)+'% · 渠道返回':'渠道未提供百分比';
  $('progressBar').classList.toggle('is-indeterminate',!known);
  $('progressFill').style.width=known?s.percent+'%':'';
  if(known)$('progressBar').setAttribute('aria-valuenow',String(s.percent));else $('progressBar').removeAttribute('aria-valuenow');
  $('progressBar').setAttribute('aria-valuetext',known?'渠道返回进度 '+s.percent+'%':'渠道未提供完成百分比');
  const step=complete?4:downloading?3:phase==='generate'?2:phase==='queue'||phase==='query'?1:0;
  document.querySelectorAll('.progress-steps span').forEach((el,i)=>{el.classList.toggle('done',i<step);el.classList.toggle('active',i===step);});
  if(s.etaAt!==null){
    $('progressEta').textContent=s.etaAt>now?'约 '+durationLabel((s.etaAt-now)/1000):'已超过预计，继续等待';
    $('progressEtaSource').textContent=s.etaOrigin==='channel'?'渠道预计时间，可能变化':'按进度变化估算，可能波动';
  }else{
    $('progressEta').textContent=complete?'已完成':s.percent===100?'等待成品返回':'暂时无法估计';
    $('progressEtaSource').textContent=complete?'已收到可用输出':queued?'排队时长由上游决定':s.percent!==null?'等待更多进度数据':'渠道未提供可用的完成时间';
  }
  $('progressNextPoll').textContent=s.inRequest?(s.taskId?'正在查询 / 接收响应':'正在等待响应'):s.nextPollAt?((s.nextPollAt>now?'下次查询 '+durationLabel((s.nextPollAt-now)/1000)+' 后':'即将查询')):s.taskId?'等待任务状态':'等待首次响应';
  $('progressBatch').textContent=(s.total>1?`第 ${s.index+1} / ${s.total} 个模型 · 已完成 ${s.index} 个`:'单模型测试')+(s.pollCount?' · 已查询 '+s.pollCount+' 次':'');
  if(s.pollDeadline)$('progressLimit').textContent='本页最多再等待 '+durationLabel((s.pollDeadline-now)/1000)+'；这是等待上限，不是完成倒计时。到期可用任务 ID 继续查询。';
  else if(s.requestDeadline)$('progressLimit').textContent='本次请求距超时上限 '+durationLabel((s.requestDeadline-now)/1000)+'；超时不代表上游任务已停止。';
  else $('progressLimit').textContent='等待真实状态更新；进度只按渠道返回的数据变化。';
}
function setBusy(value){
  if(value){cancelModelFetch();closeModelMenu();window.ChoicePickers?.closeAll();resetProgress();}
  busy=value;$('configFields').disabled=value;$('runBtn').disabled=value;$('stopBtn').disabled=!value;$('previewBtn').disabled=value;$('demoBtn').disabled=value;$('legacyBtn').disabled=value;$('basicBtn').disabled=value;
  document.querySelectorAll('.kind-tab').forEach(el=>el.disabled=value);
  $('clearBtn').disabled=value||!records.length;$('exportBtn').disabled=value||!records.length;$('running').hidden=!value;
  if(!value){clearInterval(timer);timer=null;controller=null;}
  updateModelFetchUI();
}
function requestEvent(e){
  recordProgress(e);
  if(e.type==='progress')return;
  const message=e.message||[e.method,e.url,e.status].filter(Boolean).join(' ');
  if(message)log(message,e.type==='response'?(Number(e.status)>=400?'error':'success'):'info');
  $('runningDetail').textContent=scrub(message||'等待渠道响应…');
  const id=e.taskId||(e.type==='poll'?e.id:null);if(id)$('taskId').value=String(id);
}
async function run(resumeId=null){
  if(busy)return;let configs;setBusy(true);controller=new AbortController();const signal=controller.signal;
  try{
    const models=resumeId?[$('model').value.trim()]:getModels();
    if(!resumeId&&!$('allowMismatch').checked){const mismatch=models.map(m=>({model:m,advice:recommendModel(m)})).find(x=>isMismatch(x.advice));if(mismatch)throw new Error(mismatch.model+'：'+mismatch.advice.note+'。请调整测试类型 / 协议；若渠道有特殊映射，可勾选提示中的覆盖选项。');}
    configs=models.map(m=>{const config=getConfig(m);if(models.length>1)config.extra={...config.extra,model:m};return config;});
    if(!resumeId)await E.build(configs[0]);
    else if(!configs[0].pollPath)throw new Error('请在高级参数填写任务查询路径。');
  }catch(e){notify(e.message,true);setBusy(false);return;}
  if(signal.aborted){setBusy(false);return;}
  notify('');timer=setInterval(renderProgress,500);
  if(!logs.length)$('logs').replaceChildren();
  try{
    for(let i=0;i<configs.length;i++){
      if(signal.aborted)break;const c=configs[i],t=performance.now(),startedAt=Date.now();
      beginProgress(c,i,configs.length,resumeId);
      $('runningLabel').textContent=`${resumeId?'查询任务':'正在测试'} · ${c.model}${configs.length>1?` (${i+1}/${configs.length})`:''}`;
      log(`${resumeId?'继续查询':'开始测试'} ${kinds[kind].name} / ${c.model}`);
      let result;
      try{
        result=await (resumeId?E.resume(c,resumeId,{signal,onEvent:requestEvent}):E.run(c,{signal,onEvent:requestEvent}));
        log(`${c.model} · ${result.status==='success'?'已返回结果':result.status==='pending'?'任务尚未完成':'收到响应，请检查内容'}`,result.status==='success'?'success':'info');
      }catch(e){
        result={status:signal.aborted?'stopped':'error',error:signal.aborted?'已停止本页等待。已提交的生成任务可能仍在渠道执行；可使用任务 ID 继续查询。':e.message,requests:e.requests||[],raw:e.raw||null,taskId:e.taskId,media:[],text:'',elapsedMs:performance.now()-t};
        log(`${c.model} · ${result.error}`,signal.aborted?'info':'error');
      }
      if(result.taskId)$('taskId').value=String(result.taskId);
      const rec={...result,id:crypto.randomUUID?crypto.randomUUID():String(Date.now()+Math.random()),kind,model:c.model,preset:selectedPreset().label,scenarioId:$('promptScenario').value||null,createdAt:new Date().toISOString(),config:safeSnapshot(c),elapsedMs:result.elapsedMs??performance.now()-t};
      const gptEvaluation=buildGptEvaluation(rec,c);if(gptEvaluation)rec.gpt_evaluation=gptEvaluation;
      addRecord(rec);saveBasicHistory(rec,c,startedAt,resumeId);if(signal.aborted)break;
    }
  }finally{setBusy(false);}
}
function saveBasicHistory(r,c,startedAt,resumeId){
  if(!window.HistoryCapture||r.status==='demo')return;
  const task=r.taskId||resumeId,taskKey=task?JSON.stringify([c.base,c.model,c.preset,String(task)]):'';
  const prior=taskKey?historyTasks.get(taskKey):null;
  const identity=prior||{id:r.id,createdAt:startedAt,duration:0,requests:[]};
  identity.duration+=r.elapsedMs||0;identity.requests.push(...scrub(r.requests||[]));if(taskKey)historyTasks.set(taskKey,identity);
  const media=(r.media||[]).map(m=>({type:m.kind,mime:m.mime,url:m.url}));
  const safeResult=scrub({...r,elapsedMs:identity.duration,requests:identity.requests,media:(r.media||[]).map(m=>({kind:m.kind,mime:m.mime,storage:'见记录中的媒体文件'})),gpt_evaluation:r.gpt_evaluation});
  const container=$('results').querySelector('[data-id="'+r.id+'"] .history-save-status');
  return window.HistoryCapture.record({client_id:identity.id,kind:r.kind,source:'basic',title:kinds[r.kind].name+'测试 · '+r.model,model:r.model,base:c.base,prompt:c.prompt,status:({success:'passed',error:'failed',stopped:'cancelled',pending:'pending'})[r.status]||'inconclusive',created_at:identity.createdAt/1000,duration_ms:identity.duration,result:safeResult,media},{key:c.key,container});
}
function visibleMediaUrl(media){
  const url=localMediaUrls.has(media.url)?media.url:E.safeUrl(media.url,media.kind);if(!url)return null;
  if(!url.startsWith('data:')&&!url.startsWith('blob:'))for(const key of secrets)if(key&&(url.includes(key)||url.includes(encodeURIComponent(key))))return null;
  return url;
}
function mediaItem(media,index){
  const wrapper=node('div','media-item media-'+media.kind);const source=visibleMediaUrl(media);
  if(!source){wrapper.append(node('div','media-error','结果包含无法安全预览的媒体地址，请查看原始响应。'));return wrapper;}
  const element=document.createElement(media.kind==='image'?'img':media.kind==='video'?'video':'audio');
  element.src=source;if(media.kind==='image'){element.alt='生成图片 '+(index+1);element.loading='lazy';element.referrerPolicy='no-referrer';element.tabIndex=0;element.style.cursor='zoom-in';element.title='点击放大查看';const enlarge=()=>{const dialog=document.createElement('dialog');dialog.className='image-dialog';const close=node('button','secondary','关闭');close.addEventListener('click',()=>dialog.close());const large=node('img');large.src=source;large.alt=element.alt;large.referrerPolicy='no-referrer';dialog.append(close,large);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();};element.addEventListener('click',enlarge);element.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();enlarge();}});}
  else{element.controls=true;element.preload='metadata';if(media.kind==='video')element.playsInline=true;}
  const err=node('div','media-error','媒体加载失败，可能是链接过期、权限、跨域或格式不兼容。可打开原链接，或检查渠道返回的内容。');err.hidden=true;
  element.addEventListener('error',()=>{err.hidden=false;});
  const actions=node('div','media-links');const open=node('a','',media.kind==='image'?'打开原图 ↗':'打开媒体 ↗');open.href=source;open.target='_blank';open.rel='noopener noreferrer';
  const save=node('button','media-download','下载文件 ↓');save.type='button';
  const downloadStatus=node('div','download-status');downloadStatus.hidden=true;downloadStatus.setAttribute('role','status');
  save.addEventListener('click',async()=>{
    if(save.disabled)return;save.disabled=true;save.textContent='正在下载…';downloadStatus.hidden=false;downloadStatus.classList.remove('error');downloadStatus.textContent='正在读取媒体文件…';
    try{
      const saved=await window.MediaDownloads.download({url:source,filename:`${media.kind}-${index+1}.${extension(media)}`,kind:media.kind,onStatus:event=>{
        downloadStatus.textContent=event.phase==='saving'?'正在启动浏览器下载…':event.phase==='done'?'已发起下载，请在浏览器下载列表中查看。':'正在读取媒体文件…';
      }});
      downloadStatus.textContent='已发起下载：'+saved.filename+'，请在浏览器下载列表中查看。';
    }catch(e){downloadStatus.classList.add('error');downloadStatus.textContent=scrub(e.message||'下载失败。可打开原链接后另存为。');}
    finally{save.disabled=false;save.textContent='下载文件 ↓';}
  });
  actions.append(open,save);if(media.kind==='image')actions.append(node('span','media-hint','点击图片放大'));
  const stage=node('div','media-stage');stage.append(element);wrapper.append(stage,err,actions,downloadStatus);return wrapper;
}
function extension(m){const mime=m.mime||m.url.match(/^data:([^;,]+)/)?.[1]||'';return ({'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/svg+xml':'svg','audio/mpeg':'mp3','audio/wav':'wav','audio/x-wav':'wav','audio/ogg':'ogg','audio/aac':'aac','audio/flac':'flac','video/mp4':'mp4','video/webm':'webm'}[mime])||(m.kind==='image'?'png':m.kind==='video'?'mp4':'mp3');}
function addRecord(r){records.unshift(r);renderRecord(r,true);$('resultCount').textContent=records.length;$('empty').hidden=true;$('exportBtn').disabled=busy;$('clearBtn').disabled=busy;}
function renderRecord(r,prepend=false){
  const card=node('article','result-card panel');card.dataset.id=r.id;
  const head=node('div','result-card-head');const name=node('div','result-name');name.append(node('h3','',r.model),node('small','',r.preset));
  const labels={success:'已返回结果',pending:'等待完成',error:'请求失败',unrecognized:'需检查响应',stopped:'已停止',demo:'演示样例'};
  head.append(node('span','result-kind',kinds[r.kind].icon),name,node('span','status '+r.status,labels[r.status]||r.status));card.append(head);
  const body=node('div','result-body');const meta=node('div','result-meta');
  meta.append(node('span','',new Date(r.createdAt).toLocaleTimeString('zh-CN',{hour12:false})),node('span','',`${((r.elapsedMs||0)/1000).toFixed(2)}s`),node('span','',`${r.requests?.length||0} 次 HTTP 请求`));
  if(r.status==='demo')meta.append(node('span','','本地演示 · 未调用渠道'));
  card.append(meta);
  if(r.taskId){
    const t=node('p','task-note','任务 ID：'+r.taskId);const btn=node('button','text-button','载入查询配置');
    btn.addEventListener('click',()=>{
      if(busy)return;const credentialChanged=$('base').value.trim()!==r.config.base||$('auth').value!==r.config.auth;switchKind('video');for(const id of draftIds)if(r.config?.[id]!==undefined)$(id).value=typeof r.config[id]==='object'?JSON.stringify(r.config[id],null,2):r.config[id];
      $('base').value=r.config.base;$('taskId').value=String(r.taskId);if(credentialChanged){$('key').value='';}updateFields();notify(credentialChanged?'已载入该任务配置。渠道已变化，请填写对应渠道的 API Key，再点击继续查询。':'已载入该任务配置，可点击继续查询获取结果。');$('taskId').scrollIntoView({behavior:'smooth',block:'center'});
    });t.append(btn);body.append(t);
  }
  if(r.error){body.append(node('div','result-error',scrub(r.error)));const diagnostic=diagnoseResult(r);if(diagnostic)body.append(diagnostic);}
  if(r.status==='pending')body.append(node('p','task-note','任务已提交但尚未获取成品。可用任务 ID 继续查询，不需要再次生成。'));
  if(r.text)body.append(node('div','result-text',scrub(r.text)));
  const tokenUsage=renderTokenUsage(r);if(tokenUsage)body.append(tokenUsage);
  const gptAssessment=renderGptAnimationAssessment(r);if(gptAssessment)body.append(gptAssessment);
  const htmlPreview=renderHtmlPreview(r);if(htmlPreview)body.append(htmlPreview);
  if(r.media?.length){const grid=node('div','media-grid');r.media.forEach((m,i)=>grid.append(mediaItem(m,i)));body.append(grid);}
  if(r.status==='unrecognized'&&(r.text||r.media?.length))body.append(node('p','task-note','未获得与所选测试类型匹配的有效输出，请检查下方原始响应。'));
  if(!r.text&&!r.media?.length&&!r.error&&r.status!=='pending')body.append(node('p','task-note','响应中没有识别到可展示内容，请展开原始响应核对字段和状态。'));
  renderCoverage(body,r);card.append(body);if(r.status!=='demo'){const saved=node('div','history-save-status');card.append(saved);}
  const details=node('details','raw-details');details.append(node('summary','','查看原始响应与请求记录'));
  const actions=node('div','raw-tools');const download=node('button','text-button','下载响应 JSON');download.addEventListener('click',()=>downloadBlob(new Blob([JSON.stringify(scrub({config:r.config,result:{...r,config:undefined}}),null,2)],{type:'application/json'}),'response-'+cleanFilename(r.model)+'.json'));actions.append(download);details.append(actions);
  const pre=node('pre','',JSON.stringify(scrub({response:r.raw,requests:r.requests},true),null,2));details.append(pre);card.append(details);
  if(prepend)$('results').prepend(card);else $('results').append(card);
}
function cleanFilename(v){return String(v||'result').replace(/[\\/:*?"<>|\x00-\x1f]/g,'_').slice(0,80);}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
async function previewRequest(){try{const c=getConfig(getModels()[0]);const req=await E.build(c);$('requestPreview').textContent=JSON.stringify(scrub({method:req.method,url:req.url,encoding:selectedPreset().bodyType==='multipart'?'multipart/form-data（浏览器自动添加 boundary）':'application/json',headers:req.headers,body:req.preview??req.body},true),null,2);$('previewDialog').showModal();notify('');}catch(e){notify(e.message,true);}}
async function loadModels(){
  if(busy)return;syncModelContext();const c=discoveryConfig();
  if(c.key)secrets.add(c.key);
  if(!c.base){notify('请先填写渠道地址。',true);return;}
  if(c.auth!=='none'&&!c.key){notify('请先填写 API Key，或选择不使用鉴权。',true);return;}
  modelFetchController?.abort();const epoch=++modelFetchEpoch,identity=discoveryIdentity(c),ctrl=new AbortController();modelFetchController=ctrl;modelFetchState='loading';modelFetchError='';
  updateModelFetchUI();openModelMenu();notify('');
  try{
    const result=await window.ModelDiscovery.list(c,{signal:ctrl.signal});
    if(epoch!==modelFetchEpoch||identity!==discoveryIdentity()||ctrl.signal.aborted)return;
    const list=Array.isArray(result)?result:result.models;
    if(!Array.isArray(list))throw new Error('模型列表响应格式无法识别');
    modelCatalog=[...new Set(list.map(m=>typeof m==='string'?m:typeof m?.id==='string'?m.id:typeof m?.name==='string'?m.name:'').filter(Boolean))];
    modelCatalogLoaded=true;modelFetchState='success';modelFetchError='';$('modelSearch').value='';modelSelection.setOptions(modelCatalog);
  }catch(e){
    if(epoch!==modelFetchEpoch||identity!==discoveryIdentity())return;
    modelFetchState=ctrl.signal.aborted?'canceled':'error';modelFetchError=scrub(e.message||'未知错误');
  }finally{
    if(epoch===modelFetchEpoch){modelFetchController=null;updateModelFetchUI();renderModelOptions();}
  }
}
async function exportReport(){
  if(!records.length||busy)return;
  const reportRecords=[...records],reportLogs=[...logs];
  setBusy(true);$('stopBtn').disabled=true;$('exportBtn').disabled=true;$('runningLabel').textContent='正在导出报告';$('runningDetail').textContent='正在打包本页收到的媒体，不会请求渠道。';notify('正在打包当前会话报告…');
  try{
    // Service mode uses the same server renderer as CCMax/KVV, so all exported
    // reports share one visual language and evidence semantics.
    if(/^https?:$/i.test(location.protocol) && window.HistoryCapture){
      try {
        const payload={records:reportRecords.map(r=>({...r,result:{...r,requests:r.requests||[],raw:r.raw}})),exported_at:Date.now()};
        const response=await fetch('/api/reports',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Workbench-Token':(await fetch('/api/session',{credentials:'same-origin'}).then(r=>r.json())).token},body:JSON.stringify(payload)});
        if(!response.ok)throw new Error((await response.json().catch(()=>({}))).error||'统一报告服务暂不可用');
        const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;
        const modelPart=String(reportRecords.map(r=>r.model).filter(Boolean).join('、')||'未命名模型').replace(/[\\/:*?"<>|\u0000-\u001f]+/g,'-').slice(0,80);const now=new Date(),pad=v=>String(v).padStart(2,'0');a.download=`测试报告-${modelPart}-${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.html`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);notify('统一样式报告已下载');return;
      } catch (serviceError) {
        // Continue with the portable renderer below. This keeps report export
        // usable during a transient session/service outage.
        notify('统一报告服务暂不可用，已切换为本地统一报告：'+(serviceError.message||'未知错误'));
      }
    }
    // Keep portable exports on the same renderer as service/acceptance reports.
    // This branch runs when the page is opened from file:// or the service is
    // unavailable; media URLs are embedded by WorkbenchReport when possible.
    if(window.WorkbenchReport?.render){
      const report=await window.WorkbenchReport.render(reportRecords,reportLogs);
      const models=[...new Set(reportRecords.map(record=>String(record.model||'未命名模型').trim()).filter(Boolean))];
      const modelPart=(models.join('、')||'未命名模型').replace(/[\\/:*?"<>|\u0000-\u001f]+/g,'-').replace(/\s+/g,' ').slice(0,80)||'未命名模型';
      const now=new Date(),pad=value=>String(value).padStart(2,'0'),stamp=`${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
      downloadBlob(new Blob([report],{type:'text/html;charset=utf-8'}),`测试报告-${modelPart}-${stamp}.html`);notify('统一样式报告已下载');return;
    }
    const mediaHTML=async m=>{let url=visibleMediaUrl(m);if(!url)return '<p class="muted">该媒体地址包含会话密钥或不适合预览，未写入报告。</p>';
      if(url.startsWith('blob:')){const blob=await fetch(url).then(r=>r.blob());url=await new Promise((resolve,reject)=>{const f=new FileReader();f.onload=()=>resolve(f.result);f.onerror=reject;f.readAsDataURL(blob);});}
      const safe=escapeHtml(url),tag=m.kind==='image'?'img':m.kind==='video'?'video':'audio';return `<figure><${tag} src="${safe}" ${tag==='img'?'alt="生成图片" referrerpolicy="no-referrer"':'controls preload="metadata"'}>${tag==='img'?'':`</${tag}>`}<figcaption><a href="${safe}" target="_blank" rel="noopener noreferrer">打开媒体 ↗</a></figcaption></figure>`;};
    const statusLabel={demo:'演示样例',success:'已返回结果',pending:'等待完成',error:'请求失败',stopped:'已停止',unrecognized:'需检查响应'};
    const recordText=r=>JSON.stringify(scrub({preset:r.preset,kind:r.kind,model:r.model,config:r.config,response:r.raw,requests:r.requests,text:r.text,error:r.error,taskId:r.taskId}),true).toLowerCase();
    const dimensions=[
      ['multimodal','多模态能力',r=>r.kind!=='text'||(r.media||[]).length>0||/image|video|audio|图片|视频|音频|vision|multimodal/.test(recordText(r))],
      ['tools','工具调用',r=>/tool|function|tool_choice|tools|函数调用|工具/.test(recordText(r))],
      ['max_tokens','max_tokens / 长度控制',r=>/max_tokens|max token|token.?上限|长度限制/.test(recordText(r))],
      ['cache','缓存与 usage',r=>/cache|缓存|prompt_tokens|input_tokens|cached_tokens|usage/.test(recordText(r))],
      ['protocol','协议与错误',r=>true],
      ['reliability','稳定性与性能',r=>true]
    ];
    const dims=dimensions.map(([id,label,match])=>{const rows=reportRecords.filter(match);const counts={passed:rows.filter(r=>r.status==='success').length,failed:rows.filter(r=>['error','stopped'].includes(r.status)).length,inconclusive:rows.filter(r=>!['success','error','stopped'].includes(r.status)).length};const covered=rows.length;const points=rows.reduce((n,r)=>n+(r.status==='success'?1:['error','stopped'].includes(r.status)?0:.4),0);return {id,label,covered,counts,score:covered?Math.round(points/covered*100):0,status:!covered?'not_covered':counts.failed?'failed':counts.inconclusive?'inconclusive':'passed'};});
    const coveredDims=dims.filter(d=>d.covered),totalScore=coveredDims.length?Math.round(coveredDims.reduce((n,d)=>n+d.score,0)/coveredDims.length):0;
    const recommendations=[];dims.forEach(d=>{if(d.status==='not_covered')recommendations.push(`补充“${d.label}”专项请求后再评价该能力；本次没有可计分证据。`);else if(d.status==='failed')recommendations.push(`优先复核“${d.label}”中的失败记录，展开请求原文和错误诊断。`);else if(d.status==='inconclusive')recommendations.push(`补齐“${d.label}”的等待、鉴权或响应证据后重新测试。`);});if(!recommendations.length)recommendations.push('各已覆盖维度均有完整返回证据；建议扩大模型、输入和并发样本后复测。');
    const scoreCards=dims.map(d=>`<article class="score ${d.status}"><div><b>${escapeHtml(d.label)}</b><strong>${d.score}<small>/100</small></strong></div><i><em style="width:${d.score}%"></em></i><p>覆盖 ${d.covered} 项 · 通过 ${d.counts.passed} · 失败 ${d.counts.failed} · 无法判定 ${d.counts.inconclusive}</p></article>`).join('');
    const passedCount=reportRecords.filter(r=>r.status==='success').length;
    const failedCount=reportRecords.filter(r=>['error','stopped'].includes(r.status)).length;
    const pendingCount=reportRecords.filter(r=>!['success','error','stopped'].includes(r.status)).length;
    const verdict=failedCount?'需要关注':pendingCount?'部分完成':'通过';
    const kindLabels={text:'文本',image:'图像',video:'视频',audio:'音频'};
    const coveredKinds=[...new Set(reportRecords.map(r=>kindLabels[r.kind]||r.kind).filter(Boolean))];
    const scopeItems=[
      `本次会话共记录 ${reportRecords.length} 个测试结果，覆盖：${coveredKinds.join('、')||'未识别类型'}。`,
      '报告统一展示实际请求、响应、耗时、错误诊断与可复核证据；演示样例不会计入能力结论。',
      '多模态结果以渠道是否接受对应输入与是否返回可展示媒体为判断依据。',
      '评分按已覆盖维度计算；未发起专项请求的能力标记为“未覆盖”，不会伪造通过。'
    ];
    const findings=[];
    reportRecords.filter(r=>['error','stopped','unrecognized','pending'].includes(r.status)||r.error).forEach(r=>{
      const title=r.error?'请求失败':r.status==='stopped'?'测试被停止':r.status==='pending'?'等待超时或未完成':'响应未识别';
      const observation=r.error||r.text||'渠道没有返回与本测试类型匹配的可展示内容。';
      const advice=r.error?(diagnoseResult(r)?.textContent||'请检查请求体、鉴权、模型名称和上游错误。'):'展开原始响应，确认字段结构与媒体地址是否符合协议。';
      findings.push(`<article class="finding ${r.status==='pending'?'inconclusive':''}"><span class="badge ${r.status==='error'||r.status==='stopped'?'error':'pending'}">${r.status==='error'||r.status==='stopped'?'未通过':'需核对'}</span><h3>${escapeHtml(title)} · ${escapeHtml(r.model||'未命名模型')}</h3><p><b>实际观察</b>${escapeHtml(scrub(observation))}</p><p><b>建议排查</b>${escapeHtml(scrub(advice))}</p></article>`);
    });
    const findingHtml=findings.length?findings.join(''):`<div class="empty">本轮没有记录明确失败项。建议结合“逐项检查”和“请求证据”核对是否覆盖了目标能力。</div>`;
    let checkHtml='';
    for(let i=0;i<reportRecords.length;i++){
      const r=reportRecords[i],status=statusLabel[r.status]||r.status||'未记录';
      const expectation=r.kind==='video'?'渠道应接受 video_url 或视频输入并返回对视频内容的理解。':r.kind==='image'?'渠道应接受图像输入并返回视觉理解或生成媒体。':r.kind==='audio'?'渠道应接受音频输入并返回可核验的音频理解结果。':'渠道应返回可解析的文本结果，并保留协议字段。';
      const observed=r.error||r.text||((r.media||[]).length?`返回 ${(r.media||[]).length} 个媒体结果。`:'响应中未识别到可展示内容。');
      const evidence=JSON.stringify(scrub({config:r.config,response:r.raw,requests:r.requests}),null,2);
      const media=await Promise.all((r.media||[]).map(mediaHTML));
      checkHtml+=`<article class="check" id="check-${i+1}"><div class="check-head"><div><span class="index">${String(i+1).padStart(2,'0')} / CHECK</span><h3>${escapeHtml(r.preset||kindLabels[r.kind]||'渠道测试')}</h3><div class="case-id">${escapeHtml(r.model||'未命名模型')} · ${escapeHtml(new Date(r.createdAt||Date.now()).toLocaleString('zh-CN'))}</div></div><span class="badge ${escapeHtml(r.status||'pending')}">${escapeHtml(status)}</span></div><div class="comparison"><div><b>预期行为</b><p>${escapeHtml(expectation)}</p></div><div class="actual"><b>实际结果</b><p>${escapeHtml(scrub(observed))}</p></div></div>${r.error?`<div class="diagnostic"><b>错误诊断与处理建议</b><p>${escapeHtml(scrub(diagnoseResult(r)?.textContent||'请检查请求和上游响应。'))}</p></div>`:''}${r.text?`<details class="raw"><summary>文本输出</summary><pre>${escapeHtml(scrub(r.text))}</pre></details>`:''}<div class="media">${media.join('')}</div><details class="raw"><summary>请求配置、原始响应与请求记录</summary><pre>${escapeHtml(evidence)}</pre></details></article>`;
    }
    const report=`<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>测试报告 · ${escapeHtml((reportRecords[0]?.model)||'未命名模型')}</title><style>
:root{--ink:#233d32;--muted:#6e7e73;--line:#dce5dc;--paper:#f4f7f2;--green:#28664e;--green-soft:#e9f3e9;--red:#ac4b3d;--red-soft:#faebe6;--amber:#936b22;--amber-soft:#faf1da;--card:#fff}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:90px}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}a{color:var(--green);text-decoration:none}main{max-width:1200px;margin:auto;padding:32px 28px 60px}h1,h2,h3,p{margin:0}h1{font-size:34px;line-height:1.25;letter-spacing:-.8px;overflow-wrap:anywhere}h2{font-size:21px;line-height:1.4}h3{font-size:16px;line-height:1.5}.muted{color:var(--muted);font-size:11px}.masthead{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 0 18px}.brand{font-weight:650;font-size:16px}.brand:before{content:'宇';display:inline-grid;place-items:center;width:32px;height:32px;color:#fff;background:var(--ink);border-radius:10px;margin-right:10px}.eyebrow,.index{font-size:10px;letter-spacing:.11em;color:var(--muted);text-transform:uppercase}.cover{color:#f8fbf7;background:var(--ink);padding:32px;border-radius:20px}.cover p{color:#c6d5c9;margin-top:10px}.cover-top,.section-head,.check-head,.verdict-line{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.run-id{font:11px ui-monospace,monospace;color:#a8c3af!important}.nav{display:flex;gap:20px;flex-wrap:wrap;background:#f4f7f2ed;backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:17px 0;position:sticky;top:0;z-index:2;font-size:12px}.overview,.panel,.check{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:18px;box-shadow:0 2px 8px rgba(35,61,50,.025)}.verdict-line h2{font-size:24px}.verdict-line p{font-size:12px;color:var(--muted);margin-top:3px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px}.metric{padding:18px 20px;background:#fff;border:1px solid var(--line);border-radius:12px}.metric b{display:block;font-size:30px;line-height:1.3;font-variant-numeric:tabular-nums}.metric small{display:block;font-size:10px;color:var(--muted)}.metric span{font-size:12px;color:var(--muted)}.distribution{display:flex;height:7px;overflow:hidden;border-radius:9px;background:#e6ece4;margin-top:18px}.distribution span{display:block}.distribution .success{background:#5f916d}.distribution .error,.distribution .stopped{background:#bb6453}.distribution .pending,.distribution .unrecognized{background:#c1994b}.legend{font-size:11px;color:var(--muted);margin-top:10px}.score-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.score{padding:13px;border:1px solid var(--line);border-radius:10px;background:#fbfcfa}.score>div{display:flex;justify-content:space-between;gap:8px}.score strong{font-size:18px}.score strong small{font-size:10px;color:var(--muted)}.score>i{display:block;height:5px;background:#e6ece4;border-radius:5px;margin:8px 0;overflow:hidden}.score>i em{display:block;height:100%;background:#5f916d}.score.failed>i em{background:#bb6453}.score.inconclusive>i em{background:#c1994b}.score.not_covered{opacity:.75}.score p{font-size:10px;color:var(--muted)}.recommendations{font-size:11px;color:var(--muted);padding-left:18px;margin:14px 0 0}.section{margin-top:24px}.grid-two{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px}.scope-list{margin:0;padding-left:20px;color:var(--muted);font-size:12px}.scope-list li+li{margin-top:8px}.findings{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:12px}.finding{padding:17px;border:1px solid #eadfbd;border-radius:11px;background:#fffbf1;color:#6e5c2a}.finding.inconclusive{border-color:var(--line);background:#fbfcfa;color:var(--ink)}.finding h3{margin:7px 0;font-size:15px}.finding p{font-size:12px;margin-top:6px}.finding p b,.comparison b{display:block;color:var(--muted);font-size:10px;letter-spacing:.04em;margin-bottom:2px}.check{padding:20px}.case-id{font:11px ui-monospace,monospace;color:var(--muted);margin-top:3px}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:15px}.comparison>div{padding:13px;border:1px solid var(--line);border-radius:9px;background:#fbfcfa}.comparison .actual{background:#fff}.comparison p{font-size:12px;overflow-wrap:anywhere}.badge{display:inline-block;white-space:nowrap;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;background:#eef1ed;color:#627363}.badge.success,.badge.demo{background:var(--green-soft);color:var(--green)}.badge.error,.badge.stopped{background:var(--red-soft);color:#a0483c}.badge.pending,.badge.unrecognized{background:var(--amber-soft);color:#86611d}.media{display:flex;flex-wrap:wrap;gap:13px;margin-top:14px}.media:empty{display:none}.media figure{margin:0;flex:1 1 280px;padding:10px;border:1px solid var(--line);border-radius:10px;background:#fbfcfa}.media img,.media video{display:block;width:100%;max-height:520px;object-fit:contain;border-radius:7px;background:#f0f3eb}.media audio{width:100%}.media figcaption{font-size:11px;margin-top:8px}.diagnostic{border:1px solid #eadfbd;border-radius:9px;background:#fffbf1;color:#7e692f;padding:14px;margin-top:13px}.diagnostic p{margin-top:5px;white-space:pre-wrap}.raw{margin-top:14px;border-top:1px solid #e8ede5;padding-top:10px}.raw summary{cursor:pointer;color:var(--green);font-size:12px}.raw pre,.log pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto;background:#f4f7f1;border:1px solid #e0e8dc;border-radius:8px;padding:14px;font:11px/1.75 ui-monospace,SFMono-Regular,monospace;color:#374d3b;margin-top:9px}.empty{border:1px dashed var(--line);border-radius:12px;padding:22px;color:var(--muted);margin-top:12px}.log{margin-top:18px}.log pre{max-height:360px}footer{display:flex;justify-content:space-between;gap:16px;margin-top:26px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:11px}@media(max-width:700px){main{padding:18px 14px 35px}h1{font-size:26px}.cover{padding:23px 20px;border-radius:14px}.metrics{grid-template-columns:1fr 1fr;gap:8px}.metric{padding:14px}.metric b{font-size:25px}.score-grid,.findings,.grid-two,.comparison{grid-template-columns:1fr}.section-head,.check-head{align-items:flex-start}.overview,.panel,.check{padding:17px}.nav{gap:14px;padding:13px 0}}@media print{body{background:#fff;font-size:10px}main{max-width:none;padding:0}.nav{display:none}.cover{background:#fff;color:var(--ink);border:1px solid var(--line);padding:20px}.cover p{color:var(--muted)}.overview,.panel,.check{break-inside:avoid;box-shadow:none}.raw pre,.log pre{max-height:none;overflow:visible;font-size:8px}}
</style></head><body><main><div class="masthead"><span class="brand">小小宇宙无敌</span><span class="eyebrow">CHANNEL ACCEPTANCE REPORT</span></div><header class="cover"><div class="cover-top"><span class="eyebrow">MULTIMODAL / GENERAL</span><span class="badge ${failedCount?'error':pendingCount?'pending':'success'}">${escapeHtml(verdict)}</span></div><h1>通用模型渠道测试报告</h1><p>${escapeHtml((reportRecords[0]?.model)||'未命名模型')} · ${escapeHtml(new Date().toLocaleString('zh-CN'))} · API Key 已隐藏</p><p class="run-id">本次记录 ${reportRecords.length} 条 · 评分仅基于实际请求与保存证据</p></header><nav class="nav"><a href="#overview">结论总览</a><a href="#score">能力评分</a><a href="#scope">范围与配置</a><a href="#findings">发现的问题</a><a href="#checks">逐项检查</a><a href="#logs">执行日志</a></nav><section id="overview" class="overview"><div class="verdict-line"><div><h2>${escapeHtml(verdict)}</h2><p>${failedCount?'存在失败或错误，需要结合请求证据排查。':pendingCount?'部分项目未完成或无法判定，请补充专项请求。':'已完成本次会话中的已覆盖测试。'}</p></div><span class="badge ${failedCount?'error':pendingCount?'pending':'success'}">${passedCount} 通过 · ${failedCount} 失败 · ${pendingCount} 待核对</span></div><div class="metrics"><div class="metric"><span>总分</span><b>${totalScore}<small>/ 100</small></b><small>已覆盖维度等权平均</small></div><div class="metric"><span>测试记录</span><b>${reportRecords.length}</b><small>本次会话收到的结果</small></div><div class="metric"><span>返回成功</span><b>${passedCount}</b><small>包含可展示输出</small></div><div class="metric"><span>失败 / 待判定</span><b>${failedCount} / ${pendingCount}</b><small>请展开逐项证据</small></div></div><div class="distribution"><span class="success" style="width:${reportRecords.length?passedCount/reportRecords.length*100:0}%"></span><span class="error" style="width:${reportRecords.length?failedCount/reportRecords.length*100:0}%"></span><span class="pending" style="width:${reportRecords.length?pendingCount/reportRecords.length*100:0}%"></span></div><p class="legend">评分方法：成功=100%，等待、未识别或演示=40%，失败/停止=0%；未覆盖维度不计入总分。</p></section><section id="score" class="panel"><div class="section-head"><div><span class="index">SCORE / DIMENSIONS</span><h2>能力评分与覆盖明细</h2></div></div><div class="score-grid">${scoreCards}</div><ul class="recommendations">${recommendations.map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul></section><section id="scope" class="section"><div class="section-head"><div><span class="index">01 / SCOPE</span><h2>这次测了什么</h2></div></div><div class="grid-two"><div class="panel"><dl><dt>测试模型</dt><dd>${escapeHtml((reportRecords[0]?.model)||'未命名模型')}</dd><dt>测试记录</dt><dd>${reportRecords.length} 条 · ${escapeHtml(coveredKinds.join('、')||'未识别类型')}</dd><dt>导出时间</dt><dd>${escapeHtml(new Date().toLocaleString('zh-CN'))}</dd></dl></div><div class="panel"><ul class="scope-list">${scopeItems.map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul></div></div></section><section id="findings" class="section"><div class="section-head"><div><span class="index">02 / FINDINGS</span><h2>问题与影响</h2><p class="muted">依据本轮保存的响应和诊断整理；建议结合上游日志复核。</p></div></div><div class="findings">${findingHtml}</div></section><section id="checks" class="section"><div class="section-head"><div><span class="index">03 / CHECKS</span><h2>逐项验收说明</h2></div><span class="muted">${reportRecords.length} 项</span></div>${checkHtml||'<div class="empty">暂无测试记录。</div>'}</section><section id="logs" class="panel log"><div class="section-head"><div><span class="index">04 / LOGS</span><h2>执行日志</h2></div></div><pre>${escapeHtml(scrub(reportLogs.map(l=>`[${l.time}] ${l.message}`).join('\n'))||'本次没有额外日志。')}</pre></section><footer><span>小小宇宙无敌 · 独立 HTML · 无外部依赖 · 凭据已隐藏</span><span>General Model Acceptance</span></footer></main></body></html>`;
    const models=[...new Set(reportRecords.map(record=>String(record.model||'未命名模型').trim()).filter(Boolean))];const modelPart=(models.join('、')||'未命名模型').replace(/[\\/:*?"<>|\u0000-\u001f]+/g,'-').replace(/\s+/g,' ').slice(0,80)||'未命名模型';const now=new Date(),pad=value=>String(value).padStart(2,'0'),stamp=`${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;downloadBlob(new Blob([report],{type:'text/html;charset=utf-8'}),`测试报告-${modelPart}-${stamp}.html`);notify('报告已导出；已统一包含维度评分、总分、建议和请求证据。');
  }catch(e){notify('导出失败：'+e.message,true);}finally{setBusy(false);}
}
function clearResults(){
  if(busy)return;for(const r of records)for(const m of r.media||[])if(m.owned||m.url.startsWith('blob:'))(window.HistoryCapture?.releaseMedia||URL.revokeObjectURL.bind(URL))(m.url);
  localMediaUrls.clear();records=[];logs=[];$('results').replaceChildren();$('logs').replaceChildren(node('p','log-placeholder','请求状态与视频任务进度将在这里记录。'));$('resultCount').textContent='0';$('logCount').textContent='0 条';$('empty').hidden=false;$('exportBtn').disabled=true;$('clearBtn').disabled=true;notify('');
}
async function showDemo(){
  if(busy)return;setBusy(true);$('runningLabel').textContent='生成本地演示';$('runningDetail').textContent='不会调用任何渠道，不会产生 API 费用。';
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600" viewBox="0 0 900 600"><defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#bfd9db"/><stop offset="1" stop-color="#edf1d8"/></linearGradient></defs><rect width="900" height="600" fill="url(#sky)"/><circle cx="675" cy="158" r="60" fill="#ffefc1"/><path d="M0 330 190 220 390 364 630 260 900 354V600H0" fill="#82aca4"/><path d="M0 450 240 325 500 455 740 346 900 430V600H0" fill="#467e75"/><path d="M0 530 220 457 450 528 740 449 900 510V600H0" fill="#20594f"/><path d="M530 400c-40 50 120 63 45 110s-185 53-95 90h140c-175-75 25-60 25-104s-142-55-115-96" fill="#d0e6df"/><text x="40" y="55" font-family="sans-serif" font-size="16" letter-spacing="4" fill="#466a65">小小宇宙无敌 / 本地预览</text></svg>`;
  const demo=(k,media,text='')=>{media.forEach(m=>localMediaUrls.add(m.url));addRecord({id:'demo-'+k+'-'+Date.now(),kind:k,model:'预览样例 · '+kinds[k].name,preset:'本地演示（非渠道测试结果）',createdAt:new Date().toISOString(),status:'demo',media,text,raw:{demo:true,note:'本地生成的演示内容，未向渠道发请求。'},requests:[],elapsedMs:0,config:{}});};
  try{
    demo('text',[],'这是一条文本展示样例。\n\n真正的测试结果会在这里展示模型回答、HTTP 请求记录和耗时。点击下方“查看原始响应”可核对返回数据。');
    demo('image',[{kind:'image',url:URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'})),mime:'image/svg+xml',owned:true}]);
    const rate=24000,len=rate*2,buffer=new ArrayBuffer(44+len*2),v=new DataView(buffer);const put=(p,s)=>[...s].forEach((x,i)=>v.setUint8(p+i,x.charCodeAt(0)));put(0,'RIFF');v.setUint32(4,36+len*2,true);put(8,'WAVE');put(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);put(36,'data');v.setUint32(40,len*2,true);for(let i=0;i<len;i++){const t=i/rate;v.setInt16(44+i*2,Math.sin(2*Math.PI*(t<1?440:660)*t)*4000*Math.sin(Math.PI*(t%1)),true);}
    demo('audio',[{kind:'audio',url:URL.createObjectURL(new Blob([buffer],{type:'audio/wav'})),mime:'audio/wav',owned:true}],'本地生成的两段提示音，用于检查音频播放。');
    if(window.MediaRecorder&&HTMLCanvasElement.prototype.captureStream){
      const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;const ctx=canvas.getContext('2d'),stream=canvas.captureStream(15);const mime=MediaRecorder.isTypeSupported('video/webm;codecs=vp8')?'video/webm;codecs=vp8':'video/webm';
      const recorder=new MediaRecorder(stream,{mimeType:mime}),parts=[];const finished=new Promise((resolve,reject)=>{recorder.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};recorder.onstop=()=>resolve(new Blob(parts,{type:'video/webm'}));recorder.onerror=reject;});
      recorder.start();const begin=performance.now();const animation=setInterval(()=>{const t=(performance.now()-begin)/1000;ctx.fillStyle='#dbece6';ctx.fillRect(0,0,640,360);ctx.fillStyle='#087f76';ctx.beginPath();ctx.arc(100+t*300,190,42,0,Math.PI*2);ctx.fill();ctx.fillStyle='#244f49';ctx.font='20px sans-serif';ctx.fillText('小小宇宙无敌 / 视频预览',35,55);},60);
      await new Promise(r=>setTimeout(r,1400));clearInterval(animation);recorder.stop();const blob=await finished;stream.getTracks().forEach(t=>t.stop());demo('video',[{kind:'video',url:URL.createObjectURL(blob),mime:'video/webm',owned:true}],'本地动画样例，用于检查视频播放。');
    }
    log('已添加本地预览样例，没有调用 API。');notify('当前结果带有“演示样例”标记。填写渠道后，可查看真实模型输出。');
  }catch(e){notify('部分本地预览不可用：'+e.message,true);}finally{setBusy(false);}
}
$('runBtn').addEventListener('click',()=>run());$('resumeBtn').addEventListener('click',()=>{const id=$('taskId').value.trim();id?run(id):notify('请填写已有视频任务 ID。',true);});
$('stopBtn').addEventListener('click',()=>{controller?.abort();$('stopBtn').disabled=true;log('已停止等待；渠道上已提交的任务可能继续执行。');});
$('previewBtn').addEventListener('click',previewRequest);$('closePreview').addEventListener('click',()=>$('previewDialog').close());
$('loadModels').addEventListener('click',loadModels);$('exportBtn').addEventListener('click',exportReport);$('clearBtn').addEventListener('click',clearResults);$('demoBtn').addEventListener('click',showDemo);
$('showKey').addEventListener('click',()=>{const show=$('key').type==='password';$('key').type=show?'text':'password';$('showKey').textContent=show?'隐藏':'显示';$('showKey').setAttribute('aria-label',show?'隐藏密钥':'显示密钥');});
$('preset').addEventListener('change',applyPreset);
$('imageMode').addEventListener('change',updateImageMode);
$('promptScenario').addEventListener('change',applyPromptScenario);
$('prompt').addEventListener('input',()=>{$('promptScenario').value=promptScenarios().find(item=>item.prompt===$('prompt').value)?.id||'';updateScenarioDetails();});
$('clearFilesBtn').addEventListener('click',()=>{if(busy)return;clearFiles();notify('已清空本次参考文件。');});
$('sampleFilesBtn').addEventListener('click',()=>{const item=currentPromptScenario();loadSampleReferences(item?.requiresImages||1);});
$('applyModelAdvice').addEventListener('click',()=>{if(modelAdvice)applyRecommendation(modelAdvice);});
['base','key'].forEach(id=>$(id).addEventListener('input',syncModelContext));
$('auth').addEventListener('change',syncModelContext);
$('cancelModels').addEventListener('click',cancelModelFetch);
document.querySelectorAll('.kind-tab[data-kind]').forEach(el=>el.addEventListener('click',()=>switchKind(el.dataset.kind)));
$('files').addEventListener('change',handleFilesChange);
$('inputPreview').addEventListener('click',event=>{const button=event.target.closest('button[data-action]');if(!button||busy)return;const row=button.closest('.reference-item');const index=Number(row?.dataset.index);if(!Number.isInteger(index))return;if(button.dataset.action==='move-up')reorderReference(index,-1);else if(button.dataset.action==='move-down')reorderReference(index,1);else if(button.dataset.action==='remove-reference')removeReference(index);});
$('basicBtn').addEventListener('click',()=>setTextMode('basic'));
$('legacyBtn').addEventListener('click',()=>setTextMode('deep'));
$('backBtn').addEventListener('click',()=>setTextMode('basic'));
function initChoicePickers(){
  if(!window.ChoicePickers)return;
  const attach=(id,label,options)=>{const input=$(id);if(!input)return;input.removeAttribute('list');window.ChoicePickers.attach(input,{label,options});};
  attach('size','画面尺寸',['1024x1024','1536x1024','1024x1536','1280x720','720x1280']);
  attach('resolution','视频分辨率',['480p','720p','1080p','2k','4k']);
  attach('voice','音色',['alloy','nova','shimmer','coral','Kore','Puck','Aoede']);
}
fillPresets();$('prompt').value=kinds.text.prompt;applyPreset();initChoicePickers();updatePromptScenarios();renderInputPreview();

window.addEventListener('message',event=>{
  const frame=$('legacyFrame');if(event.source!==frame?.contentWindow)return;
  if(event.origin!==location.origin&&!(location.protocol==='file:'&&event.origin==='null'))return;
  const data=event.data;if(!data||data.type!=='workbench:general-history'||!data.record||data.record.kind!=='general'||data.record.source!=='general'||typeof data.record.client_id!=='string')return;
  let holder=$('generalHistorySaveStatus');if(!holder){holder=node('div','history-save-status');holder.id='generalHistorySaveStatus';frame.parentNode.insertBefore(holder,frame);}
  if(window.HistoryCapture)window.HistoryCapture.record(data.record,{container:holder});
});
