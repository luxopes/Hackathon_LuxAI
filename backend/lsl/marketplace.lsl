# The marketplace, rewritten in LSL on top of the sqlite 1.0.0 library.
#
# It is the single writer of money in the product: wallets, escrow, jobs,
# delivery verification, settlement and refunds, card top-ups through Stripe,
# and the append-only ledger — all in one SQLite file, one transaction per
# purchase. The schema, the JSON shapes, the status codes and the ledger rows
# are byte-compatible with the Python implementation it replaces, so it can take
# over the same database file without a migration.
#
# What stays in Python on purpose: the ledger *hash chain* check and export
# (Python's float repr and LSL's %.6f do not agree, so the canonical
# implementation must keep computing those hashes) and the public HTML pages
# (ui.py). Everything else — the whole money API — runs here.
#
# Running it:
#   MARKET_CONFIG=/etc/proofpay-mvp/market.json marketserver
#     - MARKET_CONFIG   path to the config (sellers, tokens, database, Stripe)
#     - MARKET_PORT     listen port, default 3070 (use 3072 to run side by side)

load httpserver as web
load sqlite
load requests
load process
load env
load json
load time
load fd

settings = {"port": Int(env.get("MARKET_PORT", "3070")), "config": env.get("MARKET_CONFIG", "/etc/proofpay-mvp/market.json")}
config = {}
sellers = {}
# Skaláry v držáku: přiřazení do skalárního globálu uvnitř funkce by se stínilo.
secrets = {"client_token": "", "signing_key_file": "", "signing_key_id": "", "signing_public_pem": "",
    "stripe_key": "", "stripe_success_url": "", "stripe_cancel_url": "", "currency": "USD"}
problems = {"status": 0, "message": ""}
previews = {}

# ---------- jednoduché chyby jako v Pythonu (Problem) ----------
function problem(status, message):
    problems["status"] = status
    problems["message"] = message
    Error(MarketError: message)
end

# ---------- SQL přes knihovnu sqlite (bind parametry, JSON výstup) ----------
function db_path():
    return String(config.get("database", "/var/lib/proofpay-mvp/market.db"))
end

function db_rows(sql, params=None):
    if params == None:
        return sqlite.query(db_path(), sql)
    end
    return sqlite.query(db_path(), sql, params)
end

function db_one(sql, params=None):
    if params == None:
        return sqlite.query_one(db_path(), sql)
    end
    return sqlite.query_one(db_path(), sql, params)
end

function db_run(sql, params=None):
    if params == None:
        return sqlite.execute(db_path(), sql)
    end
    return sqlite.execute(db_path(), sql, params)
end

function stamp_now():
    # Mikrosekundová přesnost: stejnou hodnotu umí zapsat i přečíst Python,
    # takže hash řetěz zůstává ověřitelný kanonickou implementací.
    stamp = time.time()
    return Float(Int(stamp * 1000000.0)) / 1000000.0
end

function money(amount):
    return Int(amount)
end

function random_below(limit):
    outcome = process.capture_result("/usr/bin/od", ["-An", "-N2", "-tu2", "/dev/urandom"])
    if not outcome["ok"]:
        return Int(time.time() * 1000.0) % limit
    end
    return Int(String(outcome["stdout"]).strip()) % limit
end

function random_id(prefix, bytes_count):
    outcome = process.capture_result("/usr/bin/openssl", ["rand", "-hex", String(bytes_count)])
    token = ""
    if outcome["ok"]:
        token = String(outcome["stdout"]).strip()
    end
    if len(token) < bytes_count * 2:
        Error(MarketError: "random id generation failed")
    end
    return prefix + token
end

function sha256_text(text):
    outcome = process.capture_input_result("/usr/bin/openssl", ["dgst", "-sha256", "-r"], text)
    if not outcome["ok"]:
        Error(MarketError: "hashing failed")
    end
    return String(outcome["stdout"]).strip().split(" ")[0]
end

function encoded(value):
    # Stejný tvar jako Python: klíče seřazené, bez mezer. Vnořené dicty se
    # skládají ručně tam, kde na pořadí záleží (ledger a podpisy).
    return json.encode(value)
end

function load_config():
    raw = readfile(settings["config"])
    data = json.decode(raw)
    # config je globální dict: plní se na místě, přiřazení by se stínilo.
    for key in data:
        config[key] = data[key]
    end
    secrets["client_token"] = readfile(String(config["client_token_file"])).strip()
    if len(secrets["client_token"]) < 32:
        Error(MarketError: "client token must have at least 32 characters")
    end
    for seller in config["sellers"]:
        entry = json.decode(json.encode(seller))
        entry["token"] = readfile(String(entry["token_file"])).strip()
        if len(String(entry["token"])) < 32:
            Error(MarketError: "seller token too short")
        end
        sellers[String(entry["id"])] = entry
    end
    secrets["signing_key_file"] = String(config.get("receipt_signing_key_file", ""))
    if secrets["signing_key_file"] != "":
        try:
            secrets["signing_public_pem"] = readfile(secrets["signing_key_file"] + ".pub").strip()
        else:
            secrets["signing_public_pem"] = ""
        end
        if secrets["signing_public_pem"] != "":
            secrets["signing_key_id"] = "ppk-" + sha256_text(secrets["signing_public_pem"])[0:16]
        end
    end
    secrets["stripe_key"] = ""
    key_file = String(config.get("stripe_key_file", ""))
    if key_file != "":
        try:
            secrets["stripe_key"] = readfile(key_file).strip()
        else:
            secrets["stripe_key"] = ""
        end
    end
    secrets["stripe_success_url"] = String(config.get("stripe_success_url", "https://hackathon.lux-ai.cz/web/?stripe=ok"))
    secrets["stripe_cancel_url"] = String(config.get("stripe_cancel_url", "https://hackathon.lux-ai.cz/web/?stripe=cancel"))
    secrets["currency"] = "USD"
end

function require_client(token):
    if token != secrets["client_token"]:
        call problem(401, "unauthorized")
    end
end

function seller_for_token(token):
    for id in sellers:
        if String(sellers[id]["token"]) == token:
            return sellers[id]
        end
    end
    call problem(401, "unauthorized seller")
end

function services():
    # Katalog služeb je datová konstanta; drží se stejné jako Python services.py.
    return {
        "http-cart-audit": {"id": "http-cart-audit", "name": "Cart audit",
            "delivery": "Three HTTP cart checks with execution receipts"},
        "short-research": {"id": "short-research", "name": "Short research",
            "delivery": "Short research with live web sources fetched via Apify"},
        "python-code": {"id": "python-code", "name": "Python code",
            "delivery": "Python code and basic tests with syntax validation"},
        "text-summary": {"id": "text-summary", "name": "Text summary",
            "delivery": "Summary of the supplied text with structure checks"},
        "translation": {"id": "translation", "name": "Translation",
            "delivery": "Translation of the supplied text with structure checks"},
        "ideas": {"id": "ideas", "name": "Five ideas",
            "delivery": "Five explained ideas with structure checks"}
    }
end

