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
    sr = {"script": [{"speaker": "四国めたん", "text": "まとめ", "chapter": 2, "section": "outro", "cut": 1}]}
    cfg = {"story": {"explainer": "四国めたん",
                     "closing_lines": [{"speaker": "四国めたん", "text": "高評価お願い", "emotion": "happy"},
                                       {"speaker": "ずんだもん", "text": "登録するのだ"}],
                     "closing_chorus": "それじゃあまた見てね〜"}}
    m.append_closing_chorus(sr, cfg, rotation=0)
    s = sr["script"]
    # 元1 + CTA2 + ユニゾン1 = 4。順序: まとめ→CTA→ユニゾン
    assert [t["text"] for t in s[1:]] == ["高評価お願い", "登録するのだ", "それじゃあまた見てね〜"], s
    assert s[-1].get("chorus") is True, "末尾は二人同時"
    assert all(t.get("closing") for t in s[1:]), "追加分にclosingマーカー"
    assert s[1]["chapter"] == 2 and s[1]["cut"] == 1 and s[1]["section"] == "outro"
    # 冪等: 再実行で二重に足さない（closingマーカーで判定）
    m.append_closing_chorus(sr, cfg, rotation=0)
    assert len(sr["script"]) == 4, "重複追加しない"
    # chorus空・CTAのみでも閊えず、冪等
    sr2 = {"script": [{"speaker": "x", "text": "y", "chapter": 0, "cut": 0}]}
    cfg2 = {"story": {"closing_lines": [{"speaker": "x", "text": "z"}]}}
    m.append_closing_chorus(sr2, cfg2, rotation=0); m.append_closing_chorus(sr2, cfg2, rotation=0)
    assert len(sr2["script"]) == 2 and sr2["script"][-1].get("closing"), "CTAのみでもclosingで冪等"
    # 全部空なら何もしない
    sr3 = {"script": [{"speaker": "x", "text": "y", "chapter": 0}]}
    m.append_closing_chorus(sr3, {"story": {}}, rotation=0)
    assert len(sr3["script"]) == 1
    # 既存の締め(旧マーカー chorus のみ含む)があっても重複させず1つに付け直す
    sr4 = {"script": [{"speaker": "x", "text": "本編", "chapter": 0, "cut": 0},
                      {"speaker": "x", "text": "旧またね", "chapter": 0, "cut": 0, "chorus": True}]}
    m.append_closing_chorus(sr4, cfg, rotation=0)
    assert sr4["script"][0]["text"] == "本編", "本編は残す"
    assert sum(1 for t in sr4["script"] if t.get("chorus")) == 1, "ユニゾンは1つだけ(旧締め除去)"
    assert sr4["script"][-1].get("chorus") and sr4["script"][-1]["text"] == "それじゃあまた見てね〜"
    # Geminiがoutroに書いた定型CTA(チャンネル登録/高評価)は除去し、固定CTAだけにする
    sr5 = {"script": [{"speaker": "x", "text": "まとめ", "chapter": 0, "section": "outro", "cut": 0},
                      {"speaker": "x", "text": "チャンネル登録と高評価よろしく！", "chapter": 0, "section": "outro", "cut": 0}]}
    m.append_closing_chorus(sr5, cfg, rotation=0)
    texts = [t["text"] for t in sr5["script"]]
    assert "チャンネル登録と高評価よろしく！" not in texts, "Gemini生成CTAは除去"
    assert sum(1 for t in sr5["script"] if "登録" in t["text"]) == 1, "登録セリフは固定の1つだけ"
    assert texts[0] == "まとめ", "まとめ(非CTA)は残す"
    print("  append_closing_chorus: CTA＋ユニゾン/重複防止/Gemini-CTA除去/空無効 OK")


