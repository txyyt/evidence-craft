/* 模板页（V2 §三 §5，/templates）：树列表（派生 + 报告计数跳报告库过滤）
   + 对话 + 编辑器 + 版本与日志。树模式生成只走向导（/new），本页无生成入口。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;
  window.addEventListener('unhandledrejection', (e) => {
    window.__lastRejection = String((e && e.reason) || e).slice(0, 200);
  });

  const KINDS = [
    { label: '综述段落 text', value: 'text' },
    { label: '核心观点 views', value: 'views' },
    { label: '数据表格 table', value: 'table' },
    { label: '风险提示 risk', value: 'risk' },
    { label: '图件集 figures', value: 'figures' },
  ];
  const RENDERERS = [
    { label: 'facts_rows（事实前缀成表）', value: 'facts_rows' },
    { label: 'generic_rows（绑定表格）', value: 'generic_rows' },
    { label: 'consensus_pe（股票盈利预测）', value: 'consensus_pe' },
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
    title: '模板工作台',
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
        const newForm = reactive({ id: '', name: '', subject: '', description: '' });
        const creating = ref(false);
        const styleCards = ref([]);     // B1：文风卡（含规则与节选预览）

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

        async function loadTrees() {
          trees.value = await EC.api.get('/api/trees');
          if (selId.value && !trees.value.some((t) => t.id === selId.value)) selId.value = null;
        }
        async function selectTree(id) {
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
          if (!newForm.id || !newForm.name) { EC.toast('id 与名称必填', 'warning'); return; }
          creating.value = true;
          try {
            await EC.api.post('/api/trees', { ...newForm });
            showNew.value = false;
            EC.toast('结构树已创建', 'success');
            await loadTrees();
            await selectTree(newForm.id);
            Object.assign(newForm, { id: '', name: '', subject: '', description: '' });
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
          if (!text || chatBusy.value) return;
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
                chatMsgs.value.push({ role: 'assistant', text: '需要补充：' + r.questions.join('；') });
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

        onMounted(async () => {
          await Promise.all([loadTrees(), loadRunRows(), loadStyleCards()]);
          // 深链：#/templates?tree=<id> 直接选中（列表点击之外的可编程入口）
          const qs = location.hash.split('?')[1] || '';
          const tid = new URLSearchParams(qs).get('tree');
          if (tid && trees.value.some((t) => t.id === tid)) await selectTree(tid);
        });

        const lintErrors = computed(() => (detail.value && detail.value.lint.errors) || []);
        const lintWarnings = computed(() => (detail.value && detail.value.lint.warnings) || []);
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
          openNewCard, openEditCard, saveCard, deleteCard, addExcerpt };

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
          trees, selId, selectTree, detail, form, loading, dirty, lintErrors, lintWarnings,
          showNew, newForm, creating, createTree, KINDS, RENDERERS, GENRES, CHART_TYPES,
          addSection, delSection, moveSection, addSlot, addChart, addTable,
          onKindChange, onTitleInput, onIdInput, addVocabRow,
          saveTree, rollback, deleteTree, markDirty,
          styleCards, selectedCard, styleCardOptions, runRows,
          reportCount, goReports, deriveTree,
          chatMsgs, chatInput, chatBusy, sendChat,
          scShow, scSaving, scForm, openNewCard, openEditCard, saveCard,
          deleteCard, addExcerpt,
        };
      },
      template: `
        <n-grid :cols="24" :x-gap="12">
          <!-- 左列：树列表 -->
          <n-gi :span="6">
            <n-card size="small" class="ec-card" title="结构树">
              <template #header-extra>
                <n-button size="tiny" type="primary" @click="showNew = true">新建</n-button>
              </template>
              <n-empty v-if="!trees.length" description="还没有结构树，点「新建」创建" size="small" />
              <n-list v-else hoverable clickable show-divider style="margin:-12px">
                <n-list-item v-for="t in trees" :key="t.id"
                             :style="{background: t.id === selId ? 'var(--ec-hover, #f4f7fb)' : ''}"
                             @click="selectTree(t.id)">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                    <div style="min-width:0">
                      <div style="font-weight:600">{{ t.name }}
                        <!-- F4：lint 状态徽标（0E/0W 绿，有 W 黄，有 E 红） -->
                        <n-tag v-if="t.lint" size="tiny"
                               :type="t.lint.errors ? 'error' : t.lint.warnings ? 'warning' : 'success'"
                               style="margin-left:6px"
                               :title="'lint：' + t.lint.errors + ' 错误 / ' + t.lint.warnings + ' 提醒'">
                          {{ t.lint.errors }}E/{{ t.lint.warnings }}W</n-tag>
                      </div>
                      <div class="ec-muted ec-mono" style="font-size:12px">{{ t.id }} · v{{ t.version }} · {{ t.sections }} 节</div>
                      <div style="font-size:12px;margin-top:2px">
                        <a style="cursor:pointer;color:#2080f0" @click.stop="goReports(t.id)">
                          {{ reportCount(t.id) }} 份报告</a></div>
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

          <!-- 右列：对话卡（常驻）+ 编辑器 / 生成 -->
          <n-gi :span="18">
            <n-card size="small" class="ec-card"
                    :title="selId ? '结构对话（编辑当前树）' : '对话生成结构树'">
              <n-space vertical size="small">
                <n-alert v-if="!selId" type="info" size="small">
                  描述你要的报告（越详细越好：章节、每节内容、要哪些表和图）。
                  例：「我需要一份国内高纯石英动态简报，包括市场供需、勘查进展、政策动向，
                  市场供需里要有消费结构表。」信息足够时系统直接出树，太糊会先反问。
                </n-alert>
                <n-alert v-else type="info" size="small">
                  当前编辑 <b>{{ form.name }}</b>：直接说要改什么
                  （如「市场那节拆成两节」「加一节讲政策，放在现状后面」），确认后自动应用为新版本。
                  要生成新树请先在左侧取消选中。
                </n-alert>
                <div class="ec-log" style="max-height:260px">
                  <div v-for="(m, i) in chatMsgs" :key="i"
                       :style="{color: m.role === 'user' ? '' : '#2080f0'}">
                    <b>{{ m.role === 'user' ? '我' : '助手' }}：</b>{{ m.text }}
                  </div>
                  <div v-if="!chatMsgs.length" class="ec-muted">还没有对话。</div>
                </div>
                <n-space size="small" align="center">
                  <n-input v-model:value="chatInput" type="textarea" :rows="2"
                           style="flex:1"
                           :placeholder="selId ? '要改什么？（Ctrl+Enter 发送）' : '描述你的报告需求…（Ctrl+Enter 发送）'"
                           @keydown.enter.ctrl="sendChat" />
                </n-space>
                <n-space size="small" align="center">
                  <n-button size="small" type="primary" :loading="chatBusy" @click="sendChat">
                    {{ selId ? '发送修改意见' : '生成结构树' }}</n-button>
                  <n-button v-if="!selId" size="small" quaternary @click="chatMsgs = []; chatInput = ''">清空对话</n-button>
                </n-space>
              </n-space>
            </n-card>

            <n-empty v-if="!detail" description="生成或选择一棵结构树后，这里出现编辑器与生成入口"
                     size="small" style="margin-top:12px" />
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
                    <n-button size="small" :disabled="!dirty" type="primary" @click="saveTree">保存（新版本）</n-button>
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
                      <n-space size="small">
                        <n-input v-model:value="form.name" placeholder="树名称" size="small" style="width:160px" @update:value="markDirty" />
                        <n-input v-model:value="form.subject" placeholder="报告主体（如：国内高纯石英）" size="small" style="width:220px" @update:value="markDirty" />
                        <n-select v-model:value="form.genre" :options="GENRES" size="small" style="width:150px" @update:value="markDirty" />
                        <n-select v-model:value="form.status" size="small" style="width:110px"
                                  :options="[{label:'草稿',value:'draft'},{label:'已确认',value:'confirmed'}]" @update:value="markDirty" />
                      </n-space>
                      <n-input v-model:value="form.description" type="textarea" :rows="1" placeholder="报告描述（写作角色定位）" size="small" @update:value="markDirty" />
                      <n-space size="small" align="center">
                        <n-input v-model:value="form.writer_role" size="small" style="flex:1"
                                 placeholder="写作角色（如：资深行业研究员）" @update:value="markDirty" />
                        <n-select v-model:value="form.style_card" :options="styleCardOptions" size="small"
                                  style="width:300px" placeholder="文风卡" clearable @update:value="markDirty" />
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
                      <n-input v-model:value="form.title_style" type="textarea" :rows="2" placeholder="标题要求" size="small" @update:value="markDirty" />
                      <n-input v-model:value="form.writing_rules" type="textarea" :rows="3"
                               placeholder="行文规则（每行一条）" size="small" @update:value="markDirty" />
                      <n-space size="small" align="center">
                        <span class="ec-dim" style="font-size:12px">禁用词：</span>
                        <n-dynamic-tags v-model:value="form.forbidden_words" size="small" @update:value="markDirty" />
                        <span class="ec-muted" style="font-size:12px">生成与校验时拦截这些词</span>
                      </n-space>
                      <div>
                        <n-space size="small" align="center">
                          <span class="ec-dim" style="font-size:12px">受控词表（键 → 允许的词组，逗号分隔）：</span>
                          <n-button size="tiny" @click="addVocabRow">+ 一组</n-button>
                        </n-space>
                        <div v-for="(kv, ki) in form.controlled_vocab" :key="ki" style="margin-top:4px">
                          <n-space size="small" align="center">
                            <n-input v-model:value="kv.key" size="small" style="width:140px" placeholder="键（如：储量单位）" @update:value="markDirty" />
                            <n-input v-model:value="kv.words" size="small" style="flex:1" placeholder="允许词组（如：万t,万吨）" @update:value="markDirty" />
                            <n-button size="tiny" quaternary type="error" @click="form.controlled_vocab.splice(ki, 1); markDirty()">删</n-button>
                          </n-space>
                        </div>
                      </div>

                      <div v-for="(s, i) in form.sections" :key="i"
                           style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                        <n-space size="small" align="center">
                          <n-tag size="small" :type="s.origin === 'user' ? 'info' : 'default'">
                            {{ s.origin === 'user' ? '用户指定' : 'AI 补充' }}</n-tag>
                          <n-input v-model:value="s.id" size="small" placeholder="节标识（建议中文短名；生成报告后不要改 id）"
                                   style="width:230px" class="ec-mono" @update:value="() => onIdInput(s)" />
                          <n-input v-model:value="s.title" size="small" placeholder="节标题" style="width:150px" @update:value="() => onTitleInput(s)" />
                          <n-select v-model:value="s.kind" :options="KINDS" size="small" style="width:150px"
                                    @update:value="(v) => onKindChange(s, v)" />
                          <n-input v-model:value="s.heading" size="small" placeholder="显示标题（如：1　市场供需）" style="width:190px" @update:value="markDirty" />
                          <span class="ec-muted" style="font-size:12px">排序 {{ i + 1 }}</span>
                          <n-button size="tiny" @click="moveSection(i, -1)">↑</n-button>
                          <n-button size="tiny" @click="moveSection(i, 1)">↓</n-button>
                          <n-button size="tiny" type="error" quaternary @click="delSection(i)">删除</n-button>
                        </n-space>
                        <n-input v-model:value="s.style" type="textarea" :rows="2" size="small"
                                 placeholder="写作要求（brief）：这节写什么、写多长、什么口径" style="margin-top:6px" @update:value="markDirty" />
                        <n-space size="small" align="center" style="margin-top:6px">
                          <span class="ec-dim" style="font-size:12px">数据需求：</span>
                          <n-dynamic-tags v-model:value="s.data_needs" size="small" @update:value="markDirty" />
                        </n-space>
                        <n-space v-if="s.kind === 'text' || s.kind === 'table'" size="small" align="center" style="margin-top:6px">
                          <span class="ec-dim" style="font-size:12px">字数区间：</span>
                          <n-input-number v-model:value="s.body_min" size="small" style="width:100px" :show-button="false" @update:value="markDirty" />
                          <span>~</span>
                          <n-input-number v-model:value="s.body_max" size="small" style="width:100px" :show-button="false" @update:value="markDirty" />
                          <div v-if="s.kind === 'table'" style="display:inline-flex;align-items:center;gap:8px">
                            <span class="ec-dim" style="font-size:12px">表格模板 id：</span>
                            <n-input v-model:value="s.table" size="small" style="width:150px" @update:value="markDirty" />
                          </div>
                        </n-space>
                        <div v-if="s.kind === 'views'">
                          <div v-for="(v, vi) in s.view_slots" :key="vi" style="margin-top:6px">
                            <n-space size="small" align="center">
                              <n-input v-model:value="v.id" size="small" style="width:120px" placeholder="槽位id" @update:value="markDirty" />
                              <n-input v-model:value="v.brief" size="small" style="width:340px" placeholder="视角职责" @update:value="markDirty" />
                              <n-dynamic-tags v-model:value="v.data_needs" size="small" @update:value="markDirty" />
                            </n-space>
                          </div>
                          <n-button size="tiny" style="margin-top:6px" @click="addSlot(s)">+ 视角槽位</n-button>
                        </div>
                        <div v-if="s.charts.length">
                          <div v-for="(c, ci) in s.charts" :key="'c'+ci" style="margin-top:6px">
                            <n-space size="small" align="center">
                              <n-input v-model:value="c.id" size="small" style="width:130px" placeholder="图id" @update:value="markDirty" />
                              <n-input v-model:value="c.title" size="small" style="width:160px" placeholder="图标题" @update:value="markDirty" />
                              <n-select v-model:value="c.type" :options="CHART_TYPES" size="small" style="width:90px" @update:value="markDirty" />
                              <n-input v-model:value="c.source" size="small" style="width:200px"
                                       placeholder="数据源 table:表id / facts:事实前缀" class="ec-mono" @update:value="markDirty" />
                              <n-input v-model:value="c.unit" size="small" style="width:80px" placeholder="单位" @update:value="markDirty" />
                              <n-button size="tiny" quaternary type="error" @click="s.charts.splice(ci, 1); markDirty()">删</n-button>
                            </n-space>
                          </div>
                        </div>
                        <n-button size="tiny" style="margin-top:6px" @click="addChart(s)">+ 图</n-button>
                      </div>
                      <n-space size="small">
                        <n-button size="small" @click="addSection('text')">+ 综述节</n-button>
                        <n-button size="small" @click="addSection('views')">+ 观点节</n-button>
                        <n-button size="small" @click="addSection('table')">+ 表格节</n-button>
                        <n-button size="small" @click="addSection('risk')">+ 风险节</n-button>
                        <n-button size="small" @click="addSection('figures')">+ 图件节</n-button>
                      </n-space>

                      <div style="border-top:1px dashed var(--ec-line,#e5e7eb);padding-top:8px">
                        <n-space size="small" align="center">
                          <span class="ec-dim" style="font-size:12px">表格模板：</span>
                          <n-button size="tiny" @click="addTable">+ 表格模板</n-button>
                        </n-space>
                        <div v-for="(t, ti) in form.tables" :key="ti" style="margin-top:4px">
                          <n-space size="small" align="center">
                            <n-input v-model:value="t.id" size="small" style="width:140px" placeholder="表id" @update:value="markDirty" />
                            <n-select v-model:value="t.renderer" :options="RENDERERS" size="small" style="width:210px" @update:value="markDirty" />
                            <n-input v-model:value="t.source_prefix" size="small" style="width:170px"
                                     placeholder="facts_rows：事实id前缀（如 rag）" class="ec-mono" @update:value="markDirty" />
                            <n-button size="tiny" quaternary type="error" @click="form.tables.splice(ti, 1); markDirty()">删</n-button>
                          </n-space>
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
            <n-input v-model:value="newForm.id" placeholder="id（建议中文短名，如 萤石月报；也可用英数下划线）" class="ec-mono" />
            <n-input v-model:value="newForm.name" placeholder="名称（如：高纯石英动态简报）" />
            <n-input v-model:value="newForm.subject" placeholder="报告主体（如：国内高纯石英）" />
            <n-input v-model:value="newForm.description" type="textarea" :rows="2" placeholder="描述（可选，告诉系统这是一份什么报告）" />
            <span class="ec-muted">创建后可在结构页编辑章节；M2 起支持对话生成整棵树。</span>
          </n-space>
        </n-modal>

        <!-- C：文风卡编辑弹窗（新建 / 编辑 / 复制内置卡为自定义） -->
        <n-modal v-model:show="scShow" preset="card" style="width:660px"
                 :title="scForm._isNew ? (scForm._builtInCopy ? '复制内置卡为自定义' : '新建文风卡') : '编辑文风卡'">
          <n-space vertical size="small">
            <n-space size="small">
              <n-input v-model:value="scForm.id" :disabled="!scForm._isNew" class="ec-mono"
                       placeholder="id（英数下划线，如 my_style）" style="width:230px" />
              <n-input v-model:value="scForm.name" placeholder="名称" style="width:230px" />
            </n-space>
            <n-input v-model:value="scForm.desc" type="textarea" :rows="1"
                     placeholder="描述（一句话说明这张卡的口吻定位）" />
            <n-input v-model:value="scForm.rules" type="textarea" :rows="6"
                     placeholder="规则（多行，每行一条；注入写作提示词）" />
            <n-space size="small" align="center">
              <span class="ec-dim" style="font-size:12px">节选（学其句式与口吻，不引用内容事实）：</span>
              <n-button size="tiny" @click="addExcerpt">+ 一条节选</n-button>
            </n-space>
            <div v-for="(e, i) in scForm.excerpts" :key="i"
                 style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:6px">
              <n-input v-model:value="e.text" type="textarea" :rows="3" placeholder="节选正文" />
              <n-space size="small" align="center" style="margin-top:4px">
                <n-input v-model:value="e.source" size="small" placeholder="出处（可选）" style="flex:1" />
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
