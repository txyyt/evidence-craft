/* 全局状态与工具。
   V4-02：activeTasks 全局任务中心——可离开/恢复/取消。
   只存 id 与可公开元数据（sessionStorage ec.activeTasks.v1），
   不存密钥、报告内容与原始数据。 */
(function () {
  const { reactive } = Vue;

  EC.store = reactive({
    types: [],
    async loadTypes() {
      try { this.types = await EC.api.get('/api/types'); }
      catch (e) { this.types = []; }
    },
  });

  const KEY = 'ec.activeTasks.v1';
  const listeners = new Set();
  let tasks = [];

  function persist() {
    try { sessionStorage.setItem(KEY, JSON.stringify(tasks)); }
    catch (e) { /* 隐私模式等忽略 */ }
    for (const fn of listeners) {
      try { fn(tasks); } catch (e) { /* 订阅方异常不扩散 */ }
    }
  }

  function load() {
    try { tasks = JSON.parse(sessionStorage.getItem(KEY) || '[]') || []; }
    catch (e) { tasks = []; }
    return tasks;
  }

  /** 任务字段固定：{id, kind, treeId, treeName, artifactDir, status, stage,
      message, startedAt, updatedAt, returnRoute} */
  EC.tasks = {
    /** 注册/刷新任务（幂等：同 id 覆盖元数据，保留已有 status/stage） */
    register(t) {
      const now = new Date().toISOString();
      const found = tasks.find((x) => x.id === t.id);
      if (found) {
        Object.assign(found, t, { updatedAt: now });
      } else {
        tasks.unshift({ status: 'running', stage: '', message: '',
          startedAt: now, updatedAt: now, ...t });
        if (tasks.length > 12) tasks.length = 12;   // 会话内只留最近任务
      }
      persist();
    },
    update(id, patch) {
      // 不可变更新（重建数组与元素对象）：Vue 渲染层依赖引用变化才重渲染
      const now = new Date().toISOString();
      tasks = tasks.map((x) => x.id === id
        ? { ...x, ...(patch || {}), updatedAt: now } : x);
      persist();
    },
    remove(id) {
      tasks = tasks.filter((x) => x.id !== id);
      persist();
    },
    get(id) { return tasks.find((x) => x.id === id) || null; },
    list() { return tasks.slice(); },
    /** 运行中任务（banner/运行中区） */
    running() { return tasks.filter((x) => x.status === 'running'); },
    subscribe(fn) {
      listeners.add(fn);
      fn(tasks);
      return () => listeners.delete(fn);
    },
    restore: load,
    /** 任务 kind → 中文 */
    kindLabel(k) {
      return ({ run: '生成报告', plan: '出数据计划', preview: '数据预检',
        feedback: '意见执行', judge: '深度评审', rollback: '回滚',
        extract: '模板提取' }[k] || k || '任务');
    },
    /** 流水线 stage → 中文 */
    stageLabel(s) {
      return ({ data: '数据层', outline: '大纲生成', sections: '分节生成',
        review: '评审修订', render: '渲染输出', plan: '生成计划',
        preview: '预检数据' }[s] || s || '进行中');
    },
  };
})();
