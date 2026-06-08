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
    {"section": "intro", "title": "はじまり", "image_query": "git logo", "image_kind": "subject"},
    {"section": "turning_point", "title": "誕生", "image_query": "Linus Torvalds", "image_kind": "subject"},
    {"section": "outro", "title": "まとめ", "image_query": "source code", "image_kind": "ambient"},
]


def test_build_chapter_topics_coverage():
    script = [{"chapter": 0}, {"chapter": 0}, {"chapter": 1}, {"chapter": 2}, {"chapter": 2}]
    turns = _turns(len(script))
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, CHAPTERS)
    assert len(topics) == 3, "3章=3topic"
    assert topics[0]["start"] == 0.0, "先頭は0始まり"
    assert topics[-1]["end"] == turns[-1]["end"], "末尾はtotalで終わる"
    # 隙間なく連結
    for a, b in zip(topics, topics[1:]):
        assert a["end"] == b["start"], f"隙間/重なりなし: {a['end']} != {b['start']}"
    # 章メタ反映
    assert topics[0]["title"] == "はじまり" and topics[0]["section"] == "intro"
    assert topics[0]["chapter"] == 0 and topics[0]["chapterTotal"] == 3
    print("  build_chapter_topics: [0,total]被覆/章メタ反映 OK")


def test_build_chapter_topics_placeholder():
    script = [{"chapter": 0}, {"chapter": 1}]
    turns = _turns(2)
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, CHAPTERS)  # image_status空＝全プレースホルダ
    for t in topics:
        assert "image" not in t, "未取得はimage無し"
        assert t["placeholder"].startswith("ch_") and t["placeholder"].endswith(".png"), "決め打ち名のplaceholder"
        assert t["note"], "差し替え案内(note)が入る"
    assert topics[0]["placeholder"] == "ch_00.png"
    assert topics[1]["placeholder"] == "ch_01.png"
    print("  build_chapter_topics: プレースホルダ枠 OK")


def test_build_chapter_topics_ready_image_and_credit():
    script = [{"chapter": 0}, {"chapter": 1}]
    turns = _turns(2)
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(
        segs, turns, CHAPTERS,
        image_status={0: "ready"},
        attributions={0: "Linus Torvalds / CC-BY-3.0"},
    )
    assert topics[0]["image"] == "ch_00.png", "ready章はimage"
    assert topics[0]["credit"] == "Linus Torvalds / CC-BY-3.0", "帰属が付く"
    assert "image" not in topics[1] and topics[1]["placeholder"] == "ch_01.png", "未取得章はプレースホルダ"
    print("  build_chapter_topics: ready画像+credit / 混在 OK")


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
            {"speaker": "四国めたん", "text": "c", "chapter": 1, "section": "turning_point", "emotion": "normal", "effect": "flash"},
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
    # topics: 章数分・被覆
    assert len(meta["topics"]) == 2, "2章（chapter0が2ターン+chapter1）"
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


if __name__ == "__main__":
    print("test_story_meta:")
    test_build_chapter_topics_coverage()
    test_build_chapter_topics_placeholder()
    test_build_chapter_topics_ready_image_and_credit()
    test_build_credits()
    test_build_meta()
    test_build_meta_length_mismatch_raises()
    print("ALL PASS")
