"use strict";

const REFRESH_INTERVAL_MS = 1000;
const CONSOLE_BOTTOM_TOLERANCE_PX = 8;
const PERCENT = 100;

const PILL_BASE = "rounded-full px-3 py-1 text-xs font-bold";
const PILL_CLASSES = {
  ok: "bg-emerald-100 text-emerald-700",
  warn: "bg-amber-100 text-amber-700",
  err: "bg-red-100 text-red-700",
  neutral: "bg-slate-200 text-slate-500",
};

const SERVER_STATUS_LABELS = {
  starting: { text: "Validando token…", css: "neutral" },
  not_configured: { text: "Servidor não informado", css: "warn" },
  online: { text: "Servidor conectado", css: "ok" },
  offline: { text: "Servidor sem conexão", css: "err" },
  auth_error: { text: "Token recusado", css: "err" },
};

const SERIAL_STATUS_LABELS = {
  starting: { text: "Iniciando…", css: "neutral" },
  disabled: { text: "Desativada", css: "neutral" },
  connecting: { text: "Abrindo porta…", css: "warn" },
  listening: { text: "Escutando", css: "ok" },
  detecting: { text: "Detectando baudrate", css: "warn" },
  error: { text: "Porta com problema", css: "err" },
};

const CAPTURE_STATUS_LABELS = {
  captured: { text: "Aguardando envio", css: "warn" },
  sent: { text: "Enviada", css: "ok" },
  failed: { text: "Recusada", css: "err" },
};

const PARITY_LABELS = { N: "Nenhuma", E: "Par", O: "Ímpar", M: "Mark", S: "Space" };

const el = (id) => document.getElementById(id);

let lastState = null;
let forceSetupView = false;
let knownPorts = [];
let serialOptionsFilled = false;
let editingConnectionId = null;
const cards = new Map();
const latestConnections = new Map();
const pendingRemovals = new Set();

function setPill(element, text, css) {
  element.textContent = text;
  element.className = `${PILL_BASE} ${PILL_CLASSES[css]}`;
}

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDateTime(isoText) {
  if (!isoText) return "—";
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return isoText;
  return date.toLocaleString("pt-BR");
}

function overallStatus(state) {
  const connections = state.connections || [];
  const active = connections.filter((connection) => connection.enabled);
  if (!state.is_server_configured) return { text: "Servidor não configurado", css: "warn" };
  if (active.length === 0) return { text: "Nenhuma máquina ativa", css: "warn" };
  if (active.some((c) => c.serial_status === "error")) return { text: "Erro em uma porta serial", css: "err" };
  if (active.some((c) => c.server_status === "auth_error")) return { text: "Token recusado", css: "err" };
  if (active.some((c) => c.server_status === "offline")) return { text: "Servidor inacessível", css: "err" };
  if (active.some((c) => c.serial_status === "detecting")) return { text: "Detectando baudrate", css: "warn" };
  if (active.every((c) => c.serial_status === "listening" && c.server_status === "online"))
    return { text: "Operacional", css: "ok" };
  return { text: "Iniciando…", css: "neutral" };
}

function renderState(state) {
  lastState = state;

  const showSetup = (!state.is_server_configured && !state.setup_skipped) || forceSetupView;
  el("view-setup").hidden = !showSetup;
  el("view-main").hidden = showSetup;
  el("btn-skip-setup").textContent =
    state.is_server_configured || state.setup_skipped ? "Voltar" : "Configurar depois";
  if (showSetup) return;

  if (!serialOptionsFilled) {
    fillSerialOptions(state.serial_options);
    serialOptionsFilled = true;
  }

  const status = overallStatus(state);
  setPill(el("status-pill"), status.text, status.css);
  el("agent-version").textContent = `v${state.agent_version}`;
  el("server-banner").hidden = state.is_server_configured;
  el("card-server-url").textContent = state.server_url || "—";

  renderConnections(state.connections || []);
  renderCaptures(state.recent_captures || []);
}

