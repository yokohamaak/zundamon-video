"""
画像取得クライアントの単体テスト（ネットワーク/API/pillow不要・モンキーパッチで分岐検証）。

実行: python3 test_image_clients.py
Wikimedia: pick_license / build_attribution / build_*_url / _ext_from_url / fetch_one。
"""
import os
import tempfile

from src import wikimedia_client as w
from src import pexels_client as px
from src import pixabay_client as pb


# ---- 純関数 ----

def test_pick_license():
    def em(short):
        return {"LicenseShortName": {"value": short}}
    for ok_name in ["Public domain", "CC0", "CC BY 4.0", "CC BY 3.0", "CC-BY-2.0"]:
        ok, _ = w.pick_license(em(ok_name))
        assert ok, f"{ok_name} は通すべき"
    for ng_name in ["CC BY-SA 4.0", "CC BY-ND 4.0", "CC BY-NC 3.0", "GFDL", "", "All rights reserved"]:
        ok, _ = w.pick_license(em(ng_name))
        assert not ok, f"{ng_name} は弾くべき"
    # extmetadata欠落も弾く
    assert not w.pick_license({})[0] and not w.pick_license(None)[0]
    print("  pick_license: PD/CC0/CC-BY通し・SA/ND/NC/GFDL/不明弾き OK")


def test_build_attribution():
    em = {"LicenseShortName": {"value": "CC BY 4.0"},
          "Artist": {"value": '<a href="x">Jane Doe</a>'}}
    assert w.build_attribution(em, "File:X.jpg") == "Jane Doe / CC BY 4.0", "HTML除去+license付与"
    # Artist空 → ファイル名フォールバック
    em2 = {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": "  "}}
    assert w.build_attribution(em2, "File:Cool_pic.jpg") == "Cool_pic.jpg / CC0"
    # Artist空+title空 → Wikimedia Commons
    assert w.build_attribution({}, "") == "Wikimedia Commons"
    # license空 → artistのみ
    assert w.build_attribution({"Artist": {"value": "Bob"}}, "") == "Bob"
    print("  build_attribution: HTML除去/空フォールバック/license付与 OK")


def test_build_urls_and_ext():
    su = w.build_search_url("git logo", 5)
    assert "list=search" in su and "srnamespace=6" in su and "srlimit=5" in su
    assert "git+logo" in su or "git%20logo" in su, "queryがエンコードされる"
    iu = w.build_imageinfo_url("File:X.jpg")
    assert "prop=imageinfo" in iu and "extmetadata" in iu and "File" in iu
    assert w._ext_from_url("https://x/Foo.PNG") == ".png"
    assert w._ext_from_url("https://x/Foo.jpg?a=1") == ".jpg"
    assert w._ext_from_url("https://x/noext") == ".jpg", "想定外は.jpg"
    print("  build_*_url / _ext_from_url OK")


# ---- fetch_one（search/imageinfo/_download をモンキーパッチ） ----

def test_fetch_one_success():
    tmp = tempfile.mkdtemp()
    w.search = lambda q, n, t: ["File:Good.jpg"]
    w.imageinfo = lambda title, t: {
        "url": "https://upload.example/Good.jpg",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "<a>Jane</a>"}},
    }
    w._download = lambda url, path, t: open(path, "wb").write(b"img")
    fn, attr = w.fetch_one("git", tmp, "ch_00_00")
    assert fn == "ch_00_00.jpg", f"実拡張子で保存: {fn}"
    assert attr == "Jane / CC BY 4.0"
    assert os.path.exists(os.path.join(tmp, "ch_00_00.jpg"))
    print("  fetch_one: 成功(ライセンス適合→DL→帰属) OK")


