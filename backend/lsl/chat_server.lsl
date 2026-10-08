# Webový chat ProofPay: HTTP server, chat agent (LuxAI Flash) a živý přehled.
# Hlavní vlákno obsluhuje HTTP požadavky; každý tah chatu běží ve vlastním
# vláknu. Stav se předává frontendu přes GET /api/state (krátký polling).
# Chatová logika je sdílená s TUI přes chat_core.lsl a buyer_core.lsl.

load chat_core as agent
load buyer_core as buyer
load httpserver as web
load process
load env
load fd
load json
load time

flags = IntArray()
flags.append(0)
flags.append(0)
flags.append(0)
flags.append(0)
# Samostatný flag běžícího workeru: flags[3] je revizní čítač zámku (viz TUI),
# nesmí se používat pro busy stav.
running = IntArray()
running.append(0)

# Chat a stav jsou měněny pouze pod zámkem; pracovní vlákno píše přes progress.
chat = agent.conversation()
greeting = "Type what you want done. I will fetch the current offers, pick a seller and buy the service within budget. If no one offers it, I will tell you why.\nYou can also ask: What is on offer right now?"
state = {"messages": [{"role": "Agent", "content": greeting}], "wallet": None, "budget": None, "status": "Ready", "busy": False, "tools": [], "payments": [], "notifications": [], "notification_seq": 0, "messages_revision": 0, "audit_revision": 0, "notifications_revision": 0}
work = {"kind": "", "prompt": "", "budget": 0}
streams = {}
guard = {"last_chat": 0.0}
# Skalární přiřazení uvnitř funkce stíní globál; držák to obchází.
budget_holder = {"value": 0}
users = {"list": [], "tokens": {}}
users_file = "/var/lib/proofpay-mvp/users.json"

# ---------- účty: registrace, přihlášení, dobití ----------
# Hesla hashuje openssl (SHA-512 crypt); LSL nereimplementuje kryptografii.
function openssl_text(arguments, input_text=None):
    if input_text == None:
        outcome = process.capture_result("/usr/bin/openssl", arguments)
    else:
        outcome = process.capture_input_result("/usr/bin/openssl", arguments, input_text)
    end
    if not outcome["ok"]:
        return ""
    end
    return String(outcome["stdout"]).strip()
end

function users_load():
    try:
        raw = readfile(users_file)
    else:
        return
    end
    try:
        data = json.decode(raw)
    else:
        return
    end
    if type(data) == "Dictionary":
        users["list"] = data.get("list", [])
        users["tokens"] = data.get("tokens", {})
    end
end

function users_save():
    try:
        call writefile(users_file, json.encode(users) + chr(10))
    else:
        call time.time()
    end
end

function user_find(name):
    wanted = String(name).strip().lower()
    for user in users["list"]:
        if String(user.get("username", "")) == wanted:
            return user
        end
    end
    return None
end

function valid_username(name):
    if len(name) < 3 or len(name) > 24:
        return False
    end
    for character in name:
        code = ord(character)
        allowed = (code >= 97 and code <= 122) or (code >= 48 and code <= 57) or character == "_" or character == "-"
        if not allowed:
            return False
        end
    end
    return True
end

function token_issue(username):
    token = openssl_text(["rand", "-hex", "24"], None)
    if len(token) < 32:
        Error(ServerError: "token generation failed")
    end
    users["tokens"][token] = username
    call users_save()
    return token
end

function authenticated(request):
    header = String(request["headers"].get("authorization", "")).strip()
    if not header.startswith("Bearer "):
        return ""
    end
    token = header[7:len(header)].strip()
    if token == "":
        return ""
    end
    return String(users["tokens"].get(token, ""))
end

function token_of(request):
    header = String(request["headers"].get("authorization", "")).strip()
    if not header.startswith("Bearer "):
        return ""
    end
    return header[7:len(header)].strip()
end

# Peněženka pro platbu kartou: použije stávající session, jinak založí novou.
function ensure_session():
    call lock()
    current = chat["session_id"]
    call unlock()
    if current != "":
        return current
    end
    created = buyer.api(config, "POST", "/api/sessions", {"title": "Wallet top-up",
        "budget": Int(config.get("budget", 30)), "fixture": config.get("fixture", "buggy"), "service": "auto"})
    call lock()
    chat["session_id"] = created["id"]
    state["wallet"] = created["wallet"]
    state["budget"] = created["budget"]
    call unlock()
    return created["id"]
