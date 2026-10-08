"use strict";
// Lux Coins workspace console. Polls the console state and renders the
// conversation, this session's tasks, the seller board, the live tool-call feed
// and the wallet. Payments are simulated Lux Coins held by the marketplace.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

let latest = null;
let fullState = null;
let lastMessagesRev = -1;
let lastAuditRev = -1;
let previousBusy = false;
let lastNotificationsRev = -1;
let autoQueue = [];
let autoNextAt = 0;
let noticeUntil = 0;
let tasksFilter = "";
let account = null;

function token() { return localStorage.getItem("lux_token") || ""; }

function apiFetch(path, options) {
  const merged = Object.assign({}, options || {});
  const headers = Object.assign({}, (options && options.headers) || {});
  if (token()) headers.Authorization = "Bearer " + token();
  merged.headers = headers;
  return fetch(path, merged);
}
let searchTerm = "";
let offers = { list: [], byId: {}, sellers: {} };

/* ---------- capability presentation ---------- */
const CAPABILITY = {
  "http-cart-audit": { label: "Cart audit", tile: "data", short: "Three HTTP checks with receipts",
    prompt: "Please audit the marketplace demo cart." },
  "short-research": { label: "Research", tile: "research", short: "Live web sources via Apify",
    prompt: "Please research agentic commerce and cite the sources." },
  "python-code": { label: "Code", tile: "code", short: "Syntax-checked code with tests",
    prompt: "Write a Python function slugify(text) with basic tests." },
  "text-summary": { label: "Summary", tile: "design", short: "Concise summary of your text",
    prompt: "Summarize this text: The agent locks the price in escrow, verifies the delivery and only then releases the payment." },
  "translation": { label: "Translation", tile: "automation", short: "Any language pair you name",
    prompt: "Translate into English: Náš agent ověří platbu a uvolní úschovu." },
  "ideas": { label: "Ideas", tile: "design", short: "Five explained ideas",
    prompt: "Give me five ideas for agent services in Prague." },
};
const ICON = {
  "http-cart-audit": '<svg viewBox="0 0 24 24"><path d="M4 5h2l2 10h10l2-7H7"/><circle cx="9.5" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/></svg>',
  "short-research": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
  "python-code": '<svg viewBox="0 0 24 24"><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4"/></svg>',
  "text-summary": '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 12h6M9 16h4M9 8h3"/></svg>',
  "translation": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.6 2.4 14.4 0 17M12 3.5c-2.4 2.6-2.4 14.4 0 17"/></svg>',
  "ideas": '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>',
  "agent": '<svg viewBox="0 0 24 24"><path d="M12 3 21 19H3Z"/></svg>',
};
const QUICK = ["http-cart-audit", "short-research", "python-code", "text-summary", "translation"];

/* ---------- speech (ElevenLabs) ---------- */
let audioPlayer = null;
let speakIndex = -1;
let speakPhase = "idle";
let autoVoice = localStorage.getItem("autoVoice") === "1";
let lastSpokenContent = "";

function showNotice(text) { $("status-line").textContent = text; noticeUntil = Date.now() + 6000; }

function refreshSpeakButtons() {
  document.querySelectorAll(".speak").forEach((button) => {
    const index = Number(button.dataset.index);
    if (index !== speakIndex) { button.textContent = "🔊"; button.disabled = false; return; }
    button.textContent = speakPhase === "loading" ? "…" : speakPhase === "playing" ? "⏹" : speakPhase === "ready" ? "▶" : "🔊";
    button.disabled = speakPhase === "loading";
  });
}

function stopSpeech() {
  if (audioPlayer) { audioPlayer.pause(); audioPlayer = null; }
  speakIndex = -1;
  speakPhase = "idle";
  refreshSpeakButtons();
}

async function speakMessage(index) {
  if (speakIndex === index && speakPhase === "ready" && audioPlayer) {
    try { await audioPlayer.play(); speakPhase = "playing"; refreshSpeakButtons(); } catch { /* keep ready */ }
    return;
  }
  if (speakIndex === index && speakPhase === "playing") { stopSpeech(); return; }
  stopSpeech();
  speakIndex = index;
  speakPhase = "loading";
  refreshSpeakButtons();
  try {
    const resp = await apiFetch("api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ index }) });
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
    fullState = full;
    latest = full;
    const last = full.messages[full.messages.length - 1];
    if (last.role !== "Agent" || last.content.length < 40 || last.content === lastSpokenContent) return;
    lastSpokenContent = last.content;
    speakMessage(full.messages.length - 1);
  } catch { /* speech is optional */ }
}

/* ---------- conversation ---------- */
function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<>")]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderBody(text) {
  const out = [];
  let fence = null;
  let pre = [];
  const flush = () => { if (pre.length) { const el = document.createElement("pre"); el.textContent = pre.join("\n"); out.push(el); pre = []; } };
  for (const raw of String(text).split("\n")) {
    if (raw.trim().startsWith("```")) { if (fence === null) fence = true; else { flush(); fence = null; } continue; }
    if (fence !== null) { pre.push(raw); continue; }
    const line = document.createElement("span");
    line.className = "line";
    if (raw.startsWith("OK ·") || raw.startsWith("OK")) line.className += " ok-line";
    else if (raw.includes("AUDIT FINDING") || raw.includes("NÁLEZ")) line.className += " find-line";
    else if (raw.includes("CART BUG") || raw.includes("CHYBA")) line.className += " bad-line";
    else if (raw.trim().startsWith("•")) line.className += " info-line";
    line.innerHTML = linkify(raw === "" ? " " : raw);
    out.push(line, document.createElement("br"));
  }
  flush();
  return out;
}

const DELIVERY_RE = /^(?:Done|Hotovo) · /;

function deliveryPayment(headerLine) {
  const parts = headerLine.split(" · ");
  const seller = parts[1];
  const amount = parseInt((parts[2] || "").replace(/[^0-9]/g, ""), 10);
  const payments = (fullState && fullState.payments) || [];
  for (let index = payments.length - 1; index >= 0; index -= 1) {
    const payment = parsePayment(payments[index]);
    if (payment.seller === seller && payment.amount === amount) return payment.id;
  }
  return null;
}

