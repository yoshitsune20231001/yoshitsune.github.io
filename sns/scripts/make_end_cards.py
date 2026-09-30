"""リール最後の「見てくれてありがとう」エンドカードを、SNSごとにAIで作り直す（最初に1回だけ実行）。

  python sns/scripts/make_end_cards.py              # 全SNS
  python sns/scripts/make_end_cards.py youtube x    # 指定したSNSだけ
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import generate  # noqa: E402
import render  # noqa: E402

ROOT = generate.ROOT

DESIGNS = {
    "instagram": (
        "Bright pink-to-orange gradient background with sparkles and small hearts.",
        "「リール動画 見てくれてありがとう！」",
        "「いいね♥＆フォロー よろしくね！」",
    ),
    "youtube": (
        "Clean white and bright red background with sparkles, a big red rounded button shape behind the bottom text.",
        "「最後まで見てくれてありがとう！」",
        "「高評価👍＆チャンネル登録 よろしくね！」",
    ),
    "x": (
        "Stylish black background with white and gold sparkles, simple and modern.",
        "「最後まで見てくれてありがとう！」",
        "「いいね♥・リポスト・フォロー よろしくね！」",
    ),
}


def prompt(cfg: dict, name: str) -> str:
    bg, top, bottom = DESIGNS[name]
    return (
        "Vertical 9:16 end card for a short video (thank-you screen).\n"
        f"Art style: {cfg['art_style_en']}\n"
        f"Characters (keep designs IDENTICAL to the attached character sheets): {cfg['character']['appearance_en']}\n"
        "Both characters jump happily with arms wide open and wave at the viewer, full of energy, "
        "big smiles, motion lines. The cat is clearly a BOY (no eyelashes, no blush makeup, no ribbons, "
        "no feminine features), same chubby ceramic look as the tanuki.\n"
        f"Background: {bg}\n"
        f"Big bold Japanese text at the top reading exactly {top}, and at the bottom a rounded banner "
        f"reading exactly {bottom}. Use simple generic icons only (heart, thumbs up, person-plus). "
        "Do NOT draw any official app logos. Keep text inside the central 85% width. No other text."
    )


def main() -> None:
    cfg = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))
    cfg["character"]["reference_images"] = [p for p in cfg["character"]["reference_images"] if "キャラクターシート" in p]
    names = sys.argv[1:] or list(DESIGNS)
    for name in names:
        print(f"{name} のエンドカードを生成中…")
        out = ROOT / cfg["reel"]["platforms"][name]
        raw = out.with_name(out.stem + "_raw.jpg")
        img = generate.gen_image(cfg, prompt(cfg, name), cfg["openai"]["sizes"]["title_card"], raw)
        render.fit_cover(img, render.W, render.H).save(out, quality=92)
        raw.unlink()
        print(f"  → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
