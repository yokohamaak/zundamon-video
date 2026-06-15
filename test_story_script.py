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
    assert "切り口" in p and "由来" in p, "テーマの切り口厳守・名前由来ネタ逃げ禁止の指示が入る"
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
    # summary（章の要約）と image_query_ja（検索語の日本語）を通す
    o2 = s._clean_chapters([{"section": "trivia", "title": "T", "summary": " 要約だよ ",
                             "image_cuts": [{"image_query": "radio", "image_kind": "ambient", "image_query_ja": " ラジオ "}]}])
    assert o2[0]["summary"] == "要約だよ", "summaryをtrimして通す"
    assert o2[0]["image_cuts"][0]["image_query_ja"] == "ラジオ", "image_query_jaをtrimして通す"
    # image_query_ja が空なら付けない
    o3 = s._clean_chapters([{"section": "trivia", "title": "T", "summary": "",
                             "image_cuts": [{"image_query": "x", "image_kind": "subject"}]}])
    assert "image_query_ja" not in o3[0]["image_cuts"][0], "空のjaは省く"
    assert o3[0]["summary"] == "", "summary無しは空文字"
    print("  _clean_chapters: image_cuts正規化/後方互換/除外/summary/ja OK")


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


def test_normalize_voice_and_pause():
    # voice: 数値化＋範囲クランプ、有効キー無し/不正は削除
    script = [
        {"speaker": "x", "text": "t", "voice": {"speed": 3.0, "pitch": -0.5, "intonation": 1.4, "volume": "1.2"}},
        {"speaker": "x", "text": "t", "voice": {"bogus": 1, "speed": "no"}},  # 有効値0→削除
        {"speaker": "x", "text": "t", "voice": "nope"},                       # dict以外→削除
        {"speaker": "x", "text": "t", "pause": 5},                            # 0..2にクランプ
        {"speaker": "x", "text": "t", "pause": 0},                            # 0→削除
        {"speaker": "x", "text": "t", "pause": "x"},                          # 不正→削除
    ]
    s.normalize_turns(script)
    assert script[0]["voice"] == {"speed": 2.0, "pitch": -0.15, "intonation": 1.4, "volume": 1.2}, script[0]["voice"]
    assert "voice" not in script[1]
    assert "voice" not in script[2]
    assert script[3]["pause"] == 2.0
    assert "pause" not in script[4]
    assert "pause" not in script[5]
    print("  normalize_turns: voice/pause クランプ・不正削除 OK")


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


def test_build_regen_prompt():
    cfg = {"story": {"questioner": "ずんだもん", "explainer": "四国めたん"}}
    facts = [{"title": "Wi-Fiは略語じゃない", "summary": "造語"},
             {"title": "Bluetoothの由来", "summary": "王の名"}]
    p = s.build_regen_prompt(cfg, "デジタルの名前の謎", facts, 2)
    assert "デジタルの名前の謎" in p, "テーマ固定が入る"
    assert "Wi-Fiは略語じゃない" in p and "Bluetoothの由来" in p, "既出ネタが重複回避に渡る"
    assert "重複" in p and "intro / outro は出さない" in p, "再生成の制約が入る"
    assert "ずんだもん" in p and "四国めたん" in p, "キャラ名（共有ルール）が入る"
    assert "image_query" in p and "chapters" in p, "共有の出力形式が入る"
    print("  build_regen_prompt: テーマ固定/既出回避/共有ルール OK")


