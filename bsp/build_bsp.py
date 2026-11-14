"""Compila il BSP Xilinx standalone (Cortex-A9, Zybo Z7-10) in bsp/lib/libxil.a.

Usa lo stesso compilatore del Cortex-A9 Lab (xPack GNU Arm Embedded GCC 15.2.1)
e le stesse opzioni CPU: -mcpu=cortex-a9 -mfpu=neon-vfpv3 -mfloat-abi=hard.
"""
import concurrent.futures as cf
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from zyboemu import tools  # noqa: E402

BSP = ROOT / "bsp"
CC = tools.gcc_tool("gcc")
AR = tools.gcc_tool("ar")

CPU_FLAGS = ["-mcpu=cortex-a9", "-mfpu=neon-vfpv3", "-mfloat-abi=hard"]
# niente percorsi assoluti del computer di chi compila nelle informazioni di debug della libreria
_GCC_ROOT = pathlib.Path(CC).resolve().parent.parent
PREFIX_MAPS = [f"-ffile-prefix-map={p}={new}"
               for old, new in ((_GCC_ROOT, "gcc"), (ROOT, "."))
               for p in {str(old), str(old).replace("\\", "/")}]
CFLAGS = CPU_FLAGS + PREFIX_MAPS + [
    "-O2", "-g", "-c",
    "-fno-tree-loop-distribute-patterns",   # come i Makefile del BSP Xilinx
    "-I", str(BSP / "generated"),
    "-I", str(BSP / "include"),
]


def compile_one(src: pathlib.Path, obj_dir: pathlib.Path):
    obj = obj_dir / (src.stem + ("_S" if src.suffix == ".S" else "") + ".o")
    cmd = [CC, *CFLAGS, str(src), "-o", str(obj)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return src.name, obj, r.returncode, (r.stdout + r.stderr).strip()


def main():
    obj_dir = BSP / "obj"
    lib_dir = BSP / "lib"
    obj_dir.mkdir(exist_ok=True)
    lib_dir.mkdir(exist_ok=True)
    for o in obj_dir.glob("*.o"):
        o.unlink()

    sources = sorted((BSP / "src").glob("*.c")) + sorted((BSP / "src").glob("*.S"))
    sources += sorted((BSP / "generated").glob("*.c"))

    ok, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for name, obj, rc, out in ex.map(lambda s: compile_one(s, obj_dir), sources):
            if rc == 0:
                ok.append(obj)
                if "warning" in out:
                    pass
            else:
                failed.append((name, out))

    for name, out in failed:
        print(f"--- ERRORE in {name}\n{out}\n")
    print(f"Compilati: {len(ok)}  Falliti: {len(failed)}")
    if failed:
        sys.exit(1)

    lib = lib_dir / "libxil.a"
    if lib.exists():
        lib.unlink()
    r = subprocess.run([AR, "rcs", str(lib), *map(str, ok)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(1)
    print(f"Creato {lib} ({lib.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
