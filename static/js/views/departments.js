/* 部门管理：profile 编辑器（绑定表 / 词表 / 特性 / 单绑定测试）+ 新增部门。 */
(function () {
  const { ref, reactive, onMounted } = Vue;
  const { h } = Vue;

  EC.views['/departments'] = {
    title: '部门管理',
    component: {
      setup() {
        const depts = ref([]);
        const current = ref('');
        const profile = ref(null);
        const dirty = ref(false);
        const saving = ref(false);
        const adapters = ref([]);
        const testResult = reactive({});   // index → {ok, ...}
        const testing = reactive({});
        const runParams = reactive({});
        const showNew = ref(false);
        const newId = ref('');
        const newDesc = ref('');
        const showRaw = ref(false);
        const rawText = ref('');

        async function loadList() {
          depts.value = (await EC.api.get('/api/departments'))
            .map((d) => ({ label: d.id, value: d.id }));
          if (!current.value && depts.value.length) current.value = depts.value[0].value;
          if (current.value) await open(current.value);
        }

        async function open(id) {
          current.value = id;
          const r = await EC.api.get('/api/departments/' + id);
          profile.value = r.profile;
          dirty.value = false;
        }

        async function save() {
          saving.value = true;
          try {
            await EC.api.put('/api/departments/' + current.value,
              { profile: profile.value });
            dirty.value = false;
            EC.toast('profile 已保存（快照留痕）', 'success');
          } catch (e) { EC.toast('保存失败：' + e.message, 'error'); }
          finally { saving.value = false; }
        }

        function addBinding() {
          profile.value.bindings = profile.value.bindings || [];
          profile.value.bindings.push({ need: 'new_need', adapter: '', params: {} });
          dirty.value = true;
        }

        async function testBinding(i) {
          testing[i] = true;
          try {
            testResult[i] = await EC.api.post(
              '/api/departments/' + current.value + '/test_binding',
              { index: i, params: { ...runParams } });
          } catch (e) { testResult[i] = { ok: false, error: e.message }; }
          finally { testing[i] = false; }
        }

        async function createDept() {
          try {
            const r = await EC.api.post('/api/departments',
              { id: newId.value, description: newDesc.value });
            EC.toast('已创建部门 ' + r.id, 'success');
            showNew.value = false;
            newId.value = newDesc.value = '';
            await loadList();
            current.value = r.id;
            await open(r.id);
          } catch (e) { EC.toast('创建失败：' + e.message, 'error'); }
        }

        function paramRepr() {
          return JSON.stringify(profile.value, null, 2);
        }
        async function openRaw() {
          rawText.value = paramRepr();
          showRaw.value = true;
        }

        onMounted(async () => {
          adapters.value = (await EC.api.get('/api/sources/adapters'))
            .map((a) => ({ label: `${a.key}（${a.kind}）`, value: a.key }));
          await loadList();
        });

        return {
          depts, current, profile, dirty, saving, adapters, testResult, testing,
          runParams, showNew, newId, newDesc, showRaw, rawText,
          open, save, addBinding, testBinding, createDept, openRaw,
        };
      },
      template: `
        <h3 class="ec-page-title">部门管理</h3>
        <n-card size="small" class="ec-card">
          <n-space align="center">
            <n-select :value="current" :options="depts" @update:value="open"
                      style="width:220px" size="small" placeholder="选择部门" />
            <n-button size="small" @click="showNew = true">新增部门</n-button>
            <n-button size="small" @click="openRaw">查看 JSON</n-button>
            <n-button size="small" type="primary" :disabled="!dirty"
                      :loading="saving" @click="save">保存（快照留痕）</n-button>
            <n-input v-for="(label, key) in (profile ? profile.params_schema : {})"
                     :key="key" v-model:value="runParams[key]" :placeholder="label"
                     size="small" style="width:130px" />
          </n-space>
        </n-card>

        <template v-if="profile">
        <n-card title="基本信息" size="small" class="ec-card">
          <n-form size="small" label-placement="left" label-width="120">
            <n-form-item label="描述">
              <n-input v-model:value="profile.description" @update:value="dirty = true" />
            </n-form-item>
            <n-form-item label="judge 范文路径">
              <n-input v-model:value="profile.judge_reference"
                       placeholder="config/reference/xxx.md（部门级缺省）"
                       @update:value="dirty = true" />
            </n-form-item>
          </n-form>
        </n-card>

        <n-card title="数据绑定 bindings（按序执行，$ctx 跨源传参）" size="small" class="ec-card">
          <n-space vertical size="small">
            <div v-for="(b, i) in (profile.bindings || [])" :key="i"
                 style="border:1px solid #eee;border-radius:4px;padding:8px">
              <n-space align="center" size="small">
                <n-tag size="tiny" class="ec-mono">{{ i }}</n-tag>
                <n-input v-model:value="b.need" placeholder="need（语义标签）"
                         size="small" style="width:140px" @update:value="dirty = true" />
                <n-select v-model:value="b.adapter" :options="adapters" size="small"
                          style="width:240px" placeholder="adapter"
                          @update:value="dirty = true" />
                <n-input v-model:value="runParams.__x" v-if="false" />
                <n-button size="tiny" :loading="!!testing[i]"
                          @click="testBinding(i)">单绑定测试</n-button>
                <n-button size="tiny" quaternary type="error"
                          @click="profile.bindings.splice(i, 1); dirty = true">删除</n-button>
              </n-space>
              <n-input :value="JSON.stringify(b.params || {})"
                       @update:value="b.params = JSON.parse($event || '{}'); dirty = true"
                       type="textarea" :rows="1" size="small" class="ec-mono"
                       placeholder='params JSON，如 {"stock": "$stock"}'
                       style="margin-top:6px;font-size:12px" />
              <div v-if="testResult[i]" style="margin-top:4px;font-size:12px">
                <n-tag size="tiny" :type="testResult[i].ok ? 'success' : 'error'">
                  {{ testResult[i].ok ? 'OK' : '失败' }}</n-tag>
                <span v-if="testResult[i].ok" class="ec-muted">
                  事实 {{ testResult[i].facts }} 条，collections: {{ (testResult[i].collections||[]).join(', ') || '无' }}
                  {{ (testResult[i].warnings||[]).length ? '⚠ ' + testResult[i].warnings.join('；') : '' }}</span>
                <span v-else style="color:#d03050">{{ testResult[i].error }}</span>
              </div>
            </div>
            <n-button size="small" dashed @click="addBinding">+ 加一条绑定</n-button>
          </n-space>
        </n-card>

        <n-grid :cols="2" :x-gap="12">
          <n-gi>
            <n-card title="词表 vocabulary" size="small">
              <n-input :value="JSON.stringify(profile.vocabulary || {}, null, 2)"
                       @update:value="profile.vocabulary = JSON.parse($event || '{}'); dirty = true"
                       type="textarea" :rows="5" class="ec-mono" style="font-size:12px" />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card title="特性 features / 交叉校验 crosschecks" size="small">
              <div v-for="(v, k) in (profile.features || {})" :key="k" style="margin-bottom:6px">
                <n-checkbox :checked="!!v" @update:checked="(x) => { profile.features[k] = x; dirty = true }">
                  <span class="ec-mono" style="font-size:12px">{{ k }}</span></n-checkbox>
              </div>
              <n-input :value="JSON.stringify(profile.crosschecks || [])"
                       @update:value="profile.crosschecks = JSON.parse($event || '[]'); dirty = true"
                       type="textarea" :rows="2" class="ec-mono" size="small"
                       placeholder='crosschecks JSON 数组，如 ["financial_dual_source"]'
                       style="font-size:12px;margin-top:4px" />
            </n-card>
          </n-gi>
        </n-grid>
        </template>
        <n-empty v-else description="选择或新增一个部门" style="margin:40px 0" />

        <n-modal v-model:show="showNew" preset="dialog" title="新增部门"
                 positive-text="创建" negative-text="取消" @positive-click="createDept">
          <n-space vertical>
            <n-input v-model:value="newId" placeholder="部门 id（英文 snake_case）" />
            <n-input v-model:value="newDesc" placeholder="一句话描述（可留空）" />
          </n-space>
        </n-modal>

        <n-modal v-model:show="showRaw" preset="card" title="profile JSON" style="width:720px">
          <n-input :value="rawText" type="textarea" class="ec-mono" readonly
                   :autosize="{minRows: 12, maxRows: 26}" style="font-size:12px" />
        </n-modal>
      `,
    },
  };
})();
