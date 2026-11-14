const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem("dinelens-admin-token") || "";
let statusTimer = null;
let logTimer = null;
let logSource = "backend";
let logLines = 300;
let logAuto = true;
let logPayload = null;
let statusBusy = false;
let logBusy = false;

function toast(text, error = false) {
  const node = $("toast");
  node.textContent = text;
  node.className = error ? "toast error" : "toast";
  node.hidden = false;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => { node.hidden = true; }, 4200);
}

function showToken(message = "") {
  $("token-modal").hidden = false;
  $("token-error").textContent = message;
  $("token-input").focus();
}

function clearTimers() {
  if (statusTimer) clearInterval(statusTimer);
  if (logTimer) clearInterval(logTimer);
  statusTimer = null;
  logTimer = null;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers["X-Admin-Token"] = token;
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let data = null;
  if (response.status !== 204) {
    const text = await response.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  }
  if (response.status === 401 || response.status === 403 || response.status === 503) {
    token = "";
    sessionStorage.removeItem("dinelens-admin-token");
    showToken(data?.detail || "Token 不正确。");
    throw new Error(data?.detail || "未授权");
  }
  if (!response.ok) {
    throw new Error(data?.detail || ("HTTP " + response.status));
  }
  return data;
}

function fmtBytes(value) {
  if (value === null || value === undefined) return "--";
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
  return (value / 1024 / 1024).toFixed(1) + " MB";
}

function card(title, tone, value, lines = [], actions = []) {
  const node = document.createElement("article");
  node.className = "card " + tone;
  const head = document.createElement("div");
  head.className = "card-title";
  const h = document.createElement("h3");
  h.textContent = title;
  const dot = document.createElement("span");
  dot.className = "dot " + tone;
  head.append(h, dot);
  node.append(head);

  const valueNode = document.createElement("div");
  valueNode.className = "card-value";
  valueNode.textContent = value;
  node.append(valueNode);

  if (lines.length) {
    const detail = document.createElement("div");
    detail.className = "card-detail";
    for (const line of lines) {
      const p = document.createElement("div");
      p.textContent = line;
      detail.append(p);
    }
    node.append(detail);
  }

  if (actions.length) {
    const row = document.createElement("div");
    row.className = "card-actions";
    for (const action of actions) {
      if (action.href) {
        const a = document.createElement("a");
        a.className = "button-link";
        a.href = action.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = action.label;
        row.append(a);
      } else {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ghost";
        b.textContent = action.label;
        b.addEventListener("click", action.onClick);
        row.append(b);
      }
    }
    node.append(row);
  }
  return node;
}

