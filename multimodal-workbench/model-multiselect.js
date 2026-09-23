(function (root) {
  'use strict';
  const instances = new WeakMap(), openPickers = new Set();
  let sequence = 0;
  const unique = values => [...new Set((values || []).map(value => String(value || '').trim()).filter(Boolean))];
  const split = value => unique(String(value || '').split(/[,，\s]+/));
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  class ModelPicker {
    constructor(input, config) {
      this.input = input;
      this.batchInput = config.batchInput || null;
      this.label = config.label || '模型';
      this.max = Math.min(30, Math.max(1, Number(config.max) || 30));
      this.onChange = config.onChange;
      this.options = unique(config.options);
      this.selected = [];
      this.custom = new Set();
      this.notice = '';
      this.externalStatus = '';
      this.active = -1;
      this.disabled = false;
      this.build(config.ids || {});
      this.bind();
      this.syncFromInputs({ emit: false });
    }
    build(ids) {
      const prefix = 'model-multi-' + (++sequence);
      const identify = (node, key) => { node.id = ids[key] || prefix + '-' + key; return node; };
      this.wrapper = identify(element('div', 'mm-picker choice-picker'), 'wrapper');
      this.wrapper.dataset.modelMultiselect = '';
      this.row = element('div', 'mm-input-row');
      this.clearButton = identify(element('button', 'mm-clear', '×'), 'clear');
      this.clearButton.type = 'button';
      this.clearButton.setAttribute('aria-label', '清空已选模型');
      this.toggle = identify(element('button', 'mm-toggle', '▾'), 'toggle');
      this.toggle.type = 'button';
      this.toggle.setAttribute('aria-label', '展开模型多选');
      this.chips = element('div', 'mm-chips');
      this.chips.setAttribute('aria-label', '已选模型');
      this.summary = element('p', 'mm-summary');
      this.summary.setAttribute('aria-live', 'polite');
      this.menu = identify(element('div', 'mm-menu'), 'menu');
      this.menu.hidden = true;
      this.menu.setAttribute('role', 'region');
      this.menu.setAttribute('aria-label', this.label + '多选列表');
      const searchRow = element('div', 'mm-search-row');
      this.search = identify(element('input', 'mm-search'), 'search');
      this.search.type = 'search';
      this.search.placeholder = '搜索模型，也可输入自定义 ID…';
      this.search.autocomplete = 'off';
      this.search.spellcheck = false;
      this.search.setAttribute('aria-label', '搜索模型列表');
      this.showAll = identify(element('button', 'mm-text-button', '全部'), 'all');
      this.showAll.type = 'button';
      searchRow.append(this.search, this.showAll);
      const actions = element('div', 'mm-actions');
      this.selectVisible = element('button', 'mm-text-button', '勾选搜索结果');
      this.selectVisible.type = 'button';
      this.clearSelection = element('button', 'mm-text-button', '清空选择');
      this.clearSelection.type = 'button';
      actions.append(this.selectVisible, this.clearSelection);
      this.status = identify(element('div', 'mm-status'), 'status');
      this.status.setAttribute('role', 'status');
      this.status.setAttribute('aria-live', 'polite');
      this.list = identify(element('div', 'mm-list'), 'list');
      this.list.setAttribute('role', 'group');
      this.list.setAttribute('aria-label', '可勾选模型');
      this.addCustom = element('button', 'mm-add-custom');
      this.addCustom.type = 'button';
      this.menu.append(searchRow, actions, this.status, this.list, this.addCustom);
      this.input.parentNode.insertBefore(this.wrapper, this.input);
      this.wrapper.append(this.row, this.chips, this.summary, this.menu);
      this.row.append(this.input, this.clearButton, this.toggle);
      this.input.removeAttribute('list');
      this.input.removeAttribute('role');
      this.input.removeAttribute('aria-autocomplete');
      this.input.setAttribute('aria-controls', this.menu.id);
      this.toggle.setAttribute('aria-controls', this.menu.id);
      this.input.placeholder = '勾选一个或多个模型，也可手动填写 ID';
      this.close();
    }
    bind() {
      this.toggle.addEventListener('click', () => {
        if (this.isDisabled()) return;
        if (this.menu.hidden || this.search.value) { this.search.value = ''; this.open({ focus: true }); }
        else this.close();
      });
      this.clearButton.addEventListener('click', () => { if (!this.isDisabled()) { this.clear(); this.open(); this.input.focus(); } });
      this.clearSelection.addEventListener('click', () => { if (!this.isDisabled()) this.clear(); });
      this.showAll.addEventListener('click', () => { this.search.value = ''; this.render(); this.search.focus(); });
      this.search.addEventListener('input', () => { this.active = -1; this.render(); });
      this.selectVisible.addEventListener('click', () => {
        if (this.isDisabled()) return;
        const candidates = unique([...this.selected, ...this.filtered()]);
        if (candidates.length > this.max) { this.notice = `最多选择 ${this.max} 个模型，请缩小搜索范围后再勾选。`; this.render(); return; }
        this.selected = candidates;
        this.notice = '';
        this.sync(true);
      });
      this.addCustom.addEventListener('click', () => this.add(this.search.value, true));
      this.input.addEventListener('input', () => {
        if (this.syncing || this.isDisabled()) return;
        const value = this.input.value.trim();
        this.selected = value ? [value] : [];
        this.custom = new Set(value && !this.options.includes(value) ? [value] : []);
        if (this.batchInput) this.batchInput.value = '';
        this.notice = '';
        this.search.value = value;
        this.render();
        this.changed();
        if (this.options.length) this.open();
      });
      if (this.batchInput) this.batchInput.addEventListener('input', () => { if (!this.syncing) this.syncFromInputs(); });
      for (const control of [this.input, this.search]) control.addEventListener('keydown', event => this.keydown(event));
      this.wrapper.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !this.menu.hidden) { event.preventDefault(); this.close(); this.input.focus(); }
        if (event.key === 'Tab') setTimeout(() => { if (!this.wrapper.contains(document.activeElement)) this.close(); }, 0);
      });
      // Closing on pointerdown changes layout before pointerup (especially
      // beside a stretched fetch button), so the browser can drop the click.
      // Capture click after its target is fixed, before an outside action
      // such as "fetch models" opens this picker again.
      document.addEventListener('click', event => { if (!this.wrapper.contains(event.target)) this.close(); }, true);
      this.observer = new MutationObserver(() => this.syncDisabled());
      this.observer.observe(this.input, { attributes: true, attributeFilter: ['disabled'] });
      const fieldset = this.input.closest('fieldset');
      if (fieldset) this.observer.observe(fieldset, { attributes: true, attributeFilter: ['disabled'] });
    }
    isDisabled() { return this.disabled || this.input.disabled || !!this.input.closest('fieldset:disabled'); }
    setDisabled(value) { this.disabled = !!value; this.input.disabled = !!value; this.syncDisabled(); }
    syncDisabled() {
      const disabled = this.isDisabled();
      this.wrapper.classList.toggle('is-disabled', disabled);
      for (const control of this.wrapper.querySelectorAll('button,input[type="checkbox"],input[type="search"]')) control.disabled = disabled;
      if (disabled) this.close();
    }
    filtered() { const query = this.search.value.trim().toLowerCase(); return this.options.filter(id => !query || id.toLowerCase().includes(query)); }
    render() {
      const rows = this.filtered(), checked = new Set(this.selected);
      this.list.replaceChildren();
      rows.forEach((id, index) => {
        const option = element('label', 'mm-option model-option');
        option.dataset.model = id;
        option.id = this.list.id + '-option-' + index;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(checked.has(id)));
        const checkbox = element('input');
        checkbox.type = 'checkbox'; checkbox.value = id; checkbox.checked = checked.has(id);
        checkbox.setAttribute('aria-label', id);
        checkbox.addEventListener('change', () => {
          this.toggleModel(id);
          const next = [...this.list.querySelectorAll('input')].find(input => input.value === id);
          if (next) next.focus({ preventScroll: true });
        });
        option.append(checkbox, element('span', 'mm-model-name', id));
        this.list.append(option);
      });
      if (!rows.length) this.list.append(element('p', 'mm-empty', this.options.length ? '没有匹配的模型，可调整关键词或添加自定义映射。' : '尚无可选模型。请先获取列表，也可输入自定义模型 ID。'));
      const query = this.search.value.trim();
      this.addCustom.hidden = !query || this.options.includes(query) || checked.has(query);
      this.addCustom.textContent = `＋ 添加自定义模型「${query}」`;
      this.chips.replaceChildren();
      this.selected.forEach(id => {
        const chip = element('span', 'mm-chip');
        chip.append(element('span', '', id));
        const remove = element('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', '取消选择 ' + id);
        remove.addEventListener('click', () => { if (!this.isDisabled()) this.toggleModel(id); });
        chip.append(remove); this.chips.append(chip);
      });
      this.chips.hidden = !this.selected.length;
      this.summary.textContent = this.notice || (this.selected.length > 1 ? `已选 ${this.selected.length} / ${this.max} 个模型，将按顺序逐个测试。` : this.selected.length ? '已选 1 个模型；继续勾选可进行批量测试。' : `支持勾选多个模型，最多 ${this.max} 个；自定义映射也可添加。`);
      this.summary.classList.toggle('mm-warning', !!this.notice);
      this.status.textContent = this.externalStatus || `${query ? '匹配' : '全部'} ${rows.length} / ${this.options.length} 个模型 · 已选 ${this.selected.length}`;
      this.active = -1;
      this.input.removeAttribute('aria-activedescendant');
      this.search.removeAttribute('aria-activedescendant');
      this.syncDisabled();
    }
    changed() { if (typeof this.onChange === 'function') this.onChange(this.getSelected()); }
    sync(emit = true) {
      this.input.value = this.selected.at(-1) || '';
      if (this.batchInput) this.batchInput.value = this.selected.length > 1 ? this.selected.join(',') : '';
      this.render();
      if (emit) {
        this.syncing = true;
        try {
          for (const control of [this.input, this.batchInput].filter(Boolean)) {
            control.dispatchEvent(new Event('input', { bubbles: true }));
            control.dispatchEvent(new Event('change', { bubbles: true }));
          }
        } finally { this.syncing = false; }
        this.changed();
      }
    }
    syncFromInputs({ emit = false } = {}) {
      const batch = this.batchInput ? split(this.batchInput.value) : [];
      this.setSelected(batch.length ? batch : this.input.value.trim() ? [this.input.value.trim()] : [], { emit });
    }
    getSelected() { return [...this.selected]; }
    setSelected(values, { emit = false } = {}) {
      const ids = unique(values);
      this.selected = ids.slice(0, this.max);
      this.custom = new Set(this.selected.filter(id => !this.options.includes(id)));
      this.notice = ids.length > this.max ? `最多选择 ${this.max} 个模型，已保留前 ${this.max} 个。` : '';
      this.sync(emit);
      return this;
    }
    setOptions(values, { reconcile = true } = {}) {
      this.options = unique(values);
      const removed = reconcile ? this.selected.filter(id => !this.options.includes(id) && !this.custom.has(id)) : [];
      if (removed.length) {
        this.selected = this.selected.filter(id => !removed.includes(id));
        this.notice = '刷新后已移除渠道不再提供的模型：' + removed.join('、') + '。如为有效映射，可手动添加。';
      }
      for (const id of this.options) this.custom.delete(id);
      this.externalStatus = '';
      this.sync(!!removed.length);
      return this;
    }
    setStatus(text) { this.externalStatus = String(text || ''); this.render(); return this; }
    clear({ emit = true } = {}) { this.selected = []; this.custom.clear(); this.notice = ''; this.search.value = ''; this.sync(emit); return this; }
    toggleModel(id) {
      if (this.isDisabled()) return;
      if (this.selected.includes(id)) { this.selected = this.selected.filter(value => value !== id); this.custom.delete(id); this.notice = ''; this.sync(); }
      else this.add(id, false);
    }
    add(value, custom = false) {
      if (this.isDisabled()) return;
      const id = String(value || '').trim();
      if (!id || this.selected.includes(id)) return;
      if (/[\s,，]/.test(id)) { this.notice = '请逐个添加模型 ID；模型 ID 不能包含空白或逗号。'; this.render(); return; }
      if (this.selected.length >= this.max) { this.notice = `最多选择 ${this.max} 个模型，请先取消部分选择。`; this.render(); return; }
      this.selected.push(id);
      if (custom && !this.options.includes(id)) this.custom.add(id);
      this.notice = '';
      if (custom) this.search.value = '';
      this.sync();
    }
    open({ focus = false } = {}) {
      if (this.isDisabled()) return;
      for (const picker of openPickers) if (picker !== this) picker.close();
      this.menu.hidden = false;
      this.input.setAttribute('aria-expanded', 'true'); this.toggle.setAttribute('aria-expanded', 'true');
      this.toggle.setAttribute('aria-label', '收起模型多选'); openPickers.add(this);
      this.render();
      if (focus) this.search.focus();
      return this;
    }
    close() {
      this.menu.hidden = true;
      this.input.setAttribute('aria-expanded', 'false'); this.toggle.setAttribute('aria-expanded', 'false');
      this.toggle.setAttribute('aria-label', '展开模型多选'); openPickers.delete(this);
      return this;
    }
    keydown(event) {
      if (this.isDisabled()) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (this.menu.hidden) { this.search.value = ''; this.open(); }
        const options = [...this.list.querySelectorAll('.mm-option')];
        if (!options.length) return;
        this.active = (this.active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
        options.forEach((node, index) => node.classList.toggle('is-active', index === this.active));
        event.target.setAttribute('aria-activedescendant', options[this.active].id);
        options[this.active].scrollIntoView({ block: 'nearest' });
      } else if (event.key === 'Enter' && !this.menu.hidden) {
        event.preventDefault();
        const option = this.list.querySelectorAll('.mm-option')[this.active];
        if (option) this.toggleModel(option.dataset.model);
        else if (event.target === this.search && this.search.value.trim()) this.add(this.search.value, true);
      }
    }
  }
  root.ModelMultiselect = {
    attach(input, config = {}) {
      if (!input) throw new Error('ModelMultiselect requires an input');
      const existing = instances.get(input);
      if (existing) { if (config.options) existing.setOptions(config.options); return existing; }
      const picker = new ModelPicker(input, config); instances.set(input, picker); return picker;
    },
    get(input) { return instances.get(input); }
  };
})(window);
