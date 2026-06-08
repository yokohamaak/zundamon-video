"""
apod_script の単体テスト（Gemini呼び出しなし・純関数のみ検証）。

実行: python3 test_apod_script.py
build_prompt（必須要素の埋め込み）と parse_script_json（フェンス/余分テキスト/異常系）を検証する。
"""
from src import apod_script as a


APOD = {
    "title": "Charon: Moon of Pluto",
    "explanation": "A darkened north polar region known as Mordor Macula caps Charon.",
}


def test_build_prompt_contains_essentials():
    config = {"if_dialogue": {"questioner": "ずんだもん", "explainer": "四国めたん", "target_turns": 10}}
    p = a.build_prompt(APOD, config)
    assert APOD["title"] in p, "APODタイトルが埋め込まれる"
    assert "Mordor Macula" in p, "解説本文が埋め込まれる"
    assert "ずんだもん" in p and "四国めたん" in p, "両キャラ名が入る"
    assert "もしも" in p, "ifの企画指示が入る"
    assert "10" in p, "ターン数が反映される"
    assert "script" in p and "topic_title" in p, "出力JSON形式の指定が入る"
    print("  build_prompt: 必須要素OK")


def test_build_prompt_defaults():
    p = a.build_prompt(APOD, {})  # if_dialogue未指定 → デフォルト
    assert "ずんだもん" in p and "四国めたん" in p, "デフォルトのキャラ名"
    print("  build_prompt: デフォルト適用OK")


def test_parse_plain_json():
    text = '{"topic_title":"冥王星の衛星カロン","if_premise":"もしカロンに立ったら","script":[{"speaker":"四国めたん","text":"今日の一枚はね"},{"speaker":"ずんだもん","text":"なんなのだ？"}]}'
    data = a.parse_script_json(text)
    assert data["topic_title"] == "冥王星の衛星カロン"
    assert len(data["script"]) == 2
    print("  parse: 素のJSON OK")


def test_parse_with_code_fence():
    text = '```json\n{"script":[{"speaker":"四国めたん","text":"a"}]}\n```'
    data = a.parse_script_json(text)
    assert data["script"][0]["speaker"] == "四国めたん"
    print("  parse: ```json フェンス除去 OK")


def test_parse_with_leading_text():
    text = 'はい、台本です:\n{"script":[{"speaker":"ずんだもん","text":"なのだ"}]}\nどうぞ'
    data = a.parse_script_json(text)
    assert data["script"][0]["text"] == "なのだ"
    print("  parse: 前後の余分テキストOK")


def test_parse_emotion_required():
    # emotionが付いていれば保持
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y","emotion":"surprise"}]}')
    assert data["script"][0]["emotion"] == "surprise", "正しいemotionは保持"
    # 欠落 → normal 補完（必ず入る）
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y"}]}')
    assert data["script"][0]["emotion"] == "normal", "欠落はnormal補完"
    # 不正値 → normal に倒す
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y","emotion":"excited"}]}')
    assert data["script"][0]["emotion"] == "normal", "不正値はnormalフォールバック"
    print("  parse: emotion必須(補完/不正値フォールバック) OK")


def test_build_prompt_emotion_instruction():
    p = a.build_prompt(APOD, {})
    assert "emotion" in p and "surprise" in p, "感情指示と値が入る"
    print("  build_prompt: emotion指示OK")


def test_parse_phase_effect_required():
    # 正しい値は保持
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y","phase":"if","effect":"zoom_punch"}]}')
    assert data["script"][0]["phase"] == "if" and data["script"][0]["effect"] == "zoom_punch", "正しいphase/effectは保持"
    # 欠落 → デフォルト補完（必ず入る）
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y"}]}')
    assert data["script"][0]["phase"] == "fact", "phase欠落はfact補完"
    assert data["script"][0]["effect"] == "kenburns", "effect欠落はkenburns補完"
    # 不正値 → デフォルトに倒す
    data = a.parse_script_json('{"script":[{"speaker":"x","text":"y","phase":"climax","effect":"explode"}]}')
    assert data["script"][0]["phase"] == "fact" and data["script"][0]["effect"] == "kenburns", "不正値はフォールバック"
    print("  parse: phase/effect必須(補完/不正値フォールバック) OK")


def test_build_prompt_phase_effect_instruction():
    p = a.build_prompt(APOD, {})
    assert "phase" in p and "intro" in p, "phase指示と値が入る"
    assert "effect" in p and "zoom_punch" in p, "effect指示と値が入る"
    print("  build_prompt: phase/effect指示OK")


def test_build_prompt_voice_distinction():
    p = a.build_prompt(APOD, {})
    assert "わたし" in p and ("なのよ" in p or "〜よ" in p), "解説役に固有の女性的語尾を与える"
    assert "専用" in p or "絶対に使わせない" in p, "のだ語尾の禁止ルールが入る"
    print("  build_prompt: 役の語尾区別ルールOK")


def test_warn_role_voice():
    # 解説役がのだ語尾 → 検出
    script = [
        {"speaker": "四国めたん", "text": "これは綺麗な星なのだ。とても明るいのだよ。"},
        {"speaker": "ずんだもん", "text": "そうなのだ？"},
        {"speaker": "四国めたん", "text": "そうよ。よく見えるわね。"},
    ]
    n = a.warn_role_voice(script, "ずんだもん", "四国めたん")
    assert n == 2, f"解説役の のだ/なのだ 2文を検出: {n}"
    # 正常台本 → 0
    ok = [{"speaker": "四国めたん", "text": "綺麗な星よ。明るいわね。"},
          {"speaker": "ずんだもん", "text": "すごいのだ！"}]
    assert a.warn_role_voice(ok, "ずんだもん", "四国めたん") == 0
    print("  warn_role_voice: 解説役の のだ語尾を検出/正常は0 OK")


def test_parse_missing_script_raises():
    for bad in ['{"topic_title":"x"}', '{"script":[]}', '{"script":[{"speaker":"x"}]}']:
        try:
            a.parse_script_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"異常系でValueErrorが出ていない: {bad}")
    print("  parse: 異常系(script無/空/不備)でValueError OK")


if __name__ == "__main__":
    print("test_apod_script:")
    test_build_prompt_contains_essentials()
    test_build_prompt_defaults()
    test_parse_plain_json()
    test_parse_with_code_fence()
    test_parse_with_leading_text()
    test_parse_emotion_required()
    test_build_prompt_emotion_instruction()
    test_parse_phase_effect_required()
    test_build_prompt_phase_effect_instruction()
    test_build_prompt_voice_distinction()
    test_warn_role_voice()
    test_parse_missing_script_raises()
    print("ALL PASS")
