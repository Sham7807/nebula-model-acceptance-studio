(() => {
  'use strict';
  const assets = window.__nebulaAssets;
  if (!assets || location.origin !== assets.origin || !['/', '/index.html', '/legacy.html'].includes(location.pathname)) return;
  const style = document.createElement('style'); style.textContent = assets.css; document.head.append(style);
  document.documentElement.classList.add('nebula-desktop');
  // Only our top-level workbench owns native actions. Generated HTML stays isolated.
  if (window !== window.top || !document.getElementById('workbench')) return;
  const $ = id => document.getElementById(id);
  const post = body => window.webkit?.messageHandlers?.nebula?.postMessage(body);
  let profile = null, route = 'text', activeRoute = 'text', previousState = '', run = null, didRun = false, stopping = false, wasBusy = false;
  const legacyDocument = () => { try { const frame = $('legacyFrame'); return frame?.contentWindow?.location.pathname === '/legacy.html' ? frame.contentDocument : null; } catch { return null; } };
  const isBusy = () => run?.status === 'running' || $('configFields')?.disabled || ['stopBtn','acceptanceStop','gptStop'].some(id => $(id) && !$(id).disabled) || !!legacyDocument()?.getElementById('btnStop') && !legacyDocument().getElementById('btnStop').disabled;
  const fill = (doc,id,value) => {
    const field = doc?.getElementById(id); if (!field || field.disabled || field.value === value) return;
    field.value = value; field.dispatchEvent(new Event('input',{bubbles:true})); field.dispatchEvent(new Event('change',{bubbles:true}));
  };
  const configureLegacy = () => { if (!profile || isBusy()) return; const doc = legacyDocument(); for (const [id,key] of [['inBase','base'],['inKey','key'],['inModel','model']]) fill(doc,id,profile[key]); };
  const applyProfile = () => {
    if (!profile || isBusy()) return;
    for (const prefix of ['', 'acceptance', 'gpt']) for (const key of ['base','key','model']) fill(document,prefix ? prefix + key[0].toUpperCase()+key.slice(1) : key,profile[key]);
    configureLegacy();
  };
  const observeControls = doc => {
    if(!doc)return;
    new MutationObserver(reportState).observe(doc,{subtree:true,attributes:true,attributeFilter:['disabled']});
    doc.addEventListener('click',event=>{if(event.target.closest?.('#stopBtn,#acceptanceStop,#gptStop,#btnStop'))stopping=true;},true);
  };
  $('legacyFrame')?.addEventListener('load',()=>{configureLegacy();observeControls(legacyDocument());});
  observeControls(document);
  window.NebulaDesktop = Object.freeze({
    navigate({route:next}) {
      const allowed = ['text','image','video','audio','general','ccmax','claude','kimi','gpt','history'];
      if (!allowed.includes(next)) return;
      if (isBusy() && next !== 'history' && next !== activeRoute) { post({type:'notice',message:'当前测试仍在运行，请先停止测试再切换工作区。'}); return; }
      if (next === 'history') { $('historyTab').click(); }
      else {
        $('workspaceTab').click();
        if (isBusy()) { route = next; reportState(); return; }
        if (['text','image','video','audio'].includes(next)) {
          document.querySelector(`.kind-tab[data-kind="${next}"]`)?.click();
          if (next === 'text') $('basicBtn').click();
        } else {
          document.querySelector('.kind-tab[data-kind="text"]')?.click(); $('legacyBtn').click();
          document.querySelector(`[data-suite="${next}"]`)?.click();
        }
      }
      route = next; if(next !== 'history') activeRoute = next;
      // Fill defaults only when the workspace has no connection. Never replace a user's edits on navigation.
      if (profile && !$('base').value) applyProfile();
      window.scrollTo({top:0}); reportState();
    },
    configure(value) { profile={base:String(value.base||''),model:String(value.model||''),key:String(value.key||'')}; applyProfile(); },
    async openHistory({id}) {
      $('historyTab').click(); await window.WorkbenchHistory?.refresh();
      // IDs are compared as data, never interpolated into selectors or evaluated.
      const entry = [...document.querySelectorAll('[data-history-id]')].find(el => el.dataset.historyId === id);
      entry?.click();
    },
    stop() {
      stopping = true;
      for (const id of ['stopBtn','acceptanceStop','gptStop']) if ($(id) && !$(id).disabled) $(id).click();
      const stop = legacyDocument()?.getElementById('btnStop'); if (stop && !stop.disabled) stop.click();
    }
  });
  const text = (doc,id) => doc?.getElementById(id)?.textContent?.trim() || '';
  function reportState() {
    const busy=isBusy(), specialist=['ccmax','claude','kimi'].includes(activeRoute), general=activeRoute==='general', gpt=activeRoute==='gpt', doc=general?legacyDocument():document;
    if(busy&&!wasBusy){didRun=true;stopping=false;}
    wasBusy=busy;
    const base=doc?.getElementById(general?'inBase':specialist?'acceptanceBase':gpt?'gptBase':'base')?.value || '';
    let channel='';try{channel=new URL(base).host;}catch{}
    const model=doc?.getElementById(general?'inModel':specialist?'acceptanceModel':gpt?'gptModel':'model')?.value || '';
    const status=busy?'running':(specialist&&run?run.status:didRun?(stopping?'cancelled':'finished'):'draft');
    let fraction=null,progress=busy?'正在等待渠道响应':didRun?'已结束 · 打开查看测试结论':'准备好后即可开始测试';
    if(specialist&&run){
      progress=[text(document,'acceptanceStage'),text(document,'acceptanceCount')].filter(Boolean).join(' · ')||progress;
      if(Number(run.total)>0)fraction=Math.min(1,Number(run.completed||0)/Number(run.total));
    }else if(busy&&gpt)progress=text(document,'gptProgressText')||progress;
    else if(busy&&!general){progress=text(document,'progressStage')||progress;const v=$('progressBar')?.getAttribute('aria-valuenow');if(v!==null&&v!==undefined)fraction=Number(v)/100;}
    const state={type:'state',busy,route:activeRoute,status,model:String((specialist&&run?.model)||model).slice(0,240),channel,progress:progress.slice(0,300),fraction,runID:run?.id||null};
    const signature=JSON.stringify(state);if(signature!==previousState){previousState=signature;post(state);}
  }
  window.addEventListener('workbench:run-state',event=>{run=event.detail;didRun=true;reportState();});
  window.addEventListener('workbench:history-saved',()=>post({type:'history'}));
  setInterval(reportState,250);
  post({type:'ready'});reportState();
})();
