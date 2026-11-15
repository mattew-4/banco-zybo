"""Crea un WAV di prova: 2 s, 44,1 kHz, 24 bit, stereo; 440 Hz a -6 dBFS solo sul sinistro.
Uso: python -I tests/make_tone_wav.py uscita.wav"""
import math
import struct
import sys

rate, secs = 44100, 2
frames = bytearray()
for i in range(rate * secs):
    v = int(round(0.5 * math.sin(2 * math.pi * 440 * i / rate) * 8388607))
    frames += v.to_bytes(3, "little", signed=True) + (0).to_bytes(3, "little", signed=True)
fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 6, 6, 24)
data = b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(frames)) + b"WAVE" + \
    b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(frames)) + bytes(frames)
open(sys.argv[1], "wb").write(data)
