"""Gestione dell'emulatore Renode che esegue il binario sulla Zybo Z7-10 emulata.

Renode viene avviato una volta sola (boot) con la piattaforma del corso; ogni
esecuzione fa, come l'agente del laboratorio: reset della macchina, ps7_init
(UART1), caricamento dell'ELF in DDR, avvio della CPU dal vettore di reset.

Canali con Renode:
  - Monitor (TCP): comandi (avvio, pausa, pulsanti, switch, sensore, statistiche)
  - UART1 (TCP): il Serial Monitor, byte per byte
  - eventi UDP: JSON inviati dai modelli C# (LED, interrupt, accessi, I2C)
  - stdout di Renode: log e avvisi (es. accessi a indirizzi senza periferica)
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import queue
import re
import socket
import subprocess
import sys
import threading
import time

from . import tools

ROOT = tools.ROOT
PLATFORM = ROOT / "platform"

# Cio' che ps7_init (FSBL / agente del laboratorio) fa sulla scheda reale per UART1
PS7_INIT_UART1 = [
    "sysbus WriteDoubleWord 0xE0001004 0x20",   # MR: 8N1
    "sysbus WriteDoubleWord 0xE0001018 0x7C",   # BAUDGEN: CD = 124
    "sysbus WriteDoubleWord 0xE0001034 0x6",    # BAUDDIV: BDIV = 6  -> 115207 bit/s
    "sysbus WriteDoubleWord 0xE0001000 0x17",   # CR: reset FIFO, RX e TX abilitati
]

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\xff[\xfb-\xfe].|\xff\xfa.*?\xff\xf0|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _kill_with_parent(proc: subprocess.Popen):
    """Renode deve morire insieme al server (anche se si chiude la finestra del terminale),
    cosi' non resta acceso in background.
    Windows: Job Object.  macOS/Linux: processo guardiano (watchdog.py)."""
    if os.name != "nt":
        try:
            subprocess.Popen([sys.executable, "-I", str(pathlib.Path(__file__).with_name("watchdog.py")),
                              str(proc.pid)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
        _JOBS.append(job)                       # l'handle resta aperto finche' vive il server
    except Exception:
        pass


_JOBS: list = []


def _free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _rp(p: pathlib.Path) -> str:
    return str(p).replace("\\", "/")


class Emulator:
    def __init__(self, on_event, on_uart, on_log):
        self.on_event = on_event      # callback(dict)
        self.on_uart = on_uart        # callback(bytes)
        self.on_log = on_log          # callback(level, text)
        self.proc: subprocess.Popen | None = None
        self.monitor: socket.socket | None = None
        self.uart: socket.socket | None = None
        self.udp: socket.socket | None = None
        self.state = "off"            # off, booting, idle, running, paused
        self.elf: pathlib.Path | None = None
        self.instructions: int | None = None
        self._cmdq: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._boot_lock = threading.Lock()

    # ------------------------------------------------------------------
    def _set_state(self, st: str, **extra):
        self.state = st
        self.on_event({"t": "state", "state": st, **extra})

    def boot(self) -> bool:
        """Avvia Renode con la piattaforma Zybo Z7-10 (circa 15-40 s la prima volta)."""
        with self._boot_lock:
            if self.proc and self.proc.poll() is None and self.monitor:
                return True
            try:
                renode = tools.renode()
            except tools.ToolMissing as e:
                self.on_log("error", str(e))
                self._set_state("off", reason=str(e))
                return False
            self._stop = threading.Event()
            self._cmdq = queue.Queue()
            self._set_state("booting")
            mon_port = _free_port()
            uart_port = _free_port()
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("127.0.0.1", 0))
            ui_port = self.udp.getsockname()[1]
            threading.Thread(target=self._udp_loop, args=(self.udp, self._stop), daemon=True).start()

            work = ROOT / "work"
            work.mkdir(exist_ok=True)
            resc = work / "boot.resc"
            resc.write_text("\n".join([
                "using sysbus",
                'mach create "zybo-z7-10"',
                f"include @{_rp(PLATFORM / 'ZyboPeripherals.cs')}",
                f"machine LoadPlatformDescription @{_rp(PLATFORM / 'zybo_z7_10.repl')}",
                "cpu PerformanceInMips 667",
                "ttc0 Frequency 111111115",
                "ttc1 Frequency 111111115",
                "logLevel 2",
                "macro reset",
                '"""',
                '    echo "reset"',
                '"""',
                'echo "ZYBO-READY"',
            ]) + "\n", encoding="utf-8")

            env = dict(os.environ)
            env["ZYBO_UI_PORT"] = str(ui_port)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                [str(renode), "--disable-gui", "--plain", "-P", str(mon_port)],
                cwd=str(renode.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=flags)
            _kill_with_parent(self.proc)
            threading.Thread(target=self._stdout_loop, args=(self.proc, self._stop), daemon=True).start()

            self.monitor = self._connect(mon_port, 120)
            if not self.monitor:
                self.on_log("error", "Renode non risponde sulla porta del Monitor.")
                self.shutdown()
                return False
            self._ready = threading.Event()
            threading.Thread(target=self._monitor_reader, args=(self.monitor, self._stop), daemon=True).start()
            threading.Thread(target=self._monitor_writer, args=(self.monitor, self._stop), daemon=True).start()
            # lo script di boot passa dal Monitor: i comandi successivi partono solo quando e' finito
            self.send(f"include @{_rp(resc)}")
            if not self._ready.wait(240):
                self.on_log("error", "La piattaforma Zybo non si e' caricata in tempo (vedi log).")
                self.shutdown()
                return False

            # la UART1 (modello ZynqUart) invia i caratteri come eventi UDP, gia' al baud rate
            threading.Thread(target=self._stats_loop, args=(self._stop,), daemon=True).start()
            self._set_state("idle")
            return True

    def shutdown(self):
        self._stop.set()
        for s in (self.monitor, self.uart, self.udp):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        self.monitor = self.uart = self.udp = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(5)
            except Exception:
                pass
        self.proc = None
        self._set_state("off")

    # ------------------------------------------------------------------
    def run(self, elf: pathlib.Path, sensor: dict | None = None) -> bool:
        """Programma la scheda con l'ELF e la avvia (reset + load + run)."""
        if not self.boot():
            return False
        elf = pathlib.Path(elf).resolve()
        self.elf = elf
        self.instructions = None
        cmds = [
            "pause",
            "machine Reset",                       # reset di CPU e periferiche, orologio a 0
            f"sysbus LoadELF @{_rp(elf)}",         # come l'agente: ELF in DDR, PC = _vector_table
            *PS7_INIT_UART1,
        ]
        if sensor:
            cmds += self._sensor_cmds(sensor)
        cmds += ["start", "globalTimer ClockResume"]
        for c in cmds:
            self.send(c)
        self._set_state("running", wall=time.time(), elf=elf.name)
        return True

    def pause(self):
        if self.state == "running":
            self.send("pause")
            self.send("globalTimer ClockPause")
            self._set_state("paused")

    def resume(self):
        if self.state == "paused":
            self.send("globalTimer ClockResume")
            self.send("start")
            self._set_state("running", wall=time.time())

    def halt(self):
        """Ferma il programma (come togliere l'alimentazione alla CPU)."""
        if self.state in ("running", "paused"):
            self.send("pause")
            self.send("globalTimer ClockPause")
            self.send("machine Reset")
            self._set_state("idle")

    def send(self, cmd: str):
        self._cmdq.put(cmd)

    def uart_write(self, data: bytes):
        if data:
            self.send(f'uart1 HostSend "{base64.b64encode(data).decode()}"')

    # ingressi fisici --------------------------------------------------
    def set_button(self, idx: int, pressed: bool, bounce: int, bounce_us: int):
        self.send(f"btns Press {int(idx)} {'true' if pressed else 'false'} {int(bounce)} {int(bounce_us)}")

    def set_switch(self, idx: int, on: bool, bounce: int, bounce_us: int):
        self.send(f"sws Press {int(idx)} {'true' if on else 'false'} {int(bounce)} {int(bounce_us)}")

    def set_ps_button(self, mio: int, pressed: bool):
        self.send(f"gpio OnGPIO {int(mio)} {'true' if pressed else 'false'}")

    def set_sensor(self, sensor: dict):
        for c in self._sensor_cmds(sensor):
            self.send(c)

    @staticmethod
    def _sensor_cmds(s: dict) -> list[str]:
        cmds = []
        if "pitch" in s or "roll" in s:
            cmds.append(f"i2c1.mpu SetTilt {float(s.get('pitch', 0)):.3f} {float(s.get('roll', 0)):.3f}")
        if "temp" in s:
            cmds.append(f"i2c1.mpu TemperatureC {float(s['temp']):.2f}")
        if "noise" in s:
            cmds.append(f"i2c1.mpu NoiseScale {float(s['noise']):.3f}")
        if "whoami" in s:
            cmds.append(f"i2c1.mpu WhoAmI {int(s['whoami']) & 0xFF}")
        if s.get("mount") in ("jd", "flat"):
            cmds.append(f'i2c1.mpu Mounting "{s["mount"]}"')
        if s.get("tap"):
            cmds.append("i2c1.mpu Tap 1.5 60")
        return cmds

    # ------------------------------------------------------------------
    def _connect(self, port: int, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            if self.proc and self.proc.poll() is not None:
                return None
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2)
                s.settimeout(None)
                return s
            except OSError:
                time.sleep(0.25)
        return None

    def _monitor_writer(self, sock, stop):
        while not stop.is_set():
            try:
                cmd = self._cmdq.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                sock.sendall((cmd + "\n").encode())
            except OSError:
                return

    def _monitor_reader(self, sock, stop):
        buf = b""
        while not stop.is_set():
            try:
                data = sock.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = _ANSI.sub(b"", line).decode("utf-8", "replace").strip()
                text = re.sub(r"^\([^)]*\)\s*", "", text)          # prompt "(zybo-z7-10) "
                if not text:
                    continue
                if "ZYBO-READY" in text and not text.startswith("echo"):
                    self._ready.set()
                    continue
                if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", text):
                    try:
                        self.instructions = int(text, 0)
                    except ValueError:
                        pass
                    continue
                low = text.lower()
                if "error" in low or "could not" in low or "exception" in low or "no such" in low:
                    self.on_log("monitor", text)

    def _stats_loop(self, stop):
        last = None
        while not stop.wait(1.0):
            if self.state != "running":
                last = None
                continue
            self.send("cpu ExecutedInstructions")
            now = (time.time(), self.instructions)
            if last and now[1] is not None and last[1] is not None and now[1] >= last[1]:
                mips = (now[1] - last[1]) / max(1e-3, now[0] - last[0]) / 1e6
                self.on_event({"t": "cpu", "mips": round(mips, 1), "instr": now[1]})
            last = now

    def _uart_loop(self, sock, stop):
        while not stop.is_set():
            try:
                data = sock.recv(4096)
            except OSError:
                return
            if not data:
                return
            self.on_uart(data)

    def _udp_loop(self, sock, stop):
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(65535)
            except OSError:
                return
            try:
                self.on_event(json.loads(data.decode("utf-8")))
            except ValueError:
                pass

    _UNMAPPED = re.compile(r"\[cpu: (0x[0-9A-Fa-f]+)\] (Read|Write)(\w*) (?:from|to) non existing peripheral at (0x[0-9A-Fa-f]+)")

    def _stdout_loop(self, proc, stop):
        for raw in iter(proc.stdout.readline, b""):
            if stop.is_set():
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            m = self._UNMAPPED.search(line)
            if m:
                self.on_event({"t": "unmapped", "pc": m.group(1), "op": m.group(2), "size": m.group(3),
                               "addr": m.group(4)})
            lvl = "warning" if "[WARNING]" in line else "error" if "[ERROR]" in line else "info"
            self.on_log(lvl, line)
        if not stop.is_set():
            self._set_state("off", reason="Renode terminato")
