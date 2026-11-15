"""Audio reale per LINE IN: un file WAV (o una registrazione dal microfono, che l'interfaccia invia
come WAV) diventa un file grezzo PCM a 16 bit che il percorso audio del banco riproduce in ciclo.

Formati accettati: PCM intero a 8, 16, 24, 32 bit e virgola mobile a 32/64 bit (anche
WAVE_FORMAT_EXTENSIBLE), mono o stereo (dei file con piu' canali si usano i primi due), a qualsiasi
frequenza: il ricampionamento alla frequenza del codec lo fa il banco."""
from __future__ import annotations

import array
import pathlib
import struct
import sys

MAX_SECONDS = 120


class WavError(ValueError):
    pass


def _chunks(data: bytes):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("non e' un file WAV (manca l'intestazione RIFF/WAVE)")
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        yield cid, body
        pos += 8 + size + (size & 1)


def wav_to_raw(data: bytes, out: pathlib.Path) -> dict:
    """Converte un WAV in PCM 16 bit interlacciato (1 o 2 canali). Restituisce rate, canali, durata."""
    fmt = None
    pcm = None
    for cid, body in _chunks(data):
        if cid == b"fmt " and len(body) >= 16:
            fmt = struct.unpack("<HHIIHH", body[:16])
            if fmt[0] == 0xFFFE and len(body) >= 26:          # WAVE_FORMAT_EXTENSIBLE: sottoformato
                fmt = (int.from_bytes(body[24:26], "little"),) + fmt[1:]
        elif cid == b"data":
            pcm = body
    if fmt is None or pcm is None:
        raise WavError("WAV senza blocco 'fmt ' o 'data'")
    tag, channels, rate, _, align, bits = fmt
    if channels < 1 or rate < 1000 or rate > 384000 or align < 1:
        raise WavError(f"formato non valido ({channels} canali, {rate} Hz)")
    width = bits // 8
    if tag == 1 and bits not in (8, 16, 24, 32):
        raise WavError(f"PCM a {bits} bit non supportato")
    if tag == 3 and bits not in (32, 64):
        raise WavError(f"virgola mobile a {bits} bit non supportata")
    if tag not in (1, 3):
        raise WavError(f"codifica WAV {tag} non supportata (servono PCM o float)")
    frames = min(len(pcm) // align, int(MAX_SECONDS * rate))
    keep = min(channels, 2)

    if tag == 1 and bits == 16 and channels <= 2:
        out_samples = array.array("h")
        out_samples.frombytes(pcm[:frames * align])
        if sys.byteorder == "big":
            out_samples.byteswap()
    else:
        vals = []
        for f in range(frames):
            base = f * align
            for c in range(keep):
                o = base + c * width
                b = pcm[o:o + width]
                if tag == 3:
                    x = struct.unpack("<f" if bits == 32 else "<d", b)[0]
                elif bits == 8:
                    x = (b[0] - 128) / 128.0                     # 8 bit: senza segno
                else:
                    x = int.from_bytes(b, "little", signed=True) / float(1 << (bits - 1))
                vals.append(max(-32768, min(32767, int(round(x * 32768)))))
        out_samples = array.array("h", vals)
        if sys.byteorder == "big":
            out_samples.byteswap()
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(out_samples.tobytes())
    return {"rate": rate, "channels": keep, "seconds": round(frames / rate, 3),
            "truncated": len(pcm) // align > frames}
