/* fetch 封装：统一 JSON、错误提取（FastAPI 的 {detail}）。
   非破坏性初始化 EC 命名空间——icons/fielddict 可能先于本文件挂载。 */
(function () {
  window.EC = window.EC || {};
  EC.views = EC.views || {};

  async function request(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* 非 JSON 错误体 */ }
      throw new Error(msg);
    }
    return r.status === 204 ? null : r.json();
  }

  EC.api = {
    get: (url) => request('GET', url),
    post: (url, body) => request('POST', url, body ?? {}),
    put: (url, body) => request('PUT', url, body ?? {}),
    del: (url) => request('DELETE', url),
  };
})();
