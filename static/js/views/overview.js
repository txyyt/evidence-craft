/* 首页 Dashboard：部门卡片 / 模板与最近运行 / 快捷入口 / 系统健康。 */
(function () {
  const { ref, onMounted } = Vue;
  const { h } = Vue;

  EC.views['/overview'] = {
    title: '首页',
    component: {
      setup() {
        const data = ref(null);
        const loading = ref(false);

        async function load() {
          loading.value = true;
          try { data.value = await EC.api.get('/api/overview'); }
          catch (e) { EC.toast('加载失败：' + e.message, 'error'); }
          finally { loading.value = false; }
        }

        function go(hash) { location.hash = hash; }

        const runCols = [
          { title: '时间', key: 'mtime', width: 160,
            render: (r) => h('span', { class: 'ec-mono' }, (r.mtime || '').replace('T', ' ')) },
          { title: '对象', key: 'name', width: 180, class: 'ec-mono' },
          { title: 'judge', key: 'judge_total', width: 70,
            render: (r) => r.judge_total == null ? '—' : String(r.judge_total) },
          { title: '判定', key: 'verdict', width: 80,
            render: (r) => r.verdict
              ? h('n-tag', { size: 'small',
                  type: r.verdict === 'pass' ? 'success' : 'error' },
                  { default: () => r.verdict.toUpperCase() }) : '—' },
        ];

        onMounted(load);
        return { data, loading, runCols, go, reload: load };
      },
      template: `
        <h3 class="ec-page-title">首页</h3>
        <n-spin :show="loading">
        <n-card size="small" class="ec-card">
          <n-space align="center" size="large">
            <n-button type="primary" size="small" @click="go('/studio')">🛠 新建模板（上传样例）</n-button>
            <n-button type="primary" size="small" @click="go('/run')">▶ 发起一次报告生成</n-button>
            <n-button size="small" @click="go('/templates')">📚 模板库</n-button>
            <n-button size="small" quaternary @click="reload">刷新</n-button>
          </n-space>
        </n-card>

        <n-grid :cols="4" :x-gap="12" v-if="data">
          <n-gi :span="1">
            <n-card size="small" class="ec-card">
              <n-statistic label="部门数" :value="data.departments.length" />
            </n-card>
          </n-gi>
          <n-gi :span="1">
            <n-card size="small" class="ec-card">
              <n-statistic label="模板数（含草稿）" :value="data.templates" />
            </n-card>
          </n-gi>
          <n-gi :span="2">
            <n-card title="系统健康 · 大模型" size="small" class="ec-card">
              <div class="ec-mono" style="font-size:12px">{{ data.model.base_url }}</div>
              <n-space size="small" align="center" style="margin-top:4px">
                <n-tag size="small" type="info" class="ec-mono">{{ data.model.model }}</n-tag>
                <n-tag v-if="data.model.reasoning_effort" size="small" class="ec-mono">
                  effort: {{ data.model.reasoning_effort }}</n-tag>
                <n-tag size="small" type="default">{{ data.model.api_key_masked }}</n-tag>
              </n-space>
              <div class="ec-muted" style="font-size:12px;margin-top:4px">
                模型可在 系统设置 页修改并热生效。</div>
            </n-card>
          </n-gi>
        </n-grid>

        <n-card title="部门" size="small" class="ec-card" v-if="data">
          <n-grid :cols="4" :x-gap="10" :y-gap="10">
            <n-gi v-for="d in data.departments" :key="d.id" :span="1">
              <n-card size="small" hoverable style="cursor:pointer"
                      @click="go('/departments')">
                <b class="ec-mono">{{ d.id }}</b>
                <div class="ec-muted" style="font-size:12px;margin-top:4px">
                  {{ d.description }}</div>
                <n-space size="small" style="margin-top:6px">
                  <n-tag size="tiny">{{ d.bindings }} 条数据绑定</n-tag>
                </n-space>
              </n-card>
            </n-gi>
          </n-grid>
        </n-card>

        <n-card title="最近生成" size="small" v-if="data">
          <n-data-table :columns="runCols" :data="data.recent_runs" size="small" />
          <n-button size="tiny" style="margin-top:8px" @click="go('/run')">
            前往生成监控查看全部 →</n-button>
        </n-card>
        </n-spin>
      `,
    },
  };
})();
