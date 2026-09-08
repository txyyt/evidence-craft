/* 系统设置：模型配置（密钥只写不回显）+ 连通测试 + 关于。 */
(function () {
  const { reactive, ref, onMounted } = Vue;

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
        const effortOptions = [
          { label: '默认（不传参，服务端档位）', value: '' },
          { label: 'low（思维链最省，推荐 GLM）', value: 'low' },
          { label: 'high（更充分思考，更慢）', value: 'high' },
          { label: 'max（最大思考量）', value: 'max' },
        ];

        async function load() {
          const r = await EC.api.get('/api/settings/model');
          form.base_url = r.base_url;
          form.model = r.model;
          form.reasoning_effort = r.reasoning_effort || '';
          masked.value = r.api_key_masked;
          about.value = await EC.api.get('/api/settings/about');
        }

        async function save() {
          saving.value = true;
          try {
            const r = await EC.api.put('/api/settings/model', { ...form });
            masked.value = r.api_key_masked;
            form.api_key = '';
            EC.toast('模型配置已保存并热生效', 'success');
          } catch (e) {
            EC.toast('保存失败：' + e.message, 'error');
          } finally { saving.value = false; }
        }

        async function test() {
          testing.value = true;
          testResult.value = null;
          try {
            testResult.value = await EC.api.post('/api/settings/model/test', { ...form });
          } catch (e) {
            testResult.value = { ok: false, error: e.message };
          } finally { testing.value = false; }
        }

        onMounted(load);
        return { form, masked, saving, testing, testResult, about, effortOptions, save, test };
      },
      template: `
        <h3 class="ec-page-title">系统设置</h3>

        <n-card title="大模型配置" class="ec-card" size="small">
          <n-form label-placement="left" label-width="140">
            <n-form-item label="接口地址 base_url">
              <n-input v-model:value="form.base_url" placeholder="https://open.bigmodel.cn/api/paas/v4/" />
            </n-form-item>
            <n-form-item label="模型名 model">
              <n-input v-model:value="form.model" placeholder="glm-5.3-flash / deepseek-v4-..." />
            </n-form-item>
            <n-form-item label="API Key">
              <n-input v-model:value="form.api_key" type="password" show-password-on="click"
                :placeholder="masked ? ('已配置 ' + masked + '，留空则不修改') : '未配置'" />
            </n-form-item>
            <n-form-item label="思考档位 reasoning_effort">
              <n-select v-model:value="form.reasoning_effort" :options="effortOptions" />
            </n-form-item>
          </n-form>
          <template #footer>
            <n-space>
              <n-button type="primary" :loading="saving" @click="save">保存（热生效）</n-button>
              <n-button :loading="testing" @click="test">测试连通</n-button>
              <span v-if="testResult" :style="{color: testResult.ok ? '#18a058' : '#d03050'}">
                {{ testResult.ok
                  ? '连通正常，' + testResult.latency_s + 's，回复：' + testResult.reply
                  : '失败（' + testResult.latency_s + 's）：' + testResult.error }}
              </span>
            </n-space>
          </template>
        </n-card>

        <n-card title="关于" size="small" v-if="about">
          <div>{{ about.name }} · 里程碑 {{ about.milestone }}</div>
          <div class="ec-muted" style="margin-top:4px">
            流水线阶段：{{ about.pipeline_stages.join(' → ') }}
          </div>
        </n-card>
      `,
    },
  };
})();
