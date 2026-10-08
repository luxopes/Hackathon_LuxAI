# Backend API — frontend integration guide

Two HTTP services make up the backend. A frontend normally talks **only to the
agent console service**; it exposes the chat, the live tool-call feed and the
wallet state as one small polling API. The marketplace API below is available
for direct integrations (and for the public overview page).

| Service | Default address | Public example | Purpose |
|---|---|---|---|
| Agent console (LSL) | `http://127.0.0.1:3069` | `https://api.lux-ai.cz/hackathon01/web/` | Chat agent, tool-call feed, wallet/transactions, static frontend |
| Marketplace (Python) | `http://127.0.0.1:3070` | `https://api.lux-ai.cz/hackathon01/` | Wallets, escrow, offers, jobs, ledger, verification |

**Same-origin rule.** The services send no CORS headers. Serve the frontend from
`PROOFPAY_WEB_DIR` (the console service serves it at `/`) or reverse-proxy the
console API and the frontend under one origin (see `deploy/Caddyfile.snippet`).

---

## 1. Frontend quick start (console API)

All examples are relative to the console base URL. `POST` bodies are JSON.

```js
// 1) Poll state. ?lite=1 returns only wallet/status/revisions (~1 kB).
const lite = await (await fetch("api/state?lite=1&t=" + Date.now())).json();

// 2) Fetch the full payload only when a revision changes.
if (lite.messages_revision !== renderedMessagesRev ||
    lite.audit_revision !== renderedAuditRev) {
  const full = await (await fetch("api/state?t=" + Date.now())).json();
  render(full.messages, full.tools, full.payments, full.wallet);
}

// 3) Send a user message (one turn at a time; 409 while the agent works).
await fetch("api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "Please audit the marketplace demo cart." }),
});
```

A complete working client (the console UI) is the reference implementation:
`index.html` + `app.js` + `style.css` deployed next to the service
(`PROOFPAY_WEB_DIR`, e.g. `/opt/proofpay-mvp/web/`).

---

## 2. Console API reference

### `GET /`
Static frontend (`index.html` from `PROOFPAY_WEB_DIR`). Other static files
(`style.css`, `app.js`, …) are served by name.

### `GET /api/health`
```json
{ "ok": true, "payments": "simulated Lux Coins" }
```

### `GET /api/state`
Full agent state. Query parameters:

| Param | Meaning |
|---|---|
| `lite=1` | Omit `messages`, `tools`, `payments` arrays (only counts + revisions + wallet/status). |
| `t=<anything>` | Cache-buster; recommended on every call. |

Response (full mode):
```json
{
  "ok": true,
  "currency": "Lux Coins",
  "simulated_payments": true,
  "ai_model": "flash",
  "messages": [ { "role": "Agent", "content": "…" }, { "role": "You", "content": "…" } ],
  "tools": [ { "id": "tool-…", "summary": "OK · fetch_offers · Flash tool · 1060 ms",
               "lines": ["Tool: fetch_offers", "Status: OK · source: Flash tool", "…"] } ],
  "payments": [ { "id": "job-…", "summary": "PAID · 7 Lux Coins · complete · job-…",
                  "lines": ["Job: job-…", "…"] } ],
  "wallet": { "available": 23, "locked": 0 },
  "budget": 30,
  "status": "Ready · send another message",
  "busy": false,
  "messages_revision": 57,
  "audit_revision": 44,
  "tool_count": 30,
  "payment_count": 2,
  "time": 1791477972.37
}
```

Field notes:

