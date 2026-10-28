"use strict";

const REFRESH_INTERVAL_MS = 1000;

const PILL_BASE = "rounded-full px-3 py-1 text-xs font-bold";
const PILL_CLASSES = {
  ok: "bg-emerald-100 text-emerald-700",
  warn: "bg-amber-100 text-amber-700",
  err: "bg-red-100 text-red-700",
  neutral: "bg-slate-200 text-slate-500",
};

const SERVER_STATUS_LABELS = {
  starting: { text: "Iniciando…", css: "neutral" },
  not_paired: { text: "Não pareado", css: "warn" },
  online: { text: "Conectado", css: "ok" },
  offline: { text: "Sem conexão", css: "err" },
  auth_error: { text: "Token recusado", css: "err" },
};

const SERIAL_STATUS_LABELS = {
  starting: { text: "Iniciando…", css: "neutral" },
  not_configured: { text: "Não configurada", css: "warn" },
  connecting: { text: "Abrindo porta…", css: "warn" },
  listening: { text: "Escutando", css: "ok" },
  detecting: { text: "Detectando baudrate", css: "warn" },
  error: { text: "Com problema", css: "err" },
};

const CAPTURE_STATUS_LABELS = {
  captured: { text: "Aguardando envio", css: "warn" },
  sent: { text: "Enviada", css: "ok" },
  failed: { text: "Recusada", css: "err" },
};

const PARITY_LABELS = { N: "Nenhuma", E: "Par", O: "Ímpar", M: "Mark", S: "Space" };

const el = (id) => document.getElementById(id);

// Mantém a tela de pareamento aberta entre refreshes ("Parear agora"/"Refazer pareamento")
let forcePairingView = false;
let lastState = null;
// Enquanto o operador edita a porta serial, o refresh não sobrescreve o formulário
let serialFormDirty = false;
let serialFormInitialized = false;
let knownPorts = [];

// --- Renderização ---

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

function overallStatus(state) {
  if (state.serial_status === "error") return { text: "Erro na porta serial", css: "err" };
  if (state.server_status === "auth_error") return { text: "Token recusado", css: "err" };
  if (state.server_status === "offline") return { text: "Servidor inacessível", css: "err" };
  if (state.serial_status === "detecting") return { text: "Detectando baudrate", css: "warn" };
  if (!state.is_paired) return { text: "Pareamento pendente", css: "warn" };
  if (state.serial_status === "not_configured") return { text: "Porta não configurada", css: "warn" };
  if (state.server_status === "online" && state.serial_status === "listening")
    return { text: "Operacional", css: "ok" };
  return { text: "Iniciando…", css: "neutral" };
}

function renderState(state) {
  lastState = state;

  const showPairing = (!state.is_paired && !state.pairing_skipped) || forcePairingView;
  el("view-pairing").hidden = !showPairing;
  el("view-main").hidden = showPairing;
  el("btn-skip-pairing").textContent =
    state.is_paired || state.pairing_skipped ? "Voltar" : "Configurar depois";
  if (showPairing) return;

  const status = overallStatus(state);
  setPill(el("status-pill"), status.text, status.css);
  el("agent-version").textContent = `v${state.agent_version}`;
  el("pairing-banner").hidden = state.is_paired;

  renderServerCard(state);
  renderSerialCard(state);
  renderDetectPanel(state);
  renderErrorBanner(state);
  renderConsole(state.live_text || "");
  renderCaptures(state.recent_captures || []);
}

function renderServerCard(state) {
  el("card-server-url").textContent = state.server_url || "—";
  const machine = state.machine;
  el("card-machine").textContent = machine
    ? `${state.agent_name ? state.agent_name + " · " : ""}${machine.name}${machine.serial_number ? ` (nº série ${machine.serial_number})` : ""}`
    : "";

  const serverInfo = SERVER_STATUS_LABELS[state.server_status] || SERVER_STATUS_LABELS.starting;
  setPill(el("card-server-status"), serverInfo.text, serverInfo.css);
}