end

function credit_card_topup(who, session_id, outcome):
    # Zapíše připsané Lux Coins do účtu i stavu; volat jen když Stripe potvrdil platbu.
    call lock()
    state["wallet"] = buyer.api(config, "GET", "/api/sessions/" + session_id, None)["wallet"]
    call unlock()
    for index in rang(len(users["list"])):
        if String(users["list"][index].get("username", "")) == who:
            users["list"][index]["budget"] = Int(users["list"][index].get("budget", 0)) + Int(outcome["lux_coins"])
            budget_holder["value"] = users["list"][index]["budget"]
        end
    end
    call users_save()
    call lock()
    call add_notification("info", "Card payment confirmed (Stripe test): +" + String(outcome["lux_coins"]) + " Lux Coins", "")
    call unlock()
end

function user_budget(username):
    for user in users["list"]:
        if String(user.get("username", "")) == username:
            return Int(user.get("budget", 0))
        end
    end
    return 0
end

function user_public(user):
    return {"username": user.get("username", ""), "budget": user.get("budget", 0),
            "welcome_seen": user.get("welcome_seen", False), "created": user.get("created", 0)}
end
settings = {"port": 3069, "web_dir": "", "root": -1}
config = {}

function lock():
    while atomic_cas(flags, 0, 0, 1) != 0:
        call atomic_pause()
    end
end

function unlock():
    call atomic_add(flags, 3, 1)
    call atomic_xchg(flags, 0, 0)
end

# Notifikace pro zvonek: platby, refundace a dokončené tahy. Vždy pod zámkem.
function add_notification(kind, text, job_id):
    state["notification_seq"] += 1
    state["notifications"].append({"id": "n-" + String(state["notification_seq"]), "kind": kind,
        "text": text, "job_id": job_id, "time": time.time()})
    if len(state["notifications"]) > 20:
        state["notifications"] = state["notifications"][len(state["notifications"]) - 20:]
    end
    state["notifications_revision"] += 1
end

function append_message(role, text):
    state["messages"].append({"role": role, "content": text})
    state["messages_revision"] += 1
end

# Anglické přepisy trace_ui/ui_helpers pro web (TUI sdílí české moduly).
function transaction_name(action):
    if action == "ESCROW_LOCKED":
        return "ESCROW"
    elif action == "PAYMENT_RELEASED":
        return "PAYMENT"
    end
    return "REFUND"
end

function tool_entry(trace):
    summary = trace["status"] + " · " + trace["name"] + " · " + trace["source"]
    if "duration_ms" in trace:
        summary += " · " + String(trace["duration_ms"]) + " ms"
    end
    lines = ["Tool: " + trace["name"], "Status: " + trace["status"] + " · source: " + trace["source"], "Run ID: " + trace["id"]]
    if trace.get("model_call_id", "") != "":
        lines.append("Model tool-call ID: " + trace["model_call_id"])
    end
    lines.append("Started: " + String(trace["started_at"]) + " (Unix UTC)")
    if "duration_ms" in trace:
        lines.append("Duration: " + String(trace["duration_ms"]) + " ms")
    end
    lines.append("")
    lines.append("Input: " + json.encode(trace["input"]))
    if "output" in trace:
        lines.append("")
        lines.append("Result: " + json.encode(trace["output"]))
    end
    return {"id": trace["id"], "summary": summary, "lines": lines}
end

function payment_entry(receipt):
    lines = ["Job: " + receipt["job_id"], "State on read: " + receipt["state"] + " · " + String(receipt["amount"]) + " Lux Coins", "Buyer wallet: " + receipt["session_id"], "Seller: " + receipt["seller_id"], "Offer: " + receipt["offer_id"], "Idempotency key: " + receipt["idempotency_key"], "", "TRANSACTIONS · central ledger · simulated Lux Coins"]
    for transaction in receipt["transactions"]:
        lines.append("")
        lines.append(transaction_name(transaction["action"]) + " · " + transaction["id"] + " · " + String(transaction["amount"]) + " Lux Coins")
        lines.append("Ledger ID: " + String(transaction["ledger_id"]) + " · time: " + transaction["created_at"])
        lines.append("From: " + transaction["from_wallet"] + " / " + transaction["from_account"])
        lines.append("To: " + transaction["to_wallet"] + " / " + transaction["to_account"])
    end
    lines.append("")
    lines.append("Contract SHA256: " + receipt["contract_sha256"])
    if receipt["result_sha256"] != "":
        lines.append("Delivery SHA256: " + receipt["result_sha256"])
    end
    for id in receipt["verification_receipts"]:
        lines.append("Execution receipt: " + id)
    end
    lines.append("")
    lines.append("Transaction IDs are scoped to this marketplace database. They are not blockchain transactions.")
    summary = receipt["state"] + " · " + String(receipt["amount"]) + " Lux Coins · " + receipt["seller_id"] + " · " + receipt["job_id"]
    return {"id": receipt["job_id"], "summary": summary, "lines": lines}
