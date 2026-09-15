/* 根组件：平台壳（侧边导航 + 顶栏页头 + 内容区 hash 路由）。
   V2 §三：导航 4 项（新建报告/报告库/模板/设置）+ 参数路由 /reports/:dir
   + 旧路由重定向（overview/trees/generate/types → 新路由）。 */
(function () {
  const { createApp, ref, computed, h } = Vue;

  const NAV = [
    { key: '/new', label: '新建报告', icon: 'plus' },
    { key: '/reports', label: '报告库', icon: 'home' },
    { key: '/templates', label: '模板', icon: 'edit' },
    { key: '/settings', label: '设置', icon: 'settings' },
  ];
  const META = {
    '/new': { title: '新建报告', desc: '三步向导：结构 → 数据 → 生成', icon: 'plus' },
    '/reports': { title: '报告库', desc: '全部生成记录：预览、反馈迭代、治理体检', icon: 'home' },
    '/reports/:dir': { title: '报告详情', desc: '预览 · 反馈迭代 · 治理体检 · 文件 · 元信息', icon: 'home' },
    '/templates': { title: '模板', desc: '报告结构树：对话生成、手动编辑、版本回滚与派生', icon: 'edit' },
    '/settings': { title: '系统设置', desc: '大模型、流水线参数与全局数据连接', icon: 'settings' },
  };
  // §三 §1：旧路由重定向（含 /run?dir=X 的参数改写）
  const REDIRECTS = {
    '/overview': '/reports',
    '/trees': '/templates',
    '/generate': '/reports',
    '/types': '/templates',
    '/run': (q) => (q.dir ? '/reports/' + encodeURIComponent(q.dir) : '/reports'),
  };

  const Shell = {
    setup() {
      const message = naive.useMessage();
      EC.toast = (content, type = 'info') => {
        const fn = { info: 'info', success: 'success', error: 'error', warning: 'warning' }[type] || 'info';
        message[fn](content);
      };

      const route = ref(location.hash.replace(/^#/, '').split('?')[0] || '/reports');
      const routeQuery = ref({});
      function readHash() {
        const raw = location.hash.replace(/^#/, '') || '/reports';
        const [path, qs] = raw.split('?');
        const query = {};
        if (qs) for (const kv of qs.split('&')) {
          const [k, v] = kv.split('=');
          if (k) query[k] = decodeURIComponent(v || '');
        }
        // §0：参数路由 /reports/:dir → 视图键 /reports/:dir（query.dir 同步）
        const paramMatch = path.match(/^\/reports\/([^/]+)$/);
        let resolved = path;
        if (paramMatch) {
          resolved = '/reports/:dir';
          query.dir = decodeURIComponent(paramMatch[1]);
        }
        // §1：旧路由重定向（含 /run?dir=X 的参数改写）
        const redir = REDIRECTS[resolved];
        if (redir) {
          const target = typeof redir === 'function' ? redir(query) : redir;
          const qs2 = resolved === '/run' ? '' :
            (Object.keys(query).length
              ? '?' + Object.entries(query).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
              : '');
          location.replace('#' + target + (target.includes('?') ? '' : qs2));
          return;
        }
        route.value = resolved || '/reports';
        routeQuery.value = query;
      }
      window.addEventListener('hashchange', readHash);
      readHash();
      const meta = computed(() => META[route.value] || META['/reports']);
      Vue.watchEffect(() => { document.title = `${meta.value.title} · EvidenceCraft`; });

      const menuOptions = NAV.map((n) => ({
        label: n.label, key: n.key,
        icon: () => h('span', { style: 'display:flex', innerHTML: EC.ic[n.icon](17) }),
      }));
      function go(key) { location.hash = key; }
      // 菜单高亮：/reports/:dir 也高亮报告库
      const activeKey = computed(() => {
        if (route.value === '/reports' || route.value === '/reports/:dir')
          return '/reports';
        return route.value;
      });

      return { route, routeQuery, meta, menuOptions, go, activeKey };
    },
    template: `
      <n-layout has-sider style="height:100vh">
        <n-layout-sider bordered :width="208" content-style="display:flex;flex-direction:column;height:100%">
          <div class="ec-logo"><span class="mark">EC</span>EvidenceCraft</div>
          <n-menu :value="activeKey" :options="menuOptions" @update:value="go" style="flex:1" />
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
      const view = EC.views[ret.route.value] || EC.views['/reports'] || Object.values(EC.views)[0];
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
    window.__lastErrStack = String((err && err.stack) || '');
  };
  window.addEventListener('error', (e) => {
    window.__lastErr = String(e.message || e);
  });
  app.mount('#app');
})();
