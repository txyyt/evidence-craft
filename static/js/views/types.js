/* 报告类型管理：列表 + 详情（结构/对话/样例/数据来源/验证/版本 六个 tab）。
   状态机：草稿 → 已验证（回放通过）→ 已发布（可用于生成）。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  const ADAPTER_FORMS = {
    sqlite_query: [
      { k: 'db_ref', label: '数据库引用', type: 'db', hint: '在系统设置-全局连接中配置' },
      { k: 'query', label: '查询语句', type: 'textarea', hint: '命名参数写法：WHERE 项目 = :project' },
      { k: 'query_params', label: '查询参数', type: 'kv', hint: '值可用 $参数名 / $vocabulary.键 / $ctx.上游产出' },
      { k: 'id_prefix', label: '事实 id 前缀', type: 'text' },
      { k: 'id_column', label: 'id 列', type: 'text' },
      { k: 'name_template', label: '名称模板', type: 'text', hint: '如 {项目} {孔号} 钻探进尺' },
      { k: 'value_columns', label: '取值列', type: 'value_columns' },
      { k: 'as_of_column', label: '日期列', type: 'text' },
    ],
    xlsx_table: [
      { k: 'path', label: '文件或目录', type: 'text' },
      { k: 'pattern', label: '文件名匹配', type: 'text', hint: '目录下通配，如 *.xlsx；取修改时间最新' },
      { k: 'id_prefix', label: '事实 id 前缀', type: 'text' },
      { k: 'id_column', label: 'id 列', type: 'text' },
      { k: 'name_template', label: '名称模板', type: 'text' },
      { k: 'value_columns', label: '取值列', type: 'value_columns' },
      { k: 'as_of_column', label: '日期列', type: 'text' },
    ],
    rag_client: [
      { k: 'mock_fragments', label: '片段文件', type: 'text', hint: '全局连接为 mock 时使用' },
      { k: 'query', label: '检索词', type: 'text', hint: '可用 $参数名 引用生成参数' },
      { k: 'top_k', label: '取前 K 条', type: 'number' },
      { k: 'id_prefix', label: '事实 id 前缀', type: 'text' },
      { k: 'value_columns', label: '取值列', type: 'value_columns' },
    ],
  };

  const KIND_FIELDS = {
    views: '结论观点：多条小标题+论述，逐槽位独立成文',
    table: '数据表格 + 说明文字（表格由代码渲染）',
    risk: '风险提示或不确定性说明',
    text: '综述背景类段落',
    figures: '图件集（M9 扩展）',
  };

  const STATUS_META = {
    draft: { label: '草稿', type: 'default' },
    verified: { label: '已验证', type: 'info' },
    published: { label: '已发布', type: 'success' },
  };

  EC.views['/types'] = {
    title: '报告类型管理',
    component: {
      setup() {
        /* ---- 列表 ---- */
        const list = ref([]);
        const loadingList = ref(false);
        const showNew = ref(false);
        const newForm = reactive({ id: '', name: '', description: '' });
        const creating = ref(false);
        const copyForm = reactive({ show: false, src: '', new_id: '', new_name: '' });

        /* ---- 详情 ---- */
        const cur = ref(null);           // GET /api/types/{id} 全量
        const curId = ref('');
        const tab = ref('structure');
        const dirty = ref(false);
        const saving = ref(false);
        const editSpec = ref(null);
        const advanced = ref(false);
        const advText = ref('');
        const showDelete = ref(false);
        const deleteFiles = ref(true);
        const busyLog = ref([]);
        const extracting = ref(false);
        const extractFiles = ref([]);

        /* ---- 对话 ---- */
        const chatInput = ref('');
        const chatting = ref(false);
        const pending = ref(null);

        /* ---- 样例 ---- */
        const leftSample = ref('');
        const sampleTree = ref(null);
        const fewshotTarget = ref(null);   // {si, vi} 待设置范文的槽位
        const selectedText = ref('');

        /* ---- 数据来源 ---- */
        const adapters = ref([]);
        const adapterInfo = ref({});       // key -> {summary, param_schema, ctx_keys}
        const testResult = reactive({});
        const testing = reactive({});
        const runParams = reactive({});
        const connections = ref({ databases: {}, rag: {} });

        /* ---- 验证 ---- */
        const replaySample = ref('');
        const replayRounds = ref(2);
        const dryParams = reactive({});

        /* ---- 版本 ---- */
        const ver = reactive({ versions: [], a: 'current', b: 'current', diff: '', changed: false });

        const detail = computed(() => cur.value);
        const statusMeta = (s) => STATUS_META[s] || STATUS_META.draft;

        async function loadList() {
          loadingList.value = true;
          try { list.value = await EC.api.get('/api/types'); }
          finally { loadingList.value = false; }
        }

        async function open(id) {
          curId.value = id;
          const d = await EC.api.get('/api/types/' + id);
          cur.value = d;
          editSpec.value = d.spec;
          advText.value = d.spec ? JSON.stringify(d.spec, null, 2) : '';
          dirty.value = false;
          pending.value = null;
          fewshotTarget.value = null;
          if (d.samples.length && !leftSample.value) leftSample.value = d.samples[0];
          if (d.samples.length) replaySample.value = d.samples[0];
          for (const k of Object.keys(d.params_schema || {}))
            if (!(k in runParams)) runParams[k] = '';
        }

        function backToList() { cur.value = null; curId.value = ''; loadList(); }

        async function create() {
          creating.value = true;
          try {
            const r = await EC.api.post('/api/types', { ...newForm });
            showNew.value = false;
            EC.toast('已创建，下一步上传样例提取结构', 'success');
            newForm.id = newForm.name = newForm.description = '';
            await loadList();
            open(r.id);
          } catch (e) { EC.toast('创建失败：' + e.message, 'error'); }
          finally { creating.value = false; }
        }

        async function doCopy() {
          try {
            await EC.api.post('/api/types/' + copyForm.src + '/copy',
              { new_id: copyForm.new_id, new_name: copyForm.new_name });
            EC.toast('已复制', 'success');
            copyForm.show = false;
            loadList();
          } catch (e) { EC.toast('复制失败：' + e.message, 'error'); }
        }

        async function doDelete() {
          try {
            await EC.api.del('/api/types/' + curId.value);
            EC.toast('已删除', 'success');
            showDelete.value = false;
            backToList();
          } catch (e) { EC.toast('删除失败：' + e.message, 'error'); }
        }

        async function setStatus(status, tip) {
          try {
            await EC.api.post('/api/types/' + curId.value + '/status', { status });
            EC.toast(tip || '状态已更新', 'success');
            await open(curId.value);
          } catch (e) { EC.toast('失败：' + e.message, 'error'); }
        }

        /* ---- 结构编辑 ---- */
        function markDirty() { dirty.value = true; }

        const newParamKey = ref('');
        function addParamKey() {
          const k = (newParamKey.value || '').trim();
          if (!k) return;
          cur.value.params_schema = cur.value.params_schema || {};
          cur.value.params_schema[k] = k;
          newParamKey.value = '';
        }
        const kvNew = reactive({});
        function kvAddRow(obj, idx) {
          const row = kvNew[idx] || (kvNew[idx] = { k: '', v: '' });
          if (row.k) obj[row.k] = row.v;
          kvNew[idx] = { k: '', v: '' };
        }

        function skeletonSection(kind) {
          const n = (editSpec.value.sections || []).length + 1;
          const id = 'section_' + n;
          if (kind === 'views')
            return { id, title: '核心观点', kind, n_views: 1, view_style: '',
              view_slots: [{ id: 'slot_1', brief: '', data_needs: [], fewshot: null }] };
          if (kind === 'table')
            return { id, title: '数据表格', kind, style: '', table: id + '_table' };
          if (kind === 'risk')
            return { id, title: '风险提示', kind, strategy: 'enumerate', style: '' };
          return { id, title: '新章节', kind };
        }

        function addSection(kind) {
          editSpec.value.sections = editSpec.value.sections || [];
          const sec = skeletonSection(kind);
          editSpec.value.sections.push(sec);
          if (kind === 'table') {
            editSpec.value.tables = editSpec.value.tables || [];
            editSpec.value.tables.push({ id: sec.table, renderer: 'generic_rows', columns: [] });
          }
          markDirty();
        }

        function delSection(i) {
          const sec = editSpec.value.sections[i];
          editSpec.value.sections.splice(i, 1);
          if (sec.table && editSpec.value.tables)
            editSpec.value.tables = editSpec.value.tables.filter((t) => t.id !== sec.table);
          markDirty();
        }

        function move(arr, i, dir) {
          const j = i + dir;
          if (j < 0 || j >= arr.length) return;
          [arr[i], arr[j]] = [arr[j], arr[i]];
          markDirty();
        }

        function addSlot(si) {
          const sec = editSpec.value.sections[si];
          sec.view_slots = sec.view_slots || [];
          const n = sec.view_slots.length + 1;
          let id = 'slot_' + n;
          while (sec.view_slots.some((s) => s.id === id)) id += '_new';
          sec.view_slots.push({ id, brief: '', data_needs: [], fewshot: null });
          sec.n_views = sec.view_slots.length;
          markDirty();
        }

        function syncViewsCount(si) {
          const sec = editSpec.value.sections[si];
          if (Array.isArray(sec.view_slots)) sec.n_views = sec.view_slots.length;
          markDirty();
        }

        function tableOf(tid) {
          editSpec.value.tables = editSpec.value.tables || [];
          return editSpec.value.tables.find((t) => t.id === tid);
        }

        async function saveStructure() {
          // 客户端预检：槽位 id 唯一性
          const seen = new Set();
          for (const sec of editSpec.value.sections || [])
            for (const s of sec.view_slots || []) {
              if (seen.has(s.id)) {
                EC.toast(`槽位 id 重复：${s.id}（每个槽位 id 必须唯一）`, 'error');
                return;
              }
              seen.add(s.id);
            }
          saving.value = true;
          try {
            let r;
            if (advanced.value)
              r = await EC.api.put(`/api/types/${curId.value}/report-yaml`, { text: advText.value });
            else
              r = await EC.api.put(`/api/types/${curId.value}/report`, { spec: editSpec.value });
            editSpec.value = r.spec;
            advText.value = JSON.stringify(r.spec, null, 2);
            dirty.value = false;
            EC.toast('结构已保存（旧版自动留痕）', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        /* ---- 对话 ---- */
        async function sendChat() {
          const text = chatInput.value.trim();
          if (!text) return;
          chatting.value = true;
          try {
            const r = await EC.api.post(`/api/types/${curId.value}/patch`, { message: text });
            cur.value.chat = r.chat;
            chatInput.value = '';
            pending.value = r.error ? { error: r.error } : r;
          } catch (e) { EC.toast('对话失败：' + e.message, 'error'); }
          finally { chatting.value = false; }
        }

        async function applyPatch() {
          try {
            const r = await EC.api.post(`/api/types/${curId.value}/apply`,
              { ops: pending.value.ops });
            editSpec.value = r.spec;
            advText.value = JSON.stringify(r.spec, null, 2);
            dirty.value = false;
            pending.value = null;
            EC.toast('修改已应用（旧版自动留痕）', 'success');
          } catch (e) { EC.toast('应用失败：' + e.message, 'error'); }
        }

        /* ---- 样例与提取 ---- */
        async function startExtract() {
          if (!extractFiles.value.length) { EC.toast('请先选择样例文件', 'warning'); return; }
          const fd = new FormData();
          for (const f of extractFiles.value) fd.append('files', f.file, f.name);
          extracting.value = true;
          busyLog.value = [];
          try {
            const r = await fetch(`/api/types/${curId.value}/extract`, { method: 'POST', body: fd });
            if (!r.ok) throw new Error(((await r.json()).detail) || r.statusText);
            const { events_url } = await r.json();
            listen(events_url);
          } catch (e) { extracting.value = false; EC.toast('上传失败：' + e.message, 'error'); }
        }

        function listen(url) {
          busyLog.value = [];
          const es = new EventSource(url);
          es.onmessage = (m) => {
            const ev = JSON.parse(m.data);
            if (ev.message) busyLog.value.push(ev.message);
            if (ev.type === 'end') {
              es.close();
              extracting.value = false;
              if (ev.status !== 'done') EC.toast('任务失败：' + (ev.error || '').slice(-200), 'error');
              else EC.toast('完成', 'success');
              open(curId.value);
            }
          };
          es.onerror = () => es.close();
        }

        async function loadSample(name) {
          leftSample.value = name;
          try {
            sampleTree.value = await EC.api.get(
              `/api/types/${curId.value}/sample/${encodeURIComponent(name)}`);
          } catch (e) { sampleTree.value = null; }
        }

        function grabFewshot() {
          // 在样例正文上划选文字后调用
          const sel = window.getSelection ? window.getSelection().toString() : '';
          if (!sel || sel.length < 10) { EC.toast('请先在下方正文划选一段文字（至少 10 字）', 'warning'); return; }
          if (!fewshotTarget.value) { EC.toast('先在"结构"页的槽位上点"从样例划选范文"', 'warning'); return; }
          const { si, vi } = fewshotTarget.value;
          editSpec.value.sections[si].view_slots[vi].fewshot = sel.trim();
          markDirty();
          EC.toast(`已设为槽位「${editSpec.value.sections[si].view_slots[vi].id}」的范文，记得保存`, 'success');
          tab.value = 'structure';
        }

        function startFewshotPick(si, vi) {
          fewshotTarget.value = { si, vi };
          tab.value = 'samples';
          if (leftSample.value && !sampleTree.value) loadSample(leftSample.value);
          EC.toast('在下方正文划选一段文字，然后点确认条', 'info');
        }

        /* ---- 数据来源 ---- */
        async function saveSourcesOnly() {
          saving.value = true;
          try {
            await EC.api.put(`/api/types/${curId.value}/sources`, { sources: {
              name: cur.value.name, description: cur.value.description,
              params_schema: cur.value.params_schema, vocabulary: cur.value.vocabulary,
              features: cur.value.features, crosschecks: cur.value.crosschecks,
              judge_reference: cur.value.judge_reference,
              bindings: cur.value.bindings,
            } });
            EC.toast('数据来源已保存', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        function addBinding() {
          cur.value.bindings = cur.value.bindings || [];
          cur.value.bindings.push({ need: '', adapter: '', params: {} });
        }

        /* 表单字段：前端富表单（sqlite/xlsx/rag）优先，其余用适配器自声明 */
        function bindingFields(b) {
          if (ADAPTER_FORMS[b.adapter]) return ADAPTER_FORMS[b.adapter];
          const info = adapterInfo.value[b.adapter];
          if (info && (info.param_schema || []).length)
            return info.param_schema.map((f) => ({
              k: f.k, label: f.label || f.k, required: !!f.required,
              type: f.type || 'text', hint: f.hint || '', ph: f.ph || '',
            }));
          return null;
        }
        function adapterSummary(b) {
          return (adapterInfo.value[b.adapter] || {}).summary || '';
        }
        function adapterCtxKeys(b) {
          return (adapterInfo.value[b.adapter] || {}).ctx_keys || [];
        }

        /* ---- $ 引用：可用变量与插入 ---- */
        function availableRefs(i) {
          const c = cur.value;
          if (!c) return [];
          const refs = [];
          Object.entries(c.params_schema || {}).forEach(([k, label]) =>
            refs.push({ text: '$' + k, label: '生成参数 · ' + label }));
          Object.keys(c.vocabulary || {}).forEach((k) =>
            refs.push({ text: '$vocabulary.' + k, label: '词表 · ' + k }));
          for (let j = 0; j < i; j++) {
            const b = (c.bindings || [])[j] || {};
            (adapterCtxKeys(b) || []).forEach((k) =>
              refs.push({ text: '$ctx.' + k, label: '上游 #' + j + ' ' + (b.need || '') }));
          }
          const seen = new Set();
          return refs.filter((r) => !seen.has(r.text) && seen.add(r.text));
        }
        function insertRef(b, k, text) {
          b.params = b.params || {};
          const v = String(b.params[k] ?? '').trim();
          b.params[k] = (!v || /^\$[A-Za-z_][\w.]*$/.test(v)) ? text : v + text;
        }

        /* ---- $ctx 顺序校验：引用了下方/不存在的上游产出 → 提前报警 ---- */
        function ctxWarnings(i, b) {
          const c = cur.value;
          if (!c) return [];
          const producers = {};            // ctxKey -> 首个产出它的绑定序号
          (c.bindings || []).forEach((bb, j) => {
            (adapterCtxKeys(bb) || []).forEach((k) => {
              if (!(k in producers)) producers[k] = j;
            });
          });
          const above = new Set();
          for (let j = 0; j < i; j++)
            (adapterCtxKeys((c.bindings || [])[j] || {}) || []).forEach((k) => above.add(k));
          const warns = [];
          const text = Object.values(b.params || {})
            .filter((v) => typeof v === 'string').join(' ');
          for (const m of text.matchAll(/\$ctx\.([A-Za-z_][\w]*)/g)) {
            const key = m[1];
            if (above.has(key)) continue;
            if (key in producers) {
              const j = producers[key];
              warns.push(`$ctx.${key} 由下方第 ${j} 条（${c.bindings[j].need || '未命名'}）产出——请把该条移到本条上方，否则生成时解析不到`);
            } else {
              warns.push(`没有任何绑定产出 $ctx.${key}，生成时该引用会解析失败`);
            }
          }
          return warns;
        }

        /* 表单未覆盖的存量参数（兼容旧配置，不丢失） */
        function extraParamKeys(b) {
          const covered = new Set((bindingFields(b) || []).map((f) => f.k));
          covered.add('value_columns');
          covered.add('table_columns');
          return Object.keys(b.params || {}).filter((k) => !covered.has(k));
        }

        function kvKeys(obj) { return Object.keys(obj || {}); }
        function kvSet(obj, k, v) { obj[k] = v; }
        function kvAdd(obj) {
          const k = prompt('参数名：');
          if (k) obj[k] = '';
        }
        function kvDel(obj, k) { delete obj[k]; }

        function addValueColumn(params) {
          params.value_columns = params.value_columns || [];
          params.value_columns.push({ column: '', key: '', unit: '' });
        }

        async function testBinding(i) {
          testing[i] = true;
          try {
            testResult[i] = await EC.api.post(`/api/types/${curId.value}/bindings/test`,
              { index: i, params: { ...runParams } });
          } catch (e) { testResult[i] = { ok: false, error: e.message }; }
          finally { testing[i] = false; }
        }

        /* ---- 验证 ---- */
        async function startReplay() {
          if (!replaySample.value) { EC.toast('选一个样例用于回放', 'warning'); return; }
          const r = await EC.api.post(`/api/types/${curId.value}/replay`,
            { sample: replaySample.value, rounds: replayRounds.value });
          listen(r.events_url);
        }

        async function startDryrun() {
          const r = await EC.api.post(`/api/types/${curId.value}/dryrun`,
            { params: { ...dryParams } });
          listen(r.events_url);
        }

        const verCols = [
          { title: '时间', key: 'ts', width: 160 },
          { title: '判定', key: 'verdict', width: 150,
            render: (r) => h(NA.NTag, { size: 'small',
              type: r.verdict === 'PASS' ? 'success'
                : r.verdict === 'PASS_WITH_WARN' ? 'warning' : 'error' },
              { default: () => r.verdict }) },
          { title: '字数合规', key: 'len', width: 90,
            render: (r) => `${r.length_ok}/${r.length_total}` },
          { title: '未对账数字', key: 'unknown_total', width: 100 },
          { title: '样例', key: 'sample', width: 160, ellipsis: { tooltip: true } },
        ];

        /* ---- 版本 ---- */
        async function openVersions() {
          ver.versions = (await EC.api.get(`/api/types/${curId.value}/versions`)).versions;
          ver.a = ver.b = 'current'; ver.diff = ''; ver.changed = false;
        }
        async function runDiff() {
          const r = await EC.api.get(`/api/types/${curId.value}/versions/diff?a=${encodeURIComponent(ver.a)}&b=${encodeURIComponent(ver.b)}`);
          ver.diff = r.diff; ver.changed = r.changed;
        }
        async function doRollback() {
          try {
            await EC.api.post(`/api/types/${curId.value}/versions/rollback`,
              { version: ver.b });
            EC.toast(`已回滚到 ${ver.b}（回滚前版本已留痕）`, 'success');
            ver.show = false;
            open(curId.value);
          } catch (e) { EC.toast('回滚失败：' + e.message, 'error'); }
        }

        function watchTab(t) {
          if (t === 'versions') openVersions();
          if (t === 'validate') EC.store.loadTypes && null;
        }

        const primaryAction = computed(() => {
          if (!cur.value) return null;
          const s = cur.value.status;
          if (s === 'published')
            return { label: '撤回发布（回到已验证）', fn: () => setStatus('verified', '已撤回发布') };
          if (s === 'verified')
            return { label: '发布（可用于报告生成）', fn: () => setStatus('published', '已发布'), primary: true };
          return { label: '下一步：回放验证', fn: () => { tab.value = 'validate'; } };
        });

        onMounted(async () => {
          const alist = await EC.api.get('/api/sources/adapters');
          adapterInfo.value = Object.fromEntries(alist.map((a) => [a.key, a]));
          adapters.value = alist.map((a) => ({
            label: a.summary ? `${a.key} · ${a.summary}` : `${a.key}（${a.kind}）`,
            value: a.key }));
          connections.value = await EC.api.get('/api/sources/connections');
          await loadList();
          const m = location.hash.match(/[?&]id=([A-Za-z0-9_]+)/);
          if (m && list.value.some((t) => t.id === m[1])) open(m[1]);
          if (location.hash.includes('action=new')) showNew.value = true;
        });

        const help = (k) => EC.fh(k).desc;

        return {
          list, loadingList, showNew, newForm, creating, copyForm,
          cur, curId, tab, dirty, saving, editSpec, advanced, advText,
          showDelete, deleteFiles, busyLog, extracting, extractFiles,
          chatInput, chatting, pending, sendChat, applyPatch,
          leftSample, sampleTree, fewshotTarget, selectedText,
          loadSample, grabFewshot, startFewshotPick,
          adapters, adapterInfo, testResult, testing, runParams, connections,
          addBinding, bindingFields, adapterSummary, adapterCtxKeys,
          availableRefs, insertRef, ctxWarnings, extraParamKeys,
          kvKeys, kvSet, kvAdd, kvDel, addValueColumn, testBinding,
          replaySample, replayRounds, dryParams, startReplay, startDryrun, verCols,
          ver, openVersions, runDiff, doRollback,
          newParamKey, addParamKey, kvNew, kvAddRow,
          open, backToList, create, doCopy, doDelete, setStatus, saveSourcesOnly, watchTab,
          addSection, delSection, move, addSlot, syncViewsCount, tableOf,
          markDirty, saveStructure, startExtract,
          statusMeta, primaryAction, help, KIND_FIELDS,
          icoUp: EC.ic.up(14), icoDown: EC.ic.down(14), icoTrash: EC.ic.trash(14),
          icoPlus: EC.ic.plus(14), icoHelp: EC.ic.info(13),
        };
      },
      template: `
      <!-- 列表 -->
      <div v-if="!cur">
        <n-card size="small" class="ec-card">
          <n-space>
            <n-button type="primary" @click="showNew = true">
              <template #icon><span v-html="icoPlus"></span></template>新建报告类型</n-button>
            <n-button quaternary @click="loadList">刷新</n-button>
          </n-space>
        </n-card>
        <n-card size="small">
          <n-empty v-if="!list.length && !loadingList" description="还没有报告类型。上传一篇样例报告，让 AI 学会它的结构；也可以先载入内置演示。">
            <template #extra>
              <n-space>
                <n-button type="primary" size="small" @click="showNew = true">新建报告类型</n-button>
              </n-space>
            </template>
          </n-empty>
          <n-list v-else hoverable clickable>
            <n-list-item v-for="t in list" :key="t.id" @click="open(t.id)">
              <n-thing>
                <template #header>
                  <n-space size="small" align="center">
                    <span>{{ t.name }}</span>
                    <span class="ec-mono ec-muted">{{ t.id }}</span>
                    <n-tag size="small" :type="statusMeta(t.status).type">{{ statusMeta(t.status).label }}</n-tag>
                  </n-space>
                </template>
                <template #description>
                  <span class="ec-dim">{{ t.description || '（无说明）' }}</span>
                  <span class="ec-muted">　·　{{ t.params }} 个生成参数 · {{ t.bindings }} 条数据绑定
                    · 指纹 {{ t.fingerprint || '无结构' }}</span>
                </template>
              </n-thing>
              <template #suffix>
                <n-space size="small">
                  <n-button size="tiny" @click.stop="copyForm = { show: true, src: t.id, new_id: t.id + '_copy', new_name: t.name + ' 副本' }">复制</n-button>
                  <n-button size="tiny" quaternary type="error" @click.stop="curId = t.id; showDelete = true">删除</n-button>
                </n-space>
              </template>
            </n-list-item>
          </n-list>
        </n-card>

        <n-modal v-model:show="showNew" preset="card" title="新建报告类型" style="width:480px">
          <n-space vertical>
            <n-input v-model:value="newForm.id" placeholder="id（英文 snake_case，如 monthly_review）" />
            <n-input v-model:value="newForm.name" placeholder="名称（如：月度经营分析）" />
            <n-input v-model:value="newForm.description" type="textarea" :rows="2" placeholder="一句话说明这类报告写什么（可选）" />
            <n-button type="primary" block :loading="creating" @click="create">创建（状态：草稿）</n-button>
          </n-space>
        </n-modal>
        <n-modal v-model:show="copyForm.show" preset="card" title="复制报告类型" style="width:480px">
          <n-space vertical>
            <n-input v-model:value="copyForm.new_id" placeholder="新 id" />
            <n-input v-model:value="copyForm.new_name" placeholder="新名称" />
            <n-button type="primary" block @click="doCopy">复制</n-button>
          </n-space>
        </n-modal>
      </div>

      <!-- 详情 -->
      <div v-else>
        <n-card size="small" class="ec-card">
          <n-space align="center" justify="space-between">
            <n-space align="center" size="small">
              <n-button quaternary size="small" @click="backToList">返回列表</n-button>
              <b style="font-size:15px">{{ cur.name }}</b>
              <span class="ec-mono ec-muted">{{ cur.id }}</span>
              <n-tag size="small" :type="statusMeta(cur.status).type">{{ statusMeta(cur.status).label }}</n-tag>
              <span class="ec-muted" v-if="cur.fingerprint">结构指纹 {{ cur.fingerprint }}</span>
            </n-space>
            <n-space size="small">
              <n-button v-if="primaryAction" :type="primaryAction.primary ? 'primary' : 'default'"
                        size="small" @click="primaryAction.fn">{{ primaryAction.label }}</n-button>
              <n-button size="small" quaternary type="error"
                        @click="showDelete = true">删除</n-button>
            </n-space>
          </n-space>
          <div class="ec-dim" style="margin-top:6px">{{ cur.description || '（无说明）' }}</div>
        </n-card>

        <n-tabs v-model:value="tab" type="line" @update:value="watchTab">
          <!-- 结构 -->
          <n-tab-pane name="structure" tab="结构">
            <n-card size="small" class="ec-card">
              <template #header>
                <n-space size="small" align="center">
                  报告结构
                  <span class="ec-muted">卡片即改即存草稿，点"保存"生效</span>
                </n-space>
              </template>
              <template #header-extra>
                <n-space size="small">
                  <n-button size="tiny" @click="advanced = !advanced">
                    {{ advanced ? '返回卡片视图' : '高级模式（源码）' }}</n-button>
                  <n-button size="tiny" type="primary" :disabled="!dirty" :loading="saving"
                            @click="saveStructure">保存</n-button>
                </n-space>
              </template>

              <template v-if="advanced">
                <n-alert type="info" size="small" style="margin-bottom:8px">
                  高级模式：整份结构的源码（JSON 格式）。各字段含义与对生成的影响见系统内字段说明：
                  章节有 id/title/kind 与各自的形态字段；槽位 id 是回放与数据引用的主键；
                  字数/条数区间超限会被规则校验打回重写。
                </n-alert>
                <n-input v-model:value="advText" type="textarea" class="ec-mono"
                         :autosize="{minRows: 16, maxRows: 28}" style="font-size:12px" />
              </template>
              <template v-else>
                <div style="display:flex;flex-direction:column;gap:10px">
                  <n-card v-for="(sec, si) in editSpec ? editSpec.sections : []" :key="si" size="small">
                    <template #header>
                      <n-space size="small" align="center">
                        <n-tag size="tiny" :bordered="false">{{ sec.kind }}</n-tag>
                        <span>{{ sec.title }}</span>
                      </n-space>
                    </template>
                    <template #header-extra>
                      <n-space size="small">
                        <n-button size="tiny" quaternary @click="move(editSpec.sections, si, -1)">
                          <span v-html="icoUp"></span></n-button>
                        <n-button size="tiny" quaternary @click="move(editSpec.sections, si, 1)">
                          <span v-html="icoDown"></span></n-button>
                        <n-button size="tiny" quaternary type="error"
                                  @click="delSection(si)"><span v-html="icoTrash"></span></n-button>
                      </n-space>
                    </template>
                    <n-form size="small" label-placement="left" label-width="110">
                      <n-form-item label="章节标题">
                        <n-input v-model:value="sec.title" @update:value="markDirty" />
                      </n-form-item>
                      <n-form-item v-if="sec.kind === 'views'" label="段落形态">
                        <n-input v-model:value="sec.view_style" type="textarea" :rows="2"
                                 @update:value="markDirty" />
                      </n-form-item>
                      <n-form-item v-if="sec.kind === 'risk'" label="风险策略">
                        <n-select v-model:value="sec.strategy" @update:value="markDirty"
                          :options="[{label: 'mirror（逐条镜像前文观点）', value: 'mirror'},
                                     {label: 'enumerate（固定清单列举）', value: 'enumerate'}]" />
                      </n-form-item>
                      <n-form-item v-if="sec.kind === 'risk' || sec.kind === 'table'" label="说明写法">
                        <n-input v-model:value="sec.style" type="textarea" :rows="2" @update:value="markDirty" />
                      </n-form-item>
                      <n-form-item v-if="sec.check && sec.check.body_len" label="正文字数区间">
                        <n-space size="small" align="center">
                          <n-input-number v-model:value="sec.check.body_len[0]" size="small" style="width:100px" @update:value="markDirty" />
                          <span>至</span>
                          <n-input-number v-model:value="sec.check.body_len[1]" size="small" style="width:100px" @update:value="markDirty" />
                        </n-space>
                      </n-form-item>
                    </n-form>
                    <div v-if="sec.view_slots">
                      <div class="ec-muted" style="margin:4px 0 6px">观点槽位（每槽位独立生成一条观点）</div>
                      <div v-for="(slot, vi) in sec.view_slots" :key="vi"
                           style="border:1px solid var(--ec-line);border-radius:6px;padding:8px;margin-bottom:6px">
                        <n-space size="small" align="center">
                          <n-tag size="tiny" class="ec-mono">{{ slot.id }}</n-tag>
                          <span class="ec-muted" style="font-size:12px">
                            {{ (slot.data_needs || []).join(' / ') || '未填数据需求' }}</span>
                          <n-button size="tiny" quaternary @click="move(sec.view_slots, vi, -1)"><span v-html="icoUp"></span></n-button>
                          <n-button size="tiny" quaternary @click="move(sec.view_slots, vi, 1)"><span v-html="icoDown"></span></n-button>
                          <n-button size="tiny" quaternary type="error"
                                    @click="sec.view_slots.splice(vi, 1); syncViewsCount(si)"><span v-html="icoTrash"></span></n-button>
                        </n-space>
                        <n-input v-model:value="slot.id" size="small" class="ec-mono"
                                 placeholder="槽位 id（英文）" style="margin-top:4px" @update:value="markDirty" />
                        <n-input v-model:value="slot.brief" size="small" type="textarea" :rows="2"
                                 placeholder="槽位职责：写什么、用什么数据、怎么论证" style="margin-top:4px" @update:value="markDirty" />
                        <n-input v-if="slot.fewshot" v-model:value="slot.fewshot" size="small"
                                 type="textarea" :rows="3" style="margin-top:4px" @update:value="markDirty" />
                        <n-space size="small" style="margin-top:4px">
                          <n-button size="tiny" @click="startFewshotPick(si, vi)">从样例划选范文</n-button>
                          <span v-if="!slot.fewshot" class="ec-muted" style="font-size:12px">尚无范文（强烈建议设置，直接影响成文质量）</span>
                        </n-space>
                      </div>
                      <n-button size="tiny" dashed @click="addSlot(si)">加一个槽位</n-button>
                    </div>
                    <div v-if="sec.table" class="ec-muted" style="font-size:12px;margin-top:4px">
                      表格模板 {{ sec.table }}：渲染器与表头在 tables 定义中维护（高级模式）
                    </div>
                  </n-card>
                </div>
                <n-space size="small" style="margin-top:10px">
                  <n-dropdown @select="(k) => addSection(k)"
                              :options="Object.keys(KIND_FIELDS).map(k => ({label: KIND_FIELDS[k], key: k}))">
                    <n-button size="small" dashed>添加章节</n-button>
                  </n-dropdown>
                </n-space>
              </template>
            </n-card>
          </n-tab-pane>

          <!-- 对话修改 -->
          <n-tab-pane name="chat" tab="对话修改">
            <n-card size="small">
              <div style="max-height:320px;overflow:auto;margin-bottom:8px">
                <div v-for="(m, i) in cur.chat" :key="i"
                     :style="{textAlign: m.role === 'user' ? 'right' : 'left', margin: '6px 0'}">
                  <n-tag size="tiny" :type="m.role === 'user' ? 'primary' : 'default'">
                    {{ m.role === 'user' ? '我' : 'AI' }}</n-tag>
                  <span style="font-size:13px">{{ m.text }}</span>
                </div>
              </div>
              <n-input v-model:value="chatInput" type="textarea" :rows="2"
                       placeholder="例：风险提示改成三段式；把第二条观点的范文换成更短的段落" />
              <n-space style="margin-top:6px">
                <n-button type="primary" size="small" :loading="chatting" @click="sendChat">发送修改要求</n-button>
              </n-space>
              <n-alert v-if="pending && pending.error" type="error" size="small" style="margin-top:8px">
                修改失败：{{ pending.error }}
              </n-alert>
              <n-card v-if="pending && !pending.error" size="small" title="diff 预览（确认后才应用）" style="margin-top:8px">
                <div class="ec-diff" style="max-height:180px;overflow:auto">
                  <div v-for="(d, i) in pending.diff" :key="i">
                    <b>{{ d.op }}</b> {{ d.path }}
                    <span v-if="d.op === 'change'">：{{ JSON.stringify(d.before).slice(0, 60) }} → {{ JSON.stringify(d.after).slice(0, 60) }}</span>
                  </div>
                </div>
                <n-space style="margin-top:6px">
                  <n-button size="tiny" type="primary" @click="applyPatch">确认应用</n-button>
                  <n-button size="tiny" @click="pending = null">放弃</n-button>
                </n-space>
              </n-card>
            </n-card>
          </n-tab-pane>

          <!-- 样例与提取 -->
          <n-tab-pane name="samples" tab="样例与提取">
            <n-card size="small" class="ec-card">
              <n-space size="small" align="center">
                <n-upload :max="5" :default-upload="false" v-model:file-list="extractFiles">
                  <n-button size="small">选择样例（docx / 文字版 PDF / md / txt）</n-button>
                </n-upload>
                <n-button size="small" type="primary" :loading="extracting" @click="startExtract">
                  {{ extracting ? '提取中…' : '上传并提取结构' }}</n-button>
                <span class="ec-muted">重新提取会覆盖当前结构（旧版自动留痕）</span>
              </n-space>
              <div v-if="busyLog.length" class="ec-log" style="margin-top:8px">
                <div v-for="(l, i) in busyLog" :key="i">{{ l }}</div>
              </div>
            </n-card>
            <n-card size="small" v-if="cur.samples.length">
              <n-space size="small" align="center" style="margin-bottom:8px">
                <span class="ec-dim">样例：</span>
                <n-select :value="leftSample" size="small" style="width:240px"
                          :options="cur.samples.map(s => ({label: s, value: s}))"
                          @update:value="loadSample" />
                <span class="ec-muted">在正文划选文字可设为槽位范文</span>
              </n-space>
              <div v-if="sampleTree" style="max-height:480px;overflow:auto"
                   @mouseup="grabFewshot">
                <div v-for="(b, i) in sampleTree.blocks" :key="i"
                     :style="{marginLeft: (b.type === 'heading' ? (b.level - 1) * 12 : 12) + 'px'}">
                  <b v-if="b.type === 'heading'" style="font-size:13px">{{ b.text }}</b>
                  <div v-else-if="b.type === 'table'" class="ec-muted" style="font-size:12px">[表格 {{ (b.header||[]).length }} 列]</div>
                  <div v-else class="ec-sample-para">{{ b.text || '' }}</div>
                </div>
              </div>
              <div v-if="fewshotTarget" class="ec-slotbar">
                目标槽位：「{{ editSpec.sections[fewshotTarget.si].view_slots[fewshotTarget.vi].id }}」。
                在上方正文划选一段文字（至少 10 字）后松开鼠标即可设置。
                <n-button size="tiny" quaternary style="margin-left:8px"
                          @click="fewshotTarget = null; tab = 'structure'">取消</n-button>
              </div>
            </n-card>
            <n-card v-else size="small"><n-empty description="尚未上传样例" size="small" /></n-card>
          </n-tab-pane>

          <!-- 数据来源 -->
          <n-tab-pane name="sources" tab="数据来源">
            <n-card size="small" class="ec-card">
              <n-alert :bordered="false" type="info" size="small" style="margin-bottom:10px">
                本页定义三件事：① 生成时要人填什么（生成参数）② 每类数据从哪取（绑定）
                ③ 具体怎么取（适配器参数）。绑定从上到下依次执行，下方可引用上方产出（$ctx）。
              </n-alert>
              <n-space size="small" align="center" style="margin-bottom:10px">
                <span class="ec-dim">生成参数（生成时用户要填什么，绑定里用 $参数名 引用）：</span>
                <n-input v-for="(label, key) in cur.params_schema" :key="key"
                         :value="key + ' = ' + label" readonly size="small" style="width:180px" class="ec-mono" />
                <n-input v-model:value="newParamKey" size="small" placeholder="新参数名（英文）" style="width:130px" />
                <n-button size="tiny" @click="addParamKey">加参数</n-button>
              </n-space>
              <n-empty v-if="!(cur.bindings || []).length" description="还没有数据绑定" size="small" style="margin:20px 0">
                <template #extra>
                  <div class="ec-muted" style="font-size:12px;max-width:480px;line-height:1.9;text-align:left">
                    三步配好数据来源：<br>
                    ① 对照样例报告，列出正文用到的数据类（行情、财务、行业对比……）<br>
                    ② 每类数据加一条绑定：选数据源、按表单填参数<br>
                    ③ 右上角填测试用参数，逐条点「测试」验证取数正常
                  </div>
                  <n-button size="small" type="primary" style="margin-top:10px" @click="addBinding">添加第一条绑定</n-button>
                </template>
              </n-empty>
              <template v-else>
                <n-space size="small" align="center" style="margin-bottom:8px">
                  <span class="ec-dim">数据绑定（每类数据一条，从上到下依次执行）</span>
                  <span class="ec-dim" style="margin-left:auto">测试用参数（只用于「测试」按钮）：</span>
                  <n-input v-for="(label, key) in cur.params_schema" :key="'rp' + key"
                           v-model:value="runParams[key]" :placeholder="label" size="small" style="width:110px" />
                </n-space>
                <n-space vertical size="small">
                <div v-for="(b, i) in cur.bindings" :key="i"
                     style="border:1px solid var(--ec-line);border-radius:6px;padding:10px">
                  <n-space size="small" align="center">
                    <n-tag size="tiny" class="ec-mono">{{ i }}</n-tag>
                    <span class="ec-dim" style="font-size:12px">数据项名称</span>
                    <n-input v-model:value="b.need" placeholder="如 quote_snapshot" size="small" style="width:160px" class="ec-mono" />
                    <n-select v-model:value="b.adapter" :options="adapters" size="small" filterable
                              style="width:250px" placeholder="选择数据源（附中文说明）" />
                    <n-button size="tiny" :loading="!!testing[i]" @click="testBinding(i)">测试</n-button>
                    <n-button size="tiny" quaternary type="error"
                              @click="cur.bindings.splice(i, 1)"><span v-html="icoTrash"></span></n-button>
                  </n-space>
                  <div v-if="adapterSummary(b)" class="ec-muted" style="font-size:12px;margin-top:4px">
                    {{ adapterSummary(b) }}
                    <span v-if="(adapterCtxKeys(b) || []).length" class="ec-dim">· 产出
                      <span class="ec-mono">{{ adapterCtxKeys(b).map((k) => '$ctx.' + k).join('、') }}</span>
                      供下方绑定引用</span>
                  </div>
                  <div style="margin-top:8px">
                    <template v-if="bindingFields(b)">
                      <div v-for="f in bindingFields(b)" :key="f.k" style="margin-bottom:6px">
                        <div class="ec-muted" style="font-size:12px;margin-bottom:2px">{{ f.label }}
                          <span v-if="f.required" style="color:#d03050">＊</span>
                          <span v-if="f.hint">（{{ f.hint }}）</span></div>
                        <n-input v-if="f.type === 'text' || f.type === 'db'" v-model:value="b.params[f.k]" size="small" :placeholder="f.ph" class="ec-mono" />
                        <n-input-number v-else-if="f.type === 'number'" v-model:value="b.params[f.k]" size="small" style="width:140px" :placeholder="f.ph" />
                        <n-input v-else-if="f.type === 'textarea'" v-model:value="b.params[f.k]" type="textarea" :rows="2" size="small" class="ec-mono" :placeholder="f.ph" />
                        <div v-else-if="f.type === 'kv'" class="ec-mono" style="font-size:12px">
                          <div class="ec-kv-row" v-for="k in kvKeys(b.params[f.k] || {})" :key="k">
                            <n-tag size="tiny" class="ec-mono">{{ k }} =</n-tag>
                            <n-input :value="String(b.params[f.k][k])" size="small" style="width:240px"
                                     @update:value="(v) => kvSet(b.params[f.k] || (b.params[f.k] = {}), k, v)" />
                            <n-button size="tiny" quaternary @click="kvDel(b.params[f.k] || (b.params[f.k] = {}), k)"><span v-html="icoTrash"></span></n-button>
                          </div>
                          <div class="ec-kv-row">
                            <n-input :value="(kvNew[i] || {}).k" @update:value="(v) => { kvNew[i] = Object.assign(kvNew[i] || {}, { k: v }) }" placeholder="参数名" size="small" style="width:110px" class="ec-mono" />
                            <n-input :value="(kvNew[i] || {}).v" @update:value="(v) => { kvNew[i] = Object.assign(kvNew[i] || {}, { v }) }" placeholder="值（可用 $ 引用）" size="small" style="width:160px" class="ec-mono" />
                            <n-button size="tiny" dashed @click="kvAddRow(b.params[f.k] || (b.params[f.k] = {}), i)">添加</n-button>
                          </div>
                        </div>
                        <div v-else-if="f.type === 'value_columns'" style="font-size:12px">
                          <div class="ec-kv-row" v-for="(vc, ci) in (b.params.value_columns || [])" :key="ci">
                            <n-input v-model:value="vc.column" placeholder="列名" size="small" style="width:120px" />
                            <n-input v-model:value="vc.key" placeholder="事实 key" size="small" style="width:120px" />
                            <n-input v-model:value="vc.unit" placeholder="单位" size="small" style="width:80px" />
                            <n-button size="tiny" quaternary @click="b.params.value_columns.splice(ci, 1)"><span v-html="icoTrash"></span></n-button>
                          </div>
                          <n-button size="tiny" dashed @click="addValueColumn(b.params)">加取值列</n-button>
                        </div>
                        <div v-if="['text', 'db', 'textarea'].includes(f.type) && availableRefs(i).length" style="margin-top:3px">
                          <n-tag v-for="r in availableRefs(i)" :key="r.text" size="tiny" :bordered="false"
                                 type="info" class="ec-mono" style="cursor:pointer;margin-right:4px"
                                 :title="r.label" @click="insertRef(b, f.k, r.text)">{{ r.text }}</n-tag>
                        </div>
                      </div>
                    </template>
                    <div v-if="extraParamKeys(b).length" class="ec-muted" style="font-size:12px;margin:4px 0 2px">
                      其他参数（历史/自定义，删除前请确认无用）</div>
                    <div class="ec-kv-row" v-for="k in extraParamKeys(b)" :key="'x' + k">
                      <n-tag size="tiny" class="ec-mono">{{ k }} =</n-tag>
                      <n-input :value="String((b.params || {})[k])" size="small" style="width:260px" class="ec-mono"
                               @update:value="(v) => kvSet(b.params || (b.params = {}), k, v)" />
                      <n-button size="tiny" quaternary @click="kvDel(b.params || (b.params = {}), k)"><span v-html="icoTrash"></span></n-button>
                    </div>
                    <div class="ec-kv-row">
                      <n-input :value="(kvNew['b' + i] || {}).k" @update:value="(v) => { kvNew['b' + i] = Object.assign(kvNew['b' + i] || {}, { k: v }) }" placeholder="参数名" size="small" style="width:110px" class="ec-mono" />
                      <n-input :value="(kvNew['b' + i] || {}).v" @update:value="(v) => { kvNew['b' + i] = Object.assign(kvNew['b' + i] || {}, { v }) }" placeholder="值（可用 $ 引用）" size="small" style="width:180px" class="ec-mono" />
                      <n-button size="tiny" dashed @click="kvAddRow(b.params || (b.params = {}), 'b' + i)">加参数</n-button>
                    </div>
                  </div>
                  <div v-for="(w, wi) in ctxWarnings(i, b)" :key="'w' + wi" style="font-size:12px;color:#d03050;margin-top:6px">
                    {{ w }}
                  </div>
                  <div v-if="testResult[i]" style="margin-top:8px;font-size:12px">
                    <n-tag size="tiny" :type="testResult[i].ok ? 'success' : 'error'">
                      {{ testResult[i].ok ? 'OK' : '失败' }}</n-tag>
                    <span v-if="testResult[i].ok" class="ec-muted">
                      事实 {{ testResult[i].n_facts }} 条
                      <span v-if="(testResult[i].warnings||[]).length"> · 警告：{{ testResult[i].warnings.join('；') }}</span>
                    </span>
                    <span v-else style="color:#d03050">{{ testResult[i].error || (testResult[i].missing ? '缺参数：' + testResult[i].missing.join('、') : '') }}</span>
                    <div v-if="testResult[i].resolved" class="ec-mono ec-muted" style="margin-top:2px;word-break:break-all">
                      实际参数：{{ JSON.stringify(testResult[i].resolved) }}</div>
                  </div>
                </div>
                <n-button size="small" dashed @click="addBinding">加一条数据绑定</n-button>
                </n-space>
              </template>
              <template #footer>
                <n-space size="small">
                  <n-button type="primary" size="small" :loading="saving" @click="saveSourcesOnly">保存数据来源</n-button>
                  <span class="ec-muted">全局连接（数据库/RAG 地址）在系统设置页维护，这里只引用</span>
                </n-space>
              </template>
            </n-card>
          </n-tab-pane>

          <!-- 验证 -->
          <n-tab-pane name="validate" tab="验证">
            <n-card size="small" class="ec-card" title="回放验证（用样例自身数字检验结构是否可写）">
              <n-space size="small" align="center">
                <n-select v-model:value="replaySample" size="small" style="width:220px"
                          :options="cur.samples.map(s => ({label: s, value: s}))" placeholder="选样例" />
                <n-input-number v-model:value="replayRounds" size="small" :min="1" :max="3" style="width:90px" />
                <n-button size="small" type="primary" @click="startReplay">开始回放</n-button>
                <span class="ec-muted">回放通过后状态自动变为"已验证"</span>
              </n-space>
              <div v-if="busyLog.length" class="ec-log" style="margin-top:8px">
                <div v-for="(l, i) in busyLog" :key="i">{{ l }}</div>
              </div>
            </n-card>
            <n-card size="small" class="ec-card" title="judge 对标范文（评审打分时对照的行文风格基准）">
              <n-space size="small" align="center">
                <n-input v-model:value="cur.judge_reference" size="small" class="ec-mono"
                         placeholder="config/reference/xxx.md（建议必配）" style="width:480px" />
                <n-button size="small" :loading="saving" @click="saveSourcesOnly">保存</n-button>
                <span class="ec-muted">改动后点保存（随数据来源一并写入）</span>
              </n-space>
            </n-card>
            <n-card size="small" class="ec-card" title="回放历史（改进闭环：改一版 → 回放 → 对比）">
              <n-empty v-if="!cur.replay_history.length" description="还没有回放记录" size="small" />
              <template v-else>
                <n-data-table :columns="verCols" :data="cur.replay_history" size="small" :max-height="240" />
                <n-space size="small" align="center" style="margin-top:10px">
                  <span class="ec-dim">对比</span>
                  <n-select v-model:value="ver.a" size="small" style="width:210px"
                            :options="cur.replay_history.map((r, i) => ({label: (i === 0 ? '最新 · ' : '第' + (i + 1) + '次 · ') + r.ts, value: i}))" />
                  <span>与</span>
                  <n-select v-model:value="ver.b" size="small" style="width:210px"
                            :options="cur.replay_history.map((r, i) => ({label: (i === 0 ? '最新 · ' : '第' + (i + 1) + '次 · ') + r.ts, value: i}))" />
                </n-space>
                <n-grid :cols="2" :x-gap="12" v-if="ver.a !== ver.b && cur.replay_history[ver.a] && cur.replay_history[ver.b]">
                  <n-gi>
                    <n-card size="small" :title="'记录 A：' + cur.replay_history[ver.a].verdict">
                      <div style="font-size:12px" class="ec-mono">
                        字数合规 {{ cur.replay_history[ver.a].length_ok }}/{{ cur.replay_history[ver.a].length_total }} ·
                        未对账 {{ cur.replay_history[ver.a].unknown_total }} 个
                      </div>
                    </n-card>
                  </n-gi>
                  <n-gi>
                    <n-card size="small" :title="'记录 B：' + cur.replay_history[ver.b].verdict">
                      <div style="font-size:12px" class="ec-mono">
                        字数合规 {{ cur.replay_history[ver.b].length_ok }}/{{ cur.replay_history[ver.b].length_total }} ·
                        未对账 {{ cur.replay_history[ver.b].unknown_total }} 个
                      </div>
                    </n-card>
                  </n-gi>
                </n-grid>
              </template>
            </n-card>
            <n-card size="small" title="真实数据试跑（需先配好数据来源）">
              <n-space size="small" align="center">
                <n-input v-for="(label, key) in cur.params_schema" :key="key"
                         v-model:value="dryParams[key]" :placeholder="label" size="small" style="width:140px" />
                <n-button size="small" @click="startDryrun">试跑</n-button>
              </n-space>
              <n-alert v-if="cur.dryrun" size="small" type="info" style="margin-top:8px">
                试跑：真实事实 {{ cur.dryrun.n_facts }} 条，标题「{{ cur.dryrun.title }}」
              </n-alert>
            </n-card>
          </n-tab-pane>

          <!-- 版本 -->
          <n-tab-pane name="versions" tab="版本">
            <n-card size="small">
              <n-space size="small" align="center" style="margin-bottom:8px">
                <span class="ec-dim">对比</span>
                <n-select v-model:value="ver.a" size="small" style="width:220px"
                          :options="[{label: '当前版', value: 'current'}, ...ver.versions.map(v => ({label: v.file, value: v.file}))]" />
                <span>与</span>
                <n-select v-model:value="ver.b" size="small" style="width:220px"
                          :options="[{label: '当前版', value: 'current'}, ...ver.versions.map(v => ({label: v.file, value: v.file}))]" />
                <n-button size="small" @click="runDiff">diff</n-button>
              </n-space>
              <n-input v-if="ver.diff" :value="ver.diff" type="textarea" class="ec-mono" readonly
                       :autosize="{minRows: 6, maxRows: 20}" style="font-size:12px" />
              <n-space v-if="ver.changed && ver.b !== 'current'" style="margin-top:8px">
                <n-popconfirm @positive-click="doRollback">
                  <template #trigger><n-button size="small" type="warning">回滚到 {{ ver.b }}</n-button></template>
                  回滚前当前版本会先自动留痕，确认回滚？
                </n-popconfirm>
              </n-space>
            </n-card>
          </n-tab-pane>
        </n-tabs>

        <n-modal v-model:show="showDelete" preset="dialog" title="删除报告类型"
                 type="error" positive-text="确认删除" negative-text="取消"
                 @positive-click="doDelete">
          将删除「{{ cur ? cur.name : '' }}」及其全部版本与样例，不可恢复。确认删除？
        </n-modal>
      </div>
      `,
    },
  };
})();
