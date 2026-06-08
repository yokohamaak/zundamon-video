"""
NASA images（images.nasa.gov）からの無料画像取得モジュール

事実パートの実写補助に使う。images.nasa.gov は無料・APIキー不要・パブリックドメイン
（CLAUDE.md非抵触＝従量課金/情報漏洩なし。送る情報は検索語の英単語のみ）。

設計（apod_crop.py に倣う）:
- build_search_url / build_asset_url / parse_search_items / pick_image_url は純関数でテスト可能。
- ネットワークI/O（urllib）とPillow縮小は fetch_stock_images 内に閉じる。

API:
- 検索:  https://images-api.nasa.gov/search?q=<query>&media_type=image
- アセット: https://images-api.nasa.gov/asset/<nasa_id>  → 各サイズのURL一覧
"""
import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = "https://images-api.nasa.gov/search"
ASSET_ENDPOINT = "https://images-api.nasa.gov/asset"
USER_AGENT = "digest-to-video/1.0 (personal, non-commercial)"
TIMEOUT = 20            # 1リクエストのタイムアウト秒
MAX_ATTEMPTS = 3        # リトライ回数
DEFAULT_MAX_SIDE = 1920  # 書き出し長辺の上限（crop と揃える）

# 高解像度を優先して選ぶサフィックス順（jpgのみ。origはtif等があるため後段）。
_SIZE_PREFERENCE = ("~large.jpg", "~medium.jpg", "~orig.jpg")


def build_search_url(query, media_type="image"):
    """検索URLを組み立てる（純関数）。"""
    qs = urllib.parse.urlencode({"q": query, "media_type": media_type})
    return f"{SEARCH_ENDPOINT}?{qs}"


def build_asset_url(nasa_id):
    """アセット（各サイズURL一覧）取得URLを組み立てる（純関数）。"""
    return f"{ASSET_ENDPOINT}/{urllib.parse.quote(nasa_id)}"


def parse_search_items(json_text):
    """検索レスポンスJSONから画像itemを取り出す（純関数）。

    Returns: [{nasa_id, title, thumb}]（media_type=image のもののみ・有効nasa_idのみ）。
    """
    data = json.loads(json_text)
    items = (data.get("collection") or {}).get("items") or []
    out = []
    for it in items:
        meta = (it.get("data") or [{}])[0]
        if meta.get("media_type") != "image":
            continue
        nasa_id = meta.get("nasa_id")
        if not nasa_id:
            continue
        links = it.get("links") or []
        thumb = next((l.get("href") for l in links if l.get("href")), None)
        out.append({"nasa_id": nasa_id, "title": meta.get("title") or "", "thumb": thumb})
    return out


def pick_image_url(asset_json_text, thumb_fallback=None):
    """アセットJSONから最適な画像URLを選ぶ（純関数）。

    large→medium→orig(jpg)→任意のjpg→thumb の順で選ぶ。見つからなければ None。
    """
    try:
        data = json.loads(asset_json_text)
    except (ValueError, TypeError):
        return thumb_fallback
    hrefs = [
        it.get("href") for it in ((data.get("collection") or {}).get("items") or [])
        if it.get("href")
    ]
    for suffix in _SIZE_PREFERENCE:
        for h in hrefs:
            if h.lower().endswith(suffix):
                return h
    jpgs = [h for h in hrefs if h.lower().endswith((".jpg", ".jpeg"))]
    if jpgs:
        return jpgs[0]
    return thumb_fallback


def _http_get(url, binary=False):
    """GETしてbytes/strを返す。タイムアウト＋リトライ付き（失敗は最後に例外送出）。"""
    import time

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                return body if binary else body.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < MAX_ATTEMPTS:
                wait = 2 * attempt
                logger.warning(f"取得失敗（試行{attempt}/{MAX_ATTEMPTS}）{wait}s後再試行: {url} : {e}")
                time.sleep(wait)
    raise last


def _save_image(image_bytes, out_path, max_side=DEFAULT_MAX_SIDE):
    """ダウンロード画像を RGB・長辺max_side以内へ正規化して JPG 保存（Pillow遅延import）。"""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    img.save(out_path, quality=88)
    return img.size


def fetch_stock_images(queries, out_dir, per_query=1, max_total=4, prefix="stock"):
    """検索語ごとに images.nasa.gov から画像を取得し out_dir 直下へ保存する。

    1検索語につき上位 per_query 件。全体で max_total 件まで。失敗した語はスキップ
    （パイプライン全体は止めない）。古い <prefix>_*.jpg は事前に掃除する。

    Returns: [{file, query, title, nasa_id, source}]（保存できたものだけ・順序維持）。
    """
    # Pillow が無いと全件失敗する＝環境エラー。1件ずつ試さず即中断（無駄なNASAリクエストを避ける）。
    try:
        import PIL  # noqa: F401
    except ImportError:
        logger.warning("Pillow未導入のためstock取得をスキップします（pip install pillow）。")
        return []

    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith(f"{prefix}_") and f.lower().endswith((".jpg", ".jpeg", ".png")):
            os.remove(os.path.join(out_dir, f))

    # 1検索語あたり試す候補数の上限（持続的失敗で検索結果を全件舐めるのを防ぐ）。
    max_candidates = max(per_query * 3, 5)
    results = []
    idx = 0
    for query in queries:
        if len(results) >= max_total:
            break
        try:
            items = parse_search_items(_http_get(build_search_url(query)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"stock検索失敗（スキップ）'{query}': {e}")
            continue
        if not items:
            logger.info(f"stock該当なし: '{query}'")
            continue
        taken = 0
        for it in items[:max_candidates]:
            if taken >= per_query or len(results) >= max_total:
                break
            try:
                asset_json = _http_get(build_asset_url(it["nasa_id"]))
                url = pick_image_url(asset_json, it.get("thumb"))
                if not url:
                    continue
                idx += 1
                fname = f"{prefix}_{idx:02d}.jpg"
                size = _save_image(_http_get(url, binary=True), os.path.join(out_dir, fname))
                logger.info(f"stock#{idx} '{query}' {size} <- {it['nasa_id']} -> {fname}")
                results.append({
                    "file": fname,
                    "query": query,
                    "title": it.get("title"),
                    "nasa_id": it["nasa_id"],
                    "source": "images.nasa.gov",
                })
                taken += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"stock取得失敗（スキップ）{it.get('nasa_id')}: {e}")
                continue
    return results