def test_select_closing_lines_rotation():
    cfg = {"story": {"closing_lines_pool": [
        [{"speaker": "m", "text": "A1"}, {"speaker": "z", "text": "A2"}],
        [{"speaker": "m", "text": "C1"}],
    ], "closing_lines": [{"speaker": "m", "text": "FB"}]}}
    # 動画ごとに A→C→A… と巡回（rotation=動画数）
    assert [l["text"] for l in m.select_closing_lines(cfg, 0)] == ["A1", "A2"]
    assert [l["text"] for l in m.select_closing_lines(cfg, 1)] == ["C1"]
    assert [l["text"] for l in m.select_closing_lines(cfg, 2)] == ["A1", "A2"]
    # poolが空なら closing_lines（フォールバック）
    assert [l["text"] for l in m.select_closing_lines({"story": {"closing_lines": [{"text": "FB"}]}}, 5)] == ["FB"]
    # append でも巡回が反映（rotation=1 → C）
    sr = {"script": [{"speaker": "x", "text": "本編", "chapter": 0, "section": "outro", "cut": 0}]}
    m.append_closing_chorus(sr, dict(cfg, story=dict(cfg["story"], closing_chorus="ね")), rotation=1)
    assert any(t["text"] == "C1" for t in sr["script"]), "rotation=1でCが入る"
    print("  select_closing_lines: A/C巡回/フォールバック OK")


def test_build_chapter_topics_panel():
    # 解説パネル：章に panel があると、出現時刻を発言timingから解決し各カットtopicに載る。
    chapters = [
        {"section": "intro", "title": "i", "image_cuts": [{"image_query": "a", "image_kind": "ambient"}]},
        {"section": "trivia", "title": "GitHub北極", "image_cuts": [
            {"image_query": "github", "image_kind": "subject"}],
         "panel": {"items": [
             {"text": "全保存"},
             {"text": "北極に埋める", "arrow_from_prev": True},
             {"text": "1000年", "arrow_from_prev": True}]}},
        {"section": "outro", "title": "o", "image_cuts": [{"image_query": "z", "image_kind": "ambient"}]},
    ]
    # 章1のターン: 問い / shrink+item0 / item1 / item2
    script = [
        {"chapter": 0},
        {"chapter": 1, "cut": 0},
        {"chapter": 1, "cut": 0, "panel_event": "shrink", "panel_item": 0},
        {"chapter": 1, "cut": 0, "panel_item": 1},
        {"chapter": 1, "cut": 0, "panel_item": 2},
        {"chapter": 2},
    ]
    # build_meta と同様、台本フィールド(panel_event/panel_item)＋timingを合流して渡す。
    timing = _turns(len(script))  # 2.0秒/ターン
    turns = [{**sc, **ti} for sc, ti in zip(script, timing)]
    segs = story_script.assign_sections_to_turns(script)
    imgfiles = {(1, 0): "ch_01_00.jpg"}
    topics = m.build_chapter_topics(segs, turns, chapters, image_files=imgfiles)
    pts = [t for t in topics if t.get("chapter") == 1 and "panel" in t]
    assert pts, "trivia章にpanel付きtopicがある"
    p = pts[0]["panel"]
    assert p["shrinkAt"] == 4.0, f"shrink=ターン2のstart: {p['shrinkAt']}"
    ats = [it["at"] for it in p["items"]]
    assert ats == [4.0, 6.0, 8.0], f"item出現=各panel_item発言のstart: {ats}"
    assert p["items"][1]["arrow_from_prev"] is True
    assert p["image"] == "ch_01_00.jpg", "panel画像=章主画像に解決"
    # 後方互換: panel無し章には panel を付けない
    assert all("panel" not in t for t in topics if t.get("chapter") != 1)
    print("  build_chapter_topics: panel時刻解決/画像解決/非panel章は無印 OK")


def test_build_chapter_topics_panel_fallback():
    # panel_item 指定が無い場合、shrink後〜章末を均等割りで出現させる（フォールバック）。
    chapters = [
        {"section": "trivia", "title": "T", "image_cuts": [{"image_query": "a", "image_kind": "ambient"}],
         "panel": {"items": [{"text": "x"}, {"text": "y"}]}},
    ]
    script = [{"chapter": 0}, {"chapter": 0}, {"chapter": 0}]
    turns = _turns(3)  # total=6.0、shrink指定なし→章頭(0.0)
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, chapters)
    p = topics[0]["panel"]
    assert p["shrinkAt"] == 0.0, "shrink指定なし→章頭"
    ats = [it["at"] for it in p["items"]]
    assert ats == [2.0, 4.0], f"shrink後〜章末を均等割り: {ats}"
    print("  build_chapter_topics: panel itemフォールバック均等割り OK")