def test_regenerate_ignores_intro_outro():
    # Geminiが指示に反して intro/outro を混ぜて返しても、trivia だけを正しく拾うこと（回帰）。
    cfg = {"story": {"questioner": "ずんだもん", "explainer": "四国めたん"}}
    script_result = {"theme": "T", "chapters": [
        {"section": "intro", "title": "i"},
        {"section": "trivia", "title": "旧1", "summary": "o1"},
        {"section": "trivia", "title": "旧2", "summary": "o2"},
        {"section": "outro", "title": "e"}],
        "script": []}
    # 返答: intro(0) + trivia(1) + trivia(2) + outro(3) を含む（混入ケース）
    fake = {"theme": "T", "chapters": [
        {"section": "intro", "title": "導入も作っちゃった", "image_cuts": [{"image_query": "x", "image_kind": "ambient"}]},
        {"section": "trivia", "title": "新A", "summary": "na", "image_cuts": [{"image_query": "a", "image_kind": "ambient"}]},
        {"section": "trivia", "title": "新B", "summary": "nb", "image_cuts": [{"image_query": "b", "image_kind": "ambient"}]},
        {"section": "outro", "title": "締めも作っちゃった", "image_cuts": [{"image_query": "y", "image_kind": "ambient"}]}],
        "script": [
        {"chapter": 0, "section": "intro", "speaker": "四国めたん", "text": "イントロ挨拶", "cut": 0},
        {"chapter": 1, "section": "trivia", "speaker": "四国めたん", "text": "新Aの話", "cut": 0},
        {"chapter": 2, "section": "trivia", "speaker": "四国めたん", "text": "新Bの話", "cut": 0},
        {"chapter": 3, "section": "outro", "speaker": "四国めたん", "text": "締めの言葉", "cut": 0}]}
    orig = s._generate_parsed
    s._generate_parsed = lambda c, p, log_label="": fake
    try:
        regen = s.regenerate_chapters(cfg, script_result, [1, 2])  # 章1,2を再生成
    finally:
        s._generate_parsed = orig
    # 章1→新A・章2→新B（intro/outroは混ざらない）
    assert regen["chapters"][1]["title"] == "新A" and regen["chapters"][2]["title"] == "新B", regen["chapters"]
    t1 = [t["text"] for t in regen["turns"][1]]
    t2 = [t["text"] for t in regen["turns"][2]]
    assert t1 == ["新Aの話"] and t2 == ["新Bの話"], (t1, t2)
    # intro/outro のセリフが混入していないこと
    allt = t1 + t2
    assert "イントロ挨拶" not in allt and "締めの言葉" not in allt, allt
    assert all(t["section"] == "trivia" for t in regen["turns"][1] + regen["turns"][2])
    print("  regenerate_chapters: intro/outro混入を除外しtriviaのみ抽出 OK")


def test_is_daily_quota():
    perday = ("429 RESOURCE_EXHAUSTED quota exceeded. violations quotaId: "
              "GenerateContentRequestsPerDayPerProjectPerModel retryDelay: 32s")
    permin = ("429 RESOURCE_EXHAUSTED quota exceeded. quotaId: "
              "GenerateRequestsPerMinutePerProjectPerModel retryDelay: 20s")
    assert s._is_daily_quota(perday, 32) is True, "日次は即フォールバック"
    assert s._is_daily_quota(permin, 20) is False, "分次は待ってリトライ"
    assert s._is_daily_quota("503 UNAVAILABLE overloaded") is False, "503はクォータでない"
    assert s._is_daily_quota("some quota error", 600) is True, "長い待機指示は日次相当"
    assert s._is_daily_quota("connection reset") is False
    print("  _is_daily_quota: 日次/分次/503/長待機 の判別 OK")


def test_daily_quota_immediate_fallback():
    # 日次クォータエラーは sleep せず即 raise（呼び出し側が次モデルへ）。分次は待ってリトライ。
    import time as _t
    orig_sleep = _t.sleep
    slept = []
    _t.sleep = lambda x: slept.append(x)

    class FakeModels:
        def __init__(self, exc):
            self.exc = exc
            self.calls = 0

        def generate_content(self, model, contents):
            self.calls += 1
            raise self.exc

    class FakeClient:
        def __init__(self, exc):
            self.models = FakeModels(exc)
    try:
        # 日次: 1回で即raise・sleepなし
        c1 = FakeClient(Exception("429 RESOURCE_EXHAUSTED quotaId: RequestsPerDay retryDelay: 30s"))
        raised = False
        try:
            s._generate_with_retry(c1, "m", "p", max_attempts=3)
        except Exception:
            raised = True
        assert raised and c1.models.calls == 1 and slept == [], (c1.models.calls, slept)
        # 分次: max_attempts まで再試行（sleepあり）
        slept.clear()
        c2 = FakeClient(Exception("429 RESOURCE_EXHAUSTED quotaId: RequestsPerMinute retryDelay: 20s"))
        try:
            s._generate_with_retry(c2, "m", "p", max_attempts=2)
        except Exception:
            pass
        assert c2.models.calls == 2 and len(slept) == 1, (c2.models.calls, slept)
    finally:
        _t.sleep = orig_sleep
    print("  _generate_with_retry: 日次=即raise / 分次=待って再試行 OK")


