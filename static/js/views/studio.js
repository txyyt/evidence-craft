/* 模板工作台：左样例结构 / 中 spec 草案卡片（置信度标记） / 右对话窗。
   底部：回放验证 + 试跑 + 定稿。对话修改与手动编辑同一份 draft.yaml。 */
(function () {
  const { reactive, ref, computed, onMounted, watch } = Vue;

  EC.views['/studio'] = {
    title: '模板工作台',
    component: {
      setup() {
        const job = ref(null);            // GET /api/studio/jobs/{id}
        const jobId = ref(localStorage.getItem('ec.studioJob') || '');
        const jobList = ref([]);
        const extracting = ref(false);
        const extractLog = ref([]);
        const fileList = ref([]);         // 待上传样例
        const reportType = ref('');

        const editSpec = ref(null);       // 中间栏可编辑副本
        const yamlMode = ref(false);
        const yamlText = ref('');
        const dirty = ref(false);
        const saving = ref(false);

        const chat = ref([]);
        const chatInput = ref('');
        const chatting = ref(false);
        const pending = ref(null);        // {reply, ops, diff, error}

        const leftSample = ref('');       // 左栏选中的样例名
        const sampleTree = ref(null);

        const replaySample = ref('');
        const replayRounds = ref(2);
        const busyLog = ref([]);          // 回放/试跑的实时日志
        const finalName = ref('');
        const showFinalize = ref(false);
        const finalizing = ref(false);

        const depts = ref([]);
        const dryDept = ref('');
        const dryParams = reactive({});
        const dryParamSchema = ref({});

        async function loadJobList() {
          try {
            const r = await fetch('/api/studio/jobs_list');
            jobList.value = r.ok ? await r.json() : [];
          } catch (e) { jobList.value = []; }
        }

        async function loadJob(id) {
          if (!id) return;
          jobId.value = id;
          const j = await EC.api.get('/api/studio/jobs/' + id);
          job.value = j;
          editSpec.value = j.spec;
          yamlText.value = j.spec ? JSON.stringify(j.spec, null, 2) : '';
          dirty.value = false;
          chat.value = j.chat || [];
          pending.value = null;
          if (j.samples && j.samples.length && !leftSample.value)
            leftSample.value = j.samples[0];
          if (j.samples && j.samples.length) replaySample.value = j.samples[0];
          localStorage.setItem('ec.studioJob', id);
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
              if (ev.status !== 'done') EC.toast('任务失败：' + (ev.error || '').slice(-300), 'error');
              loadJob(jobId.value);
            }
          };
          es.onerror = () => es.close();
        }

        async function uploadExtract() {
          if (!fileList.value.length) { EC.toast('请先选择样例文件', 'warning'); return; }
          const fd = new FormData();
          for (const f of fileList.value) fd.append('files', f.file, f.name);
          if (reportType.value) fd.append('report_type', reportType.value);
          extracting.value = true;
          extractLog.value = [];
          try {
            const r = await fetch('/api/studio/extract', { method: 'POST', body: fd });
            if (!r.ok) throw new Error(((await r.json()).detail) || r.statusText);
            const { job_id, events_url } = await r.json();
            jobId.value = job_id;
            extractLog.value = ['上传完成，任务 ' + job_id];
            // 复用监听：把提取进度写进 extractLog
            const es = new EventSource(events_url);
            es.onmessage = (m) => {
              const ev = JSON.parse(m.data);
              if (ev.message) extractLog.value.push(ev.message);
              if (ev.type === 'end') {
                es.close();
                extracting.value = false;
                if (ev.status !== 'done') EC.toast('提取失败：' + (ev.error || '').slice(-300), 'error');
                loadJob(jobId.value);
                loadJobList();
              }
            };
            es.onerror = () => { es.close(); };
          } catch (e) {
            extracting.value = false;
            EC.toast('上传失败：' + e.message, 'error');
          }
        }

        async function openJob(id) { await loadJob(id); }

        async function loadSample(name) {
          leftSample.value = name;
          try {
            sampleTree.value = await EC.api.get(
              `/api/studio/jobs/${jobId.value}/sample/${encodeURIComponent(name)}`);
          } catch (e) { sampleTree.value = null; }
        }

        function markDirty() { dirty.value = true; }
        watch(editSpec, markDirty, { deep: true });

        async function saveSpec() {
          saving.value = true;
          try {
            let r;
            if (yamlMode.value) {
              r = await EC.api.put('/api/studio/spec-yaml',
                { job_id: jobId.value, text: yamlText.value });
            } else {
              r = await EC.api.put('/api/studio/spec',
                { job_id: jobId.value, spec: editSpec.value });
            }
            editSpec.value = r.spec;
            yamlText.value = JSON.stringify(r.spec, null, 2);
            dirty.value = false;
            EC.toast('已保存（快照留痕）', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        async function sendChat() {
          const text = chatInput.value.trim();
          if (!text) return;
          chatting.value = true;
          try {
            const r = await EC.api.post('/api/studio/patch',
              { job_id: jobId.value, message: text });
            chat.value = r.chat;
            chatInput.value = '';
            pending.value = r.error ? { error: r.error } : r;
          } catch (e) { EC.toast('对话失败：' + e.message, 'error'); }
          finally { chatting.value = false; }
        }

        async function applyPatch() {
          try {
            const r = await EC.api.post('/api/studio/apply',
              { job_id: jobId.value, ops: pending.value.ops });
            editSpec.value = r.spec;
            yamlText.value = JSON.stringify(r.spec, null, 2);
            dirty.value = false;
            pending.value = null;
            EC.toast('patch 已应用', 'success');
          } catch (e) { EC.toast('应用失败：' + e.message, 'error'); }
        }

        async function startReplay() {
          if (!replaySample.value) { EC.toast('选一个样例用于回放', 'warning'); return; }
          const r = await EC.api.post('/api/studio/replay',
            { job_id: jobId.value, sample: replaySample.value, rounds: replayRounds.value });
          listen(r.events_url);
        }

        async function startDryrun() {
          const r = await EC.api.post('/api/studio/dryrun', {
            job_id: jobId.value, department: dryDept.value,
            params: { ...dryParams },
          });
          listen(r.events_url);
        }

        watch(dryDept, async (id) => {
          dryParamSchema.value = {};
          if (!id) return;
          try {
            const r = await EC.api.get('/api/departments/' + id);
            dryParamSchema.value = (r.profile && r.profile.params_schema) || {};
          } catch (e) {}
        });

        async function finalize() {
          finalizing.value = true;
          try {
            if (dirty.value) await saveSpec();
            const r = await EC.api.post('/api/studio/finalize',
              { job_id: jobId.value, name: finalName.value });
            EC.toast('已定稿入库：' + r.path, 'success');
            showFinalize.value = false;
            loadJob(jobId.value);
          } catch (e) { EC.toast('定稿失败：' + e.message, 'error'); }
          finally { finalizing.value = false; }
        }

        const confColor = { high: '#18a058', medium: '#f0a020', low: '#d03050' };
        function confOf(title) {
          const secs = (job.value && job.value.extractions &&
            job.value.extractions.sections) || [];
          return secs.find((s) => s.title === title) || null;
        }

        const pendingList = computed(() => {
          const secs = (job.value && job.value.extractions &&
            job.value.extractions.sections) || [];
          return secs.filter((s) => s.divergence);
        });

        onMounted(async () => {
          await loadJobList();
          const r = await fetch('/api/departments');
          if (r.ok) depts.value = (await r.json()).map((d) => ({ label: d.id, value: d.id }));
          if (jobId.value) await loadJob(jobId.value);
        });

        return {
          job, jobId, jobList, extracting, extractLog, fileList, reportType,
          editSpec, yamlMode, yamlText, dirty, saving,
          chat, chatInput, chatting, pending, sendChat, applyPatch,
          leftSample, sampleTree, loadSample,
          replaySample, replayRounds, busyLog, startReplay, startDryrun,
          depts, dryDept, dryParams, dryParamSchema,
          finalName, showFinalize, finalizing, finalize,
          uploadExtract, openJob, saveSpec, confOf, confColor, pendingList,
        };
      },
      template: `
        <h3 class="ec-page-title">模板工作台</h3>

        <n-card title="① 上传样例 → AI 提取" size="small" class="ec-card">
          <n-space vertical>
            <n-space align="center">
              <n-upload :max="5" :default-upload="false" v-model:file-list="fileList">
                <n-button>选择样例（docx / 文字版 PDF / md / txt，建议 2~3 篇）</n-button>
              </n-upload>
              <n-input v-model:value="reportType" placeholder="报告类型名（可留空自动推断）"
                       style="width:220px" size="small" />
              <n-button type="primary" :loading="extracting" @click="uploadExtract">
                {{ extracting ? '提取中…' : '开始提取' }}
              </n-button>
              <n-select v-if="jobList.length" :value="jobId"
                        :options="jobList.map(j => ({label: j, value: j}))"
                        @update:value="openJob" placeholder="打开历史任务"
                        style="width:220px" size="small" />
            </n-space>
            <div v-if="extractLog.length" class="ec-mono ec-muted"
                 style="max-height:110px;overflow:auto;font-size:12px">
              <div v-for="(l, i) in extractLog" :key="i">{{ l }}</div>
            </div>
          </n-space>
        </n-card>

        <template v-if="job && job.spec">
        <n-grid :cols="24" :x-gap="12">
          <!-- 左：样例结构 -->
          <n-gi :span="5">
            <n-card title="② 样例结构" size="small">
              <n-select :value="leftSample" :options="job.samples.map(s => ({label: s, value: s}))"
                        @update:value="loadSample" size="small" />
              <div v-if="sampleTree" style="margin-top:8px;max-height:520px;overflow:auto">
                <div v-for="(b, i) in sampleTree.blocks" :key="i"
                     :style="{marginLeft: (b.type === 'heading' ? (b.level - 1) * 12 : 12) + 'px'}">
                  <b v-if="b.type === 'heading'" style="font-size:13px">{{ b.text }}</b>
                  <div v-else-if="b.type === 'table'" class="ec-muted" style="font-size:12px">[表格 {{ (b.header||[]).length }} 列]</div>
                  <div v-else-if="b.type === 'figure'" class="ec-muted" style="font-size:12px">[图件]</div>
                  <div v-else class="ec-muted" style="font-size:12px">{{ (b.text || '').slice(0, 60) }}</div>
                </div>
              </div>
            </n-card>
          </n-gi>

          <!-- 中：spec 草案卡片 -->
          <n-gi :span="11">
            <n-card title="③ spec 草案（可直接编辑）" size="small">
              <template #header-extra>
                <n-space size="small">
                  <n-button size="tiny" @click="yamlMode = !yamlMode">
                    {{ yamlMode ? '卡片视图' : 'YAML/JSON 视图' }}</n-button>
                  <n-button size="tiny" type="primary" :disabled="!dirty"
                            :loading="saving" @click="saveSpec">保存</n-button>
                </n-space>
              </template>

              <n-alert v-if="pendingList.length" type="warning" size="small" style="margin-bottom:10px">
                ⚠ {{ pendingList.length }} 处待人工裁决：
                <div v-for="(s, i) in pendingList" :key="i" style="font-size:12px">
                  {{ s.title }} —— {{ s.divergence }}</div>
              </n-alert>

              <template v-if="yamlMode">
                <n-input v-model:value="yamlText" type="textarea" :autosize="{minRows: 18, maxRows: 26}"
                         class="ec-mono" style="font-size:12px" @update:value="dirty = true" />
              </template>
              <template v-else>
                <n-form size="small" label-placement="left" label-width="90">
                  <n-form-item label="报告类型"><n-input v-model:value="editSpec.report_type" /></n-form-item>
                  <n-form-item label="说明"><n-input v-model:value="editSpec.description" type="textarea" :rows="2" /></n-form-item>
                  <n-form-item label="标题风格"><n-input v-model:value="editSpec.title_style" type="textarea" :rows="2" /></n-form-item>
                </n-form>

                <n-card v-for="(sec, si) in editSpec.sections" :key="si" size="small"
                        style="margin-top:10px"
                        :bordered="true">
                  <template #header>
                    <n-space size="small" align="center">
                      <span>{{ sec.title }}</span>
                      <n-tag size="tiny" :bordered="false">{{ sec.kind }}</n-tag>
                      <span v-if="confOf(sec.title)" :style="{color: confColor[confOf(sec.title).confidence] || '#999', fontSize: '12px'}">
                        置信度 {{ confOf(sec.title).confidence }}</span>
                    </n-space>
                  </template>
                  <div v-if="confOf(sec.title) && confOf(sec.title).divergence"
                       style="color:#f0a020;font-size:12px;margin-bottom:6px">分歧：{{ confOf(sec.title).divergence }}</div>
                  <n-form size="small" label-placement="left" label-width="90">
                    <n-form-item label="章节标题"><n-input v-model:value="sec.title" /></n-form-item>
                    <n-form-item v-if="sec.view_style" label="段落形态">
                      <n-input v-model:value="sec.view_style" type="textarea" :rows="2" /></n-form-item>
                    <n-form-item v-if="sec.style" label="说明写法">
                      <n-input v-model:value="sec.style" type="textarea" :rows="2" /></n-form-item>
                    <n-form-item v-if="sec.strategy" label="风险策略">
                      <n-select v-model:value="sec.strategy" :options="[
                        {label: 'mirror（逐条镜像前文观点）', value: 'mirror'},
                        {label: 'enumerate（固定清单列举）', value: 'enumerate'}]" /></n-form-item>
                  </n-form>
                  <div v-if="sec.view_slots" style="margin-top:4px">
                    <div v-for="(slot, vi) in sec.view_slots" :key="vi"
                         style="border:1px solid #eee;border-radius:4px;padding:8px;margin-bottom:6px">
                      <n-space size="small" align="center">
                        <n-tag size="tiny" class="ec-mono">{{ slot.id }}</n-tag>
                        <span class="ec-muted" style="font-size:12px">{{ (slot.data_needs || []).join(' / ') || '未提取 data_needs' }}</span>
                      </n-space>
                      <n-input v-model:value="slot.brief" placeholder="槽位职责" size="small"
                               style="margin-top:4px" />
                      <n-input v-if="slot.fewshot" v-model:value="slot.fewshot" type="textarea"
                               :rows="3" size="small" style="margin-top:4px" class="ec-muted" />
                      <div v-else class="ec-muted" style="font-size:12px;margin-top:2px">⚠ 缺范文选段</div>
                    </div>
                  </div>
                  <div v-if="sec.table" class="ec-muted" style="font-size:12px">
                    表格：{{ sec.table }}（渲染器见 tables 定义）
                  </div>
                </n-card>
              </template>
            </n-card>
          </n-gi>

          <!-- 右：对话窗 -->
          <n-gi :span="8">
            <n-card title="④ 对话修改（diff 确认后生效）" size="small">
              <div style="max-height:300px;overflow:auto;margin-bottom:8px">
                <div v-for="(m, i) in chat" :key="i"
                     :style="{textAlign: m.role === 'user' ? 'right' : 'left', margin: '6px 0'}">
                  <n-tag size="tiny" :type="m.role === 'user' ? 'primary' : 'default'">
                    {{ m.role === 'user' ? '我' : 'AI' }}</n-tag>
                  <span style="font-size:13px">{{ m.text }}</span>
                </div>
              </div>
              <n-input v-model:value="chatInput" type="textarea" :rows="2"
                       placeholder="例：风险提示改成三段式；第二槽位范文换成更短的段落" />
              <n-space style="margin-top:6px">
                <n-button type="primary" size="small" :loading="chatting"
                          :disabled="!jobId" @click="sendChat">发送修改要求</n-button>
              </n-space>
              <n-card v-if="pending && pending.error" size="small" style="margin-top:8px">
                <n-alert type="error" size="small">patch 生成失败：{{ pending.error }}</n-alert>
              </n-card>
              <n-card v-if="pending && !pending.error" size="small" title="diff 预览（未应用）"
                      style="margin-top:8px">
                <div class="ec-mono" style="font-size:12px;max-height:180px;overflow:auto">
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

            <!-- 验证与定稿 -->
            <n-card title="⑤ 验证与定稿" size="small" style="margin-top:12px">
              <n-space vertical size="small">
                <n-space align="center" size="small">
                  <n-select :value="replaySample" size="small" style="width:180px"
                            :options="job.samples.map(s => ({label: s, value: s}))"
                            @update:value="replaySample = $event" />
                  <n-input-number v-model:value="replayRounds" size="small" :min="1" :max="3" style="width:90px" />
                  <n-button size="small" @click="startReplay">回放验证</n-button>
                </n-space>
                <n-space align="center" size="small">
                  <n-select v-model:value="dryDept" size="small" style="width:140px"
                            :options="depts" placeholder="试跑部门" />
                  <n-input v-for="(label, key) in dryParamSchema" :key="key"
                           v-model:value="dryParams[key]" :placeholder="label"
                           size="small" style="width:110px" />
                  <n-button size="small" :disabled="!dryDept" @click="startDryrun">真实数据试跑</n-button>
                </n-space>
                <div v-if="busyLog.length" class="ec-mono ec-muted"
                     style="font-size:12px;max-height:120px;overflow:auto">
                  <div v-for="(l, i) in busyLog" :key="i">{{ l }}</div>
                </div>
                <n-alert v-if="job.replay" size="small"
                         :type="job.replay.verdict === 'FAIL' ? 'error' : 'success'">
                  回放：{{ job.replay.verdict }}
                  <span class="ec-muted" style="font-size:12px">
                    标题「{{ job.replay.title }}」；字数
                    {{ (job.replay.length || []).map(l => l.slot_id + ':' + l.n_chars + '字').join('，') }}
                  </span>
                </n-alert>
                <n-alert v-if="job.dryrun" size="small" type="info">
                  试跑：真实事实 {{ job.dryrun.n_facts }} 条，标题「{{ job.dryrun.title }}」
                </n-alert>
                <n-space align="center">
                  <n-input v-model:value="finalName" placeholder="定稿模板名（snake_case）"
                           size="small" style="width:220px" />
                  <n-button size="small" type="success" :loading="finalizing"
                            @click="showFinalize = true">定稿入库</n-button>
                  <n-tag v-if="job.finalized_to" size="small" type="success">
                    已定稿：{{ job.finalized_to }}</n-tag>
                </n-space>
              </n-space>
            </n-card>
          </n-gi>
        </n-grid>
        </template>
        <n-empty v-else-if="job && !job.spec" description="样例已上传但草案未生成（提取失败或进行中）"
                 style="margin:40px 0" />

        <n-modal v-model:show="showFinalize" preset="dialog" title="确认定稿"
                 positive-text="写入 config/report_types" negative-text="再看看"
                 :loading="finalizing" @positive-click="finalize">
          将以「{{ finalName }}.yaml」写入模板库；若同名模板已存在，旧版自动进 versions 留痕。
        </n-modal>
      `,
    },
  };
})();