* `messages` — the conversation as displayed. `role` is `"You"` (user) or
  `"Agent"`. Agent messages may contain markdown-ish fences (` ``` `), bullet
  lines (`•`) and `OK ·` / `CART BUG ·` check lines. Delivery previews stream
  into the last message in place.
* `tools` — one entry per real tool call (Flash model call, HTTP request, local
  validation). `summary` is `"<STATUS> · <name> · <source> · <duration> ms"`
  with `STATUS` in `RUNNING | OK | ERROR`; `lines` are human-readable details
  (input/output JSON as strings).
* `payments` — one entry per purchased job, fetched from the marketplace
  ledger. `summary` starts with `PAID` or `REFUNDED`.
* `wallet` — `available` + `locked` (escrow) Lux Coins. `null` until the first
  purchase creates the wallet.
* `budget` — the configured per-wallet budget (issued on first purchase).
* `busy` — `true` while a turn is running; the agent handles **one turn at a
  time**.
* `messages_revision` / `audit_revision` — monotonically increasing; re-render
  only on change. Use them to decide between lite and full polling.
* `tool_count` / `payment_count` — counters for tab badges in lite mode.

### `POST /api/chat`
Run one agent turn.

```json
{ "message": "What is on offer right now and at what prices?" }
```
* `200` → `{"ok": true}` — the turn started; poll `/api/state` for progress.
* `400` → `{"error": "message must have 1 to 1000 characters"}`.
* `409` → `{"error": "The agent is still working; wait for it to finish."}`.
* `429` → `{"error": "Please wait a moment before the next request."}` — a 2 s
  per-message cooldown protects the model quota.

### `POST /api/catalog`
Fetch the current marketplace catalog into the conversation (tool calls
included). Same `200`/`409` behaviour as `/api/chat`. No model call.

### `POST /api/cancel`
Set the cancel flag for the running turn. The agent stops before the next
purchase step; a funded job is always settled or refunded first. Returns
`{"ok": true}`.

### `POST /api/new`
Reset the conversation (`409` while busy). The in-memory chat history is
dropped; the marketplace wallet stays untouched and the next purchase creates a
new wallet with the configured budget.

---

## 3. Polling strategy (recommended)

1. Poll `api/state?lite=1&t=<ts>` every **300–1000 ms** (the reference client
   uses 500 ms). Never run parallel polls — the LSL server is single-threaded;
   serialise requests (await the previous response before the next call).
2. When `messages_revision` or `audit_revision` changed since the last
   *rendered* revision, fetch the full `api/state` once and re-render.
3. Keep a local copy of the last full state for detail views (tool/payment
   expansion), because lite responses carry no arrays.
4. Disable send while `busy === true`; show `status` as the activity line.
5. Version static assets (`app.js?v=…`) if a CDN sits in front.

---

## 4. Marketplace API reference

Base URL example: `https://api.lux-ai.cz/hackathon01` (or `http://127.0.0.1:3070`).

### Public endpoints (no token)

* `GET /health` → `{"ok": true, "payments": "simulated Lux Coins"}`
* `GET /` → public overview dashboard (`static/dashboard.html`)
* `GET /api/dashboard` → `{currency, simulated_payments, services, offers,
  sessions, sellers, invariant}` — includes the ledger invariant
  (`issued == accounted`, `holds`).
* `GET /api/offers` → `{"offers": [{"id","seller_id","name","price",
  "capability","delivery","tier","currency","service_name"}]}`
* `GET /api/services` → `{"services": {"<capability>": {"name","delivery"}}, "currency"}`

### Client-token endpoints

Send `Authorization: Bearer <client-token>` (token file path is configured by
`client_token_file` in `market.json`). The token stays server-side; browsers
must **not** embed it.

* `POST /api/sessions`
  ```json
  { "title": "Cart audit", "budget": 30, "fixture": "buggy", "service": "auto" }
  ```
  → session object `{id, title, budget, fixture, service, created, wallet:{id,kind,
  available,locked}, jobs:[], ledger:[], currency, simulated_payments}`.
  `budget` is the spending cap (1–100).
* `POST /api/jobs` — escrow funding
  ```json
  { "session_id": "session-…", "offer_id": "offer-scout-translation",
    "idempotency_key": "session-…-attempt-0", "task": "Translate …" }
  ```
  → job in state `FUNDED`; the price moves from `available` to `locked`.
  Repeating the same `idempotency_key` never creates a second charge.
* `POST /api/jobs/{id}/execute` — run the seller. With header
  `Accept: text/event-stream` the response is an SSE stream of
  `data: {"choices":[{"delta":{"content":"…"}}]}` preview frames ending with
  `data: [DONE]`; otherwise a single JSON response.
* `GET /api/jobs/{id}` → job (`state` ∈ `FUNDED | RUNNING | DELIVERED | FAILED |
  PAID | REFUNDED`, `contract`, `result`).
* `GET /api/jobs/{id}/verify` → `{valid_delivery, reasons[], checks[]}` —
  structural verification against the agreed contract.
* `POST /api/jobs/{id}/settle` → pays the seller (`PAID`); refused with `409`
  for an invalid delivery.
* `POST /api/jobs/{id}/refund` → returns escrow to the buyer (`REFUNDED`);
  refused with `409` for a valid delivery (no double refunds).
* `GET /api/jobs/{id}/payment` → payment receipt: `{job_id, session_id,
  offer_id, seller_id, amount, currency, state, idempotency_key, transactions[],
  verification_receipts[], contract_sha256, result_sha256, simulated_payments,
  ledger}` for the payments view.
* `GET /api/sessions/{id}` → session incl. jobs and the full ledger.

### Seller-token endpoints

Used by the seller processes only (`/sandbox/cart`, `/providers/attest`,
`/providers/research`, `/providers/progress`). Not needed by frontends.

---

## 5. Purchase lifecycle

```
POST /api/sessions            POST /api/jobs               POST /api/jobs/{id}/execute
  wallet = budget      →        FUNDED (escrow locked)  →    RUNNING  + live SSE preview
                                                                  │
                                          ┌───────────────────────┴────────────────────┐
                                          ▼                                            ▼
                              GET /verify: valid_delivery                 GET /verify: reasons[]
                                          │                                            │
                                          ▼                                            ▼
                              POST /settle → PAID                     POST /refund → REFUNDED
                              (seller paid)                           (buyer made whole)
```

The console agent performs exactly this loop automatically — including the
refund and re-purchase from another seller when a delivery fails verification.
Every transition is idempotent and recorded in the central SQLite ledger.

---

## 6. Invariants and labels (required)

* **Simulated money.** All payments are simulated Lux Coins in a central SQLite
  ledger; the UI must label them `SIMULATED` (`simulated_payments: true` is part
  of every state/receipt).
* **Caps hold.** A wallet never spends beyond its budget; the marketplace
  rejects a purchase whose price exceeds `available`.
* **Nothing pays twice.** Idempotency keys plus unique constraints guarantee one
  escrow per job, one payment or refund per job; `issued == accounted` always
  holds (`invariant.holds`).
* **Transparency.** Execution receipts, contract SHA-256 and delivery SHA-256
  are attached to every payment receipt.

---

## 7. Security notes

* Client and seller tokens live in files on the server (mode 640) and are never
  sent to a browser. The console service holds its own token and proxies all
  privileged marketplace calls.
* The console API is intentionally unauthenticated for the demo (one turn at a
  time, 2 s cooldown, 1000-character message limit). If exposed publicly,
  protect it at the reverse proxy (e.g. Caddy `basic_auth`).
* Responses carry `Cache-Control: no-store`; keep cache-busting query
  parameters in clients to be safe behind caching CDNs.
