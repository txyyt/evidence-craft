/* 生成监控：左进度（SSE 流式）/ 中产物下钻（对账红点 + judge 雷达）/ 右预览 + docx。
   历史运行可回看任意一次产物。参数表单按部门 params_schema 动态渲染。 */
(function () {
  const { reactive, ref, computed, onMounted, watch, nextTick } = Vue;
  const { h } = Vue;

  const STAGES = [
    { key: 'data', label: '① 数据' },
    { key: 'outline', label: '② 大纲' },
    { key: 'sections', label: '③ 分节' },
    { key: 'review', label: '④ 评审' },
    { key: 'render', label: '⑤ 渲染' },
  ];
  const ARTIFACTS = [
    ['meta.json', '元信息'], ['facts.json', '事实'], ['crosscheck_report.json', '交叉校验'],
    ['outline.json', '大纲'], ['sections.json', '稿件'], ['reconcile_report.json', '对账'],
    ['validate_report.json', '规则校验'], ['judge_report.json', 'judge'],
  ];
  const DIM_LABELS = {
    structure: '结构', professionalism: '专业性', data_support: '数据支撑',
    compliance: '合规', readability: '可读性',
  };

  EC.views['/run'] = {
    title: '生成监控',
    component: {
      setup() {
        const store = EC.store;
        const templates = ref([]);
        const form = reactive({ stock: '', project: '', period: '', spec: '' });
        const paramSchema = ref({});
        const starting = ref(false);

        const runId = ref(null);
        const runStatus = ref('idle');   // idle | running | done | error
        const runError = ref('');
        const events = ref([]);
        const currentDir = ref(null);    // 产物目录名（运行中实时更新）

        const artifactTab = ref(null);
        const artifactJson = ref(null);
        const artifactErr = ref('');
        const radarDiv = ref(null);
        const previewKey = ref(0);

        const history = ref([]);
        const historyLoading = ref(false);

        const specOptions = computed(() => [
          { label: '默认（company_review）', value: '' },
          ...templates.value.map((t) => ({
            label: (t.draft ? '[草稿] ' : '') + t.name, value: t.file,
          })),
        ]);

        const stageState = computed(() => {
          const st = {};
          for (const s of STAGES) st[s.key] = { msgs: [], done: false };
          for (const ev of events.value) {
            if (ev.type !== 'progress') continue;
            const s = st[ev.stage];
            if (!s) continue;
            s.msgs.push(ev.message);
          }
          // 结束事件之后全部视为完成；否则最后一个有消息的阶段是当前阶段
          let lastActive = null;
          for (const ev of events.value) {
            if (ev.type === 'progress' && st[ev.stage]) lastActive = ev.stage;
          }
          for (const s of STAGES) {
            const i = STAGES.findIndex((x) => x.key === s.key);
            const la = STAGES.findIndex((x) => x.key === lastActive);
            s.done = runStatus.value === 'done' || i < la;
            s.active = runStatus.value === 'running' && i === la;
          }
          return st;
        });

        const judgeReport = computed(() =>
          artifactTab.value === 'judge_report.json' ? artifactJson.value : null);
        const reconcileReport = computed(() =>
          artifactTab.value === 'reconcile_report.json' ? artifactJson.value : null);
        const validateReport = computed(() =>
          artifactTab.value === 'validate_report.json' ? artifactJson.value : null);

        async function loadDept() {
          paramSchema.value = {};
          form.stock = form.project = form.period = '';
          try {
            const r = await EC.api.get('/api/departments/' + store.department);
            paramSchema.value = (r.profile && r.profile.params_schema) || {};
          } catch (e) { /* 部门接口异常时表单退化为空 */ }
        }

        async function loadHistory() {
          historyLoading.value = true;
          try { history.value = await EC.api.get('/api/runs'); }
          finally { historyLoading.value = false; }
        }

        async function start() {
          starting.value = true;
          runError.value = '';
          events.value = [];
          runStatus.value = 'running';
          artifactTab.value = artifactJson.value = null;
          try {
            const r = await EC.api.post('/api/runs/start', {
              department: store.department,
              stock: form.stock || null, project: form.project || null,
              period: form.period || null, spec: form.spec || null,
            });
            runId.value = r.id;
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              events.value.push(ev);
              if (ev.data && ev.data.run_dir) {
                currentDir.value = ev.data.run_dir.replace(/\\/g, '/').split('/').pop();
              }
              if (ev.type === 'end') {
                es.close();
                runStatus.value = ev.status === 'done' ? 'done' : 'error';
                if (ev.status !== 'done') runError.value = ev.error || '未知错误';
                if (ev.run_dir) openRun(ev.run_dir.replace(/\\/g, '/').split('/').pop());
                loadHistory();
              }
            };
            es.onerror = () => { es.close(); };
          } catch (e) {
            runStatus.value = 'error';
            runError.value = e.message;
          } finally { starting.value = false; }
        }

        async function openArtifact(file) {
          artifactTab.value = file;
          artifactErr.value = '';
          artifactJson.value = null;
          if (!currentDir.value) { artifactErr.value = '尚无产物目录'; return; }
          try {
            artifactJson.value = await EC.api.get(
              `/api/runs/artifact?dir=${encodeURIComponent(currentDir.value)}&file=${encodeURIComponent(file)}`);
            if (file === 'judge_report.json') nextTick(renderRadar);
          } catch (e) { artifactErr.value = e.message; }
        }

        function renderRadar() {
          const j = artifactJson.value;
          if (!j || !radarDiv.value || !window.echarts) return;
          const keys = Object.keys(j.scores || {});
          const chart = echarts.init(radarDiv.value);
          chart.setOption({
            radar: {
              indicator: keys.map((k) => ({ name: DIM_LABELS[k] || k, max: 10 })),
              radius: '62%',
            },
            series: [{
              type: 'radar',
              data: [{ value: keys.map((k) => (j.scores[k].score || 0)),
                       name: `总分 ${j.total}` }],
              areaStyle: { opacity: 0.25 },
            }],
          });
        }

        function openRun(dir) {
          currentDir.value = dir;
          artifactTab.value = null;
          artifactJson.value = null;
          previewKey.value++;            // 强制 iframe 重载
        }

        const previewUrl = computed(() => currentDir.value && runStatus.value === 'done'
          ? `/artifacts/${encodeURIComponent(currentDir.value)}/final.html?v=${previewKey.value}`
          : null);
        const docxUrl = computed(() => currentDir.value
          ? `/api/runs/report?dir=${encodeURIComponent(currentDir.value)}&format=docx`
          : null);

        const historyColumns = [
          { title: '时间', key: 'mtime', width: 150,
            render: (r) => h('span', { class: 'ec-mono' }, (r.mtime || '').replace('T', ' ')) },
          { title: '部门', key: 'department', width: 110 },
          { title: '对象', key: 'name', width: 190, class: 'ec-mono' },
          { title: '标题', key: 'title', ellipsis: { tooltip: true } },
          { title: 'judge', key: 'judge_total', width: 70,
            render: (r) => (r.judge_total == null ? '—' : String(r.judge_total)) },
          { title: '判定', key: 'verdict', width: 90,
            render: (r) => r.verdict
              ? h('n-tag', { size: 'small', type: r.verdict === 'pass' ? 'success' : 'error' },
                  { default: () => r.verdict.toUpperCase() })
              : '—' },
          { title: '操作', key: 'act', width: 130,
            render: (r) => h('n-space', { size: 'small' }, {
              default: () => [
                h('n-button', { size: 'tiny', onClick: () => openRun(r.name) },
                  { default: () => '查看产物', ...(r.has_docx ? {} : {}) }),
                r.has_docx ? h('n-button', {
                  size: 'tiny', type: 'primary', tag: 'a', attrType: 'a',
                  href: `/api/runs/report?dir=${encodeURIComponent(r.name)}&format=docx`,
                }, { default: () => 'docx' }) : null,
              ],
            }) },
        ];

        watch(() => store.department, loadDept);
        onMounted(async () => {
          try { templates.value = await EC.api.get('/api/studio/templates'); } catch (e) {}
          await loadDept();
          await loadHistory();
        });

        return {
          store, form, paramSchema, specOptions, starting, start,
          runId, runStatus, runError, events, stageState, STAGES,
          ARTIFACTS, artifactTab, artifactJson, artifactErr,
          openArtifact, judgeReport, reconcileReport, validateReport,
          radarDiv, previewUrl, docxUrl, currentDir,
          history, historyColumns, historyLoading,
        };
      },
      template: `
        <h3 class="ec-page-title">生成监控</h3>
        <n-grid :cols="24" :x-gap="12">
          <!-- 左：发起 + 进度 -->
          <n-gi :span="6">
            <n-card title="发起生成" size="small" class="ec-card">
              <n-form size="small" label-placement="top">
                <n-form-item v-if="paramSchema.stock" :label="paramSchema.stock">
                  <n-input v-model:value="form.stock" placeholder="如 000803" />
                </n-form-item>
                <n-form-item v-if="paramSchema.project" :label="paramSchema.project">
                  <n-input v-model:value="form.project" />
                </n-form-item>
                <n-form-item v-if="paramSchema.period" :label="paramSchema.period">
                  <n-input v-model:value="form.period" />
                </n-form-item>
                <n-form-item label="模板 spec">
                  <n-select v-model:value="form.spec" :options="specOptions" />
                </n-form-item>
              </n-form>
              <n-button type="primary" block :loading="starting"
                        :disabled="runStatus === 'running'" @click="start">
                {{ runStatus === 'running' ? '运行中…' : '生成' }}
              </n-button>
            </n-card>

            <n-card title="阶段进度" size="small" v-if="runId">
              <n-steps vertical size="small" :current="99">
                <n-step v-for="s in STAGES" :key="s.key"
                        :title="s.label"
                        :status="stageState[s.key].active ? 'process'
                                : stageState[s.key].done ? 'finish' : 'wait'">
                  <div class="ec-muted ec-mono" v-for="(m, i) in stageState[s.key].msgs"
                       :key="i" style="font-size:12px">{{ m }}</div>
                </n-step>
              </n-steps>
              <n-alert v-if="runStatus === 'error'" type="error" size="small"
                       style="margin-top:8px">{{ runError }}</n-alert>
            </n-card>
          </n-gi>

          <!-- 中：产物下钻 -->
          <n-gi :span="9">
            <n-card title="中间产物" size="small">
              <n-space size="small" style="margin-bottom:10px">
                <n-button v-for="[f, label] in ARTIFACTS" :key="f" size="tiny"
                          :type="artifactTab === f ? 'primary' : 'default'"
                          :disabled="!currentDir" @click="openArtifact(f)">{{ label }}</n-button>
              </n-space>
              <div v-if="artifactErr"><n-empty :description="artifactErr" size="small" /></div>

              <!-- 对账可视化：未匹配数字标红 -->
              <div v-else-if="reconcileReport">
                <n-alert v-if="!reconcileReport.checks" type="info" size="small">等待数据…</n-alert>
                <n-tag v-if="reconcileReport.status" size="small"
                       :type="reconcileReport.status === 'pass' ? 'success'
                             : reconcileReport.status === 'warn' ? 'warning' : 'error'">
                  对账 {{ reconcileReport.status.toUpperCase() }}
                </n-tag>
                <div v-for="c in reconcileReport.checks || []" :key="c.section"
                     style="margin-top:8px">
                  <n-tag size="small" :type="(c.unknown_numbers||[]).length ? 'error' : 'default'">
                    {{ c.section }}
                  </n-tag>
                  <span v-if="(c.unknown_numbers||[]).length" style="color:#d03050">
                    未匹配数字 {{ JSON.stringify(c.unknown_numbers) }}
                  </span>
                  <span v-if="(c.cited_missing||[]).length" style="color:#f0a020">
                    缺失引用 {{ JSON.stringify(c.cited_missing) }}
                  </span>
                  <span v-if="!(c.unknown_numbers||[]).length && !(c.cited_missing||[]).length"
                        class="ec-muted">数字全部对上账</span>
                </div>
              </div>

              <!-- 规则校验可视化 -->
              <div v-else-if="validateReport">
                <div v-for="(it, i) in validateReport.items || []" :key="i" style="margin-top:6px">
                  <n-tag size="small" :type="it.status === 'fail' ? 'error'
                        : it.status === 'warn' ? 'warning' : 'success'">{{ it.status }}</n-tag>
                  <b class="ec-mono" style="font-size:12px">{{ it.rule }}</b>
                  <div style="font-size:12px">{{ it.detail }}</div>
                </div>
              </div>

              <!-- judge 可视化：五维雷达 + issues -->
              <div v-else-if="judgeReport">
                <n-space align="center">
                  <n-tag :type="judgeReport.verdict === 'pass' ? 'success' : 'error'">
                    judge {{ judgeReport.total }} 分 {{ (judgeReport.verdict||'').toUpperCase() }}
                  </n-tag>
                </n-space>
                <div ref="radarDiv" style="width:100%;height:220px"></div>
                <div v-for="(s, k) in judgeReport.scores" :key="k" style="margin-top:4px;font-size:12px">
                  <b>{{ k }}：{{ s.score }}</b> —— {{ s.comment }}
                </div>
                <n-h6 style="margin:10px 0 4px">修订意见</n-h6>
                <div v-for="(it, i) in judgeReport.issues || []" :key="i" style="font-size:12px">
                  [{{ it.target }}] {{ it.problem }}
                </div>
              </div>

              <pre v-else-if="artifactJson"
                   class="ec-mono" style="font-size:12px;max-height:560px;overflow:auto">{{ JSON.stringify(artifactJson, null, 2) }}</pre>
              <n-empty v-else description="点击上方标签查看该阶段落盘产物" size="small" />
            </n-card>
          </n-gi>

          <!-- 右：最终报告 -->
          <n-gi :span="9">
            <n-card title="最终报告" size="small">
              <n-space size="small" style="margin-bottom:8px" v-if="currentDir">
                <n-tag size="small" class="ec-mono">{{ currentDir }}</n-tag>
                <n-button v-if="docxUrl" tag="a" attrType="a" :href="docxUrl"
                          type="primary" size="tiny">下载 docx</n-button>
                <n-button tag="a" attrType="a"
                          :href="previewUrl || ('/artifacts/' + encodeURIComponent(currentDir) + '/final.html')"
                          size="tiny" target="_blank">新窗口打开</n-button>
              </n-space>
              <iframe v-if="previewUrl" :key="previewKey" :src="previewUrl"
                      style="width:100%;height:600px;border:1px solid #eee;background:#fff"></iframe>
              <n-empty v-else description="运行完成后在此预览 final.html"
                       size="small" style="margin-top:120px" />
            </n-card>
          </n-gi>
        </n-grid>

        <n-card title="运行历史" size="small" style="margin-top:14px">
          <n-data-table :columns="historyColumns" :data="history" size="small"
                        :loading="historyLoading" :max-height="320" />
        </n-card>
      `,
    },
  };
})();