function renderSerialCard(state) {
  const serialInfo = SERIAL_STATUS_LABELS[state.serial_status] || SERIAL_STATUS_LABELS.starting;
  setPill(el("card-serial-status"), serialInfo.text, serialInfo.css);

  const isDetecting = state.detect_status === "running";
  el("btn-detect").disabled = isDetecting;
  el("btn-save-serial").disabled = isDetecting;

  if (!serialFormInitialized) {
    fillSerialOptions(state.serial_options);
    serialFormInitialized = true;
    refreshPorts();
  }
  if (!serialFormDirty) fillSerialForm(state.serial_settings);
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
  // Porta salva que não aparece na lista (cabo desconectado) continua selecionável
  if (selectedPort && !ports.some((port) => port.device === selectedPort)) {
    options.unshift(`<option value="${escapeHtml(selectedPort)}">${escapeHtml(selectedPort)} — não encontrada</option>`);
  }
  options.unshift('<option value="">Selecione a porta</option>');
  select.innerHTML = options.join("");
  select.value = selectedPort || "";
}

function fillSerialForm(settings) {
  fillPortSelect(knownPorts, settings.port);
  el("input-baudrate").value = String(settings.baudrate);
  el("input-bytesize").value = String(settings.bytesize);
  el("input-parity").value = settings.parity;
  el("input-stopbits").value = String(settings.stopbits);
  el("input-idle-timeout").value = String(settings.idle_timeout_seconds);
  el("input-rtscts").checked = Boolean(settings.rtscts);
}

function readSerialForm() {
  return {
    port: el("input-port").value,
    baudrate: el("input-baudrate").value,
    bytesize: el("input-bytesize").value,
    parity: el("input-parity").value,
    stopbits: el("input-stopbits").value,
    idle_timeout_seconds: el("input-idle-timeout").value,
    rtscts: el("input-rtscts").checked,
  };
}

function renderDetectPanel(state) {
  const isRunning = state.detect_status === "running";
  const isDone = state.detect_status === "done" && state.detect_result;
  el("detect-panel").hidden = !isRunning && !isDone;
  el("detect-running").hidden = !isRunning;
  el("detect-done").hidden = !isDone;
  if (!isDone) return;

  const result = state.detect_result;
  el("detect-message").textContent = result.message;
  el("detect-scores").innerHTML = (result.scores || [])
    .map((score) => {
      const isBest = score.baudrate === result.best_baudrate;
      const css = isBest ? "bg-emerald-100 text-emerald-700" : "bg-white text-slate-500";
      const detail = score.error
        ? "erro"
        : `${score.bytes_received} B · ${Math.round(score.printable_ratio * 100)}%`;
      return `<li class="rounded-full border border-slate-200 px-2.5 py-0.5 font-mono text-[11px] ${css}">${score.baudrate}: ${escapeHtml(detail)}</li>`;
    })
    .join("");
}

function renderErrorBanner(state) {
  const hasSerialError = state.serial_status === "error";
  const hasServerError = ["offline", "auth_error"].includes(state.server_status);
  el("error-banner").hidden = !hasSerialError && !hasServerError;
  if (el("error-banner").hidden) return;

  if (hasSerialError) {
    el("error-title").textContent = "Erro na porta serial";
    el("error-detail").textContent = state.serial_error_message || "";
  } else if (state.server_status === "auth_error") {
    el("error-title").textContent = "Token recusado pelo servidor";
    el("error-detail").textContent = "Gere um novo token na tela da máquina e refaça o pareamento.";
  } else {
    el("error-title").textContent = "Servidor inacessível";
    el("error-detail").textContent = state.server_error_message || "";
  }

  el("btn-retry").hidden = state.server_status === "auth_error" && !hasSerialError;
  el("btn-repair").hidden = state.server_status !== "auth_error";
}

