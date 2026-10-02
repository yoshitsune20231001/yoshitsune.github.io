"""毎日の4コマ漫画を生成する。

  1. ChatGPT（OpenAI）が台本（起承転結・台詞・投稿文）を作る
  2. OpenAI の画像生成で、キャラクターシートと第10話の絵を見本に
     タイトルカード（縦）・4コマ（正方形・吹き出し入り）・note見出し（横）を描く
  3. 第10話と同じ構成のリール動画・カルーセル・4コマまとめ画像を作る
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
from PIL import Image
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
import render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "sns/output"
HISTORY = ROOT / "sns/history.json"
JST = ZoneInfo("Asia/Tokyo")


# ---------------------------------------------------------------- 台本の形

class Line(BaseModel):
    speaker: str = Field(description="話している人（たぬき / 猫 / ご主人 / その他の登場人物名。ナレーションなら空文字）")
    text: str = Field(description="台詞。20文字以内")


class Panel(BaseModel):
    scene_ja: str = Field(description="このコマで何が起きているか（日本語で1〜2文）")
    scene_en: str = Field(description="画像AI向けの場面説明（英語）。登場キャラの位置・表情・動き・背景・小道具。台詞は含めない（別に渡す）")
    lines: list[Line] = Field(description="台詞（0〜2個）")


class Script(BaseModel):
    title: str = Field(description="4コマのタイトル（例：百万両よりお団子。12文字以内）")
    theme: str
    location: str = Field(description="今回の背景の場所（日本語。最近の回と違う場所）")
    location_en: str = Field(description="今回の背景の場所の詳しい説明（英語。4コマ共通の舞台）")
    panels: list[Panel] = Field(description="ちょうど4コマ（起・承・転・結）")
    moral: str = Field(description="オチから導く、小さな福・気づきのひとこと（2行程度）")
    hook_line1: str = Field(description="リールのフック1行目（例：百万両あるのに、）15文字以内")
    hook_line2: str = Field(description="リールのフック（赤字の大きい文字）。改行\\nで2行まで、1行8文字以内（例：買うのは…\\nお団子!?）")
    moral_short: str = Field(description="フック画面の下に添える短い言葉（例：大金より、小さな幸せ）12文字以内")
    closing_line: str = Field(description="リール締め画面のひとこと（例：大金より、小さな幸せを。）15文字以内")
    title_card_scene_en: str = Field(description="タイトルカード（縦長）の場面説明（英語）。2匹がこの回のキーアイテムを持って笑顔、季節と近江八幡らしい背景")
    note_header_scene_en: str = Field(description="note見出し（横長）の場面説明（英語）。タイトルカードと同じ雰囲気で横長の構図")
    x_post: str = Field(description="Xの本ポスト本文（見本と同じ型。台詞の抜粋＋2行のひとこと。リンク・ハッシュタグは含めない）")
    x_reply: str = Field(description="Xのリプライ本文（見本と同じ型。第N話の案内とショップ誘導。URLは書かない＝後で自動で付く）")
    instagram_reel_caption: str = Field(description="インスタリールのキャプション（見本と同じ型。ハッシュタグは含めない）")
    instagram_post_caption: str = Field(description="インスタのカルーセル投稿キャプション（見本と同じ型。ハッシュタグは含めない）")
    tiktok_caption: str = Field(description="TikTokのキャプション（1行目に強いフック、台詞の抜粋、短いひとこと、『プロフィールのリンクから』。絵文字多め・テンポよく。ハッシュタグは含めない。200文字以内）")
    facebook_post: str = Field(description="Facebookページの投稿文（インスタより少し説明的に、ていねいに。台詞の抜粋＋ひとこと＋『オンラインショップはこちら』の一文で終える。URLとハッシュタグは書かない＝後で自動で付く。400文字以内）")
    story_text: str = Field(description="ストーリーズ画像に大きく入れる短い一言（例：新作4コマ公開！ 14文字以内）")
    tiktok_pinned_comment: str = Field(description="TikTokの固定コメント（視聴者が返信したくなる問いかけ＋ショップはプロフィールのリンクから）")
    youtube_title: str = Field(description="YouTubeショートのタイトル（見本と同じ型。『福合わせたぬき猫4コマ第N話 #shorts』で終わる）")
    youtube_description: str = Field(description="YouTube概要欄（見本と同じ型。ハッシュタグ行は含めない）")
    youtube_tags: list[str] = Field(description="YouTubeのタグ（10個前後。#は付けない）")
    youtube_pinned_comment: str = Field(description="YouTubeの固定コメント（見本と同じ型）")
    note_title: str = Field(description="note記事のタイトル（見本のように文章型で、4コマの内容と気づきが伝わるもの）")
    note_body: str = Field(description="note記事の本文（見本と同じ構成。〔①コマ目の画像〕〜〔④コマ目の画像〕を入れる）")
    extra_hashtags: list[str] = Field(description="この回のテーマに合う追加ハッシュタグ1〜2個（#付き。無理に作らない）")


# ---------------------------------------------------------------- 台本づくり

# ---------------------------------------------------------------- 作り直し（確認Issueから）

def fetch_redo(issue_number: str) -> dict:
    """作り直すIssueから、前回の出力フォルダとコメントの指示を読む。"""
    import re

    import requests

    repo = os.environ["GITHUB_REPOSITORY"]
    headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    issue = requests.get(api, headers=headers, timeout=30).json()
    comments = requests.get(f"{api}/comments?per_page=100", headers=headers, timeout=30).json()
    m = re.search(r"<!-- dir:(\S+) -->", issue.get("body") or "")
    notes = [c["body"].strip() for c in comments
             if c.get("user", {}).get("type") != "Bot" and "<!-- bot -->" not in c["body"]]
    return {"dir": m.group(1) if m else None, "feedback": "\n".join(n for n in notes if n)}


def redo_targets(feedback: str) -> set | None:
    """コメントから「3コマ目」「タイトル」「note見出し」などの作り直し対象を読み取る（無ければ None＝全部）。"""
    import re

    nums = {"1": 1, "2": 2, "3": 3, "4": 4, "１": 1, "２": 2, "３": 3, "４": 4, "①": 1, "②": 2, "③": 3, "④": 4}
    t: set = set()
    for m in re.findall(r"([1-4１-４])\s*(?:コマ|こま)|([①-④])", feedback or ""):
        t.add(nums[m[0] or m[1]])
    if re.search(r"タイトル|表紙", feedback or ""):
        t.add("title")
    if re.search(r"note|ノート|見出し", feedback or ""):
        t.add("note")
    return t or None


def reuse_image(prev: dict, name: str, out: Path) -> Image.Image:
    """前回の回の画像（GitHub Releases）をダウンロードして使う。"""
    import io

    import requests

    url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/releases/download/{prev['release_tag']}/{name}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    img.save(out, quality=92)
    return img


def previous_summary(post: dict) -> str:
    lines = [f"タイトル「{post['title']}」 場所：{post.get('location', '')}"]
    for k, p in zip("起承転結", post["panels"]):
        lines.append(f"{k}：{p['scene_ja']} " + " / ".join("{}「{}」".format(x["speaker"], x["text"]) for x in p["lines"]))
    return "\n".join(lines)


def load_history() -> list[dict]:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return []


def load_examples(cfg: dict) -> str:
    """過去の回の投稿文（見本）を読み込む。"""
    parts = []
    for d in cfg.get("examples", []):
        for f in sorted((ROOT / d).glob("*.txt")):
            parts.append(f"----- 見本ファイル：{f.name} -----\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts) or "（見本なし）"


def build_script_prompt(cfg: dict, today: dt.date, theme: str | None, history: list[dict], episode: int) -> str:
    ch = cfg["character"]
    past = "\n".join(f"- 第{h.get('episode', '?')}話「{h['title']}」テーマ:{h.get('theme', '')} 場所:{h.get('location', '?')}" for h in history[-30:]) or "（まだありません）"
    recent_places = "、".join(h.get("location", "") for h in history[-7:] if h.get("location")) or "なし"
    return f"""あなたは人気4コマ漫画の作家兼SNS担当です。「{ch['name']}」の4コマ漫画・第{episode}話の台本と、各SNSの投稿文を作ってください。

