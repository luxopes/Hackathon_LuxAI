"""Nasazení odděleného MVP; spouští se jako root na cílovém serveru po přenosu souborů."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import time
from urllib.request import urlopen
from services import PROVIDERS, provider_offers


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def install():
    if os.geteuid() != 0:
        raise SystemExit("Run on the target server as root")
    os.umask(0o077)
    app = Path(__file__).resolve().parent
    try:
        account = pwd.getpwnam("proofpay")
    except KeyError:
        command("useradd", "--system", "--home-dir", "/var/lib/proofpay-mvp", "--shell", "/usr/sbin/nologin", "proofpay")
        account = pwd.getpwnam("proofpay")
    config_dir = Path("/etc/proofpay-mvp")
    config_dir.mkdir(exist_ok=True, mode=0o750)
    os.chmod(config_dir, 0o750)
    os.chown(config_dir, 0, account.pw_gid)
    state = Path("/var/lib/proofpay-mvp")
    state.mkdir(exist_ok=True, mode=0o700)
    os.chown(state, account.pw_uid, account.pw_gid)
    provider_ids = [p["id"] for p in PROVIDERS]
    for name in ["client", "partial", "complete", *provider_ids]:
        token = config_dir / (name + ".token")
        if not token.exists():
            token.write_text(secrets.token_hex(32) + "\n")
        os.chmod(token, 0o640)
        os.chown(token, 0, account.pw_gid)
    sellers = []
    for name, price, port, label in [("partial", 3, 3081, "QuickCheck"), ("complete", 7, 3082, "ThoroughCheck")]:
        sellers.append({"id": name, "offer_id": "offer-" + name, "name": label, "price": price,
                        "url": f"http://127.0.0.1:{port}", "token_file": str(config_dir / (name + ".token"))})
        env_file = config_dir / ("seller-" + name + ".env")
        if not env_file.exists():
            env_file.write_text(f"SELLER_ID={name}\nSELLER_MODE={name}\nSELLER_PORT={port}\nSELLER_TOKEN_FILE={config_dir}/{name}.token\nMARKET_INTERNAL_URL=http://127.0.0.1:3070\n")
        os.chmod(env_file, 0o640)
        os.chown(env_file, 0, account.pw_gid)
    for provider in PROVIDERS:
        name, port = provider["id"], provider["port"]
        sellers.append({"id": name, "name": provider["name"], "url": f"http://127.0.0.1:{port}",
                        "token_file": str(config_dir / (name + ".token")), "offers": provider_offers(provider)})
        env_file = config_dir / ("seller-" + name + ".env")
        expected = f"SELLER_ID={name}\nSELLER_MODE=general\nSELLER_PORT={port}\nSELLER_TOKEN_FILE={config_dir}/{name}.token\nMARKET_INTERNAL_URL=http://127.0.0.1:3070\nLUXAI_KEY_FILE={config_dir}/luxai.key\n"
        if env_file.exists() and env_file.read_text() != expected:
            raise SystemExit("Existing provider environment differs: " + name)
        env_file.write_text(expected)
        os.chmod(env_file, 0o640)
        os.chown(env_file, 0, account.pw_gid)
    key_file = config_dir / "luxai.key"
    if not key_file.is_file():
        raise SystemExit("Install LuxAI key into /etc/proofpay-mvp/luxai.key before deployment")
    os.chmod(key_file, 0o640)
    os.chown(key_file, 0, account.pw_gid)
    config_file = config_dir / "market.json"
    if not config_file.exists():
        config_file.write_text(json.dumps({"port": 3070, "database": str(state / "market.db"),
                                          "client_token_file": str(config_dir / "client.token"), "sellers": sellers}, indent=2) + "\n")
    else:
        config = json.loads(config_file.read_text())
        known = {seller["id"]: seller for seller in config["sellers"]}
        for seller in sellers:
            if seller["id"] not in known:
                config["sellers"].append(seller)
            elif known[seller["id"]]["url"] != seller["url"] or known[seller["id"]]["token_file"] != seller["token_file"]:
                raise SystemExit("Existing provider configuration differs: " + seller["id"])
        config_file.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(config_file, 0o640)
    os.chown(config_file, 0, account.pw_gid)
    shared = """User=proofpay