function renderStatus(data) {
  const grid = $("status-grid");
  const base = data.backend_url || "http://127.0.0.1:8000";
  grid.replaceChildren();

  const backend = data.backend || {};
  grid.append(card(
    "Backend",
    backend.online ? "ok" : "err",
    backend.online ? ("v" + (backend.version || "?")) : "离线",
    [
      backend.online
        ? (backend.worker_alive === null
            ? "worker: 需重启后端启用 /status"
            : "worker: " + (backend.worker_alive ? "alive" : "stopped") + " · queue: " + (backend.queue_size ?? "-"))
        : "启动 start_all.bat 或 uvicorn 后刷新",
      backend.online
        ? ("jobs: " + (Object.entries(backend.jobs || {}).map(([k, v]) => k + " " + v).join(", ") || "暂无任务"))
        : (backend.error || ""),
    ],
    [{ label: "打开分析页", href: base + "/web/analysis.html" }]
  ));

  const web = data.local_web || {};
  grid.append(card(
    "本地前端",
    web.online ? "ok" : "err",
    web.online ? ("HTTP " + web.status) : "不可达",
    [
      web.seconds !== null && web.seconds !== undefined ? "延迟 " + web.seconds + "s" : (web.error || ""),
      base + "/web/analysis.html",
    ],
    [{ label: "打开", href: base + "/web/analysis.html" }]
  ));

  const tunnel = data.tunnel || {};
  const tunnelTone = tunnel.running ? (tunnel.listening ? "ok" : "warn") : "err";
  grid.append(card(
    "Cloudflare Tunnel",
    tunnelTone,
    tunnel.running ? (tunnel.listening ? "运行中" : "运行中，端口未监听") : "未运行",
    [
      tunnel.profile ? (tunnel.profile + " → " + (tunnel.hostname || "-") + " → localhost:" + (tunnel.port || "-")) : "未配置 profile",
      tunnel.running ? "PID " + (tunnel.pids || []).join(", ") : (tunnel.error || ""),
      tunnel.cloudflared ? "cloudflared 已安装" : "cloudflared 未找到",
    ],
    [
      ...(tunnel.public_url ? [{ label: "打开公网地址", href: tunnel.public_url }] : []),
      { label: "公网自检", onClick: testTunnel },
    ]
  ));

  const pages = data.pages || {};
  grid.append(card(
    "Cloudflare Pages",
    pages.configured ? (pages.online ? "ok" : "warn") : "warn",
    pages.configured ? (pages.online ? "在线" : "不可达") : "未配置",
    [
      pages.url || "在 tools/deploy.json 配置 pages_url",
      pages.seconds !== null && pages.seconds !== undefined ? "HTTP " + pages.status + " · " + pages.seconds + "s" : (pages.error || ""),
    ],
    pages.url ? [{ label: "打开 Pages", href: pages.url }] : []
  ));

  const llm = data.llm || {};
  const llmTone = llm.enabled ? (llm.online && llm.model_present ? "ok" : "warn") : "warn";
  grid.append(card(
    "LLM",
    llmTone,
    llm.enabled ? (llm.model || "已启用") : "规则模式",
    [
      llm.enabled ? ("Ollama " + (llm.online ? "在线" : "离线") + " · 模型 " + (llm.model_present ? "存在" : "未找到") + (llm.loaded ? " · 已加载" : "")) : "不确定评论会被排除",
      llm.model || "",
    ],
    [{ label: "配置 LLM", onClick: () => document.getElementById("settings-panel").scrollIntoView({ behavior: "smooth" }) }]
  ));

  const db = data.db || {};
  const counts = db.counts || {};
  grid.append(card(
    "数据库 / 任务",
    db.available ? "ok" : "err",
    db.available ? ((counts.done || 0) + " done") : "无数据库",
    [
      db.available ? "schema v" + (db.schema ?? "?") + " · " + fmtBytes(db.size) : (db.error || ""),
      Object.entries(counts).map(([k, v]) => k + " " + v).join(" · ") || "暂无任务",
    ]
  ));

  const git = data.git || {};
  grid.append(card(
    "Git",
    git.error ? "warn" : (git.dirty ? "warn" : "ok"),
    git.branch || "unknown",
    [
      "commit " + (git.commit || "-"),
      git.dirty ? "工作区有未提交改动" : "工作区干净",
    ]
  ));

  $("last-refresh").textContent = "刷新 " + new Date().toLocaleTimeString();
}

async function loadStatus() {
  if (statusBusy || !token) return;
  statusBusy = true;
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    toast(error.message, true);
  } finally {
    statusBusy = false;
  }
}

