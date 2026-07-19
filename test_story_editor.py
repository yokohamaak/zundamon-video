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
        '"focusSpeaker"',
        '"manualCameraFrame"',
        '"cameraTransition"',
        '"zoomTarget"',
        '"whiteboard_explain"',
        '"visibleSections"',
        '"visibleArrows"',
        '"showConclusion"',
        '"showConclusionArrow"',
        "JSON.parse",
        "calendar_event",
        '"部長"',
        '"部长"',
        "scene を省略しない",
        "完全一致",
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
                        "focusSpeaker": True,
                        "manualCameraFrame": {"cx": 0.5, "cy": 0.45, "width": 0.8},
                        "cameraTransition": "cut",
                        "cameraEffects": {"zoom": "in", "pan": "left", "shake": True},
                        "cameraEffectSettings": {"zoom": {"amount": 0.18, "duration": 0.45}},
                        "zoomTarget": {"x": 0.52, "y": 0.38},
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
            assert turn["focusSpeaker"] is True
            assert turn["manualCameraFrame"]["width"] == 0.8
            assert turn["cameraTransition"] == "cut"
            assert turn["cameraEffects"]["zoom"] == "in"
            assert turn["shake"] is True
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


def _turn(**extra):
    turn = {"speaker": "zundamon", "text": "テスト", "scene": "office"}
    turn.update(extra)
    return turn


def test_validate_story_accepts_existing_inserts():
    """既存の表示種別(insert)付きJSONがそのまま読める。未知kindも落とさない。"""
    inserts = [
        {"kind": "warning", "title": "警告", "text": "エラー"},
        {"kind": "ok", "text": "正常"},
        {"kind": "chat", "user": "質問", "ai": ["回答"]},
        {"kind": "teamchat", "messages": [{"from": "a", "text": "b"}]},
        {"kind": "mailer", "subject": "件名", "body": "本文"},
        {"kind": "videocall", "room": "定例", "participants": [{"speaker": "metan"}]},
        {"kind": "videocall", "end": True},
        {"kind": "whiteboard_explain", "title": "解説", "theme": "t", "sections": [], "conclusion": "c"},
        # 未対応の表示種別も、可能な範囲でそのまま通す（勝手に落とさない）
        {"kind": "future_unknown_kind", "foo": 1},
    ]
    se._validate_story({"script": [_turn(insert=ins) for ins in inserts]})
    # インサート無しの通常ターンも従来どおり通る
    se._validate_story({"script": [_turn()]})
    print("  validate: 既存インサート付き/通常JSONを読める(未知kindも保持): OK")


def test_validate_story_rejects_broken_insert():
    """壊れた insert（object でない・kind 無し）は保存前に弾く。"""
    for bad in ("warning", {"title": "kindが無い"}, {"kind": ""}, {"kind": 1}):
        try:
            se._validate_story({"script": [_turn(insert=bad)]})
        except ValueError:
            continue
        raise AssertionError(f"不正な insert を通してしまった: {bad!r}")
    print("  validate: 不正な insert を弾く: OK")


