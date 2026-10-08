# Roadmap — from hackathon prototype to a production agent economy

An advanced, concrete implementation plan for ProofPay / Lux Coins: the path from
the current verified prototype (escrow, verified deliveries, dispute resolution,
receipts, voice) to a network that moves **real** value between agents.

Every workstream lists concrete changes against the current code
(`backend/python/*`, `backend/lsl/*`, `frontend/*`), acceptance criteria and
honest risk notes. Protocol details (x402, AP2, A2A, MCP, MPP) are referenced
against their published specifications as of 2026 and must be re-checked before
implementation.

---

## 1. Where the system stands today (verified)

| Capability | State | Evidence |
|---|---|---|
| Escrow lifecycle | Working | `POST /api/jobs` → `FUNDED`; `settle` → `PAID`, `refund` → `REFUNDED` (`marketplace.py`) |
| Verified deliveries | Working | Contract-based checks: cart cases + receipts, Python AST, cited sources, seller attestation (`services.py`, `verification()`) |
| Dispute resolution | Working | Cheapest seller delivers 1 of 3 checks → automatic refund → re-purchase elsewhere (demo scenario) |
| Idempotency (“nothing pays twice”) | Working | `UNIQUE(session_id, idempotency_key)` + one settlement per job; ledger reconciliation on every receipt |
| Caps hold | Working | Wallet budget 1–100 LC enforced at purchase; `issued == accounted` invariant checked on every page |
| Audit trail | Working | `/receipt/{job_id}`: double-entry movements, reconstructed balances, contract + delivery SHA-256, execution receipts |
| Agent loop | Working | Flash tool calls (`fetch_offers`, `resolve_request`, `purchase_offer`) with live SSE previews; auto-demo runs the full dispute path unattended |
| Voice | Working | ElevenLabs TTS sidecar (`tts.py`), message-index API, cached mp3, auto-voice toggle |
| Public transparency | Working | `/` overview, `/payments` list, `/docs`; 114 verified working links |

**Honest gaps.** Simulated credits only; no authentication on the console API; in-memory
conversation state; polling instead of push; single-node SQLite; structural (not semantic)
verification; central operator (no independent auditors); no seller onboarding, reputation
or staking; no fiat/stablecoin rails; no spending mandates beyond the wallet budget.

---

## 2. Design principles for the next iterations

1. **Money first.** Every new feature must preserve exactly-once settlement and the
   `issued == accounted` invariant. A feature that cannot be reconciled is not shipped.
2. **Receipts are the product.** Anything the system does must be provable to a third
   party offline: signed, hash-anchored, exportable.
3. **No human presses buttons — but policies do.** Autonomy comes from pre-authorized
   spending mandates and policy engines, not from a click.
4. **Fail closed, refund fast.** Sellers are paid only after verification; every failure
   path ends in an automatic refund or an explicit arbitration, never a silent loss.
5. **Honest labels.** Simulated vs. real value is always distinguishable in the data
   (`simulated_payments` flag) and in the UI (`SIMULATED` badges).

---

## 3. Phase 1 — Hardening and trust (1–2 weeks)

### W1.1 Console authentication and spend policies
* Add an auth layer in front of `/api/chat|speak|catalog|cancel|new`:
  - self-hosted: signed session cookie (HMAC) via a small login endpoint with an operator
    password file (640 root:proofpay), or Caddy `basic_auth` for a zero-code option;
  - per-session spend policy: `{daily_cap, per_job_cap, allowed_capabilities[], allowlist
    of sellers}` loaded from `chat.env`, evaluated before `buy`;
  - audit every rejected purchase as a ledger row (`SPEND_POLICY_DENIED`, amount 0).
* Files: `chat_server.lsl` (route guard), `marketplace.py` (`policy` check in `buy`),
  `deploy/chat.env.example` (new keys).
* Acceptance: an unauthenticated `POST /api/chat` returns 401; a purchase above
  `daily_cap` fails with a receipt-visible reason; existing demo flows unaffected.

### W1.2 Push transport instead of polling
* Replace the 500 ms lite poll with server-sent events on the console:
  `GET /api/events` streams `{messages_revision, audit_revision, wallet, status}` deltas
  and full payloads on change. Keep polling as a fallback (`?transport=poll`).
* The LSL accept loop already handles long-lived connections (seller previews use SSE);
  the console must spawn a worker thread per subscriber and reuse the existing event
  queue with a bounded buffer and heartbeats.
* Acceptance: console CPU/network drops by ≥80 % while idle; reconnection with
  `Last-Event-ID` restores state without a full refetch; demo latency unchanged.

