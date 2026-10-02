"""リール用のポップスBGMを作る（自作の音なので著作権の心配なし）。

  pip install numpy scipy imageio-ffmpeg
  python sns/scripts/make_bgm.py   → sns/assets/bgm/pop*.mp3 を作り直す

ドラム・ベース・コード（エレピ／シンセ／ギター風）・メロディ・アルペジオ・ベルを合成して、
コード進行とメロディを曲ごとに組み立てます。
曲を差し替えたいときは、sns/assets/bgm/ に好きな mp3（商用OKで再配布できる音源）を置けばOK。
"""
from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

SR = 44100
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "sns/assets/bgm"
MAJOR = [0, 2, 4, 5, 7, 9, 11]


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def tt(dur: float) -> np.ndarray:
    return np.arange(max(1, int(SR * dur))) / SR


def lp(x, f, order=2):
    return sosfilt(butter(order, min(f, SR / 2 - 100), "low", fs=SR, output="sos"), x)


def hp(x, f, order=2):
    return sosfilt(butter(order, f, "high", fs=SR, output="sos"), x)


def bp(x, lo, hi):
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


def env(n, a=0.005, d=0.3, s=0.5, r=0.08):
    t = np.arange(n) / SR
    e = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) / max(d, 1e-3)))
    k = min(n, int(r * SR))
    if k:
        e[-k:] *= np.linspace(1, 0, k)
    return e


# ---------------- 楽器 ----------------
def saw(f, t, detune=0.0):
    return 2 * ((f * (1 + detune) * t + np.random.rand()) % 1) - 1


def supersaw(f, dur):
    t = tt(dur)
    s = sum(saw(f, t, d) for d in (-0.012, -0.005, 0, 0.005, 0.012)) / 5
    return s * env(t.size, a=0.08, d=0.8, s=0.7, r=0.2)


def epiano(f, dur):
    t = tt(dur)
    idx = 1.8 * np.exp(-t * 4) + 0.2
    tine = 0.15 * np.exp(-t * 30) * np.sin(2 * np.pi * f * 14 * t)
    s = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t)) + tine
    return s * np.exp(-t * 1.6) * env(t.size, a=0.002, d=1, s=1, r=0.08)


def pluck(f, dur, bright=1.0):
    """はじく音（ギター風・シンセプラック）。高い倍音ほど早く消える。"""
    t = tt(dur)
    s = np.zeros_like(t)
    for k in range(1, 14):
        if f * k > 9000:
            break
        s += np.sin(2 * np.pi * f * k * t + k) / k * np.exp(-t * (3 + k * 2.2 / bright))
    return s * env(t.size, a=0.002, d=1, s=1, r=0.05)


def bell(f, dur):
    t = tt(dur)
    return sum(a * np.sin(2 * np.pi * f * m * t) * np.exp(-t * d)
               for m, a, d in [(1, 1, 3), (2.0, 0.4, 5), (3.01, 0.25, 7), (4.2, 0.15, 10)]) * 0.5


def lead(f, dur, kind):
    t = tt(dur)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.15) * 4, 0, 1)
    ph = np.cumsum(f * vib) / SR
    if kind == "square":
        s = np.sign(np.sin(2 * np.pi * ph)) * 0.6 + np.sin(2 * np.pi * ph) * 0.4
    elif kind == "flute":
        s = np.sin(2 * np.pi * ph) + 0.2 * np.sin(4 * np.pi * ph) + 0.05 * np.random.randn(t.size)
    else:  # saw
        s = 2 * (ph % 1) - 1
    return s * env(t.size, a=0.01, d=0.3, s=0.75, r=0.06)


def kick():
    t = tt(0.45)
    f = 48 + 110 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7) + 0.3 * np.exp(-t * 300) * np.random.randn(t.size)


def snare():
    t = tt(0.3)
    n = bp(np.random.randn(t.size), 1200, 8000) * np.exp(-t * 16)
    return n * 1.3 + 0.5 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 22)


def clap():
    t = tt(0.3)
    e = sum(np.exp(-np.clip(t - o, 0, None) * 120) * (t >= o) for o in (0, 0.011, 0.022)) + np.exp(-t * 14) * 0.5
    return bp(np.random.randn(t.size), 900, 5000) * e


def hat(open_=False):
    t = tt(0.4 if open_ else 0.08)
    return hp(np.random.randn(t.size), 7000) * np.exp(-t * (9 if open_ else 55)) * 0.5


def crash():
    t = tt(2.0)
    return hp(np.random.randn(t.size), 4000) * np.exp(-t * 2.2) * 0.35


