/* 报告生成：选报告类型 → 填参数 → 数据预检 → 生成（SSE 进度/取消）→ 报告预览 +
   运行详情（对账/校验/judge 雷达）→ 下载；历史报告筛选、详情抽屉、删除、再来一次。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  const STAGES = [
    { key: 'data', label: '数据' },
    { key: 'outline', label: '大纲' },
    { key: 'sections', label: '分节' },
    { key: 'review', label: '评审' },
    { key: 'render', label: '渲染' },
  ];
  const ARTIFACTS = [
    ['meta.json', '元信息'], ['facts.json', '事实'], ['crosscheck_report.json', '交叉校验'],
    ['outline.json', '大纲'], ['sections.json', '稿件'], ['reconcile_report.json', '对账'],
    ['validate_report.json', '规则校验'], ['judge_report.json', 'judge'],
  ];
  const DIM_LABELS = { structure: '结构', professionalism: '专业性', data_support: '数据支撑',
    compliance: '合规', readability: '可读性' };

  EC.views['/generate'] = {
    title: '报告生成',
    component: {
      setup() {
        const types = ref([]);
        const selType = ref(null);
        const typeDetail = ref(null);
        const params = reactive({});
        const intent = ref('');       // 写作意图一段话（可选，意图规划模式）
        const folder = ref('');       // 本地资料文件夹（可选，现场建语料库）
        const modelTier = ref('default');  // F3 模型档位（default=按 tier_roles 分工）
        const TIER_OPTIONS = [
          { label: '默认（抽取/写作按 tier_roles 分工）', value: 'default' },
          { label: '快档 fast', value: 'fast' },
          { label: '质量档 quality', value: 'quality' },
        ];
        const starting = ref(false);
        const previewing = ref(false);
        const previewResult = ref(null);

        const runId = ref(null);
        const runStatus = ref('idle');   // idle | running | done | error | cancelled
        const runError = ref('');
        const errorDetail = ref('');
        const events = ref([]);
        const totalStats = ref({ llm_calls: 0, llm_seconds: 0 });
        const tierStats = ref(null);     // 分档调用统计 {fast:{calls,seconds,model},...}
        const currentDir = ref(null);
        const lastRun = ref(null);       // 再来一次用

        const detailTab = ref('preview');
        const artifactTab = ref(null);
        const artifactJson = ref(null);
        const artifactErr = ref('');
        const radarDiv = ref(null);
        const previewKey = ref(0);

        const history = ref([]);
        const filterType = ref(null);
        const drawer = reactive({ show: false, dir: null, tab: 'preview', artifact: null, artifactJson: null, err: '' });
        const radarDiv2 = ref(null);

        const publishedTypes = computed(() => types.value.filter((t) => t.status === 'published'));

        const stageState = computed(() => {
          const st = {};
          for (const s of STAGES) st[s.key] = { msgs: [] };
          let lastActive = null;
          for (const ev of events.value) {
            if (ev.type !== 'progress') continue;
            (st[ev.stage] || { msgs: [] }).msgs.push(ev);
            if (st[ev.stage]) lastActive = ev.stage;
          }
          const la = STAGES.findIndex((x) => x.key === lastActive);
          for (let i = 0; i < STAGES.length; i++) {
            st[STAGES[i].key].done = ['done', 'error', 'cancelled'].includes(runStatus.value) || i < la;
            st[STAGES[i].key].active = runStatus.value === 'running' && i === la;
          }
          return st;
        });

        async function loadTypes() {
          await EC.store.loadTypes();
          types.value = EC.store.types;
        }

        async function selectType(id) {
          selType.value = id;
          typeDetail.value = null;
          previewResult.value = null;
          if (!id) return;
          const d = await EC.api.get('/api/types/' + id);
          typeDetail.value = d;
          for (const k of Object.keys(d.params_schema || {})) params[k] = params[k] || '';
        }

        async function dataPreview() {
          if (!selType.value) { EC.toast('先选择报告类型', 'warning'); return; }
          previewing.value = true;
          previewResult.value = null;
          try {
            previewResult.value = await EC.api.post('/api/runs/preview',
              { type_id: selType.value, params: { ...params },
                intent: intent.value || null, folder: folder.value || null,
                model_tier: modelTier.value === 'default' ? null : modelTier.value });
          } catch (e) { previewResult.value = { ok: false, error: e.message }; }
          finally { previewing.value = false; }
        }

        async function start() {
          if (!selType.value) { EC.toast('先选择报告类型', 'warning'); return; }
          await startRun({ type_id: selType.value, params: { ...params },
            intent: intent.value || null, folder: folder.value || null,
            model_tier: modelTier.value === 'default' ? null : modelTier.value });
        }

        async function rerun() {
          if (lastRun.value) await startRun(lastRun.value);
        }

        async function startRun(body) {
          starting.value = true;
          runError.value = ''; errorDetail.value = '';
          events.value = [];
          runStatus.value = 'running';
          artifactTab.value = artifactJson.value = null;
          lastRun.value = body;
          try {
            const r = await EC.api.post('/api/runs/start', {
              type_id: body.type_id,
              stock: body.params.stock || null,
              project: body.params.project || null,
              period: body.params.period || null,
              intent: body.intent || null,
              folder: body.folder || null,
              model_tier: body.model_tier || null,
            });
            runId.value = r.id;
            const es = new EventSource(r.events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              events.value.push(ev);
              if (ev.data && ev.data.run_dir)
                currentDir.value = ev.data.run_dir.replace(/\\/g, '/').split('/').pop();
              if (ev.data && ev.data.llm_calls != null)
                totalStats.value = { llm_calls: ev.data.llm_calls, llm_seconds: ev.data.llm_seconds };
              if (ev.data && ev.data.llm_tiers) tierStats.value = ev.data.llm_tiers;
              if (ev.type === 'end') {
                es.close();
                runStatus.value = ev.status === 'done' ? 'done'
                  : ev.status === 'cancelled' ? 'cancelled' : 'error';
                if (ev.status === 'error') { runError.value = ev.error || '未知错误'; errorDetail.value = ev.error_detail || ''; }
                if (ev.run_dir) currentDir.value = ev.run_dir.replace(/\\/g, '/').split('/').pop();
                detailTab.value = runStatus.value === 'done' ? 'preview' : 'detail';
                loadHistory();
              }
            };
            es.onerror = () => es.close();
          } catch (e) {
            runStatus.value = 'error';
            runError.value = e.message;
          } finally { starting.value = false; }
        }

        async function cancel() {
          if (!runId.value) return;
          try {
            await EC.api.post(`/api/runs/${runId.value}/cancel`);
            EC.toast('已发出取消指令，等待当前步骤结束', 'info');
          } catch (e) { EC.toast('取消失败：' + e.message, 'error'); }
        }

        async function openArtifact(file, which = 'main') {
          if (which === 'main') {
            artifactTab.value = file; artifactJson.value = null; artifactErr.value = '';
            if (!currentDir.value) { artifactErr.value = '尚无产物'; return; }
            try {
              artifactJson.value = await EC.api.get(
                `/api/runs/artifact?dir=${encodeURIComponent(currentDir.value)}&file=${file}`);
              if (file === 'judge_report.json') setTimeout(() => renderRadar(radarDiv.value, artifactJson.value), 60);
            } catch (e) { artifactErr.value = e.message; }
          } else {
            drawer.artifact = file; drawer.artifactJson = null; drawer.err = '';
            try {
              drawer.artifactJson = await EC.api.get(
                `/api/runs/artifact?dir=${encodeURIComponent(drawer.dir)}&file=${file}`);
              if (file === 'judge_report.json') setTimeout(() => renderRadar(radarDiv2.value, drawer.artifactJson), 60);
            } catch (e) { drawer.err = e.message; }
          }
        }

        function renderRadar(el, j) {
          if (!el || !j || !window.echarts) return;
          const keys = Object.keys(j.scores || {});
          const chart = echarts.init(el);
          chart.setOption({
            radar: { indicator: keys.map((k) => ({ name: DIM_LABELS[k] || k, max: 10 })), radius: '62%' },
            series: [{ type: 'radar', areaStyle: { opacity: 0.25 },
              data: [{ value: keys.map((k) => (j.scores[k].score || 0)), name: `总分 ${j.total}` }] }],
          });
        }

        async function loadHistory() {
          const url = filterType.value ? `/api/runs?type_id=${encodeURIComponent(filterType.value)}` : '/api/runs';
          history.value = await EC.api.get(url);
        }

        async function viewRun(dir) {
          drawer.dir = dir; drawer.show = true; drawer.tab = 'preview';
          drawer.artifact = null; drawer.artifactJson = null; drawer.err = '';
        }

        async function deleteRun(dir) {
          try {
            await EC.api.del(`/api/runs/${encodeURIComponent(dir)}`);
            EC.toast('报告已删除', 'success');
            loadHistory();
            if (currentDir.value === dir) { currentDir.value = null; runStatus.value = 'idle'; }
          } catch (e) { EC.toast('删除失败：' + e.message, 'error'); }
        }

        const previewUrl = computed(() => currentDir.value && runStatus.value === 'done'
          ? `/artifacts/${encodeURIComponent(currentDir.value)}/final.html?v=${previewKey.value}` : null);
        const drawerPreviewUrl = computed(() => drawer.dir
          ? `/artifacts/${encodeURIComponent(drawer.dir)}/final.html` : null);

        const hisCols = [
          { title: '时间', key: 'mtime', width: 150,
            render: (r) => h('span', { class: 'ec-mono' }, (r.mtime || '').replace('T', ' ')) },
          { title: '报告类型', key: 'type_name', width: 140,
            render: (r) => r.type_name || '—' },
          { title: '对象', key: 'name', width: 170, class: 'ec-mono' },
          { title: 'judge', key: 'judge_total', width: 70,
            render: (r) => r.judge_total == null ? '—' : String(r.judge_total) },
          { title: '判定', key: 'verdict', width: 80,
            render: (r) => r.verdict
              ? h(NA.NTag, { size: 'small', type: r.verdict === 'pass' ? 'success' : 'error' },
                  { default: () => r.verdict.toUpperCase() }) : '—' },
          { title: '操作', key: 'act', width: 250,
            render: (r) => h(NA.NSpace, { size: 'small' }, { default: () => [
              h(NA.NButton, { size: 'tiny', onClick: () => viewRun(r.name) }, { default: () => '查看' }),
              h(NA.NButton, { size: 'tiny', tag: 'a', attrType: 'a',
                href: '/#/run?dir=' + encodeURIComponent(r.name) }, { default: () => '反馈' }),
              h(NA.NButton, { size: 'tiny', tag: 'a', attrType: 'a',
                href: `/api/runs/report?dir=${encodeURIComponent(r.name)}&format=docx`,
                type: 'primary' }, { default: () => 'docx' }),
              h(NA.NPopconfirm, { onPositiveClick: () => deleteRun(r.name) }, {
                trigger: () => h(NA.NButton, { size: 'tiny', quaternary: true, type: 'error' },
                  { default: () => '删除' }),
                default: () => '删除这次生成的全部产物？不可恢复。',
              }),
            ] }) },
        ];

        onMounted(async () => {
          await loadTypes();
          await loadHistory();
        });

        return {
          types, publishedTypes, selType, selectType, typeDetail, params,
          intent, folder, modelTier, TIER_OPTIONS,
          tierText: computed(() => {
            const t = tierStats.value;
            if (!t || !Object.keys(t).length) return '';
            return Object.entries(t)
              .map(([k, v]) => `${k === 'default' ? '默认' : k}档 ${v.calls} 次`)
              .join(' · ');
          }),
          starting, previewing, previewResult, dataPreview, start, rerun, lastRun,
          runId, runStatus, runError, errorDetail, events, stageState, STAGES,
          cancel, currentDir, detailTab, ARTIFACTS, artifactTab, artifactJson,
          artifactErr, openArtifact, judgeRadar: null, radarDiv, previewUrl, docxUrl: computed(() =>
            currentDir.value ? `/api/runs/report?dir=${encodeURIComponent(currentDir.value)}&format=docx` : null),
          totalStats,
          history, filterType, loadHistory, hisCols, drawer, viewRun, deleteRun,
          openArtifact2: (f) => openArtifact(f, 'drawer'), drawerPreviewUrl, radarDiv, radarDiv2,
          reconcileOf: (j) => j,
        };
      },
      template: `
        <n-card size="small" class="ec-card" title="发起生成">
          <n-space vertical size="small">
            <n-space size="small" align="center">
              <span class="ec-dim">报告类型：</span>
              <n-select v-model:value="selType" @update:value="selectType" style="width:280px"
                        :options="publishedTypes.map(t => ({label: t.name + '（' + t.id + '）', value: t.id}))"
                        placeholder="只列出已发布的报告类型" />
              <span v-if="typeDetail" class="ec-muted">{{ typeDetail.description }}</span>
            </n-space>
            <n-space size="small" align="center" v-if="typeDetail && Object.keys(typeDetail.params_schema || {}).length">
              <span class="ec-dim">参数：</span>
              <n-input v-for="(label, key) in typeDetail.params_schema" :key="key"
                       v-model:value="params[key]" :placeholder="label" size="small" style="width:150px" />
            </n-space>
            <div>
              <div class="ec-dim" style="margin-bottom:4px">写作意图（可选，一段话描述这次要生成的内容）：</div>
              <n-input v-model:value="intent" type="textarea" :rows="2"
                       placeholder="例：生成一份聚焦找矿勘查突破与政策动向的国内高纯石英报告，重点覆盖新矿种公告后的勘查进展。&#10;填写后系统按意图自动规划数据采集（语料检索 + 联网搜索），标题与选材向意图聚焦。" />
            </div>
            <n-space size="small" align="center">
              <span class="ec-dim">资料文件夹：</span>
              <n-input v-model:value="folder" size="small" style="width:430px"
                       placeholder="可选：本地资料目录（PDF），生成时自动建为本次检索语料库" class="ec-mono" />
              <span class="ec-muted">意图/文件夹留空则按报告类型的预置绑定取数</span>
            </n-space>
            <n-space size="small" align="center">
              <span class="ec-dim">模型档位：</span>
              <n-select v-model:value="modelTier" size="small" style="width:320px"
                        :options="TIER_OPTIONS" />
              <span class="ec-muted">档位在 config/settings.yaml 的 model_tiers 配置；默认档未配置时与单模型行为一致</span>
            </n-space>
            <n-space size="small">
              <n-button size="small" :loading="previewing" :disabled="!selType" @click="dataPreview">数据预检</n-button>
              <n-button size="small" type="primary" :loading="starting"
                        :disabled="runStatus === 'running' || !selType" @click="start">
                {{ runStatus === 'running' ? '生成中…' : '生成报告' }}</n-button>
              <span class="ec-muted">预检只跑数据层（秒级），提前暴露缺参数 / 无数据</span>
            </n-space>
            <n-alert v-if="previewResult" size="small"
                     :type="previewResult.ok ? 'success' : 'error'">
              <template v-if="previewResult.ok">
                数据就绪：{{ previewResult.n_facts }} 条事实
                <span v-if="previewResult.crosscheck">· 交叉校验 {{ previewResult.crosscheck.toUpperCase() }}</span>
                <span v-if="(previewResult.warnings||[]).length" style="color:#f0a020"> · 警告：{{ previewResult.warnings.join('；') }}</span>
                <div v-if="previewResult.plan" style="margin-top:4px;font-size:12px">
                  采集计划（{{ previewResult.plan.mode === 'llm' ? 'AI 规划' : '规则兜底' }}）：
                  语料检索 {{ previewResult.plan.n_rag }} 条 · 联网搜索 {{ previewResult.plan.n_web }} 条 ·
                  沿用表格 {{ previewResult.plan.n_tables }} 个
                  <span v-if="previewResult.plan.corpus"> · 语料库 {{ previewResult.plan.corpus.n_fragments }} 片段（{{ previewResult.plan.corpus.rebuilt ? '本次新建' : '缓存复用' }}，{{ previewResult.plan.corpus.n_files }} 个文件）</span>
                  <div v-if="previewResult.plan.note" style="color:#f0a020">{{ previewResult.plan.note }}</div>
                  <div v-if="previewResult.plan.focus" class="ec-muted">焦点：{{ previewResult.plan.focus }}</div>
                </div>
              </template>
              <template v-else>{{ previewResult.error }}</template>
            </n-alert>
          </n-space>
        </n-card>

        <!-- 运行中 -->
        <n-card size="small" class="ec-card" v-if="runStatus === 'running'" title="运行进度">
          <n-space size="small" align="center" style="margin-bottom:8px">
            <n-steps size="small" :current="99">
              <n-step v-for="s in STAGES" :key="s.key" :title="s.label"
                      :status="stageState[s.key].active ? 'process'
                              : stageState[s.key].done ? 'finish' : 'wait'" />
            </n-steps>
            <n-button size="tiny" type="error" @click="cancel">取消</n-button>
          </n-space>
          <div class="ec-log" style="max-height:220px">
            <div v-for="(ev, i) in events.filter(e => e.message)" :key="i">
              {{ ev.message }} <span class="ec-muted" v-if="ev.data && ev.data.llm_calls != null">
              （LLM {{ ev.data.llm_calls }} 次 / {{ ev.data.llm_seconds }}s）</span></div>
          </div>
          <div class="ec-muted" style="margin-top:6px">
            累计 LLM 调用 {{ totalStats.llm_calls }} 次 / {{ totalStats.llm_seconds }} 秒<span v-if="tierText">（{{ tierText }}）</span></div>
        </n-card>

        <!-- 失败 / 取消 -->
        <n-alert v-if="runStatus === 'error'" type="error" class="ec-card" title="生成失败">
          {{ runError }}
          <n-collapse style="margin-top:6px" v-if="errorDetail">
            <n-collapse-item title="复制详情（技术信息）" name="d">
              <n-input :value="errorDetail" type="textarea" readonly class="ec-mono"
                       :autosize="{minRows: 3, maxRows: 10}" style="font-size:12px" />
            </n-collapse-item>
          </n-collapse>
        </n-alert>
        <n-alert v-if="runStatus === 'cancelled'" type="warning" class="ec-card">
          已取消。部分产物保留在 {{ currentDir || '产物目录' }}。
        </n-alert>

        <!-- 完成 -->
        <template v-if="runStatus === 'done' && currentDir">
          <n-card size="small" class="ec-card">
            <n-space size="small" align="center">
              <n-tag size="small" class="ec-mono">{{ currentDir }}</n-tag>
              <n-button tag="a" attrType="a" :href="'/api/runs/report?dir=' + encodeURIComponent(currentDir) + '&format=docx'"
                        type="primary" size="small">下载 docx</n-button>
              <n-button size="small" tag="a" attrType="a"
                        :href="'/artifacts/' + encodeURIComponent(currentDir) + '/final.html'" target="_blank">新窗口打开</n-button>
              <n-button size="small" @click="rerun">再来一次</n-button>
              <span class="ec-muted">本次生成已固定所用报告类型版本（见运行详情-元信息）</span>
            </n-space>
          </n-card>
          <n-card size="small">
            <n-tabs v-model:value="detailTab" type="segment">
              <n-tab-pane name="preview" tab="报告预览">
                <iframe :key="previewKey" :src="previewUrl"
                        style="width:100%;height:640px;border:1px solid var(--ec-line);background:#fff"></iframe>
              </n-tab-pane>
              <n-tab-pane name="detail" tab="运行详情">
                <n-space size="small" style="margin-bottom:8px">
                  <n-button v-for="[f, label] in ARTIFACTS" :key="f" size="tiny"
                            :type="artifactTab === f ? 'primary' : 'default'" @click="openArtifact(f)">{{ label }}</n-button>
                </n-space>
                <div v-if="artifactErr"><n-empty :description="artifactErr" size="small" /></div>
                <div v-else-if="artifactTab === 'reconcile_report.json' && artifactJson">
                  <n-tag size="small" :type="artifactJson.status === 'pass' ? 'success'
                        : artifactJson.status === 'warn' ? 'warning' : 'error'">
                    对账 {{ (artifactJson.status || '').toUpperCase() }}</n-tag>
                  <div v-for="c in artifactJson.checks || []" :key="c.section" style="margin-top:8px">
                    <n-tag size="small" :type="(c.unknown_numbers||[]).length ? 'error' : 'default'">{{ c.section }}</n-tag>
                    <span v-if="(c.unknown_numbers||[]).length" style="color:#d03050">
                      未匹配数字 {{ JSON.stringify(c.unknown_numbers) }}</span>
                    <span v-else class="ec-muted">数字全部对上账</span>
                  </div>
                </div>
                <div v-else-if="artifactTab === 'validate_report.json' && artifactJson">
                  <div v-for="(it, i) in artifactJson.items || []" :key="i" style="margin-top:6px">
                    <n-tag size="small" :type="it.status === 'fail' ? 'error' : it.status === 'warn' ? 'warning' : 'success'">{{ it.status }}</n-tag>
                    <b class="ec-mono" style="font-size:12px">{{ it.rule }}</b>
                    <div style="font-size:12px">{{ it.detail }}</div>
                  </div>
                </div>
                <div v-else-if="artifactTab === 'judge_report.json' && artifactJson">
                  <n-tag :type="artifactJson.verdict === 'pass' ? 'success' : 'error'">
                    judge {{ artifactJson.total }} 分 {{ (artifactJson.verdict||'').toUpperCase() }}
                    （门槛 {{ artifactJson.threshold }}）</n-tag>
                  <div ref="radarDiv" style="width:100%;height:220px"></div>
                  <div v-for="(s, k) in artifactJson.scores" :key="k" style="font-size:12px;margin-top:4px">
                    <b>{{ k }}：{{ s.score }}</b> —— {{ s.comment }}</div>
                </div>
                <pre v-else-if="artifactJson" class="ec-mono"
                     style="font-size:12px;max-height:420px;overflow:auto">{{ JSON.stringify(artifactJson, null, 2) }}</pre>
                <n-empty v-else description="点击上方标签查看该阶段产物" size="small" />
              </n-tab-pane>
            </n-tabs>
          </n-card>
        </template>

        <n-card size="small" title="报告历史">
          <template #header-extra>
            <n-select v-model:value="filterType" size="small" style="width:200px" clearable
                      :options="types.map(t => ({label: t.name, value: t.id}))"
                      placeholder="按报告类型筛选" @update:value="loadHistory" />
          </template>
          <n-data-table :columns="hisCols" :data="history" size="small" :max-height="320" />
        </n-card>

        <n-drawer v-model:show="drawer.show" :width="720" placement="right">
          <n-drawer-content :title="'报告详情 · ' + (drawer.dir || '')" closable>
            <n-space size="small" style="margin-bottom:8px">
              <n-button size="tiny" tag="a" attrType="a"
                        :href="'/api/runs/report?dir=' + encodeURIComponent(drawer.dir || '') + '&format=docx'"
                        type="primary">下载 docx</n-button>
              <n-button size="tiny" tag="a" attrType="a"
                        :href="'/artifacts/' + encodeURIComponent(drawer.dir || '') + '/final.html'" target="_blank">新窗口打开</n-button>
            </n-space>
            <n-tabs v-model:value="drawer.tab" type="segment" size="small">
              <n-tab-pane name="preview" tab="报告预览">
                <iframe v-if="drawerPreviewUrl" :src="drawerPreviewUrl"
                        style="width:100%;height:560px;border:1px solid var(--ec-line);background:#fff"></iframe>
              </n-tab-pane>
              <n-tab-pane name="detail" tab="运行详情">
                <n-space size="small" style="margin-bottom:8px">
                  <n-button v-for="[f, label] in ARTIFACTS" :key="f" size="tiny"
                            :type="drawer.artifact === f ? 'primary' : 'default'"
                            @click="openArtifact2(f)">{{ label }}</n-button>
                </n-space>
                <div v-if="drawer.err"><n-empty :description="drawer.err" size="small" /></div>
                <div v-else-if="drawer.artifact === 'judge_report.json' && drawer.artifactJson">
                  <n-tag :type="drawer.artifactJson.verdict === 'pass' ? 'success' : 'error'">
                    judge {{ drawer.artifactJson.total }} 分 {{ (drawer.artifactJson.verdict||'').toUpperCase() }}</n-tag>
                  <div ref="radarDiv2" style="width:100%;height:220px"></div>
                </div>
                <pre v-else-if="drawer.artifactJson" class="ec-mono"
                     style="font-size:12px;max-height:440px;overflow:auto">{{ JSON.stringify(drawer.artifactJson, null, 2) }}</pre>
                <n-empty v-else description="点击上方标签查看" size="small" />
              </n-tab-pane>
            </n-tabs>
          </n-drawer-content>
        </n-drawer>
      `,
    },
  };
})();