### W1.3 Signed receipts (offline verification)
* Sign every settlement: `receipt_sig = Ed25519(canonical_json(receipt_public))`.
  Store `signatures(id, job_id, alg, public_key_id, signature, created)`; publish the
  public key at `/.well-known/proofpay-keys.json`.
* Ship `tools/verify_receipt.py`: takes a receipt JSON, fetches/embeds the public key,
  re-computes hashes and verifies the signature — **no trust in our server required**.
* Acceptance: the tool validates a fresh receipt and rejects a tampered amount;
  the receipt page shows a “signature verified” badge computed client-side.

### W1.4 Ledger export, auditor mode and anchoring
* `GET /api/ledger/export?format=jsonl|csv&from=…&to=…` (public, append-only view).
* Merkle tree over ledger row hashes per 5-minute epoch; publish epoch roots at
  `/api/epochs` and anchor them externally (OpenTimestamps → Bitcoin is free and
  sufficient for a prototype; a hash-notary API is an alternative).
* Acceptance: independent recomputation of an epoch root from the export matches the
  published root; documentation explains the verification steps.

### W1.5 Observability
* Emit OpenTelemetry spans for every tool call (the console already traces name,
  source, duration, model call id) and for marketplace state transitions; export via
  OTLP to a local collector.
* Prometheus endpoint `:9100/metrics` for the Python services: purchase counters per
  state, escrow seconds, refund ratio, verification failures, model latency.
* Acceptance: a Grafana dashboard (JSON committed under `deploy/`) shows the dispute
  path end-to-end from one demo run.

### W1.6 Test suite and CI
* Property-based tests for the ledger: random sequences of issue/escrow/settle/refund
  must preserve `issued == accounted`, and no state may allow double settlement
  (`hypothesis`-style generators written in plain Python to keep stdlib-only runtime).
* Chaos tests: kill a seller mid-execution, drop the marketplace during escrow, replay
  an idempotent purchase — assert automatic refund/pay exactly once.
* GitHub Actions: build the LSL binaries (needs the compiler in the runner), run the
  Python tests and the deterministic service tests, publish artifacts.
* Acceptance: CI green on every push; a failing invariant fails the build.

---

## 4. Phase 2 — Real money rails (2–6 weeks)

Goal: keep the exact same UX and invariants while the value becomes real. The
marketplace ledger stays the source of truth; external rails are **settlement
adapters** behind the escrow state machine.

### W2.1 x402 settlement adapter (stablecoin, per-request)
* Add a payment rail plugin interface to `marketplace.py`:
  `class Rail: authorize(job) / capture(job) / refund(job) / status(job)`.