async function loadSettings() {
  try {
    const data = await api("/api/settings");
    $("llm-enabled").checked = Boolean(data.llm_enabled);
    $("llm-model").value = data.llm_model || "";
    $("llm-url").value = data.llm_url || "";
    $("settings-source").textContent = data.source === "backend"
      ? "来源：运行中的后端"
      : "来源：本地 settings 文件（" + (data.backend_error || "后端离线") + "）";
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadModels() {
  try {
    const data = await api("/api/llm/models");
    const list = $("model-list");
    list.replaceChildren();
    for (const model of data.models || []) {
      const option = document.createElement("option");
      option.value = model.name;
      option.label = [model.parameter_size, model.quantization].filter(Boolean).join(" · ");
      list.append(option);
    }
  } catch {
    /* the test button still gives a useful error */
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const button = event.submitter || event.target.querySelector("button[type=submit]");
  if (button) button.disabled = true;
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_enabled: $("llm-enabled").checked,
        llm_model: $("llm-model").value.trim(),
        llm_url: $("llm-url").value.trim(),
      }),
    });
    toast("LLM 设置已保存，对新任务生效。");
    await loadSettings();
    await loadStatus();
    await loadModels();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function testLLM() {
  const button = $("test-llm");
  const result = $("llm-result");
  button.disabled = true;
  result.hidden = false;
  result.textContent = "测试中...";
  try {
    const data = await api("/api/llm/test", { method: "POST" });
    result.textContent = JSON.stringify(data, null, 2);
    toast("模型测试通过，耗时 " + data.seconds + "s。");
  } catch (error) {
    result.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function testTunnel() {
  try {
    const data = await api("/api/tunnel/test", { method: "POST" });
    toast(data.ok ? ("公网 HTTP " + data.status + " · " + data.seconds + "s") : (data.error || "公网自检失败"), !data.ok);
    await loadStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadJobErrors() {
  try {
    const data = await api("/api/job-errors");
    const wrap = $("job-errors");
    wrap.replaceChildren();
    if (!data.items.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "暂无失败或待人工处理的任务。";
      wrap.append(p);
      return;
    }
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const label of ["时间", "餐厅", "状态", "错误"]) {
      const th = document.createElement("th");
      th.textContent = label;
      head.append(th);
    }
    table.append(head);
    for (const item of data.items) {
      const tr = document.createElement("tr");
      [item.finished_at || "", item.store || "", item.state || "", item.error || ""].forEach((value, index) => {
        const td = document.createElement("td");
        td.textContent = value;
        if (index === 3) td.className = "err-text";
        tr.append(td);
      });
      table.append(tr);
    }
    wrap.append(table);
  } catch (error) {
    toast(error.message, true);
  }
}

function logLineClass(line) {
  if (/\b(ERROR|CRITICAL)\b/.test(line) || line.includes("Traceback")) return "log-error";
  if (/\b(WARNING|WARN)\b/.test(line)) return "log-warn";
  return "";
}

function renderLog() {
  const output = $("log-output");
  const meta = $("log-meta");
  if (!logPayload) {
    output.textContent = "加载中...";
    return;
  }
  const filter = $("log-filter").value.trim().toLowerCase();
  const lines = (logPayload.lines || []).filter(line => !filter || line.toLowerCase().includes(filter));
  meta.textContent = (logPayload.available ? logPayload.path : "暂无日志文件") +
    (logPayload.modified ? " · " + logPayload.modified : "") +
    " · " + fmtBytes(logPayload.size);
  if (!logPayload.available) {
    const path = logSource === "tunnel" ? "tools/tunnel.log" : "var/logs/" + logSource + ".log";
    output.textContent = "还没有日志。用 start_all.bat 启动后端后，日志会写到 " + path + "。";
    return;
  }
  const previousTop = output.scrollTop;
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
  output.replaceChildren();
  if (!lines.length) {
    output.textContent = filter ? "没有匹配的行。" : "日志为空。";
    return;
  }
  for (const line of lines) {
    const span = document.createElement("span");
    const cls = logLineClass(line);
    if (cls) span.className = cls;
    span.textContent = line + "\n";
    output.append(span);
  }
  output.scrollTop = nearBottom ? output.scrollHeight : previousTop;
}

async function loadLogs() {
  if (logBusy || !token) return;
  logBusy = true;
  try {
    logPayload = await api("/api/logs?source=" + encodeURIComponent(logSource) + "&lines=" + logLines);
    renderLog();
  } catch (error) {
    toast(error.message, true);
  } finally {
    logBusy = false;
  }
}

function logText() {
  const filter = $("log-filter").value.trim().toLowerCase();
  return (logPayload?.lines || []).filter(line => !filter || line.toLowerCase().includes(filter)).join("\n");
}

async function copyLog() {
  try {
    await navigator.clipboard.writeText(logText());
    toast("日志已复制。");
  } catch {
    toast("浏览器不允许复制，请手动选择。", true);
  }
}

function downloadLog() {
  const blob = new Blob([logText()], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "dinelens-" + logSource + ".log";
  a.click();
  URL.revokeObjectURL(url);
}

function initTimers() {
  clearTimers();
  statusTimer = setInterval(loadStatus, 5000);
  logTimer = setInterval(() => {
    if (logAuto && !document.hidden) loadLogs();
  }, 2000);
}

async function start() {
  if (!token) {
    showToken();
    return;
  }
  $("token-modal").hidden = true;
  $("logout").hidden = false;
  await Promise.allSettled([loadStatus(), loadSettings(), loadModels(), loadLogs(), loadJobErrors()]);
  if (!token) {
    $("logout").hidden = true;
    return;
  }
  initTimers();
}

$("token-form").addEventListener("submit", async event => {
  event.preventDefault();
  const value = $("token-input").value.trim();
  if (!value) return;
  token = value;
  sessionStorage.setItem("dinelens-admin-token", token);
  $("token-input").value = "";
  await start();
});

$("llm-form").addEventListener("submit", saveSettings);
$("test-llm").addEventListener("click", testLLM);
$("refresh").addEventListener("click", () => { loadStatus(); loadLogs(); loadJobErrors(); });
$("refresh-errors").addEventListener("click", loadJobErrors);
$("logout").addEventListener("click", () => {
  clearTimers();
  token = "";
  sessionStorage.removeItem("dinelens-admin-token");
  $("logout").hidden = true;
  showToken();
});

$("log-tabs").addEventListener("click", event => {
  const button = event.target.closest("button[data-source]");
  if (!button) return;
  logSource = button.dataset.source;
  for (const item of $("log-tabs").querySelectorAll("button")) {
    item.classList.toggle("active", item === button);
  }
  loadLogs();
});
$("log-lines").addEventListener("change", event => {
  logLines = Number(event.target.value);
  loadLogs();
});
$("log-auto").addEventListener("change", event => {
  logAuto = event.target.checked;
});
$("log-filter").addEventListener("input", renderLog);
$("log-copy").addEventListener("click", copyLog);
$("log-download").addEventListener("click", downloadLog);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    loadStatus();
    if (logAuto) loadLogs();
  }
});

start();
