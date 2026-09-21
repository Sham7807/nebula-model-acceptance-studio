/* One model-discovery path for every workbench panel. */
(function(root){
  'use strict';
  const AUTH = new Set(['bearer','anthropic','gemini','none']);
  function hosted(){try{return /^https?:$/.test(new URL(root.document?.baseURI||root.location?.href).protocol);}catch{return /^https?:$/.test(root.location?.protocol||'');}}
  function error(message,status){const value=new Error(message);if(status)value.status=status;return value;}
  function sanitized(message,key){let text=String(message||'未知错误');if(key)text=text.split(key).join('[已隐藏]');return text.replace(/(Bearer\s+)[^\s"<>]+/gi,'$1[已隐藏]');}
  function configFor(input){
    const base=String(input?.base||'').trim(),key=String(input?.key||'').trim(),auth=input?.auth||'bearer';
    if(!AUTH.has(auth))throw error('模型列表鉴权方式无效。');
    if((auth!=='none'&&!key)||/[\r\n]/.test(key))throw error('请填写有效的 API Key，或选择不使用鉴权。');
    let url;try{url=new URL(base);}catch{throw error('渠道地址必须是完整的 http:// 或 https:// URL。');}
    if(!/^https?:$/.test(url.protocol)||!url.hostname||url.username||url.password||url.search||url.hash||/[\u0000-\u0020\\]/.test(base))throw error('渠道地址仅支持 HTTP(S)，不能含账号、查询参数、片段或空白字符。');
    let path=url.pathname.replace(/\/+$/,'').replace(/\/(?:v\d+(?:beta)?|api\/v\d+)$/,'');
    path+=auth==='gemini'?'/v1beta':'/v1';
    url.pathname=path+'/models';
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
  async function jsonResponse(response,key,label){
    let body;try{body=await response.json();}catch{}
    if(!response.ok){
      const detail=body?.error?.message||body?.error||body?.message;
      const fallback=response.status===401?'登录已失效，请重新登录后重试。':response.status===403?'会话已过期，请刷新工作台后重试。':response.status===404?'当前地址未提供工作台服务，请从部署的工作台网址打开。':label+'失败';
      throw error('HTTP '+response.status+'：'+sanitized(typeof detail==='string'?detail:fallback,key),response.status);
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
        const session=await jsonResponse(await root.fetch('/api/session',{cache:'no-store',credentials:'same-origin',signal:controller.signal,redirect:'error'}),c.key,'工作台会话');
        if(typeof session?.token!=='string'||!session.token)throw error('工作台会话不可用，请刷新页面或重新登录后再获取模型列表。');
        data=await jsonResponse(await root.fetch('/api/models',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':session.token},body:JSON.stringify({base:c.base,key:c.key,auth:c.auth}),cache:'no-store',credentials:'same-origin',signal:controller.signal,redirect:'error'}),c.key,'获取模型列表');
        transport='service';
      }else{
        const headers={};
        if(c.auth==='bearer')headers.Authorization='Bearer '+c.key;
        if(c.auth==='anthropic'){headers['x-api-key']=c.key;headers['anthropic-version']='2023-06-01';headers['anthropic-dangerous-direct-browser-access']='true';}
        if(c.auth==='gemini')headers['x-goog-api-key']=c.key;
        data=await jsonResponse(await root.fetch(c.url,{headers,cache:'no-store',credentials:'omit',signal:controller.signal,redirect:'error'}),c.key,'模型列表接口');
        transport='direct';
      }
      if(controller.signal.aborted)throw error('已取消获取模型列表。');
      const models=catalog(data,c.auth);return {models,total:models.length,transport};
    }catch(reason){
      if(controller.signal.aborted){const value=error(timedOut?'获取模型列表超时，请检查渠道连通性后重试。':'已取消获取模型列表。');value.name=timedOut?'TimeoutError':'AbortError';throw value;}
      if(reason?.name==='TypeError')throw error(hosted()?'无法连接工作台服务，请检查服务器状态和登录会话后重试。':'浏览器无法连接渠道。请从部署的工作台网址打开以通过服务获取列表，或检查渠道 CORS / TLS / 网络设置。');
      reason.message=sanitized(reason.message,c.key);throw reason;
    }finally{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
  }
  root.ModelDiscovery={list};
})(typeof window!=='undefined'?window:globalThis);
