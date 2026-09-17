/* 报告详情（V2 §三 §3，/reports/:dir）：一处看全。
   五页签：预览（全站唯一）/ 反馈迭代（树模式）/ 治理体检 / 文件 / 元信息。
   经典模式报告：预览/治理/文件/元信息可用，反馈页签显示占位说明。 */
(function () {
  const { ref, reactive, computed, onMounted, h } = Vue;

  EC.views['/reports/:dir'] = {
    title: '报告详情',
    component: {
      setup() {
        const NA = window.naive;
        const dialog = NA.useDialog();
        // dir 取自参数路由 /reports/:dir（app.js 已解析进 query.dir）
        const raw = location.hash.replace(/^#/, '');
        const seg = raw.split('?')[0].match(/^\/reports\/([^/]+)/);
        const dir = ref(seg ? decodeURIComponent(seg[1])
          : new URLSearchParams(raw.split('?')[1] || '').get('dir'));
        // E5 双保险：入口统一归一化为目录名（防上游传来绝对路径 → 产物接口 403）
        if (dir.value)
          dir.value = String(dir.value).replace(/\\/g, '/').split('/').pop();
        const meta = ref(null);
        const outline = ref(null);
        const sections = ref(null);
        const judge = ref(null);
        // V4-01（P2）：详情加载三态——核心请求（meta）404 时给明确出路，
        // 不再吞错误渲染空壳（只留标题、页签、下载按钮的假页面）
        const loadState = ref('loading');   // 'loading' | 'ready' | 'error'
        const loadError = ref(null);
        const reconcileR = ref(null);
        const validateR = ref(null);
        const revHistory = ref(null);
        const judgeHistory = ref(null);      // 修订轮轨迹 [{round, total}]
        const roundsList = ref([]);
        const files = ref([]);
        const activeTab = ref('preview');
        const isTree = computed(() => !!(meta.value && meta.value.tree_id));
        const sameTree = ref([]);            // 双向追溯：同树其他报告

        const get = (f) => EC.api.get(
          `/api/runs/artifact?dir=${encodeURIComponent(dir.value)}&file=${f}`)
          .catch(() => null);

        async function load() {
          if (!dir.value) return;
          loadState.value = 'loading'; loadError.value = null;
          meta.value = await get('meta.json');
          // V4-01：meta 是核心请求——404（目录不存在/已删除/错误嵌套产物）→ 错误卡；
          // 附属 JSON 缺失仍按旧产物兼容降级（null 继续走原逻辑）
          if (!meta.value) {
            loadState.value = 'error';
            loadError.value = '报告不存在或已被删除（目录「' + dir.value + '」下没有可读的报告数据）。'
              + '如果刚生成完成，可能是产物仍在写入，可稍后点「重新加载」。';
            return;
          }
          loadState.value = 'ready';
          outline.value = await get('outline.json');
          sections.value = await get('sections.json');
          judge.value = await get('judge_report.json');
          reconcileR.value = await get('reconcile_report.json');
          validateR.value = await get('validate_report.json');
          revHistory.value = await get('revision_history.json');
          // 修订轮轨迹：revision_round*.json 的 judge 分数
          const fileRows = await EC.api.get(
            `/api/runs/${encodeURIComponent(dir.value)}/files`).catch(() => []);
          files.value = fileRows || [];
          const roundFiles = fileRows
            .filter((f) => /^revision_round\d+\.json$/.test(f.name))
            .sort((a, b) => a.name.localeCompare(b.name, undefined,
                                                { numeric: true }));
          judgeHistory.value = [];
          for (const f of roundFiles) {
            const j = await get(f.name);
            if (j && j.judge) judgeHistory.value.push({
              round: parseInt(f.name.replace(/\D/g, ''), 10),
              total: j.judge.total, verdict: j.judge.verdict });
          }
          if (isTree.value) {
            loadRounds();
            // 双向追溯：同树其他报告
            EC.api.get(`/api/runs?tree=${encodeURIComponent(meta.value.tree_id)}`)
              .then((rs) => { sameTree.value = rs.filter((r) => r.dir !== dir.value); })
              .catch(() => { sameTree.value = []; });
          }
        }
        async function loadRounds() {
          try {
            roundsList.value = await EC.api.get(
              `/api/runs/${encodeURIComponent(dir.value)}/feedback/rounds`);
          } catch (e) { roundsList.value = []; }
        }
        onMounted(load);

        const titles = computed(() => {
          const t = {};
          if (sections.value && meta.value && meta.value.tree_id) {
            for (const s of (sections.value.texts || [])) t[s.section_id] = s.section_id;
          }
          return t;
        });

        /* —— F2 图表状态：meta.chart_failures（失败清单）+ 产物 chart_*.png（成功推断） —— */
        const chartStatus = computed(() => {
          const failures = (meta.value && meta.value.chart_failures) || [];
          const okCount = files.value.filter((f) => /^chart_.+\.png$/.test(f.name)).length;
          return { failures, okCount };
        });

        /* —— 治理体检：〔〕通识标注扫描 —— */
        const genknowMarks = computed(() => {
          if (!sections.value) return [];
          const marks = [];
          const scan = (label, body) => {
            for (const m of String(body || '').matchAll(/〔[^〕]{1,40}〕/g))
              marks.push({ where: label, mark: m[0] });
          };
          scan('标题', (outline.value || {}).title);
          for (const v of (sections.value.views || [])) scan(`观点.${v.slot_id}`, v.body);
          for (const t of (sections.value.texts || [])) scan(t.section_id, t.body);
          for (const [sid, n] of Object.entries(sections.value.notes || {}))
            scan(sid, (n && n.body) || n || '');
          scan('风险提示', (sections.value.risks || {}).body);
          return marks;
        });

        /* —— 反馈迭代（树模式，反馈工作台全能力） —— */
        const fbText = ref('');
        const ops = ref([]);
        const ambiguities = ref([]);
        const parsing = ref(false);
        const autoRun = ref(false);
        const job = reactive({ id: null, status: 'idle', events: [] });
        const diff = reactive({ show: false, round: null, data: null });
        const judgeInfo = ref(null);
        const initialJudge = ref(null);   // V4-08：深评前的初始评审（折叠区展示）

        const sectionsView = computed(() => {
          if (!sections.value) return [];
          const out = [];
          if ((outline.value || {}).title)
            out.push({ anchor: 'title', title: '标题', body: outline.value.title });
          for (const t of (sections.value.texts || []))
            out.push({ anchor: t.section_id, title: t.section_id, body: t.body });
          for (const [sid, n] of Object.entries(sections.value.notes || {}))
            out.push({ anchor: sid, title: sid + '（表说明）', body: (n && n.body) || '' });
          if ((sections.value.risks || {}).body)
            out.push({ anchor: 'risks', title: '风险提示', body: sections.value.risks.body });
          return out;
        });

        async function parseFeedback() {
          if (!fbText.value.trim()) { EC.toast('先写意见', 'warning'); return; }
          parsing.value = true; ops.value = []; ambiguities.value = [];
          try {
            const r = await EC.api.post(
              `/api/runs/${encodeURIComponent(dir.value)}/feedback/parse`,
              { text: fbText.value });
            ops.value = r.ops.map((o) => ({ ...o, _on: true }));
            ambiguities.value = r.ambiguities || [];
            if (!r.ops.length) EC.toast('没有解析出可执行的意见', 'warning');
            else if (autoRun.value) applyFeedback();
          } catch (e) { EC.toast(e.message, 'error'); }
          finally { parsing.value = false; }
        }
        function watchJob(esUrl) {
          job.status = 'running'; job.events = [];
          const es = new EventSource(esUrl);
          es.onmessage = (m) => {
            const ev = JSON.parse(m.data);
            job.events.push(ev);
            if (ev.type === 'end') {
              es.close();
              job.status = ev.status;
              if (ev.status === 'error') EC.toast(ev.error || '执行失败', 'error');
              else { load(); EC.toast('完成', 'success'); }
            }
          };
          es.onerror = () => es.close();
        }
        async function applyFeedback() {
          const chosen = ops.value.filter((o) => o._on);
          if (!chosen.length) { EC.toast('没有勾选任何意见', 'warning'); return; }
          try {
            const r = await EC.api.post(
              `/api/runs/${encodeURIComponent(dir.value)}/feedback/apply`,
              { text: fbText.value, ops: chosen.map(({ _on, ...o }) => o) });
            watchJob(r.events_url);
          } catch (e) { EC.toast(e.message, 'error'); }
        }
        async function showDiff(round) {
          diff.round = round;
          diff.data = await EC.api.get(
            `/api/runs/${encodeURIComponent(dir.value)}/feedback/diff?round=${round}`);
          diff.show = true;
        }
        function doRollback(round) {
          EC.api.post(`/api/runs/${encodeURIComponent(dir.value)}/feedback/rollback`,
            { round })
            .then((r) => watchJob(r.events_url))
            .catch((e) => EC.toast(e.message, 'error'));
        }
        function rollback(round) {
          dialog.warning({
            title: '回滚确认',
            content: `撤销第 ${round} 轮？将恢复该轮之前的稿面并重渲染（树的结构改动请用模板页的版本回滚）。`,
            positiveText: '确认回滚',
            negativeText: '取消',
            onPositiveClick: () => doRollback(round),
          });
        }
        async function judgeDeep() {
          // V4-08（P10）：单一当前评审——深评结果落到 judge（页头实时更新），
          // 深评前的分数保留在 initialJudge（初始评审折叠区），不再写入未渲染变量
          const r = await EC.api.post(`/api/runs/${encodeURIComponent(dir.value)}/judge`, {});
          job.id = r.id; job.status = 'running'; job.events = [];
          const es = new EventSource(r.events_url);
          es.onmessage = (m) => {
            const ev = JSON.parse(m.data);
            job.events.push(ev);
            if (ev.type === 'end') {
              es.close(); job.status = ev.status;
              EC.api.get(`/api/runs/artifact?dir=${encodeURIComponent(dir.value)}&file=judge_report.json`)
                .then((j) => {
                  if (j && judge.value && judge.value.total !== j.total
                    && !initialJudge.value)
                    initialJudge.value = judge.value;
                  if (j) judge.value = j;
                  EC.toast(`深度评审完成：${j ? j.total : '?'} 分`, 'success');
                });
            }
          };
          es.onerror = () => es.close();
        }

        /* —— 文件页签（V4-13：可交付文件置顶，生成与审计记录默认折叠） —— */
        const DELIVERABLE_RE = /^(final\.html|final\.docx|chart_.+\.png)$/;
        const deliverableFiles = computed(() =>
          files.value.filter((f) => DELIVERABLE_RE.test(f.name)));
        const auditFiles = computed(() =>
          files.value.filter((f) => !DELIVERABLE_RE.test(f.name)));
        const auditOpen = ref(false);   // V4-13：审计记录默认折叠
        const fileContent = reactive({ show: false, name: null, text: '' });
        const fileCols = [
          { title: '文件', key: 'name',
            render: (r) => h('a', { style: 'cursor:pointer;color:#2080f0',
                                    onClick: () => viewFile(r) }, r.name) },
          { title: '大小', key: 'size', width: 110,
            render: (r) => (r.size / 1024).toFixed(1) + ' KB' },
          { title: '修改时间', key: 'mtime', width: 150 },
        ];
        async function viewFile(f) {
          const isHtml = f.name.endsWith('.html');
          if (isHtml) {
            window.open(`/artifacts/${encodeURIComponent(dir.value)}/${f.name}`,
                        '_blank');
            return;
          }
          const text = await get(f.name);
          fileContent.name = f.name;
          fileContent.text = text ? JSON.stringify(text, null, 2)
                                  : '（二进制或不可预览：用下方直接链接）';
          fileContent.show = true;
        }

        const scoreColor = (s) => s >= 8 ? '#18a058' : s >= 6 ? '#f0a020' : '#d03050';

        // V4-07：报告完成后的回环——补数据 / 改结构 / 复用数据再写一版。
        // reuseSameData 仅当 V4 新 meta 字段齐备时显示（旧产物按不可复用展示）；
        // 深链只负责预填，最终由后端三重校验（路径/facts/计划）与用户点击放行
        const reuseLinks = computed(() => {
          const m = meta.value;
          if (!m || !m.tree_id) return null;
          const qs = 'tree=' + encodeURIComponent(m.tree_id)
            + (m.plan_file ? '&plan=' + encodeURIComponent(m.plan_file) : '');
          return {
            redraft: '#/new?' + qs + '&mode=data',
            restructure: '#/templates?tree=' + encodeURIComponent(m.tree_id),
            reuseSameData: (m.plan_file && m.facts_count != null)
              ? '#/new?' + qs + '&reuse=' + encodeURIComponent(dir.value) : null,
          };
        });
        function goLink(h) { location.hash = h; }

        // V4-09：术语层映射（主文案中文，技术键留在数据属性/tooltip）
        const dimLabel = (k) => EC.term(k).label;
        const kindLabel = (k) => ({ style: '文风', data: '数据',
          structure: '结构' }[k] || k);

        // V4-01：导航动作放 setup（模板内联裸 location 解析不到——实测点击无效）
        function goReportsList() { location.hash = '/reports'; }
        function openReport(d) {
          location.hash = '/reports/' + encodeURIComponent(d);
        }

        // 自动化观测缝（仅挂内存引用，无 UI 影响）
        EC._detailView = { dir, meta, sections, outline, judge, activeTab,
          fbText, ops, roundsList, parseFeedback, applyFeedback, showDiff,
          rollback, judgeDeep, load, chartStatus, initialJudge,
          reuseLinks, loadState };

        return {
          dir, meta, outline, sections, judge, reconcileR, validateR,
          revHistory, judgeHistory, roundsList, files, activeTab, isTree,
          sameTree, genknowMarks, scoreColor, chartStatus,
          dimLabel, kindLabel, verdictLabel: EC.verdictLabel,
          gateLabel: EC.gateLabel, lintText: EC.lintText,
          loadState, loadError,
          goReportsList, openReport, reuseLinks, goLink,
          fbText, ops, ambiguities, parsing, autoRun, job, diff, judgeInfo,
          sectionsView, parseFeedback, applyFeedback, showDiff, rollback,
          judgeDeep, fileContent, viewFile, fileCols,
          deliverableFiles, auditFiles, auditOpen,
          encode: encodeURIComponent,
        };
      },
      template: `
        <n-space vertical size="small">
          <!-- V4-01：加载中 / 错误出路（P2 死胡同修复） -->
          <n-card v-if="loadState === 'loading'" size="small" class="ec-card">
            <n-skeleton text :repeat="3" />
          </n-card>
          <n-card v-else-if="loadState === 'error'" size="small" class="ec-card"
                  data-testid="detail-error">
            <n-result status="404" title="报告打不开" :description="loadError">
              <template #footer>
                <n-space size="small" justify="center">
                  <n-button data-testid="detail-back" @click="goReportsList">返回报告库</n-button>
                  <n-button data-testid="detail-retry" type="primary" @click="load">重新加载</n-button>
                </n-space>
              </template>
            </n-result>
          </n-card>

          <div v-if="loadState === 'ready'" style="display:contents">
          <n-card size="small" class="ec-card">
            <template #header>
              {{ (outline && outline.title) || dir }}
              <n-tag v-if="meta" size="small" class="ec-mono" style="margin-left:8px">{{ dir }}</n-tag>
              <!-- V4-08：页头 = 当前评审（深评后实时更新）；初始分数在折叠区 -->
              <n-tag v-if="judge" size="small" style="margin-left:6px" data-testid="current-judge"
                     :type="judge.verdict === 'pass' ? 'success' : 'error'">
                当前评审 {{ judge.total }} 分 {{ verdictLabel(judge.verdict) }}</n-tag>
            </template>
            <n-collapse v-if="initialJudge" style="margin-top:4px">
              <n-collapse-item title="初始评审（生成时的分数，供对照）" name="init">
                <span class="ec-muted" style="font-size:13px">
                  初始评审 {{ initialJudge.total }} 分 {{ verdictLabel(initialJudge.verdict) }}；
                  当前评审为 {{ judge ? judge.total : '—' }} 分</span>
              </n-collapse-item>
            </n-collapse>
          </n-card>

          <n-alert v-if="judge && judge.verdict !== 'pass'" type="error" size="small">
            <b>评审未达标（遗留 {{ (judge.issues || []).length }} 个问题）——可在「反馈迭代」页签修订：</b>
            <div v-for="(it, i) in (judge.issues || []).slice(0, 3)" :key="i" style="font-size:12px">
              {{ i + 1 }}. [{{ it.target }}] {{ it.problem }}</div>
          </n-alert>

          <!-- V4-07：回环操作（顶部） -->
          <n-card v-if="reuseLinks" size="small" style="border:1px dashed var(--ec-line,#e5e7eb)"
                  data-testid="reuse-ctas">
            <n-space size="small" align="center">
              <span class="ec-muted" style="font-size:12px">继续处理：</span>
              <n-button size="tiny" type="primary" data-testid="cta-redraft"
                        @click="goLink(reuseLinks.redraft)">补数据再生成</n-button>
              <n-button size="tiny" data-testid="cta-restructure"
                        @click="goLink(reuseLinks.restructure)">先改结构</n-button>
              <n-button v-if="reuseLinks.reuseSameData" size="tiny" data-testid="cta-reuse"
                        @click="goLink(reuseLinks.reuseSameData)">按本次数据再写一版</n-button>
            </n-space>
          </n-card>

          <n-alert v-if="chartStatus.failures.length" type="warning" size="small">
            {{ chartStatus.failures.length }} 张图因数据不足未渲染（详见治理体检「图表状态」）——图表由代码按数据渲染，反馈迭代无法补图；请补数据后重新生成。
          </n-alert>

          <n-card size="small" class="ec-card">
            <n-tabs v-model:value="activeTab" type="line" size="small">
              <!-- ① 预览（全站唯一） -->
              <n-tab-pane name="preview" tab="预览">
                <n-space size="small" align="center" style="margin-bottom:8px">
                  <n-button size="small" type="primary" tag="a" attrType="a" data-testid="detail-download"
                            :href="'/api/runs/report?dir=' + encode(dir || '') + '&format=docx'">下载 docx</n-button>
                  <n-button size="small" tag="a" attrType="a" target="_blank"
                            :href="'/artifacts/' + encode(dir || '') + '/final.html'">新窗口打开</n-button>
                </n-space>
                <iframe :src="'/artifacts/' + encode(dir || '') + '/final.html'"
                        style="width:100%;height:calc(100vh - 300px);border:1px solid var(--ec-line,#e5e7eb);background:#fff"></iframe>
              </n-tab-pane>

              <!-- ② 反馈迭代（树模式） -->
              <n-tab-pane name="feedback" tab="反馈迭代">
                <div v-if="!isTree" style="padding:24px 0">
                  <n-empty description="经典模式报告不支持反馈迭代（经典模式已废弃；树模式报告可完整使用本页签）" size="large" />
                </div>
                <template v-else>
                <n-alert v-if="job.status === 'running'" type="info" size="small" style="margin-bottom:8px">
                  <div v-for="(ev, i) in job.events.filter(e => e.message)" :key="i">{{ ev.message }}</div>
                </n-alert>
                <n-grid :cols="24" :x-gap="12">
                  <n-gi :span="14">
                    <n-card size="small" title="报告（按节锚点）">
                      <div v-for="s in sectionsView" :key="s.anchor" style="margin-bottom:10px">
                        <n-tag size="small" class="ec-mono" style="margin-right:6px">{{ s.anchor }}</n-tag>
                        <b style="font-size:13px">{{ s.title }}</b>
                        <div class="ec-muted" style="font-size:13px;white-space:pre-wrap;margin-top:2px">{{ (s.body || '').slice(0, 400) }}{{ (s.body || '').length > 400 ? '…' : '' }}</div>
                      </div>
                    </n-card>
                  </n-gi>
                  <n-gi :span="10">
                    <n-card size="small" title="意见反馈">
                      <n-space vertical size="small">
                        <n-input v-model:value="fbText" type="textarea" :rows="4"
                                 placeholder="一段总意见，可混着说。例：市场那节太干，扩一倍；补一下2024年的产量数据；再加一节讲环保约束。" />
                        <n-space size="small" align="center">
                          <n-button size="small" type="primary" :loading="parsing" @click="parseFeedback">解析意见</n-button>
                          <n-button size="small" :disabled="!ops.length || job.status === 'running'" @click="applyFeedback">执行（{{ ops.filter(o => o._on).length }}）</n-button>
                          <span class="ec-muted" style="font-size:12px">解析后直接执行</span>
                          <n-switch v-model:value="autoRun" size="small" />
                          <n-button size="small" :loading="job.status === 'running'" @click="judgeDeep">深度评审</n-button>
                        </n-space>
                        <n-alert v-if="ambiguities.length" type="warning" size="small">
                          <div v-for="(a, i) in ambiguities" :key="i">{{ a }}</div>
                        </n-alert>
                        <div v-for="(o, i) in ops" :key="i"
                             style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:6px">
                          <n-space size="small" align="center">
                            <n-checkbox v-model:checked="o._on" />
                            <n-tag size="small" :type="o.kind === 'style' ? 'default' : o.kind === 'data' ? 'warning' : 'info'">
                              {{ kindLabel(o.kind) }}</n-tag>
                            <span class="ec-mono" style="font-size:12px">{{ o.target }}</span>
                          </n-space>
                          <div style="font-size:13px;margin-top:2px">{{ o.instruction }}</div>
                        </div>
                      </n-space>
                    </n-card>
                    <n-card size="small" title="反馈轮次" style="margin-top:10px">
                      <n-empty v-if="!roundsList.length" description="还没有反馈轮次" size="small" />
                      <n-list v-else show-divider>
                        <n-list-item v-for="r in roundsList" :key="r.round">
                          <n-space size="small" align="center" justify="space-between">
                            <span style="font-size:13px">
                              <!-- V4-08：kind=rollback 的记录行展示指向；apply 行展示意见
                                   （硬约束12：v-for 内不用结构性 template v-if，用 span） -->
                              <span v-if="r.kind === 'rollback'">↩ 已撤销第 {{ r.target_round }} 轮（第 {{ r.round }} 条记录）</span>
                              <span v-else>第 {{ r.round }} 轮
                                <span class="ec-muted">{{ (r.text || '').slice(0, 30) }}</span></span>
                              <!-- C3：当轮对账/校验徽标（V4-09 中文状态） -->
                              <n-tag v-if="r.reconcile" size="small"
                                     :type="r.reconcile === 'pass' ? 'success' : r.reconcile === 'warn' ? 'warning' : 'error'">
                                对账 {{ gateLabel(r.reconcile) }}</n-tag>
                              <n-tag v-if="r.validate" size="small"
                                     :type="r.validate === 'pass' ? 'success' : r.validate === 'warn' ? 'warning' : 'error'">
                                校验 {{ gateLabel(r.validate) }}</n-tag>
                              <n-tag v-if="r.rolled_back" size="small" type="error">已回滚</n-tag>
                              <n-tag v-if="r.tree_lint" size="small"
                                     :type="r.tree_lint.errors ? 'error' : r.tree_lint.warnings ? 'warning' : 'success'"
                                     :title="'结构检查（lint）：' + (r.tree_lint.errors || 0) + ' errors / ' + (r.tree_lint.warnings || 0) + ' warnings'">
                                树检查 {{ lintText(r.tree_lint) }}</n-tag>
                            </span>
                            <n-space size="small">
                              <n-button v-if="r.has_diff" size="tiny" @click="showDiff(r.round)">看 diff</n-button>
                              <!-- V4-08：仅未回滚的修改轮可回滚；已回滚/回滚记录行不再给按钮 -->
                              <n-button v-if="r.kind === 'apply' && !r.rolled_back"
                                        size="tiny" type="error" quaternary
                                        :disabled="job.status === 'running'"
                                        @click="rollback(r.round)">回滚此轮</n-button>
                            </n-space>
                          </n-space>
                        </n-list-item>
                      </n-list>
                    </n-card>
                  </n-gi>
                </n-grid>
                </template>
              </n-tab-pane>

              <!-- ③ 治理体检 -->
              <n-tab-pane name="audit" tab="治理体检">
                <n-grid :cols="24" :x-gap="12">
                  <n-gi :span="10">
                    <n-card size="small" title="报告评审 · 五维评分（各维满分 10）">
                      <div v-for="(d, k) in (judge && judge.scores) || {}" :key="k" style="margin-bottom:8px">
                        <div style="display:flex;justify-content:space-between;font-size:12px">
                          <span>{{ dimLabel(k) }} <term-help :term="k" :text="false" /></span>
                          <b :style="{ color: scoreColor(d.score) }">{{ d.score }}/10</b>
                        </div>
                        <n-progress type="line" :percentage="d.score * 10" :show-indicator="false"
                                    :color="scoreColor(d.score)" style="margin-top:2px" />
                        <div class="ec-muted" style="font-size:12px">{{ d.comment }}</div>
                      </div>
                      <div v-if="judge && judge.method === 'median_of_3'" class="ec-muted" style="font-size:12px">
                        三评取中位：原始分 {{ (judge.runs || []).map(r => r.total).join(' / ') }}
                        <span v-if="judge.unstable_note" style="color:#f0a020">{{ judge.unstable_note }}</span>
                      </div>
                    </n-card>
                    <n-card size="small" title="〔〕通识标注" style="margin-top:10px">
                      <n-empty v-if="!genknowMarks.length" description="正文无〔〕通识标注" size="small" />
                      <div v-else>
                        <div class="ec-muted" style="font-size:12px;margin-bottom:4px">共 {{ genknowMarks.length }} 处（全篇至多 3 处）：</div>
                        <n-tag v-for="(m, i) in genknowMarks.slice(0, 20)" :key="i" size="small"
                               style="margin:0 4px 4px 0">{{ m.mark }}（{{ m.where }}）</n-tag>
                      </div>
                    </n-card>
                    <n-card v-if="reuseLinks" size="small" title="继续处理" style="margin-top:10px"
                            data-testid="reuse-ctas-audit">
                      <n-space size="small" vertical>
                        <n-button size="tiny" type="primary" @click="goLink(reuseLinks.redraft)">补数据再生成</n-button>
                        <n-button size="tiny" @click="goLink(reuseLinks.restructure)">先改结构</n-button>
                        <n-button v-if="reuseLinks.reuseSameData" size="tiny"
                                  @click="goLink(reuseLinks.reuseSameData)">按本次数据再写一版</n-button>
                      </n-space>
                    </n-card>
                    <n-card size="small" title="图表状态" style="margin-top:10px">
                      <n-empty v-if="!chartStatus.failures.length && !chartStatus.okCount"
                               description="本报告未声明图表" size="small" />
                      <div v-else>
                        <div v-if="chartStatus.okCount" class="ec-muted" style="font-size:12px;margin-bottom:4px">
                          ✓ {{ chartStatus.okCount }} 张渲染成功</div>
                        <div v-for="(f, i) in chartStatus.failures" :key="i"
                             style="display:flex;gap:8px;align-items:flex-start;margin-bottom:4px;font-size:12px">
                          <n-tag size="small" type="error">✗ {{ f.title }}</n-tag>
                          <span class="ec-muted" style="flex:1">{{ f.reason }}</span>
                        </div>
                      </div>
                    </n-card>
                  </n-gi>
                  <n-gi :span="14">
                    <n-card size="small" title="对账逐节">
                      <div v-for="c in (reconcileR && reconcileR.checks) || []" :key="c.section"
                           style="display:flex;gap:8px;align-items:center;margin-bottom:4px;font-size:13px">
                        <n-tag size="small" :type="!c.unknown_numbers.length && !c.cited_missing.length ? 'success' : 'warning'">
                          {{ !c.unknown_numbers.length && !c.cited_missing.length ? '✓' : '⚠' }}</n-tag>
                        <span style="flex:1">{{ c.section }}</span>
                        <span class="ec-muted" style="font-size:12px">
                          未匹配 {{ (c.unknown_numbers || []).length }} ｜缺失引用 {{ (c.cited_missing || []).length }}</span>
                      </div>
                      <n-divider style="margin:8px 0" />
                      <div class="ec-muted" style="font-size:12px">
                        数字对账 {{ gateLabel((reconcileR || {}).status) }}
                        ｜规则校验 {{ gateLabel((validateR || {}).status) }}
                        （{{ ((validateR || {}).items || []).length }} 条）</div>
                      <div v-for="(it, i) in (validateR && validateR.items) || []" :key="i" style="font-size:12px;margin-top:2px">
                        <n-tag size="tiny" :type="it.status === 'pass' ? 'success' : it.status === 'fail' ? 'error' : 'warning'">
                          {{ gateLabel(it.status) }}</n-tag>
                        {{ it.rule }}：{{ it.detail }}</div>
                    </n-card>
                    <n-card size="small" title="修订轮轨迹" style="margin-top:10px">
                      <n-empty v-if="!(judgeHistory || []).length && !(revHistory && (revHistory.rounds || []).length)"
                               description="无修订轮（一次过或未触发修订）" size="small" />
                      <div v-else>
                        <div v-for="r in judgeHistory" :key="r.round" style="display:flex;gap:10px;align-items:center;font-size:13px;margin-bottom:4px">
                          <span class="ec-mono">第 {{ r.round }} 轮修订后</span>
                          <n-tag size="small" :type="r.verdict === 'pass' ? 'success' : 'error'">{{ r.total }} 分</n-tag>
                        </div>
                        <div class="ec-muted" style="font-size:12px">
                          记录轮数：{{ (revHistory && revHistory.rounds || []).length }}
                          <span v-if="revHistory && (revHistory.structure_notes || []).length">
                            ｜结构处理：{{ revHistory.structure_notes.join('；') }}</span>
                          <span v-if="revHistory && (revHistory.thin_warnings || []).length">
                            ｜数据稀薄节：{{ revHistory.thin_warnings.length }}</span>
                        </div>
                      </div>
                    </n-card>
                  </n-gi>
                </n-grid>
              </n-tab-pane>

              <!-- ④ 文件（V4-13：可交付文件置顶 + 审计记录默认折叠） -->
              <n-tab-pane name="files" tab="文件">
                <n-space vertical size="small">
                  <n-space size="small" align="center">
                    <n-button size="small" type="primary" tag="a" attrType="a"
                              :href="'/api/runs/report?dir=' + encode(dir || '') + '&format=docx'">下载 docx</n-button>
                    <n-button size="small" tag="a" attrType="a" target="_blank"
                              :href="'/artifacts/' + encode(dir || '') + '/final.html'">新窗口打开 HTML</n-button>
                  </n-space>
                  <n-card size="small" title="可交付文件（HTML / DOCX / 图表）"
                          data-testid="deliverables" style="border:1px solid var(--ec-line,#e5e7eb)">
                    <n-empty v-if="!deliverableFiles.length"
                             description="还没有可交付文件（生成完成后这里会出现 HTML/DOCX 与图表）" size="small" />
                    <n-data-table v-else :columns="fileCols" :data="deliverableFiles"
                                  size="small" :max-height="260" />
                  </n-card>
                  <div>
                    <n-button size="tiny" quaternary data-testid="audit-toggle"
                              @click="auditOpen = !auditOpen">
                      {{ auditOpen ? '▾' : '▸' }} 生成与审计记录（{{ auditFiles.length }}
                      个文件：事实 / 评审 / 轮次 / 日志）</n-button>
                    <div v-if="auditOpen" data-testid="audit-files" style="margin-top:6px">
                      <n-data-table :columns="fileCols" :data="auditFiles"
                                    size="small" :max-height="420" />
                    </div>
                  </div>
                </n-space>
                <n-drawer v-model:show="fileContent.show" :width="640" placement="right">
                  <n-drawer-content :title="fileContent.name || '文件'" closable>
                    <pre class="ec-log" style="max-height:70vh;white-space:pre-wrap;font-size:12px">{{ fileContent.text }}</pre>
                  </n-drawer-content>
                </n-drawer>
              </n-tab-pane>

              <!-- ⑤ 元信息（双向追溯之一） -->
              <n-tab-pane name="meta" tab="元信息">
                <n-grid :cols="24" :x-gap="12">
                  <n-gi :span="12">
                    <n-card size="small" title="生成信息">
                      <div v-if="meta" style="font-size:13px;line-height:1.9">
                        <div>树：<b>{{ meta.tree_id || '（经典模式：' + (meta.type_name || meta.type_id || '—') + '）' }}</b></div>
                        <div v-if="meta.tree_id">树版本：v{{ meta.tree_version }}
                          <n-tag size="tiny" class="ec-mono" style="margin-left:4px">{{ meta.tree_fingerprint }}</n-tag></div>
                        <div>参数：{{ JSON.stringify(meta.params || {}) }}</div>
                        <div>模型档位：{{ meta.model_tier || '按分工' }}　计划模式：{{ meta.plan_mode || '—' }}</div>
                        <div>创建时间：{{ meta.created_at }}</div>
                        <div v-if="meta.style_card">文风卡：{{ meta.style_card }}</div>
                        <div v-if="meta.reuse_data" class="ec-muted">数据复用自：{{ meta.reuse_data }}</div>
                        <div v-if="meta.facts_count != null" class="ec-muted">
                          事实数：{{ meta.facts_count }}
                          <span v-if="meta.data_generated_at">｜数据生成于 {{ meta.data_generated_at }}</span></div>
                      </div>
                      <n-space v-if="reuseLinks" size="small" style="margin-top:8px"
                               data-testid="reuse-ctas-meta">
                        <n-button size="tiny" @click="goLink(reuseLinks.redraft)">补数据再生成</n-button>
                        <n-button size="tiny" @click="goLink(reuseLinks.restructure)">先改结构</n-button>
                        <n-button v-if="reuseLinks.reuseSameData" size="tiny"
                                  @click="goLink(reuseLinks.reuseSameData)">按本次数据再写一版</n-button>
                      </n-space>
                    </n-card>
                  </n-gi>
                  <n-gi :span="12">
                    <n-card size="small" title="同树其他报告（双向追溯）">
                      <n-empty v-if="!isTree" description="经典报告无树追溯" size="small" />
                      <n-empty v-else-if="!sameTree.length" description="该树暂无其他报告" size="small" />
                      <n-list v-else show-divider>
                        <n-list-item v-for="r in sameTree" :key="r.dir">
                          <n-space size="small" align="center" justify="space-between">
                            <span style="font-size:13px">{{ (r.title || r.dir).slice(0, 30) }}
                              <span class="ec-muted ec-mono" style="font-size:12px">{{ r.mtime }}</span></span>
                            <n-space size="small">
                              <n-tag size="small" :type="r.verdict === 'pass' ? 'success' : 'error'">
                                {{ r.judge_total == null ? '—' : r.judge_total }}</n-tag>
                              <n-button size="tiny" @click="openReport(r.dir)">打开</n-button>
                            </n-space>
                          </n-space>
                        </n-list-item>
                      </n-list>
                    </n-card>
                  </n-gi>
                </n-grid>
              </n-tab-pane>
            </n-tabs>
          </n-card>
          </div><!-- /loadState==='ready' (V4-01) -->

          <n-modal v-model:show="diff.show" preset="card" :title="'第 ' + diff.round + ' 轮 diff'" style="width:900px">
            <div v-for="(d, sid) in (diff.data && diff.data.sections) || {}" :key="sid" style="margin-bottom:12px">
              <b class="ec-mono" style="font-size:12px">{{ sid }}</b>
              <n-grid :cols="2" :x-gap="8">
                <n-gi>
                  <!-- V4-08：两栏加文字标题，颜色只作辅助 -->
                  <div class="ec-muted" style="font-size:12px;margin-bottom:2px"
                       data-testid="diff-before">修改前</div>
                  <div class="ec-log" style="max-height:200px;white-space:pre-wrap">{{ d.old || '（新增）' }}</div>
                </n-gi>
                <n-gi>
                  <div class="ec-muted" style="font-size:12px;margin-bottom:2px"
                       data-testid="diff-after">修改后</div>
                  <div class="ec-log" style="max-height:200px;white-space:pre-wrap;background:#f6ffed">{{ d.new }}</div>
                </n-gi>
              </n-grid>
            </div>
            <div v-if="diff.data && diff.data.title && diff.data.title.old !== diff.data.title.new">
              <b class="ec-mono" style="font-size:12px">title</b>
              <div>{{ diff.data.title.old }} → <b>{{ diff.data.title.new }}</b></div>
            </div>
          </n-modal>
        </n-space>
      `,
    },
  };
})();
