# Deployment guide

Target: a single Linux host (x86_64) running all backend processes on loopback,
with a reverse proxy (Caddy) exposing the console and the marketplace.

For a local development run from zero (including the LSL installation) see
`SETUP.md`; this document covers the server.

## 1. Prerequisites

* Python 3.11+ (standard library only — no pip packages).
* LSL 0.8.9 (`lsl compile`) and the `aikit` package installed:
  ```sh
  lsl install aikit
  ```
* A LuxAI API key (Flash model) and a reverse proxy with TLS for public use.

## 2. Build the LSL binaries

```sh
lsl compile lsl/seller.lsl      build/seller
lsl compile lsl/chat_server.lsl build/web-server
```

Both binaries are static native executables; one `seller` binary serves all
seller instances (behaviour comes from the environment file).

## 3. Install files

```sh
install -d -m 750 -o root -g proofpay /opt/proofpay-mvp
install -m 644 python/marketplace.py python/services.py python/ui.py /opt/proofpay-mvp/
install -m 755 build/seller build/web-server /opt/proofpay-mvp/

# frontend statics served by the console service
install -d -m 750 -o root -g proofpay /opt/proofpay-mvp/web
install -m 644 web/index.html web/app.js web/style.css /opt/proofpay-mvp/web/

# runtime data (SQLite, JSON reports)
install -d -m 750 -o proofpay -g proofpay /var/lib/proofpay-mvp
install -d -m 750 -o proofpay -g proofpay /var/lib/proofpay-mvp/web
```

## 4. Secrets and configuration

```sh
install -d -m 750 -o root -g proofpay /etc/proofpay-mvp

# one random token per account and seller (>= 32 chars), mode 640
openssl rand -hex 32 > /etc/proofpay-mvp/client.token
for s in partial complete scout insight atlas; do
  openssl rand -hex 32 > /etc/proofpay-mvp/$s.token
done
chmod 640 /etc/proofpay-mvp/*.token && chown root:proofpay /etc/proofpay-mvp/*.token

install -m 640 -o root -g proofpay luxai.key /etc/proofpay-mvp/luxai.key
# receipt signing key (Ed25519): every settled job gets a verifiable signature
openssl genpkey -algorithm ed25519 -out /etc/proofpay-mvp/receipt-signing.key
openssl pkey -in /etc/proofpay-mvp/receipt-signing.key -pubout -out /etc/proofpay-mvp/receipt-signing.key.pub
chmod 640 /etc/proofpay-mvp/receipt-signing.key* && chown root:proofpay /etc/proofpay-mvp/receipt-signing.key*
# market.json: "receipt_signing_key_file": "/etc/proofpay-mvp/receipt-signing.key"

# optional: Apify token for live-web research (falls back to Wikipedia without it)
printf '%s' 'apify_api_…' > /etc/proofpay-mvp/apify.token
chmod 640 /etc/proofpay-mvp/apify.token && chown root:proofpay /etc/proofpay-mvp/apify.token
cp deploy/market.json.example /etc/proofpay-mvp/market.json   # edit paths
cp deploy/chat.env.example  /etc/proofpay-mvp/chat.env        # edit paths
for s in partial complete scout insight atlas; do
  cp deploy/seller.env.example /etc/proofpay-mvp/seller-$s.env   # edit per seller
done
chmod 640 /etc/proofpay-mvp/market.json /etc/proofpay-mvp/chat.env /etc/proofpay-mvp/seller-*.env
chown root:proofpay /etc/proofpay-mvp/market.json /etc/proofpay-mvp/chat.env /etc/proofpay-mvp/seller-*.env
```

Required per-seller values: `SELLER_ID`, `SELLER_MODE` (`partial` for the
deliberately incomplete QuickCheck, `general` otherwise), `SELLER_PORT`
(3081 partial, 3082 complete, 3083 scout, 3084 insight, 3085 atlas),
`SELLER_TOKEN_FILE`, `MARKET_INTERNAL_URL`, `LUXAI_KEY_FILE`.

## 5. systemd

```sh
install -m 644 deploy/proofpay-market.service deploy/proofpay-seller@.service \
               deploy/proofpay-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now proofpay-market.service proofpay-web.service
for s in partial complete scout insight atlas; do
  systemctl enable --now proofpay-seller@$s.service
done
```

Units run as `proofpay` with `ProtectSystem=strict`, `NoNewPrivileges`,
`MemoryMax` 128–256 MB and a `ReadWritePaths=/var/lib/proofpay-mvp` exception.

## 5b. Speech sidecar (optional, ElevenLabs)

```sh
install -m 644 backend/python/tts.py /opt/proofpay-mvp/tts.py
printf '%s' 'sk_…' > /etc/proofpay-mvp/elevenlabs.key
chmod 640 /etc/proofpay-mvp/elevenlabs.key && chown root:proofpay /etc/proofpay-mvp/elevenlabs.key
install -m 644 deploy/proofpay-tts.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now proofpay-tts.service
```

The sidecar listens on `127.0.0.1:3071`, reads the message text from the console
state (`TTS_CONSOLE_URL`) and caches rendered mp3 files in
`/var/lib/proofpay-mvp/tts`. Without the key file it answers `503` and the
frontend button reports that speech is not configured.

## 6. Reverse proxy

Use `deploy/Caddyfile.snippet`. Notes:

* Route `/hackathon01/web/api/speak` to the speech sidecar **before** the
  console route (the console serves everything else under `/hackathon01/web/`).
* The console route needs `header_up -Via` — the LSL HTTP parser rejects
  duplicated hop-by-hop headers that a proxy chain may add.
* Keep the console and its API under one origin so the browser needs no CORS.
* If the deployment sits behind a caching CDN, keep `Cache-Control: no-store`
  on API responses (already sent by the console and the marketplace).

## 7. Verification

```sh
curl -s http://127.0.0.1:3070/health
curl -s http://127.0.0.1:3069/api/health
curl -s http://127.0.0.1:3070/api/offers | head -c 300
for p in 3081 3082 3083 3084 3085; do curl -s http://127.0.0.1:$p/health; done
```

End-to-end smoke test (uses one real model call + one purchase):

```sh
curl -s -X POST http://127.0.0.1:3069/api/chat \
  -H 'Content-Type: application/json' -d '{"message":"Please audit the marketplace demo cart."}'
sleep 60 && curl -s 'http://127.0.0.1:3069/api/state?lite=0' | head -c 500
```

The public dashboard at `/hackathon01/` must show `issued == accounted`
("invariant holds").

## 8. Operational notes

* **Conversation state is in-memory** in the console service; a restart starts
  a new conversation. Wallets, jobs and the ledger persist in SQLite.
* The console holds one turn at a time in a worker thread; HTTP requests are
  served by a single accept loop, so clients should serialise their polls.
* State history is capped (60 messages / 200 tool calls / 100 payment receipts)
  to keep the process memory bounded; `gc()` runs between turns only (running
  it during a model stream corrupts the LSL stream parser).
* Back up `/var/lib/proofpay-mvp/market.db` (SQLite) before upgrades; the
  config `market.json` is safe to re-read on restart and re-seeds missing
  offers without touching existing balances.
* Tokens must never be committed, logged or embedded in a browser.
