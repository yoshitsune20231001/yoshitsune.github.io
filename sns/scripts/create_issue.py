"""生成した4コマを「確認用」の GitHub Issue にする（スマホのGitHubアプリで確認できる）。

環境変数: GITHUB_TOKEN, GITHUB_REPOSITORY, OUT_DIR, COMMIT_SHA, REDO_ISSUE(任意)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.github.com"
LABELS = {
    "承認待ち": ("fbca04", "確認してください"),
    "承認": ("0e8a16", "OK。投稿してよい"),
    "作り直し": ("d93f0b", "付けると新しい4コマを作り直します"),
    "4コマ": ("c9a96e", "福合わせたぬき猫 4コマ"),
}


def gh(method: str, path: str, **kw):
    r = requests.request(method, f"{API}{path}", headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }, timeout=30, **kw)
    if r.status_code >= 400 and not (method == "POST" and path.endswith("/labels") and r.status_code == 422):
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else {}


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["COMMIT_SHA"]
    out = os.environ["OUT_DIR"]
    post = json.loads((ROOT / out / "post.json").read_text(encoding="utf-8"))

    for name, (color, desc) in LABELS.items():
        gh("POST", f"/repos/{repo}/labels", json={"name": name, "color": color, "description": desc})

    raw = f"https://raw.githubusercontent.com/{repo}/{sha}/{out}"
    blob = f"https://github.com/{repo}/blob/{sha}/{out}"
    rows = []
    for k, p in zip("起承転結", post["panels"]):
        lines = " / ".join("{}「{}」".format(ln["speaker"], ln["text"]) for ln in p["lines"])
        rows.append(f"| {k} | {p['scene_ja']} | {lines} |")
    story = "\n".join(rows)
    tags = " ".join(post["hashtags_all"])
    body = f"""## 今日の4コマ「{post['title']}」

![4コマ]({raw}/manga.jpg)

🎬 **リール動画**：[再生する（GitHub）]({blob}/reel.mp4)　／　[公開URL]({post['public_urls']['reel.mp4']})（反映まで数分かかります）

| | 場面 | 台詞 |
|---|---|---|
{story}

---
### 📷 Instagram リール
```
{post['caption_instagram']}

{tags}
```

### ▶️ YouTube ショート
**タイトル**：{post['youtube_title']} #Shorts

### 🧵 Threads
```
{post['caption_threads']}

{tags}
```

### 𝕏
```
{post['caption_x']}
{' '.join(post['hashtags_all'][:3])}
```

### 📝 note（下書き）
[note.md を開く]({blob}/note.md)

---
### ✅ 確認のしかた
- **OK** → 右の「Labels」から **承認** を付ける
- **作り直し** → **作り直し** ラベルを付ける（数分で新しい4コマの Issue が届きます）
- 台詞や投稿文を少し直したいときは、このIssueにコメントで書いてください

> ※ いまは「生成＋確認」まで。SNSへの自動投稿は、品質が安定してから追加します。
"""
    issue = gh("POST", f"/repos/{repo}/issues", json={
        "title": f"【4コマ確認】{post['date']}「{post['title']}」",
        "body": body,
        "labels": ["4コマ", "承認待ち"],
    })
    print(f"Issue を作成しました: {issue['html_url']}")

    redo = os.environ.get("REDO_ISSUE")
    if redo:
        gh("POST", f"/repos/{repo}/issues/{redo}/comments",
           json={"body": f"作り直しました → #{issue['number']}"})
        gh("PATCH", f"/repos/{repo}/issues/{redo}", json={"state": "closed", "state_reason": "not_planned"})


if __name__ == "__main__":
    main()
