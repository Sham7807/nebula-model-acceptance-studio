/* One model-discovery path for every workbench panel. */
(function(root){
  'use strict';
  const AUTH = new Set(['bearer','anthropic','gemini','none']);
  function hosted(){
    const protocol=String(root.location?.protocol||'');
    if(protocol==='http:'||protocol==='https:')return true;
    const base=String(root.document?.baseURI||'');
    if(/^https?:/i.test(base))return true;
    try{return !!(root.parent&&root.parent!==root&&/^https?:$/.test(String(root.parent.location?.protocol||'')));}catch{return false;}
  }
  // Keep service paths relative. In a same-origin srcdoc the browser resolves
  // `/api/*` against the host workbench while preserving the inherited origin.
  function serviceUrl(path){return path;}
  function error(message,status){const value=new Error(message);if(status)value.status=status;return value;}
  function sanitized(message,key){let text=String(message||'未知错误');if(key){text=text.split(key).join('[已隐藏]');text=text.split(encodeURIComponent(key)).join('[已隐藏]');}return text.replace(/(Bearer\s+)[^\s"<>]+/gi,'$1[已隐藏]').replace(/([?&](?:key|api_key|token|access_token)=)[^&#\s"<>]*/gi,'$1[已隐藏]');}
  function safeDetails(value,key){
    if(typeof value==='string')return sanitized(value,key);
    if(Array.isArray(value))return value.map(v=>safeDetails(v,key));
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,/^(?:key|api[_-]?key|authorization|x-api-key|x-goog-api-key|password|cookie|token)$/i.test(k)?'[已隐藏]':safeDetails(v,key)]));
    return value;
  }
  function configFor(input){
    const base=String(input?.base||'').trim(),key=String(input?.key||'').trim(),auth=input?.auth||'bearer';
    if(!AUTH.has(auth))throw error('模型列表鉴权方式无效。');
    if((auth!=='none'&&!key)||/[\r\n]/.test(key))throw error('请填写有效的 API Key，或选择不使用鉴权。');
    let url;try{url=new URL(base);}catch{throw error('渠道地址必须是完整的 http:// 或 https:// URL。');}
    if(!/^https?:$/.test(url.protocol)||!url.hostname||url.username||url.password||url.search||url.hash||/[\u0000-\u0020\\]/.test(base))throw error('渠道地址仅支持 HTTP(S)，不能含账号、查询参数、片段或空白字符。');
    let path=url.pathname.replace(/\/+$/,'').replace(/\/(?:chat\/completions|completions|responses|messages|images\/(?:generations|edits)|audio\/(?:speech|transcriptions|translations))$/,'');
    if(!/\/models$/.test(path)){
      if(!/\/v\d+(?:beta\d*)?(?:\/openai)?$/i.test(path))path+=auth==='gemini'?'/v1beta':'/v1';
      path+='/models';
    }
    url.pathname=path;
    return {base,key,auth,url:url.href};
  }
  function catalog(data,auth){
    if(data&&typeof data==='object'&&!Array.isArray(data)&&data.error!=null)throw error('模型列表接口返回错误，请检查渠道配置与权限。');
    const rows=Array.isArray(data)?data:Array.isArray(data?.data)?data.data:Array.isArray(data?.models)?data.models:null;
    if(rows===null)throw error('模型列表响应格式无法识别：应为数组，或包含 data / models 数组的对象。');
    const ids=[];
    for(const row of rows){
      const options=typeof row==='string'?[row]:row&&typeof row==='object'?[row.id,row.name]:[];
      let id=options.find(value=>typeof value==='string'&&value.trim());
      if(!id)continue;id=id.trim();if(auth==='gemini')id=id.replace(/^models\//,'');
      if(id&&!ids.includes(id))ids.push(id);
    }
    if(rows.length&&!ids.length)throw error('模型列表没有可识别的模型 ID；仍可手动填写。');
    return ids;
  }
  async function jsonResponse(response,key,label,service=true){
    let body;try{body=await response.json();}catch{}
    if(!response.ok){
      const detail=body?.error?.message||body?.error||body?.message;
      const fallback=service?(response.status===401?'登录已失效，请重新登录后重试。':response.status===403?'会话已过期，请刷新工作台后重试。':response.status===404?'当前地址未提供工作台服务，请从部署的工作台网址打开。':label+'失败'):'渠道接口拒绝请求，请检查 API Key、接口地址和模型列表权限。';
      const value=error((body?.diagnostics?'':'HTTP '+response.status+'：')+sanitized(typeof detail==='string'?detail:fallback,key),response.status);
      value.diagnostics=safeDetails(body?.diagnostics,key);value.code=body?.code;value.advice=sanitized(body?.advice||body?.diagnostics?.suggestion||'',key);value.retryable=!!body?.retryable;
      value.sessionExpired=service&&response.status===401;
      throw value;
    }
    if(body===undefined)throw error(label+'没有返回有效 JSON，请检查工作台或渠道地址。',response.status);
    return body;
  }
  async function list(input,options={}){
    const c=configFor(input),controller=new AbortController();
    const timeout=Math.min(30,Math.max(1,Number(options.timeout)||30));let timedOut=false;
    const abort=()=>controller.abort(options.signal?.reason);
    if(options.signal?.aborted)abort();else options.signal?.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeout*1000);
    try{
      if(controller.signal.aborted)throw Object.assign(error('已取消获取模型列表。'),{name:'AbortError'});
      let data,transport;
      if(hosted()){
        for(let attempt=0;attempt<2;attempt++){
          const session=await jsonResponse(await root.fetch(serviceUrl('/api/session'),{cache:'no-store',credentials:'same-origin',signal:controller.signal,redirect:'error'}),c.key,'工作台会话');
          if(typeof session?.token!=='string'||!session.token)throw error('工作台会话不可用，请刷新页面或重新登录后再获取模型列表。');
          try{data=await jsonResponse(await root.fetch(serviceUrl('/api/models'),{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':session.token},body:JSON.stringify({base:c.base,key:c.key,auth:c.auth}),cache:'no-store',credentials:'same-origin',signal:controller.signal,redirect:'error'}),c.key,'获取模型列表');break;}
          catch(e){if(attempt===0&&e.status===403&&!e.diagnostics&&/会话已过期/.test(e.message))continue;throw e;}
        }
        transport='service';
      }else{
        const headers={};
        if(c.auth==='bearer')headers.Authorization='Bearer '+c.key;
        if(c.auth==='anthropic'){headers['x-api-key']=c.key;headers['anthropic-version']='2023-06-01';headers['anthropic-dangerous-direct-browser-access']='true';}
        if(c.auth==='gemini')headers['x-goog-api-key']=c.key;
        data=await jsonResponse(await root.fetch(c.url,{headers,cache:'no-store',credentials:'omit',signal:controller.signal,redirect:'error'}),c.key,'模型列表接口',false);
        transport='direct';
      }
      if(controller.signal.aborted)throw error('已取消获取模型列表。');
      const models=catalog(data,c.auth);return {models,total:models.length,transport,diagnostics:safeDetails(data?.diagnostics,c.key)};
    }catch(reason){
      if(controller.signal.aborted){const value=error(timedOut?'获取模型列表超时，请检查渠道连通性后重试。':'已取消获取模型列表。');value.name=timedOut?'TimeoutError':'AbortError';throw value;}
      if(reason?.name==='TypeError')throw error(hosted()?'无法连接工作台服务，请检查服务器状态和登录会话后重试。':'浏览器无法连接渠道。请从部署的工作台网址打开以通过服务获取列表，或检查渠道 CORS / TLS / 网络设置。');
      reason.message=sanitized(reason.message,c.key);throw reason;
    }finally{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
  }
  function showDetails(anchor,value){
    if(!anchor?.ownerDocument)return;
    const doc=anchor.ownerDocument,id=anchor.id+'DiscoveryDetails';doc.getElementById(id)?.remove();
    if(!value)return;
    const d=value.diagnostics;if(!d&&!value.sessionExpired)return;
    const make=(tag,text)=>{const element=doc.createElement(tag);element.textContent=text;return element;};
    const panel=doc.createElement('details');panel.id=id;panel.className='discovery-details';panel.open=value instanceof Error||!!value.message;panel.dataset.state=panel.open?'error':'success';
    panel.append(make('summary',value.sessionExpired?'登录状态需要更新':panel.open?'查看连接诊断与处理建议':'连接成功 · 查看接口信息'));
    if(value.sessionExpired){const a=make('a','重新登录工作台 →');a.href='/login';a.target='_top';panel.append(a);}
    if(d){
      const list=doc.createElement('dl');
      for(const [label,content] of [['实际接口',d.endpoint],['鉴权方式',({bearer:'Bearer Token',anthropic:'Anthropic x-api-key',gemini:'Gemini x-goog-api-key',none:'无鉴权'})[d.auth]||d.auth],['耗时',Number.isFinite(d.elapsed_ms)?(d.elapsed_ms/1000).toFixed(1)+' 秒':null]])if(content){list.append(make('dt',label),make('dd',content));}
      panel.append(list);
      if(d.suggestion)panel.append(make('p',d.suggestion));
      if(d.attempts?.length){const trail=doc.createElement('ol');for(const a of d.attempts.slice(0,24))trail.append(make('li',(a.status?'HTTP '+a.status:a.code||'连接检查')+' · '+(a.url||'')+(a.message?' · '+a.message:'')));panel.append(trail);}
    }
    anchor.insertAdjacentElement('afterend',panel);
  }
  root.ModelDiscovery={list,showDetails};
})(typeof window!=='undefined'?window:globalThis);
