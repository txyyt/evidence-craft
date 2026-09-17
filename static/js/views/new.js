/* 新建报告向导（V2 §三 §4，/new）：三步——
   ① 结构：示例树（复制为草稿）/ 空白对话生成 / 现有树选择；
   ② 数据：出计划 → 缺口裁决 → 预检 → 确认（备选：资料夹 / 意图，全空禁下一步）；
   ③ 生成：SSE 进度 + 断线兜底（轮询产物完整度），完成跳详情。 */
(function () {
  const { ref, reactive, computed, onMounted, onUnmounted, watch } = Vue;

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
          // V4-11（P13）：按钮已按空值禁用；防御校验兜程序调用/竞态，不再静默 return
          if (chatBusy.value) return;
          if (!text) {
            EC.toast('请先描述要写的报告或调整要求', 'warning');
            return;
          }
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
                // V4-11：追问按条目分段展示（保守原文分段，不改写原意），
                // 焦点送回输入框、按钮转「发送回答」
                chatMsgs.value.push({ role: 'assistant', kind: 'questions',
                  text: '需要补充以下信息：', questions: r.questions });
                refocusChat();
              } else {
                chatMsgs.value.push({ role: 'assistant',
                  text: `已生成结构树「${r.name}」（${r.spec_dict.sections.length} 节）` });
                await loadTrees();
                await selectTree(r.tree_id);
                lastCardAction.value = 'create';
                resultCard.value = r.tree_id;    // B：结果卡出现
                focusResultTitle();
              }
            }
          } catch (e) {
            chatMsgs.value.push({ role: 'assistant', text: '出错：' + e.message });
          } finally { chatBusy.value = false; }
        }
        function refocusChat() {
          // V4-11：追问返回后焦点回输入框（键盘用户能顺着追问继续）
          setTimeout(() => {
            const el = document.querySelector('#ec-chat-input textarea');
            if (el) el.focus();
          }, 120);
        }
        function focusResultTitle() {
          // V4-11：成功生成树后焦点移到结果卡标题（读屏/键盘可感知结果位置）
          setTimeout(() => {
            const el = document.querySelector('#ec-result-title');
            if (el) el.focus();
          }, 150);
        }
        const structOk = computed(() => !!selId.value);
        // V4-11：最后一条助手消息是追问 → 需要回答态（按钮文案 + 状态播报）
        const needAnswer = computed(() => {
          const m = chatMsgs.value[chatMsgs.value.length - 1];
          return !!m && m.kind === 'questions';
        });
        const chatStatus = computed(() => chatBusy.value ? '正在生成…'
          : needAnswer.value ? '需要补充信息' : resultCard.value ? '已生成' : '');

        /* ---------- ② 数据（V4-03：上下文 → 计划 → 覆盖检查 → 预检） ---------- */
        const gen = reactive({ intent: '', folder: '', plan: null });
        const plans = ref([]);           // 计划列表（含 V4-03 增量字段）
        const planJob = reactive({ status: 'idle' });
        const currentPlan = ref(null);
        const decisions = reactive({});
        const preview = reactive({ result: null });
        // V4-04：预检状态机 idle/running/pass/fail/stale + 请求指纹
        const previewState = ref('idle');
        const qualConfirmed = ref(false);   // 全定性二次确认（0 事实例外）
        const planAction = ref('new');      // 'existing' | 'new'（V4-03）
        const skipPlan = ref(false);        // 高级旁路：跳过计划直接生成
        // V4-05：模型档位（后端允许列表；拉不到只显示"自动"）
        const tierOptions = ref([{ value: '', label: '自动（推荐）' }]);
        const modelTier = ref('');

        const genHasSource = computed(() =>
          !!(gen.intent.trim() || gen.folder.trim() || gen.plan));
        async function loadPlans() {
          plans.value = selId.value
            ? await EC.api.get(`/api/trees/${encodeURIComponent(selId.value)}/plans`)
            : [];
        }
        const planOptions = computed(() => plans.value.map((p) => {
          const stale = p.tree_fingerprint != null && detail.value
            && p.tree_fingerprint !== detail.value.fingerprint;
          const tags = [];
          if (p.confirmed) tags.push('已确认');
          else if (p.confirmed === false) tags.push('未确认');
          if (stale) tags.push('树已变化，需重新生成');
          if (p.preview_cached) tags.push('有预检缓存');
          return { value: p.name,
                   label: `${p.name}${tags.length ? '（' + tags.join('，') + '）' : ''}`,
                   stale, plan: p };
        }));
        const selectedPlanRow = computed(() =>
          plans.value.find((p) => p.name === gen.plan) || null);
        // V4-03：树指纹不匹配 → 旧计划不能直接进入下一步
        const planStale = computed(() => {
          const row = selectedPlanRow.value;
          return !!(row && row.tree_fingerprint != null && detail.value
            && row.tree_fingerprint !== detail.value.fingerprint);
        });
        const planConfirmed = computed(() => {
          if (currentPlan.value && currentPlan.value.plan)
            return !!currentPlan.value.plan.confirmed;
          const row = selectedPlanRow.value;
          return !!(row && row.plan && row.plan.confirmed);
        });
        // 默认聪明缺省：最新且已确认且指纹与当前树一致；没有才默认"生成新计划"
        function pickDefaultPlan() {
          const fp = detail.value ? detail.value.fingerprint : null;
          const match = plans.value.find((p) =>
            p.confirmed && p.tree_fingerprint != null && p.tree_fingerprint === fp);
          if (match) {
            gen.plan = match.name;
            planAction.value = 'existing';
          } else {
            gen.plan = null;
            planAction.value = 'new';
          }
        }
        async function loadPlanContent(name) {
          if (!name) { currentPlan.value = null; return; }
          try {
            const plan = await EC.api.get(`/api/trees/${
              encodeURIComponent(selId.value)}/plans/${
              encodeURIComponent(name)}`);
            currentPlan.value = { plan_file: name, plan };
            for (const c of (plan.needs_coverage || []))
              if (c.status === 'gap') decisions[c.need] = decisions[c.need] || '';
            // V4-03：0 缺口且未确认 → 自动确认（用户无需理解"缺口裁决"）
            const gaps = (plan.needs_coverage || []).filter(
              (c) => c.status === 'gap');
            if (!gaps.length && !plan.confirmed) autoConfirmPlan();
          } catch (e) { currentPlan.value = null; }
        }
        watch(() => gen.plan, (name) => {
          // V4-03：切换计划 → 重载覆盖检查；V4-04：预检失效
          loadPlanContent(name);
          markPreviewStale();
        });
        // V4-04：树/计划/意图/资料/裁决任一变化 → 已完成预检标 stale
        const previewSignature = computed(() => JSON.stringify({
          v: detail.value ? detail.value.meta.version : null,
          fp: detail.value ? detail.value.fingerprint : null,
          plan: gen.plan,
          intent: gen.intent.trim(),
          folder: gen.folder.trim(),
          dec: Object.entries(decisions).map(([k, v]) => k + '=' + v)
            .sort().join(';'),
        }));
        function markPreviewStale() {
          if (previewState.value === 'pass') previewState.value = 'stale';
          qualConfirmed.value = false;
        }
        watch(previewSignature, () => markPreviewStale());
        async function loadTiers() {
          try {
            const p = await EC.api.get('/api/settings/pipeline');
            if (Array.isArray(p.model_tiers) && p.model_tiers.length)
              tierOptions.value = p.model_tiers.map((t) =>
                ({ value: t.value, label: t.label, title: t.description || '' }));
          } catch (e) { /* 旧后端：只显示"自动" */ }
        }
        async function startPlan() {
          if (!selId.value) return;
          planJob.status = 'running';
          try {
            const r = await EC.api.post(
              `/api/trees/${encodeURIComponent(selId.value)}/plan/start`,
              { intent: gen.intent || '', folder: gen.folder || null });
            // V4-02：注册进全局任务中心（可离开/恢复/取消）
            EC.tasks.register({ id: r.id, kind: 'plan', treeId: selId.value,
              treeName: detail.value ? detail.value.meta.name : selId.value,
              artifactDir: null, status: 'running', stage: 'plan',
              message: '正在生成数据计划', returnRoute: location.hash });
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              if (ev.stage)
                EC.tasks.update(r.id, { stage: ev.stage, message: ev.message || '' });
              if (ev.data && ev.data.plan_file) {
                currentPlan.value = { plan_file: ev.data.plan_file, plan: ev.data.plan };
                for (const c of (ev.data.plan.needs_coverage || []))
                  if (c.status === 'gap') decisions[c.need] = decisions[c.need] || '';
                // V4-03：新计划生成后自动选中
                gen.plan = ev.data.plan_file;
                planAction.value = 'existing';
                loadPlans();
              }
              if (ev.type === 'end') {
                es.close(); planJob.status = ev.status;
                EC.tasks.update(r.id, { status: ev.status,
                  message: ev.status === 'done' ? '计划已生成' : (ev.error || '') });
              }
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
          const gaps = coverageList.value.filter((c) => c.status === 'gap');
          const ds = gaps.filter((c) => decisions[c.need])
            .map((c) => ({ need: c.need, decision: decisions[c.need] }));
          if (gaps.some((c) => !decisions[c.need])) {
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
          currentPlan.value.plan.confirmed = true;   // V4-03：按钮转"已确认"不可重复
          gen.plan = currentPlan.value.plan_file;
          EC.toast('数据计划已确认', 'success');
        }
        // V4-03：0 缺口计划自动确认（无需用户理解"缺口裁决"）
        async function autoConfirmPlan() {
          if (!currentPlan.value || currentPlan.value.plan.confirmed) return;
          try {
            const r = await EC.api.post(
              `/api/trees/${encodeURIComponent(selId.value)}/plan/confirm`,
              { plan_file: currentPlan.value.plan_file, decisions: [] });
            if (r.ok) {
              currentPlan.value.plan.needs_coverage = r.coverage;
              currentPlan.value.plan.confirmed = true;
              gen.plan = currentPlan.value.plan_file;
            }
          } catch (e) { /* 自动确认失败不阻塞：用户仍可手动确认 */ }
        }
        async function confirmPlanAll() {
          const r = await EC.api.post(
            `/api/trees/${encodeURIComponent(selId.value)}/plan/confirm`,
            { plan_file: currentPlan.value.plan_file, decisions: [],
              all_qualitative: true });
          if (r.ok) {
            currentPlan.value.plan.needs_coverage = r.coverage;
            currentPlan.value.plan.confirmed = true;
            gen.plan = currentPlan.value.plan_file;
            EC.toast('已按全定性确认', 'success');
          }
        }
        async function previewPlan() {
          if (!gen.plan || previewState.value === 'running') return;
          previewState.value = 'running'; preview.result = null;
          try {
            // V4-02：预检后台化（/plan/preview/start），同步端点保留给旧消费者
            const r = await EC.api.post(
              `/api/trees/${encodeURIComponent(selId.value)}/plan/preview/start`,
              { plan_file: gen.plan });
            EC.tasks.register({ id: r.id, kind: 'preview', treeId: selId.value,
              treeName: detail.value ? detail.value.meta.name : selId.value,
              artifactDir: null, status: 'running', stage: 'preview',
              message: '正在预检数据', returnRoute: location.hash });
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              if (ev.stage)
                EC.tasks.update(r.id, { stage: ev.stage, message: ev.message || '' });
              if (ev.type === 'end') {
                es.close();
                EC.tasks.update(r.id, { status: ev.status,
                  message: ev.status === 'done' ? '预检完成' : (ev.error || '') });
                if (ev.status === 'done' && ev.result) {
                  preview.result = ev.result;
                  const zeroFacts = ev.result.ok === false
                    && (ev.result.n_facts || 0) === 0;
                  const allDecided = coverageList.value.length > 0
                    && coverageList.value.every((c) => c.status !== 'gap');
                  if (ev.result.ok) {
                    previewState.value = 'pass';
                  } else if (zeroFacts && allDecided) {
                    // V4-04：全定性例外——复用后端既有裁决结果 + 二次确认
                    previewState.value = 'fail';
                    dialog.warning({
                      title: '全定性生成确认',
                      content: '预检取到 0 条事实。本报告将不陈述可核验数字'
                        + '（全部按定性写作）。确认继续？',
                      positiveText: '确认继续',
                      negativeText: '返回调整数据',
                      onPositiveClick: () => {
                        qualConfirmed.value = true;
                        previewState.value = 'pass';
                      },
                    });
                  } else {
                    previewState.value = 'fail';
                  }
                } else {
                  previewState.value = 'fail';
                  preview.result = { error: ev.error || '预检失败' };
                }
              }
            };
            es.onerror = () => es.close();
          } catch (e) {
            previewState.value = 'fail';
            preview.result = { error: e.message };
          }
        }

        /* ---------- ③ 生成（SSE + 断线兜底） ---------- */
        const run = reactive({ id: null, status: 'idle', events: [], runDir: null,
                               startedAt: null });
        // V4-02：组件卸载标记——卸载后完成事件不再劫持路由（由任务条接管）
        let disposed = false;
        onUnmounted(() => {
          disposed = true;
          if (run.es) { try { run.es.close(); } catch (e) { /* 已关 */ } }
          if (pollTimer) clearInterval(pollTimer);
        });
        function startRun() {
          run.status = 'running'; run.events = []; run.runDir = null;
          run.startedAt = Date.now();
          EC.api.post('/api/runs/from_tree', {
            tree_id: selId.value, intent: gen.intent || null,
            folder: gen.folder || null, plan: gen.plan || null,
            model_tier: modelTier.value || null,   // V4-05：档位显式发送
            reuse_data_from: reuseDir.value || null,   // V4-07：复用数据
          }).then((r) => {
            run.id = r.id;
            // V4-02：注册全局任务（跨页可见/可取消/可恢复）
            EC.tasks.register({ id: r.id, kind: 'run', treeId: selId.value,
              treeName: detail.value ? detail.value.meta.name : selId.value,
              artifactDir: null, status: 'running', stage: 'data',
              message: '任务已启动', returnRoute: location.hash });
            const es = new EventSource(r.events_url);
            run.es = es;                       // 断线兜底演示/测试可显式断开
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              run.events.push(ev);
              if (ev.stage)
                EC.tasks.update(r.id, { stage: ev.stage, message: ev.message || '' });
              if (ev.data && ev.data.run_dir)
                run.runDir = ev.data.run_dir.replace(/\\/g, '/').split('/').pop();
              if (ev.type === 'end') {
                es.close();
                run.status = ev.status === 'done' ? 'done'
                  : ev.status === 'cancelled' ? 'cancelled' : 'error';
                const art = ev.artifact_dir || run.runDir || null;
                EC.tasks.update(r.id, { status: run.status, artifactDir: art,
                  message: ev.status === 'done' ? '生成完成，可查看报告'
                    : (ev.error || run.status === 'cancelled' ? '已取消' : '生成失败') });
                // V4-02：仅当用户仍停留在向导第③步才自动跳详情；否则任务条"查看"
                if (!disposed && step.value === 3) {
                  if (ev.artifact_dir) goDetail(ev.artifact_dir);
                  else if (ev.run_dir) goDetail(ev.run_dir);
                  else if (run.runDir) goDetail(run.runDir);
                }
              }
            };
            es.onerror = () => {
              // §4 断线兜底：SSE 断开（含服务器重启）→ 轮询产物完整度
              es.close();
              if (!disposed) pollRecover();
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
              EC.tasks.update(run.id, { status: 'done', artifactDir: dirName,
                message: '生成完成，可查看报告' });
              EC.toast('检测到生成完成（断线恢复）', 'success');
              if (!disposed && step.value === 3) goDetail(dirName);
            }
          }, 4000);
        }
        function goDetail(dirName) {
          // V4-01：end 事件优先给 artifact_dir（目录 basename，已安全）；旧事件
          // 或兜底轮询仍可能传来绝对路径——统一按 Path 语义取最后一段（E5 双保险），
          // 且只 encodeURIComponent 一次，绝不自行拆解含 / 的产物名
          const name = String(dirName || '').replace(/\\/g, '/').split('/').pop();
          if (!name) return;
          location.hash = '/reports/' + encodeURIComponent(name);
        }

        function goStep2() {
          step.value = 2;
          loadPlans().then(() => pickDefaultPlan());
          loadTiers();
        }
        const canNext1 = computed(() => structOk.value);
        // V4-04：canNext2 状态矩阵——预检 running/fail/stale 或未 pass 不能下一步；
        // 高级旁路（跳过计划）保持旧来源门禁
        const canNext2 = computed(() => {
          if (run.status === 'running') return false;
          if (skipPlan.value) return genHasSource.value;
          if (!gen.plan) return false;
          if (planStale.value) return false;        // 树已变化的旧计划
          if (!planConfirmed.value) return false;   // 未确认裁决
          if (previewState.value === 'running'
            || previewState.value === 'fail'
            || previewState.value === 'stale') return false;
          return previewState.value === 'pass';     // 必须 pass（含全定性确认后）
        });
        // V4-05：最终复核卡数据
        const finalReview = computed(() => {
          const d = detail.value;
          const plan = (currentPlan.value && currentPlan.value.plan) || {};
          const row = selectedPlanRow.value || {};
          return {
            treeName: d ? d.meta.name : selId.value,
            treeId: selId.value || '—',
            version: d ? d.meta.version : '—',
            nSections: d && d.spec_dict ? (d.spec_dict.sections || []).length : '—',
            lintText: d && d.lint ? EC.lintText(d.lint) : '—',
            plan: gen.plan || '（无：跳过计划旁路）',
            planConfirmed: planConfirmed.value,
            planFocus: plan.focus || row.focus || gen.intent.trim() || '—',
            sources: gen.plan ? '数据计划（检索 / 联网 / 本地资料绑定）'
              : (gen.folder.trim() ? '本地资料文件夹' : '写作意图'),
            nFacts: preview.result ? preview.result.n_facts : null,
            nWarnings: preview.result
              ? (preview.result.warnings || []).length : null,
            tierLabel: (tierOptions.value.find(
              (t) => t.value === modelTier.value) || {}).label || '自动（推荐）',
          };
        });

        // V4-06/07：深链状态（tree=选中树；start=chat 展开对话；plan/mode/reuse=回环预填）
        const treeLinkError = ref(null);
        const reuseDir = ref(null);
        const reuseInfo = ref(null);
        const deepLinkNote = ref(null);
        onMounted(async () => {
          await Promise.all([loadTrees(), loadTemplates()]);
          const params = new URLSearchParams(location.hash.split('?')[1] || '');
          const treeId = params.get('tree');
          const start = params.get('start');
          const planFile = params.get('plan');
          const mode = params.get('mode');
          const reuse = params.get('reuse');
          if (treeId) {
            if (trees.value.some((t) => t.id === treeId)) {
              await selectTree(treeId);
              startSel.value = 'existing';
              startPoint.value = 'existing';
              if (planFile) {
                try {
                  await loadPlans();
                  if (plans.value.some((p) => p.name === planFile)) {
                    gen.plan = planFile;
                    deepLinkNote.value = '已带入原报告的数据计划，请重新预检后再生成。';
                  }
                } catch (e) { /* 计划校验失败按无计划处理 */ }
              }
              if (mode === 'data') step.value = 2;   // 补数据再生成：直接落第②步
            } else {
              // V4-06：无效深链可恢复——显示错误，回到三起点，不发任何计划/生成请求
              treeLinkError.value = `深链的结构树「${treeId}」不存在（可能已删除），请重新选择起点。`;
            }
          } else if (start === 'chat') {
            startSel.value = 'chat';
            startPoint.value = 'chat';
          }
          if (reuse) {
            // V4-07：复用数据深链——逐项向 API 校验，失败回第②步
            try {
              const files = await EC.api.get('/api/runs/'
                + encodeURIComponent(reuse) + '/files');
              if (!files.some((f) => f.name === 'facts.json'))
                throw new Error('缺少 facts.json');
              const meta = await EC.api.get('/api/runs/artifact?dir='
                + encodeURIComponent(reuse) + '&file=meta.json').catch(() => null);
              reuseDir.value = reuse;
              reuseInfo.value = {
                facts: meta ? meta.facts_count : null,
                date: meta ? meta.data_generated_at : null,
              };
              deepLinkNote.value = '按本次数据再写一版：数据层结果将复用，'
                + '请在第③步确认后手动开始。';
              if (mode !== 'data') step.value = 3;
            } catch (e) {
              deepLinkNote.value = '无法复用该产物数据（目录不可访问或缺少数据文件）'
                + '——请回到第②步重新预检。';
              step.value = 2;
            }
          }
        });

        // 自动化观测缝（仅挂内存引用，无 UI 影响）
        EC._newView = { step, templates, trees, selId, detail, gen, run, plans,
          chatInput, chatMsgs, currentPlan, decisions, preview, selectTree, useTemplate,
          sendChat, startPlan, confirmPlan, previewPlan, startRun, pollRecover,
          loadPlans, startSel, startPoint, showOutline, resultCard, lastCardAction,
          confirmStart, changeStart, replaceSelection, switchToChatBranch,
          needAnswer, planAction, planOptions, planStale, planConfirmed,
          previewState, canNext2, finalReview, modelTier, tierOptions,
          pickDefaultPlan, autoConfirmPlan, skipPlan, qualConfirmed, goStep2,
          treeLinkError, reuseDir, reuseInfo, deepLinkNote,
          treeLinkError, reuseDir, reuseInfo, deepLinkNote };

        return {
          step, templates, trees, selId, detail, form, chatMsgs, chatInput,
          chatBusy, selectTree, useTemplate, sendChat, structOk, goStep2,
          needAnswer, chatStatus,
          gen, plans, planJob, currentPlan, decisions, preview, genHasSource,
          planAction, planOptions, selectedPlanRow, planStale, planConfirmed,
          previewState, qualConfirmed, skipPlan,
          startPlan, coverageList, confirmPlan, previewPlan, materialLine,
          tierOptions, modelTier, finalReview,
          gateLabel: EC.gateLabel,
          treeLinkError, reuseDir, reuseInfo, deepLinkNote,
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

          <!-- V4-06/07：深链错误与提示 -->
          <n-alert v-if="treeLinkError" type="warning" size="small" style="margin-bottom:12px"
                   data-testid="tree-link-error">
            {{ treeLinkError }}
            <n-button size="tiny" style="margin-left:8px" @click="treeLinkError = null">知道了</n-button>
          </n-alert>
          <n-alert v-if="deepLinkNote" type="info" size="small" style="margin-bottom:12px"
                   data-testid="deep-link-note">
            {{ deepLinkNote }}
            <n-button size="tiny" style="margin-left:8px" @click="deepLinkNote = null">知道了</n-button>
          </n-alert>

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
                  <!-- V4-11：焦点目标（生成成功后 focus 到这里） -->
                  <span id="ec-result-title" tabindex="-1" data-testid="result-card-title"
                        style="outline:none">
                    <span v-if="lastCardAction === 'edit'">已按意见更新：{{ detail.meta.name }}（v{{ detail.meta.version }}）</span>
                    <span v-else-if="lastCardAction === 'template'">已从示例复制为草稿：{{ detail.meta.name }}（{{ (detail.spec_dict.sections || []).length }} 节）</span>
                    <span v-else>结构树已生成：{{ detail.meta.name }}（{{ (detail.spec_dict.sections || []).length }} 节）</span>
                  </span>
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
                           : (detail.lint && detail.lint.warnings.length) ? 'warning' : 'success'"
                         :title="'结构检查（lint）：' + ((detail.lint && detail.lint.errors.length) || 0)
                           + ' errors / ' + ((detail.lint && detail.lint.warnings.length) || 0) + ' warnings'">
                    结构检查：{{ ((detail.lint && detail.lint.errors.length) || 0) }} 个错误 /
                    {{ ((detail.lint && detail.lint.warnings.length) || 0) }} 个提醒</n-tag>
                  <span v-if="detail.lint && detail.lint.errors.length" class="ec-muted" style="font-size:12px">
                    有错误建议先在模板工作台处理</span>
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
                      <b>{{ m.role === 'user' ? '我' : '助手' }}：</b>{{ m.text }}
                      <!-- V4-11：追问逐条分段（保守按返回条目原样列出） -->
                      <div v-if="m.questions" style="margin-top:2px">
                        <div v-for="(q, qi) in m.questions" :key="qi"
                             style="margin-left:1.2em" :data-testid="'chat-question-' + qi">• {{ q }}</div>
                      </div>
                    </div>
                    <div v-if="!chatMsgs.length" class="ec-muted">还没有对话。</div>
                  </div>
                  <!-- V4-11：状态播报区（读屏 + 可见细字） -->
                  <div aria-live="polite" class="ec-muted" data-testid="chat-status"
                       style="font-size:12px;min-height:16px;margin-top:4px">{{ chatStatus }}</div>
                  <n-input id="ec-chat-input" v-model:value="chatInput" type="textarea" :rows="4"
                           style="margin-top:6px"
                           placeholder="① 报告主题与主体（如：国内高纯石英行业月度动态）
② 章节清单（要哪几节、每节写什么）
③ 表格与图件要求（要有 XX 表 / XX 图）
④ 口吻与篇幅（如：克制陈述，全文 3000~5000 字）" />
                  <n-space size="small" style="margin-top:6px" align="center">
                    <!-- V4-11：空输入/请求中禁用；追问后按钮转「发送回答」 -->
                    <n-button size="small" type="primary" data-testid="chat-send"
                              :loading="chatBusy" :disabled="chatBusy || !chatInput.trim()"
                              @click="sendChat">
                      {{ selId ? '发送修改意见' : (needAnswer ? '发送回答' : '生成结构树') }}</n-button>
                    <span class="ec-muted" style="font-size:12px">输入为空时不能发送；示例：上方占位文字</span>
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
                           : (detail.lint && detail.lint.warnings.length) ? 'warning' : 'success'"
                         :title="'结构检查（lint）：' + ((detail.lint && detail.lint.errors.length) || 0)
                           + ' errors / ' + ((detail.lint && detail.lint.warnings.length) || 0) + ' warnings'">
                    {{ ((detail.lint && detail.lint.errors.length) || 0) }} 个错误 /
                    {{ ((detail.lint && detail.lint.warnings.length) || 0) }} 个提醒</n-tag>
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

          <!-- ② 数据（V4-03：数据上下文 → 计划动作 → 覆盖检查 → 预检） -->
          <div v-if="step === 2" data-testid="data-step">
            <n-space vertical size="small">
              <!-- ① 数据上下文（信息先于动作） -->
              <div data-testid="data-context"
                   style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                <b style="font-size:13px">写作背景（可先填，出计划前可改）</b>
                <n-space vertical size="small" style="margin-top:6px">
                  <n-input v-model:value="gen.intent" type="textarea" :rows="2"
                           :input-props="{ 'aria-label': '写作意图' }"
                           placeholder="写作意图（可选）：这份报告想回答什么问题、给谁看" />
                  <n-input v-model:value="gen.folder" size="small"
                           :input-props="{ 'aria-label': '本地资料文件夹' }"
                           placeholder="本地资料文件夹（可选：用于本地 PDF 建语料 + Excel 进表格）" class="ec-mono" />
                </n-space>
              </div>

              <!-- ② 计划动作：使用已有计划 / 生成新计划 -->
              <div data-testid="plan-action"
                   style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                <n-space size="small" align="center" justify="space-between">
                  <n-radio-group v-model:value="planAction" size="small">
                    <n-radio value="existing">使用已有计划</n-radio>
                    <n-radio value="new">生成新计划</n-radio>
                  </n-radio-group>
                  <n-button size="small" :loading="planJob.status === 'running'"
                            :disabled="!selId" @click="startPlan">
                    {{ planJob.status === 'running' ? '生成中…' : '按当前设置生成新计划' }}</n-button>
                </n-space>
                <div v-if="planAction === 'existing'" style="margin-top:8px">
                  <n-select v-model:value="gen.plan" :options="planOptions"
                            data-testid="saved-plan-select" size="small"
                            placeholder="选择一份已保存的数据计划" clearable />
                  <n-alert v-if="gen.plan && planStale" type="warning" size="small" style="margin-top:6px">
                    该计划生成后结构树已变化（版本或指纹不一致）——请重新生成计划，不能用旧计划直接生成。
                  </n-alert>
                  <div v-if="selectedPlanRow" class="ec-muted" style="font-size:12px;margin-top:4px">
                    生成时间：{{ selectedPlanRow.mtime ? new Date(selectedPlanRow.mtime * 1000).toLocaleString() : '—' }}
                    ｜{{ selectedPlanRow.confirmed ? '已确认' : '未确认' }}
                    <span v-if="selectedPlanRow.gap_count != null">｜缺口 {{ selectedPlanRow.gap_count }}</span>
                    <span v-if="selectedPlanRow.preview_cached">｜有预检缓存</span>
                  </div>
                </div>
                <div v-else class="ec-muted" style="font-size:12px;margin-top:6px">
                  按树的数据需求 + 上方写作背景规划检索/联网/资料，产出缺口回执。</div>
              </div>

              <!-- ③ 覆盖检查（缺口裁决） -->
              <div v-if="currentPlan && coverageList.length"
                   style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                <n-space size="small" align="center" justify="space-between">
                  <b style="font-size:13px"><term-help term="gap_decision" />（{{ currentPlan.plan_file }}）</b>
                  <n-tag v-if="planConfirmed" size="small" type="success"
                         data-testid="plan-confirmed">已确认</n-tag>
                  <n-tag v-else size="small"
                         :type="coverageList.some(c => c.status === 'gap') ? 'warning' : 'success'">
                    {{ coverageList.filter(c => c.status === 'gap').length }} 个缺口待裁决</n-tag>
                </n-space>
                <div v-if="planConfirmed" class="ec-muted" style="font-size:12px;margin-top:4px">
                  {{ coverageList.some(c => c.status === 'gap')
                    ? '裁决已确认；如需修改请生成新计划。'
                    : '所有章节已有数据策略，无需逐项裁决。' }}</div>
                <div v-if="!planConfirmed">
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
                </div>
                <div v-if="materialLine" class="ec-muted" data-testid="material-line"
                     style="font-size:12px;margin-top:4px">资料：{{ materialLine }}</div>
                <n-space size="small" style="margin-top:8px">
                  <n-button v-if="!planConfirmed" size="tiny" type="primary"
                            @click="confirmPlan">确认裁决</n-button>
                </n-space>
              </div>

              <!-- ④ 预检（V4-04：生成前可判定门） -->
              <div style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:8px">
                <n-space size="small" align="center" justify="space-between">
                  <b style="font-size:13px"><term-help term="preview" /></b>
                  <n-space size="small" align="center">
                    <n-tag v-if="previewState === 'pass'" size="small" type="success">预检通过</n-tag>
                    <n-tag v-else-if="previewState === 'fail'" size="small" type="error">预检未通过</n-tag>
                    <n-tag v-else-if="previewState === 'stale'" size="small" type="warning"
                           data-testid="preview-stale">需重新预检（数据已变化）</n-tag>
                    <n-tag v-else-if="previewState === 'running'" size="small" type="info">预检中…</n-tag>
                    <n-button size="tiny" :disabled="!gen.plan || previewState === 'running'"
                              @click="previewPlan">
                      {{ previewState === 'running' ? '预检中…' : (previewState === 'pass' ? '重新预检' : '预检数据') }}</n-button>
                  </n-space>
                </n-space>
                <!-- 结果卡：事实/来源/缓存/交叉校验/警告/样例 -->
                <div v-if="previewState === 'pass' && preview.result" style="margin-top:6px"
                     data-testid="preview-result">
                  <n-space size="small" align="center">
                    <span data-testid="preview-facts">事实 <b>{{ preview.result.n_facts }}</b> 条</span>
                    <span v-if="preview.result.by_source" data-testid="preview-sources">
                      ｜{{ Object.entries(preview.result.by_source).map(([k, v]) => k + '×' + v).join('，') }}</span>
                    <span data-testid="preview-crosscheck">｜交叉校验：{{ gateLabel(preview.result.crosscheck) }}</span>
                    <span class="ec-muted">｜{{ preview.result.cached ? '缓存复用' : '全新执行' }}</span>
                  </n-space>
                  <div v-if="(preview.result.warnings || []).length" data-testid="preview-warnings"
                       style="margin-top:4px;font-size:12px;color:#f0a020">
                    ⚠ 警告 {{ preview.result.warnings.length }} 条：
                    <div v-for="(w, i) in preview.result.warnings" :key="i"
                         class="ec-muted" style="font-size:12px;margin-left:1em">- {{ w }}</div>
                  </div>
                  <div v-if="qualConfirmed" style="margin-top:4px;font-size:12px;color:#f0a020">
                    已确认全定性生成：本报告不陈述可核验数字。</div>
                  <n-collapse style="margin-top:4px">
                    <n-collapse-item title="查看 3 条事实样例" name="sample">
                      <div v-for="(f, i) in (preview.result.sample || []).slice(0, 3)" :key="i"
                           class="ec-muted" style="font-size:12px;margin-bottom:2px">
                        {{ i + 1 }}. {{ f.name }} = {{ f.value }} {{ f.unit || '' }}（{{ f.source }}，{{ f.as_of || '—' }}）</div>
                    </n-collapse-item>
                  </n-collapse>
                </div>
                <div v-else-if="previewState === 'fail' && preview.result" style="margin-top:6px">
                  <n-alert type="error" size="small">{{ preview.result.error || '预检未通过' }}</n-alert>
                  <n-space size="small" style="margin-top:6px">
                    <n-button size="tiny" @click="step = 2">修改数据设置</n-button>
                    <n-button size="tiny" :disabled="!gen.plan" @click="previewPlan">重新预检</n-button>
                  </n-space>
                </div>
                <div v-else-if="previewState === 'stale'" class="ec-muted"
                     style="font-size:12px;margin-top:6px">
                  树、计划、意图、资料或裁决有变化——旧预检结果已过期，请重新预检后再生成。</div>
                <div v-else-if="previewState === 'idle'" class="ec-muted"
                     style="font-size:12px;margin-top:6px">
                  还未预检。进入下一步前必须先预检通过（确认数据可取到）。</div>
              </div>

              <!-- 高级：跳过计划旁路（默认关闭；仍受来源门禁约束） -->
              <n-collapse>
                <n-collapse-item title="高级：跳过计划直接生成（不推荐）" name="skip">
                  <div class="ec-muted" style="font-size:12px;margin-bottom:6px">
                    只凭写作意图或资料文件夹生成：不会得到逐节覆盖检查与预检回执，
                    数据问题只会在生成后暴露。空来源仍会被门禁拦截。</div>
                  <n-switch v-model:value="skipPlan" size="small" />
                  <span class="ec-muted" style="font-size:12px;margin-left:8px">
                    {{ skipPlan ? '已启用旁路（用下方意图/资料作为取数来源）' : '关闭' }}</span>
                </n-collapse-item>
              </n-collapse>
              <span class="ec-muted">意图 / 资料文件夹 / 已确认计划至少给一个（三者全空不允许生成）</span>
            </n-space>
            <n-space size="small" justify="space-between" style="margin-top:12px">
              <n-button @click="() => { step = 1 }" :disabled="run.status === 'running'">上一步</n-button>
              <n-button type="primary" :disabled="!canNext2 || run.status === 'running'"
                        data-testid="to-step3" @click="step = 3">下一步：生成</n-button>
            </n-space>
          </div>

          <!-- ③ 生成（V4-05：最终复核卡 + 模型档位） -->
          <div v-if="step === 3">
            <n-space vertical size="small">
              <n-card size="small" data-testid="final-review"
                      style="border:1px solid var(--ec-line,#e5e7eb)">
                <b style="font-size:13px">生成前确认</b>
                <div style="font-size:13px;line-height:1.9;margin-top:6px">
                  <div>结构树：<b>{{ finalReview.treeName }}</b>
                    <span class="ec-mono ec-muted">（{{ finalReview.treeId }}）</span>
                    ｜v{{ finalReview.version }}｜{{ finalReview.nSections }} 节
                    ｜结构检查：{{ finalReview.lintText }}</div>
                  <div>数据计划：{{ finalReview.plan }}
                    <n-tag size="tiny" :type="finalReview.planConfirmed ? 'success' : 'warning'">
                      {{ finalReview.planConfirmed ? '已确认' : '未确认' }}</n-tag>
                    <span v-if="finalReview.planFocus !== '—'" class="ec-muted">｜意图：{{ String(finalReview.planFocus).slice(0, 40) }}</span></div>
                  <div>数据来源：{{ finalReview.sources }}
                    <span v-if="finalReview.nFacts != null">｜预检事实 <b>{{ finalReview.nFacts }}</b> 条</span>
                    <span v-if="finalReview.nWarnings != null">｜警告 {{ finalReview.nWarnings }} 条</span></div>
                  <div v-if="reuseDir" data-testid="reuse-info" class="ec-muted">
                    复用数据：{{ reuseDir }}
                    <span v-if="reuseInfo && reuseInfo.facts != null">（{{ reuseInfo.facts }} 条事实</span>
                    <span v-if="reuseInfo && reuseInfo.date">，生成于 {{ reuseInfo.date }}）</span>
                    ——生成时将跳过取数，直接沿用这份数据。</div>
                  <div class="ec-muted">预计耗时 5–10 分钟；生成完成会出现在报告库，可随时离开此页（任务条可查看进度与取消）。</div>
                </div>
              </n-card>
              <n-space size="small" align="center">
                <span class="ec-dim" style="font-size:13px">模型档位：</span>
                <n-radio-group v-model:value="modelTier" size="small" data-testid="model-tier">
                  <n-radio v-for="t in tierOptions" :key="t.value" :value="t.value"
                           :title="t.title || ''">{{ t.label }}</n-radio>
                </n-radio-group>
                <span class="ec-muted" style="font-size:12px">当前选择：{{ finalReview.tierLabel }}</span>
              </n-space>
              <n-space size="small" align="center">
                <n-button type="primary" :disabled="run.status === 'running'" @click="startRun">
                  {{ run.status === 'running' ? '生成中…' : '开始生成' }}</n-button>
                <div v-if="run.events.length" class="ec-log" style="max-height:200px;flex:1">
                  <div v-for="(ev, i) in run.events.filter(e => e.message)" :key="i">{{ ev.message }}</div>
                </div>
              </n-space>
              <n-alert v-if="run.status === 'error'" type="error" size="small">生成失败，可回上一步调整数据来源后重试。</n-alert>
              <n-alert v-if="run.status === 'cancelled'" type="warning" size="small">已取消。</n-alert>
            </n-space>
            <n-space size="small" justify="space-between" style="margin-top:12px">
              <n-button @click="step = 2" :disabled="run.status === 'running'">上一步</n-button>
            </n-space>
          </div>
        </n-card>
      `,
    },
  };
})();