function setup_schema():
    statements = [
        "CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY, kind TEXT NOT NULL, available INTEGER NOT NULL CHECK(available >= 0), locked INTEGER NOT NULL CHECK(locked >= 0))",
        "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL, budget INTEGER NOT NULL, fixture TEXT NOT NULL, created REAL NOT NULL, service TEXT NOT NULL DEFAULT 'auto')",
        "CREATE TABLE IF NOT EXISTS offers_list(id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, name TEXT NOT NULL, price INTEGER NOT NULL CHECK(price > 0), active INTEGER NOT NULL DEFAULT 1, capability TEXT NOT NULL DEFAULT 'http-cart-audit', delivery TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'compact')",
        "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), offer_id TEXT NOT NULL REFERENCES offers_list(id), seller_id TEXT NOT NULL, price INTEGER NOT NULL, state TEXT NOT NULL, contract TEXT NOT NULL, result TEXT, failure TEXT, idempotency_key TEXT NOT NULL, created REAL NOT NULL, UNIQUE(session_id, idempotency_key))",
        "CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), case_id TEXT NOT NULL, total_cents INTEGER NOT NULL, UNIQUE(job_id, case_id))",
        "CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, job_id TEXT, action TEXT NOT NULL, amount INTEGER NOT NULL, created REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS service_receipts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), artifact_sha256 TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS research_sources(job_id TEXT NOT NULL REFERENCES jobs(id), url TEXT NOT NULL, title TEXT NOT NULL, extract TEXT NOT NULL, PRIMARY KEY(job_id,url))",
        "CREATE TABLE IF NOT EXISTS stripe_payments(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, lux_coins INTEGER NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, credited REAL)",
        "CREATE TABLE IF NOT EXISTS signatures(job_id TEXT PRIMARY KEY, alg TEXT NOT NULL, public_key_id TEXT NOT NULL, signature TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, created REAL NOT NULL)"
    ]
    for statement in statements:
        call db_run(statement)
    end
    # Dotažení sloupců, aby šlo použít i databázi založenou Pythonem.
    for column in ["capability", "delivery", "tier"]:
        present = False
        for entry in db_rows("PRAGMA table_info(offers)"):
            if String(entry["name"]) == column:
                present = True
            end
        end
        if not present:
            if column == "capability":
                call db_run("ALTER TABLE offers ADD COLUMN capability TEXT NOT NULL DEFAULT 'http-cart-audit'")
            elif column == "delivery":
                call db_run("ALTER TABLE offers ADD COLUMN delivery TEXT NOT NULL DEFAULT ''")
            else:
                call db_run("ALTER TABLE offers ADD COLUMN tier TEXT NOT NULL DEFAULT 'compact'")
            end
        end
    end
    present = False
    for entry in db_rows("PRAGMA table_info(sessions)"):
        if String(entry["name"]) == "service":
            present = True
        end
    end
    if not present:
        call db_run("ALTER TABLE sessions ADD COLUMN service TEXT NOT NULL DEFAULT 'auto'")
    end
    catalog = services()
    for id in sellers:
        seller = sellers[id]
        call db_run("INSERT OR IGNORE INTO wallets VALUES(?, 'seller', 0, 0)", [String(seller["id"])])
        offers = seller.get("offers", [])
        if len(offers) == 0:
            offers = [{"id": seller.get("offer_id", ""), "name": seller.get("name", ""),
                       "price": seller.get("price", 0), "capability": "http-cart-audit", "tier": "compact"}]
        end
        for offer in offers:
            capability = String(offer["capability"])
            if capability not in catalog:
                Error(MarketError: "invalid configured offer")
            end
            call db_run("INSERT OR IGNORE INTO offers_list(id,seller_id,name,price,active,capability,delivery,tier) VALUES(?,?,?,?,1,?,?,?)",
                [String(offer["id"]), String(seller["id"]), String(offer["name"]), Int(offer["price"]), capability,
                 String(offer.get("delivery", catalog[capability]["delivery"])), String(offer.get("tier", "compact"))])
        end
    end
    # Přerušené spuštění po restartu nesmí držet úschovu navždy.
    for job in db_rows("SELECT id, session_id FROM jobs WHERE state = 'RUNNING'"):
        call db_run("UPDATE jobs SET state = 'FAILED', failure = 'Execution interrupted by restart' WHERE id = ?", [String(job["id"])])
        call ledger_record(String(job["session_id"]), String(job["id"]), "EXECUTION_INTERRUPTED", 0)
    end
end

function ledger_record(session_id, job_id, action, amount):
    call db_run("INSERT INTO ledger(session_id,job_id,action,amount,created) VALUES(?,?,?,?,?)",
        [session_id, job_id, action, money(amount), stamp_now()])
end

function tables():
    return sqlite.tables(db_path())
end

# ---------- čtení ----------
function offers_list():
    items = []
    for row in db_rows("SELECT id,seller_id,name,price,capability,delivery,tier FROM offers WHERE active = 1 ORDER BY price,id"):
        entry = json.decode(json.encode(row))
        entry["currency"] = secrets["currency"]
        entry["service_name"] = String(services()[String(row["capability"])]["name"])
        items.append(entry)
    end
    return items
end

function job_view(row):
    entry = json.decode(json.encode(row))
    entry["contract"] = json.decode(String(row["contract"]))
    if row["result"] != None:
        entry["result"] = json.decode(String(row["result"]))
        entry["result_sha256"] = sha256_text(String(row["result"]))
    else:
        entry["result"] = None
    end
    return entry
end

function job_view_by_id(job_id):
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None:
        call problem(404, "job not found")
    end
    return job_view(row)
end

function session_view(session_id):
    row = db_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if row == None:
        call problem(404, "session not found")
    end
    entry = json.decode(json.encode(row))
    entry["wallet"] = db_one("SELECT * FROM wallets WHERE id = ?", [session_id])
    jobs = []
    for item in db_rows("SELECT * FROM jobs WHERE session_id = ? ORDER BY created", [session_id]):
        jobs.append(job_view(item))
    end
    ledger = []
    for item in db_rows("SELECT * FROM ledger WHERE session_id = ? ORDER BY id", [session_id]):
        ledger.append(json.decode(json.encode(item)))
    end
    entry["jobs"] = jobs
    entry["ledger"] = ledger
    entry["currency"] = secrets["currency"]
    entry["simulated_payments"] = True
    return entry
end

# ---------- zápis ----------
function create_session(payload):
    budget = payload.get("budget", None)
    title = String(payload.get("title", "Cart audit"))
    fixture = String(payload.get("fixture", "buggy"))
    service = String(payload.get("service", "auto"))
    if type(budget) != "Int" or budget < 1 or budget > 100:
        call problem(400, "budget must be an integer from 1 to 100 USD")
    end
    if len(title.strip()) < 1 or len(title.strip()) > 1000 or (fixture != "healthy" and fixture != "buggy"):
        call problem(400, "invalid session parameters")
    end
    if service != "auto" and service not in services():
        call problem(400, "invalid session parameters")
    end
    session_id = random_id("session-", 8)
    call db_run("INSERT INTO sessions(id,title,budget,fixture,created,service) VALUES(?,?,?,?,?,?)",
        [session_id, title, Int(budget), fixture, stamp_now(), service])
    call db_run("INSERT INTO wallets VALUES(?, 'buyer', ?, 0)", [session_id, Int(budget)])
    call ledger_record(session_id, None, "LUX_COINS_ISSUED", Int(budget))
    return session_view(session_id)
end

function make_contract(capability, task, tier):
    if capability != "http-cart-audit":
        return {"capability": capability, "currency": secrets["currency"], "task": task, "tier": tier,
            "delivery": String(services()[capability]["delivery"]),
            "verification": "Structure, syntax and seller attestation; no general semantic guarantee"}
    end
    # Náhodná čísla jako decimal (LSL Int() neumí jiný základ než 10).
    unit = 100 + random_below(900)
    quantity = 2 + random_below(4)
    return {"capability": "http-cart-audit", "currency": secrets["currency"], "task": task, "tier": tier, "cases": [
        {"id": "single_item", "unit_cents": unit, "quantity": 1, "expected_cents": unit},
        {"id": "quantity_update", "unit_cents": unit, "quantity": quantity, "expected_cents": unit * quantity},
        {"id": "empty_cart", "unit_cents": unit, "quantity": 0, "expected_cents": 0}
    ]}
end