function renderChat(messages) {
  const box = $("messages");
  const pinned = box.scrollTop + box.clientHeight >= box.scrollHeight - 80;
  box.replaceChildren();
  messages.forEach((message, index) => {
    const content = message.content || "";
    const firstLine = content.split("\n")[0].trim();
    const isDelivery = message.role !== "You" && DELIVERY_RE.test(firstLine);
    const tooLong = message.role !== "You" && content.length > 420;

    if (isDelivery || tooLong) {
      // Kompaktní karta: první řádek + přepínač textu; dlouhé dodávky nezabírají chat.
      const card = document.createElement("div");
      card.className = "msg agent compact";
      const head = document.createElement("div");
      head.className = "head";
      if (isDelivery) {
        const tick = document.createElement("span");
        tick.className = "tick";
        tick.textContent = "✓";
        head.append(tick);
      }
      const title = document.createElement("b");
      title.textContent = firstLine.length > 120 ? firstLine.slice(0, 117) + "…" : firstLine;
      const toggle = document.createElement("button");
      toggle.className = "toggle";
      toggle.textContent = "show text ▸";
      toggle.addEventListener("click", () => {
        card.classList.toggle("open");
        toggle.textContent = card.classList.contains("open") ? "hide text ▾" : "show text ▸";
      });
      head.append(title, toggle);
      if (isDelivery) {
        const jobId = deliveryPayment(firstLine);
        if (jobId) {
          const view = document.createElement("button");
          view.className = "view";
          view.textContent = "open delivery";
          view.addEventListener("click", () => openDelivery(jobId));
          head.append(view);
        }
      }
      const body = document.createElement("div");
      body.className = "body";
      body.append(...renderBody(content));
      card.append(head, body);
      box.append(card);
      return;
    }

    const row = document.createElement("div");
    row.className = "msg " + (message.role === "You" ? "user" : "agent");
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = message.role === "You" ? "You" : "Agent · LuxAI Flash";
    const body = document.createElement("div");
    body.className = "body";
    body.append(...renderBody(content));
    if (message.role !== "You" && content.trim().length > 20) {
      const speak = document.createElement("button");
      speak.className = "speak";
      speak.dataset.index = String(index);
      speak.title = "Read aloud with ElevenLabs";
      speak.textContent = "🔊";
      speak.addEventListener("click", () => speakMessage(index));
      body.append(speak);
    }
    row.append(role, body);
    box.append(row);
  });
  if (pinned) box.scrollTop = box.scrollHeight;
  $("conv-count").textContent = messages.length + " messages";
  refreshSpeakButtons();
}

function renderToolStrip(tools) {
  const strip = $("tool-strip");
  const signature = tools.map((tool) => tool.id + tool.summary).join("|");
  if (strip.dataset.signature === signature) return;
  strip.dataset.signature = signature;
  strip.replaceChildren();
  for (const tool of tools.slice(-12).reverse()) {
    const parts = (tool.summary || "").split(" · ");
    const chip = document.createElement("span");
    chip.className = "tool-chip" + (parts[0] === "RUNNING" ? " running" : parts[0] === "ERROR" ? " error" : "");
    chip.title = tool.summary;
    chip.textContent = (parts[1] || "tool") + (parts[3] ? " · " + parts[3] : "");
    strip.append(chip);
  }
}

/* ---------- payments helpers ---------- */
function parsePayment(entry) {
  const parts = (entry.summary || "").split(" · ");
  const lines = entry.lines || [];
  const pick = (prefix) => {
    const line = lines.find((item) => item.startsWith(prefix));
    return line ? line.slice(prefix.length).trim() : "";
  };
  let stamp = pick("Ledger ID: ").split("time: ")[1] || "";
  if (!stamp) {
    const matches = [...(entry.summary || "").matchAll(/(\d{4}-\d{2}-\d{2}T[\d:.+]+)/g)];
    stamp = matches.length ? matches[matches.length - 1][1] : "";
  }
  const offer = offers.byId[pick("Offer: ")] || {};
  const amount = parseInt((parts[1] || "0").replace(/[^0-9]/g, ""), 10) || 0;
  return { id: entry.id, state: parts[0] || "", amount, seller: parts[2] || "", job: parts[3] || entry.id,
           capability: offer.capability || "short-research", stamp };
}