# 投稿日（季節や行事はこの日に合わせる）
{today.strftime('%Y年%m月%d日')}（{'月火水木金土日'[today.weekday()]}曜日）

# キャラクター
{ch['personality']}
脇役：{ch['supporting_cast']}

# テーマ
{theme or 'つぎの候補から、今日の日付・季節に合うものを1つ選ぶ：' + ' / '.join(cfg['story']['themes'])}

# お話のルール
{cfg['story']['rules']}

# 背景の場所（最近7回で使った場所「{recent_places}」は使わない）
{' / '.join(cfg['locations'])}

{cfg.get("_redo_section", "")}
# 過去の回（ネタがかぶらないようにする。第11話・第12話はストック済みで内容は不明）
{past}

# 投稿文の見本（第10話）
下の見本と「同じ構成・同じ口調・同じ長さ感・同じ締めの定型文」で、第{episode}話の内容に置き換えて書いてください。
ショップ誘導の文言（オンラインショップにて販売中、プロフィールのリンクから 等）や署名（よしつねの秘密基地　義恒スタッフ一同）も見本どおりにします。

{load_examples(cfg)}

# 画像AI向けの指示（scene_en）
主人公2匹は必ず "the TANUKI" と "the NEKO (boy cat)" と書き、見た目の細かい説明はしないでください（見本画像を別に渡します）。
どのコマに誰が出るか・どんな大きな動きをしているか・表情・小道具・カメラの寄り引きをはっきり書いてください。
2匹がただ並んで立っているコマは作らないでください（第10話は動きが少なかったので、もっと動かす）。
4コマとも location_en の同じ舞台で、コマごとに角度や寄り引きを変えます。吹き出しが入る余白を上の方に残します。
title_card_scene_en / note_header_scene_en は、2匹がこの回のキーアイテム（例：お団子）を持って笑顔でいる、季節に合った明るい場面にしてください。"""


def make_script(cfg: dict, today: dt.date, theme: str | None, history: list[dict], episode: int) -> Script:
    from openai import OpenAI

    client = OpenAI()
    prompt = build_script_prompt(cfg, today, theme, history, episode)
    for attempt in range(3):
        resp = client.responses.parse(model=cfg["openai"]["text_model"], input=prompt, text_format=Script)
        script = resp.output_parsed
        if script and len(script.panels) == 4:
            return script
        print(f"台本の形式が不正のため再生成します（{attempt + 1}回目）", file=sys.stderr)
    raise RuntimeError("台本を生成できませんでした")


# ---------------------------------------------------------------- 絵づくり

def _common(cfg: dict) -> str:
    return (
        f"Art style: {cfg['art_style_en']}\n"
        f"Characters (keep designs IDENTICAL to the attached character sheets): {cfg['character']['appearance_en']}\n"
        "Match ONLY the drawing style and coloring of the attached sample 4-koma panel. Do NOT copy its background, composition or poses.\n"
        "Never draw Hikonyan or any other existing mascot, real person, brand or logo."
        + (f"\nIMPORTANT extra instructions from the owner (follow these first): {cfg['_feedback']}" if cfg.get("_feedback") else "")
    )


def panel_prompt(cfg: dict, sc: Script, panel: Panel, idx: int) -> str:
    who = {"たぬき": "the TANUKI", "猫": "the NEKO", "ご主人": "the owner's hand"}
    lines = "\n".join(f'- bubble from {who.get(ln.speaker, ln.speaker)}: {ln.text}' for ln in panel.lines) or "- (no dialogue)"
    title = (f'Put a wooden title banner at the top reading exactly 「{sc.title}」 in bold Japanese brush lettering.\n'
             if idx == 0 else "")
    return (
        f"Square panel {idx + 1} of 4 of a Japanese 4-koma manga.\n{_common(cfg)}\n"
        f"Location (same for all 4 panels): {sc.location_en}\n"
        f"Scene: {panel.scene_en}\n{cfg['motion_rules_en']}\n{title}"
        f"Draw a small black circled number {'①②③④'[idx]} in the top-left corner.\n"
        "Draw white round speech bubbles with this exact Japanese dialogue (vertical-friendly line breaks, "
        "clear readable black text, tail pointing to the speaker). Write ONLY the Japanese words after the colon "
        "inside each bubble; never write the speaker's name, a colon or quotation marks in the bubble:\n"
        f"{lines}\n"
        "If a line is spoken by ご主人 (the owner), point that bubble's tail to the owner's hand/sleeve; never draw the owner's face.\n"
        "Keep the same background location and character designs as the other panels. No other text."
    )


def title_card_prompt(cfg: dict, sc: Script, episode: int) -> str:
    return (
        f"Vertical 9:16 title card illustration for a 4-koma manga episode.\n{_common(cfg)}\n"
        f"Location: {sc.location_en}\nScene: {sc.title_card_scene_en}\n"
        "Characters in lively, dynamic poses (jumping, running or dancing), not just standing.\n"
        f"At the top, a large ornate wooden signboard with sakura/plum decorations that reads exactly "
        f"「第{episode}話」 (small) and 「{sc.title}」 (large, bold Japanese lettering, one keyword in red).\n"
        "Keep the signboard and both characters inside the central 85% width. No other text."
    )


def note_header_prompt(cfg: dict, sc: Script, episode: int) -> str:
    return (
        f"Wide horizontal banner illustration (about 2:1) for a blog header.\n{_common(cfg)}\n"
        f"Location: {sc.location_en}\nScene: {sc.note_header_scene_en}\n"
        "Characters in lively, dynamic poses, not just standing.\n"
        f"In the upper center, a wide wooden signboard that reads exactly 「第{episode}話」 (small) and "
        f"「{sc.title}」 (large bold Japanese lettering, one keyword in red). The two characters on the left and right.\n"
        "Keep all important content inside the middle 70% of the height (top and bottom will be cropped). No other text."
    )


def gen_image(cfg: dict, prompt: str, size: str, out: Path, extra_refs: list[Path] = ()) -> Image.Image:
    from openai import OpenAI

    client = OpenAI()
    ocfg = cfg["openai"]
    refs = [ROOT / p for p in cfg["character"]["reference_images"]] + list(extra_refs)
    last_err = None
    for attempt in range(3):
        files = [open(p, "rb") for p in refs]
        try:
            result = client.images.edit(model=ocfg["image_model"], image=files, prompt=prompt,
                                        size=size, quality=ocfg["image_quality"])
            img = Image.open(__import__("io").BytesIO(base64.b64decode(result.data[0].b64_json))).convert("RGB")
            img.save(out, quality=92)
            return img
        except Exception as e:  # noqa: BLE001  一時的なエラーは待って再試行
            last_err = e
            print(f"{out.name} の生成に失敗（{attempt + 1}回目）: {e}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
        finally:
            for f in files:
                f.close()
    raise RuntimeError(f"{out.name} を生成できませんでした: {last_err}")


# ---------------------------------------------------------------- テスト用

def mock_script() -> Script:
    return Script(
        title="秋の開店じゅんび",
        theme="古民家のお店の日常", location="古民家のお店の店内", location_en="",
        panels=[
            Panel(scene_ja="朝、お店の前を掃除する", scene_en="", lines=[Line(speaker="たぬき", text="今日もいい天気！")]),
            Panel(scene_ja="落ち葉が舞い込む", scene_en="", lines=[Line(speaker="たぬき", text="落ち葉がいっぱい…"), Line(speaker="猫", text="秋だねえ")]),
            Panel(scene_ja="右手で招くと、落ち葉がさらに集まる", scene_en="", lines=[Line(speaker="猫", text="えいっ、招きパワー！")]),
            Panel(scene_ja="落ち葉の山の上で満足げ", scene_en="", lines=[Line(speaker="たぬき", text="福じゃなくて葉っぱが来た！")]),
        ],
        moral="（テスト）", hook_line1="福を招いたつもりが、", hook_line2="集まったのは…\n落ち葉!?",
        moral_short="秋も、福もいっぱい", closing_line="小さな秋に、小さな福を。",
        title_card_scene_en="", note_header_scene_en="",
        x_post="（テスト）X本ポスト", x_reply="（テスト）Xリプライ",
        instagram_reel_caption="（テスト）リール", instagram_post_caption="（テスト）カルーセル",
        facebook_post="（テスト）Facebook", story_text="新作4コマ公開！",
        tiktok_caption="（テスト）TikTok", tiktok_pinned_comment="（テスト）固定コメント",
        youtube_title="（テスト）福合わせたぬき猫4コマ第13話 #shorts", youtube_description="（テスト）",
        youtube_tags=["福合わせたぬき猫"], youtube_pinned_comment="（テスト）",
        note_title="（テスト）note", note_body="（テスト）〔①コマ目の画像〕", extra_hashtags=["#秋"],
    )


def mock_images(out: Path) -> tuple[list[Image.Image], Image.Image, Image.Image]:
    """AIの代わりに第10話の絵を使う（料金ゼロの動作テスト用）。"""
    ref = ROOT / "sns/character/reference/第10話"
    panels = [Image.open(ref / f"百万両_1コマずつ_{i}.png").convert("RGB") for i in range(1, 5)]
    for i, p in enumerate(panels):
        p.save(out / f"panel{i + 1}.jpg", quality=92)
    return panels, Image.open(ref / "タイトルカード_縦.png").convert("RGB"), Image.open(ref / "note見出し_横.png").convert("RGB")


# ---------------------------------------------------------------- 本体

def next_post_date(cfg: dict, history: list[dict], today: dt.date) -> dt.date:
    """投稿日＝「開始日」「今日＋lead_days」「予定済みの最後の日＋interval_days」のうち一番遅い日。"""
    start = dt.date.fromisoformat(str(cfg.get("start_date", today)))
    lead = today + dt.timedelta(days=int(cfg.get("lead_days", 1)))
    step = dt.timedelta(days=int(cfg.get("interval_days", 1)))
    booked = [dt.date.fromisoformat(h.get("post_date", h["date"])) for h in history]
    return max([start, lead] + [d + step for d in booked])


def next_episode(cfg: dict, history: list[dict]) -> int:
    done = [h["episode"] for h in history if h.get("episode")]
    return max([cfg.get("episode_start", 1) - 1, *done]) + 1


BAR = "━━━━━━━━━━━━━━━━━━━━"


def write_post_files(out: Path, sc: Script, ep: int, cfg: dict) -> None:
    """第10話と同じ形式の投稿文ファイルを書き出す。"""
    t = sc.title
    tags = cfg["hashtags"]
    extra = [h for h in sc.extra_hashtags if h not in tags["youtube"]][:2]
    links = "\n".join(cfg.get("reply_links", []))
    files = {
        f"X投稿文_{t}.txt": f"""{BAR}