function buy(payload):
    session_id = payload.get("session_id", None)
    offer_id = payload.get("offer_id", None)
    key = payload.get("idempotency_key", None)
    if type(session_id) != "String" or type(offer_id) != "String" or type(key) != "String":
        call problem(400, "session, offer and idempotency key are required")
    end
    if len(session_id) < 1 or len(session_id) > 100 or len(offer_id) < 1 or len(offer_id) > 100 or len(key) < 1 or len(key) > 100:
        call problem(400, "session, offer and idempotency key are required")
    end
    task = payload.get("task", None)
    if "task" in payload and (type(task) != "String" or len(task.strip()) < 1 or len(task.strip()) > 1000):
        call problem(400, "task must have 1 to 1000 characters")
    end
    existing = db_one("SELECT * FROM jobs WHERE session_id = ? AND idempotency_key = ?", [session_id, key])
    if existing != None:
        if String(existing["offer_id"]) != offer_id:
            call problem(409, "idempotency key already belongs to another offer")
        end
        contract = json.decode(String(existing["contract"]))
        if task != None and String(contract.get("task", "")) != String(task):
            call problem(409, "idempotency key already belongs to another task")
        end
        return job_view(existing)
    end
    session = db_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    offer = db_one("SELECT * FROM offers WHERE id = ? AND active = 1", [offer_id])
    if session == None or offer == None:
        call problem(404, "session or active offer not found")
    end
    if String(session["service"]) != "auto" and String(offer["capability"]) != String(session["service"]):
        call problem(409, "offer does not provide the requested service")
    end
    wallet = db_one("SELECT * FROM wallets WHERE id = ?", [session_id])
    price = Int(offer["price"])
    if Int(wallet["available"]) < price:
        call problem(409, "insufficient available USD")
    end
    job_id = random_id("job-", 8)
    wanted = String(session["title"])
    if task != None:
        wanted = String(task)
    end
    contract = make_contract(String(offer["capability"]), wanted, String(offer["tier"]))
    contract["task"] = wanted
    call db_run("UPDATE wallets SET available = available - ?, locked = locked + ? WHERE id = ?", [price, price, session_id])
    call db_run("INSERT INTO jobs VALUES(?,?,?,?,?,'FUNDED',?,NULL,NULL,?,?)",
        [job_id, session_id, offer_id, String(offer["seller_id"]), price, encoded(contract), key, stamp_now()])
    call ledger_record(session_id, job_id, "ESCROW_LOCKED", price)
    return job_view_by_id(job_id)
end

function execute_job(job_id):
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None:
        call problem(404, "job not found")
    end
    state = String(row["state"])
    if state == "DELIVERED" or state == "PAID" or state == "REFUNDED":
        return job_view(row)
    end
    if state != "FUNDED":
        call problem(409, "job is already executing or execution failed")
    end
    call db_run("UPDATE jobs SET state = 'RUNNING' WHERE id = ?", [job_id])
    outgoing = job_view(row)
    seller = sellers[String(row["seller_id"])]
    result = None
    failure = None
    try:
        response = requests.post(String(seller["url"]) + "/execute", outgoing,
            {"Authorization": "Bearer " + String(seller["token"])}, 135)
        if Int(response.status) != 200:
            failure = "Seller unavailable or invalid delivery"
        else:
            body = response.text
            if len(body) > 65536:
                failure = "Seller unavailable or invalid delivery"
            else:
                try:
                    result = json.decode(body)
                else:
                    result = None
                end
                if type(result) != "Dictionary":
                    failure = "Seller unavailable or invalid delivery"
                end
            end
        end
    else:
        failure = "Seller unavailable or invalid delivery"
    end
    encoded_result = None
    if result != None and failure == None:
        encoded_result = encoded(result)
    end
    final_state = "DELIVERED"
    if failure != None:
        final_state = "FAILED"
    end
    call db_run("UPDATE jobs SET state = ?, result = ?, failure = ? WHERE id = ?",
        [final_state, encoded_result, failure, job_id])
    if failure != None:
        call ledger_record(String(row["session_id"]), job_id, "DELIVERY_FAILED", 0)
    else:
        call ledger_record(String(row["session_id"]), job_id, "RESULT_DELIVERED", 0)
    end
    return job_view_by_id(job_id)
end

function top_up(session_id, amount):
    if type(amount) != "Int" or amount < 1 or amount > 1000:
        call problem(400, "top-up must be an integer from 1 to 1000 USD")
    end
    wallet = db_one("SELECT * FROM wallets WHERE id = ?", [session_id])
    if wallet == None:
        call problem(404, "session not found")
    end
    if Int(wallet["available"]) + Int(wallet["locked"]) + amount > 10000:
        call problem(409, "wallet balance limit reached")
    end
    call db_run("UPDATE wallets SET available = available + ? WHERE id = ?", [amount, session_id])
    call db_run("UPDATE sessions SET budget = budget + ? WHERE id = ?", [amount, session_id])
    call ledger_record(session_id, None, "TOPUP_ISSUED", amount)
    return session_view(session_id)
end


# ---------- kanonický JSON a časy shodné s Pythonem ----------
function string_less(left, right):
    a = String(left)
    b = String(right)
    limit = len(a)
    if len(b) < limit:
        limit = len(b)
    end
    index = 0
    while index < limit:
        x = ord(a[index])
        y = ord(b[index])
        if x != y:
            return x < y
        end
        index += 1
    end
    return len(a) < len(b)
end

function sorted_keys(value):
    keys = []
    for key in value:
        keys.append(key)
    end
    index = 0
    while index < len(keys):
        best = index
        scan = index + 1
        while scan < len(keys):
            if string_less(keys[scan], keys[best]):
                best = scan
            end
            scan += 1
        end
        swap = keys[index]
        keys[index] = keys[best]
        keys[best] = swap
        index += 1
    end
    return keys
end

function canonical(value):
    # Rekurzivně seřazené klíče; stejné bajty jako json.dumps(sort_keys=True,
    # separators=(",", ":")), takže hashe i podpisy zůstávají ověřitelné Pythonem.
    if type(value) == "Dictionary":
        result = {}
        for key in sorted_keys(value):
            result[key] = canonical(value[key])
        end
        return result
    elif type(value) == "List":
        result = []
        for item in value:
            result.append(canonical(item))
        end
        return result
    end
    return value
end

function iso_sql(column):
    # ISO čas spočítaný SQLite (plná přesnost); LSL floaty drží méně míst.
    return "strftime('%Y-%m-%dT%H:%M:%S', " + column + ", 'unixepoch') || '.' || substr('000000' || CAST(CAST(round((" + column + " - CAST(" + column + " AS INTEGER)) * 1000000) AS INTEGER) AS TEXT), -6) || '+00:00'"
end

function canonical_json(value):
    return json.encode(canonical(value))
end

function iso_utc(stamp):
    seconds = Int(stamp)
    fraction = Float(stamp) - Float(seconds)
    micros = Int(fraction * 1000000.0 + 0.5)
    if micros > 999999:
        micros = 999999
    end
    tail = String(micros)
    while len(tail) < 6:
        tail = "0" + tail
    end
    return format_time(seconds) + "." + tail + "+00:00"
end

function format_time(seconds):
    outcome = process.capture_result("/bin/date", ["-u", "-d", "@" + String(seconds), "+%Y-%m-%dT%H:%M:%S"])
    if not outcome["ok"]:
        Error(MarketError: "date formatting failed")
    end
    return String(outcome["stdout"]).strip()
end

# ---------- ověření dodávky ----------
function python_syntax_ok(code):
    outcome = process.capture_input_result("/usr/bin/python3", ["-c", "import ast,sys; ast.parse(sys.stdin.read())"], code)
    return outcome["ok"]