def test_fetch_one_skips_bad_license():
    tmp = tempfile.mkdtemp()
    w.search = lambda q, n, t: ["File:Bad.png", "File:Good.jpg"]

    def fake_ii(title, t):
        if "Bad" in title:
            return {"url": "https://x/Bad.png", "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}
        return {"url": "https://x/Good.jpg", "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}

    w.imageinfo = fake_ii
    w._download = lambda url, path, t: open(path, "wb").write(b"x")
    fn, attr = w.fetch_one("q", tmp, "ch_01_00")
    assert fn == "ch_01_00.jpg", "SA(Bad.png)を飛ばしてPD(Good.jpg)を採用"
    assert "Public domain" in attr
    print("  fetch_one: 不適合ライセンスを飛ばし次候補を採用 OK")


def test_fetch_one_none_when_all_denied():
    tmp = tempfile.mkdtemp()
    w.search = lambda q, n, t: ["File:A.jpg"]
    w.imageinfo = lambda title, t: {"url": "https://x/A.jpg", "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}

    def no_dl(*a):
        raise AssertionError("弾いた画像をDLしてはいけない")

    w._download = no_dl
    fn, attr = w.fetch_one("q", tmp, "ch_02_00")
    assert fn is None and attr is None, "全て不適合なら None"
    print("  fetch_one: 全て不適合ライセンス→None OK")


def test_fetch_one_search_error():
    tmp = tempfile.mkdtemp()

    def boom(*a):
        raise RuntimeError("network down")

    w.search = boom
    fn, attr = w.fetch_one("q", tmp, "ch_03_00")
    assert fn is None and attr is None, "検索失敗は None（呼び出し側でプレースホルダ）"
    print("  fetch_one: 検索失敗→None OK")


# ---- Pexels ----

def test_pexels_url_and_pick():
    u = px.build_search_url("server room", 8)
    assert "query=server+room" in u and "per_page=8" in u and "orientation=landscape" in u
    assert px.pick_photo([]) is None
    assert px.pick_photo([{"id": 1}, {"id": 2}])["id"] == 1, "先頭を選ぶ"
    assert px._image_url({"src": {"large2x": "a", "large": "b"}}) == "a", "large2x優先"
    assert px._image_url({"src": {"original": "o"}}) == "o", "fallback original"
    print("  pexels: build_search_url/pick_photo/_image_url OK")


def test_pexels_fetch_one():
    tmp = tempfile.mkdtemp()
    px.search = lambda q, k, pp, t: [{"src": {"large2x": "https://x/p.jpeg"}, "photographer": "Ann"}]
    px._download = lambda url, path, t: open(path, "wb").write(b"x")
    fn, attr = px.fetch_one("q", tmp, "ch_00_01", "KEY")
    assert fn == "ch_00_01.jpeg" and attr == "Ann / Pexels"
    # APIキー無し → None
    assert px.fetch_one("q", tmp, "ch_00_02", "") == (None, None)
    # 該当なし → None
    px.search = lambda q, k, pp, t: []
    assert px.fetch_one("q", tmp, "ch_00_03", "KEY") == (None, None)
    print("  pexels: fetch_one 成功/キー無し/該当なし OK")


# ---- Pixabay ----

def test_pixabay_url_and_pick():
    u = pb.build_search_url("network", "KEY", 5)
    assert "q=network" in u and "key=KEY" in u and "per_page=5" in u and "orientation=horizontal" in u
    assert pb.pick_hit([]) is None
    assert pb.pick_hit([{"id": 1}])["id"] == 1
    assert pb._image_url({"largeImageURL": "L", "webformatURL": "W"}) == "L", "large優先"
    assert pb._image_url({"webformatURL": "W"}) == "W", "fallback webformat"
    print("  pixabay: build_search_url/pick_hit/_image_url OK")


def test_pixabay_fetch_one():
    tmp = tempfile.mkdtemp()
    pb.search = lambda q, k, pp, t: [{"largeImageURL": "https://x/i.png", "user": "Bob"}]
    pb._download = lambda url, path, t: open(path, "wb").write(b"x")
    fn, attr = pb.fetch_one("q", tmp, "ch_01_00", "KEY")
    assert fn == "ch_01_00.png" and attr == "Bob / Pixabay"
    assert pb.fetch_one("q", tmp, "ch_01_01", "") == (None, None), "キー無し→None"
    print("  pixabay: fetch_one 成功/キー無し OK")


# ---- image_fetch（振り分け・各clientのfetch_oneをモンキーパッチ） ----

def test_fetch_images_routing():
    import os
    from src import image_fetch as fch
    calls = []
    w.fetch_one = lambda q, d, b, t=30: (calls.append(("wiki", q)), (b + ".jpg", "WikiAttr"))[1]
    px.fetch_one = lambda q, d, b, k, t=30: (calls.append(("px", q)), (b + ".jpg", "PxAttr"))[1]
    pb.fetch_one = lambda q, d, b, k, t=30: (calls.append(("pb", q)), (b + ".jpg", "PbAttr"))[1]
    os.environ["PEXELS_API_KEY"] = "K"
    os.environ["PIXABAY_API_KEY"] = "K2"
    config = {"images": {"wikimedia": {"enable": True},
                         "pexels": {"enable": True, "api_key_env": "PEXELS_API_KEY"},
                         "pixabay": {"enable": True, "api_key_env": "PIXABAY_API_KEY"}}}
    chapters = [{"image_cuts": [
        {"image_query": "Linus", "image_kind": "subject"},   # → wiki
        {"image_query": "code", "image_kind": "ambient"},    # → px
    ]}]
    files, attrs = fch.fetch_images(chapters, "/tmp", config)
    assert files[(0, 0)] == "ch_00_00.jpg" and attrs[(0, 0)] == "WikiAttr", "subjectはWikimedia優先"
    assert files[(0, 1)] == "ch_00_01.jpg" and attrs[(0, 1)] == "PxAttr", "ambientはPexels優先"
    assert ("wiki", "Linus") in calls and ("px", "code") in calls
    print("  fetch_images: subject→wiki / ambient→px 振り分け OK")


def test_fetch_images_ambient_fallback_to_pixabay():
    import os
    from src import image_fetch as fch
    px.fetch_one = lambda q, d, b, k, t=30: (None, None)   # Pexelsは失敗
    pb.fetch_one = lambda q, d, b, k, t=30: (b + ".png", "PbAttr")
    os.environ["PEXELS_API_KEY"] = "K"
    os.environ["PIXABAY_API_KEY"] = "K2"
    config = {"images": {"wikimedia": {"enable": True},
                         "pexels": {"enable": True, "api_key_env": "PEXELS_API_KEY"},
                         "pixabay": {"enable": True, "api_key_env": "PIXABAY_API_KEY"}}}
    chapters = [{"image_cuts": [{"image_query": "server", "image_kind": "ambient"}]}]
    files, attrs = fch.fetch_images(chapters, "/tmp", config)
    assert files[(0, 0)] == "ch_00_00.png" and attrs[(0, 0)] == "PbAttr", "Pexels失敗→Pixabayフォールバック"
    print("  fetch_images: ambient Pexels失敗→Pixabay OK")


def test_fetch_images_all_fail_is_placeholder():
    import os
    from src import image_fetch as fch
    w.fetch_one = lambda q, d, b, t=30: (None, None)
    px.fetch_one = lambda q, d, b, k, t=30: (None, None)
    pb.fetch_one = lambda q, d, b, k, t=30: (None, None)
    os.environ["PEXELS_API_KEY"] = "K"
    os.environ["PIXABAY_API_KEY"] = "K2"
    config = {"images": {"wikimedia": {"enable": True},
                         "pexels": {"enable": True, "api_key_env": "PEXELS_API_KEY"},
                         "pixabay": {"enable": True, "api_key_env": "PIXABAY_API_KEY"}}}
    chapters = [{"image_cuts": [{"image_query": "x", "image_kind": "subject"},
                                {"image_query": "", "image_kind": "ambient"}]}]  # 空queryはskip
    files, attrs = fch.fetch_images(chapters, "/tmp", config)
    assert files == {} and attrs == {}, "全失敗・空queryはimage_filesに入らない(プレースホルダ)"
    print("  fetch_images: 全失敗/空query→プレースホルダ OK")


if __name__ == "__main__":
    print("test_image_clients:")
    test_pick_license()
    test_build_attribution()
    test_build_urls_and_ext()
    test_fetch_one_success()
    test_fetch_one_skips_bad_license()
    test_fetch_one_none_when_all_denied()
    test_fetch_one_search_error()
    test_pexels_url_and_pick()
    test_pexels_fetch_one()
    test_pixabay_url_and_pick()
    test_pixabay_fetch_one()
    test_fetch_images_routing()
    test_fetch_images_ambient_fallback_to_pixabay()
    test_fetch_images_all_fail_is_placeholder()
    print("ALL PASS")