X投稿文｜{t} 第{ep}話（4コマ動画）
※二段構え：本ポスト＝動画＋フックのみ（リンク無し）／リプライ＝ショップリンク
※動画＝reel_x.mp4（無音でOK）
{BAR}

【本ポスト】（動画を添付・リンクは入れない）
{sc.x_post}

{' '.join(tags['x'])}


【リプライ】（本ポストに自分でぶら下げる・ここにリンク）
{sc.x_reply}
{links}
""",
        f"note記事_4コマ漫画_第{ep}話_{t}.txt": f"""【タイトル】
{sc.note_title}

※見出し画像：note_header.jpg
※画像の入れ方：〔 〕の位置に、その画像を1枚ずつ挿入してください（carousel_1〜4.jpg）。

【本文】

{sc.note_body}
""",
        f"youtube投稿文_{t}.txt": f"""{BAR}
YouTube Shorts 投稿文｜{t} 第{ep}話
※縦型 → 自動でShorts扱い
{BAR}

【タイトル】
{sc.youtube_title}

【説明（概要欄）】
{sc.youtube_description}

{' '.join(tags['youtube'][:-1] + extra + tags['youtube'][-1:])}

【タグ（カンマ区切り／タグ欄に入れる場合）】
{', '.join(sc.youtube_tags)}

