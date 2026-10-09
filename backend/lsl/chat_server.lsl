# ProofPay agent console: the web service behind hackathon.lux-ai.cz/web/.
# One thread answers HTTP requests while each chat turn runs in its own worker
# thread; the frontend polls GET /api/state for state, tool trace and questions.
# Owns accounts (register/login/top-up), the notification bell, the wallet view,
# card top-ups through Stripe, per-account isolation and on-disk state so a
# restart never loses a user's tasks. Chat logic comes from chat_core.lsl.

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
greeting = "Describe the task once and I take it from there: I read the live catalog, buy the right service in escrow, verify the delivery, settle or refund it, and hand you a signed receipt. I only open a dialog when something is genuinely missing that only you have.\nYou can also ask: What is on offer right now?"
state = {"messages": [{"role": "Agent", "content": greeting}], "wallet": None, "budget": None, "question": None, "status": "Ready", "busy": False, "tools": [], "payments": [], "notifications": [], "notification_seq": 0, "messages_revision": 0, "audit_revision": 0, "notifications_revision": 0}
work = {"kind": "", "prompt": "", "budget": 0}
# Původní zadání uživatele; drží se přes dotazy v popupu, aby se dalo pokračovat.
root_task = {"value": ""}
streams = {}
# Každý účet má vlastní konverzaci (session/history) i vlastní zachycené
# peněženky; zobrazený stav se filtruje podle vlastníka záznamu.
chats = {}
root_tasks = {}
account_wallets = {}
account_budgets = {}
worker_owner = {"value": ""}
# Stav se ukládá na disk (dva střídavé soubory), aby restart služby
# nesmazal účtům jejich úlohy, konverzaci ani notifikace.
state_file_a = "/var/lib/proofpay-mvp/console-state-a.json"
state_file_b = "/var/lib/proofpay-mvp/console-state-b.json"
state_slot = {"which": "b", "last_save": 0.0}
owner_list = []

function state_adopt(source):
    # Obnova sdíleného (tagovaného) stavu po restartu služby.
    state["messages"] = source.get("messages", [{"role": "Agent", "content": greeting, "who": ""}])
    state["wallet"] = source.get("wallet", None)
    state["budget"] = source.get("budget", None)
    state["question"] = source.get("question", None)
    state["status"] = "Ready · send another message"
    state["busy"] = False
    state["tools"] = source.get("tools", [])
    state["payments"] = source.get("payments", [])
    state["notifications"] = source.get("notifications", [])
    state["notification_seq"] = Int(source.get("notification_seq", 0))
    state["messages_revision"] = Int(source.get("messages_revision", 0)) + 1
    state["audit_revision"] = Int(source.get("audit_revision", 0)) + 1
    state["notifications_revision"] = Int(source.get("notifications_revision", 0)) + 1
end

function workspaces_snapshot():
    saved = {"saved": time.time(), "owners": [], "state": state, "chats": {}, "wallets": {}, "budgets": {}}
    for owner in owner_list:
        saved["owners"].append(owner)
        if owner in chats:
            saved["chats"][owner] = chats[owner]
        end
        if owner in account_wallets:
            saved["wallets"][owner] = account_wallets[owner]
        end
        if owner in account_budgets:
            saved["budgets"][owner] = account_budgets[owner]
        end
    end
    return saved
end

function workspaces_save(force=False):
    # Střídavé soubory: kdyby proces spadl uprostřed zápisu, druhý zůstane celý.
    if not force and time.time() - state_slot["last_save"] < 2.0:
        return
    end
    state_slot["last_save"] = time.time()
    which = "a"
    if state_slot["which"] == "a":
        which = "b"
    end
    path = state_file_a
    if which == "b":
        path = state_file_b
    end
    try:
        payload = json.encode(workspaces_snapshot()) + chr(10)
    else:
        print "state save FAILED while encoding"
        return
    end
    try:
        call writefile(path, payload)
        state_slot["which"] = which
    else:
        print "state save FAILED for " + path + " (" + String(len(payload)) + " bytes)"
    end
end

function workspaces_load():
    best = None
    for path in [state_file_a, state_file_b]:
        try:
            raw = readfile(path)
        else:
            continue
        end
        try:
            data = json.decode(raw)
        else:
            continue
        end
        if type(data) != "Dictionary":
            continue
        end
        if best == None or Float(data.get("saved", 0)) > Float(best.get("saved", 0)):
            best = data
        end
    end
    if best == None:
        return
    end
    call state_adopt(best.get("state", {}))
    for owner in best.get("owners", []):
        if owner not in owner_list:
            owner_list.append(owner)
        end
    end
    for owner in best.get("chats", {}):
        chats[owner] = best["chats"][owner]
    end
    for owner in best.get("wallets", {}):
        account_wallets[owner] = best["wallets"][owner]
    end
    for owner in best.get("budgets", {}):
        account_budgets[owner] = best["budgets"][owner]
    end