def test_build_prompt_also_avoid():
    cfg = {"story": {"theme": "X", "topics": 5, "questioner": "ずんだもん", "explainer": "四国めたん"}}
    # 指定なし＝既出ネタ節は出ない（従来通り）
    assert "既出ネタ（重複禁止" not in s.build_prompt(cfg)
    # 指定あり＝過去ネタが重複禁止に入る
    p = s.build_prompt(cfg, also_avoid=[{"title": "過去ネタZ", "summary": "z"}])
    assert "既出ネタ（重複禁止" in p and "過去ネタZ" in p
    print("  build_prompt: also_avoid指定時のみ既出ネタ節 OK")


def test_regenerate_uses_also_avoid():
    # also_avoid（過去に却下したネタ）が重複回避リストとしてプロンプトに渡ること。
    cfg = {"story": {"questioner": "ずんだもん", "explainer": "四国めたん"}}
    script_result = {"theme": "T", "chapters": [
        {"section": "trivia", "title": "現存ネタ", "summary": "now"}], "script": []}
    captured = {}

    def fake_gen(c, p, log_label=""):
        captured["prompt"] = p
        return {"theme": "T",
                "chapters": [{"section": "trivia", "title": "新", "summary": "n",
                              "image_cuts": [{"image_query": "x", "image_kind": "ambient"}]}],
                "script": [{"chapter": 0, "section": "trivia", "speaker": "四国めたん", "text": "新A", "cut": 0}]}
    orig = s._generate_parsed
    s._generate_parsed = fake_gen
    try:
        s.regenerate_chapters(cfg, script_result, [0],
                              also_avoid=[{"title": "却下したネタ", "summary": "rej"}])
    finally:
        s._generate_parsed = orig
    p = captured["prompt"]
    assert "現存ネタ" in p and "却下したネタ" in p, "現存＋却下の両方が重複禁止に入る"
    print("  regenerate_chapters: also_avoid(却下履歴)を重複回避に渡す OK")


def test_splice_regenerated():
    sr = {"theme": "t", "chapters": [
        {"section": "intro", "title": "i"},
        {"section": "trivia", "title": "old1", "summary": "o1"},
        {"section": "trivia", "title": "old2", "summary": "o2"},
        {"section": "outro", "title": "e"}],
        "script": [
        {"chapter": 0, "section": "intro", "text": "導入", "speaker": "四国めたん"},
        {"chapter": 1, "section": "trivia", "text": "旧1A", "speaker": "四国めたん"},
        {"chapter": 1, "section": "trivia", "text": "旧1B", "speaker": "ずんだもん"},
        {"chapter": 2, "section": "trivia", "text": "旧2A", "speaker": "四国めたん"},
        {"chapter": 3, "section": "outro", "text": "締め", "speaker": "四国めたん"}]}
    regen = {"chapters": {1: {"section": "trivia", "title": "new1", "summary": "n1",
                              "image_cuts": [{"image_query": "x", "image_kind": "ambient"}]}},
             "turns": {1: [{"chapter": 1, "section": "trivia", "text": "新1A", "speaker": "四国めたん", "cut": 0},
                           {"chapter": 1, "section": "trivia", "text": "新1B", "speaker": "ずんだもん", "cut": 0}]}}
    s.splice_regenerated(sr, regen)
    assert sr["chapters"][1]["title"] == "new1", "章メタ差し替え"
    assert [t["text"] for t in sr["script"]] == ["導入", "新1A", "新1B", "旧2A", "締め"], "章1だけ置換・順序維持"
    assert [t["chapter"] for t in sr["script"]] == [0, 1, 1, 2, 3], "章番号は不変"
    print("  splice_regenerated: 指定章のみ置換・順序/章番号維持 OK")


