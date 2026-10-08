# Hackathon_LuxAI

Agentic-economy hackathon project: a marketplace where AI agents discover
services, pay each other through escrow, verify deliveries and resolve disputes
— with **simulated** money (Lux Coins).

## Layout

```
backend/   server side: marketplace (Python), LSL agents and sellers,
           deployment assets and the frontend integration guide
frontend/  (separate) web client — connect it using backend/docs/API.md
```

## Start here

* `backend/README.md` — components, architecture, purchase lifecycle.
* `backend/docs/API.md` — **how to connect a frontend**: endpoints, state
  object, polling strategy, marketplace API, invariants.
* `backend/docs/DEPLOY.md` — build and deployment guide.

## Quick facts

* Sellers are [LSL](https://lsl.lux-ai.cz) programs (compiled to native
  binaries) paid per verified delivery; the marketplace is Python + SQLite.
* The agent console serves the frontend from the same origin as its API —
  no CORS, no tokens in the browser.
* Payments are labelled SIMULATED everywhere: caps hold, nothing pays twice.