function renderConnections(connections) {
  const list = el("connections-list");
  el("connections-empty").hidden = connections.length > 0;
  const seen = new Set();

  for (const connection of connections) {
    seen.add(connection.id);
    latestConnections.set(connection.id, connection);
    let card = cards.get(connection.id);
    if (!card) {
      card = createCard(connection.id);
      cards.set(connection.id, card);
      list.appendChild(card);
    }
    updateCard(card, connection);
  }

  for (const [id, card] of cards) {
    if (!seen.has(id)) {
      card.remove();
      cards.delete(id);
      latestConnections.delete(id);
      pendingRemovals.delete(id);
    }
  }
}

function roleEl(card, role) {
  return card.querySelector(`[data-role="${role}"]`);
}

function createCard(connectionId) {
  const fragment = el("connection-card-template").content.cloneNode(true);
  const card = fragment.firstElementChild;
  card.dataset.connectionId = String(connectionId);

  const api = () => window.pywebview.api;
  const actions = {
    edit: () => openDialog(latestConnections.get(connectionId)),
    detect: () => startDetection(connectionId, card),
    reconnect: () => api().reconnect(connectionId),
    retry: () => api().retry_connection(connectionId),
    "clear-console": () => api().clear_console(connectionId),
    "detect-dismiss": () => api().dismiss_detect_result(connectionId),
    remove: () => {
      pendingRemovals.add(connectionId);
      updateCard(card, latestConnections.get(connectionId));
    },
    "remove-cancel": () => {
      pendingRemovals.delete(connectionId);
      updateCard(card, latestConnections.get(connectionId));
    },
    "remove-confirm": async () => {
      await api().delete_connection(connectionId);
      pendingRemovals.delete(connectionId);
      await refresh();
    },
  };

  for (const button of card.querySelectorAll("[data-action]")) {
    button.addEventListener("click", () => actions[button.dataset.action]());
  }
  return card;
}

function describeSerial(serial) {
  const flow = serial.rtscts ? "RTS/CTS" : "sem RTS/CTS";
  return `${serial.port} · ${serial.baudrate} ${serial.bytesize}${serial.parity}${serial.stopbits} · ${flow} · silêncio ${serial.idle_timeout_seconds}s`;
}

function describeMachine(connection) {
  const machine = connection.machine;
  if (!machine) return connection.has_token ? "Máquina ainda não validada no servidor" : "Sem token";
  const serial = machine.serial_number ? ` (nº série ${machine.serial_number})` : "";
  const agent = connection.agent_name ? `${connection.agent_name} · ` : "";
  return `${agent}${machine.name}${serial}`;
}

function updateCard(card, connection) {
  roleEl(card, "name").textContent = connection.name;
  roleEl(card, "machine").textContent = describeMachine(connection);
  roleEl(card, "port").textContent = describeSerial(connection.serial);
  card.classList.toggle("opacity-60", !connection.enabled);

  const serialInfo = SERIAL_STATUS_LABELS[connection.serial_status] || SERIAL_STATUS_LABELS.starting;
  setPill(roleEl(card, "serial-status"), serialInfo.text, serialInfo.css);
  const serverInfo = SERVER_STATUS_LABELS[connection.server_status] || SERVER_STATUS_LABELS.starting;
  setPill(roleEl(card, "server-status"), serverInfo.text, serverInfo.css);

  const errorMessage =
    (connection.serial_status === "error" && connection.serial_error_message) ||
    (["offline", "auth_error"].includes(connection.server_status) && connection.server_error_message) ||
    "";
  roleEl(card, "error").textContent = errorMessage;
  roleEl(card, "error").hidden = !errorMessage;

  const isDetecting = connection.detect_status === "running";
  card.querySelector('[data-action="detect"]').disabled = isDetecting || !connection.enabled;
  renderDetectPanel(card, connection);
  renderConsole(roleEl(card, "console"), connection.live_text || "");

  const confirming = pendingRemovals.has(connection.id);
  card.querySelector('[data-action="remove"]').hidden = confirming;
  roleEl(card, "remove-confirm").hidden = !confirming;
}

