"""Compilazione dei programmi per la Zybo Z7-10, come nel Cortex-A9 Lab del corso.

Compilatore: xPack GNU Arm Embedded GCC 15.2.1
Opzioni CPU: -mcpu=cortex-a9 -mfpu=neon-vfpv3 -mfloat-abi=hard  (codice A32)
Libreria:    BSP Xilinx standalone (boot.S, MMU, vettori, xil_printf, XIicPs, XTime, ...)
Linker:      lscript.ld in DDR da 0x0010_0000, come un'applicazione Vitis
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from . import tools

ROOT = tools.ROOT
BSP = ROOT / "bsp"

CPU_FLAGS = ["-mcpu=cortex-a9", "-mfpu=neon-vfpv3", "-mfloat-abi=hard"]
OPT_LEVELS = {"-O0", "-Og", "-O1", "-O2", "-O3", "-Os"}


@dataclass
class BuildOptions:
    opt: str = "-O2"           # default del Cortex-A9 Lab
    warnings: bool = True      # -Wall -Wextra
    werror: bool = False
    debug: bool = True         # -g
    libm: bool = True          # -lm (necessaria per atan2f, sqrtf del Lab 2)

    @staticmethod
    def from_dict(d: dict) -> "BuildOptions":
        o = BuildOptions()
        if d.get("opt") in OPT_LEVELS:
            o.opt = d["opt"]
        for k in ("warnings", "werror", "debug", "libm"):
            if k in d:
                setattr(o, k, bool(d[k]))
        return o


@dataclass
class BuildResult:
    ok: bool
    log: str
    elf: pathlib.Path | None = None
    size: str = ""
    seconds: float = 0.0
    commands: list[str] = field(default_factory=list)


def _run(cmd: list[str], cwd: pathlib.Path) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    return r.returncode, (r.stdout + r.stderr)


def _short(cmd: list[str]) -> str:
    out = []
    for a in cmd:
        a = a.replace(str(ROOT), "<ZyboEmu>")
        out.append(f'"{a}"' if " " in a else a)
    return " ".join(out)


def build(sources: dict[str, str], workdir: pathlib.Path, opts: BuildOptions) -> BuildResult:
    """sources: nome file -> contenuto (.c, .h, .S). Restituisce l'ELF in workdir/app.elf."""
    t0 = time.time()
    try:
        CC, SIZE = tools.gcc_tool("gcc"), tools.gcc_tool("size")
    except tools.ToolMissing as e:
        return BuildResult(False, str(e))
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    src_dir = workdir / "src"
    obj_dir = workdir / "obj"
    src_dir.mkdir(parents=True)
    obj_dir.mkdir()

    for name, text in sources.items():
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", pathlib.Path(name).name)
        with open(src_dir / safe, "w", encoding="utf-8", newline="\n") as f:   # Python 3.9: niente write_text(newline=)
            f.write(text)

    cflags = CPU_FLAGS + [opts.opt, "-I", str(src_dir), "-I", str(BSP / "generated"), "-I", str(BSP / "include")]
    if opts.warnings:
        cflags += ["-Wall", "-Wextra"]
    if opts.werror:
        cflags += ["-Werror"]
    if opts.debug:
        cflags += ["-g"]

    log_parts: list[str] = []
    commands: list[str] = []
    objs: list[str] = []
    compiled_any = False
    for src in sorted(src_dir.iterdir()):
        if src.suffix not in (".c", ".S", ".s"):
            continue
        compiled_any = True
        obj = obj_dir / (src.stem + ".o")
        cmd = [CC, *cflags, "-c", str(src), "-o", str(obj)]
        commands.append(_short(cmd))
        rc, out = _run(cmd, workdir)
        if out.strip():
            log_parts.append(out.replace(str(src_dir) + "\\", "").replace(str(src_dir) + "/", ""))
        if rc != 0:
            return BuildResult(False, "\n".join(log_parts) or "errore di compilazione", None, "",
                               time.time() - t0, commands)
        objs.append(str(obj))

    if not compiled_any:
        return BuildResult(False, "Nessun file .c da compilare.", None, "", time.time() - t0, commands)

    elf = workdir / "app.elf"
    libs = "-Wl,--start-group,-lxil,-lgcc,-lc" + (",-lm" if opts.libm else "") + ",--end-group"
    ldcmd = [CC, *CPU_FLAGS, "-Wl,-build-id=none", "-Wl,--no-warn-rwx-segments",
             f"-specs={BSP / 'Xilinx.spec'}", f"-Wl,-T,{BSP / 'lscript.ld'}", "-L", str(BSP / "lib"),
             "-o", str(elf), *objs, libs]
    commands.append(_short(ldcmd))
    rc, out = _run(ldcmd, workdir)
    if out.strip():
        log_parts.append(out)
    if rc != 0:
        return BuildResult(False, "\n".join(log_parts), None, "", time.time() - t0, commands)

    _, size = _run([SIZE, str(elf)], workdir)
    return BuildResult(True, "\n".join(log_parts), elf, size.strip(), time.time() - t0, commands)


def disassemble(elf: pathlib.Path, function: str = "main") -> str:
    rc, out = _run([tools.gcc_tool("objdump"), "-d", "--no-show-raw-insn", f"--disassemble={function}", str(elf)], elf.parent)
    return out if rc == 0 else ""


def symbols(elf: pathlib.Path) -> dict[str, int]:
    rc, out = _run([tools.gcc_tool("nm"), str(elf)], elf.parent)
    res = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            try:
                res[parts[2]] = int(parts[0], 16)
            except ValueError:
                pass
    return res