function formatWhen(stamp) {
  if (!stamp) return "—";
  const parsed = new Date(stamp);
  return isNaN(parsed) ? "—" : parsed.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function relative(stamp) {
  if (!stamp) return "—";
  const parsed = new Date(stamp);
  if (isNaN(parsed)) return "—";
  const seconds = Math.max(0, (Date.now() - parsed.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
  if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
  return Math.round(seconds / 86400) + " d ago";
}

/* ---------- tasks table ---------- */
function renderTasks(payments) {
  const body = $("tasks-table").querySelector("tbody");
  body.replaceChildren();
  const counts = { "": payments.length, PAID: 0, REFUNDED: 0 };
  for (const entry of payments) {
    const payment = parsePayment(entry);
    if (counts[payment.state] !== undefined) counts[payment.state] += 1;
    const meta = CAPABILITY[payment.capability] || { label: "Service", tile: "research" };
    const row = document.createElement("tr");
    row.dataset.state = payment.state;
    row.dataset.search = (payment.job + " " + payment.seller + " " + meta.label).toLowerCase();
    row.innerHTML = `<td><div class="task-cell"><span class="agent-icon ${meta.tile}">${ICON[payment.capability] || ICON.agent}</span>
        <span><b>${esc(meta.label)}</b><small>${esc(payment.job)}</small></span></div></td>
      <td><span class="avatars"><span class="avatar">${esc(payment.seller.slice(0, 2).toUpperCase())}</span></span></td>
      <td><span class="badge ${payment.state === "PAID" ? "completed" : "failed"}">${payment.state === "PAID" ? "Paid" : "Refunded"}</span></td>
      <td>${payment.amount}.00 LC</td>
      <td>${esc(relative(payment.stamp))}</td>
      <td><a class="text-button" href="../receipt/${esc(payment.id)}" target="_blank" rel="noopener">receipt →</a></td>`;
    row.addEventListener("click", (event) => {
      if (event.target.tagName === "A") return;
      openDelivery(payment.id);
    });
    if ((tasksFilter && payment.state !== tasksFilter) || !row.dataset.search.includes(searchTerm)) row.style.display = "none";
    body.append(row);
  }
  $("cnt-all").textContent = counts[""];
  $("cnt-paid").textContent = counts.PAID;
  $("cnt-refunded").textContent = counts.REFUNDED;
  if (!payments.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6"><div class="empty">No tasks yet — create one or run the auto demo.</div></td>';
    body.append(row);
  }
}

/* ---------- stats, balance ---------- */
function renderStats(st) {
  const payments = (fullState && fullState.payments) || [];
  const parsed = payments.map(parsePayment);
  const paid = parsed.filter((payment) => payment.state === "PAID");
  const busy = st.busy;
  $("stat-active").textContent = busy ? "1" : "0";
  $("stat-active-note").textContent = busy ? "In progress" : "Nothing running";
  $("stat-completed").textContent = String(paid.length);
  $("stat-completed-note").textContent = paid.length ? "Paid in this session" : "Delivered in this session";
  $("stat-spent").textContent = paid.reduce((sum, payment) => sum + payment.amount, 0) + " LC";
  const sellers = Object.keys(offers.sellers).length || 5;
  $("stat-agents").textContent = String(sellers);
  $("stat-agents-note").textContent = busy ? "1 running" : "Ready for a new task";
}

function renderWallet(st) {
  const available = st.wallet ? st.wallet.available : null;
  const locked = st.wallet ? st.wallet.locked : 0;
  const budget = st.budget != null ? st.budget : null;
  $("side-balance").innerHTML = (available === null ? "—" : available) + "<small> LC</small>";
  $("balance-total").innerHTML = (available === null ? "—" : available + locked) + "<small> LC</small>";
  $("balance-note").textContent = available === null ? "Demo credits only"
    : `available ${available} · in escrow ${locked}${budget ? " · budget " + budget : ""}`;
  $("balance-bar").style.width = available === null || !budget ? "100%" : Math.min(100, Math.round(((available + locked) / budget) * 100)) + "%";
  const field = $("task-budget");
  if (available === null) {
    field.value = budget ? budget + " LC budget · wallet created on first purchase" : "no wallet yet";
  } else {
    field.value = available + " LC available" + (locked ? " · " + locked + " LC in escrow" : "")
      + (budget ? "  (wallet budget " + budget + " LC)" : "");
  }
}

/* ---------- sellers board ---------- */
const FALLBACK_SELLERS = {
  scout: { id: "scout", name: "Scout", offers: [{ capability: "text-summary", price: 2 }, { capability: "translation", price: 2 },
    { capability: "ideas", price: 3 }, { capability: "short-research", price: 3 }, { capability: "python-code", price: 5 }] },
  insight: { id: "insight", name: "Insight Lab", offers: [{ capability: "translation", price: 4 }, { capability: "text-summary", price: 4 },
    { capability: "ideas", price: 5 }, { capability: "short-research", price: 6 }, { capability: "python-code", price: 9 }] },
  atlas: { id: "atlas", name: "Atlas Studio", offers: [{ capability: "translation", price: 6 }, { capability: "text-summary", price: 7 },
    { capability: "ideas", price: 8 }, { capability: "short-research", price: 9 }, { capability: "http-cart-audit", price: 10 }, { capability: "python-code", price: 14 }] },
  partial: { id: "partial", name: "QuickCheck", offers: [{ capability: "http-cart-audit", price: 3 }] },
  complete: { id: "complete", name: "ThoroughCheck", offers: [{ capability: "http-cart-audit", price: 7 }] },
};

function renderAgents() {
  const grid = $("agents-grid");
  grid.replaceChildren();
  const sellers = offers.list.length ? offers.sellers : FALLBACK_SELLERS;
  for (const seller of Object.values(sellers)) {
    const cheapest = seller.offers.reduce((best, offer) => (best === null || offer.price < best.price ? offer : best), null);
    const capability = cheapest ? cheapest.capability : "short-research";
    const meta = CAPABILITY[capability] || CAPABILITY["short-research"];
    const tags = [...new Set(seller.offers.map((offer) => (CAPABILITY[offer.capability] || {}).label).filter(Boolean))];
    const card = document.createElement("div");
    card.className = "agent-card";
    card.dataset.search = (seller.name + " " + tags.join(" ")).toLowerCase();
    card.innerHTML = `<div class="agent-card-head"><span class="agent-icon ${meta.tile}">${ICON[capability] || ICON.agent}</span>
        <span><b>${esc(seller.name)}</b><p>${esc(tags.slice(0, 3).join(", ")) || "Services"}</p></span></div>
      <div class="agent-card-bottom"><span class="available">Available</span><span>${cheapest ? cheapest.price : "?"}.00 LC / task</span></div>`;
    const hire = document.createElement("button");
    hire.className = "hire";
    hire.textContent = "Hire";
    hire.addEventListener("click", () => openDialog(seller.offers[0].capability, seller));
    card.append(hire);
    if (!card.dataset.search.includes(searchTerm)) card.style.display = "none";
    grid.append(card);
  }
}

/* ---------- quick start ---------- */
function renderQuick() {
  const list = $("quick-list");
  list.replaceChildren();
  for (const capability of QUICK) {
    const meta = CAPABILITY[capability];
    const item = document.createElement("button");
    item.type = "button";
    item.className = "quick-item";
    item.innerHTML = `<span class="agent-icon ${meta.tile}">${ICON[capability]}</span>
      <span><b>${esc(meta.label)}</b><small>${esc(meta.short)}</small></span><span class="chev">›</span>`;
    item.addEventListener("click", () => openDialog(capability));
    list.append(item);
  }
}

/* ---------- activity ---------- */
function renderActivity(tools) {
  const box = $("activity-list");
  const signature = tools.map((tool) => tool.id + tool.summary).join("|");
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature;
  box.replaceChildren();
  const recent = tools.slice(-6).reverse();
  if (!recent.length) {
    box.innerHTML = '<div class="empty">No tool calls yet.</div>';
    return;
  }
  for (const tool of recent) {
    const parts = (tool.summary || "").split(" · ");
    const source = (parts[2] || "").toLowerCase();
    const tile = parts[0] === "ERROR" ? "design" : source.includes("flash") ? "research" : source.includes("lsl") ? "data" : "code";
    const glyph = parts[0] === "ERROR" ? ICON.ideas : source.includes("flash") ? ICON["short-research"]
      : source.includes("lsl") ? ICON["http-cart-audit"] : ICON["python-code"];
    const started = (tool.lines || []).find((line) => line.startsWith("Started: "));
    const stamp = started ? new Date(parseFloat(started.slice(9).trim()) * 1000) : null;
    const item = document.createElement("div");
    item.className = "activity-item";
    item.innerHTML = `<span class="agent-icon ${tile}">${glyph}</span>
      <span><b>${esc(parts[1] || tool.id)}</b><p>${esc(parts[2] || "")}${parts[3] ? " · " + esc(parts[3]) : ""}</p></span>
      <time>${stamp ? stamp.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : ""}</time>`;
    const name = item.querySelector("b");
    name.style.cursor = "pointer";
    name.addEventListener("click", () => {
      const existing = item.querySelector(".activity-detail");
      if (existing) { existing.remove(); return; }
      const detail = document.createElement("div");
      detail.className = "activity-detail";
      detail.textContent = (tool.lines || []).join("\n");
      item.querySelector("span:nth-child(2)").append(detail);
    });
    box.append(item);
  }
}

function renderSystem(st) {
  $("sys-inline").textContent = `LuxAI Flash · ${st.currency} (${st.simulated_payments ? "simulated" : "?"}) · ${st.tool_count || 0} tool calls · ${st.payment_count || 0} receipts · structural checks only`;
}

/* ---------- speech helpers for deliveries ---------- */
let deliveryAudio = null;
let deliveryJob = "";
let deliverySpeakState = "idle";

function refreshDeliverySpeak() {
  const button = $("delivery-speak");
  if (!button) return;
  button.textContent = deliverySpeakState === "loading" ? "🔊 preparing…"
    : deliverySpeakState === "playing" ? "⏹ Stop" : deliverySpeakState === "ready" ? "▶ Play" : "🔊 Read aloud";
  button.disabled = deliverySpeakState === "loading";
}

function stopDeliveryAudio() {
  if (deliveryAudio) { deliveryAudio.pause(); deliveryAudio = null; }
  deliverySpeakState = "idle";
  refreshDeliverySpeak();
}

async function speakDelivery() {
  if (!deliveryJob) return;
  if (deliverySpeakState === "playing") { stopDeliveryAudio(); return; }
  if (deliverySpeakState === "ready" && deliveryAudio) {
    try { await deliveryAudio.play(); deliverySpeakState = "playing"; refreshDeliverySpeak(); } catch { /* zustava ready */ }
    return;
  }
  stopDeliveryAudio();
  deliverySpeakState = "loading";
  refreshDeliverySpeak();
  try {
    const resp = await apiFetch("api/speak", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: deliveryJob }) });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      stopDeliveryAudio();
      showNotice("🔊 " + (err.error || "Text-to-speech failed") + " (HTTP " + resp.status + ")");
      return;
    }
    const url = URL.createObjectURL(await resp.blob());
    deliveryAudio = new Audio(url);
    deliveryAudio.addEventListener("ended", () => { URL.revokeObjectURL(url); stopDeliveryAudio(); });
    deliveryAudio.addEventListener("error", () => { URL.revokeObjectURL(url); stopDeliveryAudio(); });
    try { await deliveryAudio.play(); deliverySpeakState = "playing"; }
    catch (error) {
      if (error && error.name === "NotAllowedError") { deliverySpeakState = "ready"; showNotice("🔊 Browser blocked autoplay — click ▶ Play."); }
      else throw error;
    }
    refreshDeliverySpeak();
  } catch {
    stopDeliveryAudio();
    showNotice("🔊 Text-to-speech failed");
  }
}

