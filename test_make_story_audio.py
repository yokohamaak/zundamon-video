"""make_story_audio のプロファイル解決まわりの回帰テスト。"""
import json
import tempfile

import make_story_audio as msa


def test_load_voice_profiles_includes_troublemakers():
    profiles = msa.load_voice_profiles()
    for name in (
        "troublemaker_male_normal",
        "troublemaker_male_creepy",
        "troublemaker_female_normal",
        "troublemaker_female_creepy",
    ):
        assert name in profiles, name
        assert profiles[name]["engine"] == "voicevox"
        assert "speaker" in profiles[name]
        assert "params" in profiles[name]
    print("  profiles: troublemaker男女4種を読める OK")


def test_build_script_turns_uses_profile_fx_and_narration_defaults():
    profiles = msa.load_voice_profiles()
    data = {
        "script": [
            {"speaker": "troublemaker_male_normal", "text": "確認。"},
            {"speaker": "zundamon", "text": "読み上げ。", "narrationVoice": "棒読み男"},
        ]
    }
    script = msa.build_script_turns(data, profiles)
    assert script[0]["speaker"] == "troublemaker_male_normal"
    assert script[0]["audioFx"]["lowpass"] == 7200
    assert script[1]["speaker"] == "棒読み男"
    assert "audioFx" not in script[1]
    print("  script-turns: speaker名→プロファイル/fx を解決 OK")


def test_build_script_turns_resolves_v2_instance_voice_id():
    profiles = msa.load_voice_profiles()
    data = {
        "schemaVersion": 2,
        "instances": {
            "boss": {"voiceId": "部長", "role": "stage", "characterId": "boss_mob"},
            "narrator": {"voiceId": "棒読み男", "role": "voiceOnly"},
        },
        "script": [
            {"speaker": "boss", "text": "報告して。", "scene": "office"},
            {"speaker": "narrator", "text": "その後。", "scene": "office"},
        ],
    }
    script = msa.build_script_turns(data, profiles)
    assert [item["speaker"] for item in script] == ["部長", "棒読み男"]
    print("  script-turns: v2個体IDをvoiceIdへ解決 OK")


def test_build_tts_config_maps_profiles_to_speakers():
    profiles = {
        "troublemaker_male_normal": {
            "engine": "voicevox",
            "speaker": 11,
            "params": {"speedScale": 1.08},
        }
    }
    cfg = msa.build_tts_config(profiles)
    assert cfg["tts_voicevox"]["speakers"]["troublemaker_male_normal"] == 11
    assert cfg["tts_voicevox"]["voice_params"]["troublemaker_male_normal"]["speedScale"] == 1.08
    print("  config: speaker id と params を tts設定へ反映 OK")


def test_load_voice_profiles_merges_override_file():
    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/voice_profiles.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "troublemaker_male_normal": {
                    "params": {"speedScale": 1.2},
                    "fx": {"volume": 0.8},
                }
            }, f, ensure_ascii=False)
        profiles = msa.load_voice_profiles(path)
        p = profiles["troublemaker_male_normal"]
        assert p["params"]["speedScale"] == 1.2
        assert p["params"]["pitchScale"] == -0.03
        assert p["fx"]["volume"] == 0.8
        assert p["fx"]["lowpass"] == 7200
    print("  merge: 外部voice_profiles.jsonを部分上書きできる OK")


if __name__ == "__main__":
    test_load_voice_profiles_includes_troublemakers()
    test_build_script_turns_uses_profile_fx_and_narration_defaults()
    test_build_script_turns_resolves_v2_instance_voice_id()
    test_build_tts_config_maps_profiles_to_speakers()
    test_load_voice_profiles_merges_override_file()
    print("OK")
