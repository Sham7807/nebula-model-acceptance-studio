(function () {
  'use strict';
  const $ = id => document.getElementById(id), labels = { text: '文本测试', image: '图像生成', video: '视频生成', audio: '音频测试', general: '通用检测', ccmax: 'CCMax 验收', claude: 'Claude 上游验收', kimi: 'Kimi KVV' }, symbols = { text: 'Aa', image: '▧', video: '▷', audio: '≋', general: '◎', ccmax: 'C', claude: 'C', kimi: 'K' };
  if (!$('historyView')) return;
  let token = '', available = false, authenticated = false, ready = false, selected = 'workspace', offset = 0, total = 0, limit = 20, requestSerial = 0, detailSerial = 0, listController = null, detailController = null, searchTimer = null, previousFocus = null, currentRecord = null, connecting = null;
  const stateNames = { passed: '通过', success: '成功', failed: '未通过', error: '请求失败', stopped: '已停止', cancelled: '已取消', pending: '待完成', running: '进行中', inconclusive: '待确认', skipped: '已跳过', completed: '已完成', warning: '需检查' };
  function make(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = String(text); if (className) node.className = className; return node; }
  function statusClass(status) { return ['passed', 'success'].includes(status) ? 'is-passed' : ['failed', 'error'].includes(status) ? 'is-failed' : ''; }
  function badge(status) { return make('span', stateNames[status] || status || '待确认', 'history-status ' + statusClass(status)); }
  function safeText(value) { if (value == null) return ''; return typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
  function date(value, full) { let ms = typeof value === 'number' ? (value < 1e12 ? value * 1000 : value) : value; const d = new Date(ms); return Number.isNaN(d.getTime()) ? '时间未知' : d.toLocaleString('zh-CN', full ? { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false } : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); }
  function duration(ms) { if (!Number.isFinite(Number(ms)) || ms == null) return '耗时未知'; const seconds = Number(ms) / 1000; return seconds < 60 ? seconds.toFixed(1) + ' 秒' : Math.floor(seconds / 60) + ' 分 ' + Math.round(seconds % 60) + ' 秒'; }
  function showSessionExpired() {
    available = false; token = ''; $('sessionNotice').hidden = false; $('sessionNotice').replaceChildren(make('span', '登录状态已过期，当前页面的测试内容仍保留。请重新登录后刷新历史记录。'));
    const link = make('a', '重新登录 ↗'); link.href = '/login?expired=1'; link.target = '_blank'; link.rel = 'noopener'; $('sessionNotice').append(link);
  }
  async function request(path, options = {}) {
    const controller = options.signal ? null : new AbortController(), timer = controller ? setTimeout(() => controller.abort(), 20000) : null;
    try {
      const response = await fetch(path, { ...options, signal: options.signal || controller.signal, cache: 'no-store', credentials: 'same-origin', headers: { 'X-Workbench-Token': token, ...options.headers } });
      if (response.status === 401) { showSessionExpired(); throw new Error('登录状态已过期，请重新登录。'); }
      if (!response.ok) { let message; try { message = (await response.json()).error; } catch (_) {} throw new Error(message || '请求失败（HTTP ' + response.status + '），请稍后重试。'); }
      return response;
    } finally { clearTimeout(timer); }
  }
  function listState(title, description, retry = false, symbol = '↺') {
    $('historyListState').hidden = false; $('historyListState').replaceChildren(make('span', symbol, 'history-empty-symbol'), make('h2', title), make('p', description));
    if (retry) { const button = make('button', '重新连接', 'secondary'); button.type = 'button'; button.addEventListener('click', async () => { await connect(true); if (available) loadList(); }); $('historyListState').append(button); }
  }
  function stats(data) { const values = data || {}; for (const [id, key] of [['historyTotal', 'total'], ['historyPassed', 'passed'], ['historyFailed', 'failed'], ['historyOther', 'other']]) $(id).textContent = Number(values[key] || 0).toLocaleString('zh-CN'); $('historyNavCount').textContent = Number(values.total || 0) > 999 ? '999+' : String(values.total || 0); $('historyNavCount').hidden = false; }
  async function connect(force = false) {
    if (location.protocol === 'file:') { ready = true; available = false; return; }
    if (connecting) return connecting;
    if (ready && !force) return;
    connecting = (async () => {
      try {
        const response = await request('/api/session'), data = await response.json();
        token = typeof data.token === 'string' ? data.token : ''; available = !!data.history_enabled && !!token; authenticated = !!data.auth_enabled; ready = true;
        $('accountMenu').hidden = !authenticated; $('accountName').textContent = data.username || '已登录'; $('sessionMode').textContent = authenticated ? '私享工作台' : available ? '服务已连接' : '本地会话';
        if (available) { $('sessionNotice').hidden = true; $('historySubtitle').textContent = '回看提示词、生成内容与检测证据。测试完成后自动保存到数据库。'; }
      } catch (error) { ready = true; available = false; }
      finally { connecting = null; }
    })();
    return connecting;
  }
  function showView(view) {
    selected = view; $('workbench').hidden = view !== 'workspace'; $('historyView').hidden = view !== 'history';
    for (const [id, active] of [['workspaceTab', view === 'workspace'], ['historyTab', view === 'history']]) { $(id).classList.toggle('active', active); if (active) $(id).setAttribute('aria-current', 'page'); else $(id).removeAttribute('aria-current'); }
    if (window.ChoicePickers) window.ChoicePickers.closeAll();
    if (view === 'history') loadList();
  }
  async function loadList() {
    const serial = ++requestSerial; if (listController) listController.abort(); listController = new AbortController(); const controller = listController;
    $('historyRefresh').disabled = true; $('historyList').setAttribute('aria-busy', 'true'); $('historyList').classList.add('history-loading');
    try {
      await connect(); if (serial !== requestSerial) return;
      if (!available) { $('historyList').replaceChildren(); $('historyPagination').hidden = true; listState(location.protocol === 'file:' ? '在服务模式下留住测试记录' : '历史记录暂未连接', location.protocol === 'file:' ? '当前为独立 HTML。请启动验收工作台服务，或打开部署后的工作台，使用数据库历史记录。基础测试可照常使用。' : '连接工作台服务后，可查看保存在数据库中的测试。当前测试配置与内容已保留。', location.protocol !== 'file:', '▤'); return; }
      if (!$('historyList').children.length) listState('正在读取历史记录', '稍等片刻，正在从工作台数据库加载。');
      const query = new URLSearchParams({ kind: $('historyKind').value, status: $('historyStatus').value, q: $('historySearch').value.trim(), offset: String(offset), limit: String(limit) });
      const timer = setTimeout(() => controller.abort(), 20000); let data;
      try { data = await (await request('/api/history?' + query, { signal: controller.signal })).json(); } finally { clearTimeout(timer); }
      if (serial !== requestSerial) return;
      const items = Array.isArray(data.items) ? data.items : []; total = Number(data.total || 0); stats(data.stats);
      if (!items.length && offset > 0 && total > 0) { offset = Math.max(0, Math.floor((total - 1) / limit) * limit); return loadList(); }
      $('historyList').replaceChildren(...items.map(recordCard)); $('historyListState').hidden = !!items.length;
      if (!items.length) { const filtered = $('historyKind').value || $('historyStatus').value || $('historySearch').value; listState(filtered ? '没有找到匹配的测试' : '你的测试档案，从这里开始', filtered ? '试试调整筛选条件，或换一个搜索关键词。' : '完成一次文本、图像、视频、音频或深度检测后，结果会自动保存。', false, filtered ? '⌕' : '▤'); }
      $('historyPagination').hidden = !total; $('historyRange').textContent = `第 ${total ? offset + 1 : 0}–${Math.min(offset + items.length, total)} 条 / 共 ${total} 条`;
      $('historyPrevious').disabled = offset === 0; $('historyNext').disabled = offset + items.length >= total;
    } catch (error) { if (serial !== requestSerial) return; $('historyList').replaceChildren(); $('historyPagination').hidden = true; listState('暂时未能读取记录', error.name === 'AbortError' ? '连接超时，请检查网络后重试。' : error.message, true, '!'); }
    finally { if (serial === requestSerial) { $('historyRefresh').disabled = false; $('historyList').setAttribute('aria-busy', 'false'); $('historyList').classList.remove('history-loading'); } }
  }
  function recordCard(record) {
    const button = make('button', null, 'history-record'); button.type = 'button'; button.dataset.kind = record.kind || ''; button.dataset.historyId = record.id;
    const main = make('span', null, 'history-record-main'), title = make('span', null, 'history-record-title');
    title.append(make('b', record.model || record.title || '未命名测试'), make('span', labels[record.kind] || record.kind || '测试', 'history-type-tag'));
    main.append(title, make('span', record.prompt || record.title || record.base || '查看测试详情与完整证据', 'history-record-prompt'));
    const meta = make('span', null, 'history-record-meta'); meta.append(make('span', date(record.created_at), 'history-record-time'), make('span', duration(record.duration_ms) + (record.media_count ? ' · ' + record.media_count + ' 份媒体' : '')));
    button.append(make('span', symbols[record.kind] || '◎', 'history-record-symbol'), main, meta, badge(record.status), make('span', '↗', 'history-record-arrow'));
    button.addEventListener('click', () => openDetail(record.id, button)); return button;
  }
  function section(title, node) { const s = make('section', null, 'history-detail-section'); s.append(make('h3', title), node); return s; }
  function paragraph(value, error) { return make('pre', safeText(value), 'history-detail-text' + (error ? ' is-error' : '')); }
  function detailMeta(record) { const list = make('dl', null, 'history-detail-meta'); const values = [['测试类型', labels[record.kind] || record.kind], ['测试结果', badge(record.status)], ['测试时间', date(record.created_at, true)], ['模型名称', record.model || '—'], ['总耗时', duration(record.duration_ms)], ['渠道地址', record.base || '—']]; for (const [key, value] of values) { const group = make('div'); group.append(make('dt', key)); const dd = make('dd'); if (value instanceof Node) dd.append(value); else dd.textContent = value || '—'; group.append(dd); list.append(group); } return list; }
  function safeMediaURL(value, kind, stored) {
    if (typeof value !== 'string' || !['image', 'video', 'audio'].includes(kind)) return null;
    try { const url = new URL(value, location.href); if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null; if (stored && (url.origin !== location.origin || !url.pathname.startsWith('/api/history/'))) return null; return url.href; } catch (_) { return null; }
  }
  function mediaView(media) {
    const grid = make('div', null, 'history-media-grid');
    for (const [index, item] of media.entries()) {
      const kind = item.type || item.kind, url = safeMediaURL(item.url, kind, !!item.stored), figure = make('figure', null, 'history-media');
      if (!url) { figure.append(make('p', '这份媒体没有可用的预览地址。', 'media-warning')); grid.append(figure); continue; }
      const mount = () => {
        const node = make(kind === 'image' ? 'img' : kind); if (kind === 'image') { node.alt = '测试生成的图片 ' + (index + 1); node.loading = 'lazy'; node.referrerPolicy = 'no-referrer'; } else { node.controls = true; node.preload = 'metadata'; if (kind === 'video') node.playsInline = true; }
        node.src = url; node.addEventListener('error', () => { if (!figure.querySelector('.media-warning')) figure.insertBefore(make('p', '媒体暂时无法加载，原链接可能已过期或限制访问。', 'media-warning'), figure.lastElementChild); }); figure.prepend(node);
      };
      const caption = make('figcaption'), link = make('a', '打开原媒体 ↗'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; caption.append(make('span', (kind === 'image' ? '图片' : kind === 'video' ? '视频' : '音频') + ' ' + (index + 1) + ' · ' + (item.stored ? '已存储' : '渠道链接')), link); figure.append(caption);
      if (item.stored) mount(); else { const button = make('button', '点击加载媒体预览', 'media-load'); button.type = 'button'; button.addEventListener('click', () => { button.remove(); mount(); }, { once: true }); figure.prepend(button); }
      grid.append(figure);
    } return grid;
  }
  function checkView(checks) { const list = make('div', null, 'history-detail-checks'); for (const item of checks) { const row = make('div', null, 'history-detail-check'), head = make('div'); head.append(make('b', item.title || item.label || item.name || item.id || '检查项'), badge(item.status || item.result?.status || (typeof item.result === 'string' ? item.result : 'inconclusive'))); row.append(head); const detail = item.detail || item.observed || item.message || item.judge || item.error || item.reason || (typeof item.result === 'object' ? item.result : ''); if (detail) row.append(make('p', safeText(detail))); if (Array.isArray(item.details) && item.details.length) { const details = make('details'); details.append(make('summary', `${item.details.length} 条采样证据`), paragraph(item.details)); row.append(details); } list.append(row); } return list; }
  function renderDetail(record) {
    currentRecord = record; const body = $('historyDetailBody'); body.replaceChildren(detailMeta(record)); $('historyDetailTitle').textContent = record.model || record.title || '测试详情';
    const result = record.result || {}, prompt = record.prompt || result.config?.prompt;
    if (prompt) body.append(section('测试提示词', paragraph(prompt)));
    const verdict = result.verdict; if (verdict) body.append(section('检测结论', paragraph([verdict.label, verdict.detail].filter(Boolean).join('\n'))));
    const error = record.error || result.error; if (error) body.append(section('问题与错误', paragraph(error, true)));
    const output = result.text || result.output_text || (typeof result.output === 'string' ? result.output : ''); if (output) body.append(section('模型输出', paragraph(output)));
    if (Array.isArray(record.media) && record.media.length) body.append(section('生成内容', mediaView(record.media)));
    const mediaNotes = result.media_notes || record.media_notes; if (mediaNotes && (!Array.isArray(mediaNotes) || mediaNotes.length)) body.append(section('媒体保存说明', paragraph(Array.isArray(mediaNotes) ? mediaNotes.join('\n') : mediaNotes)));
    const checks = Array.isArray(result.checks) ? result.checks : Array.isArray(result.cases) ? result.cases : []; if (checks.length) body.append(section('逐项测试结果', checkView(checks)));
    if (result.scores || result.total) body.append(section('检测评分', paragraph({ scores: result.scores, total: result.total })));
    if (result.log || result.logs) { const log = make('details', null, 'history-detail-raw'); log.append(make('summary', '查看测试日志'), make('pre', safeText(result.log || result.logs))); body.append(log); }
    const configuration = result.config || result.configuration || record.configuration; if (configuration && Object.keys(configuration).length) { const config = make('details', null, 'history-detail-raw'); config.append(make('summary', '查看本次测试配置'), make('pre', safeText(configuration))); body.append(config); }
    const downloadRow = make('div', null, 'history-detail-downloads'); const runId = record.run_id;
    const exports = runId ? [['report.html', '下载详细报告'], ['evidence.zip', '下载证据包'], ['report.json', 'JSON 数据']] : [['report.html', '下载详细报告'], ['report.json', '下载测试记录 JSON']];
    for (const [format, label] of exports) { const button = make('button', label, 'secondary'); button.type = 'button'; button.addEventListener('click', () => download(record, format, button)); downloadRow.append(button); } body.append(downloadRow);
    const raw = make('details', null, 'history-detail-raw'); raw.append(make('summary', '查看完整脱敏记录 JSON'), make('pre', JSON.stringify(record, null, 2))); body.append(raw);
    if (!prompt && !output && !error && !checks.length && !record.media?.length) body.insertBefore(section('记录说明', paragraph('本次测试已保存。请展开完整脱敏记录，查看渠道响应与具体参数。')), downloadRow);
  }
  async function openDetail(id, opener) {
    previousFocus = opener || document.activeElement; currentRecord = null; const serial = ++detailSerial; if (detailController) detailController.abort(); detailController = new AbortController();
    $('historyDetailTitle').textContent = '测试详情'; $('historyDetailBody').replaceChildren(make('p', '正在读取完整测试内容…')); if (!$('historyDetail').open) $('historyDetail').showModal(); $('historyDetailClose').focus();
    const timer = setTimeout(() => detailController?.abort(), 20000);
    try { const record = await (await request('/api/history/' + encodeURIComponent(id), { signal: detailController.signal })).json(); if (serial !== detailSerial || !$('historyDetail').open) return; renderDetail(record); }
    catch (error) { if (serial !== detailSerial || !$('historyDetail').open) return; const button = make('button', '重试读取', 'secondary'); button.type = 'button'; button.addEventListener('click', () => openDetail(id, previousFocus)); $('historyDetailBody').replaceChildren(make('p', error.name === 'AbortError' ? '连接超时，请重试。' : error.message, 'history-detail-error'), button); }
    finally { clearTimeout(timer); }
  }
  async function download(record, format, button) {
    button.disabled = true; $('historyDetailBody').querySelectorAll('.history-detail-error').forEach(node => node.remove());
    try { const path = record.run_id ? '/api/runs/' + encodeURIComponent(record.run_id) + '/' + format : '/api/history/' + encodeURIComponent(record.id) + '/' + format; const response = await request(path), blob = await response.blob(), url = URL.createObjectURL(blob), a = make('a'); a.href = url; const raw = Number(record.finished_at || record.created_at || Date.now() / 1000), date = new Date((raw < 1e12 ? raw * 1000 : raw)), pad = value => String(value).padStart(2, '0'), stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`, model = String(record.model || record.title || '未命名模型').trim().replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '-').replace(/\s+/g, ' ').slice(0, 80) || '未命名模型', suffix = format === 'evidence.zip' ? '-证据.zip' : '.' + format.split('.').pop(); a.download = `测试报告-${model}-${stamp}${suffix}`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 10000); }
    catch (error) { if (currentRecord?.id === record.id) $('historyDetailBody').append(make('p', error.message, 'history-detail-error')); }
    finally { button.disabled = false; }
  }
  function closeDetail() { $('historyDetail').close(); }
  $('historyDetailClose').addEventListener('click', closeDetail); $('historyDetail').addEventListener('close', () => { detailSerial++; if (detailController) detailController.abort(); $('historyDetailBody').querySelectorAll('audio,video').forEach(node => node.pause()); $('historyDetailBody').replaceChildren(); currentRecord = null; if (previousFocus?.isConnected) previousFocus.focus(); });
  $('historyDetail').addEventListener('click', event => { if (event.target !== $('historyDetail')) return; const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDetail(); });
  $('workspaceTab').addEventListener('click', () => showView('workspace')); $('historyTab').addEventListener('click', () => showView('history'));
  $('historyRefresh').addEventListener('click', async () => { await connect(true); loadList(); });
  for (const id of ['historyKind', 'historyStatus']) $(id).addEventListener('change', () => { clearTimeout(searchTimer); offset = 0; loadList(); });
  $('historySearch').addEventListener('input', () => { clearTimeout(searchTimer); requestSerial++; if (listController) listController.abort(); offset = 0; searchTimer = setTimeout(loadList, 250); });
  $('historyPrevious').addEventListener('click', () => { offset = Math.max(0, offset - limit); loadList(); }); $('historyNext').addEventListener('click', () => { if (offset + limit >= total) return; offset += limit; loadList(); });
  $('logoutButton').addEventListener('click', async () => { $('logoutButton').disabled = true; try { await request('/api/auth/logout', { method: 'POST' }); location.replace('/login'); } catch (error) { $('sessionNotice').hidden = false; $('sessionNotice').textContent = '退出失败：' + error.message; } finally { $('logoutButton').disabled = false; } });
  async function refresh() { await connect(); if (!available) return; if (selected === 'history') return loadList(); try { const response = await request('/api/history?limit=1&offset=0'), data = await response.json(); stats(data.stats); } catch (_) {} }
  window.WorkbenchHistory = { refresh, open: () => showView('history') };
  window.addEventListener('workbench:history-saved', refresh);
  connect().then(() => { if (available) refresh(); });
})();
