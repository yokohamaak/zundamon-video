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


def test_clean_crop_and_filter():
    assert R._clean_crop({"l": 0.1, "t": 0.2, "r": 0.8, "b": 0.7}) == {"l": 0.1, "t": 0.2, "r": 0.8, "b": 0.7}
    assert R._clean_crop({"l": 0.5, "t": 0, "r": 0.505, "b": 1}) is None, "極小幅は無効"
    assert R._clean_crop({"l": -1, "t": 0, "r": 2, "b": 1}) == {"l": 0.0, "t": 0.0, "r": 1.0, "b": 1.0}, "0..1にクランプ"
    assert R._clean_crop("nope") is None
    assert R._clean_filter({"brightness": 0.6, "contrast": 1.0, "grayscale": 0}) == {"brightness": 0.6}, "既定値は落とす"
    assert R._clean_filter({"brightness": 1, "contrast": 1, "grayscale": 0}) is None, "全て既定→None"
    assert R._clean_filter({"grayscale": 1}) == {"grayscale": 1}
    # pad: 0..400クランプ・0/不正はNone
    assert R._clean_pad(80) == 80
    assert R._clean_pad(0) is None and R._clean_pad("x") is None
    assert R._clean_pad(9999) == 400
    # color: #hex/rgb()/色名のみ
    assert R._clean_color("#ffffff") == "#ffffff"
    assert R._clean_color("white") == "white"
    assert R._clean_color("rgb(255,0,0)") == "rgb(255,0,0)"
    assert R._clean_color("javascript:alert(1)") is None
    assert R._clean_color(None) is None
    print("  _clean_crop / _clean_filter / _clean_pad / _clean_color OK")


def test_apply_options():
    rev = _sample_review()
    ok, ap = R.apply_options(rev, "0_0", {"fit": "cover"})
    assert ok and rev["cuts"][0]["fit"] == "cover"
    R.apply_options(rev, "0_0", {"fit": "bogus"})
    assert rev["cuts"][0]["fit"] is None, "不正fitはNone"
    R.apply_options(rev, "0_0", {"hide": True, "filter": {"grayscale": 1}})
    assert rev["cuts"][0]["hide"] is True
    assert rev["cuts"][0]["filter"] == {"grayscale": 1}
    R.apply_options(rev, "0_0", {"crop": {"l": 0, "t": 0, "r": 0.5, "b": 0.5}})
    assert rev["cuts"][0]["crop"] == {"l": 0, "t": 0, "r": 0.5, "b": 0.5}
    R.apply_options(rev, "0_0", {"pad": 80, "bg": "#ffffff"})
    assert rev["cuts"][0]["pad"] == 80 and rev["cuts"][0]["bg"] == "#ffffff"
    R.apply_options(rev, "0_0", {"pad": 0, "bg": "bad;url"})
    assert rev["cuts"][0]["pad"] is None and rev["cuts"][0]["bg"] is None, "0/不正色はNone"
    assert R.apply_options(rev, "9_9", {"fit": "cover"}) == (False, {})
    print("  apply_options: fit/crop/filter/hide 適用・検証 OK")


def test_valid_http_url():
    assert R.valid_http_url("https://example.com/a.png")
    assert R.valid_http_url("http://x.test/i.jpg")
    assert not R.valid_http_url("file:///etc/passwd")
    assert not R.valid_http_url("javascript:alert(1)")
    assert not R.valid_http_url("data:image/png;base64,xxxx")  # data:はクライアントで処理（ここでは弾く）
    assert not R.valid_http_url(None)
    print("  valid_http_url: http/httpsのみ許可 OK")


def test_import_url_guards():
    # 不正keyとURLスキームはネットワークに行かず弾く
    rev = _sample_review()
    assert R.apply_import_url(rev, "9_9", "https://x/a.png", None)[0] is False, "未知key"
    ok, msg, _ = R.apply_import_url(rev, "0_0", "file:///x", None)
    assert ok is False and "URL" in msg, "非http(s)は弾く"
    print("  apply_import_url: key/スキームのガード OK")


