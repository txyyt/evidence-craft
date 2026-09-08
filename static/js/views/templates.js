/* 模板库：全部模板 + 版本时间线 + 两版 diff + 回滚。 */
(function () {
  const { ref, reactive, onMounted } = Vue;
  const { h } = Vue;

  EC.views['/templates'] = {
    title: '模板库',
    component: {
      setup() {
        const list = ref([]);
        const loading = ref(false);
        const raw = reactive({ show: false, name: '', text: '' });
        const ver = reactive({ show: false, name: '', versions: [], a: 'current', b: 'current', diff: '', changed: false });
        const rolling = ref(false);

        async function load() {
          loading.value = true;
          try { list.value = await EC.api.get('/api/templates'); }
          finally { loading.value = false; }
        }

        async function openRaw(name) {
          const r = await EC.api.get(`/api/templates/${name}/raw`);
          raw.name = name; raw.text = r.text; raw.show = true;
        }

        async function openVersions(name) {
          const r = await EC.api.get(`/api/templates/${name}/versions`);
          ver.name = name;
          ver.versions = r.versions;
          ver.a = 'current'; ver.b = 'current'; ver.diff = ''; ver.changed = false;
          ver.show = true;
        }

        async function runDiff() {
          const r = await EC.api.get(
            `/api/templates/${ver.name}/diff?a=${encodeURIComponent(ver.a)}&b=${encodeURIComponent(ver.b)}`);
          ver.diff = r.diff; ver.changed = r.changed;
        }

        async function rollback() {
          rolling.value = true;
          try {
            await EC.api.post('/api/templates/rollback', { name: ver.name, version: ver.b });
            EC.toast(`已回滚 ${ver.name} ← ${ver.b}（回滚前版本已留痕）`, 'success');
            ver.show = false;
            load();
          } catch (e) { EC.toast('回滚失败：' + e.message, 'error'); }
          finally { rolling.value = false; }
        }

        const columns = [
          { title: '模板', key: 'name', render: (r) => h('span', { class: 'ec-mono' }, r.name) },
          { title: '状态', key: 'draft', width: 90,
            render: (r) => h('n-tag', { size: 'small', type: r.draft ? 'warning' : 'success' },
              { default: () => (r.draft ? '草稿' : '正式') }) },
          { title: '最后修改', key: 'mtime', width: 170 },
          { title: '历史版本', key: 'versions', width: 90 },
          { title: '操作', key: 'act', width: 200,
            render: (r) => h('n-space', { size: 'small' }, { default: () => [
              h('n-button', { size: 'tiny', onClick: () => openRaw(r.name) }, { default: () => '查看 YAML' }),
              h('n-button', { size: 'tiny', onClick: () => openVersions(r.name) }, { default: () => '版本 / 回滚' }),
            ] }) },
        ];

        onMounted(load);
        return { list, loading, columns, raw, ver, runDiff, rollback, rolling };
      },
      template: `
        <h3 class="ec-page-title">模板库</h3>
        <n-data-table :columns="columns" :data="list" :loading="loading" size="small" />

        <n-modal v-model:show="raw.show" preset="card" :title="'YAML · ' + raw.name" style="width:760px">
          <n-input :value="raw.text" type="textarea" class="ec-mono"
                   :autosize="{minRows: 12, maxRows: 28}" style="font-size:12px" readonly />
        </n-modal>

        <n-modal v-model:show="ver.show" preset="card" :title="'版本 · ' + ver.name" style="width:820px">
          <n-space vertical size="small">
            <n-alert v-if="!ver.versions.length" type="info" size="small">
              还没有历史版本——每次定稿覆盖同名模板、工作台每次保存草案前都会自动留痕。
            </n-alert>
            <template v-else>
              <n-space align="center" size="small">
                <span>对比</span>
                <n-select v-model:value="ver.a" size="small" style="width:230px"
                          :options="[{label: '当前版', value: 'current'}, ...ver.versions.map(v => ({label: v.file, value: v.file}))]" />
                <span>与</span>
                <n-select v-model:value="ver.b" size="small" style="width:230px"
                          :options="[{label: '当前版', value: 'current'}, ...ver.versions.map(v => ({label: v.file, value: v.file}))]" />
                <n-button size="small" @click="runDiff">diff</n-button>
              </n-space>
              <n-input v-if="ver.diff" :value="ver.diff" type="textarea" class="ec-mono"
                       :autosize="{minRows: 8, maxRows: 22}" style="font-size:12px" readonly />
              <n-space v-if="ver.changed && ver.b !== 'current'">
                <n-button type="warning" size="small" :loading="rolling" @click="rollback">
                  回滚到 {{ ver.b }}</n-button>
              </n-space>
            </template>
          </n-space>
        </n-modal>
      `,
    },
  };
})();
