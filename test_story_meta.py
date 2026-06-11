"""
main_story の meta組み立て単体テスト（VOICEVOX/Gemini呼び出しなし・純関数のみ）。

実行: python3 test_story_meta.py
build_chapter_topics（[0,total]被覆/section/chapter/placeholder/image+credit）、
build_credits、build_meta（speakers/script合流/title）を検証する。
"""
import main_story as m
from src import story_script


def _turns(n, dur=2.0):
    """連続するn個のターン時刻を作る（start/end/sentences付き）。"""
    out = []
    for i in range(n):
        out.append({"start": round(i * dur, 3), "end": round((i + 1) * dur, 3),
                    "sentences": [{"text": f"s{i}", "start": i * dur, "end": (i + 1) * dur}]})
    return out


CHAPTERS = [
    {"section": "intro", "title": "はじまり", "image_cuts": [
        {"image_query": "wifi router", "image_kind": "subject"},
        {"image_query": "developers", "image_kind": "ambient"},
    ]},
    {"section": "trivia", "title": "Wi-Fiは略語じゃない", "image_cuts": [
        {"image_query": "wifi symbol", "image_kind": "subject"},
    ]},
    {"section": "outro", "title": "まとめ", "image_cuts": [
        {"image_query": "gadgets", "image_kind": "ambient"},
    ]},
]


def test_build_chapter_topics_coverage():
    # 章0: cut2個・ターン2→2カット / 章1: cut1・ターン1→1 / 章2: cut1・ターン2→1。計4topic。
    script = [{"chapter": 0}, {"chapter": 0}, {"chapter": 1}, {"chapter": 2}, {"chapter": 2}]
    turns = _turns(len(script))
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, CHAPTERS)
    assert len(topics) == 4, f"章0が2カット+章1+章2=4topic: {len(topics)}"
    assert topics[0]["start"] == 0.0, "先頭は0始まり"
    assert topics[-1]["end"] == turns[-1]["end"], "末尾はtotalで終わる"
    # 隙間なく連結
    for a, b in zip(topics, topics[1:]):
        assert a["end"] == b["start"], f"隙間/重なりなし: {a['end']} != {b['start']}"
    # 章0の2カットは同じ章メタ・別ファイル名
    assert topics[0]["chapter"] == 0 and topics[1]["chapter"] == 0
    assert topics[0]["title"] == "はじまり" and topics[0]["section"] == "intro"
    assert topics[0]["chapterTotal"] == 3
    assert topics[0]["placeholder"] == "ch_00_00.png" and topics[1]["placeholder"] == "ch_00_01.png"
    # trivia章だけ「実は」通し番号が付く（intro/outroには付かない）
    trivia = [t for t in topics if t["section"] == "trivia"]
    intro = [t for t in topics if t["section"] == "intro"]
    assert trivia and all(t["triviaIndex"] == 1 and t["triviaTotal"] == 1 for t in trivia), "trivia章に通し番号"
    assert all("triviaIndex" not in t for t in intro), "intro章には通し番号を付けない"
    print("  build_chapter_topics: 章内複数カット/[0,total]被覆/trivia番号 OK")


def test_build_chapter_topics_placeholder():
    script = [{"chapter": 0}, {"chapter": 0}, {"chapter": 1}]
    turns = _turns(3)
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, CHAPTERS)  # image_status空＝全プレースホルダ
    for t in topics:
        assert "image" not in t, "未取得はimage無し"
        assert t["placeholder"].startswith("ch_") and t["placeholder"].endswith(".png"), "決め打ち名のplaceholder"
        assert t["note"], "差し替え案内(note)が入る"
    # 章0の2カットはcut別の検索語がnoteに出る
    assert topics[0]["note"] == "wifi router" and topics[1]["note"] == "developers"
    print("  build_chapter_topics: プレースホルダ枠/cut別query OK")


def test_build_chapter_topics_ready_image_and_credit():
    script = [{"chapter": 0}, {"chapter": 0}, {"chapter": 1}]
    turns = _turns(3)
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(
        segs, turns, CHAPTERS,
        image_files={(0, 0): "ch_00_00.png", (1, 0): "ch_01_00.jpg"},   # 章0cut0と章1cut0だけ取得済
        attributions={(0, 0): "Linus / CC-BY-3.0"},
    )
    assert topics[0]["image"] == "ch_00_00.png", "取得済cutはimage(実ファイル名)"
    assert topics[0]["credit"] == "Linus / CC-BY-3.0", "帰属が付く"
    assert "image" not in topics[1] and topics[1]["placeholder"] == "ch_00_01.png", "同章の未取得cutはプレースホルダ"
    assert topics[2]["image"] == "ch_01_00.jpg", "章1cut0は取得済(実ファイル名jpg)"
    print("  build_chapter_topics: 取得済画像+credit / 混在 OK")


