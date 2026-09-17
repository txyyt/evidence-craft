/* 系统设置：大模型 / 流水线参数 / 全局数据连接 / 可用适配器。 */
(function () {
  const { ref, reactive, onMounted } = Vue;

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
        // V4-12（P14）：适配器列表修复（此前 setup() 漏 return adapters，接口
        // 返回 14 项页面却永远空白）；各设置卡加载互相隔离——单卡失败不影响其他卡
        const adaptersLoading = ref(false);
        const adaptersError = ref(null);
        const modelError = ref(null);
        const pipeError = ref(null);
        const connError = ref(null);
        const aboutError = ref(null);

        async function loadModel() {
          modelError.value = null;
          try {
            const r = await EC.api.get('/api/settings/model');
            form.base_url = r.base_url; form.model = r.model;
            form.reasoning_effort = r.reasoning_effort || '';
            masked.value = r.api_key_masked;
          } catch (e) { modelError.value = e.message; }
        }
        async function loadPipeline() {
          pipeError.value = null;
          try {
            const p = await EC.api.get('/api/settings/pipeline');
            pipe.judge_threshold = p.judge_threshold;
            pipe.revise_rounds = p.revise_rounds;
          } catch (e) { pipeError.value = e.message; }
        }
        async function loadConns() {
          connError.value = null;
          try {
            const c = await EC.api.get('/api/sources/connections');
            conns.databases = c.databases || {};
            conns.rag = c.rag || {};
            dbText.value = JSON.stringify(conns.databases, null, 2);
            ragEndpoint.value = conns.rag.endpoint || 'mock';
            dbRef.value = Object.keys(conns.databases)[0] || '';
          } catch (e) { connError.value = e.message; }
        }
        async function loadAdapters() {
          adaptersLoading.value = true; adaptersError.value = null;
          try {
            adapters.value = await EC.api.get('/api/sources/adapters');
          } catch (e) { adaptersError.value = e.message; }
          finally { adaptersLoading.value = false; }
        }
        const reloadAdapters = loadAdapters;   // V4-12：失败卡「重试」入口
        async function loadAbout() {
          aboutError.value = null;
          try { about.value = await EC.api.get('/api/settings/about'); }
          catch (e) { aboutError.value = e.message; }
        }

        async function load() {
          // V4-12：五张卡并行加载、失败互不拖累（Promise.allSettled 语义）
          await Promise.allSettled([loadModel(), loadPipeline(), loadConns(),
                                    loadAdapters(), loadAbout()]);
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
        // 自动化观测缝（仅挂内存引用，无 UI 影响）：可驱动各卡独立加载
        EC._settingsView = { load, loadAdapters, adapters, adaptersError,
          adaptersLoading };
        // V4-12：adapters / 各卡 error / reloadAdapters 必须从 setup() 返回
        // （P14 实锤：此前漏 return adapters，响应式数据到了但模板拿不到）
        return {
          form, masked, saving, testing, testResult, about, effortOptions, saveModel, testModel,
          pipe, savingPipe, savePipeline,
          dbText, ragEndpoint, savingConn, dbTest, ragTest, testingDb, testingRag,
          dbRef, conns, saveConn, testDb, testRag,
          adapters, adaptersLoading, adaptersError, reloadAdapters,
          modelError, pipeError, connError, aboutError,
          loadModel, loadPipeline, loadConns, loadAbout,
        };
      },
      template: `
        <n-card title="大模型" size="small" class="ec-card">
          <n-alert v-if="modelError" type="error" size="small" style="margin-bottom:8px"
                   data-testid="model-error">
            模型配置加载失败：{{ modelError }}
            <n-button size="tiny" style="margin-left:8px" @click="loadModel">重试</n-button>
          </n-alert>
          <n-form label-placement="left" label-width="150" size="small">
            <n-form-item label="接口地址">
              <n-input v-model:value="form.base_url" placeholder="https://open.bigmodel.cn/api/paas/v4/" />
            </n-form-item>
            <n-form-item label="模型名称">
              <n-input v-model:value="form.model" placeholder="glm-5.3-flash / deepseek-..." />
            </n-form-item>
            <n-form-item label="API 密钥">
              <n-input v-model:value="form.api_key" type="password" show-password-on="click"
                :placeholder="masked ? ('已配置 ' + masked + '，留空则不修改') : '未配置'" />
            </n-form-item>
            <n-form-item label="思考档位">
              <n-select v-model:value="form.reasoning_effort" :options="effortOptions" />
            </n-form-item>
          </n-form>
          <div class="ec-muted" style="font-size:12px;margin-top:2px">
            配置键：base_url ｜ model ｜ api_key ｜ reasoning_effort（排障时对照日志使用）</div>
          <n-space size="small">
            <n-button type="primary" size="small" :loading="saving" @click="saveModel">保存（热生效）</n-button>
            <n-button size="small" :loading="testing" @click="testModel">测试连通</n-button>
            <span v-if="testResult" :style="{color: testResult.ok ? '#18a058' : '#d03050', fontSize: '13px'}">
              {{ testResult.ok ? '连通正常，' + testResult.latency_s + 's，回复：' + testResult.reply
                : '失败（' + testResult.latency_s + 's）：' + testResult.error }}</span>
          </n-space>
        </n-card>

        <n-card title="流水线参数" size="small" class="ec-card">
          <n-alert v-if="pipeError" type="error" size="small" style="margin-bottom:8px">
            流水线参数加载失败：{{ pipeError }}
            <n-button size="tiny" style="margin-left:8px" @click="loadPipeline">重试</n-button>
          </n-alert>
          <n-form label-placement="left" label-width="150" size="small">
            <n-form-item label="报告评审通过门槛">
              <n-input-number v-model:value="pipe.judge_threshold" :min="20" :max="50" style="width:140px" />
              <span class="ec-muted" style="margin-left:8px" title="配置键 judge_threshold">五维总分达到门槛且无单维过低才判已达标</span>
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
              <n-alert v-if="connError" type="error" size="small" style="margin-bottom:8px">
                连接配置加载失败：{{ connError }}
                <n-button size="tiny" style="margin-left:8px" @click="loadConns">重试</n-button>
              </n-alert>
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
          <!-- V4-12：加载中 / 失败 / 真·空列表 三态分离，不再共用空态 -->
          <div v-if="adaptersLoading" class="ec-muted" style="font-size:13px">适配器加载中…</div>
          <n-alert v-else-if="adaptersError" type="error" size="small" data-testid="adapter-error">
            适配器加载失败：{{ adaptersError }}
            <n-button size="tiny" style="margin-left:8px" data-testid="adapter-retry"
                      @click="reloadAdapters">重试</n-button>
          </n-alert>
          <n-empty v-else-if="!adapters.length" description="当前没有可用适配器" size="small" />
          <table v-else data-testid="adapter-list"
                 style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="text-align:left;color:var(--ec-muted,#666);font-size:12px">
                <th style="padding:4px 8px;border-bottom:1px solid var(--ec-line,#e5e7eb)">适配器</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--ec-line,#e5e7eb)">类型</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--ec-line,#e5e7eb)">事实默认可靠性</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in adapters" :key="a.key">
                <td style="padding:4px 8px;border-bottom:1px dashed var(--ec-line,#eee)"
                    class="ec-mono">{{ a.key }}</td>
                <td style="padding:4px 8px;border-bottom:1px dashed var(--ec-line,#eee)">{{ a.kind }}</td>
                <td style="padding:4px 8px;border-bottom:1px dashed var(--ec-line,#eee)">{{ a.reliability }}</td>
              </tr>
            </tbody>
          </table>
        </n-card>

        <n-card title="关于" size="small" v-if="about || aboutError">
          <n-alert v-if="aboutError" type="error" size="small">
            关于信息加载失败：{{ aboutError }}
            <n-button size="tiny" style="margin-left:8px" @click="loadAbout">重试</n-button>
          </n-alert>
          <div v-else-if="about">
            <div>{{ about.name }}</div>
            <div class="ec-muted" style="margin-top:4px">
              流水线阶段：{{ (about.pipeline_stages || []).join(' → ') }}</div>
          </div>
        </n-card>
      `,
    },
  };
})();