end

function upsert(entries, entry):
    for index in rang(len(entries)):
        if entries[index]["id"] == entry["id"]:
            entries[index] = entry
            return
        end
    end
    entries.append(entry)
end

function status_line(event):
    action = event["action"]
    data = event["data"]
    if action == "TOOL_STARTED":
        return "Calling " + data["name"] + " · " + data["source"]
    elif action == "TOOL_FINISHED":
        return data["name"] + " · " + data["status"] + " · " + String(data["duration_ms"]) + " ms"
    elif action == "PAYMENT_UPDATED":
        return "Receipt " + data["state"] + " · " + data["job_id"]
    elif action == "PAYMENT_UNAVAILABLE":
        return data["message"]
    elif action == "SESSION_CREATED":
        return "New session · budget " + String(data["budget"]) + " Lux Coins"
    elif action == "PLANNER_THINKING":
        return "Flash is resolving the request…"
    elif action == "CATALOG_FETCHED":
        return "Catalog fetched · " + String(data["count"]) + " offers"
    elif action == "CHAT_DECISION":
        return data["message"]
    elif action == "ESCROW_LOCKED":
        return "Escrow " + String(data["amount"]) + " Lux Coins · " + String(data["offer_id"])
    elif action == "DELIVERY_CHECKED":
        if data["verdict"]["valid_delivery"]:
            return "Delivery passed the agreed checks"
        end
        return "DISPUTE · " + ", ".join(data["verdict"]["reasons"])
    elif action == "REFUND_RECEIVED":
        return "REFUND · " + String(data["amount"]) + " Lux Coins returned"
    elif action == "PAYMENT_RELEASED":
        return "PAID · " + String(data["amount"]) + " Lux Coins → " + String(data["seller_id"])
    elif action == "FINISHED":
        return "DONE · available " + String(data["wallet"]["available"]) + " Lux Coins"
    elif action == "STOPPED":
        return "STOPPED · available " + String(data["wallet"]["available"]) + " Lux Coins"
    end
    return action
end

function trim_state():
    # Volá se jen po skončení tahu (žádné aktivní streamy): ořez historie
    # udrží paměť serveru v mezích i po mnoha tazích demo noci.
    if len(state["messages"]) > 60:
        drop = len(state["messages"]) - 60
        kept = []
        index = drop
        while index < len(state["messages"]):
            kept.append(state["messages"][index])
            index += 1
        end
        state["messages"] = kept
    end
    if len(state["tools"]) > 200:
        state["tools"] = state["tools"][len(state["tools"]) - 200:]
    end
    if len(state["payments"]) > 100:
        state["payments"] = state["payments"][len(state["payments"]) - 100:]
    end
    state["messages_revision"] += 1
    state["audit_revision"] += 1
end

function cancel_requested():
    return atomic_add(flags, 2, 0) != 0
end