end

function account_chat(who):
    if who == "":
        return chat
    end
    if who not in chats:
        chats[who] = agent.conversation()
        if who not in owner_list:
            owner_list.append(who)
        end
    end
    return chats[who]
end

function account_root_task(who):
    if who == "":
        return root_task
    end
    if who not in root_tasks:
        root_tasks[who] = {"value": ""}
    end
    return root_tasks[who]
end

function owned(entry, who):
    # Záznamy bez vlastníka (úvodní zpráva) vidí každý.
    owner = String(entry.get("who", ""))
    return owner == "" or owner == who
end
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
function ensure_session(account):
    call lock()
    current = account_chat(account)["session_id"]
    call unlock()
    if current != "":
        call lock()
        existing = buyer.api(config, "GET", "/api/sessions/" + current, None)
        state["wallet"] = existing["wallet"]
        state["budget"] = existing["budget"]
        if account != "":
            account_wallets[account] = existing["wallet"]
            account_budgets[account] = existing["budget"]
        end
        call unlock()
        return current
    end
    # Peněženka startuje s budgetem účtu (marketplace povoluje 1-100).
    budget = user_budget(account)
    if budget > 100:
        budget = 100
    end
    if budget < 1:
        budget = Int(config.get("budget", 30))
    end
    created = buyer.api(config, "POST", "/api/sessions", {"title": "Wallet top-up",
        "budget": budget, "fixture": config.get("fixture", "buggy"), "service": "auto"})
    call lock()
    account_chat(account)["session_id"] = created["id"]
    state["wallet"] = created["wallet"]
    state["budget"] = created["budget"]
    if account != "":
        account_wallets[account] = created["wallet"]
        account_budgets[account] = created["budget"]
    end
    call unlock()
    return created["id"]
end

function credit_card_topup(who, session_id, outcome):
    # Zapíše připsané dolary do účtu i stavu; volat jen když Stripe potvrdil platbu.
    # HTTP volání záměrně mimo zámek: pomalá odpověď nesmí zablokovat ostatní vlákna.
    refreshed = buyer.api(config, "GET", "/api/sessions/" + session_id, None)
    call lock()
    try:
        state["wallet"] = refreshed["wallet"]
        state["budget"] = refreshed["budget"]
        if who != "":
            account_wallets[who] = refreshed["wallet"]
            account_budgets[who] = refreshed["budget"]
        end
    else:
        call time.time()
    end
    call unlock()
    for index in rang(len(users["list"])):
        if String(users["list"][index].get("username", "")) == who:
            users["list"][index]["budget"] = Int(users["list"][index].get("budget", 0)) + Int(outcome["lux_coins"])
            budget_holder["value"] = users["list"][index]["budget"]
        end
    end
    call users_save()
    call lock()
    try:
        charged = Int(outcome.get("amount_usd", outcome["lux_coins"]))
        call add_notification("info", "Stripe charged " + usd(charged) + " by card → wallet credited " + usd(outcome["lux_coins"]) + " (1:1)", "", who)
    else:
        call time.time()
    end
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

# Jedna jednotka ledgeru = 1 USD (simulovaně); zobrazujeme dolary.
function usd(amount):
    return "$" + String(Int(amount)) + ".00"
end

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
function add_notification(kind, text, job_id, who=""):
    state["notification_seq"] += 1
    state["notifications"].append({"id": "n-" + String(state["notification_seq"]), "kind": kind,
        "text": text, "job_id": job_id, "who": who, "time": time.time()})
    if len(state["notifications"]) > 20:
        state["notifications"] = state["notifications"][len(state["notifications"]) - 20:]
    end
    state["notifications_revision"] += 1
end

