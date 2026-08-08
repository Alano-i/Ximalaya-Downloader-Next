const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  view: "tasks",
  mode: "album",
  filter: "all",
  search: "",
  settings: null,
  login: null,
  tasks: [],
  counts: {},
  page: {
    offset: 0,
    limit: 100,
    total: 0,
    has_previous: false,
    has_next: false,
  },
  operation: null,
  taskError: null,
  taskRenderKey: "",
  operationRenderKey: "",
  riskLoaded: false,
  settingsPopulated: false,
  handledTerminals: new Set(),
};

const polling = {
  timer: null,
  running: false,
  searchTimer: null,
  taskRequest: 0,
  lastTaskRefreshAt: 0,
};

const ACTIVE_OPERATION_POLL_MS = 850;
const IDLE_OPERATION_POLL_MS = 4000;
const ACTIVE_TASK_POLL_MS = 1500;
const IDLE_TASK_POLL_MS = 10000;
let riskReportPromise = null;

const terminalStatuses = new Set(["succeeded", "failed", "stopped"]);
const operationLabels = {
  login: "登录",
  download_track: "单曲下载",
  download_album: "专辑下载",
  resume: "恢复任务",
  formats: "音质探测",
  inspect_storage: "浏览器存储检查",
  gen_sign: "签名冒烟",
  extract_device: "设备信息采集",
  refresh_cookies: "登录凭据刷新",
};

async function api(path, options = {}) {
  const init = {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  const response = await fetch(path, init);
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (!response.ok) {
    const error = new Error(payload.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    error.category = payload.category;
    throw error;
  }
  return payload;
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " is-error" : ""}`;
  node.textContent = message;
  $("#toast-region").append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function switchView(view) {
  state.view = view;
  $$('[data-view-panel]').forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
  });
  $$('.nav-item[data-view]').forEach((item) => {
    item.classList.toggle("is-active", item.dataset.view === view);
  });
  if (view === "diagnostics" && !state.riskLoaded) loadRiskReport();
  if (view === "tasks" && Date.now() - polling.lastTaskRefreshAt > IDLE_TASK_POLL_MS) {
    loadTaskPage();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function focusComposer() {
  switchView("tasks");
  window.setTimeout(() => {
    $("#download-target").focus();
    $("#download-form").scrollIntoView({ block: "center", behavior: "smooth" });
  }, 80);
}

function setDownloadMode(mode) {
  state.mode = mode;
  $$('[data-mode]').forEach((button) => {
    button.classList.toggle("is-active", button.dataset.mode === mode);
  });
  const range = $("#download-range");
  const hint = $("#composer-hint");
  const rangeField = $(".range-field");
  if (mode === "track") {
    range.value = "";
    range.disabled = true;
    rangeField.classList.add("is-hidden");
    hint.textContent = "单曲下载会按所选音质自动回退到可用格式。";
  } else {
    range.disabled = false;
    rangeField.classList.remove("is-hidden");
    hint.textContent = "区间支持 1-20、5-、-10 或单集序号。";
  }
}

function renderHeader() {
  const loginButton = $("#login-status");
  const loginText = $("#login-status-text");
  // 登录态按浏览器分家，所以状态里必须带上"是哪个浏览器"，否则用户切换浏览器后
  // 只会看到莫名其妙的"尚未登录"。
  const browserName = state.login?.browser_name || "";
  const prefix = browserName ? `${browserName} · ` : "";
  const other = state.login?.other_browser_authenticated;
  const otherName = other === "edge" ? "Edge" : other === "chrome" ? "Chrome" : "";
  if (state.login?.authenticated) {
    loginText.textContent = `${prefix}已保存登录态`;
    loginButton.classList.remove("is-warning");
    loginButton.title = "点击重新登录";
  } else if (otherName) {
    loginText.textContent = `${prefix}尚未登录`;
    loginButton.classList.add("is-warning");
    loginButton.title = `${otherName} 中已有登录态并已完整保留，可在设置里切回；`
      + `或点击在${browserName || "当前浏览器"}中登录。`;
  } else if (state.login?.profile_exists) {
    loginText.textContent = `${prefix}凭据未缓存`;
    loginButton.classList.add("is-warning");
    loginButton.title = "点击登录或刷新凭据";
  } else {
    loginText.textContent = `${prefix}尚未登录`;
    loginButton.classList.add("is-warning");
    loginButton.title = "点击打开浏览器登录";
  }
  const backend = state.settings?.source_backend || "http";
  $("#backend-status").textContent =
    backend === "pc" ? "PC 桌面端接口" : backend === "http" ? "HTTP 后端" : "浏览器后端";
  $("#concurrency-status").textContent = `并发 ${state.settings?.max_concurrency ?? 1}`;
  if (state.settings?.default_quality) {
    $("#download-quality").value = state.settings.default_quality;
  }
}

function renderCounts() {
  for (const key of ["all", "pending", "downloading", "done", "failed"]) {
    $(`#count-${key}`).textContent = state.counts?.[key] ?? 0;
    const button = $(`[data-filter="${key}"]`);
    button?.setAttribute("aria-pressed", String(state.filter === key));
  }
}

