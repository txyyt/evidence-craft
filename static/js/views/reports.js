/* 报告库（V2 §三 §2）：全部运行统一列表（树模式 + 历史经典报告）。
   列＝时间｜来源｜标题｜judge｜verdict（FAIL 标红）｜操作（详情/删除）；
   来源筛选、统计摘要、顶部「+ 新建报告」。反馈入口在详情页内。 */
(function () {
  const { ref, computed, onMounted } = Vue;
  const { h } = Vue;
  const NA = window.naive;

  EC.views['/reports'] = {
    title: '报告库',
    component: {
      setup() {
        const rows = ref([]);
        const loading = ref(false);
        // 来源筛选：tree_id 或 type_id；支持 /reports?tree=<id> 深链（模板页跳转）
        const filter = ref(
          new URLSearchParams(location.hash.split('?')[1] || '').get('tree'));

        async function load() {
          loading.value = true;
          try { rows.value = await EC.api.get('/api/runs'); }
          catch (e) { EC.toast('加载失败：' + e.message, 'error'); }
          finally { loading.value = false; }
        }
        onMounted(load);

        const sourceOptions = computed(() => {
          const seen = new Map();
          for (const r of rows.value) {
            const key = r.tree_id || r.type_id || '';
            if (!key) continue;
            if (!seen.has(key)) seen.set(key, r.source_name || key);
          }
          return [{ label: '全部来源', value: null },
                  ...[...seen.entries()].map(([k, v]) => ({ label: v, value: k }))];
        });
        const filtered = computed(() => filter.value
          ? rows.value.filter((r) => (r.tree_id || r.type_id) === filter.value)
          : rows.value);
        const stats = computed(() => ({
          total: rows.value.length,
          tree: rows.value.filter((r) => r.tree_id).length,
          classic: rows.value.filter((r) => !r.tree_id).length,
          fail: rows.value.filter((r) => r.verdict === 'fail').length,
        }));

        function goNew() { location.hash = '/new'; }
        function goDetail(r) {
          location.hash = '/reports/' + encodeURIComponent(r.dir);
        }
        async function delRun(r) {
          try {
            await EC.api.del('/api/runs/' + encodeURIComponent(r.dir));
            EC.toast('已删除', 'success');
            await load();
          } catch (e) { EC.toast(e.message, 'error'); }
        }

        const cols = [
          { title: '时间', key: 'mtime', width: 145,
            render: (r) => h('span', { class: 'ec-mono' }, (r.mtime || '').replace('T', ' ')) },
          { title: '来源', key: 'source_name', width: 150, ellipsis: { tooltip: true },
            render: (r) => r.source_name || r.type_name || '—' },
          { title: '标题', key: 'title', ellipsis: { tooltip: true } },
          { title: 'judge', key: 'judge_total', width: 70,
            render: (r) => r.judge_total == null ? '—' : String(r.judge_total) },
          { title: '判定', key: 'verdict', width: 85,
            render: (r) => r.verdict
              ? h(NA.NTag, { size: 'small', type: r.verdict === 'pass' ? 'success' : 'error' },
                  { default: () => r.verdict.toUpperCase() }) : '—' },
          { title: '操作', key: 'op', width: 130,
            render: (r) => h('n-space', { size: 'small' }, {
              default: () => [
                h(NA.NButton, { size: 'tiny', type: 'primary', quaternary: true,
                                onClick: () => goDetail(r) }, { default: () => '详情' }),
                h(NA.NButton, { size: 'tiny', type: 'error', quaternary: true,
                                onClick: () => delRun(r) }, { default: () => '删除' }),
              ] }) },
        ];

        return { rows, loading, filter, sourceOptions, filtered, stats,
                 cols, goNew, goDetail, reload: load, icoPlus: EC.ic.plus(15) };
      },
      template: `
        <n-spin :show="loading">
          <n-card size="small" class="ec-card" style="margin-bottom:12px">
            <n-space align="center" justify="space-between">
              <n-space align="center" size="large">
                <n-statistic label="报告总数" :value="stats.total" />
                <n-statistic label="树模式" :value="stats.tree" />
                <n-statistic label="经典报告" :value="stats.classic" />
                <n-statistic label="FAIL">
                  <template #default>
                    <span :style="stats.fail ? 'color:#d03050;font-weight:600' : ''">{{ stats.fail }}</span>
                  </template>
                </n-statistic>
              </n-space>
              <n-space align="center">
                <n-select v-model:value="filter" :options="sourceOptions" size="small"
                          style="width:200px" placeholder="来源筛选" clearable />
                <n-button type="primary" @click="goNew">
                  <template #icon><span v-html="icoPlus"></span></template>
                  新建报告</n-button>
                <n-button quaternary @click="reload">刷新</n-button>
              </n-space>
            </n-space>
          </n-card>
          <n-card size="small" class="ec-card">
            <n-data-table :columns="cols" :data="filtered" size="small"
                          :row-key="(r) => r.dir"
                          :row-class-name="(r) => r.verdict === 'fail' ? 'ec-row-fail' : ''"
                          :pagination="{ pageSize: 15 }"
                          @update:page="() => {}" />
          </n-card>
        </n-spin>
      `,
    },
  };
})();