function append_message(role, text, who=""):
    state["messages"].append({"role": role, "content": text, "who": who})
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
    lines = ["Job: " + receipt["job_id"], "State on read: " + receipt["state"] + " · " + usd(receipt["amount"]), "Buyer wallet: " + receipt["session_id"], "Seller: " + receipt["seller_id"], "Offer: " + receipt["offer_id"], "Idempotency key: " + receipt["idempotency_key"], "", "TRANSACTIONS · central ledger · simulated USD"]
    for transaction in receipt["transactions"]:
        lines.append("")
        lines.append(transaction_name(transaction["action"]) + " · " + transaction["id"] + " · " + usd(transaction["amount"]))
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
    summary = receipt["state"] + " · " + usd(receipt["amount"]) + " · " + receipt["seller_id"] + " · " + receipt["job_id"]
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
        return "New session · budget " + usd(data["budget"])
    elif action == "PLANNER_THINKING":
        return "Flash is resolving the request…"
    elif action == "CATALOG_FETCHED":
        return "Catalog fetched · " + String(data["count"]) + " offers"
    elif action == "CHAT_DECISION":
        return data["message"]
    elif action == "ESCROW_LOCKED":
        return "Escrow " + usd(data["amount"]) + " · " + String(data["offer_id"])
    elif action == "DELIVERY_CHECKED":
        if data["verdict"]["valid_delivery"]:
            return "Delivery passed the agreed checks"
        end
        return "DISPUTE · " + ", ".join(data["verdict"]["reasons"])
    elif action == "REFUND_RECEIVED":
        return "REFUND · " + usd(data["amount"]) + " returned"
    elif action == "PAYMENT_RELEASED":
        return "PAID · " + usd(data["amount"]) + " → " + String(data["seller_id"])
    elif action == "FINISHED":
        return "DONE · available " + usd(data["wallet"]["available"])
    elif action == "STOPPED":
        return "STOPPED · available " + usd(data["wallet"]["available"])
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
    owner = worker_owner["value"]
    prepared = None
    if event["action"] == "TOOL_STARTED" or event["action"] == "TOOL_FINISHED":
        prepared = tool_entry(event["data"])
    elif event["action"] == "PAYMENT_UPDATED":
        prepared = payment_entry(event["data"])
    end
    if prepared != None:
        prepared["who"] = owner
    end
    call lock()
    try:
        action = event["action"]
        data = event["data"]
        if action == "TOOL_STARTED" or action == "TOOL_FINISHED":
            call upsert(state["tools"], prepared)
            state["audit_revision"] += 1
            # Dokončené volání zapíšeme i do konverzace, ať je vidět, co agent dělá.
            if action == "TOOL_FINISHED":
                mark = "✔"
                if String(data.get("status", "OK")) == "ERROR":
                    mark = "✖"
                end
                duration = ""
                if "duration_ms" in data:
                    duration = " · " + String(data["duration_ms"]) + " ms"
                end
                call append_message("Tool", mark + " " + String(data.get("name", "tool")) + " · " + String(data.get("source", "")) + duration, owner)
            end
        elif action == "PAYMENT_UPDATED":
            call upsert(state["payments"], prepared)
            state["audit_revision"] += 1
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
            call append_message("Agent", "Delivery did not meet the contract. " + usd(data["amount"]) + " refunded; selecting another seller.", owner)
            call add_notification("refund", usd(data["amount"]) + " refunded by " + String(data["seller_id"]) + " (delivery failed verification)", data.get("job_id", ""), owner)
        elif action == "PAYMENT_RELEASED":
            state["wallet"]["locked"] -= data["amount"]
            call add_notification("paid", usd(data["amount"]) + " paid to " + String(data["seller_id"]) + " (verified delivery)", data.get("job_id", ""), owner)
        elif action == "SESSION_CREATED":
            state["budget"] = data["budget"]
        elif action == "CHAT_DECISION":
            state["status"] = data["message"]
        end
        if action[0:7] != "STREAM_" and action not in ["TOOL_STARTED", "TOOL_FINISHED", "PAYMENT_UPDATED"]:
            state["status"] = status_line(event)
        end
        if owner != "":
            account_wallets[owner] = state["wallet"]
            if state["budget"] != None:
                account_budgets[owner] = state["budget"]
            end
        end
    else:
        # Aktualizace stavu nesmí nikdy nechat zámek zamčený.
        state["status"] = "Internal update skipped (state unchanged)"
        state["audit_revision"] += 1
    end
    call unlock()
    call workspaces_save()
end