function progress(event):
    prepared = None
    if event["action"] == "TOOL_STARTED" or event["action"] == "TOOL_FINISHED":
        prepared = tool_entry(event["data"])
    elif event["action"] == "PAYMENT_UPDATED":
        prepared = payment_entry(event["data"])
    end
    call lock()
    action = event["action"]
    data = event["data"]
    if action == "TOOL_STARTED" or action == "TOOL_FINISHED":
        call upsert(state["tools"], prepared)
        state["audit_revision"] += 1
    elif action == "PAYMENT_UPDATED":
        call upsert(state["payments"], prepared)
        state["audit_revision"] += 1
    elif action == "STREAM_START":
        call append_message("Agent", data["label"] + chr(10))
        streams[data["id"]] = len(state["messages"]) - 1
    elif action == "STREAM_DELTA" and data["id"] in streams:
        index = streams[data["id"]]
        state["messages"][index]["content"] += data["text"]
        state["messages_revision"] += 1
    elif action == "STREAM_COMMIT" and data["id"] in streams:
        state["messages"][streams[data["id"]]]["content"] = data["text"]
        state["messages_revision"] += 1
    elif action == "STREAM_DISCARD" and data["id"] in streams:
        state["messages"][streams[data["id"]]]["content"] = "Delivery preview was not accepted: the seller did not meet the contract. Refund follows."
        state["messages_revision"] += 1
    elif action == "WALLET_UPDATED" or action == "FINISHED" or action == "STOPPED":
        wallet = data
        if action != "WALLET_UPDATED":
            wallet = data["wallet"]
        end
        state["wallet"] = json.decode(json.encode(wallet))
    elif action == "ESCROW_LOCKED":
        state["wallet"]["available"] -= data["amount"]
        state["wallet"]["locked"] += data["amount"]
    elif action == "REFUND_RECEIVED":
        state["wallet"]["available"] += data["amount"]
        state["wallet"]["locked"] -= data["amount"]
        call append_message("Agent", "Delivery did not meet the contract. " + String(data["amount"]) + " Lux Coins refunded; selecting another seller.")
        call add_notification("refund", String(data["amount"]) + " Lux Coins refunded by " + String(data["seller_id"]) + " (delivery failed verification)", data.get("job_id", ""))
    elif action == "PAYMENT_RELEASED":
        state["wallet"]["locked"] -= data["amount"]
        call add_notification("paid", String(data["amount"]) + " Lux Coins paid to " + String(data["seller_id"]) + " (verified delivery)", data.get("job_id", ""))
    elif action == "SESSION_CREATED":
        state["budget"] = data["budget"]
    elif action == "CHAT_DECISION":
        state["status"] = data["message"]
    end
    if action[0:7] != "STREAM_" and action not in ["TOOL_STARTED", "TOOL_FINISHED", "PAYMENT_UPDATED"]:
        state["status"] = status_line(event)
    end
    call unlock()
end

function worker():
    try:
        if work["kind"] == "catalog":
            catalog = buyer.traced_api(config, [], progress, "fetch_offers_http", "GET", "/api/offers")
            text = "Current offers (" + String(len(catalog["offers"])) + "):"
            for offer in catalog["offers"]:
                text += chr(10) + offer["service_name"] + " · " + offer["name"] + " · " + String(offer["price"]) + " Lux Coins · " + offer["delivery"]
            end
            call lock()
            call append_message("Agent", text)
            call add_notification("info", "Catalog refreshed: " + String(len(catalog["offers"])) + " active offers", "")
            state["status"] = "Catalog refreshed"
            state["busy"] = False
            call trim_state()
            call unlock()
        else:
            turn_config = json.decode(json.encode(config))
            if work["budget"] > 0:
                turn_config["budget"] = work["budget"]
            end
            report = agent.turn(turn_config, chat, work["prompt"], progress, cancel_requested)
            call lock()
            if state["messages"][len(state["messages"]) - 1]["content"] != report["reply"]:
                call append_message("Agent", report["reply"])
            end
            if report["session"] != None:
                state["wallet"] = report["session"]["wallet"]
            end
            state["status"] = "Ready · send another message"
            state["busy"] = False
            call trim_state()
            call unlock()
        end
    else:
        # Syrové chyby API nevypisujeme kvůli možným citlivým údajům.
        call lock()
        call append_message("Agent", "The request could not be completed. Check the connection and try again. If a purchase was already in progress, its state is on the marketplace.")
        call add_notification("error", "A task could not be completed — nothing was paid", "")
        state["status"] = "Connection or agent-decision error"
        state["busy"] = False
        call unlock()
    end
    call atomic_xchg(flags, 1, 1)
    # Běžící worker končí; od této chvíle smí hlavní vlákno znovu gc().
    call atomic_xchg(running, 0, 0)
end

