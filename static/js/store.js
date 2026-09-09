/* 全局状态与工具。 */
(function () {
  const { reactive } = Vue;
  EC.store = reactive({
    types: [],
    async loadTypes() {
      try { this.types = await EC.api.get('/api/types'); }
      catch (e) { this.types = []; }
    },
  });
})();