def test_select_theme():
    # 固定theme優先
    assert s.select_theme({"story": {"theme": "固定", "theme_pool": ["a", "b"]}}, ["a"]) == "固定"
    # poolから未使用を先頭順に
    cfg = {"story": {"theme": "", "theme_pool": ["a", "b", "c"]}}
    assert s.select_theme(cfg, ["a"]) == "b", "使用済みaを飛ばしb"
    assert s.select_theme(cfg, []) == "a", "未使用は先頭"
    # 全部使用済み→最後に使われた位置が最も古いもの（巡回）
    assert s.select_theme(cfg, ["b", "c", "a"]) == "b", "最も昔に使ったbへ巡回"
    # poolもthemeも無ければ空（Gemini自動）
    assert s.select_theme({"story": {"theme": ""}}, []) == ""
    print("  select_theme: 固定>プール巡回>自動 OK")


def test_strip_redundant_kana_gloss():
    assert s.strip_redundant_kana_gloss("ロバート・メトカーフ（ロバート・メトカーフ）博士") == "ロバート・メトカーフ博士"
    assert s.strip_redundant_kana_gloss("イーサネット（イーサネット）") == "イーサネット"
    # 英字（かな）グロスは残す
    assert s.strip_redundant_kana_gloss("USB（ユーエスビー）") == "USB（ユーエスビー）"
    # ひらがな読みは残す
    assert s.strip_redundant_kana_gloss("ハーラル1世（いちせい）") == "ハーラル1世（いちせい）"
    # 別語の補足カタカナは残す
    assert s.strip_redundant_kana_gloss("ブルー（レッド）") == "ブルー（レッド）"
    # normalize_turns 経由でも効く
    sc = [{"text": "ロバート・メトカーフ（ロバート・メトカーフ）博士", "chapter": 0}]
    s.normalize_turns(sc)
    assert sc[0]["text"] == "ロバート・メトカーフ博士", "normalize_turnsで除去"
    print("  strip_redundant_kana_gloss: 冗長カナ読み除去/グロス温存 OK")


def test_confidence_source_hint():
    # build_prompt に事実の確度の指示が入る
    config = {"story": {"theme": "T", "topics": 6,
                        "questioner": "ずんだもん", "explainer": "四国めたん"}}
    p = s.build_prompt(config)
    for kw in ["事実の確度", "confidence", "source_hint"]:
        assert kw in p, f"build_prompt に {kw} が入る"
    # 運営者コメント機能は削除済み（プロンプトに出ない）
    assert "運営者コメント" not in p and "owner_comment" not in p
    # _clean_chapters が confidence/source_hint を保持する
    data = s.parse_script_json(
        '{"chapters":[{"section":"trivia","title":"A","summary":"s",'
        '"confidence":"medium","source_hint":"hint",'
        '"image_cuts":[{"image_query":"q","image_kind":"subject"}]}],'
        '"script":[{"speaker":"四国めたん","text":"hi","chapter":0}]}')
    ch = data["chapters"][0]
    assert ch["confidence"] == "medium" and ch["source_hint"] == "hint"
    # owner_comment は保持しない（機能削除）
    data2 = s.parse_script_json(
        '{"chapters":[{"section":"trivia","title":"A","confidence":"bogus",'
        '"owner_comment":true,"image_cuts":[{"image_query":"q","image_kind":"subject"}]}],'
        '"script":[{"speaker":"x","text":"hi"}]}')
    assert "confidence" not in data2["chapters"][0]
    assert "owner_comment" not in data2["chapters"][0]
    print("  confidence/source_hint: 指示と保持（owner_comment削除）OK")


