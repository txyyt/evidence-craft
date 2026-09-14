/* 模板工作台：结构树列表 + 树编辑器（增删/排序/逐节编辑/lint/版本回滚）+ 生成入口。
   M2 在此追加对话面板（树生成/编辑）与数据计划面板（缺口回执/裁决）。 */
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
  const STAGES = [
    { key: 'data', label: '数据' }, { key: 'outline', label: '大纲' },
    { key: 'sections', label: '分节' }, { key: 'review', label: '评审' },
    { key: 'render', label: '渲染' },
  ];

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
      writing_rules: (sd.writing_rules || []).join('\n'),
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

  EC.views['/trees'] = {
    title: '模板工作台',
    component: {
      setup() {
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

        /* ---- 运行（生成入口）---- */
        const gen = reactive({ intent: '', folder: '', plan: null, model_tier: null });
        const plans = ref([]);
        const runId = ref(null);
        const runStatus = ref('idle');
        const runError = ref('');
        const events = ref([]);
        const currentDir = ref(null);

        async function loadTrees() {
          trees.value = await EC.api.get('/api/trees');
          if (selId.value && !trees.value.some((t) => t.id === selId.value)) selId.value = null;
        }
        async function loadPlans() {
          plans.value = selId.value ? await EC.api.get(`/api/trees/${selId.value}/plans`) : [];
        }
        async function selectTree(id) {
          selId.value = id;
          dirty.value = false;
          runStatus.value = 'idle'; events.value = []; currentDir.value = null;
          if (!id) { detail.value = null; return; }
          loading.value = true;
          try {
            detail.value = await EC.api.get('/api/trees/' + id);
            Object.assign(form, formFromSpec(detail.value.spec_dict, detail.value.meta));
            await loadPlans();
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
        function addSection(kind) { form.sections.push(blankSection(kind)); markDirty(); }
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

        async function saveTree() {
          if (!selId.value) return;
          try {
            const r = await EC.api.put('/api/trees/' + selId.value, {
              spec_dict: specFromForm(form, selId.value),
              meta: { name: form.name, subject: form.subject, status: form.status },
              summary: '编辑器保存' });
            dirty.value = false;
            EC.toast(`已保存 v${r.version}（${(r.lint.errors.length)} 错误 / `
                     + `${r.lint.warnings.length} 提醒）`, 'success');
            await loadTrees();
            await selectTree(selId.value);
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        async function rollback(v) {
          try {
            await EC.api.post(`/api/trees/${selId.value}/rollback`, { version: v });
            EC.toast(`已回滚到 v${v}`, 'success');
            await selectTree(selId.value);
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        async function deleteTree(t) {
          try {
            await EC.api.del('/api/trees/' + t.id);
            EC.toast('结构树已删除', 'success');
            if (selId.value === t.id) selId.value = null;
            await loadTrees();
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        /* ---- 生成 ---- */
        async function startRun() {
          runError.value = ''; events.value = [];
          runStatus.value = 'running';
          try {
            const r = await EC.api.post('/api/runs/from_tree', {
              tree_id: selId.value, intent: gen.intent || null,
              folder: gen.folder || null, plan: gen.plan || null,
              model_tier: gen.model_tier || null });
            runId.value = r.id;
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              events.value.push(ev);
              if (ev.data && ev.data.run_dir)
                currentDir.value = ev.data.run_dir.replace(/\\/g, '/').split('/').pop();
              if (ev.type === 'end') {
                es.close();
                runStatus.value = ev.status === 'done' ? 'done'
                  : ev.status === 'cancelled' ? 'cancelled' : 'error';
                if (ev.status === 'error') runError.value = ev.error || '未知错误';
              }
            };
            es.onerror = () => es.close();
          } catch (e) { runStatus.value = 'error'; runError.value = e.message; }
        }
        async function cancelRun() {
          if (runId.value) { try { await EC.api.post(`/api/runs/${runId.value}/cancel`); } catch (e) { /* 已结束 */ } }
        }
        const stageState = computed(() => {
          const st = {};
          for (const s of STAGES) st[s.key] = { msgs: [] };
          let last = null;
          for (const ev of events.value) {
            if (ev.type !== 'progress') continue;
            (st[ev.stage] || { msgs: [] }).msgs.push(ev);
            if (st[ev.stage]) last = ev.stage;
          }
          const la = STAGES.findIndex((x) => x.key === last);
          STAGES.forEach((s, i) => {
            st[s.key].done = ['done', 'error', 'cancelled'].includes(runStatus.value) || i < la;
            st[s.key].active = runStatus.value === 'running' && i === la;
          });
          return st;
        });

        /* ---- 对话（M2）：树选中=编辑对话；未选中=对话生成新树 ---- */
        const chatMsgs = ref([]);        // [{role:'user'|'assistant', text}]
        const chatInput = ref('');
        const chatBusy = ref(false);

        async function sendChat() {
          const text = chatInput.value.trim();
          if (!text || chatBusy.value) return;
          chatMsgs.value.push({ role: 'user', text });
          chatInput.value = '';
          chatBusy.value = true;
          try {
            if (selId.value) {   // 编辑对话
              const r = await EC.api.post(`/api/trees/${selId.value}/chat`, { message: text });
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
            chatMsgs.value.push({ role: 'assistant', text: '出错：' + e.message });
          } finally { chatBusy.value = false; }
        }

        /* ---- 数据计划（M2）：出计划 / 缺口三态 / 四选一裁决 / 确认 ---- */
        const planJob = reactive({ id: null, status: 'idle', events: [] });
        const currentPlan = ref(null);   // {plan_file, plan}
        const decisions = reactive({});  // need -> decision

        async function startPlan() {
          if (!selId.value) return;
          planJob.id = null; planJob.status = 'running'; planJob.events = [];
          try {
            const r = await EC.api.post(`/api/trees/${selId.value}/plan/start`,
              { intent: gen.intent || '' });
            planJob.id = r.id;
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              planJob.events.push(ev);
              if (ev.data && ev.data.plan_file) {
                currentPlan.value = { plan_file: ev.data.plan_file, plan: ev.data.plan };
                for (const c of (ev.data.plan.needs_coverage || [])) {
                  if (c.status === 'gap') decisions[c.need] = decisions[c.need] || '';
                }
                loadPlans();
              }
              if (ev.type === 'end') { es.close(); planJob.status = ev.status; }
            };
            es.onerror = () => es.close();
          } catch (e) { planJob.status = 'error'; EC.toast(e.message, 'error'); }
        }

        const coverageList = computed(() => (currentPlan.value &&
          currentPlan.value.plan.needs_coverage) || []);
        const unresolvedGaps = computed(() =>
          coverageList.value.filter((c) => c.status === 'gap'));

        async function confirmPlan() {
          if (!currentPlan.value) return;
          const ds = coverageList.value
            .filter((c) => c.status === 'gap' && decisions[c.need])
            .map((c) => ({ need: c.need, decision: decisions[c.need] }));
          if (coverageList.value.some((c) => c.status === 'gap' && !decisions[c.need])) {
            EC.toast('每个 ❌ 缺口都要选一个处理方式', 'warning'); return;
          }
          const body = { plan_file: currentPlan.value.plan_file, decisions: ds };
          const r = await EC.api.post(`/api/trees/${selId.value}/plan/confirm`, body);
          if (!r.ok && r.need_confirm) {
            if (window.confirm(r.need_confirm + '，确认继续？'))
              return confirmPlanAll();
            return;
          }
          if (!r.ok) { EC.toast('还有未裁决缺口：' + (r.unresolved || []).join('、'), 'warning'); return; }
          currentPlan.value.plan.needs_coverage = r.coverage;
          gen.plan = currentPlan.value.plan_file;
          EC.toast('数据计划已确认，可以生成报告', 'success');
        }
        async function confirmPlanAll() {
          const r = await EC.api.post(`/api/trees/${selId.value}/plan/confirm`, {
            plan_file: currentPlan.value.plan_file,
            decisions: [], all_qualitative: true });
          if (r.ok) {
            currentPlan.value.plan.needs_coverage = r.coverage;
            gen.plan = currentPlan.value.plan_file;
            EC.toast('已按全定性生成确认', 'success');
          }
        }

        async function loadPlanContent(name) {
          if (!name || !selId.value) return;
          try {
            const plan = await EC.api.get(
              `/api/trees/${selId.value}/plans/${encodeURIComponent(name)}`);
            currentPlan.value = { plan_file: name, plan };
            for (const c of (plan.needs_coverage || [])) {
              if (c.status === 'gap') decisions[c.need] = decisions[c.need] || '';
            }
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        onMounted(async () => {
          await loadTrees();
          // 深链：#/trees?tree=<id> 直接选中（列表点击之外的可编程入口）
          const qs = location.hash.split('?')[1] || '';
          const tid = new URLSearchParams(qs).get('tree');
          if (tid && trees.value.some((t) => t.id === tid)) await selectTree(tid);
        });

        const lintErrors = computed(() => (detail.value && detail.value.lint.errors) || []);
        const lintWarnings = computed(() => (detail.value && detail.value.lint.warnings) || []);

        return {
          trees, selId, selectTree, detail, form, loading, dirty, lintErrors, lintWarnings,
          showNew, newForm, creating, createTree, KINDS, RENDERERS, GENRES, CHART_TYPES,
          addSection, delSection, moveSection, addSlot, addChart, addTable,
          saveTree, rollback, deleteTree, markDirty,
          gen, plans, loadPlans, startRun, cancelRun, runId, runStatus, runError,
          events, stageState, STAGES, currentDir,
          chatMsgs, chatInput, chatBusy, sendChat,
          planJob, currentPlan, decisions, startPlan, coverageList, unresolvedGaps, confirmPlan,
          loadPlanContent,
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
                      <div style="font-weight:600">{{ t.name }}</div>
                      <div class="ec-muted ec-mono" style="font-size:12px">{{ t.id }} · v{{ t.version }} · {{ t.sections }} 节</div>
                    </div>
                    <n-button size="tiny" quaternary type="error" @click.stop="deleteTree(t)">删</n-button>
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
                      <n-input v-model:value="form.writer_role" size="small" placeholder="写作角色（如：资深行业研究员）" @update:value="markDirty" />
                      <n-input v-model:value="form.title_style" type="textarea" :rows="2" placeholder="标题要求" size="small" @update:value="markDirty" />
                      <n-input v-model:value="form.writing_rules" type="textarea" :rows="3"
                               placeholder="行文规则（每行一条）" size="small" @update:value="markDirty" />

                      <div v-for="(s, i) in form.sections" :key="i"
                           style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                        <n-space size="small" align="center">
                          <n-tag size="small" :type="s.origin === 'user' ? 'info' : 'default'">
                            {{ s.origin === 'user' ? '用户指定' : 'AI 补充' }}</n-tag>
                          <n-input v-model:value="s.id" size="small" placeholder="节id" style="width:130px" @update:value="markDirty" />
                          <n-input v-model:value="s.title" size="small" placeholder="节标题" style="width:150px" @update:value="markDirty" />
                          <n-select v-model:value="s.kind" :options="KINDS" size="small" style="width:150px" @update:value="markDirty" />
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

                  <!-- 生成 -->
                  <n-tab-pane name="gen" tab="生成报告">
                    <n-space vertical size="small">
                      <n-space size="small" align="center">
                        <n-button size="small" :loading="planJob.status === 'running'" @click="startPlan">① 出数据计划</n-button>
                        <span class="ec-muted">按树的数据需求规划检索/联网/数据库查询，并给出缺口回执</span>
                      </n-space>
                      <div v-if="coverageList.length" style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                          <n-space size="small" align="center" justify="space-between">
                            <b style="font-size:13px">数据计划 · 缺口回执（{{ currentPlan.plan_file }}）</b>
                            <n-tag size="small" :type="unresolvedGaps.length ? 'warning' : 'success'">
                              {{ unresolvedGaps.length ? unresolvedGaps.length + ' 个缺口待裁决' : '无未裁决缺口' }}</n-tag>
                          </n-space>
                          <div v-for="c in coverageList" :key="c.need"
                               style="display:flex;gap:10px;align-items:center;margin-top:6px;font-size:13px">
                            <n-tag size="small" :type="c.status === 'covered' ? 'success'
                                    : c.status === 'search' ? 'warning' : c.status === 'gap' ? 'error' : 'default'">
                              {{ c.status === 'covered' ? '✓ 已覆盖' : c.status === 'search' ? '⚠ 需联网/资料'
                                 : c.status === 'gap' ? '✗ 缺口' : '— 已处理(' + c.status + ')' }}</n-tag>
                            <span style="flex:1">{{ c.need }}</span>
                            <span v-if="c.status === 'gap'" style="display:inline-flex;gap:8px;align-items:center">
                              <n-radio-group v-model:value="decisions[c.need]" size="small">
                                <n-radio value="search">去搜</n-radio>
                                <n-radio value="provide_folder">我提供资料</n-radio>
                                <n-radio value="qualitative">定性写</n-radio>
                                <n-radio value="drop">砍掉</n-radio>
                              </n-radio-group>
                            </span>
                          </div>
                          <n-space size="small" style="margin-top:8px">
                            <n-button size="tiny" type="primary" @click="confirmPlan">② 确认裁决</n-button>
                            <span class="ec-muted">确认后自动选中该计划；定性写的节会标注不引数字</span>
                          </n-space>
                      </div>
                      <n-input v-model:value="gen.intent" type="textarea" :rows="2"
                               placeholder="写作意图（可选）：本次报告聚焦什么" @update:value="markDirty" />
                      <n-space size="small" align="center">
                        <n-input v-model:value="gen.folder" size="small" style="width:340px"
                                 placeholder="本地资料文件夹（可选，PDF 自动建检索语料库）" class="ec-mono" />
                        <n-select v-model:value="gen.plan" size="small" style="width:220px" clearable
                                  :options="plans.map(p => ({label: p.name, value: p.name}))"
                                  placeholder="沿用数据计划（可选）"
                                  @update:value="(v) => { if (v) loadPlanContent(v); }" />
                      </n-space>
                      <n-space size="small" align="center">
                        <button class="gen-go" style="background:#2080f0;color:#fff;border:none;
                                border-radius:3px;padding:5px 14px;cursor:pointer;font-size:14px"
                                :disabled="runStatus === 'running'" @click="startRun">
                          {{ runStatus === 'running' ? '生成中…' : '生成报告' }}</button>
                        <n-button v-if="runStatus === 'running'" size="small" type="error" @click="cancelRun">取消</n-button>
                        <span class="ec-muted">意图/文件夹/计划都不填则按树直接生成（无数据支撑，仅定性）</span>
                      </n-space>
                      <n-steps v-if="runStatus === 'running' || runStatus === 'done'" size="small" :current="99">
                        <n-step v-for="s in STAGES" :key="s.key" :title="s.label"
                                :status="stageState[s.key].active ? 'process' : stageState[s.key].done ? 'finish' : 'wait'" />
                      </n-steps>
                      <div v-if="events.length" class="ec-log" style="max-height:200px">
                        <div v-for="(ev, i) in events.filter(e => e.message)" :key="i">{{ ev.message }}</div>
                      </div>
                      <n-alert v-if="runStatus === 'error'" type="error" size="small">{{ runError }}</n-alert>
                      <n-alert v-if="runStatus === 'cancelled'" type="warning" size="small">已取消。</n-alert>
                      <div v-if="runStatus === 'done' && currentDir">
                        <n-space size="small" align="center">
                          <n-tag size="small" class="ec-mono">{{ currentDir }}</n-tag>
                          <n-button size="tiny" tag="a" attrType="a" type="primary"
                                    :href="'/api/runs/report?dir=' + encodeURIComponent(currentDir) + '&format=docx'">下载 docx</n-button>
                          <n-button size="tiny" tag="a" attrType="a" target="_blank"
                                    :href="'/artifacts/' + encodeURIComponent(currentDir) + '/final.html'">新窗口打开</n-button>
                          <n-button size="tiny" tag="a" attrType="a" type="info"
                                    :href="'/#/run?dir=' + encodeURIComponent(currentDir)">反馈迭代</n-button>
                        </n-space>
                        <iframe :src="'/artifacts/' + encodeURIComponent(currentDir) + '/final.html'"
                                style="width:100%;height:600px;border:1px solid var(--ec-line,#e5e7eb);background:#fff"></iframe>
                      </div>
                    </n-space>
                  </n-tab-pane>
                </n-tabs>
              </n-card>
            </template>
          </n-gi>
        </n-grid>

        <n-modal v-model:show="showNew" preset="dialog" title="新建结构树" :show-icon="false"
                 positive-text="创建" :loading="creating" @positive-click="createTree">
          <n-space vertical size="small">
            <n-input v-model:value="newForm.id" placeholder="id（字母数字下划线，如 quartz_brief）" class="ec-mono" />
            <n-input v-model:value="newForm.name" placeholder="名称（如：高纯石英动态简报）" />
            <n-input v-model:value="newForm.subject" placeholder="报告主体（如：国内高纯石英）" />
            <n-input v-model:value="newForm.description" type="textarea" :rows="2" placeholder="描述（可选，告诉系统这是一份什么报告）" />
            <span class="ec-muted">创建后可在结构页编辑章节；M2 起支持对话生成整棵树。</span>
          </n-space>
        </n-modal>
      `,
    },
  };
})();
