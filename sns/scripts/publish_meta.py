"""承認済みの4コマを、インスタ（リール／カルーセル）・Facebook・ストーリーズへ自動投稿する。

確認Issueの「📮 投稿チェック」で、時刻が来ていて未チェックのものだけ投稿し、
投稿できたらチェックを入れてリンクをコメントする（手動で投稿した分はチェックを入れておけば飛ばす）。

環境変数: META_ACCESS_TOKEN, GITHUB_TOKEN, GITHUB_REPOSITORY, MODE(post|check), ISSUE(任意: 番号指定)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
GH = "https://api.github.com"
FB = "https://graph.facebook.com/v23.0"
FB_VIDEO = "https://graph-video.facebook.com/v23.0"
JST = dt.timezone(dt.timedelta(hours=9))
LINE = re.compile(r"^- \[( |x)\] (.+?) ｜ (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$", re.M)
STALE_HOURS = 12  # これより古い予定は投稿しない（遅れて変な時間に出ないように）
TOKEN_WARN_DAYS = 7


# ---------------------------------------------------------------- GitHub
def gh(method: str, path: str, **kw):
    r = requests.request(method, f"{GH}{path}", headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }, timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


# ---------------------------------------------------------------- Meta
class MetaError(RuntimeError):
    pass


def meta(method: str, path: str, token: str, base: str = FB, **kw):
    params = kw.pop("params", {}) or {}
    params["access_token"] = token
    r = requests.request(method, f"{base}/{path.lstrip('/')}", params=params, timeout=kw.pop("timeout", 120), **kw)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", data)
        msg = err.get("error_user_msg") or err.get("message") or str(err)
        raise MetaError(f"{method} {path.split('?')[0]} -> {r.status_code}: {msg} (code {err.get('code')}/{err.get('error_subcode')})")
    return data


class Accounts:
    """トークンから Facebookページ・インスタ・商品 を見つける。"""

    def __init__(self, token: str, product_name: str, product_id: str = ""):
        self.user_token = token
        pages = meta("GET", "me/accounts", token, params={
            "fields": "id,name,access_token,instagram_business_account{id,username}"})["data"]
        if not pages:
            raise MetaError("Facebookページが見つかりません（sns-bot にページが割り当てられているか確認）")
        page = next((p for p in pages if p.get("instagram_business_account")), pages[0])
        self.page_id, self.page_name = page["id"], page["name"]
        self.page_token = page.get("access_token") or token
        ig = page.get("instagram_business_account") or {}
        self.ig_id, self.ig_name = ig.get("id"), ig.get("username")
        self.product_id = None
        self.product_note = ""
        self.candidates: list[str] = []
        if self.ig_id and product_name:
            try:
                self.product_id, self.product_note = self._find_product(product_name, str(product_id or ""))
            except MetaError as e:
                self.product_note = f"商品が見つかりませんでした（{e}）"

    def _find_product(self, name: str, want_id: str):
        """商品を探す。config の instagram_product_id があればそれ、なければ名前がいちばん短いもの（＝本体）。"""
        found = []
        for c in meta("GET", f"{self.ig_id}/available_catalogs", self.user_token)["data"]:
            res = meta("GET", f"{self.ig_id}/catalog_product_search", self.user_token,
                       params={"catalog_id": c["catalog_id"], "q": name})["data"]
            found += [p for p in res if p.get("review_status", "approved") in ("approved", "")]
        self.candidates = [f"`{p['product_id']}` {p.get('product_name', '')}" for p in found]
        if want_id:
            p = next((p for p in found if str(p["product_id"]) == want_id), None)
            if not p:
                return None, f"instagram_product_id {want_id} が見つかりませんでした"
        elif found:
            p = min(found, key=lambda p: len(p.get("product_name", "")))
        else:
            return None, "商品が見つかりませんでした"
        return p["product_id"], p.get("product_name", name)


def release_url(repo: str, tag: str, name: str) -> str:
    """Releases のファイルの、Meta が直接読める URL（リダイレクト先・30分有効）を返す。"""
    url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    r = requests.head(url, allow_redirects=False, timeout=30)
    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
        return r.headers["Location"]
    if r.status_code == 200:
        return url
    raise RuntimeError(f"{name} が Releases に見つかりません（{r.status_code}）")


def fb_hosted_url(acc, repo: str, tag: str, name: str) -> str:
    """画像を Facebookページに非公開でアップして、その画像URLを使う（URL方式がダメなときの予備）。"""
    pid = meta("POST", f"{acc.page_id}/photos", acc.page_token, data={"published": "false"},
               files={"source": (name, download(repo, tag, name), "image/jpeg")})["id"]
    imgs = meta("GET", pid, acc.page_token, params={"fields": "images"})["images"]
    return max(imgs, key=lambda i: i.get("width", 0))["source"]


def download(repo: str, tag: str, name: str) -> bytes:
    r = requests.get(f"https://github.com/{repo}/releases/download/{tag}/{name}", timeout=300)
    r.raise_for_status()
    return r.content


def wait_container(acc: Accounts, cid: str, minutes: int = 10) -> None:
    end = time.time() + minutes * 60
    while time.time() < end:
        st = meta("GET", cid, acc.user_token, params={"fields": "status_code,status"})
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise MetaError(f"インスタ側の処理に失敗: {st.get('status', code)}")
        time.sleep(10)
    raise MetaError("インスタ側の処理が10分で終わりませんでした")


def ig_publish(acc: Accounts, cid: str) -> str:
    wait_container(acc, cid)
    mid = meta("POST", f"{acc.ig_id}/media_publish", acc.user_token, data={"creation_id": cid})["id"]
    try:
        return meta("GET", mid, acc.user_token, params={"fields": "permalink"}).get("permalink", "")
    except MetaError:
        return f"(media id {mid})"


def ig_video_container(acc: Accounts, repo: str, tag: str, file: str, fields: dict) -> str:
    """動画のコンテナを作る。URLで渡してダメなら、ファイルを直接アップロードする。"""
    try:
        cid = meta("POST", f"{acc.ig_id}/media", acc.user_token,
                   data={**fields, "video_url": release_url(repo, tag, file)})["id"]
        wait_container(acc, cid)
        return cid
    except MetaError as e:
        print(f"  URL方式に失敗 → 直接アップロードで再挑戦: {e}")
    cid = meta("POST", f"{acc.ig_id}/media", acc.user_token, data={**fields, "upload_type": "resumable"})["id"]
    body = download(repo, tag, file)
    r = requests.post(f"https://rupload.facebook.com/ig-api-upload/v23.0/{cid}", data=body, timeout=600, headers={
        "Authorization": f"OAuth {acc.user_token}", "offset": "0", "file_size": str(len(body))})
    if r.status_code >= 400:
        raise MetaError(f"動画アップロードに失敗: {r.status_code} {r.text[:200]}")
    return cid


def caption(post: dict, key: str, tags_key: str, extra: str = "") -> str:
    tags = " ".join(post.get("hashtags", {}).get(tags_key, []))
    return "\n\n".join(x for x in (post[key].strip(), extra.strip(), tags) if x)


# ---------------------------------------------------------------- 各SNS
def post_reel(acc, post, repo, tag, cfg):
    fields = {"media_type": "REELS", "share_to_feed": "true",
              "caption": caption(post, "instagram_reel_caption", "instagram")}
    note = ""
    if acc.product_id:
        try:
            cid = ig_video_container(acc, repo, tag, "reel_instagram.mp4",
                                     {**fields, "product_tags": json.dumps([{"product_id": acc.product_id}])})
            return ig_publish(acc, cid), "🛍️ 商品タグ付き"
        except MetaError as e:
            note = f"⚠️ リールへの商品タグはAPIで付けられなかったので、タグなしで投稿しました（{e}）"
    cid = ig_video_container(acc, repo, tag, "reel_instagram.mp4", fields)
    return ig_publish(acc, cid), note


def post_story(acc, post, repo, tag, cfg):
    try:
        cid = ig_video_container(acc, repo, tag, "reel_instagram.mp4", {"media_type": "STORIES"})
    except MetaError as e:
        print(f"  動画ストーリーズに失敗 → 画像で投稿: {e}")
        cid = meta("POST", f"{acc.ig_id}/media", acc.user_token, data={
            "media_type": "STORIES", "image_url": fb_hosted_url(acc, repo, tag, "story.jpg")})["id"]
    return ig_publish(acc, cid), "※ストーリーズの商品スタンプ・リンクスタンプはAPIでは付けられません。必要ならアプリで追加してください"


def post_carousel(acc, post, repo, tag, cfg):
    files = ["carousel_0_cover.jpg", "carousel_1.jpg", "carousel_2.jpg", "carousel_3.jpg", "carousel_4.jpg"]
    tagged = {0, 4}

    def children(with_tags: bool):
        ids = []
        for i, f in enumerate(files):
            data = {"is_carousel_item": "true"}
            if with_tags and i in tagged:
                data["product_tags"] = json.dumps([{"product_id": acc.product_id, "x": 0.5, "y": 0.85}])
            try:
                cid = meta("POST", f"{acc.ig_id}/media", acc.user_token,
                           data={**data, "image_url": release_url(repo, tag, f)})["id"]
            except MetaError as e:
                if "product" in str(e).lower() or "tag" in str(e).lower():
                    raise
                print(f"  {f}: URL方式に失敗 → Facebook経由で再挑戦: {e}")
                cid = meta("POST", f"{acc.ig_id}/media", acc.user_token,
                           data={**data, "image_url": fb_hosted_url(acc, repo, tag, f)})["id"]
            ids.append(cid)
        return ids

    note = ""
    try:
        ids = children(bool(acc.product_id))
        note = "🛍️ 表紙と④に商品タグ付き" if acc.product_id else ""
    except MetaError as e:
        if not acc.product_id:
            raise
        note = f"⚠️ 商品タグが付けられなかったので、タグなしで投稿しました（{e}）"
        ids = children(False)
    cid = meta("POST", f"{acc.ig_id}/media", acc.user_token, data={
        "media_type": "CAROUSEL", "children": ",".join(ids),
        "caption": caption(post, "instagram_post_caption", "instagram")})["id"]
    return ig_publish(acc, cid), note


def post_facebook(acc, post, repo, tag, cfg):
    text = caption(post, "facebook_post", "facebook", f"▼オンラインショップ\n{cfg.get('shop_url', '')}")
    body = download(repo, tag, "reel_instagram.mp4")
    vid = meta("POST", f"{acc.page_id}/videos", acc.page_token, base=FB_VIDEO, timeout=600,
               data={"description": text, "published": "true"},
               files={"source": ("reel.mp4", body, "video/mp4")})["id"]
    return f"https://www.facebook.com/{acc.page_id}/videos/{vid}", ""


POSTERS = {
    "インスタ（リール）": post_reel,
    "Facebook": post_facebook,
    "ストーリーズ": post_story,
    "インスタ（カルーセル）": post_carousel,
}


# ---------------------------------------------------------------- 本体
def token_days_left(token: str):
    try:
        d = meta("GET", "debug_token", token, params={"input_token": token})["data"]
    except MetaError as e:
        print(f"トークンの期限を確認できませんでした: {e}")
        return None
    exp = d.get("expires_at") or 0
    if not exp:
        return None  # 無期限
    return (dt.datetime.fromtimestamp(exp, dt.timezone.utc) - dt.datetime.now(dt.timezone.utc)).days


def warn_token(repo: str, days: int) -> None:
    title = "⚠️ インスタ・Facebook自動投稿の合言葉（トークン）の期限が近づいています"
    for it in gh("GET", f"/repos/{repo}/issues", params={"state": "open", "per_page": 50}):
        if it["title"] == title:
            return
    gh("POST", f"/repos/{repo}/issues", json={"title": title, "body": f"""あと **{days}日** で、Metaのトークンが切れます。切れると自動投稿が止まります。

