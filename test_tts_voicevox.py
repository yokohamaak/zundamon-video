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
    test_generate_audio_end_to_end()
    print("ALL PASS")
