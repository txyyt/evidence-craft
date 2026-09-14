/* 反馈工作台：报告结构化视图 + 一段总意见 → 路由回显确认 → 三类执行（SSE）
   → 轮次 diff / 回滚 / 深度评审。入口：#/run?dir=<产物目录>。 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  EC.views['/run'] = {
    title: '反馈工作台',
    component: {
      setup() {
        const dialog = NA.useDialog();
        const dir = ref(new URLSearchParams(location.hash.split('?')[1] || '').get('dir'));
        const sec = ref(null);          // {meta, outline, sections, titles}
        const fbText = ref('');
        const ops = ref([]);            // 路由出的 ops（勾选执行）
        const ambiguities = ref([]);
        const parsing = ref(false);
        const autoRun = ref(false);     // 直接执行（默认关=先确认）
        const job = reactive({ id: null, status: 'idle', events: [] });
        const roundsList = ref([]);
        const diff = reactive({ show: false, round: null, data: null });
        const judgeInfo = ref(null);

        async function loadRun() {
          if (!dir.value) return;
          const get = (f) => EC.api.get(`/api/runs/artifact?dir=${encodeURIComponent(dir.value)}&file=${f}`);
          const meta = await get('meta.json');
          const outline = await get('outline.json');
          const sections = await get('sections.json');
          let titles = {};
          if (meta.tree_id) {
            try {
              const t = await EC.api.get('/api/trees/' + meta.tree_id);
              titles = Object.fromEntries(t.spec_dict.sections.map((s) => [s.id, s]));
            } catch (e) { /* 树可能被删，标题退化 */ }
          }
          sec.value = { meta, outline, sections, titles };
          loadRounds();
        }
        async function loadRounds() {
          try { roundsList.value = await EC.api.get(`/api/runs/${dir.value}/feedback/rounds`); }
          catch (e) { roundsList.value = []; }
        }

        const sectionsView = computed(() => {
          if (!sec.value) return [];
          const s = sec.value;
          const out = [];
          if (s.outline.title) out.push({ anchor: 'title', title: '标题', body: s.outline.title });
          for (const t of s.sections.texts || []) {
            const ti = s.titles[t.section_id] || {};
            out.push({ anchor: t.section_id, title: ti.title || t.section_id, body: t.body });
          }
          for (const [sid, n] of Object.entries(s.sections.notes || {})) {
            const ti = s.titles[sid] || {};
            out.push({ anchor: sid, title: (ti.title || sid) + '（表说明）',
                       body: (n.body || '') });
          }
          if ((s.sections.risks || {}).body)
            out.push({ anchor: 'risks', title: '风险提示', body: s.sections.risks.body });
          return out;
        });

        async function parseFeedback() {
          if (!fbText.value.trim()) { EC.toast('先写意见', 'warning'); return; }
          parsing.value = true; ops.value = []; ambiguities.value = [];
          try {
            const r = await EC.api.post(`/api/runs/${dir.value}/feedback/parse`,
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
              else { loadRun(); EC.toast('完成', 'success'); }
            }
          };
          es.onerror = () => es.close();
        }

        async function applyFeedback() {
          const chosen = ops.value.filter((o) => o._on);
          if (!chosen.length) { EC.toast('没有勾选任何意见', 'warning'); return; }
          const r = await EC.api.post(`/api/runs/${dir.value}/feedback/apply`, {
            text: fbText.value, ops: chosen.map(({ _on, ...o }) => o) });
          watchJob(r.events_url);
        }

        async function showDiff(round) {
          diff.round = round;
          diff.data = await EC.api.get(
            `/api/runs/${dir.value}/feedback/diff?round=${round}`);
          diff.show = true;
        }
        function doRollback(round) {
          EC.api.post(`/api/runs/${dir.value}/feedback/rollback`, { round })
            .then((r) => watchJob(r.events_url))
            .catch((e) => EC.toast(e.message, 'error'));
        }
        function rollback(round) {
          // 页内对话框（naive-ui），不用系统原生 confirm
          dialog.warning({
            title: '回滚确认',
            content: `撤销第 ${round} 轮？将恢复该轮之前的稿面并重渲染（树的结构改动请用模板工作台的版本回滚）。`,
            positiveText: '确认回滚',
            negativeText: '取消',
            onPositiveClick: () => doRollback(round),
          });
        }
        async function judgeDeep() {
          const r = await EC.api.post(`/api/runs/${dir.value}/judge`, {});
          job.id = r.id; job.status = 'running'; job.events = [];
          const es = new EventSource(r.events_url);
          es.onmessage = (m) => {
            const ev = JSON.parse(m.data);
            job.events.push(ev);
            if (ev.type === 'end') {
              es.close(); job.status = ev.status;
              EC.api.get(`/api/runs/artifact?dir=${encodeURIComponent(dir.value)}&file=judge_report.json`)
                .then((j) => { judgeInfo.value = j; EC.toast(`judge ${j.total} 分`, 'success'); });
            }
          };
          es.onerror = () => es.close();
        }

        onMounted(loadRun);

        return { dir, sec, fbText, ops, ambiguities, parsing, autoRun, job,
                 roundsList, diff, judgeInfo, sectionsView,
                 parseFeedback, applyFeedback, showDiff, rollback, judgeDeep };
      },
      template: `
        <n-space vertical size="small">
          <n-card size="small" class="ec-card">
            <template #header>反馈工作台 <n-tag size="small" class="ec-mono">{{ dir }}</n-tag></template>
            <template #header-extra>
              <n-space size="small" align="center">
                <span class="ec-muted">解析后直接执行</span>
                <n-switch v-model:value="autoRun" size="small" />
                <n-button size="small" type="primary" :loading="job.status === 'running'" @click="judgeDeep">深度评审</n-button>
                <n-button size="small" tag="a" attrType="a"
                          :href="'/artifacts/' + encodeURIComponent(dir || '') + '/final.html'" target="_blank">打开报告</n-button>
              </n-space>
            </template>
            <n-alert v-if="job.status === 'running'" type="info" size="small" style="margin-bottom:8px">
              <div v-for="(ev, i) in job.events.filter(e => e.message)" :key="i">{{ ev.message }}</div>
            </n-alert>
            <n-grid :cols="24" :x-gap="12">
              <n-gi :span="14">
                <n-card size="small" title="报告（按节锚点）">
                  <div v-for="s in sectionsView" :key="s.anchor" style="margin-bottom:10px">
                    <n-tag size="small" class="ec-mono" style="margin-right:6px">{{ s.anchor }}</n-tag>
                    <b style="font-size:13px">{{ s.title }}</b>
                    <div class="ec-muted" style="font-size:13px;white-space:pre-wrap;margin-top:2px">{{ s.body }}</div>
                  </div>
                </n-card>
              </n-gi>
              <n-gi :span="10">
                <n-card size="small" title="意见反馈">
                  <n-space vertical size="small">
                    <n-input v-model:value="fbText" type="textarea" :rows="4"
                             placeholder="一段总意见，可混着说。例：市场那节太干，扩一倍并用上消费结构表的数据；市场数据里补一下2024年的产量；再加一节讲环保约束。" />
                    <n-space size="small" align="center">
                      <n-button size="small" type="primary" :loading="parsing" @click="parseFeedback">解析意见</n-button>
                      <n-button size="small" :disabled="!ops.length || job.status === 'running'" @click="applyFeedback">执行（{{ ops.filter(o => o._on).length }}）</n-button>
                    </n-space>
                    <n-alert v-if="ambiguities.length" type="warning" size="small">
                      <div v-for="(a, i) in ambiguities" :key="i">{{ a }}</div>
                    </n-alert>
                    <div v-for="(o, i) in ops" :key="i"
                         style="border:1px solid var(--ec-line,#e5e7eb);border-radius:6px;padding:6px">
                      <n-space size="small" align="center">
                        <n-checkbox v-model:checked="o._on" />
                        <n-tag size="small" :type="o.kind === 'style' ? 'default' : o.kind === 'data' ? 'warning' : 'info'">
                          {{ o.kind }}</n-tag>
                        <span class="ec-mono" style="font-size:12px">{{ o.target }}</span>
                        <span class="ec-muted" style="font-size:12px">{{ o.action }}</span>
                      </n-space>
                      <div style="font-size:13px;margin-top:2px">{{ o.instruction }}
                        <span v-if="(o.web_queries || []).length" class="ec-muted">（查询：{{ (o.web_queries || []).join('；') }}）</span>
                      </div>
                    </div>
                  </n-space>
                </n-card>
                <n-card size="small" title="反馈轮次" style="margin-top:10px">
                  <n-empty v-if="!roundsList.length" description="还没有反馈轮次" size="small" />
                  <n-list v-else show-divider>
                    <n-list-item v-for="r in roundsList" :key="r.round">
                      <n-space size="small" align="center" justify="space-between">
                        <span style="font-size:13px">第 {{ r.round }} 轮
                          <span class="ec-muted">{{ (r.text || '').slice(0, 40) }}</span>
                          <n-tag v-if="r.rolled_back" size="small" type="error">已回滚</n-tag></span>
                        <n-space size="small">
                          <n-button size="tiny" @click="showDiff(r.round)">看 diff</n-button>
                          <n-button size="tiny" type="error" quaternary @click="rollback(r.round)">回滚此轮</n-button>
                        </n-space>
                      </n-space>
                    </n-list-item>
                  </n-list>
                  <div v-if="judgeInfo" style="margin-top:8px">
                    <n-tag :type="judgeInfo.verdict === 'pass' ? 'success' : 'error'">
                      深度评审 judge {{ judgeInfo.total }} 分 {{ (judgeInfo.verdict || '').toUpperCase() }}</n-tag>
                  </div>
                </n-card>
              </n-gi>
            </n-grid>
          </n-card>

          <n-modal v-model:show="diff.show" preset="card" :title="'第 ' + diff.round + ' 轮 diff'" style="width:900px">
            <div v-for="(d, sid) in diff.data.sections" :key="sid" style="margin-bottom:12px">
              <b class="ec-mono" style="font-size:12px">{{ sid }}</b>
              <n-grid :cols="2" :x-gap="8">
                <n-gi><div class="ec-log" style="max-height:200px;white-space:pre-wrap">{{ d.old || '（新增）' }}</div></n-gi>
                <n-gi><div class="ec-log" style="max-height:200px;white-space:pre-wrap;background:#f6ffed">{{ d.new }}</div></n-gi>
              </n-grid>
            </div>
            <div v-if="diff.data.title && diff.data.title.old !== diff.data.title.new">
              <b class="ec-mono" style="font-size:12px">title</b>
              <div>{{ diff.data.title.old }} → <b>{{ diff.data.title.new }}</b></div>
            </div>
          </n-modal>
        </n-space>
      `,
    },
  };
})();
