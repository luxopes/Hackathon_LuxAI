# Setup — run everything locally from zero

This guide takes a clean Linux x86_64 machine to a running ProofPay stack:
marketplace + five LSL sellers + the agent console with its web frontend, all
on loopback. For a production server installation see `DEPLOY.md`.

## 1. Install LSL

The backend agents and sellers are written in **LSL (Lux Simple Lang)**, compiled
to native binaries. Download the release for Linux x86_64 from
<https://lsl.lux-ai.cz/> (section *Ke stažení / Downloads*):

```sh
curl -LO https://lsl.lux-ai.cz/downloads/lsl-0.8.9-linux-x86_64.tar.gz
tar -xzf lsl-0.8.9-linux-x86_64.tar.gz
./lsl-0.8.9/bin/lsl version          # prints 0.8.9
```

Install it for your user (recommended) or system-wide:

```sh
# per-user install (adds ~/.local/bin to your PATH)
./lsl-0.8.9/bin/lsl-install "$HOME/.local"

# or system-wide (needs root)
sudo ./lsl-0.8.9/bin/lsl-install /usr/local
```

Updates later: `lsl update` (or `sudo lsl update --system`).

## 2. Install the `aikit` package

`aikit` provides the LuxAI Flash model client and SSE streaming used by the
agent, the chat loop and the sellers. It is distributed through the LSL
package registry:

```sh
lsl install aikit
lsl list | grep aikit                # aikit  user  ~/.local/lib/lsl/stdlib/aikit.lsl
```

All other libraries used here are bundled with LSL (`httpserver`, `requests`,
`http`, `json`, `env`, `fd`, `time`) or live in `backend/lsl/`.

## 3. Get the code and run

```sh
git clone https://github.com/luxopes/Hackathon_LuxAI.git
cd Hackathon_LuxAI
scripts/run-local.sh
```

The script

1. generates a local configuration (tokens, `market.json`, seller and console
   environment) under `~/.local/state/proofpay-local/etc`,
2. compiles the two LSL binaries with `lsl compile`,
3. starts the marketplace, five sellers and the console,
4. waits for all health endpoints and prints the URLs.

Result:

| What | URL |
|---|---|
| Agent console (frontend + chat/tools/payments API) | <http://127.0.0.1:3069/> |
| Marketplace public dashboard | <http://127.0.0.1:3070/> |

Try in the console chat: `Please audit the marketplace demo cart.` — the agent
buys the cheapest audit, refuses the incomplete delivery, gets a refund and
re-purchases from another seller. Logs are in `~/.local/state/proofpay-local/logs`;
stop everything with `scripts/stop-local.sh`.

### The LuxAI key

Chat and the general services (research, Python code, summary, translation,
ideas) call the **LuxAI Flash** model. Put your key in a file and point the
setup at it:

```sh
LUXAI_KEY_FILE=/path/to/luxai-key scripts/run-local.sh
```

Default path: `~/.config/lux-runner/api-key`. Without a key the stack still
runs and the cart-audit demo (HTTP checks only) can be exercised by calling the
marketplace API directly; chat turns will fail.

## 4. Manual setup (what the script does)

```sh
# configuration + tokens
LUXAI_KEY_FILE=$HOME/.config/lux-runner/api-key python3 scripts/local_config.py

# build
lsl compile backend/lsl/seller.lsl      ~/.local/state/proofpay-local/build/seller
lsl compile backend/lsl/chat_server.lsl ~/.local/state/proofpay-local/build/web-server

# run (one terminal each, or use nohup as the script does)
python3 backend/python/marketplace.py --config ~/.local/state/proofpay-local/etc/market.json
set -a; . ~/.local/state/proofpay-local/etc/seller-scout.env; set +a
  ~/.local/state/proofpay-local/build/seller        # repeat per seller env
PROOFPAY_CHAT_CONFIG=~/.local/state/proofpay-local/etc/chat.env \
  ~/.local/state/proofpay-local/build/web-server
```

Ports: marketplace 3070, sellers 3081–3085
(partial, complete, scout, insight, atlas), console 3069.

## 5. Smoke test

```sh
curl -s http://127.0.0.1:3070/health
curl -s http://127.0.0.1:3069/api/health
curl -s http://127.0.0.1:3070/api/offers | head -c 300
curl -s http://127.0.0.1:3069/ | grep '<title>'

# one agent turn (needs the LuxAI key)
curl -s -X POST http://127.0.0.1:3069/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is on offer right now and at what prices?"}'
sleep 30
curl -s 'http://127.0.0.1:3069/api/state?t=1' | head -c 400
```

The public dashboard shows the ledger invariant — `issued == accounted` must
stay true after every purchase.

## 5b. Optional: speech (ElevenLabs)

Put an ElevenLabs API key into a file:

```sh
mkdir -p ~/.config/elevenlabs
printf '%s' 'sk_…' > ~/.config/elevenlabs/key
chmod 600 ~/.config/elevenlabs/key
```

`scripts/run-local.sh` then starts the speech sidecar (`backend/python/tts.py`,
port 3071) and the 🔊 buttons on agent messages work. Without the key the
sidecar is skipped and the button reports `503 Text-to-speech is not
configured`. The key is never exposed to the browser; only message indexes are
accepted by the service and rendered audio is cached on disk.

## 6. Frontend

`frontend/` contains the reference web client (`index.html`, `app.js`,
`style.css`). The console service serves it directly because
`PROOFPAY_WEB_DIR` points at the repository's `frontend/` directory in the local
configuration. To build your own frontend, read `docs/API.md` — the console API
is a simple poller (`GET /api/state?lite=1` → full state on revision change,
`POST /api/chat` to send a message). No token is needed in the browser.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `lsl: command not found` | LSL not installed or `~/.local/bin` not in `PATH` (step 1). |
| `LuxAI key not found` warning | Expected without a key; export `LUXAI_KEY_FILE=…` and re-run. |
| Console starts but every chat turn errors | The LuxAI key file is missing/empty, or the marketplace is not reachable at `MARKET_URL`. |
| `Model nevrátil platné rozhodnutí` | Transient model/stream error — retry the message. |
| Port already in use | Another instance is running: `scripts/stop-local.sh` first. |
| `FileError: cannot open file` on start | Environment file paths are wrong — delete `~/.local/state/proofpay-local` and re-run the script. |
| Cross-origin fetch errors in a browser | The frontend must be served from the console origin; it sends no CORS headers. |
