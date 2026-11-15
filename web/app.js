const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  settings: null,
  mcp: null,
  busy: false,
  streaming: false,
  currentAssistant: null,
  currentAssistantRaw: "",
  renderTimer: null,
  controller: null,
  lastUsage: null,
};

const els = {
  messages: $("#messages"),
  welcome: $("#welcome"),
  input: $("#input"),
  send: $("#btn-send"),
  stop: $("#btn-stop"),
  mcpDot: $("#mcp-dot"),
  mcpLabel: $("#mcp-label"),
  footModel: $("#foot-model"),
  footTokens: $("#foot-tokens"),
  settings: $("#settings"),
  oauthModal: $("#oauth-modal"),
  lightbox: $("#lightbox"),
  lightboxImg: $("#lightbox-img"),
};

marked.setOptions({ gfm: true, breaks: true });

function md(text) {
  try {
    return DOMPurify.sanitize(marked.parse(text || ""));
  } catch {
    return escapeHtml(text || "");
  }
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function nearBottom() {
  const el = els.messages;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function scrollToBottom(force = false) {
  if (force || nearBottom()) {
    els.messages.scrollTop = els.messages.scrollHeight;
  }
}

function hideWelcome() {
  if (els.welcome && els.welcome.parentNode) els.welcome.remove();
}

function showWelcome() {
  if (!els.welcome) return;
  els.messages.innerHTML = "";
  els.messages.appendChild(els.welcome);
}

/* ------------------------------------------------------------------ messages */

function addUserMessage(text) {
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  els.messages.appendChild(wrap);
  scrollToBottom(true);
}

function ensureAssistant() {
  if (state.currentAssistant) return state.currentAssistant;
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  const content = document.createElement("div");
  content.className = "content cursor";
  wrap.appendChild(content);
  els.messages.appendChild(wrap);
  state.currentAssistant = content;
  state.currentAssistantRaw = "";
  scrollToBottom(true);
  return content;
}

function appendAssistant(text) {
  ensureAssistant();
  state.currentAssistantRaw += text;
  if (!state.renderTimer) {
    state.renderTimer = setTimeout(() => {
      state.renderTimer = null;
      if (!state.currentAssistant) return;
      state.currentAssistant.innerHTML = md(state.currentAssistantRaw);
      scrollToBottom();
    }, 70);
  }
}

function finalizeAssistant() {
  if (!state.currentAssistant) return;
  if (state.renderTimer) {
    clearTimeout(state.renderTimer);
    state.renderTimer = null;
  }
  const raw = state.currentAssistantRaw;
  const wrapper = state.currentAssistant.parentElement;
  state.currentAssistant.classList.remove("cursor");
  if (!raw.trim()) {
    if (wrapper) wrapper.remove();
  } else {
    state.currentAssistant.innerHTML = md(raw);
  }
  state.currentAssistant = null;
  state.currentAssistantRaw = "";
}

function addError(text) {
  finalizeAssistant();
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg error";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  els.messages.appendChild(wrap);
  scrollToBottom(true);
}

function toolSummary(name, input) {
  const i = input || {};
  switch (name) {
    case "connect":
      return "подключение к запущенному КОМПАСу";
    case "create_part":
      return `документ «${i.name || "Part"}»`;
    case "create_sketch": {
      const spec = i.spec || {};
      const curves = Array.isArray(spec.curves) ? spec.curves.length : 0;
      return `плоскость ${spec.plane || "XY"}, кривых: ${curves}`;
    }
    case "extrude":
      return `глубина ${i.spec?.depth ?? "?"} мм, ${i.spec?.operation === "cut" ? "вырез" : "добавление"}`;
    case "revolve":
      return `угол ${i.spec?.angle ?? 360}°`;
    case "save_model":
      return i.path || "сохранение .m3d";
    case "model_preview":
      return "текущий вид";
    case "inspect_model":
      return "габариты и объём";
    case "api_invoke":
      return i.method || i.interface || "COM-вызов";
    default:
      return "";
  }
}

function toolGlyph(name) {
  const map = {
    connect: "⇄",
    create_part: "▣",
    create_sketch: "✎",
    extrude: "⬆",
    revolve: "◉",
    inspect_model: "◫",
    save_model: "⤓",
    model_preview: "◉",
    export_model: "⇥",
    api_invoke: "⚙",
  };
  return map[name] || "⚙";
}

function addToolCard(ev) {
  finalizeAssistant();
  hideWelcome();
  const card = document.createElement("div");
  card.className = "tool";
  card.dataset.id = ev.id;
  card.innerHTML = `
    <div class="tool-head">
      <div class="tool-icon">${escapeHtml(toolGlyph(ev.name))}</div>
      <div class="tool-meta">
        <div class="tool-title">${escapeHtml(ev.title || ev.name)}</div>
        <div class="tool-sub">${escapeHtml(toolSummary(ev.name, ev.input))}</div>
      </div>
      <div class="tool-state"><span class="spin"></span><span>работает</span></div>
    </div>
    <div class="tool-body">
      <details class="tool-params">
        <summary>Параметры</summary>
        <pre class="tool-input">${escapeHtml(JSON.stringify(ev.input || {}, null, 2))}</pre>
      </details>
      <div class="tool-out"></div>
      <div class="shots"></div>
    </div>`;
  const head = card.querySelector(".tool-head");
  head.addEventListener("click", () => card.classList.toggle("open"));
  els.messages.appendChild(card);
  scrollToBottom(true);
}

function updateTool(ev) {
  const card = els.messages.querySelector(`.tool[data-id="${CSS.escape(ev.id)}"]`);
  if (!card) return;
  const stateEl = card.querySelector(".tool-state");
  const out = card.querySelector(".tool-out");
  const shots = card.querySelector(".shots");
  const icon = card.querySelector(".tool-icon");
  if (ev.ok) {
    stateEl.innerHTML = `<span class="ok">✓</span><span>${(ev.duration_ms / 1000).toFixed(1)} с</span>`;
  } else {
    stateEl.innerHTML = `<span class="bad">✕</span><span>ошибка</span>`;
    icon.classList.add("err");
    card.classList.add("open");
  }
  if (ev.text) {
    out.textContent = ev.text;
    out.classList.toggle("tool-error", !ev.ok);
  }
  if (Array.isArray(ev.images) && ev.images.length) {
    shots.innerHTML = "";
    ev.images.forEach((src) => {
      const img = document.createElement("img");
      img.src = src;
      img.alt = "Скриншот модели";
      img.addEventListener("click", (e) => {
        e.stopPropagation();
        openLightbox(src);
      });
      shots.appendChild(img);
    });
    card.classList.add("open");
  }
  scrollToBottom();
}

function openLightbox(src) {
  els.lightboxImg.src = src;
  els.lightbox.classList.remove("hidden");
}

/* ------------------------------------------------------------------ history */

function renderHistory(items) {
  els.messages.innerHTML = "";
  if (!items || !items.length) {
    if (els.welcome) els.messages.appendChild(els.welcome);
    updateWelcome();
    return;
  }
  hideWelcome();
  items.forEach((item) => {
    if (item.role === "user") {
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = item.text;
      wrap.appendChild(bubble);
      els.messages.appendChild(wrap);
    } else if (item.role === "assistant") {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      const content = document.createElement("div");
      content.className = "content";
      content.innerHTML = md(item.text);
      wrap.appendChild(content);
      els.messages.appendChild(wrap);
    } else if (item.role === "tool") {
      addToolCard({ id: item.id, name: item.name, title: item.title, input: item.input });
      updateTool({
        id: item.id,
        ok: item.status === "ok",
        text: item.text,
        images: item.images,
        duration_ms: item.duration_ms || 0,
      });
    } else if (item.role === "error") {
      addError(item.text);
    }
  });
  scrollToBottom(true);
}

function updateWelcome() {
  const note = $("#welcome-note");
  if (!note) return;
  if (state.settings && !state.settings.has_key) {
    note.innerHTML = 'Нужен ключ <b>Kimi Code</b> — <a href="#" id="welcome-settings">открыть настройки</a>.';
    const link = $("#welcome-settings");
    if (link) link.addEventListener("click", (e) => { e.preventDefault(); openSettings(); });
  } else if (state.mcp && !state.mcp.running) {
    note.textContent = "MCP-сервер КОМПАС запускается…";
  } else {
    note.textContent = "Ключ сохранён. Опишите деталь или выберите пример.";
  }
}

/* ------------------------------------------------------------------ stream */

async function readNdjson(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line) continue;
      try {
        onEvent(JSON.parse(line));
      } catch (err) {
        console.error("bad event", line, err);
      }
    }
  }
  if (buffer.trim()) {
    try { onEvent(JSON.parse(buffer.trim())); } catch { /* ignore */ }
  }
}

