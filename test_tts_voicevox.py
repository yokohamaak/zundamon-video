"""
tts_voicevox の単体テスト（VOICEVOXエンジン不要・ネットワーク部分はモック）。

実行: python3 test_tts_voicevox.py
文単位合成・字幕単位分割・タイムスタンプ・話者別パラメータ・ターン間無音・未知話者・
generate_audioのe2e(ffmpeg) を検証する。
"""
import io
import wave

from src import tts_voicevox as tv

RATE = 24000
SEC_PER_CHAR = 0.05  # モック合成: 1文字=0.05秒

_last_queries = []  # fake_synthesisが受け取ったqueryを記録


def _make_wav(duration_sec, rate=RATE, channels=1, width=2):
    n = int(duration_sec * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(b"\x11\x22" * n)
    return buf.getvalue()


def _install_fakes():
    _last_queries.clear()

    def fake_audio_query(base_url, text, speaker, timeout=60):
        return {"text": text, "accent_phrases": [], "outputSamplingRate": RATE}

    def fake_synthesis(base_url, query, speaker, timeout=120):
        _last_queries.append(dict(query))
        return _make_wav(max(0.1, len(query["text"]) * SEC_PER_CHAR))

    tv.audio_query = fake_audio_query
    tv.synthesis = fake_synthesis


def _dur(text):
    return max(0.1, len(text) * SEC_PER_CHAR)


def test_sentence_level_captions():
    _install_fakes()
    script = [{"speaker": "四国めたん", "text": "短い文。もう少し長い文だよ。"}]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2}}}
    pcm, turns, params = tv.synthesize_dialogue(script, cfg)

    assert len(turns) == 1
    caps = turns[0]["sentences"]
    assert [c["text"] for c in caps] == ["短い文。", "もう少し長い文だよ。"], caps
    # 連続・単調増加
    assert caps[0]["start"] == 0.0
    assert abs(caps[1]["start"] - caps[0]["end"]) < 1e-6
    # ターンend = 2文の実尺合計
    assert abs(turns[0]["end"] - (_dur("短い文。") + _dur("もう少し長い文だよ。"))) < 1e-3
    print("  sentences: 文ごとに字幕分割・連続タイムスタンプ OK")


def test_long_sentence_split_by_chars():
    _install_fakes()
    # 1文だが長い → 読点で字幕単位に分割し、文の実尺を文字数比で配分
    script = [{"speaker": "四国めたん", "text": "あいうえお、かきくけこ、さしすせそ。"}]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2}, "caption_max_chars": 8}}
    _, turns, _ = tv.synthesize_dialogue(script, cfg)
    caps = turns[0]["sentences"]
    assert len(caps) == 3, [c["text"] for c in caps]
    assert all(len(c["text"]) <= 8 for c in caps), [c["text"] for c in caps]
    # 字幕は隙間なく連続し、合計＝文の実尺
    assert caps[0]["start"] == 0.0
    for a, b in zip(caps, caps[1:]):
        assert abs(b["start"] - a["end"]) < 1e-6
    assert abs(caps[-1]["end"] - turns[0]["end"]) < 1e-6
    print("  long-split: 長文を字幕単位に分割・実尺を配分 OK")


def test_per_speaker_voice_params():
    # 純関数: デフォルト＋話者別上書き
    vc = {
        "speed": 1.0, "pitch": 0.0, "intonation": 1.0,
        "voice_params": {"四国めたん": {"speed": 0.92}, "ずんだもん": {"intonation": 1.3}},
    }
    assert tv._resolve_voice_params(vc, "四国めたん") == {"speed": 0.92, "pitch": 0.0, "intonation": 1.0}
    assert tv._resolve_voice_params(vc, "ずんだもん") == {"speed": 1.0, "pitch": 0.0, "intonation": 1.3}
    assert tv._resolve_voice_params(vc, "知らない人") == {"speed": 1.0, "pitch": 0.0, "intonation": 1.0}

    # synthesisに正しいパラメータが渡るか（queryに反映）
    _install_fakes()
    script = [
        {"speaker": "四国めたん", "text": "ゆっくり。"},
        {"speaker": "ずんだもん", "text": "元気なのだ。"},
    ]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}, **vc}}
    tv.synthesize_dialogue(script, cfg)
    assert _last_queries[0]["speedScale"] == 0.92, "めたんは遅く"
    assert _last_queries[1]["intonationScale"] == 1.3, "ずんだもんは抑揚強め"
    print("  voice-params: 話者別 speed/intonation 反映 OK")