/* ---------- voice input (ElevenLabs Scribe) ---------- */
let recorder = null;
let recordedChunks = [];

async function toggleRecording() {
  const button = $("task-mic");
  const status = $("mic-status");
  if (recorder && recorder.state === "recording") { recorder.stop(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    status.textContent = "This browser cannot record audio.";
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const name = (error && error.name) || "Error";
    status.textContent = name === "NotAllowedError"
      ? "Microphone is blocked for this site. Allow it via the padlock icon in the address bar, then click Dictate again."
      : "Microphone unavailable (" + name + ") — check the device and browser permissions.";
    return;
  }
  recordedChunks = [];
  recorder = new MediaRecorder(stream);
  recorder.addEventListener("dataavailable", (event) => { if (event.data && event.data.size) recordedChunks.push(event.data); });
  recorder.addEventListener("stop", async () => {
    stream.getTracks().forEach((track) => track.stop());
    button.classList.remove("recording");
    button.disabled = true;
    button.textContent = "🎤 Transcribing…";
    status.textContent = "Transcribing with ElevenLabs Scribe…";
    try {
      const blob = new Blob(recordedChunks, { type: recorder.mimeType || "audio/webm" });
      const form = new FormData();
      form.append("file", blob, "task.webm");
      form.append("model_id", "scribe_v2");
      const resp = await apiFetch("api/transcribe", { method: "POST", body: form });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        status.textContent = (data.error || "Transcription failed") + " (HTTP " + resp.status + ")";
      } else if (!data.text) {
        status.textContent = "Nothing intelligible was recognised — try again.";
      } else {
        const field = $("task-description");
        field.value = (field.value ? field.value.trim() + " " : "") + data.text;
        status.textContent = "Dictated: “" + data.text.slice(0, 90) + (data.text.length > 90 ? "…" : "") + "”";
      }
    } catch (error) {
      status.textContent = "Transcription failed: " + error.message;
    }
    button.disabled = false;
    button.textContent = "🎤 Dictate";
  });
  recorder.start();
  button.classList.add("recording");
  button.textContent = "⏹ Stop dictation";
  status.textContent = "Recording… speak your task, then stop.";
}

/* ---------- purchased delivery dialog ---------- */
function metaRow(label, value) {
  const span = document.createElement("span");
  span.innerHTML = esc(label) + " <b>" + esc(value) + "</b>";
  return span;
}

function artifactBlocks(artifact) {
  const blocks = [];
  if (!artifact) return blocks;
  const section = (title, content) => {
    const wrap = document.createElement("div");
    wrap.className = "delivery-section";
    if (title) {
      const head = document.createElement("h3");
      head.textContent = title;
      wrap.append(head);
    }
    wrap.append(content);
    blocks.push(wrap);
  };
  if (artifact.summary) {
    const text = document.createElement("div");
    text.className = "delivery-text";
    text.textContent = artifact.summary;
    section("What was delivered", text);
  }
  if (artifact.content) {
    const text = document.createElement("div");
    text.className = "delivery-text";
    text.textContent = artifact.content;
    section(artifact.summary ? "Full text" : "What was delivered", text);
  }
  if (Array.isArray(artifact.ideas) && artifact.ideas.length) {
    const list = document.createElement("ol");
    for (const idea of artifact.ideas) {
      const item = document.createElement("li");
      item.textContent = idea;
      list.append(item);
    }
    section("Ideas", list);
  }
  if (artifact.code) {
    const pre = document.createElement("pre");
    pre.textContent = artifact.code;
    section("Python code", pre);
  }
  if (artifact.tests) {
    const pre = document.createElement("pre");
    pre.textContent = artifact.tests;
    section("Tests (syntax-checked, not executed)", pre);
  }
  if (Array.isArray(artifact.sources) && artifact.sources.length) {
    const list = document.createElement("ul");
    for (const source of artifact.sources) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = source.title + " — " + source.url;
      item.append(link);
      list.append(item);
    }
    section("Sources fetched", list);
  }
  return blocks;
}

function renderDelivery(data) {
  const job = data.job || {};
  const artifact = (job.result || {}).artifact;
  const verification = data.verification || {};
  const capability = (job.contract || {}).capability;
  const meta = CAPABILITY[capability] || { label: "Service" };
  $("delivery-title").textContent = meta.label + " · " + (job.seller_id || "");
  const body = $("delivery-body");
  body.replaceChildren();

  const info = document.createElement("div");
  info.className = "delivery-meta";
  info.append(metaRow("Amount", job.price + " LC"), metaRow("State", job.state),
    metaRow("Seller", job.seller_id || ""), metaRow("Job", job.id || ""),
    metaRow("When", job.created ? new Date(job.created * 1000).toLocaleString("en-GB") : "—"));

  const verdict = document.createElement("div");
  verdict.className = "delivery-section";
  const badge = document.createElement("span");
  badge.className = "delivery-badge" + (verification.valid_delivery ? "" : " warn");
  badge.textContent = verification.valid_delivery ? "verified & paid" : "rejected — refunded";
  verdict.append(badge);

  body.append(info, verdict);
  for (const block of artifactBlocks(artifact)) body.append(block);

  const checks = verification.checks || [];
  if (checks.length) {
    const section = document.createElement("div");
    section.className = "delivery-section";
    const head = document.createElement("h3");
    head.textContent = "Contracted cart checks";
    const table = document.createElement("table");
    table.className = "checks-table";
    table.innerHTML = "<tr><th>Case</th><th>Expected</th><th>Observed</th><th>Result</th></tr>"
      + checks.map((check) => `<tr><td>${esc(check.case_id)}</td><td>${check.expected_cents}</td>
          <td>${check.observed_cents}</td><td>${check.passed ? "OK" : "DEFECT FOUND"}</td></tr>`).join("");
    section.append(head, table);
    body.append(section);
  }
  if (!artifact && !checks.length) {
    const note = document.createElement("p");
    note.className = "text-muted";
    note.textContent = "No artifact stored for this task — open the full receipt for the ledger trail.";
    body.append(note);
  }
  const scope = verification.scope;
  if (scope) {
    const note = document.createElement("p");
    note.className = "text-muted";
    note.style.marginTop = "10px";
    note.textContent = scope;
    body.append(note);
  }
}