def test_build_prompt_panel_guidance():
    # 台本生成プロンプトに画像エリア演出の指示（5種・使いどころ・フィールド）が入る。
    p = s.build_prompt({"story": {"theme": "T", "topics": 6,
                                  "questioner": "ずんだもん", "explainer": "四国めたん"}})
    for kw in ["## 画像エリアの演出", "panel_event", "panel_item", "arrow_from_prev",
               "体言止め", "quiz", "compare", "stat", "callouts", "reveal", "callout_item",
               "1章につき多くても1種類"]:
        assert kw in p, f"演出指示に {kw} が入る"
    print("  build_prompt: 画像エリア演出の指示 OK")


def test_panel_fields_preserved():
    # 章の panel と、発言の panel_event/panel_item がパース/正規化を生き残る。
    data = s.parse_script_json(
        '{"chapters":[{"section":"trivia","title":"A",'
        '"panel":{"bg":"#102030","bgOpacity":0.6,"cut":2,"image":"x.jpg",'
        '"markerType":"square","markerColor":"#00ff00","markerSize":1.3,"textColor":"#ffeeaa","textSize":0.9,"items":['
        '{"text":"全保存"},{"text":"北極","arrow_from_prev":true}]},'
        '"image_cuts":[{"image_query":"q","image_kind":"subject"}]}],'
        '"script":[{"speaker":"四国めたん","text":"x","chapter":0,"panel_event":"shrink","panel_item":0},'
        '{"speaker":"ずんだもん","text":"y","chapter":0,"panel_item":1}]}')
    pn = data["chapters"][0]["panel"]
    # 画像はセリフ毎＝panelはimage/cutを持たない（bgとitemsのみ）
    assert "image" not in pn and "cut" not in pn
    assert pn["bg"] == "#102030" and pn["bgOpacity"] == 0.6 and len(pn["items"]) == 2
    assert pn["items"][1]["arrow_from_prev"] is True
    assert pn["markerType"] == "square" and pn["markerColor"] == "#00ff00" and pn["markerSize"] == 1.3
    assert pn["textColor"] == "#ffeeaa" and pn["textSize"] == 0.9
    assert data["script"][0]["panel_event"] == "shrink"
    assert data["script"][0]["panel_item"] == 0 and data["script"][1]["panel_item"] == 1
    # 不正値は落とす: panel_event非shrink・panel_item非整数・items空panel
    d2 = s.parse_script_json(
        '{"chapters":[{"section":"trivia","title":"A","panel":{"items":[]},'
        '"image_cuts":[{"image_query":"q","image_kind":"subject"}]}],'
        '"script":[{"speaker":"x","text":"y","panel_event":"wiggle","panel_item":"NaN"}]}')
    assert "panel" not in d2["chapters"][0], "items空のpanelは付けない"
    assert "panel_event" not in d2["script"][0] and "panel_item" not in d2["script"][0]
    print("  panel / panel_event / panel_item: 保持と不正値除去 OK")


