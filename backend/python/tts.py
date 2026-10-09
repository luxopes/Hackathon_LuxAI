#!/usr/bin/env python3
"""Legacy ElevenLabs speech sidecar — the fallback, not the running service.

The deployed sidecar is backend/lsl/speech.lsl, which does the same work in LSL
(text to speech plus Scribe transcription). This Python version is kept so the
stack can fall back by pointing the systemd unit at it again; it reads a stored
message by index from the console state, renders audio/mpeg through the
ElevenLabs API and caches the result on disk, and it never accepts free text.

Environment:
  ELEVENLABS_KEY_FILE   key file (default /etc/proofpay-mvp/elevenlabs.key)
  TTS_PORT              listen port (default 3071)
  TTS_CONSOLE_URL       console base URL (default http://127.0.0.1:3069)
  TTS_CACHE_DIR         mp3 cache directory (default /var/lib/proofpay-mvp/tts)
  TTS_VOICE_ID          ElevenLabs voice id
  TTS_MODEL_ID          ElevenLabs model id (default eleven_v4)
  TTS_MAX_CHARS         maximum characters per request (default 2000)
"""
import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

KEY_FILE = Path(os.environ.get("ELEVENLABS_KEY_FILE", "/etc/proofpay-mvp/elevenlabs.key"))
PORT = int(os.environ.get("TTS_PORT", "3071"))
CONSOLE = os.environ.get("TTS_CONSOLE_URL", "http://127.0.0.1:3069")
CACHE = Path(os.environ.get("TTS_CACHE_DIR", "/var/lib/proofpay-mvp/tts"))
VOICE = os.environ.get("TTS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
MODEL = os.environ.get("TTS_MODEL_ID", "eleven_v4")
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "2000"))
API = "https://api.elevenlabs.io/v1/text-to-speech/"
MAX_AUDIO_BYTES = 8_000_000
COOLDOWN_SECONDS = 2.0
RECEIPT_URL = os.environ.get("TTS_RECEIPT_URL", "http://127.0.0.1:3070/api/receipt/")
STT_ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"
MAX_UPLOAD_BYTES = 8_000_000

KEY = KEY_FILE.read_text().strip() if KEY_FILE.is_file() else ""
CACHE.mkdir(parents=True, exist_ok=True)
guard = threading.Lock()
last_call = [0.0]


def spoken_text(message):
    """Strip code fences, links, file paths and squeeze whitespace for speech."""
    text = re.sub(r"```[\s\S]*?```", " ", message)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s*Saved:.*$", " ", text, flags=re.M)
    text = re.sub(r"^\s*(Python code|Tests|Basic tests):\s*$", " ", text, flags=re.M)
    text = text.replace("•", ".")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = text.strip()
    return text[:MAX_CHARS]


