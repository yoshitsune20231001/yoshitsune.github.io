"""AIが描いた絵から、第10話と同じ形式のリール動画・4コマまとめ画像・カルーセル・note見出しを作る。

リールの構成（約25秒・1080x1920）
  1. タイトルカード（AIの縦長イラスト）         2.75秒
  2. フック（「百万両あるのに、／買うのは…お団子!?」） 2.5秒
  3. 4コマを1コマずつ（クリーム地・金枠）        3.5秒 x 4
  4. 締め（第N話・ロゴ・販売中）                2.5秒
  5. エンドカード（いいね＆フォロー）            3.25秒
"""
from __future__ import annotations

import glob
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "sns/assets/reel"

# 第10話のリールから取った色
BG_TOP = (249, 242, 224)
BG_BOTTOM = (240, 229, 206)
GOLD = (199, 161, 72)
INK = (84, 66, 50)       # 見出しの文字
GRAY = (110, 100, 90)
RED = (178, 40, 34)
W, H = 1080, 1920

_FONT_CANDIDATES = [
    os.environ.get("SNS_FONT", ""),
    str(ROOT / "sns/assets/fonts/NotoSansJP-VF.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]


def font(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if path and os.path.exists(path):
            f = ImageFont.truetype(path, size)
            try:  # 可変フォントなら太さを指定
                f.set_variation_by_name(weight)
            except (OSError, ValueError):
                pass
            return f
    raise FileNotFoundError("日本語フォントが見つかりません（SNS_FONT で指定してください）")


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def fit_cover(img: Image.Image, w: int, h: int, anchor_y: float = 0.5) -> Image.Image:
    """縦横比を保って拡大し、はみ出た分を切り落とす（anchor_y=0 で上寄せ）。"""
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x = (img.width - w) // 2
    y = int((img.height - h) * anchor_y)
    return img.crop((x, y, x + w, y + h))


def cream(w: int = W, h: int = H) -> Image.Image:
    """クリームのグラデーション＋上下の金ライン（第10話と同じ地）。"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    d.rectangle((0, 0, w, 7), fill=GOLD)
    d.rectangle((0, h - 8, w, h), fill=GOLD)
    return img


def framed(panel: Image.Image, size: int) -> Image.Image:
    """コマに金の細枠を付ける。"""
    pad = 7
    out = Image.new("RGB", (size + pad * 2, size + pad * 2), GOLD)
    ImageDraw.Draw(out).rectangle((3, 3, size + pad * 2 - 4, size + pad * 2 - 4), outline=(250, 244, 228), width=2)
    out.paste(panel.convert("RGB").resize((size, size), Image.LANCZOS), (pad, pad))
    return out


def centered(d: ImageDraw.ImageDraw, y: int, text: str, size: int, fill, weight: str = "Bold") -> None:
    d.text((W // 2, y), text, font=font(size, weight), fill=fill, anchor="mm")


# ---------------------------------------------------------------- リールの各画面

def slide_hook(s: dict) -> Image.Image:
    img = cream()
    d = ImageDraw.Draw(img)
    d.text((80, 470), f"福合わせたぬき猫の4コマ・第{s['episode']}話「{s['title']}」", font=font(34, "Medium"), fill=INK)
    centered(d, 680, s["hook_line1"], 56, INK, "Medium")
    lines = s["hook_line2"].split("\n")[:3]
    for i, line in enumerate(lines):
        centered(d, 820 + i * 140, line, 88, RED)
    centered(d, 820 + len(lines) * 140 + 120, f"— {s['moral_short']} —", 38, GRAY, "Medium")
    return img


def slide_panel(panel: Image.Image, episode: int) -> Image.Image:
    img = cream()
    d = ImageDraw.Draw(img)
    centered(d, 375, f"福合わせたぬき猫の4コマ　｜　第{episode}話", 38, INK, "Medium")
    f = framed(panel, 1000)
    img.paste(f, ((W - f.width) // 2, 520))
    return img


def slide_closing(s: dict, cfg: dict) -> Image.Image:
    img = cream()
    d = ImageDraw.Draw(img)
    centered(d, 330, f"第{s['episode']}話「{s['title']}」", 60, RED)
    logo = ASSETS / "logo_stamp.png"
    if logo.exists():
        stamp = Image.open(logo).convert("RGB").resize((420, 420), Image.LANCZOS)
        mask = Image.new("L", stamp.size, 0)
        ImageDraw.Draw(mask).ellipse((2, 2, 417, 417), fill=255)
        img.paste(stamp, ((W - 420) // 2, 520), mask)
    centered(d, 1120, s["closing_line"], 50, INK, "Medium")
    centered(d, 1265, cfg["reel"]["shop_line"], 52, RED)
    centered(d, 1355, cfg["reel"]["shop_sub"], 34, GRAY, "Medium")
    return img


def end_card(path: Path) -> Image.Image:
    if not path.exists():
        path = ASSETS / "end_card.jpg"
    return fit_cover(Image.open(path), W, H) if path.exists() else cream()


# ---------------------------------------------------------------- 静止画

def manga_grid(panels: list[Image.Image], s: dict) -> Image.Image:
    """X・Threads・note用の4コマまとめ（2x2、左上→右上→左下→右下）。"""
    size, gap, head = 520, 20, 110
    gw = size * 2 + gap * 3 + 14 * 2
    img = cream(gw, head + (size + 14) * 2 + gap * 3 + 50)
    d = ImageDraw.Draw(img)
    d.text((gw // 2, 60), f"福合わせたぬき猫の4コマ　第{s['episode']}話「{s['title']}」", font=font(36), fill=INK, anchor="mm")
    for i, p in enumerate(panels):
        f = framed(p, size)
        img.paste(f, (gap + (i % 2) * (f.width + gap), head + (i // 2) * (f.height + gap)))
    d.text((gw // 2, img.height - 38), "よしつねの秘密基地", font=font(24), fill=GRAY, anchor="mm")
    return img


def render_all(panels: list[Image.Image], title_card: Image.Image, note_art: Image.Image,
               s: dict, cfg: dict, out: Path) -> None:
    # 4コマ（1コマずつ・正方形）＝カルーセル①〜④、note本文用
    for i, p in enumerate(panels):
        fit_cover(p, 1080, 1080).save(out / f"carousel_{i + 1}.jpg", quality=92)
    title_v = fit_cover(title_card, W, H)
    title_v.save(out / "title_card.jpg", quality=92)
    fit_cover(title_card, 1080, 1080, anchor_y=0.15).save(out / "carousel_0_cover.jpg", quality=92)
    fit_cover(note_art, 1280, 670).save(out / "note_header.jpg", quality=92)
    manga_grid(panels, s).save(out / "manga.jpg", quality=90)

    r = cfg["reel"]
    frames = [(title_v, r["title_seconds"], "title"), (slide_hook(s), r["hook_seconds"], "still")]
    frames += [(slide_panel(p, s["episode"]), r["panel_seconds"], "zoom") for p in panels]
    frames += [(slide_closing(s, cfg), r["closing_seconds"], "still")]
    ends = {name: (end_card(ROOT / path), r["end_seconds"], "still") for name, path in r["platforms"].items()}
    build_reels(frames, ends, out, r, ROOT / "sns/assets/bgm")


# ---------------------------------------------------------------- 動画

def _segment(ff: str, frame: Image.Image, sec: float, kind: str, fps: int, zoom: float, tmp: Path, name: str) -> Path:
    png = tmp / f"{name}.png"
    frame.save(png)
    seg = tmp / f"{name}.mp4"
    n = int(sec * fps)
    if kind == "title":  # 暗いところからふわっと出て、ゆっくりズーム
        vf = (f"scale=2160:3840,zoompan=z='1+0.04*on/{n}':d={n}:s={W}x{H}:fps={fps}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fade=t=in:st=0:d=0.8,format=yuv420p")
    elif kind == "zoom" and zoom > 0:  # コマにゆっくり寄る
        vf = (f"scale=2160:3840,zoompan=z='1+{zoom}*on/{n}':d={n}:s={W}x{H}:fps={fps}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fade=t=in:st=0:d=0.25,format=yuv420p")
    else:
        vf = f"fps={fps},fade=t=in:st=0:d=0.25,format=yuv420p"
    subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps), "-i", str(png),
                    "-frames:v", str(n), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
                    "-crf", "25", "-maxrate", "2500k", "-bufsize", "5000k", str(seg)], check=True)
    return seg


def build_reels(frames: list, ends: dict, out: Path, r: dict, bgm_dir: Path) -> None:
    """共通部分は1回だけ作り、エンドカードだけ差し替えてSNSごとの動画を作る。"""
    ff = ffmpeg_exe()
    fps, zoom = r["fps"], r.get("panel_zoom", 0)
    bgms = sorted(glob.glob(str(bgm_dir / "*.mp3")) + glob.glob(str(bgm_dir / "*.m4a")))
    bgm = random.choice(bgms) if bgms else None
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        common = [_segment(ff, f, sec, kind, fps, zoom, tmp, f"c{i}") for i, (f, sec, kind) in enumerate(frames)]
        for name, (frame, sec, kind) in ends.items():
            segs = common + [_segment(ff, frame, sec, kind, fps, zoom, tmp, f"end_{name}")]
            lst = tmp / f"list_{name}.txt"
            lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs))
            video = tmp / f"video_{name}.mp4"
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                            "-c", "copy", str(video)], check=True)
            total = sum(x[1] for x in frames) + sec
            if bgm:
                audio_in = ["-stream_loop", "-1", "-i", bgm]
                af = f"volume={r.get('bgm_volume', 0.35)},afade=t=in:d=0.5,afade=t=out:st={total - 1.5:.2f}:d=1.5"
            else:  # 無音（BGMはアプリで付ける運用）。投稿APIのため無音トラックは入れておく
                audio_in = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
                af = "anull"
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(video), *audio_in, "-map", "0:v", "-map", "1:a",
                            "-af", af, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                            "-t", f"{total:.2f}", "-movflags", "+faststart", str(out / f"reel_{name}.mp4")], check=True)