function worker():
    try:
        if work["kind"] == "catalog":
            catalog = buyer.traced_api(config, [], progress, "fetch_offers_http", "GET", "/api/offers")
            text = "Current offers (" + String(len(catalog["offers"])) + "):"
            for offer in catalog["offers"]:
                text += chr(10) + offer["service_name"] + " · " + offer["name"] + " · " + usd(offer["price"]) + " · " + offer["delivery"]
            end
            call lock()
            try:
            call append_message("Agent", text, worker_owner["value"])
            call add_notification("info", "Catalog refreshed: " + String(len(catalog["offers"])) + " active offers", "", worker_owner["value"])
            state["status"] = "Catalog refreshed"
            state["busy"] = False
            call trim_state()
            else:
                state["status"] = "Catalog refresh finished with a partial state update"
            end
            call unlock()
        else:
            turn_config = json.decode(json.encode(config))
            if work["budget"] > 0:
                turn_config["budget"] = work["budget"]
                if turn_config["budget"] > 100:
                    turn_config["budget"] = 100
                end
            end
            report = agent.turn(turn_config, account_chat(worker_owner["value"]), work["prompt"], progress, cancel_requested)
            call lock()
            try:
            if state["messages"][len(state["messages"]) - 1]["content"] != report["reply"]:
                call append_message("Agent", report["reply"], worker_owner["value"])
            end
            # Agent se zastavil jen proto, že mu chybí vstup, který má jedině uživatel.
            if report.get("decision", "") == "needs_input":
                task = account_root_task(worker_owner["value"])["value"]
                if task == "":
                    task = work["prompt"]
                end
                state["question"] = {"id": "q-" + String(account_chat(worker_owner["value"])["turn"]), "text": report["reply"],
                    "options": report.get("options", []), "task": task, "who": worker_owner["value"], "time": time.time()}
                state["status"] = "Waiting for your answer"
                state["audit_revision"] += 1
            else:
                state["question"] = None
            end
            if report["session"] != None:
                state["wallet"] = report["session"]["wallet"]
            end
            state["status"] = "Ready · send another message"
            if state["question"] != None:
                state["status"] = "Waiting for your answer"
            end
            state["busy"] = False
            call trim_state()
            else:
                state["status"] = "Turn finished with a partial state update"
            end
            call unlock()
        end
    else:
        # Syrové chyby API nevypisujeme kvůli možným citlivým údajům.
        call lock()
        try:
            call append_message("Agent", "The request could not be completed. Check the connection and try again. If a purchase was already in progress, its state is on the marketplace.", worker_owner["value"])
            call add_notification("error", "A task could not be completed — nothing was paid", "", worker_owner["value"])
            state["status"] = "Connection or agent-decision error"
            state["busy"] = False
        else:
            state["status"] = "Task failed; nothing was paid"
            state["busy"] = False
        end
        call unlock()
    end
    call atomic_xchg(flags, 1, 1)
    call workspaces_save(True)
    # Běžící worker končí; od této chvíle smí hlavní vlákno znovu gc().
    call atomic_xchg(running, 0, 0)
end

function start(kind, prompt, owner=""):
    call atomic_xchg(flags, 1, 0)
    call atomic_xchg(flags, 2, 0)
    work["kind"] = kind
    work["prompt"] = prompt
    work["budget"] = budget_holder["value"]
    worker_owner["value"] = owner
    call lock()
    state["busy"] = True
    state["status"] = "Fetching the current offers…"
    if kind == "turn":
        call append_message("You", prompt, owner)
    end
    call workspaces_save(True)
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

function snapshot(lite=False, who=""):
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
            if owned(message, who):
                messages.append({"role": message["role"], "content": message["content"]})
            end
        end
        for entry in state["tools"]:
            if owned(entry, who):
                tools.append(entry)
            end
        end
        for entry in state["payments"]:
            if owned(entry, who):
                payments.append(entry)
            end
        end
        for entry in state["notifications"]:
            if owned(entry, who):
                notifications.append(entry)
            end
        end
    end
    wallet = None
    active = state["wallet"]
    if who in account_wallets:
        active = account_wallets[who]
    else:
        active = None
    end
    if who == "" and worker_owner["value"] == "":
        active = state["wallet"]
    end
    if active != None:
        wallet = {"available": active["available"], "locked": active["locked"]}
    end
    budget = state["budget"]
    if who in account_budgets:
        budget = account_budgets[who]
    elif who != "":
        budget = user_budget(who)
    end
    question = None
    if state["question"] != None and owned(state["question"], who):
        question = {"id": state["question"]["id"], "text": state["question"]["text"],
                    "options": state["question"]["options"], "time": state["question"]["time"]}
    end
    busy = state["busy"]
    status = state["status"]
    if who != "" and who != worker_owner["value"]:
        if busy:
            status = "Agent busy · another account is running a task"
        else:
            status = "Ready · send another message"
        end
    end
    view = {"ok": True, "currency": "USD", "simulated_payments": True, "ai_model": "flash", "question": question, "tools": tools, "payments": payments, "messages": messages, "wallet": wallet, "budget": budget, "status": status, "busy": busy, "messages_revision": state["messages_revision"], "audit_revision": state["audit_revision"], "tool_count": len(state["tools"]), "payment_count": len(state["payments"]), "notifications": notifications, "notifications_revision": state["notifications_revision"], "notification_count": len(state["notifications"]), "time": time.time()}
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