def console_message(index):
    request = Request(CONSOLE + "/api/state", headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        state = json.loads(response.read(2_000_001))
    messages = state.get("messages") or []
    if type(index) is not int or index < 0 or index >= len(messages):
        return None
    message = messages[index]
    if message.get("role") != "Agent":
        return None
    return message.get("content") or ""


def job_text(job_id):
    """Text zakoupené dodávky (shrnutí + obsah + nápady) pro předčítání."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", job_id or ""):
        return None
    request = Request(RECEIPT_URL + job_id, headers={"Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        receipt = json.loads(response.read(2_000_001))
    artifact = (receipt.get("job", {}).get("result") or {}).get("artifact") or {}
    parts = []
    if artifact.get("summary"):
        parts.append(str(artifact["summary"]))
    if artifact.get("content"):
        parts.append(str(artifact["content"]))
    ideas = artifact.get("ideas") or []
    if ideas:
        parts.append(" ".join(f"{index + 1}. {idea}" for index, idea in enumerate(ideas) if isinstance(idea, str)))
    return "\n\n".join(parts)


def transcribe_raw(body, content_type):
    """Přepis nahrávky přes ElevenLabs Scribe; multipart se posílá beze změny."""
    request = Request(STT_ENDPOINT, data=body,
                      headers={"xi-api-key": KEY, "Content-Type": content_type, "Accept": "application/json"})
    with urlopen(request, timeout=180) as response:
        data = json.loads(response.read(400_001))
    return (data.get("text") or "").strip()


def synthesize(text, path):
    with guard:
        wait = COOLDOWN_SECONDS - (time.time() - last_call[0])
        if wait > 0:
            time.sleep(wait)
        last_call[0] = time.time()
        body = json.dumps({"text": text, "model_id": MODEL}).encode()
        request = Request(API + VOICE + "?output_format=mp3_44100_128", data=body,
                          headers={"xi-api-key": KEY, "Content-Type": "application/json",
                                   "Accept": "audio/mpeg"})
        try:
            with urlopen(request, timeout=90) as response:
                audio = response.read(MAX_AUDIO_BYTES + 1)
        except HTTPError as error:
            detail = error.read(600).decode("utf-8", "replace")
            raise RuntimeError(f"ElevenLabs HTTP {error.code}: {detail[:300]}")
        except (URLError, OSError, ValueError) as error:
            raise RuntimeError(f"ElevenLabs unavailable: {type(error).__name__}")
    if len(audio) > MAX_AUDIO_BYTES:
        raise RuntimeError("Audio response too large")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(audio)
    os.replace(temporary, path)


class Handler(BaseHTTPRequestHandler):
    server_version = "ProofPayTTS/1"

    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_audio(self, audio):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(audio)

    def do_GET(self):
        if self.path == "/health":
            return self.send_json(200, {"ok": True, "configured": bool(KEY), "model": MODEL,
                                        "voice": VOICE, "cached": len(list(CACHE.glob("*.mp3")))})
        return self.send_json(404, {"error": "route not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path.endswith("/transcribe"):
            return self.transcribe()
        if not (path == "/" or path.endswith("/speak")):
            return self.send_json(404, {"error": "route not found"})
        if not KEY:
            return self.send_json(503, {"error": "Text-to-speech is not configured on this server."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                return self.send_json(413, {"error": "body too large"})
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            return self.send_json(400, {"error": "invalid JSON"})
        if type(payload) is not dict:
            return self.send_json(400, {"error": "JSON object required"})
        job_id = payload.get("job_id")
        if type(job_id) is str and job_id:
            try:
                raw = job_text(job_id)
            except (URLError, OSError, ValueError):
                return self.send_json(502, {"error": "Delivery is unavailable."})
            if raw is None:
                return self.send_json(400, {"error": "invalid job id"})
            text = spoken_text(raw)
            if len(text) < 5:
                return self.send_json(400, {"error": "this delivery has no readable text"})
        else:
            try:
                message = console_message(payload.get("index"))
            except (URLError, OSError, ValueError):
                return self.send_json(502, {"error": "Agent state is unavailable."})
            if message is None:
                return self.send_json(400, {"error": "index must point at an agent message"})
            text = spoken_text(message)
        if len(text) < 5:
            return self.send_json(400, {"error": "message has nothing to read aloud"})
        digest = hashlib.sha256(f"{VOICE}|{MODEL}|{text}".encode()).hexdigest()
        path = CACHE / (digest + ".mp3")
        if not path.is_file():
            try:
                synthesize(text, path)
            except RuntimeError as error:
                return self.send_json(502, {"error": str(error)})
        audio = path.read_bytes()
        return self.send_audio(audio)

    def transcribe(self):
        if not KEY:
            return self.send_json(503, {"error": "Speech-to-text is not configured on this server."})
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self.send_json(400, {"error": "multipart/form-data with an audio file is required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_json(400, {"error": "invalid body length"})
        if not 0 < length <= MAX_UPLOAD_BYTES:
            return self.send_json(413, {"error": "audio must have 1 byte to 8 MB"})
        body = self.rfile.read(length)
        with guard:
            try:
                text = transcribe_raw(body, content_type)
            except HTTPError as error:
                detail = error.read(400).decode("utf-8", "replace")
                return self.send_json(502, {"error": f"ElevenLabs HTTP {error.code}: {detail[:200]}"})
            except (URLError, OSError, ValueError) as error:
                return self.send_json(502, {"error": f"Transcription failed: {type(error).__name__}"})
        return self.send_json(200, {"text": text})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"ProofPay TTS listening on http://127.0.0.1:{PORT}/ configured={bool(KEY)}", flush=True)
    server.serve_forever()
