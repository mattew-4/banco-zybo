"""Dove trovare compilatore e Renode, su Windows, macOS e Linux.

Ordine di ricerca:
  1. variabili d'ambiente ZYBO_GCC_BIN (cartella bin di arm-none-eabi-gcc) e ZYBO_RENODE (eseguibile);
  2. la cartella tools/ preparata da setup.py;
  3. il PATH di sistema (per esempio Renode installato con Homebrew su un Mac Intel).
"""
from __future__ import annotations

import os
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
EXE = ".exe" if os.name == "nt" else ""


class ToolMissing(RuntimeError):
    pass


_HINT = "Esegui prima l'installazione degli strumenti:  python3 setup.py  (su Windows: python setup.py)"


def gcc_bin() -> pathlib.Path:
    env = os.environ.get("ZYBO_GCC_BIN")
    if env and (pathlib.Path(env) / f"arm-none-eabi-gcc{EXE}").is_file():
        return pathlib.Path(env)
    for b in sorted((TOOLS / "gcc").glob("*/bin")):
        if (b / f"arm-none-eabi-gcc{EXE}").is_file():
            return b
    found = shutil.which("arm-none-eabi-gcc")
    if found:
        return pathlib.Path(found).resolve().parent
    raise ToolMissing("Compilatore arm-none-eabi-gcc non trovato. " + _HINT)


def gcc_tool(name: str) -> str:
    """name: 'gcc', 'objdump', 'size', 'nm', 'ar'"""
    return str(gcc_bin() / f"arm-none-eabi-{name}{EXE}")


def renode() -> pathlib.Path:
    env = os.environ.get("ZYBO_RENODE")
    if env and pathlib.Path(env).is_file():
        return pathlib.Path(env)
    base = TOOLS / "renode"
    candidates = [base / "Renode.app" / "Contents" / "MacOS" / "renode"]   # macOS (.dmg)
    candidates += sorted(base.glob(f"*/renode{EXE}"))                      # Windows / Linux portable
    for c in candidates:
        if c.is_file():
            return c
    found = shutil.which("renode")
    if found:
        return pathlib.Path(found)
    raise ToolMissing("Emulatore Renode non trovato. " + _HINT)