async function openDelivery(jobId) {
  const dialog = $("delivery-dialog");
  deliveryJob = jobId;
  stopDeliveryAudio();
  $("delivery-title").textContent = "Loading delivery…";
  $("delivery-receipt").href = "../receipt/" + jobId;
  $("delivery-body").innerHTML = '<p class="text-muted">Fetching the delivery from the public receipt…</p>';
  dialog.showModal();
  try {
    const resp = await fetch("../api/receipt/" + encodeURIComponent(jobId));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    renderDelivery(await resp.json());
  } catch (error) {
    $("delivery-body").innerHTML = '<p class="text-muted">Could not load this delivery (' + esc(error.message) + '). Open the full receipt instead.</p>';
  }
}

/* ---------- task dialog ---------- */
function openDialog(capability, seller) {
  const select = $("task-agent");
  const current = seller ? seller.id : "";
  select.replaceChildren();
  const cheapest = document.createElement("option");
  cheapest.value = "";
  cheapest.textContent = "Cheapest matching seller";
  select.append(cheapest);
  for (const item of Object.values(offers.list.length ? offers.sellers : FALLBACK_SELLERS)) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    select.append(option);
  }
  select.value = current;
  if (capability && CAPABILITY[capability]) $("task-description").value = CAPABILITY[capability].prompt;
  else if (!$("task-description").value) $("task-description").value = "";
  $("task-dialog").showModal();
  $("task-description").focus();
}

async function submitTask() {
  const description = $("task-description").value.trim();
  if (description.length < 5) return;
  const sellerId = $("task-agent").value;
  const seller = sellerId ? (offers.sellers[sellerId] || FALLBACK_SELLERS[sellerId]) : null;
  const message = (seller ? `Prefer ${seller.name}'s offer. ` : "") + description;
  $("task-dialog").close();
  try {
    const resp = await apiFetch("api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    if (resp.status !== 200) {
      const err = await resp.json().catch(() => ({}));
      showNotice((err.error || "Error") + " · HTTP " + resp.status);
    } else {
      showNotice("Task sent — the agent is working.");
    }
  } catch {
    showNotice("Request failed — check the connection.");
  }
}

/* ---------- actions ---------- */
async function post(path, body) {
  return apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

async function sendMessage(text) {
  const resp = await post("api/chat", { message: text });
  if (resp.status === 200) return true;
  const err = await resp.json().catch(() => ({}));
  showNotice((err.error || "Error") + " · HTTP " + resp.status);
  return false;
}

async function stopTurn() { try { await post("api/cancel"); } catch { /* state follows */ } }

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
    if (resp.status === 200) { lastMessagesRev = -1; lastAuditRev = -1; lastSpokenContent = ""; renderChat([]); }
  } catch { /* state follows */ }
}

const AUTO_STEPS = ["What is on offer right now and at what prices?", "Please audit the marketplace demo cart."];

function startAutoDemo() {
  if (autoQueue.length) return;
  if (latest && latest.busy) { showNotice("The agent is still working; the auto demo will not start."); return; }
  autoQueue = AUTO_STEPS.slice();
  autoNextAt = Date.now() + 300;
}

function autoTick(st) {
  if (!autoQueue.length) return;
  if (st.busy || Date.now() < autoNextAt) return;
  const message = autoQueue.shift();
  autoNextAt = Date.now() + 4000;
  sendMessage(message).then((ok) => { if (!ok) { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; } })
    .catch(() => { autoQueue.unshift(message); autoNextAt = Date.now() + 6000; });
}

/* ---------- notifications (bell) ---------- */
function seenNotification() { return localStorage.getItem("notificationsSeen") || ""; }

function unreadCount(notifications) {
  const seen = seenNotification();
  const seenNumber = seen ? parseInt(seen.replace("n-", ""), 10) || 0 : 0;
  return notifications.filter((item) => (parseInt((item.id || "").replace("n-", ""), 10) || 0) > seenNumber).length;
}

const NOTIFICATION_ICON = { paid: "▸", refund: "↩", done: "✓", error: "!", info: "i" };

function renderNotifications(st) {
  const list = $("bell-list");
  if (!list) return;
  const notifications = (fullState && fullState.notifications) || [];
  list.replaceChildren();
  if (!notifications.length) {
    list.innerHTML = '<div class="bell-empty">No notifications yet — create a task or run the auto demo.</div>';
  } else {
    for (const item of [...notifications].reverse()) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "bell-item " + (item.kind || "info");
      const when = item.time ? new Date(item.time * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";
      row.innerHTML = '<span class="dot-badge"></span><span><b>' + esc(item.text || "") + '</b>'
        + (item.job_id ? '<small>job ' + esc(item.job_id) + ' · click to open the delivery</small>' : '')
        + '</span><time>' + esc(when) + '</time>';
      if (item.job_id) row.addEventListener("click", () => { $("bell-menu").hidden = true; openDelivery(item.job_id); });
      else row.addEventListener("click", () => { $("bell-menu").hidden = true; });
      list.append(row);
    }
  }
  const unread = unreadCount(notifications);
  const count = $("bell-count");
  count.hidden = unread === 0;
  count.textContent = unread === 0 ? "" : unread > 9 ? "9+" : String(unread);
}

function markNotificationsSeen() {
  const notifications = (fullState && fullState.notifications) || [];
  if (notifications.length) localStorage.setItem("notificationsSeen", notifications[notifications.length - 1].id);
  renderNotifications(fullState || {});
}

/* ---------- accounts: auth screens, welcome, guide, top-up ---------- */
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  const form = $("auth-form");
  if (form) {
    form.style.animation = "none";
    void form.offsetHeight;
    form.style.animation = "";
  }
  document.querySelectorAll(".auth-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === mode));
  $("auth-submit").textContent = mode === "register" ? "Create account" : "Sign in";
  $("auth-password").setAttribute("autocomplete", mode === "register" ? "new-password" : "current-password");
  $("auth-error").textContent = "";
}

function showAuth(message) {
  $("auth-overlay").hidden = false;
  $("auth-error").textContent = message || "";
  $("auth-username").focus();
}

function hideAuth() {
  const overlay = $("auth-overlay");
  if (overlay.hidden || overlay.classList.contains("closing")) return;
  overlay.classList.add("closing");
  setTimeout(() => {
    overlay.hidden = true;
    overlay.classList.remove("closing");
  }, 300);
}