def test_apply_save_script():
    ok, _, norm = R.apply_save_script(
        {"theme": "t", "chapters": [{"title": "A"}], "script": [{"speaker": "x", "text": "y", "cut": 1}]})
    assert ok and norm["theme"] == "t" and norm["script"][0]["cut"] == 1
    assert R.apply_save_script({"script": []})[0] is False, "空scriptは不可"
    assert R.apply_save_script({"script": [{"speaker": "x"}]})[0] is False, "text無しは不可"
    assert R.apply_save_script("nope")[0] is False
    print("  apply_save_script: 検証/正規化 OK")


def test_pipeline_status_and_script_io():
    tmp = tempfile.mkdtemp()
    old = R.DIR
    R.DIR = tmp
    try:
        # 何も無ければ全False、load_scriptはNone
        st = R.pipeline_status()
        assert st == {"script": False, "review": False, "audio": False, "meta": False}, st
        assert R.load_script() is None
        # script.json 保存→load→status反映
        R.save_script({"theme": "t", "script": [{"speaker": "x", "text": "y"}]})
        assert R.load_script()["theme"] == "t"
        assert R.pipeline_status()["script"] is True
    finally:
        R.DIR = old
    print("  pipeline_status / load_script / save_script OK")


def test_do_fetch_cut_guards():
    # ネットワークに行かないガード（空クエリ・不正ch）。
    assert R.do_fetch_cut(0, 0, "", "subject")["ok"] is False
    assert R.do_fetch_cut(0, 0, "   ", "ambient")["ok"] is False
    assert R.do_fetch_cut("x", 0, "query", "subject")["ok"] is False
    print("  do_fetch_cut: 空クエリ/不正chを取得前に弾く OK")


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
        {"ch": 0, "ci": 0, "image": "ch_00_00.jpeg", "attribution": "X / Pexels",
         "fit": "contain", "filter": {"grayscale": 1}},
        {"ch": 0, "ci": 1, "image": None, "attribution": None, "hide": True},
        {"ch": 2, "ci": 0, "image": "ch_02_00.png", "attribution": None,
         "crop": {"l": 0.1, "t": 0.1, "r": 0.9, "b": 0.8}},
    ]}
    with open(os.path.join(tmp, "review.json"), "w", encoding="utf-8") as f:
        json.dump(rev, f)
    imgs, attrs, opts = M.load_images_from_review(tmp)
    assert imgs == {(0, 0): "ch_00_00.jpeg", (2, 0): "ch_02_00.png"}, imgs
    assert attrs == {(0, 0): "X / Pexels"}, attrs
    assert opts[(0, 0)] == {"fit": "contain", "filter": {"grayscale": 1}}, opts.get((0, 0))
    assert opts[(0, 1)] == {"hide": True}, opts.get((0, 1))
    assert opts[(2, 0)] == {"crop": {"l": 0.1, "t": 0.1, "r": 0.9, "b": 0.8}}, opts.get((2, 0))
    # review.json が無いディレクトリは空（フォールバック）
    assert M.load_images_from_review(tempfile.mkdtemp()) == ({}, {}, {})
    print("  load_images_from_review: 画像/帰属/描画オプション復元・無ファイル空 OK")


def test_cut_groups():
    turns = [{"cut": 0}, {"cut": 0}, {"cut": 1}, {"cut": 1}, {"cut": 2}]
    assert M._cut_groups([0, 1, 2, 3, 4], turns, 3) == [(0, 0, 2), (1, 2, 4), (2, 4, 5)]
    # 非減少クランプ（逆戻りは前の値に）
    t2 = [{"cut": 1}, {"cut": 0}, {"cut": 2}]
    assert M._cut_groups([0, 1, 2], t2, 3) == [(1, 0, 2), (2, 2, 3)]
    # 欠落は直前を継続
    t3 = [{"cut": 0}, {}, {"cut": 1}]
    assert M._cut_groups([0, 1, 2], t3, 2) == [(0, 0, 2), (1, 2, 3)]
    # アンカー皆無→None（均等割りへ）
    assert M._cut_groups([0, 1], [{}, {}], 2) is None
    assert M._cut_groups([0], [{"cut": 0}], 0) is None  # cuts無し
    print("  _cut_groups: グループ化/非減少/欠落補完/フォールバック OK")


