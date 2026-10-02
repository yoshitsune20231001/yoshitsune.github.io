"""リール用の和風BGMを作る（自作の音なので著作権の心配なし）。

  python sns/scripts/make_bgm.py   → sns/assets/bgm/*.mp3 を作り直す

琴（Karplus-Strong）・三味線・鈴・太鼓を合成して、陽音階／都節音階のメロディを作ります。
曲を差し替えたいときは、sns/assets/bgm/ に好きな mp3（フリーBGMなど）を置けばOK。
"""
from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 44100
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "sns/assets/bgm"

YO = [0, 2, 5, 7, 9]        # 陽音階（明るい民謡風）
MIYAKO = [0, 1, 5, 7, 8]    # 都節音階（しっとり）
RYUKYU = [0, 4, 5, 7, 11]   # 琉球音階（沖縄風）


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def pluck(freq: float, dur: float, rng, bright=0.5, decay=0.996) -> np.ndarray:
    """弦をはじいた音（琴・三味線）。"""
    n = int(SR * dur)
    p = max(2, int(SR / freq))
    buf = rng.uniform(-1, 1, p)
    buf = bright * buf + (1 - bright) * np.convolve(buf, [0.5, 0.5], "same")
    out = np.empty(n)
    for i in range(n):
        j = i % p
        out[i] = buf[j]
        buf[j] = decay * 0.5 * (buf[j] + buf[(j + 1) % p])
    env = np.minimum(1, np.arange(n) / (SR * 0.003))
    return out * env


def bell(freq: float, dur: float) -> np.ndarray:
    """鈴（高い金属音）。"""
    t = np.arange(int(SR * dur)) / SR
    s = sum(a * np.sin(2 * np.pi * freq * m * t) * np.exp(-t * d)
            for m, a, d in [(1, 1, 6), (2.76, 0.5, 9), (5.4, 0.25, 14)])
    return s * 0.3


def flute(freq: float, dur: float, rng) -> np.ndarray:
    """笛（息の音＋ゆれ）。"""
    t = np.arange(int(SR * dur)) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1, t / 0.4)
    ph = 2 * np.pi * np.cumsum(freq * vib) / SR
    tone = np.sin(ph) + 0.25 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)
    breath = np.convolve(rng.uniform(-1, 1, t.size), np.ones(8) / 8, "same") * 0.12
    env = np.minimum(1, t / 0.08) * np.minimum(1, (dur - t) / 0.15).clip(0)
    return (tone + breath) * env * 0.5


def taiko(dur: float, rng, low=True) -> np.ndarray:
    """太鼓（ドン／カッ）。"""
    t = np.arange(int(SR * dur)) / SR
    if low:
        f = 70 + 60 * np.exp(-t * 30)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
        hit = rng.uniform(-1, 1, t.size) * np.exp(-t * 60) * 0.3
        return body + hit
    return rng.uniform(-1, 1, t.size) * np.exp(-t * 90) * 0.5


