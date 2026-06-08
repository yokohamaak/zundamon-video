"""
src.apod_crop 純関数の単体テスト（genai/Pillow/network不要）。

実行: python3 test_apod_crop.py
build_prompt / parse_crops / box_to_pixels / assign_cut_times を検証する。
"""
from src import apod_crop as ac


def test_build_prompt():
    p = ac.build_prompt("Andromeda", "A nearby galaxy.", n=4)
    assert "4 個" in p, "カット数を反映"
    assert "Andromeda" in p and "A nearby galaxy." in p
    assert "box_2d" in p and "[ymin, xmin, ymax, xmax]" in p
    # explanationは1500字に切り詰める（静的テキスト分を差し引いて測る）
    base = ac.build_prompt("T", "", n=3).count("あ")
    long_p = ac.build_prompt("T", "あ" * 3000, n=3)
    assert long_p.count("あ") - base == 1500, "explanationを1500字に切り詰め"
    print("  build_prompt: カット数/題材/bbox形式/切詰め OK")


def test_parse_crops_plain():
    text = '{"crops": [{"label": "中心", "box_2d": [100, 200, 400, 600], "reason": "r"}]}'
    crops = ac.parse_crops(text)
    assert len(crops) == 1 and crops[0]["box_2d"] == [100, 200, 400, 600]
    print("  parse_crops(plain): OK")


def test_parse_crops_codefence_and_filter():
    # コードフェンス＋box_2d不正な要素は除外される
    text = """```json
{"crops": [
  {"label": "ok", "box_2d": [0, 0, 500, 500]},
  {"label": "bad3要素", "box_2d": [1, 2, 3]},
  {"label": "box無し"}
]}
```"""
    crops = ac.parse_crops(text)
    assert len(crops) == 1 and crops[0]["label"] == "ok", crops
    print("  parse_crops(fence+filter): 不正boxを除外 OK")


def test_parse_crops_all_invalid_raises():
    try:
        ac.parse_crops('{"crops": [{"label": "x"}]}')
    except ValueError:
        print("  parse_crops: 有効box無しでValueError OK")
        return
    raise AssertionError("有効box無しでValueErrorが出ていない")


def test_box_to_pixels():
    # 0-1000正規化 → 画素。w=1000,h=2000なら等倍/2倍
    left, top, right, bottom = ac.box_to_pixels([100, 200, 600, 800], 1000, 2000)
    assert (left, top, right, bottom) == (200, 200, 800, 1200), (left, top, right, bottom)
    # 逆転座標・範囲外もクランプ＆ソートされる
    l, t, r, b = ac.box_to_pixels([900, 900, 100, 100], 1000, 1000)
    assert l <= r and t <= b, "min/maxが整列"
    assert 0 <= l and r <= 1000
    print("  box_to_pixels: 正規化変換/クランプ/整列 OK")


def test_box_to_focus():
    # 0-1000 → 0..1 正規化。box=[ymin,xmin,ymax,xmax]
    f = ac.box_to_focus([100, 200, 600, 800])
    assert f == {"l": 0.2, "t": 0.1, "r": 0.8, "b": 0.6}, f
    # 逆転座標・範囲外はクランプ＆整列
    f2 = ac.box_to_focus([1200, 900, -50, 100])
    assert f2["l"] <= f2["r"] and f2["t"] <= f2["b"], f2
    assert 0.0 <= f2["l"] and f2["r"] <= 1.0 and 0.0 <= f2["t"] and f2["b"] <= 1.0, f2
    print("  box_to_focus: 正規化/クランプ/整列 OK")


def _turns(ends):
    """startは前のend、最初は0で生成（簡易）。"""
    out, prev = [], 0.0
    for e in ends:
        out.append({"start": prev, "end": e})
        prev = e
    return out


def test_assign_cut_times_basic():
    crops = [{"label": "a"}, {"label": "b"}]
    turns = _turns([1.0, 2.0, 3.0, 4.0])  # 4ターン → 2カットに均等(2+2)
    out = ac.assign_cut_times(crops, turns)
    assert len(out) == 2
    # [0, total] を隙間なく覆う・重ならない
    assert out[0]["start"] == 0.0
    assert out[-1]["end"] == 4.0
    assert out[0]["end"] == out[1]["start"], "境界が一致（隙間/重なり無し）"
    # 切替はターン境界（turns[2].start=2.0）
    assert out[1]["start"] == 2.0, out
    print("  assign_cut_times: 均等割り/全尺被覆/境界一致 OK")


