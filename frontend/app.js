"use strict";
// Agent console: polls the console state, renders the conversation, the seller
// board, this session's tasks, the active turn and the live tool-call feed.
// All money is simulated Lux Coins; the marketplace holds the ledger.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

let latest = null;
let fullState = null;
let lastMessagesRev = -1;
let lastAuditRev = -1;
let previousBusy = false;
let autoQueue = [];
let autoNextAt = 0;
let autoTotal = 0;
let noticeUntil = 0;
let offers = { list: [], byId: {}, sellers: {} };
let tasksFilter = "";

/* ---------- speech (ElevenLabs) ---------- */
let audioPlayer = null;
let speakIndex = -1;
let speakPhase = "idle";
let autoVoice = localStorage.getItem("autoVoice") === "1";
let lastSpokenContent = "";

function showNotice(text, ms = 5000) {
  $("status-line").textContent = text;
  noticeUntil = Date.now() + ms;
}

function refreshSpeakButtons() {
  document.querySelectorAll(".speak").forEach((btn) => {
    const index = Number(btn.dataset.index);
    if (index !== speakIndex) { btn.textContent = "🔊"; btn.disabled = false; return; }
    btn.textContent = speakPhase === "loading" ? "…" : speakPhase === "playing" ? "⏹" : speakPhase === "ready" ? "▶" : "🔊";
    btn.disabled = speakPhase === "loading";
  });
}

function stopSpeech() {
  if (audioPlayer) { audioPlayer.pause(); audioPlayer = null; }
  speakIndex = -1; speakPhase = "idle";
  refreshSpeakButtons();
}