* Implement `X402Rail`: the marketplace serves `402 Payment Required` challenges for
  purchase endpoints; the buyer agent (or the operator's wallet service) pays in USDC on
  Base; the rail verifies the on-chain transfer (tx hash + confirmation depth) before
  the job leaves `FUNDED`.
* Map internal movements to chain events in a new table
  `settlements(job_id, rail, external_id, amount_minor, asset, network, confirmations,
  status)`; the receipt page links the on-chain transaction.
* Acceptance: a cart-audit job settles through testnet USDC end-to-end; the receipt
  shows both the internal ledger and the external transaction; refunds execute as
  on-chain transfers with the same receipt guarantees.

### W2.2 AP2 mandates as our contract layer
* Adopt the AP2 vocabulary: our purchase **contract** becomes a *Cart Mandate*
  (items, price, delivery terms, expiry) derived from an *Intent Mandate*
  (budget, allowed capabilities, per-job cap — exactly the W1.1 policy).
* Persist mandates: `mandates(id, session_id, kind, payload, signature, created,
  expires_at, revoked_at)`; the agent’s authority is the signed mandate, not an
  implicit session.
* Produce AP2-style receipts (Checkout Receipt on delivery, Payment Receipt on
  settlement) alongside our current receipt; keep both verifiable with W1.3.
* Acceptance: a purchase can be executed from a signed mandate alone (no UI); an
  expired mandate is rejected; the mandate→cart→receipt chain is visible on the
  receipt page.

### W2.3 Session budgets (MPP-style) for long-running work
* Support streamed micropayments for ongoing services (research feeds, monitoring):
  pre-authorized session with a spending limit and per-interval drawdown; each drawdown
  is a ledger row and an x402/stablecoin transfer.
* Acceptance: a session runs for N intervals within the cap; stopping mid-session
  refunds the unspent remainder automatically.

### W2.4 Fiat rail (optional, Stripe)
* `StripeRail`: PaymentIntents for top-ups (buyer) and Connect transfers for payouts
  (sellers); the same job lifecycle, with payout after verification.
* Acceptance: a top-up converts to wallet balance with a receipt; payout settles to a
  connected account in test mode; refunds reverse both rails.

### W2.5 Compliance hooks
* Per-rail limits and KYC gating (e.g., unverified accounts are capped to small
  stablecoin amounts), transaction reporting export, invoice generation per session.
* Acceptance: limits are enforced in code and visible in receipts; the export contains
  everything a bookkeeper needs.

---

## 5. Phase 3 — Open network (1–3 months)

### W3.1 Seller onboarding, staking and reputation
* Public registration API (`POST /api/sellers`) with capability declarations, price
  list, SLA and a **stake/deposit** (simulated first, then stablecoin).
* Reputation score from verified history: delivery success rate, refund rate, median
  latency, dispute outcomes — computed from the ledger, published on a seller page.
* Acceptance: a new seller joins without operator edits, gets discovered, fails a
  delivery and the stake/refund policy applies automatically.

### W3.2 Discovery and interop: A2A cards + MCP server
* Publish an A2A Agent Card at `/.well-known/agent-card.json` (capabilities, prices,
  accepted payment protocols) so foreign agents can discover us; implement the A2A
  task lifecycle for `execute` (submit → working → completed/failed + artifacts).
* Ship an MCP server (`mcp_server.py`) exposing tools: `fetch_offers`, `buy`,
  `verify`, `receipt`, `balance` — the same operations the console uses, so any MCP
  client (Claude, IDEs, other agents) can transact with the marketplace.
* Acceptance: an external MCP client completes a real purchase; an A2A client
  discovers our card and executes a job.

### W3.3 Arbitration and evidence
* Two-stage dispute path: (1) automatic structural verification (today), (2) LLM jury
  with a fixed rubric on the evidence pack (contract, delivery, receipts, previews),
  (3) human escalation for large amounts.
* Evidence pack export (`/api/jobs/{id}/evidence.zip`) with hashes and signatures, so a
  neutral third party can reproduce the verdict.
* Acceptance: a deliberately bad delivery is rejected by the jury with a reasoned
  verdict; the verdict references only hashed evidence.

### W3.4 Model routing, semantic verification, cost control
* Route work across model providers by price/latency/quality; a second (cheap) model
  spot-checks the delivery for semantic drift; disagreements trigger the jury path.
* Track cost per job (`jobs.cost_minor`) and expose margin per seller/service;
  arbitrage: the agent may split work across sellers when the price-quality curve
  favours it.
* Acceptance: a report shows cost, price and margin per job; semantic checks catch an
  injected wrong-fact delivery in a fixture test.

### W3.5 Scale and durability
* Migrate SQLite → PostgreSQL with the same schema; add a transactional outbox for
  rail calls, exactly-once delivery to external APIs and idempotent consumers.
* Sessions become shardable (per-session partition key); the marketplace runs multiple
  replicas with a leader for settlement.
* Acceptance: a load test (≥50 jobs/minute) keeps the invariant and p99 receipt
  completeness; a kill -9 during settlement never double-pays.

---

## 6. Concrete data-model and API deltas (Phase 1–2)

```sql
-- W1.3 signed receipts
CREATE TABLE signatures(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, alg TEXT NOT NULL,
  public_key_id TEXT NOT NULL, signature TEXT NOT NULL, created REAL NOT NULL);
-- W1.4 anchoring
CREATE TABLE epochs(id INTEGER PRIMARY KEY, started REAL, ended REAL, root TEXT,
  anchored_at REAL, anchor_ref TEXT);
-- W2.1 external settlement
CREATE TABLE settlements(job_id TEXT NOT NULL, rail TEXT NOT NULL, external_id TEXT,
  amount_minor INTEGER, asset TEXT, network TEXT, confirmations INTEGER,
  status TEXT NOT NULL, created REAL, PRIMARY KEY(job_id, rail));
-- W1.1 policies / W2.2 mandates
CREATE TABLE mandates(id TEXT PRIMARY KEY, session_id TEXT, kind TEXT,
  payload TEXT, signature TEXT, created REAL, expires_at REAL, revoked_at REAL);
```

New public endpoints: `GET /api/epochs`, `GET /api/ledger/export`,
`GET /.well-known/proofpay-keys.json`, `POST /api/sellers`, `GET /api/sellers/{id}`,
`GET /api/jobs/{id}/evidence.zip`, `/api/state` event stream.

Modified endpoints: `POST /api/jobs` gains `rail` and `mandate_id`;
`GET /receipt/{job_id}` renders signatures, epochs and rail transactions.

---

## 7. Protocol mapping

| Layer | Protocol | Our equivalent today | Plan |
|---|---|---|---|
| Context/tools | **MCP** | Console tool calls (Flash) | W3.2: expose the marketplace as an MCP server |
| Messaging/discovery | **A2A** | Offers API + console | W3.2: Agent Card + task lifecycle |
| Payments | **AP2** (mandates, receipts) | Wallet budget + contract + receipts | W2.2: adopt mandate/receipt vocabulary and signatures |
| Settlement (crypto) | **x402** | Simulated ledger only | W2.1: stablecoin adapter behind escrow |
| Settlement (sessions) | **MPP-style** | One job per turn | W2.3: session budgets with interval drawdowns |
| Settlement (fiat) | Stripe | — | W2.4: PaymentIntents + Connect |

---

## 8. Security, compliance, cost

* **Secrets**: keys stay in files (640) on the server; nothing in the browser or repo
  (already true for LuxAI, Apify, ElevenLabs, client/seller tokens).
* **Authorization**: sessions and seller tokens are per-identity; every privileged call
  is scoped (W1.1 adds operator auth on the console).
* **Replay/idempotency**: keys are per attempt; rails must be idempotent externally too
  (x402 nonce, Stripe idempotency keys, Connect transfer metadata).
* **Compliance**: stablecoin rails need geo/limits review; fiat needs KYC; keep the
  simulated mode as the default for demos and CI.
* **Cost model**: model cost per job is bounded by the contract tier; the marketplace
  can enforce a max-cost-per-job and a daily provider budget.

---

## 9. Metrics and SLOs

| Metric | Target (Phase 1) | Target (Phase 3) |
|---|---|---|
| Settlement correctness (no double pay) | 100 % over test suite | 100 % under chaos/load |
| Receipt completeness (movements + hashes + signature) | 100 % | 100 % |
| End-to-end purchase latency (p50/p95) | < 45 s / < 120 s | < 25 s / < 60 s |
| Refund on failed delivery | automatic, < 5 s after verdict | < 2 s, rail-agnostic |
| Uptime of public pages + console | 99 % (single node) | 99.9 % (multi-replica) |
| Invariant check | per request | per epoch with anchoring |

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Real rails introduce irreversible failures | Money loss | Testnet first, small caps, manual approval above a threshold (policy), refund-first design |
| Model delivers plausible but wrong work | Reputation | Semantic second-model checks + jury + honest “structural only” labels |
| Seller collusion / Sybil sellers | Trust | Staking, reputation from verified history, per-seller caps |
| Protocol churn (x402/AP2 still evolving) | Rework | Adapter layer isolates rails; specs pinned and re-checked per release |
| Single-node SQLite | Availability | Phase 3 migration with outbox; nightly verified backups |
| Voice/TTS or model quota exhaustion | Demo failure | All expensive calls cached (TTS mp3 cache, offer cache), graceful fallbacks (Wikipedia for research) |

---

## 11. Milestones and effort

| Milestone | Scope | Effort |
|---|---|---|
| M1 “Trusted demo” | W1.1 auth+policy, W1.3 signatures, W1.4 export, W1.6 CI | 1–2 weeks |
| M2 “Push & observe” | W1.2 SSE, W1.5 observability | 1 week |
| M3 “Testnet money” | W2.1 x402 adapter on testnet, W2.2 mandates | 2–3 weeks |
| M4 “Open doors” | W3.1 sellers, W3.2 A2A/MCP | 3–4 weeks |
| M5 “Arbitrated market” | W3.3 jury, W3.4 routing, W3.5 Postgres | 4–6 weeks |

---

## 12. Open questions

1. Which rail is the default for the first real deployment — x402 on Base, Stripe, or
   both behind the same adapter?
2. Do we require mandates to be signed by the **buyer** (user key) or accept an
   operator-signed policy as the authority (lower friction, weaker guarantees)?
3. Is anchoring receipts externally (OpenTimestamps) enough for auditors, or is a
   notary service with SLAs required?
4. How much autonomy is acceptable without human review: per-job cap, daily cap, or a
   cumulative budget per mandate?
5. Should sellers be allowed to own the delivery keys (sign their own artifacts) so the
   marketplace is never the sole attestor?
