# TODO

## Hackathon status (2026-10-09, 02:15)

**Payments (judging criterion #1)**

- [x] Stripe test-mode card top-ups in USD: hosted Checkout page, `stripe-confirm`
      credits the wallet exactly once, a `STRIPE_TOPUP` row lands in the ledger,
      idempotency verified twice, the invariant still holds
- [x] Console: "Pay by card (Stripe)" with USD presets, the return URL confirms
      and cleans itself; the card page is real Stripe (sandbox), the coins it
      buys are simulated and labelled as such everywhere
- [x] Internal amounts read in both units (`7 simulated USD (approx $0.35)` on
      receipts and payments, `available $X.XX` in the console)
- [x] The account budget clamps to the marketplace session cap (1–100 USD), so a
      topped-up account can still run tasks (verified with a 150 USD account)

**Agent-to-agent protocol (judging criterion #2)**

- [x] Marketplace HTTP API with escrow, delivery verification, contract refunds,
      signed receipts and the append-only hash-chained ledger (invariant holds)

**Autonomy and the one dialog**

- [x] From the first message the agent runs the whole task itself: catalog,
      purchase, escrow, verification, settlement or refund — no confirmation
      requests, sensible defaults, one service bought per turn
- [x] A dialog pops up only when a required input is genuinely missing (for
      example the text to translate): `/api/answer` resumes the original task
      with the saved assignment; verified end to end from the console
- [x] The autonomy logic lives in LSL (`chat_core.lsl` prompt and decision
      handling, `chat_server.lsl` state, route and resume)

**Stability**

- [x] The console state lock can no longer leak: a fixed hard hang during audits
      (verified: the audit runs in about 12 s, repeats, and `/api/state` answers
      throughout while CPU falls back to idle)

**Remaining before the freeze (07:14)**

- [ ] Record the 2-minute demo video (beats in `SPRINT.md`) and submit it with the codebase
- [ ] Rehearse once on the recording browser: sign in, pay by card, auto demo, receipt
- [ ] Rotate the hackathon keys after the event (Stripe, GitHub, Apify, ElevenLabs, LuxAI)

**Known limitation (after the hackathon)**

- One console workspace is shared by every account: the wallet session is global
  while the account budget is per account. Single-account demos are unaffected;
  per-account workspaces need the session to move into the account record.

## 1. Rewrite every other page in the agent-console design

**Target design:** https://hackathon.lux-ai.cz/web/ (the agent console).
Everything else should look like the same product.

**Design reference (source of truth):** `frontend/index.html` + `frontend/style.css`
(the console) plus the theme tokens in that stylesheet:

| Token | Value | Use |
|---|---|---|
| `--ink` | `#17202b` | text, primary buttons |
| `--muted` | `#697586` | secondary text |
| `--line` | `#e7eaee` | borders, table separators |
| `--surface` | `#fff` | cards |
| `--canvas` | `#f8f9fb` | page background |
| `--blue` | `#245dee` | links, info pills |
| `--green` | `#078a4d` | success pills, balance bar |

**Shell to reuse everywhere**

* Sidebar (white, 224 px): brand with the triangle mark + "Agentic Economy",
  `WORKSPACE` group (Overview, Create Task, Agent Marketplace, My Tasks,
  Transactions), `RESOURCES` group (Documentation, API reference, Status),
  the credits box ("Available demo credits" + amount + Manage credits button)
  and the "Agentic Economy / Work. Delegate. Create." signature.
* Top bar (78 px): tab links with the active underline, the search field with
  the results dropdown, the bell with notifications, the profile menu.
* Content: hero panel with the mountain photo and the gradient fade, stat cards
  with tinted icon tiles, `panel` cards with `panel-head`, tables that use
  `agent-icon` tiles, overlapping `avatars`, dotted `badge` pills, the
  `activity` timeline and the `balance` card.

**Pages to convert** (all rendered server-side by `backend/python/ui.py`,
currently in the older "protocol explorer" styling):

| Page | What it becomes |
|---|---|
| `/` marketplace overview | hero-style header, stat cards (sessions, offers, `issued == accounted`), a panel with the service cards, a panel with recent sessions as the console's task table |
| `/payments` | stat cards (payments, paid, refunded, success rate), Sellers and Services panels, the payment list as the same table component, filters as the console's tabs, the ledger-chain badge row |
| `/receipt/{job_id}` | hero with the amount and status badges, the fact grid, money-movement table, lifecycle stepper, contract / verification / integrity panels, the signature block, the raw JSON |
| `/docs` | the same panels: architecture table, payment lifecycle, API table, invariants, run-it-yourself, support |

**How to share the styling** (pick one and document it):

1. Serve the console stylesheet from the marketplace (e.g. `GET /assets/site.css`)
   and link it from the generated pages and from the console
   (`<link rel="stylesheet" href="../assets/site.css">`) — one source of truth.
2. Or port the ui.py CSS to the same tokens and components, keeping two copies.

**Acceptance**

* Side-by-side with the console, the pages read as one product (same shell,
  spacing, pills, tables, buttons, type).
* Every page still renders with the same data and keeps the honest labels
  (`SIMULATED PAYMENTS`, "not a blockchain").
* Link crawler over `/`, `/payments`, `/receipt/{id}`, `/docs`, `/web/` reports
  HTTP 200 for every same-origin link (the script used on 2026-10-08 is in the
  session notes; 114 links were green before the redesign).
* Verified in Firefox at 1366×768, 1900×1100 and 820×1300 (fold states).

## 2. Speech sidecar — done in LSL

`backend/lsl/speech.lsl` replaces `backend/python/tts.py`, which stays in the
repository only as a fallback. Ported behaviour, verified end to end:

- `/api/speak` with `{index}` or `{job_id}`: the sidecar fetches the console
  state (forwarding the caller's token) or the receipt, strips code fences and
  links, and asks ElevenLabs for an mp3 written straight into
  `/srv/www/proofpay-tts` by curl (`-o`), so no binary passes through LSL.
- `/api/transcribe` with `{audio_base64}`: openssl decodes the recording,
  curl posts it to Scribe as multipart, the transcript comes back as JSON.
- The cache key is the same `sha256(voice|model|text)`, so the 37 mp3 files the
  Python version had already produced are reused; Caddy serves `/web/tts/*`
  from that directory.
- The API key never appears in process arguments: curl reads it from a config
  file in a private work directory.
- Verified: an audit and a research delivery both synthesise (`cached: true` on
  the second call), the mp3 downloads over the public host as `audio/mpeg`, and
  a transcription round-trip of the generated audio returned the exact spoken
  text ("Cart audit. Two of three checks passed. …").

## 3. Smaller leftovers

- [ ] The guide tour ends on the profile step; consider a final "you are ready"
  card linking to the docs.
- [ ] `marketplace.py` remains Python on purpose (SQLite transactions and
  constraints are the safety net for money); revisit only with a real ledger
  design, not as a quick port.

## 3. Marketplace in LSL — ported, not switched

`backend/lsl/marketplace.lsl` replaces `backend/python/marketplace.py` on the
same database (schema, JSON shapes, ledger rows). Verified on a copy of the
live database with the LSL service on port 3072: offers and the invariant match,
a full buy-refuse-refund flow ran, and a receipt signed by the LSL verifies with
`tools/verify_receipt.py`. Before switching production, fix or accept:

- the seller handshake in `/api/jobs/{id}/execute` — a test seller pointed at the
  LSL marketplace failed in its idle phase while the Python path works, so the
  delivery leg needs one more debugging pass;
- heavy reads stay in Python on purpose (the ledger hash chain and export,
  `/api/payments`, the HTML pages in ui.py) and Caddy must route accordingly
  (API to the LSL, pages and those reads to Python);
- the seller-progress SSE relay is simplified in the LSL port: the console no
  longer renders streamed previews, so the endpoint returns the finished job
  instead of relaying the seller stream.

## 4. Old signed receipts do not verify after the LC to USD rename

Receipts signed before today's rename carry `"currency": "Lux Coins"` inside the
signed payload, while the verifier rebuilds it with the current constant
(`USD`), so `tools/verify_receipt.py` reports tampering. This affects the Python
marketplace as well; the fresh receipts (signed after the rename) verify. Decide:
keep a historic currency constant, store the currency per receipt, or treat the
payload currency as part of the signed data and never rename it again.

## 5. Speech sidecar runs in Python again

The LSL sidecar (`backend/lsl/speech.lsl`) works and stays in the repository, but
production runs the Python one (`backend/python/tts.py`), which proved more
reliable in practice. The unit keeps the cache directory
`/srv/www/proofpay-tts` so the mp3 files Caddy serves stay where they are.
`tts.py` now also strips markdown before speaking (so `**bold**` is not read as
asterisks), forwards the caller's token when it reads a message from the console
(messages are per account now, so this is required), and rejects nothing else.
The console renders `**bold**`, `*italic*`, `code` and links in every agent
message and delivery, so the model output stops showing raw asterisks.