function handleEvent(ev) {
  switch (ev.type) {
    case "turn_start":
      hideWelcome();
      break;
    case "text":
      appendAssistant(ev.text);
      break;
    case "tool_start":
      addToolCard(ev);
      break;
    case "tool_result":
      updateTool(ev);
      break;
    case "assistant_end":
      if (ev.usage) {
        state.lastUsage = ev.usage;
        const inTok = ev.usage.input_tokens || 0;
        const outTok = ev.usage.output_tokens || 0;
        els.footTokens.textContent = `${fmtTokens(inTok)} → ${fmtTokens(outTok)}`;
      }
      finalizeAssistant();
      break;
    case "warning":
      toast(ev.message, "warn");
      break;
    case "error":
      finalizeAssistant();
      addError(ev.message || "Неизвестная ошибка");
      toast(ev.message || "Ошибка", "err");
      break;
    case "done":
      finalizeAssistant();
      setBusy(false);
      break;
    default:
      break;
  }
}

function fmtTokens(n) {
  if (!n) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

async function send(text) {
  if (state.busy) return;
  const value = (text ?? els.input.value).trim();
  if (!value) return;
  if (!state.settings?.has_key) {
    toast("Сначала добавьте ключ Kimi", "warn");
    openSettings();
    return;
  }
  els.input.value = "";
  autosize();
  setBusy(true);
  hideWelcome();
  addUserMessage(value);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: value }),
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        message = data.error || message;
      } catch { /* ignore */ }
      if (response.status === 400 && String(message).includes("ключ")) {
        openSettings();
      }
      throw new Error(message);
    }
    await readNdjson(response, handleEvent);
  } catch (err) {
    if (err.name !== "AbortError") {
      addError(err.message || String(err));
      toast(err.message || String(err), "err");
    }
  } finally {
    setBusy(false);
    finalizeAssistant();
    els.input.focus();
  }
}