def test_viz_fields_preserved():
    # quiz/compare/stat/callouts と reveal/callout_item がパースを生き残る。
    data = s.parse_script_json(
        '{"chapters":['
        '{"section":"trivia","title":"Q","quiz":{"question":"何の略?","answer":"造語","bg":"#1a2333","bgOpacity":0.55,"textColor":"#eeeeff","answerBg":"#ffcc00","answerBgOpacity":0.8,"answerTextColor":"#222222"},'
        '"image_cuts":[{"image_query":"q","image_kind":"subject"}]},'
        '{"section":"trivia","title":"C","compare":{"left":{"label":"陸上"},"right":{"label":"海底","cut":1},"labelColor":"#101820","labelTextColor":"#ffff00","labelSize":1.2,"dividerColor":"#ff0000"},'
        '"image_cuts":[{"image_query":"a","image_kind":"ambient"},{"image_query":"b","image_kind":"ambient"}]},'
        '{"section":"trivia","title":"S","stat":{"value":"8","unit":"分の1","label":"故障率","color":"#ff5050","size":1.4,"bg":"#101820","bgOpacity":0.3,"countSpeed":"slow"},'
        '"image_cuts":[{"image_query":"a","image_kind":"ambient"}]},'
        '{"section":"trivia","title":"O","callouts":[{"text":"ここ","x":0.3,"y":0.4,"arrow":true,"lx":0.6,"ly":0.15},{"text":"範囲外","x":2,"y":0.5}],'
        '"calloutStyle":{"markerColor":"#00ccff","markerSize":1.5,"labelColor":"#222a33","labelTextColor":"#ffee00","labelBorderColor":"#ffffff","labelSize":0.8,"arrowSize":2.0,"arrowShape":"sharp"},'
        '"image_cuts":[{"image_query":"a","image_kind":"subject"}]}'
        '],"script":['
        '{"speaker":"四国めたん","text":"a","chapter":0,"reveal":true},'
        '{"speaker":"四国めたん","text":"b","chapter":3,"callout_item":0}]}')
    ch = data["chapters"]
    assert ch[0]["quiz"] == {"question": "何の略?", "answer": "造語", "bg": "#1a2333", "bgOpacity": 0.55,
                             "textColor": "#eeeeff", "answerBg": "#ffcc00", "answerBgOpacity": 0.8,
                             "answerTextColor": "#222222"}
    assert ch[1]["compare"]["left"]["cut"] == 0 and ch[1]["compare"]["right"]["cut"] == 1
    assert ch[1]["compare"]["labelColor"] == "#101820" and ch[1]["compare"]["labelTextColor"] == "#ffff00"
    assert ch[1]["compare"]["labelSize"] == 1.2 and ch[1]["compare"]["dividerColor"] == "#ff0000"
    assert ch[2]["stat"] == {"value": "8", "unit": "分の1", "label": "故障率",
                             "color": "#ff5050", "size": 1.4, "bg": "#101820", "bgOpacity": 0.3,
                             "countSpeed": "slow"}
    # x>1 の範囲外注釈は除去され、正しい1件だけ残る
    assert len(ch[3]["callouts"]) == 1 and ch[3]["callouts"][0]["arrow"] is True
    # ラベル位置 lx/ly が保持される
    assert ch[3]["callouts"][0]["lx"] == 0.6 and ch[3]["callouts"][0]["ly"] == 0.15
    assert ch[3]["calloutStyle"] == {"markerColor": "#00ccff", "markerSize": 1.5,
                                     "labelColor": "#222a33", "labelTextColor": "#ffee00",
                                     "labelBorderColor": "#ffffff", "labelSize": 0.8,
                                     "arrowSize": 2.0, "arrowShape": "sharp"}
    assert data["script"][0]["reveal"] is True and data["script"][1]["callout_item"] == 0
    # 必須欠落は None 化（quiz answer 無し / compare label 無し / stat value 無し）
    d2 = s.parse_script_json(
        '{"chapters":[{"section":"trivia","title":"X","quiz":{"question":"q"},'
        '"compare":{"left":{"label":"A"}},"stat":{"unit":"倍"},'
        '"image_cuts":[{"image_query":"q","image_kind":"subject"}]}],'
        '"script":[{"speaker":"x","text":"y"}]}')
    assert "quiz" not in d2["chapters"][0] and "compare" not in d2["chapters"][0]
    assert "stat" not in d2["chapters"][0]
    print("  quiz / compare / stat / callouts / reveal / callout_item: 保持と不正値除去 OK")


if __name__ == "__main__":
    print("test_story_script:")
    test_select_theme()
    test_strip_redundant_kana_gloss()
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
    test_normalize_voice_and_pause()
    test_normalize_turns_enums()
    test_normalize_turns_chapter_clamp_and_section_from_chapters()
    test_assign_sections_to_turns()
    test_assign_sections_noncontiguous()
    test_warn_role_voice()
    test_parse_integration_section_from_chapters()
    test_build_regen_prompt()
    test_is_daily_quota()
    test_daily_quota_immediate_fallback()
    test_build_prompt_also_avoid()
    test_regenerate_ignores_intro_outro()
    test_regenerate_uses_also_avoid()
    test_splice_regenerated()
    test_confidence_source_hint()
    test_build_prompt_panel_guidance()
    test_panel_fields_preserved()
    test_viz_fields_preserved()
    print("ALL PASS")
