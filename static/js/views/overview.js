/* 首页：快捷入口 / 报告类型卡片 / 最近生成 / 模型健康。 */
(function () {
  const { ref, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  EC.views['/overview'] = {
    title: '首页',
    component: {
      setup() {
        const data = ref(null);
        const loading = ref(false);
        const loadingDemo = ref(false);

        async function load() {
          loading.value = true;
          try { data.value = await EC.api.get('/api/overview'); }
          catch (e) { EC.toast('加载失败：' + e.message, 'error'); }
          finally { loading.value = false; }
        }

        async function loadDemo() {
          loadingDemo.value = true;
          try {
            const r = await EC.api.post('/api/types/load-demo');
            if (r.created.length) EC.toast('已载入演示报告类型：' + r.created.join('、'), 'success');
            else EC.toast('演示报告类型已存在', 'info');
            await load();
          } catch (e) { EC.toast('载入失败：' + e.message, 'error'); }
          finally { loadingDemo.value = false; }
        }

        function go(hash) { location.hash = hash; }

        const runCols = [
          { title: '时间', key: 'mtime', width: 150,
            render: (r) => h('span', { class: 'ec-mono' }, (r.mtime || '').replace('T', ' ')) },
          { title: '报告类型', key: 'type_name', width: 150,
            render: (r) => r.type_name || '—' },
          { title: '对象', key: 'name', width: 170, class: 'ec-mono' },
          { title: 'judge', key: 'judge_total', width: 70,
            render: (r) => r.judge_total == null ? '—' : String(r.judge_total) },
          { title: '判定', key: 'verdict', width: 80,
            render: (r) => r.verdict
              ? h(NA.NTag, { size: 'small', type: r.verdict === 'pass' ? 'success' : 'error' },
                  { default: () => r.verdict.toUpperCase() }) : '—' },
        ];

        onMounted(load);
        return { data, loading, runCols, go, reload: load, loadDemo, loadingDemo,
                 icoPlus: EC.ic.plus(15) };
      },
      template: `
        <n-spin :show="loading">
        <n-card size="small" class="ec-card">
          <n-space>
            <n-button type="primary" @click="go('/types?action=new')">
              <template #icon><span v-html="icoPlus"></span></template>
              新建报告类型</n-button>
            <n-button type="primary" ghost @click="go('/generate')">发起一次报告生成</n-button>
            <n-button quaternary @click="reload">刷新</n-button>
          </n-space>
        </n-card>

        <template v-if="data">
        <n-grid :cols="4" :x-gap="14" v-if="data.types.length">
          <n-gi :span="1" v-for="t in data.types" :key="t.id">
            <n-card size="small" hoverable style="cursor:pointer;margin-bottom:14px"
                    @click="go('/types?id=' + t.id)">
              <div class="ec-flex" style="justify-content:space-between">
                <b>{{ t.name }}</b>
                <n-tag size="small" :type="t.status === 'published' ? 'success'
                        : t.status === 'verified' ? 'info' : 'default'">
                  {{ t.status === 'published' ? '已发布' : t.status === 'verified' ? '已验证' : '草稿' }}</n-tag>
              </div>
              <div class="ec-dim" style="margin-top:6px;min-height:36px">{{ t.description || '（无说明）' }}</div>
              <div class="ec-muted" style="margin-top:8px">
                {{ t.params }} 个生成参数 · {{ t.bindings }} 条数据绑定</div>
            </n-card>
          </n-gi>
          <n-gi :span="1">
            <n-card size="small" style="margin-bottom:14px;border-style:dashed;cursor:pointer"
                    hoverable @click="go('/types?action=new')">
              <div style="text-align:center;padding:18px 0;color:var(--ec-primary)">
                <span v-html="icoPlus"></span> 新建报告类型</div>
            </n-card>
          </n-gi>
        </n-grid>
        <n-card size="small" class="ec-card" v-else>
          <n-empty description="还没有报告类型——先载入内置演示体验完整流程，或新建一个">
            <template #extra>
              <n-space>
                <n-button type="primary" size="small" :loading="loadingDemo" @click="loadDemo">
                  载入演示报告类型</n-button>
                <n-button size="small" @click="go('/types?action=new')">新建</n-button>
              </n-space>
            </template>
          </n-empty>
        </n-card>

        <n-grid :cols="3" :x-gap="14">
          <n-gi :span="2">
            <n-card title="最近生成" size="small">
              <n-data-table :columns="runCols" :data="data.recent_runs" size="small" />
              <n-button v-if="data.recent_runs.length" size="tiny" style="margin-top:8px"
                        @click="go('/generate')">前往报告生成查看全部</n-button>
            </n-card>
          </n-gi>
          <n-gi :span="1">
            <n-card title="模型健康" size="small">
              <div class="ec-mono" style="font-size:12px;word-break:break-all">{{ data.model.base_url }}</div>
              <n-space size="small" style="margin-top:8px">
                <n-tag size="small" type="info" class="ec-mono">{{ data.model.model }}</n-tag>
                <n-tag v-if="data.model.reasoning_effort" size="small" class="ec-mono">
                  effort: {{ data.model.reasoning_effort }}</n-tag>
              </n-space>
              <div class="ec-muted" style="margin-top:8px">模型与流水线参数可在系统设置页调整并热生效。</div>
            </n-card>
          </n-gi>
        </n-grid>
        </template>
        </n-spin>
      `,
    },
  };
})();
