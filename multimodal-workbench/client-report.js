'use strict';
/* Portable counterpart of integrations/report_renderer.py.  It intentionally
 * uses the same section names and status vocabulary as the server renderer so
 * basic, general, CCMax and KVV exports have one visual language. */
(function () {
  const esc = v => String(v == null ? '—' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const STATUS = {success:'通过',passed:'通过',error:'未通过',failed:'未通过',pending:'无法判定',unrecognized:'无法判定',inconclusive:'无法判定',stopped:'已取消',cancelled:'已取消',demo:'已跳过',skipped:'已跳过',not_covered:'未覆盖'};
  const cls = s => ({success:'passed',passed:'passed',error:'failed',failed:'failed',pending:'inconclusive',unrecognized:'inconclusive',inconclusive:'inconclusive',stopped:'cancelled',cancelled:'cancelled',demo:'skipped',skipped:'skipped',not_covered:'not_covered'}[s] || 'inconclusive');
  const badge = s => `<span class="badge ${cls(s)}">${esc(STATUS[s] || s || '未记录')}</span>`;
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
  const extractUsage = raw => {
    const seen=new WeakSet(); let found=null;
    const num=v=>{if(v==null||v==='')return null;const n=Number(v);return Number.isFinite(n)&&n>=0?n:null;};
    const walk=v=>{
      if(!v||typeof v!=='object'||seen.has(v)||found)return; seen.add(v);
      if(Array.isArray(v)){v.forEach(walk);return;}
      for(const [k,val] of Object.entries(v)){
        if((k==='usage'||k==='token_usage'||k==='tokenUsage')&&val&&typeof val==='object'){
          const input=num(val.prompt_tokens??val.promptTokens??val.input_tokens??val.inputTokens??val.input);
          const output=num(val.completion_tokens??val.completionTokens??val.output_tokens??val.outputTokens??val.output);
          const total=num(val.total_tokens??val.totalTokens??val.total);
          const cached=num(val.prompt_tokens_details?.cached_tokens??val.input_tokens_details?.cached_tokens??val.cached_tokens??val.cachedTokens);
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
        const reason=check.skip_reason||check.reason||check.meaning||check.judge||description.meaning||(status==='passed'?'本轮保存的断言通过；结论限定于当前样本。':status==='skipped'?'此项已跳过或不适用于本次协议，不计入评分。':status==='not_covered'?'本轮未执行该项，不评价模型是否支持。':'保存的证据不足或断言未通过；应依据 HTTP 状态、错误正文和用例预期核对，不能仅据模型名称归因。');
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
  function themeCss(){return (css+(typeof window.WORKBENCH_REPORT_THEME==='string'?'\n'+window.WORKBENCH_REPORT_THEME:'')).replace(/<\/style/gi,'<\\/style');}
  async function render(records, logs) {
    if(window.WORKBENCH_REPORT_THEME_READY)await Promise.resolve(window.WORKBENCH_REPORT_THEME_READY).catch(()=>{});
    records=scrub(list(records));logs=scrub(list(logs));
    const {cases,requests,originals}=normalize(records),total=cases.length,count=s=>cases.filter(x=>x.status===s).length;
    const dims=DIMENSIONS.filter(([key])=>key!=='security'||cases.some(x=>x.dims.includes(key))).map(([key,label])=>{
      const matched=cases.filter(x=>x.dims.includes(key)),scored=matched.filter(eligible),value=scored.length?Math.round(scored.reduce((sum,x)=>sum+points(x),0)/scored.length*100):null;
      const sampled=matched.filter(observed),status=!sampled.length?'not_covered':scored.some(x=>x.status==='failed')?'failed':!scored.length||sampled.some(x=>x.status==='inconclusive')?'inconclusive':'passed';
      const samples=new Set(sampled.flatMap(x=>x.linked.map(r=>r.id))).size,scenarios=new Set(sampled.map(x=>x.raw.scenario_id||x.raw.metadata?.scenario_id||x.sourceId||x.id)).size,parameters=new Set(sampled.map(x=>x.raw.parameters||x.raw.metadata?.parameters).filter(x=>x!=null).map(x=>JSON.stringify(x&&typeof x==='object'?Object.fromEntries(Object.entries(x).filter(([key])=>!['repetition','round','sample_id','request_id','source_request_id','prefix_sha256','prefix_chars','estimate'].includes(key)).sort(([a],[b])=>a.localeCompare(b))):x))).size;
      return {key,label,matched,sampled,scored,value,status,samples,scenarios,parameters};
    });
    const covered=dims.filter(d=>d.value!==null),score=covered.length?Math.round(covered.reduce((sum,d)=>sum+d.value,0)/covered.length):null;
    const pending=count('inconclusive')+count('cancelled'),skipped=count('skipped')+count('not_covered'),failed=count('failed');
    const verdict=failed?'需要关注':pending?'证据待补齐':!cases.some(eligible)?'尚无可评分证据':skipped?'已执行项通过，部分未覆盖':'本轮已执行检查通过';
    const models=[...new Set(cases.map(c=>c.model))].join('、')||'未命名模型';
    const overviewTable=`<section id="all-results" class="all-results"><div class="section-head"><div><span class="index">RESULTS / COMPLETE MATRIX</span><h2>全项测试结果（${total} 项）</h2></div></div><p class="all-results-note">请求数仅来自明确关联证据；同一请求可支持多项检查，不可逐行相加为总请求量。HTTP 异常是否符合负向测试预期，以检查断言为准。</p><div class="results-scroll"><table class="results-table"><thead><tr><th>测试项</th><th>测试方法</th><th>预期 / 实际结果</th><th>证据次数</th><th>检查状态</th></tr></thead><tbody>${cases.map((x,i)=>`<tr class="row-${x.status}"><td><span class="result-number">${String(i+1).padStart(2,'0')}</span><a class="result-name" href="#${x.id}">${esc(x.title)}</a><small>${esc(x.model)}</small></td><td>${esc(display(x.method))}</td><td><div class="result-observation"><b>预期</b><br>${esc(display(x.expected))}</div><div class="result-observation"><b>实际</b><br>${esc(display(x.observed))}</div></td><td>${x.linked.length?`<span class="result-stat">${x.linked.length} / ${x.linked.filter(r=>r.status==='failed').length}</span><small>请求 / HTTP 异常</small>`:'<span class="result-unlinked">未关联</span>'}</td><td class="result-status">${badge(x.status)}</td></tr>`).join('')}</tbody></table></div></section>`;
    const scoreCards=dims.map(d=>`<article class="score-dimension ${d.status}"><div class="score-dimension-head"><span>${esc(d.label)}</span><b>${d.value===null?'—':d.value}<small>${d.value===null?' 无可判定分数':' /100'}</small></b></div><div class="bar"><i style="width:${d.value||0}%"></i></div><div class="score-status">${d.scored.length} 项计分 · 通过 ${d.scored.filter(x=>x.status==='passed').length} · 失败 ${d.scored.filter(x=>x.status==='failed').length}</div><div class="sampling-note"><b>${d.samples<10||d.scenarios<3?'有限样本 · 不代表完整能力':'当前参数矩阵 · 不外推长期能力'}</b><span>场景 ${d.scenarios} · 参数组合 ${d.parameters} · 关联请求 ${d.samples} · 可判定率 ${d.sampled.length?Math.round(d.scored.length/d.sampled.length*100)+'%':'—'}</span></div><p class="score-evidence">${d.matched.length?`无法判定 / 跳过 / 不适用 / 未覆盖 ${d.matched.filter(x=>!eligible(x)).length} 项，不参与分母。`:'本轮未保存该能力的专项断言。'}</p></article>`).join('');
    const checks=await Promise.all(cases.map(async(x,i)=>`<article class="check" id="${x.id}"><div class="check-head"><div><span class="index">${String(i+1).padStart(2,'0')} / CHECK</span><h3>${esc(x.title)}</h3><div class="case-id">${esc(x.model)} · ${esc(x.sourceId||x.id)}</div></div>${badge(x.status)}</div><div class="method">${field('测试方法',x.method)}</div>${parameterFacts(x)}<div class="matrix"><table class="matrix-table"><thead><tr><th>预期行为</th><th>实际结果</th></tr></thead><tbody><tr class="row-${x.status}"><td>${esc(display(x.expected))}</td><td>${esc(display(x.observed))}</td></tr></tbody></table></div><div class="interpretation">${field('原因与结果说明',x.reason)}${field('下一步建议',x.next)}</div><p class="muted">评分归属：${esc(x.dims.map(key=>DIMENSIONS.find(d=>d[0]===key)?.[1]||key).join('、')||'未归类')} · ${eligible(x)?`本项计分 ${points(x)*100} / 100`:'不参与分母'}${x.localOnly?' · 本地辅助断言':''}</p><div class="request-links">${x.linked.length?x.linked.map(r=>`<a href="#${r.id}">查看 ${esc(r.id)} →</a>`).join(' · '):'<span class="muted">未保存明确的逐项请求关联；不按先后顺序猜测。</span>'}</div>${usageHtml(x.r.gpt_evaluation?{gpt_evaluation:x.r.gpt_evaluation}:x.r.raw,x.r.scenarioId==='gpt-html-animation')}${gptAssessmentHtml(x.r)}${x.r.kind!=='general'&&x.r.text?rawBlock('文本输出',x.r.text):''}${x.r.kind!=='general'?await mediaHtml(x.r.media):''}${rawBlock('原始检查与配置（已脱敏）',{check:x.raw,config:x.r.config,response:x.r.kind==='general'?undefined:x.r.raw})}</article>`));
    const findings=cases.filter(x=>['failed','inconclusive','cancelled'].includes(x.status)).map(x=>`<article class="finding">${badge(x.status)}<h3>${esc(x.title)} · ${esc(x.model)}</h3>${field('实际观察',x.observed)}${field('原因与影响',x.reason)}${field('处理建议',x.next)}<p><a href="#${x.id}">查看检查项 →</a></p></article>`).join('')||'<div class="empty">未记录明确失败项。请同时确认各维度覆盖范围，未覆盖不代表能力已通过。</div>';
    const requestHtml=requests.map((r,i)=>`<details class="request" id="${r.id}"><summary><b>${esc(r.model)} · request-${i+1}</b><span class="request-summary">${esc(r.raw.method||'方法未记录')} · HTTP ${esc(r.code??'未记录')} · ${esc(r.raw.duration_ms??r.raw.elapsedMs??'—')} ms</span>${badge(r.status)}</summary><div class="request-body"><dl class="key-value"><dt>请求地址</dt><dd>${esc(r.url||'未记录')}</dd><dt>所属检测项</dt><dd>${r.caseIds.length?r.caseIds.map(id=>`<a href="#${id}">${esc(cases.find(c=>c.id===id)?.title||id)}</a>`).join('、'):'未明确关联；仍完整保留本请求。'}</dd><dt>请求 ID</dt><dd>${esc(r.raw.id||r.raw.request_id||r.id)}</dd></dl>${rawBlock('完整请求、响应头与响应体（已脱敏）',r.raw)}</div></details>`).join('')||'<div class="empty">本轮没有保存 HTTP 请求明细；没有采集的字段不会补造。</div>';
    const originalHtml=originals.map(o=>`<article class="panel"><h3>${esc(o.model)}</h3><p class="muted">模式：${esc(o.mode||'未记录')} · 原始总分：${o.total==null?'未记录':esc(o.total)+' / 100'}</p>${Object.keys(object(o.scores)).length?`<table class="compact-table"><thead><tr><th>原始维度</th><th>页面原始分值 / 10</th></tr></thead><tbody>${Object.entries(o.scores).map(([key,v])=>`<tr><td>${esc(key)}</td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>`:''}${list(o.batch).length?rawBlock('批量原始结果',o.batch):''}${rawBlock('执行日志',o.logs)}${o.intelligence?rawBlock('补充观察',o.intelligence):''}</article>`).join('');
    const recommendations=dims.filter(d=>d.status!=='passed').map(d=>d.status==='not_covered'?`补充“${d.label}”专项后再评价；当前没有可计分证据。`:`按请求 ID 核对“${d.label}”中的失败或证据不足项，再复测。`);
    const gptHtml=records.map(record=>{const g=record.result?.gpt_evaluation||record.gpt_evaluation;if(!g)return '';return `<article class="panel"><h3>GPT 生成质量与 Token 账本 · ${esc(record.model)}</h3>${usageHtml({gpt_evaluation:g},true)}${field('可观察信号',g.signals)}${field('源码特征',`HTML=${g.html_detected}；SVG=${g.svg_detected}；动画=${g.animation_detected}；鹈鹕=${g.pelican_detected}；自行车=${g.bicycle_detected}`)}<p class="muted">命名与源码特征属于启发式观察，不能单独证明模型降智、身份、蒸馏或真实计费。完整源码仅作为文本保存，不在报告内执行。</p>${rawBlock('完整 HTML / SVG 源码（未执行）',g.source||g.html||g.output)}${rawBlock('GPT 专项原始判读',g)}</article>`;}).join('');
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>测试报告-${esc(models)}</title><style>${themeCss()}</style></head><body><main><header class="masthead"><div class="brand">小小宇宙无敌</div><div class="eyebrow">MODEL ACCEPTANCE REPORT</div></header><section class="cover"><div class="cover-top"><div><div class="eyebrow">渠道完整验收 · 可复核证据</div><h1>模型渠道测试报告</h1><p>${esc(models)}</p></div><span class="run-id">${esc(stamp(Date.now()))}</span></div><div class="cover-meta">记录 ${records.length} 组 · 检查 ${total} 项 · 请求 ${requests.length} 次 · 凭据已隐藏</div></section><nav class="nav"><a href="#overview">结论总览</a><a href="#score">评分明细</a><a href="#scope">测试范围</a><a href="#findings">问题与建议</a><a href="#checks">逐项验收</a><a href="#requests">请求证据</a>${originals.length?'<a href="#original-results">原始评分</a>':''}</nav><section id="overview" class="overview"><div class="verdict-line"><div><h2>${esc(verdict)}</h2><p>仅评价本轮实际保存的检查与请求；模型能力结论以明确断言和证据为准。</p></div></div><div class="metrics"><div class="metric"><span>统一证据评分</span><b>${score===null?'—':score}<small>${score===null?'尚未覆盖':'/ 100'}</small></b></div><div class="metric"><span>实际请求</span><b>${requests.length}</b><small>不以检查数代替请求数</small></div><div class="metric"><span>通过 / 检查数</span><b>${count('passed')} / ${total}</b><small>跳过 / 未覆盖 ${skipped} 项</small></div><div class="metric"><span>失败 / 待核对</span><b>${failed} / ${pending}</b><small>详见逐项证据</small></div></div>${overviewTable}<p class="legend">HTTP 异常数只按明确关联的请求计算；预期错误响应是否通过，以检查断言为准。未关联不会冒充 0 次请求。</p></section><section id="score" class="score-panel"><div class="score-head"><div><span class="index">SCORE / DIMENSIONS</span><h2>能力评分与覆盖明细</h2></div><div class="score-total">${score===null?'—':score}<small> / 100</small></div></div><p class="muted">各可评分维度等权汇总；通过率 = 通过 ÷（通过 + 失败）。无法判定不赠分，也不作为能力失败；无可判定项显示 —。跳过、不适用、未覆盖、取消、来源观察及本地辅助项不参与分母。覆盖 ${dims.filter(d=>d.sampled.length).length} / ${dims.length} 维；可评分 ${covered.length} 维。少量样本通过不代表完整能力 100%。</p><div class="score-grid">${scoreCards}</div><ul class="recommendations">${(recommendations.length?recommendations:['已覆盖维度的当前样本通过；可扩大样本后复测。']).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></section><section id="scope" class="section"><div class="section-head"><div><span class="index">01 / SCOPE</span><h2>这次测了什么</h2></div></div><div class="grid-two"><div class="panel"><dl class="key-value"><dt>测试模型</dt><dd>${esc(models)}</dd><dt>记录类型</dt><dd>${esc([...new Set(records.map(r=>kindName[r.kind]||r.kind))].join('、'))}</dd><dt>报告模式</dt><dd>统一完整验收模板 · 便携导出</dd><dt>检查 / 请求</dt><dd>${total} 项 / ${requests.length} 次</dd></dl>${rawBlock('各记录请求配置',records.map(r=>({...object(r.config),...object(r.result?.config)})))}</div><div class="panel"><ul class="scope-list"><li>协议、工具、多模态、长度控制、缓存和稳定性分别按已保存断言评价。</li><li>不适用、未执行和证据不足分别保留，不把一次基础成功响应扩展成全部能力通过。</li><li>多模型每项结果与请求单独标注；未保存的精确关联不会根据顺序猜测。</li><li>本报告仅使用已有记录，不重跑模型、不下载远程媒体。远程媒体链接可能失效。</li></ul></div></div></section><section id="findings" class="section"><div class="section-head"><div><span class="index">02 / FINDINGS</span><h2>问题、原因与处理建议</h2></div></div><div class="findings">${findings}</div></section><section id="checks" class="section"><div class="section-head"><div><span class="index">03 / CHECKS</span><h2>逐项验收说明</h2></div></div>${checks.join('')}${gptHtml}</section><section id="requests" class="section"><div class="section-head"><div><span class="index">04 / EVIDENCE</span><h2>完整请求明细与证据</h2></div></div>${requestHtml}</section>${originalHtml?`<section id="original-results" class="section"><h2>原始评分、批量结果与执行日志</h2><p class="muted">保留测试页面采集的原分，不用统一证据评分覆盖原始结论。</p>${originalHtml}</section>`:''}<section class="section"><div class="panel"><span class="index">05 / READING NOTES</span><h2>判读边界</h2><ul class="scope-list"><li>接口返回成功不代表内容质量、真实模型身份、蒸馏情况或计费正确。</li><li>缓存 token 与输入、输出口径依提供方协议解释；缺少字段时只能记为无法判定。</li><li>报告保留脱敏证据；所有文本和模型输出仅以文本展示，不执行其中的指令或代码。</li></ul>${logs.length?rawBlock('本次导出日志',logs):''}</div></section><footer><span>小小宇宙无敌 · 独立 HTML · 无外部依赖 · 凭据已隐藏</span><span>统一完整验收报告</span></footer></main></body></html>`;
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