function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.send.classList.toggle("hidden", busy);
  els.stop.classList.toggle("hidden", !busy);
  els.input.disabled = busy;
  if (!busy) {
    els.footTokens.textContent = els.footTokens.textContent || "";
  }
}

async function stopAgent() {
  try {
    await fetch("/api/stop", { method: "POST" });
    toast("Останавливаю…", "warn");
  } catch { /* ignore */ }
}

/* ------------------------------------------------------------------ settings */

function fillSettings() {
  const s = state.settings || {};
  $("#set-key").value = "";
  $("#set-model").value = s.model || "k3.1-pro";
  $("#set-base").value = s.base_url || "https://api.kimi.ai/coding/";
  $("#set-tools").value = s.tool_mode || "core";
  $("#set-savedir").value = s.save_dir || "";
  $("#set-width").value = s.panel_width || 440;
  $("#set-mcpcmd").value = s.mcp_command || "";
  $("#config-path").textContent = s.config_path ? `Конфиг: ${s.config_path}` : "";
  $("#settings-msg").textContent = "";
  $("#settings-msg").className = "settings-msg";
  updateMcpBox();
  updateAuthUI();
}

function updateAuthUI() {
  const s = state.settings || {};
  const mode = s.auth_mode || (s.use_kimi_code ? "kimi_code" : s.has_key ? "api_key" : "none");
  const oauth = mode === "kimi_code";
  const dot = $("#oauth-dot");
  const status = $("#oauth-status");
  const button = $("#btn-oauth");
  const out = $("#btn-oauth-out");
  if (!dot || !status || !button || !out) return;
  dot.className = "dot " + (mode === "none" ? "wait" : "ok");
  if (oauth) {
    status.textContent = "подключён — ключ не нужен";
    button.textContent = "Переподключить";
  } else if (mode === "api_key") {
    status.textContent = "API-ключ сохранён";
    button.textContent = "Войти через Kimi Code";
  } else {
    status.textContent = "не подключён";
    button.textContent = "Войти";
  }
  out.classList.toggle("hidden", !oauth);
  const key = $("#set-key");
  if (key) {
    key.placeholder = oauth
      ? "не используется — вход через Kimi Code"
      : mode === "api_key"
        ? "ключ сохранён — оставьте пустым"
        : "sk-kimi-…";
  }
}

let oauthPollTimer = null;

function stopOAuthPolling() {
  if (oauthPollTimer) {
    clearTimeout(oauthPollTimer);
    oauthPollTimer = null;
  }
}

