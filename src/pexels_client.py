"""
Pexels 画像取得クライアント。

無料枠・APIキー(.env: PEXELS_API_KEY)。ambient（抽象・雰囲気）画像向け。
帰属は不要だが、任意で出典（撮影者 / Pexels）を返して画面/概要に出せる。
取得画像はリサイズ/変換せず元バイナリのまま保存（pillow不要）。

純関数（build_search_url/pick_photo/_image_url/_ext_from_url）はテスト可能。
ネットワークI/Oは urllib（依存追加なし）。
"""
import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SEARCH = "https://api.pexels.com/v1/search"
UA = "zundamon-video/0.1 (educational; https://github.com/yokohamaak/zundamon-video)"
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def build_search_url(query, per_page=10, locale=None):
    """locale 指定時は Pexels がクエリをその言語で解釈して検索する（例 "ja-JP"＝日本語で検索）。"""
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    if locale:
        params["locale"] = locale
    return f"{SEARCH}?{urllib.parse.urlencode(params)}"


def pick_photo(photos):
    """検索結果（関連度順）から先頭を選ぶ純関数。orientation=landscapeで横長を要求済み。"""
    return photos[0] if photos else None


def _image_url(photo):
    """表示に十分な大きさのURLを選ぶ（large2x→large→original）。"""
    src = (photo or {}).get("src", {})
    return src.get("large2x") or src.get("large") or src.get("original")


def _ext_from_url(url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext if ext in _IMG_EXTS else ".jpg"


def _get_json(url, api_key, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url, out_path, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(out_path, "wb") as f:
        f.write(data)


def search(query, api_key, per_page=10, timeout=30, locale=None):
    return _get_json(build_search_url(query, per_page, locale), api_key, timeout).get("photos", [])


def candidates(query, api_key, per_page=12, timeout=30, locale=None):
    """検索結果を候補リストで返す（DLしない・サムネ表示用）。1検索=複数件で追加課金なし。

    Returns: [{"source","thumb","url","attribution"}]。キー無し/失敗時は []。
    """
    if not api_key:
        return []
    try:
        photos = search(query, api_key, per_page, timeout, locale)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pexels候補検索失敗 '{query}': {e}")
        return []
    out = []
    for p in photos:
        url = _image_url(p)
        if not url:
            continue
        src = p.get("src", {})
        thumb = src.get("medium") or src.get("small") or url
        ph = (p.get("photographer") or "").strip()
        out.append({"source": "pexels", "thumb": thumb, "url": url,
                    "attribution": f"{ph} / Pexels" if ph else "Pexels"})
    return out


def fetch_one(query, out_dir, base_name, api_key, timeout=30, per_page=10, locale=None):
    """query で検索し先頭画像を out_dir/base_name.<ext> に保存する。

    locale 指定時はその言語でクエリを解釈して検索（例 "ja-JP"）。
    Returns: (filename, attribution) 成功時 / (None, None) キー無し・該当なし・失敗時。
    """
    if not api_key:
        return None, None
    try:
        photos = search(query, api_key, per_page, timeout, locale)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pexels検索失敗 '{query}': {e}")
        return None, None
    photo = pick_photo(photos)
    url = _image_url(photo)
    if not url:
        logger.info(f"Pexels該当なし: '{query}'")
        return None, None
    try:
        filename = base_name + _ext_from_url(url)
        _download(url, os.path.join(out_dir, filename), timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Pexels取得失敗 '{query}': {e}")
        return None, None
    photographer = (photo.get("photographer") or "").strip()
    attribution = f"{photographer} / Pexels" if photographer else "Pexels"
    logger.info(f"Pexels取得 '{query}' → {filename}（{attribution}）")
    return filename, attribution
