"""フルパイプライン結合テスト（Gemini/VOICEVOX/network無し・モック）。

parse正規化(story_script) → 音声合成(tts_voicevox・query/timingはモック) → meta組立(main_story)
を一気通貫で回し、新パラメータ(cut/voice/pause/summary/image_query_ja)が端から端まで
正しく流れることを検証する。単体テストでは拾えない“モジュール間の連携崩れ”の回帰防止。

実行: python3 test_pipeline_integration.py
"""
import main_story as M
from src import story_script as ss
from src import tts_voicevox as tv
from test_tts_voicevox import _install_fakes, _last_queries


def test_full_pipeline_mocked():
    raw = {
        "theme": "実は知らないデジタルの名前",
        "chapters": [
            {"section": "intro", "title": "今日のテーマ", "summary": " 名前の意外な由来 ",
             "image_cuts": [{"image_query": "router", "image_kind": "ambient", "image_query_ja": " ルーター "}]},
            {"section": "trivia", "title": "JPEGはグループ名", "summary": "JPEGは開発したグループの名前。",
             "image_cuts": [{"image_query": "jpeg logo", "image_kind": "subject"},
                            {"image_query": "group photo", "image_kind": "ambient"}]},
        ],
        "script": [
            {"speaker": "四国めたん", "text": "はじめるわ。", "chapter": 0, "section": "intro", "cut": 0, "emotion": "happy"},
            {"speaker": "ずんだもん", "text": "やるのだ！", "chapter": 0, "section": "intro", "cut": 0, "voice": {"speed": 1.3, "volume": 1.2}},
            {"speaker": "四国めたん", "text": "JPEGって何の略？", "chapter": 1, "section": "trivia", "cut": 0},
            {"speaker": "ずんだもん", "text": "画像形式なのだ？", "chapter": 1, "section": "trivia", "cut": 0, "pause": 1.0},
            {"speaker": "四国めたん", "text": "実はグループ名なのよ。", "chapter": 1, "section": "trivia", "cut": 1, "emotion": "surprise"},
        ],
    }

    # 1) parse正規化（Geminiパース後の正規化に相当）
    raw["chapters"] = ss._clean_chapters(raw["chapters"])
    ss.normalize_turns(raw["script"], raw["chapters"])
    assert raw["chapters"][0]["summary"] == "名前の意外な由来", "summaryがtrimして通る"
    assert raw["chapters"][0]["image_cuts"][0]["image_query_ja"] == "ルーター", "image_query_ja通る"
    assert raw["script"][1]["voice"] == {"speed": 1.3, "volume": 1.2}, "voice通る"
    assert raw["script"][3]["pause"] == 1.0, "pause通る"
    assert raw["script"][4]["cut"] == 1, "cut通る"

    # 2) 音声合成（query/timingはモック・voice上書きとpauseの効きを確認）
    _install_fakes()
    cfg = {"tts_voicevox": {"speakers": {"四国めたん": 2, "ずんだもん": 3}}}
    _, turns, _ = tv.synthesize_dialogue(raw["script"], cfg)
    assert _last_queries[1]["speedScale"] == 1.3 and _last_queries[1]["volumeScale"] == 1.2, "台詞voiceがqueryに反映"
    # pause=1.0（turn idx3の後）→ idx4の開始が1秒ぶん後ろにずれる
    assert abs((turns[4]["start"] - turns[3]["end"]) - 1.0) < 1e-3, "pauseが尺に反映"

    # 3) meta組立（cutアンカーで章内が区切られる・voice/pauseがscriptに残る）
    meta = M.build_meta(raw, turns,
                        {"characters_gender": {"四国めたん": "female", "ずんだもん": "male"}, **cfg},
                        "2026-06-10T00:00:00+09:00")
    ch1 = [t for t in meta["topics"] if t["chapter"] == 1]
    assert len(ch1) == 2, "章1=cut0(2台詞)+cut1(1台詞)で2topic"
    # cutアンカー: cut1は global turn idx4 で始まる（均等割りなら境界は idx3 になるはず）
    assert ch1[0]["end"] == round(turns[4]["start"], 3), "cut切替は4台詞目の頭(=アンカー)"
    assert ch1[1]["start"] == round(turns[4]["start"], 3)
    # script に voice/pause/cut が保持される
    assert meta["script"][1].get("voice") == {"speed": 1.3, "volume": 1.2}
    assert meta["script"][3].get("pause") == 1.0
    assert meta["script"][4].get("cut") == 1
    # 被覆: 先頭0.0〜末尾=総尺
    assert meta["topics"][0]["start"] == 0.0 and meta["topics"][-1]["end"] == turns[-1]["end"]
    print("  full pipeline: parse→音声(voice/pause)→meta(cutアンカー) 一気通貫 OK")


if __name__ == "__main__":
    print("test_pipeline_integration:")
    test_full_pipeline_mocked()
    print("ALL PASS")
