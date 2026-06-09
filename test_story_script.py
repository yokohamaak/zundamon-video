"""
story_script の単体テスト（Gemini呼び出しなし・純関数のみ検証）。

実行: python3 test_story_script.py
build_prompt / parse_script_json / _clean_chapters / normalize_turns /
assign_sections_to_turns / warn_role_voice を検証する。
"""
from src import story_script as s


def test_build_prompt_contains_essentials():
    config = {"story": {"theme": "デジタルの名前の謎", "topics": 5,
                        "questioner": "ずんだもん", "explainer": "四国めたん"}}
    p = s.build_prompt(config)
    assert "デジタルの名前の謎" in p, "テーマが埋め込まれる"
    assert "ずんだもん" in p and "四国めたん" in p, "両キャラ名が入る"
    assert "5" in p and "ネタ" in p, "ネタ数が反映される"
    assert "intro" in p and "trivia" in p and "outro" in p, "section種別が入る"
    assert "実は" in p, "実は雑学の型が入る"
    assert "image_cuts" in p and "image_query" in p and "image_kind" in p, "章の画像cut指示が入る"
    assert "subject" in p and "ambient" in p, "image_kindの値が入る"
    assert "chapters" in p and "script" in p, "出力JSON形式の指定が入る"
    print("  build_prompt: 必須要素OK")


def test_build_prompt_theme_auto():
    p = s.build_prompt({})  # story未指定 → テーマ自動選定・デフォルトキャラ
    assert "ずんだもん" in p and "四国めたん" in p, "デフォルトのキャラ名"
    assert "あなたが選んで" in p, "テーマ未指定なら自動選定の指示"
    print("  build_prompt: テーマ自動/デフォルト適用OK")


def test_build_prompt_emotion_effect_instruction():
    p = s.build_prompt({})
    assert "emotion" in p and "surprise" in p, "感情指示と値が入る"
    assert "effect" in p and "flash" in p, "演出指示と値が入る"
    print("  build_prompt: emotion/effect指示OK")


def test_build_prompt_voice_distinction():
    p = s.build_prompt({})
    assert "わたし" in p and ("なのよ" in p or "〜よ" in p), "解説役に固有の女性的語尾を与える"
    assert "専用" in p or "絶対に使わせない" in p, "のだ語尾の禁止ルールが入る"
    print("  build_prompt: 役の語尾区別ルールOK")


def test_parse_plain_json():
    text = ('{"theme":"実は知らないデジタルの名前の謎",'
            '"chapters":[{"section":"trivia","title":"Wi-Fiは略語じゃない","image_cuts":[{"image_query":"wifi symbol","image_kind":"subject"}]}],'
            '"script":[{"speaker":"四国めたん","text":"Wi-Fiって何の略か言える？","chapter":0},'
            '{"speaker":"ずんだもん","text":"わからないのだ？","chapter":0}]}')
    data = s.parse_script_json(text)
    assert data["theme"] == "実は知らないデジタルの名前の謎"
    assert len(data["script"]) == 2
    assert len(data["chapters"]) == 1
    print("  parse: 素のJSON OK")


def test_parse_with_code_fence():
    text = '```json\n{"script":[{"speaker":"四国めたん","text":"a","chapter":0}]}\n```'
    data = s.parse_script_json(text)
    assert data["script"][0]["speaker"] == "四国めたん"
    print("  parse: ```json フェンス除去 OK")


def test_parse_with_leading_text():
    text = 'はい、台本です:\n{"script":[{"speaker":"ずんだもん","text":"なのだ","chapter":0}]}\nどうぞ'
    data = s.parse_script_json(text)
    assert data["script"][0]["text"] == "なのだ"
    print("  parse: 前後の余分テキストOK")


def test_parse_missing_script_raises():
    for bad in ['{"theme":"x"}', '{"script":[]}', '{"script":[{"speaker":"x"}]}']:
        try:
            s.parse_script_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"異常系でValueErrorが出ていない: {bad}")
    print("  parse: 異常系(script無/空/speaker無)でValueError OK")


