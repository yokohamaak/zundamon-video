"""
Pixabay 画像取得クライアント。

無料枠・APIキー(.env: PIXABAY_API_KEY)。ambient（抽象・雰囲気）画像向け＝Pexelsのフォールバック。
帰属は不要だが、任意で出典（投稿者 / Pixabay）を返せる。
取得画像はリサイズ/変換せず元バイナリのまま保存（pillow不要）。

純関数（build_search_url/pick_hit/_image_url/_ext_from_url）はテスト可能。
ネットワークI/Oは urllib（依存追加なし）。
"""
import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API = "https://pixabay.com/api/"
UA = "zundamon-video/0.1 (educational; https://github.com/yokohamaak/zundamon-video)"
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def build_search_url(query, key, per_page=10):
    qs = urllib.parse.urlencode({
        "key": key, "q": query, "image_type": "photo",
        "orientation": "horizontal", "safesearch": "true", "per_page": per_page,
    })
    return f"{API}?{qs}"


def pick_hit(hits):
    """検索結果（関連度順）から先頭を選ぶ純関数。"""
    return hits[0] if hits else None


def _image_url(hit):
    """表示に十分な大きさのURLを選ぶ（largeImageURL→webformatURL）。"""
    h = hit or {}
    return h.get("largeImageURL") or h.get("webformatURL")


def _ext_from_url(url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext if ext in _IMG_EXTS else ".jpg"


def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, out_path, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(out_path, "wb") as f:
        f.write(data)


def search(query, key, per_page=10, timeout=30):
    return _get_json(build_search_url(query, key, per_page), timeout).get("hits", [])


def candidates(query, api_key, per_page=12, timeout=30):
    """検索結果を候補リストで返す（DLしない・サムネ表示用）。1検索=複数件で追加課金なし。

    Returns: [{"source","thumb","url","attribution"}]。キー無し/失敗時は []。
    """
    if not api_key:
        return []
    try:
        hits = search(query, api_key, per_page, timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pixabay候補検索失敗 '{query}': {e}")
        return []
    out = []
    for h in hits:
        url = _image_url(h)
        if not url:
            continue
        thumb = h.get("webformatURL") or h.get("previewURL") or url
        user = (h.get("user") or "").strip()
        out.append({"source": "pixabay", "thumb": thumb, "url": url,
                    "attribution": f"{user} / Pixabay" if user else "Pixabay"})
    return out


def fetch_one(query, out_dir, base_name, api_key, timeout=30, per_page=10):
    """query で検索し先頭画像を out_dir/base_name.<ext> に保存する。

    Returns: (filename, attribution) 成功時 / (None, None) キー無し・該当なし・失敗時。
    """
    if not api_key:
        return None, None
    try:
        hits = search(query, api_key, per_page, timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pixabay検索失敗 '{query}': {e}")
        return None, None
    hit = pick_hit(hits)
    url = _image_url(hit)
    if not url:
        logger.info(f"Pixabay該当なし: '{query}'")
        return None, None
    try:
        filename = base_name + _ext_from_url(url)
        _download(url, os.path.join(out_dir, filename), timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pixabay取得失敗 '{query}': {e}")
        return None, None
    user = (hit.get("user") or "").strip()
    attribution = f"{user} / Pixabay" if user else "Pixabay"
    logger.info(f"Pixabay取得 '{query}' → {filename}（{attribution}）")
    return filename, attribution
