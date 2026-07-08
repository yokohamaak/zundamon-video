"""story_editor の台本生成・取り込みまわりの回帰テスト。

実行: python3 test_story_editor.py
"""
import json
import os
import tempfile

import story_editor as se


def test_prompt_mentions_current_story_fields():
    prompt = se._build_script_prompt("題材", "5分", "補足")
    for token in (
        '"transition"',
        '"pose"',
        '"narrationVoice"',
        '"voice"',
        '"noLipSync"',
        '"subtitleMode"',
        '"continueBubble"',
        '"disableAutoBubbleSplit"',
        '"manualPos"',
        '"faceMode"',
        '"clearFace"',
        '"se"',
        '"sparklePos"',
        '"cameraEffects"',
        '"cameraEffectSettings"',
        '"zoomTarget"',
        '"cameraCenter"',
        '"whiteboard_explain"',
        '"visibleSections"',
        '"visibleArrows"',
        '"showConclusion"',
        '"showConclusionArrow"',
    ):
        assert token in prompt, f"{token} がプロンプトに無い"
    print("  現行の主要ターン項目がプロンプトに含まれる: OK")


def test_import_recognizes_supported_story_fields():
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        scenes_path = os.path.join(tmp, "story-scenes.json")
        expr_path = os.path.join(tmp, "expressions.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({"scenes": {"office": {"label": "オフィス"}}}, f, ensure_ascii=False)
        with open(expr_path, "w", encoding="utf-8") as f:
            json.dump({"zundamon": {"normal": {}, "panic": {}}}, f, ensure_ascii=False)

        old_story, old_scenes, old_expr = se.STORY_JSON, se.SCENES_JSON, se.EXPRESSIONS_JSON
        try:
            se.STORY_JSON = story_path
            se.SCENES_JSON = scenes_path
            se.EXPRESSIONS_JSON = expr_path
            raw = json.dumps({
                "title": "test",
                "script": [
                    {
                        "speaker": "zundamon",
                        "text": "警報なのだ！",
                        "scene": "office",
                        "expression": "panic",
                        "pose": "flustered",
                        "transition": "fade-black",
                        "voice": {"speed": 0.88, "pitch": 0.0, "intonation": 0.0},
                        "narrationVoice": "棒読み男",
                        "noLipSync": True,
                        "subtitleMode": "subtitle",
                        "subtitleStyle": {"fontSize": 46, "textColor": "#ffffff"},
                        "continueBubble": True,
                        "disableAutoBubbleSplit": True,
                        "face": {"zundamon": "front_right"},
                        "faceMode": "hold",
                        "clearFace": ["zundamon"],
                        "manualPos": {"zundamon": {"x": 0.58, "y": 0.96}},
                        "cameraEffects": {"zoom": "in", "pan": "left"},
                        "cameraEffectSettings": {"zoom": {"amount": 0.18, "duration": 0.45}},
                        "zoomTarget": {"x": 0.52, "y": 0.38},
                        "cameraCenter": {"x": 0.50, "y": 0.45},
                        "se": [{"file": "se/alarm.mp3", "at": 0.0, "volume": 0.9}],
                        "sparklePos": {"x": 0.62, "y": 0.30},
                        "insert": {
                            "kind": "whiteboard_explain",
                            "title": "めたんの解説コーナー",
                            "theme": "障害報告",
                            "sections": [
                                {"heading": "原因", "bullets": ["監視漏れ"], "icon": "warning"},
                                {"heading": "影響", "bullets": ["進捗遅延"], "icon": "memo"},
                                {"heading": "対策", "bullets": ["再発防止"], "icon": "checklist"},
                            ],
                            "conclusion": "先に共有が大事",
                            "visibleSections": [True, False, False],
                            "visibleArrows": [True, False],
                            "showConclusion": False,
                            "showConclusionArrow": True,
                        },
                    }
                ],
            }, ensure_ascii=False)
            ok, msg, info = se._import_script_text(raw)
            assert ok, msg
            report = info["report"]
            assert report["newFields"] == {}, report["newFields"]
            assert report["newScenes"] == {}, report["newScenes"]
            assert report["newExpr"] == {}, report["newExpr"]
            assert report["newInserts"] == {}, report["newInserts"]
            saved = json.load(open(story_path, encoding="utf-8"))
            turn = saved["script"][0]
            assert turn["pose"] == "flustered"
            assert turn["transition"] == "fade-black"
            assert turn["narrationVoice"] == "棒読み男"
            assert turn["noLipSync"] is True
            assert turn["subtitleMode"] == "subtitle"
            assert turn["continueBubble"] is True
            assert turn["disableAutoBubbleSplit"] is True
            assert turn["faceMode"] == "hold"
            assert turn["manualPos"]["zundamon"]["x"] == 0.58
            assert turn["cameraEffects"]["zoom"] == "in"
            assert turn["sparklePos"]["x"] == 0.62
            assert turn["insert"]["kind"] == "whiteboard_explain"
            assert turn["insert"]["visibleArrows"] == [True, False]
            assert turn["se"][0]["file"] == "se/alarm.mp3"
        finally:
            se.STORY_JSON, se.SCENES_JSON, se.EXPRESSIONS_JSON = old_story, old_scenes, old_expr
    print("  現行サポート項目を取り込み時に新演出扱いしない: OK")


def test_speakers_include_troublemaker_profiles():
    for name in (
        "troublemaker_male_normal",
        "troublemaker_male_creepy",
        "troublemaker_female_normal",
        "troublemaker_female_creepy",
    ):
        assert name in se.BASE_SPEAKERS
    print("  speakers: troublemaker男女4種がエディタ選択肢にある: OK")


if __name__ == "__main__":
    test_prompt_mentions_current_story_fields()
    test_import_recognizes_supported_story_fields()
    test_speakers_include_troublemaker_profiles()
    print("OK")