function applyTaskPayload(payload, { force = false } = {}) {
  const renderKey = JSON.stringify([
    payload.tasks || [], payload.counts || {}, payload.page || {}, payload.error || null,
  ]);
  state.tasks = payload.tasks || [];
  state.counts = payload.counts || {};
  state.page = { ...state.page, ...(payload.page || {}) };
  state.taskError = payload.error || null;
  if (force || renderKey !== state.taskRenderKey) {
    state.taskRenderKey = renderKey;
    renderTasks();
  }
}

function renderTasks() {
  renderCounts();
  const list = $("#task-list");
  const empty = $("#task-empty");
  const tasks = state.tasks;
  list.innerHTML = tasks.map(taskRow).join("");
  empty.classList.toggle("is-hidden", tasks.length > 0);
  if (tasks.length === 0) {
    const heading = $("#task-empty h2");
    const copy = $("#task-empty p");
    const hasAny = Number(state.counts?.all || 0) > 0;
    heading.textContent = hasAny ? "没有符合条件的任务" : "还没有下载任务";
    copy.textContent = hasAny
      ? "切换筛选条件，或尝试搜索其他曲目和 ID。"
      : "粘贴专辑或曲目链接，第一条任务会出现在这里。";
  }
  renderTaskError();
  renderPagination();
}

function renderTaskError() {
  const taskError = $("#task-error");
  taskError.classList.toggle("is-hidden", !state.taskError);
  taskError.textContent = state.taskError ? `任务库暂时不可用：${state.taskError}` : "";
}

function renderPagination() {
  const page = state.page;
  const total = Number(page.total || 0);
  const limit = Math.max(1, Number(page.limit || 100));
  const offset = Math.max(0, Number(page.offset || 0));
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const pageNumber = total ? Math.floor(offset / limit) + 1 : 1;
  const start = total ? offset + 1 : 0;
  const end = Math.min(offset + state.tasks.length, total);
  const globalTotal = Number(state.counts?.all || 0);
  const scope = globalTotal !== total ? `，任务库共 ${globalTotal} 条` : "";
  $("#task-page-summary").textContent = total
    ? `显示 ${start}–${end}，当前筛选共 ${total} 条${scope}`
    : `当前筛选没有任务${scope}`;
  $("#task-page-number").textContent = `第 ${pageNumber} / ${pageCount} 页`;
  $("#task-page-previous").disabled = !page.has_previous;
  $("#task-page-next").disabled = !page.has_next;
}

