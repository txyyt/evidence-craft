/* 全局状态：部门上下文（localStorage 持久化）。 */
(function () {
  const { reactive } = Vue;

  EC.store = reactive({
    departments: [],
    department: localStorage.getItem('ec.department') || 'stock_demo',
    async loadDepartments() {
      try {
        this.departments = (await EC.api.get('/api/departments'))
          .map((d) => ({ label: d.id, value: d.id }));
      } catch (e) { /* 壳降级：接口未就绪时下拉为空 */ }
    },
    setDepartment(v) {
      this.department = v;
      localStorage.setItem('ec.department', v);
    },
  });
})();
