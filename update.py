"""Aggiornamento automatico del banco, lanciato dagli avviatori prima di setup.py.

- Cartella creata con git clone: git pull --ff-only.
- Cartella estratta da uno ZIP: confronta la versione installata con l'ultima su GitHub e, se e'
  diversa, scarica lo ZIP di quella versione e sostituisce i file del banco.

Non tocca mai tools/ (compilatore ed emulatore), work/, downloads/ e config.json.
Non blocca mai l'avvio: senza internet, o se qualcosa va storto, il banco parte con la versione
installata. Per disattivarlo: "auto_update": false in config.json, oppure ZYBO_NO_UPDATE=1.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
REPO = os.environ.get("ZYBO_UPDATE_REPO", "mattew-4/banco-zybo")
BRANCH = "main"
STATE = ROOT / ".banco-version.json"          # versione installata e file del banco (solo ZIP)
KEEP = {"tools", "work", "downloads", "config.json", ".git", STATE.name}   # mai toccati


def say(msg):
    print("[aggiornamento] " + msg, flush=True)


def fetch(url: str, dest: pathlib.Path | None = None, timeout: int = 15) -> bytes | None:
    """Scarica url (in memoria, o nel file dest). curl se c'e' (usa i certificati del sistema)."""
    curl = shutil.which("curl")
    try:
        if curl:
            cmd = [curl, "-fsSL", "--max-time", str(timeout * 8 if dest else timeout), "-A", "banco-zybo-updater"]
            cmd += ["-o", str(dest), url] if dest else [url]
            r = subprocess.run(cmd, capture_output=True, timeout=timeout * 10)
            if r.returncode != 0:
                return None
            return b"" if dest else r.stdout
        req = urllib.request.Request(url, headers={"User-Agent": "banco-zybo-updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if dest:
            dest.write_bytes(data)
            return b""
        return data
    except Exception:  # noqa: BLE001 - rete assente, certificati, timeout: si parte senza aggiornare
        return None


def disabled() -> bool:
    if os.environ.get("ZYBO_NO_UPDATE"):
        return True
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("auto_update") is False
    except (OSError, ValueError):
        return False


def update_git() -> None:
    git = shutil.which("git")
    if not git:
        say("cartella git ma git non e' installato: aggiornamento saltato")
        return
    before = subprocess.run([git, "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    try:
        r = subprocess.run([git, "-C", str(ROOT), "pull", "--ff-only", "--quiet", "origin", BRANCH],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        say("GitHub non risponde: parto con la versione installata")
        return
    after = subprocess.run([git, "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    if r.returncode != 0:
        say("non riesco ad aggiornare con git (nessuna connessione o modifiche locali): parto con la versione " + before)
    elif before != after:
        say(f"banco aggiornato: {before} -> {after}")
    else:
        say(f"il banco e' gia' aggiornato ({after})")


def update_zip() -> None:
    data = fetch(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    try:
        info = json.loads(data.decode("utf-8")) if data else None
        latest = info["sha"]
        when = info["commit"]["committer"]["date"][:10]
    except (ValueError, KeyError, TypeError):
        say("nessuna connessione a GitHub: parto con la versione installata")
        return
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("sha") == latest:
        say(f"il banco e' gia' aggiornato ({latest[:7]})")
        return

    say(f"scarico la versione {latest[:7]} del {when}...")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="banco-update-"))
    try:
        archive = tmp / "banco.zip"
        if fetch(f"https://codeload.github.com/{REPO}/zip/{latest}", archive) is None:
            say("download non riuscito: parto con la versione installata")
            return
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            prefix = names[0].split("/")[0] + "/"
            new = {}
            for n in names:
                rel = n[len(prefix):]
                if rel and rel.split("/")[0] not in KEEP:
                    new[rel] = z.getinfo(n)
            if "server/app.py" not in new or "setup.py" not in new:
                say("archivio inatteso: aggiornamento saltato")
                return
            failed = []
            for rel, zi in new.items():
                dst = ROOT / rel
                content = z.read(zi)
                try:
                    if dst.is_file() and dst.read_bytes() == content:
                        continue                         # invariato: non si tocca
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    part = dst.with_name(dst.name + ".part")
                    part.write_bytes(content)
                    mode = (zi.external_attr >> 16) & 0o777
                    if os.name != "nt" and mode:
                        os.chmod(part, mode)             # es. l'avviatore .command resta eseguibile
                    os.replace(part, dst)
                except OSError:
                    failed.append(rel)
        # file tolti dal banco nella versione nuova (solo quelli installati da un aggiornamento precedente)
        for rel in set(state.get("files", [])) - set(new):
            if rel.split("/")[0] not in KEEP:
                try:
                    (ROOT / rel).unlink()
                except OSError:
                    pass
        if failed:
            say(f"{len(failed)} file non sostituiti (in uso?): riprovo al prossimo avvio")
            return
        STATE.write_text(json.dumps({"sha": latest, "date": when, "files": sorted(new)}, indent=1), encoding="utf-8")
        say(f"banco aggiornato alla versione {latest[:7]} del {when}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if disabled():
        say("disattivato (config.json o ZYBO_NO_UPDATE)")
        return 0
    try:
        if (ROOT / ".git").exists():
            update_git()
        else:
            update_zip()
    except Exception as e:  # noqa: BLE001 - l'aggiornamento non deve mai impedire l'avvio
        say(f"errore imprevisto ({e}): parto con la versione installata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
