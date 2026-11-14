const api = new DineLensClient();
const message = document.getElementById('message');
let activeJob = null;
let pollingTimer = null;
let historyOffset = 0;
let notificationOffset = 0;
let loadingNotifications = false;

function textElement(tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
}

function notify(text, error = false) {
  message.textContent = text;
  message.className = error ? 'error' : '';
}

async function history(reset = true) {
  const container = document.getElementById('history');
  if (reset) { historyOffset = 0; container.replaceChildren(); }
  const data = await api.request('/history?offset=' + historyOffset);
  if (reset && !data.items.length) container.append(textElement('p', '当前浏览器还没有历史报告。'));
  for (const entry of data.items) {
    const article = document.createElement('article');
    const link = textElement('a', entry.branch.name || '餐厅报告');
    link.href = 'report.html#report=' + encodeURIComponent(entry.report_id);
    const remove = textElement('button', '删除历史');
    remove.className = 'danger';
    remove.addEventListener('click', async () => {
      if (!confirm('删除后报告和关联分享将停止访问，确定删除吗？')) return;
      try {
        await api.request('/history/' + encodeURIComponent(entry.history_id), { method: 'DELETE' });
        await history();
        await notifications();
      } catch (error) { notify(error.message, true); }
    });
    article.append(link, textElement('p', new Date(entry.generated_at).toLocaleString()), remove);
    container.append(article);
  }
  historyOffset = data.next_offset;
  document.getElementById('more-history').hidden = historyOffset === null;
}

async function notifications(reset = true) {
  if (loadingNotifications) return;
  loadingNotifications = true;
  const status = document.getElementById('notification-status');
  try {
    const offset = reset ? 0 : notificationOffset;
    const data = await api.request('/notifications?offset=' + offset);
    const container = document.getElementById('notifications');
    if (reset) container.replaceChildren();
    for (const entry of data.items) {
      const article = document.createElement('article');
      const link = textElement('a', (entry.branch.name || '餐厅') + '的报告已完成');
      link.href = 'report.html#report=' + encodeURIComponent(entry.report_id);
      article.append(link, textElement('p', new Date(entry.created_at).toLocaleString()));
      if (entry.read_at) {
        article.append(textElement('span', '已读'));
      } else {
        const read = textElement('button', '标记已读');
        read.className = 'secondary';
        read.addEventListener('click', async () => {
          read.disabled = true;
          try {
            await api.request('/notifications/' + encodeURIComponent(entry.notification_id) + '/read', { method: 'POST' });
            await notifications();
          } catch (error) { status.textContent = error.message; }
          finally { read.disabled = false; }
        });
        article.append(read);
      }
      container.append(article);
    }
    document.getElementById('unread-count').textContent = data.unread_count ? '(' + data.unread_count + ' 条未读)' : '';
    notificationOffset = data.next_offset;
    document.getElementById('more-notifications').hidden = notificationOffset === null;
    status.textContent = reset && !data.items.length ? '暂无报告通知。' : '';
  } catch (error) {
    status.textContent = '通知读取失败，将自动重试。' + error.message;
  } finally { loadingNotifications = false; }
}

const labels = { queued: '排队中', collecting: '采集中', analysing: '分析中', done: '已完成',
  failed: '失败', cancelled: '已取消', needs_operator: '需要人工处理' };

async function poll() {
  try {
    const job = await api.request('/jobs/' + encodeURIComponent(activeJob));
    document.getElementById('task-state').textContent = (labels[job.state] || job.state) + ' · ' + job.progress + (job.error ? ' · ' + job.error : '');
    const running = ['queued', 'collecting', 'analysing'].includes(job.state);
    const cancel = document.getElementById('cancel');
    cancel.hidden = !running;
    cancel.disabled = job.cancel_requested;
    cancel.textContent = job.cancel_requested ? '等待任务安全停止' : '取消任务';
    if (job.state === 'done' && job.report_id) {
      const link = document.getElementById('report-link');
      link.href = 'report.html#report=' + encodeURIComponent(job.report_id);
      link.hidden = false;
      await history();
      await notifications();
    }
    if (running) pollingTimer = setTimeout(poll, 2000);
  } catch (error) {
    notify('读取任务失败，请稍后刷新。' + error.message, true);
  }
}

async function start(branch, button) {
  button.disabled = true;
  try {
    const job = await api.request('/jobs', { method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: { store: branch.name || 'Restaurant', branch_id: branch.branch_id, branch_confirmed: true } });
    clearTimeout(pollingTimer);
    activeJob = job.id;
    document.getElementById('task').hidden = false;
    document.getElementById('report-link').hidden = true;
    notify('已创建任务，请等待处理。');
    await poll();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

document.getElementById('search-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('search-button');
  const candidates = document.getElementById('candidates');
  button.disabled = true;
  candidates.replaceChildren();
  notify('正在搜索分店，来源响应可能需要一些时间。');
  const params = new URLSearchParams({ q: document.getElementById('restaurant').value.trim(),
    city: document.getElementById('city').value.trim(), source: document.getElementById('source').value });
  const cityCode = document.getElementById('city-code').value.trim();
  if (cityCode) params.set('city_code', cityCode);
  try {
    const data = await api.request('/branches?' + params);
    notify(data.items.length ? '请核对地址，确认要分析的分店。' : '未找到分店，请调整名称或城市。');
    for (const branch of data.items) {
      const article = document.createElement('article');
      const confirmButton = textElement('button', '确认此分店并分析');
      confirmButton.addEventListener('click', () => start(branch, confirmButton));
      article.append(textElement('h3', branch.name || '餐厅'), textElement('p', [branch.city, branch.address].filter(Boolean).join(' · ')), confirmButton);
      candidates.append(article);
    }
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
});

document.getElementById('cancel').addEventListener('click', async () => {
  try {
    await api.request('/jobs/' + encodeURIComponent(activeJob) + '/cancel', { method: 'POST' });
    clearTimeout(pollingTimer);
    await poll();
  } catch (error) { notify(error.message, true); }
});
document.getElementById('refresh-history').addEventListener('click', () => history().catch(error => notify(error.message, true)));
document.getElementById('more-history').addEventListener('click', () => history(false).catch(error => notify(error.message, true)));
document.getElementById('more-notifications').addEventListener('click', () => notifications(false));
document.addEventListener('visibilitychange', () => { if (!document.hidden) notifications(); });
history().catch(error => notify(error.message, true));
notifications();
setInterval(() => { if (!document.hidden) notifications(); }, 10000);
