/* 根组件：平台壳（侧边导航 + 顶栏页头 + 内容区 hash 路由）。蓝色主题、SVG 图标。 */
(function () {
  const { createApp, ref, computed, onMounted, h } = Vue;

  const NAV = [
    { key: '/overview', label: '首页', icon: 'home' },
    { key: '/generate', label: '报告生成', icon: 'play' },
    { key: '/types', label: '报告类型管理', icon: 'layers' },
    { key: '/settings', label: '系统设置', icon: 'settings' },
  ];
  const META = {
    '/overview': { title: '首页', desc: '总览与快捷入口', icon: 'home' },
    '/generate': { title: '报告生成', desc: '选择报告类型，生成报告并查看结果', icon: 'play' },
    '/types': { title: '报告类型管理', desc: '创建和维护报告类型：报告结构、数据来源、验证与版本', icon: 'layers' },
    '/settings': { title: '系统设置', desc: '大模型、流水线参数与全局数据连接', icon: 'settings' },
  };

  const Shell = {
    setup() {
      const message = naive.useMessage();
      EC.toast = (content, type = 'info') => {
        const fn = { info: 'info', success: 'success', error: 'error', warning: 'warning' }[type] || 'info';
        message[fn](content);
      };

      const route = ref(location.hash.replace(/^#/, '').split('?')[0] || '/overview');
      const routeQuery = ref({});
      function readHash() {
        const raw = location.hash.replace(/^#/, '') || '/overview';
        const [path, qs] = raw.split('?');
        const query = {};
        if (qs) for (const kv of qs.split('&')) {
          const [k, v] = kv.split('=');
          if (k) query[k] = decodeURIComponent(v || '');
        }
        route.value = path || '/overview';
        routeQuery.value = query;
      }
      window.addEventListener('hashchange', readHash);
      readHash();
      const meta = computed(() => META[route.value] || META['/overview']);
      Vue.watchEffect(() => { document.title = `${meta.value.title} · EvidenceCraft`; });

      const menuOptions = NAV.map((n) => ({
        label: n.label, key: n.key,
        icon: () => h('span', { style: 'display:flex', innerHTML: EC.ic[n.icon](17) }),
      }));
      function go(key) { location.hash = key; }

      return { route, routeQuery, meta, menuOptions, go };
    },
    template: `
      <n-layout has-sider style="height:100vh">
        <n-layout-sider bordered :width="208" content-style="display:flex;flex-direction:column;height:100%">
          <div class="ec-logo"><span class="mark">EC</span>EvidenceCraft</div>
          <n-menu :value="route" :options="menuOptions" @update:value="go" style="flex:1" />
          <div class="ec-sider-foot">单机版 · 报告生成工具</div>
        </n-layout-sider>
        <n-layout>
          <n-layout-header bordered style="height:56px">
            <div class="ec-header">
              <div>
                <div class="t">{{ meta.title }}</div>
                <div class="d">{{ meta.desc }}</div>
              </div>
            </div>
          </n-layout-header>
          <n-layout-content content-style="padding:20px 24px;overflow:auto;height:calc(100vh - 56px)">
            <component :is="routeComponent" :key="routeKey" />
          </n-layout-content>
        </n-layout>
      </n-layout>
    `,
    setup_return: null,
  };

  // 把 routeComponent 计算并挂进 Shell setup（保持模板简洁）
  const _origSetup = Shell.setup;
  Shell.setup = function () {
    const ret = _origSetup();
    ret.routeComponent = computed(() => {
      const view = EC.views[ret.route.value] || EC.views['/overview'];
      return view.component;
    });
    ret.routeKey = computed(() => ret.route.value + JSON.stringify(ret.routeQuery.value));
    return ret;
  };

  const app = createApp({
    components: { Shell },
    template: `
      <n-config-provider :theme-overrides="themeOverrides" :locale="naive.zhCN" :date-locale="naive.dateZhCN">
        <n-message-provider>
          <n-dialog-provider>
            <Shell />
          </n-dialog-provider>
        </n-message-provider>
      </n-config-provider>
    `,
    setup() {
      const themeOverrides = {
        common: {
          primaryColor: '#2080f0',
          primaryColorHover: '#4098fc',
          primaryColorPressed: '#1060c9',
          primaryColorSuppl: '#4098fc',
          borderRadius: '6px',
          fontWeightStrong: '600',
        },
      };
      return { naive: window.naive, themeOverrides };
    },
  });
  for (const k of Object.keys(window.naive)) {
    if (/^N[A-Z]/.test(k)) app.component(k, window.naive[k]);
  }
  app.config.errorHandler = (err, _inst, info) => {
    console.error(err);
    const msg = String((err && err.message) || err).slice(0, 60);
    document.title = `渲染错误: ${msg}`;
    window.__lastErr = `${msg} @${info}`;
  };
  window.addEventListener('error', (e) => {
    window.__lastErr = String(e.message || e);
  });
  app.mount('#app');
})();
