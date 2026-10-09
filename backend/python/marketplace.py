"""The marketplace: ledger, wallets, escrow, jobs, verification and receipts.

It is the single writer of money in the product and the market every agent
trades on. Wallets, escrow, settlements, refunds and the append-only hash chain
live in one SQLite database, so each purchase is one transaction; the invariant
issued == accounted is checked on every page. It also serves the public pages
(dashboard, payments, receipts, docs, ledger export), verifies deliveries
against the agreed contract, signs settled receipts with Ed25519 and accepts
card top-ups through Stripe in test mode. Why Python: the transactional money
math, not the agent logic — every agent runs in LSL."""
from __future__ import annotations

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlencode, quote

import ui

from services import CURRENCY, SERVICES, validate_artifact


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Market:
    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.previews = {}
        self.client_token = Path(config["client_token_file"]).read_text().strip()
        apify_file = config.get("apify_token_file", "")
        self.apify_token = Path(apify_file).read_text().strip() if apify_file and Path(apify_file).is_file() else ""
        # Podpisový klíč pro doklady (Ed25519); bez něj se doklady jen nepodepisují.
        stripe_file = config.get("stripe_key_file", "")
        self.stripe_key = Path(stripe_file).read_text().strip() if stripe_file and Path(stripe_file).is_file() else ""
        # Connect: výplaty prodejcům přes Stripe (zapne se účtem v stripe_accounts).
        self.connect_enabled = bool(config.get("stripe_connect_enabled", False))
        self.connect_currency = config.get("stripe_connect_currency", "usd")
        self.stripe_success_url = config.get("stripe_success_url", "https://hackathon.lux-ai.cz/web/?stripe=ok")
        self.stripe_cancel_url = config.get("stripe_cancel_url", "https://hackathon.lux-ai.cz/web/?stripe=cancel")
        key_file = config.get("receipt_signing_key_file", "")
        self.signing_key_file = key_file
        self.signing_key_id = ""
        self.signing_public_pem = ""
        if key_file and Path(key_file).is_file() and Path(key_file + ".pub").is_file():
            self.signing_public_pem = Path(key_file + ".pub").read_text().strip()
            self.signing_key_id = "ppk-" + hashlib.sha256(self.signing_public_pem.encode()).hexdigest()[:16]
        self.sellers = {s["id"]: {**s, "token": Path(s["token_file"]).read_text().strip()} for s in config["sellers"]}
        if len(self.client_token) < 32 or any(len(s["token"]) < 32 for s in self.sellers.values()):
            raise ValueError("Tokens must have at least 32 characters")
        Path(config["database"]).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    available INTEGER NOT NULL CHECK(available >= 0), locked INTEGER NOT NULL CHECK(locked >= 0));
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL, budget INTEGER NOT NULL,
                    fixture TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, name TEXT NOT NULL,
                    price INTEGER NOT NULL CHECK(price > 0), active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    offer_id TEXT NOT NULL REFERENCES offers(id), seller_id TEXT NOT NULL, price INTEGER NOT NULL,
                    state TEXT NOT NULL, contract TEXT NOT NULL, result TEXT, failure TEXT,
                    idempotency_key TEXT NOT NULL, created REAL NOT NULL, UNIQUE(session_id, idempotency_key));
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                    case_id TEXT NOT NULL, total_cents INTEGER NOT NULL, UNIQUE(job_id, case_id));
                CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, job_id TEXT,
                    action TEXT NOT NULL, amount INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS service_receipts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
                    artifact_sha256 TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS research_sources(job_id TEXT NOT NULL REFERENCES jobs(id), url TEXT NOT NULL,
                    title TEXT NOT NULL, extract TEXT NOT NULL, PRIMARY KEY(job_id,url));
                CREATE TABLE IF NOT EXISTS stripe_payments(id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    lux_coins INTEGER NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, credited REAL);
                CREATE TABLE IF NOT EXISTS stripe_accounts(seller_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS stripe_transfers(job_id TEXT PRIMARY KEY, seller_id TEXT NOT NULL,
                    transfer_id TEXT, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
                    status TEXT NOT NULL, detail TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS signatures(job_id TEXT PRIMARY KEY, alg TEXT NOT NULL,
                    public_key_id TEXT NOT NULL, signature TEXT NOT NULL, payload TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, created REAL NOT NULL);
            """)
            # Doplnění sloupců zachovává zůstatky, původní zakázky i jejich kontrakty.
            columns = {r[1] for r in db.execute("PRAGMA table_info(offers)")}
            for name, definition in [("capability", "TEXT NOT NULL DEFAULT 'http-cart-audit'"),
                                     ("delivery", "TEXT NOT NULL DEFAULT 'Tři HTTP kontroly s doklady provedení'"),
                                     ("tier", "TEXT NOT NULL DEFAULT 'compact'")]:
                if name not in columns:
                    db.execute("ALTER TABLE offers ADD COLUMN " + name + " " + definition)
            if "service" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN service TEXT NOT NULL DEFAULT 'auto'")
            for seller in self.sellers.values():
                db.execute("INSERT OR IGNORE INTO wallets VALUES(?, 'seller', 0, 0)", (seller["id"],))
                offers = seller.get("offers", [{"id": seller.get("offer_id"), "name": seller.get("name"),
                    "price": seller.get("price"), "capability": "http-cart-audit", "tier": "compact"}])
                for offer in offers:
                    capability = offer["capability"]
                    if capability not in SERVICES or type(offer["price"]) is not int or not 1 <= offer["price"] <= 100:
                        raise ValueError("Invalid configured offer")
                    db.execute("INSERT OR IGNORE INTO offers(id,seller_id,name,price,active,capability,delivery,tier) VALUES(?,?,?,?,1,?,?,?)",
                               (offer["id"], seller["id"], offer["name"], offer["price"], capability,
                                offer.get("delivery", SERVICES[capability]["delivery"]), offer.get("tier", "compact")))
            # Přerušené síťové volání po restartu nesmí trvale zablokovat úschovu.
            for job in db.execute("SELECT id, session_id FROM jobs WHERE state = 'RUNNING'").fetchall():
                db.execute("UPDATE jobs SET state = 'FAILED', failure = 'Execution interrupted by restart' WHERE id = ?", (job["id"],))
                self.record(db, job["session_id"], job["id"], "EXECUTION_INTERRUPTED", 0)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.config["database"], timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def require_client(self, token):
        if not hmac.compare_digest(token, self.client_token):
            raise Problem(401, "unauthorized")

    def seller_for_token(self, token):
        for seller in self.sellers.values():
            if hmac.compare_digest(token, seller["token"]):
                return seller
        raise Problem(401, "unauthorized seller")

    def offers(self):
        with self.db() as db:
            return [{**dict(row), "currency": CURRENCY, "service_name": SERVICES[row["capability"]]["name"]}
                    for row in db.execute("SELECT id,seller_id,name,price,capability,delivery,tier FROM offers WHERE active = 1 ORDER BY price,id")]

    def session(self, session_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                raise Problem(404, "session not found")
            wallet = dict(db.execute("SELECT * FROM wallets WHERE id = ?", (session_id,)).fetchone())
            jobs = [self.job_dict(j) for j in db.execute("SELECT * FROM jobs WHERE session_id = ? ORDER BY created", (session_id,))]
            ledger = [dict(j) for j in db.execute("SELECT * FROM ledger WHERE session_id = ? ORDER BY id", (session_id,))]
            return {**dict(row), "wallet": wallet, "jobs": jobs, "ledger": ledger, "currency": CURRENCY, "simulated_payments": True}

    @staticmethod
    def job_dict(row):
        result = dict(row)
        result["contract"] = json.loads(result["contract"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        if result["result"]:
            result["result_sha256"] = hashlib.sha256(encoded(result["result"]).encode()).hexdigest()
        return result

    def job(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise Problem(404, "job not found")
            return self.job_dict(row)

    def payment(self, job_id):
        # ID transakcí vychází z uloženého ledgeru; čtení nikdy nevytváří platbu.
        with self.db() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise Problem(404, "job not found")
            job = self.job_dict(row)
            transactions = []
            for entry in db.execute("SELECT * FROM ledger WHERE job_id = ? AND action IN "
                                    "('ESCROW_LOCKED','PAYMENT_RELEASED','REFUND_AUTHORIZED_BY_CONTRACT') ORDER BY id", (job_id,)):
                released = entry["action"] == "PAYMENT_RELEASED"
                locked = entry["action"] == "ESCROW_LOCKED"
                transactions.append({"id": "lux-tx-" + str(entry["id"]), "ledger_id": entry["id"],
                    "action": entry["action"], "amount": entry["amount"], "currency": CURRENCY,
                    "created": entry["created"], "created_at": datetime.fromtimestamp(entry["created"], timezone.utc).isoformat(),
                    "from_wallet": job["session_id"],
                    "from_account": "available" if locked else "locked",
                    "to_wallet": job["seller_id"] if released else job["session_id"],
                    "to_account": "locked" if locked else "available",
                    "job_id": job_id, "session_id": job["session_id"]})
            receipts = [r[0] for r in db.execute("SELECT id FROM receipts WHERE job_id = ? ORDER BY id", (job_id,))]
            receipts += [r[0] for r in db.execute("SELECT id FROM service_receipts WHERE job_id = ?", (job_id,))]
            return {"job_id": job_id, "session_id": job["session_id"], "offer_id": job["offer_id"],
                "seller_id": job["seller_id"], "amount": job["price"], "currency": CURRENCY,
                "state": job["state"], "idempotency_key": job["idempotency_key"],
                "transactions": transactions, "verification_receipts": receipts,
                "result_sha256": job.get("result_sha256", ""),
                "contract_sha256": hashlib.sha256(encoded(job["contract"]).encode()).hexdigest(),
                "simulated_payments": True, "ledger": "Central marketplace SQLite ledger; IDs are scoped to this marketplace"}

    @staticmethod
    def record(db, session_id, job_id, action, amount):
        db.execute("INSERT INTO ledger(session_id,job_id,action,amount,created) VALUES(?,?,?,?,?)",
                   (session_id, job_id, action, amount, time.time()))

    def create_session(self, payload):
        budget = payload.get("budget", 20)
        title = payload.get("title", "Cart audit")
        fixture = payload.get("fixture", "buggy")
        service = payload.get("service", "auto")
        if type(budget) is not int or not 1 <= budget <= 100:
            raise Problem(400, "budget must be an integer from 1 to 100 USD")
        if type(title) is not str or not 1 <= len(title.strip()) <= 1000 or fixture not in ["healthy", "buggy"] or service not in ["auto", *SERVICES]:
            raise Problem(400, "invalid session parameters")
        session_id = "session-" + secrets.token_hex(8)
        with self.transaction() as db:
            db.execute("INSERT INTO sessions(id,title,budget,fixture,created,service) VALUES(?,?,?,?,?,?)", (session_id, title, budget, fixture, time.time(), service))
            db.execute("INSERT INTO wallets VALUES(?, 'buyer', ?, 0)", (session_id, budget))
            self.record(db, session_id, None, "LUX_COINS_ISSUED", budget)
        return self.session(session_id)

    @staticmethod
    def contract(capability="http-cart-audit", task="", tier="compact"):
        if capability != "http-cart-audit":
            return {"capability": capability, "currency": CURRENCY, "task": task, "tier": tier,
                    "delivery": SERVICES[capability]["delivery"], "verification": "Structure, syntax and seller attestation; no general semantic guarantee"}
        # Ceny a množství se mění mezi zakázkami; pracovník musí test opravdu provést.
        unit = 100 + secrets.randbelow(900)
        quantity = 2 + secrets.randbelow(4)
        return {"capability": "http-cart-audit", "currency": CURRENCY, "cases": [
            {"id": "single_item", "unit_cents": unit, "quantity": 1, "expected_cents": unit},
            {"id": "quantity_update", "unit_cents": unit, "quantity": quantity, "expected_cents": unit * quantity},
            {"id": "empty_cart", "unit_cents": unit, "quantity": 0, "expected_cents": 0},
        ]}

    def buy(self, payload):
        session_id, offer_id, key = (payload.get(x) for x in ["session_id", "offer_id", "idempotency_key"])
        if any(type(x) is not str or not 1 <= len(x) <= 100 for x in [session_id, offer_id, key]):
            raise Problem(400, "session, offer and idempotency key are required")
        task = payload.get("task")
        if "task" in payload and (type(task) is not str or not 1 <= len(task.strip()) <= 1000):
            raise Problem(400, "task must have 1 to 1000 characters")
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM jobs WHERE session_id = ? AND idempotency_key = ?", (session_id, key)).fetchone()
            if existing:
                if existing["offer_id"] != offer_id:
                    raise Problem(409, "idempotency key already belongs to another offer")
                if task is not None and json.loads(existing["contract"]).get("task") != task:
                    raise Problem(409, "idempotency key already belongs to another task")
                return self.job_dict(existing)
            session = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            offer = db.execute("SELECT * FROM offers WHERE id = ? AND active = 1", (offer_id,)).fetchone()
            if not session or not offer:
                raise Problem(404, "session or active offer not found")
            if session["service"] != "auto" and offer["capability"] != session["service"]:
                raise Problem(409, "offer does not provide the requested service")
            wallet = db.execute("SELECT * FROM wallets WHERE id = ?", (session_id,)).fetchone()
            if wallet["available"] < offer["price"]:
                raise Problem(409, "insufficient available USD")
            job_id = "job-" + secrets.token_hex(8)
            # Každá zpráva chatu má vlastní neměnné zadání, peněženka zůstává společná.
            contract = self.contract(offer["capability"], task if task is not None else session["title"], offer["tier"])
            contract["task"] = task if task is not None else session["title"]
            db.execute("UPDATE wallets SET available = available - ?, locked = locked + ? WHERE id = ?",
                       (offer["price"], offer["price"], session_id))
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,'FUNDED',?,NULL,NULL,?,?)",
                       (job_id, session_id, offer_id, offer["seller_id"], offer["price"],
                        encoded(contract), key, time.time()))
            self.record(db, session_id, job_id, "ESCROW_LOCKED", offer["price"])
        return self.job(job_id)

    def execute(self, job_id):
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job:
                raise Problem(404, "job not found")
            if job["state"] in ["DELIVERED", "PAID", "REFUNDED"]:
                return self.job_dict(job)
            if job["state"] != "FUNDED":
                raise Problem(409, "job is already executing or execution failed")
            db.execute("UPDATE jobs SET state = 'RUNNING' WHERE id = ?", (job_id,))
            seller = self.sellers[job["seller_id"]]
            outgoing = self.job_dict(job)
        result, failure = None, None
        try:
            request = Request(seller["url"] + "/execute", data=encoded(outgoing).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + seller["token"]})
            with urlopen(request, timeout=120) as response:
                body = response.read(65537)
                if len(body) > 65536:
                    raise ValueError("delivery too large")
                result = json.loads(body)
                if type(result) is not dict:
                    raise ValueError("delivery must be an object")
        except (URLError, TimeoutError, OSError, ValueError) as error:
            if isinstance(error, HTTPError):
                error.close()
            failure = "Seller unavailable or invalid delivery"
        with self.transaction() as db:
            db.execute("UPDATE jobs SET state = ?, result = ?, failure = ? WHERE id = ?",
                       ("FAILED" if failure else "DELIVERED", encoded(result) if result else None, failure, job_id))
            self.record(db, job["session_id"], job_id, "DELIVERY_FAILED" if failure else "RESULT_DELIVERED", 0)
        return self.job(job_id)

    def sandbox(self, payload, token):
        seller = self.seller_for_token(token)
        job_id, case_id = payload.get("job_id"), payload.get("case_id")
        if type(job_id) is not str or type(case_id) is not str:
            raise Problem(400, "invalid sandbox request")
        with self.transaction() as db:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["seller_id"] != seller["id"]:
                raise Problem(403, "seller does not own this job")
            if job["state"] != "RUNNING":
                raise Problem(409, "sandbox execution requires a running funded job")
            contract = json.loads(job["contract"])
            if contract["capability"] != "http-cart-audit":
                raise Problem(409, "this job does not provide cart audit")
            test_case = next((c for c in contract["cases"] if c["id"] == case_id), None)
            if not test_case:
                raise Problem(400, "case outside agreed contract")
            existing = db.execute("SELECT * FROM receipts WHERE job_id = ? AND case_id = ?", (job_id, case_id)).fetchone()
            if existing:
                return {"receipt_id": existing["id"], "total_cents": existing["total_cents"]}
            fixture = db.execute("SELECT fixture FROM sessions WHERE id = ?", (job["session_id"],)).fetchone()[0]
            quantity = test_case["quantity"]
            # Ukázkový košík obsahuje skutečnou chybu přepočtu při zvýšení množství.
            total = test_case["unit_cents"] * (1 if fixture == "buggy" and quantity > 1 else quantity)
            receipt_id = "receipt-" + secrets.token_hex(12)
            db.execute("INSERT INTO receipts VALUES(?,?,?,?)", (receipt_id, job_id, case_id, total))
            return {"receipt_id": receipt_id, "total_cents": total}

    def verification(self, job_id):
        job = self.job(job_id)
        reasons = []
        result = job["result"]
        if not result:
            return {"valid_delivery": False, "reasons": ["No delivery"], "checks": []}
        capability = job["contract"]["capability"]
        if capability != "http-cart-audit":
            artifact = result.get("artifact")
            with self.db() as db:
                receipt = db.execute("SELECT * FROM service_receipts WHERE job_id=?", (job_id,)).fetchone()
                sources = [dict(row) for row in db.execute("SELECT * FROM research_sources WHERE job_id=?", (job_id,))]
            reasons = validate_artifact(capability, artifact, sources)
            if result.get("job_id") != job_id or result.get("seller_id") != job["seller_id"] or result.get("capability") != capability:
                reasons.append("Delivery identity or service mismatch")
            digest = hashlib.sha256(encoded(artifact).encode()).hexdigest()
            if not receipt or receipt["id"] != result.get("receipt_id") or receipt["artifact_sha256"] != digest:
                reasons.append("Missing or mismatched seller attestation")
            return {"valid_delivery": not reasons, "reasons": reasons, "checks": [], "capability": capability,
                    "scope": "Structure, Python syntax and cited source fetches; seller attestation is not proof of general semantic quality"}
        checks = result.get("checks")
        if type(checks) is not list or len(checks) > 10:
            return {"valid_delivery": False, "reasons": ["Invalid checks"], "checks": []}
        if result.get("job_id") != job_id or result.get("seller_id") != job["seller_id"]:
            reasons.append("Delivery identity mismatch")
        with self.db() as db:
            receipts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM receipts WHERE job_id = ?", (job_id,))}
        seen = set()
        for expected in job["contract"]["cases"]:
            matching = [c for c in checks if type(c) is dict and c.get("case_id") == expected["id"]]
            if len(matching) != 1:
                reasons.append("Missing or duplicate check: " + expected["id"])
                continue
            check = matching[0]
            seen.add(expected["id"])
            receipt = receipts.get(check.get("receipt_id")) if type(check.get("receipt_id")) is str else None
            if not receipt or receipt["case_id"] != expected["id"]:
                reasons.append("Missing execution receipt: " + expected["id"])
                continue
            if (type(check.get("expected_cents")) is not int or check["expected_cents"] != expected["expected_cents"]
                    or type(check.get("observed_cents")) is not int or check["observed_cents"] != receipt["total_cents"]
                    or type(check.get("passed")) is not bool
                    or check["passed"] != (receipt["total_cents"] == expected["expected_cents"])):
                reasons.append("Invalid observation: " + expected["id"])
        if len(checks) != len(seen):
            reasons.append("Unexpected checks")
        return {"valid_delivery": not reasons, "reasons": reasons, "checks": checks,
                "scope": "Completeness and receipts; finding a shop bug is a valid delivery"}

    def provider_job(self, db, job_id, token):
        if type(job_id) is not str or not 1 <= len(job_id) <= 100:
            raise Problem(400, "invalid job id")
        seller = self.seller_for_token(token)
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["seller_id"] != seller["id"]:
            raise Problem(403, "seller does not own this job")
        if row["state"] != "RUNNING":
            raise Problem(409, "provider operation requires a running job")
        return self.job_dict(row)

    def attest(self, payload, token):
        with self.transaction() as db:
            job = self.provider_job(db, payload.get("job_id"), token)
            if job["contract"]["capability"] == "http-cart-audit":
                raise Problem(409, "cart audit uses execution receipts")
            artifact = payload.get("artifact")
            if type(artifact) is not dict or len(encoded(artifact)) > 50000:
                raise Problem(400, "invalid artifact")
            digest = hashlib.sha256(encoded(artifact).encode()).hexdigest()
            receipt = db.execute("SELECT * FROM service_receipts WHERE job_id=?", (job["id"],)).fetchone()
            if receipt:
                if receipt["artifact_sha256"] != digest:
                    raise Problem(409, "a different delivery is already attested")
                return {"receipt_id": receipt["id"], "artifact_sha256": digest}
            receipt_id = "delivery-" + secrets.token_hex(12)
            db.execute("INSERT INTO service_receipts VALUES(?,?,?,?,?)", (receipt_id, job["id"], digest, "flash", time.time()))
            return {"receipt_id": receipt_id, "artifact_sha256": digest}

    def progress(self, payload, token):
        delta = payload.get("delta")
        if type(delta) is not str or not 1 <= len(delta) <= 2000:
            raise Problem(400, "invalid preview delta")
        with self.changed, self.db() as db:
            job = self.provider_job(db, payload.get("job_id"), token)
            preview = self.previews.setdefault(job["id"], "")
            if len(preview) + len(delta) > 60000:
                raise Problem(400, "preview limit exceeded")
            self.previews[job["id"]] = preview + delta
            # Náhledy jsou dočasné; nejsou dokladem provedení ani součástí platby.
            if len(self.previews) > 256:
                self.previews.pop(next(iter(self.previews)))
            self.changed.notify_all()
        return {"ok": True}

    def research(self, payload, token):
        query = payload.get("query")
        if type(query) is not str or not 1 <= len(query.strip()) <= 200:
            raise Problem(400, "research query must have 1 to 200 characters")
        with self.db() as db:
            job = self.provider_job(db, payload.get("job_id"), token)
            if job["contract"]["capability"] != "short-research":
                raise Problem(409, "this job does not provide research")
            existing = [dict(row) for row in db.execute("SELECT url,title,extract FROM research_sources WHERE job_id=?", (job["id"],))]
            if existing:
                return {"sources": existing, "scope": "Wikipedia article introductions"}
        sources = self.apify_research(query)
        scope = "Live web pages fetched with the Apify RAG Web Browser (query is data, not a URL)"
        if len(sources) < 2:
            # Záložní cesta udrží demo funkční i bez Apify kvóty nebo při výpadku.
            sources = self.wikipedia_research(query)
            scope = "Wikipedia article introductions (Apify fallback)"
        if len(sources) < 2:
            raise Problem(422, "research needs at least two matching sources; use a broader topic")
        with self.transaction() as db:
            self.provider_job(db, job["id"], token)
            for source in sources:
                db.execute("INSERT OR IGNORE INTO research_sources VALUES(?,?,?,?)",
                           (job["id"], source["url"], source["title"], source["extract"]))
        return {"sources": sources, "scope": scope}

    def apify_research(self, query):
        # Živé výsledky z webu přes Apify RAG Web Browser (vyhledá a stáhne obsah
        # stránek). Při chybě vrací prázdný seznam; volající použije záložní cestu.
        if not self.apify_token:
            return []
        params = urlencode({"maxTotalChargeUsd": "0.50"})
        request = Request("https://api.apify.com/v2/acts/apify~rag-web-browser/run-sync-get-dataset-items?" + params,
                          data=encoded({"query": query, "maxResults": 3}).encode(),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.apify_token})
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read(2000001)
                if len(raw) > 2000000:
                    raise ValueError("source response too large")
                items = json.loads(raw)
        except (URLError, OSError, ValueError):
            return []
        if type(items) is not list:
            return []
        sources = []
        seen = set()
        for item in items:
            if type(item) is not dict:
                continue
            metadata = item.get("metadata") or {}
            url = metadata.get("url") or metadata.get("canonicalUrl") or item.get("url")
            title = metadata.get("title") or (item.get("searchResult") or {}).get("title") or ""
            extract = item.get("markdown") or item.get("text") or ""
            if type(url) is not str or not url.startswith("https://") or type(title) is not str or not title.strip() or type(extract) is not str or not extract.strip():
                continue
            if url in seen:
                continue
            seen.add(url)
            sources.append({"title": title.strip()[:300], "url": url, "extract": extract.strip()[:1800]})
        return sources[:3]

    def wikipedia_research(self, query):
        # Pevný původ dotazu brání tomu, aby zadání vedlo k požadavkům na interní adresy.
        params = urlencode({"action": "query", "generator": "search", "gsrsearch": query,
                            "gsrlimit": 3, "prop": "extracts", "exintro": 1, "explaintext": 1,
                            "exchars": 900, "format": "json"})
        request = Request("https://en.wikipedia.org/w/api.php?" + params, headers={"User-Agent": "ProofPayResearchMVP/1.0"})
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(100001)
                if len(raw) > 100000:
                    raise ValueError("source response too large")
                data = json.loads(raw)
        except (URLError, OSError, ValueError):
            return []
        return [{"title": page["title"], "url": "https://en.wikipedia.org/wiki/" + quote(page["title"].replace(" ", "_")),
                 "extract": page.get("extract", "")[:1800]}
                for page in data.get("query", {}).get("pages", {}).values() if page.get("extract")]

    def settlement_payload(self, job_id):
        # Neměnný obsah, který se podepisuje: identita, částka, hashe a pohyby.
        job = self.job(job_id)
        payment = self.payment(job_id)
        settled = [t for t in payment["transactions"]
                   if t["action"] in ("PAYMENT_RELEASED", "REFUND_AUTHORIZED_BY_CONTRACT")]
        return {"payload_version": "1.0", "job_id": job["id"], "session_id": job["session_id"],
                "seller_id": job["seller_id"], "offer_id": job["offer_id"], "amount": job["price"],
                "currency": CURRENCY, "state": job["state"], "idempotency_key": job["idempotency_key"],
                "contract_sha256": payment["contract_sha256"], "result_sha256": payment["result_sha256"],
                "settled_at": settled[0]["created_at"] if settled else None,
                "transactions": [{"id": t["id"], "action": t["action"], "amount": t["amount"],
                                  "created_at": t["created_at"]} for t in payment["transactions"]],
                "verification_receipts": payment["verification_receipts"]}

    def sign_settlement(self, job_id):
        # Podpis nesmí nikdy zablokovat vypořádání; při chybě se jen neuloží.
        if not self.signing_key_file or not Path(self.signing_key_file).is_file():
            return
        try:
            payload = encoded(self.settlement_payload(job_id)).encode()
            with tempfile.TemporaryDirectory() as workdir:
                payload_file = Path(workdir) / "payload.json"
                signature_file = Path(workdir) / "signature.bin"
                payload_file.write_bytes(payload)
                # Ed25519 podepisuje data přímo (raw), ne digest – proto pkeyutl -rawin.
                subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", self.signing_key_file,
                                "-rawin", "-in", str(payload_file), "-out", str(signature_file)],
                               capture_output=True, check=True, timeout=20)
                signature = signature_file.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            with self.transaction() as db:
                db.execute("INSERT OR REPLACE INTO signatures(job_id, alg, public_key_id, signature, payload, payload_sha256, created)"
                           " VALUES(?,?,?,?,?,?,?)",
                           (job_id, "ed25519-sha256", self.signing_key_id,
                            base64.b64encode(signature).decode(), payload.decode(), digest, time.time()))
        except (OSError, subprocess.SubprocessError, sqlite3.Error):
            return

    # --- Stripe (test mode): karta dobije simulované USD; ledger zůstává jediné účetnictví.
    STRIPE_USD_TO_LC = 1   # 1 jednotka = 1 USD (simulovaně), karta dobíjí 1:1
    STRIPE_MIN_USD = 1
    STRIPE_MAX_USD = 25

    def stripe_request(self, method, path, params=None):
        if not self.stripe_key:
            raise Problem(503, "Stripe is not configured on this marketplace")
        data = urlencode(params or {}).encode()
        request = Request("https://api.stripe.com/v1/" + path, data=data if method == "POST" else None,
                          headers={"Authorization": "Bearer " + self.stripe_key,
                                   "Content-Type": "application/x-www-form-urlencoded"})
        if method == "GET" and params:
            request = Request("https://api.stripe.com/v1/" + path + "?" + urlencode(params),
                              headers={"Authorization": "Bearer " + self.stripe_key})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read(200_001))
        except HTTPError as error:
            detail = error.read(600).decode("utf-8", "replace")
            try:
                message = json.loads(detail).get("error", {}).get("message", detail)
            except ValueError:
                message = detail
            raise Problem(502, "Stripe: " + message[:200])
        except (URLError, OSError, ValueError):
            raise Problem(502, "Stripe is unavailable")

    CONNECT_VERSION = "2025-12-15.clover"

    def stripe_request_v2(self, method, path, payload):
        """Stripe API v2: JSON tělo a explicitní verze (Accounts v2)."""
        if not self.stripe_key:
            raise Problem(503, "Stripe is not configured on this marketplace")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            body_file = handle.name
        try:
            completed = subprocess.run([
                "curl", "-sS", "-m", "40", "-X", method,
                "-H", "Authorization: Bearer " + self.stripe_key,
                "-H", "Content-Type: application/json",
                "-H", "Stripe-Version: " + self.CONNECT_VERSION,
                "--data-binary", "@" + body_file, "https://api.stripe.com/v2/" + path,
            ], capture_output=True, text=True, timeout=60)
        finally:
            Path(body_file).unlink(missing_ok=True)
        try:
            answer = json.loads(completed.stdout or "{}")
        except ValueError:
            raise Problem(502, "Stripe v2 returned an unreadable answer")
        if answer.get("error"):
            raise Problem(502, "Stripe: " + str(answer["error"].get("message", ""))[:200])
        return answer

    def stripe_amount(self, amount_usd):
        if type(amount_usd) is not int or not self.STRIPE_MIN_USD <= amount_usd <= self.STRIPE_MAX_USD:
            raise Problem(400, f"card amount must be a whole number of US dollars from {self.STRIPE_MIN_USD} to {self.STRIPE_MAX_USD}")
        return amount_usd * 100, amount_usd * self.STRIPE_USD_TO_LC

    def stripe_checkout(self, session_id, amount_usd):
        cents, coins = self.stripe_amount(amount_usd)
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                raise Problem(404, "session not found")
        created = self.stripe_request("POST", "checkout/sessions", {
            "mode": "payment",
            "success_url": self.stripe_success_url + "&sc={CHECKOUT_SESSION_ID}",
            "cancel_url": self.stripe_cancel_url,
            "client_reference_id": session_id,
            "metadata[session_id]": session_id,
            "metadata[lux_coins]": str(coins),
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(cents),
            "line_items[0][price_data][product_data][name]": f"Wallet top-up 1:1 (Stripe test): ${coins:,}.00",
        })
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO stripe_payments VALUES(?,?,?,?,?,?,?,?,NULL)",
                       (created["id"], session_id, coins, cents, "usd", "checkout", created.get("status", "open"), time.time()))
        return {"stripe_session_id": created["id"], "checkout_url": created.get("url", ""), "lux_coins": coins, "mode": "stripe-test"}

    def stripe_sandbox_pay(self, session_id, amount_usd):
        # Serverová testovací platba (jen s testovacím klíčem) – pro ověření a jako záloha dema.
        if not self.stripe_key.startswith("sk_test_"):
            raise Problem(409, "sandbox payments are only available with a test key")
        cents, coins = self.stripe_amount(amount_usd)
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                raise Problem(404, "session not found")
        intent = self.stripe_request("POST", "payment_intents", {
            "amount": str(cents), "currency": "usd", "payment_method": "pm_card_visa", "confirm": "true",
            "automatic_payment_methods[enabled]": "true", "automatic_payment_methods[allow_redirects]": "never",
            "description": f"Wallet top-up 1:1 (Stripe test): ${coins:,}.00",
            "metadata[session_id]": session_id, "metadata[lux_coins]": str(coins),
        })
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO stripe_payments VALUES(?,?,?,?,?,?,?,?,NULL)",
                       (intent["id"], session_id, coins, cents, "usd", "payment_intent", intent.get("status", "unknown"), time.time()))
        return {"stripe_session_id": intent["id"], "lux_coins": coins, "status": intent.get("status"), "mode": "stripe-test"}

    def stripe_confirm(self, stripe_id):
        if type(stripe_id) is not str or not 1 <= len(stripe_id) <= 200:
            raise Problem(400, "invalid Stripe payment id")
        with self.db() as db:
            row = db.execute("SELECT * FROM stripe_payments WHERE id = ?", (stripe_id,)).fetchone()
        if not row:
            raise Problem(404, "this Stripe payment is not known here")
        if row["credited"]:
            return {"credited": False, "reason": "already credited", "lux_coins": row["lux_coins"], "session_id": row["session_id"]}
        if row["kind"] == "checkout":
            remote = self.stripe_request("GET", "checkout/sessions/" + stripe_id, None)
            paid = remote.get("payment_status") == "paid"
            reference = remote.get("payment_intent") or stripe_id
        else:
            remote = self.stripe_request("GET", "payment_intents/" + stripe_id, None)
            paid = remote.get("status") == "succeeded"
            reference = stripe_id
        if not paid:
            raise Problem(409, "this Stripe payment is not completed yet")
        with self.transaction() as db:
            fresh = db.execute("SELECT * FROM stripe_payments WHERE id = ?", (stripe_id,)).fetchone()
            if fresh["credited"]:
                return {"credited": False, "reason": "already credited", "lux_coins": fresh["lux_coins"], "session_id": fresh["session_id"]}
            session_id, coins = fresh["session_id"], fresh["lux_coins"]
            db.execute("UPDATE wallets SET available = available + ? WHERE id = ?", (coins, session_id))
            db.execute("UPDATE sessions SET budget = budget + ? WHERE id = ?", (coins, session_id))
            self.record(db, session_id, None, "STRIPE_TOPUP", coins)
            db.execute("UPDATE stripe_payments SET status = 'paid', credited = ? WHERE id = ?", (time.time(), stripe_id))
        return {"credited": True, "lux_coins": coins, "session_id": session_id, "stripe_reference": reference,
                "amount_cents": fresh["amount_cents"], "amount_usd": fresh["amount_cents"] // 100, "currency": "usd",
                "ratio": "1:1", "mode": "stripe-test", "payment": "card payment in Stripe test mode"}

    def connected_account(self, seller_id):
        with self.db() as db:
            row = db.execute("SELECT account_id FROM stripe_accounts WHERE seller_id = ?", (seller_id,)).fetchone()
        return row["account_id"] if row else ""

    def last_exchange_rate(self, currency):
        """Kurz z poslední skutečné karty: kolik měny platformy dal jeden dolar."""
        try:
            charges = self.stripe_request("GET", "charges?limit=5", None)
        except Problem:
            return None, ""
        for charge in charges.get("data", []):
            if charge.get("currency") != currency or not charge.get("paid") or not charge.get("balance_transaction"):
                continue
            try:
                balance = self.stripe_request("GET", "balance_transactions/" + charge["balance_transaction"], None)
            except Problem:
                continue
            net, amount = balance.get("amount"), charge.get("amount")
            if net and amount:
                return float(net) / float(amount), balance.get("currency", "")
        return None, ""

    def connect_balance(self, currency):
        # Převod lze vytvořit jen z disponibilního zůstatku platformy.
        try:
            balance = self.stripe_request("GET", "balance", None)
        except Problem:
            return 0
        for bucket in balance.get("available", []):
            if bucket.get("currency") == currency:
                return int(bucket.get("amount", 0))
        return 0

    def connect_status(self):
        with self.db() as db:
            accounts = [dict(row) for row in db.execute("SELECT seller_id, account_id FROM stripe_accounts ORDER BY seller_id")]
            transfers = [dict(row) for row in db.execute(
                "SELECT job_id, seller_id, transfer_id, amount_cents, currency, status, detail FROM stripe_transfers ORDER BY created DESC LIMIT 10")]
        return {"enabled": self.connect_enabled, "currency": self.connect_currency, "accounts": accounts,
                "transfers": transfers, "available": self.connect_balance(self.connect_currency) if self.connect_enabled else None,
                "hint": "" if self.connect_enabled else "Enable Connect in the Stripe dashboard, then set stripe_connect_enabled in market.json"}

    def setup_connect(self):
        """Založí connected účty prodejců přes Accounts v2 (v1 Stripe odmítá)."""
        if not self.connect_enabled:
            raise Problem(409, "Connect is not enabled in this marketplace configuration")
        created = []
        for seller_id in sorted(self.sellers):
            if self.connected_account(seller_id):
                continue
            account = self.stripe_request_v2("POST", "core/accounts", {
                "contact_email": "seller-" + seller_id + "@proofpay.local",
                "display_name": "ProofPay seller " + seller_id,
                "dashboard": "express",
                "identity": {"country": "CZ"},
                "configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}},
                "defaults": {"responsibilities": {"fees_collector": "application", "losses_collector": "application"}},
                "metadata": {"seller_id": seller_id},
            })
            with self.transaction() as db:
                db.execute("INSERT OR REPLACE INTO stripe_accounts VALUES(?,?,?)", (seller_id, account["id"], time.time()))
            created.append({"seller_id": seller_id, "account_id": account["id"]})
        return {"created": created, **self.connect_status()}

    def connect_onboarding_link(self, seller_id):
        """Odkaz na Stripe-hostovaný onboarding; identitu vyplňuje prodejce, ne platforma."""
        if not self.connect_enabled:
            raise Problem(409, "Connect is not enabled in this marketplace configuration")
        account = self.connected_account(seller_id)
        if not account:
            raise Problem(404, "seller has no connected account yet; run /api/connect/setup first")
        link = self.stripe_request("POST", "account_links", {
            "account": account, "type": "account_onboarding",
            "return_url": self.stripe_success_url.split("?")[0],
            "refresh_url": self.stripe_cancel_url.split("?")[0],
        })
        return {"seller_id": seller_id, "account_id": account, "url": link.get("url", ""),
                "expires_at": link.get("expires_at"), "status": self.connect_status()}

    def payoff_seller(self, job_id, seller_id, amount_usd):
        """Skutečná výplata prodejci přes Stripe Connect. Nikdy neblokuje vypořádání.

        Když Connect není zapnutý, neudělá nic (žádný záznam, žádná chyba);
        interní ledger platí dál. Selhání převodu se zapíše do stripe_transfers
        a vypořádání to nijak neovlivní.
        """
        if not self.connect_enabled:
            return None
        account = self.connected_account(seller_id)
        detail, status, transfer_id = "", "skipped", None
        if not account:
            detail = "seller has no connected account yet"
        elif not self.stripe_key:
            detail = "stripe key is not configured"
        else:
            cents = int(amount_usd) * 100
            available = self.connect_balance(self.connect_currency)
            currency = self.connect_currency
            note = ""
            if available < cents:
                # Platforma drží jen svou měnu (CZK): převedeme kurzem z reálné karty.
                rate, held = self.last_exchange_rate(self.connect_currency)
                if rate and held:
                    converted = int(round(int(amount_usd) * rate))
                    if self.connect_balance(held) >= converted:
                        cents, currency = converted, held
                        note = f"{amount_usd} USD converted at the rate of the funding charge"
            if self.connect_balance(currency) < cents:
                status = "pending_balance"
                detail = f"platform holds {self.connect_balance(currency)} {currency}, needs {cents}"
            else:
                try:
                    created = self.stripe_request("POST", "transfers", {
                        "amount": str(cents), "currency": currency, "destination": account,
                        "transfer_group": job_id, "metadata[job_id]": job_id, "metadata[seller_id]": seller_id,
                    })
                    transfer_id, status, detail = created.get("id", ""), "created", note
                except Problem as error:
                    status, detail = "failed", error.message[:200]
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO stripe_transfers VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, seller_id, transfer_id, cents, currency, status, detail, time.time()))
        return {"transfer_id": transfer_id, "status": status, "detail": detail}

    def top_up(self, session_id, amount):
        # Simulované dobití peněženky: nový řádek v ledgeru, žádná změna historie.
        if type(amount) is not int or not 1 <= amount <= 1000:
            raise Problem(400, "top-up must be an integer from 1 to 1000 USD")
        with self.transaction() as db:
            session = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not session:
                raise Problem(404, "session not found")
            wallet = db.execute("SELECT * FROM wallets WHERE id = ?", (session_id,)).fetchone()
            if wallet["available"] + wallet["locked"] + amount > 10000:
                raise Problem(409, "wallet balance limit reached")
            db.execute("UPDATE wallets SET available = available + ? WHERE id = ?", (amount, session_id))
            db.execute("UPDATE sessions SET budget = budget + ? WHERE id = ?", (amount, session_id))
            self.record(db, session_id, None, "TOPUP_ISSUED", amount)
        return self.session(session_id)

    def finish(self, job_id, refund):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise Problem(404, "job not found")
            final = "REFUNDED" if refund else "PAID"
            if row["state"] == final:
                return self.job_dict(row)
            if row["state"] not in ["DELIVERED", "FAILED"]:
                raise Problem(409, "job cannot be settled in its current state")
            verdict = self.verification(job_id)
            if refund and verdict["valid_delivery"]:
                raise Problem(409, "complete, verifiable delivery cannot be refunded automatically")
            if not refund and not verdict["valid_delivery"]:
                raise Problem(409, "incomplete or unverifiable delivery cannot be paid")
            price, session_id = row["price"], row["session_id"]
            if refund:
                db.execute("UPDATE wallets SET locked = locked - ?, available = available + ? WHERE id = ?", (price, price, session_id))
            else:
                db.execute("UPDATE wallets SET locked = locked - ? WHERE id = ?", (price, session_id))
                db.execute("UPDATE wallets SET available = available + ? WHERE id = ?", (price, row["seller_id"]))
            db.execute("UPDATE jobs SET state = ? WHERE id = ?", (final, job_id))
            self.record(db, session_id, job_id, "REFUND_AUTHORIZED_BY_CONTRACT" if refund else "PAYMENT_RELEASED", price)
        # Skutečná výplata prodejci (jen při zaplacení, ne u refundace).
        if not refund:
            try:
                self.payoff_seller(job_id, row["seller_id"], price)
            except Exception:
                pass
        self.sign_settlement(job_id)
        return self.job(job_id)

    def dashboard(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM sessions ORDER BY created DESC LIMIT 30")]
            wallets = [dict(r) for r in db.execute("SELECT * FROM wallets WHERE kind = 'seller'")]
            minted = db.execute("SELECT COALESCE(SUM(budget),0) FROM sessions").fetchone()[0]
            held = db.execute("SELECT COALESCE(SUM(available + locked),0) FROM wallets").fetchone()[0]
        return {"currency": CURRENCY, "simulated_payments": True, "services": SERVICES, "offers": self.offers(),
                "sessions": [self.session(i) for i in ids], "sellers": wallets,
                "invariant": {"issued": minted, "accounted": held, "holds": minted == held}}

    def ledger_chain(self):
        # Append-only hash chain: každý řádek nese hash předchozího; přepis je vidět.
        with self.db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT l.id, l.session_id, l.job_id, l.action, l.amount, l.created, s.title AS session_title "
                "FROM ledger l LEFT JOIN sessions s ON s.id = l.session_id ORDER BY l.id")]
        previous = "0" * 64
        chain = []
        for row in rows:
            core = {"ledger_id": row["id"], "session_id": row["session_id"], "job_id": row["job_id"],
                    "action": row["action"], "amount": row["amount"], "created": row["created"],
                    "prev_hash": previous, "currency": CURRENCY}
            row_hash = hashlib.sha256((previous + encoded(core)).encode()).hexdigest()
            chain.append({**core, "id": "lux-tx-" + str(row["id"]),
                          "created_at": datetime.fromtimestamp(row["created"], timezone.utc).isoformat(),
                          "session_title": row["session_title"], "row_hash": row_hash})
            previous = row_hash
        return chain

    def ledger_export(self, format="jsonl"):
        chain = self.ledger_chain()
        if format == "csv":
            lines = ["tx_id,ledger_id,session_id,job_id,action,amount,currency,created_at,row_hash"]
            for row in chain:
                fields = [row["id"], row["ledger_id"], row["session_id"] or "", row["job_id"] or "",
                          row["action"], row["amount"], CURRENCY, row["created_at"], row["row_hash"]]
                lines.append(",".join('"' + str(value).replace('"', '""') + '"' for value in fields))
            return "\n".join(lines) + "\n"
        return "".join(encoded({k: v for k, v in row.items() if k != "session_title"}) + "\n" for row in chain)

    def ledger_check(self):
        chain = self.ledger_chain()
        previous = "0" * 64
        for index, row in enumerate(chain):
            core = {k: row[k] for k in ["ledger_id", "session_id", "job_id", "action", "amount", "created", "prev_hash", "currency"]}
            expected = hashlib.sha256((previous + encoded(core)).encode()).hexdigest()
            if row["row_hash"] != expected:
                return {"ok": False, "rows": len(chain), "broken_at": row["ledger_id"]}
            previous = row["row_hash"]
        return {"ok": True, "rows": len(chain), "head": previous}

    # Veřejný seznam všech plateb (zakázek) se souhrnem a pohyby ledgeru.
    def payments(self, limit=1000):
        with self.db() as db:
            jobs = [dict(r) for r in db.execute(
                "SELECT j.id, j.session_id, j.seller_id, j.offer_id, j.price, j.state, j.created, j.contract, "
                "s.title AS session_title, s.budget AS session_budget "
                "FROM jobs j JOIN sessions s ON s.id = j.session_id ORDER BY j.created DESC LIMIT ?", (limit,))]
            ledger = [dict(r) for r in db.execute(
                "SELECT id, job_id, action, amount, created FROM ledger WHERE job_id IS NOT NULL ORDER BY id")]
            issued = db.execute("SELECT COALESCE(SUM(budget),0) FROM sessions").fetchone()[0]
            held = db.execute("SELECT COALESCE(SUM(available + locked),0) FROM wallets").fetchone()[0]
        moves = {}
        for row in ledger:
            moves.setdefault(row["job_id"], []).append(row)
        iso = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        items = []
        for job in jobs:
            contract = json.loads(job.pop("contract"))
            job_moves = moves.get(job["id"], [])
            settled = next((m for m in job_moves if m["action"] in ("PAYMENT_RELEASED", "REFUND_AUTHORIZED_BY_CONTRACT")), None)
            items.append({**job, "capability": contract.get("capability"),
                          "delivery": contract.get("delivery", ""), "created_at": iso(job["created"]),
                          "transactions": [{"id": "lux-tx-" + str(m["id"]), "action": m["action"], "amount": m["amount"],
                                            "created_at": iso(m["created"])} for m in job_moves],
                          "settled_at": iso(settled["created"]) if settled else None,
                          "settled_in_seconds": round(settled["created"] - job["created"], 3) if settled else None})
        paid = [p for p in items if p["state"] == "PAID"]
        refunded = [p for p in items if p["state"] == "REFUNDED"]
        revenue = {}
        for payment in items:
            entry = revenue.setdefault(payment["seller_id"], {"seller": payment["seller_id"], "paid_count": 0,
                                                              "paid_total": 0, "refunded_count": 0, "refunded_total": 0})
            if payment["state"] == "PAID":
                entry["paid_count"] += 1
                entry["paid_total"] += payment["price"]
            elif payment["state"] == "REFUNDED":
                entry["refunded_count"] += 1
                entry["refunded_total"] += payment["price"]
        services = {}
        for payment in paid:
            entry = services.setdefault(payment["capability"], {"capability": payment["capability"], "count": 0, "total": 0})
            entry["count"] += 1
            entry["total"] += payment["price"]
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "simulated_payments": True,
                "currency": CURRENCY, "payments": items,
                "summary": {"total": len(items), "paid_count": len(paid), "refunded_count": len(refunded),
                            "paid_total": sum(p["price"] for p in paid),
                            "refunded_total": sum(p["price"] for p in refunded),
                            "success_rate": round(100 * len(paid) / len(items), 1) if items else None},
                "revenue": sorted(revenue.values(), key=lambda r: r["paid_total"], reverse=True),
                "services": sorted(services.values(), key=lambda s: s["total"], reverse=True),
                "invariant": {"issued": issued, "accounted": held, "holds": issued == held}}

    # Veřejný doklad o platbě: kompletní auditní stopa jedné zakázky.
    LEDGER_LABELS = {
        "LUX_COINS_ISSUED": ("Test credits issued to the buyer wallet", "issue"),
        "TOPUP_ISSUED": ("Wallet topped up with simulated USD", "issue"),
        "STRIPE_TOPUP": ("Card payment in Stripe test mode credited as USD", "issue"),
        "ESCROW_LOCKED": ("Funds locked in escrow for the job", "escrow"),
        "RESULT_DELIVERED": ("Seller delivery recorded by the marketplace", "delivery"),
        "DELIVERY_FAILED": ("Seller delivery failed or was invalid", "failure"),
        "PAYMENT_RELEASED": ("Escrow released to the seller after verification", "payment"),
        "REFUND_AUTHORIZED_BY_CONTRACT": ("Escrow returned to the buyer by contract", "refund"),
        "EXECUTION_INTERRUPTED": ("Execution interrupted (restart); job settled separately", "failure"),
    }

    def receipt(self, job_id):
        if type(job_id) is not str or not 1 <= len(job_id) <= 100:
            raise Problem(400, "invalid job id")
        job = self.job(job_id)
        payment = self.payment(job_id)
        with self.db() as db:
            session_row = db.execute("SELECT * FROM sessions WHERE id = ?", (job["session_id"],)).fetchone()
            if not session_row:
                raise Problem(404, "session not found")
            wallet = dict(db.execute("SELECT * FROM wallets WHERE id = ?", (job["session_id"],)).fetchone())
            seller_wallet = dict(db.execute("SELECT * FROM wallets WHERE id = ?", (job["seller_id"],)).fetchone())
            ledger_rows = [dict(row) for row in db.execute("SELECT * FROM ledger WHERE session_id = ? ORDER BY id", (job["session_id"],))]
            signature = db.execute("SELECT * FROM signatures WHERE job_id = ?", (job_id,)).fetchone()
            transfer_row = db.execute("SELECT * FROM stripe_transfers WHERE job_id = ?", (job_id,)).fetchone()
            minted = db.execute("SELECT COALESCE(SUM(budget),0) FROM sessions").fetchone()[0]
            held = db.execute("SELECT COALESCE(SUM(available + locked),0) FROM wallets").fetchone()[0]
        # Rekonstrukce zůstatků z ledgeru: každý pohyb i se stavem po něm.
        available = locked = 0
        timeline = []
        job_moves = []
        for row in ledger_rows:
            action, amount = row["action"], row["amount"]
            if action in ("LUX_COINS_ISSUED", "TOPUP_ISSUED", "STRIPE_TOPUP"):
                available += amount
            elif action == "ESCROW_LOCKED":
                available -= amount
                locked += amount
            elif action == "PAYMENT_RELEASED":
                locked -= amount
            elif action == "REFUND_AUTHORIZED_BY_CONTRACT":
                locked -= amount
                available += amount
            label, kind = self.LEDGER_LABELS.get(action, (action.replace("_", " ").capitalize(), "other"))
            entry = {"id": "lux-tx-" + str(row["id"]), "ledger_id": row["id"], "job_id": row["job_id"],
                     "action": action, "label": label, "kind": kind, "amount": amount,
                     "available_after": available, "locked_after": locked, "created": row["created"],
                     "created_at": datetime.fromtimestamp(row["created"], timezone.utc).isoformat()}
            timeline.append(entry)
            if row["job_id"] == job_id:
                job_moves.append(entry)
        credits = sum(e["amount"] for e in job_moves if e["kind"] in ["escrow", "payment", "refund"])
        escrow_locked = sum(e["amount"] for e in job_moves if e["kind"] == "escrow")
        settled = sum(e["amount"] for e in job_moves if e["kind"] in ["payment", "refund"])
        return {"receipt_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
                "simulated_payments": True, "currency": CURRENCY,
                "job": job, "payment": payment, "verification": self.verification(job_id),
                "session": {"id": session_row["id"], "title": session_row["title"], "budget": session_row["budget"],
                            "fixture": session_row["fixture"], "service": session_row["service"],
                            "created": session_row["created"],
                            "created_at": datetime.fromtimestamp(session_row["created"], timezone.utc).isoformat()},
                "wallets": {"buyer": wallet, "seller": seller_wallet},
                "timeline": timeline, "job_timeline": job_moves,
                "reconciliation": {"escrow_locked": escrow_locked, "settled": settled, "price": job["price"],
                                   "balanced": escrow_locked == settled == job["price"] and credits >= 0,
                                   "movements": len(job_moves)},
                "stripe_transfer": (dict(transfer_row) if transfer_row else None),
                "signature": ({"alg": signature["alg"], "key_id": signature["public_key_id"],
                               "value_base64": signature["signature"], "payload": signature["payload"],
                               "payload_sha256": signature["payload_sha256"],
                               "created_at": datetime.fromtimestamp(signature["created"], timezone.utc).isoformat()}
                              if signature else None),
                "public_key": ({"id": self.signing_key_id, "alg": "Ed25519", "public_key_pem": self.signing_public_pem}
                               if self.signing_public_pem else None),
                "invariant": {"issued": minted, "accounted": held, "holds": minted == held}}

class Handler(BaseHTTPRequestHandler):
    server_version = "ProofPayMVP/1"

    def log_message(self, *args):
        pass

    def send(self, status, value, content_type="application/json; charset=utf-8"):
        if isinstance(value, bytes):
            body = value
        else:
            body = encoded(value).encode() if content_type.startswith("application/json") else value.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def execute_stream(self, job_id):
        market = self.server.market
        market.job(job_id)
        completed = threading.Event()
        failure = []

        def execute():
            try:
                market.execute(job_id)
            except Exception:
                failure.append(True)
            finally:
                completed.set()
                with market.changed:
                    market.changed.notify_all()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        threading.Thread(target=execute, daemon=True).start()
        offset = 0
        heartbeat = time.monotonic()
        try:
            while True:
                with market.changed:
                    text = market.previews.get(job_id, "")
                    delta = text[offset:]
                    offset = len(text)
                    finished = completed.is_set()
                    if not delta and not finished:
                        market.changed.wait(timeout=0.1)
                if delta:
                    value = {"choices": [{"delta": {"content": delta}}]}
                    self.wfile.write(("data: " + encoded(value) + "\n\n").encode())
                    self.wfile.flush()
                if finished:
                    if failure:
                        self.wfile.write(b'data: {"error":"execution failed"}\n\n')
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                if time.monotonic() - heartbeat > 1:
                    self.wfile.write(b": waiting\n\n")
                    self.wfile.flush()
                    heartbeat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            # Odpojení náhledu neruší již financovanou práci prodejce.
            return

    def proxy_console(self, console_url, path):
        # Local runs only (market.json "console_url"): serve the console under
        # web/ on this origin, as Caddy does in production, so relative links
        # between the console and these pages work and they share the sign-in.
        if path == "/web":
            self.send_response(308)
            self.send_header("Location", "/web/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        query = "?" + self.path.split("?", 1)[1] if "?" in self.path else ""
        body = None
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise Problem(400, "invalid body length")
            if not 0 <= length <= 1048576:
                raise Problem(413, "body too large")
            body = self.rfile.read(length)
        headers = {name: self.headers[name] for name in ("Authorization", "Content-Type", "Accept") if self.headers.get(name)}
        request = Request(console_url + path.removeprefix("/web/") + query, data=body, headers=headers, method=self.command)
        try:
            response = urlopen(request, timeout=60)
        except HTTPError as error:
            response = error
        except (URLError, OSError):
            raise Problem(502, "agent console unavailable")
        with response:
            payload = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    def route(self):
        market = self.server.market
        path = self.path.split("?", 1)[0]
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        console_url = market.config.get("console_url")
        if console_url and (path == "/web" or path.startswith("/web/")):
            return self.proxy_console(console_url, path)
        if self.command == "GET":
            if path == "/health":
                return self.send(200, {"ok": True, "payments": "simulated USD"})
            if path == "/" or path == "/overview":
                # / vede na konzoli (Caddy), /overview je přímý odkaz na přehled.
                return self.send(200, ui.dashboard_page(market.dashboard()), "text/html; charset=utf-8")
            if path == "/.well-known/proofpay-keys.json":
                keys = [{"id": market.signing_key_id, "alg": "Ed25519", "public_key_pem": market.signing_public_pem}] if market.signing_public_pem else []
                return self.send(200, {"keys": keys})
            if path == "/assets/site.css":
                return self.send(200, ui.SITE_CSS, "text/css; charset=utf-8")
            if path == "/assets/favicon.svg":
                return self.send(200, ui.FAVICON_SVG, "image/svg+xml; charset=utf-8")
            if path == "/assets/hero.jpg":
                # The console's hero photo: frontend/ in the repository, web/ next to this file when deployed.
                here = Path(__file__).resolve().parent
                for candidate in (here / "web" / "hero.jpg", here.parent.parent / "frontend" / "hero.jpg"):
                    if candidate.is_file():
                        return self.send(200, candidate.read_bytes(), "image/jpeg")
                raise Problem(404, "route not found")
            if path == "/api/connect/status":
                return self.send(200, market.connect_status() if token == market.client_token else {"enabled": market.connect_enabled})
            if path == "/api/dashboard":
                return self.send(200, market.dashboard())
            if path == "/api/offers":
                return self.send(200, {"offers": market.offers()})
            if path == "/api/services":
                return self.send(200, {"services": SERVICES, "currency": CURRENCY})
            if path == "/api/ledger/export":
                query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                export_format = (query.get("format", ["jsonl"])[0] or "jsonl").lower()
                if export_format == "csv":
                    return self.send(200, market.ledger_export("csv"), "text/csv; charset=utf-8")
                return self.send(200, market.ledger_export("jsonl"), "application/x-ndjson; charset=utf-8")
            if path == "/api/ledger/check":
                return self.send(200, market.ledger_check())
            if path == "/api/payments":
                return self.send(200, market.payments())
            if path == "/docs":
                return self.send(200, ui.docs_page(), "text/html; charset=utf-8")
            if path == "/payments":
                search = parse_qs(self.path.split("?", 1)[1]).get("q", [""])[0][:200] if "?" in self.path else ""
                return self.send(200, ui.payments_page(market.payments(), search, market.ledger_check()), "text/html; charset=utf-8")
            if path.startswith("/api/receipt/"):
                return self.send(200, market.receipt(path.removeprefix("/api/receipt/")))
            if path.startswith("/receipt/"):
                job_id = path.removeprefix("/receipt/")
                return self.send(200, ui.receipt_page(market.receipt(job_id), job_id), "text/html; charset=utf-8")
            market.require_client(token)
            if path.startswith("/api/sessions/"):
                return self.send(200, market.session(path.removeprefix("/api/sessions/")))
            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/")
                if job_id.endswith("/payment"):
                    return self.send(200, market.payment(job_id.removesuffix("/payment")))
                if job_id.endswith("/verify"):
                    return self.send(200, market.verification(job_id.removesuffix("/verify")))
                return self.send(200, market.job(job_id))
        elif self.command == "POST":
            if path in ["/sandbox/cart", "/providers/attest", "/providers/research", "/providers/progress"]:
                market.seller_for_token(token)
            else:
                market.require_client(token)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise Problem(400, "invalid body length")
            if not 0 < length <= 65536:
                raise Problem(413, "body must have 1 to 65536 bytes")
            try:
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise Problem(400, "invalid JSON")
            if type(payload) is not dict:
                raise Problem(400, "JSON object required")
            if path == "/sandbox/cart":
                return self.send(200, market.sandbox(payload, token))
            if path == "/providers/attest":
                return self.send(200, market.attest(payload, token))
            if path == "/providers/progress":
                return self.send(200, market.progress(payload, token))
            if path == "/providers/research":
                return self.send(200, market.research(payload, token))
            if path == "/api/connect/setup":
                return self.send(200, market.setup_connect())
            if path == "/api/connect/link":
                seller_id = payload.get("seller_id")
                if type(seller_id) is not str or seller_id not in market.sellers:
                    raise Problem(400, "unknown seller_id")
                return self.send(200, market.connect_onboarding_link(seller_id))
            if path == "/api/sessions":
                return self.send(201, market.create_session(payload))
            if path == "/api/jobs":
                return self.send(201, market.buy(payload))
            if path.startswith("/api/sessions/") and path.endswith("/stripe-checkout"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/stripe-checkout")
                return self.send(200, market.stripe_checkout(session_id, payload.get("amount_usd")))
            if path.startswith("/api/sessions/") and path.endswith("/stripe-sandbox-pay"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/stripe-sandbox-pay")
                return self.send(200, market.stripe_sandbox_pay(session_id, payload.get("amount_usd")))
            if path.startswith("/api/sessions/") and path.endswith("/stripe-confirm"):
                return self.send(200, market.stripe_confirm(payload.get("stripe_session_id")))
            if path.startswith("/api/sessions/") and path.endswith("/topup"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/topup")
                amount = payload.get("amount")
                return self.send(200, market.top_up(session_id, amount))
            if path.startswith("/api/jobs/"):
                parts = path.removeprefix("/api/jobs/").split("/")
                if len(parts) == 2:
                    job_id, action = parts
                    if action == "execute":
                        if self.headers.get("Accept") == "text/event-stream":
                            return self.execute_stream(job_id)
                        return self.send(200, market.execute(job_id))
                    if action in ["refund", "settle"]:
                        return self.send(200, market.finish(job_id, action == "refund"))
        raise Problem(404, "route not found")

    def dispatch(self):
        self.connection.settimeout(150)
        try:
            self.route()
        except Problem as error:
            self.send(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.send(500, {"error": "internal error"})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    server = ThreadingHTTPServer(("127.0.0.1", config.get("port", 3070)), Handler)
    server.daemon_threads = True
    server.market = Market(config)
    print("ProofPay MVP listening on", server.server_address, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
