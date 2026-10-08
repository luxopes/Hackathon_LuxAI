"use strict";
// ProofPay web frontend. Renders state from GET api/state (short polling),
// sends requests to POST api/chat, api/catalog, api/cancel, api/new.
// Served from the same origin as the LSL chat server — relative paths only.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

let latest = null;
let fullState = null;
let lastMessagesRev = -1;
let lastAuditRev = -1;
let expanded = {};
let tab = "tools";
let autoQueue = [];
let autoNextAt = 0;
let autoTotal = 0;

/* ---------- ElevenLabs speech (voice of the agent) ---------- */
let audioPlayer = null;
let speakIndex = -1;
let speakPhase = "idle";
let autoVoice = localStorage.getItem("autoVoice") === "1";
let lastSpokenContent = "";
let previousBusy = false;
let noticeUntil = 0;

function showNotice(text, ms = 5000) {
  $("status-line").textContent = text;
  noticeUntil = Date.now() + ms;
}

function refreshSpeakButtons() {
  document.querySelectorAll(".speak").forEach((btn) => {
    const index = Number(btn.dataset.index);
    if (index !== speakIndex) {
      btn.textContent = "🔊";
      btn.disabled = false;
      btn.classList.remove("active");
      return;
    }
    btn.classList.toggle("active", speakPhase !== "idle");
    btn.textContent = speakPhase === "loading" ? "…" : speakPhase === "playing" ? "⏹" : speakPhase === "ready" ? "▶" : "🔊";
    btn.disabled = speakPhase === "loading";
  });
}

function stopSpeech() {
  if (audioPlayer) {
    audioPlayer.pause();
    audioPlayer = null;
  }
  speakIndex = -1;
  speakPhase = "idle";
  refreshSpeakButtons();
}

async function speakMessage(index) {
  // A second click on a ready message plays the already fetched audio
  // (browsers drop the user activation while the first synthesis runs).
  if (speakIndex === index && speakPhase === "ready" && audioPlayer) {
    try {
      await audioPlayer.play();
      speakPhase = "playing";
      refreshSpeakButtons();
    } catch { /* stays ready */ }
    return;
  }
  if (speakIndex === index && speakPhase === "playing") {
    stopSpeech();
    return;
  }
  stopSpeech();
  speakIndex = index;
  speakPhase = "loading";
  refreshSpeakButtons();
  try {
    const resp = await fetch("api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      stopSpeech();
      showNotice("🔊 " + (err.error || "Text-to-speech failed") + " (HTTP " + resp.status + ")");
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    audioPlayer = new Audio(url);
    audioPlayer.addEventListener("ended", () => { URL.revokeObjectURL(url); stopSpeech(); });
    audioPlayer.addEventListener("error", () => { URL.revokeObjectURL(url); stopSpeech(); });
    try {
      await audioPlayer.play();
      speakPhase = "playing";
    } catch (error) {
      if (error && error.name === "NotAllowedError") {
        speakPhase = "ready";
        showNotice("🔊 Browser blocked autoplay — click ▶ to play.");
      } else {
        throw error;
      }
    }
    refreshSpeakButtons();
  } catch {
    stopSpeech();
    showNotice("🔊 Text-to-speech failed");
  }
}

async function autoSpeakIfEnabled() {
  if (!autoVoice) return;
  try {
    const full = await (await fetch("api/state?t=" + Date.now())).json();
    if (!full || full.ok !== true || full.busy || !full.messages || !full.messages.length) return;
    fullState = full;
    latest = full;
    const last = full.messages[full.messages.length - 1];
    if (last.role !== "Agent" || last.content.length < 40 || last.content === lastSpokenContent) return;
    lastSpokenContent = last.content;
    speakMessage(full.messages.length - 1);
  } catch { /* speech is optional; polling keeps running */ }
}

/* ---------- message text formatting ---------- */
function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<>")]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderBody(text) {
  const lines = String(text).split("\n");
  const out = [];
  let fence = null;
  let pre = [];
  const flushPre = () => {
    if (pre.length) {
      const el = document.createElement("pre");
      el.textContent = pre.join("\n");
      out.push(el);
      pre = [];
    }
  };
  for (const raw of lines) {
    const trimmed = raw.trim();
    if (trimmed.startsWith("```")) {
      if (fence === null) fence = true;
      else { flushPre(); fence = null; }
      continue;
    }
    if (fence !== null) { pre.push(raw); continue; }
    const line = document.createElement("span");
    line.className = "line";
    if (raw.startsWith("OK ·") || raw.startsWith("OK")) line.className += " ok-line";
    else if (raw.includes("CART BUG") || raw.includes("CHYBA")) line.className += " bad-line";
    else if (raw.trim().startsWith("•")) line.className += " info-line";
    line.innerHTML = linkify(raw === "" ? " " : raw);
    out.push(line, document.createElement("br"));
  }
  flushPre();
  return out;
}

