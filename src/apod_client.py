"""
NASA APOD（Astronomy Picture of the Day）取得モジュール

if動画のソース。APOD APIから当日（または指定日）の画像・タイトル・解説を取得し、
中央ビジュアル用に画像をローカルへ保存する。

- 無料API。APIキーは環境変数 NASA_API_KEY で管理（未設定時はDEMO_KEY=レート制限ありで警告）。
- media_type が "video" の場合は thumbnail_url（thumbs=true）を画像として扱う。
- 画像にはクレジット（copyright）が付く場合があるので必ず保持し、動画側で表示する。

標準ライブラリのみ（urllib/json）。ネットワーク部（_http_get）はテストで差し替え可能。
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

APOD_ENDPOINT = "https://api.nasa.gov/planetary/apod"
DEMO_KEY = "DEMO_KEY"


def _http_get(url, timeout=30):
    """URLからバイト列を取得（リトライ付き）。テストで差し替え可能。"""
    def _once():
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()

    last = None
    for i in range(1, 4):
        try:
            return _once()
        except Exception as e:  # noqa: BLE001 - ネットワーク全般をリトライ
            last = e
            if i == 3:
                break
            wait = 2.0 * i
            logger.warning(f"GET失敗（試行{i}/3）、{wait:.0f}秒後に再試行: {e}")
            time.sleep(wait)
    raise last


def resolve_api_key(api_key=None):
    """明示指定 > 環境変数 NASA_API_KEY > DEMO_KEY(警告)。"""
    key = api_key or os.environ.get("NASA_API_KEY")
    if not key:
        logger.warning("NASA_API_KEY未設定のためDEMO_KEYを使用します（レート制限あり・本番は無料キー取得推奨）")
        return DEMO_KEY
    return key


def fetch_apod(api_key=None, date=None, prefer_hd=True):
    """
    APODを取得し正規化dictを返す。

    Args:
        api_key: APIキー（未指定なら環境変数→DEMO_KEY）
        date: "YYYY-MM-DD"（未指定なら当日）
        prefer_hd: 画像はhdurl優先（無ければurl）

    Returns: {
        "date", "title", "explanation", "media_type",
        "image_url",     # 画像URL（videoならthumbnail_url。無ければNone）
        "copyright",     # クレジット（無ければNone）
        "source_url",    # APODページURL（無ければNone）
    }
    """
    key = resolve_api_key(api_key)
    params = {"api_key": key, "thumbs": "true"}
    if date:
        params["date"] = date
    url = f"{APOD_ENDPOINT}?{urllib.parse.urlencode(params)}"

    raw = _http_get(url)
    data = json.loads(raw)

    media_type = data.get("media_type", "image")
    if media_type == "image":
        image_url = (data.get("hdurl") if prefer_hd else None) or data.get("url")
    else:
        # 動画の日: サムネイルを画像として使う（無ければNone）
        image_url = data.get("thumbnail_url") or None

    return {
        "date": data.get("date"),
        "title": data.get("title"),
        "explanation": data.get("explanation"),
        "media_type": media_type,
        "image_url": image_url,
        "copyright": _clean_copyright(data.get("copyright")),
        "source_url": _apod_page_url(data.get("date")),
    }


def _clean_copyright(value):
    if not value:
        return None
    # APODのcopyrightは改行・余分な空白を含むことがある
    return " ".join(value.split()).strip() or None


def _apod_page_url(date):
    """APODの該当ページURL（apYYMMDD.html）。dateが無ければNone。"""
    if not date or len(date) != 10:
        return None
    try:
        yy = date[2:4]
        mm = date[5:7]
        dd = date[8:10]
    except Exception:  # noqa: BLE001
        return None
    return f"https://apod.nasa.gov/apod/ap{yy}{mm}{dd}.html"


def download_image(image_url, dest_path):
    """画像URLをダウンロードして dest_path に保存する。保存パスを返す。"""
    if not image_url:
        raise ValueError("image_urlが空です（media_type=videoでthumbnail無し等）")
    data = _http_get(image_url)
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)
    logger.info(f"APOD画像保存: {dest_path}（{len(data)}バイト）")
    return dest_path
