/* 新建报告向导（V2 §三 §4，/new）：三步——
   ① 结构：示例树（复制为草稿）/ 空白对话生成 / 现有树选择；
   ② 数据：出计划 → 缺口裁决 → 预检 → 确认（备选：资料夹 / 意图，全空禁下一步）；
   ③ 生成：SSE 进度 + 断线兜底（轮询产物完整度），完成跳详情。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;

  EC.views['/new'] = {
    title: '新建报告',
    component: {
      setup() {
        const NA = window.naive;
        const dialog = NA.useDialog();
        const step = ref(1);

        /* ---------- ① 结构（V3-A：先选起点，再走分支） ---------- */
        const templates = ref([]);       // 内置示例树
        const trees = ref([]);           // 现有树
        const selId = ref(null);
        const detail = ref(null);
        const form = reactive({ name: '', subject: '', status: 'draft' });
        const chatMsgs = ref([]);
        const chatInput = ref('');
        const chatBusy = ref(false);
        const treeDirty = ref(false);
        // A：起点两段式——startSel=起点卡高亮选择，startPoint=已进入的分支
        const startSel = ref(null);
        const startPoint = ref(null);    // null=起点选择视图 | 'templates'|'chat'|'existing'
        const showOutline = ref(false);  // 摘要条「查看结构」展开
        const resultCard = ref(null);    // B：生成/复制成功的树 id（渲染取 detail 最新版）
        const lastCardAction = ref('create');   // B：结果卡标题文案区分 新建/编辑
        const startOptions = [
          { key: 'templates', title: '从示例开始', desc: '用内置模板改一改，最快' },
          { key: 'chat', title: '对话生成', desc: '描述你的报告，AI 出结构' },
          { key: 'existing', title: '选择现有树', desc: '复用之前建好的结构' },
        ];
        function confirmStart() {
          if (!startSel.value) return;
          startPoint.value = startSel.value;
          if (startSel.value === 'existing') loadTrees();
        }
        function changeStart() {
          // A：换起点清空已选树，避免"示例的树 + 对话的语境"串状态
          startSel.value = null;
          startPoint.value = null;
          selId.value = null;
          detail.value = null;
          resultCard.value = null;
          showOutline.value = false;
        }
        function replaceSelection() {
          // 摘要条「更换」：清空当前选择，留在当前分支重选
          selId.value = null;
          detail.value = null;
          resultCard.value = null;
          showOutline.value = false;
        }
        async function switchToChatBranch() {
          // B：示例分支结果卡「继续调整（对话）」→ 切到对话分支（编辑模式）
          startPoint.value = 'chat';
          startSel.value = 'chat';
          setTimeout(() => {
            const el = document.querySelector('#ec-chat-input textarea');
            if (el) el.focus();
          }, 120);
        }

        async function loadTrees() {
          trees.value = await EC.api.get('/api/trees');
          if (selId.value && !trees.value.some((t) => t.id === selId.value)) {
            selId.value = null;
            detail.value = null;
          }
        }
        async function loadTemplates() {
          try { templates.value = await EC.api.get('/api/trees/templates'); }
          catch (e) { templates.value = []; }
        }
        async function selectTree(id) {
          selId.value = id;
          if (!id) { detail.value = null; return; }
          detail.value = await EC.api.get('/api/trees/' + encodeURIComponent(id));
          form.name = detail.value.meta.name;
          form.subject = detail.value.meta.subject || '';
          form.status = detail.value.meta.status || 'draft';
          await loadTrees();
        }
        async function useTemplate(t) {
          try {
            const r = await EC.api.post('/api/trees/templates/copy',
                                        { template_id: t.id });
            EC.toast(`已从示例「${t.name}」复制为草稿「${r.id}」`, 'success');
            await selectTree(r.id);
            lastCardAction.value = 'template';
            resultCard.value = r.id;       // B：结果卡出现
          } catch (e) { EC.toast(e.message, 'error'); }
        }
        async function sendChat() {
          const text = chatInput.value.trim();
          if (!text || chatBusy.value) return;
          chatMsgs.value.push({ role: 'user', text });
          chatInput.value = '';
          chatBusy.value = true;
          try {
            if (selId.value) {
              // B：编辑模式——改这棵（/chat + base_version 乐观锁），不再造新树
              const r = await EC.api.post(
                `/api/trees/${encodeURIComponent(selId.value)}/chat`,
                { message: text,
                  base_version: detail.value ? detail.value.meta.version : null });
              chatMsgs.value.push({ role: 'assistant',
                text: r.changed ? `${r.summary}（v${r.version}）` : r.summary });
              if (r.changed) {
                await selectTree(selId.value);   // 结果卡/摘要条随 detail 刷新（版本+1 可见）
                lastCardAction.value = 'edit';
                resultCard.value = selId.value;
              }
            } else {
              // 生成模式——无选中树时才生成新树
              const msgs = chatMsgs.value.map((m) => ({ role: m.role, text: m.text }));
              const r = await EC.api.post('/api/trees/generate', { messages: msgs });
              if (r.kind === 'questions') {
                chatMsgs.value.push({ role: 'assistant',
                  text: '需要补充：' + r.questions.join('；') });
              } else {
                chatMsgs.value.push({ role: 'assistant',
                  text: `已生成结构树「${r.name}」（${r.spec_dict.sections.length} 节）` });
                await loadTrees();
                await selectTree(r.tree_id);
                lastCardAction.value = 'create';
                resultCard.value = r.tree_id;    // B：结果卡出现
              }
            }
          } catch (e) {
            chatMsgs.value.push({ role: 'assistant', text: '出错：' + e.message });
          } finally { chatBusy.value = false; }
        }
        const structOk = computed(() => !!selId.value);

        /* ---------- ② 数据 ---------- */
        const gen = reactive({ intent: '', folder: '', plan: null });
        const plans = ref([]);
        const planJob = reactive({ status: 'idle' });
        const currentPlan = ref(null);
        const decisions = reactive({});
        const preview = reactive({ status: 'idle', result: null });

        const genHasSource = computed(() =>
          !!(gen.intent.trim() || gen.folder.trim() || gen.plan));
        async function loadPlans() {
          plans.value = selId.value
            ? await EC.api.get(`/api/trees/${encodeURIComponent(selId.value)}/plans`)
            : [];
        }
        async function startPlan() {
          if (!selId.value) return;
          planJob.status = 'running';
          try {
            const r = await EC.api.post(
              `/api/trees/${encodeURIComponent(selId.value)}/plan/start`,
              { intent: gen.intent || '', folder: gen.folder || null });
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              if (ev.data && ev.data.plan_file) {
                currentPlan.value = { plan_file: ev.data.plan_file, plan: ev.data.plan };
                for (const c of (ev.data.plan.needs_coverage || []))
                  if (c.status === 'gap') decisions[c.need] = decisions[c.need] || '';
                loadPlans();
              }
              if (ev.type === 'end') { es.close(); planJob.status = ev.status; }
            };
            es.onerror = () => es.close();
          } catch (e) { planJob.status = 'error'; EC.toast(e.message, 'error'); }
        }
        const coverageList = computed(() => (currentPlan.value &&
          currentPlan.value.plan.needs_coverage) || []);
        // D（P4 收尾）：出计划成功后回执区顶部资料摘要行——
        // 「PDF 语料 N 片段（新建/缓存）｜ Excel M 张（表 id 前 3 个）」，
        // 数据来自 plan.corpus 与 plan.files（make_plan 已落盘，纯前端展示）；
        // 两者皆无时不显示
        const materialLine = computed(() => {
          const plan = (currentPlan.value && currentPlan.value.plan) || {};
          const hasPdf = !!(plan.corpus && plan.corpus.n_fragments);
          const files = plan.files || [];
          if (!hasPdf && !files.length) return '';
          const parts = [hasPdf
            ? `PDF 语料 ${plan.corpus.n_fragments} 片段（${plan.corpus.rebuilt ? '新建' : '缓存复用'}）`
            : 'PDF 无'];
          if (files.length) {
            const ids = files.map((f) =>
              String(f.file || '').replace(/\.xlsx?$/i, '')).slice(0, 3);
            parts.push(`Excel ${files.length} 张（${ids.join('、')}）`);
          }
          return parts.join('｜');
        });
        async function confirmPlan() {
          if (!currentPlan.value) return;
          const ds = coverageList.value
            .filter((c) => c.status === 'gap' && decisions[c.need])
            .map((c) => ({ need: c.need, decision: decisions[c.need] }));
          if (coverageList.value.some((c) => c.status === 'gap' && !decisions[c.need])) {
            EC.toast('每个 ❌ 缺口都要选一个处理方式', 'warning'); return;
          }
          const r = await EC.api.post(
            `/api/trees/${encodeURIComponent(selId.value)}/plan/confirm`,
            { plan_file: currentPlan.value.plan_file, decisions: ds });
          if (!r.ok && r.need_confirm) {
            // 全定性二次确认（页内对话框）
            dialog.warning({
              title: '全定性生成确认',
              content: r.need_confirm + '。生成后正文不会有数据引用，确认继续？',
              positiveText: '确认全定性生成',
              negativeText: '返回裁决',
              onPositiveClick: () => confirmPlanAll(),
            });
            return;
          }
          if (!r.ok) { EC.toast('还有未裁决缺口', 'warning'); return; }
          currentPlan.value.plan.needs_coverage = r.coverage;
          gen.plan = currentPlan.value.plan_file;
          EC.toast('数据计划已确认', 'success');
        }
        async function confirmPlanAll() {
          const r = await EC.api.post(
            `/api/trees/${encodeURIComponent(selId.value)}/plan/confirm`,
            { plan_file: currentPlan.value.plan_file, decisions: [],
              all_qualitative: true });
          if (r.ok) {
            currentPlan.value.plan.needs_coverage = r.coverage;
            gen.plan = currentPlan.value.plan_file;
            EC.toast('已按全定性确认', 'success');
          }
        }
        async function previewPlan() {
          if (!gen.plan) return;
          preview.status = 'running'; preview.result = null;
          try {
            preview.result = await EC.api.post(
              `/api/trees/${encodeURIComponent(selId.value)}/plan/preview`,
              { plan_file: gen.plan });
            preview.status = 'done';
          } catch (e) { preview.status = 'error'; preview.result = { error: e.message }; }
        }

        /* ---------- ③ 生成（SSE + 断线兜底） ---------- */
        const run = reactive({ id: null, status: 'idle', events: [], runDir: null,
                               startedAt: null });
        function startRun() {
          run.status = 'running'; run.events = []; run.runDir = null;
          run.startedAt = Date.now();
          EC.api.post('/api/runs/from_tree', {
            tree_id: selId.value, intent: gen.intent || null,
            folder: gen.folder || null, plan: gen.plan || null,
          }).then((r) => {
            run.id = r.id;
            const es = new EventSource(r.events_url);
            run.es = es;                       // 断线兜底演示/测试可显式断开
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              run.events.push(ev);
              if (ev.data && ev.data.run_dir)
                run.runDir = ev.data.run_dir.replace(/\\/g, '/').split('/').pop();
              if (ev.type === 'end') {
                es.close();
                run.status = ev.status === 'done' ? 'done'
                  : ev.status === 'cancelled' ? 'cancelled' : 'error';
                if (ev.run_dir) goDetail(ev.run_dir);
                else if (run.runDir) goDetail(run.runDir);
              }
            };
            es.onerror = () => {
              // §4 断线兜底：SSE 断开（含服务器重启）→ 轮询产物完整度
              es.close();
              pollRecover();
            };
          }).catch((e) => { run.status = 'error'; run.error = e.message; });
        }
        let pollTimer = null;
        function pollRecover() {
          EC.toast('进度连接断开：正在轮询产物恢复状态…', 'warning');
          pollTimer = setInterval(async () => {
            let dirName = run.runDir;
            if (!dirName) {
              // 未捕获 run_dir：取本会话启动时间之后最新的运行
              const rows = await EC.api.get('/api/runs').catch(() => []);
              const since = rows.filter((r) => run.startedAt &&
                new Date(r.mtime).getTime() >= run.startedAt - 5000);
              if (since.length) dirName = since[0].dir;
            }
            if (!dirName) return;
            run.runDir = dirName;
            // 只探测存在性（final.html 非 JSON，不能用 EC.api.get 解析）
            const done = await fetch(
              `/api/runs/artifact?dir=${encodeURIComponent(dirName)}&file=final.html`
            ).then((r) => r.ok).catch(() => false);
            if (done) {
              clearInterval(pollTimer);
              run.status = 'done';
              EC.toast('检测到生成完成（断线恢复）', 'success');
              goDetail(dirName);
            }
          }, 4000);
        }
        function goDetail(dirName) {
          // E5：end 事件的 run_dir 可能是绝对路径（D:\...\xxx）——统一取最后
          // 一段目录名，否则产物接口白名单校验遇反斜杠全 403、页签全空
          const name = String(dirName).replace(/\\/g, '/').split('/').pop();
          location.hash = '/reports/' + encodeURIComponent(name);
        }

        function goStep2() { step.value = 2; loadPlans(); }
        const canNext1 = computed(() => structOk.value);
        const canNext2 = computed(() => genHasSource.value &&
          (!currentPlan.value || !coverageList.value.some((c) => c.status === 'gap')));

        onMounted(async () => {
          await Promise.all([loadTrees(), loadTemplates()]);
        });

        // 自动化观测缝（仅挂内存引用，无 UI 影响）
        EC._newView = { step, templates, trees, selId, detail, gen, run,
          chatInput, chatMsgs, currentPlan, decisions, preview, selectTree, useTemplate,
          sendChat, startPlan, confirmPlan, previewPlan, startRun, pollRecover,
          loadPlans, startSel, startPoint, showOutline, resultCard, lastCardAction,
          confirmStart, changeStart, replaceSelection, switchToChatBranch };

        return {
          step, templates, trees, selId, detail, form, chatMsgs, chatInput,
          chatBusy, selectTree, useTemplate, sendChat, structOk, goStep2,
          gen, plans, planJob, currentPlan, decisions, preview, genHasSource,
          startPlan, coverageList, confirmPlan, previewPlan, materialLine,
          run, startRun, canNext1, canNext2,
          startSel, startPoint, showOutline, resultCard, lastCardAction, startOptions,
          confirmStart, changeStart, replaceSelection, switchToChatBranch,
        };
      },
      template: `
        <n-card size="small" class="ec-card">
          <n-steps :current="step" size="small" style="margin-bottom:16px">
            <n-step title="结构" description="示例 / 对话 / 现有树" />
            <n-step title="数据" description="计划 · 裁决 · 预检" />
            <n-step title="生成" description="进度与断线兜底" />
          </n-steps>

          <!-- ① 结构（V3-A：先选起点，再走分支） -->
          <div v-if="step === 1">
            <!-- 起点选择视图 -->
            <div v-if="!startPoint">
              <n-grid :cols="3" :x-gap="12">
                <n-gi v-for="o in startOptions" :key="o.key">
                  <div @click="startSel = o.key"
                       :style="{ border: startSel === o.key ? '2px solid #2080f0' : '1px solid var(--ec-line,#e5e7eb)',
                                 borderRadius: '8px', padding: '18px 14px', cursor: 'pointer',
                                 background: startSel === o.key ? 'var(--ec-hover,#f0f7ff)' : '#fff' }"
                       :data-testid="'start-' + o.key">
                    <b style="font-size:15px">{{ o.title }}</b>
                    <div class="ec-muted" style="font-size:12px;margin-top:6px">{{ o.desc }}</div>
                  </div>
                </n-gi>
              </n-grid>
              <n-space justify="center" style="margin-top:18px">
                <n-button type="primary" :disabled="!startSel" @click="confirmStart">用这个开始</n-button>
              </n-space>
            </div>

            <!-- 分支视图 -->
            <div v-else>
              <n-space size="small" align="center" style="margin-bottom:10px">
                <n-button size="small" quaternary @click="changeStart">← 换个起点</n-button>
                <span class="ec-muted" style="font-size:12px">
                  {{ startPoint === 'templates' ? '从示例开始：选一个内置模板复制为草稿'
                     : startPoint === 'chat' ? (selId ? '对话调整当前树' : '对话生成一棵新结构树')
                     : '选择一棵现有结构树继续' }}</span>
              </n-space>

              <!-- B：结果卡（生成/复制成功后出现；编辑成功后随 detail 刷新） -->
              <n-card v-if="resultCard && detail" size="small"
                      style="margin-bottom:10px;border:1px solid #63e2b7" data-testid="result-card">
                <template #header>
                  <span v-if="lastCardAction === 'edit'">已按意见更新：{{ detail.meta.name }}（v{{ detail.meta.version }}）</span>
                  <span v-else-if="lastCardAction === 'template'">已从示例复制为草稿：{{ detail.meta.name }}（{{ (detail.spec_dict.sections || []).length }} 节）</span>
                  <span v-else>结构树已生成：{{ detail.meta.name }}（{{ (detail.spec_dict.sections || []).length }} 节）</span>
                </template>
                <div style="max-height:180px;overflow:auto">
                  <div v-for="s in (detail.spec_dict.sections || [])" :key="s.id"
                       style="font-size:12px;margin-bottom:3px">
                    <n-tag size="tiny" class="ec-mono" style="margin-right:6px">{{ s.kind || 'text' }}</n-tag>
                    <b>{{ s.title }}</b>
                    <span class="ec-muted">　{{ (s.style || s.brief || '').slice(0, 40) }}</span>
                  </div>
                </div>
                <n-space size="small" align="center" style="margin-top:8px">
                  <n-tag size="small"
                         :type="(detail.lint && detail.lint.errors.length) ? 'error'
                           : (detail.lint && detail.lint.warnings.length) ? 'warning' : 'success'">
                    lint：{{ detail.lint ? detail.lint.errors.length : 0 }} 错误 /
                    {{ detail.lint ? detail.lint.warnings.length : 0 }} 提醒</n-tag>
                  <span v-if="detail.lint && detail.lint.errors.length" class="ec-muted" style="font-size:12px">
                    有 error 建议先在模板工作台处理</span>
                </n-space>
                <n-space size="small" style="margin-top:8px">
                  <n-button size="small" @click="switchToChatBranch">继续调整（对话）</n-button>
                  <n-button size="small" type="primary" @click="goStep2">就这样，去配数据 →</n-button>
                </n-space>
              </n-card>

              <!-- 示例分支 -->
              <div v-if="startPoint === 'templates'">
                <div v-for="t in templates" :key="t.id"
                     style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:10px;margin-bottom:8px">
                  <b>{{ t.name }}</b>
                  <div class="ec-muted" style="font-size:12px;margin:2px 0">{{ t.description }}</div>
                  <n-space size="small" align="center">
                    <n-tag size="tiny">{{ t.n_sections }} 节</n-tag>
                    <n-tag v-if="t.style_card" size="tiny" type="info">{{ t.style_card }}</n-tag>
                    <n-button size="tiny" type="primary" @click="useTemplate(t)">复制为草稿</n-button>
                  </n-space>
                </div>
              </div>

              <!-- 对话分支（B：双模式） -->
              <div v-if="startPoint === 'chat'">
                <n-card size="small">
                  <!-- 编辑模式顶部灰字：把"改这棵"和"造新的"两条路分开 -->
                  <div v-if="selId" class="ec-muted" style="font-size:12px;margin-bottom:6px"
                       data-testid="chat-edit-hint">
                    正在调整「{{ detail ? detail.meta.name : selId }}」：发"再加一节讲 XX"这类意见即可；
                    要另起一棵新树请点 ← 换个起点。</div>
                  <div class="ec-log" style="max-height:220px">
                    <div v-for="(m, i) in chatMsgs" :key="i"
                         :style="{ color: m.role === 'user' ? '' : '#2080f0' }">
                      <b>{{ m.role === 'user' ? '我' : '助手' }}：</b>{{ m.text }}</div>
                    <div v-if="!chatMsgs.length" class="ec-muted">还没有对话。</div>
                  </div>
                  <n-input id="ec-chat-input" v-model:value="chatInput" type="textarea" :rows="4"
                           style="margin-top:6px"
                           placeholder="① 报告主题与主体（如：国内高纯石英行业月度动态）
② 章节清单（要哪几节、每节写什么）
③ 表格与图件要求（要有 XX 表 / XX 图）
④ 口吻与篇幅（如：克制陈述，全文 3000~5000 字）" />
                  <n-space size="small" style="margin-top:6px">
                    <n-button size="small" type="primary" :loading="chatBusy" @click="sendChat">
                      {{ selId ? '发送修改意见' : '生成结构树' }}</n-button>
                  </n-space>
                </n-card>
              </div>

              <!-- 现有树分支 -->
              <div v-if="startPoint === 'existing'">
                <n-select :value="selId" @update:value="selectTree" filterable
                          :options="trees.map(t => ({ label: t.name + '（' + t.id + '，' + t.sections + ' 节）', value: t.id }))"
                          placeholder="选择现有结构树" clearable />
              </div>

              <!-- 结构摘要条（任一分支选定树后出现） -->
              <div v-if="selId && detail"
                   style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px;margin-top:10px"
                   data-testid="summary-bar">
                <n-space size="small" align="center">
                  <span style="font-size:13px">已选：<b>{{ detail.meta.name }}</b>
                    <span class="ec-mono ec-muted">v{{ detail.meta.version }}</span>
                     ｜ {{ (detail.spec_dict.sections || []).length }} 节</span>
                  <n-tag size="small"
                         :type="(detail.lint && detail.lint.errors.length) ? 'error'
                           : (detail.lint && detail.lint.warnings.length) ? 'warning' : 'success'">
                    {{ detail.lint ? detail.lint.errors.length : 0 }}E/
                    {{ detail.lint ? detail.lint.warnings.length : 0 }}W</n-tag>
                  <n-button size="tiny" @click="showOutline = !showOutline">查看结构</n-button>
                  <n-button size="tiny" quaternary @click="replaceSelection">更换</n-button>
                </n-space>
                <div v-if="showOutline" style="margin-top:6px;border-top:1px dashed var(--ec-line,#e5e7eb);padding-top:6px">
                  <div v-for="s in (detail.spec_dict.sections || [])" :key="s.id"
                       style="font-size:12px;margin-bottom:2px">
                    <n-tag size="tiny" class="ec-mono" style="margin-right:6px">{{ s.kind || 'text' }}</n-tag>
                    <b>{{ s.title }}</b>
                    <span class="ec-muted">　{{ (s.style || s.brief || '').slice(0, 40) }}</span>
                  </div>
                </div>
              </div>

              <n-space size="small" justify="end" style="margin-top:12px">
                <n-button type="primary" :disabled="!canNext1" @click="goStep2">下一步：数据</n-button>
              </n-space>
            </div>
          </div>

          <!-- ② 数据 -->
          <div v-if="step === 2">
            <n-space vertical size="small">
              <n-space size="small" align="center">
                <n-button size="small" :loading="planJob.status === 'running'" @click="startPlan">① 出数据计划</n-button>
                <n-button size="small" :disabled="!gen.plan" :loading="preview.status === 'running'" @click="previewPlan">预检数据</n-button>
                <span class="ec-muted">按树的数据需求规划检索/联网/资料，给出缺口回执（文件夹要在出计划前填写）；预检只跑数据层（结果缓存）</span>
              </n-space>
              <div v-if="coverageList.length" style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                <n-space size="small" align="center" justify="space-between">
                  <b style="font-size:13px">缺口回执（{{ currentPlan.plan_file }}）</b>
                  <n-tag size="small" :type="coverageList.some(c => c.status === 'gap') ? 'warning' : 'success'">
                    {{ coverageList.filter(c => c.status === 'gap').length }} 个缺口待裁决</n-tag>
                </n-space>
                <div v-if="materialLine" class="ec-muted" data-testid="material-line"
                     style="font-size:12px;margin-top:4px">资料：{{ materialLine }}</div>
                <div v-for="c in coverageList" :key="c.need"
                     style="display:flex;gap:10px;align-items:center;margin-top:6px;font-size:13px">
                  <n-tag size="small" :type="c.status === 'covered' ? 'success'
                          : c.status === 'search' ? 'warning' : c.status === 'gap' ? 'error' : 'default'">
                    {{ c.status === 'covered' ? '✓ 已覆盖' : c.status === 'search' ? '⚠ 需联网/资料'
                       : c.status === 'gap' ? '✗ 缺口' : '— 已处理' }}</n-tag>
                  <span style="flex:1">{{ c.need }}</span>
                  <span v-if="c.status === 'gap'">
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
                </n-space>
              </div>
              <n-alert v-if="preview.status === 'done' && preview.result" type="info" size="small">
                预检{{ preview.result.cached ? '（缓存复用）' : '' }}：事实 <b>{{ preview.result.n_facts }}</b> 条
                <span v-if="preview.result.by_source">｜{{ Object.entries(preview.result.by_source).map(([k, v]) => k + '×' + v).join('，') }}</span>
              </n-alert>
              <n-alert v-if="preview.status === 'error'" type="error" size="small">{{ preview.result && preview.result.error }}</n-alert>
              <n-space size="small" align="center">
                <n-input v-model:value="gen.intent" type="textarea" :rows="2"
                         placeholder="备选：写作意图（不推荐，无缺口回执）" />
                <n-input v-model:value="gen.folder" size="small"
                         placeholder="备选：本地资料文件夹（PDF 建语料 + Excel 进表格与事实，跳过缺口回执）" class="ec-mono" />
              </n-space>
              <span class="ec-muted">意图 / 资料文件夹 / 已确认计划至少给一个（三者全空不允许生成）</span>
            </n-space>
            <n-space size="small" justify="space-between" style="margin-top:12px">
              <n-button @click="() => { step = 1 }">上一步</n-button>
              <n-button type="primary" :disabled="!canNext2" @click="step = 3">下一步：生成</n-button>
            </n-space>
          </div>

          <!-- ③ 生成 -->
          <div v-if="step === 3">
            <n-space vertical size="small">
              <n-space size="small" align="center">
                <n-button type="primary" :disabled="run.status === 'running'" @click="startRun">
                  {{ run.status === 'running' ? '生成中…' : '开始生成' }}</n-button>
                <span class="ec-muted">树：{{ selId }}｜计划：{{ gen.plan || gen.folder || gen.intent || '（无）' }}</span>
              </n-space>
              <div v-if="run.events.length" class="ec-log" style="max-height:280px">
                <div v-for="(ev, i) in run.events.filter(e => e.message)" :key="i">{{ ev.message }}</div>
              </div>
              <n-alert v-if="run.status === 'error'" type="error" size="small">生成失败，可回上一步调整数据来源后重试。</n-alert>
              <n-alert v-if="run.status === 'cancelled'" type="warning" size="small">已取消。</n-alert>
              <span class="ec-muted">生成完成会自动跳转报告详情；进度连接断开时自动轮询产物恢复状态。</span>
            </n-space>
            <n-space size="small" justify="space-between" style="margin-top:12px">
              <n-button @click="step = 2">上一步</n-button>
            </n-space>
          </div>
        </n-card>
      `,
    },
  };
})();