function renderChat(messages) {
  const box = $("messages");
  const pinned = box.scrollTop + box.clientHeight >= box.scrollHeight - 80;
  box.replaceChildren();
  for (const [index, m] of messages.entries()) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + (m.role === "You" ? "user" : "agent");
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = m.role === "You" ? "You" : "Agent · LuxAI Flash";
    const body = document.createElement("div");
    body.className = "body";
    body.append(...renderBody(m.content));
    if (m.role !== "You" && m.content.trim().length > 20) {
      const speak = document.createElement("button");
      speak.className = "speak";
      speak.dataset.index = String(index);
      speak.title = "Read aloud with ElevenLabs";
      speak.textContent = "🔊";
      speak.addEventListener("click", () => speakMessage(index));
      body.append(speak);
    }
    wrap.append(role, body);
    box.append(wrap);
  }
  if (pinned) box.scrollTop = box.scrollHeight;
  refreshSpeakButtons();
}

/* ---------- right panel ---------- */
function prettyJson(value) {
  try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
}

function jsonBlock(value) {
  const pre = document.createElement("pre");
  pre.style.cssText = "margin:4px 0;background:#0b1020;border:1px solid #29364e;border-radius:8px;padding:8px;overflow-x:auto";
  pre.textContent = prettyJson(value);
  return pre;
}

function detailLines(lines) {
  const wrap = document.createElement("div");
  wrap.className = "detail";
  for (const line of lines) {
    if (line === "") { wrap.append(document.createElement("br")); continue; }
    const div = document.createElement("div");
    if (line.startsWith("Input:")) {
      div.className = "k";
      div.textContent = "Input";
      wrap.append(div, jsonBlock(line.slice(6).trim()));
      continue;
    }
    if (line.startsWith("Result:")) {
      div.className = "k";
      div.textContent = "Result";
      wrap.append(div, jsonBlock(line.slice(7).trim()));
      continue;
    }
    if (line.startsWith("ESCROW") || line.startsWith("PAYMENT") || line.startsWith("REFUND")) div.className = "tx-line";
    if (line.startsWith("Model tool-call ID")) div.className = "ok";
    div.textContent = line;
    wrap.append(div);
  }
  return wrap;
}

function renderTools(st) {
  const box = $("tab-content");
  box.replaceChildren();
  if (!st.tools.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No tool calls yet. Send a request in the chat — every real call (Flash tool, HTTP, LSL) appears here live.";
    box.append(empty);
    return;
  }
  for (const t of st.tools) {
    const parts = (t.summary || "").split(" · ");
    const entry = document.createElement("div");
    entry.className = "entry";
    const head = document.createElement("div");
    head.className = "head";
    const chip = document.createElement("span");
    chip.className = "chip " + parts[0];
    chip.textContent = parts[0] || "?";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = parts[1] || t.id;
    const source = document.createElement("span");
    source.className = "source";
    source.textContent = parts[2] || "";
    const dur = document.createElement("span");
    dur.className = "duration";
    dur.textContent = parts[3] ? parts[3] + " ms" : "";
    const chevron = document.createElement("span");
    chevron.className = "chevron";
    chevron.textContent = expanded[t.id] ? "▾" : "▸";
    head.append(chip, name, source, dur, chevron);
    head.addEventListener("click", () => {
      expanded[t.id] = !expanded[t.id];
      renderTab(fullState);
    });
    entry.append(head);
    if (expanded[t.id]) entry.append(detailLines(t.lines || []));
    box.append(entry);
  }
}

