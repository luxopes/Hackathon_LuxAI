# TODO

## Where the project stands (2026-10-09, 07:00)

**Submission:** the code on `main` (`luxopes/Hackathon_LuxAI`) plus the
90-second demo video `media/lux-agents-epic-90s-2026-10-09.mp4`.
Live: <https://hackathon.lux-ai.cz/web/> (demo login `hacker` / `dusk2dawn2026`).

**Payments (judging criterion #1)**

- [x] Stripe test-mode card top-ups in USD: hosted Checkout page, the confirm
      call credits the wallet exactly once, a `STRIPE_TOPUP` row lands in the
      ledger, idempotency verified twice, the invariant still holds. Rate 1:1,
      so the amounts stay readable as dollars.
- [x] Console: "Pay by card (Stripe)" with USD presets; the card page is real
      Stripe (sandbox) and every simulated amount is labelled as simulated.
- [x] Every displayed amount is USD (receipts, payments page, console, API).
- [x] Stripe Connect payouts to sellers: every *paid* settlement creates a
      Transfer to the seller's connected account (test mode) and the receipt
      shows it; refunds never transfer. Verified live end to end.
- [x] The account budget clamps to the marketplace session cap (1–100 USD), so a
      topped-up account can still run tasks (verified with a 150 USD account).

**Agent-to-agent protocol (judging criterion #2)**

- [x] Marketplace HTTP API with escrow, delivery verification, contract refunds,
      signed receipts and the append-only hash-chained ledger (invariant holds).
- [x] Five sellers run as separate LSL services; they are paid only for
      deliveries the marketplace verifies against the agreed contract.
- [x] Per-account isolation: an account sees only its own tasks, wallet,
      budgets and notifications (verified with three identities side by side).

**Autonomy and the one dialog**

- [x] From the first message the agent runs the whole task itself: catalog,
      purchase, escrow, verification, settlement or refund — no confirmation
      requests, sensible defaults, one service bought per turn.
- [x] A dialog pops up only when a required input is genuinely missing (for
      example the text to translate): `/api/answer` resumes the original task
      with the saved assignment; verified end to end from the console.
- [x] The autonomy logic lives in LSL (`chat_core.lsl` prompt and decision
      handling, `chat_server.lsl` state, route and resume).

**Stability**

- [x] The console state lock can no longer leak: a fixed hard hang during audits
      (verified: the audit runs in about 12 s, repeats, and `/api/state` answers
      throughout while CPU falls back to idle).
- [x] Console state survives restarts (alternating state files, saved debounced
      and forced at turn boundaries).

**UI**

- [x] One design for the console and the marketplace pages (shared shell, dark
      mode behind a toggle, light by default).
- [x] The account menu shows the live balance (`balance $X · budget $Y`) and can
      sign out from every page, not just the console.

## Left to do

- [ ] Upload the demo video and paste the link into the HQ submission form.
- [ ] Rotate the hackathon keys after the event (Stripe, GitHub, Apify,
      ElevenLabs, LuxAI, the receipt signing key).
- [ ] Optional: switch the marketplace from Python to LSL — see the gaps below.

## Known gaps (for a handover)

### 1. Marketplace in LSL — ported, not switched

`backend/lsl/marketplace.lsl` replaces `backend/python/marketplace.py` on the
same database (schema, JSON shapes, ledger rows). Verified on a copy of the live
database with the LSL service on port 3072: offers and the invariant match, a
full buy-refuse-refund flow ran, and a receipt signed by the LSL verifies with
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

### 2. Old signed receipts do not verify after the LC to USD rename

Receipts signed before the rename carry `"currency": "Lux Coins"` inside the
signed payload, while the verifier rebuilds it with the current constant
(`USD`), so `tools/verify_receipt.py` reports tampering. This affects the Python
marketplace as well; the fresh receipts (signed after the rename) verify. Decide:
keep a historic currency constant, store the currency per receipt, or treat the
payload currency as part of the signed data and never rename it again.

Related, by design: ledger action names such as `LUX_COINS_ISSUED` keep their
historic spelling because they are stored in existing rows and mirrored by the
LSL port. Everything *displayed* is USD.

### 3. Speech sidecar runs in Python (the LSL one stays as an alternative)

The LSL sidecar (`backend/lsl/speech.lsl`) works and stays in the repository, but
production runs the Python one (`backend/python/tts.py`), which proved more
reliable in practice. The unit keeps the cache directory
`/srv/www/proofpay-tts` so the mp3 files Caddy serves stay where they are.
`tts.py` also strips markdown before speaking (so `**bold**` is not read as
asterisks) and forwards the caller's token when it reads a message from the
console (messages are per account, so this is required). The console renders
`**bold**`, `*italic*`, `code` and links in every agent message and delivery, so
the model output stops showing raw asterisks.

### 4. Smaller leftovers

- [ ] The guide tour ends on the profile step; consider a final "you are ready"
      card linking to the docs.
- [ ] `marketplace.py` remains Python on purpose (SQLite transactions and
      constraints are the safety net for money); revisit only with a real ledger
      design, not as a quick port.
