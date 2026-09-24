'use strict';
/* Portable counterpart of integrations/report_renderer.py.  It intentionally
 * uses the same section names and status vocabulary as the server renderer so
 * basic, general, CCMax and KVV exports have one visual language. */
(function () {
  const esc = v => String(v == null ? '—' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const STATUS = {success:'符合预期',passed:'符合预期',error:'本项异常',failed:'本项异常',pending:'证据待补齐',unrecognized:'证据待补齐',inconclusive:'证据待补齐',stopped:'已取消',cancelled:'已取消',demo:'已跳过',skipped:'已跳过',not_covered:'未覆盖'};
  const cls = s => ({success:'passed',passed:'passed',error:'failed',failed:'failed',pending:'inconclusive',unrecognized:'inconclusive',inconclusive:'inconclusive',stopped:'cancelled',cancelled:'cancelled',demo:'skipped',skipped:'skipped',not_covered:'not_covered'}[s] || 'inconclusive');
  const badge = (s,label,tone) => `<span class="badge ${tone||cls(s)}" data-status="${cls(s)}">${esc(label||STATUS[s]||s||'未记录')}</span>`;
  const scrub = value => {
    const sensitive = /^(authorization|proxy-authorization|x-api-key|x-goog-api-key|cookie|set-cookie|api[_-]?key|access_token|refresh_token|secret|password|key|x-workbench-token)$/i;
    const secrets = new Set(), scanned = new WeakSet(), parents = new WeakSet();
    const collect = (v,k='') => {
      if(sensitive.test(k)&&typeof v==='string'&&v&&!v.includes('[已隐藏]')){secrets.add(v);if(/^Bearer\s+/i.test(v))secrets.add(v.replace(/^Bearer\s+/i,''));}
      if(!v||typeof v!=='object'||scanned.has(v))return;scanned.add(v);
      for(const [name,item]of Object.entries(v))collect(item,name);
    };collect(value);
    const walk = (v, k='') => {
      if (sensitive.test(k)) return '[已隐藏]';
      if (typeof v === 'string') {
        for(const secret of secrets)v=v.split(secret).join('[已隐藏]').split(encodeURIComponent(secret)).join('[已隐藏]');
        return v.replace(/Bearer\s+[^\s"<>]+/ig,'Bearer [已隐藏]').replace(/sk-[A-Za-z0-9_-]{8,}/g,'[已隐藏]').replace(/([?&](?:api[_-]?key|key|access_token|refresh_token|secret|password)=)[^&#\s"<>]*/gi,'$1[已隐藏]');
      }
      if (v && typeof v === 'object') {
        if(parents.has(v))return '[循环引用]';parents.add(v);
        const out=Array.isArray(v)?v.map(x=>walk(x)):Object.fromEntries(Object.entries(v).map(([key,val])=>[key,walk(val,key)]));
        parents.delete(v);return out;
      }
      return v;
    };
    return walk(value);
  };
  const kindName = {text:'文本',image:'图像',video:'视频',audio:'音频',general:'通用深度检测',gpt:'GPT 生成专项'};
  const tokenValue=v=>{if(v==null||v==='')return null;const n=Number(v);return Number.isInteger(n)&&Number.isFinite(n)&&n>=0?n:null;};
  const firstToken=(value,...keys)=>{const source=value&&typeof value==='object'?value:{};for(const key of keys){const n=tokenValue(source[key]);if(n!==null)return n;}return null;};
  const usageParts=val=>{
    const usage=val&&typeof val==='object'?val:{},promptDetails=object(usage.prompt_tokens_details),inputDetails=object(usage.input_tokens_details);
    const native=Object.hasOwn(usage,'cache_read_input_tokens')||Object.hasOwn(usage,'cache_creation_input_tokens')||Object.hasOwn(usage,'cache_read_tokens')||Object.hasOwn(usage,'cache_creation_tokens');
    const input=firstToken(usage,'prompt_tokens','promptTokenCount','prompt_tokens_count','input_tokens','inputTokens');
    const cached=native?firstToken(usage,'cache_read_input_tokens','cache_read_tokens','prompt_cache_read_tokens','prompt_cache_hit_tokens'):firstToken(promptDetails,'cached_tokens','cachedTokens','cache_read_tokens')??firstToken(inputDetails,'cached_tokens','cachedTokens','cache_read_tokens')??firstToken(usage,'cached_tokens','cachedTokens','cachedContentTokenCount','cache_read_tokens','prompt_cache_read_tokens','prompt_cache_hit_tokens');
    const created=native?firstToken(usage,'cache_creation_input_tokens','cache_creation_tokens','prompt_cache_creation_tokens'):0;
    return {input,cached,created,native};
  };
  const extractUsage = raw => {
    const seen=new WeakSet(); let found=null;
    const num=v=>{if(v==null||v==='')return null;const n=Number(v);return Number.isFinite(n)&&n>=0?n:null;};
    const walk=v=>{
      if(!v||typeof v!=='object'||seen.has(v)||found)return; seen.add(v);
      if(Array.isArray(v)){v.forEach(walk);return;}
      for(const [k,val] of Object.entries(v)){
        if((k==='usage'||k==='token_usage'||k==='tokenUsage')&&val&&typeof val==='object'){
          const parts=usageParts(val);
          const input=parts.input===null?num(val.input):parts.input;
          const output=num(val.completion_tokens??val.completionTokens??val.output_tokens??val.outputTokens??val.output);
          const total=num(val.total_tokens??val.totalTokens??val.total);
          const cached=parts.cached;
          const derived=val.total_derived===true||val.total_tokens_derived===true||v.total_derived===true||v.total_tokens_derived===true;
          const applicable=val.accounting_applicable!==false&&v.accounting_applicable!==false;
          if(input!==null||output!==null||total!==null)found={input,output,total,cached,derived,applicable};
        }
        walk(val);
      }
    }; walk(raw); return found;
  };
  const usageHtml = (raw, force=false) => {
    const u=extractUsage(raw); if(!u&&!force)return '';
    if(!u)return `<div class="token-report"><b>Token usage</b><span>输入 —</span><span>输出 —</span><span>总计 —</span><em class="wait">渠道未返回 usage，无法核对 token 一致性</em></div>`;
    const fmt=v=>v===null||v===undefined?'—':Number(v).toLocaleString('en-US');
    const check=!u.derived&&u.applicable&&u.input!==null&&u.output!==null&&u.total!==null ? (u.input+u.output===u.total) : null;
    const text=u.derived?'总量为本地派生值，未验证渠道上报总量':!u.applicable?'当前协议不适用总量加总校验，未验证渠道上报总量':check===null?'未提供完整 token 字段':check?'输入 + 输出 = 总计，usage 一致':'输入 + 输出 ≠ 总计，请核对渠道 usage 定义';
    return `<div class="token-report"><b>Token usage</b><span>输入 ${fmt(u.input)}</span><span>输出 ${fmt(u.output)}</span><span>总计 ${fmt(u.total)}${u.derived?'（派生）':''}</span>${u.cached!==null?`<span>缓存 ${fmt(u.cached)}</span>`:''}<em class="${check===true?'ok':check===false?'bad':'wait'}">${esc(text)}</em></div>`;
  };
  const gptAssessmentHtml = r => {
    const prompt=String(r.config?.prompt||''); if(r.scenarioId!=='gpt-html-animation'&&!/鹈鹕骑自行车|SVG绘制.*2D动画/i.test(prompt))return '';
    const output=String(r.text||'').trim(),clean=output.replace(/^```(?:html)?\s*/i,'').replace(/\s*```$/,'').trim();
    const checks=[['HTML 文档',/(?:<!doctype\s+html|<html\b|<body\b)/i.test(clean)],['SVG 绘制',/<svg\b/i.test(clean)],['动画效果',/(?:<animate\b|<animateTransform\b|@keyframes\b|animation(?:-name|-duration)?\s*:|requestAnimationFrame\s*\(|setInterval\s*\()/i.test(clean)],['未使用 Markdown 围栏',!/^```|```$/m.test(output)],['未出现拒答',!/抱歉|我不能|无法完成|不能帮助|拒绝/i.test(clean)]];
    const passed=checks.filter(([,ok])=>ok).length;
    return `<div class="gpt-report-assessment"><b>GPT 专项判读 · HTML/SVG 降智检查</b><div class="gpt-report-grid">${checks.map(([label,ok])=>`<span>${esc(label)} <em class="${ok?'ok':'bad'}">${ok?'通过':'需核对'}</em></span>`).join('')}</div><p>启发式结果：${passed}/${checks.length} 项符合预期。请结合 HTML/SVG 输出和浏览器预览人工确认主体与连续运动。</p></div>`;
  };
  const DIMENSIONS = [['multimodal','多模态能力'],['tools','工具调用'],['max_tokens','max_tokens / 长度控制'],['cache','缓存与 usage'],['protocol','协议与错误'],['reliability','稳定性与性能'],['security','注入与指令隔离']];
  const list = v => Array.isArray(v) ? v : [];
  const object = v => v && typeof v==='object' && !Array.isArray(v) ? v : {};
  const display = v => v==null||v===''?'未记录':typeof v==='string'?v:JSON.stringify(v,null,2);
  const dimensions = check => {
    const explicit=list(check.dimensions).length?check.dimensions:list(check.metadata?.dimensions);
    if(explicit.length)return [...new Set(explicit.map(key=>key==='injection'?'security':key).filter(key=>DIMENSIONS.some(d=>d[0]===key)))];
    const name=String(check.title||check.name||check.id||'');
    const keys=[['multimodal',/视觉|多模态|图像|图片|视频|音频|vision/i],['tools',/工具|tool|function/i],['max_tokens',/max.?tokens|max.?output.?tokens|长度|参数/i],['cache',/缓存|cache|usage|token.*账本|token.*一致/i],['reliability',/延迟|吞吐|并发|稳定|流式|stream/i]].filter(([,pattern])=>pattern.test(name)).map(([key])=>key);
    return keys.length?keys:['protocol'];
  };
  // Match the browser report's weighted modules; each check belongs to one
  // module while dimensions remain independent views of the same evidence.
  const MODULES = [
    ['protocol','接口与协议',20,'请求、HTTP 状态、响应结构与错误诊断。'],
    ['multimodal','多模态结果',20,'图像、视频、音频输入及返回内容的实际证据。'],
    ['tools','工具调用',15,'工具声明、参数、选择与结果回传。'],
    ['max_tokens','长度控制',10,'合法长度上限、截断与非法参数对照。'],
    ['cache','缓存与 usage',10,'计量字段及大 Token 前缀重复请求。'],
    ['reliability','稳定性与性能',10,'受控负载、耗时、失败、限流与超时。'],
    ['security','注入与指令隔离',15,'合成金丝雀与不可信输入的隔离观察。'],
  ];
  const numeric=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0?v:null;
  const tokenNumber=v=>numeric(v)!==null&&Number.isInteger(v)?v:null;
  const roundScore=value=>{const low=Math.floor(value);return value-low===.5?(low%2===0?low:low+1):Math.round(value);};
  const compactNumber=v=>Number.isInteger(v)?String(v):String(Math.round(v*10)/10);
  function moduleId(x){
    const raw=x.raw,explicit=raw.module||raw.metadata?.module;
    const canonical={injection:'security',stress:'reliability'}[explicit]||explicit;
    if(MODULES.some(m=>m[0]===canonical))return canonical;
    if(x.r.kind!=='general'&&['image','video','audio'].includes(x.r.kind))return 'multimodal';
    return x.dims.find(key=>MODULES.some(m=>m[0]===key))||'protocol';
  }
  function scoreGroup(matched){
    const units=matched.flatMap(x=>{
      const rows=list(x.raw?.evidence_rows);
      if(!x.raw?.metadata?.score_by_sample||rows.length<2)return [x];
      return rows.map(row=>({...x,status:row.status||'inconclusive',linked:row.request_ids?x.linked.filter(req=>row.request_ids.includes(req.id)):x.linked}));
    });
    const scored=units.filter(eligible),sampled=units.filter(observed),passed=scored.filter(x=>x.status==='passed').length,failed=scored.length-passed;
    const value=scored.length?roundScore(passed/scored.length*100):null;
    const status=failed?'failed':!sampled.length?'not_covered':sampled.some(x=>x.status==='inconclusive')||!scored.length?'inconclusive':'passed';
    return {matched,scored,sampled,passed,failed,value,status};
  }
  // Presentation does not rewrite the stored assertion status or its score.
  function groupPresentation(s){
    if(s.failed)return {display_label:s.passed?'部分异常':'需重点核查',display_tone:s.passed?'attention':'risk'};
    if(!s.scored.length)return {display_label:s.status==='not_covered'?'未覆盖':'证据待补齐',display_tone:'neutral'};
    if(s.sampled.length>s.scored.length)return {display_label:'已测项符合预期',display_tone:'attention'};
    return {display_label:'本轮符合预期',display_tone:'passed'};
  }
  function groupConclusion(s,label){
    if(s.failed&&s.passed)return `${s.passed>s.failed?'多数检查通过':'部分检查通过'} · ${s.failed} 项异常。通过 ${s.passed}/${s.scored.length} 项已判定检查。`;
    if(s.failed)return `${label}需重点核查：${s.failed}/${s.scored.length} 项已判定检查出现异常。`;
    if(s.scored.length)return `已测检查符合预期：${s.passed}/${s.scored.length} 项已判定检查通过。`;
    return `${label}证据不足，尚不能确认。`;
  }
  function resourceGrade(score,resolution,weightCovered,cases){
    const sampleCount=new Set(cases.filter(observed).flatMap(x=>x.linked.map(r=>r.id))).size;
    const reasons=[];
    if(resolution===null||resolution<80)reasons.push(`证据可判定率 ${resolution===null?'未记录':resolution+'%'}（目标 ≥80%）`);
    if(weightCovered<70)reasons.push(`可评分权重 ${weightCovered}%（目标 ≥70%）`);
    if(sampleCount<10)reasons.push(`关联能力请求 ${sampleCount} 个（目标 ≥10）`);
    const level=score===null?'unknown':score>=90?'high':score>=70?'medium':'low';
    const label={high:'优质资源',medium:'中等资源',low:'低等级资源',unknown:'待评估'}[level];
    const batch=new Set(cases.map(x=>x.model)).size>1;
    return {level,label,provisional:batch||(score!==null&&reasons.length>0),reasons,sampleCount,batch};
  }
  function responseUsage(raw){
    // Never inspect request bodies or a quoted model answer for accounting.
    let value=raw.response_body??raw.response??raw.raw_response;
    if(typeof value==='string'){try{value=JSON.parse(value);}catch(_){return null;}}
    if(!value||typeof value!=='object')return null;
    const body=typeof value.body==='string'?(()=>{try{return JSON.parse(value.body);}catch(_){return null;}})():value.body;
    for(const candidate of [value.usage,body?.usage,value.response?.usage,value.normalized_usage,value.usageMetadata,body?.usageMetadata,value])if(candidate&&typeof candidate==='object'&&(candidate.input_tokens!=null||candidate.prompt_tokens!=null||candidate.promptTokenCount!=null||candidate.cache_read_input_tokens!=null||candidate.cachedContentTokenCount!=null))return candidate;
    return null;
  }
  const percentLabel=value=>{
    if(value===null||value===undefined)return '未记录';
    if(value>0&&value<0.01)return '<0.01';
    if(value>0&&value<1)return Number(value.toFixed(6)).toString();
    return Number(value.toFixed(6)).toString();
  };
  function cacheBrief(cases){
    const matched=cases.filter(x=>x.dims.includes('cache')),warm=new Map();
    for(const x of matched){
      const params=x.raw.parameters||x.raw.metadata?.parameters||{},round=String(params.variant||params.round||x.raw.scenario_id||x.sourceId||'');
      // Claude's native acceptance result keeps the per-round usage under
      // cache_observations.  It may have no separate browser request row, so
      // associate those observations directly instead of guessing a missing
      // usage field as zero.
      for(const observation of list(x.raw.cache_observations)){
        const sampleId=String(observation.sample_id||observation.id||'');
        if(!sampleId||observation.prefix_control||!/cache-(?:2|3)|warm|suffix/i.test(sampleId))continue;
        const read=tokenValue(observation.cache_read_input_tokens??observation.cache_read_tokens);
        const total=tokenValue(observation.input_tokens??observation.prompt_tokens??observation.promptTokenCount);
        if(read===null&&total===null)continue;
        const usage={prompt_tokens:total,prompt_tokens_details:{cached_tokens:read}};
        warm.set(sampleId,{req:{id:sampleId,raw:{response_body:{usage}},status:observation.status==='failed'||observation.status==='cancelled'?'failed':'passed'},x});
      }
      if(/cold|prefix_changed|changed_prefix/i.test(round)||!/(?:^|[-_ ])warm(?:[-_ \d]|$)|suffix_changed/i.test(round))continue;
      for(const req of x.linked)warm.set(req.id,{req,x});
    }
    let read=0,input=0,complete=0,hits=0;
    const used=[];
    for(const {req,x}of warm.values()){
      const usage=responseUsage(req.raw);if(!usage||req.status!=='passed')continue;
      const parts=usageParts(usage),cached=parts.cached,base=parts.input,created=parts.created;
      // Anthropic input_tokens excludes cache reads and creation; both fields
      // must exist before a complete input denominator can be claimed.
      if(cached===null||base===null||created===null)continue;
      const denominator=parts.native?base+cached+created:base;
      if(!denominator||cached>denominator)continue;
      complete++;read+=cached;input+=denominator;if(cached>0)hits++;used.push(x.id);
    }
    const rawPercent=complete?read/input*100:null,percent=complete?(rawPercent>0&&rawPercent<0.01?rawPercent:Number(rawPercent.toFixed(6))):null,zeroObserved=complete>0&&read===0;
    const text=percent===null?`缓存命中率未知：${warm.size?`已关联 ${warm.size} 个暖请求，缺少完整缓存读取 / 输入计量。`:'没有明确关联暖请求及完整 usage。'}缓存模块分数不等于缓存命中率。`:`${zeroObserved?'本轮未观察到复用。':''}${complete<warm.size?'已计量暖请求':'暖请求'}缓存 Token 命中率 ${percentLabel(percent)}%（读取 ${read.toLocaleString('en-US')} / 完整输入 ${input.toLocaleString('en-US')} Token）；命中请求 ${hits}/${complete}，字段完整 ${complete}/${warm.size} 个暖请求。${zeroObserved?'需核对前缀、缓存条件及上游计量。':''}`;
    return {id:'cache',label:'缓存复用',status:percent===null||complete<warm.size||zeroObserved?'inconclusive':'passed',display_label:percent===null?'复用证据待补齐':zeroObserved?'本轮未观察到复用':complete<warm.size?'部分计量已验证':'已观察到复用',display_tone:percent===null?'neutral':zeroObserved||complete<warm.size?'attention':'passed',text,check_ids:[...new Set(used.length?used:matched.map(x=>x.id))],percent};
  }
  const timestamp=value=>{
    if(value==null||value==='')return null;
    if(typeof value==='number')return Number.isFinite(value)&&value>0?(value<1e11?value*1000:value):null;
    const parsed=Date.parse(value);return Number.isFinite(parsed)?parsed:null;
  };
  const durationLabel=ms=>ms===null?'未记录':ms<1000?`${Math.round(ms)} 毫秒`:ms<60000?`${compactNumber(ms/1000)} 秒`:ms<3600000?`${Math.floor(ms/60000)} 分 ${Math.round(ms%60000/1000)} 秒`:`${Math.floor(ms/3600000)} 时 ${Math.floor(ms%3600000/60000)} 分`;
  function timing(records,requests){
    const intervals=[];
    for(const record of records){
      const result=object(record.result),start=timestamp(record.started_at??record.created_at??result.started_at??result.created_at),elapsed=numeric(record.duration_ms??result.duration_ms);
      const end=timestamp(record.finished_at??record.completed_at??result.finished_at??result.completed_at)??(start!==null&&elapsed!==null&&elapsed>0?start+elapsed:null);
      if(start!==null&&end!==null&&end>=start)intervals.push([start,end]);
    }
    let source='完整测试时间',available=intervals.length===records.length&&intervals.length>0;
    if(!available){intervals.length=0;source='请求观测窗口（非完整测试耗时）';for(const req of requests){const start=timestamp(req.raw.started_at),elapsed=numeric(req.raw.duration_ms??req.raw.elapsedMs);if(start!==null&&elapsed!==null)intervals.push([start,start+elapsed]);}}
    const start=intervals.length?Math.min(...intervals.map(x=>x[0])):null,end=intervals.length?Math.max(...intervals.map(x=>x[1])):null;
    const durations=requests.map(r=>numeric(r.raw.duration_ms??r.raw.elapsedMs)).filter(v=>v!==null).sort((a,b)=>a-b);
    const percentile=p=>{if(!durations.length)return null;const n=(durations.length-1)*p,i=Math.floor(n);return durations[i]+(durations[Math.ceil(n)]-durations[i])*(n-i);};
    return {label:durationLabel(start===null?null:end-start),source:intervals.length?source:'未记录测试起止时间',start,end,p50:percentile(.5),p95:percentile(.95),request_count:durations.length};
  }
  function executive(cases,requests,records){
    const items=[],stats=scoreGroup(cases);
    const add=(id,label,selected)=>{
      const s=scoreGroup(selected),text=groupConclusion(s,label);
      items.push({id,label,status:s.status,...groupPresentation(s),text:text+(s.sampled.length>s.scored.length?` 另有 ${s.sampled.length-s.scored.length} 项待判定。`:''),check_ids:selected.filter(x=>x.status==='failed').concat(selected).map(x=>x.id)});
    };
    add('protocol','基础能力',cases.filter(x=>moduleId(x)==='protocol'));
    add('tools_media','工具与多模态',cases.filter(x=>x.dims.includes('tools')||x.dims.includes('multimodal')));
    const limited=cases.filter(x=>x.dims.includes('max_tokens'));
    const limits=scoreGroup(limited),invalid=limited.filter(x=>{
      const p=x.raw.parameters||x.raw.metadata?.parameters||{};return [p.max_tokens,p.max_output_tokens,p.max_completion_tokens,p.cap].some(v=>typeof v==='number'&&(!Number.isInteger(v)||v<=0))||/invalid|非法|负值/.test(String(x.raw.scenario_id||x.title));
    }),invalidFailures=invalid.filter(x=>eligible(x)&&x.status==='failed'),capFailure=limited.filter(x=>eligible(x)&&x.status==='failed'&&!invalid.includes(x)&&x.raw.reason_code==='output_cap_exceeded');
    const limitText=capFailure.length?`发现 ${capFailure.length} 个合法上限样本输出超限，相关样本的长度控制未遵守请求。已判定检查通过 ${limits.passed}/${limits.scored.length} 项。`:limits.failed?`${invalidFailures.length===limits.failed?'非法限长参数校验存在异常，不能据此判定合法限长失效':'限长测试部分参数需核查，按具体错误原因复核'}（通过 ${limits.passed}/${limits.scored.length}，异常 ${limits.failed} 项）。`:limits.scored.length?`本轮限长检查通过 ${limits.passed}/${limits.scored.length} 项。${limits.sampled.length>limits.scored.length?'仍有未触及截断或证据不足的样本。':''}`:'限长证据不足，尚不能确认截断是否生效。';
    items.push({id:'max_tokens',label:'长度控制',status:limits.status,...groupPresentation(limits),text:limitText,check_ids:limited.filter(x=>x.status==='failed').concat(limited).map(x=>x.id)});
    const isolation=cases.filter(x=>x.dims.includes('security')),is=scoreGroup(isolation);
    items.push({id:'security',label:'指令隔离',status:is.status,...groupPresentation(is),text:(is.failed?`指令隔离存在异常，${is.failed}/${is.scored.length} 项已判定检查需核查，另有 ${is.passed} 项通过。`:is.scored.length?`本轮隔离检查 ${is.passed}/${is.scored.length} 项通过。`:'指令隔离证据不足。')+'不能据此认定上游私自添加提示词。',check_ids:isolation.filter(x=>x.status==='failed').concat(isolation).map(x=>x.id)});
    items.push(cacheBrief(cases));
    add('reliability','负载与稳定性',cases.filter(x=>x.dims.includes('reliability')));
    const distinctModels=[...new Set(cases.map(x=>x.model))];
    if(distinctModels.length>1)return {items:[{id:'batch',label:'多模型结果',status:stats.failed?'failed':'inconclusive',display_label:'按模型独立判读',display_tone:'neutral',text:'各模型独立判读。异常和缓存率请按对应模型检查证据阅读；跨模型汇总不能证明单个模型能力。',check_ids:cases.filter(x=>x.status==='failed').map(x=>x.id)}],headline:'多模型报告：各模型独立判读',detail:`包含 ${distinctModels.length} 个模型。综合分和评级汇总本报告证据，单个模型需独立判读。`,resolution:stats.sampled.length?roundScore(stats.scored.length/stats.sampled.length*100):null,timing:timing(records,requests)};
    const warnings=items.filter(x=>x.status==='failed'),basic=items[0];
    const cache=items.find(x=>x.id==='cache'),cachePhrase=cache.percent===null?'':`已计量暖缓存 Token ${compactNumber(cache.percent)}%`;
    const basicStats=scoreGroup(cases.filter(x=>moduleId(x)==='protocol'));
    const basicPhrase=basicStats.passed?(basicStats.failed?'基础能力部分符合预期':'基础接口可用'):'';
    const headline=warnings.length?[basicPhrase,...warnings.filter(x=>x.id!=='protocol'||!basicPhrase).slice(0,cachePhrase?2:3).map(x=>x.id==='max_tokens'&&capFailure.length?'部分限长样本出现超限':x.id==='security'?'指令隔离需核查':x.label+(x.display_tone==='attention'?'部分异常':'需重点核查')),cachePhrase].filter(Boolean).slice(0,4).join('；'):[stats.scored.length?'已判定检查符合预期'+(stats.sampled.length>stats.scored.length?'，部分能力证据待补齐':'，以本轮覆盖范围为准'):'尚无可判定能力证据',cachePhrase].filter(Boolean).join('；');
    return {items,headline,detail:`${stats.passed} 项通过，${stats.failed} 项异常；${stats.sampled.length-stats.scored.length} 项待判定。逐项异常不等于整个模型不可用；缓存比例依据实际暖请求计量。`,resolution:stats.sampled.length?roundScore(stats.scored.length/stats.sampled.length*100):null,timing:timing(records,requests)};
  }
  const css = `:root{--ink:#233d32;--muted:#6e7e73;--line:#dce5dc;--paper:#f4f7f2;--green:#28664e;--red:#ac4b3d;--amber:#936b22}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:80px}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}a{color:var(--green);text-decoration:none}main{max-width:1200px;margin:auto;padding:32px 28px 60px}h1,h2,h3,p{margin:0}h1{font-size:32px;line-height:1.3;overflow-wrap:anywhere}h2{font-size:21px}h3{font-size:16px}.masthead,.cover-top,.verdict-line,.section-head,.check-head,.score-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.masthead{padding-bottom:18px}.brand{font-weight:650;font-size:16px}.brand:before{content:'宇';display:inline-grid;place-items:center;width:32px;height:32px;color:#fff;background:var(--ink);border-radius:10px;margin-right:10px}.eyebrow,.index{font-size:10px;letter-spacing:.13em;color:var(--muted);font-weight:600}.cover{background:var(--ink);color:#f8fbf7;border-radius:20px;padding:32px}.cover .eyebrow{color:#b6cbb8}.cover p{color:#c6d5c9;margin-top:9px}.run-id{font:11px ui-monospace,monospace;opacity:.75}.nav{display:flex;gap:20px;flex-wrap:wrap;position:sticky;top:0;z-index:2;padding:17px 0;background:#f4f7f2ed;border-bottom:1px solid var(--line);font-size:12px}.overview{padding:24px 0}.verdict-line p{color:var(--muted);margin-top:5px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric,.panel,.check,.request,.finding,.score-dimension{background:#fff;border:1px solid var(--line);border-radius:14px}.metric{padding:18px 20px}.metric b{font-size:30px;display:block;line-height:1.3}.metric span,.metric small,.muted,.score-status,.score-evidence{color:var(--muted);font-size:11px}.metric small{display:block;margin-top:4px}.distribution{display:flex;height:7px;border-radius:10px;overflow:hidden;background:#e5ebe3;margin-top:18px}.distribution span{min-width:0}.distribution .passed{background:#418062}.distribution .failed{background:#bb6453}.distribution .inconclusive,.distribution .cancelled{background:#c1994b}.legend{font-size:11px;color:var(--muted);margin-top:8px}.section{margin-top:26px}.panel{padding:22px}.grid-two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.key-value{display:grid;grid-template-columns:125px minmax(0,1fr);gap:8px 14px;margin:0}.key-value dt{color:var(--muted);font-size:12px}.key-value dd{margin:0;overflow-wrap:anywhere}.scope-list{padding-left:19px;margin:0;color:var(--muted);font-size:12px}.scope-list li+li{margin-top:7px}.badge{display:inline-block;white-space:nowrap;font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;background:#eef1ed;color:#627363}.badge.passed{color:#28664e;background:#e9f3e9}.badge.failed{color:#a0483c;background:#faebe6}.badge.inconclusive,.badge.cancelled{color:#86611d;background:#faf1da}.badge.skipped{color:#687568;background:#edf0ea}.score-panel{margin-top:18px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}.score-total{font-size:32px;color:var(--green)}.score-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.score-dimension{padding:13px;background:#fbfcfa}.score-dimension-head{display:flex;justify-content:space-between;gap:8px;font-size:12px}.score-dimension b{font-size:19px}.bar{height:5px;border-radius:5px;background:#e6ece4;margin:8px 0;overflow:hidden}.bar i{display:block;height:100%;background:#5f916d}.score-dimension.failed .bar i{background:#bb6453}.score-dimension.inconclusive .bar i{background:#c1994b}.recommendations{margin:14px 0 0;padding-left:18px;font-size:11px;color:var(--muted)}.check{padding:21px;margin-top:12px}.case-id{font:11px ui-monospace,monospace;color:var(--muted);margin-top:4px}.method{margin-top:12px}.field-label{display:block;color:var(--muted);font-size:11px;font-weight:600}.matrix-table,.compact-table{width:100%;border-collapse:collapse;font-size:11px;margin-top:14px;background:#fff}.matrix-table{border:1px solid var(--line);border-radius:14px;overflow:hidden}.matrix-table th,.matrix-table td,.compact-table th,.compact-table td{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}.matrix-table th,.compact-table th{color:var(--muted);font-weight:600}.matrix-table .status-cell{width:38px;text-align:center;font-size:19px}.matrix-table tr.row-failed td{background:#fff9f7}.matrix-table tr.row-inconclusive td{background:#fffdf5}.interpretation{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:13px;font-size:12px}.interpretation p{margin-top:4px}.findings{display:grid;grid-template-columns:1fr 1fr;gap:14px}.finding{padding:18px;border-left:3px solid #be6a58}.finding h3{margin:9px 0}.finding p{margin-top:7px;font-size:12px}.request{margin:10px 0;padding:15px 18px}.request summary{cursor:pointer;list-style:none;display:flex;gap:12px;align-items:center}.request summary:before{content:'+';color:#7f9579;font-size:17px}.request[open] summary:before{content:'−'}.request-summary{color:var(--muted);font-size:11px;flex:1}.request-body{border-top:1px solid var(--line);margin-top:14px;padding-top:15px}.raw{margin-top:12px;border-top:1px solid #e8ede5;padding-top:10px}.raw summary{cursor:pointer;color:var(--green);font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#f4f7f1;border:1px solid #e0e8dc;border-radius:8px;padding:14px;font:11px/1.75 ui-monospace,monospace}.media{display:flex;flex-wrap:wrap;gap:13px;margin-top:14px}.media figure{margin:0;flex:1 1 280px;padding:10px;border:1px solid var(--line);border-radius:10px;background:#fbfcfa}.media img,.media video{display:block;width:100%;max-height:500px;object-fit:contain;border-radius:7px;background:#f0f3eb}.media audio{width:100%}.media figcaption{font-size:11px;margin-top:7px}.token-report{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:13px;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:#fbfcfa;font-size:11px}.token-report>b{font:600 10px ui-monospace,monospace;letter-spacing:.06em;color:var(--muted)}.token-report span{padding:2px 7px;border:1px solid var(--line);border-radius:5px;background:#fff;font-variant-numeric:tabular-nums}.token-report em{font-style:normal;color:var(--muted);font-size:10px}.token-report em.ok{color:var(--green)}.token-report em.bad{color:var(--red);font-weight:600}.token-report em.wait{color:var(--amber)}footer{display:flex;justify-content:space-between;gap:16px;margin-top:26px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:11px}@media(max-width:700px){main{padding:18px 14px 35px}h1{font-size:25px}.metrics,.grid-two,.findings,.interpretation{grid-template-columns:1fr 1fr}.score-grid{grid-template-columns:1fr 1fr}.panel,.check{padding:17px}.nav{gap:14px;padding:13px 0}.grid-two,.findings,.interpretation{grid-template-columns:1fr}.key-value{grid-template-columns:90px minmax(0,1fr)}.request summary{flex-wrap:wrap}}@media print{body{background:#fff}main{max-width:none;padding:0}.nav{display:none}.cover{background:#fff;color:var(--ink);border:1px solid var(--line)}.cover p,.cover .eyebrow{color:var(--muted)}.panel,.check,.request,.finding{break-inside:avoid}}`;
  async function mediaHtml(items) {
    const rows = [];
    for (const item of Array.isArray(items) ? items : []) {
      const kind = item?.kind || item?.type; let url = String(item?.url || '');
      if (!['image','video','audio'].includes(kind) || !url) continue;
      if (url.startsWith('blob:') || url.startsWith('filesystem:')) {
        try { const blob = await fetch(url).then(r => r.blob()); url = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob); }); } catch (_) { continue; }
      }
      if (!/^https?:\/\//i.test(url) && !/^data:(image|video|audio)\//i.test(url)) continue;
      if(/^https?:/i.test(url)){try{const parsed=new URL(url);if(parsed.username||parsed.password)continue;}catch(_){continue;}}
      const tag = kind === 'image' ? 'img' : kind;
      rows.push(`<figure><${tag} src="${esc(url)}"${tag === 'img' ? ' alt="生成图片" loading="lazy"' : ' controls preload="none"'}>${tag === 'img' ? '' : `</${tag}>`}<figcaption>${/^data:/.test(url) ? '已嵌入本报告 · 可离线查看' : `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开媒体 ↗</a> · 远程链接需联网`}</figcaption></figure>`);
    }
    return rows.length ? `<div class="media">${rows.join('')}</div>` : '';
  }
  function normalize(records) {
    const cases=[],requests=[],originals=[];
    list(records).forEach((record,index)=>{
      const result=Object.keys(object(record.result)).length?record.result:record,config={...object(record.config),...object(result.config)};
      const model=record.model||config.model||'未命名模型',prefix='record-'+(index+1),isGeneral=record.kind==='general';
      const aliases=new Map(),recordRequests=[];
      list(result.requests||record.requests).forEach((raw,i)=>{
        const code=typeof raw.status==='number'?raw.status:raw.http_status;
        const req={raw,id:prefix+'-request-'+(i+1),model:raw.model||model,code,url:raw.url||raw.endpoint,status:raw.error||Number(code)>=400?'failed':Number(code)>0&&Number(code)<400?'passed':'inconclusive',caseIds:[]};
        for(const key of [raw.id,raw.request_id,...list(raw.ids),...list(raw.request_ids)].filter(v=>v!=null)){const id=String(key);aliases.set(id,[...(aliases.get(id)||[]),req]);}
        requests.push(req);recordRequests.push(req);
      });
      const addCase=(check,i)=>{
        const sourceId=check.id||check.check_id,title=check.title||check.name||(isGeneral?'通用检查':result.preset||record.preset||`${kindName[record.kind]||'渠道'}基础测试`);
        const applicable=check.applicable!==false,status=applicable?cls(check.status):'skipped',checkModel=check.model||model,id=prefix+'-check-'+(i+1);
        const description=isGeneral?object(window.GeneralCheckContent?.describe?.(title,status)):{};
        let linked=isGeneral?[]:[...recordRequests];
        for(const alias of [check.request_id,check.requestId,...list(check.request_ids),...list(check.requestIds)].filter(v=>v!=null)){
          const matches=(aliases.get(String(alias))||[]).filter(req=>!check.model||req.model===checkModel);
          if(matches.length===1&&!linked.includes(matches[0]))linked.push(matches[0]);
        }
        if(isGeneral&&sourceId!=null)for(const req of recordRequests){const raw=req.raw,refs=[raw.case_id,raw.check_id,...list(raw.case_ids),...list(raw.check_ids)].filter(v=>v!=null).map(String);if(refs.includes(String(sourceId))&&(!check.model||req.model===checkModel)&&!linked.includes(req))linked.push(req);}
        linked.forEach(req=>req.caseIds.push(id));
        const reason=check.skip_reason||check.reason||check.meaning||check.judge||description.meaning||(status==='passed'?'本轮保存的断言通过；结论限定于当前样本。':status==='skipped'?'此项已跳过或不适用于本次协议，不计入评分。':status==='not_covered'?'本轮未执行该项，不评价模型是否支持。':'保存的证据不足或断言存在异常；应依据 HTTP 状态、错误正文和用例预期核对，不能仅据模型名称归因。');
        const next=check.next_step||check.advice||(!applicable?'不适用于当前协议，不参与计分。':description.next_step)||(status==='passed'?'需要上线验收时扩大输入、重复次数与模型样本。':status==='skipped'||status==='not_covered'?'如业务需要此能力，选择适用协议后补测该专项。':'按本项请求 ID 核对端点、模型、鉴权与原始响应，再重跑失败用例。');
        const r={...record,...result,config,model:checkModel,raw:check.raw||result.raw};
        cases.push({id,sourceId,title,model:checkModel,status,applicable,localOnly:check.local_only===true,dims:isGeneral?dimensions({...description,...check}):['protocol',...(['image','video','audio'].includes(record.kind)?['multimodal']:[])],method:check.method||description.method||(isGeneral?`执行已保存的“${title}”内置用例；具体方法以请求证据为准。`:'使用所选协议提交一次实际请求，检查接口是否返回可解析的文本或媒体。'),expected:check.expected||description.expected||(isGeneral?'旧记录未保存独立预期值，按原始用例判定复核；不补造具体断言。':'返回与当前协议匹配的文本或媒体；普通返回成功不代表专项能力已通过。'),observed:check.observed??check.result??check.actual??result.error??result.text??result.output??(list(record.media).length?`返回 ${record.media.length} 项媒体。`:'未记录可展示结果。'),reason,next,linked,raw:check,r});
      };
      if(isGeneral){
        originals.push({model,mode:result.mode,total:result.total,scores:result.scores,batch:result.batch,logs:result.logs,intelligence:result.intelligence,config});
        list(result.checks).forEach(addCase);
        if(!list(result.checks).length)addCase({title:'检查证据不完整',status:'inconclusive',observed:'记录未保存任何逐项检查。不能把空检查列表判为通过。',next_step:'重新运行检测并确认逐项结果和请求证据已保存。'},0);
      }else addCase({status:record.status||result.status,observed:result.error||result.text||result.output,applicable:record.applicable,local_only:record.local_only},0);
    });
    return {cases,requests,originals};
  }
  const ancillary=x=>['observation','control','aggregate'].includes(x.raw.evidence_category);
  const observed=x=>!ancillary(x)&&x.applicable&&!x.localOnly&&['passed','failed','inconclusive'].includes(x.status);
  const eligible=x=>observed(x)&&['passed','failed'].includes(x.status)&&x.raw.score_applicable!==false&&x.raw.metadata?.score_applicable!==false&&x.raw.evidence_category!=='observation';
  const points=x=>x.status==='passed'?1:0;
  const rawBlock=(label,value)=>`<details class="raw"><summary>${esc(label)}</summary><pre>${esc(display(value))}</pre></details>`;
  const stamp=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?'未记录':d.toLocaleString('zh-CN');};
  const field=(label,value)=>`<div><span class="field-label">${esc(label)}</span><p>${esc(display(value))}</p></div>`;
  const REASONS={assertion_passed:'本轮断言通过',assertion_failed:'实测不符合断言',transport_error:'连接或上游传输异常',http_error:'HTTP 错误，核对状态与正文',rate_limited:'限流或额度阻断',unsupported_parameter:'当前请求参数不支持',unsupported_format:'协议不适用',evidence_missing:'返回证据不足',prerequisite_failed:'正向对照尚未成立',authentication_error:'鉴权或权限阻断',auto_no_tool_selected:'自动模式本轮未选择工具',cache_not_observed:'本轮未观察到缓存命中',cache_context_too_small:'实际前缀规模未达到大 Token 目标',cache_scale_not_reached:'实际前缀规模未达到大 Token 目标',cancelled:'用户取消，证据不完整',usage_missing:'返回 Token 计量字段缺失',cap_not_exercised:'本轮未触及输出上限',budget_exhausted:'预算耗尽，未取得充分证据'};
  function parameterFacts(x){
    const raw=x.raw,params=raw.parameters||raw.metadata?.parameters,rows=[];
    const scenario=raw.scenario_id||raw.metadata?.scenario_id;if(scenario)rows.push(['场景',scenario]);
    if(raw.repetition!=null)rows.push(['重复序号',raw.repetition]);
    if(raw.reason_code)rows.push(['结论类型',REASONS[raw.reason_code]||raw.reason_code]);
    if(params&&typeof params==='object')rows.push(...Object.entries(params));else if(params!=null)rows.push(['测试参数',params]);
    return rows.length?`<div class="parameter-facts"><dl class="key-value">${rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(display(v))}</dd>`).join('')}</dl></div>`:'';
  }
  const portableSummaryCss=`.badge.attention{color:#8b661f;background:#fff3d7}.badge.risk{color:#a0483c;background:#faebe6}.badge.neutral{color:#657487;background:#eef2f6}.brief-item.attention{border-top:3px solid #c1994b}.brief-item.risk{border-top:3px solid #bb6453}.brief-item.passed{border-top:3px solid #418062}.brief-item.neutral{border-top:3px solid #aab6c3}.module-card.failed.tone-attention .module-bar i,.score-dimension.failed.tone-attention .bar i{background:#c1994b}.resource-grade{display:flex;flex-wrap:wrap;align-items:center;gap:7px 12px;margin:10px 0 6px;padding:10px 13px;border:1px solid #dce5ed;border-radius:9px;background:#f2f6fa;color:#536d85}.resource-grade .grade-label{font-size:11px;font-weight:500}.resource-grade .grade-status{font-size:19px;line-height:1.5;font-weight:700}.resource-grade small{font-size:10px;color:inherit;opacity:.85}.resource-grade.grade-high{border-color:#cde7d9;background:#eff8f3;color:#287b52}.resource-grade.grade-medium{border-color:#d4e3f1;background:#eff5fc;color:#315f8c}.resource-grade.grade-low{border-color:#eadbbd;background:#fcf6e9;color:#936c2a}.resource-grade.grade-unknown{border-color:#e0e6ec;background:#f3f6f9;color:#687b8b}.executive-verdict .grade-detail{font-size:11px;line-height:1.75;color:#627487;margin:4px 0 0}.grade-criteria{font-size:11px;line-height:1.75;color:#63778a;margin:5px 0 12px}.grade-criteria>summary{color:#456887;cursor:pointer}.grade-criteria>div{padding:7px 10px;border-left:2px solid #d9e4ed;margin-top:6px;background:#f7fafc}.executive-verdict>.grade-criteria+h2{margin-top:14px}.executive-panel,.module-panel{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px}.executive-top{display:flex;justify-content:space-between;gap:28px}.executive-verdict{flex:1;min-width:0}.executive-verdict h2{font-size:25px;line-height:1.5;margin:8px 0}.executive-verdict p,.executive-note,.executive-timing small{color:var(--muted);font-size:12px}.executive-score{flex:0 0 175px;text-align:right}.executive-score strong{font-size:48px;color:var(--green);line-height:1.2}.executive-score small{color:var(--muted);font-size:11px}.executive-timing{display:grid;grid-template-columns:1fr 2fr 1.5fr;gap:16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:14px 0;margin:20px 0}.executive-timing span,.executive-timing small,.executive-timing strong{display:block}.executive-latency{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:16px}.executive-latency b{color:var(--ink)}.executive-timing span{font-size:11px;color:var(--muted)}.executive-timing strong{font-size:22px}.executive-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.brief-item{border:1px solid var(--line);border-radius:10px;padding:14px;background:#f8fafc;min-width:0}.brief-item.failed{border-top:3px solid var(--red)}.brief-item-head{display:flex;justify-content:space-between;gap:8px}.brief-item h3{font-size:14px}.brief-item p{font-size:12px;margin-top:9px;overflow-wrap:anywhere}.brief-links{display:flex;gap:10px;margin-top:10px;font-size:11px}.executive-note{margin-top:16px}.module-panel{margin-top:20px}.module-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:18px}.module-card{border:1px solid var(--line);border-radius:10px;padding:15px}.module-card h3{font-size:15px}.module-weight,.module-desc,.module-meta{font-size:11px;color:var(--muted)}.module-score strong{font-size:28px}.module-score small{font-size:11px;color:var(--muted)}.module-bar{height:5px;background:#e8edf3;border-radius:5px;margin:10px 0;overflow:hidden}.module-bar i{display:block;height:100%;background:var(--green)}.module-card.failed .module-bar i{background:var(--red)}.module-meta{display:flex;justify-content:space-between}.all-results{margin-top:28px}.results-table{width:100%;border-collapse:collapse;background:#fff;font-size:12px}.results-table th,.results-table td{padding:12px;vertical-align:top;text-align:left;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.results-table small,.result-number,.result-name{display:block}.result-number,.all-results-note{font-size:11px;color:var(--muted)}.result-observation+.result-observation{margin-top:10px}.results-scroll{overflow-x:auto}@media(max-width:800px){.executive-grid,.module-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.executive-timing{grid-template-columns:1fr 1fr}}@media(max-width:520px){.executive-top{display:block}.executive-score{text-align:left;margin-top:18px}.executive-grid,.module-grid,.executive-timing{grid-template-columns:1fr}.executive-panel,.module-panel{padding:18px}}`;
  function themeCss(){return (css+portableSummaryCss+(typeof window.WORKBENCH_REPORT_THEME==='string'?'\n'+window.WORKBENCH_REPORT_THEME:'')).replace(/<\/style/gi,'<\\/style');}
  async function render(records, logs) {
    if(window.WORKBENCH_REPORT_THEME_READY)await Promise.resolve(window.WORKBENCH_REPORT_THEME_READY).catch(()=>{});
    records=scrub(list(records));logs=scrub(list(logs));
    const {cases,requests,originals}=normalize(records),total=cases.length,count=s=>cases.filter(x=>x.status===s).length;
    const dims=DIMENSIONS.filter(([key])=>key!=='security'||cases.some(x=>x.dims.includes(key))).map(([key,label])=>{
      const matched=cases.filter(x=>x.dims.includes(key)),scored=matched.filter(eligible),value=scored.length?roundScore(scored.reduce((sum,x)=>sum+points(x),0)/scored.length*100):null;
      const sampled=matched.filter(observed),status=!sampled.length?'not_covered':scored.some(x=>x.status==='failed')?'failed':!scored.length||sampled.some(x=>x.status==='inconclusive')?'inconclusive':'passed';
      const samples=new Set(sampled.flatMap(x=>x.linked.map(r=>r.id))).size,scenarios=new Set(sampled.map(x=>x.raw.scenario_id||x.raw.metadata?.scenario_id||x.sourceId||x.id)).size,parameters=new Set(sampled.map(x=>x.raw.parameters||x.raw.metadata?.parameters).filter(x=>x!=null).map(x=>JSON.stringify(x&&typeof x==='object'?Object.fromEntries(Object.entries(x).filter(([key])=>!['repetition','round','sample_id','request_id','source_request_id','prefix_sha256','prefix_chars','estimate'].includes(key)).sort(([a],[b])=>a.localeCompare(b))):x))).size;
      return {key,label,matched,sampled,scored,value,status,samples,scenarios,parameters,...groupPresentation(scoreGroup(matched))};
    });
    const covered=dims.filter(d=>d.value!==null),modules=MODULES.map(([key,label,weight,description])=>({key,label,weight,description,...scoreGroup(cases.filter(x=>moduleId(x)===key))})).map(m=>({...m,...groupPresentation(m)})),scoredModules=modules.filter(m=>m.value!==null),weightCovered=scoredModules.reduce((sum,m)=>sum+m.weight,0),score=weightCovered?roundScore(scoredModules.reduce((sum,m)=>sum+m.value*m.weight,0)/weightCovered):null;
    const brief=executive(cases,requests,records),grade=resourceGrade(score,brief.resolution,weightCovered,cases);
    const pending=count('inconclusive')+count('cancelled'),skipped=count('skipped')+count('not_covered'),failed=count('failed');
    const verdict=failed?'需要关注':pending?'证据待补齐':!cases.some(eligible)?'尚无可评分证据':skipped?'已执行项通过，部分未覆盖':'本轮已执行检查通过';
    const models=[...new Set(cases.map(c=>c.model))].join('、')||'未命名模型';
    const overviewTable=`<section id="all-results" class="all-results"><div class="section-head"><div><span class="index">RESULTS / COMPLETE MATRIX</span><h2>全项测试结果（${total} 项）</h2></div></div><p class="all-results-note">请求数仅来自明确关联证据；同一请求可支持多项检查，不可逐行相加为总请求量。HTTP 异常是否符合负向测试预期，以检查断言为准。</p><div class="results-scroll"><table class="results-table"><thead><tr><th>测试项</th><th>测试方法</th><th>预期 / 实际结果</th><th>证据次数</th><th>检查状态</th></tr></thead><tbody>${cases.map((x,i)=>`<tr class="row-${x.status}"><td><span class="result-number">${String(i+1).padStart(2,'0')}</span><a class="result-name" href="#${x.id}">${esc(x.title)}</a><small>${esc(x.model)}</small></td><td>${esc(display(x.method))}</td><td><div class="result-observation"><b>预期</b><br>${esc(display(x.expected))}</div><div class="result-observation"><b>实际</b><br>${esc(display(x.observed))}</div></td><td>${x.linked.length?`<span class="result-stat">${x.linked.length} / ${x.linked.filter(r=>r.status==='failed').length}</span><small>请求 / HTTP 异常</small>`:'<span class="result-unlinked">未关联</span>'}</td><td class="result-status">${badge(x.status)}</td></tr>`).join('')}</tbody></table></div></section>`;
    const scoreCards=dims.map(d=>`<article class="score-dimension ${d.status} tone-${d.display_tone}"><div class="score-dimension-head"><span>${esc(d.label)}</span><b>${d.value===null?'—':d.value}<small>${d.value===null?' 无可判定分数':' /100'}</small></b></div><div class="bar"><i style="width:${d.value||0}%"></i></div><div class="score-status">${d.scored.length} 项计分 · 通过 ${d.scored.filter(x=>x.status==='passed').length} · 异常 ${d.scored.filter(x=>x.status==='failed').length}</div><div class="sampling-note"><b>${d.samples<10||d.scenarios<3?'有限样本 · 不代表完整能力':'当前参数矩阵 · 不外推长期能力'}</b><span>场景 ${d.scenarios} · 参数组合 ${d.parameters} · 关联请求 ${d.samples} · 可判定率 ${d.sampled.length?roundScore(d.scored.length/d.sampled.length*100)+'%':'—'}</span></div><p class="score-evidence">${d.matched.length?`无法判定 / 跳过 / 不适用 / 未覆盖 ${d.matched.filter(x=>!eligible(x)).length} 项，不参与分母。`:'本轮未保存该能力的专项断言。'}</p></article>`).join('');
    const checks=await Promise.all(cases.map(async(x,i)=>`<article class="check" id="${x.id}"><div class="check-head"><div><span class="index">${String(i+1).padStart(2,'0')} / CHECK</span><h3>${esc(x.title)}</h3><div class="case-id">${esc(x.model)} · ${esc(x.sourceId||x.id)}</div></div>${badge(x.status)}</div><div class="method">${field('测试方法',x.method)}</div>${parameterFacts(x)}<div class="matrix"><table class="matrix-table"><thead><tr><th>预期行为</th><th>实际结果</th></tr></thead><tbody><tr class="row-${x.status}"><td>${esc(display(x.expected))}</td><td>${esc(display(x.observed))}</td></tr></tbody></table></div><div class="interpretation">${field('原因与结果说明',x.reason)}${field('下一步建议',x.next)}</div><p class="muted">评分归属：${esc(x.dims.map(key=>DIMENSIONS.find(d=>d[0]===key)?.[1]||key).join('、')||'未归类')} · ${eligible(x)?`本项计分 ${points(x)*100} / 100`:'不参与分母'}${x.localOnly?' · 本地辅助断言':''}</p><div class="request-links">${x.linked.length?x.linked.map(r=>`<a href="#${r.id}">查看 ${esc(r.id)} →</a>`).join(' · '):'<span class="muted">未保存明确的逐项请求关联；不按先后顺序猜测。</span>'}</div>${usageHtml(x.r.gpt_evaluation?{gpt_evaluation:x.r.gpt_evaluation}:x.r.raw,x.r.scenarioId==='gpt-html-animation')}${gptAssessmentHtml(x.r)}${x.r.kind!=='general'&&x.r.text?rawBlock('文本输出',x.r.text):''}${x.r.kind!=='general'?await mediaHtml(x.r.media):''}${rawBlock('原始检查与配置（已脱敏）',{check:x.raw,config:x.r.config,response:x.r.kind==='general'?undefined:x.r.raw})}</article>`));
    const findings=cases.filter(x=>['failed','inconclusive','cancelled'].includes(x.status)).map(x=>`<article class="finding">${badge(x.status)}<h3>${esc(x.title)} · ${esc(x.model)}</h3>${field('实际观察',x.observed)}${field('原因与影响',x.reason)}${field('处理建议',x.next)}<p><a href="#${x.id}">查看检查项 →</a></p></article>`).join('')||'<div class="empty">未记录明确异常项。请同时确认各维度覆盖范围，未覆盖不代表能力已通过。</div>';
    const requestHtml=requests.map((r,i)=>`<details class="request" id="${r.id}"><summary><b>${esc(r.model)} · request-${i+1}</b><span class="request-summary">${esc(r.raw.method||'方法未记录')} · HTTP ${esc(r.code??'未记录')} · ${esc(r.raw.duration_ms??r.raw.elapsedMs??'—')} ms</span>${badge(r.status,r.status==='passed'?'HTTP 成功':r.status==='failed'?'HTTP 异常':undefined)}</summary><div class="request-body"><dl class="key-value"><dt>请求地址</dt><dd>${esc(r.url||'未记录')}</dd><dt>所属检测项</dt><dd>${r.caseIds.length?r.caseIds.map(id=>`<a href="#${id}">${esc(cases.find(c=>c.id===id)?.title||id)}</a>`).join('、'):'未明确关联；仍完整保留本请求。'}</dd><dt>请求 ID</dt><dd>${esc(r.raw.id||r.raw.request_id||r.id)}</dd></dl>${rawBlock('完整请求、响应头与响应体（已脱敏）',r.raw)}</div></details>`).join('')||'<div class="empty">本轮没有保存 HTTP 请求明细；没有采集的字段不会补造。</div>';
    const originalHtml=originals.map(o=>`<article class="panel"><h3>${esc(o.model)}</h3><p class="muted">模式：${esc(o.mode||'未记录')} · 原始总分：${o.total==null?'未记录':esc(o.total)+' / 100'}</p>${Object.keys(object(o.scores)).length?`<table class="compact-table"><thead><tr><th>原始维度</th><th>页面原始分值 / 10</th></tr></thead><tbody>${Object.entries(o.scores).map(([key,v])=>`<tr><td>${esc(key)}</td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>`:''}${list(o.batch).length?rawBlock('批量原始结果',o.batch):''}${rawBlock('执行日志',o.logs)}${o.intelligence?rawBlock('补充观察',o.intelligence):''}</article>`).join('');
    const recommendations=dims.filter(d=>d.status!=='passed').map(d=>d.status==='not_covered'?`补充“${d.label}”专项后再评价；当前没有可计分证据。`:`按请求 ID 核对“${d.label}”中的异常或证据不足项，再复测。`);
    const gptHtml=records.map(record=>{const g=record.result?.gpt_evaluation||record.gpt_evaluation;if(!g)return '';return `<article class="panel"><h3>GPT 生成质量与 Token 账本 · ${esc(record.model)}</h3>${usageHtml({gpt_evaluation:g},true)}${field('可观察信号',g.signals)}${field('源码特征',`HTML=${g.html_detected}；SVG=${g.svg_detected}；动画=${g.animation_detected}；鹈鹕=${g.pelican_detected}；自行车=${g.bicycle_detected}`)}<p class="muted">命名与源码特征属于启发式观察，不能单独证明模型降智、身份、蒸馏或真实计费。完整源码仅作为文本保存，不在报告内执行。</p>${rawBlock('完整 HTML / SVG 源码（未执行）',g.source||g.html||g.output)}${rawBlock('GPT 专项原始判读',g)}</article>`;}).join('');
    const timingInfo=brief.timing;
    const moduleHtml=`<section id="modules" class="module-panel"><div class="module-head"><div><span class="index">MODULES / ACCEPTANCE</span><h2>验收模块总览</h2><p class="muted">可评分权重 ${weightCovered}% / 100%；分数 = 通过 ÷ 已判定检查数。未判定、不适用与来源观察不计分。</p></div></div><div class="module-grid">${modules.map(m=>`<article class="module-card ${m.status} tone-${m.display_tone}"><div class="module-weight">权重 ${m.weight}%</div><h3>${esc(m.label)}</h3>${badge(m.status,m.display_label,m.display_tone)}<div class="module-score"><strong>${m.value===null?'—':m.value}</strong><small>/100</small></div><div class="module-bar"><i style="width:${m.value||0}%"></i></div><p class="module-desc">${esc(m.description)}</p><div class="module-meta"><span>${m.scored.length} 项计分</span><span>通过 ${m.passed} · 异常 ${m.failed}</span></div><p class="module-desc">证据可判定率 ${m.sampled.length?roundScore(m.scored.length/m.sampled.length*100)+'%':'—'} · 无法判定 ${m.sampled.length-m.scored.length} 项</p></article>`).join('')}</div></section>`;
    const gradeNote=grade.batch?'多模型汇总评级仅供参考，不能代表单个模型；请按对应模型独立判读。':score===null?'尚无可评分证据，暂不评级。':`评级仅反映本轮已测渠道表现，不代表模型全部能力或官方身份。${grade.provisional?'证据待完善，当前评级为暂定。':''}`;
    const gradeCriteria=`90–100 分为优质资源，70–89 分为中等资源，低于 70 分为低等级资源。${grade.reasons.length?'暂定评级依据：'+grade.reasons.join('；')+'。':'当前已满足本报告的评级证据门槛。'}${grade.batch?'多模型汇总评级仅供参考，不能代表单个模型。':''}`;
    const gradeHtml=`<div class="resource-grade grade-${grade.level}"><span class="grade-label">本轮资源评级</span><b class="grade-status">${grade.label}</b>${grade.provisional?'<small>暂定 · 证据待完善</small>':''}</div><p class="grade-note grade-detail">${esc(gradeNote)}</p><details class="grade-criteria"><summary>查看评级标准与适用范围</summary><div>${esc(gradeCriteria)}</div></details>`;
    const executiveHtml=`<div class="executive-panel"><div class="executive-top"><div class="executive-verdict"><span class="index">VERDICT / 本轮结论</span>${gradeHtml}<h2>${esc(brief.headline)}</h2><p>${esc(brief.detail)}</p></div><div class="executive-score"><span>综合验收分</span><div><strong>${score===null?'—':score}</strong><small> / 100</small></div><p>证据可判定率 <b>${brief.resolution===null?'未记录':brief.resolution+'%'}</b></p><small>按验收模块权重汇总</small></div></div><div class="executive-timing"><div><span>${timingInfo.source==='完整测试时间'?'测试总耗时':timingInfo.start===null?'测试耗时':'请求观测时段'}</span><strong>${esc(timingInfo.label)}</strong><small>${esc(timingInfo.source)}</small></div><div><span>测试时间</span><p>${timingInfo.start===null?'未记录':esc(stamp(timingInfo.start))+' → '+esc(stamp(timingInfo.end))}</p><small>实际经过时间；并发请求耗时不累加。</small></div><div class="executive-latency"><span>单请求 P50 <b>${esc(durationLabel(timingInfo.p50))}</b></span><span>单请求 P95 <b>${esc(durationLabel(timingInfo.p95))}</b></span><span>已保存耗时 <b>${timingInfo.request_count}/${requests.length} 个请求</b></span></div></div><div class="executive-grid">${brief.items.map(item=>`<article class="brief-item ${item.display_tone}" data-status="${item.status}"><div class="brief-item-head"><h3>${esc(item.label)}</h3>${badge(item.status,item.display_label,item.display_tone)}</div><p>${esc(item.text)}</p>${item.check_ids.length?`<div class="brief-links">${[...new Set(item.check_ids)].slice(0,3).map((id,i)=>`<a href="#${id}">证据 ${i+1} ↗</a>`).join('')}</div>`:''}</article>`).join('')}</div><p class="executive-note">分数反映本轮已判定检查的通过表现；证据覆盖和异常项需同时看。缓存命中率单独按实际 Token 统计，不等于缓存模块得分。</p></div>`;
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>测试报告-${esc(models)}</title><style>${themeCss()}</style></head><body><main>
<header class="masthead"><div class="brand">小小宇宙无敌</div><div class="eyebrow">MODEL ACCEPTANCE REPORT</div></header>
<section class="cover"><div class="cover-top"><div><div class="eyebrow">渠道完整验收 · 可复核证据</div><h1>模型渠道测试报告</h1><p>${esc(models)}</p></div><span class="run-id">${esc(stamp(Date.now()))}</span></div><div class="cover-meta">记录 ${records.length} 组 · 检查 ${total} 项 · 请求 ${requests.length} 次 · 凭据已隐藏</div></section>
<nav class="nav"><a href="#overview">结论总览</a><a href="#modules">验收模块</a><a href="#score">能力评分</a><a href="#scope">测试范围</a><a href="#findings">问题与建议</a><a href="#checks">逐项验收</a><a href="#requests">请求证据</a><a href="#all-results">全项结果</a></nav>
<section id="overview" class="overview">${executiveHtml}<p class="legend">${esc(verdict)} · HTTP 异常按明确关联的请求计算；预期错误是否通过，以检查断言为准。</p></section>
${moduleHtml}
<section id="score" class="score-panel"><div class="score-head"><div><span class="index">SCORE / DIMENSIONS</span><h2>能力评分与覆盖明细</h2></div></div><p class="muted">每个维度单独展示已判定检查通过率。顶部综合验收分按模块权重汇总；维度不重复计算另一个总分。无法判定不赠分，也不作为能力失败；无可判定项显示 —。跳过、不适用、未覆盖、取消、来源观察及本地辅助项不参与分母。覆盖 ${dims.filter(d=>d.sampled.length).length} / ${dims.length} 维；可评分 ${covered.length} 维。</p><div class="score-grid">${scoreCards}</div><ul class="recommendations">${(recommendations.length?recommendations:['已覆盖维度的当前样本通过；可扩大样本后复测。']).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></section>
<section id="scope" class="section"><div class="section-head"><div><span class="index">01 / SCOPE</span><h2>这次测了什么</h2></div></div><div class="grid-two"><div class="panel"><dl class="key-value"><dt>测试模型</dt><dd>${esc(models)}</dd><dt>记录类型</dt><dd>${esc([...new Set(records.map(r=>kindName[r.kind]||r.kind))].join('、'))}</dd><dt>报告模式</dt><dd>统一完整验收模板 · 便携导出</dd><dt>检查 / 请求</dt><dd>${total} 项 / ${requests.length} 次</dd></dl>${rawBlock('各记录请求配置',records.map(r=>({...object(r.config),...object(r.result?.config)})))}</div><div class="panel"><ul class="scope-list"><li>协议、工具、多模态、长度控制、缓存和稳定性分别按已保存断言评价。</li><li>不适用、未执行和证据不足分别保留，不把一次基础成功响应扩展成全部能力通过。</li><li>多模型每项结果与请求单独标注；未保存的精确关联不会根据顺序猜测。</li><li>本报告仅使用已有记录，不重跑模型、不下载远程媒体。远程媒体链接可能失效。</li></ul></div></div></section>
<section id="findings" class="section"><div class="section-head"><div><span class="index">02 / FINDINGS</span><h2>问题、原因与处理建议</h2></div></div><div class="findings">${findings}</div></section>
<section id="checks" class="section"><div class="section-head"><div><span class="index">03 / CHECKS</span><h2>逐项验收说明</h2></div></div>${checks.join('')}${gptHtml}</section>
<section id="requests" class="section"><div class="section-head"><div><span class="index">04 / EVIDENCE</span><h2>完整请求明细与证据</h2></div></div>${requestHtml}</section>
${originalHtml?`<section id="original-results" class="section"><details class="panel"><summary>原始评分、批量结果与执行日志</summary><p class="muted">仅保留页面历史原分供溯源；顶部综合验收分使用当前统一口径。</p>${originalHtml}</details></section>`:''}
<section class="section"><div class="panel"><span class="index">05 / READING NOTES</span><h2>判读边界</h2><ul class="scope-list"><li>接口返回成功不代表内容质量、真实模型身份、蒸馏情况或计费正确。</li><li>缓存比例只统计明确暖请求的完整输入计量；冷请求与变更整段前缀不计为缓存复用样本。</li><li>报告保留脱敏证据；所有文本和模型输出仅以文本展示，不执行其中的指令或代码。</li></ul>${logs.length?rawBlock('本次导出日志',logs):''}</div></section>
${overviewTable}
<footer><span>小小宇宙无敌 · 独立 HTML · 无外部依赖 · 凭据已隐藏</span><span>统一完整验收报告</span></footer></main></body></html>`;
  }
  async function renderExport(records,logs=[]) {
    const localMedia=async items=>Promise.all(list(items).map(async item=>{
      if(!/^blob:|^filesystem:/i.test(String(item?.url||'')))return item;
      try{
        const response=await fetch(item.url),blob=await response.blob();
        const url=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
        return {...item,url};
      }catch(_){return {...item,url:'',export_note:'已接收媒体暂无法读取，请从结果页下载原文件。'};}
    }));
    const prepared=scrub(await Promise.all(list(records).map(async record=>{
      const result=Object.keys(object(record.result)).length?record.result:{...record};
      return {...record,media:await localMedia(record.media||result.media),result:{...result,requests:list(result.requests||record.requests)}};
    })));
    const hosted=(()=>{try{return /^https?:$/i.test(new URL(window.document?.baseURI||window.location?.href||window.location?.protocol||'file:').protocol);}catch(_){return /^https?:$/i.test(window.location?.protocol||'');}})();
    if(hosted){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
      try{
        const session=await fetch('/api/session',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
        if(!session.ok)throw Error('报告会话不可用');
        const data=await session.json();if(!data.token)throw Error('报告会话凭据缺失');
        const response=await fetch('/api/reports',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-Workbench-Token':data.token},body:JSON.stringify({records:prepared,logs:scrub(logs),exported_at:Date.now()})});
        if(!response.ok)throw Error('统一报告服务不可用');
        const html=await response.text();
        if(!/<!doctype\s+html|<html\b/i.test(html)||!/text\/html/i.test(response.headers.get('content-type')||''))throw Error('统一报告响应格式异常');
        return html;
      }catch(_){/* Existing observations remain exportable during offline or session failures. */}
      finally{clearTimeout(timer);}
    }
    return render(prepared,logs);
  }
  window.WorkbenchReport = { render, renderExport };
})();
