/* 根组件：平台壳（侧边导航 + 顶部部门上下文 + 内容区 hash 路由）。 */
(function () {
  const { createApp, ref, computed, onMounted } = Vue;

  const Shell = {
    setup() {
      const message = naive.useMessage();
      EC.toast = (content, type = 'info') => {
        const fn = { info: 'info', success: 'success', error: 'error', warning: 'warning' }[type] || 'info';
        message[fn](content);
      };

      const route = ref(location.hash.replace(/^#/, '') || '/overview');
      window.addEventListener('hashchange', () => {
        route.value = location.hash.replace(/^#/, '') || '/overview';
      });
      const active = computed(() => EC.views[route.value] || EC.views['/overview']);

      const menuOptions = [
        { label: '🏠 首页', key: '/overview' },
        { label: '🛠 模板工作台', key: '/studio' },
        { label: '📚 模板库', key: '/templates' },
        { label: '▶️ 生成监控', key: '/run' },
        { label: '🏢 部门管理', key: '/departments' },
        { label: '🔌 数据源', key: '/sources' },
        { label: '⚙️ 系统设置', key: '/settings' },
      ];
      function go(key) { location.hash = key; }

      onMounted(() => EC.store.loadDepartments());
      return { route, active, menuOptions, go, store: EC.store };
    },
    template: `
      <n-layout has-sider style="height:100vh">
        <n-layout-sider bordered :width="200" content-style="display:flex;flex-direction:column;">
          <div class="ec-logo"><span class="dot"></span>EvidenceCraft</div>
          <n-menu :value="route" :options="menuOptions" @update:value="go" style="flex:1;" />
          <div class="ec-muted" style="padding:10px 20px;">多部门报告平台 · M8</div>
        </n-layout-sider>
        <n-layout>
          <n-layout-header bordered style="height:48px;">
            <div class="ec-header">
              <div class="ec-header-left">
                <span style="font-weight:600">{{ active.title }}</span>
                <n-select v-model:value="store.department" :options="store.departments"
                          @update:value="store.setDepartment" size="small"
                          style="width:180px" placeholder="部门上下文" />
              </div>
              <n-tag type="success" size="small" round>单机模式</n-tag>
            </div>
          </n-layout-header>
          <n-layout-content content-style="padding:20px;overflow:auto;height:calc(100vh - 48px);">
            <component :is="active.component" :key="route" />
          </n-layout-content>
        </n-layout>
      </n-layout>
    `,
  };

  const app = createApp({
    components: { Shell },
    template: `
      <n-config-provider :locale="naive.zhCN" :date-locale="naive.dateZhCN">
        <n-message-provider>
          <Shell />
        </n-message-provider>
      </n-config-provider>
    `,
    setup() {
      return { naive: window.naive };
    },
  });

  // 注册 naive-ui 全部组件（无构建链模式）
  for (const k of Object.keys(window.naive)) {
    if (/^N[A-Z]/.test(k)) app.component(k, window.naive[k]);
  }
  app.mount('#app');
})();
