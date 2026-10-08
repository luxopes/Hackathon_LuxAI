#!/usr/bin/env python3
"""Offline verifier for ProofPay payment receipts.

Rebuilds the signed settlement payload from the receipt fields, checks the
SHA-256 digest and verifies the Ed25519 signature with `openssl` against the
public key published by the marketplace (or the one embedded in the receipt).

Usage:
  verify_receipt.py receipt.json
  verify_receipt.py --url https://hackathon.lux-ai.cz/api/receipt/job-…
  verify_receipt.py receipt.json --keys proofpay-keys.json

Exit code 0 means: the receipt is internally consistent and correctly signed
by the holder of the published key. It does not judge the quality of the
delivery (that is what the verification section describes).
"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen


def load_source(source):
    if source.startswith("http://") or source.startswith("https://"):
        with urlopen(source, timeout=20) as response:
            return json.loads(response.read(4_000_001))
    return json.loads(Path(source).read_text())


def settlement_payload(receipt):
    job, payment = receipt["job"], receipt["payment"]
    settled = [t for t in payment["transactions"]
               if t["action"] in ("PAYMENT_RELEASED", "REFUND_AUTHORIZED_BY_CONTRACT")]
    return {"payload_version": "1.0", "job_id": job["id"], "session_id": job["session_id"],
            "seller_id": job["seller_id"], "offer_id": job["offer_id"], "amount": job["price"],
            "currency": receipt["currency"], "state": job["state"],
            "idempotency_key": job["idempotency_key"],
            "contract_sha256": payment["contract_sha256"], "result_sha256": payment["result_sha256"],
            "settled_at": settled[0]["created_at"] if settled else None,
            "transactions": [{"id": t["id"], "action": t["action"], "amount": t["amount"],
                              "created_at": t["created_at"]} for t in payment["transactions"]],
            "verification_receipts": payment["verification_receipts"]}


def main():
    parser = argparse.ArgumentParser(description="Verify a ProofPay receipt")
    parser.add_argument("receipt", nargs="?", help="path to a receipt JSON or an http(s) URL")
    parser.add_argument("--url", help="fetch the receipt from this URL instead")
    parser.add_argument("--keys", help="key file or URL with {'keys': [{'id', 'alg', 'public_key_pem'}]}")
    arguments = parser.parse_args()
    source = arguments.url or arguments.receipt
    if not source:
        parser.error("provide a receipt path, an http(s) URL or --url")
    receipt = load_source(source)
    signature = receipt.get("signature")
    if not signature:
        print("FAIL: this receipt has no signature (created before signing was enabled)")
        return 1
    canonical = json.dumps(settlement_payload(receipt), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if canonical != signature["payload"]:
        print("FAIL: receipt fields do not match the signed payload (tampered data)")
        return 1
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if digest != signature["payload_sha256"]:
        print("FAIL: payload digest mismatch")
        return 1
    public_pem = None
    embedded = receipt.get("public_key") or {}
    if embedded.get("public_key_pem") and embedded.get("id") == signature["key_id"]:
        public_pem = embedded["public_key_pem"]
    if arguments.keys:
        keys = load_source(arguments.keys)
        for key in keys.get("keys", []):
            if key.get("id") == signature["key_id"]:
                public_pem = key["public_key_pem"]
        if public_pem is None:
            print(f"FAIL: key {signature['key_id']} not found in {arguments.keys}")
            return 1
    if public_pem is None:
        print("FAIL: no public key available (use --keys or a receipt that embeds it)")
        return 1
    with tempfile.TemporaryDirectory() as workdir:
        payload_file = Path(workdir) / "payload.json"
        signature_file = Path(workdir) / "signature.bin"
        key_file = Path(workdir) / "public.pem"
        payload_file.write_bytes(canonical.encode())
        signature_file.write_bytes(base64.b64decode(signature["value_base64"]))
        key_file.write_text(public_pem + "\n")
        # Ed25519: pkeyutl -rawin (pure EdDSA, no separate digest).
        result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key_file),
                                 "-rawin", "-in", str(payload_file), "-sigfile", str(signature_file)],
                                capture_output=True, text=True)
    if result.returncode != 0 or "Success" not in (result.stdout or ""):
        print("FAIL: signature does not verify:", result.stdout.strip() or result.stderr.strip())
        return 1
    job = receipt["job"]
    print("OK: receipt verified")
    print(f"  job            {job['id']}")
    print(f"  state          {job['state']} · {job['price']} {receipt['currency']} · seller {job['seller_id']}")
    print(f"  payload SHA256 {signature['payload_sha256']}")
    print(f"  key            {signature['key_id']} ({signature['alg']})")
    print(f"  signed at      {signature['created_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
