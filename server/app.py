"""Banco Zybo Z7-10 emulato -- server web locale (solo libreria standard di Python).

Avvio:  python -I server/app.py   ->  http://127.0.0.1:8765
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import signal
import queue
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from zyboemu import build, emulator  # noqa: E402

ROOT = HERE.parent
WEB = ROOT / "web"
WORK = ROOT / "work"
CONFIG = ROOT / "config.json"
HOST, PORT = "127.0.0.1", 8765

# cartella dei sorgenti mostrata nel pannello «Lab»: si cambia dall'interfaccia (salvata in config.json)
DEFAULT_LABS = ROOT / "examples"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"labs_dir": str(DEFAULT_LABS)}


# ---------------------------------------------------------------------------
# Bus degli eventi verso i browser (Server-Sent Events)
# ---------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.last_state = {"t": "state", "state": "off"}
        self.last_codec: dict | None = None
        self.last_out: dict[str, dict] = {}
        self._pending_out: dict[str, dict] = {}
        self._uart_buf = bytearray()
        self._log_count = 0
        threading.Thread(target=self._flusher, daemon=True).start()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self.lock:
            self.clients.append(q)
        # stato iniziale per chi si collega
        q.put(self.last_state)
        for e in self.last_out.values():
            q.put(e)
        if self.last_codec:
            q.put(self.last_codec)        # chi apre la pagina a programma avviato vede comunque lo stato del codec
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, e: dict):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(e)
            except queue.Full:
                pass

    # --- callback dall'emulatore ---
    def on_event(self, e: dict):
        t = e.get("t")
        if t == "state":
            self.last_state = e
            if e.get("state") in ("running", "booting"):
                self.last_out.clear()
        if t == "codec":
            self.last_codec = e
        if t in ("out", "pin"):
            key = e.get("dev", "") + str(e.get("n", ""))
            self.last_out[key] = e
            with self.lock:
                self._pending_out[key] = e      # coalescenza: al massimo ~60 aggiornamenti/s per uscita
            return
        self.publish(e)

    def on_uart(self, data: bytes):
        with self.lock:
            self._uart_buf.extend(data)

    def on_log(self, level: str, text: str):
        if level == "info":
            return
        self._log_count += 1
        if self._log_count > 3000:
            return
        self.publish({"t": "log", "level": level, "text": text})

    def _flusher(self):
        while True:
            time.sleep(1 / 60)
            with self.lock:
                outs = list(self._pending_out.values())
                self._pending_out.clear()
                data = bytes(self._uart_buf)
                self._uart_buf.clear()
            for e in outs:
                self.publish(e)
            if data:
                self.publish({"t": "uart", "b64": base64.b64encode(data).decode()})


HUB = Hub()
EMU = emulator.Emulator(HUB.on_event, HUB.on_uart, HUB.on_log)
BUILDS: dict[str, pathlib.Path] = {}
SENSOR: dict = {"pitch": 0.0, "roll": 0.0, "temp": 28.5, "noise": 1.0, "whoami": 0x72, "mount": "jd"}
# segnale su LINE IN (Lab 3): sinusoide a 1000 Hz, volume basso, come nel testo del lab
AUDIO: dict = {"type": "sine", "freq": 1000.0, "level": -12.0}


def run_elf(elf) -> bool:
    """Programma la scheda: orologio scelto in config.json ("auto", "real" o "cpu")."""
    cfg = load_config()
    return EMU.run(elf, SENSOR, clock=cfg.get("clock", "auto"), audio=AUDIO, timing=cfg.get("timing"))


def boot_async():
    def _b():
        try:
            EMU.boot()
        except Exception:
            HUB.on_log("error", traceback.format_exc())
    threading.Thread(target=_b, daemon=True).start()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ZyboEmu/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- helpers ---
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _body_json(self) -> dict:
        try:
            return json.loads(self._body().decode("utf-8") or "{}")
        except ValueError:
            return {}

    def _local_only(self) -> bool:
        # protezione minima: niente richieste da altri siti (CSRF) verso il server locale
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            self._json({"error": "origine non consentita"}, 403)
            return False
        return True

    # --- GET ---
    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p == "/api/events":
            return self._sse()
        if p == "/api/status":
            cfg = load_config()
            return self._json({"state": EMU.state, "sensor": SENSOR, "labs_dir": cfg.get("labs_dir"),
                               "clock": cfg.get("clock", "auto"), "clock_now": EMU.clock, "audio": AUDIO})
        if p == "/api/labs":
            return self._list_labs()
        if p == "/api/labfile":
            return self._read_lab(parse_qs(url.query).get("path", [""])[0])
        if p == "/api/disasm":
            q = parse_qs(url.query)
            elf = BUILDS.get(q.get("id", [""])[0])
            if not elf:
                return self._json({"error": "compila prima il programma"}, 404)
            return self._json({"text": build.disassemble(elf, q.get("fn", ["main"])[0])})
        if p == "/api/elf":
            elf = BUILDS.get(parse_qs(url.query).get("id", [""])[0])
            if not elf or not elf.exists():
                return self._json({"error": "nessun ELF"}, 404)
            data = elf.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="app.elf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # file statici
        rel = "index.html" if p in ("/", "") else p.lstrip("/")
        f = (WEB / rel).resolve()
        if WEB.resolve() not in f.parents and f != WEB.resolve() or not f.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.subscribe()
        try:
            while True:
                try:
                    e = q.get(timeout=15)
                    batch = [e]
                    while len(batch) < 200:
                        try:
                            batch.append(q.get_nowait())
                        except queue.Empty:
                            break
                    payload = "".join("data: " + json.dumps(x) + "\n\n" for x in batch)
                except queue.Empty:
                    payload = ": ping\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def _list_labs(self):
        base = pathlib.Path(load_config().get("labs_dir", str(DEFAULT_LABS)))
        files = []
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.suffix.lower() in (".c", ".h", ".s") and f.is_file():
                    files.append({"path": str(f), "name": str(f.relative_to(base)).replace("\\", "/"),
                                  "size": f.stat().st_size})
        return self._json({"base": str(base), "files": files[:500]})

    def _read_lab(self, path: str):
        base = pathlib.Path(load_config().get("labs_dir", str(DEFAULT_LABS))).resolve()
        f = pathlib.Path(path).resolve()
        if base not in f.parents or f.suffix.lower() not in (".c", ".h", ".s") or not f.is_file():
            return self._json({"error": "file non consentito"}, 403)
        return self._json({"name": f.name, "text": f.read_text(encoding="utf-8", errors="replace")})

    # --- POST ---
    def do_POST(self):
        if not self._local_only():
            return
        p = urlparse(self.path).path
        try:
            if p == "/api/build":
                return self._build(run=False)
            if p == "/api/buildrun":
                return self._build(run=True)
            if p == "/api/run":
                d = self._body_json()
                elf = BUILDS.get(d.get("id", ""))
                if not elf:
                    return self._json({"error": "compila prima il programma"}, 400)
                ok = run_elf(elf)
                return self._json({"ok": ok})
            if p == "/api/upload-elf":
                return self._upload_elf()
            if p == "/api/control":
                a = self._body_json().get("action")
                if a == "pause":
                    EMU.pause()
                elif a == "resume":
                    EMU.resume()
                elif a == "stop":
                    EMU.halt()
                elif a == "reset" and EMU.elf:
                    run_elf(EMU.elf)
                elif a == "reboot":
                    EMU.shutdown()
                    boot_async()
                return self._json({"ok": True, "state": EMU.state})
            if p == "/api/input":
                d = self._body_json()
                kind, idx, val = d.get("kind"), int(d.get("idx", 0)), bool(d.get("value"))
                bounce, bus = int(d.get("bounce", 0)), int(d.get("bounceUs", 0))
                if kind == "btn" and 0 <= idx < 4:
                    EMU.set_button(idx, val, bounce, bus)
                elif kind == "sw" and 0 <= idx < 4:
                    EMU.set_switch(idx, val, bounce, bus)
                elif kind == "psbtn" and idx in (50, 51):
                    EMU.set_ps_button(idx, val)
                return self._json({"ok": True})
            if p == "/api/sensor":
                d = self._body_json()
                for k in ("pitch", "roll", "temp", "noise"):
                    if k in d:
                        SENSOR[k] = float(d[k])
                if "whoami" in d:
                    SENSOR["whoami"] = int(d["whoami"]) & 0xFF
                if d.get("mount") in ("jd", "flat"):
                    SENSOR["mount"] = d["mount"]
                send = {k: v for k, v in d.items() if k in ("pitch", "roll", "temp", "noise", "whoami", "mount", "tap")}
                if "pitch" in send or "roll" in send:
                    send["pitch"], send["roll"] = SENSOR["pitch"], SENSOR["roll"]
                if EMU.state in ("running", "paused", "idle"):
                    EMU.set_sensor(send)
                return self._json({"ok": True, "sensor": SENSOR})
            if p == "/api/audioin":
                d = self._body_json()
                if d.get("type") in ("sine", "square", "sweep", "noise", "silence"):
                    AUDIO["type"] = d["type"]
                if "freq" in d:
                    AUDIO["freq"] = min(23000.0, max(20.0, float(d["freq"])))
                if "level" in d:
                    AUDIO["level"] = min(0.0, max(-90.0, float(d["level"])))
                if EMU.state in ("running", "paused", "idle"):
                    EMU.set_audio_input(AUDIO)
                return self._json({"ok": True, "audio": AUDIO})
            if p == "/api/uart":
                text = self._body_json().get("text", "")
                EMU.uart_write(text.encode("utf-8"))
                return self._json({"ok": True})
            if p == "/api/config":
                d = self._body_json()
                cfg = load_config()
                if "labs_dir" in d:
                    s = str(d["labs_dir"]).strip().strip('"').strip("'")
                    if os.name != "nt":
                        s = s.replace("\\ ", " ")      # percorso trascinato nel Terminale del Mac
                    p = pathlib.Path(s).expanduser()
                    if not p.is_dir():
                        return self._json({"error": f"Cartella non trovata: {p}"}, 400)
                    cfg["labs_dir"] = str(p.resolve())
                if d.get("clock") in ("auto", "real", "cpu"):
                    cfg["clock"] = d["clock"]
                CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                return self._json({"ok": True})
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as ex:  # errore interno: lo riporto all'interfaccia
            HUB.on_log("error", traceback.format_exc())
            return self._json({"error": str(ex)}, 500)

    def _build(self, run: bool):
        d = self._body_json()
        files = d.get("files") or {}
        if not isinstance(files, dict) or not files:
            return self._json({"ok": False, "log": "Nessun file da compilare."})
        opts = build.BuildOptions.from_dict(d.get("options") or {})
        bid = uuid.uuid4().hex[:10]
        res = build.build({str(k): str(v) for k, v in files.items()}, WORK / "builds" / bid, opts)
        if res.ok and res.elf:
            BUILDS[bid] = res.elf
            # tengo solo le ultime build
            for old in list(BUILDS)[:-8]:
                BUILDS.pop(old, None)
        started = False
        if run and res.ok and res.elf:
            started = run_elf(res.elf)
        return self._json({"ok": res.ok, "id": bid if res.ok else None, "log": res.log, "size": res.size,
                           "seconds": round(res.seconds, 2), "commands": res.commands, "started": started})

    def _upload_elf(self):
        data = self._body()
        if len(data) < 52 or data[:4] != b"\x7fELF":
            return self._json({"ok": False, "error": "Il file non e' un ELF."})
        if data[4] != 1 or data[5] != 1 or int.from_bytes(data[18:20], "little") != 40:
            return self._json({"ok": False, "error": "L'ELF non e' ARM 32 bit little-endian (Cortex-A9)."})
        entry = int.from_bytes(data[24:28], "little")
        bid = "elf" + uuid.uuid4().hex[:8]
        d = WORK / "builds" / bid
        d.mkdir(parents=True, exist_ok=True)
        elf = d / "app.elf"
        elf.write_bytes(data)
        BUILDS[bid] = elf
        warn = "" if 0x00100000 <= entry < 0x40000000 else (
            f"Attenzione: entry point 0x{entry:08X} fuori dalla DDR (atteso 0x00100000).")
        started = run_elf(elf)
        return self._json({"ok": True, "id": bid, "entry": f"0x{entry:08X}", "warning": warn, "started": started})


class ExclusiveServer(ThreadingHTTPServer):
    """Su Windows SO_REUSEADDR lascerebbe partire due server sulla stessa porta:
    chiedo l'uso esclusivo, cosi' un secondo avvio se ne accorge.
    Su macOS/Linux invece SO_REUSEADDR serve per riavviare subito il banco (porta in TIME_WAIT)
    e non permette comunque due server in ascolto insieme."""
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # il browser che chiude o ricarica la pagina interrompe le connessioni: e' normale
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    WORK.mkdir(exist_ok=True)
    url = f"http://{HOST}:{PORT}/"
    try:
        srv = ExclusiveServer((HOST, PORT), Handler)
    except OSError:
        # porta occupata: se e' gia' acceso un banco, apro solo la pagina
        try:
            import urllib.request
            with urllib.request.urlopen(url + "api/status", timeout=3) as r:
                r.read()
            print(f"Il banco Zybo e' gia' acceso: apro {url}")
            if "--no-browser" not in sys.argv:
                webbrowser.open(url)
            time.sleep(2)
            sys.exit(0)
        except OSError:
            print(f"La porta {PORT} e' occupata da un altro programma.")
            raise
    srv.daemon_threads = True

    def _quit(*_):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):            # chiusura del Terminale su macOS/Linux
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _quit)

    print(f"Banco Zybo Z7-10 emulato: {url}")
    print("Avvio dell'emulatore Renode in corso (circa 15-40 s la prima volta)...")
    print("Per spegnere tutto: chiudi questa finestra oppure premi Ctrl+C.")
    boot_async()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        EMU.shutdown()


if __name__ == "__main__":
    main()