function reset_conversation(who=""):
    call lock()
    # Nová konverzace pro daný účet: jeho historie i session ID se zahodí.
    own = account_chat(who)
    own["history"] = []
    own["session_id"] = ""
    own["turn"] = 0
    account_root_task(who)["value"] = ""
    kept = [{"role": "Agent", "content": greeting, "who": ""}]
    for message in state["messages"]:
        if not owned(message, who):
            kept.append(message)
        end
    end
    state["messages"] = kept
    kept_tools = []
    for entry in state["tools"]:
        if not owned(entry, who):
            kept_tools.append(entry)
        end
    end
    state["tools"] = kept_tools
    kept_payments = []
    for entry in state["payments"]:
        if not owned(entry, who):
            kept_payments.append(entry)
        end
    end
    state["payments"] = kept_payments
    if state["question"] != None and String(state["question"].get("who", "")) == who and who != "":
        state["question"] = None
    end
    if who != "":
        account_wallets.pop(who, None)
        account_budgets.pop(who, None)
    end
    state["status"] = "New conversation · the wallet is created on the first purchase"
    state["messages_revision"] += 1
    state["audit_revision"] += 1
    call unlock()
    call workspaces_save(True)
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
        call send_json(client, 200, snapshot(raw.find("lite=1") >= 0, authenticated(request)))
        return
    end
    if target == "/api/health" and method == "GET":
        call send_json(client, 200, {"ok": True, "payments": "simulated USD"})
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
    if method == "POST" and (target == "/api/answer" or target == "/api/logout" or target == "/api/welcome-seen" or target == "/api/topup"
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
        if target == "/api/answer":
            payload = None
            if type(request.get("body_text", None)) == "String":
                try:
                    payload = json.decode(request["body_text"])
                else:
                    payload = None
                end
            end
            question_id = None
            answer = None
            if type(payload) == "Dictionary":
                question_id = payload.get("question_id")
                answer = payload.get("answer")
            end
            call lock()
            pending = state["question"]
            call unlock()
            if pending == None:
                call send_error_json(client, 409, "no question is waiting for an answer")
                return
            end
            if type(question_id) == "String" and question_id != "" and question_id != String(pending["id"]):
                call send_error_json(client, 409, "this question is no longer open")
                return
            end
            if String(pending.get("who", "")) != "" and String(pending.get("who", "")) != who:
                call send_error_json(client, 409, "this question belongs to another account")
                return
            end
            if type(answer) != "String" or len(answer.strip()) == 0 or len(answer) > 2000:
                call send_error_json(client, 400, "the answer must have 1 to 2000 characters")
                return
            end
            if busy_now():
                call send_error_json(client, 409, "The agent is still working; wait for it to finish.")
                return
            end
            budget_holder["value"] = user_budget(who)
            if account_root_task(who)["value"] == "":
                account_root_task(who)["value"] = String(pending["task"])
            end
            prompt = "Original task: " + String(pending["task"]) + chr(10) + "Your question: " + String(pending["text"]) + chr(10) + "The user's answer: " + answer.strip() + chr(10) + "Now carry out the original task completely and autonomously. Ask again only if another required input is genuinely missing."
            call lock()
            state["question"] = None
            call append_message("You", answer.strip())
            call unlock()
            call start("resume", prompt, who)
            call send_json(client, 200, {"ok": True, "account": who})
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
            session_id = ensure_session(who)
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
                call send_error_json(client, 400, "top-up amount must be a whole number from 1 to 500 USD")
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
            session_id = account_chat(who)["session_id"]
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
                    state["budget"] = topped["budget"]
                    account_wallets[who] = wallet
                    account_budgets[who] = topped["budget"]
                    call unlock()
                end
            end
            call lock()
            call add_notification("info", "Account topped up: +" + usd(amount) + " (budget " + usd(new_budget) + ")", "", who)
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
            call reset_conversation(who)
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
            call start("catalog", "", who)
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
        account_root_task(who)["value"] = message
        call start("turn", message, who)
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
call workspaces_load()
try:
    call serve()
else:
    print "Web chat server failed: " + String(last_error())
    exit(1)
end