function scheduleOAuthPoll(seconds) {
  stopOAuthPolling();
  oauthPollTimer = setTimeout(pollOAuth, Math.max(2, Number(seconds) || 5) * 1000);
}

async function loginOAuth() {
  const msg = $("#settings-msg");
  msg.textContent = "Получаю код для входа…";
  msg.className = "settings-msg";
  try {
    const response = await fetch("/api/oauth/start", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "не удалось начать вход");
    $("#oauth-code").textContent = data.user_code || "••••-••••";
    $("#oauth-link").href = data.verification_url || "https://www.kimi.ai/code/authorize_device";
    $("#oauth-wait").textContent = "Ожидаю подтверждения…";
    els.oauthModal.classList.remove("hidden");
    msg.textContent = "";
    scheduleOAuthPoll(data.interval || 5);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "settings-msg err";
  }
}

async function pollOAuth() {
  try {
    const response = await fetch("/api/oauth/poll", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "ошибка входа");
    if (data.status === "success") {
      stopOAuthPolling();
      els.oauthModal.classList.add("hidden");
      const boot = await fetch("/api/bootstrap").then((r) => r.json());
      state.settings = boot.settings;
      state.mcp = boot.mcp;
      updateAuthUI();
      const msg = $("#settings-msg");
      msg.textContent = "Kimi Code подключён. Ключ не нужен.";
      msg.className = "settings-msg ok";
      toast("Вход через Kimi Code выполнен", "ok");
      return;
    }
    if (data.status === "pending") {
      scheduleOAuthPoll(5);
      return;
    }
    if (data.status === "slow_down") {
      scheduleOAuthPoll(data.interval || 8);
      return;
    }
    if (data.status === "expired" || data.status === "denied" || data.status === "error") {
      stopOAuthPolling();
      $("#oauth-wait").textContent =
        data.status === "denied"
          ? "Доступ отклонён. Попробуйте снова."
          : data.status === "expired"
            ? "Код истёк. Нажмите «Войти» ещё раз."
            : data.error || "Не удалось войти.";
      return;
    }
    scheduleOAuthPoll(5);
  } catch (err) {
    stopOAuthPolling();
    $("#oauth-wait").textContent = err.message;
  }
}

async function cancelOAuth() {
  stopOAuthPolling();
  try {
    await fetch("/api/oauth/cancel", { method: "POST" });
  } catch {
    /* ignore */
  }
  els.oauthModal.classList.add("hidden");
}

async function logoutOAuth() {
  const msg = $("#settings-msg");
  try {
    const response = await fetch("/api/oauth/logout", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "не удалось выйти");
    state.settings = data.settings;
    updateAuthUI();
    msg.textContent = "Вход через Kimi Code отключён.";
    msg.className = "settings-msg";
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "settings-msg err";
  }
}

function updateMcpBox() {
  const mcp = state.mcp || {};
  const dot = $("#set-mcp-dot");
  const text = $("#set-mcp-text");
  dot.className = "dot " + (mcp.running ? "ok" : mcp.error ? "err" : "wait");
  text.textContent = mcp.running
    ? `работает · инструментов ${mcp.tools_selected} из ${mcp.tools_total}`
    : mcp.error || "запускается…";
}

function openSettings() {
  fillSettings();
  els.settings.classList.remove("hidden");
}

function closeSettings() {
  els.settings.classList.add("hidden");
}

async function saveSettings() {
  const msg = $("#settings-msg");
  const payload = {
    model: $("#set-model").value,
    base_url: $("#set-base").value.trim(),
    tool_mode: $("#set-tools").value,
    save_dir: $("#set-savedir").value.trim(),
    panel_width: Number($("#set-width").value) || 440,
    mcp_command: $("#set-mcpcmd").value.trim(),
  };
  const key = $("#set-key").value.trim();
  if (key) payload.api_key = key;
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "ошибка сохранения");
    state.settings = data.settings;
    els.footModel.textContent = state.settings.model;
    updateAuthUI();
    msg.textContent = "Сохранено.";
    msg.className = "settings-msg ok";
    toast("Настройки сохранены", "ok");
    updateWelcome();
    setTimeout(closeSettings, 600);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "settings-msg err";
  }
}

