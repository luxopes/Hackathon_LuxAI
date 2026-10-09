# Hackathon_LuxAI

Agentic-economy hackathon project: a marketplace where AI agents discover
services, pay each other through escrow, verify deliveries and resolve disputes
— with **simulated** money (simulated USD). Mocked payments are labelled SIMULATED,
caps hold and nothing pays twice.

## LSL sqlite library in production

The `sqlite` 1.0.0 library from the LSL package registry
(`lsl install sqlite`, published 2026-10-09) is installed on the build machine
and on the server, and `tools/ledger_check.lsl` is its first production user: an
independent reader that re-derives the market invariant straight from the
database with SQL.

```sh
lsl compile tools/ledger_check.lsl build/ledger-check
PROOFPAY_DB=/var/lib/proofpay-mvp/market.db build/ledger-check
# market invariant: 2927 issued == 2927 accounted -> HOLDS
```

It prints a human line plus one JSON line and exits non-zero when the numbers do
not match, so it runs unchanged in monitoring: `proofpay-ledger-check.timer`
executes it every five minutes on the server and the result lands in the
journal.

## Quick start (from zero)

```sh
# 1) LSL — the language the agents and sellers are written in — https://lsl.lux-ai.cz/
curl -LO https://lsl.lux-ai.cz/downloads/lsl-0.8.9-linux-x86_64.tar.gz
tar -xzf lsl-0.8.9-linux-x86_64.tar.gz
./lsl-0.8.9/bin/lsl-install "$HOME/.local"
lsl install aikit

# 2) the whole stack (marketplace, five sellers, agent console + frontend)
git clone https://github.com/luxopes/Hackathon_LuxAI.git
cd Hackathon_LuxAI
scripts/run-local.sh
#   console + frontend: http://127.0.0.1:3069/
#   marketplace:        http://127.0.0.1:3070/
```

Try in the console chat: `Please audit the marketplace demo cart.` — the agent
buys the cheapest audit, refuses the incomplete delivery, gets a refund and
re-purchases from another seller. `scripts/stop-local.sh` stops everything.

## Layout

```
frontend/   reference web client (the console UI)
backend/    marketplace (Python), LSL agents and sellers, deploy assets, docs
scripts/    local run/config helpers
```

## Docs

* **`TODO.md`** — what is left: rewrite the marketplace pages (`/`, `/payments`,
  `/receipt/{id}`, `/docs`) in the agent-console design, and port the speech
  sidecar from Python to LSL.

* **`tools/verify_receipt.py`** — verify a payment receipt offline: recomputes
  the canonical payload, checks the SHA-256 and the Ed25519 signature against the
  published key (`python3 tools/verify_receipt.py --url <receipt-url>`).
* **`SPRINT.md`** — the plan for the hackathon night (7 hours to code freeze).
* **`ROADMAP.md`** — the post-hackathon plan: hardening, real payment rails
  (x402/AP2/Stripe), seller network, arbitration and scale.
* **`backend/docs/SETUP.md`** — run everything locally from zero (incl. LSL
  install and troubleshooting).
* **`backend/docs/API.md`** — how to connect a frontend: endpoints, state
  object, polling strategy, marketplace API, invariants.
* **`backend/docs/DEPLOY.md`** — production deployment (systemd, Caddy, env).
* **`backend/README.md`** — architecture and the purchase lifecycle.
* **`CLAUDE.md`** — a single-file project brief with all the context (also
  handy to hand to an AI assistant).
* **`CONTRIBUTING.md`** — branch rules: `main` is owner-only, others use
  branches and pull requests.

## Quick facts

* Sellers are [LSL](https://lsl.lux-ai.cz) programs compiled to native binaries
  and paid per verified delivery; the marketplace is Python + SQLite.
* The agent console serves the frontend from the same origin as its API — no
  CORS, no tokens in the browser.
* Everything runs on loopback: marketplace 3070, sellers 3081–3085, console 3069.
* Sellers are paid for real: every *paid* settlement creates a Stripe Connect
  **Transfer** to the seller's connected account (test mode), tagged with the
  job and shown on the receipt; refunds never transfer.
* Buyers can top up with a card through **Stripe in test mode** (Checkout); the
  card payment is real in the sandbox, the coins it buys stay simulated and are
  labelled as such everywhere.
