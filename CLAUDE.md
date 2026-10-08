# CLAUDE.md — project brief for AI assistants

Everything needed to understand, run and modify this repository. Read this
first; detailed docs are linked at the end.

## What this is

An **agentic-economy marketplace with simulated money** built for the
*From Dusk Till Dawn* hackathon (Case 02: Agentic Economy). A conversational
agent discovers offered services, pays through escrow, verifies deliveries and
resolves disputes — **without a human pressing buttons**. Key guarantees that
are demonstrated and must never regress:

* **Mocked payments are labelled SIMULATED** — test credits ("Lux Coins") in a
  central SQLite ledger, no blockchain, no real money.
* **Caps hold** — a wallet can never spend beyond its budget.
* **Nothing pays twice** — idempotency keys + unique constraints; one escrow
  and one settlement per job.

The conflict story: the cheapest cart-audit seller (`QuickCheck`) promises
three checks and delivers one. The marketplace verifies against the agreed
contract, the agent gets a **refund**, re-purchases from another seller and
only pays for a verified delivery. The demo cart contains a real
quantity-recalculation bug (`CART BUG · quantity_update`) that the paid audit
uncovers.

## Repository layout

```
frontend/                     reference web client (HTML/JS/CSS, no build step)
scripts/run-local.sh          build + start the whole stack locally
scripts/stop-local.sh         stop it
scripts/local_config.py       generate local tokens/config/env files
backend/
  README.md                   architecture and purchase lifecycle
  docs/API.md                 frontend integration guide (endpoints, state, polling)
  docs/DEPLOY.md              production deployment (systemd, Caddy, env)
  docs/SETUP.md               run everything locally from zero (LSL install included)
  python/marketplace.py       marketplace: wallets, escrow, jobs, ledger, verification
  python/services.py          service catalog (capabilities, prices, structure checks)
  python/deploy_server.py     server installer (tokens, market.json, systemd, Caddy)
  lsl/chat_server.lsl         agent console: HTTP API + static frontend serving
  lsl/chat_core.lsl           conversational agent (Flash tool calls, buy/refund flow)
  lsl/buyer_core.lsl          purchase engine (HTTP to marketplace, SSE previews, tracing)
  lsl/seller.lsl              seller worker (cart audit via HTTP checks, model deliveries)
  lsl/stream_codec.lsl        SSE delta parser for streaming deliveries
  lsl/service_menu.lsl        service descriptions/limits used in prompts
  deploy/                     systemd units, Caddyfile snippet, *.example configs
  static/dashboard.html       public read-only marketplace overview
  python/tts.py               speech sidecar (ElevenLabs, message-index API)
```

## Run it (from zero)

```sh
# 1) LSL (the language these agents are written in) — https://lsl.lux-ai.cz/
curl -LO https://lsl.lux-ai.cz/downloads/lsl-0.8.9-linux-x86_64.tar.gz
tar -xzf lsl-0.8.9-linux-x86_64.tar.gz
./lsl-0.8.9/bin/lsl-install "$HOME/.local"        # or sudo … /usr/local
lsl install aikit                                 # LuxAI Flash client + SSE streaming

# 2) the stack
git clone https://github.com/luxopes/Hackathon_LuxAI.git
cd Hackathon_LuxAI
scripts/run-local.sh
#   console + frontend: http://127.0.0.1:3069/
#   marketplace:        http://127.0.0.1:3070/
```

Config/tokens/logs live under `~/.local/state/proofpay-local`. The LuxAI key
defaults to `~/.config/lux-runner/api-key`; override with
`LUXAI_KEY_FILE=/path/to/key scripts/run-local.sh`. Cart-audit demos work
without a key; chat and the other services need it. Full detail:
`backend/docs/SETUP.md`.

## Architecture

| Process | Source | Port | Notes |
|---|---|---|---|
| Marketplace | `python/marketplace.py` + SQLite | 3070 | Python stdlib `ThreadingHTTPServer`; persistent state |
| Sellers ×5 | `lsl/seller.lsl` (one binary) | 3081–3085 | partial/complete = cart audits (HTTP checks), scout/insight/atlas = model services |
| Agent console | `lsl/chat_server.lsl` | 3069 | Serves the frontend; holds the marketplace client token; runs one chat turn per worker thread |
| Speech sidecar | `python/tts.py` | 3071 | ElevenLabs text-to-speech for stored agent messages (index-only API, disk cache) |