def melody(scale, rng, bars, base, steps_per_bar=8, rest=0.25):
    """音階の上をゆるく歩くメロディ。4小節ごとに主音で終わる。"""
    notes, deg = [], 5
    for bar in range(bars):
        for s in range(steps_per_bar):
            end_of_phrase = bar % 4 == 3 and s >= steps_per_bar - 2
            if end_of_phrase:
                deg = 5 if s == steps_per_bar - 2 else None
            elif rng.random() < rest and s % 2 == 1:
                notes.append(None)
                continue
            else:
                deg = int(np.clip(deg + rng.choice([-2, -1, -1, 0, 1, 1, 2]), 0, 9))
            if deg is None:
                notes.append(None)
                deg = 5
            else:
                notes.append(base + 12 * (deg // 5) + scale[deg % 5])
    return notes


def mix_at(track: np.ndarray, sound: np.ndarray, at: float, gain: float) -> None:
    i = int(at * SR)
    j = min(track.size, i + sound.size)
    if i < track.size:
        track[i:j] += sound[: j - i] * gain


def song(name, scale, bpm, seed, base, drums, inst="koto", bars=16, fue=False, bass=True):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    step = beat / 2
    total = bars * 4 * beat + 2
    L = np.zeros(int(SR * total))
    R = np.zeros_like(L)

    # 主旋律
    notes = melody(scale, rng, bars, base, rest=0.15 if inst == "fue" else 0.25)
    for k, m in enumerate(notes):
        if m is None:
            continue
        if inst == "fue":  # 笛が主役：2音分の長さでゆったり
            if k % 2:
                continue
            s = flute(hz(m + 12), step * 2.2, rng)
        elif inst == "shamisen":
            s = pluck(hz(m), step * 2.5, rng, bright=0.9, decay=0.990)
        else:
            s = pluck(hz(m), step * 4, rng, bright=0.6, decay=0.997)
        mix_at(L, s, k * step, 0.55)
        mix_at(R, s, k * step + 0.012, 0.45)

    # 笛の合いの手（4小節ごと、後半2小節で長い音）
    if fue and inst != "fue":
        for bar in range(2, bars, 4):
            for b, d in [(0, 0), (2, 2)]:
                m = base + 12 + scale[[4, 2][b // 2] if d else 3]
                mix_at(L, flute(hz(m), beat * 2, rng), (bar * 4 + b) * beat, 0.3)
                mix_at(R, flute(hz(m), beat * 2, rng), (bar * 4 + b) * beat, 0.35)

    # 低音の琴（1小節に2回、主音と5度）
    for bar in range(bars if bass else 0):
        for b, off in [(0, 0), (2, 7)]:
            s = pluck(hz(base - 12 + off), beat * 2, rng, bright=0.3, decay=0.998)
            mix_at(L, s, (bar * 4 + b) * beat, 0.35)
            mix_at(R, s, (bar * 4 + b) * beat, 0.35)

    # 鈴
    for bar in range(0, bars, 2):
        s = bell(hz(base + 24 + scale[2]), 1.2)
        mix_at(L, s, bar * 4 * beat, 0.25)
        mix_at(R, s, bar * 4 * beat + 0.02, 0.3)

    # 太鼓
    if drums:
        for bar in range(bars):
            for b, low in drums:
                s = taiko(0.6, rng, low)
                g = 0.5 if low else 0.18
                mix_at(L, s, (bar * 4 + b) * beat, g)
                mix_at(R, s, (bar * 4 + b) * beat, g)

    st = np.stack([L, R], 1)
    st /= np.max(np.abs(st)) + 1e-9
    st *= 0.85
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((st * 32767).astype("<i2").tobytes())
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav),
                        "-b:a", "160k", str(OUT / f"{name}.mp3")], check=True)
    print("作成:", OUT / f"{name}.mp3", f"{total:.1f}秒")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    song("wa_pokapoka", YO, 104, 11, 74, [(0, True), (2, True), (3, False), (3.5, False)])
    song("wa_hokkori", MIYAKO, 80, 23, 72, [(0, True)])
    song("wa_omatsuri", YO, 124, 37, 69, [(0, True), (1, False), (1.5, True), (2, True), (3, False)],
         inst="shamisen")
    song("wa_sakura", MIYAKO, 92, 41, 74, [], fue=True)
    song("wa_minori", YO, 96, 53, 72, [(0, True), (2, True)], inst="fue")
    song("wa_ryukyu", RYUKYU, 108, 61, 72, [(0, True), (1.5, False), (2, True), (3, False)], inst="shamisen")
    song("wa_yuuyake", MIYAKO, 70, 71, 74, [], inst="fue")
    song("wa_ennichi", YO, 136, 83, 71, [(0, True), (0.5, False), (1, False), (2, True), (2.5, True), (3, False)],
         inst="shamisen", fue=True)
    song("wa_ochanoma", YO, 88, 97, 76, [(0, True)], bass=False)
    song("wa_tsukimi", MIYAKO, 76, 101, 69, [(0, True)], fue=True)


if __name__ == "__main__":
    main()
