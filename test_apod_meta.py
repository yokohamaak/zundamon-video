"""
main_apod.build_meta / build_credits の単体テスト（yaml/network不要）。

実行: python3 test_apod_meta.py
タイムスタンプ合流・speakers性別割当・topics(画像/全尺)・credits・異常系 を検証する。
"""
import main_apod as m


APOD = {"date": "2026-06-06", "title": "Charon", "explanation": "E", "copyright": "Jane A."}
SCRIPT_RESULT = {
    "topic_title": "冥王星の衛星カロン",
    "if_premise": "もしカロンに立ったら",
    "script": [
        {"speaker": "四国めたん", "text": "今日の一枚はカロンよ"},
        {"speaker": "ずんだもん", "text": "なんなのだ？"},
        {"speaker": "四国めたん", "text": "冥王星の衛星なの"},
    ],
}
TS = [
    {"start": 0.0, "end": 2.0, "sentences": [{"text": "今日の一枚はカロンよ", "start": 0.0, "end": 2.0}]},
    {"start": 2.3, "end": 3.0, "sentences": [{"text": "なんなのだ？", "start": 2.3, "end": 3.0}]},
    {"start": 3.0, "end": 6.5, "sentences": [{"text": "冥王星の衛星なの", "start": 3.0, "end": 6.5}]},
]
CONFIG = {
    "characters_gender": {"四国めたん": "female", "ずんだもん": "male"},
    "tts_voicevox": {"speakers": {"ずんだもん": 3, "四国めたん": 2}},
}


def test_build_meta_basic():
    meta = m.build_meta(APOD, SCRIPT_RESULT, TS, CONFIG, "apod.jpg", "2026-06-06T12:00:00+09:00")
    # タイムスタンプ合流
    assert meta["script"][0]["start"] == 0.0 and meta["script"][0]["end"] == 2.0
    assert meta["script"][2]["end"] == 6.5
    assert all("text" in t and "speaker" in t for t in meta["script"])
    # 字幕単位(sentences)も合流している
    assert meta["script"][0]["sentences"][0]["text"] == "今日の一枚はカロンよ"
    assert all("sentences" in t and t["sentences"] for t in meta["script"])
    # speakers: 登場順・性別マッピング
    assert meta["speakers"] == [
        {"name": "四国めたん", "gender": "female"},
        {"name": "ずんだもん", "gender": "male"},
    ], meta["speakers"]
    # topics: APOD画像が全尺
    topic = meta["topics"][0]
    assert topic["image"] == "apod.jpg"
    assert topic["title"] == "冥王星の衛星カロン"
    assert topic["start"] == 0.0 and topic["end"] == 6.5, "全尺(最終ターンend)に渡る"
    print("  build_meta: ts合流・speakers性別・topics全尺 OK")


def test_build_meta_with_cuts():
    cuts = [
        {"label": "全体", "file": "cut_01.jpg", "box_2d": [0, 0, 1000, 1000]},
        {"label": "中心", "file": "cut_02.jpg", "box_2d": [400, 400, 600, 600]},
    ]
    meta = m.build_meta(APOD, SCRIPT_RESULT, TS, CONFIG, "apod.jpg", "now", cuts=cuts)
    topics = meta["topics"]
    assert len(topics) == 2, "カット数ぶんのtopics"
    assert topics[0]["image"] == "cut_01.jpg" and topics[1]["image"] == "cut_02.jpg"
    # 全尺を隙間なく覆う
    assert topics[0]["start"] == 0.0 and topics[-1]["end"] == 6.5
    assert topics[0]["end"] == topics[1]["start"], "境界一致"
    # labelはtitleに入る（画像ありなので画面には出ない）
    assert topics[0]["title"] == "全体"
    print("  build_meta(cuts): 複数カットtopics/全尺被覆 OK")


def test_build_credits():
    creds = m.build_credits(APOD, CONFIG)
    assert "VOICEVOX:ずんだもん" in creds and "VOICEVOX:四国めたん" in creds, "VOICEVOXクレジット"
    assert any("Jane A." in c for c in creds), "APOD copyrightを反映"
    print("  credits:", creds)


def test_build_credits_public_domain():
    apod_nc = dict(APOD)
    apod_nc["copyright"] = None
    creds = m.build_credits(apod_nc, CONFIG)
    assert any("Public Domain" in c for c in creds), "copyright無しはPublic Domain表記"
    print("  credits(PD): OK")


def test_length_mismatch_raises():
    try:
        m.build_meta(APOD, SCRIPT_RESULT, TS[:2], CONFIG, "apod.jpg", "now")
    except ValueError:
        print("  mismatch: ターン数≠ts数でValueError OK")
        return
    raise AssertionError("不一致でValueErrorが出ていない")


def test_gender_fallback_by_order():
    cfg = {"tts_voicevox": {"speakers": {}}}  # characters_gender無し
    meta = m.build_meta(APOD, SCRIPT_RESULT, TS, cfg, "apod.jpg", "now")
    # 登場順: 0番目=female, 1番目=male
    assert meta["speakers"][0]["gender"] == "female"
    assert meta["speakers"][1]["gender"] == "male"
    print("  gender-fallback: 登場順で割当 OK")


if __name__ == "__main__":
    print("test_apod_meta:")
    test_build_meta_basic()
    test_build_meta_with_cuts()
    test_build_credits()
    test_build_credits_public_domain()
    test_length_mismatch_raises()
    test_gender_fallback_by_order()
    print("ALL PASS")