end

function validate_artifact(capability, artifact, sources):
    reasons = []
    if type(artifact) != "Dictionary":
        return ["Missing structured artifact"]
    end
    summary = artifact.get("summary", None)
    if type(summary) != "String" or len(String(summary).strip()) < 10 or len(String(summary).strip()) > 2000:
        reasons.append("Missing useful summary")
    end
    if capability == "python-code":
        for field in ["code", "tests"]:
            text = artifact.get(field, None)
            if type(text) != "String" or len(String(text)) < 20 or len(String(text)) > 18000:
                reasons.append("Missing Python " + field)
                continue
            end
            if not python_syntax_ok(String(text)):
                reasons.append("Invalid Python syntax: " + field)
                continue
            end
            if field == "code" and String(text).find("def ") < 0 and String(text).find("class ") < 0:
                reasons.append("Python code must define a function or class")
            end
            if field == "tests" and String(text).find("assert") < 0:
                reasons.append("Tests must include an assertion")
            end
        end
    elif capability == "ideas":
        ideas = artifact.get("ideas", None)
        if type(ideas) != "List" or len(ideas) != 5:
            reasons.append("Exactly five explained ideas are required")
        else:
            for idea in ideas:
                if type(idea) != "String" or len(String(idea)) < 15 or len(String(idea)) > 1500:
                    reasons.append("Exactly five explained ideas are required")
                end
            end
        end
    else:
        content = artifact.get("content", None)
        if type(content) != "String" or len(String(content).strip()) < 30 or len(String(content).strip()) > 18000:
            reasons.append("Missing useful text delivery")
        end
        if capability == "short-research":
            citations = artifact.get("sources", None)
            good = True
            if type(citations) != "List" or len(citations) < 2 or len(citations) > 5:
                good = False
            else:
                for citation in citations:
                    if type(citation) != "Dictionary":
                        good = False
                        continue
                    end
                    title = String(citation.get("title", "")).strip()
                    url = String(citation.get("url", ""))
                    found = False
                    for source in sources:
                        if String(source["url"]) == url:
                            found = True
                        end
                    end
                    if title == "" or url == "" or not found:
                        good = False
                    end
                end
            end
            if not good:
                reasons.append("At least two citations must match actually fetched sources")
            end
        end
    end
    return reasons
end

function verification(job_id):
    entry = job_view_by_id(job_id)
    reasons = []
    result = entry["result"]
    if result == None:
        return {"valid_delivery": False, "reasons": ["No delivery"], "checks": []}
    end
    capability = String(entry["contract"]["capability"])
    if capability != "http-cart-audit":
        artifact = result.get("artifact", None)
        sources = []
        for row in db_rows("SELECT url,title,extract FROM research_sources WHERE job_id = ?", [job_id]):
            sources.append(json.decode(json.encode(row)))
        end
        receipt = db_one("SELECT * FROM service_receipts WHERE job_id = ?", [job_id])
        reasons = validate_artifact(capability, artifact, sources)
        if String(result.get("job_id", "")) != job_id or String(result.get("seller_id", "")) != String(entry["seller_id"]) or String(result.get("capability", "")) != capability:
            reasons.append("Delivery identity or service mismatch")
        end
        digest = sha256_text(canonical_json(artifact))
        if receipt == None or String(receipt["id"]) != String(result.get("receipt_id", "")) or String(receipt["artifact_sha256"]) != digest:
            reasons.append("Missing or mismatched seller attestation")
        end
        return {"valid_delivery": len(reasons) == 0, "reasons": reasons, "checks": [], "capability": capability,
            "scope": "Structure, Python syntax and cited source fetches; seller attestation is not proof of general semantic quality"}
    end
    checks = result.get("checks", None)
    if type(checks) != "List" or len(checks) > 10:
        return {"valid_delivery": False, "reasons": ["Invalid checks"], "checks": []}
    end
    if String(result.get("job_id", "")) != job_id or String(result.get("seller_id", "")) != String(entry["seller_id"]):
        reasons.append("Delivery identity mismatch")
    end
    receipts = {}
    for row in db_rows("SELECT * FROM receipts WHERE job_id = ?", [job_id]):
        receipts[String(row["id"])] = row
    end
    seen = []
    for expected in entry["contract"]["cases"]:
        case_id = String(expected["id"])
        matching = []
        for check in checks:
            if type(check) == "Dictionary" and String(check.get("case_id", "")) == case_id:
                matching.append(check)
            end
        end
        if len(matching) != 1:
            reasons.append("Missing or duplicate check: " + case_id)
            continue
        end
        check = matching[0]
        seen.append(case_id)
        receipt = None
        if type(check.get("receipt_id", None)) == "String":
            receipt = receipts.get(String(check["receipt_id"]), None)
        end
        if receipt == None or String(receipt["case_id"]) != case_id:
            reasons.append("Missing execution receipt: " + case_id)
            continue
        end
        observed = Int(check.get("observed_cents", -1))
        expected_cents = Int(expected["expected_cents"])
        passed = Bool(check.get("passed", None))
        if type(check.get("expected_cents", None)) != "Int" or Int(check["expected_cents"]) != expected_cents or type(check.get("observed_cents", None)) != "Int" or observed != Int(receipt["total_cents"]) or type(check.get("passed", None)) != "Bool" or passed != (Int(receipt["total_cents"]) == expected_cents):
            reasons.append("Invalid observation: " + case_id)
        end
    end
    if len(checks) != len(seen):
        reasons.append("Unexpected checks")
    end
    return {"valid_delivery": len(reasons) == 0, "reasons": reasons, "checks": checks,
        "scope": "Completeness and receipts; finding a shop bug is a valid delivery"}
end

function finish(job_id, refund):
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None:
        call problem(404, "job not found")
    end
    final = "PAID"
    if refund:
        final = "REFUNDED"
    end
    if String(row["state"]) == final:
        return job_view(row)
    end
    state = String(row["state"])
    if state != "DELIVERED" and state != "FAILED":
        call problem(409, "job cannot be settled in its current state")
    end
    verdict = verification(job_id)
    valid = Bool(verdict["valid_delivery"])
    if refund and valid:
        call problem(409, "complete, verifiable delivery cannot be refunded automatically")
    end
    if not refund and not valid:
        call problem(409, "incomplete or unverifiable delivery cannot be paid")
    end
    price = Int(row["price"])
    session_id = String(row["session_id"])
    if refund:
        call db_run("UPDATE wallets SET locked = locked - ?, available = available + ? WHERE id = ?", [price, price, session_id])
    else:
        call db_run("UPDATE wallets SET locked = locked - ? WHERE id = ?", [price, session_id])
        call db_run("UPDATE wallets SET available = available + ? WHERE id = ?", [price, String(row["seller_id"])])
    end
    call db_run("UPDATE jobs SET state = ? WHERE id = ?", [final, job_id])
    if refund:
        call ledger_record(session_id, job_id, "REFUND_AUTHORIZED_BY_CONTRACT", price)
    else:
        call ledger_record(session_id, job_id, "PAYMENT_RELEASED", price)
    end
    call sign_settlement(job_id)
    return job_view_by_id(job_id)
end

