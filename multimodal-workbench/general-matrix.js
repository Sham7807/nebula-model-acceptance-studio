/* Deterministic request plans and evidence-based judgments. No network calls. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.GeneralMatrix=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const modes={quick:{caps:[1,10,20],prompts:1,repeats:1,streams:[false],cacheTokens:4096,loadRounds:1},std:{caps:[1,10,20],prompts:2,repeats:1,streams:[false,true],cacheTokens:12000,loadRounds:2},full:{caps:[1,10,20,64,128,256],prompts:3,repeats:2,streams:[false,true],cacheTokens:24000,loadRounds:3}};
  const prompts=[{id:'enumeration',label:'连续编号',text:'请从1到500逐行输出，每行严格写“编号N：这是用于验证输出长度边界的完整测试句子”，N依次递增。不要总结或省略，不要提前结束。'}, {id:'prose',label:'长篇说明',text:'请写一篇不少于1500字的中文文章，详细介绍海洋生态的10个方面，每个方面至少150字。不要总结，不要列提纲，直接写完整正文。'}, {id:'json_array',label:'结构化长列表',text:'请输出一个包含200个对象的完整JSON数组，每个对象含index、title、description，description至少30个英文单词。不要解释，不要省略对象。'}];
  const mode=value=>modes[value]?value:'quick';
  function caps(value){const p=modes[mode(value)],out=[];for(let repeat=1;repeat<=p.repeats;repeat++)for(const prompt of prompts.slice(0,p.prompts))for(const cap of p.caps)for(const stream of p.streams)out.push({id:`cap_${prompt.id}_${cap}_${stream?'sse':'json'}_${repeat}`,cap,stream,repeat,prompt:prompt.text,scenario:prompt.id,label:prompt.label});return out;}
  function load(value,maximum=5){const p=modes[mode(value)],max=Math.max(1,Math.min(10,Math.trunc(Number(maximum)||5)));return [...new Set(value==='quick'?[1]:[1,Math.min(2,max),max])].map(concurrency=>({concurrency,requests:Math.max(2,concurrency*p.loadRounds)}));}
  function plan(value,maximum=5){const key=mode(value),p=modes[key],capCases=caps(key),stages=load(key,maximum),tools=key==='quick'?2:key==='std'?5:8;return {mode:key,cap_cases:capCases.length,cap_values:[...p.caps],cap_scenarios:p.prompts,cap_repeats:p.repeats,cap_streams:p.streams.length,cap_output_token_budget:capCases.reduce((sum,item)=>sum+item.cap,0),tool_cases:tools,tool_request_upper_bound:tools+(key==='quick'?0:key==='std'?2:4),cache_prefix_token_estimate:p.cacheTokens,cache_requests:key==='full'?8:4,load_stages:stages,load_requests:stages.reduce((sum,item)=>sum+item.requests,0),estimate_notice:'Token前缀按字符估算，不是分词实测；请求上限与实际计费以原始usage和账单为准。'};}
  function errorResult(response){
    if(response?.ok)return null;const status=response?.status,message=String(response?.err||response?.error||response?.body?.error?.message||'');
    const reason_code=status===429?'rate_limited':['network','timeout',0,null,undefined].includes(status)?'transport_error':/not.support|unsupported|not.allowed|不支持|unknown.parameter|unrecognized/i.test(message)?'unsupported_parameter':Number(status)>=200&&Number(status)<300?'protocol_error':'http_error';
    return {status:'failed',reason_code,observed:`HTTP ${status??'未收到'}${message?' · '+message:''}`,meaning:reason_code==='transport_error'?'本次请求没有有效上游响应，不能据此评价模型能力。':reason_code==='unsupported_parameter'?'当前端点明确拒绝该参数，所请求能力未通过；不扩大为其它协议均不支持。':reason_code==='rate_limited'?'当前采样触发限流，记录实际负载和响应后复测。':'上游返回错误，先按请求证据定位鉴权、模型权限、输入或服务错误。'};
  }
  function judgeCap(response,item,format='openai-chat'){
    const error=errorResult(response);if(error)return error;const u=response.body?.usage||{},finish=response.body?.choices?.[0]?.finish_reason;
    // Gemini reports candidate output and thoughts separately; do not compare a derived sum to its visible-output cap.
    const output=format==='gemini'&&typeof u.raw_usage?.candidatesTokenCount==='number'?u.raw_usage.candidatesTokenCount:u.completion_tokens;
    const source=format==='gemini'?'usageMetadata.candidatesTokenCount':'规范化 completion_tokens（原生字段保存在请求证据）';
    const observed=`上限=${item.cap} · finish_reason=${finish??'未上报'} · 输出tokens=${output??'未上报'} · 字段=${source}${u.completion_tokens_details?.reasoning_tokens!=null?' · 推理tokens='+u.completion_tokens_details.reasoning_tokens:''}`;
    if(typeof output!=='number'||!Number.isFinite(output)||!finish)return {status:'inconclusive',reason_code:'evidence_missing',observed,meaning:'缺少输出token计数或停止原因，无法核验截断契约；没有按字符数伪造token计数。'};
    if(output>item.cap)return {status:'failed',reason_code:'output_cap_exceeded',observed,meaning:'上游原生输出计数超过请求上限，需核对参数映射、推理token口径和渠道透传。'};
    if(finish!=='length')return {status:'inconclusive',reason_code:'cap_not_exercised',observed,meaning:'本次提前自然停止、拒答或安全终止，没有真正触达上限；不将此样本当作已证明截断，也不单凭自然停止判定参数被忽略。'};
    return {status:'passed',reason_code:'assertion_passed',observed,meaning:'本次长度停止与上报输出token均符合所请求上限；不据此推定其它上限或模型均通过。'};
  }
  function percentile(values,p){const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);return sorted.length?sorted[Math.max(0,Math.ceil(sorted.length*p)-1)]:null;}
  function stats(rows,durationSeconds){const errors={},round=value=>value===null?null:Math.round(value*100)/100;for(const row of rows)if(!row.good)errors[String(row.status)]=(errors[String(row.status)]||0)+1;return {requests:rows.length,passed:rows.filter(row=>row.good).length,rate_limited:rows.filter(row=>row.status===429).length,errors,p50_ms:round(percentile(rows.map(row=>row.dt*1000),.5)),p95_ms:round(percentile(rows.map(row=>row.dt*1000),.95)),duration_ms:round(durationSeconds*1000),requests_per_second:round(rows.length/(durationSeconds||1)),output_tokens_per_second:round(rows.reduce((sum,row)=>sum+(row.ct||0),0)/(durationSeconds||1))};}
  return {caps,load,plan,errorResult,judgeCap,stats,percentile};
});
