# ProofPay — agentic economy backend

An agent-to-agent marketplace with **simulated** money: a conversational agent
discovers services, pays through escrow, verifies deliveries and resolves
disputes without human button presses. Sellers are independent processes that
deliver real work (Wikipedia research, Python code, summaries, translations,
ideas, HTTP cart audits) and are paid only for verified deliveries.

* `simulated_payments: true` is part of every API response — payments are test
  credits ("Lux Coins") in a central SQLite ledger, not a blockchain.
* Caps hold (a wallet can never spend more than its budget).
* Nothing pays twice (idempotency keys + unique constraints + one settlement
  per job).

## Components

| Component | Source | Port | Role |
|---|---|---|---|
| Marketplace | `python/marketplace.py`, `python/services.py` | 3070 | Wallets, escrow, offers, jobs, ledger, structural verification |
| Agent console | `lsl/chat_server.lsl` | 3069 | Chat agent (LuxAI Flash), tool-call feed, serves the web frontend |
| Sellers (×5) | `lsl/seller.lsl` | 3081–3085 | Deliver purchased services; two cart-audit sellers + three general sellers |
| Public overview | `static/dashboard.html` | — | Read-only marketplace dashboard (public API consumer) |

The LSL programs are written in [LSL](https://lsl.lux-ai.cz) and compiled to
native Linux binaries with `lsl compile`; they reuse the `aikit` package for
model calls and SSE streaming. The marketplace is Python standard library +
SQLite.

## Layout

```
backend/
├── python/          marketplace.py, services.py, deploy_server.py
├── lsl/             chat_server.lsl, seller.lsl, chat_core.lsl,
│                    buyer_core.lsl, stream_codec.lsl, service_menu.lsl
├── deploy/          systemd units, Caddyfile.snippet, *.example configs
├── static/          dashboard.html (public overview)
├── docs/API.md      frontend integration guide (endpoints, state, polling)
└── docs/DEPLOY.md   build + deployment guide
```

## How a purchase works

1. The agent calls `fetch_offers` (Flash tool) and fetches the real catalog.
2. It picks the cheapest matching provider and funds the job — the price moves
   from the wallet's `available` to `locked` (escrow).
3. The seller executes; the console streams the live preview to the browser.
4. The marketplace verifies the delivery against the agreed contract
   (structure, syntax, cited sources, execution receipts).
5. Verified delivery → escrow released to the seller (`PAID`). Failed delivery
   → automatic refund (`REFUNDED`), and the agent re-purchases from another
   seller.

The demo cart fixture (`PROOFPAY_FIXTURE=buggy`) plants a real
quantity-recalculation bug so the full dispute path can be demonstrated with
one click.

## Frontend

The reference web client lives in `../frontend/` and is served by the console
service (`PROOFPAY_WEB_DIR`). It polls `GET /api/state?lite=1`, fetches the full
state on revision changes and posts messages with `POST /api/chat`.

See **`docs/API.md`**. A frontend is a simple poller: `GET /api/state?lite=1`
every ~500 ms, a full `GET /api/state` whenever a revision changes, and
`POST /api/chat` to send a message. The deployed console UI
(`index.html`/`app.js`/`style.css` in `PROOFPAY_WEB_DIR`) is the reference
client.

## Run it

From zero on a clean machine: **`docs/SETUP.md`** (LSL install, `aikit`, build,
config, smoke test). The short version:

```sh
scripts/run-local.sh      # build + start everything on loopback
scripts/stop-local.sh
```

## Deploy

See **`docs/DEPLOY.md`** for build commands, environment files, systemd units,
reverse-proxy routing and verification steps.
