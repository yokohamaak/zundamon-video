"""
src.nasa_images 純関数の単体テスト（network/Pillow不要）。

実行: python3 test_nasa_images.py
URL組み立て・検索レスポンス解析・アセットURL選択を検証する。
fetch_stock_images（ネットワーク）はここでは検証しない。
"""
from src import nasa_images as ni


def test_build_urls():
    u = ni.build_search_url("Alpha Centauri")
    assert u.startswith(ni.SEARCH_ENDPOINT) and "q=Alpha+Centauri" in u and "media_type=image" in u
    a = ni.build_asset_url("PIA12345")
    assert a == f"{ni.ASSET_ENDPOINT}/PIA12345"
    print("  build_search_url/build_asset_url: OK")


def test_parse_search_items():
    text = """{"collection": {"items": [
      {"data": [{"nasa_id": "id1", "title": "Galaxy", "media_type": "image"}],
       "links": [{"href": "https://x/id1~thumb.jpg", "rel": "preview"}]},
      {"data": [{"nasa_id": "vid1", "title": "Clip", "media_type": "video"}], "links": []},
      {"data": [{"title": "no id", "media_type": "image"}], "links": []}
    ]}}"""
    items = ni.parse_search_items(text)
    assert len(items) == 1, "image かつ nasa_id ありのみ"
    assert items[0]["nasa_id"] == "id1"
    assert items[0]["thumb"] == "https://x/id1~thumb.jpg"
    print("  parse_search_items: image/nasa_idフィルタ+thumb抽出 OK")


def test_pick_image_url_prefers_large():
    text = """{"collection": {"items": [
      {"href": "https://x/id~thumb.jpg"},
      {"href": "https://x/id~orig.jpg"},
      {"href": "https://x/id~large.jpg"},
      {"href": "https://x/id~medium.jpg"}
    ]}}"""
    assert ni.pick_image_url(text).endswith("~large.jpg"), "largeを最優先"
    print("  pick_image_url: large優先 OK")


def test_pick_image_url_fallbacks():
    # large/medium/origが無ければ任意のjpg
    text = '{"collection": {"items": [{"href": "https://x/id~foo.jpg"}]}}'
    assert ni.pick_image_url(text).endswith("~foo.jpg")
    # jpgが無ければthumb_fallback
    text2 = '{"collection": {"items": [{"href": "https://x/id~orig.tif"}]}}'
    assert ni.pick_image_url(text2, thumb_fallback="T") == "T"
    # 壊れたJSONはfallback
    assert ni.pick_image_url("not json", thumb_fallback="T") == "T"
    print("  pick_image_url: jpgフォールバック/thumb/壊れJSON OK")


if __name__ == "__main__":
    print("test_nasa_images:")
    test_build_urls()
    test_parse_search_items()
    test_pick_image_url_prefers_large()
    test_pick_image_url_fallbacks()
    print("ALL PASS")
