"""ダッシュボードの「超人チーム」6人の絵をAIで作る（最初に1回だけ実行）。

  python sns/scripts/make_crew.py                 # 全員
  python sns/scripts/make_crew.py commander x     # 指定した役割だけ
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import generate  # noqa: E402

ROOT = generate.ROOT
OUT = ROOT / "sns/assets/crew"


def prompt(cfg: dict, m: dict) -> str:
    who = "the TANUKI" if m["animal"] == "TANUKI" else "the NEKO (boy cat)"
    return (
        "Square collectible sticker-card illustration of ONE character.\n"
        f"Art style: {cfg['art_style_en']} Bold, glossy trading-sticker look with a thick border.\n"
        f"Character: {who} from the attached character sheets (keep face, body shape, red rope necklace "
        "with bell, 百万両 koban and colors recognizable), dressed up as an ORIGINAL masked pro-wrestling "
        f"hero: {m['look_en']}.\n"
        f"At the top, a ribbon banner reading exactly 「{m['name']}」 in bold Japanese letters.\n"
        "Background: a red-brown pattern of small gold koban coins and 福 characters.\n"
        "IMPORTANT: this is an original design. Do not depict or imitate any existing anime or manga "
        "character, no letters or kanji on the forehead, no military or political insignia, no official "
        "app logos. The cat is a boy: no eyelashes, no ribbons, no makeup. No other text."
    )


def main() -> None:
    cfg = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))
    cfg["character"]["reference_images"] = [p for p in cfg["character"]["reference_images"] if "キャラクターシート" in p]
    OUT.mkdir(parents=True, exist_ok=True)
    # ダッシュボードが読む名簿
    (OUT / "crew.json").write_text(json.dumps(
        [{k: m[k] for k in ("role", "label", "name")} for m in cfg["crew"]], ensure_ascii=False, indent=1),
        encoding="utf-8")
    roles = sys.argv[1:]
    for m in cfg["crew"]:
        if roles and m["role"] not in roles:
            continue
        print(f"{m['label']}（{m['name']}）を生成中…")
        generate.gen_image(cfg, prompt(cfg, m), "1024x1024", OUT / f"{m['role']}.jpg")


if __name__ == "__main__":
    main()