async function testKey() {
  const msg = $("#settings-msg");
  msg.textContent = "Проверяю ключ…";
  msg.className = "settings-msg";
  const key = $("#set-key").value.trim();
  try {
    if (key) {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
    }
    const res = await fetch("/api/test-key", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "ключ не принят");
    msg.textContent = `Ключ работает. Модель: ${data.model}. Ответ: ${data.reply || "ok"}`;
    msg.className = "settings-msg ok";
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "settings-msg err";
  }
}

async function restartMcp() {
  const msg = $("#settings-msg");
  msg.textContent = "Перезапускаю MCP…";
  msg.className = "settings-msg";
  try {
    await fetch("/api/mcp/restart", { method: "POST" });
    setTimeout(async () => {
      await refreshStatus();
      updateMcpBox();
      msg.textContent = state.mcp?.running ? "MCP перезапущен." : "MCP ещё поднимается…";
      msg.className = "settings-msg " + (state.mcp?.running ? "ok" : "");
    }, 2500);
  } catch {
    msg.textContent = "Не удалось перезапустить MCP";
    msg.className = "settings-msg err";
  }
}

/* ------------------------------------------------------------------ misc */

let toastTimer = null;
function toast(text, kind = "") {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3800);
}

function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 150) + "px";
}

function postHost(payload) {
  try {
    if (window.chrome && window.chrome.webview) {
      window.chrome.webview.postMessage(JSON.stringify(payload));
      return true;
    }
  } catch { /* ignore */ }
  return false;
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/bootstrap");
    const data = await res.json();
    state.mcp = data.mcp;
    state.settings = data.settings;
    const dot = els.mcpDot;
    dot.className = "dot " + (data.mcp?.running ? "ok" : data.mcp?.error ? "err" : "wait");
    els.mcpLabel.textContent = data.mcp?.running ? "MCP" : "MCP…";
    els.mcpLabel.title = data.mcp?.error || "";
    updateMcpBox();
    updateAuthUI();
  } catch { /* ignore */ }
}

async function newChat() {
  if (state.busy) {
    toast("Сначала остановите агента", "warn");
    return;
  }
  try {
    await fetch("/api/reset", { method: "POST" });
  } catch { /* ignore */ }
  showWelcome();
  updateWelcome();
  els.footTokens.textContent = "";
  els.input.focus();
}

/* ------------------------------------------------------------------ init */

function bind() {
  els.send.addEventListener("click", () => send());
  els.stop.addEventListener("click", stopAgent);

  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  $("#btn-new").addEventListener("click", newChat);
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-settings-close").addEventListener("click", closeSettings);
  $("#btn-save").addEventListener("click", saveSettings);
  $("#btn-test").addEventListener("click", testKey);
  $("#btn-mcp-restart").addEventListener("click", restartMcp);
  $("#btn-oauth").addEventListener("click", loginOAuth);
  $("#btn-oauth-out").addEventListener("click", logoutOAuth);
  $("#btn-oauth-cancel").addEventListener("click", cancelOAuth);
  $("#btn-oauth-cancel2").addEventListener("click", cancelOAuth);
  $("#btn-close").addEventListener("click", () => {
    if (!postHost({ type: "close" })) closeSettings();
  });

  els.settings.addEventListener("click", (e) => {
    if (e.target === els.settings) closeSettings();
  });
  els.lightbox.addEventListener("click", () => els.lightbox.classList.add("hidden"));

  const dragzone = $("#dragzone");
  dragzone.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest("button")) return;
    postHost({ type: "drag" });
  });

  $$(".example").forEach((button) => {
    button.addEventListener("click", () => {
      els.input.value = button.dataset.prompt || "";
      autosize();
      els.input.focus();
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSettings();
      els.lightbox.classList.add("hidden");
    }
  });
}

async function init() {
  bind();
  autosize();
  try {
    const data = await fetch("/api/bootstrap").then((r) => r.json());
    state.settings = data.settings;
    state.mcp = data.mcp;
    state.busy = data.busy;
    els.footModel.textContent = data.settings.model || "k3.1-pro";
    renderHistory(data.history || []);
    setBusy(Boolean(data.busy));
    els.mcpDot.className = "dot " + (data.mcp?.running ? "ok" : data.mcp?.error ? "err" : "wait");
    updateWelcome();
    updateAuthUI();
  } catch (err) {
    addError("Не удалось подключиться к локальному серверу панели: " + err.message);
  }
  setInterval(refreshStatus, 5000);
  if (new URLSearchParams(location.search).has("settings")) openSettings();
}

init();
