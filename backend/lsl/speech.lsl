# Speech sidecar v LSL: ElevenLabs převod textu na řeč a přepis nahrávky.
#
# Binární data záměrně neprocházejí LSL řetězci: MP3 stahuje curl přímo do
# cache (tu servíruje Caddy) a nahrávku dekóduje openssl base64. Klíč se
# předává curlu konfiguračním souborem, aby se neobjevil v argumentech.

load httpserver as web
load process
load env
load json
load time
load fd

key_file = env.get("ELEVENLABS_KEY_FILE", "/etc/proofpay-mvp/elevenlabs.key")
cache_dir = env.get("TTS_CACHE_DIR", "/var/lib/proofpay-mvp/tts")
work_dir = env.get("TTS_WORK_DIR", "/var/lib/proofpay-mvp/tts-work")
port = Int(env.get("TTS_PORT", "3071"))
voice = env.get("TTS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
model = env.get("TTS_MODEL_ID", "eleven_v4")
max_chars = Int(env.get("TTS_MAX_CHARS", "2000"))
console_url = env.get("TTS_CONSOLE_URL", "http://127.0.0.1:3069")
receipt_url = env.get("TTS_RECEIPT_URL", "http://127.0.0.1:3070/api/receipt/")
api_url = "https://api.elevenlabs.io/v1/text-to-speech/"
stt_url = "https://api.elevenlabs.io/v1/speech-to-text"
max_audio = 8000000
cooldown = 2.0
guard = {"last_call": 0.0, "temp": 0}
key = ""
try:
    key = readfile(key_file).strip()
else:
    key = ""
end

function file_exists(path):
    outcome = process.capture_result("/usr/bin/test", ["-f", path])
    return outcome["ok"]
end

function send_json(client, status, value):
    body = json.encode(value)
    call web.send_response(client, status, web.status_reason(status), body, "application/json; charset=utf-8", False)
end

function text_of(value):
    return String(value).strip()
end

function joined(items, separator):
    text = ""
    for item in items:
        if text != "":
            text += separator
        end
        text += String(item)
    end
    return text
end

function spoken_text(raw):
    # Vyhodí bloky ```…``` (liché díly jsou kód), odkazy a prázdné řádky.
    parts = String(raw).split("```")
    kept = []
    index = 0
    for part in parts:
        if index % 2 == 0:
            kept.append(part)
        end
        index += 1
    end
    text = joined(kept, " ")
    words = []
    for word in text.split(" "):
        if not word.startswith("http"):
            words.append(word)
        end
    end
    text = joined(words, " ")
    text = joined(text.split("•"), ". ")
    lines = []
    for line in text.split(chr(10)):
        clean = line.strip()
        if clean != "":
            lines.append(clean)
        end
    end
    text = joined(lines, chr(10)).strip()
    if len(text) > max_chars:
        text = text[0:max_chars]
    end
    return text
end

function http_get(url, bearer):
    arguments = ["-sS", "-m", "20", "-H", "Accept: application/json"]
    if bearer != "":
        arguments.append("-H")
        arguments.append("Authorization: " + bearer)
    end
    arguments.append(url)
    outcome = process.capture_result("/usr/bin/curl", arguments)
    if not outcome["ok"]:
        return None
    end
    try:
        return json.decode(String(outcome["stdout"]))
    else:
        return None
    end
end

function console_message(index, bearer):
    state = http_get(console_url + "/api/state", bearer)
    if type(state) != "Dictionary":
        return None
    end
    messages = state.get("messages", [])
    if type(index) != "Int" or index < 0 or index >= len(messages):
        return None
    end
    message = messages[index]
    if String(message.get("role", "")) != "Agent":
        return None
    end
    return String(message.get("content", ""))
end

function job_text(job_id):
    receipt = http_get(receipt_url + job_id, "")
    if type(receipt) != "Dictionary":
        return None
    end
    result = receipt.get("job", {}).get("result", None)
    if type(result) != "Dictionary":
        result = {}
    end
    artifact = result.get("artifact", None)
    if type(artifact) != "Dictionary":
        artifact = {}
    end
    parts = []
    if artifact.get("summary", "") != "":
        parts.append(String(artifact["summary"]))
    end
    if artifact.get("content", "") != "":
        parts.append(String(artifact["content"]))
    end
    checks = result.get("checks", [])
    if type(checks) == "List" and len(checks) > 0:
        passed = 0
        sentences = []
        for check in checks:
            line = String(check.get("case_id", "check"))
            if Bool(check.get("passed", False)):
                passed += 1
                line += " passed, expected " + money_cents(check.get("expected_cents", 0))
            else:
                line += " failed: expected " + money_cents(check.get("expected_cents", 0)) + ", observed " + money_cents(check.get("observed_cents", 0))
            end
            sentences.append(line)
        end
        parts.append("Cart audit: " + String(passed) + " of " + String(len(checks)) + " checks passed.")
        parts.append(joined(sentences, ". ") + ".")
    end
    ideas = artifact.get("ideas", [])
    if type(ideas) == "List" and len(ideas) > 0:
        numbered = []
        position = 1
        for idea in ideas:
            if type(idea) == "String":
                numbered.append(String(position) + ". " + idea)
                position += 1
            end
        end
        parts.append(joined(numbered, " "))
    end
    return joined(parts, chr(10) + chr(10))
end

function money_cents(cents):
    whole = Int(cents) / 100
    rest = Int(cents) % 100
    tail = String(rest)
    if rest < 10:
        tail = "0" + tail
    end
    return "$" + String(whole) + "." + tail
end

function cache_path(text):
    digest = ""
    try:
        outcome = process.capture_input_result("/usr/bin/openssl", ["dgst", "-sha256", "-r"], voice + "|" + model + "|" + text)
        if outcome["ok"]:
            digest = String(outcome["stdout"]).strip().split(" ")[0]
        end
    else:
        digest = ""
    end
    if len(digest) != 64:
        return ""
    end
    return cache_dir + "/" + digest + ".mp3"
end

function synthesize(text, path):
    # Zdvořilá prodleva mezi voláními, ať se API nezaplaví.
    wait = cooldown - (time.time() - guard["last_call"])
    if wait > 0:
        call time.sleep(wait)
    end
    guard["last_call"] = time.time()
    guard["temp"] += 1
    config_path = work_dir + "/curl-" + String(guard["temp"]) + ".cfg"
    body = json.encode({"text": text, "model_id": model})
    call writefile(config_path, 'header = "xi-api-key: ' + key + '"' + chr(10))
    arguments = ["-sS", "-m", "90", "-K", config_path, "-X", "POST",
        "-H", "Content-Type: application/json", "-H", "Accept: audio/mpeg",
        "-d", body, "-o", path + ".tmp", api_url + voice + "?output_format=mp3_44100_128"]
    outcome = process.capture_result("/usr/bin/curl", arguments)
    try:
        call process.capture_result("/bin/rm", ["-f", config_path])
    else:
        call time.time()
    end
    if not outcome["ok"]:
        return "ElevenLabs unavailable"
    end
    # Přesunout až po úspěšném stažení; neúplný soubor nikdy neposloužíme.
    if len(String(outcome["stderr"])) > 0 and not file_exists(path + ".tmp"):
        return "ElevenLabs error: " + String(outcome["stderr"])[0:200]
    end
    moved = process.capture_result("/bin/mv", [path + ".tmp", path])
    if not moved["ok"]:
        return "cached file could not be stored"
    end
    return ""
end

function speak(request, client, payload):
    if key == "":
        call send_json(client, 503, {"error": "Text-to-speech is not configured on this server."})
        return
    end
    bearer = String(request["headers"].get("authorization", ""))
    job_id = String(payload.get("job_id", ""))
    if job_id != "":
        raw = job_text(job_id)
        if raw == None:
            call send_json(client, 502, {"error": "Delivery is unavailable."})
            return
        end
        text = spoken_text(raw)
    else:
        index = payload.get("index", None)
        message = console_message(index, bearer)
        if message == None:
            call send_json(client, 400, {"error": "index must point at an agent message"})
            return
        end
        text = spoken_text(message)
    end
    if len(text) < 5:
        call send_json(client, 400, {"error": "message has nothing to read aloud"})
        return
    end
    path = cache_path(text)
    if path == "":
        call send_json(client, 500, {"error": "cache key could not be computed"})
        return
    end
    cached = file_exists(path)
    if not cached:
        problem = synthesize(text, path)
        if problem != "":
            call send_json(client, 502, {"error": problem})
            return
        end
    end
    name = path.split("/")[len(path.split("/")) - 1]
    call send_json(client, 200, {"ok": True, "url": "/web/tts/" + name, "cached": cached, "chars": len(text)})
end

function transcribe(request, client, payload):
    if key == "":
        call send_json(client, 503, {"error": "Speech-to-text is not configured on this server."})
        return
    end
    encoded = String(payload.get("audio_base64", ""))
    if len(encoded) < 16 or len(encoded) > max_audio:
        call send_json(client, 413, {"error": "audio must have 1 byte to 8 MB"})
        return
    end
    guard["temp"] += 1
    suffix = String(guard["temp"])
    encoded_path = work_dir + "/upload-" + suffix + ".b64"
    audio_path = work_dir + "/upload-" + suffix + ".webm"
    config_path = work_dir + "/curl-" + suffix + ".cfg"
    call writefile(encoded_path, encoded)
    call writefile(config_path, 'header = "xi-api-key: ' + key + '"' + chr(10))
    decoded = process.capture_result("/usr/bin/openssl", ["base64", "-d", "-A", "-in", encoded_path, "-out", audio_path])
    if not decoded["ok"] or not file_exists(audio_path):
        call cleanup([encoded_path, audio_path, config_path])
        call send_json(client, 400, {"error": "audio could not be decoded"})
        return
    end
    arguments = ["-sS", "-m", "180", "-K", config_path, "-X", "POST",
        "-H", "Accept: application/json", "-F", "file=@" + audio_path, "-F", "model_id=scribe_v1", stt_url]
    outcome = process.capture_result("/usr/bin/curl", arguments)
    call cleanup([encoded_path, audio_path, config_path])
    if not outcome["ok"]:
        call send_json(client, 502, {"error": "Transcription failed"})
        return
    end
    try:
        data = json.decode(String(outcome["stdout"]))
    else:
        data = None
    end
    if type(data) != "Dictionary":
        call send_json(client, 502, {"error": "Transcription failed: " + String(outcome["stdout"])[0:200]})
        return
    end
    call send_json(client, 200, {"text": String(data.get("text", "")).strip()})
end

function cleanup(paths):
    for path in paths:
        try:
            call process.capture_result("/bin/rm", ["-f", path])
        else:
            call time.time()
        end
    end
end

function cached_count():
    outcome = process.capture_result("/bin/ls", [cache_dir])
    if not outcome["ok"]:
        return 0
    end
    count = 0
    for line in String(outcome["stdout"]).split(chr(10)):
        if line.strip().endswith(".mp3"):
            count += 1
        end
    end
    return count
end

function route(client, request):
    method = request["method"]
    raw = String(request["target"])
    query = raw.find("?")
    target = raw
    if query >= 0:
        target = raw[0:query]
    end
    if method == "GET" and target == "/health":
        count = 0
        try:
            count = cached_count()
        else:
            count = 0
        end
        call send_json(client, 200, {"ok": True, "configured": key != "", "model": model,
            "voice": voice, "cached": count, "runtime": "LSL"})
        return
    end
    if method != "POST":
        call send_json(client, 404, {"error": "route not found"})
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
    if type(payload) != "Dictionary":
        call send_json(client, 400, {"error": "JSON object required"})
        return
    end
    if target.endswith("/transcribe"):
        call transcribe(request, client, payload)
        return
    end
    if target == "/" or target.endswith("/speak"):
        call speak(request, client, payload)
        return
    end
    call send_json(client, 404, {"error": "route not found"})
end

function serve():
    web.set_body_limit(max_audio + 2000000)
    listener = web.listen_ipv4("127.0.0.1", port, 64)
    if listener < 0:
        Error(ServerError: "listen failed on port " + String(port))
    end
    print "ProofPay speech (LSL) listening on http://127.0.0.1:" + String(port) + "/ configured=" + String(key != "")
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
            try:
                call route(client, request)
            else:
                call send_json(client, 500, {"error": "internal error"})
            end
        end
        call fd.close(client)
    end
end

try:
    call serve()
else:
    print "speech sidecar could not start"
end