function start(kind, prompt):
    call atomic_xchg(flags, 1, 0)
    call atomic_xchg(flags, 2, 0)
    work["kind"] = kind
    work["prompt"] = prompt
    work["budget"] = budget_holder["value"]
    call lock()
    state["busy"] = True
    state["status"] = "Fetching the current offers…"
    if kind == "turn":
        call append_message("You", prompt)
    end
    call unlock()
    unsafe:
        tid = thread_spawn(worker)
    end
    if tid < 0:
        call lock()
        state["busy"] = False
        call unlock()
        call atomic_xchg(flags, 1, 1)
        Error(ChatError: "Failed to start the network thread")
    end
    call atomic_xchg(running, 0, 1)
    return True
end

function busy_now():
    return atomic_add(running, 0, 0) != 0
end

function snapshot(lite=False):
    # Lite režim posílá jen peněženku, stav a revize; plná data (zprávy,
    # nástroje, platby) se přenášejí jen při změně revize. Šetří hlavní
    # vlákno i síť při pollingu každých 500 ms.
    call lock()
    messages = []
    tools = []
    payments = []
    notifications = []
    if not lite:
        for message in state["messages"]:
            messages.append({"role": message["role"], "content": message["content"]})
        end
        for entry in state["tools"]:
            tools.append(entry)
        end
        for entry in state["payments"]:
            payments.append(entry)
        end
        for entry in state["notifications"]:
            notifications.append(entry)
        end
    end
    wallet = None
    if state["wallet"] != None:
        wallet = {"available": state["wallet"]["available"], "locked": state["wallet"]["locked"]}
    end
    view = {"ok": True, "currency": "Lux Coins", "simulated_payments": True, "ai_model": "flash", "tools": tools, "payments": payments, "messages": messages, "wallet": wallet, "budget": state["budget"], "status": state["status"], "busy": state["busy"], "messages_revision": state["messages_revision"], "audit_revision": state["audit_revision"], "tool_count": len(state["tools"]), "payment_count": len(state["payments"]), "notifications": notifications, "notifications_revision": state["notifications_revision"], "notification_count": len(state["notifications"]), "time": time.time()}
    call atomic_xchg(flags, 0, 0)
    return view
end

function send_json(client, status, value):
    call send_raw(client, status, web.status_reason(status), json.encode(value), "application/json; charset=utf-8", False)
end

function send_error_json(client, status, message):
    call send_json(client, status, {"error": message})
end

# Vlastní odpověď s Cache-Control: no-store: WEDOS CDN před doménou cachuje
# GET odpovědi bez řídící hlavičky až 10 minut, což by vracelo starý stav.
function send_raw(client, status, reason, body, mime, head_only):
    payload = body
    if type(payload) == "String":
        payload = web.text_bytes(payload)
    end
    header = "HTTP/1.1 " + String(status) + " " + String(reason) + chr(13) + chr(10)
    header += "Content-Length: " + String(len(payload)) + chr(13) + chr(10)
    header += "Content-Type: " + String(mime) + chr(13) + chr(10)
    header += "Cache-Control: no-store" + chr(13) + chr(10)
    header += "Connection: close" + chr(13) + chr(10)
    header += "X-Content-Type-Options: nosniff" + chr(13) + chr(10)
    header += chr(13) + chr(10)
    call web.socket_write(client, web.text_bytes(header))
    if not Bool(head_only) and len(payload) > 0:
        call web.socket_write(client, payload)
    end
end

function static_response(client, request):
    if request["method"] != "GET" and request["method"] != "HEAD":
        call send_error_json(client, 405, "method not allowed")
        return
    end
    relative = web.safe_relative_target(request["target"])
    if relative == None:
        call send_error_json(client, 403, "forbidden")
        return
    end
    handle = web.open_static(settings["root"], relative)
    if handle < 0:
        call send_error_json(client, 404, "not found")
        return
    end
    file = web.read_handle_limit(handle, 8388608)
    call fd.close(handle)
    if not file["ok"]:
        call send_error_json(client, 404, "not found")
        return
    end
    call send_raw(client, 200, "OK", file["data"], web.content_type(relative), request["method"] == "HEAD")
end

function reset_conversation():
    call lock()
    # Nová konverzace: historie modelu a session ID se zahodí; peněženka
    # v marketplace zůstává beze změny, další nákup vytvoří novou.
    chat["history"] = []
    chat["session_id"] = ""
    chat["turn"] = 0
    state["messages"] = [{"role": "Agent", "content": greeting}]
    state["wallet"] = None
    state["budget"] = None
    state["tools"] = []
    state["payments"] = []
    state["status"] = "New conversation · the wallet is created on the first purchase"
    state["messages_revision"] += 1
    state["audit_revision"] += 1
    call unlock()
