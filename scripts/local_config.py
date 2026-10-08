#!/usr/bin/env python3
"""Generate a local development configuration for the ProofPay stack.

Creates tokens, market.json, seller environment files and the console
environment under PROOFPAY_LOCAL_STATE (default ~/.local/state/proofpay-local).
Idempotent: existing tokens and seller environment files are kept.
"""
import json
import os
import subprocess
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "python"))
from services import PROVIDERS, provider_offers  # noqa: E402

STATE = Path(os.environ.get("PROOFPAY_LOCAL_STATE", Path.home() / ".local/state/proofpay-local"))
CFG = STATE / "etc"
DATA = STATE / "data"
WEB = STATE / "data/web"
MARKET_URL = "http://127.0.0.1:3070"


def token(name):
    path = CFG / (name + ".token")
    if not path.exists():
        path.write_text(secrets.token_hex(32) + "\n")
    os.chmod(path, 0o600)
    return path


def write_env(path, lines):
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def main():
    CFG.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    client = token("client")
    key_file = os.environ.get("LUXAI_KEY_FILE", str(Path.home() / ".config/lux-runner/api-key"))

    sellers = []
    for name, price, port, label in [("partial", 3, 3081, "QuickCheck"), ("complete", 7, 3082, "ThoroughCheck")]:
        tok = token(name)
        sellers.append({"id": name, "offer_id": "offer-" + name, "name": label, "price": price,
                        "url": f"http://127.0.0.1:{port}", "token_file": str(tok)})
        write_env(CFG / f"seller-{name}.env", [
            f"SELLER_ID={name}",
            f"SELLER_MODE={name}",
            f"SELLER_PORT={port}",
            f"SELLER_TOKEN_FILE={tok}",
            f"MARKET_INTERNAL_URL={MARKET_URL}",
        ])
    for provider in PROVIDERS:
        name, port = provider["id"], provider["port"]
        tok = token(name)
        sellers.append({"id": name, "name": provider["name"], "url": f"http://127.0.0.1:{port}",
                        "token_file": str(tok), "offers": provider_offers(provider)})
        write_env(CFG / f"seller-{name}.env", [
            f"SELLER_ID={name}",
            "SELLER_MODE=general",
            f"SELLER_PORT={port}",
            f"SELLER_TOKEN_FILE={tok}",
            f"MARKET_INTERNAL_URL={MARKET_URL}",
            f"LUXAI_KEY_FILE={key_file}",
        ])

    market = {"port": 3070, "database": str(DATA / "market.db"),
              "client_token_file": str(client), "sellers": sellers}
    apify = os.environ.get("APIFY_TOKEN_FILE", str(Path.home() / ".config/apify/token"))
    if Path(apify).is_file():
        market["apify_token_file"] = apify
    # Podpisový klíč dokladů (Ed25519) – vytvoří se při prvním spuštění.
    signing = CFG / "receipt-signing.key"
    if not signing.is_file():
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(signing)],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(signing), "-pubout", "-out", str(signing) + ".pub"],
                       check=True, capture_output=True)
        os.chmod(signing, 0o600)
        os.chmod(str(signing) + ".pub", 0o600)
    market["receipt_signing_key_file"] = str(signing)
    # Locally the console has its own port; the marketplace redirects web/ there
    # (in production Caddy serves the console under web/ on the same origin).
    market["console_url"] = "http://127.0.0.1:3069/"
    path = CFG / "market.json"
    path.write_text(json.dumps(market, indent=2) + "\n")
    os.chmod(path, 0o600)

    write_env(CFG / "chat.env", [
        f"MARKET_URL={MARKET_URL}",
        f"MARKET_TOKEN_FILE={client}",
        f"LUXAI_KEY_FILE={key_file}",
        f"PROOFPAY_RESULT_FILE={WEB / 'latest-run.json'}",
        "PROOFPAY_BUDGET=30",
        "PROOFPAY_FIXTURE=buggy",
        "PROOFPAY_LANG=en",
        "PROOFPAY_WEB_PORT=3069",
        f"PROOFPAY_WEB_DIR={ROOT / 'frontend'}",
        # Accounts must persist locally too (the default is the server path /var/lib/proofpay-mvp).
        f"PROOFPAY_USERS_FILE={DATA / 'users.json'}",
    ])
    print(f"Local configuration: {CFG}")
    if not Path(key_file).is_file():
        print(f"WARNING: LuxAI key not found at {key_file}.")
        print("         Cart-audit demos work offline; chat and other services need the key.")
        print("         Re-run with LUXAI_KEY_FILE=/path/to/key to point at your key.")


if __name__ == "__main__":
    main()