function renderPayments(st) {
  const box = $("tab-content");
  box.replaceChildren();
  if (!st.payments.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No payment receipts yet. After the first purchase, escrow, payment or refund entries fetched from the marketplace ledger appear here.";
    box.append(empty);
    return;
  }
  for (const p of st.payments) {
    const parts = (p.summary || "").split(" · ");
    const entry = document.createElement("div");
    entry.className = "entry";
    const head = document.createElement("div");
    head.className = "head";
    const chip = document.createElement("span");
    chip.className = "chip " + (parts[0] === "PAID" ? "OK" : parts[0] === "REFUNDED" ? "RUNNING" : "OK");
    chip.textContent = parts[0] || "?";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = (parts[1] || "") + " " + (parts[2] || "");
    const id = document.createElement("span");
    id.className = "duration";
    id.textContent = parts.slice(3).join(" · ");
    const receiptLink = document.createElement("a");
    receiptLink.className = "receipt-link";
    receiptLink.href = "../receipt/" + p.id;
    receiptLink.target = "_blank";
    receiptLink.rel = "noopener";
    receiptLink.textContent = "🧾";
    receiptLink.title = "Full payment receipt (ledger, escrow, hashes, verification)";
    receiptLink.addEventListener("click", (event) => event.stopPropagation());
    const chevron = document.createElement("span");
    chevron.className = "chevron";
    chevron.textContent = expanded[p.id] ? "▾" : "▸";
    head.append(chip, name, id, receiptLink, chevron);
    head.addEventListener("click", () => {
      expanded[p.id] = !expanded[p.id];
      renderTab(fullState);
    });
    entry.append(head);
    if (expanded[p.id]) entry.append(detailLines(p.lines || []));
    box.append(entry);
  }
}

function renderSystem(st) {
  const box = $("tab-content");
  box.replaceChildren();
  const table = document.createElement("table");
  table.className = "sys-table";
  const rows = [
    ["Currency", st.currency],
    ["Payments", st.simulated_payments ? "SIMULATED · test credits, not a blockchain" : "?"],
    ["Agent model", st.ai_model === "flash" ? "LuxAI Flash" : st.ai_model],
    ["Status", st.status],
    ["Agent working", st.busy ? "yes" : "no"],
    ["Messages", String(st.messages.length)],
    ["Tool calls", String(st.tools.length)],
    ["Payment receipts", String(st.payments.length)],
    ["Last refresh", new Date(st.time * 1000).toLocaleTimeString("en-GB")],
  ];
  for (const [k, v] of rows) {
    const tr = document.createElement("tr");
    const a = document.createElement("td");
    a.textContent = k;
    const b = document.createElement("td");
    b.textContent = v;
    tr.append(a, b);
    table.append(tr);
  }
  const note = document.createElement("div");
  note.className = "sys-note";
  note.textContent = "Payments are simulated Lux Coins in the marketplace's central ledger; not real money and not a blockchain. Delivery checks are structural (syntax, citations, execution receipts) and do not guarantee general semantic correctness. The agent purchases one service per turn; the catalog is fetched with a real HTTP call.";
  box.append(table, note);
}

function renderTab(st) {
  if (tab === "tools") renderTools(st);
  else if (tab === "payments") renderPayments(st);
  else renderSystem(st);
}

/* ---------- top bar ---------- */
function renderTopbar(st) {
  $("stat-available").textContent = st.wallet ? String(st.wallet.available) : "—";
  $("stat-locked").textContent = st.wallet ? String(st.wallet.locked) : "—";
  $("stat-budget").textContent = st.budget != null ? String(st.budget) : "—";
  // Lite polling carries no arrays; the last movement comes from the last full state.
  const paySource = fullState && fullState.payments && fullState.payments.length ? fullState.payments : st.payments;
  const last = paySource[paySource.length - 1];
  $("tx-last").textContent = last ? last.summary : (st.wallet ? "Wallet active" : "No transactions yet");
  const dot = $("conn-dot");
  const conn = $("conn-status");
  const autoRunning = autoQueue.length > 0 || (st.busy && autoTotal > 0);
  if (autoRunning) {
    const step = autoTotal - autoQueue.length;
    conn.textContent = "Auto demo · step " + Math.min(step, autoTotal) + "/" + autoTotal;
  } else {
    conn.textContent = st.status;
  }
  dot.className = "dot " + (st.busy ? "busy" : "ok");
  const line = $("status-line");
  if (Date.now() >= noticeUntil) {
    line.textContent = (st.busy ? "⟳ " : "") + st.status;
    line.className = st.busy ? "busy" : "";
  }
  $("btn-send").disabled = st.busy;
  $("btn-catalog").disabled = st.busy;
  $("btn-auto").disabled = st.busy || autoQueue.length > 0;
  $("btn-stop").hidden = !st.busy;
}

