"""
apod_client の単体テスト（NASA APIへの通信なし・_http_getをモック）。

実行: python3 test_apod_client.py
正規化・hdurl優先・動画日のthumbnail扱い・クレジット整形・APODページURL・
画像保存・APIキー解決 を検証する。
"""
import json
import os
import tempfile

from src import apod_client as ac


def _install_json(payload):
    """fetch_apod用: _http_get がAPOD JSONを返すようにする。"""
    ac._http_get = lambda url, timeout=30: json.dumps(payload).encode("utf-8")


def test_image_day_prefers_hdurl():
    _install_json({
        "date": "2026-06-06",
        "title": "Andromeda Galaxy",
        "explanation": "A spiral galaxy.",
        "media_type": "image",
        "url": "https://apod.nasa.gov/img_small.jpg",
        "hdurl": "https://apod.nasa.gov/img_hd.jpg",
        "copyright": "\n Jane Astronomer \n",
    })
    r = ac.fetch_apod(api_key="X", date="2026-06-06")
    assert r["image_url"] == "https://apod.nasa.gov/img_hd.jpg", "hdurl優先"
    assert r["title"] == "Andromeda Galaxy"
    assert r["media_type"] == "image"
    assert r["copyright"] == "Jane Astronomer", "改行/余白を整形"
    assert r["source_url"] == "https://apod.nasa.gov/apod/ap260606.html"
    print("  image-day: hdurl優先・クレジット整形・ページURL OK")


def test_image_day_no_hdurl_falls_back_to_url():
    _install_json({
        "date": "2026-01-02",
        "title": "T",
        "explanation": "E",
        "media_type": "image",
        "url": "https://apod.nasa.gov/only_url.jpg",
    })
    r = ac.fetch_apod()
    assert r["image_url"] == "https://apod.nasa.gov/only_url.jpg", "hdurl無しはurlにフォールバック"
    assert r["copyright"] is None, "copyright無しはNone"
    print("  no-hdurl: urlフォールバック・copyright None OK")


def test_video_day_uses_thumbnail():
    _install_json({
        "date": "2026-03-03",
        "title": "A Rocket Launch",
        "explanation": "E",
        "media_type": "video",
        "url": "https://www.youtube.com/embed/xxxx",
        "thumbnail_url": "https://img.youtube.com/vi/xxxx/hqdefault.jpg",
    })
    r = ac.fetch_apod()
    assert r["media_type"] == "video"
    assert r["image_url"] == "https://img.youtube.com/vi/xxxx/hqdefault.jpg", "動画日はthumbnail"
    print("  video-day: thumbnail採用 OK")


def test_download_image():
    payload = b"\x89PNG\r\n\x1a\n" + b"fakeimagebytes"
    ac._http_get = lambda url, timeout=30: payload
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "sub", "apod.jpg")
        out = ac.download_image("https://example/x.jpg", dest)
        assert out == dest
        assert os.path.exists(dest)
        with open(dest, "rb") as f:
            assert f.read() == payload, "画像バイトがそのまま保存される"
    print("  download: 画像保存（ディレクトリ自動作成）OK")


def test_download_image_empty_url_raises():
    try:
        ac.download_image(None, "/tmp/x.jpg")
    except ValueError:
        print("  download: 空URLでValueError OK")
        return
    raise AssertionError("空URLでValueErrorが出ていない")


def test_api_key_resolution():
    os.environ.pop("NASA_API_KEY", None)
    assert ac.resolve_api_key() == "DEMO_KEY", "未設定はDEMO_KEY"
    assert ac.resolve_api_key("MYKEY") == "MYKEY", "明示指定が最優先"
    os.environ["NASA_API_KEY"] = "ENVKEY"
    try:
        assert ac.resolve_api_key() == "ENVKEY", "環境変数を使用"
    finally:
        os.environ.pop("NASA_API_KEY", None)
    print("  api-key: 明示>環境変数>DEMO_KEY OK")


if __name__ == "__main__":
    print("test_apod_client:")
    test_image_day_prefers_hdurl()
    test_image_day_no_hdurl_falls_back_to_url()
    test_video_day_uses_thumbnail()
    test_download_image()
    test_download_image_empty_url_raises()
    test_api_key_resolution()
    print("ALL PASS")
