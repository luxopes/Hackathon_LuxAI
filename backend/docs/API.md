# Backend API — frontend integration guide

Two HTTP services make up the backend. A frontend normally talks **only to the
agent console service**; it exposes the chat, the live tool-call feed and the
wallet state as one small polling API. The marketplace API below is available
for direct integrations (and for the public overview page).

| Service | Default address | Public example | Purpose |
|---|---|---|---|
| Agent console (LSL) | `http://127.0.0.1:3069` | `https://hackathon.lux-ai.cz/web/` | Chat agent, tool-call feed, wallet/transactions, static frontend |
| Marketplace (Python) | `http://127.0.0.1:3070` | `https://hackathon.lux-ai.cz/` | Wallets, escrow, offers, jobs, ledger, verification |

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
`frontend/index.html` + `frontend/app.js` + `frontend/style.css` in this
repository. The console service serves that directory directly
(`PROOFPAY_WEB_DIR`); in production it is deployed to e.g.
`/opt/proofpay-mvp/web/`.

---

## 2. Console API reference

### `GET /`
Static frontend (`index.html` from `PROOFPAY_WEB_DIR`). Other static files
(`style.css`, `app.js`, …) are served by name.

### `GET /api/health`
```json
{ "ok": true, "payments": "simulated USD" }
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
  "currency": "USD",
  "simulated_payments": true,
  "ai_model": "flash",
  "messages": [ { "role": "Agent", "content": "…" }, { "role": "You", "content": "…" } ],
  "tools": [ { "id": "tool-…", "summary": "OK · fetch_offers · Flash tool · 1060 ms",
               "lines": ["Tool: fetch_offers", "Status: OK · source: Flash tool", "…"] } ],
  "payments": [ { "id": "job-…", "summary": "PAID · $7.00 · complete · job-…",
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
* `wallet` — `available` + `locked` (escrow) in simulated dollars. `null` until the first
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

### `POST /api/speak`
Read one stored agent message (or a purchased delivery) aloud. ElevenLabs runs
behind the **LSL speech sidecar** (`backend/lsl/speech.lsl`); the request
carries only an index or a job id, never free text, so the endpoint cannot be
abused as a TTS proxy. The API key stays on the server (curl receives it in a
config file, never in arguments) and identical texts are served from the shared
mp3 cache under `/srv/www/proofpay-tts`, which Caddy serves directly so no
binary ever passes through LSL strings.

```json
{ "index": 4 }
{ "job_id": "job-…" }
```
* `200` → `{"ok": true, "url": "/web/tts/<sha256>.mp3", "cached": false, "chars": 171}`
* `400` → `{"error": "index must point at an agent message"}`.
* `502` → ElevenLabs failed (the message carries the reason).

### `POST /api/transcribe`
Dictate a task with ElevenLabs Scribe. The body carries the recording as
base64; the sidecar decodes it with `openssl base64`, uploads it with curl and
returns the transcript.

```json
{ "audio_base64": "GkXfo…" }
```
* `200` → `{"text": "audit the demo cart"}`.
* `413` → the recording is empty or larger than 8 MB.

### `GET /health`
`{"ok": true, "configured": true, "model": "eleven_v4", "cached": 37, "runtime": "LSL"}`.
* `503` → `{"error": "Text-to-speech is not configured on this server."}`.

The reference frontend shows a 🔊 button on agent messages; when the browser
blocks autoplay (slow first synthesis) the button turns into ▶ and replays the
already fetched audio on the next click. An "auto voice" toggle reads every
finished agent reply aloud.

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

Base URL example: `https://hackathon.lux-ai.cz` (or `http://127.0.0.1:3070`). The older path-based URLs on `api.lux-ai.cz/hackathon01` keep working.

### Public endpoints (no token)

* `GET /health` → `{"ok": true, "payments": "simulated USD"}`
* `GET /` → public overview dashboard (server-rendered by `ui.py`)
* `GET /assets/site.css` → shared stylesheet for the public pages
* `GET /docs` → in-app documentation: architecture, payment lifecycle, API table, invariants and local setup
* `GET /api/dashboard` → `{currency, simulated_payments, services, offers,
  sessions, sellers, invariant}` — includes the ledger invariant
  (`issued == accounted`, `holds`).
* `GET /api/offers` → `{"offers": [{"id","seller_id","name","price",
  "capability","delivery","tier","currency","service_name"}]}`
* `GET /api/services` → `{"services": {"<capability>": {"name","delivery"}}, "currency"}`
* `GET /payments` → HTML **payments list**: totals (paid/refunded, success rate),
  revenue per seller and service, and a filterable table of every payment with
  links to its receipt.
* `GET /api/payments` → the same data as JSON (summary, revenue, services,
  payments with ledger movements, invariant).
* `GET /.well-known/proofpay-keys.json` → published Ed25519 public key(s) for
  verifying receipt signatures offline.
* `GET /api/ledger/export?format=jsonl|csv` → the append-only ledger as JSONL
  (each row carries `prev_hash`/`row_hash`, forming a tamper-evident chain) or CSV.
* `GET /api/ledger/check` → recomputes the chain: `{ok, rows, head}` (or
  `broken_at` when a row was edited).
* `GET /receipt/{job_id}` → self-contained HTML **payment receipt**: every ledger
  movement with reconstructed buyer balances, escrow lifecycle, contract and
  hashes, verification findings and execution receipts, reconciliation
  (`escrow_locked == settled == price`), the market-wide invariant and the raw
  JSON. Link to it from any job id.
* `GET /api/receipt/{job_id}` → the same receipt as machine-readable JSON
  (`receipt_version: "1.0"`), including the Ed25519 `signature` block (canonical
  settlement payload, payload SHA-256, key id) and the embedded `public_key`.
  Verify offline with `tools/verify_receipt.py`.

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
* `POST /api/sessions/{id}/stripe-checkout` `{amount_eur}` → creates a Stripe
  **test-mode** Checkout Session in USD (1–25 USD, credited one for one) and returns
  `{checkout_url, stripe_session_id, lux_coins}`. The card page is hosted by
  Stripe; nothing is credited until `stripe-confirm`.
* `POST /api/sessions/{id}/stripe-confirm` `{stripe_session_id}` → verifies the
  payment with Stripe and credits the wallet **once** (idempotent by the Stripe
  object id; a `STRIPE_TOPUP` row lands in the ledger).
* `POST /api/sessions/{id}/stripe-sandbox-pay` `{amount_eur}` → server-side test
  helper: creates and confirms a Stripe test payment with `pm_card_visa`
  (test keys only). Used by the console's "Simulate card payment (test)" button
  and by the automated tests.
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

* **Simulated money.** All payments are simulated US dollars in a central SQLite
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