function hideWelcome() {
  const overlay = $("welcome-overlay");
  overlay.classList.add("closing");
  setTimeout(() => {
    overlay.hidden = true;
    overlay.classList.remove("closing");
  }, 300);
}

function applyAccount(user) {
  account = user;
  $("profile-name").textContent = user.username;
  $("profile-avatar").textContent = user.username.slice(0, 1).toUpperCase();
  $("pm-username").textContent = user.username;
  $("pm-budget").textContent = "budget " + user.budget + " LC";
  $("topup-current").textContent = "Current account budget: " + user.budget + " LC";
}

async function authSubmit(event) {
  event.preventDefault();
  const username = $("auth-username").value.trim().toLowerCase();
  const password = $("auth-password").value;
  $("auth-submit").disabled = true;
  try {
    const resp = await fetch("api/" + authMode, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      $("auth-error").textContent = data.error || "Sign-in failed (HTTP " + resp.status + ")";
      return;
    }
    localStorage.setItem("lux_token", data.token);
    applyAccount(data.user);
    hideAuth();
    $("auth-password").value = "";
    if (!data.user.welcome_seen) {
      $("welcome-name").textContent = data.user.username;
      $("welcome-budget").textContent = data.user.budget;
      $("welcome-overlay").hidden = false;
    }
    lastMessagesRev = -1;
    lastAuditRev = -1;
    lastNotificationsRev = -1;
  } catch (error) {
    $("auth-error").textContent = "Sign-in failed: " + error.message;
  } finally {
    $("auth-submit").disabled = false;
  }
}

async function markWelcomeSeen() {
  try { await post("api/welcome-seen"); } catch { /* not fatal */ }
  if (account) account.welcome_seen = true;
}

function closeWelcome(withGuide) {
  hideWelcome();
  markWelcomeSeen();
  if (withGuide) setTimeout(startGuide, 260);
}

const GUIDE_STEPS = [
  { sel: "#hero-create", title: "Create a task", text: "Describe what you need in plain language. The agent reads the live catalog, picks a seller and locks the price in escrow." },
  { sel: "#hero-auto", title: "Or run the auto demo", text: "One click runs the whole scenario: discovery, escrow, a failed delivery, a refund, a re-purchase and a paid receipt." },
  { sel: "#tasks", title: "Your tasks", text: "Every purchase of this conversation. Click a row to open the delivered artifact, or the receipt for the full ledger trail." },
  { sel: "#conversation", title: "The conversation", text: "The agent's replies plus a compact strip of the tool calls it really made." },
  { sel: "#activity-list", title: "Live activity", text: "Every Flash, HTTP and LSL call with its duration. Click a name to see the raw input and output." },
  { sel: "#profile", title: "Your account", text: "Top up simulated coins, open transactions, or sign out." },
];
let guideIndex = 0;
let stripeEur = 1;

function positionGuide() {
  const step = GUIDE_STEPS[guideIndex];
  const target = document.querySelector(step.sel);
  if (!target) { nextGuide(1); return; }
  const rect = target.getBoundingClientRect();
  const spot = $("guide-spot");
  const pad = 6;
  spot.style.left = Math.max(4, rect.left - pad) + "px";
  spot.style.top = Math.max(4, rect.top - pad) + "px";
  spot.style.width = Math.min(window.innerWidth - 8, rect.width + pad * 2) + "px";
  spot.style.height = (rect.height + pad * 2) + "px";
  const bubble = $("guide-bubble");
  const width = 310;
  let left = Math.min(Math.max(16, rect.left), window.innerWidth - width - 16);
  let top = rect.bottom + 14;
  if (top + 210 > window.innerHeight) top = Math.max(16, rect.top - 210);
  bubble.style.left = left + "px";
  bubble.style.top = top + "px";
  $("guide-step").textContent = (guideIndex + 1) + " / " + GUIDE_STEPS.length;
  $("guide-title").textContent = step.title;
  $("guide-text").textContent = step.text;
  $("guide-prev").style.visibility = guideIndex === 0 ? "hidden" : "visible";
  $("guide-next").textContent = guideIndex === GUIDE_STEPS.length - 1 ? "Finish" : "Next";
}

function startGuide() {
  guideIndex = 0;
  $("guide-overlay").hidden = false;
  positionGuide();
}

function nextGuide(delta) {
  guideIndex += delta;
  if (guideIndex < 0) guideIndex = 0;
  if (guideIndex >= GUIDE_STEPS.length) { $("guide-overlay").hidden = true; return; }
  positionGuide();
}

async function openTopUp() {
  $("profile-menu").hidden = true;
  $("topup-status").textContent = "";
  $("topup-dialog").showModal();
}

function setStripeEur(amount) {
  stripeEur = amount;
  document.querySelectorAll("#stripe-presets .eur").forEach((button) => button.classList.toggle("active", Number(button.dataset.eur) === amount));
}

async function payByCard() {
  $("stripe-status").textContent = "Creating a Stripe test checkout…";
  try {
    const resp = await post("api/topup/stripe", { amount_eur: stripeEur });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.stripe || !data.stripe.checkout_url) {
      $("stripe-status").textContent = (data.error || "Stripe checkout failed") + " (HTTP " + resp.status + ")";
      return;
    }
    $("stripe-status").textContent = "Redirecting to Stripe test checkout…";
    window.location.href = data.stripe.checkout_url;
  } catch (error) {
    $("stripe-status").textContent = "Stripe checkout failed: " + error.message;
  }
}

async function sandboxCardPay() {
  $("stripe-sandbox").disabled = true;
  $("stripe-status").textContent = "Creating a Stripe test payment with the visa test card…";
  try {
    const resp = await post("api/topup/stripe/sandbox", { amount_eur: stripeEur });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) { $("stripe-status").textContent = (data.error || "Stripe payment failed") + " (HTTP " + resp.status + ")"; return; }
    const stripe = data.stripe || {};
    if (data.user) applyAccount(data.user);
    $("stripe-status").textContent = "Stripe test payment " + (stripe.stripe_reference || "") + " confirmed: +" + stripe.lux_coins + " Lux Coins (" + stripe.session_id + ").";
    lastAuditRev = -1;
  } catch (error) {
    $("stripe-status").textContent = "Stripe payment failed: " + error.message;
  } finally {
    $("stripe-sandbox").disabled = false;
  }
}

async function confirmStripeReturn(stripeId) {
  try {
    const resp = await post("api/topup/stripe/confirm", { stripe_session_id: stripeId });
    const data = await resp.json().catch(() => ({}));
    const stripe = data.stripe || {};
    if (resp.ok && data.user) applyAccount(data.user);
    if (resp.ok && stripe.credited) {
      showNotice("Card payment confirmed (Stripe test): +" + stripe.lux_coins + " Lux Coins.");
    } else if (resp.ok) {
      showNotice("Stripe payment already credited (+" + stripe.lux_coins + " Lux Coins).");
    } else {
      showNotice((data.error || "Stripe confirmation failed") + " (HTTP " + resp.status + ")");
    }
    lastAuditRev = -1;
  } catch (error) {
    showNotice("Stripe confirmation failed: " + error.message);
  }
}

