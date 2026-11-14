"""Installa gli strumenti del banco Zybo nella cartella tools/ (una volta sola).

  * xPack GNU Arm Embedded GCC 15.2.1  (lo stesso compilatore del Cortex-A9 Lab)
  * Renode 1.17.0                      (l'emulatore che esegue il codice ARM)

Tutto viene scaricato dalle pagine ufficiali di GitHub dei due progetti e verificato con
il checksum SHA-256 pubblicato. Non serve essere amministratori, non si tocca il sistema:
per disinstallare basta cancellare la cartella del banco.

Uso:   python3 setup.py           (su Windows: python setup.py)
       python3 setup.py --check   (controlla soltanto, senza scaricare)
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
DL = TOOLS / "_download"
sys.path.insert(0, str(ROOT / "server"))
from zyboemu import tools  # noqa: E402

GCC_VER = "15.2.1-1.1"
GCC_URL = "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v{v}/xpack-arm-none-eabi-gcc-{v}-{p}"
GCC = {   # piattaforma: (file, sha256)  -- checksum dai file .sha ufficiali di xPack
    "windows-x64": ("win32-x64.zip", "bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea"),
    "macos-arm64": ("darwin-arm64.tar.gz", "574082d35e49a2bcbdc355836b2a3ae5e5bb3b9456c9f5e37177db2ab4aad870"),
    "macos-x64": ("darwin-x64.tar.gz", "5be906a5194c3173e145a58048e5607ffff947773237802b93e55e23c415f1b6"),
    "linux-x64": ("linux-x64.tar.gz", "da6a49ad4003944b823c6c93702a8787c922ab34bd7e918ec0eaf6933a9b1ff6"),
    "linux-arm64": ("linux-arm64.tar.gz", "67980c7990eba7bb7ffdf39699102effd70889f5ac427be19a8c8a6c5fab2972"),
}

RENODE_VER = "1.17.0"
RENODE_URL = "https://github.com/renode/renode/releases/download/v{v}/renode-{v}.{p}"
RENODE = {   # checksum SHA-256 pubblicati da GitHub per gli allegati della release ufficiale
    "windows-x64": ("windows-portable.zip", "18bcf145422039e8702c3e5f9864c7787561bfd5deb7a3ee6e168ecf19e2b9db"),
    "macos-arm64": ("osx-arm64-portable.dmg", "63b1fb691207f503cea937e4ec8fad3e068a517d0abac3c24c0206c62f6c4d12"),
    "linux-x64": ("linux-portable.tar.gz", "4ba7c68b59e2447f188ef4b4b112fcccd2802582c460beedceb63b84e5605a5f"),
    "linux-arm64": ("linux-arm64-portable.tar.gz", "e9051578411441bfb47b77de0381cfba3badce03663367fab561472fd7807f1f"),
}

# l'unica libreria del compilatore che serve: Cortex-A9, NEON, hard-float (le altre ~1 GB si cancellano)
CPU_FLAGS = ["-mcpu=cortex-a9", "-mfpu=neon-vfpv3", "-mfloat-abi=hard"]


def host() -> str:
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "x64" if m in ("x86_64", "amd64") else m
    if sys.platform == "win32":
        return "windows-" + arch
    if sys.platform == "darwin":
        return "macos-" + arch
    if sys.platform.startswith("linux"):
        return "linux-" + arch
    return sys.platform + "-" + arch


def say(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
def download(url: str, sha256: str) -> pathlib.Path:
    DL.mkdir(parents=True, exist_ok=True)
    dest = DL / url.rsplit("/", 1)[1]
    if not (dest.is_file() and _sha(dest) == sha256):
        say(f"  scarico {url}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        curl = shutil.which("curl")
        if curl:
            r = subprocess.run([curl, "-fL", "--retry", "3", "--progress-bar", "-o", str(tmp), url])
            if r.returncode != 0:
                sys.exit(f"ERRORE: download non riuscito ({url})")
        else:
            import urllib.request
            with urllib.request.urlopen(url) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, 1 << 20)
        tmp.replace(dest)
    got = _sha(dest)
    if got != sha256:
        dest.unlink()
        sys.exit(f"ERRORE: checksum SHA-256 diverso da quello ufficiale per {dest.name}\n"
                 f"  atteso {sha256}\n  trovato {got}\nIl file e' stato cancellato: non viene usato.")
    say(f"  checksum SHA-256 verificato: {dest.name}")
    return dest


def _sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(archive: pathlib.Path, dest: pathlib.Path):
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name
    say(f"  estraggo {name} ...")
    if name.endswith(".zip"):
        base = dest.resolve()
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():
                target = (dest / m.filename).resolve()
                if base not in target.parents and target != base:
                    sys.exit(f"ERRORE: percorso non valido nell'archivio: {m.filename}")
            z.extractall(dest)
    elif name.endswith(".tar.gz"):
        # tar di sistema: conserva link simbolici e permessi di esecuzione
        r = subprocess.run(["tar", "-xzf", str(archive), "-C", str(dest)])
        if r.returncode != 0:
            sys.exit(f"ERRORE: estrazione non riuscita ({name})")
    elif name.endswith(".dmg"):
        mnt = pathlib.Path(tempfile.mkdtemp(prefix="renode-dmg-"))
        r = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-quiet",
                            "-mountpoint", str(mnt), str(archive)])
        if r.returncode != 0:
            sys.exit("ERRORE: impossibile aprire il .dmg di Renode")
        try:
            app = next(mnt.glob("*.app"))
            r = subprocess.run(["ditto", str(app), str(dest / "Renode.app")])
            if r.returncode != 0:
                sys.exit("ERRORE: copia di Renode.app non riuscita")
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", str(mnt)])
            shutil.rmtree(mnt, ignore_errors=True)
    else:
        sys.exit(f"ERRORE: formato sconosciuto {name}")


def prune_gcc(bin_dir: pathlib.Path):
    """Tiene solo la libreria per Cortex-A9 hard-float, come nel banco originale (~1 GB in meno)."""
    gcc = str(bin_dir / f"arm-none-eabi-gcc{tools.EXE}")
    keep = subprocess.run([gcc, *CPU_FLAGS, "-print-multi-directory"],
                          capture_output=True, text=True).stdout.strip()
    if not keep or keep == ".":
        return
    parts = keep.split("/")
    root = bin_dir.parent
    libdirs = [root / "arm-none-eabi" / "lib"] + list((root / "lib" / "gcc" / "arm-none-eabi").glob("*"))

    def prune(d: pathlib.Path, rest: list[str]):
        if not rest:
            return
        for child in d.iterdir():
            if child.is_dir() and not child.is_symlink():
                if child.name == rest[0]:
                    prune(child, rest[1:])
                else:
                    shutil.rmtree(child)

    for lib in libdirs:
        for top in ("thumb", "arm"):
            d = lib / top
            if not d.is_dir():
                continue
            if top == parts[0]:
                prune(d, parts[1:])
            else:
                for child in d.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
    say(f"  tenuta solo la libreria {keep} (Cortex-A9, NEON, hard-float)")


# ---------------------------------------------------------------------------
def have_gcc() -> pathlib.Path | None:
    for b in sorted((TOOLS / "gcc").glob("*/bin")):
        if (b / f"arm-none-eabi-gcc{tools.EXE}").is_file():
            return b
    return None


def have_renode() -> pathlib.Path | None:
    try:
        return tools.renode()
    except tools.ToolMissing:
        return None


def check_versions() -> bool:
    ok = True
    try:
        out = subprocess.run([tools.gcc_tool("gcc"), "--version"], capture_output=True, text=True,
                             timeout=60).stdout.splitlines()[0]
        say(f"  compilatore: {out}")
    except Exception as e:  # noqa: BLE001
        say(f"  compilatore: NON FUNZIONA ({e})")
        ok = False
    try:
        r = tools.renode()
        say(f"  Renode:      {r}")
    except tools.ToolMissing:
        say("  Renode:      mancante")
        ok = False
    return ok


def main():
    h = host()
    say(f"Banco Zybo Z7-10 -- installazione strumenti ({h}, Python {platform.python_version()})")
    if sys.version_info < (3, 9):
        sys.exit("ERRORE: serve Python 3.9 o piu' recente.")
    if "--check" in sys.argv:
        sys.exit(0 if check_versions() else 1)

    need_gcc = have_gcc() is None
    need_renode = have_renode() is None
    if not need_gcc and not need_renode:
        say("Strumenti gia' installati.")
        sys.exit(0 if check_versions() else 1)

    if need_gcc and h not in GCC:
        sys.exit(f"ERRORE: nessun compilatore xPack per questa piattaforma ({h}).")
    free = shutil.disk_usage(ROOT).free / 2**30
    if free < 2.5:
        say(f"ATTENZIONE: solo {free:.1f} GB liberi; durante l'installazione servono circa 2,5 GB "
            f"(alla fine ne restano occupati circa 0,7).")

    if need_gcc:
        say(f"\n[1/2] Compilatore xPack arm-none-eabi-gcc {GCC_VER} (~300 MB da scaricare)")
        f, sha = GCC[h]
        arc = download(GCC_URL.format(v=GCC_VER, p=f), sha)
        extract(arc, TOOLS / "gcc")
        prune_gcc(have_gcc())
        arc.unlink()
    else:
        say("\n[1/2] Compilatore gia' presente.")

    if need_renode:
        if h not in RENODE:
            say(f"\n[2/2] Renode: non esiste un pacchetto ufficiale pronto per {h}.")
            if h == "macos-x64":
                say("  Sui Mac con processore Intel installalo con Homebrew (compila da sorgente, ci vuole un po'):\n"
                    "      brew install renode/tap/renode\n"
                    "  poi rilancia questo script: il banco usera' il comando 'renode' del sistema.")
            sys.exit(1)
        say(f"\n[2/2] Emulatore Renode {RENODE_VER} (~70-110 MB da scaricare)")
        f, sha = RENODE[h]
        arc = download(RENODE_URL.format(v=RENODE_VER, p=f), sha)
        extract(arc, TOOLS / "renode")
        arc.unlink()
    else:
        say("\n[2/2] Renode gia' presente.")

    shutil.rmtree(DL, ignore_errors=True)
    say("\nVerifica:")
    if not check_versions():
        sys.exit("\nQualcosa non va: copia questo testo e chiedi aiuto.")
    say("\nFatto. Avvia il banco con 'Avvia Banco Zybo' (Windows: .bat, Mac: .command).")


if __name__ == "__main__":
    main()