def test_inter_turn_pause():
    _install_fakes()
    script = [
        {"speaker": "四国めたん", "text": "そうね。"},
        {"speaker": "ずんだもん", "text": "なのだ。"},
    ]
    pause = 0.5
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}, "inter_turn_pause": pause}}
    _, turns, params = tv.synthesize_dialogue(script, cfg)
    assert abs(turns[1]["start"] - (turns[0]["end"] + pause)) < 1e-6, "ターン間に無音"
    print("  pause: ターン間無音 反映 OK")


def test_unknown_speaker():
    _install_fakes()
    script = [{"speaker": "謎", "text": "だれ？"}]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2}}}
    try:
        tv.synthesize_dialogue(script, cfg)
    except KeyError as e:
        assert "謎" in str(e)
        print("  unknown: 未知話者でKeyError OK")
        return
    raise AssertionError("未知話者でKeyErrorが出ていない")


def test_sentence_keeps_closing_bracket():
    # 終止符の後の閉じ括弧は同じ文に残る（次の字幕にこぼれない）。
    assert tv._split_sentences("「へぇ！」そうなの。") == ["「へぇ！」", "そうなの。"]
    assert tv._split_sentences("彼は言った（本当だ）。次へ。") == ["彼は言った（本当だ）。", "次へ。"]
    assert tv._split_sentences("すごい！？」ほんと。") == ["すごい！？」", "ほんと。"]
    print("  _split_sentences: 終止符後の閉じ括弧を保持 OK")


