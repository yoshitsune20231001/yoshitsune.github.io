"""毎日の4コマ漫画を生成する。

  1. ChatGPT（OpenAI）が台本（起承転結・台詞・投稿文）を作る
  2. OpenAI の画像生成で、見本画像をもとに4コマ分の絵を描く
  3. 吹き出しを入れて、4コマ画像とリール動画を作る
  4. sns/output/<日付>/ に保存し、sns/history.json に記録する

使い方:
  python sns/scripts/generate.py            # 本番（OPENAI_API_KEY が必要）
  python sns/scripts/generate.py --mock     # APIを使わない動作テスト
  python sns/scripts/generate.py --theme "お月見"
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
import render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "sns/output"
HISTORY = ROOT / "sns/history.json"
JST = ZoneInfo("Asia/Tokyo")


# ---------------------------------------------------------------- 台本の形

class Line(BaseModel):
    speaker: str = Field(description="話している人（キャラ名。ナレーションなら空文字）")
    text: str = Field(description="台詞。15文字以内")


class Panel(BaseModel):
    scene_ja: str = Field(description="このコマで何が起きているか（日本語で1〜2文）")
    scene_en: str = Field(description="画像AI向けの場面説明（英語）。登場キャラの位置・表情・動き・背景。文字や看板の文字は含めない")
    lines: list[Line] = Field(description="台詞（0〜2個）")


class Script(BaseModel):
    title: str = Field(description="今日の4コマのタイトル（12文字以内）")
    theme: str
    panels: list[Panel] = Field(description="ちょうど4コマ（起・承・転・結）")
    caption_instagram: str = Field(description="Instagramリールの投稿文（口調ルールどおり。ハッシュタグは含めない）")
    caption_threads: str = Field(description="Threadsの投稿文（300文字以内。ハッシュタグは含めない）")
    caption_x: str = Field(description="Xの投稿文（全角100文字以内。ハッシュタグは含めない）")
    youtube_title: str = Field(description="YouTubeショートのタイトル（40文字以内、#Shortsは不要）")
    hashtags: list[str] = Field(description="その回のテーマに合うハッシュタグ3〜5個（#付き）")
    note_article: str = Field(description="note用の短い記事の下書き（4コマの見どころと裏話、400〜600文字）")


# ---------------------------------------------------------------- 台本づくり

def load_history() -> list[dict]:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return []


def build_script_prompt(cfg: dict, today: dt.date, theme: str | None, history: list[dict]) -> str:
    ch = cfg["character"]
    past = "\n".join(f"- {h['date']}「{h['title']}」{h.get('theme', '')}" for h in history[-30:]) or "（まだありません）"
    return f"""あなたは人気4コマ漫画の作家です。SNS（Instagramリール・YouTubeショートなど）で毎日公開する4コマ漫画の台本を1本作ってください。

# 今日の日付
{today.strftime('%Y年%m月%d日')}（{'月火水木金土日'[today.weekday()]}曜日）

# 主人公
コンビ名：{ch['name']}
キャラクター・話し方：{ch['personality']}
台詞の speaker は {' / '.join(ch['speakers'])} / ご主人 / その他の登場人物名 のいずれか
脇役：{ch['supporting_cast']}

# テーマ
{theme or 'つぎの候補から、今日の日付・季節に合うものを1つ選ぶ：' + ' / '.join(cfg['story']['themes'])}

# お話のルール
{cfg['story']['rules']}

# 投稿文の口調（Instagram）
{cfg['caption_style']}
お店：{cfg['brand']['shop']}

# 最近の回（ネタがかぶらないようにする）
{past}