def test_build_chapter_topics_viz():
    # quiz/compare/stat/callouts が発言timing・画像へ解決されtopicに載る。
    chapters = [
        {"section": "trivia", "title": "Q", "image_cuts": [{"image_query": "a", "image_kind": "subject"}],
         "quiz": {"question": "何の略?", "answer": "造語"}},
        {"section": "trivia", "title": "C", "image_cuts": [
            {"image_query": "a", "image_kind": "ambient"}, {"image_query": "b", "image_kind": "ambient"}],
         "compare": {"left": {"label": "陸上", "cut": 0}, "right": {"label": "海底", "cut": 1}}},
        {"section": "trivia", "title": "S", "image_cuts": [{"image_query": "a", "image_kind": "ambient"}],
         "stat": {"value": "500000", "unit": "時間"}},
        {"section": "trivia", "title": "O", "image_cuts": [{"image_query": "a", "image_kind": "subject"}],
         "callouts": [{"text": "ここ", "x": 0.3, "y": 0.4}, {"text": "そこ", "x": 0.7, "y": 0.6}]},
    ]
    script = [
        {"chapter": 0}, {"chapter": 0, "reveal": True},      # quiz: reveal発言でrevealAt
        {"chapter": 1},                                       # compare: 章頭
        {"chapter": 2, "reveal": True},                       # stat: reveal発言
        {"chapter": 3, "callout_item": 0}, {"chapter": 3, "callout_item": 1},
    ]
    timing = _turns(len(script))  # 2.0秒/ターン
    turns = [{**sc, **ti} for sc, ti in zip(script, timing)]
    segs = story_script.assign_sections_to_turns(script)
    imgfiles = {(0, 0): "q.jpg", (1, 0): "l.jpg", (1, 1): "r.jpg", (2, 0): "s.jpg", (3, 0): "c.jpg"}
    topics = m.build_chapter_topics(segs, turns, chapters, image_files=imgfiles)

    def grab(chn, key):
        return next(t[key] for t in topics if t.get("chapter") == chn and key in t)
    q = grab(0, "quiz")
    # クイズは画像を使わない演出＝quizに専用画像は載せない。背後の通常画像(topic.image)をそのまま使う。
    assert q["revealAt"] == 2.0 and "image" not in q, q
    qtopic = next(t for t in topics if t.get("chapter") == 0)
    assert qtopic["image"] == "q.jpg", qtopic  # 通常画像が背景としてそのまま残る
    cmp = grab(1, "compare")
    assert cmp["left"]["image"] == "l.jpg" and cmp["right"]["image"] == "r.jpg"
    # compare_item 指定なし → 最初から2分割（at0==at1==章頭）
    assert cmp["at0"] == cmp["at1"], cmp
    st = grab(2, "stat")
    assert st["showAt"] == 6.0 and st["countTo"] == 500000, st
    co = grab(3, "callouts")
    assert [c["at"] for c in co] == [8.0, 10.0], co
    print("  build_chapter_topics: quiz/compare/stat/callouts 解決 OK")


def test_build_chapter_topics_compare_split_timing():
    # compare_item 0/1 で左右の出現（分割）時刻を制御できる。
    chapters = [{"section": "trivia", "title": "C", "image_cuts": [
        {"image_query": "a", "image_kind": "ambient"}, {"image_query": "b", "image_kind": "ambient"}],
        "compare": {"left": {"label": "俗説", "cut": 0}, "right": {"label": "事実", "cut": 1}}}]
    # ターン0で左、ターン2で右（分割）
    script = [
        {"chapter": 0, "compare_item": 0},
        {"chapter": 0},
        {"chapter": 0, "compare_item": 1},
    ]
    timing = _turns(3)  # 2.0秒/ターン
    turns = [{**sc, **ti} for sc, ti in zip(script, timing)]
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, chapters, image_files={(0, 0): "l.jpg", (0, 1): "r.jpg"})
    cmp = next(t["compare"] for t in topics if "compare" in t)
    assert cmp["at0"] == 0.0 and cmp["at1"] == 4.0, cmp  # 左=章頭, 右=ターン2のstart
    print("  build_chapter_topics: compare 分割タイミング制御 OK")