【固定コメント】※公開後に投稿し、ピン留め（固定）する
{sc.youtube_pinned_comment}

【設定メモ】
- 動画：reel_youtube.mp4（BGM入り）
- AI開示（改変/合成コンテンツ）：はい（AIイラストのため）
- 子ども向けではない → 「いいえ、子ども向けではありません」
- 再生リスト「福合わせたぬき猫 4コマ漫画」に追加
""",
        f"TikTok投稿文_{t}.txt": f"""{BAR}
TikTok投稿文｜第{ep}話「{t}」（4コマ動画）
※動画：reel_tiktok.mp4（無音・BGMはアプリで人気曲を付ける）
※AI生成コンテンツのラベル：ON（AIイラスト）
※キャプションのリンクは押せないので「プロフィールのリンクから」へ誘導
{BAR}

【キャプション】
{sc.tiktok_caption}

{' '.join(tags['tiktok'])}


【固定コメント】※投稿後に自分でコメントし、ピン留め（固定）する
{sc.tiktok_pinned_comment}
""",
        f"インスタリール投稿文_{t}.txt": f"""{BAR}
インスタリール投稿文｜第{ep}話「{t}」（たぬき猫本人視点）
※動画：reel_instagram.mp4（BGM入り）
※AIラベル：ON（AIイラスト）
※リールはキャプション途中省略。1行目にフックを置く
※🛍️ 商品タグ：「{cfg.get('instagram_product_tag', '')}」を付ける（投稿画面の「商品をタグ付け」から）
※Facebookにもシェア：ON（Facebook用の文章は「Facebook投稿文」を使う場合はOFFにして別投稿）
{BAR}