def test_clean_chapters():
    raw = [
        {"section": "intro", "title": "  はじまり  ", "image_cuts": [
            {"image_query": " wifi symbol ", "image_kind": "subject"},
            {"image_query": "router", "image_kind": "bogus"},   # kind不正→ambient
            {"image_query": "  "},                              # query空→除外
        ]},
        {"section": "bogus", "title": "x"},                     # section不正→trivia / cut無→空query1cut
        "not a dict",                                           # 除外
        {"title": "z", "image_query": "old style", "image_kind": "subject"},  # 旧単数→1cut(後方互換)
    ]
    out = s._clean_chapters(raw)
    assert len(out) == 3, f"dict以外は除外: {len(out)}"
    assert out[0]["title"] == "はじまり", "trim"
    assert len(out[0]["image_cuts"]) == 2, "query空cutは除外"
    assert out[0]["image_cuts"][0]["image_query"] == "wifi symbol", "queryがtrimされる"
    assert out[0]["image_cuts"][0]["image_kind"] == "subject"
    assert out[0]["image_cuts"][1]["image_kind"] == "ambient", "不正kindはambient"
    assert out[1]["section"] == "trivia", "不正sectionはtrivia"
    assert len(out[1]["image_cuts"]) == 1 and out[1]["image_cuts"][0]["image_query"] == "", "cut無しでも最低1cut(空query)"
    assert out[2]["image_cuts"][0]["image_query"] == "old style", "旧単数形式→1cut(後方互換)"
    assert out[2]["image_cuts"][0]["image_kind"] == "subject"
    assert s._clean_chapters(None) == [], "list以外は空"
    print("  _clean_chapters: image_cuts正規化/後方互換/除外 OK")


def test_repair_json_trailing_comma():
    assert s._repair_json('{"a":1,}') == '{"a":1}'
    assert s._repair_json('[1,2,]') == '[1,2]'
    import json as _j
    assert _j.loads(s._repair_json('[1, 2, ]')) == [1, 2]  # 空白入り末尾カンマもパース可
    # 末尾カンマ入りでもパースできる（修復フォールバック）
    d = s.parse_script_json('{"script":[{"speaker":"x","text":"y"},],"chapters":[]}')
    assert len(d["script"]) == 1
    print("  _repair_json: 末尾カンマ修復・パース成功 OK")


def test_strip_markdown():
    assert s.strip_markdown("**実は**すごい") == "実はすごい"
    assert s.strip_markdown("これは*強調*だ") == "これは強調だ"
    assert s.strip_markdown("`code`と~~消~~と__太__") == "codeと消と太"
    assert s.strip_markdown("詳しくは[ここ](http://x)を見て") == "詳しくはここを見て"
    assert s.strip_markdown("## 見出し") == "見出し"
    assert s.strip_markdown("- 箇条書き") == "箇条書き"
    assert s.strip_markdown("壊れた**強調") == "壊れた強調", "孤立した**も除去"
    # 日本語の普通の文は不変
    assert s.strip_markdown("普通の台詞だよ。") == "普通の台詞だよ。"
    print("  strip_markdown: 強調/リンク/見出し/箇条書き/孤立マーカー除去 OK")


def test_normalize_turns_strips_markdown():
    script = [{"speaker": "x", "text": "**実は**ね、すごいの"}]
    s.normalize_turns(script)
    assert script[0]["text"] == "実はね、すごいの", script[0]["text"]
    print("  normalize_turns: 本文のMarkdown除去 OK")


def test_normalize_turns_cut_clamp():
    chapters = [{"section": "intro", "image_cuts": [{"image_query": "q"}]},
                {"section": "trivia", "image_cuts": [{"image_query": "a"}, {"image_query": "b"}]}]
    script = [
        {"speaker": "x", "text": "t", "chapter": 1, "cut": 5},   # 範囲外→1にクランプ
        {"speaker": "x", "text": "t", "chapter": 1, "cut": "0"},  # 文字列→0
        {"speaker": "x", "text": "t", "chapter": 0, "cut": 3},   # 章0はcut1個→0
        {"speaker": "x", "text": "t", "chapter": 1, "cut": "x"},  # 不正→削除
        {"speaker": "x", "text": "t", "chapter": 1},               # 未指定→無し
    ]
    s.normalize_turns(script, chapters)
    assert script[0]["cut"] == 1
    assert script[1]["cut"] == 0
    assert script[2]["cut"] == 0
    assert "cut" not in script[3]
    assert "cut" not in script[4]
    print("  normalize_turns: cut の整数化/範囲クランプ/不正削除 OK")


def test_normalize_turns_enums():
    script = [{"speaker": "x", "text": "y"}]  # emotion/effect/chapter/section 欠落
    s.normalize_turns(script)
    assert script[0]["emotion"] == "normal", "emotion欠落はnormal"
    assert script[0]["effect"] == "kenburns", "effect欠落はkenburns"
    assert script[0]["chapter"] == 0, "chapter欠落は0"
    assert script[0]["section"] == "trivia", "chapters無し・section欠落はtrivia"
    script = [{"speaker": "x", "text": "y", "emotion": "excited", "effect": "explode", "section": "history"}]
    s.normalize_turns(script)
    assert script[0]["emotion"] == "normal" and script[0]["effect"] == "kenburns"
    assert script[0]["section"] == "trivia", "chapters無し・不正sectionはtrivia"
    print("  normalize_turns: emotion/effect/section enum OK")