def test_reading_gloss_pure():
    # 英字（かな）→ かなだけ（音声用）。読み仮名以外は触らない。
    assert tv._spoken_text("HIFI（ハイファイ）はね") == "ハイファイはね"
    assert tv._spoken_text("Wi-Fi（ワイファイ）") == "ワイファイ"
    assert tv._spoken_text("音響のHIFI（ハイファイ）だ") == "音響のハイファイだ"
    # 括弧内のかなに空白があっても畳む（Wireless Fidelity の二重読み対策）
    assert tv._spoken_text("「Wireless Fidelity（ワイヤレス フィデリティ）」の略") == "「ワイヤレス フィデリティ」の略"
    # 語末の促音「っ」は落とす（囁き化対策）。
    assert tv._spoken_text("そうなんだっ。") == "そうなんだ。"
    assert tv._spoken_text("やっぱりそうか。") == "やっぱりそうか。", "語中の促音は残す"
    # 驚きの「ええ」系（伸ばした形）は正規形に。促音除去と併用。
    assert tv._spoken_text("えぇーっ！？") == "ええ〜！？"
    assert tv._spoken_text("ええーっ！") == "ええ〜！"
    assert tv._spoken_text("えええー！") == "ええ〜！"
    assert tv._spoken_text("え！") == "え！", "単独のえは不変"
    assert tv._spoken_text("えーと、それは") == "えーと、それは", "えー(フィラー)は不変"
    assert tv._spoken_text("ええ、そうです。") == "ええ、そうです。", "肯定のええ(伸ばし無し)は不変"
    # 「へぇ」系は正規形に揃える（小さいぇ/ーの有無に関わらず）。字幕は原文（synthesis側で担保）。
    assert tv._spoken_text("へぇ！すごい") == "へえ〜！すごい"
    assert tv._spoken_text("へぇー！") == "へえ〜！"          # ー付きも対象
    assert tv._spoken_text("へえ。") == "へえ〜。"
    assert tv._spoken_text("へぇっ！") == "へえ〜！"          # 語末促音→へぇ系正規化の併用
    assert tv._spoken_text("頭が「へぇ」でいっぱい") == "頭が「へえ〜」でいっぱい"
    assert tv._spoken_text("へぇって言わせる") == "へぇって言わせる", "感嘆用法でない所は不変"
    # 英字IT用語は辞書でカタカナ読みに（音声専用）。
    assert tv._apply_readings("Hi-Fiの話") == "ハイファイの話"
    assert tv._apply_readings("wifiとWi-Fi") == "ワイファイとワイファイ", "大小無視"
    assert tv._spoken_text("Bluetoothは便利。") == "ブルートゥースは便利。"
    assert tv._apply_readings("Applications") == "Applications", "辞書外/部分一致は不変(境界)"
    # 台本に（かな）読みがあればそれ優先（辞書より先に畳む）
    assert tv._spoken_text("Hi-Fi（ハイファイ）だ") == "ハイファイだ"
    # 読み仮名でないものは不変
    assert tv._spoken_text("そう（笑）") == "そう（笑）"             # 中身が漢字
    assert tv._spoken_text("諸説あり（諸説あり）") == "諸説あり（諸説あり）"  # 漢字含む
    assert tv._spoken_text("（なるほど）") == "（なるほど）"          # 直前が英字でない
    assert tv._spoken_text("Mac (2020)") == "マック (2020)"        # 数字の()は読みに畳まない（Macは辞書で変換）
    print("  _spoken_text: 英字の読み仮名だけ畳む・他は不変 OK")


def test_reading_gloss_in_synthesis():
    # 合成には畳んだテキスト、字幕には原文が使われることを確認。
    _install_fakes()
    script = [{"speaker": "四国めたん", "text": "HIFI（ハイファイ）の話。"}]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2}}}
    _, turns, _ = tv.synthesize_dialogue(script, cfg)
    sent_texts = [q["text"] for q in _last_queries]
    assert sent_texts == ["ハイファイの話。"], sent_texts          # 合成は畳んだ方
    assert turns[0]["sentences"][0]["text"] == "HIFI（ハイファイ）の話。"  # 字幕は原文
    print("  synthesis=畳む / caption=原文 OK")


def test_generate_audio_end_to_end():
    import os
    import shutil
    import tempfile

    if not shutil.which("ffmpeg"):
        print("  e2e: ffmpeg無しのためスキップ")
        return
    _install_fakes()
    script = [
        {"speaker": "四国めたん", "text": "今日の一枚はカロン。とても大きい衛星なの。"},
        {"speaker": "ずんだもん", "text": "すごいのだ。もっと知りたいのだ。"},
    ]
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}}}
    with tempfile.TemporaryDirectory() as d:
        out = f"{d}/out.mp3"
        turns = tv.generate_audio(script, cfg, out)
        assert len(turns) == 2 and all(t["sentences"] for t in turns)
        assert os.path.exists(out) and os.path.getsize(out) > 0
        assert not os.path.exists(out.replace(".mp3", ".wav"))
        print(f"  e2e: mp3生成 {os.path.getsize(out)}B・総尺{turns[-1]['end']:.2f}s OK")


if __name__ == "__main__":
    print("test_tts_voicevox:")
    test_sentence_level_captions()
    test_long_sentence_split_by_chars()
    test_per_speaker_voice_params()
    test_inter_turn_pause()
    test_unknown_speaker()
    test_sentence_keeps_closing_bracket()
    test_reading_gloss_pure()
    test_reading_gloss_in_synthesis()
    test_generate_audio_end_to_end()
    print("ALL PASS")
