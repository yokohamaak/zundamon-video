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
        "theme": "なぜGitは世界を変えたのか",
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
    assert meta["title"] == "なぜGitは世界を変えたのか", "title=theme"
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


if __name__ == "__main__":
    print("test_story_meta:")
    test_build_chapter_topics_coverage()
    test_build_chapter_topics_placeholder()
    test_build_chapter_topics_ready_image_and_credit()
    test_build_credits()
    test_build_meta()
    test_build_meta_length_mismatch_raises()
    test_write_credits_txt()
    print("ALL PASS")