def test_normalize_turns_chapter_clamp_and_section_from_chapters():
    chapters = [
        {"section": "intro", "title": "a", "image_cuts": [{"image_query": "", "image_kind": "ambient"}]},
        {"section": "outro", "title": "b", "image_cuts": [{"image_query": "", "image_kind": "ambient"}]},
    ]
    script = [
        {"speaker": "x", "text": "1", "chapter": 0, "section": "trivia"},  # sectionはchapters[0]=introで上書き
        {"speaker": "x", "text": "2", "chapter": 5},                       # 範囲外→clampして1
        {"speaker": "x", "text": "3", "chapter": -3},                      # 負→clampして0
        {"speaker": "x", "text": "4", "chapter": "1"},                     # 文字列→int化して1
    ]
    s.normalize_turns(script, chapters)
    assert script[0]["section"] == "intro", "sectionはchapters由来で上書き（proseより構造を信頼）"
    assert script[1]["chapter"] == 1 and script[1]["section"] == "outro", "範囲外はclamp"
    assert script[2]["chapter"] == 0, "負はclamp"
    assert script[3]["chapter"] == 1, "文字列chapterはint化"
    print("  normalize_turns: chapter clamp / section上書き OK")


def test_assign_sections_to_turns():
    script = [
        {"chapter": 0, "section": "intro"},
        {"chapter": 0, "section": "intro"},
        {"chapter": 1, "section": "trivia"},
        {"chapter": 2, "section": "outro"},
        {"chapter": 2, "section": "outro"},
        {"chapter": 2, "section": "outro"},
    ]
    segs = s.assign_sections_to_turns(script)
    assert [seg["chapter"] for seg in segs] == [0, 1, 2], "章順の連続塊"
    assert segs[0]["turns"] == [0, 1], "章0は2ターン"
    assert segs[1]["turns"] == [2], "章1は1ターン"
    assert segs[2]["turns"] == [3, 4, 5], "章2は3ターン"
    assert segs[2]["section"] == "outro"
    covered = [i for seg in segs for i in seg["turns"]]
    assert covered == list(range(len(script))), "全ターンを隙間なく被覆"
    print("  assign_sections_to_turns: 連続塊化/全被覆 OK")


def test_assign_sections_noncontiguous():
    script = [{"chapter": 0}, {"chapter": 1}, {"chapter": 0}]
    segs = s.assign_sections_to_turns(script)
    assert [seg["chapter"] for seg in segs] == [0, 1, 0], "非連続は別セグメント"
    print("  assign_sections_to_turns: 非連続は分割 OK")


def test_warn_role_voice():
    script = [
        {"speaker": "四国めたん", "text": "これは意外な事実なのだ。とても面白いのだよ。"},
        {"speaker": "ずんだもん", "text": "そうなのだ？"},
        {"speaker": "四国めたん", "text": "そうよ。よく出来た話だわね。"},
    ]
    n = s.warn_role_voice(script, "ずんだもん", "四国めたん")
    assert n == 2, f"解説役の のだ/なのだ 2文を検出: {n}"
    ok = [{"speaker": "四国めたん", "text": "意外な事実よ。面白いわ。"},
          {"speaker": "ずんだもん", "text": "すごいのだ！"}]
    assert s.warn_role_voice(ok, "ずんだもん", "四国めたん") == 0
    print("  warn_role_voice: 解説役の のだ語尾を検出/正常は0 OK")


def test_parse_integration_section_from_chapters():
    text = ('{"chapters":[{"section":"intro","title":"a","image_cuts":[{"image_query":"q","image_kind":"ambient"}]},'
            '{"section":"trivia","title":"b","image_cuts":[{"image_query":"r","image_kind":"subject"}]}],'
            '"script":[{"speaker":"四国めたん","text":"x","chapter":0,"section":"outro"},'
            '{"speaker":"ずんだもん","text":"y","chapter":1}]}')
    data = s.parse_script_json(text)
    assert data["script"][0]["section"] == "intro", "chapter0のsectionはintroに上書き"
    assert data["script"][1]["section"] == "trivia", "chapter1のsectionはtrivia"
    print("  parse: chapters由来のsection上書き(統合) OK")


if __name__ == "__main__":
    print("test_story_script:")
    test_build_prompt_contains_essentials()
    test_build_prompt_theme_auto()
    test_build_prompt_emotion_effect_instruction()
    test_build_prompt_voice_distinction()
    test_parse_plain_json()
    test_parse_with_code_fence()
    test_parse_with_leading_text()
    test_parse_missing_script_raises()
    test_clean_chapters()
    test_repair_json_trailing_comma()
    test_strip_markdown()
    test_normalize_turns_strips_markdown()
    test_normalize_turns_cut_clamp()
    test_normalize_turns_enums()
    test_normalize_turns_chapter_clamp_and_section_from_chapters()
    test_assign_sections_to_turns()
    test_assign_sections_noncontiguous()
    test_warn_role_voice()
    test_parse_integration_section_from_chapters()
    print("ALL PASS")
