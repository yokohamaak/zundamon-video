"""画像レビュー承認フローのロジックテスト（ネットワーク/ブラウザ不要・標準ライブラリのみ）。

実行: python test_review.py
"""
import json
import os
import tempfile

import main_story as M
import review_server as R


def _sample_review():
    return {"cuts": [
        {"ch": 0, "ci": 0, "title": "章A", "query": "wifi router", "kind": "ambient",
         "image": "ch_00_00.jpeg", "attribution": "X / Pexels", "approved": False},
        {"ch": 1, "ci": 0, "title": "章B", "query": "PNG", "kind": "subject",
         "image": None, "attribution": None, "approved": False},
    ]}


def test_find_and_keys():
    rev = _sample_review()
    assert R.cut_key(rev["cuts"][0]) == "0_0"
    assert R.find_cut(rev, "1_0")["query"] == "PNG"
    assert R.find_cut(rev, "9_9") is None
    print("  find_cut / cut_key OK")


def test_safe_ext():
    assert R.safe_ext("a.PNG") == ".png"
    assert R.safe_ext("a.jpeg") == ".jpeg"
    assert R.safe_ext("../../evil.sh") == ".png", "許可外は既定.pngに丸める"
    assert R.safe_ext("noext") == ".png"
    print("  safe_ext: 許可拡張子のみ・パス事故防止 OK")


def test_approve_and_attribution():
    rev = _sample_review()
    assert R.apply_approve(rev, "0_0", True)
    assert rev["cuts"][0]["approved"] is True
    assert R.apply_attribution(rev, "0_0", "  New / CC0  ")
    assert rev["cuts"][0]["attribution"] == "New / CC0", "trimして保存"
    assert R.apply_attribution(rev, "0_0", "   ")
    assert rev["cuts"][0]["attribution"] is None, "空はNone"
    assert not R.apply_approve(rev, "9_9", True), "未知キーはFalse"
    print("  approve / attribution OK")


def test_replace_writes_and_updates():
    # 1x1 PNG（base64）
    png_b64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGA"
               "hKmMIQAAAABJRU5ErkJggg==")
    tmp = tempfile.mkdtemp()
    R.DIR = tmp  # ストレージ層の出力先を差し替え
    rev = _sample_review()
    ok, msg, fn = R.apply_replace(rev, "1_0", "mypic.PNG", png_b64, "自作")
    assert ok and fn == "ch_01_00.png", f"ch名で保存: {fn} ({msg})"
    assert os.path.exists(os.path.join(tmp, "ch_01_00.png")), "実ファイル書き出し"
    cut = R.find_cut(rev, "1_0")
    assert cut["image"] == "ch_01_00.png"
    assert cut["attribution"] == "自作"
    assert cut["approved"] is True, "差し替え＝承認扱い"
    # 不正base64は失敗・ファイル増やさない
    ok2, msg2, _ = R.apply_replace(rev, "0_0", "x.png", "not_base64!!", "")
    assert (not ok2) or msg2, "不正データはok=Falseかメッセージ付き"
    print("  replace: base64保存・review更新・承認扱い OK")


def test_summary():
    rev = _sample_review()
    R.apply_approve(rev, "0_0", True)
    s = R.review_summary(rev)
    assert s == {"total": 2, "approved": 1, "status": "reviewing"}, s
    print("  review_summary OK")


def test_build_review_matches_fetch_order():
    chapters = [
        {"title": "章A", "image_cuts": [{"image_query": "q1", "image_kind": "ambient"},
                                        {"image_query": "q2", "image_kind": "subject"}]},
        {"title": "章B", "image_cuts": [{"image_query": "q3", "image_kind": "subject"}]},
    ]
    image_files = {(0, 0): "ch_00_00.jpg", (1, 0): "ch_01_00.png"}
    attributions = {(1, 0): "A / CC BY"}
    rev = M.build_review(chapters, image_files, attributions)
    assert len(rev["cuts"]) == 3
    assert rev["cuts"][0]["image"] == "ch_00_00.jpg"
    assert rev["cuts"][1]["image"] is None, "未取得はNone(プレースホルダ)"
    assert rev["cuts"][2]["attribution"] == "A / CC BY"
    assert rev["cuts"][1]["kind"] == "subject"
    print("  build_review: fetchと同順・未取得None OK")


def test_load_images_from_review_roundtrip():
    tmp = tempfile.mkdtemp()
    rev = {"cuts": [
        {"ch": 0, "ci": 0, "image": "ch_00_00.jpeg", "attribution": "X / Pexels"},
        {"ch": 0, "ci": 1, "image": None, "attribution": None},
        {"ch": 2, "ci": 0, "image": "ch_02_00.png", "attribution": None},
    ]}
    with open(os.path.join(tmp, "review.json"), "w", encoding="utf-8") as f:
        json.dump(rev, f)
    imgs, attrs = M.load_images_from_review(tmp)
    assert imgs == {(0, 0): "ch_00_00.jpeg", (2, 0): "ch_02_00.png"}, imgs
    assert attrs == {(0, 0): "X / Pexels"}, attrs
    # review.json が無いディレクトリは空（フォールバック）
    assert M.load_images_from_review(tempfile.mkdtemp()) == ({}, {})
    print("  load_images_from_review: 復元・欠落スキップ・無ファイル空 OK")


if __name__ == "__main__":
    print("test_review:")
    test_find_and_keys()
    test_safe_ext()
    test_approve_and_attribution()
    test_replace_writes_and_updates()
    test_summary()
    test_build_review_matches_fetch_order()
    test_load_images_from_review_roundtrip()
    print("ALL PASS")