/* ---------- actions ---------- */
async function post(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

async function sendMessage(text) {
  const resp = await post("api/chat", { message: text });
  if (resp.status === 200) return true;
  const err = await resp.json().catch(() => ({}));
  showNotice((err.error || "Error") + " · HTTP " + resp.status);
  return false;
}

async function send() {
  const text = $("input").value.trim();
  if (!text) return;
  if (latest && latest.busy) {
    showNotice("The agent is still working. Send the message after it finishes.");
    return;
  }
  try {
    if (await sendMessage(text)) $("input").value = "";
  } catch {
    showNotice("Request failed — check the connection.");
  }
}

async function stopTurn() {
  try { await post("api/cancel"); } catch { /* state shown by polling */ }
}

async function refreshCatalog() {
  try {
    const resp = await post("api/catalog");
    if (resp.status !== 200) {
      const err = await resp.json().catch(() => ({}));
      showNotice((err.error || "Error") + " · HTTP " + resp.status);
    }
  } catch {
    showNotice("Request failed — check the connection.");
  }
}

async function newConversation() {
  if (!window.confirm("Discard the conversation history? Wallets stay on the marketplace; the next purchase creates a new wallet with the configured budget.")) return;
  try {
    const resp = await post("api/new");
    if (resp.status === 200) {
      lastMessagesRev = -1;
      lastAuditRev = -1;
      expanded = {};
      renderChat([]);
    }
  } catch { /* state shown by polling */ }
}

/* ---------- auto demo: scripted end-to-end scenario ----------
 * One click, then nothing else: discovery → cheapest auditor → failed
 * delivery → refund → repurchase → verified delivery → settlement. */
const AUTO_STEPS = [
  "What is on offer right now and at what prices?",
  "Please audit the marketplace demo cart.",
];

function startAutoDemo() {
  if (autoQueue.length) return;
  if (latest && latest.busy) {
    $("status-line").textContent = "The agent is still working; the auto demo will not start.";
    return;
  }
  autoQueue = AUTO_STEPS.slice();
  autoTotal = autoQueue.length;
  autoNextAt = Date.now() + 300;
}

function autoTick(st) {
  if (!autoQueue.length) {
    if (autoTotal > 0 && !st.busy) autoTotal = 0;
    return;
  }
  if (st.busy || Date.now() < autoNextAt) return;
  const message = autoQueue.shift();
  autoNextAt = Date.now() + 4000;
  sendMessage(message)
    .then((ok) => { if (!ok) { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; } })
    .catch(() => { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; });
}

/* ---------- polling ---------- */
async function poll() {
  try {
    // Cache-busting: the CDN in front of the domain caches responses without
    // no-store for up to 10 minutes; the parameter guarantees fresh state.
    // Lite polling carries only wallet/status/revisions; the full payload is
    // fetched when a revision changes.
    const resp = await fetch("api/state?lite=1&t=" + Date.now());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const st = await resp.json();
    if (!st || st.ok !== true) throw new Error("invalid state");
    latest = st;
    renderTopbar(st);
    $("cnt-tools").textContent = st.tool_count != null ? st.tool_count : st.tools.length;
    $("cnt-payments").textContent = st.payment_count != null ? st.payment_count : st.payments.length;
    if (st.messages_revision !== lastMessagesRev || st.audit_revision !== lastAuditRev) {
      const full = await (await fetch("api/state?t=" + Date.now())).json();
      if (full && full.ok === true) {
        fullState = full;
        latest = full;
        renderTopbar(full);
        if (full.messages_revision !== lastMessagesRev) {
          lastMessagesRev = full.messages_revision;
          renderChat(full.messages);
        }
        if (full.audit_revision !== lastAuditRev) {
          lastAuditRev = full.audit_revision;
          renderTab(full);
        }
        $("cnt-tools").textContent = full.tool_count;
        $("cnt-payments").textContent = full.payment_count;
      }
    }
    // Auto voice: speak the finished reply once per turn (needs the full state).
    if (previousBusy && !st.busy) autoSpeakIfEnabled();
    previousBusy = st.busy;
    autoTick(latest);
  } catch (e) {
    $("conn-dot").className = "dot err";
    $("conn-status").textContent = "Connection failed";
    showNotice("Server unavailable: " + e.message);
  }
  setTimeout(poll, 500);
}

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    tab = btn.dataset.tab;
    if (fullState) renderTab(fullState);
  });
});
$("btn-send").addEventListener("click", send);
$("btn-stop").addEventListener("click", stopTurn);
$("btn-auto").addEventListener("click", startAutoDemo);
$("btn-catalog").addEventListener("click", refreshCatalog);
$("btn-new").addEventListener("click", newConversation);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
const voiceBox = $("chk-voice");
voiceBox.checked = autoVoice;
voiceBox.addEventListener("change", () => {
  autoVoice = voiceBox.checked;
  localStorage.setItem("autoVoice", autoVoice ? "1" : "0");
  if (!autoVoice) stopSpeech();
});
$("input").focus();
poll();
