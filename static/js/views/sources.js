/* 数据源：adapter 注册表总览 / 数据库与 RAG 连接配置（写 settings.yaml）/ 测试连接。 */
(function () {
  const { ref, reactive, onMounted } = Vue;
  const { h } = Vue;

  EC.views['/sources'] = {
    title: '数据源',
    component: {
      setup() {
        const adapters = ref([]);
        const conns = reactive({ databases: {}, rag: {} });
        const dbText = ref('');
        const ragEndpoint = ref('');
        const saving = ref(false);
        const dbTest = ref(null);
        const ragTest = ref(null);
        const testingDb = ref(false);
        const testingRag = ref(false);
        const dbRef = ref('');

        async function load() {
          adapters.value = await EC.api.get('/api/sources/adapters');
          const c = await EC.api.get('/api/sources/connections');
          conns.databases = c.databases || {};
          conns.rag = c.rag || {};
          dbText.value = JSON.stringify(conns.databases, null, 2);
          ragEndpoint.value = conns.rag.endpoint || 'mock';
          const first = Object.keys(conns.databases)[0];
          if (first) dbRef.value = first;
        }

        async function save() {
          saving.value = true;
          try {
            let dbs;
            try { dbs = JSON.parse(dbText.value || '{}'); }
            catch (e) { throw new Error('databases 不是合法 JSON：' + e.message); }
            const r = await EC.api.put('/api/sources/connections',
              { databases: dbs, rag: { endpoint: ragEndpoint.value } });
            conns.databases = r.databases;
            conns.rag = r.rag;
            if (!Object.keys(conns.databases).includes(dbRef.value))
              dbRef.value = Object.keys(conns.databases)[0] || '';
            EC.toast('连接配置已保存', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        async function testDb() {
          testingDb.value = true;
          dbTest.value = null;
          try {
            dbTest.value = await EC.api.post('/api/sources/test/database',
              { db_ref: dbRef.value });
          } catch (e) { dbTest.value = { ok: false, error: e.message }; }
          finally { testingDb.value = false; }
        }

        async function testRag() {
          testingRag.value = true;
          ragTest.value = null;
          try { ragTest.value = await EC.api.post('/api/sources/test/rag'); }
          catch (e) { ragTest.value = { ok: false, error: e.message }; }
          finally { testingRag.value = false; }
        }

        const adapterCols = [
          { title: 'adapter key', key: 'key', render: (r) => h('span', { class: 'ec-mono' }, r.key) },
          { title: '类型', key: 'kind', width: 110,
            render: (r) => h('n-tag', { size: 'small' }, { default: () => r.kind }) },
          { title: '事实默认可靠性', key: 'reliability', width: 150 },
        ];

        onMounted(load);
        return {
          adapters, dbText, ragEndpoint, saving, dbTest, ragTest,
          testingDb, testingRag, dbRef, conns, adapterCols, save, testDb, testRag,
        };
      },
      template: `
        <h3 class="ec-page-title">数据源</h3>

        <n-card title="adapter 注册表（datalayer/adapters/ 自动注册）" size="small" class="ec-card">
          <n-data-table :columns="adapterCols" :data="adapters" size="small" :max-height="320" />
        </n-card>

        <n-grid :cols="2" :x-gap="12">
          <n-gi>
            <n-card title="数据库连接（写 settings.yaml）" size="small">
              <n-space vertical size="small">
                <n-select v-model:value="dbRef" size="small" style="width:200px"
                          :options="Object.keys(conns.databases).map(k => ({label: k, value: k}))"
                          placeholder="db_ref" />
                <n-input v-model:value="dbText" type="textarea" class="ec-mono"
                         :autosize="{minRows: 4, maxRows: 10}" style="font-size:12px" />
                <n-space size="small">
                  <n-button size="small" type="primary" :loading="saving" @click="save">保存</n-button>
                  <n-button size="small" :disabled="!dbRef" :loading="testingDb" @click="testDb">测试连接</n-button>
                </n-space>
                <div v-if="dbTest" style="font-size:12px">
                  <n-tag size="tiny" :type="dbTest.ok ? 'success' : 'error'">
                    {{ dbTest.ok ? 'OK' : '失败' }}</n-tag>
                  <span v-if="dbTest.ok" class="ec-muted">
                    {{ dbTest.n_tables }} 张表：{{ (dbTest.tables||[]).join(', ') }}</span>
                  <span v-else style="color:#d03050">{{ dbTest.error }}</span>
                </div>
              </n-space>
            </n-card>
          </n-gi>
          <n-gi>
            <n-card title="RAG 服务（对方现成服务，只做对接）" size="small">
              <n-space vertical size="small">
                <n-input v-model:value="ragEndpoint" placeholder="http://... 或 mock"
                         size="small" class="ec-mono" />
                <n-space size="small">
                  <n-button size="small" type="primary" :loading="saving" @click="save">保存</n-button>
                  <n-button size="small" :loading="testingRag" @click="testRag">测试连接</n-button>
                </n-space>
                <div v-if="ragTest" style="font-size:12px">
                  <n-tag size="tiny" :type="ragTest.ok ? 'success' : 'error'">
                    {{ ragTest.ok ? 'OK' : '失败' }}</n-tag>
                  <span class="ec-muted">{{ ragTest.note || ragTest.error || '' }}
                    {{ ragTest.status ? ('（HTTP ' + ragTest.status + '）') : '' }}</span>
                </div>
                <div class="ec-muted" style="font-size:12px;margin-top:6px">
                  片段抽取规则：数字必须在片段原文中找到才收（抽取即对账），reliability=retrieved。
                </div>
              </n-space>
            </n-card>
          </n-gi>
        </n-grid>

        <n-card title="受控采集（playwright）" size="small" style="margin-top:12px">
          <n-space align="center">
            <n-checkbox disabled>playwright 受控采集（登录墙站点）</n-checkbox>
            <n-tag size="small" type="default">默认关闭 · M9 按试点部门需要启用</n-tag>
          </n-space>
        </n-card>
      `,
    },
  };
})();