Group=proofpay
WorkingDirectory=/opt/proofpay-mvp
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/proofpay-mvp
UMask=0077
CPUQuota=50%
TasksMax=64
"""
    units = {
        "proofpay-market.service": "[Unit]\nDescription=ProofPay MVP marketplace (simulated credits)\nAfter=network.target\n\n[Service]\n" + shared + "MemoryMax=128M\nExecStart=/usr/bin/python3 /opt/proofpay-mvp/marketplace.py --config /etc/proofpay-mvp/market.json\n\n[Install]\nWantedBy=multi-user.target\n",
        "proofpay-seller@.service": "[Unit]\nDescription=ProofPay LSL seller %i\nAfter=network.target proofpay-market.service\n\n[Service]\n" + shared + "MemoryMax=96M\nEnvironmentFile=/etc/proofpay-mvp/seller-%i.env\nExecStart=/opt/proofpay-mvp/seller\n\n[Install]\nWantedBy=multi-user.target\n",
    }
    for name, body in units.items():
        target = Path("/etc/systemd/system") / name
        if target.exists() and target.read_text() != body:
            raise SystemExit("Existing unit differs; inspect before replacing: " + name)
        target.write_text(body)
        os.chmod(target, 0o644)
    command("systemctl", "daemon-reload")
    active_units = ["proofpay-market.service"] + ["proofpay-seller@" + name + ".service" for name in ["partial", "complete", *provider_ids]]
    for unit in active_units:
        if subprocess.run(["systemctl", "is-failed", "--quiet", unit]).returncode == 0:
            command("systemctl", "reset-failed", unit)
    command("systemctl", "enable", "--now", *active_units)
    command("systemctl", "restart", *active_units)
    for port in [3070, 3081, 3082, *[p["port"] for p in PROVIDERS]]:
        for _ in range(50):
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit(f"Service on port {port} did not become ready")
    caddy_file = Path("/etc/caddy/Caddyfile")
    original = caddy_file.read_text()
    if "handle_path /hackathon01/*" not in original:
        anchor = "api.lux-ai.cz {\n"
        if original.count(anchor) != 1:
            raise SystemExit("Caddy hostname anchor is missing or ambiguous; services are running locally")
        addition = "\tredir /hackathon01 /hackathon01/ 308\n\thandle_path /hackathon01/* {\n\t\treverse_proxy 127.0.0.1:3070\n\t}\n"
        candidate = original.replace(anchor, anchor + addition, 1)
        temporary = caddy_file.with_name("Caddyfile.proofpay-check")
        temporary.write_text(candidate)
        os.chmod(temporary, 0o640)
        command("caddy", "validate", "--config", str(temporary), "--adapter", "caddyfile")
        backups = Path("/var/backups/proofpay-mvp")
        backups.mkdir(exist_ok=True, mode=0o700)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backups / ("Caddyfile-" + timestamp)
        shutil.copy2(caddy_file, backup)
        os.chmod(backup, 0o600)
        caddy_file.write_text(candidate)
        try:
            command("systemctl", "reload", "caddy")
        except Exception:
            shutil.copy2(backup, caddy_file)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        (backups / ("deployment-" + timestamp + ".json")).write_text(json.dumps({"previous_caddy_sha256": hashlib.sha256(original.encode()).hexdigest(), "current_caddy_sha256": hashlib.sha256(candidate.encode()).hexdigest(), "added_path": "/hackathon01/"}) + "\n")
    print("Deployed: https://api.lux-ai.cz/hackathon01/")
    print("Payments: simulated Lux Coins; buyer and sellers: native LSL")


if __name__ == "__main__":
    install()
