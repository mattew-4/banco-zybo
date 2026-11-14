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

expect = []
while "--expect" in sys.argv:
    i = sys.argv.index("--expect")
    expect.append(sys.argv[i + 1])
    del sys.argv[i:i + 2]

src = pathlib.Path(sys.argv[1])
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 6
actions = sys.argv[3] if len(sys.argv) > 3 else ""
opt = sys.argv[4] if len(sys.argv) > 4 else "-O2"

work = pathlib.Path(__file__).resolve().parents[1] / "work" / "selftest"
res = build.build({src.name: src.read_text(encoding="utf-8", errors="replace")}, work,
                  build.BuildOptions(opt=opt))
print("== BUILD ok=%s (%.1fs)\n%s\n%s" % (res.ok, res.seconds, res.log, res.size))
if not res.ok:
    sys.exit(1)

uart = bytearray()
events = []
logs = []
lock = threading.Lock()


def on_event(e):
    with lock:
        if e.get("t") == "uart":
            import base64
            uart.extend(base64.b64decode(e["b64"]))
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
ok = emu.run(res.elf)
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
        if e.get("t") in ("out", "irq", "in", "pin", "state", "cpu", "unmapped", "i2c"):
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

missing = [e for e in expect if e not in text]
if expect:
    print("== VERIFICA:", "OK" if not missing else "MANCANO %r" % missing)
sys.exit(1 if missing or not ok else 0)
