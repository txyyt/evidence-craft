/* 模板页（V2 §三 §5，/templates）：树列表（派生 + 报告计数跳报告库过滤）
   + 对话 + 编辑器 + 版本与日志。树模式生成只走向导（/new），本页无生成入口。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;
  window.addEventListener('unhandledrejection', (e) => {
    window.__lastRejection = String((e && e.reason) || e).slice(0, 200);
  });

  // V4-10：值不变，显示中文在前（技术键放后缀，详见 fielddict EC.TERMS）
  const KINDS = [
    { label: '综述段（text）', value: 'text' },
    { label: '观点节（views）', value: 'views' },
    { label: '表格节（table）', value: 'table' },
    { label: '风险节（risk）', value: 'risk' },
    { label: '图件节（figures）', value: 'figures' },
  ];
  const RENDERERS = [
    { label: '事实行表格（facts_rows）', value: 'facts_rows' },
    { label: '通用数据表（generic_rows）', value: 'generic_rows' },
    { label: '盈利预测表（consensus_pe）', value: 'consensus_pe' },
  ];
  const GENRES = [
    { label: '（自动推断）', value: '' },
    { label: '期刊论文体 journal', value: 'journal' },
    { label: '研报体 research', value: 'research' },
    { label: '简报体 brief', value: 'brief' },
  ];
  const CHART_TYPES = ['bar', 'line', 'pie', 'scatter', 'hist']
    .map((t) => ({ label: t, value: t }));

  function blankSection(kind = 'text') {
    const base = { id: '', title: '', kind, heading: '', subheading: '',
      style: '', data_needs: [], origin: 'user',
      charts: [], view_slots: [], n_views: 2, table: '',
      strategy: 'enumerate', body_min: null, body_max: null };
    if (kind === 'views') base.view_slots = [{ id: 'v1', brief: '', data_needs: [] }];
    return base;
  }

  /* 编辑表单 ↔ API spec_dict 互转 */
  function formFromSpec(sd, meta) {
    return {
      name: meta.name || meta.id, subject: meta.subject || '', status: meta.status || 'draft',
      genre: sd.genre || '', description: sd.description || '',
      writer_role: sd.writer_role || '', title_style: sd.title_style || '',
      style_card: sd.style_card || '',
      writing_rules: (sd.writing_rules || []).join('\n'),
      forbidden_words: (sd.forbidden_words || []).slice(),
      controlled_vocab: Object.entries(sd.controlled_vocab || {})
        .map(([key, words]) => ({ key, words: (words || []).join(',') })),
      tables: (sd.tables || []).map((t) => ({ id: t.id, renderer: t.renderer,
        source_prefix: t.source_prefix || '', columns: (t.columns || []).join(',') })),
      sections: (sd.sections || []).map((s) => ({
        id: s.id, title: s.title, kind: s.kind,
        heading: s.heading || '', subheading: s.subheading || '',
        style: s.style || '', data_needs: s.data_needs || [],
        origin: s.origin || 'agent',
        charts: (s.charts || []).map((c) => ({ id: c.id, title: c.title,
          type: c.type || 'bar', source: c.source, unit: c.unit || '' })),
        view_slots: (s.view_slots || []).map((v) => ({ id: v.id, brief: v.brief || '',
          data_needs: v.data_needs || [] })),
        n_views: s.n_views || (s.view_slots || []).length || 2,
        table: s.table || '', strategy: s.strategy || 'enumerate',
        body_min: (s.check && s.check.body_len) ? s.check.body_len[0] : null,
        body_max: (s.check && s.check.body_len) ? s.check.body_len[1] : null,
      })),
    };
  }

  function specFromForm(f, treeId) {
    const sections = f.sections.map((s) => {
      const out = { id: s.id, title: s.title, kind: s.kind };
      if (s.heading) out.heading = s.heading;
      if (s.subheading) out.subheading = s.subheading;
      if (s.style) out.style = s.style;
      if (s.data_needs.length) out.data_needs = s.data_needs;
      out.origin = s.origin || 'agent';
      if (s.kind === 'views') {
        out.n_views = s.view_slots.length;
        out.view_slots = s.view_slots.map((v) => ({ id: v.id, brief: v.brief,
          data_needs: v.data_needs }));
        out.view_style = s.style || '';
      }
      if (s.kind === 'table') out.table = s.table;
      if (s.kind === 'risk') out.strategy = s.strategy;
      if (s.charts.length) out.charts = s.charts;
      const bl = [s.body_min, s.body_max].every((x) => x != null && x !== '')
        ? [Number(s.body_min), Number(s.body_max)] : null;
      if (bl) out.check = { body_len: bl };
      return out;
    });
    const out = { report_type: treeId, description: f.description,
      writer_role: f.writer_role || '资深报告撰写人',
      title_style: f.title_style, sections };
    if (f.genre) out.genre = f.genre;
    if (f.style_card) out.style_card = f.style_card;    // B1：保存不再抹掉文风卡
    if (f.forbidden_words && f.forbidden_words.length)
      out.forbidden_words = f.forbidden_words.filter(Boolean);
    const cv = {};
    (f.controlled_vocab || []).forEach((kv) => {
      const words = String(kv.words || '').split(',').map((x) => x.trim()).filter(Boolean);
      if (kv.key && words.length) cv[kv.key] = words;
    });
    if (Object.keys(cv).length) out.controlled_vocab = cv;
    const rules = f.writing_rules.split('\n').map((x) => x.trim()).filter(Boolean);
    if (rules.length) out.writing_rules = rules;
    const tables = f.tables.filter((t) => t.id).map((t) => {
      const o = { id: t.id, renderer: t.renderer };
      if (t.source_prefix) o.source_prefix = t.source_prefix;
      const cols = t.columns.split(',').map((x) => x.trim()).filter(Boolean);
      if (cols.length) o.columns = cols;
      return o;
    });
    if (tables.length) out.tables = tables;
    return out;
  }

  EC.views['/templates'] = {
    title: '结构与文风',
    component: {
      setup() {
        const dialog = NA.useDialog();
        /* ---- 列表 ---- */
        const trees = ref([]);
        const selId = ref(null);
        const detail = ref(null);       // {meta, spec_dict, fingerprint, lint, versions, ops}
        const form = reactive(formFromSpec({}, {}));
        const loading = ref(false);
        const dirty = ref(false);
        const showNew = ref(false);
        // V4-10：新建只必填名称/主题；id 省略时后端从名称派生（自定义 id 折叠在高级里）
        const newForm = reactive({ id: '', name: '', subject: '', description: '',
                                   _customId: false });
        const creating = ref(false);
        const styleCards = ref([]);     // B1：文风卡（含规则与节选预览）

        // V4-10：树列表可找到——搜索 / lint 筛选 / 排序
        const treeQuery = ref('');
        const lintFilter = ref(null);   // null 全部 | 'ok' | 'warn' | 'err'
        const sortBy = ref('updated');  // 'updated' | 'name'
        const treeList = computed(() => {
          let arr = trees.value.slice();
          const q = treeQuery.value.trim().toLowerCase();
          if (q) arr = arr.filter((t) =>
            String(t.name || '').toLowerCase().includes(q)
            || String(t.id || '').toLowerCase().includes(q));
          if (lintFilter.value === 'ok')
            arr = arr.filter((t) => t.lint && !t.lint.errors && !t.lint.warnings);
          else if (lintFilter.value === 'warn')
            arr = arr.filter((t) => t.lint && !t.lint.errors && t.lint.warnings);
          else if (lintFilter.value === 'err')
            arr = arr.filter((t) => t.lint && t.lint.errors);
          if (sortBy.value === 'name')
            arr.sort((a, b) => String(a.name).localeCompare(String(b.name), 'zh'));
          else
            arr.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
          return arr;
        });
        const lintFilterOptions = [
          { label: '全部检查状态', value: null },
          { label: '0 错误 0 提醒', value: 'ok' },
          { label: '有提醒', value: 'warn' },
          { label: '有错误', value: 'err' },
        ];
        const sortOptions = [
          { label: '按更新时间', value: 'updated' },
          { label: '按名称', value: 'name' },
        ];

        /* ---- 树报告计数（V2 §三 §5 双向追溯之二）---- */
        const runRows = ref([]);
        async function loadRunRows() {
          try { runRows.value = await EC.api.get('/api/runs'); }
          catch (e) { runRows.value = []; }
        }
        const reportCount = (id) =>
          runRows.value.filter((r) => r.tree_id === id).length;
        function goReports(id) {
          location.hash = '/reports?tree=' + encodeURIComponent(id);
        }
        // V4-06：编辑完一键带树进向导（#/new?tree=<id>）
        function useTreeForReport() {
          if (!selId.value) return;
          location.hash = '/new?tree=' + encodeURIComponent(selId.value);
        }
        function goNewChat() { location.hash = '/new?start=chat'; }

        async function loadTrees() {
          trees.value = await EC.api.get('/api/trees');
          if (selId.value && !trees.value.some((t) => t.id === selId.value)) selId.value = null;
        }
        function guardUnsaved() {
          // V4-10：有未保存编辑时三选一（保存 / 放弃 / 留在这里），不静默覆盖
          if (!dirty.value) return Promise.resolve(true);
          return new Promise((resolve) => {
            const d = dialog.warning({
              title: '有未保存的编辑',
              content: '切换前要先处理当前编辑：保存为新版本、放弃修改，或留在当前树。',
              positiveText: '保存并切换',
              negativeText: '放弃修改',
              onClose: () => resolve(false),
              onMaskClick: () => { d.destroy(); resolve(false); },
              onPositiveClick: async () => {
                await saveTree();
                resolve(true);
              },
              onNegativeClick: () => {
                dirty.value = false;
                resolve(true);
              },
            });
          });
        }
        async function selectTree(id) {
          // V4-10：切树前脏保护（列表点击/程序选择统一走这里）
          if (dirty.value && id !== selId.value) {
            const ok = await guardUnsaved();
            if (!ok) return;
          }
          selId.value = id;
          dirty.value = false;
          if (!id) { detail.value = null; return; }
          loading.value = true;
          try {
            detail.value = await EC.api.get('/api/trees/' + encodeURIComponent(id));
            Object.assign(form, formFromSpec(detail.value.spec_dict, detail.value.meta));
          } finally { loading.value = false; }
        }

        async function createTree() {
          if (!newForm.name.trim()) { EC.toast('名称必填', 'warning'); return; }
          creating.value = true;
          try {
            // V4-10：id 可省略——后端从名称派生并在冲突时加短序号；
            // 高级里勾了「自定义 id」才要求填写
            const payload = { name: newForm.name.trim(),
              subject: newForm.subject.trim(),
              description: newForm.description.trim() };
            if (newForm._customId && newForm.id.trim()) payload.id = newForm.id.trim();
            const r = await EC.api.post('/api/trees', payload);
            showNew.value = false;
            EC.toast('结构树已创建', 'success');
            await loadTrees();
            await selectTree(r.id);
            Object.assign(newForm, { id: '', name: '', subject: '', description: '',
                                     _customId: false });
          } catch (e) { EC.toast(e.message, 'error'); }
          finally { creating.value = false; }
        }

        function markDirty() { dirty.value = true; }
        function addSection(kind) {
          const s = blankSection(kind);
          form.sections.push(s);
          markDirty();
          // C2：切到 views 必须有槽位——自动补默认槽位并提示
          if (kind === 'views') {
            EC.toast('已自动补一个视角槽位，请在 brief 里写清这条观点的职责', 'info');
          }
        }
        // C2：节类型切换到 views 且无槽位时自动补默认槽位
        function onKindChange(s, kind) {
          s.kind = kind;
          if (kind === 'views' && (!(s.view_slots || []).length)) {
            s.view_slots = [{ id: 'v1', brief: s.style || '', data_needs: s.data_needs || [] }];
            s.n_views = 1;
            EC.toast('已切到观点节：自动补了一个视角槽位，请补全槽位 brief', 'info');
          }
          markDirty();
        }
        // C5：id 缺省跟随标题（中文短名）；生成报告后不要改 id
        function onTitleInput(s) {
          if (!s.id || s._idAuto) { s.id = s.title; s._idAuto = true; }
          markDirty();
        }
        function onIdInput(s) { s._idAuto = false; markDirty(); }
        function delSection(i) { form.sections.splice(i, 1); markDirty(); }
        function moveSection(i, d) {
          const j = i + d;
          if (j < 0 || j >= form.sections.length) return;
          [form.sections[i], form.sections[j]] = [form.sections[j], form.sections[i]];
          markDirty();
        }
        function addSlot(s) { s.view_slots.push({ id: 'v' + (s.view_slots.length + 1), brief: '', data_needs: [] }); markDirty(); }
        function addChart(s) { s.charts.push({ id: '', title: '', type: 'bar', source: '', unit: '' }); markDirty(); }
        function addTable() { form.tables.push({ id: '', renderer: 'facts_rows', source_prefix: '', columns: '' }); markDirty(); }
        function addVocabRow() { form.controlled_vocab.push({ key: '', words: '' }); markDirty(); }

        async function saveTree() {
          if (!selId.value) return;
          try {
            const r = await EC.api.put('/api/trees/' + encodeURIComponent(selId.value), {
              spec_dict: specFromForm(form, selId.value),
              meta: { name: form.name, subject: form.subject, status: form.status },
              summary: '编辑器保存',
              base_version: detail.value ? detail.value.meta.version : null });
            dirty.value = false;
            EC.toast(`已保存 v${r.version}（${(r.lint.errors.length)} 错误 / `
                     + `${r.lint.warnings.length} 提醒）`, 'success');
            await loadTrees();
            await selectTree(selId.value);
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        async function rollback(v) {
          try {
            await EC.api.post(`/api/trees/${encodeURIComponent(selId.value)}/rollback`, { version: v });
            EC.toast(`已回滚到 v${v}`, 'success');
            await selectTree(selId.value);
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        function deleteTree(t) {
          // B4：页内二次确认（显示树名与版本数），不再一键删链
          dialog.warning({
            title: '删除结构树',
            content: `确定删除「${t.name}」（${t.id}，v${t.version}，共 ${t.version} 个版本）？`
              + ' 整条版本链与全部数据计划都会被删除，不可恢复。',
            positiveText: '确认删除',
            negativeText: '取消',
            onPositiveClick: async () => {
              try {
                await EC.api.del('/api/trees/' + encodeURIComponent(t.id));
                EC.toast('结构树已删除', 'success');
                if (selId.value === t.id) selId.value = null;
                await loadTrees();
              } catch (e) { EC.toast(e.message, 'error'); }
            },
          });
        }

        /* ---- 对话（M2）：树选中=编辑对话；未选中=对话生成新树 ---- */
        const chatMsgs = ref([]);        // [{role:'user'|'assistant', text}]
        const chatInput = ref('');
        const chatBusy = ref(false);

        // B3：对话改树会自动重载编辑器——有未保存编辑时先给三选一
        function guardDirty() {
          if (!dirty.value) return Promise.resolve(true);
          return new Promise((resolve) => {
            const d = dialog.warning({
              title: '有未保存的编辑',
              content: '对话改树会自动保存并重载编辑器，当前手动编辑还没保存。'
                + '先保存这些修改，还是放弃修改继续对话？',
              positiveText: '先保存再改',
              negativeText: '放弃修改继续',
              onClose: () => resolve(false),
              onMaskClick: () => { d.destroy(); resolve(false); },
              onPositiveClick: async () => {
                await saveTree();
                resolve(true);
              },
              onNegativeClick: () => {
                dirty.value = false;
                resolve(true);
              },
            });
          });
        }

        async function sendChat() {
          const text = chatInput.value.trim();
          // V4-11（P13）：按钮已按空值禁用；防御校验兜程序调用/竞态，不再静默 return
          if (chatBusy.value) return;
          if (!text) {
            EC.toast('请先描述要写的报告或调整要求', 'warning');
            return;
          }
          if (selId.value && !(await guardDirty())) return;   // B3：取消发送
          chatMsgs.value.push({ role: 'user', text });
          chatInput.value = '';
          chatBusy.value = true;
          try {
            if (selId.value) {   // 编辑对话
              const r = await EC.api.post(`/api/trees/${encodeURIComponent(selId.value)}/chat`,
                { message: text, base_version: detail.value ? detail.value.meta.version : null });
              chatMsgs.value.push({
                role: 'assistant',
                text: r.changed
                  ? `${r.summary}（v${r.version}）`
                  : r.summary });
              if (r.changed) { await selectTree(selId.value); }
            } else {             // 生成对话
              const msgs = chatMsgs.value.map((m) => ({ role: m.role, text: m.text }));
              const r = await EC.api.post('/api/trees/generate', { messages: msgs });
              if (r.kind === 'questions') {
                // V4-11：追问分段展示，焦点送回输入框、按钮转「发送回答」
                chatMsgs.value.push({ role: 'assistant', kind: 'questions',
                  text: '需要补充以下信息：', questions: r.questions });
                refocusChat();
              } else {
                chatMsgs.value.push({ role: 'assistant',
                  text: `已生成结构树「${r.name}」（v${r.version}，${r.spec_dict.sections.length} 节）` });
                await loadTrees();
                await selectTree(r.tree_id);
              }
            }
          } catch (e) {
            if (String(e.message).includes('已被其他页面修改')) {
              chatMsgs.value.push({ role: 'assistant',
                text: '保存冲突：' + e.message + '（已为你重载最新版本，请重发意见）' });
              await selectTree(selId.value);
            } else {
              chatMsgs.value.push({ role: 'assistant', text: '出错：' + e.message });
            }
          } finally { chatBusy.value = false; }
        }
        function refocusChat() {
          // V4-11：追问返回后焦点回输入框
          setTimeout(() => {
            const el = document.querySelector('#ec-tpl-chat-input textarea');
            if (el) el.focus();
          }, 120);
        }
        // V4-11：追问待回答态（按钮文案）+ 状态播报
        const needAnswer = Vue.computed(() => {
          const m = chatMsgs.value[chatMsgs.value.length - 1];
          return !!m && m.kind === 'questions';
        });
        const chatStatus = Vue.computed(() => chatBusy.value ? '正在生成…'
          : needAnswer.value ? '需要补充信息' : '');

        onMounted(async () => {
          await Promise.all([loadTrees(), loadRunRows(), loadStyleCards()]);
          // 深链：#/templates?tree=<id> 直接选中（列表点击之外的可编程入口）
          const qs = location.hash.split('?')[1] || '';
          const tid = new URLSearchParams(qs).get('tree');
          if (tid && trees.value.some((t) => t.id === tid)) await selectTree(tid);
        });

        const lintErrors = computed(() => (detail.value && detail.value.lint.errors) || []);
        const lintWarnings = computed(() => (detail.value && detail.value.lint.warnings) || []);

        /* ---- V4-10：常用 / 高级渐进展开（默认折叠，本会话记忆） ---- */
        const advOpen = ref(sessionStorage.getItem('ec.tpl.advOpen.v1') === '1');
        function toggleAdv() {
          advOpen.value = !advOpen.value;
          try { sessionStorage.setItem('ec.tpl.advOpen.v1', advOpen.value ? '1' : '0'); }
          catch (e) { /* 隐私模式等场景忽略 */ }
        }
        // 节级高级字段已有非默认值的个数（折叠标题提示"已配置 N 项"）
        function secAdvCount(s) {
          let n = 0;
          if (s.heading) n++;
          if (s.subheading) n++;
          if (s.body_min != null && s.body_min !== '') n++;
          if (s.body_max != null && s.body_max !== '') n++;
          if (s.kind === 'table' && s.table) n++;
          if ((s.charts || []).length) n++;
          if ((s.view_slots || []).length) n++;
          return n;
        }
        const treeAdvCount = computed(() => {
          let n = 0;
          if ((form.forbidden_words || []).length) n++;
          if ((form.controlled_vocab || []).length) n++;
          if ((form.tables || []).length) n++;
          return n;
        });
        // B1：选中文风卡对象（下拉预览用）
        const selectedCard = computed(() =>
          styleCards.value.find((c) => c.id === form.style_card) || null);
        const styleCardOptions = computed(() => [
          { label: '（不使用文风卡）', value: '' },
          ...styleCards.value.map((c) => ({
            label: `${c.name}｜${(c.rules || c.desc || '').split('\n')[0].slice(0, 30)}`
              + `｜节选×${c.n_excerpts || 0}`,
            value: c.id }))]);

        /* ---- C：文风卡管理（全局资产，与单棵树平级；写入即生效——后端无缓存） ---- */
        const scShow = ref(false);
        const scSaving = ref(false);
        const scForm = reactive({ id: '', name: '', desc: '', rules: '',
                                  excerpts: [], _isNew: true, _builtInCopy: false });
        async function loadStyleCards() {
          try { styleCards.value = await EC.api.get('/api/trees/style-cards/all'); }
          catch (e) { /* 文风卡加载失败不阻塞工作台 */ }
        }
        function openNewCard() {
          Object.assign(scForm, { id: '', name: '', desc: '', rules: '',
                                  excerpts: [], _isNew: true, _builtInCopy: false });
          scShow.value = true;
        }
        function openEditCard(c) {
          const excerpts = (c.excerpts || []).map((e) =>
            ({ text: e.text || '', source: e.source || '' }));
          if (c.source === 'built-in') {
            // 内置卡只读：编辑动作自动转「复制为自定义」流程
            Object.assign(scForm, { id: c.id + '_custom', name: c.name + '（自定义）',
                                    desc: c.desc || '', rules: c.rules || '',
                                    excerpts, _isNew: true, _builtInCopy: true });
            EC.toast('内置卡只读：已转为「复制为自定义」，保存后生成新卡', 'info');
          } else {
            Object.assign(scForm, { id: c.id, name: c.name, desc: c.desc || '',
                                    rules: c.rules || '',
                                    excerpts, _isNew: false, _builtInCopy: false });
          }
          scShow.value = true;
        }
        function addExcerpt() { scForm.excerpts.push({ text: '', source: '' }); }
        async function saveCard() {
          if (!/^[A-Za-z0-9_]{1,40}$/.test(scForm.id)) {
            EC.toast('id 只允许英数字与下划线（≤40 字符）', 'warning'); return;
          }
          if (!scForm.name.trim()) { EC.toast('先填名称', 'warning'); return; }
          scSaving.value = true;
          try {
            const payload = { id: scForm.id, name: scForm.name, desc: scForm.desc,
                              rules: scForm.rules,
                              excerpts: scForm.excerpts.filter((e) => (e.text || '').trim()) };
            if (scForm._isNew)
              await EC.api.post('/api/trees/style-cards', payload);
            else
              await EC.api.put('/api/trees/style-cards/'
                               + encodeURIComponent(scForm.id), payload);
            EC.toast('文风卡已保存（立即生效）', 'success');
            scShow.value = false;
            await loadStyleCards();
          } catch (e) { EC.toast(e.message, 'error'); }
          finally { scSaving.value = false; }
        }
        function deleteCard(c) {
          dialog.warning({
            title: '删除文风卡',
            content: `确定删除自定义文风卡「${c.name}」（${c.id}）？`
                     + '被树引用时会被拒绝并提示引用清单。',
            positiveText: '删除',
            negativeText: '取消',
            onPositiveClick: async () => {
              try {
                await EC.api.del('/api/trees/style-cards/'
                                 + encodeURIComponent(c.id));
                EC.toast('已删除', 'success');
                await loadStyleCards();
              } catch (e) { EC.toast(e.message, 'error'); }
            },
          });
        }

        // 自动化观测缝（仅挂内存引用，无 UI 影响）：浏览器自动化可驱动真实处理函数
        EC._templatesView = { form, detail, selId, dirty, onKindChange,
          onTitleInput, saveTree, deleteTree, selectTree, sendChat, chatInput,
          chatMsgs, deriveTree, styleCards, loadStyleCards, scForm, scShow,
          openNewCard, openEditCard, saveCard, deleteCard, addExcerpt,
          needAnswer, treeList, treeQuery, lintFilter, sortBy, advOpen, toggleAdv,
          showNew, newForm, creating, createTree, useTreeForReport };

        // F10：树派生（复制为可编辑草稿）
        async function deriveTree(t) {
          try {
            const r = await EC.api.post(`/api/trees/${encodeURIComponent(t.id)}/copy`);
            EC.toast(`已派生为「${r.id}」`, 'success');
            await loadTrees();
            await selectTree(r.id);
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        return {
          trees, treeList, treeQuery, lintFilter, sortBy,
          lintFilterOptions, sortOptions, lintText: EC.lintText,
          selId, selectTree, detail, form, loading, dirty, lintErrors, lintWarnings,
          showNew, newForm, creating, createTree, KINDS, RENDERERS, GENRES, CHART_TYPES,
          addSection, delSection, moveSection, addSlot, addChart, addTable,
          onKindChange, onTitleInput, onIdInput, addVocabRow,
          saveTree, rollback, deleteTree, markDirty,
          advOpen, toggleAdv, secAdvCount, treeAdvCount,
          styleCards, selectedCard, styleCardOptions, runRows,
          reportCount, goReports, deriveTree, useTreeForReport, goNewChat,
          chatMsgs, chatInput, chatBusy, sendChat, needAnswer, chatStatus,
          scShow, scSaving, scForm, openNewCard, openEditCard, saveCard,
          deleteCard, addExcerpt,
        };
      },
      template: `
        <n-grid :cols="24" :x-gap="12">
          <!-- 左列：树列表（V4-10：可找到——搜索/筛选/排序，行内名称+短id+版本+章节数+中文检查） -->
          <n-gi :span="6">
            <n-card size="small" class="ec-card" title="结构树">
              <template #header-extra>
                <n-button size="tiny" type="primary" @click="showNew = true">新建</n-button>
              </template>
              <n-space vertical size="small">
                <n-input v-model:value="treeQuery" size="small" clearable
                         :input-props="{ 'aria-label': '按名称或 id 搜索结构树' }"
                         placeholder="按名称或 id 搜索…" data-testid="tree-search" />
                <n-space size="small" align="center">
                  <n-select v-model:value="lintFilter" size="small" style="flex:1"
                            :options="lintFilterOptions" data-testid="tree-lint-filter" />
                  <n-select v-model:value="sortBy" size="small" style="flex:1"
                            :options="sortOptions" />
                </n-space>
              </n-space>
              <n-empty v-if="!treeList.length"
                       :description="trees.length ? '没有匹配的树，调整搜索或筛选' : '还没有结构树，点「新建」创建'"
                       size="small" style="margin-top:8px" />
              <n-list v-else hoverable clickable show-divider style="margin:-12px">
                <n-list-item v-for="t in treeList" :key="t.id"
                             :style="{background: t.id === selId ? 'var(--ec-hover, #f4f7fb)' : ''}"
                             @click="selectTree(t.id)">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                    <div style="min-width:0">
                      <div style="font-weight:600">{{ t.name }}
                        <!-- V4-10：中文检查结果（技术计数放 title） -->
                        <n-tag v-if="typeof t.lint === 'object'" size="tiny"
                               :type="t.lint.errors ? 'error' : t.lint.warnings ? 'warning' : 'success'"
                               style="margin-left:6px"
                               :title="'结构检查（lint）：' + (t.lint.errors || 0) + ' errors / ' + (t.lint.warnings || 0) + ' warnings'">
                          {{ lintText(t.lint) }}</n-tag>
                      </div>
                      <div class="ec-muted ec-mono" style="font-size:12px">{{ t.id }} · v{{ t.version }} · {{ t.sections }} 节</div>
                      <div style="font-size:12px;margin-top:2px;display:flex;gap:8px;align-items:center">
                        <a style="cursor:pointer;color:#2080f0" @click.stop="goReports(t.id)">
                          {{ reportCount(t.id) }} 份报告</a>
                        <span class="ec-muted">{{ (t.updated_at || '').replace('T', ' ') }}</span>
                      </div>
                    </div>
                    <n-space size="small" :wrap="false" align="center">
                      <n-button size="tiny" quaternary @click.stop="deriveTree(t)">派生</n-button>
                      <n-button size="tiny" quaternary type="error" @click.stop="deleteTree(t)">删</n-button>
                    </n-space>
                  </div>
                </n-list-item>
              </n-list>
            </n-card>
          </n-gi>

          <!-- 右列：对话卡（选中树=改树对话；未选中=说明+入口，不再复制生成对话 V4-06）
               + 编辑器 -->
          <n-gi :span="18">
            <!-- V4-06：未选中树时不再提供第二套生成对话 -->
            <n-card v-if="!selId" size="small" class="ec-card" title="对话生成新结构">
              <div style="font-size:13px;line-height:1.9">
                造树统一在 <b>新建报告向导</b> 里进行，避免两处入口造成混乱：
                <n-space size="small" style="margin-top:8px">
                  <n-button size="small" type="primary" data-testid="go-new-chat"
                            @click="goNewChat">对话生成新结构</n-button>
                  <n-button size="small" @click="showNew = true">新建空白结构（高级）</n-button>
                </n-space>
                <div class="ec-muted" style="font-size:12px;margin-top:8px">
                  对话里描述你的报告（章节、表格、口吻），AI 出结构后可直接配数据生成。</div>
              </div>
            </n-card>
            <n-card v-else size="small" class="ec-card" title="结构对话（编辑当前树）">
              <n-space vertical size="small">
                <n-alert type="info" size="small">
                  当前编辑 <b>{{ form.name }}</b>：直接说要改什么
                  （如「市场那节拆成两节」「加一节讲政策，放在现状后面」），确认后自动应用为新版本。
                  编辑完成后点上方「用这棵树写报告」继续。
                </n-alert>
                <div class="ec-log" style="max-height:260px">
                  <div v-for="(m, i) in chatMsgs" :key="i"
                       :style="{color: m.role === 'user' ? '' : '#2080f0'}">
                    <b>{{ m.role === 'user' ? '我' : '助手' }}：</b>{{ m.text }}
                    <!-- V4-11：追问逐条分段 -->
                    <div v-if="m.questions" style="margin-top:2px">
                      <div v-for="(q, qi) in m.questions" :key="qi"
                           style="margin-left:1.2em">• {{ q }}</div>
                    </div>
                  </div>
                  <div v-if="!chatMsgs.length" class="ec-muted">还没有对话。</div>
                </div>
                <!-- V4-11：状态播报区 -->
                <div aria-live="polite" class="ec-muted" data-testid="chat-status"
                     style="font-size:12px;min-height:16px">{{ chatStatus }}</div>
                <n-space size="small" align="center">
                  <n-input id="ec-tpl-chat-input" v-model:value="chatInput" type="textarea" :rows="2"
                           :input-props="{ 'aria-label': '对话输入' }"
                           style="flex:1"
                           placeholder="要改什么？（Ctrl+Enter 发送）"
                           @keydown.enter.ctrl="sendChat" />
                </n-space>
                <n-space size="small" align="center">
                  <!-- V4-11：空输入/请求中禁用；追问后按钮转「发送回答」 -->
                  <n-button size="small" type="primary" data-testid="chat-send"
                            :loading="chatBusy" :disabled="chatBusy || !chatInput.trim()"
                            @click="sendChat">
                    {{ needAnswer ? '发送回答' : '发送修改意见' }}</n-button>
                </n-space>
              </n-space>
            </n-card>

            <div v-if="!detail" class="ec-card" style="margin-top:12px;border:1px dashed var(--ec-line,#e5e7eb);border-radius:6px;padding:16px;font-size:13px">
              还没有选中结构树：<b>在左侧选择一棵</b>即可编辑并使用「用这棵树写报告」；
              或 <a style="cursor:pointer;color:#2080f0" @click="goNewChat">对话生成新结构</a>、
              点右上角「新建」创建空白结构。</div>
            <template v-else>
              <n-card size="small" class="ec-card">
                <template #header>
                  树编辑器
                  <n-tag size="small" style="margin-left:8px" :type="detail.meta.status === 'confirmed' ? 'success' : 'default'">
                    v{{ detail.meta.version }} · {{ detail.meta.status === 'confirmed' ? '已确认' : '草稿' }}</n-tag>
                  <n-tag size="small" class="ec-mono" style="margin-left:6px">{{ detail.fingerprint }}</n-tag>
                </template>
                <template #header-extra>
                  <n-space size="small">
                    <n-button size="small" type="primary" data-testid="use-tree"
                              @click="useTreeForReport">用这棵树写报告</n-button>
                    <n-button size="small" :disabled="!dirty" @click="saveTree">保存（新版本）</n-button>
                  </n-space>
                </template>
                <n-space vertical size="small">
                  <n-alert v-if="lintErrors.length" type="error" size="small">
                    <div v-for="e in lintErrors" :key="e">{{ e }}</div>
                  </n-alert>
                  <n-alert v-if="lintWarnings.length" type="warning" size="small">
                    <div v-for="w in lintWarnings" :key="w">{{ w }}</div>
                  </n-alert>
                </n-space>
                <n-tabs type="segment" size="small" style="margin-top:8px" default-value="struct">
                  <!-- 结构 -->
                  <n-tab-pane name="struct" tab="结构">
                    <n-space vertical size="small">
                      <!-- ===== 常用设置（V4-10）===== -->
                      <n-space size="small">
                        <n-input v-model:value="form.name" :input-props="{ 'aria-label': '树名称' }" placeholder="树名称" size="small" style="width:160px" @update:value="markDirty" />
                        <n-input v-model:value="form.subject" :input-props="{ 'aria-label': '报告主体' }" placeholder="报告主体（如：国内高纯石英）" size="small" style="width:220px" @update:value="markDirty" />
                        <n-select v-model:value="form.genre" :options="GENRES" size="small" style="width:150px" aria-label="文体风格" @update:value="markDirty" />
                        <n-select v-model:value="form.status" size="small" style="width:110px" aria-label="树状态"
                                  :options="[{label:'草稿',value:'draft'},{label:'已确认',value:'confirmed'}]" @update:value="markDirty" />
                      </n-space>
                      <n-input v-model:value="form.description" type="textarea" :rows="1" placeholder="报告描述（写作角色定位）" size="small" :input-props="{ 'aria-label': '报告描述' }" @update:value="markDirty" />
                      <n-space size="small" align="center">
                        <n-input v-model:value="form.writer_role" size="small" style="flex:1"
                                 :input-props="{ 'aria-label': '写作角色' }" placeholder="写作角色（如：资深行业研究员）" @update:value="markDirty" />
                        <n-select v-model:value="form.style_card" :options="styleCardOptions" size="small"
                                  style="width:300px" aria-label="文风卡" placeholder="文风卡" clearable @update:value="markDirty" />
                      </n-space>
                      <!-- C：文风卡生效语义说明 -->
                      <div class="ec-muted" style="font-size:12px;margin-top:-2px">
                        文风卡在<b>下一次生成</b>时生效；已生成报告的文风调整走反馈迭代（反馈意见 &gt; 文风卡 &gt; 默认规范）。管理文风卡见页面底部「文风卡管理」。</div>
                      <!-- B1：文风卡内容预览（不能让用户对着名字盲选） -->
                      <n-alert v-if="selectedCard" type="info" size="small" :bordered="true">
                        <div style="font-size:12px;white-space:pre-wrap;max-height:120px;overflow:auto"><b>{{ selectedCard.name }}</b>
{{ selectedCard.rules || selectedCard.desc || '（无规则说明）' }}</div>
                        <div v-for="(e, i) in (selectedCard.excerpts || []).slice(0, 2)" :key="i"
                             class="ec-muted" style="font-size:12px;margin-top:4px">节选{{ i + 1 }}：{{ (e.text || '').slice(0, 80) }}…</div>
                      </n-alert>
                      <n-input v-model:value="form.title_style" type="textarea" :rows="2" placeholder="标题要求" size="small" :input-props="{ 'aria-label': '标题要求' }" @update:value="markDirty" />
                      <n-input v-model:value="form.writing_rules" type="textarea" :rows="3"
                               placeholder="行文规则（每行一条）" size="small" :input-props="{ 'aria-label': '行文规则' }" @update:value="markDirty" />

                      <!-- ===== 节列表（每节：常用字段 + 可折叠高级） ===== -->
                      <div v-for="(s, i) in form.sections" :key="i"
                           style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                        <n-space size="small" align="center">
                          <n-tag size="small" :type="s.origin === 'user' ? 'info' : 'default'">
                            {{ s.origin === 'user' ? '用户指定' : 'AI 补充' }}</n-tag>
                          <n-tag size="tiny" class="ec-mono">{{ s.id || '（未命名）' }}</n-tag>
                          <n-input v-model:value="s.title" size="small" :input-props="{ 'aria-label': '节标题' }" placeholder="节标题" style="width:170px" @update:value="() => onTitleInput(s)" />
                          <n-select v-model:value="s.kind" :options="KINDS" size="small" style="width:150px" aria-label="章节类型"
                                    @update:value="(v) => onKindChange(s, v)" />
                          <span class="ec-muted" style="font-size:12px">排序 {{ i + 1 }}</span>
                          <n-button size="tiny" @click="moveSection(i, -1)">↑</n-button>
                          <n-button size="tiny" @click="moveSection(i, 1)">↓</n-button>
                          <n-button size="tiny" type="error" quaternary @click="delSection(i)">删除</n-button>
                        </n-space>
                        <n-input v-model:value="s.style" type="textarea" :rows="2" size="small"
                                 :id="'sec-' + s.id + '-style'" :input-props="{ 'aria-label': '节 ' + s.id + ' 写作要求' }"
                                 placeholder="写作要求：这节写什么、写多长、什么口径" style="margin-top:6px" @update:value="markDirty" />
                        <n-space size="small" align="center" style="margin-top:6px">
                          <span class="ec-dim" style="font-size:12px">数据需求：</span>
                          <n-dynamic-tags v-model:value="s.data_needs" size="small" @update:value="markDirty" />
                        </n-space>
                        <!-- 高级区：默认折叠；已有配置时标题提示 N 项；会话内记忆展开状态 -->
                        <div style="margin-top:6px;border-top:1px dashed var(--ec-line,#e5e7eb);padding-top:6px">
                          <n-button size="tiny" quaternary @click="toggleAdv">
                            {{ advOpen ? '▾' : '▸' }} 高级设置{{ secAdvCount(s) ? '（已配置 ' + secAdvCount(s) + ' 项）' : '' }}
                          </n-button>
                          <div v-if="advOpen" style="margin-top:6px">
                            <n-space size="small" align="center">
                              <span class="ec-dim" style="font-size:12px">节 id：</span>
                              <n-input v-model:value="s.id" size="small" :id="'sec-' + s.id + '-id'"
                                       :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 的 id' }"
                                       placeholder="节标识（生成报告后不要改 id）"
                                       style="width:230px" class="ec-mono" @update:value="() => onIdInput(s)" />
                              <n-input v-model:value="s.heading" size="small" :id="'sec-' + s.id + '-heading'"
                                       :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 显示标题' }"
                                       placeholder="显示标题（如：1　市场供需）" style="width:190px" @update:value="markDirty" />
                            </n-space>
                            <n-space v-if="s.kind === 'text' || s.kind === 'table'" size="small" align="center" style="margin-top:6px">
                              <span class="ec-dim" style="font-size:12px">字数区间：</span>
                              <n-input-number v-model:value="s.body_min" size="small" style="width:100px" :show-button="false"
                                              :input-props="{ 'aria-label': '字数下限' }" @update:value="markDirty" />
                              <span>~</span>
                              <n-input-number v-model:value="s.body_max" size="small" style="width:100px" :show-button="false"
                                              :input-props="{ 'aria-label': '字数上限' }" @update:value="markDirty" />
                              <div v-if="s.kind === 'table'" style="display:inline-flex;align-items:center;gap:8px">
                                <span class="ec-dim" style="font-size:12px">表格模板 id：</span>
                                <n-input v-model:value="s.table" size="small" :id="'sec-' + s.id + '-table'"
                                         :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 引用的表格模板 id' }"
                                         style="width:150px" @update:value="markDirty" />
                              </div>
                            </n-space>
                            <div v-if="s.kind === 'views'">
                              <div v-for="(v, vi) in s.view_slots" :key="vi" style="margin-top:6px">
                                <n-space size="small" align="center">
                                  <n-input v-model:value="v.id" size="small" :id="'sec-' + s.id + '-slot-' + vi"
                                           :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 槽位 ' + (vi + 1) + ' 的 id' }"
                                           style="width:120px" placeholder="槽位id" @update:value="markDirty" />
                                  <n-input v-model:value="v.brief" size="small" :id="'sec-' + s.id + '-slot-brief-' + vi"
                                           :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 槽位 ' + (vi + 1) + ' 的职责' }"
                                           style="width:340px" placeholder="视角职责" @update:value="markDirty" />
                                  <n-dynamic-tags v-model:value="v.data_needs" size="small" @update:value="markDirty" />
                                </n-space>
                              </div>
                              <n-button size="tiny" style="margin-top:6px" @click="addSlot(s)">+ 视角槽位</n-button>
                            </div>
                            <div v-if="s.charts.length">
                              <div v-for="(c, ci) in s.charts" :key="'c'+ci" style="margin-top:6px">
                                <n-space size="small" align="center">
                                  <n-input v-model:value="c.id" size="small" :id="'sec-' + s.id + '-chart-' + ci"
                                           :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 图 ' + (ci + 1) + ' 的 id' }"
                                           style="width:130px" placeholder="图id" @update:value="markDirty" />
                                  <n-input v-model:value="c.title" size="small" style="width:160px"
                                           :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 图 ' + (ci + 1) + ' 的标题' }"
                                           placeholder="图标题" @update:value="markDirty" />
                                  <n-select v-model:value="c.type" :options="CHART_TYPES" size="small" style="width:90px" aria-label="图表类型" @update:value="markDirty" />
                                  <n-input v-model:value="c.source" size="small" style="width:200px"
                                           :input-props="{ 'aria-label': '节 ' + (s.title || i) + ' 图 ' + (ci + 1) + ' 的数据源' }"
                                           placeholder="数据源 table:表id / facts:事实前缀" class="ec-mono" @update:value="markDirty" />
                                  <n-input v-model:value="c.unit" size="small" style="width:80px" placeholder="单位" @update:value="markDirty" />
                                  <n-button size="tiny" quaternary type="error" @click="s.charts.splice(ci, 1); markDirty()">删</n-button>
                                </n-space>
                              </div>
                            </div>
                            <n-button size="tiny" style="margin-top:6px" @click="addChart(s)">+ 图</n-button>
                          </div>
                        </div>
                      </div>
                      <n-space size="small">
                        <n-button size="small" @click="addSection('text')">+ 综述节</n-button>
                        <n-button size="small" @click="addSection('views')">+ 观点节</n-button>
                        <n-button size="small" @click="addSection('table')">+ 表格节</n-button>
                        <n-button size="small" @click="addSection('risk')">+ 风险节</n-button>
                        <n-button size="small" @click="addSection('figures')">+ 图件节</n-button>
                      </n-space>

                      <!-- ===== 树级高级（禁用词 / 受控词表 / 表格模板）：默认折叠 ===== -->
                      <div style="border-top:1px dashed var(--ec-line,#e5e7eb);padding-top:8px">
                        <n-button size="tiny" quaternary @click="toggleAdv">
                          {{ advOpen ? '▾' : '▸' }} 高级：禁用词 · 受控词表 · 表格模板{{ treeAdvCount ? '（已配置 ' + treeAdvCount + ' 项）' : '' }}
                        </n-button>
                        <div v-if="advOpen" style="margin-top:8px">
                          <n-space size="small" align="center">
                            <span class="ec-dim" style="font-size:12px">禁用词：</span>
                            <n-dynamic-tags v-model:value="form.forbidden_words" size="small" @update:value="markDirty" />
                            <span class="ec-muted" style="font-size:12px">生成与校验时拦截这些词</span>
                          </n-space>
                          <div style="margin-top:8px">
                            <n-space size="small" align="center">
                              <span class="ec-dim" style="font-size:12px">受控词表（键 → 允许的词组，逗号分隔）：</span>
                              <n-button size="tiny" @click="addVocabRow">+ 一组</n-button>
                            </n-space>
                            <div v-for="(kv, ki) in form.controlled_vocab" :key="ki" style="margin-top:4px">
                              <n-space size="small" align="center">
                                <n-input v-model:value="kv.key" size="small" style="width:140px" :input-props="{ 'aria-label': '受控词表 ' + (ki + 1) + ' 键' }" placeholder="键（如：储量单位）" @update:value="markDirty" />
                                <n-input v-model:value="kv.words" size="small" style="flex:1" :input-props="{ 'aria-label': '受控词表 ' + (ki + 1) + ' 允许词组' }" placeholder="允许词组（如：万t,万吨）" @update:value="markDirty" />
                                <n-button size="tiny" quaternary type="error" @click="form.controlled_vocab.splice(ki, 1); markDirty()">删</n-button>
                              </n-space>
                            </div>
                          </div>
                          <div style="margin-top:8px">
                            <n-space size="small" align="center">
                              <span class="ec-dim" style="font-size:12px">表格模板：</span>
                              <n-button size="tiny" @click="addTable">+ 表格模板</n-button>
                            </n-space>
                            <div v-for="(t, ti) in form.tables" :key="ti" style="margin-top:4px">
                              <n-space size="small" align="center">
                                <n-input v-model:value="t.id" size="small" style="width:140px" :input-props=\"{ 'aria-label': '表格模板 ' + (ti + 1) + ' 的 id' }\" placeholder="表id" @update:value="markDirty" />
                                <n-select v-model:value="t.renderer" :options="RENDERERS" size="small" style="width:210px" aria-label="表格渲染器" @update:value="markDirty" />
                                <n-input v-model:value="t.source_prefix" size="small" style="width:170px" :input-props=\"{ 'aria-label': '表格模板 ' + (ti + 1) + ' 事实 id 前缀' }\"
                                         placeholder="facts_rows：事实id前缀（如 rag）" class="ec-mono" @update:value="markDirty" />
                                <n-button size="tiny" quaternary type="error" @click="form.tables.splice(ti, 1); markDirty()">删</n-button>
                              </n-space>
                            </div>
                          </div>
                        </div>
                      </div>
                    </n-space>
                  </n-tab-pane>

                  <n-tab-pane name="versions" tab="版本与日志">
                    <n-space vertical size="small">
                      <n-list show-divider>
                        <n-list-item v-for="v in detail.versions" :key="v.version">
                          <n-space size="small" align="center" justify="space-between">
                            <span>v{{ v.version }} <span class="ec-muted">（{{ (v.updated_at || '').replace('T', ' ') }}，{{ v.status }}）</span></span>
                            <n-button size="tiny" @click="rollback(v.version)">回滚到此版</n-button>
                          </n-space>
                        </n-list-item>
                      </n-list>
                      <n-log v-if="detail.ops.length" :log="detail.ops.map(o => o.ts + ' [' + o.actor + '] ' + o.summary + ' → v' + o.version_after).join('\\n')"
                             style="max-height:220px;font-size:12px" />
                    </n-space>
                  </n-tab-pane>

</n-tabs>
              </n-card>
            </template>
          </n-gi>
        </n-grid>

        <!-- C：文风卡管理（页面底部独立卡片，全局资产） -->
        <n-card size="small" class="ec-card" style="margin-top:12px" data-testid="style-card-mgmt">
          <template #header>文风卡管理</template>
          <template #header-extra>
            <n-space size="small">
              <span class="ec-muted" style="font-size:12px">写入立即生效（含手改 YAML）；内置三张只读</span>
              <n-button size="tiny" type="primary" @click="openNewCard">新建文风卡</n-button>
            </n-space>
          </template>
          <n-empty v-if="!styleCards.length" description="还没有文风卡，点「新建文风卡」创建" size="small" />
          <n-list v-else show-divider>
            <n-list-item v-for="c in styleCards" :key="c.id">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
                <div style="min-width:0">
                  <div style="font-weight:600;font-size:13px">{{ c.name }}
                    <n-tag size="tiny" :type="c.source === 'built-in' ? 'default' : 'success'"
                           style="margin-left:6px">{{ c.source === 'built-in' ? '内置' : '自定义' }}</n-tag>
                    <n-tag size="tiny" class="ec-mono" style="margin-left:4px">{{ c.id }}</n-tag>
                  </div>
                  <div class="ec-muted" style="font-size:12px;margin-top:2px">
                    {{ ((c.rules || c.desc || '').split('\\n')[0] || '（无规则说明）').slice(0, 60) }}
                     ｜ 节选×{{ c.n_excerpts || 0 }}</div>
                </div>
                <n-space size="small" :wrap="false">
                  <n-button size="tiny" @click="openEditCard(c)">
                    {{ c.source === 'built-in' ? '复制为自定义' : '编辑' }}</n-button>
                  <n-button size="tiny" quaternary type="error"
                            :disabled="c.source === 'built-in'" @click="deleteCard(c)">删</n-button>
                </n-space>
              </div>
            </n-list-item>
          </n-list>
        </n-card>

        <n-modal v-model:show="showNew" preset="dialog" title="新建结构树" :show-icon="false"
                 positive-text="创建" :loading="creating" @positive-click="createTree">
          <n-space vertical size="small">
            <!-- V4-10：只必填名称与报告主题；id 由系统派生（冲突自动加序号） -->
            <n-input v-model:value="newForm.name" placeholder="名称（如：高纯石英动态简报）" data-testid="new-tree-name" />
            <n-input v-model:value="newForm.subject" placeholder="报告主体（如：国内高纯石英）" data-testid="new-tree-subject" />
            <n-input v-model:value="newForm.description" type="textarea" :rows="2" :input-props="{ 'aria-label': '树描述' }" placeholder="描述（可选，告诉系统这是一份什么报告）" />
            <div style="border:1px dashed var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
              <n-checkbox v-model:checked="newForm._customId" size="small">自定义 id（高级）</n-checkbox>
              <n-input v-if="newForm._customId" v-model:value="newForm.id" class="ec-mono"
                       style="margin-top:6px" size="small" :input-props="{ 'aria-label': '自定义树 id' }"
                       placeholder="树 id（英数下划线或中文短名；冲突会被拒绝）" />
              <div v-else class="ec-muted" style="font-size:12px;margin-top:4px">
                不填 id：系统从名称生成，冲突时自动加短序号。</div>
            </div>
            <span class="ec-muted">创建后可在结构页编辑章节，也可以用对话继续生成整棵树。</span>
          </n-space>
        </n-modal>

        <!-- C：文风卡编辑弹窗（新建 / 编辑 / 复制内置卡为自定义） -->
        <n-modal v-model:show="scShow" preset="card" style="width:660px"
                 :title="scForm._isNew ? (scForm._builtInCopy ? '复制内置卡为自定义' : '新建文风卡') : '编辑文风卡'">
          <n-space vertical size="small">
            <n-space size="small">
              <n-input v-model:value="scForm.id" :disabled="!scForm._isNew" class="ec-mono" :input-props="{ 'aria-label': '文风卡 id' }"
                       placeholder="id（英数下划线，如 my_style）" style="width:230px" />
              <n-input v-model:value="scForm.name" :input-props="{ 'aria-label': '文风卡名称' }" placeholder="名称" style="width:230px" />
            </n-space>
            <n-input v-model:value="scForm.desc" type="textarea" :rows="1" :input-props="{ 'aria-label': '文风卡描述' }"
                     placeholder="描述（一句话说明这张卡的口吻定位）" />
            <n-input v-model:value="scForm.rules" type="textarea" :rows="6" :input-props="{ 'aria-label': '文风卡规则' }"
                     placeholder="规则（多行，每行一条；注入写作提示词）" />
            <n-space size="small" align="center">
              <span class="ec-dim" style="font-size:12px">节选（学其句式与口吻，不引用内容事实）：</span>
              <n-button size="tiny" @click="addExcerpt">+ 一条节选</n-button>
            </n-space>
            <div v-for="(e, i) in scForm.excerpts" :key="i"
                 style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:6px">
              <n-input v-model:value="e.text" type="textarea" :rows="3" :input-props=\"{ 'aria-label': '节选 ' + (i + 1) + ' 正文' }\" placeholder="节选正文" />
              <n-space size="small" align="center" style="margin-top:4px">
                <n-input v-model:value="e.source" size="small" :input-props=\"{ 'aria-label': '节选 ' + (i + 1) + ' 出处' }\" placeholder="出处（可选）" style="flex:1" />
                <n-button size="tiny" quaternary type="error"
                          @click="scForm.excerpts.splice(i, 1)">删</n-button>
              </n-space>
            </div>
          </n-space>
          <template #footer>
            <n-space justify="end">
              <n-button @click="scShow = false">取消</n-button>
              <n-button type="primary" :loading="scSaving" @click="saveCard">保存（立即生效）</n-button>
            </n-space>
          </template>
        </n-modal>
      `,
    },
  };
})();