scene_en は画像生成AIへの指示です。主人公2匹は必ず "the TANUKI" と "the NEKO (boy cat)" と書き、見た目の細かい説明はしないでください（見本画像を別に渡します）。どのコマに誰が出るかをはっきり書いてください。
各コマの上から4分の1は吹き出しを入れるので、キャラクターは画面の中央〜下に配置する指示を含めてください。"""


def make_script(cfg: dict, today: dt.date, theme: str | None, history: list[dict]) -> Script:
    from openai import OpenAI

    client = OpenAI()
    prompt = build_script_prompt(cfg, today, theme, history)
    for attempt in range(3):
        resp = client.responses.parse(model=cfg["openai"]["text_model"], input=prompt, text_format=Script)
        script = resp.output_parsed
        if script and len(script.panels) == 4:
            return script
        print(f"台本の形式が不正のため再生成します（{attempt + 1}回目）", file=sys.stderr)
    raise RuntimeError("台本を生成できませんでした")


# ---------------------------------------------------------------- 絵づくり

def build_image_prompt(cfg: dict, panel: Panel, idx: int) -> str:
    ch = cfg["character"]
    return (
        f"Panel {idx + 1} of a 4-koma manga (vertical single panel, no border).\n"
        f"Art style: {cfg['art_style_en']}\n"
        f"Main characters (use the attached reference image(s) and keep the designs identical): {ch['appearance_en']}\n"
        f"Scene: {panel.scene_en}\n"
        "Composition: keep the top quarter of the image as simple, calm background with no important "
        "objects (speech bubbles will be added there later). Characters in the middle and lower part.\n"
        "IMPORTANT: absolutely no text, letters, speech bubbles, captions, signatures or watermarks in the image. "
        "Do not draw Hikonyan or any other existing mascot, real person, brand or logo."
    )


def make_panel_image(cfg: dict, panel: Panel, idx: int, out: Path) -> Image.Image:
    from openai import OpenAI

    client = OpenAI()
    ocfg = cfg["openai"]
    refs = [ROOT / p for p in cfg["character"]["reference_images"]]
    last_err = None
    for attempt in range(3):
        files = [open(p, "rb") for p in refs]
        try:
            result = client.images.edit(
                model=ocfg["image_model"],
                image=files,
                prompt=build_image_prompt(cfg, panel, idx),
                size=ocfg["image_size"],
                quality=ocfg["image_quality"],
            )
            out.write_bytes(base64.b64decode(result.data[0].b64_json))
            img = Image.open(out).convert("RGB")
            img.save(out.with_suffix(".jpg"), quality=88)
            out.unlink()
            return img
        except Exception as e:  # noqa: BLE001  一時的なエラーは待って再試行
            last_err = e
            print(f"コマ{idx + 1}の画像生成に失敗（{attempt + 1}回目）: {e}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
        finally:
            for f in files:
                f.close()
    raise RuntimeError(f"コマ{idx + 1}の画像を生成できませんでした: {last_err}")


# ---------------------------------------------------------------- テスト用

def mock_script() -> Script:
    return Script(
        title="秋の開店じゅんび",
        theme="古民家のお店の日常",
        panels=[
            Panel(scene_ja="朝、お店の前を掃除する", scene_en="", lines=[Line(speaker="たぬき", text="今日もいい天気だぬ！")]),
            Panel(scene_ja="落ち葉が舞い込む", scene_en="", lines=[Line(speaker="たぬき", text="落ち葉がいっぱいだぬ…"), Line(speaker="ねこ", text="秋だにゃあ")]),
            Panel(scene_ja="右手で招くと、落ち葉がさらに集まる", scene_en="", lines=[Line(speaker="ねこ", text="えいっ、招きパワー！")]),
            Panel(scene_ja="落ち葉の山の上で満足げ", scene_en="", lines=[Line(speaker="たぬき", text="福じゃなくて葉っぱ招いたぬ")]),
        ],
        caption_instagram="【4コマ】秋の開店じゅんび🍂\nこんにちは、福合わせたぬき猫です🍂\n\n（テスト投稿文）\n\n右手で金運💰左手で良縁🤝",
        caption_threads="（テスト）秋の開店じゅんび🍂",
        caption_x="（テスト）秋の開店じゅんび🍂",
        youtube_title="【4コマ】秋の開店じゅんび🍂 福合わせたぬき猫",
        hashtags=["#秋", "#落ち葉"],
        note_article="（テスト記事）",
    )


def mock_panel(idx: int, out: Path) -> Image.Image:
    colors = [(236, 214, 180), (214, 226, 200), (226, 206, 214), (206, 218, 236)]
    img = Image.new("RGB", (1024, 1536), colors[idx])
    d = ImageDraw.Draw(img)
    d.ellipse((312, 700, 712, 1200), fill=(170, 130, 90), outline=(60, 40, 20), width=8)
    d.text((512, 1300), f"MOCK PANEL {idx + 1}", fill=(60, 40, 20), anchor="mm", font=render.font(60))
    ref = Image.open(ROOT / cfg_global["character"]["reference_images"][0]).convert("RGB")
    ref.thumbnail((360, 520))
    img.paste(ref, (512 - ref.width // 2, 950 - ref.height // 2))
    img.save(out.with_suffix(".jpg"), quality=88)
    return img


# ---------------------------------------------------------------- 本体

def new_output_dir(today: dt.date) -> Path:
    base = OUTPUT / today.isoformat()
    out, n = base, 2
    while out.exists():
        out = OUTPUT / f"{today.isoformat()}-{n}"
        n += 1
    out.mkdir(parents=True)
    return out


def prune_old(keep_days: int, today: dt.date) -> None:
    if not OUTPUT.exists():
        return
    limit = today - dt.timedelta(days=keep_days)
    for d in OUTPUT.iterdir():
        try:
            day = dt.date.fromisoformat(d.name[:10])
        except ValueError:
            continue
        if d.is_dir() and day < limit:
            shutil.rmtree(d)


def main() -> None:
    global cfg_global
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="APIを使わずに動作テスト")
    ap.add_argument("--theme", default=os.environ.get("THEME") or None)
    args = ap.parse_args()

    cfg = cfg_global = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))
    today = dt.datetime.now(JST).date()
    history = load_history()

    if not args.mock and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY が設定されていません（GitHub の Secrets に登録してください）")

    print("① 台本を作成中…")
    script = mock_script() if args.mock else make_script(cfg, today, args.theme, history)
    print(f"   タイトル：{script.title}")

    out = new_output_dir(today)
    panels = []
    for i, p in enumerate(script.panels):
        print(f"② コマ{i + 1}の絵を生成中…")
        path = out / f"panel{i + 1}.png"
        panels.append(mock_panel(i, path) if args.mock else make_panel_image(cfg, p, i, path))

    print("③ 吹き出し・4コマ画像・動画を作成中…")
    data = script.model_dump()
    render.render_all(panels, data, cfg, out)

    tags = list(dict.fromkeys(cfg["hashtags_base"] + script.hashtags))
    data.update(
        date=today.isoformat(),
        dir=out.relative_to(ROOT).as_posix(),
        hashtags_all=tags,
        public_urls={
            name: f"{cfg['public_base_url']}/{out.relative_to(ROOT).as_posix()}/{name}"
            for name in ["reel.mp4", "manga.jpg", "cover.jpg"]
        },
        status="承認待ち",
        mock=args.mock,
    )
    (out / "post.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "note.md").write_text(f"# {script.title}\n\n![4コマ](manga.jpg)\n\n{script.note_article}\n\n{' '.join(tags)}\n", encoding="utf-8")

    if not args.mock:
        history.append({"date": today.isoformat(), "title": script.title, "theme": script.theme, "dir": data["dir"]})
        HISTORY.write_text(json.dumps(history[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    prune_old(cfg.get("keep_days", 30), today)

    print(f"④ 完了：{data['dir']}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"dir={data['dir']}\n")


cfg_global: dict = {}

if __name__ == "__main__":
    main()
