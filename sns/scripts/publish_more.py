"""承認済みの4コマを X・YouTubeショート・TikTok へ自動投稿する（インスタ・Facebookは publish_meta.py）。

確認Issueの「📮 投稿チェック」で、時刻が来ていて未チェックのものだけ投稿する。
合言葉（Secrets）が登録されていないSNSは何もしない（今まで通り手動）。

- X：本ポスト（動画＋フック）→ リプライ（ショップ・noteのリンク）
- YouTube：Shortsとしてアップロード（Googleの審査が通るまでは「非公開」になるので、アプリで公開する）
- TikTok：アプリの受信箱（下書き）に送る。BGMを付けて投稿するのはアプリで

環境変数:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET（＋ sns/state/tiktok.enc に暗号化したリフレッシュトークン）
  GITHUB_TOKEN, GITHUB_REPOSITORY, MODE(post|check|tiktok_code), ISSUE(任意), CODE(tiktok_code のとき)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_meta import JST, LINE, ROOT, STALE_HOURS, download, gh  # noqa: E402

TIKTOK_STATE = ROOT / "sns/state/tiktok.enc"
TIKTOK_REDIRECT = "https://yoshitsune20231001.github.io/yoshitsune.github.io/sns/oauth/tiktok/"


def env(*names: str) -> list[str] | None:
    vals = [os.environ.get(n, "").strip() for n in names]
    return vals if all(vals) else None


def secrets() -> list[str]:
    return [v for k, v in os.environ.items() if k.startswith(("X_", "YOUTUBE_", "TIKTOK_")) and len(v) > 8]


def hide(msg: str) -> str:
    for s in secrets():
        msg = msg.replace(s, "***")
    return msg


# ================================================================ X
def x_session():
    from requests_oauthlib import OAuth1Session
    k = env("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    return OAuth1Session(k[0], client_secret=k[1], resource_owner_key=k[2], resource_owner_secret=k[3]) if k else None


def x_check(r: requests.Response, what: str) -> dict:
    if r.status_code >= 400:
        raise RuntimeError(f"X {what} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def x_upload_video(s, body: bytes) -> str:
    base = "https://api.x.com/2/media/upload"
    init = x_check(s.post(f"{base}/initialize", json={
        "media_type": "video/mp4", "total_bytes": len(body), "media_category": "tweet_video"}), "動画の準備")
    mid = init["data"]["id"]
    chunk = 4 * 1024 * 1024
    for i in range(0, len(body), chunk):
        x_check(s.post(f"{base}/{mid}/append", data={"segment_index": i // chunk},
                       files={"media": ("reel.mp4", body[i:i + chunk], "application/octet-stream")}), "動画の送信")
    info = x_check(s.post(f"{base}/{mid}/finalize"), "動画の仕上げ").get("data", {}).get("processing_info")
    end = time.time() + 600
    while info and info.get("state") in ("pending", "in_progress") and time.time() < end:
        time.sleep(max(3, int(info.get("check_after_secs", 5))))
        info = x_check(s.get(base, params={"command": "STATUS", "media_id": mid}), "動画の確認").get("data", {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise RuntimeError(f"X 動画の処理に失敗: {info}")
    return mid


def post_x(post, repo, tag, cfg):
    s = x_session()
    mid = x_upload_video(s, download(repo, tag, "reel_x.mp4"))
    tags = " ".join(post.get("hashtags", {}).get("x", []))
    text = f"{post['x_post'].strip()}\n\n{tags}".strip()
    main = x_check(s.post("https://api.x.com/2/tweets", json={"text": text, "media": {"media_ids": [mid]}}), "本ポスト")["data"]["id"]
    reply = "\n".join([post["x_reply"].strip(), *cfg.get("reply_links", [])])
    note = ""
    try:
        x_check(s.post("https://api.x.com/2/tweets", json={
            "text": reply, "reply": {"in_reply_to_tweet_id": main}}), "リプライ")
    except RuntimeError as e:
        note = f"⚠️ リプライ（リンク）は失敗しました。手動でぶら下げてください（{e}）"
    me = x_check(s.get("https://api.x.com/2/users/me"), "アカウント確認")["data"]["username"]
    return f"https://x.com/{me}/status/{main}", note


def check_x():
    s = x_session()
    if not s:
        return "- X：未設定（手動）"
    me = x_check(s.get("https://api.x.com/2/users/me"), "アカウント確認")["data"]
    return f"- X：✅ @{me['username']}"


# ================================================================ YouTube
def yt_token() -> str | None:
    k = env("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
    if not k:
        return None
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": k[0], "client_secret": k[1], "refresh_token": k[2], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube の合言葉が使えません（作り直しが必要かも）: {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


def post_youtube(post, repo, tag, cfg):
    tok = yt_token()
    tags = post.get("hashtags", {}).get("youtube", [])
    extra = [h for h in post.get("extra_hashtags", []) if h not in tags][:2]
    desc = f"{post['youtube_description'].strip()}\n\n{' '.join(tags[:-1] + extra + tags[-1:])}"
    meta = {
        "snippet": {"title": post["youtube_title"][:100], "description": desc[:4900],
                    "tags": post.get("youtube_tags", [])[:15], "categoryId": "24",
                    "defaultLanguage": "ja", "defaultAudioLanguage": "ja"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    body = download(repo, tag, "reel_youtube.mp4")
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos",
                      params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(body))},
                      data=json.dumps(meta), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube 準備に失敗: {r.status_code} {r.text[:300]}")
    up = requests.put(r.headers["Location"], data=body, timeout=900,
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    if up.status_code >= 400:
        raise RuntimeError(f"YouTube アップロードに失敗: {up.status_code} {up.text[:300]}")
    v = up.json()
    status = v.get("status", {}).get("privacyStatus", "")
    note = "" if status == "public" else (
        "⚠️ YouTubeの審査が通るまでは **非公開** で上がります。YouTube Studio アプリで「公開」に変えてください"
        "（固定コメント・再生リストもアプリで）")
    return f"https://youtube.com/shorts/{v['id']}", note


def check_youtube():
    tok = yt_token()
    if not tok:
        return "- YouTube：未設定（手動）"
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code == 403:  # youtube.upload だけの権限ではチャンネル名は読めない
        return "- YouTube：✅ 合言葉OK（アップロード権限）"
    items = r.json().get("items", [])
    return f"- YouTube：✅ {items[0]['snippet']['title']}" if items else "- YouTube：✅ 合言葉OK"


# ================================================================ TikTok
def tiktok_key() -> bytes | None:
    k = env("TIKTOK_CLIENT_SECRET")
    if not k:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(("tiktok-state:" + k[0]).encode()).digest())


def tiktok_save(refresh: str) -> None:
    from cryptography.fernet import Fernet
    TIKTOK_STATE.parent.mkdir(parents=True, exist_ok=True)
    TIKTOK_STATE.write_bytes(Fernet(tiktok_key()).encrypt(refresh.encode()))
    for cmd in (["git", "config", "user.name", "sns-bot"],
                ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
                ["git", "add", str(TIKTOK_STATE)], ["git", "commit", "-m", "TikTok の合言葉を更新（暗号化済み）"]):
        subprocess.run(cmd, cwd=ROOT, check=False)
    for i in range(4):
        if subprocess.run(["git", "pull", "--rebase", "origin", os.environ.get("GITHUB_REF_NAME", "main")], cwd=ROOT).returncode == 0 and \
           subprocess.run(["git", "push", "origin", f"HEAD:{os.environ.get('GITHUB_REF_NAME', 'main')}"], cwd=ROOT).returncode == 0:
            return
        time.sleep(5 * (i + 1))


def tiktok_token(code: str = "") -> str | None:
    k = env("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")
    if not k:
        return None
    if code:
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": TIKTOK_REDIRECT}
    else:
        if not TIKTOK_STATE.exists():
            return None
        from cryptography.fernet import Fernet
        data = {"grant_type": "refresh_token", "refresh_token": Fernet(tiktok_key()).decrypt(TIKTOK_STATE.read_bytes()).decode()}
    r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", timeout=30,
                      data={"client_key": k[0], "client_secret": k[1], **data},
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    j = r.json()
    if r.status_code >= 400 or "access_token" not in j:
        raise RuntimeError(f"TikTok の合言葉が使えません（つなぎ直しが必要かも）: {j.get('error_description') or j.get('error') or r.text[:200]}")
    old = data.get("refresh_token")
    if j.get("refresh_token") and j["refresh_token"] != old:
        tiktok_save(j["refresh_token"])
    return j["access_token"]


def post_tiktok(post, repo, tag, cfg):
    tok = tiktok_token()
    body = download(repo, tag, "reel_tiktok.mp4")
    r = requests.post("https://open.tiktokapis.com/v2/post/publish/inbox/video/init/", timeout=60,
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"},
                      json={"source_info": {"source": "FILE_UPLOAD", "video_size": len(body),
                                            "chunk_size": len(body), "total_chunk_count": 1}})
    j = r.json()
    if r.status_code >= 400 or j.get("error", {}).get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok 準備に失敗: {j.get('error', r.text[:300])}")
    up = requests.put(j["data"]["upload_url"], data=body, timeout=900, headers={
        "Content-Type": "video/mp4", "Content-Length": str(len(body)),
        "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}"})
    if up.status_code >= 400:
        raise RuntimeError(f"TikTok アップロードに失敗: {up.status_code} {up.text[:200]}")
    tags = " ".join(post.get("hashtags", {}).get("tiktok", []))
    return "", ("📥 TikTokアプリの **受信箱（お知らせ）** に動画を送りました。アプリで開いて、BGMを付けて投稿してください。\n"
                "投稿したら上のチェックを入れてください。\n\n**キャプション（コピー用）**\n```\n"
                f"{post['tiktok_caption'].strip()}\n\n{tags}\n```\n**固定コメント**\n```\n{post.get('tiktok_pinned_comment', '').strip()}\n```")


def check_tiktok():
    if not env("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        return "- TikTok：未設定（手動）"
    if not TIKTOK_STATE.exists():
        return "- TikTok：⚠️ アプリの登録はOK。あとは「TikTokとつなぐ」が必要です"
    tok = tiktok_token()
    r = requests.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "display_name"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()
    return f"- TikTok：✅ {r.get('data', {}).get('user', {}).get('display_name', 'つながっています')}"


# ================================================================ 本体
POSTERS = {  # SNS名: (投稿する関数, 使えるか, 投稿後にチェックを入れるか)
    "X": (post_x, lambda: bool(env("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")), True),
    "YouTubeショート": (post_youtube, lambda: bool(env("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")), True),
    "TikTok": (post_tiktok, lambda: bool(env("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")) and TIKTOK_STATE.exists(), False),
}


def write_summary(lines: list[str]) -> None:
    text = hide("\n".join(lines))
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### X・YouTube・TikTok 自動投稿\n" + text + "\n")


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    mode = os.environ.get("MODE", "post")
    cfg = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))

    if mode == "tiktok_code":
        code = os.environ.get("CODE", "").strip()
        if not code:
            print("コードが空です")
            return 1
        tiktok_token(code)
        write_summary(["- TikTok：✅ つながりました（合言葉は暗号化して保存しました）"])
        return 0

    if mode == "check":
        lines = []
        for f in (check_x, check_youtube, check_tiktok):
            try:
                lines.append(f())
            except Exception as e:  # noqa: BLE001
                lines.append(f"- ❌ {e}")
        write_summary(lines)
        return 0

    now = dt.datetime.now(JST)
    only = os.environ.get("ISSUE", "").strip()
    issues = [gh("GET", f"/repos/{repo}/issues/{only}")] if only else gh(
        "GET", f"/repos/{repo}/issues", params={"state": "open", "labels": "4コマ,承認", "per_page": 30})
    summary, failed = [], False
    for it in issues:
        if it.get("state") != "open" or "承認" not in {l["name"] for l in it.get("labels", [])}:
            continue
        body = it["body"] or ""
        mdir = re.search(r"<!-- dir:(.+?) -->", body)
        mtag = re.search(r"<!-- tag:(.+?) -->", body)
        if not (mdir and mtag):
            continue
        post = json.loads((ROOT / mdir.group(1).strip() / "post.json").read_text(encoding="utf-8"))
        tag = mtag.group(1).strip()
        for m in LINE.finditer(body):
            done, sns, when = m.group(1) == "x", m.group(2).strip(), m.group(3)
            if done or sns not in POSTERS:
                continue
            fn, ready, tick = POSTERS[sns]
            sent_mark = f"<!-- sent:{sns}:{when} -->"
            if not ready() or sent_mark in body:
                continue
            at = dt.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            if at > now or (not only and now - at > dt.timedelta(hours=STALE_HOURS)):
                continue
            print(f"#{it['number']} {sns}（{when}）を投稿します")
            try:
                link, note = fn(post, repo, tag, cfg)
            except Exception as e:  # noqa: BLE001
                failed = True
                msg = hide(str(e))
                print(f"  失敗: {msg}")
                gh("POST", f"/repos/{repo}/issues/{it['number']}/comments", json={
                    "body": f"❌ **{sns}** の自動投稿に失敗しました（{now:%H:%M}）\n```\n{msg}\n```\n"
                            "少しあとにもう一度自動で試します。だめなら手動で投稿して、チェックを入れてください。\n<!-- bot -->"})
                continue
            cur = gh("GET", f"/repos/{repo}/issues/{it['number']}")["body"]
            if tick:
                cur = cur.replace(f"- [ ] {sns} ｜ {when}", f"- [x] {sns} ｜ {when}", 1)
            else:
                cur = cur.rstrip() + f"\n{sent_mark}\n"
            gh("PATCH", f"/repos/{repo}/issues/{it['number']}", json={"body": cur})
            body = cur
            head = f"✅ **{sns}** に自動投稿しました" if tick else f"📤 **{sns}** に送りました"
            gh("POST", f"/repos/{repo}/issues/{it['number']}/comments", json={
                "body": f"{head}（{dt.datetime.now(JST):%m/%d %H:%M}）\n{link}\n{note}\n<!-- bot -->"})
            summary.append(f"- ✅ #{it['number']} {sns}: {link}")

    write_summary(summary or ["- 今回投稿するものはありませんでした"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
