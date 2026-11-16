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
import shutil
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

# Modello dei tempi in modalita' "tempo CPU" (README, "Lab 3"):
#   cpi          cicli medi per istruzione a 667 MHz: 1,51 riproduce i 157 us dell'FFT N = 512 a -O2
#                misurati sulla scheda (testo del Lab 3)
#   pl_read_ns   lettura di un registro nella PL (bus M_AXI_GP0, AXI4-Stream FIFO, AXI GPIO)
#   pl_write_ns  scrittura (memoria strongly-ordered: il processore attende la risposta)
TIMING_DEFAULTS = {"cpi": 1.51, "pl_read_ns": 1000.0, "pl_write_ns": 700.0,
                   "l2_hit_cycles": 25.0, "ddr_cycles": 60.0, "cache": 2.0, "cpi_cache": 1.40}

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


def uses_audio(elf: pathlib.Path) -> bool:
    """True se il programma accede al percorso audio della PL (FIFO 0x43C0_0000, I2S 0x43C1_0000).

    Cerca nelle sezioni di codice dell'ELF le istruzioni ARM "movt rX, #0x43C0/0x43C1" e le costanti
    0x43C0xxxx/0x43C1xxxx: e' cosi' che il compilatore costruisce quegli indirizzi. Funziona anche
    con un .elf compilato altrove (per esempio scaricato dall'IDE del corso)."""
    try:
        data = pathlib.Path(elf).read_bytes()
        if data[:4] != b"\x7fELF" or data[4] != 1:
            return False
        shoff = int.from_bytes(data[0x20:0x24], "little")
        shentsize = int.from_bytes(data[0x2E:0x30], "little")
        shnum = int.from_bytes(data[0x30:0x32], "little")
        for k in range(shnum):
            h = data[shoff + k * shentsize: shoff + (k + 1) * shentsize]
            sh_type = int.from_bytes(h[4:8], "little")
            sh_flags = int.from_bytes(h[8:12], "little")
            off = int.from_bytes(h[16:20], "little")
            size = int.from_bytes(h[20:24], "little")
            # solo il codice (PROGBITS eseguibili, con le costanti accanto alle istruzioni): la tabella
            # della MMU del BSP contiene la base di ogni MB, 0x43C0_0000 compreso, e va esclusa
            if sh_type != 1 or not (sh_flags & 0x4):
                continue
            sec = data[off:off + size]
            for i in range(0, len(sec) - 3, 4):
                w = int.from_bytes(sec[i:i + 4], "little")
                # movt rX, #0x43C0 / #0x43C1 (senza condizione), oppure la costante 0x43C0_00xx / 0x43C1_00xx
                # (indirizzo di un registro: i 16 bit bassi sono un offset piccolo, non parte di un'istruzione)
                if (w & 0xFFFF0FFF) in (0xE34403C0, 0xE34403C1) or (w & 0xFFFEFF00) == 0x43C00000:
                    return True
    except (OSError, ValueError, IndexError):
        pass
    return False


_CACHE_SYMBOLS = (b"\0Xil_DCache", b"\0Xil_L1DCache", b"\0Xil_L2Cache", b"\0Xpm_")