end

function route(client, request):
    who = ""
    method = request["method"]
    raw = String(request["target"])
    query = raw.find("?")
    target = raw
    if query >= 0:
        target = raw[0:query]
    end
    if target == "/api/state" and method == "GET":
        call send_json(client, 200, snapshot(raw.find("lite=1") >= 0))
        return
    end
    if target == "/api/health" and method == "GET":
        call send_json(client, 200, {"ok": True, "payments": "simulated Lux Coins"})
        return
    end
    # --- účty: registrace a přihlášení (bez tokenu) ---
    if method == "POST" and (target == "/api/register" or target == "/api/login"):
        payload = None
        if type(request.get("body_text", None)) == "String":
            try:
                payload = json.decode(request["body_text"])
            else:
                payload = None
            end
        end
        if type(payload) != "Dictionary":
            call send_error_json(client, 400, "JSON object with username and password is required")
            return
        end
        username = String(payload.get("username", "")).strip().lower()
        password = String(payload.get("password", ""))
        if target == "/api/register":
            if not valid_username(username):
                call send_error_json(client, 400, "username must be 3-24 characters: a-z, 0-9, _ or -")
                return
            end
            if len(password) < 8 or len(password) > 128:
                call send_error_json(client, 400, "password must have 8 to 128 characters")
                return
            end
            if user_find(username) != None:
                call send_error_json(client, 409, "this username is already taken")
                return
            end
            salt = openssl_text(["rand", "-hex", "8"], None)
            digest = openssl_text(["passwd", "-6", "-salt", salt, "-stdin"], password + chr(10))
            if digest == "":
                call send_error_json(client, 500, "cannot hash the password right now")
                return
            end
            user = {"username": username, "hash": digest, "created": time.time(),
                    "budget": Int(config.get("budget", 30)), "welcome_seen": False}
            users["list"].append(user)
            token = token_issue(username)
            call send_json(client, 201, {"token": token, "user": user_public(user)})
            return
        end
        user = user_find(username)
        if user == None or len(password) == 0:
            call send_error_json(client, 401, "invalid username or password")
            return
        end
        parts = String(user["hash"]).split("$")
        if len(parts) < 4:
            call send_error_json(client, 401, "invalid username or password")
            return
        end
        digest = openssl_text(["passwd", "-6", "-salt", String(parts[2]), "-stdin"], password + chr(10))
        if digest == "" or digest != String(user["hash"]):
            call send_error_json(client, 401, "invalid username or password")
            return
        end
        token = token_issue(username)
        call send_json(client, 200, {"token": token, "user": user_public(user)})
        return
    end
    if method == "GET" and target == "/api/me":
        who = authenticated(request)
        if who == "":
            call send_error_json(client, 401, "sign in first")
            return
        end
        user = user_find(who)
        if user == None:
            call send_error_json(client, 401, "unknown account")
            return
        end
        call send_json(client, 200, {"user": user_public(user)})
        return
    end
    if method == "POST" and (target == "/api/logout" or target == "/api/welcome-seen" or target == "/api/topup"
            or target == "/api/topup/stripe" or target == "/api/topup/stripe/confirm" or target == "/api/topup/stripe/sandbox"
            or target == "/api/chat" or target == "/api/catalog" or target == "/api/cancel" or target == "/api/new"):
        who = authenticated(request)
        if who == "":
            call send_error_json(client, 401, "sign in first")
            return
        end
        if target == "/api/logout":
            token = token_of(request)
            if token != "" and token in users["tokens"]:
                users["tokens"].pop(token)
                call users_save()
            end
            call send_json(client, 200, {"ok": True})
            return
        end
        if target == "/api/welcome-seen":
            for index in rang(len(users["list"])):
                if String(users["list"][index].get("username", "")) == who:
                    users["list"][index]["welcome_seen"] = True
                end
            end
            call users_save()
            call send_json(client, 200, {"ok": True})
            return
        end
        if target == "/api/topup/stripe" or target == "/api/topup/stripe/confirm" or target == "/api/topup/stripe/sandbox":
            payload = None
            if type(request.get("body_text", None)) == "String":
                try:
                    payload = json.decode(request["body_text"])
                else:
                    payload = None
                end
            end
            if busy_now():
                call send_error_json(client, 409, "The agent is still working; wait for it to finish.")
                return
            end
            if target == "/api/topup/stripe/confirm":
                stripe_id = None
                if type(payload) == "Dictionary":
                    stripe_id = payload.get("stripe_session_id")
                end
                if type(stripe_id) != "String" or len(stripe_id) < 6:
                    call send_error_json(client, 400, "stripe_session_id is required")
                    return
                end
                try:
                    outcome = buyer.api(config, "POST", "/api/sessions/card/stripe-confirm", {"stripe_session_id": stripe_id})
                else:
                    call send_error_json(client, 502, "Stripe confirmation failed")
                    return
                end
                if outcome.get("credited", False):
                    call credit_card_topup(who, String(outcome["session_id"]), outcome)
                end
                call send_json(client, 200, {"stripe": outcome, "user": user_public(user_find(who)), "wallet": state["wallet"]})
                return
            end
            amount_usd = None
            if type(payload) == "Dictionary":
                amount_usd = payload.get("amount_usd")
            end
            if type(amount_usd) != "Int" or amount_usd < 1 or amount_usd > 25:
                call send_error_json(client, 400, "card amount must be a whole number of US dollars from 1 to 25")
                return
            end
            session_id = ensure_session()
            if target == "/api/topup/stripe/sandbox":
                try:
                    payment = buyer.api(config, "POST", "/api/sessions/" + session_id + "/stripe-sandbox-pay", {"amount_usd": amount_usd})
                    outcome = buyer.api(config, "POST", "/api/sessions/card/stripe-confirm", {"stripe_session_id": payment["stripe_session_id"]})
                else:
                    call send_error_json(client, 502, "Stripe request failed")
                    return
                end
                if outcome.get("credited", False):
                    call credit_card_topup(who, session_id, outcome)
                end
                call send_json(client, 200, {"stripe": outcome, "user": user_public(user_find(who)), "wallet": state["wallet"]})
                return
            end
            try:
                checkout = buyer.api(config, "POST", "/api/sessions/" + session_id + "/stripe-checkout", {"amount_usd": amount_usd})
            else:
                call send_error_json(client, 502, "Stripe checkout failed")
                return
            end
            call send_json(client, 200, {"stripe": checkout, "user": user_public(user_find(who))})
            return
        end
        if target == "/api/topup":
            payload = None
            if type(request.get("body_text", None)) == "String":
                try:
                    payload = json.decode(request["body_text"])
                else:
                    payload = None
                end
            end
            amount = None
            if type(payload) == "Dictionary":
                amount = payload.get("amount")
            end
            if type(amount) != "Int" or amount < 1 or amount > 500:
                call send_error_json(client, 400, "top-up amount must be a whole number from 1 to 500 Lux Coins")
                return
            end
            if busy_now():
                call send_error_json(client, 409, "The agent is still working; wait for it to finish.")
                return
            end
            new_budget = 0
            for index in rang(len(users["list"])):
                if String(users["list"][index].get("username", "")) == who:
                    users["list"][index]["budget"] = Int(users["list"][index].get("budget", 0)) + amount
                    new_budget = users["list"][index]["budget"]
                end
            end
            call users_save()
            budget_holder["value"] = new_budget
            wallet = None
            call lock()
            session_id = chat["session_id"]
            call unlock()
            if session_id != "":
                try:
                    topped = buyer.api(config, "POST", "/api/sessions/" + session_id + "/topup", {"amount": amount})
                else:
                    topped = None
                end
                if topped != None:
                    wallet = topped["wallet"]
                    call lock()
                    state["wallet"] = wallet
                    call unlock()
                end
            end
            call lock()
            call add_notification("info", "Account topped up: +" + String(amount) + " Lux Coins (budget " + String(new_budget) + ")", "")
            call unlock()
            call send_json(client, 200, {"user": user_public(user_find(who)), "wallet": wallet})
            return
        end
    end

    if method == "POST" and (target == "/api/chat" or target == "/api/catalog" or target == "/api/cancel" or target == "/api/new"):
        if target == "/api/cancel":
            call atomic_xchg(flags, 2, 1)
            call send_json(client, 200, {"ok": True})
            return
        end
        if target == "/api/new":
            if busy_now():
                call send_error_json(client, 409, "The agent is still working; wait for it to finish.")
                return
            end
            call reset_conversation()
            call send_json(client, 200, {"ok": True})
            return
        end
        if busy_now():
            call send_error_json(client, 409, "The agent is still working; wait for it to finish.")
            return
        end
        payload = None
        if type(request.get("body_text", None)) == "String":
            try:
                payload = json.decode(request["body_text"])
            else:
                payload = None
            end
        end
        if target == "/api/catalog":
            call start("catalog", "")
            call send_json(client, 200, {"ok": True})
            return
        end
        # /api/chat: omezení tempa chrání kvótu modelu před neúmyslným spamem.
        if time.time() - guard["last_chat"] < 2.0:
            call send_error_json(client, 429, "Please wait a moment before the next request.")
            return
        end
        if type(payload) != "Dictionary":
            call send_error_json(client, 400, "JSON object with message is required")
            return
        end
        message = payload.get("message", "")
        if type(message) != "String" or len(message.strip()) == 0 or len(message) > 1000:
            call send_error_json(client, 400, "message must have 1 to 1000 characters")
            return
        end
        guard["last_chat"] = time.time()
        budget_holder["value"] = user_budget(who)
        call start("turn", message)
        call send_json(client, 200, {"ok": True, "account": who, "budget": budget_holder["value"]})
        return
    end
    if method == "GET" or method == "HEAD":
        call static_response(client, request)
        return
    end
    call send_error_json(client, 405, "method not allowed")
