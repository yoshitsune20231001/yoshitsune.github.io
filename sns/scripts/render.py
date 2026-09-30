"""4コマの絵に吹き出しを入れ、4コマ画像（2x2）とリール動画（縦長スライド）を作る。"""
from __future__ import annotations

import glob
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]

CREAM = (253, 246, 232)
BROWN = (44, 26, 14)
GOLD = (201, 169, 110)
WHITE = (255, 255, 255)

W, H = 1080, 1920  # リール・ショートの縦長サイズ
KI_SHO_TEN_KETSU = ["起", "承", "転", "結"]

_FONT_CANDIDATES = [
    os.environ.get("SNS_FONT", ""),
    str(ROOT / "sns/assets/fonts/ZenMaruGothic-Bold.ttf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("日本語フォントが見つかりません（SNS_FONT で指定してください）")


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- 吹き出し

def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """日本語を1文字ずつ詰めて折り返す（句読点が行頭に来ないよう調整）。"""
    lines, cur = [], ""
    for ch in text:
        if fnt.getlength(cur + ch) <= max_w or not cur:
            cur += ch
        elif ch in "、。！？!?」』）ー…〜":
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def draw_bubble(img: Image.Image, text: str, speaker: str, box_x: int, box_y: int,
                max_w: int, size: int, align_right: bool) -> int:
    """吹き出しを描いて、その下端のyを返す。"""
    d = ImageDraw.Draw(img)
    fnt = font(size)
    sfnt = font(int(size * 0.5))
    lines = wrap(text, fnt, max_w - size)
    line_h = int(size * 1.3)
    pad = int(size * 0.55)
    text_w = max(fnt.getlength(line) for line in lines)
    bw = int(text_w + pad * 2)
    bh = int(line_h * len(lines) + pad * 2 - (line_h - size))
    x0 = box_x + max_w - bw if align_right else box_x
    y0 = box_y + (int(size * 0.55) if speaker else 0)
    r = int(size * 0.8)
    # 影 → 本体
    d.rounded_rectangle((x0 + 5, y0 + 6, x0 + bw + 5, y0 + bh + 6), r, fill=(0, 0, 0, 60))
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), r, fill=WHITE, outline=BROWN, width=max(3, size // 14))
    # しっぽ（下向き）
    tx = x0 + (bw * 3 // 4 if align_right else bw // 4)
    tail = [(tx - size // 3, y0 + bh - 3), (tx + size // 3, y0 + bh - 3), (tx + (size // 4 if align_right else -size // 4), y0 + bh + size // 2)]
    d.polygon(tail, fill=WHITE)
    d.line([tail[0], tail[2], tail[1]], fill=BROWN, width=max(3, size // 14))
    if speaker:
        sx = x0 + bw - sfnt.getlength(speaker) - pad // 2 if align_right else x0 + pad // 2
        d.text((sx, y0 - int(size * 0.6)), speaker, font=sfnt, fill=BROWN, stroke_width=4, stroke_fill=WHITE)
    for i, line in enumerate(lines):
        d.text((x0 + pad, y0 + pad + i * line_h), line, font=fnt, fill=BROWN)
    return y0 + bh + size // 2


def panel_with_bubbles(panel: Image.Image, lines: list[dict], width: int) -> Image.Image:
    """絵を width 幅にして、上部に台詞の吹き出しを重ねる。"""
    h = int(panel.height * width / panel.width)
    img = panel.convert("RGBA").resize((width, h), Image.LANCZOS)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    size = max(24, width // 17)
    margin = width // 22
    y = margin
    for i, line in enumerate(lines[:2]):
        y = draw_bubble(overlay, line["text"], line.get("speaker", ""), margin, y,
                        int(width * 0.78), size, align_right=(i % 2 == 1))
    return Image.alpha_composite(img, overlay).convert("RGB")


# ---------------------------------------------------------------- 静止画

def reel_frame(panel: Image.Image, lines: list[dict], idx: int, title: str) -> Image.Image:
    frame = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(frame)
    d.text((W // 2, 95), title, font=font(54), fill=BROWN, anchor="mm")
    pw = 1000
    art = panel_with_bubbles(panel, lines, pw)
    top = 170
    frame.paste(art, ((W - pw) // 2, top))
    d.rectangle(((W - pw) // 2 - 4, top - 4, (W + pw) // 2 + 3, top + art.height + 3), outline=BROWN, width=6)
    # コマ番号バッジ（起承転結）
    cx, cy = (W - pw) // 2 + 60, top + art.height - 60
    d.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), fill=BROWN, outline=GOLD, width=5)
    d.text((cx, cy), KI_SHO_TEN_KETSU[idx], font=font(50), fill=CREAM, anchor="mm")
    d.text((W // 2, top + art.height + 70), f"{idx + 1} / 4", font=font(36), fill=GOLD, anchor="mm")
    return frame


def title_frame(first_panel: Image.Image, series: str, title: str) -> Image.Image:
    bg = first_panel.convert("RGB").resize((W, int(first_panel.height * W / first_panel.width)))
    bg = bg.crop((0, 0, W, min(H, bg.height))).resize((W, H)).filter(ImageFilter.GaussianBlur(18))
    frame = Image.blend(bg, Image.new("RGB", (W, H), CREAM), 0.55)
    d = ImageDraw.Draw(frame)
    d.text((W // 2, H // 2 - 170), series, font=font(58), fill=GOLD, anchor="mm", stroke_width=6, stroke_fill=WHITE)
    for i, line in enumerate(wrap(title, font(96), W - 160)):
        d.text((W // 2, H // 2 + i * 120), line, font=font(96), fill=BROWN, anchor="mm", stroke_width=8, stroke_fill=WHITE)
    return frame


def end_frame(brand: str, shop: str, tag: str) -> Image.Image:
    frame = Image.new("RGB", (W, H), BROWN)
    d = ImageDraw.Draw(frame)
    d.text((W // 2, H // 2 - 180), tag, font=font(60), fill=GOLD, anchor="mm")
    d.text((W // 2, H // 2), brand, font=font(84), fill=CREAM, anchor="mm")
    shop_lines = [x.strip("）") for x in shop.split("（")]
    for i, line in enumerate(shop_lines):
        d.text((W // 2, H // 2 + 130 + i * 62), line, font=font(44), fill=CREAM, anchor="mm")
    d.text((W // 2, H // 2 + 330), "右手で金運　左手で良縁", font=font(50), fill=GOLD, anchor="mm")
    return frame


def manga_grid(panels: list[Image.Image], script: dict, series: str) -> Image.Image:
    """X・Threads・note 用の4コマ画像（2x2、読む順は 左上→右上→左下→右下）。"""
    pw, gap, head = 520, 16, 120
    ph = int(panels[0].height * pw / panels[0].width)
    gw = pw * 2 + gap * 3
    gh = head + ph * 2 + gap * 3 + 60
    img = Image.new("RGB", (gw, gh), CREAM)
    d = ImageDraw.Draw(img)
    d.text((gw // 2, 42), series, font=font(30), fill=GOLD, anchor="mm")
    d.text((gw // 2, 86), script["title"], font=font(44), fill=BROWN, anchor="mm")
    for i, (p, s) in enumerate(zip(panels, script["panels"])):
        x = gap + (i % 2) * (pw + gap)
        y = head + gap + (i // 2) * (ph + gap)
        img.paste(panel_with_bubbles(p, s["lines"], pw), (x, y))
        d.rectangle((x - 2, y - 2, x + pw + 1, y + ph + 1), outline=BROWN, width=4)
        d.ellipse((x + 10, y + ph - 58, x + 58, y + ph - 10), fill=BROWN)
        d.text((x + 34, y + ph - 34), KI_SHO_TEN_KETSU[i], font=font(28), fill=CREAM, anchor="mm")
    d.text((gw // 2, gh - 32), "義恒（よしつね）｜よしつねの秘密基地", font=font(24), fill=BROWN, anchor="mm")
    return img


# ---------------------------------------------------------------- 動画

def build_reel(frames: list[tuple[Image.Image, float]], out: Path, fps: int, bgm_dir: Path, bgm_volume: float) -> None:
    ff = ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seg_list = []
        for i, (frame, sec) in enumerate(frames):
            png = tmp / f"f{i}.png"
            frame.save(png)
            seg = tmp / f"s{i}.mp4"
            n = int(sec * fps)
            fade = 0.3
            vf = (
                f"scale=2160:3840,zoompan=z='1+0.035*on/{n}':d={n}:s={W}x{H}:fps={fps}"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
                f"fade=t=in:st=0:d={fade},fade=t=out:st={sec - fade:.2f}:d={fade},format=yuv420p"
            )
            subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps), "-i", str(png),
                            "-frames:v", str(n), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
                            "-crf", "24", str(seg)], check=True)
            seg_list.append(seg)
        (tmp / "list.txt").write_text("".join(f"file '{s.as_posix()}'\n" for s in seg_list))
        silent = tmp / "video.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(silent)], check=True)
        total = sum(sec for _, sec in frames)
        bgms = sorted(glob.glob(str(bgm_dir / "*.mp3")) + glob.glob(str(bgm_dir / "*.m4a")))
        if bgms:
            audio_in = ["-stream_loop", "-1", "-i", random.choice(bgms)]
            af = f"volume={bgm_volume},afade=t=in:d=0.5,afade=t=out:st={total - 1.5:.2f}:d=1.5"
        else:  # Instagram は音声トラック付きが無難なので無音を入れる
            audio_in = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
            af = "anull"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(silent), *audio_in, "-map", "0:v", "-map", "1:a",
                        "-af", af, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)], check=True)


def note_header(panels: list[Image.Image], series: str, episode_label: str, title: str) -> Image.Image:
    """note の見出し画像（横長 1280x670）。左に4コマの絵、右にタイトル。"""
    nw, nh = 1280, 670
    img = Image.new("RGB", (nw, nh), CREAM)
    # 左：1コマ目と4コマ目を並べる
    pw, ph = 300, 450
    for j, p in enumerate([panels[0], panels[-1]]):
        art = p.convert("RGB").resize((pw, int(p.height * pw / p.width)))
        art = art.crop((0, (art.height - ph) // 2, pw, (art.height - ph) // 2 + ph))
        x, y = 50 + j * (pw + 24), (nh - ph) // 2 + (-18 if j == 0 else 18)
        img.paste(art, (x, y))
        ImageDraw.Draw(img).rectangle((x - 3, y - 3, x + pw + 2, y + ph + 2), outline=BROWN, width=6)
    d = ImageDraw.Draw(img)
    tx = 50 + 2 * pw + 24 + 50
    tw = nw - tx - 50
    d.text((tx, 150), series, font=font(38), fill=GOLD)
    d.rounded_rectangle((tx, 215, tx + 190, 285), 35, fill=BROWN)
    d.text((tx + 95, 250), episode_label, font=font(40), fill=CREAM, anchor="mm")
    size = 76 if len(title) <= 7 else 60
    for i, line in enumerate(wrap(title, font(size), tw)[:3]):
        d.text((tx, 320 + i * int(size * 1.3)), line, font=font(size), fill=BROWN)
    d.text((tx, nh - 80), "近江八幡・よしつねの秘密基地", font=font(28), fill=BROWN)
    d.rectangle((0, nh - 16, nw, nh), fill=GOLD)
    return img


def render_all(panels: list[Image.Image], script: dict, cfg: dict, out_dir: Path) -> None:
    series = f"{cfg['character']['name']} 4コマ"
    episode_label = f"第{script['episode']}話"
    note_header(panels, series, episode_label, script["title"]).save(out_dir / "note_header.jpg", quality=90)
    series = f"{series}　{episode_label}"
    vcfg = cfg["video"]
    manga_grid(panels, script, series).save(out_dir / "manga.jpg", quality=88)
    frames = [(title_frame(panels[0], series, script["title"]), vcfg["title_seconds"])]
    for i, (p, s) in enumerate(zip(panels, script["panels"])):
        frame = reel_frame(p, s["lines"], i, script["title"])
        frames.append((frame, vcfg["panel_seconds"]))
    frames.append((end_frame(cfg["brand"]["name"], cfg["brand"]["shop"], cfg["story"]["ending_tag"]), vcfg["end_seconds"]))
    frames[1][0].save(out_dir / "cover.jpg", quality=88)  # サムネ用
    build_reel(frames, out_dir / "reel.mp4", vcfg["fps"], ROOT / "sns/assets/bgm", vcfg["bgm_volume"])