* **Tokens never reach the browser.** The console stores the marketplace
  `client_token` and proxies privileged calls. Public marketplace endpoints:
  `/health`, `/api/dashboard`, `/api/offers`, `/api/services`.
* **Languages:** backend logic in **LSL** (compiled to native ELF with
  `lsl compile`), marketplace in **Python 3 stdlib**, frontend plain JS.
* **Streaming:** the console consumes the marketplace's SSE delivery preview
  (`POST /api/jobs/{id}/execute` with `Accept: text/event-stream`) and
  re-renders it into the polled state; sellers stream model output through
  `stream_codec.lsl`.

## State and API (browser side)

The frontend polls `GET /api/state?lite=1` (~500 ms) and fetches the full state
when `messages_revision`/`audit_revision` changes; `POST /api/chat` starts one
turn (HTTP 409 while busy, 429 on the 2 s cooldown). The full state carries
`messages`, `tools` (every real Flash/HTTP/LSL call with durations and I/O) and
`payments` (PAID/REFUNDED receipts with ledger IDs and SHA-256 hashes). The
complete reference is `backend/docs/API.md`.

Purchase lifecycle:

```
POST /api/sessions → wallet+budget
POST /api/jobs     → FUNDED (price moves available → locked = escrow)
POST /api/jobs/{id}/execute → RUNNING (+SSE preview)
GET  /api/jobs/{id}/verify  → valid_delivery / reasons[]
POST /api/jobs/{id}/settle  → PAID     (verified delivery only)
POST /api/jobs/{id}/refund  → REFUNDED (failed delivery only)
```

## LSL language notes (important when editing)

* Compiled: `lsl compile source.lsl output-binary`. Modules: `load buyer_core as buyer`.
* Syntax: `function f(a, b=1):` … `end`; void calls need `call f(x)`;
  `try: … else: … end`; `Error(Type: "message")`; lists/dicts as in
  `chat_server.lsl`; string indexing/slices `s[0:4]`, `list[n:]`.
* **There is no ternary operator** (`a if c else b` is invalid) — use
  variables and `if/end`.
* Threads: `unsafe:\n tid = thread_spawn(worker)\nend`. Shared state uses a
  spinlock over an `IntArray` (`atomic_cas`, `atomic_xchg`, `atomic_add`).
* **Do not call `gc()` while a worker turn is running** — it corrupts the SSE
  stream parser. `chat_server.lsl` calls it only when no turn is active.
* The console's accept loop is single-threaded: clients must serialise their
  HTTP requests (the frontend uses sequential polling for this reason).
* Secrets come from files (`readfile`, `env.require`); never embed them.

## Operational gotchas (learned in production)

* **Caching CDNs:** every response must carry `Cache-Control: no-store`
  (`send_raw` in `chat_server.lsl` does this); clients also add cache-busting
  query parameters. Without it a CDN cached the state for 10 minutes.
* **Proxy chains:** the LSL HTTP parser rejects duplicated `Via` headers —
  reverse proxies should send `header_up -Via` for the console route.
* **Redeploying a running binary fails (ETXTBSY):** stop the service, copy,
  start.
* State history is capped (60 messages / 200 tool calls / 100 receipts) and
  services run with `MemoryMax`; `gc()` only reclaims between turns.
* Conversation state is in-memory; a restart starts a new conversation while
  wallets/jobs/ledger stay in SQLite.

## Verification

```sh
curl -s http://127.0.0.1:3070/api/dashboard | python3 -m json.tool | head   # invariant.holds
curl -s -X POST http://127.0.0.1:3069/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"Please audit the marketplace demo cart."}'
# after ~1 min the state must show: REFUNDED (partial seller) + PAID (complete seller),
# and a CART BUG check line in the paid delivery.
```

## Conventions

* `main` is owner-only; contribute via a branch (fork if you lack write access)
  and a pull request — see `CONTRIBUTING.md`.
* Keep agent-facing text and delivery labels in the language selected by
  `PROOFPAY_LANG` (`en`/`cs`); seller prompts stay language-neutral.
* Never commit tokens, keys, databases or generated binaries.