end

function serve():
    settings["root"] = web.open_root(settings["web_dir"])
    if settings["root"] < 0:
        Error(ServerError: "Web directory cannot be opened: " + settings["web_dir"])
    end
    listener = web.listen_ipv4("127.0.0.1", settings["port"], 128)
    if listener < 0:
        Error(ServerError: "listen failed on port " + String(settings["port"]))
    end
    print "ProofPay web chat listening on http://127.0.0.1:" + String(settings["port"]) + "/"
    loop:
        client = web.accept_client(listener)
        if client == -4:
            continue
        elif client < 0:
            Error(ServerError: "accept failed (" + String(client) + ")")
        end
        request = web.read_request(client, True)
        if not request["ok"]:
            call send_raw(client, request["status"], request["reason"], String(request["status"]) + " " + String(request["reason"]) + chr(10), "text/plain; charset=utf-8", False)
        else:
            try:
                call route(client, request)
            else:
                call send_raw(client, 500, "Internal Server Error", "500 Internal Server Error\n", "text/plain; charset=utf-8", False)
            end
        end
        call fd.close(client)
        # gc() smí běžet jen bez aktivního pracovního vlákna: při běžícím tahu
        # poškodí alokace stream parseru modelu. Mezi tahy drží paměť plochou.
        if not busy_now():
            call gc()
        end
    end
