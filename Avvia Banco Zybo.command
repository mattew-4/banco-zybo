#!/bin/sh
# Banco Zybo Z7-10 emulato -- avvio su macOS (doppio clic nel Finder) e Linux (./"Avvia Banco Zybo.command")
cd "$(dirname "$0")" || exit 1
echo "Banco Zybo Z7-10 emulato"
echo

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Serve Python 3.9 o piu' recente."
    echo "Mac: installalo da https://www.python.org/downloads/macos/ (oppure: brew install python), poi riprova."
    printf "Premi Invio per chiudere. "; read -r _
    exit 1
fi

# la prima volta scarica compilatore ed emulatore (circa 400 MB); poi non fa niente
if ! "$PY" -I setup.py; then
    printf "Premi Invio per chiudere. "; read -r _
    exit 1
fi
echo
echo "Si apre nel browser su http://127.0.0.1:8765/"
echo "Per spegnere tutto: Ctrl+C oppure chiudi questa finestra."
echo
exec "$PY" -I server/app.py