{sc.instagram_reel_caption}

{' '.join(tags['instagram'])}
""",
        f"インスタ投稿文_{t}.txt": f"""{BAR}
インスタ投稿文｜第{ep}話「{t}」（4コマ・カルーセル／たぬき猫本人視点）
※カルーセル順：表紙（carousel_0_cover.jpg）→①②③④（carousel_1〜4.jpg）
※AIラベル：ON（AIイラスト）
※🛍️ 商品タグ：「{cfg.get('instagram_product_tag', '')}」を表紙（1枚目）と④枚目に付ける
{BAR}

{sc.instagram_post_caption}

{' '.join(tags['instagram'])}
""",
    }
    files[f"Facebook投稿文_{t}.txt"] = f"""{BAR}
Facebook投稿文｜第{ep}話「{t}」（4コマ動画）
※動画：reel_instagram.mp4（インスタのリールと同じ動画でOK）
※インスタから「Facebookにもシェア」で同時投稿する場合は、この文章に差し替えるか、そのままでもOK
※AIラベル：ON（AIイラスト）
{BAR}

{sc.facebook_post}
▼オンラインショップ
{cfg.get('shop_url', '')}

{' '.join(tags.get('facebook', []))}
"""
    files[f"ストーリーズ_{t}.txt"] = f"""{BAR}