async function submitTopUp() {
  const amount = parseInt($("topup-amount").value, 10);
  if (!amount || amount < 1 || amount > 500) { $("topup-status").textContent = "Enter a whole number from 1 to 500."; return; }
  $("topup-submit").disabled = true;
  try {
    const resp = await post("api/topup", { amount });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) { $("topup-status").textContent = data.error || "Top-up failed (HTTP " + resp.status + ")"; return; }
    applyAccount(data.user);
    $("topup-status").textContent = "Topped up by " + amount + " LC. Budget is now " + data.user.budget + " LC.";
    lastAuditRev = -1;
  } catch (error) {
    $("topup-status").textContent = "Top-up failed: " + error.message;
  } finally {
    $("topup-submit").disabled = false;
  }
}

async function signOut() {
  $("profile-menu").hidden = true;
  try { await post("api/logout"); } catch { /* ignore */ }
  localStorage.removeItem("lux_token");
  account = null;
  const overlay = $("auth-overlay");
  overlay.classList.remove("closing");
  overlay.hidden = false;
  const form = $("auth-form");
  form.style.animation = "none";
  void form.offsetHeight;
  form.style.animation = "";
  showAuth("Signed out.");
}

async function boot() {
  if (!token()) { showAuth(); return; }
  try {
    const resp = await fetch("api/me", { headers: { Authorization: "Bearer " + token() } });
    if (!resp.ok) { localStorage.removeItem("lux_token"); showAuth("Please sign in again."); return; }
    const data = await resp.json();
    applyAccount(data.user);
    hideAuth();
    if (!data.user.welcome_seen) {
      $("welcome-name").textContent = data.user.username;
      $("welcome-budget").textContent = data.user.budget;
      $("welcome-overlay").hidden = false;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("stripe") === "ok" && params.get("sc")) {
      await confirmStripeReturn(params.get("sc"));
      window.history.replaceState({}, "", window.location.pathname);
    } else if (params.get("stripe") === "cancel") {
      showNotice("Card payment cancelled — nothing was charged.");
      window.history.replaceState({}, "", window.location.pathname);
    }
  } catch {
    showAuth("Cannot reach the server.");
  }
}

/* ---------- search with results under the field ---------- */
const SEARCH_PAGES = [
  { title: "Payments", sub: "Every settled purchase with its receipt", url: "../payments" },
  { title: "Documentation", sub: "Architecture, API and invariants", url: "../docs" },
  { title: "Marketplace overview", sub: "Offers, sessions and the ledger invariant", url: "../" },
];

function searchItems(term) {
  const needle = term.trim().toLowerCase();
  if (needle.length < 2) return [];
  const groups = [];
  const label = (capability) => (CAPABILITY[capability] || {}).label || "Service";

  const tasks = ((fullState && fullState.payments) || []).map(parsePayment)
    .filter((payment) => (payment.job + " " + payment.seller + " " + label(payment.capability) + " " + payment.state
      + " " + payment.amount + " lc").toLowerCase().includes(needle))
    .slice(0, 5)
    .map((payment) => ({ icon: ICON[payment.capability] || ICON.agent, title: label(payment.capability) + " · " + payment.amount + " LC",
                         sub: payment.job + " · " + payment.seller + " · " + payment.state, action: () => openDelivery(payment.id) }));
  if (tasks.length) groups.push({ label: "Your tasks", items: tasks });

  const sellerMap = offers.list.length ? offers.sellers : FALLBACK_SELLERS;
  const sellers = Object.values(sellerMap)
    .filter((seller) => (seller.name + " " + seller.offers.map((offer) => label(offer.capability)).join(" ")).toLowerCase().includes(needle))
    .slice(0, 5)
    .map((seller) => ({ icon: ICON[seller.offers[0].capability] || ICON.agent, title: seller.name,
                        sub: "from " + seller.offers[0].price + " LC · " + seller.offers.map((offer) => label(offer.capability)).slice(0, 3).join(", "),
                        action: () => openDialog(seller.offers[0].capability, seller) }));
  if (sellers.length) groups.push({ label: "Agents", items: sellers });

  const actions = QUICK.map((capability) => CAPABILITY[capability])
    .filter((meta) => (meta.label + " " + meta.short + " " + meta.prompt).toLowerCase().includes(needle))
    .slice(0, 5)
    .map((meta) => ({ icon: ICON[Object.keys(CAPABILITY).find((key) => CAPABILITY[key] === meta)] || ICON.agent,
                      title: meta.label, sub: meta.short, action: () => {
                        const capability = Object.keys(CAPABILITY).find((key) => CAPABILITY[key] === meta);
                        openDialog(capability);
                      } }));
  if (actions.length) groups.push({ label: "Start a task", items: actions });

  const pages = SEARCH_PAGES.filter((page) => (page.title + " " + page.sub).toLowerCase().includes(needle))
    .map((page) => ({ icon: '<svg viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8"/></svg>', title: page.title, sub: page.sub,
                      action: () => { window.location.href = page.url; } }));
  if (pages.length) groups.push({ label: "Pages", items: pages });

  return groups.slice(0, 4);
}

function renderSearchPop() {
  const pop = $("search-pop");
  const groups = searchItems($("search").value);
  pop.replaceChildren();
  if (!groups.length) {
    if ($("search").value.trim().length >= 2) {
      const empty = document.createElement("div");
      empty.className = "sr-empty";
      empty.textContent = "No results for “" + $("search").value.trim() + "”.";
      pop.append(empty);
      pop.hidden = false;
    } else {
      pop.hidden = true;
    }
    return;
  }
  for (const group of groups) {
    const head = document.createElement("div");
    head.className = "sr-group";
    head.textContent = group.label;
    pop.append(head);
    for (const item of group.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sr-item";
      const icon = document.createElement("span");
      icon.className = "sr-icon";
      icon.innerHTML = item.icon;
      const text = document.createElement("span");
      text.className = "sr-text";
      text.innerHTML = "<b>" + esc(item.title) + "</b><small>" + esc(item.sub) + "</small>";
      button.append(icon, text);
      button.addEventListener("click", () => { hideSearchPop(); item.action(); });
      pop.append(button);
    }
  }
  pop.hidden = false;
}

function hideSearchPop() { const pop = $("search-pop"); if (pop) pop.hidden = true; }