function sandbox(payload, token):
    seller = seller_for_token(token)
    job_id = String(payload.get("job_id", ""))
    case_id = String(payload.get("case_id", ""))
    if job_id == "" or case_id == "":
        call problem(400, "invalid sandbox request")
    end
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None or String(row["seller_id"]) != String(seller["id"]):
        call problem(403, "seller does not own this job")
    end
    if String(row["state"]) != "RUNNING":
        call problem(409, "sandbox execution requires a running funded job")
    end
    contract = json.decode(String(row["contract"]))
    if String(contract["capability"]) != "http-cart-audit":
        call problem(409, "this job does not provide cart audit")
    end
    test_case = None
    for candidate in contract["cases"]:
        if String(candidate["id"]) == case_id:
            test_case = candidate
        end
    end
    if test_case == None:
        call problem(400, "case outside agreed contract")
    end
    existing = db_one("SELECT * FROM receipts WHERE job_id = ? AND case_id = ?", [job_id, case_id])
    if existing != None:
        return {"receipt_id": String(existing["id"]), "total_cents": Int(existing["total_cents"])}
    end
    fixture = String(db_one("SELECT fixture FROM sessions WHERE id = ?", [String(row["session_id"])])["fixture"])
    quantity = Int(test_case["quantity"])
    unit = Int(test_case["unit_cents"])
    billed = quantity
    if fixture == "buggy" and quantity > 1:
        billed = 1
    end
    total = unit * billed
    receipt_id = random_id("receipt-", 12)
    call db_run("INSERT INTO receipts VALUES(?,?,?,?)", [receipt_id, job_id, case_id, total])
    return {"receipt_id": receipt_id, "total_cents": total}
end

function provider_job(job_id, token):
    seller = seller_for_token(token)
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None or String(row["seller_id"]) != String(seller["id"]):
        call problem(403, "seller does not own this job")
    end
    if String(row["state"]) != "RUNNING":
        call problem(409, "provider operation requires a running job")
    end
    return job_view(row)
end

function attest(payload, token):
    entry = provider_job(String(payload.get("job_id", "")), token)
    if String(entry["contract"]["capability"]) == "http-cart-audit":
        call problem(409, "cart audit uses execution receipts")
    end
    artifact = payload.get("artifact", None)
    if type(artifact) != "Dictionary" or len(canonical_json(artifact)) > 50000:
        call problem(400, "invalid artifact")
    end
    digest = sha256_text(canonical_json(artifact))
    existing = db_one("SELECT * FROM service_receipts WHERE job_id = ?", [String(entry["id"])])
    if existing != None:
        if String(existing["artifact_sha256"]) != digest:
            call problem(409, "a different delivery is already attested")
        end
        return {"receipt_id": String(existing["id"]), "artifact_sha256": digest}
    end
    receipt_id = random_id("delivery-", 12)
    call db_run("INSERT INTO service_receipts VALUES(?,?,?,?,?)", [receipt_id, String(entry["id"]), digest, "flash", stamp_now()])
    return {"receipt_id": receipt_id, "artifact_sha256": digest}
end

function report_progress(payload, token):
    delta = String(payload.get("delta", ""))
    if len(delta) < 1 or len(delta) > 2000:
        call problem(400, "invalid preview delta")
    end
    entry = provider_job(String(payload.get("job_id", "")), token)
    current = String(previews.get(String(entry["id"]), ""))
    if len(current) + len(delta) > 60000:
        call problem(400, "preview limit exceeded")
    end
    previews[String(entry["id"])] = current + delta
    return {"ok": True}
end

function research(payload, token):
    query = String(payload.get("query", "")).strip()
    if len(query) < 1 or len(query) > 200:
        call problem(400, "research query must have 1 to 200 characters")
    end
    entry = provider_job(String(payload.get("job_id", "")), token)
    if String(entry["contract"]["capability"]) != "short-research":
        call problem(409, "this job does not provide research")
    end
    existing = []
    for row in db_rows("SELECT url,title,extract FROM research_sources WHERE job_id = ?", [String(entry["id"])]):
        existing.append(json.decode(json.encode(row)))
    end
    if len(existing) > 0:
        return {"sources": existing, "scope": "Wikipedia article introductions"}
    end
    sources = apify_research(query)
    scope = "Live web pages fetched with the Apify RAG Web Browser (query is data, not a URL)"
    if len(sources) < 2:
        sources = wikipedia_research(query)
        scope = "Wikipedia article introductions (Apify fallback)"
    end
    if len(sources) < 2:
        call problem(422, "research needs at least two matching sources; use a broader topic")
    end
    for source in sources:
        call db_run("INSERT OR IGNORE INTO research_sources VALUES(?,?,?,?)",
            [String(entry["id"]), String(source["url"]), String(source["title"]), String(source["extract"])])
    end
    return {"sources": sources, "scope": scope}
end

function apify_research(query):
    token = apify_token()
    if token == "":
        return []
    end
    body = json.encode({"query": query, "maxResults": 3})
    response = requests.post("https://api.apify.com/v2/acts/apify~rag-web-browser/run-sync-get-dataset-items?maxTotalChargeUsd=0.50",
        body, {"Content-Type": "application/json", "Authorization": "Bearer " + token}, 90)
    items = None
    if Int(response.status) == 200:
        try:
            items = json.decode(response.text)
        else:
            items = None
        end
    end
    if type(items) != "List":
        return []
    end
    sources = []
    seen = []
    for item in items:
        if type(item) != "Dictionary":
            continue
        end
        metadata = item.get("metadata", None)
        if type(metadata) != "Dictionary":
            metadata = {}
        end
        url = String(metadata.get("url", metadata.get("canonicalUrl", item.get("url", ""))))
        title = String(metadata.get("title", ""))
        if title == "":
            search = item.get("searchResult", None)
            if type(search) == "Dictionary":
                title = String(search.get("title", ""))
            end
        end
        extract = String(item.get("markdown", item.get("text", "")))
        if not url.startswith("https://") or title.strip() == "" or extract.strip() == "":
            continue
        end
        if url in seen:
            continue
        end
        seen.append(url)
        entry = {"title": title.strip()}
        entry["title"] = title.strip()[0:300]
        entry["url"] = url
        entry["extract"] = extract.strip()[0:1800]
        sources.append(entry)
    end
    kept = []
    index = 0
    while index < len(sources) and index < 3:
        kept.append(sources[index])
        index += 1
    end
    return kept
end

function wikipedia_research(query):
    outcome = process.capture_result("/usr/bin/curl", ["-sS", "-m", "20", "-G",
        "-H", "User-Agent: ProofPayResearchMVP/1.0",
        "--data-urlencode", "action=query", "--data-urlencode", "generator=search",
        "--data-urlencode", "gsrsearch=" + query, "--data-urlencode", "gsrlimit=3",
        "--data-urlencode", "prop=extracts", "--data-urlencode", "exintro=1",
        "--data-urlencode", "explaintext=1", "--data-urlencode", "exchars=900",
        "--data-urlencode", "format=json", "https://en.wikipedia.org/w/api.php"])
    if not outcome["ok"]:
        return []
    end
    data = None
    try:
        data = json.decode(String(outcome["stdout"]))
    else:
        data = None
    end
    if type(data) != "Dictionary":
        return []
    end
    pages = data.get("query", {}).get("pages", {})
    sources = []
    for key in sorted_keys(pages):
        page = pages[key]
        extract = String(page.get("extract", ""))
        if extract == "":
            continue
        end
        entry = {"title": String(page["title"])}
        entry["url"] = "https://en.wikipedia.org/wiki/" + String(page["title"]).replace(" ", "_")
        entry["extract"] = extract[0:1800]
        sources.append(entry)
    end
    return sources
end


function ledger_labels(action):
    mapping = {
        "LUX_COINS_ISSUED": ["Test credits issued to the buyer wallet", "issue"],
        "TOPUP_ISSUED": ["Wallet topped up with simulated USD", "issue"],
        "STRIPE_TOPUP": ["Card payment in Stripe test mode credited as USD", "issue"],
        "ESCROW_LOCKED": ["Funds locked in escrow for the job", "escrow"],
        "RESULT_DELIVERED": ["Seller delivery recorded by the marketplace", "delivery"],
        "DELIVERY_FAILED": ["Seller delivery failed or was invalid", "failure"],
        "PAYMENT_RELEASED": ["Escrow released to the seller after verification", "payment"],
        "REFUND_AUTHORIZED_BY_CONTRACT": ["Escrow returned to the buyer by contract", "refund"],
        "EXECUTION_INTERRUPTED": ["Execution interrupted (restart); job settled separately", "failure"]
    }
    if action in mapping:
        return mapping[action]
    end
    return [String(action).replace("_", " ").capitalize(), "other"]