def test_validate_story_v2_resolves_instance_voice_ids():
    """v2保存時はscene slotと音声profileの両方を確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        scenes_path = os.path.join(tmp, "story-scenes.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 2,
                "scenes": {"office": {"layouts": {"standard": {"slots": {
                    "left": {"origin": {"x": 0.3, "y": 0.95}},
                }}}, "cameraPresets": {}}},
            }, f, ensure_ascii=False)
        old_scenes = se.SCENES_JSON
        try:
            se.SCENES_JSON = scenes_path
            story = {
                "schemaVersion": 2,
                "instances": {"hero": {"characterId": "zundamon", "voiceId": "zundamon"}},
                "script": [{
                    "id": "turn-0001", "speaker": "hero", "text": "テスト", "scene": "office",
                    "stage": {"enter": [{"instanceId": "hero", "placement": {"mode": "slot", "slotId": "left"}}]},
                }],
            }
            se._validate_story(story)
            story["instances"]["hero"]["voiceId"] = "存在しない声"
            try:
                se._validate_story(story)
            except ValueError as exc:
                assert "音声プロファイル" in str(exc)
            else:
                raise AssertionError("存在しないv2 voiceIdを通してしまった")
        finally:
            se.SCENES_JSON = old_scenes
    print("  validate: v2個体のslot/voiceIdを検証する: OK")


def test_save_story_prunes_empty_v2_whiteboard_display_mode():
    """V2ホワイトボードの入力を全削除した状態は通常表示として保存する。"""
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        scenes_path = os.path.join(tmp, "story-scenes.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 2,
                "scenes": {"office": {"layouts": {"standard": {"slots": {}}}, "cameraPresets": {}}},
            }, f, ensure_ascii=False)
        old_story, old_scenes = se.STORY_JSON, se.SCENES_JSON
        try:
            se.STORY_JSON = story_path
            se.SCENES_JSON = scenes_path
            story = {
                "schemaVersion": 2,
                "instances": {"hero": {"characterId": "zundamon", "voiceId": "zundamon"}},
                "script": [{
                    "id": "turn-0001",
                    "speaker": "hero",
                    "text": "テスト",
                    "scene": "office",
                    "displayMode": {
                        "kind": "whiteboard",
                        "whiteboard": {
                            "title": "",
                            "theme": "",
                            "sections": [
                                {"heading": "", "bullets": []},
                                {"heading": "", "bullets": []},
                                {"heading": "", "bullets": []},
                            ],
                            "conclusion": "",
                            "layout": "default",
                        },
                    },
                }],
            }
            se._save_story(story)
            saved = json.load(open(story_path, encoding="utf-8"))
            assert "displayMode" not in saved["script"][0]
        finally:
            se.STORY_JSON, se.SCENES_JSON = old_story, old_scenes
    print("  save: 空のV2ホワイトボードは通常表示として保存する: OK")


def test_save_story_prunes_overlays_referencing_deleted_turns():
    """削除済みturn.idを参照するoverlayは保存時に落とす。"""
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        scenes_path = os.path.join(tmp, "story-scenes.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 2,
                "scenes": {"office": {"layouts": {"standard": {"slots": {}}}, "cameraPresets": {}}},
            }, f, ensure_ascii=False)
        old_story, old_scenes = se.STORY_JSON, se.SCENES_JSON
        try:
            se.STORY_JSON = story_path
            se.SCENES_JSON = scenes_path
            story = {
                "schemaVersion": 2,
                "instances": {"hero": {"characterId": "zundamon", "voiceId": "zundamon"}},
                "script": [{
                    "id": "turn-live",
                    "speaker": "hero",
                    "text": "残るターン",
                    "scene": "office",
                }],
                "overlays": [
                    {
                        "id": "ok",
                        "kind": "text",
                        "text": "残す",
                        "x": 0.5,
                        "y": 0.5,
                        "w": 0.3,
                        "start": {"turnId": "turn-live", "at": 0},
                        "end": {"turnId": "turn-live", "at": 1},
                    },
                    {
                        "id": "stale",
                        "kind": "text",
                        "text": "消す",
                        "x": 0.5,
                        "y": 0.5,
                        "w": 0.3,
                        "start": {"turnId": "turn-deleted", "at": 0},
                        "end": {"turnId": "turn-live", "at": 1},
                    },
                ],
            }
            se._save_story(story)
            saved = json.load(open(story_path, encoding="utf-8"))
            assert [overlay["id"] for overlay in saved["overlays"]] == ["ok"]
        finally:
            se.STORY_JSON, se.SCENES_JSON = old_story, old_scenes
    print("  save: 削除済みターンを参照するoverlayを保存前に削除する: OK")


def test_import_v2_story_keeps_stage_and_instance_references():
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        scenes_path = os.path.join(tmp, "story-scenes.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 2,
                "scenes": {"office": {"layouts": {"standard": {"slots": {
                    "left": {"origin": {"x": 0.3, "y": 0.95}},
                }}}, "cameraPresets": {}}},
            }, f, ensure_ascii=False)
        old_story, old_scenes = se.STORY_JSON, se.SCENES_JSON
        try:
            se.STORY_JSON, se.SCENES_JSON = story_path, scenes_path
            raw = json.dumps({
                "schemaVersion": 2,
                "instances": {"hero": {"characterId": "zundamon", "voiceId": "zundamon", "label": "主人公"}},
                "script": [{
                    "id": "generated-id", "speaker": "hero", "text": "始めるのだ", "scene": "office",
                    "displayMode": {"kind": "standard"},
                    "stage": {"enter": [{"instanceId": "hero", "placement": {"mode": "slot", "slotId": "left"}}]},
                }],
            }, ensure_ascii=False)
            ok, msg, info = se._import_script_text(raw)
            assert ok, msg
            assert info["report"]["newFields"] == {}
            assert info["report"]["newSpeakers"] == {}
            saved = json.load(open(story_path, encoding="utf-8"))
            assert saved["instances"]["hero"]["voiceId"] == "zundamon"
            assert saved["script"][0]["speaker"] == "hero"
            assert saved["script"][0]["stage"]["enter"][0]["instanceId"] == "hero"
        finally:
            se.STORY_JSON, se.SCENES_JSON = old_story, old_scenes
    print("  import: v2個体/stageを未知項目・未知話者扱いせず保存する: OK")


def test_prompt_v2_mentions_v2_contract():
    with tempfile.TemporaryDirectory() as tmp:
        scenes_path = os.path.join(tmp, "story-scenes.json")
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump({
                "schemaVersion": 2,
                "scenes": {"office": {"label": "オフィス", "layouts": {"standard": {"slots": {
                    "speakerLeft": {"origin": {"x": 0.3, "y": 0.95}},
                    "speakerRight": {"origin": {"x": 0.7, "y": 0.95}},
                }}}, "cameraPresets": {"default": {"cx": 0.5, "cy": 0.5, "width": 1}}}},
            }, f, ensure_ascii=False)
        old_scenes = se.SCENES_JSON
        try:
            se.SCENES_JSON = scenes_path
            prompt = se._build_script_prompt_v2("題材", "5分", "補足")
        finally:
            se.SCENES_JSON = old_scenes
    for token in (
        '"schemaVersion": 2',
        '"instances"',
        '"enter"',
        '"exit"',
        '"update"',
        '"slotId"',
        '"framing"',
        '"cameraMotion"',
        '"cameraTransition"',
        '"displayMode"',
        '"caption"',
        '"effects"',
        '"voiceLines"',
        '"framedStage"',
        "voiceOnly",
        "speakerLeft",
        "在席簿",
        "旧形式のキー",
        "JSON.parse",
        "手直し済み台本から抽出した品質基準",
        "1分あたり350〜420文字・13〜17ターン",
        "cameraMotion は全ターンの15〜20%程度",
        "text が空白だけのターン",
    ):
        assert token in prompt, f"{token} がV2プロンプトに無い"
    default_prompt = se._build_script_prompt_v2("題材", "", "補足")
    assert "8〜11分・全体で120〜170ターン、3,200〜4,300文字ほど" in default_prompt
    # 旧形式キーは「禁止リスト」としてだけ登場し、使い方としては案内しない。
    for legacy in ('"focusSpeaker": true', '"speakerAnchor":', '"telop":', '"enterDir":', '"insert":'):
        assert legacy not in prompt, f"旧形式の使い方 {legacy} がV2プロンプトに残っている"
    print("  V2プロンプトがV2契約を案内し旧形式を案内しない: OK")


def test_prompt_v2_example_is_importable():
    """プロンプトが教える出力例そのものが、実データの検証を通って取り込めること。"""
    prompt = se._build_script_prompt_v2("題材", "5分", "補足")
    marker = "━━━ 出力例（最小）━━━\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n\nでは、上記の【題材】", start)
    example = prompt[start:end]
    with tempfile.TemporaryDirectory() as tmp:
        old_story = se.STORY_JSON
        try:
            se.STORY_JSON = os.path.join(tmp, "story-01.json")
            ok, msg, info = se._import_script_text(example)
            assert ok, f"V2プロンプトの出力例が取り込めない: {msg}"
            assert info["turns"] >= 3
        finally:
            se.STORY_JSON = old_story
    print("  V2プロンプトの出力例が実データでそのまま取込可能: OK")


def test_prompt_builder_dispatches_by_schema():
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        old_story = se.STORY_JSON
        try:
            se.STORY_JSON = story_path
            assert se._script_prompt_builder() is se._build_script_prompt, "台本なし→旧ビルダーのはず"
            with open(story_path, "w", encoding="utf-8") as f:
                json.dump({"schemaVersion": 2, "instances": {}, "script": []}, f)
            assert se._script_prompt_builder() is se._build_script_prompt_v2, "V2台本→V2ビルダーのはず"
        finally:
            se.STORY_JSON = old_story
    print("  プロンプト生成が台本schemaで切り替わる: OK")


def test_import_rejects_legacy_script_while_editing_v2_story():
    """V2台本の編集中に旧形式を取り込むと、V2台本ごと旧形式で上書きされる事故を防ぐ。"""
    with tempfile.TemporaryDirectory() as tmp:
        story_path = os.path.join(tmp, "story-01.json")
        current_v2 = {
            "schemaVersion": 2,
            "instances": {"hero": {"characterId": "zundamon", "voiceId": "zundamon"}},
            "script": [{"id": "turn-0001", "speaker": "hero", "text": "現行データ", "scene": "office"}],
        }
        with open(story_path, "w", encoding="utf-8") as f:
            json.dump(current_v2, f, ensure_ascii=False)
        old_story = se.STORY_JSON
        try:
            se.STORY_JSON = story_path
            raw = json.dumps({
                "title": "旧形式ドラフト",
                "script": [{"speaker": "zundamon", "text": "旧形式なのだ", "scene": "office"}],
            }, ensure_ascii=False)
            ok, msg, _info = se._import_script_text(raw)
            assert not ok, "V2編集中の旧形式取込を通してしまった"
            assert "V2形式" in msg and "旧形式" in msg, f"エラーメッセージが案内になっていない: {msg}"
            saved = json.load(open(story_path, encoding="utf-8"))
            assert saved == current_v2, "拒否したのに story-01.json が書き換わっている"
        finally:
            se.STORY_JSON = old_story
    print("  import: V2編集中は旧形式の取込を拒否する: OK")


if __name__ == "__main__":
    test_prompt_mentions_current_story_fields()
    test_import_recognizes_supported_story_fields()
    test_speakers_include_troublemaker_profiles()
    test_validate_story_accepts_existing_inserts()
    test_validate_story_rejects_broken_insert()
    test_validate_story_v2_resolves_instance_voice_ids()
    test_save_story_prunes_empty_v2_whiteboard_display_mode()
    test_save_story_prunes_overlays_referencing_deleted_turns()
    test_import_v2_story_keeps_stage_and_instance_references()
    test_prompt_v2_mentions_v2_contract()
    test_prompt_v2_example_is_importable()
    test_prompt_builder_dispatches_by_schema()
    test_import_rejects_legacy_script_while_editing_v2_story()
    print("OK")