def test_build_credits():
    config = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}}}
    # 帰属なし → 汎用クレジット
    creds = m.build_credits(config)
    assert "VOICEVOX:四国めたん" in creds and "VOICEVOX:ずんだもん" in creds
    assert any("商用可" in c for c in creds), "帰属なしは汎用ライセンス表記"
    # 帰属あり → 列挙（重複除去）
    creds = m.build_credits(config, {0: "A / CC-BY", 1: "A / CC-BY", 2: "B / CC-BY"})
    out = [c for c in creds if c.startswith("画像出典")]
    assert out and "A / CC-BY" in out[0] and "B / CC-BY" in out[0], "出典を列挙"
    assert out[0].count("A / CC-BY") == 1, "重複は除去"
    print("  build_credits: VOICEVOX+画像出典/重複除去 OK")


def test_build_meta():
    script_result = {
        "theme": "実は知らないデジタルの名前の謎",
        "chapters": CHAPTERS,
        "script": [
            {"speaker": "四国めたん", "text": "a", "chapter": 0, "section": "intro", "emotion": "normal", "effect": "kenburns"},
            {"speaker": "ずんだもん", "text": "b", "chapter": 0, "section": "intro", "emotion": "surprise", "effect": "kenburns"},
            {"speaker": "四国めたん", "text": "c", "chapter": 1, "section": "trivia", "emotion": "normal", "effect": "flash"},
        ],
    }
    turns = _turns(3)
    config = {"characters_gender": {"四国めたん": "female", "ずんだもん": "male"},
              "tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}}}
    meta = m.build_meta(script_result, turns, config, "2026-06-08T12:00:00+09:00")
    assert meta["title"] == "実は知らないデジタルの名前の謎", "title=theme"
    # speakers: 定義順=画面配置（左=四国めたん/右=ずんだもん）
    assert [s["name"] for s in meta["speakers"]] == ["四国めたん", "ずんだもん"]
    assert meta["speakers"][0]["gender"] == "female" and meta["speakers"][1]["gender"] == "male"
    # script合流（start/end/sentences付与）
    assert len(meta["script"]) == 3
    assert meta["script"][0]["start"] == 0.0 and "sentences" in meta["script"][0]
    # topics: 章0(cut2・ターン2→2カット)+章1(cut1・ターン1→1)=3・被覆
    assert len(meta["topics"]) == 3, "章0が2カット+章1=3topic"
    assert meta["topics"][0]["start"] == 0.0 and meta["topics"][-1]["end"] == turns[-1]["end"]
    print("  build_meta: title/speakers/script合流/topics OK")


def test_build_meta_cut_anchors():
    # C-1: cutは台本ターンに付く。TTSターン(turns)はcut無し。build_metaがmerged scriptを
    # build_chapter_topicsに渡すことで、均等割りでなくcutアンカーで切替されることを検証。
    script_result = {
        "theme": "t", "chapters": CHAPTERS,
        "script": [
            {"speaker": "A", "text": "a", "chapter": 0, "section": "intro", "cut": 0},
            {"speaker": "A", "text": "b", "chapter": 0, "section": "intro", "cut": 0},  # 両方cut0
            {"speaker": "A", "text": "c", "chapter": 1, "section": "trivia", "cut": 0},
        ],
    }
    turns = _turns(3)  # TTSターン（cut無し）
    config = {"characters_gender": {"A": "female"}, "tts_voicevox": {"speakers": {"A": 2}}}
    meta = m.build_meta(script_result, turns, config, "2026-06-08T12:00:00+09:00")
    ch0 = [t for t in meta["topics"] if t["chapter"] == 0]
    assert len(ch0) == 1, f"章0は2ターンとも cut0 → 1topic(均等割りなら2): {len(ch0)}"
    assert len(meta["topics"]) == 2, "章0(1)+章1(1)=2"
    assert ch0[0]["start"] == 0.0 and ch0[0]["end"] == turns[1]["end"], "章0=2ターン分の区間"
    print("  build_meta: cutアンカーが実経路で効く(turnsでなくscriptを渡す) OK")


def test_build_meta_length_mismatch_raises():
    script_result = {"theme": "x", "chapters": CHAPTERS,
                     "script": [{"speaker": "x", "text": "y", "chapter": 0}]}
    try:
        m.build_meta(script_result, _turns(2), {}, "iso")
    except ValueError:
        print("  build_meta: ターン数不一致でValueError OK")
        return
    raise AssertionError("ターン数不一致でValueErrorが出ていない")


def test_write_credits_txt():
    import pathlib
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    config = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}}}
    m.write_credits_txt(tmp, config, {(0, 0): "Jane / CC BY 4.0", (1, 0): "Ann / Pexels",
                                      (2, 0): "Jane / CC BY 4.0"})
    txt = (tmp / "credits.txt").read_text(encoding="utf-8")
    assert "VOICEVOX:四国めたん" in txt and "VOICEVOX:ずんだもん" in txt
    assert "Jane / CC BY 4.0" in txt and "Ann / Pexels" in txt
    assert txt.count("Jane / CC BY 4.0") == 1, "重複除去"
    # 帰属なし → 汎用表記
    m.write_credits_txt(tmp, config, {})
    assert "商用可ライセンス" in (tmp / "credits.txt").read_text(encoding="utf-8")
    print("  write_credits_txt: VOICEVOX+画像帰属/重複除去/帰属なし OK")