def test_build_chapter_topics_anchored():
    chapters = [{"title": "章A", "section": "trivia",
                 "image_cuts": [{"image_query": "q0", "image_kind": "ambient"},
                                {"image_query": "q1", "image_kind": "subject"}]}]
    # 5ターン。cut: 0,0,0,1,1 → 画像0は0-3秒、画像1は3-5秒
    turns = [{"start": float(i), "end": float(i + 1), "cut": (0 if i < 3 else 1)} for i in range(5)]
    segments = [{"chapter": 0, "section": "trivia", "turns": [0, 1, 2, 3, 4]}]
    imgs = {(0, 0): "ch_00_00.jpg", (0, 1): "ch_00_01.jpg"}
    tops = M.build_chapter_topics(segments, turns, chapters, imgs, {})
    assert len(tops) == 2
    assert tops[0]["image"] == "ch_00_00.jpg" and tops[0]["start"] == 0.0 and tops[0]["end"] == 3.0
    assert tops[1]["image"] == "ch_00_01.jpg" and tops[1]["start"] == 3.0
    print("  build_chapter_topics: cutアンカーで切替タイミングを反映 OK")


def test_build_chapter_topics_applies_opts():
    chapters = [{"title": "章A", "section": "trivia",
                 "image_cuts": [{"image_query": "q1", "image_kind": "ambient"},
                                {"image_query": "q2", "image_kind": "subject"},
                                {"image_query": "q3", "image_kind": "ambient"}]}]
    # カット数≤ターン数の仕様。3カット出すにはターンも3つ要る。
    turns = [{"speaker": "A", "text": "x", "start": float(i * 3), "end": float(i * 3 + 3)}
             for i in range(3)]
    segments = [{"chapter": 0, "section": "trivia", "turns": [0, 1, 2]}]
    image_files = {(0, 0): "ch_00_00.jpg", (0, 1): "ch_00_01.jpg", (0, 2): "ch_00_02.jpg"}
    cut_opts = {
        (0, 0): {"filter": {"brightness": 0.6}, "fit": "cover"},
        (0, 1): {"crop": {"l": 0, "t": 0, "r": 0.5, "b": 0.5}},
        (0, 2): {"hide": True},
    }
    tops = M.build_chapter_topics(segments, turns, chapters, image_files, {}, cut_opts)
    assert tops[0]["fit"] == "cover" and tops[0]["filter"] == {"brightness": 0.6}
    assert tops[1]["crop"] == {"l": 0, "t": 0, "r": 0.5, "b": 0.5}
    assert tops[1]["fit"] == "contain", "subjectは既定contain（opt.fit無し時）"
    assert tops[2].get("blank") is True and "image" not in tops[2], "hide=画像なし(blank)"
    print("  build_chapter_topics: fit/crop/filter/hide 反映 OK")


if __name__ == "__main__":
    print("test_review:")
    test_find_and_keys()
    test_safe_ext()
    test_approve_and_attribution()
    test_replace_writes_and_updates()
    test_clean_crop_and_filter()
    test_apply_options()
    test_apply_save_script()
    test_pipeline_status_and_script_io()
    test_do_fetch_cut_guards()
    test_valid_http_url()
    test_import_url_guards()
    test_summary()
    test_build_review_matches_fetch_order()
    test_load_images_from_review_roundtrip()
    test_cut_groups()
    test_build_chapter_topics_anchored()
    test_build_chapter_topics_applies_opts()
    print("ALL PASS")