def uses_cache(elf: pathlib.Path) -> bool:
    """True se il programma misura o gestisce le cache: legge i contatori del PMU (mrc p15 su c9,
    c13, in ARM o Thumb-2) oppure usa le funzioni del BSP Xil_DCache*, Xil_L1DCache*,
    Xil_L2Cache* o Xpm_*. Solo per questi programmi il banco attiva il modello delle cache, che
    rende l'emulazione circa 5 volte piu' lenta."""
    try:
        data = pathlib.Path(elf).read_bytes()
        if data[:4] != b"\x7fELF" or data[4] != 1:
            return False
        shoff = int.from_bytes(data[0x20:0x24], "little")
        shentsize = int.from_bytes(data[0x2E:0x30], "little")
        shnum = int.from_bytes(data[0x30:0x32], "little")
        shstrndx = int.from_bytes(data[0x32:0x34], "little")
        heads = [data[shoff + k * shentsize: shoff + (k + 1) * shentsize] for k in range(shnum)]
        names_off = int.from_bytes(heads[shstrndx][16:20], "little")
        for h in heads:
            sh_name = int.from_bytes(h[0:4], "little")
            sh_type = int.from_bytes(h[4:8], "little")
            sh_flags = int.from_bytes(h[8:12], "little")
            off = int.from_bytes(h[16:20], "little")
            size = int.from_bytes(h[20:24], "little")
            name = data[names_off + sh_name: data.index(b"\0", names_off + sh_name)]
            sec = data[off:off + size]
            if name == b".strtab" and any(s in sec for s in _CACHE_SYMBOLS):
                return True
            if sh_type != 1 or not (sh_flags & 0x4):
                continue
            # solo le LETTURE dei contatori (mrc p15, 0, rX, c9, c13, {0,2}: PMCCNTR, PMXEVCNTR): le
            # scritture ci sono in ogni programma, perche' __cpu_init del BSP abilita il contatore di cicli
            for i in range(0, len(sec) - 3, 2):
                w = int.from_bytes(sec[i:i + 4], "little")
                if i % 4 == 0 and (w & 0x0FFF0F1F) == 0x0E190F1D:                     # ARM
                    return True
                hw1, hw2 = w & 0xFFFF, w >> 16
                if hw1 == 0xEE19 and (hw2 & 0x0F1F) == 0x0F1D:                         # Thumb-2
                    return True
    except (OSError, ValueError, IndexError):
        pass
    return False


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
        self.clock = "real"
        self.last_run: dict = {}
        self._boot_error: str | None = None
        self.cache_model = False
        self._last_out: list = []                  # ultime righe scritte da Renode (diagnosi)
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
                # GIC v1 dello Zynq: in ICDICFR il driver XScuGic scrive 01 (livello) o 11 (fronte); il bit
                # basso e' il modello 1-N, che il GIC di Renode non conosce e per cui scarta la configurazione.
                # Su un solo core non cambia nulla: lo si toglie e resta il bit livello/fronte.
                'sysbus SetHookBeforePeripheralWrite gic "value = (value & 0xAAAAAAAA) if 0xC00 <= offset < 0xD00 else value"',
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
            # Cartella temporanea propria, nuova a ogni avvio. All'avvio Renode controlla le cartelle
            # "renode-<PID>" lasciate in %TEMP% dalle esecuzioni precedenti e chiede a Windows se quei
            # processi sono vivi: dopo un riavvio il PID puo' appartenere a un servizio protetto, la
            # domanda riceve "Accesso negato" e Renode si chiude subito.
            tmp_root = work / "renode-tmp"
            for old in tmp_root.glob("*"):
                shutil.rmtree(old, ignore_errors=True)    # quella di un Renode ancora acceso resta
            tmp = tmp_root / f"{os.getpid()}-{int(time.time() * 1000)}"
            tmp.mkdir(parents=True, exist_ok=True)
            env["TMP"] = env["TEMP"] = env["TMPDIR"] = str(tmp)
            self._last_out = []
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                [str(renode), "--disable-gui", "--plain", "-P", str(mon_port)],
                cwd=str(renode.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=flags)
            _kill_with_parent(self.proc)
            threading.Thread(target=self._stdout_loop, args=(self.proc, self._stop), daemon=True).start()

            self.monitor = self._connect(mon_port, 120)
            if not self.monitor:
                cause = next((l.strip() for l in reversed(self._last_out) if "Exception" in l or "rror" in l), "")
                self.on_log("error", "Renode non risponde sulla porta del Monitor." + (f" ({cause[:200]})" if cause else ""))
                self.shutdown()
                return False
            self._ready = threading.Event()
            threading.Thread(target=self._monitor_reader, args=(self.monitor, self._stop), daemon=True).start()
            threading.Thread(target=self._monitor_writer, args=(self.monitor, self._stop), daemon=True).start()
            # lo script di boot passa dal Monitor: i comandi successivi partono solo quando e' finito
            self._boot_error = None
            self.send(f"include @{_rp(resc)}")
            deadline = time.time() + 240
            while not self._ready.wait(0.5):
                if self.proc.poll() is not None:
                    # Renode si e' chiuso durante l'avvio: lo si dice subito, con la causa
                    msgs = [l.strip() for l in self._last_out if l.strip() and not l.strip().startswith("at ")]
                    cause = next((l for l in reversed(msgs) if "negato" in l or "denied" in l), "") or \
                        next((l for l in reversed(msgs) if "Exception" in l or "rror" in l), "")
                    self._boot_error = "Renode si e' chiuso durante l'avvio" + (f" ({cause[:200]})" if cause else "")
                    break
                if time.time() > deadline:
                    break
            if not self._ready.is_set() or self._boot_error:
                self.on_log("error", "La piattaforma Zybo non si e' caricata: " + (self._boot_error or "tempo scaduto (vedi log)"))
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
    def run(self, elf: pathlib.Path, sensor: dict | None = None, clock: str = "auto",
            audio: dict | None = None, timing: dict | None = None) -> bool:
        """Programma la scheda con l'ELF e la avvia (reset + load + run).

        clock: "real" (tempo reale del PC), "cpu" (tempo calcolato dalle istruzioni del Cortex-A9,
        per il Lab 3) oppure "auto": "cpu" se il programma usa il percorso audio, altrimenti "real"."""
        if not self.boot():
            return False
        elf = pathlib.Path(elf).resolve()
        self.elf = elf
        self.instructions = None
        audio_prog = uses_audio(elf)
        # tempo CPU anche per chi misura le prestazioni (PMU, cache): in tempo reale misurerebbe il PC
        mode = clock if clock in ("real", "cpu") else ("cpu" if audio_prog or uses_cache(elf) else "real")
        self.clock = mode
        self.last_run = {"sensor": sensor, "clock": clock, "audio": audio}
        # taratura del tempo CPU (config.json, "timing"): cicli per istruzione e latenze dei registri della PL
        t = dict(TIMING_DEFAULTS)
        for k, v in (timing or {}).items():
            if k in t and isinstance(v, (int, float)) and (v > 0 or (k == "cache" and v == 0)):
                t[k] = float(v)
        cache_on = mode == "cpu" and (t["cache"] == 1 or (t["cache"] >= 2 and uses_cache(elf)))
        self.cache_model = cache_on
        # con le cache modellate parte del tempo e' nei miss: il CPI medio e' piu' basso, tarato in
        # modo che l'FFT del Lab 3 (N = 512, -O2) duri gli stessi 157 us
        cpi = t["cpi_cache"] if cache_on else t["cpi"]
        cmds = [
            "pause",
            f"globalTimer CyclesPerInstruction {cpi:.3f}",
            f"globalTimer PlReadNs {t['pl_read_ns']:.1f}",
            f"globalTimer PlWriteNs {t['pl_write_ns']:.1f}",
            f'globalTimer ClockMode "{mode}"',
            "machine Reset",                       # reset di CPU e periferiche, orologio a 0
            "cpu IsHalted false",                  # dopo un Data Abort la CPU era stata fermata: si riparte
            # tempo CPU: cache L1D/L2 modellate (i miss costano cicli) e PMU allineato all'orologio;
            # timing "cache": 2 = solo per i programmi che usano PMU o funzioni di cache, 1 = sempre, 0 = mai
            f"globalTimer L2HitCycles {int(t['l2_hit_cycles'])}",
            f"globalTimer DdrCycles {int(t['ddr_cycles'])}",
            f"globalTimer CacheModel {'true' if cache_on else 'false'}",
            # DDR "sporca" come sulla scheda (residui, non zeri): i primi 32 MB, dove stanno programma,
            # stack e heap; i dati cambiano a ogni esecuzione
            f"globalTimer ScrambleMemory 0x100000 0x2000000 {int(time.time() * 1000) & 0x7FFFFFFF}",
            f"sysbus LoadELF @{_rp(elf)}",         # come l'agente: ELF in DDR, PC = _vector_table
            *PS7_INIT_UART1,
        ]
        if sensor:
            cmds += self._sensor_cmds(sensor)
        if audio:
            cmds += self._audio_cmds(audio)
        cmds += ["start", "globalTimer ClockResume"]
        for c in cmds:
            self.send(c)
        self._set_state("running", wall=time.time(), elf=elf.name, clock=mode, audio=audio_prog)
        return True

    def set_audio_input(self, audio: dict):
        for c in self._audio_cmds(audio):
            self.send(c)

    @staticmethod
    def _audio_cmds(a: dict) -> list[str]:
        kind = a.get("type", "sine")
        if kind not in ("sine", "square", "sweep", "noise", "silence", "file"):
            kind = "sine"
        cmds = []
        f = a.get("file") or {}
        if kind == "file":
            if f.get("path") and pathlib.Path(f["path"]).is_file():
                # audio reale (WAV o microfono) gia' convertito dal server in PCM 16 bit
                cmds.append(f'i2s LoadInputFile "{_rp(pathlib.Path(f["path"]))}" '
                            f'{float(f.get("rate", 48000)):.1f} {int(f.get("channels", 2))}')
            else:
                kind = "silence"
        cmds.append(f'i2s SetInput "{kind}" {float(a.get("freq", 1000)):.2f} {float(a.get("level", -12)):.2f}')
        return cmds

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
                    if self.state == "booting" and not self._ready.is_set():
                        self._boot_error = text            # un comando dello script di avvio e' fallito
                        self._ready.set()

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
            self._last_out.append(line)
            del self._last_out[:-40]
            m = self._UNMAPPED.search(line)
            if m:
                self.on_event({"t": "unmapped", "pc": m.group(1), "op": m.group(2), "size": m.group(3),
                               "addr": m.group(4)})
            lvl = "warning" if "[WARNING]" in line else "error" if "[ERROR]" in line else "info"
            self.on_log(lvl, line)
        if not stop.is_set():
            self._set_state("off", reason="Renode terminato")