/* ---------- data loading and polling ---------- */
async function loadOffers() {
  try {
    const resp = await fetch("../api/offers");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    offers = { list: data.offers || [], byId: {}, sellers: {} };
    for (const offer of offers.list) {
      offers.byId[offer.id] = offer;
      const seller = offers.sellers[offer.seller_id] || (offers.sellers[offer.seller_id] = { id: offer.seller_id, name: offer.name, offers: [] });
      seller.offers.push(offer);
    }
    for (const seller of Object.values(offers.sellers)) seller.offers.sort((a, b) => a.price - b.price);
  } catch {
    offers = { list: [], byId: {}, sellers: {} };
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
    renderStats(st);
    if (Date.now() >= noticeUntil) {
      $("status-line").textContent = autoQueue.length ? `Auto demo · step ${Math.min(AUTO_STEPS.length - autoQueue.length, AUTO_STEPS.length)}/${AUTO_STEPS.length}`
        : (st.busy ? "⟳ " : "") + st.status;
    }
    $("btn-catalog").disabled = st.busy;
    $("btn-auto").disabled = st.busy || autoQueue.length > 0;
    $("btn-stop").hidden = !st.busy;
    $("dialog-submit").disabled = st.busy;
    $("bell-dot").hidden = !st.busy || !$("bell-count").hidden;
    if (st.messages_revision !== lastMessagesRev || st.audit_revision !== lastAuditRev
        || st.notifications_revision !== lastNotificationsRev) {
      const full = await (await fetch("api/state?t=" + Date.now())).json();
      if (full && full.ok === true) {
        fullState = full;
        latest = full;
        renderWallet(full);
        renderStats(full);
        renderSystem(full);
        if (full.messages_revision !== lastMessagesRev) { lastMessagesRev = full.messages_revision; renderChat(full.messages); }
        if (full.notifications_revision !== lastNotificationsRev) {
          lastNotificationsRev = full.notifications_revision;
          renderNotifications(full);
        }
        if (full.audit_revision !== lastAuditRev) {
          lastAuditRev = full.audit_revision;
          renderTasks(full.payments || []);
          renderActivity(full.tools || []);
          renderToolStrip(full.tools || []);
        }
      }
    }
    if (previousBusy && !st.busy) autoSpeakIfEnabled();
    previousBusy = st.busy;
    autoTick(latest);
  } catch (error) {
    showNotice("Server unavailable: " + error.message);
  }
  setTimeout(poll, 500);
}

/* ---------- wiring ---------- */
const openers = ["side-create", "hero-create", "right-create", "tasks-new"];
openers.forEach((id) => { const el = $(id); if (el) el.addEventListener("click", () => openDialog()); });
$("hero-auto").addEventListener("click", startAutoDemo);
$("btn-auto").addEventListener("click", startAutoDemo);
$("btn-stop").addEventListener("click", stopTurn);
$("btn-catalog").addEventListener("click", refreshCatalog);
$("btn-new").addEventListener("click", newConversation);
$("delivery-speak").addEventListener("click", speakDelivery);
$("task-mic").addEventListener("click", toggleRecording);
$("delivery-close").addEventListener("click", () => { stopDeliveryAudio(); $("delivery-dialog").close(); });
$("delivery-done").addEventListener("click", () => { stopDeliveryAudio(); $("delivery-dialog").close(); });
document.querySelectorAll(".auth-tab").forEach((tab) => tab.addEventListener("click", () => setAuthMode(tab.dataset.mode)));
$("auth-form").addEventListener("submit", authSubmit);
$("welcome-guide").addEventListener("click", () => closeWelcome(true));
$("welcome-skip").addEventListener("click", () => closeWelcome(false));
$("guide-next").addEventListener("click", () => nextGuide(1));
$("guide-prev").addEventListener("click", () => nextGuide(-1));
$("guide-skip").addEventListener("click", () => { $("guide-overlay").hidden = true; });
window.addEventListener("resize", () => { if (!$("guide-overlay").hidden) positionGuide(); });
$("pm-topup").addEventListener("click", openTopUp);
$("pm-logout").addEventListener("click", signOut);
$("topup-close").addEventListener("click", () => $("topup-dialog").close());
$("topup-cancel").addEventListener("click", () => $("topup-dialog").close());
$("topup-submit").addEventListener("click", submitTopUp);
document.querySelectorAll("#topup-presets button").forEach((button) => button.addEventListener("click", () => {
  $("topup-amount").value = button.dataset.amount;
}));
document.querySelectorAll("#stripe-presets .eur").forEach((button) => button.addEventListener("click", () => setStripeEur(Number(button.dataset.eur))));
$("stripe-pay").addEventListener("click", payByCard);
$("stripe-sandbox").addEventListener("click", sandboxCardPay);
$("dialog-close").addEventListener("click", () => $("task-dialog").close());
$("dialog-cancel").addEventListener("click", () => $("task-dialog").close());
$("task-form").addEventListener("submit", (event) => { event.preventDefault(); submitTask(); });
$("search").addEventListener("input", (event) => {
  searchTerm = event.target.value.trim().toLowerCase();
  if (fullState) renderTasks(fullState.payments || []);
  renderAgents();
  renderSearchPop();
});
$("search").addEventListener("focus", renderSearchPop);
$("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const first = document.querySelector("#search-pop .sr-item");
    if (first) { event.preventDefault(); hideSearchPop(); first.click(); }
  } else if (event.key === "Escape") {
    hideSearchPop();
    $("search").blur();
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".search-wrap")) hideSearchPop();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== $("search")) { event.preventDefault(); $("search").focus(); }
});
document.querySelectorAll(".filter").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach((other) => other.classList.remove("active"));
    chip.classList.add("active");
    tasksFilter = chip.dataset.state;
    if (fullState) renderTasks(fullState.payments || []);
  });
});
$("side-tasks").addEventListener("click", (event) => { event.preventDefault(); $("tasks").scrollIntoView({ behavior: "smooth" }); });
$("top-tasks").addEventListener("click", (event) => { event.preventDefault(); $("tasks").scrollIntoView({ behavior: "smooth" }); });
$("top-agents").addEventListener("click", (event) => { event.preventDefault(); $("agents").scrollIntoView({ behavior: "smooth" }); });
$("bell").addEventListener("click", (event) => {
  event.stopPropagation();
  const menu = $("bell-menu");
  menu.hidden = !menu.hidden;
  if (!menu.hidden) markNotificationsSeen();
});
$("bell-activity").addEventListener("click", () => {
  $("bell-menu").hidden = true;
  $("activity-list").scrollIntoView({ behavior: "smooth", block: "center" });
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".bell-wrap")) $("bell-menu").hidden = true;
});
$("profile").addEventListener("click", (event) => {
  event.stopPropagation();
  const menu = $("profile-menu");
  menu.hidden = !menu.hidden;
});
$("profile-new").addEventListener("click", () => { $("profile-menu").hidden = true; newConversation(); });
document.addEventListener("click", (event) => {
  if (!event.target.closest(".profile-wrap")) $("profile-menu").hidden = true;
});
const voiceBox = $("chk-voice");
voiceBox.checked = autoVoice;
voiceBox.addEventListener("change", () => {
  autoVoice = voiceBox.checked;
  localStorage.setItem("autoVoice", autoVoice ? "1" : "0");
  if (!autoVoice) stopSpeech();
});
renderQuick();
setAuthMode("login");
loadOffers();
poll();
boot();
