"""
Wikimedia Commons 画像取得クライアント。

無認証・課金ゼロ。検索→imageinfo→ライセンス選別→DL→帰属生成。
- ライセンス判定は extmetadata の構造フィールド(LicenseShortName)のみで行い、
  PD/CC0/CC-BY のみ通す（CC-BY-SA/ND/NC/GFDL/不明は除外＝商用可・帰属のみ許容）。
- Artist は HTML 混在のためサニタイズして「帰属表示」にだけ使い、判定には使わない
  （apod_crop の教訓「構造のみ採用、proseは信頼しない」）。
- 取得画像はリサイズ/変換せず元バイナリのまま保存（pillow不要）。

純関数（pick_license/build_attribution/build_*_url/_ext_from_url）はテスト可能。
ネットワークI/Oは urllib（依存追加なし）。
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia API は User-Agent 必須（無いと 403）。連絡先を含める礼儀。
UA = "zundamon-video/0.1 (educational; https://github.com/yokohamaak/zundamon-video)"

# 弾くライセンス語（正規化後に含まれたら除外）。SA/ND/NC は商用や改変で制約、GFDL は帰属が重い。
_DENIED = ("sa", "nd", "nc", "gfdl")
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_TAG = re.compile(r"<[^>]+>")


def _norm_license(short):
    """ライセンス短縮名を比較用に正規化（小文字・ハイフン→空白・連続空白圧縮）。"""
    return re.sub(r"\s+", " ", (short or "").strip().lower()).replace("-", " ")


def pick_license(extmetadata):
    """extmetadata からライセンスを判定する純関数。

    商用可・帰属のみ(PD/CC0/CC-BY)を通す。SA/ND/NC/GFDL/不明は弾く（構造値のみで判定）。
    Returns: (ok: bool, short_name: str)
    """
    short = ((extmetadata or {}).get("LicenseShortName") or {}).get("value", "") or ""
    n = _norm_license(short)
    if not n:
        return False, short
    if any(d in n for d in _DENIED):
        return False, short
    if "cc0" in n or "public domain" in n or n == "pd":
        return True, short
    if "cc by" in n:  # _DENIED は上で除外済みなので CC BY (-SA等でない) のみ残る
        return True, short
    return False, short


def build_attribution(extmetadata, title=""):
    """帰属クレジット文字列を作る純関数。

    Artist はHTMLタグ除去・空白圧縮し、空ならファイル名→"Wikimedia Commons" にフォールバック。
    末尾にライセンス短縮名を付す（例: "John Doe / CC BY 4.0"）。
    """
    em = extmetadata or {}
    artist = _TAG.sub("", (em.get("Artist") or {}).get("value", "") or "").strip()
    artist = re.sub(r"\s+", " ", artist)
    if not artist:
        artist = re.sub(r"^File:", "", title).strip() or "Wikimedia Commons"
    short = ((em.get("LicenseShortName") or {}).get("value", "") or "").strip()
    return f"{artist} / {short}" if short else artist


def build_search_url(query, limit=10):
    qs = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srnamespace": 6, "srlimit": limit, "format": "json",
    })
    return f"{API}?{qs}"


def build_imageinfo_url(title):
    qs = urllib.parse.urlencode({
        "action": "query", "titles": title,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "format": "json",
    })
    return f"{API}?{qs}"


def _ext_from_url(url):
    """画像URLの拡張子を返す（想定外は .jpg）。保存名の拡張子決めに使う。"""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext if ext in _IMG_EXTS else ".jpg"


# ラスタ画像のみ採用（FileネームスペースにはPDF/SVG/動画も含まれ、無関係ファイルを拾う事故を防ぐ）。
_RASTER_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _is_raster_url(url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext in _RASTER_EXTS


# 検索語に付きがちな汎用語。これが最長語になると判定が骨抜きになる
# （"PNG logo"→"logo"で全ロゴ一致）ため、固有名の核を選ぶ際は除外する。
_GENERIC_WORDS = {
    "logo", "image", "photo", "picture", "icon", "symbol", "illustration",
    "diagram", "format", "system", "file", "screenshot", "wordmark", "sign",
}


def _title_matches(query, title):
    """検索結果タイトルがクエリに関連するか（純関数）。

    Wikimedia全文検索は関連度が低く1位が被写体と限らない（"BitKeeper"→"The Keeper of
    the Bees"等）。クエリの固有名の核（汎用語を除いた最長の3字以上の語）がFile名に含まれる
    ものだけ採用し、無関係画像の誤採用を防ぐ。該当語が無い短いクエリは判定せず通す。

    - 略語(PNG/GIF/Siri等3字)も判定対象にする。
    - title の拡張子は除いて比較する（"png"/"gif" が全PNG/GIFファイルの拡張子に一致して
      素通りする事故を防ぐ）。
    - "logo"/"format" 等の汎用語は核から除外（残らなければ全語にフォールバック）。
    """
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3]
    key_words = [w for w in words if w not in _GENERIC_WORDS]
    if not key_words:
        return True  # 固有名の核が無い（汎用語のみ/短い）クエリは判定せず通す
    stem = os.path.splitext(title)[0].lower()  # 拡張子(.png/.gif)を除く
    return max(key_words, key=len) in stem


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


def search(query, limit=10, timeout=30):
    """Commons をファイル検索し File:タイトル のリストを返す。"""
    data = _get_json(build_search_url(query, limit), timeout)
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


def imageinfo(title, timeout=30):
    """ファイルの imageinfo(url + extmetadata) を返す。無ければ None。"""
    data = _get_json(build_imageinfo_url(title), timeout)
    for _, page in data.get("query", {}).get("pages", {}).items():
        ii = page.get("imageinfo")
        if ii:
            return ii[0]
    return None


def fetch_one(query, out_dir, base_name, timeout=30, max_candidates=10):
    """query で検索し、ライセンスを通る最初の画像を out_dir/base_name.<ext> に保存する。

    Returns: (filename, attribution) 成功時 / (None, None) 該当なし・失敗時。
    """
    try:
        titles = search(query, max_candidates, timeout)
    except Exception as e:  # noqa: BLE001 - 検索失敗はスキップ（呼び出し側でプレースホルダ）
        logger.warning(f"Wikimedia検索失敗 '{query}': {e}")
        return None, None
    for title in titles:
        if not _title_matches(query, title):
            continue  # クエリの固有名を含まない＝無関係画像。誤採用を防ぐ。
        try:
            ii = imageinfo(title, timeout)
            if not ii or not ii.get("url"):
                continue
            if not _is_raster_url(ii["url"]):
                # PDF/SVG/動画など非ラスタは表示できない/無関係ファイルの事故源なので除外。
                logger.info(f"非ラスタ除外 '{title}': {ii['url'].rsplit('.', 1)[-1]}")
                continue
            ok, short = pick_license(ii.get("extmetadata", {}))
            if not ok:
                logger.info(f"ライセンス除外 '{title}': {short or '不明'}")
                continue
            filename = base_name + _ext_from_url(ii["url"])
            _download(ii["url"], os.path.join(out_dir, filename), timeout)
            attribution = build_attribution(ii.get("extmetadata", {}), title)
            logger.info(f"Wikimedia取得 '{query}' → {filename}（{attribution}）")
            return filename, attribution
        except Exception as e:  # noqa: BLE001 - 個別失敗は次候補へ
            logger.warning(f"Wikimedia取得失敗 '{title}': {e}")
            continue
    logger.info(f"Wikimedia該当なし（ライセンス適合画像が見つからず）: '{query}'")
    return None, None