function renderConsole(text) {
  const consoleEl = el("console");
  if (consoleEl.textContent === text) return;
  const wasAtBottom =
    consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 8;
  consoleEl.textContent = text;
  if (wasAtBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
}

function formatDateTime(isoText) {
  if (!isoText) return "—";
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return isoText;
  return date.toLocaleString("pt-BR");
}

function renderCaptures(captures) {
  const tbody = el("captures-tbody");
  el("btn-retry-failed").hidden = !captures.some((capture) => capture.status === "failed");

  if (!captures.length) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="px-2.5 py-8 text-center text-slate-400">Nenhuma captura ainda</td></tr>';
    return;
  }

  tbody.innerHTML = captures
    .map((capture) => {
      const statusInfo = CAPTURE_STATUS_LABELS[capture.status] || { text: capture.status, css: "neutral" };
      return `<tr class="border-b border-slate-100 last:border-0">
        <td class="px-2.5 py-2.5 font-mono text-[13px]">#${capture.id}</td>
        <td class="px-2.5 py-2.5 text-slate-500">${escapeHtml(formatDateTime(capture.captured_at))}</td>
        <td class="px-2.5 py-2.5 font-mono text-[13px]">${escapeHtml(capture.preview || "—")}</td>
        <td class="px-2.5 py-2.5"><span class="${PILL_BASE} ${PILL_CLASSES[statusInfo.css]}">${statusInfo.text}</span></td>
        <td class="px-2.5 py-2.5 font-mono text-[13px]">${capture.server_capture_id ? `#${capture.server_capture_id}` : "—"}</td>
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
    // Janela pode estar abrindo antes da bridge ficar pronta; tenta de novo no próximo tick
  }
}

// --- Pareamento ---

async function submitPairing() {
  const button = el("btn-pair");
  const errorText = el("pairing-error");
  button.disabled = true;
  button.textContent = "Validando…";
  errorText.hidden = true;

  const result = await window.pywebview.api.save_pairing(
    el("input-server-url").value,
    el("input-token").value
  );

  button.disabled = false;
  button.textContent = "Conectar";

  if (!result.ok) {
    errorText.textContent = result.error;
    errorText.hidden = false;
    return;
  }
  el("input-token").value = "";
  forcePairingView = false;
  await refresh();
}

async function leavePairingView() {
  const isConfigured = lastState && (lastState.is_paired || lastState.pairing_skipped);
  if (!isConfigured) {
    await window.pywebview.api.skip_pairing();
  }
  forcePairingView = false;
  await refresh();
}

function openPairingView() {
  if (lastState && lastState.server_url) el("input-server-url").value = lastState.server_url;
  forcePairingView = true;
  refresh();
}

// --- Porta serial ---

async function refreshPorts() {
  const result = await window.pywebview.api.list_ports();
  if (!result.ok) return;
  knownPorts = result.ports;
  const selected = el("input-port").value || (lastState && lastState.serial_settings.port);
  fillPortSelect(knownPorts, selected);
}

async function submitSerialSettings() {
  const errorText = el("serial-error");
  errorText.hidden = true;

  const result = await window.pywebview.api.save_serial_settings(readSerialForm());
  if (!result.ok) {
    errorText.textContent = result.error;
    errorText.hidden = false;
    return;
  }
  serialFormDirty = false;
  await refresh();
}

async function startDetection() {
  const errorText = el("serial-error");
  errorText.hidden = true;

  const result = await window.pywebview.api.detect_baudrate();
  if (!result.ok) {
    errorText.textContent = result.error;
    errorText.hidden = false;
    return;
  }
  await refresh();
}

async function dismissDetection() {
  await window.pywebview.api.dismiss_detect_result();
  serialFormDirty = false;
  await refresh();
}

// --- Bindings ---

function bindEvents() {
  el("btn-pair").addEventListener("click", submitPairing);
  el("btn-skip-pairing").addEventListener("click", leavePairingView);
  el("btn-open-pairing").addEventListener("click", openPairingView);
  el("btn-repair").addEventListener("click", openPairingView);
  el("btn-repair-card").addEventListener("click", openPairingView);
  el("btn-retry").addEventListener("click", () => {
    window.pywebview.api.retry_connection();
    window.pywebview.api.reconnect_serial();
  });

  el("btn-refresh-ports").addEventListener("click", refreshPorts);
  el("btn-save-serial").addEventListener("click", submitSerialSettings);
  el("btn-reconnect").addEventListener("click", () => window.pywebview.api.reconnect_serial());
  el("btn-detect").addEventListener("click", startDetection);
  el("btn-detect-dismiss").addEventListener("click", dismissDetection);
  el("btn-clear-console").addEventListener("click", () => window.pywebview.api.clear_console());
  el("btn-retry-failed").addEventListener("click", async () => {
    await window.pywebview.api.retry_failed_captures();
    await refresh();
  });

  const serialInputs = [
    "input-port", "input-baudrate", "input-bytesize", "input-parity",
    "input-stopbits", "input-idle-timeout", "input-rtscts",
  ];
  for (const id of serialInputs) {
    el(id).addEventListener("change", () => (serialFormDirty = true));
    el(id).addEventListener("input", () => (serialFormDirty = true));
  }
}

window.addEventListener("pywebviewready", () => {
  bindEvents();
  refresh();
  setInterval(refresh, REFRESH_INTERVAL_MS);
});