function renderDetectPanel(card, connection) {
  const isRunning = connection.detect_status === "running";
  const isDone = connection.detect_status === "done" && connection.detect_result;
  roleEl(card, "detect-panel").hidden = !isRunning && !isDone;
  roleEl(card, "detect-running").hidden = !isRunning;
  roleEl(card, "detect-done").hidden = !isDone;
  if (!isDone) return;

  const result = connection.detect_result;
  roleEl(card, "detect-message").textContent = result.message;
  roleEl(card, "detect-scores").innerHTML = (result.scores || [])
    .map((score) => {
      const isBest = score.baudrate === result.best_baudrate;
      const css = isBest ? "bg-emerald-100 text-emerald-700" : "bg-white text-slate-500";
      const detail = score.error
        ? "erro"
        : `${score.bytes_received} B · ${Math.round(score.printable_ratio * PERCENT)}%`;
      return `<li class="rounded-full border border-slate-200 px-2.5 py-0.5 font-mono text-[11px] ${css}">${score.baudrate}: ${escapeHtml(detail)}</li>`;
    })
    .join("");
}

function renderConsole(consoleEl, text) {
  if (consoleEl.textContent === text) return;
  const wasAtBottom =
    consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < CONSOLE_BOTTOM_TOLERANCE_PX;
  consoleEl.textContent = text;
  if (wasAtBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
}

function renderCaptures(captures) {
  const tbody = el("captures-tbody");
  el("btn-retry-failed").hidden = !captures.some((capture) => capture.status === "failed");

  if (!captures.length) {
    tbody.innerHTML =
      '<tr><td colspan="7" class="px-2.5 py-8 text-center text-slate-400">Nenhuma captura ainda</td></tr>';
    return;
  }

  tbody.innerHTML = captures
    .map((capture) => {
      const statusInfo = CAPTURE_STATUS_LABELS[capture.status] || { text: capture.status, css: "neutral" };
      return `<tr class="border-b border-slate-100 last:border-0">
        <td class="px-2.5 py-2.5 font-mono text-[13px]">${capture.id}</td>
        <td class="px-2.5 py-2.5">${escapeHtml(capture.connection_name || "—")}</td>
        <td class="px-2.5 py-2.5 text-slate-500">${escapeHtml(formatDateTime(capture.captured_at))}</td>
        <td class="px-2.5 py-2.5 font-mono text-[13px]">${escapeHtml(capture.preview || "—")}</td>
        <td class="px-2.5 py-2.5"><span class="${PILL_BASE} ${PILL_CLASSES[statusInfo.css]}">${statusInfo.text}</span></td>
        <td class="px-2.5 py-2.5 font-mono text-[13px]">${capture.server_capture_id ? capture.server_capture_id : "—"}</td>
        <td class="px-2.5 py-2.5 text-xs text-slate-500">${escapeHtml(capture.error_message || "—")}</td>
      </tr>`;
    })
    .join("");
}

async function refresh() {
  try {
    const state = await window.pywebview.api.get_state();
    renderState(state);
  } catch (error) {
    return;
  }
}

async function submitServerUrl() {
  const button = el("btn-save-server");
  const errorText = el("setup-error");
  button.disabled = true;
  errorText.hidden = true;

  const result = await window.pywebview.api.save_server_url(el("input-server-url").value);
  button.disabled = false;

  if (!result.ok) {
    errorText.textContent = result.error;
    errorText.hidden = false;
    return;
  }
  forceSetupView = false;
  await refresh();
}

async function leaveSetupView() {
  const isConfigured = lastState && (lastState.is_server_configured || lastState.setup_skipped);
  if (!isConfigured) {
    await window.pywebview.api.skip_setup();
  }
  forceSetupView = false;
  await refresh();
}

function openSetupView() {
  if (lastState && lastState.server_url) el("input-server-url").value = lastState.server_url;
  forceSetupView = true;
  refresh();
}

function fillSelect(select, values, labelOf = (value) => String(value)) {
  select.innerHTML = values
    .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(labelOf(value))}</option>`)
    .join("");
}

function fillSerialOptions(options) {
  fillSelect(el("input-baudrate"), options.baudrates);
  fillSelect(el("input-bytesize"), options.bytesizes);
  fillSelect(el("input-parity"), options.parities, (value) => `${value} — ${PARITY_LABELS[value] ?? value}`);
  fillSelect(el("input-stopbits"), options.stopbits);
}

function fillPortSelect(ports, selectedPort) {
  const select = el("input-port");
  const options = ports.map(
    (port) =>
      `<option value="${escapeHtml(port.device)}">${escapeHtml(port.device)}${port.description ? ` — ${escapeHtml(port.description)}` : ""}</option>`
  );
  if (selectedPort && !ports.some((port) => port.device === selectedPort)) {
    options.unshift(`<option value="${escapeHtml(selectedPort)}">${escapeHtml(selectedPort)} — não encontrada</option>`);
  }
  options.unshift('<option value="">Selecione a porta</option>');
  select.innerHTML = options.join("");
  select.value = selectedPort || "";
}

async function refreshPorts() {
  const result = await window.pywebview.api.list_ports();
  if (!result.ok) return;
  knownPorts = result.ports;
  fillPortSelect(knownPorts, el("input-port").value);
}

function fillDialogForm(connection) {
  const serial = connection ? connection.serial : lastState.serial_defaults;
  el("input-name").value = connection ? connection.name : "";
  fillPortSelect(knownPorts, serial.port);
  el("input-baudrate").value = String(serial.baudrate);
  el("input-bytesize").value = String(serial.bytesize);
  el("input-parity").value = serial.parity;
  el("input-stopbits").value = String(serial.stopbits);
  el("input-idle-timeout").value = String(serial.idle_timeout_seconds);
  el("input-rtscts").checked = Boolean(serial.rtscts);
  el("input-token").value = "";
  el("input-token").placeholder = connection ? "Deixe em branco para manter o token atual" : "Cole o token aqui";
  el("input-skip-validation").checked = false;
  el("input-enabled").checked = connection ? connection.enabled : true;
}

function openDialog(connection) {
  editingConnectionId = connection ? connection.id : null;
  el("dialog-title").textContent = connection ? `Editar: ${connection.name}` : "Adicionar máquina";
  el("dialog-error").hidden = true;
  fillDialogForm(connection);
  el("connection-dialog").hidden = false;
  refreshPorts();
}

function closeDialog() {
  el("connection-dialog").hidden = true;
  editingConnectionId = null;
}

function readDialogForm() {
  return {
    id: editingConnectionId,
    name: el("input-name").value,
    port: el("input-port").value,
    baudrate: el("input-baudrate").value,
    bytesize: el("input-bytesize").value,
    parity: el("input-parity").value,
    stopbits: el("input-stopbits").value,
    idle_timeout_seconds: el("input-idle-timeout").value,
    rtscts: el("input-rtscts").checked,
    agent_token: el("input-token").value,
    skip_validation: el("input-skip-validation").checked,
    enabled: el("input-enabled").checked,
  };
}

async function submitDialog() {
  const button = el("btn-dialog-save");
  const errorText = el("dialog-error");
  button.disabled = true;
  button.textContent = "Validando…";
  errorText.hidden = true;

  const result = await window.pywebview.api.save_connection(readDialogForm());
  button.disabled = false;
  button.textContent = "Salvar";

  if (!result.ok) {
    errorText.textContent = result.error;
    errorText.hidden = false;
    return;
  }
  closeDialog();
  await refresh();
}

async function startDetection(connectionId, card) {
  const result = await window.pywebview.api.detect_baudrate(connectionId);
  if (!result.ok) {
    roleEl(card, "error").textContent = result.error;
    roleEl(card, "error").hidden = false;
    return;
  }
  await refresh();
}

function bindEvents() {
  el("btn-save-server").addEventListener("click", submitServerUrl);
  el("btn-skip-setup").addEventListener("click", leaveSetupView);
  el("btn-open-setup").addEventListener("click", openSetupView);
  el("btn-edit-server").addEventListener("click", openSetupView);
  el("btn-add-connection").addEventListener("click", () => openDialog(null));
  el("btn-refresh-ports").addEventListener("click", refreshPorts);
  el("btn-dialog-cancel").addEventListener("click", closeDialog);
  el("btn-dialog-save").addEventListener("click", submitDialog);
  el("btn-retry-failed").addEventListener("click", async () => {
    await window.pywebview.api.retry_failed_captures();
    await refresh();
  });
  el("input-server-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") submitServerUrl();
  });
}

window.addEventListener("pywebviewready", () => {
  bindEvents();
  refresh();
  setInterval(refresh, REFRESH_INTERVAL_MS);
});