# ---------------- 曲づくり ----------------
DRUMS = {  # 1小節＝16ステップ（16分音符）の、どこで鳴らすか
    "four": dict(k=[0, 4, 8, 12], s=[4, 12], c=[4, 12], h=list(range(0, 16, 2)), o=[2, 6, 10, 14]),
    "back": dict(k=[0, 7, 8, 10], s=[4, 12], c=[], h=list(range(16)), o=[14]),
    "half": dict(k=[0, 3, 10], s=[8], c=[8], h=list(range(0, 16, 2)), o=[]),
    "lofi": dict(k=[0, 7, 10], s=[4, 12], c=[], h=list(range(0, 16, 2)), o=[]),
    "skank": dict(k=[0, 8, 11], s=[4, 12], c=[], h=list(range(0, 16, 2)), o=[6, 14]),
}
RHYTHMS = {  # 2小節（32ステップ）のメロディのリズム
    "pop": [0, 4, 6, 8, 12, 16, 20, 22, 24],
    "busy": [0, 2, 4, 8, 10, 12, 14, 16, 18, 20, 24],
    "slow": [0, 6, 8, 14, 16, 22, 24],
    "drive": [0, 2, 3, 6, 8, 10, 11, 14, 16, 18, 19, 22, 24, 26],
}


class Mix:
    def __init__(self, seconds):
        self.n = int(SR * seconds)
        self.tracks: dict[str, np.ndarray] = {}

    def add(self, name, sound, at, gain=1.0, pan=0.0):
        tr = self.tracks.setdefault(name, np.zeros((self.n, 2)))
        i = int(at * SR)
        if i >= self.n:
            return
        j = min(self.n, i + sound.size)
        s = sound[: j - i] * gain
        tr[i:j, 0] += s * np.sqrt((1 - pan) / 2) * 1.41
        tr[i:j, 1] += s * np.sqrt((1 + pan) / 2) * 1.41