インスタ ストーリーズ｜第{ep}話「{t}」
※画像：story.jpg（縦長。下の空いているところにスタンプを置く）
{BAR}

【手順】
1. リール投稿のあと、リール画面の「紙飛行機」→「ストーリーズに追加」でリールをシェアする
   （または story.jpg を使って新しいストーリーズを作る）
2. スタンプを付ける
   - 🛍️ 商品スタンプ：「{cfg.get('instagram_product_tag', '')}」
   - 🔗 リンクスタンプ：{cfg.get('shop_url', '')}（表示名：「ショップを見る」）
3. 保存しておきたい回は、ハイライト「4コマ漫画」に追加

【画像に入っている一言】
{sc.story_text}
"""
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="APIを使わずに動作テスト")
    ap.add_argument("--theme", default=os.environ.get("THEME") or None)
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))
    today = dt.datetime.now(JST).date()
    history = load_history()

    if not args.mock and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY が設定されていません（GitHub の Secrets に登録してください）")

    redo_issue = os.environ.get("REDO_ISSUE")
    mode = os.environ.get("REDO_MODE", "full")  # full＝お話から / images＝絵だけ
    prev = None
    if redo_issue:
        redo = fetch_redo(redo_issue)
        cfg["_feedback"] = redo["feedback"]
        if redo["dir"] and (ROOT / redo["dir"] / "post.json").exists():
            prev = json.loads((ROOT / redo["dir"] / "post.json").read_text(encoding="utf-8"))
            history = [h for h in history if h.get("dir") != redo["dir"]]  # 前回分は履歴から外す
        print(f"作り直し（{'絵だけ' if mode == 'images' else 'お話から'}）指示：{redo['feedback'] or 'なし'}")

    if prev:
        post_date = dt.date.fromisoformat(prev.get("post_date", prev["date"]))
    else:
        post_date = next_post_date(cfg, history, today)
        target = today + dt.timedelta(days=int(cfg.get("lead_days", 1)))
        # 毎朝の自動実行：明日の分がもう予定済みなら作らない（作りすぎ防止）
        if os.environ.get("EVENT_NAME") == "schedule" and post_date > max(target, dt.date.fromisoformat(str(cfg.get("start_date", target)))):
            print(f"{target} の分はもう予定済みです（次に空いているのは {post_date}）。今回は作りません。")
            return
    print(f"投稿日：{post_date}")

    if prev and mode == "images":
        print("① 台本は前回のまま使います")
        episode = prev["episode"]
        script = Script.model_validate(prev)
    else:
        print("① 台本を作成中…")
        episode = prev["episode"] if prev else next_episode(cfg, history)
        if prev:
            cfg["_redo_section"] = (
                "# 作り直し（最優先）\n前回の案は採用されませんでした。前回とは違うお話にしてください。\n"
                f"前回の案：\n{previous_summary(prev)}\n"
                f"ご主人からの修正指示：{cfg.get('_feedback') or '（特になし）'}\n"
            )
        script = mock_script() if args.mock else make_script(cfg, post_date, args.theme, history, episode)
    print(f"   第{episode}話：{script.title}")

    out = new_output_dir(today)
    if args.mock:
        panels, title_card, note_art = mock_images(out)
    else:
        sizes = cfg["openai"]["sizes"]
        # 「絵だけ作り直し」でコメントに「3コマ目」などとあれば、その絵だけ描き直して残りは前回のものを使う
        only = redo_targets(cfg.get("_feedback", "")) if (prev and mode == "images") else None
        keep = (lambda name: only is not None and name not in only)
        if keep("title"):
            title_card = reuse_image(prev, "title_card.jpg", out / "title_card_raw.jpg")
        else:
            print("② タイトルカードを生成中…")
            title_card = gen_image(cfg, title_card_prompt(cfg, script, episode), sizes["title_card"], out / "title_card_raw.jpg")
        panels = []
        for i, p in enumerate(script.panels):
            if keep(i + 1):
                print(f"② コマ{i + 1}は前回の絵を使います")
                panels.append(reuse_image(prev, f"panel{i + 1}.jpg", out / f"panel{i + 1}.jpg"))
                continue
            print(f"② コマ{i + 1}の絵を生成中…")
            # 2コマ目以降は1コマ目も見本にして、背景とキャラをそろえる
            extra = [out / "panel1.jpg"] if i > 0 else []
            panels.append(gen_image(cfg, panel_prompt(cfg, script, p, i), sizes["panel"], out / f"panel{i + 1}.jpg", extra))
        if keep("note"):
            note_art = reuse_image(prev, "note_header.jpg", out / "note_header_raw.jpg")
        else:
            print("② note見出しを生成中…")
            note_art = gen_image(cfg, note_header_prompt(cfg, script, episode), sizes["note_header"], out / "note_header_raw.jpg")

    print("③ 吹き出し・4コマ画像・動画を作成中…")
    data = script.model_dump()
    data["episode"] = episode
    render.render_all(panels, title_card, note_art, data, cfg, out)

    data.update(
        date=today.isoformat(),
        post_date=post_date.isoformat(),
        dir=out.relative_to(ROOT).as_posix(),
        release_tag=f"ep{episode}-{out.name}",
        media=sorted(p.name for p in out.iterdir() if p.suffix in (".jpg", ".mp4") and "raw" not in p.name),
        hashtags=cfg["hashtags"],
        product_tag=cfg.get("instagram_product_tag", ""),
        schedule=cfg.get("schedule", []),
        status="承認待ち",
        mock=args.mock,
    )
    (out / "post.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_post_files(out, script, episode, cfg)

    if not args.mock:
        history.append({"episode": episode, "date": today.isoformat(), "post_date": post_date.isoformat(), "title": script.title, "theme": script.theme,
                        "location": script.location, "dir": data["dir"]})
        HISTORY.write_text(json.dumps(history[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    prune_old(cfg.get("keep_days", 30), today)

    print(f"④ 完了：{data['dir']}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"dir={data['dir']}\ntag={data['release_tag']}\nepisode={episode}\ntitle={script.title}\n")


if __name__ == "__main__":
    main()