def test_build_audio():
    # config.audio が無ければ None
    assert m.build_audio({}, []) is None
    cfg = {"story": {"questioner": "ずんだもん"},
           "audio": {"bgm": {"file": "bgm.mp3", "volume": 0.07},
                     "se_volume": 0.5, "se_min_gap": 0.8, "se_lead": 0.15,
                     "se": {"intro": "i.mp3", "outro": "o.mp3",
                            "flash": "f.mp3", "surprise": "s.mp3"}}}
    # 章境界に約1秒の無音を挟んだ並び（start/end付き）。flash/outro SE は前発話末+se_lead に置く。
    script = [
        {"speaker": "四国めたん", "section": "intro", "effect": "kenburns", "emotion": "happy", "start": 0.0, "end": 4.0},
        # 章1: 無音1.0後に声5.0。flash SE = 前末4.0+lead0.15 = 4.15（声より前・無音内）
        {"speaker": "四国めたん", "section": "trivia", "effect": "flash", "emotion": "normal", "start": 5.0, "end": 6.0},
        {"speaker": "四国めたん", "section": "trivia", "effect": "zoom_punch", "emotion": "surprise", "start": 6.0, "end": 6.8},  # 解説役surprise→除外
        {"speaker": "ずんだもん", "section": "trivia", "effect": "kenburns", "emotion": "surprise", "start": 6.8, "end": 7.6},  # 聞き手surprise→発話頭6.8
        # 章2: 無音1.0後に声8.6。flash SE = 前末7.6+0.15 = 7.75
        {"speaker": "四国めたん", "section": "trivia", "effect": "flash", "emotion": "normal", "start": 8.6, "end": 9.6},
        # outro: 無音1.0後。outro SE = 前末9.6+0.15 = 9.75（同時刻のflashより優先）
        {"speaker": "四国めたん", "section": "outro", "effect": "flash", "emotion": "happy", "start": 10.6, "end": 11.2},
    ]
    a = m.build_audio(cfg, script)
    assert a["bgm"]["file"] == "bgm.mp3" and a["se_volume"] == 0.5
    got = [(round(e["t"], 2), e["se"]) for e in a["events"]]
    # SEは発話頭でなく「章境界の無音内(前末+lead)」に前出し。解説役surprise除外。outro章頭はoutro優先。
    assert got == [(0.0, "intro"), (4.15, "flash"), (6.8, "surprise"), (7.75, "flash"), (9.75, "outro")], got
    print("  build_audio: SEを章境界の無音へ前出し/聞き手surprise限定/outro優先 OK")


def test_append_closing_chorus():
    sr = {"script": [{"speaker": "四国めたん", "text": "またね", "chapter": 2, "section": "outro", "cut": 0}]}
    cfg = {"story": {"explainer": "四国めたん", "closing_chorus": "それじゃあまた見てね〜"}}
    m.append_closing_chorus(sr, cfg)
    last = sr["script"][-1]
    assert last["chorus"] is True and last["text"] == "それじゃあまた見てね〜"
    assert last["section"] == "outro" and last["chapter"] == 2, last
    # 冪等: 再実行で二重に足さない
    m.append_closing_chorus(sr, cfg)
    assert sum(1 for t in sr["script"] if t.get("chorus")) == 1, "重複追加しない"
    # 空設定なら何もしない
    sr2 = {"script": [{"speaker": "x", "text": "y", "chapter": 0}]}
    m.append_closing_chorus(sr2, {"story": {}})
    assert len(sr2["script"]) == 1
    print("  append_closing_chorus: ユニゾン締め追加/冪等/空無効 OK")


if __name__ == "__main__":
    print("test_story_meta:")
    test_append_closing_chorus()
    test_build_audio()
    test_build_chapter_topics_coverage()
    test_build_chapter_topics_placeholder()
    test_build_chapter_topics_ready_image_and_credit()
    test_build_credits()
    test_build_meta()
    test_build_meta_cut_anchors()
    test_build_meta_length_mismatch_raises()
    test_write_credits_txt()
    print("ALL PASS")
