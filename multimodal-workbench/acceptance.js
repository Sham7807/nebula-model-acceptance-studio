/* Optional local verification service; basic browser tests remain independent. */
(function(){
'use strict';
const el=id=>document.getElementById(id),make=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
let modelFetchEpoch=0,modelCatalog=[],acceptanceModelPicker=null,modelController=null;
let selected='general',runningSuite='',token='',runId='',active=false,pollTimer=null,serviceReady=false;
let serviceState='connecting',kvvRevision='';
const suiteRuns=new Map(),historyNotified=new Set();
let displayedRunId='',pollGeneration=0,restoring=false;
/* The overview is deliberately kept separate from the verifier's case list.  It
 * gives operators a fast, weighted view of coverage while the list below keeps
 * the exact upstream case names (and therefore remains backwards compatible). */
const acceptanceModules={
 kimi:[
  {id:'protocol',title:'K3 契约预检',weight:40,requests:4,desc:'基础协议、响应结构与错误映射'},
  {id:'max_tokens',title:'max_tokens',weight:15,requests:2,desc:'限制透传、截断与 finish_reason'},
  {id:'tools',title:'工具调用',weight:15,requests:3,desc:'顶层工具、强制调用与参数 JSON'},
  {id:'cache',title:'缓存真伪',weight:15,requests:2,desc:'usage 缓存字段与重复前缀观测'},
  {id:'multimodal',title:'多模态输入',weight:15,requests:2,desc:'图片、视频 URL 与能力声明'}
 ],
 ccmax:[
  {id:'protocol',title:'协议与流式',weight:35,requests:4,desc:'SSE 收尾、事件顺序与连接关闭'},
  {id:'parameters',title:'参数与错误',weight:15,requests:3,desc:'非法参数、错误状态与诊断'},
  {id:'tools',title:'工具调用',weight:20,requests:3,desc:'工具增量与 JSON 参数完整性'},
  {id:'security',title:'安全与一致性',weight:15,requests:3,desc:'指令层级、注入与重复行为'},
  {id:'cache',title:'Usage / 缓存',weight:15,requests:2,desc:'token、缓存字段与计数'}
 ],
 claude:[
  {id:'protocol',title:'Claude 协议与透传',weight:18,requests:4,desc:'Messages / SSE / usage / 错误结构'},
  {id:'auth_signature',title:'鉴权与签名',weight:14,requests:3,desc:'thinking 签名原样回传与篡改对照'},
  {id:'tools',title:'工具调用与多模态',weight:14,requests:4,desc:'tool_use、JSON Schema、图片输入'},
  {id:'max_tokens',title:'max_tokens 与长度',weight:10,requests:2,desc:'上限透传、截断与 stop_reason'},
  {id:'injection',title:'注入与指令层级',weight:14,requests:3,desc:'合成金丝雀与越权行为观察'},
  {id:'identity',title:'模型来源线索',weight:10,requests:3,desc:'响应标识与路由负对照'},
  {id:'cache',title:'长前缀缓存',weight:10,requests:3,desc:'大 token cache_control 与 usage 观测'},
  {id:'stress',title:'受控压测',weight:10,requests:20,desc:'并发、延迟、错误率与限流观测'}
 ]
};
let enabledModules={kimi:new Set(acceptanceModules.kimi.map(m=>m.id)),ccmax:new Set(acceptanceModules.ccmax.map(m=>m.id)),claude:new Set(acceptanceModules.claude.map(m=>m.id))};
const statuses={passed:'通过',failed:'未通过',skipped:'已跳过',inconclusive:'无法判定',error:'运行错误',cancelled:'已取消',completed:'测试已完成',running:'运行中',not_covered:'未覆盖'};
const claudeItems=['基础请求与 SSE 收尾','thinking 签名回传与篡改对照','工具调用与 JSON Schema','图片 / 多模态输入','max_tokens=1 与截断','系统提示词注入金丝雀','指令层级越权覆盖','模型来源线索与路由负对照','长前缀缓存（大 token）','重复请求缓存字段','受控并发压测与延迟','错误状态 / request-id 透传'];
const ccItems=['无效 thinking 签名','message_start 唯一性','message_stop 完整收尾','连接及时关闭','流中错误事件','错误状态与格式','usage / 缓存字段','工具参数 JSON 增量','系统提示词注入与金丝雀泄露','指令层级与越权覆盖','重复行为一致性（蒸馏风险启发式）','非法参数拒绝与错误诊断'];
const quickItems=['基础请求 · non-thinking','基础请求 · thinking','非法温度 · non-thinking','非法温度 · thinking','Tool Schema · 非流式','Tool Schema · 流式','Dynamic tools','JSON Object 输出','tool_choice required','Prompt Tokens · 基础','Prompt Tokens · 工具'];
const openaiQuickItems=['非流式基础请求','SSE 收尾与 usage','max_tokens=1 限制','非法 max_tokens 拒绝','强制工具调用','禁止工具调用','多个顶层工具选择','JSON 对象输出','内置样例图片识别','重复前缀缓存观测','Token 计数一致性'];
const openaiFullItems=['参数与协议 · 标准兼容断言','工具与 JSON Schema · 兼容矩阵','多模态与能力扩展 · 独立记录支持情况','Token / usage / 缓存 · 计数一致性'];
const openaiCcItems=['响应 ID 与流式增量','finish_reason 与 [DONE] 收尾','[DONE] 后响应流结束','流中错误事件','非法模型错误状态与格式','usage / cached_tokens 字段','delta.tool_calls JSON 增量','系统提示词注入与金丝雀泄露','指令层级与越权覆盖','重复行为一致性（蒸馏风险启发式）','非法 max_tokens 拒绝'];
const fullItems=['参数约束 · 全量','Tool JSON Schema · 全量','K3 特性契约 · 全量','Prompt Token · 文本与视觉'];
function message(text,error=false){el('acceptanceMessage').hidden=!text;el('acceptanceMessage').textContent=text;el('acceptanceMessage').className='notice'+(error?' error':'');}
function updateServiceBadge(){
 const badge=el('acceptanceService'),kimi=selected==='kimi',claude=selected==='claude';
 if(selected==='gpt'){
  badge.textContent='浏览器内执行 · GPT 专项';badge.title='GPT 生成专项直接在深度检测工作区执行，不依赖 KVV 或 CCMax 本地验收服务。';return;
 }
 if(serviceState==='file'){
  badge.textContent='需要本地验收服务';badge.title='双击启动验收工作台.command，各专项共用同一个本地服务。';
 }else if(serviceState==='connecting'){
  badge.textContent='正在连接本地验收服务';badge.title='正在检查网页与本地验收服务的连接。';
 }else if(serviceState==='connected'){
  badge.textContent=kimi?'本地服务已连接 · '+(kvvRevision?'集成 KVV '+kvvRevision:'KVV 版本未提供'):claude?'本地服务已连接 · Claude 专项检测':'本地验收服务已连接';
  badge.title=kimi?'仅表示网页已连接本地服务；开始 Kimi 验收后会调用已集成的官方 KVV，启动结果以任务日志为准。':claude?'Claude 专项与 KVV 独立运行，检测渠道暴露的 Anthropic Messages 或 OpenAI 兼容接口。':'网页已连接本地服务，CCMax 按所选请求格式运行独立检测器。';
 }else{
  badge.textContent='本地验收服务未连接';badge.title='请检查本地验收服务是否运行，恢复服务后刷新页面重新连接。';
 }
}
function selectedAcceptanceModels(){
 const ids=acceptanceModelPicker?.getSelected?.()||[];const typed=el('acceptanceModel').value.trim();
 return [...new Set((ids.length?ids:[typed]).map(v=>String(v||'').trim()).filter(Boolean))];
}
const productionDrafts=new Map();let productionDefaults;
const productionPresets={screening:{duration_seconds:120,max_requests:60,concurrency:2,context_tokens:4096,output_tokens:2048},standard:{duration_seconds:1800,max_requests:600,concurrency:4,context_tokens:12000,output_tokens:4096},soak:{duration_seconds:21600,max_requests:2000,concurrency:4,context_tokens:16000,output_tokens:4096}};
const workloadLabels={short:'短问答',long_output:'长输出',long_context:'长上下文',stream:'长流式',thinking:'思考模式',vision:'图片识别',tools:'工具会话',cancellation:'主动取消',custom:'自定义业务'};
const productionFields={duration_seconds:'claudeProductionDuration',max_requests:'claudeProductionRequests',concurrency:'claudeProductionConcurrency',requests_per_minute:'claudeProductionRpm',context_tokens:'claudeProductionContext',output_tokens:'claudeProductionOutput',recovery_retries:'claudeProductionRetries',max_estimated_tokens:'claudeProductionTokenBudget'};
const admissionFields={target_success_rate:'claudeAdmissionSuccess',min_samples:'claudeAdmissionSamples',min_observation_seconds:'claudeAdmissionObservation',max_p95_first_content_ms:'claudeAdmissionFirstContent',max_p95_latency_ms:'claudeAdmissionLatency'};
const priceFields={input_per_million:'claudePriceInput',output_per_million:'claudePriceOutput',cache_read_per_million:'claudePriceCacheRead',cache_write_per_million:'claudePriceCacheWrite'};
const nullableNumber=id=>el(id).value.trim()===''?null:Number(el(id).value);
function customProductionCases(validate=false){
 const raw=el('claudeProductionCases').value.trim();if(!raw)return [];
 try{
  const parsed=JSON.parse(raw),cases=Array.isArray(parsed)?parsed:parsed?.cases;
  if(!Array.isArray(cases)||cases.length>20)throw new Error('请提供最多 20 个业务样本的 JSON 数组');
  const hasSecret=value=>value&&typeof value==='object'&&Object.entries(value).some(([key,item])=>/^(authorization|x-api-key|api[_-]?key|secret|password|access[_-]?token)$/i.test(key)||hasSecret(item));
  return cases.map((item,index)=>{const name=item?.name||item?.title;if(!item||typeof item!=='object'||Array.isArray(item)||typeof name!=='string'||!name.trim()||!item.body||typeof item.body!=='object'||Array.isArray(item.body))throw new Error(`第 ${index+1} 项需要 name 和 body 对象`);if(hasSecret(item))throw new Error(`第 ${index+1} 项包含凭据字段，请移除后导入`);const expected=item.expected_text??item.expect?.exact_text,contains=item.expect?.contains;if(expected!=null&&typeof expected!=='string')throw new Error(`第 ${index+1} 项 expected_text 必须为字符串`);if(!expected&&(!Array.isArray(contains)||!contains.length||contains.some(x=>typeof x!=='string'||!x)))throw new Error(`第 ${index+1} 项需要非空 expected_text 或 expect.contains 断言`);const body={...item.body};delete body.model;return {name:name.trim(),body,...(expected!=null?{expected_text:expected}:{}),...(Array.isArray(contains)?{expect:{contains}}:{})};});
 }catch(error){if(validate)throw new Error('自定义业务样本无效：'+error.message);return [];}
}
function productionConfiguration(){
 return {production:{profile:el('claudeProductionProfile').value,...Object.fromEntries(Object.entries(productionFields).map(([key,id])=>[key,nullableNumber(id)])),workloads:[...document.querySelectorAll('[data-production-workload]:checked')].map(x=>x.dataset.productionWorkload),custom_cases:customProductionCases()},admission:{...Object.fromEntries(Object.entries(admissionFields).map(([key,id])=>[key,nullableNumber(id)])),required_workloads:[...document.querySelectorAll('[data-admission-workload]:checked')].map(x=>x.dataset.admissionWorkload)},pricing:{currency:el('claudePriceCurrency').value,version:el('claudePriceVersion').value.trim()||null,...Object.fromEntries(Object.entries(priceFields).map(([key,id])=>[key,nullableNumber(id)]))}};
}
function validateProductionConfiguration(){
 if(!['claude','ccmax','kimi'].includes(selected))return;
 const fields=el('claudeProductionSettings').querySelectorAll('input[type="number"]');for(const field of fields){if(!field.disabled&&!field.checkValidity())throw new Error((document.querySelector('label[for="'+field.id+'"]')?.textContent||'配置')+'超出允许范围');}
 const c=productionConfiguration();if(Object.values(c.admission).some(value=>value===null)||!c.admission.required_workloads.length)throw new Error('请填写完整的准入门槛，并选择必须验证的业务');if(c.production.profile==='off')return;
 for(const [key,value] of Object.entries(c.production)){if(Object.hasOwn(productionFields,key)&&key!=='max_estimated_tokens'&&value===null)throw new Error('请填写完整的业务验证运行参数');}
 if(!c.production.workloads.length)throw new Error('请选择至少一种业务负载');
 const cases=customProductionCases(true);if(c.production.workloads.includes('custom')&&!cases.length)throw new Error('已选择自定义业务，请先导入业务样本');
}
function updateProductionSettings(){
 const profile=el('claudeProductionProfile').value;el('claudeProductionFields').disabled=profile==='off';
 el('claudeProductionBadge').textContent=({screening:'接入初筛',standard:'标准观察',soak:'持续观察',custom:'自定义',off:'未启用'})[profile];
 const {production:p}=productionConfiguration();el('claudeProductionHelp').textContent=profile==='off'?'仅执行原专项与参数矩阵；没有持续业务证据时，报告会保留准入证据缺口。':`原专项与参数矩阵之外追加：最多 ${p.max_requests??'—'} 次请求（含重试），观察 ${duration(p.duration_seconds||0)}，最多 ${p.concurrency??'—'} 路并发。达到时长或预算即停止调度；初筛不等于正式准入。`;
}
function restoreProductionConfiguration(saved){
 for(const [section,fields] of [['production',productionFields],['admission',admissionFields],['pricing',priceFields]]){const source=saved[section];if(!source||typeof source!=='object'||Array.isArray(source))continue;for(const [key,id] of Object.entries(fields)){const value=source[key];if(value===null)el(id).value='';else if(typeof value==='number'&&Number.isFinite(value))el(id).value=String(value);}}
 const p=saved.production,a=saved.admission,pricing=saved.pricing;
 if(p&&typeof p==='object'){if(['off','screening','standard','soak','custom'].includes(p.profile))el('claudeProductionProfile').value=p.profile;if(Array.isArray(p.workloads))document.querySelectorAll('[data-production-workload]').forEach(x=>x.checked=p.workloads.includes(x.dataset.productionWorkload));if(Array.isArray(p.custom_cases))el('claudeProductionCases').value=JSON.stringify(p.custom_cases,null,2);}
 if(Array.isArray(a?.required_workloads))document.querySelectorAll('[data-admission-workload]').forEach(x=>x.checked=a.required_workloads.includes(x.dataset.admissionWorkload));
 if(['CNY','USD'].includes(pricing?.currency))el('claudePriceCurrency').value=pricing.currency;
 if(pricing&&Object.hasOwn(pricing,'version'))el('claudePriceVersion').value=pricing.version==null?'':String(pricing.version);
 updateProductionSettings();updateCustomCaseStatus();
}
function updateCustomCaseStatus(){try{const cases=customProductionCases(true);el('claudeProductionCasesStatus').textContent=cases.length?`已就绪 ${cases.length} 个业务样本`:'未导入自定义业务样本';el('claudeProductionCasesStatus').classList.remove('error');}catch(error){el('claudeProductionCasesStatus').textContent=error.message;el('claudeProductionCasesStatus').classList.add('error');}}
function config(){
 const isClaude=selected==='claude', requestFormat=isClaude?el('claudeFormat').value:selected==='ccmax'?el('acceptanceFormat').value:el('acceptanceThinkMode').value==='openai'?'openai':'native';
 const models=selectedAcceptanceModels();
 return {...productionConfiguration(),matrix_profile:el('acceptanceMatrixProfile').value,matrix_modules:[...document.querySelectorAll('[data-matrix-module]:checked')].map(x=>x.dataset.matrixModule),suite:isClaude?'claude':selected==='ccmax'?'ccmax':el('acceptanceScope').value,base:el('acceptanceBase').value.trim(),key:el('acceptanceKey').value.trim(),model:models[0]||el('acceptanceModel').value.trim(),models,timeout:Number(el('acceptanceTimeout').value),signature_samples:Number(isClaude?el('claudeSignature').value:el('acceptanceSignature').value),sse_samples:Number(isClaude?el('claudeSse').value:el('acceptanceSse').value),concurrency:isClaude?Number(el('claudeStressConcurrency').value):2,auth:requestFormat==='openai'?'bearer':isClaude?el('claudeAuth').value:el('acceptanceAuth').value,request_format:requestFormat,provider:isClaude?el('claudeProvider').value:undefined,sampling:isClaude?el('claudeSampling').value:undefined,cache_tokens:isClaude?Number(el('claudeCacheTokens').value):undefined,stress_requests:isClaude?Number(el('claudeStressRequests').value):undefined,stress_concurrency:isClaude?Number(el('claudeStressConcurrency').value):undefined,think_mode:el('acceptanceThinkMode').value,thinking:!['none','openai'].includes(el('acceptanceThinkMode').value),advanced:selected==='ccmax'||isClaude,enabled_modules:[...(enabledModules[selected]||[]) ]};
}
function updatePlan(){
 updateProductionSettings();
 const mp=el('acceptanceMatrixProfile').value;el('acceptanceMatrixHelp').textContent=mp==='off'?'仅执行原有套件。选择标准或完整矩阵可增加多参数证据。':mp==='quick'?'长度上限 1 / 10 / 20，各独立验证；包含基础阶梯压力采样。':mp==='standard'?'长度上限 1 / 10 / 20 × 两种场景 × 流式与非流式，共 12 条长度用例；另外验证工具、图像、注入、缓存与分阶段压力。':'长度上限 1 / 10 / 20 / 64 / 128 / 256 × 三种场景 × 流式与非流式 × 两轮，共 72 条长度用例；扩大各模块样本与压力阶段。';
 const cc=selected==='ccmax',claude=selected==='claude',full=el('acceptanceScope').value==='kvvfull',openai=cc?el('acceptanceFormat').value==='openai':claude?el('claudeFormat').value==='openai':el('acceptanceThinkMode').value==='openai';
 el('acceptanceTitle').textContent=cc?'CCMax渠道验收':claude?'Claude 上游专项验收':'Kimi Vendor Verifier';
 el('acceptanceDescription').textContent=claude?'面向 Anthropic 官方或 AWS Bedrock 上游的 Claude 中转渠道，检查基础能力、长前缀缓存、注入防护、签名和接口透传，保留逐请求证据。':cc?(openai?'使用 OpenAI Chat Completions 请求与响应格式，检查 Claude 兼容渠道的流式、工具、错误及安全行为。':'使用独立的 Anthropic Messages 检测器，检查流式可靠性、参数校验和工具调用，保留每次样本证据。'):(openai?'四个检测层面统一使用 OpenAI 兼容请求。预检运行 11 个兼容用例；全套增加能力探针及 KVV 工具 Schema 矩阵。':'网页通过同一个本地服务自动调用已集成的 MoonshotAI 官方 KVV，提供原生预检和完整 API 验证，无需另开项目。');
 el('acceptanceKimiFields').hidden=cc||claude;el('acceptanceCcFields').hidden=!cc;el('acceptanceClaudeFields').hidden=!claude;
 el('acceptanceAuth').disabled=cc&&openai;
 el('claudeAuth').disabled=claude&&openai;if(claude&&openai)el('claudeAuth').value='bearer';
 el('claudeSignatureGroup').hidden=claude&&openai;
 el('acceptanceClaudeFormatHelp').textContent=openai?'OpenAI 格式发送到 /v1/chat/completions，签名原生语义不适用；缓存是否暴露取决于渠道，缺字段不会判为命中。':'Messages 格式发送到 /v1/messages，支持 x-api-key 或 Bearer。AWS 来源通过渠道提供的兼容接口验收，AWS SigV4 内部代签需要上游日志佐证。';
 el('acceptanceRun').textContent=claude?'开始 Claude 专项 ↗':'开始验收 ↗';
 el('acceptanceSignatureGroup').hidden=cc&&openai;
 el('acceptanceFormatHelp').textContent=openai&&!cc?'OpenAI 模式覆盖参数、工具 Schema、能力特性和 Token / 缓存。原生动态工具改用顶层工具工作流；固定 Kimi token 基准不参与兼容评分，报告会列明未验证的原生语义。':'Kimi 原生格式保留官方断言；开源 / 不传 thinking 选项仍仅调整参数和 Schema 用例，其他专项保留 Kimi 原生约定。';
 el('acceptanceCcFormatHelp').textContent=openai?'请求发送到 /v1/chat/completions，使用 Bearer 鉴权；签名校验不适用于此协议，不发送请求、不计入评分。':'请求发送到 /v1/messages；Bearer 仅改变鉴权，不改变原生 Messages 请求体。';
 const items=claude?claudeItems:cc?(openai?openaiCcItems:ccItems):openai?(full?openaiFullItems:openaiQuickItems):full?fullItems:quickItems;
 const title=claude?'Claude 专业上游验收 · 检测范围':cc?(openai?'11 类 OpenAI 兼容验收检查':'12 类渠道验收检查（含 4 项安全与一致性探针）'):openai?(full?'OpenAI 全范围兼容验证':'11 项 OpenAI 兼容预检'):full?'全套 API verifier':'11 项代表性预检';
 const root=el('acceptancePlan');root.replaceChildren();
 const moduleSuite=claude?'claude':cc?'ccmax':'kimi', modules=acceptanceModules[moduleSuite];
 const overview=make('div','acceptance-module-overview');
 const overviewHead=make('div','acceptance-module-head');
 overviewHead.append(make('div','', '检测模块'),make('small','', '勾选要纳入本轮验收的能力维度 · 权重合计决定最终评分'));
 overview.append(overviewHead);
 const cards=make('div','acceptance-module-cards');
 modules.forEach(module=>{
  const card=make('label','acceptance-module-card');card.dataset.module=module.id;
  const check=document.createElement('input');check.type='checkbox';check.checked=enabledModules[moduleSuite].has(module.id);check.dataset.module=module.id;check.disabled=active||restoring;
  const mark=make('i','module-check','✓');
  const body=make('div','module-card-body');body.append(make('strong','',module.title),make('small','',module.desc));
  const meta=make('div','module-card-meta');meta.append(make('b','',module.weight+'%'),make('em','','原专项模块'));
  card.append(check,mark,body,meta);cards.append(card);
  check.addEventListener('change',()=>{if(check.checked)enabledModules[moduleSuite].add(module.id);else enabledModules[moduleSuite].delete(module.id);updatePlan();});
 });
 overview.append(cards);
 const selectedWeight=modules.filter(m=>enabledModules[moduleSuite].has(m.id)).reduce((sum,m)=>sum+m.weight,0);
 overview.append(make('p','acceptance-module-summary',`已启用 ${enabledModules[moduleSuite].size}/${modules.length} 个模块 · 覆盖权重 ${selectedWeight}%`));
 root.append(overview,make('h3','acceptance-plan-title',title));
 const list=make('ol','acceptance-plan-list');items.forEach((text,i)=>{const item=make('li');item.append(make('i','',String(i+1).padStart(2,'0')),make('span','',text));list.append(item);});root.append(list);
 const note=claude?'来源线索仅记录响应标识、模型字段与非法模型路由对照；客户端验收无法证明官方来源、权重或 AWS SigV4 内部代签。未命中缓存需结合实测 token 门槛、请求前缀与上游日志定位。':cc?(openai?'按 OpenAI delta / tool_calls / [DONE] 验证流式响应；Claude 专属签名项明确列为不适用。安全和一致性探针只记录本轮行为，不能证明模型来源或蒸馏事实。':'包含协议、流式、工具、错误映射及安全与一致性探针。401、429、网络错误记为无法判定。'):openai?'兼容模式使用独立断言，不冒充官方原生验证通过。视频 URL、reasoning 等扩展不被支持、未观察到缓存命中时会记录限制；完整 Schema 矩阵可能超出部分渠道支持的子集。':full?'官方全套逐项执行，保留官方跳过项和本地检查。实际用例数量以收集结果为准，不自动重试失败请求。':'这是官方用例的抽样组合，Kimi-K3 会另外追加工作台能力探针；非法参数遇到鉴权或网络错误不会记为通过。';
 root.append(make('p','acceptance-plan-note',note));
 el('acceptanceFootnote').textContent=note+' 本地服务保存脱敏请求证据；未执行和不适用项不会记为通过。';
 el('acceptanceRequestHint').textContent=claude?`专业方案 · 长前缀目标约 ${Number(el('claudeCacheTokens').value).toLocaleString()} token，压测 ${Number(el('claudeStressRequests').value)} 次 / ${Number(el('claudeStressConcurrency').value)} 路并发；条件请求上限请查看测试请求预览，实际请求数随模块和前置结果变化。`:cc?`计划 ${(openai?0:Number(el('acceptanceSignature').value))+Number(el('acceptanceSse').value)+7} 次请求，按渠道计费。`:full?'全量可能执行数百次请求，实际数量取决于收集结果；按渠道计费。':openai?'计划 11 个兼容测试项，部分项目包含多次请求；不自动重试，按渠道计费。':'计划 11 个官方预检项，Kimi-K3 另有扩展项；不自动重试，按渠道计费。';
}

async function api(path,options={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='/api/models'?30000:12000);
 try{const r=await fetch(path,{...options,signal:controller.signal,cache:'no-store',headers:{'Content-Type':'application/json','X-Workbench-Token':token,...options.headers}});if(!r.ok){let text;try{text=(await r.json()).error;}catch{}const error=new Error(text||'本地服务错误 '+r.status);error.status=r.status;throw error;}return r;}finally{clearTimeout(timer);}
}
function setActive(value){active=value;el('acceptanceFields').disabled=value||restoring;el('acceptanceRun').disabled=value||restoring||!serviceReady;el('acceptanceStop').disabled=!value;document.querySelectorAll('.acceptance-module-card input').forEach(input=>{input.disabled=value||restoring;});document.querySelectorAll('.deep-suite-tabs button[data-suite]:not([data-suite="general"])').forEach(b=>b.disabled=value&&b.dataset.suite!==runningSuite);}
function syncDownloads(){
 const saved=suiteRuns.get(selected),available=!!(saved&&saved.id===displayedRunId&&saved.data.result);
 document.querySelectorAll('[data-acceptance-download]').forEach(b=>b.disabled=!available);
}
function hideResults(){
 displayedRunId='';el('acceptanceProgress').hidden=true;el('acceptanceVerdict').hidden=true;el('claudeAdmissionPanel').hidden=true;
 for(const id of ['acceptanceStage','acceptanceCount','acceptanceElapsed','acceptanceEta','acceptanceSummary','acceptanceVerdict','acceptanceCases','acceptanceLog'])el(id).replaceChildren();
 el('acceptanceBar').value=0;syncDownloads();
}
function showSuiteResult(){
 const saved=suiteRuns.get(selected);
 if(saved)render(saved.data,saved.id);else hideResults();
}
function recordRun(data,id){
 const suite=data.suite==='ccmax'?'ccmax':data.suite==='claude'?'claude':'kimi';
 suiteRuns.set(suite,{id,data});runningSuite=suite;setActive(data.status==='running');
 if(new URLSearchParams(location.search).has('desktop_workspace')){
  sessionStorage.setItem('desktopAcceptanceRun',id);
  window.dispatchEvent(new CustomEvent('workbench:run-state',{detail:{id,status:data.status,model:data.model,completed:data.completed,total:data.total}}));
 }
 if(data.history_saved&&data.history_id&&!historyNotified.has(data.history_id)){historyNotified.add(data.history_id);window.dispatchEvent(new CustomEvent('workbench:history-saved',{detail:{id:data.history_id,kind:suite}}));}
 if(selected===suite)render(data,id);
}
function selectSuite(value){
 if(active&&value!=='general'&&value!==runningSuite)return;
 if(value==='gpt'){
  selected='gpt';updateServiceBadge();document.querySelectorAll('[data-suite]').forEach(b=>{b.classList.toggle('active',b.dataset.suite==='gpt');b.setAttribute('aria-selected',String(b.dataset.suite==='gpt'));});
  if(el('generalHistorySaveStatus'))el('generalHistorySaveStatus').hidden=true;
  el('legacyFrame').hidden=true;el('acceptancePanel').hidden=true;el('gptPanel').hidden=false;
  window.GptSuite?.activate?.();
  return;
 }
 if(value!==selected&&selected!=='gpt')invalidateModels();
 if(['claude','ccmax','kimi'].includes(value)){if(['claude','ccmax','kimi'].includes(selected))productionDrafts.set(selected,productionConfiguration());const draft=productionDrafts.get(value)||JSON.parse(JSON.stringify(productionDefaults));if(!productionDrafts.has(value)&&value!=='claude')draft.production.profile='off';restoreProductionConfiguration(draft);}
 selected=value;updateServiceBadge();document.querySelectorAll('[data-suite]').forEach(b=>{b.classList.toggle('active',b.dataset.suite===value);b.setAttribute('aria-selected',String(b.dataset.suite===value));});
 if(el('generalHistorySaveStatus'))el('generalHistorySaveStatus').hidden=value!=='general';
  el('legacyFrame').hidden=value!=='general';el('acceptancePanel').hidden=value==='general';el('gptPanel').hidden=true;
  if(value==='general'){
   const frame=el('legacyFrame');
   // A hidden iframe may keep the previous responsive height. Ask the embedded
   // detector to measure again after it returns to the document flow.
   const requestResize=()=>{try{frame.contentWindow?.postMessage({type:'workbench:deep-resize-request'},'*');}catch{}};
   requestAnimationFrame(()=>requestAnimationFrame(requestResize));
  }
 if(value!=='general'){
  for(const [to,from] of [['acceptanceBase','base'],['acceptanceKey','key'],['acceptanceModel','model']])if(!el(to).value)el(to).value=el(from).value;
  message('');updatePlan();showSuiteResult();
 }
}
function latestEventCases(events){
 const latest=new Map();
 for(const event of events){
  const item=event.case||event.sample||(event.phase==='sample_complete'?{id:event.sample_id,status:event.status}:event.type==='result'?event.result:null);
  if(!item||!item.status)continue;
  const id=item.id||item.nodeid||item.sample_id||event.sample_id;
  const key=id||item;
  latest.delete(key);latest.set(key,id?{...item,id}:item);
 }
 return [...latest.values()];
}
function restoreConfiguration(job){
 const saved=job.result?.configuration||job.configuration;
 if(!saved||typeof saved!=='object'||Array.isArray(saved))return;
 const suiteKey=job.suite==='ccmax'?'ccmax':job.suite==='claude'?'claude':'kimi';
 if(saved.production||saved.admission||saved.pricing){restoreProductionConfiguration(saved);productionDrafts.set(suiteKey,productionConfiguration());}
 if(Array.isArray(saved.enabled_modules)){
  const allowed=new Set(acceptanceModules[suiteKey].map(module=>module.id));
  const restoredModules=new Set(saved.enabled_modules.map(value=>String(value)).filter(value=>allowed.has(value)));
  if(restoredModules.size)enabledModules[suiteKey]=restoredModules;
 }
 const restored={};
 for(const [field,id,min,max] of [['signature_samples','acceptanceSignature',1,20],['sse_samples','acceptanceSse',1,200],['timeout','acceptanceTimeout',5,600]]){
  const value=Number(saved[field]);
  if(Number.isFinite(value)&&value>=min&&value<=max&&(field==='timeout'||Number.isInteger(value))){el(id).value=String(value);restored[field]=value;}
 }
 if(['anthropic','bearer'].includes(saved.auth))el('acceptanceAuth').value=saved.auth;
 if(job.suite==='claude'){
  if(['auto','anthropic','aws'].includes(saved.provider))el('claudeProvider').value=saved.provider;
  if(['anthropic','openai'].includes(saved.request_format))el('claudeFormat').value=saved.request_format;
  if(['anthropic','bearer'].includes(saved.auth))el('claudeAuth').value=saved.auth;
  for(const [field,id,min,max] of [['signature_samples','claudeSignature',1,20],['sse_samples','claudeSse',1,200],['cache_tokens','claudeCacheTokens',1024,100000],['stress_requests','claudeStressRequests',1,200],['stress_concurrency','claudeStressConcurrency',1,20]]){const value=Number(saved[field]??(field==='stress_concurrency'?saved.concurrency:undefined));if(Number.isFinite(value)&&Number.isInteger(value)&&value>=min&&value<=max)el(id).value=String(value);}
  const knownPlan=['quick','professional','stress','custom'].includes(saved.sampling)?saved.sampling:null;
  const numbers=['signature_samples','sse_samples','cache_tokens','stress_requests','stress_concurrency'].map(field=>Number(saved[field]??(field==='stress_concurrency'?saved.concurrency:undefined)));
  const inferredPlan=Object.entries({quick:[1,3,12000,1,1],professional:[3,5,12000,20,4],stress:[2,10,12000,100,10]}).find(([,values])=>values.every((value,index)=>numbers[index]===value))?.[0]||'custom';
  el('claudeSampling').value=knownPlan||inferredPlan;
 }
 if(['off','quick','standard','comprehensive'].includes(saved.matrix_profile))el('acceptanceMatrixProfile').value=saved.matrix_profile;
 if(Array.isArray(saved.matrix_modules))document.querySelectorAll('[data-matrix-module]').forEach(x=>x.checked=saved.matrix_modules.includes(x.dataset.matrixModule));
 if(['anthropic','openai'].includes(saved.request_format)&&job.suite==='ccmax')el('acceptanceFormat').value=saved.request_format;
 if(['kimi','opensource','none','openai'].includes(saved.think_mode))el('acceptanceThinkMode').value=saved.think_mode;
 if(restored.signature_samples!==undefined&&restored.sse_samples!==undefined){
  const signature=restored.signature_samples,sse=restored.sse_samples;
  el('acceptanceSampling').value=signature===1&&sse===3?'quick':signature===5&&sse===50?'batch':'custom';
 }
}
function renderCase(item){
 const status=item.applicable===false?'not_covered':(item.status||'inconclusive');
 const row=make('div','acceptance-case '+status);row.dataset.caseId=item.id||item.nodeid||'';
 const title=(item.title||item.label||item.name||item.id||item.probe||'测试项')+(Number.isInteger(item.samples)?`（${item.samples} 样本 / ${item.failures||0} 异常）`:'' );
 const detail=item.skip_reason||item.detail||item.details||item.issues||item.notes||item.observations;
 const expected=item.expected||item.expectation||item.expected_result||item.requirement||'接口按协议返回可判定结果';
 let actual=item.actual||item.observed||item.result||detail||statuses[status]||status;
 if(typeof actual!=='string')actual=JSON.stringify(actual,null,2);
 const grid=make('div','acceptance-matrix-row');
 const resultCell=make('div','matrix-result');resultCell.append(make('span','status-dot '+status,item.applicable===false?'—':status==='passed'?'✓':status==='failed'||status==='error'?'×':'!'),make('b','',item.applicable===false?'不适用':statuses[status]||status));
 grid.append(make('div','matrix-case',title),make('div','matrix-expected',typeof expected==='string'?expected:JSON.stringify(expected,null,2)),make('div','matrix-actual',actual),resultCell);
 row.append(grid);
 if(detail&&(typeof detail==='string'||detail.length)){
  const details=make('details','matrix-details'),summary=make('summary','','查看请求证据与诊断');details.append(summary,make('pre','',typeof detail==='string'?detail:JSON.stringify(detail,null,2)));row.append(details);
 }
 return row;
}
function duration(seconds){return seconds>=60?`${Math.floor(seconds/60)} 分 ${Math.floor(seconds%60)} 秒`:`${Math.floor(seconds)} 秒`;}
const metricText=value=>value==null?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
const rateText=value=>typeof value==='number'&&Number.isFinite(value)?(value*100).toFixed(2).replace(/\.00$/,'')+'%':'未记录';
const msText=value=>typeof value==='number'&&Number.isFinite(value)?(value/1000).toFixed(2)+' 秒':'未记录';
function metricCard(label,value,detail){const card=make('div','admission-stat');card.append(make('small','',label),make('strong','',value));if(detail)card.append(make('span','',detail));return card;}
function renderAdmission(data,id){
 const panel=el('claudeAdmissionPanel'),result=data.result,admission=result?.admission;
 panel.replaceChildren();panel.hidden=!admission&&!Array.isArray(result?.results);
 if(Array.isArray(result?.results)){
  panel.append(make('h3','','逐模型准入结论'),make('p','admission-summary','各模型独立判断稳定性、运行范围和费用；批量结果不合并评分或账单。'));
  const grid=make('div','admission-workload-grid');for(const child of result.results){const assessment=child.result?.admission,card=make('div','admission-workload');card.append(make('b','',child.model||'未命名模型'),make('span','',assessment?.label||'准入证据待整理'));if(assessment?.reliability)card.append(make('small','',`业务成功率 ${rateText(assessment.reliability.business_success_rate)} · ${assessment.reliability.normal_tasks??'—'} 个任务`));if(child.run_id){const open=make('button','text-button','查看独立准入与账单');open.type='button';open.disabled=data.status==='running';open.addEventListener('click',async()=>{open.disabled=true;try{const job=await(await api('/api/runs/'+encodeURIComponent(child.run_id))).json();restoreConfiguration(job);recordRun(job,child.run_id);message('已打开 '+(child.model||'该模型')+' 的独立结果，可单独核对账单和下载报告。');}catch(error){message(error.message,true);open.disabled=false;}});card.append(open);}grid.append(card);}panel.append(grid);return;
 }
 if(!admission)return;
 const reliability=admission.reliability||{},cost=admission.cost||{},envelope=admission.validated_envelope||{};
 const head=make('div','admission-head'),heading=make('div');heading.append(make('span','admission-kicker','CHANNEL READINESS'),make('h3','','渠道准入结论'));head.append(heading,make('span','admission-label '+(admission.status||'retest'),admission.label||'待评估'));
 panel.append(head,make('p','admission-summary','准入结论与能力得分分别判断：只有已测业务、持续观察与关键门槛都满足，才建议在验证范围内接入。'));
 const stats=make('div','admission-stats');stats.append(metricCard('首试业务成功率',rateText(reliability.first_attempt_success_rate),'客户首次请求的完成表现'),metricCard('协议完整率',rateText(reliability.protocol_success_rate),'响应结构与流式收尾'),metricCard('完整业务成功率',rateText(reliability.business_success_rate),`${metricText(reliability.business_successful)} / ${metricText(reliability.normal_tasks)} 个正常任务`),metricCard('最终成功率',rateText(reliability.eventual_success_rate),'包含允许的恢复重试'),metricCard('P95 首个有效内容',msText(reliability.first_content_p95_ms),'不把心跳和思考元数据当正文'),metricCard(result.production_validation?.metrics?.task_latency_p95_ms!=null?'P95 完整任务':'P95 请求完成',msText(reliability.latency_p95_ms),`实际观察 ${envelope.observation_seconds==null?'未记录':duration(envelope.observation_seconds)}`));panel.append(stats);
 const latencyNote=result.production_validation?.metrics?.task_latency_population||result.production_validation?.metrics?.latency_population;if(latencyNote)panel.append(make('p','admission-footnote',latencyNote));
 const confidence=reliability.confidence;if(confidence&&confidence.lower_percent!=null&&confidence.upper_percent!=null)panel.append(make('p','admission-confidence',`成功率 ${(confidence.level||0.95)*100}% 置信区间：${confidence.lower_percent}% – ${confidence.upper_percent}%（${confidence.method||'统计区间'}，${confidence.n??'—'} 个样本）。${confidence.note||'少量请求全部成功，不等于长期稳定性得到证明。'}`));
 const reasons=Array.isArray(admission.reasons)?admission.reasons:[];if(reasons.length){const list=make('ul','admission-reasons');for(const reason of reasons.slice(0,6))list.append(make('li','',String(reason)));panel.append(list);}
 const gates=make('details','admission-details');gates.open=true;gates.append(make('summary','',`准入门槛 · ${(admission.gates||[]).filter(g=>g.status==='passed').length} / ${(admission.gates||[]).length} 项满足`));const gateList=make('div','admission-gates');for(const gate of admission.gates||[]){const row=make('div','admission-gate'),title=make('div','admission-gate-title');title.append(make('b','',gate.label||gate.id),make('span','admission-gate-status '+gate.status,({passed:'已满足',failed:'需改进',inconclusive:'待验证'})[gate.status]||'待验证'));row.append(title,make('p','',`实测：${metricText(gate.observed)} · 要求：${metricText(gate.required)}`));if(gate.detail)row.append(make('small','',gate.detail));gateList.append(row);}gates.append(gateList);panel.append(gates);
 const workloads=envelope.workloads||result.production_validation?.metrics?.workloads||{};
 if(Object.keys(workloads).length){const section=make('details','admission-details');section.append(make('summary','','实际验证的业务范围'));const grid=make('div','admission-workload-grid');for(const [key,work] of Object.entries(workloads)){const count=work.tasks??work.normal_tasks,successful=work.business_successful??work.successful;const card=make('div','admission-workload');card.append(make('b','',workloadLabels[key]||key),make('span','',`${metricText(successful)} / ${metricText(count)} 个业务任务完成`));if(work.protocol_success_rate!=null)card.append(make('small','',`协议完整率 ${rateText(work.protocol_success_rate)}`));if(work.latency_p95_ms!=null)card.append(make('small','',`P95 ${msText(work.latency_p95_ms)}`));grid.append(card);}section.append(grid,make('p','admission-footnote',envelope.detail||'仅反映本轮实际运行的业务和负载，不把计划上限当作已经验证。'));panel.append(section);}
 const metrics=result.production_validation?.metrics||{},buckets=Array.isArray(metrics.time_buckets)?metrics.time_buckets:[],failures=Object.entries(metrics.failure_counts||{}).filter(([,count])=>count>0);
 if(buckets.length||failures.length||metrics.retry_attempts!=null){const stability=make('details','admission-details');stability.append(make('summary','','时段稳定性、错误与恢复'));if(metrics.retry_attempts!=null)stability.append(make('p','admission-footnote',`实际重试 ${metrics.retry_attempts} 次 · ${metrics.tasks_retried??0} 个业务任务触发重试 · ${metrics.recovered_tasks??0} 个任务恢复成功${metrics.max_content_gap_ms!=null?' · 最大有效内容间隔 '+msText(metrics.max_content_gap_ms):''}。未观察到暂态故障时，恢复能力仍需补充证据。`));if(failures.length){const labels={authentication:'鉴权或账户',rate_limited:'429 限流',upstream_5xx:'上游 5xx',parameter_or_capability:'参数或能力拒绝',timeout:'超时',response_protocol:'响应协议不完整',business_assertion:'业务断言异常'},list=make('div','admission-failure-tags');for(const [kind,count] of failures)list.append(make('span','',(labels[kind]||kind)+' · '+count+' 次'));stability.append(list);}if(buckets.length){const grid=make('div','admission-workload-grid');for(const [index,bucket] of buckets.entries()){const item=make('div','admission-workload');item.append(make('b','',`时段 ${index+1} · +${duration(bucket.offset_seconds||0)}`),make('span','',`${bucket.business_successful??'—'} / ${bucket.normal_tasks??bucket.tasks??'—'} 个业务任务`),make('small','',`业务成功率 ${rateText(bucket.business_success_rate)}`));grid.append(item);}stability.append(grid);}panel.append(stability);}
 const costSection=make('div','admission-cost'),costHead=make('div','admission-cost-head');costHead.append(make('h4','','成功交付成本'),make('span','',cost.label||'成本待核算'));costSection.append(costHead,make('p','admission-footnote',`报价版本：${cost.price_version||'未记录'} · 币种：${cost.currency||'未配置'}`));
 const money=value=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumSignificantDigits:10})+' '+(cost.currency||''):'待核算';
 const coverage=item=>item&&typeof item==='object'?`${item.covered??0} / ${item.total??0} 次请求`:'未提供';
 const costStats=make('div','admission-cost-stats');costStats.append(metricCard('全部请求估算费用',money(cost.estimated_total),`usage / 报价覆盖 ${coverage(cost.estimated_coverage)}`),metricCard('全部请求实际扣费',money(cost.actual_total),`账单覆盖 ${coverage(cost.actual_coverage)}`),metricCard('每个合格业务实际成本',money(cost.production?.actual_cost_per_success),'含正常业务失败与重试的费用'));costSection.append(costStats);
 if(cost.actual_total==null&&cost.actual_partial_total!=null)costSection.append(make('p','admission-footnote',`已核对的部分费用：${money(cost.actual_partial_total)}；其余请求账单尚缺，暂不计算全量成本。`));
 if(cost.estimated_total==null&&cost.estimated_partial_total!=null)costSection.append(make('p','admission-footnote',`可估算的部分费用：${money(cost.estimated_partial_total)}；缺失 usage 或单价的请求未当作免费。`));
 costSection.append(make('p','admission-footnote',cost.production?.detail||'实际扣费需导入账单；未知费用不计作 0，估算费用不等同已核对账单。'),make('p','admission-price-conclusion',(cost.comparison?.status==='lower'?'已核算成本低于可比参考：':cost.comparison?.status==='higher'?'已核算成本高于可比参考：':'价格优势待核对：')+(cost.comparison?.detail||'需要同币种、同业务配比的实际账单对照。')));
 if(data.status!=='running'){
  const actions=make('div','admission-ledger-actions'),download=make('button','secondary','下载账单模板'),upload=make('button','secondary','导入实际账单'),input=make('input');download.type=upload.type='button';download.dataset.costTemplate='';upload.dataset.costImport='';input.type='file';input.accept='.json,application/json';input.hidden=true;input.dataset.costFile='';const state=make('small','admission-ledger-status');state.setAttribute('role','status');state.textContent='每次导入会替换本轮对账记录；只填写已核对金额，空金额保留未核算。';
  download.addEventListener('click',async()=>{download.disabled=true;try{const value=await(await api('/api/runs/'+encodeURIComponent(id)+'/cost-template')).json();const rows=Array.isArray(value)?value:value.rows||value.billing?.rows||[];const blob=new Blob([JSON.stringify({rows},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=make('a');a.href=url;a.download=`账单模板-${filenamePart(data.model||result.configuration?.model)}-${reportStamp(data)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);state.textContent='模板金额留空；只填写可核对的实际扣费。无需提供任何 API 密钥。';}catch(error){state.textContent='下载失败：'+error.message;}finally{download.disabled=false;}});
  upload.addEventListener('click',()=>input.click());input.addEventListener('change',async()=>{const file=input.files?.[0];if(!file)return;upload.disabled=true;state.textContent='正在核对实际账单…';try{if(file.size>2*1024*1024)throw new Error('账单文件不得超过 2 MB');const source=JSON.parse(await file.text()),billing=Array.isArray(source)?{rows:source}:source.billing||source;if(!billing||!Array.isArray(billing.rows))throw new Error('账单需要 rows 数组');const seen=new Set();const rows=billing.rows.filter(row=>row?.amount!==null&&row?.amount!==''&&row?.amount!==undefined).map(row=>{if(typeof row.request_id!=='string'||!row.request_id||seen.has(row.request_id))throw new Error('请求 ID 缺失或重复');seen.add(row.request_id);if(typeof row.amount!=='number'||!Number.isFinite(row.amount)||row.amount<0||!['CNY','USD'].includes(row.currency))throw new Error('每笔费用需要非负数字 amount 与 CNY / USD 币种');return {request_id:row.request_id,amount:row.amount,currency:row.currency,...(typeof row.note==='string'?{note:row.note}:{}),...(typeof row.provider_request_id==='string'?{provider_request_id:row.provider_request_id}:{})};});if(!rows.length)throw new Error('没有已填写金额的账单行，空金额不会作为免费提交');const updated=await(await api('/api/runs/'+encodeURIComponent(id)+'/billing',{method:'POST',body:JSON.stringify({billing:{rows,...(typeof billing.note==='string'?{note:billing.note}:{})}})})).json();const next={...data,result:updated.result||{...result,admission:updated.admission||admission},history_saved:updated.history_saved??data.history_saved,history_id:updated.history_id??data.history_id};const owner=data.suite==='claude'?'claude':data.suite==='ccmax'?'ccmax':'kimi';suiteRuns.set(owner,{id,data:next});if(displayedRunId===id)render(next,id);if(next.history_saved)window.dispatchEvent(new CustomEvent('workbench:history-saved',{detail:{id:next.history_id,kind:owner}}));message(`已核对 ${rows.length} 笔实际费用，报告成本结论已更新。`);}catch(error){state.textContent='账单未更新：'+error.message;}finally{upload.disabled=false;input.value='';}});
  actions.append(download,upload,input);costSection.append(actions,state);
 }
 panel.append(costSection);
}
function filenamePart(value){return String(value||'未命名模型').trim().replace(/[\\/:*?"<>|\u0000-\u001f]+/g,'-').replace(/\s+/g,' ').slice(0,80)||'未命名模型';}
function reportStamp(data){const raw=Number(data?.finished_at||data?.started_at||Date.now()/1000);const date=new Date((raw<1e12?raw*1000:raw));const pad=value=>String(value).padStart(2,'0');return `${date.getFullYear()}${pad(date.getMonth()+1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;}
function reportDownloadName(data,format){const result=data?.result||{},model=result.configuration?.model||data?.model||'未命名模型',base=`测试报告-${filenamePart(model)}-${reportStamp(data)}`;return format==='evidence.zip'?base+'-证据.zip':base+'.'+format.split('.').pop();}
function render(data,id){
 displayedRunId=id;el('acceptanceProgress').hidden=false;
 const done=data.completed||0,total=data.total||0,pct=total?Math.min(100,done/total*100):0;
 el('acceptanceStage').textContent=`${data.suite==='ccmax'?'CCMax':data.suite==='claude'?'Claude 专项':data.suite==='kvv11'?'KVV 11 项预检':'KVV 全套验证'} · ${statuses[data.status]||data.status}`;
 el('acceptanceCount').textContent=total?`${done} / ${total}`:`已完成 ${done}`;if(total){el('acceptanceBar').value=pct;}else el('acceptanceBar').removeAttribute('value');
 el('acceptanceElapsed').textContent='已用 '+duration(data.elapsed||0);
 el('acceptanceEta').textContent=data.status!=='running'?'本次运行已结束':done>=3&&total>done?'按已完成用例估计剩余约 '+duration((data.elapsed||0)/done*(total-done))+'，仅供参考':'预计剩余：等待足够样本';
 const result=data.result,summary=result?.summary||data.summary;
 const verdict=result?.verdict;el('acceptanceVerdict').hidden=!verdict;if(verdict){el('acceptanceVerdict').className='acceptance-verdict '+verdict.status;el('acceptanceVerdict').replaceChildren(make('b','',verdict.label),make('p','',verdict.detail));}
 el('acceptanceSummary').textContent=summary?`${data.suite==='ccmax'?'请求样本':'用例'}：通过 ${summary.passed||0} · 未通过 ${summary.failed||0} · 跳过 ${summary.skipped||0} · 未覆盖 ${summary.not_covered||0} · 无法判定 ${summary.inconclusive||0}`:'';
 const actualRequests=result?.transport?.request_count??data.request_count;if(actualRequests!==undefined)el('acceptanceSummary').textContent+=` · 实际 API 请求 ${actualRequests} 次`;
 if(result?.error)message(result.error,true);
 const current=data.batch&&data.status==='running'?data.current_run_snapshot:null;
 const currentModel=current?.model||'';
 const events=Array.isArray(current?.events)?current.events:(data.events||[]);
 if(currentModel)el('acceptanceSummary').textContent+=`${el('acceptanceSummary').textContent?' · ':''}当前模型：${currentModel}`;
 const productionEvent=[...events].reverse().find(event=>event.suite==='channel_production');if(data.status==='running'&&productionEvent)el('acceptanceEta').textContent=`业务观察：${productionEvent.completed??0} / ${productionEvent.total??'—'} 次实际请求 · ${productionEvent.logical_tasks??0} 个业务任务 · 已观察 ${duration(productionEvent.elapsed_seconds||0)}`;
 const observedCases=latestEventCases(events).map(item=>currentModel?{...item,title:currentModel+' · '+(item.title||item.label||item.name||item.id||item.probe||'测试项'),model:currentModel}:item);
 const cases=result?.results?result.results.map(item=>({id:'batch-'+item.model,label:item.model+' · '+(item.status||'未完成'),status:item.status==='passed'?'passed':item.status==='failed'?'failed':'inconclusive',detail:item.result?.verdict?.detail||'该模型独立子任务已保存，可下载总报告查看逐项证据。'})):(result?[...(result.cases||result.checks||[]),...(result.matrix_validation?.cases||[]),...(result.production_validation?.cases||[])]:observedCases);
 const transportCases=(result?.transport?.checks||[]).filter(x=>x.status!=='passed');
 const matrixCases=[...cases.slice(-700),...transportCases];
 const matrix=el('acceptanceCases');matrix.replaceChildren();
 if(matrixCases.length){
  const head=make('div','acceptance-matrix-head');head.append(make('span','','检测项'),make('span','','期望'),make('span','','实际结果'),make('span','','状态'));matrix.append(head,...matrixCases.map(renderCase));
 }
 renderAdmission(data,id);
 renderHistoryState(data,id);
 el('acceptanceLog').textContent=result?.log||events.slice(-20).map(e=>(currentModel?'['+currentModel+'] ':'')+(e.message||e.case?.id||`${e.completed??''}${e.total?' / '+e.total:''}`)).join('\n');
 syncDownloads();
}
function renderHistoryState(data,id){
 let box=el('acceptanceHistorySaveStatus');if(!box){box=make('div','history-save-status');box.id='acceptanceHistorySaveStatus';el('acceptanceProgress').append(box);}box.replaceChildren();box.hidden=data.status==='running'||(!data.history_saved&&!data.history_error);
 if(box.hidden)return;box.setAttribute('role','status');box.className='history-save-status is-'+(data.history_saved?'saved':'error');box.append(make('span','',data.history_saved?'已保存到历史记录':'历史记录未保存，请重试'));
 if(!data.history_saved){const retry=make('button','text-button','重试保存');retry.type='button';retry.addEventListener('click',async()=>{retry.disabled=true;try{await api('/api/runs/'+id+'/history',{method:'POST',body:'{}'});const refreshed=await(await api('/api/runs/'+id)).json();if(id===runId)recordRun(refreshed,id);else{const suite=refreshed.suite==='ccmax'?'ccmax':refreshed.suite==='claude'?'claude':'kimi';if(suiteRuns.get(suite)?.id===id)suiteRuns.set(suite,{id,data:refreshed});if(displayedRunId===id)render(refreshed,id);if(refreshed.history_saved)window.dispatchEvent(new CustomEvent('workbench:history-saved',{detail:{id:refreshed.history_id,kind:suite}}));}}catch(e){box.firstChild.textContent='历史记录未保存：'+e.message;retry.disabled=false;}});box.append(retry);}
}
async function poll(){
 if(!runId)return;
 const id=runId,generation=pollGeneration;
 try{const data=await (await api('/api/runs/'+id)).json();if(id!==runId||generation!==pollGeneration)return;serviceReady=!!token;serviceState='connected';updateServiceBadge();recordRun(data,id);if(data.status==='running')pollTimer=setTimeout(poll,1000);}
 catch(e){if(id!==runId||generation!==pollGeneration)return;if(!e.status){serviceReady=false;serviceState='disconnected';updateServiceBadge();setActive(active);}message('状态获取失败：'+e.message+'。任务可能仍在后台运行，可刷新页面重新连接。',true);pollTimer=setTimeout(poll,4000);}
}
async function start(){
 if(restoring)return;
 try{validateProductionConfiguration();}catch(error){message(error.message,true);return;}
 const c=config();if(!c.base||!c.key||!c.model){message('请填写渠道地址、API Key，并至少勾选一个模型。',true);return;}
 if(!serviceReady){message('请先双击「启动验收工作台.command」并打开本地工作台。',true);return;}
 runningSuite=selected;setActive(true);message('');clearTimeout(pollTimer);pollGeneration++;hideResults();
 try{const data=await (await api('/api/runs',{method:'POST',body:JSON.stringify(c)})).json();runId=data.id;recordRun({suite:c.suite,status:'running',batch:(c.models||[]).length>1,models:c.models||[]},runId);await poll();}
 catch(e){setActive(false);showSuiteResult();message(e.message,true);}
}
async function connect(){
 if(location.protocol==='file:'){serviceState='file';updateServiceBadge();el('acceptanceLocalHelp').hidden=false;setActive(false);return;}
 serviceState='connecting';updateServiceBadge();setActive(false);
 let data;
 try{
  data=await (await api('/api/session')).json();token=typeof data.token==='string'?data.token:'';
  if(!token)throw new Error('本地验收会话不可用');
  // Each desktop workspace owns one run. A fresh workspace must never claim
  // another channel's active job or reopen an unrelated completed report.
  if(new URLSearchParams(location.search).has('desktop_workspace')){
   data.active=sessionStorage.getItem('desktopAcceptanceRun')||new URLSearchParams(location.search).get('desktop_run');data.latest=null;
  }
  serviceReady=true;serviceState='connected';kvvRevision=typeof data.kvv_revision==='string'?data.kvv_revision:'';restoring=!!(data.active||data.latest);
  updateServiceBadge();el('acceptanceLocalHelp').hidden=true;setActive(false);
 }catch{
  token='';serviceReady=false;serviceState='disconnected';kvvRevision='';updateServiceBadge();el('acceptanceLocalHelp').hidden=false;setActive(false);return;
 }
 if(data.active||data.latest){
  runId=data.active||data.latest;
  try{
   const job=await (await api('/api/runs/'+runId)).json();el('acceptanceBase').value=job.base||'';if(job.models?.length)acceptanceModelPicker?.setSelected(job.models,{emit:false});else if(job.model)acceptanceModelPicker?.setSelected([job.model],{emit:false});restoreConfiguration(job);
   recordRun(job,runId);if(job.suite!=='ccmax'&&job.suite!=='claude')el('acceptanceScope').value=job.suite;selectSuite(runningSuite);
   if(typeof setTextMode==='function')setTextMode('deep');await poll();
  }catch(e){
   if(!e.status){serviceReady=false;serviceState='disconnected';updateServiceBadge();setActive(false);}
   message('任务恢复失败：'+e.message+'。可刷新页面重试，已有报告仍保存在本地。',true);
  }finally{restoring=false;setActive(active);}
 }
}
el('claudeProductionProfile').addEventListener('change',()=>{const preset=productionPresets[el('claudeProductionProfile').value];if(preset){for(const [key,value] of Object.entries(preset))el(productionFields[key]).value=value;el('claudeProductionRpm').value='0';}updateProductionSettings();});
for(const id of Object.values(productionFields))el(id).addEventListener('input',()=>{el('claudeProductionProfile').value='custom';updateProductionSettings();});
el('claudeProductionCases').addEventListener('input',updateCustomCaseStatus);
el('claudeProductionImport').addEventListener('click',()=>el('claudeProductionFile').click());
el('claudeProductionFile').addEventListener('change',async()=>{const file=el('claudeProductionFile').files?.[0];if(!file)return;try{if(file.size>1024*1024)throw new Error('业务样本文件不得超过 1 MB');const text=await file.text();el('claudeProductionCases').value=text;const cases=customProductionCases(true);el('claudeProductionCases').value=JSON.stringify(cases,null,2);document.querySelector('[data-production-workload="custom"]').checked=!!cases.length;updateCustomCaseStatus();}catch(error){el('claudeProductionCasesStatus').textContent=error.message;el('claudeProductionCasesStatus').classList.add('error');}finally{el('claudeProductionFile').value='';}});
for(const b of document.querySelectorAll('[data-suite]'))b.addEventListener('click',()=>selectSuite(b.dataset.suite));
el('acceptanceScope').addEventListener('change',updatePlan);
for(const id of ['claudeProvider','claudeFormat','claudeAuth','claudeSampling','claudeSignature','claudeSse','claudeCacheTokens','claudeStressRequests','claudeStressConcurrency'])el(id)?.addEventListener('change',()=>{if(id==='claudeSampling'){const mode=el(id).value;if(mode!=='custom')el('claudeCacheTokens').value=12000;if(mode==='quick'){el('claudeSignature').value=1;el('claudeSse').value=3;el('claudeStressRequests').value=1;el('claudeStressConcurrency').value=1;}else if(mode==='professional'){el('claudeSignature').value=3;el('claudeSse').value=5;el('claudeStressRequests').value=20;el('claudeStressConcurrency').value=4;}else if(mode==='stress'){el('claudeSignature').value=2;el('claudeSse').value=10;el('claudeStressRequests').value=100;el('claudeStressConcurrency').value=10;}}updatePlan();});
for(const id of ['claudeSignature','claudeSse','claudeCacheTokens','claudeStressRequests','claudeStressConcurrency'])el(id).addEventListener('input',()=>{el('claudeSampling').value='custom';updatePlan();});
el('acceptanceThinkMode').addEventListener('change',updatePlan);
el('acceptanceFormat').addEventListener('change',()=>{if(el('acceptanceFormat').value==='openai')el('acceptanceAuth').value='bearer';invalidateModels();updatePlan();});
el('acceptanceSampling').addEventListener('change',()=>{const mode=el('acceptanceSampling').value;if(mode!=='custom'){el('acceptanceSignature').value=mode==='batch'?5:1;el('acceptanceSse').value=mode==='batch'?50:3;}updatePlan();});
for(const id of ['acceptanceSignature','acceptanceSse'])el(id).addEventListener('input',()=>{el('acceptanceSampling').value='custom';updatePlan();});
async function previewClaudePlan(matrixOnly=false,sourceId=''){
 const button=el(sourceId||(matrixOnly?'acceptanceMatrixPreview':'claudePlanPreview'));if((!matrixOnly&&selected!=='claude')||button.disabled)return;
 if(!serviceReady){message('请求预览需要验收服务。请启动或恢复当前工作台服务后重试。',true);return;}
 try{validateProductionConfiguration();}catch(error){message(error.message,true);return;}
 const {key:discardedKey,...payload}=config();
 button.disabled=true;button.textContent='正在生成请求预览…';message('');
 try{
  const data=await(await api(matrixOnly?'/api/acceptance/plan':'/api/claude/plan',{method:'POST',body:JSON.stringify(payload)})).json();
  const requests=Array.isArray(data.requests)?data.requests:[];
  const summary=el('claudePlanSummary');summary.replaceChildren();
  if(data.matrix_only)summary.append(make('p','','以下计划不包含原专项，附加矩阵与业务验证分别列出；不会因预览产生模型调用。'));
  const tokenEstimate=data.token_estimate,tokenText=tokenEstimate&&typeof tokenEstimate==='object'?`${tokenEstimate.cache_total_target_input_tokens??'未提供'} 输入 token（缓存请求合计目标）`:tokenEstimate??'未提供';
  for(const [label,value] of [[data.request_count_is_maximum?'请求数上限':'计划请求',data.request_count??'按前置结果确定'],['Token 估算',tokenText],['当前模型',payload.model||'模板模型']]){const item=make('div','claude-plan-stat');item.append(make('small','',label),make('strong','',String(value)));summary.append(item);}
  if(tokenEstimate&&typeof tokenEstimate==='object'){
   summary.append(make('p','',`缓存长前缀目标 ${tokenEstimate.cache_prefix_target_tokens??'—'} token × ${tokenEstimate.cache_requests??'—'} 次；${tokenEstimate.basis||tokenEstimate.note||'实际消耗以上游 usage 为准。'}`));
   if(tokenEstimate.output_token_limit_sum!=null)summary.append(make('p','',`输出额度上限合计 ${tokenEstimate.output_token_limit_sum} token；这是请求预算之和，实际生成量通常更少，也可能包含思考 Token。`));
  }
  if(Array.isArray(data.pressure_stages)&&data.pressure_stages.length)summary.append(make('p','',`阶梯压测：${data.pressure_stages.map(x=>`并发 ${x.concurrency} × ${x.requests} 请求`).join(' → ')}；每阶段完成后再升级，不自动重试。`));
  if(data.request_count_is_maximum)summary.append(make('p','',`包含 ${data.conditional_requests??0} 个条件请求；只有前置响应提供所需工具结果或签名时才会执行，实际数量以进度和报告为准。`));
  if((payload.models||[]).length>1)summary.append(make('p','',`已选 ${payload.models.length} 个模型；以下为当前模型请求模板，批量测试会逐个替换模型 ID。`));
  const productionPlan=data.production||data.production_plan;if(productionPlan?.enabled){const p=productionPlan.configuration||payload.production;summary.append(make('p','production-plan-highlight',`追加业务验证：${p.profile} · 请求上限 ${p.max_requests}（含重试与多轮工具）· 观察 ${duration(p.duration_seconds||0)} · 并发上限 ${p.concurrency}。与原专项、参数矩阵分开计数。`));if(productionPlan.estimated_tokens!=null)summary.append(make('p','',`业务 Token 调度估算：${metricText(productionPlan.estimated_tokens)}；估算预算并非实际账单硬上限。`));}
  const limitations=el('claudePlanLimitations');limitations.replaceChildren();
  const notes=Array.isArray(data.limitations)?data.limitations:data.limitations?[data.limitations]:[];
  limitations.hidden=!notes.length;if(notes.length){limitations.append(make('b','','执行边界'));const list=make('ul');for(const note of notes)list.append(make('li','',typeof note==='string'?note:JSON.stringify(note)));limitations.append(list);}
  const list=el('claudePlanRequests');list.replaceChildren();const groups=new Map();
  for(const request of requests){const module=String(request.module||'protocol');if(!groups.has(module))groups.set(module,[]);groups.get(module).push(request);}
  for(const [module,items] of groups){const section=make('section','claude-plan-module'),definition=acceptanceModules.claude.find(item=>item.id===module);section.append(make('h3','',({multimodal:'多模态识别与多图对照'}[module]||definition?.title||module)));for(const request of items){const details=make('details','claude-plan-request'),head=make('summary');head.append(make('strong','',request.title||request.id||'测试请求'),make('span','',`${request.method||'POST'}${request.repeat?' × '+request.repeat:''}${request.conditional?' · 条件请求':''}`));details.append(head,make('p','claude-plan-endpoint',request.url||'使用当前渠道端点'));if(request.notes){const text=Array.isArray(request.notes)?request.notes.join('；'):String(request.notes);details.append(make('p','claude-plan-request-note',text));}const code=make('pre','',JSON.stringify(request.body??{},null,2));code.setAttribute('aria-label',(request.title||request.id||'测试')+' 请求体');details.append(code);section.append(details);}list.append(section);}
  if(productionPlan?.enabled&&Array.isArray(productionPlan.workloads)){const section=make('section','claude-plan-module');section.append(make('h3','','业务验证请求模板'));for(const workload of productionPlan.workloads){const row=make('details','claude-plan-request');row.append(make('summary','',workload.label||workloadLabels[workload.id]||workload.id),make('p','claude-plan-request-note',`每个任务包含 ${workload.requests_per_task??'按交互决定'} 次请求。${metricText(workload.expected||'按完整业务结果判断。')}`),make('pre','',JSON.stringify(workload.example_request||workload.request||workload.example||{},null,2)));section.append(row);}list.append(section);}
  if(!requests.length)list.append(make('p','','当前选择未生成可执行请求。请检查已启用模块和接口格式。'));
  el('claudePlanDialog').showModal();
 }catch(error){message('请求预览失败：'+error.message,true);}finally{button.disabled=false;button.textContent=sourceId==='claudeProductionPreview'?'预览业务与矩阵计划':matrixOnly?'预览矩阵请求与用量':'查看测试请求';}
}
el('claudePlanPreview').addEventListener('click',()=>previewClaudePlan(false));
el('claudeProductionPreview').addEventListener('click',()=>previewClaudePlan(true,'claudeProductionPreview'));
el('acceptanceMatrixPreview').addEventListener('click',()=>previewClaudePlan(true));
el('acceptanceMatrixProfile').addEventListener('change',updatePlan);
el('claudePlanClose').addEventListener('click',()=>el('claudePlanDialog').close());
el('claudePlanDialog').addEventListener('click',event=>{if(event.target===el('claudePlanDialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
el('acceptanceRun').addEventListener('click',start);
el('acceptanceStop').addEventListener('click',async()=>{if(!runId)return;el('acceptanceStop').disabled=true;try{await api('/api/runs/'+runId+'/cancel',{method:'POST',body:'{}'});message('已请求取消，正在关闭后台请求并整理已完成结果。');}catch(e){message(e.message,true);el('acceptanceStop').disabled=false;}});
for(const button of document.querySelectorAll('[data-acceptance-download]'))button.addEventListener('click',async()=>{const saved=suiteRuns.get(selected),id=displayedRunId;if(!saved||saved.id!==id||!saved.data.result)return;button.disabled=true;try{const r=await api('/api/runs/'+id+'/'+button.dataset.acceptanceDownload);const blob=await r.blob(),url=URL.createObjectURL(blob),a=make('a');a.href=url;a.download=reportDownloadName(saved.data,button.dataset.acceptanceDownload);a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(e){message(e.message,true);}finally{syncDownloads();}});
async function loadModels(){
 if(active)return;
 const c=config();if(!c.base||!c.key){message('请先填写渠道地址和 API Key。',true);return;}
 const epoch=++modelFetchEpoch;modelController?.abort();modelController=new AbortController();el('acceptanceModels').disabled=true;el('acceptanceModelHint').textContent='正在获取模型列表（最多 30 秒）…';window.ModelDiscovery.showDetails(el('acceptanceModelHint'),null);
 try{const data=await window.ModelDiscovery.list({base:c.base,key:c.key,auth:(selected==='ccmax'||selected==='claude')?c.auth:'bearer'},{signal:modelController.signal});if(epoch!==modelFetchEpoch)return;modelCatalog=data.models;if(acceptanceModelPicker)acceptanceModelPicker.search.value='';acceptanceModelPicker?.setOptions(modelCatalog);acceptanceModelPicker?.open({focus:true});el('acceptanceModelHint').textContent=`已获取 ${modelCatalog.length} 个模型，可勾选多个后按顺序测试；未列出的映射模型可手动添加。`;window.ModelDiscovery.showDetails(el('acceptanceModelHint'),data);message('');if(!serviceReady&&data.transport==='service')connect();}
 catch(e){if(epoch===modelFetchEpoch){el('acceptanceModelHint').textContent='获取失败：'+e.message+'；仍可手动填写模型 ID。';window.ModelDiscovery.showDetails(el('acceptanceModelHint'),e);message(e.message,true);}}
 finally{if(epoch===modelFetchEpoch){el('acceptanceModels').disabled=false;modelController=null;}}
}
function invalidateModels(){modelFetchEpoch++;modelController?.abort();modelController=null;modelCatalog=[];window.ModelDiscovery?.showDetails(el('acceptanceModelHint'),null);/* retain manually entered/selected IDs while invalidating the catalog */acceptanceModelPicker?.setOptions([],{reconcile:false});acceptanceModelPicker?.close();el('acceptanceModels').disabled=false;el('acceptanceModelHint').textContent='渠道配置已变化，请重新获取模型列表；支持手动添加。';}
for(const id of ['acceptanceBase','acceptanceKey','acceptanceAuth','claudeAuth','claudeFormat'])el(id).addEventListener('input',invalidateModels);
el('acceptanceModels').addEventListener('click',loadModels);
acceptanceModelPicker=window.ModelMultiselect?.attach(el('acceptanceModel'),{label:'渠道模型',max:30,ids:{wrapper:'acceptanceModelPicker',menu:'acceptanceModelMenu',search:'acceptanceModelSearch',list:'acceptanceModelList',status:'acceptanceModelStatus',toggle:'acceptanceModelToggle',clear:'acceptanceModelClear',all:'acceptanceModelShowAll'},onChange:(ids)=>{el('acceptanceModelHint').textContent=ids.length>1?`已选 ${ids.length} 个模型，将按顺序逐个测试。`:'已选 1 个模型，可继续勾选或开始测试。';}});
productionDefaults=productionConfiguration();
updateProductionSettings();
connect();
})();