async function speakMessage(index) {
  if (speakIndex === index && speakPhase === "ready" && audioPlayer) {
    try { await audioPlayer.play(); speakPhase = "playing"; refreshSpeakButtons(); } catch { /* stays ready */ }
    return;
  }
  if (speakIndex === index && speakPhase === "playing") { stopSpeech(); return; }
  stopSpeech();
  speakIndex = index; speakPhase = "loading";
  refreshSpeakButtons();
  try {
    const resp = await fetch("api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ index }) });
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
    try { await audioPlayer.play(); speakPhase = "playing"; }
    catch (error) {
      if (error && error.name === "NotAllowedError") { speakPhase = "ready"; showNotice("🔊 Browser blocked autoplay — click ▶ to play."); }
      else throw error;
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
    fullState = full; latest = full;
    const last = full.messages[full.messages.length - 1];
    if (last.role !== "Agent" || last.content.length < 40 || last.content === lastSpokenContent) return;
    lastSpokenContent = last.content;
    speakMessage(full.messages.length - 1);
  } catch { /* speech is optional */ }
}

/* ---------- message rendering ---------- */
function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<>")]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderBody(text) {
  const out = [];
  let fence = null, pre = [];
  const flush = () => { if (pre.length) { const el = document.createElement("pre"); el.textContent = pre.join("\n"); out.push(el); pre = []; } };
  for (const raw of String(text).split("\n")) {
    if (raw.trim().startsWith("```")) { if (fence === null) fence = true; else { flush(); fence = null; } continue; }
    if (fence !== null) { pre.push(raw); continue; }
    const line = document.createElement("span");
    line.className = "line";
    if (raw.startsWith("OK ·") || raw.startsWith("OK")) line.className += " ok-line";
    else if (raw.includes("CART BUG") || raw.includes("CHYBA")) line.className += " bad-line";
    else if (raw.trim().startsWith("•")) line.className += " info-line";
    line.innerHTML = linkify(raw === "" ? " " : raw);
    out.push(line, document.createElement("br"));
  }
  flush();
  return out;
}

function renderChat(messages) {
  const box = $("messages");
  const pinned = box.scrollTop + box.clientHeight >= box.scrollHeight - 80;
  box.replaceChildren();
  messages.forEach((message, index) => {
    const wrap = document.createElement("div");
    wrap.className = "msg " + (message.role === "You" ? "user" : "agent");
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = message.role === "You" ? "You" : "Agent · LuxAI Flash";
    const body = document.createElement("div");
    body.className = "body";
    body.append(...renderBody(message.content));
    if (message.role !== "You" && message.content.trim().length > 20) {
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
  });
  if (pinned) box.scrollTop = box.scrollHeight;
  $("conv-count").textContent = messages.length + " messages";
  refreshSpeakButtons();
}

/* ---------- sellers board ---------- */
const FALLBACK_SELLERS = [
  { id: "scout", name: "Scout", desc: "Cheapest compact seller: research, code, summaries, translation and ideas.", offers: [
    { capability: "text-summary", price: 2 }, { capability: "translation", price: 2 }, { capability: "ideas", price: 3 },
    { capability: "short-research", price: 3 }, { capability: "python-code", price: 5 }] },
  { id: "insight", name: "Insight Lab", desc: "Balanced tier with more context per delivery.", offers: [
    { capability: "translation", price: 4 }, { capability: "text-summary", price: 4 }, { capability: "ideas", price: 5 },
    { capability: "short-research", price: 6 }, { capability: "python-code", price: 9 }] },
  { id: "atlas", name: "Atlas Studio", desc: "Detailed tier: extra edge cases, plus a cart audit with receipts.", offers: [
    { capability: "translation", price: 6 }, { capability: "text-summary", price: 7 }, { capability: "ideas", price: 8 },
    { capability: "short-research", price: 9 }, { capability: "http-cart-audit", price: 10 }, { capability: "python-code", price: 14 }] },
  { id: "partial", name: "QuickCheck", desc: "Fastest cart audit. Deliberately incomplete: ships one of three promised checks.", offers: [
    { capability: "http-cart-audit", price: 3 }] },
  { id: "complete", name: "ThoroughCheck", desc: "Full three-check cart audit with execution receipts.", offers: [
    { capability: "http-cart-audit", price: 7 }] },
];
const CAPABILITY_LABEL = { "http-cart-audit": "Cart audit", "short-research": "Research", "python-code": "Code",
  "text-summary": "Summary", "translation": "Translation", "ideas": "Ideas" };
const CAPABILITY_DESC = { "http-cart-audit": "Three HTTP cart checks with execution receipts (demo sandbox).",
  "short-research": "Short brief from live web sources fetched via Apify.",
  "python-code": "Small standard-library Python function with tests (syntax-checked).",
  "text-summary": "Concise summary of the submitted text.", "translation": "Translation of the submitted text as requested.",
  "ideas": "Exactly five explained ideas." };
const CAPABILITY_ICON = {
  "http-cart-audit": '<svg viewBox="0 0 24 24"><path d="M4 5h2l2 10h10l2-7H7"/><circle cx="9.5" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/></svg>',
  "short-research": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
  "python-code": '<svg viewBox="0 0 24 24"><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4"/></svg>',
  "text-summary": '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 12h6M9 16h4M9 8h3"/></svg>',
  "translation": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.6 2.4 14.4 0 17M12 3.5c-2.4 2.6-2.4 14.4 0 17"/></svg>',
  "ideas": '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>',
};

function renderAgents() {
  const grid = $("agents-grid");
  grid.replaceChildren();
  const sellers = offers.list.length ? offers.sellers : Object.fromEntries(FALLBACK_SELLERS.map((s) => [s.id, s]));
  for (const seller of Object.values(sellers)) {
    const cheapest = seller.offers.reduce((best, offer) => (best === null || offer.price < best.price ? offer : best), null);
    const capability = cheapest ? cheapest.capability : "short-research";
    const card = document.createElement("div");
    card.className = "agent-card";
    const tile = document.createElement("div");
    tile.className = "tile " + capability;
    tile.innerHTML = CAPABILITY_ICON[capability] || CAPABILITY_ICON["short-research"];
    const head = document.createElement("div");
    head.className = "head";
    const text = document.createElement("div");
    text.innerHTML = `<div class="name">${esc(seller.name)}</div>
      <div class="state"><span class="dot ok"></span>Online</div>`;
    head.append(tile, text);
    const desc = document.createElement("div");
    desc.className = "desc";
    desc.textContent = seller.desc || CAPABILITY_DESC[capability] || "";
    const tags = document.createElement("div");
    tags.className = "tags";
    const unique = [...new Set(seller.offers.map((offer) => offer.capability))];
    unique.forEach((item) => {
      const tag = document.createElement("span");
      tag.className = "tag-pill";
      tag.textContent = CAPABILITY_LABEL[item] || item;
      tags.append(tag);
    });
    const price = document.createElement("div");
    price.className = "price";
    price.innerHTML = `${cheapest ? cheapest.price : "?"} <span>LC / task</span>`;
    const hire = document.createElement("button");
    hire.className = "hire";
    hire.textContent = "Hire";
    hire.title = `Order from ${seller.name}`;
    hire.addEventListener("click", () => {
      $("input").value = hirePrompt(capability);
      $("input").focus();
      showNotice(`Task drafted for ${seller.name} — press Send.`);
    });
    card.append(head, desc, tags, price, hire);
    grid.append(card);
  }
}

function hirePrompt(capability) {
  return {
    "http-cart-audit": "Please audit the marketplace demo cart.",
    "short-research": "Please research agentic commerce and cite the sources.",
    "python-code": "Write a Python function slugify(text) with basic tests.",
    "text-summary": "Summarize this text: The agent locks the price in escrow, verifies the delivery and only then releases the payment.",
    "translation": "Translate into English: Náš agent ověří platbu a uvolní úschovu.",
    "ideas": "Give me five ideas for agent services in Prague.",
  }[capability] || "Please audit the marketplace demo cart.";
}

/* ---------- payments / tasks ---------- */
function parsePayment(entry) {
  const parts = (entry.summary || "").split(" · ");
  const lines = entry.lines || [];
  const pick = (prefix) => {
    const line = lines.find((item) => item.startsWith(prefix));
    return line ? line.slice(prefix.length).trim() : "";
  };
  const times = [...(entry.summary || "").matchAll(/(\d{4}-\d{2}-\d{2}T[\d:.+]+)/g)];
  let stamp = pick("Ledger ID: ").split("time: ")[1] || "";
  if (!stamp) stamp = times.length ? times[times.length - 1][1] : "";
  const offerId = pick("Offer: ");
  const offer = offers.byId[offerId] || {};
  const amount = parseInt((parts[1] || "0").replace(/[^0-9]/g, ""), 10) || 0;
  return { id: entry.id, state: parts[0] || "", amount, seller: parts[2] || "", job: parts[3] || entry.id,
           capability: offer.capability || "", service: offer.service_name || CAPABILITY_LABEL[offer.capability] || "",
           stamp };
}

function formatWhen(stamp) {
  if (!stamp) return "—";
  const parsed = new Date(stamp);
  if (isNaN(parsed)) return "—";
  return parsed.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function renderTasks(payments) {
  const table = $("tasks-table");
  [...table.querySelectorAll("tr:not(:first-child)")].forEach((row) => row.remove());
  const counts = { "": payments.length, PAID: 0, REFUNDED: 0 };
  payments.forEach((entry) => {
    const payment = parsePayment(entry);
    if (counts[payment.state] !== undefined) counts[payment.state] += 1;
    const row = document.createElement("tr");
    row.dataset.state = payment.state;
    row.innerHTML = `<td><div class="title">${esc(payment.service || payment.capability || "Service")}</div>
        <div class="sub">${esc(payment.job)}</div></td>
      <td><span class="tx-title">${esc(payment.seller)}</span></td>
      <td><span class="pill ${payment.state === "PAID" ? "ok" : payment.state === "REFUNDED" ? "warn" : "neutral"}">${esc(payment.state)}</span></td>
      <td class="tx-time">${esc(formatWhen(payment.stamp))}</td>
      <td><a href="../receipt/${esc(payment.id)}" target="_blank" rel="noopener">receipt ↗</a></td>`;
    if (tasksFilter && payment.state !== tasksFilter) row.style.display = "none";
    table.append(row);
  });
  $("cnt-all").textContent = counts[""];
  $("cnt-paid").textContent = counts.PAID;
  $("cnt-refunded").textContent = counts.REFUNDED;
  if (!payments.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="5" class="sub" style="padding:14px 18px">No tasks yet — send a request or run the auto demo.</td>';
    table.append(row);
  }
}

function renderTransactions(payments) {
  const box = $("tx-body");
  box.replaceChildren();
  const recent = payments.slice(-5).reverse();
  if (!recent.length) {
    box.innerHTML = '<div class="muted pad">No transactions yet.</div>';
    return;
  }
  for (const entry of recent) {
    const payment = parsePayment(entry);
    const incoming = payment.state === "REFUNDED";
    const row = document.createElement("div");
    row.className = "tx-row";
    row.innerHTML = `<span class="tx-icon ${incoming ? "down" : "up"}">${incoming
        ? '<svg viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6"/></svg>'
        : '<svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg>'}</span>
      <span class="tx-main"><span class="tx-title">${incoming ? "Refund from" : "Payment to"} ${esc(payment.seller)}</span>
        <span class="tx-sub">${esc(payment.service || payment.capability || "service")} · ${esc(payment.job)}</span></span>
      <span class="tx-right"><span class="tx-amount ${incoming ? "down" : "up"}">${incoming ? "+" : "−"}${payment.amount} LC</span>
        <span class="tx-time">${esc(formatWhen(payment.stamp))}</span></span>`;
    row.addEventListener("click", () => window.open("../receipt/" + payment.id, "_blank", "noopener"));
    row.style.cursor = "pointer";
    box.append(row);
  }
}

/* ---------- live activity ---------- */
function renderActivity(tools, force) {
  const box = $("activity-body");
  const signature = tools.map((tool) => tool.id + tool.summary).join("|");
  if (!force && box.dataset.signature === signature) return;
  box.dataset.signature = signature;
  box.replaceChildren();
  const recent = tools.slice(-8).reverse();
  if (!recent.length) {
    box.innerHTML = '<div class="muted pad">No tool calls yet.</div>';
    return;
  }
  for (const tool of recent) {
    const parts = (tool.summary || "").split(" · ");
    const source = (parts[2] || "").toLowerCase();
    const dotClass = parts[0] === "RUNNING" ? "running" : parts[0] === "ERROR" ? "error"
      : source.includes("flash") ? "flash" : source.includes("lsl") ? "lsl" : "http";
    const row = document.createElement("div");
    row.className = "act-row";
    row.innerHTML = `<span class="act-dot ${dotClass}"></span>
      <span class="tx-main"><span class="act-name">${esc(parts[1] || tool.id)}</span>
        <span class="act-sub">${esc(parts[2] || "")}${parts[3] ? " · " + esc(parts[3]) : ""}</span></span>`;
    const name = row.querySelector(".act-name");
    name.addEventListener("click", () => {
      let detail = row.querySelector(".act-detail");
      if (detail) { detail.remove(); return; }
      detail = document.createElement("div");
      detail.className = "act-detail";
      detail.dataset.id = tool.id;
      detail.textContent = (tool.lines || []).join("\n");
      row.querySelector(".tx-main").append(detail);
    });
    box.append(row);
  }
  $("activity-count").textContent = tools.length + " calls";
}

/* ---------- active task / wallet / system ---------- */
const AVATAR_TINTS = ["green", "purple", "blue"];
function renderActive(st) {
  const busy = st.busy;
  const payments = st.payments || [];
  const percent = busy ? 42 : payments.length ? 100 : 0;
  $("active-state-pill").innerHTML = busy ? '<span class="pill blue">In progress</span>' : '<span class="pill ok">Ready</span>';
  $("active-percent").textContent = percent + "%";
  $("active-title").textContent = busy ? "Working on your request" : (payments.length ? "Last task settled" : "No task running");
  $("active-sub").textContent = (st.status || "Waiting for a request…");
  const bar = $("active-bar");
  bar.style.width = percent + "%";
  bar.style.background = busy ? "var(--amber)" : "var(--green)";
  const sellers = [...new Set(payments.map((entry) => (entry.summary || "").split(" · ")[2]).filter(Boolean))].slice(-3);
  const avatars = $("active-avatars");
  avatars.replaceChildren();
  if (sellers.length) {
    sellers.forEach((seller, index) => {
      const mini = document.createElement("span");
      mini.className = "mini " + AVATAR_TINTS[index % AVATAR_TINTS.length];
      mini.textContent = seller.slice(0, 2).toUpperCase();
      mini.title = seller;
      avatars.append(mini);
    });
    const note = document.createElement("span");
    note.className = "muted";
    note.style.cssText = "font-size:12px;align-self:center;margin-left:4px";
    note.textContent = sellers.length + " seller" + (sellers.length > 1 ? "s" : "") + " working";
    avatars.append(note);
  } else {
    const note = document.createElement("span");
    note.className = "muted";
    note.style.fontSize = "12px";
    note.textContent = "No sellers engaged yet";
    avatars.append(note);
  }
  $("active-counts").textContent = `${st.tool_count || 0} calls · ${payments.length} payments`;
}

function renderWallet(st) {
  const available = st.wallet ? st.wallet.available : null;
  const locked = st.wallet ? st.wallet.locked : 0;
  $("wallet-total").textContent = available === null ? "—" : (available + locked);
  $("wallet-available").textContent = available === null ? "—" : available + " LC";
  $("wallet-locked").textContent = locked + " LC";
  $("wallet-budget").textContent = st.budget != null ? st.budget + " LC" : "—";
}

function renderSystem(st) {
  const target = $("sys-inline");
  if (!target) return;
  const model = st.ai_model === "flash" ? "LuxAI Flash" : st.ai_model;
  target.textContent = `${model} · ${st.currency} (${st.simulated_payments ? "simulated" : "?"}) · ${st.tool_count || 0} tool calls · ${st.payment_count || 0} receipts`;
}

/* ---------- actions ---------- */
async function post(path, body) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
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
  if (latest && latest.busy) { showNotice("The agent is still working — send after it finishes."); return; }
  try {
    if (await sendMessage(text)) $("input").value = "";
  } catch {
    showNotice("Request failed — check the connection.");
  }
}

async function stopTurn() { try { await post("api/cancel"); } catch { /* polling shows state */ } }

async function refreshCatalog() {
  try {
    const resp = await post("api/catalog");
    if (resp.status !== 200) {
      const err = await resp.json().catch(() => ({}));
      showNotice((err.error || "Error") + " · HTTP " + resp.status);
    }
  } catch { showNotice("Request failed — check the connection."); }
}

async function newConversation() {
  if (!window.confirm("Start a new conversation? Wallets and the ledger stay on the marketplace; the next purchase creates a new wallet.")) return;
  try {
    const resp = await post("api/new");
    if (resp.status === 200) {
      lastMessagesRev = -1; lastAuditRev = -1; lastSpokenContent = "";
      renderChat([]);
    }
  } catch { /* polling shows state */ }
}

const AUTO_STEPS = [
  "What is on offer right now and at what prices?",
  "Please audit the marketplace demo cart.",
];

function startAutoDemo() {
  if (autoQueue.length) return;
  if (latest && latest.busy) { showNotice("The agent is still working; the auto demo will not start."); return; }
  autoQueue = AUTO_STEPS.slice();
  autoTotal = autoQueue.length;
  autoNextAt = Date.now() + 300;
}

function autoTick(st) {
  if (!autoQueue.length) { if (autoTotal > 0 && !st.busy) autoTotal = 0; return; }
  if (st.busy || Date.now() < autoNextAt) return;
  const message = autoQueue.shift();
  autoNextAt = Date.now() + 4000;
  sendMessage(message)
    .then((ok) => { if (!ok) { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; } })
    .catch(() => { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; });
}

/* ---------- polling ---------- */
async function loadOffers() {
  try {
    const resp = await fetch("../api/offers");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const list = data.offers || [];
    offers = { list, byId: {}, sellers: {} };
    for (const offer of list) {
      offers.byId[offer.id] = offer;
      const seller = offers.sellers[offer.seller_id] || (offers.sellers[offer.seller_id] = {
        id: offer.seller_id, name: offer.name, offers: [], desc: "" });
      seller.offers.push(offer);
    }
    for (const seller of Object.values(offers.sellers)) {
      seller.offers.sort((a, b) => a.price - b.price);
      const caps = [...new Set(seller.offers.map((offer) => offer.capability))];
      seller.desc = caps.map((cap) => CAPABILITY_LABEL[cap] || cap).join(" · ") + " — from " + seller.offers[0].price + " LC.";
    }
  } catch {
    offers = { list: [], byId: {}, sellers: {} };   // local dev: fall back to the built-in list
  }
  renderAgents();
}

async function poll() {
  try {
    const resp = await fetch("api/state?lite=1&t=" + Date.now());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const st = await resp.json();
    if (!st || st.ok !== true) throw new Error("invalid state");
    latest = st;
    renderWallet(st);
    renderActive(st);
    $("conn-dot").className = "dot " + (st.busy ? "busy" : "ok");
    $("conn-status").textContent = autoQueue.length || (st.busy && autoTotal > 0)
      ? "Auto demo · step " + Math.min(autoTotal - autoQueue.length, autoTotal) + "/" + autoTotal
      : st.status;
    if (Date.now() >= noticeUntil) $("status-line").textContent = (st.busy ? "⟳ " : "") + st.status;
    $("btn-send").disabled = st.busy;
    $("btn-catalog").disabled = st.busy;
    $("btn-auto").disabled = st.busy || autoQueue.length > 0;
    $("btn-stop").hidden = !st.busy;
    if (st.messages_revision !== lastMessagesRev || st.audit_revision !== lastAuditRev) {
      const full = await (await fetch("api/state?t=" + Date.now())).json();
      if (full && full.ok === true) {
        fullState = full; latest = full;
        renderWallet(full); renderActive(full); renderSystem(full);
        if (full.messages_revision !== lastMessagesRev) { lastMessagesRev = full.messages_revision; renderChat(full.messages); }
        if (full.audit_revision !== lastAuditRev) {
          lastAuditRev = full.audit_revision;
          renderTasks(full.payments || []);
          renderTransactions(full.payments || []);
          renderActivity(full.tools || []);
        }
      }
    }
    if (previousBusy && !st.busy) autoSpeakIfEnabled();
    previousBusy = st.busy;
    autoTick(latest);
  } catch (error) {
    $("conn-dot").className = "dot err";
    $("conn-status").textContent = "Connection failed";
    showNotice("Server unavailable: " + error.message);
  }
  setTimeout(poll, 500);
}

/* ---------- wiring ---------- */
document.querySelectorAll(".chip[data-fill]").forEach((chip) => {
  chip.addEventListener("click", () => { $("input").value = chip.dataset.fill; $("input").focus(); });
});
document.querySelectorAll("#tasks-filters .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#tasks-filters .chip").forEach((other) => other.classList.remove("active"));
    chip.classList.add("active");
    tasksFilter = chip.dataset.state;
    if (fullState) renderTasks(fullState.payments || []);
  });
});
$("btn-send").addEventListener("click", send);
$("btn-stop").addEventListener("click", stopTurn);
$("btn-auto").addEventListener("click", startAutoDemo);
$("btn-catalog").addEventListener("click", refreshCatalog);
$("btn-new").addEventListener("click", newConversation);
$("btn-new-2").addEventListener("click", newConversation);
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
});
const voiceBox = $("chk-voice");
voiceBox.checked = autoVoice;
voiceBox.addEventListener("change", () => {
  autoVoice = voiceBox.checked;
  localStorage.setItem("autoVoice", autoVoice ? "1" : "0");
  if (!autoVoice) stopSpeech();
});
$("input").focus();
loadOffers();
poll();
