/* 系统设置：大模型 / 流水线参数 / 全局数据连接 / 可用适配器。 */
(function () {
  const { ref, reactive, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  EC.views['/settings'] = {
    title: '系统设置',
    component: {
      setup() {
        const form = reactive({ base_url: '', model: '', api_key: '', reasoning_effort: '' });
        const masked = ref('');
        const saving = ref(false);
        const testing = ref(false);
        const testResult = ref(null);
        const about = ref(null);

        const pipe = reactive({ judge_threshold: 36, revise_rounds: 2 });
        const savingPipe = ref(false);

        const dbText = ref('');
        const ragEndpoint = ref('');
        const savingConn = ref(false);
        const dbTest = ref(null);
        const ragTest = ref(null);
        const testingDb = ref(false);
        const testingRag = ref(false);
        const dbRef = ref('');
        const conns = reactive({ databases: {}, rag: {} });

        const adapters = ref([]);
        const adapterCols = [
          { title: '适配器 key', key: 'key', render: (r) => h('span', { class: 'ec-mono' }, r.key) },
          { title: '类型', key: 'kind', width: 110,
            render: (r) => h(NA.NTag, { size: 'small' }, { default: () => r.kind }) },
          { title: '事实默认可靠性', key: 'reliability', width: 150 },
        ];

        async function load() {
          const r = await EC.api.get('/api/settings/model');
          form.base_url = r.base_url; form.model = r.model;
          form.reasoning_effort = r.reasoning_effort || '';
          masked.value = r.api_key_masked;
          about.value = await EC.api.get('/api/settings/about');

          const p = await EC.api.get('/api/settings/pipeline');
          pipe.judge_threshold = p.judge_threshold;
          pipe.revise_rounds = p.revise_rounds;

          const c = await EC.api.get('/api/sources/connections');
          conns.databases = c.databases || {};
          conns.rag = c.rag || {};
          dbText.value = JSON.stringify(conns.databases, null, 2);
          ragEndpoint.value = conns.rag.endpoint || 'mock';
          dbRef.value = Object.keys(conns.databases)[0] || '';

          adapters.value = await EC.api.get('/api/sources/adapters');
        }

        async function saveModel() {
          saving.value = true;
          try {
            const r = await EC.api.put('/api/settings/model', { ...form });
            masked.value = r.api_key_masked;
            form.api_key = '';
            EC.toast('模型配置已保存并热生效', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        async function testModel() {
          testing.value = true; testResult.value = null;
          try { testResult.value = await EC.api.post('/api/settings/model/test', { ...form }); }
          catch (e) { testResult.value = { ok: false, error: e.message }; }
          finally { testing.value = false; }
        }

        async function savePipeline() {
          savingPipe.value = true;
          try {
            await EC.api.put('/api/settings/pipeline', { ...pipe });
            EC.toast('流水线参数已保存', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { savingPipe.value = false; }
        }

        async function saveConn() {
          savingConn.value = true;
          try {
            let dbs;
            try { dbs = JSON.parse(dbText.value || '{}'); }
            catch (e) { throw new Error('databases 不是合法 JSON：' + e.message); }
            const r = await EC.api.put('/api/sources/connections',
              { databases: dbs, rag: { endpoint: ragEndpoint.value } });
            conns.databases = r.databases; conns.rag = r.rag;
            dbText.value = JSON.stringify(conns.databases, null, 2);
            dbRef.value = Object.keys(conns.databases)[0] || '';
            EC.toast('连接配置已保存', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { savingConn.value = false; }
        }

        async function testDb() {
          testingDb.value = true; dbTest.value = null;
          try { dbTest.value = await EC.api.post('/api/sources/test/database', { db_ref: dbRef.value }); }
          catch (e) { dbTest.value = { ok: false, error: e.message }; }
          finally { testingDb.value = false; }
        }

        async function testRag() {
          testingRag.value = true; ragTest.value = null;
          try { ragTest.value = await EC.api.post('/api/sources/test/rag'); }
          catch (e) { ragTest.value = { ok: false, error: e.message }; }
          finally { testingRag.value = false; }
        }

        const effortOptions = [
          { label: '默认（不传参，服务端档位）', value: '' },
          { label: 'low（思维链最省，推荐 GLM）', value: 'low' },
          { label: 'high（更充分思考，更慢）', value: 'high' },
          { label: 'max（最大思考量）', value: 'max' },
        ];

        onMounted(load);
        return {
          form, masked, saving, testing, testResult, about, effortOptions, saveModel, testModel,
          pipe, savingPipe, savePipeline,
          dbText, ragEndpoint, savingConn, dbTest, ragTest, testingDb, testingRag,
          dbRef, conns, adapterCols, saveConn, testDb, testRag,
        };
      },
      template: `
        <n-card title="大模型" size="small" class="ec-card">
          <n-form label-placement="left" label-width="150" size="small">
            <n-form-item label="接口地址 base_url">
              <n-input v-model:value="form.base_url" placeholder="https://open.bigmodel.cn/api/paas/v4/" />
            </n-form-item>
            <n-form-item label="模型名 model">
              <n-input v-model:value="form.model" placeholder="glm-5.3-flash / deepseek-..." />
            </n-form-item>
            <n-form-item label="API Key">
              <n-input v-model:value="form.api_key" type="password" show-password-on="click"
                :placeholder="masked ? ('已配置 ' + masked + '，留空则不修改') : '未配置'" />
            </n-form-item>
            <n-form-item label="思考档位 reasoning_effort">
              <n-select v-model:value="form.reasoning_effort" :options="effortOptions" />
            </n-form-item>
          </n-form>
          <n-space size="small">
            <n-button type="primary" size="small" :loading="saving" @click="saveModel">保存（热生效）</n-button>
            <n-button size="small" :loading="testing" @click="testModel">测试连通</n-button>
            <span v-if="testResult" :style="{color: testResult.ok ? '#18a058' : '#d03050', fontSize: '13px'}">
              {{ testResult.ok ? '连通正常，' + testResult.latency_s + 's，回复：' + testResult.reply
                : '失败（' + testResult.latency_s + 's）：' + testResult.error }}</span>
          </n-space>
        </n-card>

        <n-card title="流水线参数" size="small" class="ec-card">
          <n-form label-placement="left" label-width="150" size="small">
            <n-form-item label="judge 通过门槛">
              <n-input-number v-model:value="pipe.judge_threshold" :min="20" :max="50" style="width:140px" />
              <span class="ec-muted" style="margin-left:8px">五维总分达到门槛且无单维过低才判 PASS</span>
            </n-form-item>
            <n-form-item label="不合格修订轮数上限">
              <n-input-number v-model:value="pipe.revise_rounds" :min="0" :max="5" style="width:140px" />
              <span class="ec-muted" style="margin-left:8px">评审不过时退回重写的最大轮数</span>
            </n-form-item>
          </n-form>
          <n-button type="primary" size="small" :loading="savingPipe" @click="savePipeline">保存</n-button>
        </n-card>

        <n-grid :cols="2" :x-gap="14">
          <n-gi>
            <n-card title="全局连接 · 数据库" size="small" class="ec-card">
              <n-space vertical size="small">
                <n-select v-model:value="dbRef" size="small" style="width:200px"
                          :options="Object.keys(conns.databases).map(k => ({label: k, value: k}))" placeholder="db_ref" />
                <n-input v-model:value="dbText" type="textarea" class="ec-mono"
                         :autosize="{minRows: 3, maxRows: 8}" style="font-size:12px" />
                <n-space size="small">
                  <n-button size="small" type="primary" :loading="savingConn" @click="saveConn">保存</n-button>
                  <n-button size="small" :disabled="!dbRef" :loading="testingDb" @click="testDb">测试连接</n-button>
                </n-space>
                <div v-if="dbTest" style="font-size:12px">
                  <n-tag size="tiny" :type="dbTest.ok ? 'success' : 'error'">{{ dbTest.ok ? 'OK' : '失败' }}</n-tag>
                  <span v-if="dbTest.ok" class="ec-muted">{{ dbTest.n_tables }} 张表：{{ (dbTest.tables||[]).join(', ') }}</span>
                  <span v-else style="color:#d03050">{{ dbTest.error }}</span>
                </div>
                <div class="ec-muted" style="font-size:12px">报告类型的数据绑定通过 db_ref 引用这里的连接。</div>
              </n-space>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card title="全局连接 · RAG 服务" size="small" class="ec-card">
              <n-space vertical size="small">
                <n-input v-model:value="ragEndpoint" placeholder="http://... 或 mock" size="small" class="ec-mono" />
                <n-space size="small">
                  <n-button size="small" type="primary" :loading="savingConn" @click="saveConn">保存</n-button>
                  <n-button size="small" :loading="testingRag" @click="testRag">测试连接</n-button>
                </n-space>
                <div v-if="ragTest" style="font-size:12px">
                  <n-tag size="tiny" :type="ragTest.ok ? 'success' : 'error'">{{ ragTest.ok ? 'OK' : '失败' }}</n-tag>
                  <span class="ec-muted">{{ ragTest.note || ragTest.error || '' }}
                    {{ ragTest.status ? ('（HTTP ' + ragTest.status + '）') : '' }}</span>
                </div>
                <div class="ec-muted" style="font-size:12px">片段抽取规则：数字必须在片段原文中找到才收（抽取即对账）。</div>
              </n-space>
            </n-card>
          </n-gi>
        </n-grid>

        <n-card title="可用适配器（数据绑定里按这些选）" size="small" class="ec-card">
          <n-data-table :columns="adapterCols" :data="adapters" size="small" :max-height="260" />
        </n-card>

        <n-card title="关于" size="small" v-if="about">
          <div>{{ about.name }} · {{ about.milestone }}</div>
          <div class="ec-muted" style="margin-top:4px">
            流水线阶段：{{ about.pipeline_stages.join(' → ') }}</div>
        </n-card>
      `,
    },
  };
})();