def test_viz_window_range():
    # viz_start/viz_end で演出の表示範囲を限定。範囲外topicには演出を付けない。
    chapters = [{"section": "trivia", "title": "P", "image_cuts": [
        {"image_query": "a", "image_kind": "ambient"}, {"image_query": "b", "image_kind": "ambient"}],
        "panel": {"items": [{"text": "x"}]}}]
    # cut0=turn0,1 / cut1=turn2,3。viz_startをturn0・viz_endをturn0(end=2.0)→窓[0,2]はcut0のみ
    script = [
        {"chapter": 0, "cut": 0, "viz_start": True, "panel_event": "shrink", "viz_end": True},
        {"chapter": 0, "cut": 0},
        {"chapter": 0, "cut": 1},
        {"chapter": 0, "cut": 1}]
    timing = _turns(4)  # 2.0秒/ターン
    turns = [{**sc, **ti} for sc, ti in zip(script, timing)]
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, chapters, image_files={(0, 0): "c0.jpg", (0, 1): "c1.jpg"})
    assert m._viz_window([0, 1, 2, 3], turns, 0.0, 8.0) == (0.0, 2.0)
    has = [("panel" in t) for t in topics]
    assert has[0] is True and has[1] is False, has  # cut0のみ演出・cut1は通常画像
    print("  _viz_window: 範囲内topicのみ演出・範囲外は通常画像 OK")


def test_viz_window_boundary_snap():
    # 章先頭セリフ起点ならseg_startへ・末尾セリフ起点ならseg_endへスナップ（章頭の元画像チラ見え対策）。
    # 章間に無音があり seg_start(=10.0) < 先頭セリフのstart(=10.5)・seg_end(=20.0) > 末尾セリフのend(=19.5)。
    turns = [
        {"start": 10.5, "end": 13.0, "viz_start": True},
        {"start": 13.0, "end": 16.0},
        {"start": 16.0, "end": 19.5, "viz_end": True},
    ]
    # 先頭/末尾起点 → 章境界へスナップ
    assert m._viz_window([0, 1, 2], turns, 10.0, 20.0) == (10.0, 20.0)
    # 中間セリフ起点はスナップしない（従来どおりセリフ時刻）
    turns2 = [
        {"start": 10.5, "end": 13.0},
        {"start": 13.0, "end": 16.0, "viz_start": True, "viz_end": True},
        {"start": 16.0, "end": 19.5},
    ]
    assert m._viz_window([0, 1, 2], turns2, 10.0, 20.0) == (13.0, 16.0)
    print("  _viz_window: 先頭/末尾起点は章境界へスナップ・中間はセリフ時刻 OK")


def test_build_chapter_topics_viz_reveal_fallback():
    # reveal発言が無いと zoom_punch 発言→章60% の順で revealAt を決める。
    chapters = [{"section": "trivia", "title": "S", "image_cuts": [{"image_query": "a", "image_kind": "ambient"}],
                 "stat": {"value": "8", "unit": "倍"}}]
    # zoom_punch を2番目に置く（reveal指定なし）
    script = [{"chapter": 0}, {"chapter": 0, "effect": "zoom_punch"}, {"chapter": 0}]
    timing = _turns(3)
    turns = [{**sc, **ti} for sc, ti in zip(script, timing)]
    segs = story_script.assign_sections_to_turns(script)
    topics = m.build_chapter_topics(segs, turns, chapters)
    st = next(t["stat"] for t in topics if "stat" in t)
    assert st["showAt"] == 2.0, f"zoom_punch発言のstart: {st['showAt']}"
    assert st["countTo"] == 8, "value=8 は整数なのでカウントアップ到達値が付く"
    print("  build_chapter_topics: reveal無し→zoom_punchで解決 OK")


if __name__ == "__main__":
    print("test_story_meta:")
    test_append_closing_chorus()
    test_select_closing_lines_rotation()
    test_build_audio()
    test_build_chapter_topics_coverage()
    test_build_chapter_topics_placeholder()
    test_build_chapter_topics_ready_image_and_credit()
    test_build_chapter_topics_panel()
    test_build_chapter_topics_panel_fallback()
    test_build_chapter_topics_viz()
    test_build_chapter_topics_compare_split_timing()
    test_viz_window_range()
    test_viz_window_boundary_snap()
    test_build_chapter_topics_viz_reveal_fallback()
    test_build_credits()
    test_build_meta()
    test_build_meta_cut_anchors()
    test_build_meta_length_mismatch_raises()
    test_write_credits_txt()
    print("ALL PASS")