end

# Konfigurace se čte výhradně z .env; klíče nikdy nevstupují do odpovědí.
call env.load_dotenv(env.get("PROOFPAY_CHAT_CONFIG", "/etc/proofpay-mvp/chat.env"))
settings["port"] = Int(env.get("PROOFPAY_WEB_PORT", "3069"))
settings["web_dir"] = env.get("PROOFPAY_WEB_DIR", "/opt/proofpay-mvp/web")
config = {"market_url": env.require("MARKET_URL"), "market_token": readfile(env.require("MARKET_TOKEN_FILE")).strip(), "ai_url": env.get("LUXAI_URL", "https://api.lux-ai.cz/v1/chat/completions"), "ai_token": readfile(env.get("LUXAI_KEY_FILE", env.get("HOME", "") + "/.config/lux-runner/api-key")).strip(), "budget": Int(env.get("PROOFPAY_BUDGET", "30")), "fixture": env.get("PROOFPAY_FIXTURE", "buggy"), "lang": env.get("PROOFPAY_LANG", "en"), "output_file": env.require("PROOFPAY_RESULT_FILE")}
if len(config["market_token"]) < 32 or len(config["ai_token"]) < 8:
    Error(ConfigError: "Market or AI token file does not contain a usable key")
end
state["budget"] = config["budget"]
users_file = env.get("PROOFPAY_USERS_FILE", users_file)
call users_load()
try:
    call serve()
else:
    print "Web chat server failed: " + String(last_error())
    exit(1)
end
