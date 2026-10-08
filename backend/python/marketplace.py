"""ProofPay MVP: testovací kredity, transakční úschova a skuteční vzdálení LSL pracovníci."""
from __future__ import annotations

import argparse
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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

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
            raise Problem(400, "budget must be an integer from 1 to 100 Lux Coins")
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
                raise Problem(409, "insufficient available Lux Coins")
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

    # Veřejný doklad o platbě: kompletní auditní stopa jedné zakázky.
    LEDGER_LABELS = {
        "LUX_COINS_ISSUED": ("Test credits issued to the buyer wallet", "issue"),
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
            minted = db.execute("SELECT COALESCE(SUM(budget),0) FROM sessions").fetchone()[0]
            held = db.execute("SELECT COALESCE(SUM(available + locked),0) FROM wallets").fetchone()[0]
        # Rekonstrukce zůstatků z ledgeru: každý pohyb i se stavem po něm.
        available = locked = 0
        timeline = []
        job_moves = []
        for row in ledger_rows:
            action, amount = row["action"], row["amount"]
            if action == "LUX_COINS_ISSUED":
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
                "invariant": {"issued": minted, "accounted": held, "holds": minted == held}}

    def receipt_html(self, job_id):
        data = self.receipt(job_id)
        job, payment, verification = data["job"], data["payment"], data["verification"]
        esc = html.escape
        money = lambda n: f"{n} {esc(data['currency'])}"
        balances = {entry["ledger_id"]: entry for entry in data["timeline"]}
        state = job["state"]
        state_cls = "ok" if state == "PAID" else "warn" if state == "REFUNDED" else ""
        rows = []
        for tx in payment["transactions"]:
            balance = balances.get(tx["ledger_id"], {})
            rows.append(
                "<tr>"
                f"<td class=mono>{esc(tx['id'])}</td>"
                f"<td class=num>{tx['ledger_id']}</td>"
                f"<td><b>{esc(tx['action'])}</b></td>"
                f"<td class=num>{money(tx['amount'])}</td>"
                f"<td class=mono>{esc(tx['from_wallet'])} · {esc(tx['from_account'])}</td>"
                f"<td class=mono>{esc(tx['to_wallet'])} · {esc(tx['to_account'])}</td>"
                f"<td class=num>{balance.get('available_after', '—')}</td>"
                f"<td class=num>{balance.get('locked_after', '—')}</td>"
                f"<td class=mono>{esc(tx['created_at'])}</td>"
                "</tr>")
        timeline_rows = []
        previous = None
        for entry in data["job_timeline"]:
            delta = ""
            if previous is not None:
                delta = f"+{entry['created'] - previous:.3f}s"
            previous = entry["created"]
            timeline_rows.append(
                "<tr>"
                f"<td class=num>{entry['ledger_id']}</td>"
                f"<td><span class='badge {esc(entry['kind'])}'>{esc(entry['kind'])}</span> {esc(entry['label'])}</td>"
                f"<td class=num>{money(entry['amount']) if entry['amount'] else '—'}</td>"
                f"<td class=mono>{esc(entry['created_at'])} <span class=muted>{delta}</span></td>"
                "</tr>")
        checks = verification.get("checks") or []
        check_rows = "".join(
            f"<tr><td class=mono>{esc(c['case_id'])}</td><td class=num>{c['expected_cents']}</td>"
            f"<td class=num>{c['observed_cents']}</td>"
            f"<td>{'<span class=\'badge ok\'>PASSED</span>' if c['passed'] else '<span class=\'badge failure\'>FAILED</span>'}</td></tr>"
            for c in checks if type(c) is dict)
        reasons = verification.get("reasons") or []
        reason_html = "".join(f"<li>{esc(r)}</li>" for r in reasons) or "<li>No discrepancies found.</li>"
        receipts = "".join(f"<li class=mono>{esc(r)}</li>" for r in payment["verification_receipts"]) or "<li class=muted>None</li>"
        artifact = (job.get("result") or {}).get("artifact") or {}
        delivery_preview = ""
        if artifact:
            preview = artifact.get("content") or artifact.get("summary") or ""
            delivery_preview = (f"<h3>Delivery preview</h3><pre class=preview>{esc(preview[:1500])}</pre>"
                                + ("<p class=muted>…truncated; the full artifact is in the raw JSON below.</p>" if len(preview) > 1500 else ""))
        reconciliation = data["reconciliation"]
        invariant = data["invariant"]
        raw = esc(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        contract = esc(json.dumps(job["contract"], ensure_ascii=False, indent=2, sort_keys=True))
        css = """
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#0b1020;color:#e8edf8}
*{box-sizing:border-box}body{max-width:1180px;margin:auto;padding:28px 20px 60px}
h1{font-size:clamp(28px,4vw,44px);letter-spacing:-1px;margin:6px 0}h2{font-size:20px;margin:34px 0 10px}
h3{font-size:15px;margin:18px 0 6px;color:#c7d3ea}
a{color:#a9bfff}.muted{color:#9daac4}.mono{font-family:ui-monospace,monospace;font-size:12.5px;overflow-wrap:anywhere}
.num{font-variant-numeric:tabular-nums;text-align:right}
.badge{display:inline-block;border:1px solid #586275;background:#242b3c;border-radius:20px;padding:3px 10px;font-size:11px;font-weight:700;letter-spacing:.6px}
.badge.ok,.badge.payment{color:#85edb2;border-color:#2f6b45}.badge.warn,.badge.escrow{color:#f9cf71;border-color:#7a6327}
.badge.failure,.badge.refund{color:#ff9393;border-color:#7c4a4a}.badge.issue,.badge.delivery{color:#a9bfff}
.chip{display:inline-block;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:700}
.chip.ok{background:#12351f;color:#85edb2}.chip.warn{background:#3a3415;color:#f9cf71}
.card{background:#141d32;border:1px solid #29364e;border-radius:14px;padding:18px;margin-top:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.fact label{display:block;font-size:11px;color:#9daac4;letter-spacing:.4px}.fact b,.fact .mono{font-size:14px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
th{color:#9daac4;text-align:left;font-size:11px;letter-spacing:.5px;text-transform:uppercase}
td,th{padding:9px 8px;border-bottom:1px solid #29364e;vertical-align:top}
pre{background:#0b1020;border:1px solid #29364e;border-radius:10px;padding:12px;overflow:auto;font:12px ui-monospace,monospace;max-height:420px}
pre.preview{max-height:260px;white-space:pre-wrap}
details{margin-top:12px}summary{cursor:pointer;color:#a9bfff}
.notice{border-left:3px solid #fbbf24;background:#1e2435;padding:12px 16px;border-radius:8px;margin-top:14px;font-size:13px;line-height:1.6}
.row{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
ul{margin:6px 0 0 18px;padding:0}
"""
        generated = esc(data["generated_at"])
        return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Payment receipt · {esc(job_id)}</title><style>{css}</style>
<div class="row"><span class="badge">PAYMENT RECEIPT</span>
<span class="badge failure">SIMULATED PAYMENTS</span>
<span class="chip {state_cls}">{esc(state)}</span>
<span class="muted mono">{esc(job_id)}</span></div>
<h1>{money(job['price'])}</h1>
<p class="muted">Complete audit trail of one agent-to-agent purchase: contract, escrow,
verification, settlement and the central ledger entries behind every movement.
Generated {generated}.</p>
<div class="card grid">
<div class="fact"><label>Amount</label><b>{money(job['price'])}</b></div>
<div class="fact"><label>Status</label><b>{esc(state)}</b></div>
<div class="fact"><label>Payer (buyer wallet)</label><div class="mono">{esc(job['session_id'])}</div></div>
<div class="fact"><label>Payee (seller)</label><div class="mono">{esc(job['seller_id'])}</div></div>
<div class="fact"><label>Offer / service</label><div class="mono">{esc(job['offer_id'])} · {esc(job['contract'].get('capability', ''))}</div></div>
<div class="fact"><label>Idempotency key</label><div class="mono">{esc(job['idempotency_key'])}</div></div>
<div class="fact"><label>Created (UTC)</label><div class="mono">{esc(data['session']['created_at'])}</div></div>
<div class="fact"><label>Method</label><div>Central SQLite ledger · escrow · double-entry</div></div>
<div class="fact"><label>Session</label><div class="mono">{esc(data['session']['title'])}<br>budget {money(data['session']['budget'])} · fixture {esc(data['session']['fixture'])}</div></div>
<div class="fact"><label>Buyer wallet now</label><div class="mono">available {data['wallets']['buyer']['available']} · locked {data['wallets']['buyer']['locked']}</div></div>
<div class="fact"><label>Seller wallet now</label><div class="mono">available {data['wallets']['seller']['available']} · locked {data['wallets']['seller']['locked']}</div></div>
<div class="fact"><label>Contract SHA-256</label><div class="mono">{esc(payment['contract_sha256'])}</div></div>
</div>

<h2>Money movement</h2>
<div class="card"><table>
<tr><th>Transaction ID</th><th class="num">Ledger #</th><th>Action</th><th class="num">Amount</th><th>From</th><th>To</th><th class="num">Buyer avail. after</th><th class="num">Buyer locked after</th><th>Timestamp (UTC)</th></tr>
{''.join(rows)}
</table>
<p class="muted">Every row is an immutable ledger record; the buyer balance columns are reconstructed
from the session ledger (Lux Coins issued → escrow locked → released or refunded).</p></div>

<h2>Settlement lifecycle</h2>
<div class="card"><table>
<tr><th class="num">Ledger #</th><th>Event</th><th class="num">Amount</th><th>When (UTC, + since previous)</th></tr>
{''.join(timeline_rows)}
</table>
<p class="muted">One settlement per job: a verified delivery releases the escrow, an invalid one refunds it.
The unique idempotency key and database constraints make a second charge impossible.</p></div>

<h2>Contract</h2>
<div class="card"><pre>{contract}</pre>
<p class="muted">Contract SHA-256: <span class="mono">{esc(payment['contract_sha256'])}</span></p></div>

<h2>Verification</h2>
<div class="card">
<div class="row"><span class="chip {'ok' if verification['valid_delivery'] else 'warn'}">{'VALID DELIVERY' if verification['valid_delivery'] else 'NOT VERIFIED'}</span>
<span class="muted">{esc(verification.get('scope', ''))}</span></div>
<h3>Findings</h3><ul>{reason_html}</ul>
{('<h3>Cart checks</h3><table><tr><th>Case</th><th class="num">Expected (cents)</th><th class="num">Observed (cents)</th><th>Result</th></tr>' + check_rows + '</table>') if check_rows else ''}
<h3>Execution receipts</h3><ul>{receipts}</ul>
<p class="muted">Delivery SHA-256: <span class="mono">{esc(payment['result_sha256'] or '—')}</span></p>
{delivery_preview}
</div>

<h2>Integrity</h2>
<div class="card">
<div class="row"><span class="chip {'ok' if reconciliation['balanced'] else 'warn'}">ESCROW RECONCILIATION {'BALANCED' if reconciliation['balanced'] else 'MISMATCH'}</span>
<span class="muted">locked {money(reconciliation['escrow_locked'])} · settled {money(reconciliation['settled'])} · price {money(reconciliation['price'])} · {reconciliation['movements']} movements</span></div>
<div class="row" style="margin-top:10px"><span class="chip {'ok' if invariant['holds'] else 'warn'}">MARKET INVARIANT {'HOLDS' if invariant['holds'] else 'BROKEN'}</span>
<span class="muted">issued {invariant['issued']} == accounted {invariant['accounted']} Lux Coins</span></div>
<div class="notice"><strong>Honest disclosure.</strong> Payments are simulated Lux Coins in a central SQLite
ledger on this marketplace; nothing here is a blockchain transaction and transaction IDs are scoped to this
database. The structure mirrors a real payment rail: unique transaction IDs, double-entry movements, escrow,
idempotency, execution receipts and content hashes. Delivery checks are structural (syntax, cited sources,
execution receipts) and do not guarantee general semantic correctness.</div>
<p class="muted">Raw machine-readable receipt: <a href="../api/receipt/{esc(job_id)}">../api/receipt/{esc(job_id)}</a></p>
</div>

<details><summary>Raw receipt JSON</summary><pre>{raw}</pre></details>
<p class="muted" style="margin-top:26px"><a href="..">← marketplace overview</a> · <a href="../web/">agent console</a></p>
</html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ProofPayMVP/1"

    def log_message(self, *args):
        pass

    def send(self, status, value, content_type="application/json; charset=utf-8"):
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

    def route(self):
        market = self.server.market
        path = self.path.split("?", 1)[0]
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if self.command == "GET":
            if path == "/health":
                return self.send(200, {"ok": True, "payments": "simulated Lux Coins"})
            if path == "/":
                return self.send(200, Path(__file__).with_name("dashboard.html").read_text(), "text/html; charset=utf-8")
            if path == "/api/dashboard":
                return self.send(200, market.dashboard())
            if path == "/api/offers":
                return self.send(200, {"offers": market.offers()})
            if path == "/api/services":
                return self.send(200, {"services": SERVICES, "currency": CURRENCY})
            if path.startswith("/api/receipt/"):
                return self.send(200, market.receipt(path.removeprefix("/api/receipt/")))
            if path.startswith("/receipt/"):
                return self.send(200, market.receipt_html(path.removeprefix("/receipt/")), "text/html; charset=utf-8")
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
            if path == "/api/sessions":
                return self.send(201, market.create_session(payload))
            if path == "/api/jobs":
                return self.send(201, market.buy(payload))
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
