"""Guardiano per macOS/Linux: chiude Renode quando il server del banco termina.

Su Windows lo stesso compito lo fa un Job Object (vedi emulator.py). Su macOS non esiste
un equivalente, quindi il server avvia questo piccolo processo: se il server muore (anche
chiudendo la finestra del Terminale) il guardiano viene "adottato" da un altro processo,
se ne accorge e termina Renode, cosi' non resta nulla acceso in background.

Uso interno:  python -I watchdog.py <pid_renode>
"""
import os
import signal
import sys
import time


def main():
    renode = int(sys.argv[1])
    parent = os.getppid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)     # sopravvive alla chiusura del Terminale
    while True:
        time.sleep(0.5)
        try:
            os.kill(renode, 0)                       # Renode e' ancora vivo?
        except OSError:
            return                                   # gia' terminato: niente da fare
        if os.getppid() != parent:                   # il server non c'e' piu'
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(renode, sig)
                except OSError:
                    return
                time.sleep(2)
            return


if __name__ == "__main__":
    main()