def test_assign_cut_times_more_cuts_than_turns():
    crops = [{"label": "a"}, {"label": "b"}, {"label": "c"}]
    turns = _turns([1.0, 2.0])  # 2ターンに3カット → 2に丸め
    out = ac.assign_cut_times(crops, turns)
    assert len(out) == 2, "カット数はターン数に丸める"
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 2.0
    print("  assign_cut_times: カット>ターンは丸め OK")


def test_assign_cut_times_empty():
    assert ac.assign_cut_times([], _turns([1.0])) == []
    assert ac.assign_cut_times([{"label": "a"}], []) == []
    print("  assign_cut_times: 空入力で[] OK")


def _phased(specs):
    """specs=[(end, phase)] から start/end/phase 付きturnsを生成。"""
    out, prev = [], 0.0
    for end, phase in specs:
        out.append({"start": prev, "end": end, "phase": phase})
        prev = end
    return out


def test_assign_cuts_by_phase_stock_on_fact():
    # intro1 / fact2 / if2 / outro1。stockはfact区間に入る。
    turns = _phased([(1.0, "intro"), (3.0, "fact"), (5.0, "fact"),
                     (7.0, "if"), (9.0, "if"), (10.0, "outro")])
    crops = [{"file": "cut_01.jpg"}, {"file": "cut_02.jpg"}, {"file": "cut_03.jpg"}]
    stocks = [{"file": "stock_01.jpg"}, {"file": "stock_02.jpg"}]
    out = ac.assign_cuts_by_phase(turns, crops, stocks)
    # 全尺被覆・隙間/重なり無し
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 10.0
    for i in range(len(out) - 1):
        assert out[i]["end"] == out[i + 1]["start"], out
    # fact区間(2.0〜6.0)に入るカットはstock由来
    fact_cuts = [c for c in out if c["start"] >= 2.0 and c["end"] <= 6.0]
    assert fact_cuts and all(c["file"].startswith("stock_") for c in fact_cuts), out
    # fact以外はcrop由来
    nonfact = [c for c in out if c["end"] <= 2.0 or c["start"] >= 6.0]
    assert nonfact and all(c["file"].startswith("cut_") for c in nonfact), out
    print("  assign_cuts_by_phase: factにstock/他にcrop・全尺被覆 OK")


def test_assign_cuts_by_phase_manual_on_if():
    # fact→stock / if→manual / intro,outro→crop。manualはfile=Noneでも枠として配置。
    turns = _phased([(1.0, "intro"), (3.0, "fact"), (5.0, "if"), (6.0, "outro")])
    crops = [{"file": "cut_01.jpg"}, {"file": "cut_02.jpg"}]
    stocks = [{"file": "stock_01.jpg"}]
    manuals = [{"file": None, "label": "夢", "prompt": "p", "target": "manual_01.png"}]
    out = ac.assign_cuts_by_phase(turns, crops, stocks, manuals)
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 6.0
    # fact区間(1.0〜3.0)はstock、if区間(3.0〜5.0)はmanual枠
    fact_cut = next(c for c in out if c["start"] == 1.0)
    assert fact_cut["file"] == "stock_01.jpg", fact_cut
    if_cut = next(c for c in out if c["start"] == 3.0)
    assert if_cut.get("target") == "manual_01.png" and if_cut.get("file") is None, if_cut
    print("  assign_cuts_by_phase: ifにmanual(file無でも枠) OK")


def test_assign_cuts_by_phase_no_stock_uses_crop():
    # stock空ならfactもcropで埋まる（フォールバック）
    turns = _phased([(2.0, "fact"), (4.0, "if")])
    out = ac.assign_cuts_by_phase(turns, [{"file": "cut_01.jpg"}], [])
    assert out and all(c["file"].startswith("cut_") for c in out)
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 4.0
    print("  assign_cuts_by_phase: stock無しはcropで埋める OK")


def test_assign_cuts_by_phase_empty():
    assert ac.assign_cuts_by_phase(_phased([(1.0, "fact")]), [], []) == []
    assert ac.assign_cuts_by_phase([], [{"file": "c"}], [{"file": "s"}]) == []
    print("  assign_cuts_by_phase: 両プール空/ターン空で[] OK")


if __name__ == "__main__":
    print("test_apod_crop:")
    test_build_prompt()
    test_parse_crops_plain()
    test_parse_crops_codefence_and_filter()
    test_parse_crops_all_invalid_raises()
    test_box_to_pixels()
    test_box_to_focus()
    test_assign_cut_times_basic()
    test_assign_cut_times_more_cuts_than_turns()
    test_assign_cut_times_empty()
    test_assign_cuts_by_phase_stock_on_fact()
    test_assign_cuts_by_phase_manual_on_if()
    test_assign_cuts_by_phase_no_stock_uses_crop()
    test_assign_cuts_by_phase_empty()
    print("ALL PASS")
