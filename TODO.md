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

**Target design:** https://api.lux-ai.cz/hackathon01/web/ (the agent console).
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

## 2. Port the speech sidecar from Python to LSL

`backend/python/tts.py` (ElevenLabs TTS + Scribe transcription, mp3 cache) is
the only service added in Python; the console, agent, sellers and accounts are
already LSL. Port it to `backend/lsl/speech.lsl` so the whole agentic stack is
LSL, and keep the Python service as a fallback until the LSL version passes the
same tests.

Notes for the port:

* LSL can shell out with `process.check(executable, arguments)` /
  `process.check_input(...)`; run `curl --output <file>` for binary bodies and
  read them back with `readbytes` (the text-oriented `http` stdlib cannot carry
  mp3 bytes).
* Serve the audio through `httpserver` with a `ByteArray` body; keep the disk
  cache keyed by the SHA-256 of the spoken text (see the Python version).
* Transcription: write the request body to a temp file (`writebytes`), forward
  it with `curl -F file=@…`, parse the JSON answer.
* Keep the same public surface: `POST /api/speak` (message index or `job_id`)
  and `POST /api/transcribe`, routed by Caddy to the sidecar.
* Acceptance: the same round-trip checks that passed on 2026-10-08 — read a
  delivery aloud, transcribe a generated mp3 back to text, cached second call
  served without an API request, tampered/unknown ids rejected.

## 3. Smaller leftovers

- [ ] The guide tour ends on the profile step; consider a final "you are ready"
  card linking to the docs.
- [ ] `marketplace.py` remains Python on purpose (SQLite transactions and
  constraints are the safety net for money); revisit only with a real ledger
  design, not as a quick port.
