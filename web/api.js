/* Same-origin client for both Cloudflare Pages and the local /web mount. */
class DineLensClient {
  constructor(base = location.pathname.startsWith('/web/') ? '' : '/api') {
    this.base = base.replace(/\/+$/, '');
    this.sessionPromise = null;
  }

  async session() {
    if (!this.sessionPromise) {
      this.sessionPromise = (async () => {
        let response = await fetch(this.base + '/owner-session', { credentials: 'include', cache: 'no-store' });
        if (response.status === 401) {
          response = await fetch(this.base + '/owner-session', {
            method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}'
          });
        }
        if (!response.ok) throw new Error('无法建立会话，请通过网页服务打开页面并检查允许的来源。');
        return (await response.json()).csrf_token;
      })().catch(error => { this.sessionPromise = null; throw error; });
    }
    return this.sessionPromise;
  }

  async request(path, { method = 'GET', body, headers = {} } = {}, retry = true) {
    const csrf = await this.session();
    const options = { method, credentials: 'include', cache: 'no-store', headers: { ...headers } };
    if (method !== 'GET') options.headers['X-CSRF-Token'] = csrf;
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(this.base + path, options);
    if (response.status === 401 && retry) {
      this.sessionPromise = null;
      return this.request(path, { method, body, headers }, false);
    }
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const error = new Error(typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail || data));
      error.status = response.status;
      throw error;
    }
    return data;
  }
}