function taskRow(task) {
  const labels = {
    downloading: "进行中",
    pending: "待恢复",
    done: "已完成",
    failed: "失败",
  };
  const episode = task.album_index > 0 ? `第 ${String(task.album_index).padStart(2, "0")} 集` : "单曲";
  const parent = task.album_id ? `专辑 ${task.album_id}` : "独立曲目";
  const error = task.last_error_msg
    ? `<span class="task-error-copy" title="${escapeHtml(task.last_error_msg)}">${escapeHtml(task.last_error_msg)}</span>`
    : "";
  return `
    <tr data-task-id="${task.id ?? ""}">
      <td>
        <div class="task-title">
          <strong title="${escapeHtml(task.title)}">${escapeHtml(task.title)}</strong>
          <span>${escapeHtml(parent)} · ${episode} · ID ${escapeHtml(task.track_id)}</span>
          ${error}
        </div>
      </td>
      <td><span class="state-badge state-${task.state}">${labels[task.state] || task.state}</span></td>
      <td><span class="format-data">${qualityLabel(task.quality)}</span></td>
      <td><span class="size-data">${formatBytes(task.total_bytes)}</span></td>
      <td>
        <div class="progress-cell">
          <span class="progress-value">${task.progress}%</span>
          ${chapterTicks(task.progress, task.state)}
        </div>
      </td>
      <td><button class="row-action" type="button" data-open-task="${task.id}">打开目录</button></td>
    </tr>`;
}

function chapterTicks(progress, taskState) {
  const total = 20;
  const filled = taskState === "done" ? total : Math.floor((progress / 100) * total);
  const ticks = [];
  for (let index = 0; index < total; index += 1) {
    let klass = "chapter-tick";
    if (index < filled) klass += " is-filled";
    if (taskState === "downloading" && index === filled && filled < total) klass += " is-current";
    ticks.push(`<span class="${klass}"></span>`);
  }
  return `<div class="chapter-progress" role="progressbar" aria-label="下载进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}">${ticks.join("")}</div>`;
}