def chord_notes(key, deg, seventh):
    idx = [deg, deg + 2, deg + 4] + ([deg + 6] if seventh else [])
    return [key + MAJOR[i % 7] + 12 * (i // 7) for i in idx]


def make_melody(key, prog, rng, rhythm, bars):
    """2小節のモチーフをくり返して、覚えやすいメロディにする（8小節で1フレーズ）。"""
    steps = RHYTHMS[rhythm]
    scale = [key + 12 + o + 12 * k for k in range(2) for o in MAJOR] + [key + 36]
    top = len(scale) - 1

    def motif(start_bar, cur, end_on_tonic=False):
        out = []
        for n, st in enumerate(steps):
            bar = start_bar + st // 16
            pcs = {c % 12 for c in chord_notes(key, prog[bar % len(prog)], False)}
            if st % 8 == 0:  # 強拍はコードの音
                cands = [i for i in range(top + 1) if scale[i] % 12 in pcs]
                cur = min(cands, key=lambda i: abs(i - cur) + rng.random() * 2.5)
            else:
                cur = int(np.clip(cur + rng.choice([-2, -1, -1, 1, 1, 2]), 0, top))
            if end_on_tonic and n == len(steps) - 1:
                cands = [i for i in range(top + 1) if scale[i] % 12 == key % 12]
                cur = min(cands, key=lambda i: abs(i - cur))
            out.append((start_bar * 16 + st, scale[cur]))
        return out, cur

    a, cur = motif(0, 7)
    a2, cur = motif(2, cur)
    b, cur = motif(4, cur)
    b2, _ = motif(6, cur, end_on_tonic=True)
    phrase = (a + [(s + 32, m) for s, m in a[:-3]] + a2[-3:]
              + b + [(s + 32, m) for s, m in b[:-3]] + b2[-3:])
    notes = [(s + rep * 128, m) for rep in range(bars // 8) for s, m in phrase]
    res = []
    for i, (s, m) in enumerate(notes):
        nxt = notes[i + 1][0] if i + 1 < len(notes) else s + 8
        res.append((s, min(nxt - s, 8), m))
    return res


def song(name, bpm, key, prog, drums, chords, lead_kind, bass, seed, rhythm="pop",
         arp=True, swing=0.0, seventh=False, bars=16, bright=1.0):
    np.random.seed(seed)
    rng = np.random.default_rng(seed)
    step = 60 / bpm / 4
    total = bars * 16 * step + 2.5
    mx = Mix(total)

    def at(s):
        return s * step + (step * swing if s % 2 else 0)

    # ドラム
    K, S, C, HC, HO, CR = kick(), snare(), clap(), hat(), hat(True), crash()
    pat = DRUMS[drums]
    kicks = []
    for bar in range(bars):
        fill = bar % 8 == 7
        for s in pat["k"]:
            mx.add("drum", K, at(bar * 16 + s), 0.95)
            kicks.append(at(bar * 16 + s))
        for s in pat["s"]:
            mx.add("snare", S, at(bar * 16 + s), 0.5)
        for s in pat["c"]:
            mx.add("snare", C, at(bar * 16 + s), 0.45)
        for s in pat["h"]:
            if not (fill and s >= 12):
                mx.add("hat", HC, at(bar * 16 + s), 0.22 if s % 4 else 0.3, pan=0.3)
        for s in pat["o"]:
            mx.add("hat", HO, at(bar * 16 + s), 0.18, pan=0.3)
        if fill:  # フィルイン
            for i, s in enumerate([12, 13, 14, 15]):
                mx.add("snare", S, at(bar * 16 + s), 0.25 + 0.08 * i)
        if bar % 8 == 0:
            mx.add("hat", CR, at(bar * 16), 0.6, pan=-0.2)

    for bar in range(bars):
        deg = prog[bar % len(prog)]
        ch = chord_notes(key + 12, deg, seventh)
        root = key - 24 + MAJOR[deg % 7]
        bar_t, bar_len = at(bar * 16), 16 * step

        # コード
        if chords == "saw":
            for i, m in enumerate(ch):
                mx.add("pad", supersaw(hz(m), bar_len + 0.1), bar_t, 0.16, pan=(-0.4, 0.4, 0, 0.2)[i])
        elif chords == "epiano":
            for hit in ((0, 6, 10) if bpm > 95 else (0, 8)):
                for i, m in enumerate(ch):
                    mx.add("keys", epiano(hz(m), step * 6), at(bar * 16 + hit) + i * 0.008, 0.2, pan=-0.2 + i * 0.15)
            for i, m in enumerate(ch):
                mx.add("pad", supersaw(hz(m), bar_len), bar_t, 0.05)
        elif chords == "stab":  # 裏拍のカッティング
            for hit in (2, 6, 10, 14):
                for i, m in enumerate(ch):
                    mx.add("keys", pluck(hz(m), step * 1.6, 1.5), at(bar * 16 + hit), 0.22, pan=-0.3 + i * 0.2)
            for i, m in enumerate(ch):
                mx.add("pad", supersaw(hz(m - 12), bar_len), bar_t, 0.07)
        elif chords == "guitar":  # ストローク
            for hit in (0, 3, 6, 8, 10, 12, 14):
                for i, m in enumerate(ch + [ch[0] + 12]):
                    mx.add("keys", pluck(hz(m), step * 3, 0.9), at(bar * 16 + hit) + i * 0.012, 0.16, pan=0.25)
            for i, m in enumerate(ch):
                mx.add("pad", supersaw(hz(m), bar_len), bar_t, 0.06, pan=-0.3)

        # ベース
        pattern = {"eighth": range(0, 16, 2), "root": [0, 6, 8, 14], "octave": range(0, 16, 2),
                   "syncop": [0, 3, 6, 8, 11, 14]}[bass]
        for j, s in enumerate(pattern):
            m = root + (12 if bass == "octave" and j % 2 else 0)
            t = tt(step * (3.5 if bass == "root" else 1.8))
            b = (saw(hz(m), t) * 0.6 + np.sin(2 * np.pi * hz(m) * t)) * env(t.size, a=0.004, d=0.25, s=0.7, r=0.04)
            mx.add("bass", b, at(bar * 16 + s), 0.5)

        # アルペジオ（後半は少し大きく）
        if arp:
            tones = ch + [ch[0] + 12, ch[1] + 12]
            order = [0, 1, 2, 3, 4, 3, 2, 1]
            every = 2 if bpm > 110 else 1
            for s in range(0, 16, every):
                m = tones[order[(s // every) % 8]] + 12
                mx.add("arp", pluck(hz(m), step * 2, 1.3), at(bar * 16 + s),
                       0.09 if bar < bars // 2 else 0.13, pan=0.5 if s % 4 else -0.5)

    # メロディ（後半はベルを重ねる）
    for s, ln, m in make_melody(key, prog, rng, rhythm, bars):
        d = ln * step
        mx.add("lead", lead(hz(m), d, lead_kind), at(s), 0.2)
        if s >= bars * 8:
            mx.add("lead", bell(hz(m + 12), max(d, 0.6)), at(s), 0.12, pan=0.2)

    # ---- ミックス ----
    tr = mx.tracks
    duck = np.ones(mx.n)
    for k in kicks:  # キックに合わせてパッド・ベースを少し下げる
        i = int(k * SR)
        seg = np.arange(min(mx.n - i, int(0.25 * SR))) / SR
        duck[i:i + seg.size] = np.minimum(duck[i:i + seg.size], 1 - 0.45 * np.exp(-seg / 0.08))
    if "pad" in tr:
        tr["pad"] = lp(tr["pad"].T, 3200 * bright).T * duck[:, None]
    tr["bass"] = lp(tr["bass"].T, 900 * bright).T * (0.5 + 0.5 * duck[:, None])
    tr["lead"] = lp(tr["lead"].T, 4500 * bright).T
    if "arp" in tr:
        tr["arp"] = lp(tr["arp"].T, 6000).T

    dly = int(step * 3 * SR)  # メロディのやまびこ（左右）
    echo = np.zeros_like(tr["lead"])
    echo[dly:, 1] = tr["lead"][:-dly, 0] * 0.3
    echo[2 * dly:, 0] = tr["lead"][:-2 * dly, 1] * 0.15
    tr["lead"] = tr["lead"] + echo
    send = tr["lead"] * 0.5 + tr["snare"] * 0.3
    for name_, g in (("pad", 0.3), ("keys", 0.4), ("arp", 0.3)):
        if name_ in tr:
            send = send + tr[name_] * g
    irt = tt(1.8)
    rev = np.stack([fftconvolve(send[:, c], np.random.randn(irt.size) * np.exp(-irt * 3.2) * 0.03)[: mx.n]
                    for c in range(2)], 1)
    mix = sum(tr.values()) + hp(rev.T, 250).T
    mix /= np.max(np.abs(mix)) + 1e-9
    mix = np.tanh(mix * 1.5) / np.tanh(1.5)
    fade = int(SR * 2.0)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((mix * 0.95 * 32767).astype("<i2").tobytes())
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav),
                        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", "-b:a", "192k",
                        str(OUT / f"{name}.mp3")], check=True)
    print("作成:", name, f"{total:.1f}秒")


SONGS = [  # 度数：0=I 1=ii 2=iii 3=IV 4=V 5=vi
    dict(name="pop01_hareiro", bpm=118, key=60, prog=[0, 4, 5, 3], drums="four", chords="saw",
         lead_kind="square", bass="octave", seed=1),
    dict(name="pop02_ohisama", bpm=124, key=62, prog=[3, 4, 2, 5], drums="four", chords="stab",
         lead_kind="saw", bass="eighth", seed=2, rhythm="busy"),
    dict(name="pop03_cafe", bpm=88, key=65, prog=[1, 4, 0, 5], drums="lofi", chords="epiano",
         lead_kind="flute", bass="root", seed=3, rhythm="slow", swing=0.25, seventh=True, arp=False, bright=0.7),
    dict(name="pop04_disco", bpm=120, key=57, prog=[5, 3, 0, 4], drums="four", chords="stab",
         lead_kind="square", bass="octave", seed=4, rhythm="drive"),
    dict(name="pop05_kirakira", bpm=128, key=64, prog=[0, 5, 3, 4], drums="four", chords="saw",
         lead_kind="square", bass="eighth", seed=5, rhythm="busy"),
    dict(name="pop06_sanpo", bpm=104, key=67, prog=[0, 3, 4, 0], drums="back", chords="guitar",
         lead_kind="flute", bass="syncop", seed=6),
    dict(name="pop07_yumemiru", bpm=92, key=63, prog=[0, 2, 3, 4], drums="half", chords="saw",
         lead_kind="flute", bass="root", seed=7, rhythm="slow", seventh=True),
    dict(name="pop08_march", bpm=126, key=60, prog=[0, 3, 4, 0], drums="back", chords="guitar",
         lead_kind="square", bass="eighth", seed=8, rhythm="drive"),
    dict(name="pop09_ukiuki", bpm=100, key=62, prog=[0, 4, 3, 4], drums="skank", chords="stab",
         lead_kind="flute", bass="syncop", seed=9),
    dict(name="pop10_emo", bpm=96, key=59, prog=[5, 3, 0, 4], drums="back", chords="epiano",
         lead_kind="saw", bass="root", seed=10, seventh=True, bright=0.8),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("wa_*.mp3"):
        old.unlink()
    for s in SONGS:
        song(**s)


if __name__ == "__main__":
    main()