end

function payment(job_id):
    row = db_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if row == None:
        call problem(404, "job not found")
    end
    entry = job_view(row)
    transactions = []
    for item in db_rows("SELECT *, " + iso_sql("created") + " AS created_at FROM ledger WHERE job_id = ? AND action IN ('ESCROW_LOCKED','PAYMENT_RELEASED','REFUND_AUTHORIZED_BY_CONTRACT') ORDER BY id", [job_id]):
        released = String(item["action"]) == "PAYMENT_RELEASED"
        locked = String(item["action"]) == "ESCROW_LOCKED"
        transaction = {"id": "lux-tx-" + String(item["id"]), "ledger_id": Int(item["id"]),
            "action": String(item["action"]), "amount": Int(item["amount"]), "currency": secrets["currency"],
            "created": Float(item["created"]), "created_at": String(item["created_at"]),
            "from_wallet": String(entry["session_id"]), "from_account": "locked",
            "to_wallet": String(entry["session_id"]), "to_account": "available",
            "job_id": job_id, "session_id": String(entry["session_id"])}
        if locked:
            transaction["from_account"] = "available"
            transaction["to_account"] = "locked"
        end
        if released:
            transaction["to_wallet"] = String(entry["seller_id"])
            transaction["from_account"] = "locked"
            transaction["to_account"] = "available"
        end
        transactions.append(transaction)
    end
    receipts = []
    for item in db_rows("SELECT id FROM receipts WHERE job_id = ? ORDER BY id", [job_id]):
        receipts.append(String(item["id"]))
    end
    for item in db_rows("SELECT id FROM service_receipts WHERE job_id = ?", [job_id]):
        receipts.append(String(item["id"]))
    end
    return {"job_id": job_id, "session_id": String(entry["session_id"]), "offer_id": String(entry["offer_id"]),
        "seller_id": String(entry["seller_id"]), "amount": Int(entry["price"]), "currency": secrets["currency"],
        "state": String(entry["state"]), "idempotency_key": String(entry["idempotency_key"]),
        "transactions": transactions, "verification_receipts": receipts,
        "result_sha256": String(entry.get("result_sha256", "")),
        "contract_sha256": sha256_text(canonical_json(entry["contract"])),
        "simulated_payments": True,
        "ledger": "Central marketplace SQLite ledger; IDs are scoped to this marketplace"}
end

function settlement_payload(job_id):
    entry = job_view_by_id(job_id)
    pay = payment(job_id)
    settled_at = None
    moves = []
    for transaction in pay["transactions"]:
        action = String(transaction["action"])
        if action == "PAYMENT_RELEASED" or action == "REFUND_AUTHORIZED_BY_CONTRACT":
            if settled_at == None:
                settled_at = String(transaction["created_at"])
            end
        end
        moves.append({"id": String(transaction["id"]), "action": action,
            "amount": Int(transaction["amount"]), "created_at": String(transaction["created_at"])})
    end
    return {"payload_version": "1.0", "job_id": String(entry["id"]), "session_id": String(entry["session_id"]),
        "seller_id": String(entry["seller_id"]), "offer_id": String(entry["offer_id"]), "amount": Int(entry["price"]),
        "currency": secrets["currency"], "state": String(entry["state"]), "idempotency_key": String(entry["idempotency_key"]),
        "contract_sha256": String(pay["contract_sha256"]), "result_sha256": String(pay["result_sha256"]),
        "settled_at": settled_at, "transactions": moves, "verification_receipts": pay["verification_receipts"]}
end

function sign_settlement(job_id):
    if secrets["signing_key_file"] == "" or secrets["signing_key_id"] == "":
        return
    end
    payload = canonical_json(settlement_payload(job_id))
    stamp = String(Int(time.time() * 1000.0))
    payload_path = "/tmp/pp-sign-" + stamp + ".json"
    signature_path = "/tmp/pp-sign-" + stamp + ".bin"
    call writefile(payload_path, payload)
    signed = process.capture_result("/usr/bin/openssl",
        ["pkeyutl", "-sign", "-inkey", secrets["signing_key_file"], "-rawin", "-in", payload_path, "-out", signature_path])
    if not signed["ok"]:
        return
    end
    # binární podpis se čte přes base64 ze souboru (LSL řetězce nejsou na binárky)
    encoded_signature = process.capture_result("/usr/bin/openssl", ["base64", "-A", "-in", signature_path])
    call process.capture_result("/bin/rm", ["-f", payload_path, signature_path])
    if not encoded_signature["ok"]:
        return
    end
    signature = String(encoded_signature["stdout"]).strip()
    if signature == "":
        return
    end
    digest = sha256_text(payload)
    call db_run("INSERT OR REPLACE INTO signatures(job_id, alg, public_key_id, signature, payload, payload_sha256, created) VALUES(?,?,?,?,?,?,?)",
        [job_id, "ed25519-sha256", secrets["signing_key_id"], signature, payload, digest, stamp_now()])
end

function base64_encode(binary):
    outcome = process.capture_input_result("/usr/bin/openssl", ["base64", "-A"], binary)
    if not outcome["ok"]:
        return ""
    end
    return String(outcome["stdout"]).strip()
end

function stripe_request(method, path, params):
    if secrets["stripe_key"] == "":
        call problem(503, "Stripe is not configured on this marketplace")
    end
    arguments = ["-sS", "-m", "40", "-X", method, "-H", "Authorization: Bearer " + stripe_key]
    for name in params:
        arguments.append("-d")
        arguments.append(String(name) + "=" + String(params[name]))
    end
    arguments.append("https://api.stripe.com/v1/" + path)
    outcome = process.capture_result("/usr/bin/curl", arguments)
    if not outcome["ok"]:
        call problem(502, "Stripe request failed")
    end
    try:
        return json.decode(String(outcome["stdout"]))
    else:
        call problem(502, "Stripe request failed")
    end
end

function stripe_amount(amount_usd):
    if type(amount_usd) != "Int" or amount_usd < 1 or amount_usd > 25:
        call problem(400, "card amount must be a whole number of US dollars from 1 to 25")
    end
    return [amount_usd * 100, amount_usd]
end

function stripe_checkout(session_id, amount_usd):
    amounts = stripe_amount(amount_usd)
    cents = amounts[0]
    coins = amounts[1]
    wallet = db_one("SELECT * FROM wallets WHERE id = ?", [session_id])
    if wallet == None:
        call problem(404, "session not found")
    end
    created = stripe_request("POST", "checkout/sessions", {
        "mode": "payment",
        "success_url": secrets["stripe_success_url"] + "&sc={CHECKOUT_SESSION_ID}",
        "cancel_url": secrets["stripe_cancel_url"],
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": String(cents),
        "line_items[0][price_data][product_data][name]": "Wallet top-up (Stripe test): " + String(coins) + " USD",
        "metadata[session_id]": session_id
    })
    if type(created) != "Dictionary" or String(created.get("id", "")) == "":
        call problem(502, "Stripe did not create a checkout session")
    end
    call db_run("INSERT INTO stripe_payments(id,session_id,lux_coins,amount_cents,currency,kind,status,created,credited) VALUES(?,?,?,?,?,?,?,?,NULL)",
        [String(created["id"]), session_id, coins, cents, "usd", "checkout", String(created.get("status", "open")), stamp_now()])
    url = ""
    if String(created.get("url", "")) != "":
        url = String(created["url"])
    end
    return {"checkout_url": url, "stripe_session_id": String(created["id"]), "lux_coins": coins,
        "amount_cents": cents, "currency": "usd", "mode": "stripe-test"}