function qualityLabel(value) {
  return { high: "高", standard: "标准", low: "低" }[value] || value || "—";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function applyOperation(operation, { force = false } = {}) {
  const renderKey = JSON.stringify(operation || null);
  state.operation = operation;
  if (force || renderKey !== state.operationRenderKey) {
    state.operationRenderKey = renderKey;
    renderOperation();
  }
}

function renderOperation() {
  const panel = $("#operation-strip");
  const operation = state.operation;
  if (!operation) {
    panel.classList.add("is-hidden");
    document.title = "XDL · 下载任务";
    return;
  }
  panel.classList.remove("is-hidden");
  panel.dataset.status = operation.status;
  const statusLabels = {
    running: "进行中",
    succeeded: "已完成",
    failed: "失败",
    stopped: "已停止",
  };
  $("#operation-state").textContent = statusLabels[operation.status] || operation.status;
  $("#operation-title").textContent = operation.current_title || operation.label || operationLabels[operation.kind] || "后台操作";
  $("#operation-message").textContent = operation.message || operationLabels[operation.kind] || "正在准备";
  const total = Number(operation.progress_total || 0);
  const done = Number(operation.progress_done || 0);
  const percent = total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
  const progress = $("#operation-progress");
  progress.classList.toggle("is-indeterminate", operation.status === "running" && total <= 0);
  $("#operation-progress span").style.width = total > 0 ? `${percent}%` : "";
  progress.setAttribute("aria-valuenow", String(percent));
  const stop = $("#stop-button");
  stop.classList.toggle("is-hidden", operation.status !== "running" || !operation.cancellable);
  stop.disabled = Boolean(operation.stop_requested);
  stop.textContent = operation.stop_requested ? "正在停止" : "优雅停止";
  $("#operation-notes").innerHTML = (operation.notes || [])
    .slice().reverse().map((note) => `<li>${escapeHtml(note.message)}</li>`).join("");
  document.title = operation.status === "running"
    ? `● ${operation.label} · XDL`
    : "XDL · 下载任务";
  handleOperationTerminal(operation);
}

function handleOperationTerminal(operation) {
  if (!terminalStatuses.has(operation.status)) return;
  const key = `${operation.id}:${operation.status}`;
  if (state.handledTerminals.has(key)) return;
  state.handledTerminals.add(key);
  if (operation.status === "failed") {
    toast(operation.message || `${operation.label}失败`, "error");
  } else if (operation.status === "stopped") {
    toast("任务已停止，进度已保留");
  } else {
    toast(`${operation.label}已完成`);
  }
  if (shouldShowResult(operation)) {
    showOperationResult(operation);
  } else if (operation.has_result) {
    loadOperationResult(operation.id);
  }
  if (["login", "refresh_cookies"].includes(operation.kind) && operation.status === "succeeded") {
    window.setTimeout(() => loadBootstrap(false), 100);
  }
  if (["download_track", "download_album", "resume"].includes(operation.kind)) {
    window.setTimeout(() => loadTaskPage(), 100);
  }
}

async function loadOperationResult(operationId) {
  try {
    const payload = await api("/api/operation?include_result=true");
    const operation = payload.operation;
    if (!operation || operation.id !== operationId) return;
    if (shouldShowResult(operation)) showOperationResult(operation);
  } catch (error) {
    console.warn("载入操作结果失败", error);
  }
}

function shouldShowResult(operation) {
  if (!operation.result) return false;
  if (["formats", "inspect_storage", "gen_sign", "extract_device", "refresh_cookies"].includes(operation.kind)) return true;
  if (operation.kind === "download_album") {
    const result = operation.result.album;
    return Boolean(result?.failed?.length || result?.risk_control || result?.incomplete);
  }
  if (operation.kind === "resume") {
    return operation.result.albums?.some((item) => item.failed?.length || item.risk_control || item.incomplete);
  }
  return false;
}

function showOperationResult(operation) {
  $("#dialog-tag").textContent = operationLabels[operation.kind] || "操作结果";
  $("#dialog-title").textContent = operation.status === "failed" ? "操作失败" : "操作结果";
  const content = $("#dialog-content");
  content.innerHTML = resultMarkup(operation);
  const dialog = $("#result-dialog");
  if (!dialog.open) dialog.showModal();
}

function resultMarkup(operation) {
  const result = operation.result || {};
  if (operation.kind === "formats") {
    const rows = (result.formats || []).map((format) => `
      <tr><td>${escapeHtml(format.type)}</td><td>${escapeHtml(format.codec)}</td><td>${format.bitrate || "—"}k</td><td>${formatBytes(format.file_size)}</td></tr>`).join("");
    return `<p class="result-summary"><strong>${escapeHtml(result.title || "曲目")}</strong><br>ID ${escapeHtml(result.track_id || "—")} · 共 ${(result.formats || []).length} 种可用格式</p>
      <table class="result-table"><thead><tr><th>格式</th><th>编码</th><th>码率</th><th>大小</th></tr></thead><tbody>${rows || '<tr><td colspan="4">没有可用格式</td></tr>'}</tbody></table>`;
  }
  if (operation.kind === "gen_sign") {
    return `<p class="result-summary">已生成 ${result.repeat || 0} 个签名值。签名仅用于检查本地链路。</p><code class="result-code">${(result.values || []).map(escapeHtml).join("\n\n")}</code>`;
  }
  if (operation.kind === "extract_device") {
    return `<p class="result-summary">${escapeHtml(result.summary || "采集完成")}</p><dl class="definition-list"><div><dt>字段</dt><dd>${result.field_count ?? "—"}</dd></div><div><dt>短指纹</dt><dd>${escapeHtml(result.identity || "—")}</dd></div></dl><p><code>${escapeHtml(result.output_path || "")}</code></p>`;
  }
  if (operation.kind === "refresh_cookies") {
    return `<p class="result-summary">已验证登录 token，并保存 ${result.cookie_count || 0} 个 Cookie。匿名结果不会覆盖缓存。</p><p><code>${escapeHtml(result.output_path || "")}</code></p>`;
  }
  if (operation.kind === "download_album") return albumMarkup(result.album);
  if (operation.kind === "resume") return (result.albums || []).map(albumMarkup).join("");
  return `<pre class="json-output">${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
}

function albumMarkup(album = {}) {
  const failures = (album.failed || []).map((item) => `<li>第 ${item.index} 集 ${escapeHtml(item.title)}：${escapeHtml(item.error)}</li>`).join("");
  return `<p class="result-summary">${escapeHtml(album.summary || "任务已完成")}</p>${failures ? `<h3>失败明细</h3><ul>${failures}</ul>` : ""}`;
}

async function startOperation(path, body = null) {
  try {
    const options = { method: "POST" };
    if (body !== null) options.body = JSON.stringify(body);
    applyOperation(await api(path, options), { force: true });
    toast(`${state.operation.label || "操作"}已开始`);
    scheduleRuntimeRefresh(100);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function stopOperation() {
  try {
    applyOperation(await api("/api/operations/stop", { method: "POST" }), { force: true });
    scheduleRuntimeRefresh(100);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openDownloads(taskId = null) {
  try {
    const result = await api("/api/open-downloads", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId }),
    });
    toast(`已打开 ${result.path}`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadBootstrap(populateSettings = true) {
  try {
    const payload = await api("/api/bootstrap");
    state.settings = payload.settings;
    state.login = payload.login;
    renderHeader();
    applyOperation(payload.operation, { force: true });
    if (populateSettings || !state.settingsPopulated) {
      applyTaskPayload({
        tasks: payload.tasks,
        counts: payload.counts,
        page: payload.page,
        error: payload.task_error,
      }, { force: true });
      polling.lastTaskRefreshAt = Date.now();
    }
    if (populateSettings || !state.settingsPopulated) populateSettingsForm();
  } catch (error) {
    toast(`无法载入 WebUI：${error.message}`, "error");
  }
}

function taskQueryPath() {
  const params = new URLSearchParams({
    limit: String(state.page.limit || 100),
    offset: String(state.page.offset || 0),
  });
  if (state.filter !== "all") params.set("state", state.filter);
  if (state.search.trim()) params.set("search", state.search.trim().slice(0, 200));
  return `/api/tasks?${params}`;
}

async function loadTaskPage() {
  const request = ++polling.taskRequest;
  $("#task-table-wrap").setAttribute("aria-busy", "true");
  try {
    const payload = await api(taskQueryPath());
    if (request !== polling.taskRequest) return;
    applyTaskPayload(payload);
    polling.lastTaskRefreshAt = Date.now();
  } catch (error) {
    if (request !== polling.taskRequest) return;
    state.taskError = error.message;
    renderTaskError();
    $("#task-page-summary").textContent = "刷新失败，已保留当前任务列表";
  } finally {
    if (request === polling.taskRequest) {
      $("#task-table-wrap").setAttribute("aria-busy", "false");
    }
  }
}

async function loadOperationSnapshot() {
  try {
    const payload = await api("/api/operation");
    applyOperation(payload.operation);
    return true;
  } catch (error) {
    if (!document.hidden) console.warn("刷新运行状态失败", error);
    return false;
  }
}

function scheduleRuntimeRefresh(delay = null) {
  window.clearTimeout(polling.timer);
  polling.timer = null;
  if (document.hidden) return;
  const running = state.operation?.status === "running";
  polling.timer = window.setTimeout(
    refreshRuntime,
    delay ?? (running ? ACTIVE_OPERATION_POLL_MS : IDLE_OPERATION_POLL_MS),
  );
}

async function refreshRuntime({ forceTasks = false } = {}) {
  if (document.hidden || polling.running) return;
  polling.running = true;
  const wasRunning = state.operation?.status === "running";
  try {
    await loadOperationSnapshot();
    const isRunning = state.operation?.status === "running";
    const taskInterval = isRunning ? ACTIVE_TASK_POLL_MS : IDLE_TASK_POLL_MS;
    const taskDue = Date.now() - polling.lastTaskRefreshAt >= taskInterval;
    if (forceTasks || wasRunning !== isRunning || taskDue) {
      await loadTaskPage();
    }
  } finally {
    polling.running = false;
    scheduleRuntimeRefresh();
  }
}

async function loadRiskReport(force = false) {
  if (state.riskLoaded && !force) return;
  if (riskReportPromise) return riskReportPromise;
  riskReportPromise = (async () => {
    try {
      const payload = await api("/api/risk-report");
      renderRiskReport(payload);
      state.riskLoaded = true;
    } catch (error) {
      toast(error.message, "error");
    } finally {
      riskReportPromise = null;
    }
  })();
  return riskReportPromise;
}

function renderRiskReport(payload) {
  const summary = payload.summary || {};
  $("#risk-path").textContent = payload.path || "未配置风控日志";
  $("#risk-total").textContent = summary.total ?? 0;
  $("#risk-first").textContent = summary.first_risk_request_index ?? "—";
  $("#risk-inflight").textContent = summary.max_in_flight ?? 0;
  $("#risk-rate").textContent = summary.requests_per_minute ?? 0;
  const outcomes = Object.entries(summary.outcomes || {});
  $("#risk-outcomes").innerHTML = outcomes.length
    ? outcomes.map(([name, count]) => `<span class="distribution-item">${escapeHtml(outcomeLabel(name))}<strong>${count}</strong></span>`).join("")
    : '<span class="distribution-item">暂无观测数据</span>';
  const latency = summary.latency_ms || {};
  $("#risk-latency").innerHTML = ["min", "p50", "p95", "max"].map((key) => `
    <div><dt>${key}</dt><dd>${latency[key] ?? "—"}${latency[key] == null ? "" : " ms"}</dd></div>`).join("");
}

function outcomeLabel(value) {
  return { success: "成功", risk_control: "风控", auth: "鉴权", network: "网络", unknown: "未知" }[value] || value;
}

const BROWSER_NOTE_DEFAULT =
  "登录与设备采集所用的浏览器。每个浏览器的 Profile、登录凭据与设备指纹各自独立保存，"
  + "互不覆盖；自定义填写的路径保持不变。";

function renderBrowserNote() {
  const note = $("#browser-note");
  if (!note) return;
  const selected = $('#settings-form [name="browser"]')?.value;
  const saved = state.settings?.browser || "auto";
  if (!selected || selected === saved) {
    note.textContent = BROWSER_NOTE_DEFAULT;
    note.classList.remove("is-warning");
    return;
  }
  // 切换是换一整套身份，需要重新登录一次——但旧浏览器的登录态完整保留，
  // 切回即可恢复。把"可逆"讲清楚，用户才不会以为自己把登录弄丢了。
  const current = state.login?.browser_name || "当前浏览器";
  const target = { auto: "自动选择的浏览器", chrome: "Chrome", edge: "Edge" }[selected]
    || selected;
  note.textContent = `保存后将切换到 ${target}，需要在其中重新登录一次；`
    + `${current} 的登录态与设备指纹会完整保留，切回即可恢复。`;
  note.classList.add("is-warning");
}

function populateSettingsForm() {
  if (!state.settings) return;
  const form = $("#settings-form");
  $$('[name]', form).forEach((input) => {
    const value = state.settings[input.name];
    if (value === undefined || value === null) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = String(value);
  });
  renderBrowserNote();
  state.settingsPopulated = true;
}

function settingsPayload() {
  const payload = {};
  $$('[name]', $("#settings-form")).forEach((input) => {
    if (input.type === "checkbox") {
      payload[input.name] = input.checked;
    } else if (input.type === "number") {
      payload[input.name] = input.value === "" ? null : Number(input.value);
    } else {
      payload[input.name] = input.value;
    }
  });
  return payload;
}

async function saveSettings() {
  try {
    const result = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(settingsPayload()),
    });
    state.settings = result.settings;
    renderHeader();
    populateSettingsForm();
    toast("设置已保存，运行器已重新加载");
    window.setTimeout(() => refreshRuntime({ forceTasks: true }), 100);
  } catch (error) {
    toast(error.message, "error");
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function changeTaskPage(direction) {
  const step = Number(state.page.limit || 100);
  if (direction < 0 && !state.page.has_previous) return;
  if (direction > 0 && !state.page.has_next) return;
  state.page.offset = Math.max(0, Number(state.page.offset || 0) + direction * step);
  loadTaskPage();
  $("#task-table-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
}

document.addEventListener("click", (event) => {
  const view = event.target.closest("[data-view]");
  if (view) switchView(view.dataset.view);

  const mode = event.target.closest("[data-mode]");
  if (mode) setDownloadMode(mode.dataset.mode);

  const filter = event.target.closest("[data-filter]");
  if (filter) {
    state.filter = filter.dataset.filter;
    state.page.offset = 0;
    $$('[data-filter]').forEach((button) => button.classList.toggle("is-active", button === filter));
    renderCounts();
    loadTaskPage();
  }

  const taskButton = event.target.closest("[data-open-task]");
  if (taskButton) openDownloads(Number(taskButton.dataset.openTask));

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  const actions = {
    "focus-composer": focusComposer,
    login: () => startOperation("/api/operations/login"),
    resume: () => startOperation("/api/operations/resume"),
    stop: stopOperation,
    "open-downloads": () => openDownloads(),
    "refresh-risk": () => loadRiskReport(true),
    "tasks-previous": () => changeTaskPage(-1),
    "tasks-next": () => changeTaskPage(1),
    "refresh-cookies": () => startOperation("/api/operations/refresh-cookies", {
      headless: !$("#cookies-visible").checked,
    }),
    "inspect-storage": () => startOperation("/api/operations/inspect-storage"),
  };
  actions[action]?.();
});

$("#download-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startOperation("/api/operations/download", {
    mode: state.mode,
    target: $("#download-target").value.trim(),
    quality: $("#download-quality").value,
    range: state.mode === "album" ? $("#download-range").value.trim() || null : null,
  });
});

$("#formats-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startOperation("/api/operations/formats", {
    target: $("#formats-target").value.trim(),
  });
});

$("#sign-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startOperation("/api/operations/gen-sign", {
    repeat: Number($("#sign-repeat").value || 1),
  });
});

$("#extract-form").addEventListener("submit", (event) => {
  event.preventDefault();
  startOperation("/api/operations/extract-device", {
    output: $("#extract-output").value.trim() || null,
    profile: $("#extract-profile").value.trim() || null,
    headless: !$("#extract-visible").checked,
    refresh: $("#extract-refresh").checked,
    fresh_profile: $("#extract-fresh").checked,
  });
});

$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveSettings();
});

$("#settings-form").addEventListener("change", (event) => {
  if (event.target?.name === "browser") renderBrowserNote();
});

$("#task-search").addEventListener("input", (event) => {
  state.search = event.target.value;
  state.page.offset = 0;
  window.clearTimeout(polling.searchTimer);
  polling.searchTimer = window.setTimeout(loadTaskPage, 300);
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    window.clearTimeout(polling.timer);
    polling.timer = null;
  } else {
    refreshRuntime({ forceTasks: true });
  }
});

setDownloadMode("album");
await loadBootstrap();
scheduleRuntimeRefresh(500);