### 作り直し方（5分）
1. https://business.facebook.com/settings/system-users を開いて **sns-bot** を選ぶ
2. **「トークンを生成」** → アプリ **yoshitsune-sns** → 期限 **60日間** → 次の8つにチェック → **「トークンを生成」**
   `instagram_basic` `instagram_content_publish` `instagram_shopping_tag_products` `catalog_management`
   `pages_show_list` `pages_read_engagement` `pages_manage_posts` `business_management`
3. 出てきた文字をコピー（チャットには貼らない）
4. https://github.com/{repo}/settings/secrets/actions で **META_ACCESS_TOKEN** の ✏️ を押して貼り付け → **Update secret**
5. このIssueを閉じる
"""})


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    mode = os.environ.get("MODE", "post")
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not token:
        print("META_ACCESS_TOKEN が登録されていません")
        return 1
    cfg = yaml.safe_load((ROOT / "sns/config.yml").read_text(encoding="utf-8"))
    summary = []

    acc = Accounts(token, cfg.get("instagram_product_tag", ""), cfg.get("instagram_product_id", ""))
    days = token_days_left(token)
    summary += [
        f"- Facebookページ：**{acc.page_name}**",
        f"- インスタ：**@{acc.ig_name}**" if acc.ig_id else "- インスタ：⚠️ ページにつながったインスタが見つかりません",
        f"- 商品タグ：{acc.product_note or '（設定なし）'}",
        f"- トークンの残り：{'無期限' if days is None else f'{days}日'}",
    ]
    if days is not None and days <= TOKEN_WARN_DAYS:
        warn_token(repo, days)

    if mode == "check":
        summary += ["", "#### 見つかった商品（instagram_product_id に番号を書くと、その商品に固定できます）"] + [f"- {c}" for c in acc.candidates]
        write_summary(summary)
        return 0

    now = dt.datetime.now(JST)
    only = os.environ.get("ISSUE", "").strip()
    issues = [gh("GET", f"/repos/{repo}/issues/{only}")] if only else gh(
        "GET", f"/repos/{repo}/issues", params={"state": "open", "labels": "4コマ,承認", "per_page": 30})
    failed = False
    for it in issues:
        labels = {l["name"] for l in it.get("labels", [])}
        if it.get("state") != "open" or "承認" not in labels:
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
            fn = POSTERS.get(sns)
            if done or not fn:
                continue
            at = dt.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            if at > now or (not only and now - at > dt.timedelta(hours=STALE_HOURS)):
                continue
            if sns != "Facebook" and not acc.ig_id:
                continue
            print(f"#{it['number']} {sns}（{when}）を投稿します")
            try:
                link, note = fn(acc, post, repo, tag, cfg)
            except Exception as e:  # noqa: BLE001
                failed = True
                msg = str(e).replace(token, "***")
                print(f"  失敗: {msg}")
                gh("POST", f"/repos/{repo}/issues/{it['number']}/comments", json={
                    "body": f"❌ **{sns}** の自動投稿に失敗しました（{now:%H:%M}）\n```\n{msg}\n```\n"
                            "少しあとにもう一度自動で試します。だめなら手動で投稿して、チェックを入れてください。\n<!-- bot -->"})
                continue
            # チェックを入れる（最新の本文に対して）
            cur = gh("GET", f"/repos/{repo}/issues/{it['number']}")["body"]
            cur = cur.replace(f"- [ ] {sns} ｜ {when}", f"- [x] {sns} ｜ {when}", 1)
            gh("PATCH", f"/repos/{repo}/issues/{it['number']}", json={"body": cur})
            body = cur
            gh("POST", f"/repos/{repo}/issues/{it['number']}/comments", json={
                "body": f"✅ **{sns}** に自動投稿しました（{dt.datetime.now(JST):%m/%d %H:%M}）\n{link}\n{note}\n<!-- bot -->"})
            summary.append(f"- ✅ #{it['number']} {sns}: {link}")
            print(f"  投稿しました: {link}")

    write_summary(summary)
    return 1 if failed else 0


def write_summary(lines: list[str]) -> None:
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### インスタ・Facebook 自動投稿\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