end

function stripe_sandbox_pay(session_id, amount_usd):
    amounts = stripe_amount(amount_usd)
    cents = amounts[0]
    coins = amounts[1]
    wallet = db_one("SELECT * FROM wallets WHERE id = ?", [session_id])
    if wallet == None:
        call problem(404, "session not found")
    end
    if not secrets["stripe_key"].startswith("sk_test_"):
        call problem(409, "the sandbox card path is available only with a test key")
    end
    intent = stripe_request("POST", "payment_intents", {
        "amount": String(cents), "currency": "usd", "payment_method": "pm_card_visa", "confirm": "true",
        "automatic_payment_methods[enabled]": "true", "automatic_payment_methods[allow_redirects]": "never",
        "description": "Wallet top-up (Stripe test): " + String(coins) + " USD",
        "metadata[session_id]": session_id
    })
    if type(intent) != "Dictionary" or String(intent.get("id", "")) == "":
        call problem(502, "Stripe did not create a payment")
    end
    call db_run("INSERT INTO stripe_payments(id,session_id,lux_coins,amount_cents,currency,kind,status,created,credited) VALUES(?,?,?,?,?,?,?,?,NULL)",
        [String(intent["id"]), session_id, coins, cents, "usd", "payment_intent", String(intent.get("status", "unknown")), stamp_now()])
    return {"stripe_session_id": String(intent["id"]), "status": String(intent.get("status", ""))}
end

function stripe_confirm(stripe_id):
    if type(stripe_id) != "String" or len(stripe_id) < 6:
        call problem(400, "stripe_session_id is required")
    end
    record_row = db_one("SELECT * FROM stripe_payments WHERE id = ?", [stripe_id])
    if record_row == None:
        call problem(404, "unknown Stripe payment")
    end
    paid = False
    if String(record_row["kind"]) == "payment_intent":
        remote = stripe_request("GET", "payment_intents/" + stripe_id, {})
        paid = String(remote.get("status", "")) == "succeeded"
    else:
        remote = stripe_request("GET", "checkout/sessions/" + stripe_id, {})
        paid = String(remote.get("payment_status", "")) == "paid"
    end
    if not paid:
        call problem(409, "this Stripe payment is not completed yet")
    end
    if record_row["credited"] != None:
        return {"credited": False, "reason": "already credited", "lux_coins": Int(record_row["lux_coins"]),
            "session_id": String(record_row["session_id"])}
    end
    session_id = String(record_row["session_id"])
    coins = Int(record_row["lux_coins"])
    call db_run("UPDATE wallets SET available = available + ? WHERE id = ?", [coins, session_id])
    call db_run("UPDATE sessions SET budget = budget + ? WHERE id = ?", [coins, session_id])
    call ledger_record(session_id, None, "STRIPE_TOPUP", coins)
    call db_run("UPDATE stripe_payments SET status = 'paid', credited = ? WHERE id = ?", [stamp_now(), stripe_id])
    return {"credited": True, "lux_coins": coins, "session_id": session_id, "stripe_reference": stripe_id,
        "mode": "stripe-test", "payment": "card payment in Stripe test mode"}
end


function apify_token():
    path = String(config.get("apify_token_file", ""))
    if path == "":
        return ""
    end
    try:
        return readfile(path).strip()
    else:
        return ""
    end
end

function dashboard():
    ids = []
    for row in db_rows("SELECT id FROM sessions ORDER BY created DESC LIMIT 30"):
        ids.append(String(row["id"]))
    end
    seller_wallets = []
    for row in db_rows("SELECT * FROM wallets WHERE kind = 'seller'"):
        seller_wallets.append(json.decode(json.encode(row)))
    end
    minted = Int(db_one("SELECT COALESCE(SUM(budget),0) AS total FROM sessions")["total"])
    held = Int(db_one("SELECT COALESCE(SUM(available + locked),0) AS total FROM wallets")["total"])
    sessions = []
    for id in ids:
        sessions.append(session_view(id))
    end
    return {"currency": secrets["currency"], "simulated_payments": True, "services": services(), "offers": offers_list(),
        "sessions": sessions, "sellers": seller_wallets,
        "invariant": {"issued": minted, "accounted": held, "holds": minted == held}}
end

function receipt(job_id):
    if type(job_id) != "String" or len(job_id) < 1 or len(job_id) > 100:
        call problem(400, "invalid job id")
    end
    entry = job_view_by_id(job_id)
    pay = payment(job_id)
    session_row = db_one("SELECT * FROM sessions WHERE id = ?", [String(entry["session_id"])])
    if session_row == None:
        call problem(404, "session not found")
    end
    wallet = db_one("SELECT * FROM wallets WHERE id = ?", [String(entry["session_id"])])
    seller_wallet = db_one("SELECT * FROM wallets WHERE id = ?", [String(entry["seller_id"])])
    signature = db_one("SELECT *, " + iso_sql("created") + " AS created_at FROM signatures WHERE job_id = ?", [job_id])
    minted = Int(db_one("SELECT COALESCE(SUM(budget),0) AS total FROM sessions")["total"])
    held = Int(db_one("SELECT COALESCE(SUM(available + locked),0) AS total FROM wallets")["total"])
    available = 0
    locked = 0
    timeline = []
    job_moves = []
    credits = 0
    for row in db_rows("SELECT *, " + iso_sql("created") + " AS created_at FROM ledger WHERE session_id = ? ORDER BY id", [String(entry["session_id"])]):
        action = String(row["action"])
        amount = Int(row["amount"])
        if action == "LUX_COINS_ISSUED" or action == "TOPUP_ISSUED" or action == "STRIPE_TOPUP":
            available += amount
        elif action == "ESCROW_LOCKED":
            available -= amount
            locked += amount
        elif action == "PAYMENT_RELEASED":
            locked -= amount
        elif action == "REFUND_AUTHORIZED_BY_CONTRACT":
            locked -= amount
            available += amount
        end
        labels = ledger_labels(action)
        move = {"id": "lux-tx-" + String(row["id"]), "ledger_id": Int(row["id"]), "job_id": row["job_id"],
            "action": action, "label": String(labels[0]), "kind": String(labels[1]), "amount": amount,
            "available_after": available, "locked_after": locked, "created": Float(row["created"]),
            "created_at": String(row["created_at"])}
        timeline.append(move)
        if row["job_id"] != None and String(row["job_id"]) == job_id:
            job_moves.append(move)
            kind = String(labels[1])
            if kind == "escrow" or kind == "payment" or kind == "refund":
                credits += amount
            end
        end
    end
    escrow_locked = 0
    settled = 0
    for move in job_moves:
        kind = String(move["kind"])
        if kind == "escrow":
            escrow_locked += Int(move["amount"])
        elif kind == "payment" or kind == "refund":
            settled += Int(move["amount"])
        end
    end
    price = Int(entry["price"])
    signature_view = None
    if signature != None:
        signature_view = {"alg": String(signature["alg"]), "key_id": String(signature["public_key_id"]),
            "value_base64": String(signature["signature"]), "payload": String(signature["payload"]),
            "payload_sha256": String(signature["payload_sha256"]),
            "created_at": iso_utc(Float(signature["created"]))}
    end
    public_key = None
    if secrets["signing_public_pem"] != "":
        public_key = {"id": secrets["signing_key_id"], "alg": "Ed25519", "public_key_pem": secrets["signing_public_pem"]}
    end
    return {"receipt_version": "1.0", "generated_at": iso_utc(stamp_now()), "simulated_payments": True, "currency": secrets["currency"],
        "job": entry, "payment": pay, "verification": verification(job_id),
        "session": {"id": String(session_row["id"]), "title": String(session_row["title"]),
            "budget": Int(session_row["budget"]), "fixture": String(session_row["fixture"]),
            "service": String(session_row["service"]), "created": Float(session_row["created"]),
            "created_at": iso_utc(Float(session_row["created"]))},
        "wallets": {"buyer": wallet, "seller": seller_wallet},
        "timeline": timeline, "job_timeline": job_moves,
        "reconciliation": {"escrow_locked": escrow_locked, "settled": settled, "price": price,
            "balanced": escrow_locked == settled and settled == price and credits >= 0, "movements": len(job_moves)},
        "signature": signature_view, "public_key": public_key,
        "invariant": {"issued": minted, "accounted": held, "holds": minted == held}}
