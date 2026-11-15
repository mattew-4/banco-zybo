"""Prova da riga di comando: compila un .c, lo esegue nell'emulatore per N secondi
e stampa seriale, eventi e avvisi. Uso: python -I selftest.py file.c [secondi] [azioni] [-Ox]

azioni (opzionali), separate da ';', es. "1.0:sw 0 1;2.0:btn 0 1;2.2:btn 0 0;3:tilt 30 0"
--expect "testo"  (ripetibile): esce con codice 1 se il testo non compare sul Serial Monitor
"""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from zyboemu import build, emulator  # noqa: E402

def _opt(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        v = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return v
    return default


expect = []
while "--expect" in sys.argv:
    expect.append(_opt("--expect"))
expect_not = []                          # testi che NON devono comparire (es. dopo un Data Abort)
while "--expect-not" in sys.argv:
    expect_not.append(_opt("--expect-not"))
expect_abort = "--expect-abort" in sys.argv
if expect_abort:
    sys.argv.remove("--expect-abort")
clock = _opt("--clock", "auto")          # auto | real | cpu
timing = {}                              # --timing chiave=valore (taratura, es. cpi=1.4 cache=0)
while "--timing" in sys.argv:
    k, _, v = _opt("--timing").partition("=")
    timing[k] = float(v)
audio_in = _opt("--input")               # es. "sine 1000 -12"

opt = _opt("--opt")

src = pathlib.Path(sys.argv[1])
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6
actions = sys.argv[3] if len(sys.argv) > 3 and ":" in sys.argv[3] else ""
if opt is None:
    opt = next((a for a in sys.argv[3:] if a.startswith("-O")), "-O2")

# come un progetto dell'IDE: il .c piu' gli header della sua cartella
files = {src.name: src.read_text(encoding="utf-8", errors="replace")}
for h in src.resolve().parent.glob("*.h"):
    files[h.name] = h.read_text(encoding="utf-8", errors="replace")

work = pathlib.Path(__file__).resolve().parents[1] / "work" / "selftest"
res = build.build(files, work, build.BuildOptions(opt=opt))
print("== BUILD ok=%s (%.1fs)\n%s\n%s" % (res.ok, res.seconds, res.log, res.size))
if not res.ok:
    sys.exit(1)

uart = bytearray()
events = []
logs = []
lock = threading.Lock()


audio = {"chunks": 0, "seconds": 0.0, "maxocc": 0, "ovf_bins": 0, "last": None, "codec": None, "pcm": []}


def _fft(x):
    n = len(x)
    if n == 1:
        return x
    even, odd = _fft(x[0::2]), _fft(x[1::2])
    import cmath
    t = [cmath.exp(-2j * cmath.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + t[k] for k in range(n // 2)] + [even[k] - t[k] for k in range(n // 2)]


def analyze_headphones(pcm, rate=24000, n=8192):
    """Livello, frequenza dominante e THD+N (dB) delle ultime n uscite delle cuffie."""
    import math
    if len(pcm) < n:
        return None
    x = pcm[-n:]
    rms = math.sqrt(sum(v * v for v in x) / n)
    if rms < 1e-6:
        return {"rms_db": -120.0, "freq": 0.0, "thdn_db": None}
    w = [x[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / n)) for i in range(n)]
    p = [abs(c) ** 2 for c in _fft(w)[: n // 2]]
    k = max(range(2, n // 2), key=lambda i: p[i])
    sig = sum(p[max(0, k - 4):k + 5])
    rest = sum(p[2:]) - sig
    return {"rms_db": 20 * math.log10(rms), "freq": k * rate / n,
            "thdn_db": 10 * math.log10(max(rest, 1e-30) / max(sig, 1e-30))}


def on_event(e):
    with lock:
        if e.get("t") == "uart":
            import base64
            uart.extend(base64.b64decode(e["b64"]))
            return
        if e.get("t") == "audio":
            import base64
            raw = base64.b64decode(e.get("pcm", ""))
            audio["pcm"].extend(int.from_bytes(raw[i:i + 2], "little", signed=True) / 32768 for i in range(0, len(raw) - 1, 2))
            del audio["pcm"][:-16384]
            audio["chunks"] += 1
            audio["seconds"] += len(e.get("pcm", "")) * 3 / 4 / 2 / e.get("rate", 24000)
            occ = e.get("occ", [])
            if occ:
                audio["maxocc"] = max(audio["maxocc"], max(abs(v) if v >= 0 else -v - 1 for v in occ))
                audio["ovf_bins"] += sum(1 for v in occ if v < 0)
            audio["last"] = e
            return
        if e.get("t") == "codec":
            audio["codec"] = e.get("regs")
            return
        events.append((time.time(), e))


def on_uart(b):
    with lock:
        uart.extend(b)


def on_log(level, text):
    with lock:
        logs.append((level, text))


emu = emulator.Emulator(on_event, on_uart, on_log)
t_start = time.time()
ok = emu.boot()
print("== BOOT Renode ok=%s in %.1fs" % (ok, time.time() - t_start))
ain = None
if audio_in and audio_in.startswith("file "):
    # audio reale da un WAV: "file percorso.wav [guadagno dB]"
    from zyboemu import audiofile
    parts = audio_in[5:].strip().rsplit(" ", 1)
    wav, gain = (parts[0], float(parts[1])) if len(parts) == 2 and parts[1].lstrip("-").replace(".", "").isdigit() \
        else (audio_in[5:].strip(), 0.0)
    info = audiofile.wav_to_raw(pathlib.Path(wav).read_bytes(), work / "audio_in.raw")
    info["path"] = str(work / "audio_in.raw")
    print("== INGRESSO: file %s, %d Hz, %d canali, %.2f s" % (wav, info["rate"], info["channels"], info["seconds"]))
    ain = {"type": "file", "freq": 1000, "level": gain, "file": info}
elif audio_in:
    k, f, lv = (audio_in.split() + ["1000", "-12"])[:3]
    ain = {"type": k, "freq": float(f), "level": float(lv)}
ok = emu.run(res.elf, clock=clock, audio=ain, timing=timing)
print("== OROLOGIO:", emu.clock, "| percorso audio nel programma:", emulator.uses_audio(res.elf),
      "| modello delle cache:", emu.cache_model)
t0 = time.time()
plan = []
for a in filter(None, actions.split(";")):
    at, cmd = a.split(":", 1)
    plan.append((float(at), cmd.split()))
while time.time() - t0 < secs:
    now = time.time() - t0
    while plan and plan[0][0] <= now:
        _, c = plan.pop(0)
        if c[0] == "sw":
            emu.set_switch(int(c[1]), c[2] == "1", int(c[3]) if len(c) > 3 else 0, 3000)
        elif c[0] == "btn":
            emu.set_button(int(c[1]), c[2] == "1", int(c[3]) if len(c) > 3 else 0, 3000)
        elif c[0] == "tilt":
            emu.set_sensor({"pitch": float(c[1]), "roll": float(c[2])})
        elif c[0] == "raw":
            emu.send(" ".join(c[1:]))
        print(f"   [{now:5.2f}s] azione: {' '.join(c)}")
    time.sleep(0.05)
emu.shutdown()

with lock:
    print("== UART (%d byte)" % len(uart))
    print(uart.decode("utf-8", "replace"))
    kinds = {}
    for _, e in events:
        kinds[e.get("t")] = kinds.get(e.get("t"), 0) + 1
    print("== EVENTI per tipo:", kinds)
    for wall, e in events:
        if e.get("t") in ("out", "irq", "in", "pin", "state", "cpu", "unmapped", "i2c", "abort", "warn"):
            print("   %6.2fs  %s" % (wall - t0, e))
    stats = [e for _, e in events if e.get("t") == "stats"]
    if stats:
        print("   ultimo stats:", [s for s in stats[-3:]])
    print("== LOG (warning/error, max 40)")
    shown = 0
    for lvl, text in logs:
        if lvl in ("warning", "error", "monitor") and shown < 40:
            print("  ", lvl, text[:220])
            shown += 1
    print("   (righe di log totali: %d)" % len(logs))
    text = uart.decode("utf-8", "replace")
    if audio["chunks"]:
        last = audio["last"]
        print("== AUDIO: %.2f s di uscita, tempo scheda %.3f s, parole perse (overrun) %d, underrun %d, "
              "FIFO RX max %d, intervalli con perdite %d" % (audio["seconds"], last.get("now", 0), last.get("lost", 0),
                                                            last.get("under", 0), audio["maxocc"], audio["ovf_bins"]))
        print("   codec:", audio["codec"])
        hp = analyze_headphones(audio["pcm"])
        if hp:
            print("== CUFFIE: livello %.1f dBFS, frequenza dominante %.0f Hz, THD+N %s" % (
                hp["rms_db"], hp["freq"], "-" if hp["thdn_db"] is None else "%.1f dB" % hp["thdn_db"]))

missing = [e for e in expect if e not in text]
present = [e for e in expect_not if e in text]
aborted = any(e.get("t") == "abort" for _, e in events)
problems = []
if missing:
    problems.append("MANCANO %r" % missing)
if present:
    problems.append("NON DOVEVANO COMPARIRE %r" % present)
if expect_abort and not aborted:
    problems.append("NESSUN DATA ABORT")
if expect or expect_not or expect_abort:
    print("== VERIFICA:", "OK" if not problems else "; ".join(problems))
sys.exit(1 if problems or not ok else 0)