end


# ---------- HTTP vrstva ----------
function send_json(client, status, value):
    call web.send_response(client, status, web.status_reason(status), json.encode(value), "application/json; charset=utf-8", False)
end

function send_text(client, status, body, mime):
    call web.send_response(client, status, web.status_reason(status), body, mime, False)
end

function parse_body(request):
    payload = None
    if type(request.get("body_text", None)) == "String":
        try:
            payload = json.decode(String(request["body_text"]))
        else:
            payload = None
        end
    end
    if type(payload) != "Dictionary":
        call problem(400, "JSON object required")
    end
    return payload
end

function route(client, request):
    method = String(request["method"])
    raw = String(request["target"])
    query = raw.find("?")
    path = raw
    if query >= 0:
        path = raw[0:query]
    end
    header = String(request["headers"].get("authorization", "")).strip()
    token = ""
    if header.startswith("Bearer "):
        token = header[7:len(header)].strip()
    end
    if method == "GET":
        if path == "/health":
            call send_json(client, 200, {"ok": True, "payments": "simulated USD"})
            return
        end
        if path == "/.well-known/proofpay-keys.json":
            keys = []
            if secrets["signing_public_pem"] != "":
                keys.append({"id": secrets["signing_key_id"], "alg": "Ed25519", "public_key_pem": secrets["signing_public_pem"]})
            end
            call send_json(client, 200, {"keys": keys})
            return
        end
        if path == "/api/dashboard":
            call send_json(client, 200, dashboard())
            return
        end
        if path == "/api/offers":
            call send_json(client, 200, {"offers": offers_list()})
            return
        end
        if path == "/api/services":
            call send_json(client, 200, {"services": services(), "currency": secrets["currency"]})
            return
        end
        if path == "/api/ledger/check":
            # Kanonický řetěz (formát floatů) umí jen Python; LSL hlásí totéž,
            # co vidí v datech, ale ověření si drží původní implementace.
            call problem(501, "the ledger chain is verified by the canonical implementation")
            return
        end
        if path == "/api/ledger/export":
            call problem(501, "the ledger export is served by the canonical implementation")
            return
        end
        if path == "/api/payments":
            # Těžký čtecí pohled (137 úloh × dotazy) zůstává kanonické
            # implementaci; LSL drží zápisy a lehké čtení.
            call problem(501, "the payments report is served by the canonical implementation")
            return
        end
        if path.startswith("/api/receipt/"):
            call send_json(client, 200, receipt(path[13:len(path)]))
            return
        end
        if path.startswith("/api/sessions/"):
            call require_client(token)
            call send_json(client, 200, session_view(path[14:len(path)]))
            return
        end
        if path.startswith("/api/jobs/"):
            call require_client(token)
            rest = path[10:len(path)]
            if rest.endswith("/payment"):
                call send_json(client, 200, payment(rest[0:len(rest) - 8]))
                return
            end
            if rest.endswith("/verify"):
                call send_json(client, 200, verification(rest[0:len(rest) - 7]))
                return
            end
            call send_json(client, 200, job_view_by_id(rest))
            return
        end
        call problem(404, "route not found")
    elif method == "POST":
        seller_route = path == "/sandbox/cart" or path == "/providers/attest" or path == "/providers/research" or path == "/providers/progress"
        if seller_route:
            call seller_for_token(token)
        else:
            call require_client(token)
        end
        body = String(request.get("body_text", ""))
        if len(body) < 1 or len(body) > 65536:
            call problem(413, "body must have 1 to 65536 bytes")
        end
        payload = parse_body(request)
        if path == "/sandbox/cart":
            call send_json(client, 200, sandbox(payload, token))
            return
        end
        if path == "/providers/attest":
            call send_json(client, 200, attest(payload, token))
            return
        end
        if path == "/providers/progress":
            call send_json(client, 200, report_progress(payload, token))
            return
        end
        if path == "/providers/research":
            call send_json(client, 200, research(payload, token))
            return
        end
        if path == "/api/sessions":
            call send_json(client, 201, create_session(payload))
            return
        end
        if path == "/api/jobs":
            call send_json(client, 201, buy(payload))
            return
        end
        if path.startswith("/api/sessions/") and path.endswith("/stripe-checkout"):
            session_id = path[14:len(path) - 16]
            call send_json(client, 200, stripe_checkout(session_id, payload.get("amount_usd", None)))
            return
        end
        if path.startswith("/api/sessions/") and path.endswith("/stripe-sandbox-pay"):
            session_id = path[14:len(path) - 19]
            call send_json(client, 200, stripe_sandbox_pay(session_id, payload.get("amount_usd", None)))
            return
        end
        if path.startswith("/api/sessions/") and path.endswith("/stripe-confirm"):
            call send_json(client, 200, stripe_confirm(payload.get("stripe_session_id", None)))
            return
        end
        if path.startswith("/api/sessions/") and path.endswith("/topup"):
            session_id = path[14:len(path) - 6]
            call send_json(client, 200, top_up(session_id, payload.get("amount", None)))
            return
        end
        if path.startswith("/api/jobs/"):
            rest = path[10:len(path)]
            parts = rest.split("/")
            if len(parts) == 2:
                job_id = parts[0]
                action = parts[1]
                if action == "execute":
                    call send_json(client, 200, execute_job(job_id))
                    return
                end
                if action == "refund" or action == "settle":
                    call send_json(client, 200, finish(job_id, action == "refund"))
                    return
                end
            end
        end
        call problem(404, "route not found")
    else:
        call problem(405, "method not allowed")
    end
end

function dispatch(client, request):
    problems["status"] = 0
    problems["message"] = ""
    try:
        call route(client, request)
    else:
        status = problems["status"]
        message = problems["message"]
        if status == 0:
            status = 500
            message = "internal error"
        end
        call send_json(client, status, {"error": message})
    end
end

function serve():
    call load_config()
    call setup_schema()
    web.set_body_limit(1000000)
    listener = web.listen_ipv4("127.0.0.1", settings["port"], 128)
    if listener < 0:
        Error(ServerError: "listen failed on port " + String(settings["port"]))
    end
    print "ProofPay marketplace (LSL) listening on http://127.0.0.1:" + String(settings["port"]) + "/ database=" + db_path()
    loop:
        client = web.accept_client(listener)
        if client == -4:
            continue
        elif client < 0:
            Error(ServerError: "accept failed (" + String(client) + ")")
        end
        request = web.read_request(client, True)
        if not request["ok"]:
            call send_json(client, request["status"], {"error": "bad request"})
        else:
            call dispatch(client, request)
        end
        call fd.close(client)
    end
end

print "startup: loading config from " + settings["config"]
call load_config()
print "startup: config loaded, database " + db_path()
call setup_schema()
print "startup: schema ready, listening"
call serve()
