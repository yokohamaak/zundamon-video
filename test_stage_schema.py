import copy
import unittest

from stage_schema import validate_scene_library_v2, validate_story_v2


SCENES = {
    "schemaVersion": 2,
    "scenes": {
        "office": {
            "bg": "background/office.png",
            "layouts": {
                "standard": {
                    "slots": {
                        "speakerLeft": {
                            "origin": {"x": 0.28, "y": 0.96},
                            "cameraPresetId": "left",
                        },
                        "speakerRight": {
                            "origin": {"x": 0.72, "y": 0.96},
                            "cameraPresetId": "right",
                        },
                        "backgroundLeft": {
                            "origin": {"x": 0.1, "y": 0.98},
                            "allowOverlap": True,
                        },
                    }
                }
            },
            "cameraPresets": {
                "default": {"cx": 0.5, "cy": 0.5, "width": 1},
                "left": {"cx": 0.35, "cy": 0.5, "width": 0.7},
                "right": {"cx": 0.65, "cy": 0.5, "width": 0.7},
            },
        }
    },
}

STORY = {
    "schemaVersion": 2,
    "instances": {
        "zundamon": {"characterId": "zundamon", "voiceId": "zundamon"},
        "metan": {"characterId": "metan", "voiceId": "metan"},
        "narrator": {"role": "voiceOnly", "voiceId": "narration"},
    },
    "script": [
        {
            "id": "turn-0001",
            "speaker": "zundamon",
            "text": "始めるのだ。",
            "scene": "office",
            "stage": {
                "enter": [
                    {
                        "instanceId": "zundamon",
                        "placement": {"mode": "slot", "slotId": "speakerLeft"},
                    }
                ],
                "framing": {"mode": "speaker"},
            },
        },
        {
            "id": "turn-0002",
            "speaker": "metan",
            "text": "了解です。",
            "scene": "office",
            "stage": {
                "enter": [
                    {
                        "instanceId": "metan",
                        "placement": {"mode": "slot", "slotId": "speakerRight"},
                    }
                ],
                "update": {"zundamon": {"expression": "smile"}},
                "framing": {"mode": "speaker"},
            },
        },
    ],
}


class StageSchemaTest(unittest.TestCase):
    def test_valid_v2_story(self):
        validate_scene_library_v2(SCENES)
        validate_story_v2(STORY, SCENES)

    def test_v2_accepts_enter_exit_animation_direction(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["stage"]["enter"][0]["animation"] = {"direction": "up"}
        story["script"][1]["stage"]["exit"] = [{"instanceId": "zundamon", "animation": {"direction": "down"}}]
        story["script"][1]["stage"].pop("update", None)
        story["script"][1]["stage"]["framing"] = {"mode": "slot", "slotId": "speakerRight"}
        validate_story_v2(story, SCENES)

    def test_v2_rejects_invalid_enter_exit_animation_direction(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["stage"]["enter"][0]["animation"] = {"direction": "diagonal"}
        with self.assertRaisesRegex(ValueError, "left/right/up/down/instant"):
            validate_story_v2(story, SCENES)
        story = copy.deepcopy(STORY)
        story["script"][1]["stage"]["exit"] = [{"instanceId": "zundamon", "animation": {"direction": "diagonal"}}]
        with self.assertRaisesRegex(ValueError, "left/right/up/down/instant"):
            validate_story_v2(story, SCENES)

    def test_v2_accepts_display_contract_fields(self):
        story = copy.deepcopy(STORY)
        story["idleFace"] = "hold"
        story["displaySettings"] = {
            "bubble": {"fontSize": 48, "bgColor": "#ffffff", "textColor": "#111111", "borderWidth": 4, "radius": 14},
            "subtitle": {"fontSize": 42, "bgColor": "#111111", "textColor": "#ffffff", "border": True, "borderColor": "#ffffff"},
            "telop": {"x": 0.05, "y": 0.08, "size": 1.2},
            "speakerColors": {"zundamon": "#5fb84f", "default": "#9aa0a6"},
        }
        story["script"][0].update({
            "hideCharacters": True,
            "hideBubble": False,
            "subtitleMode": "subtitle",
            "subtitleStyle": {"fontSize": 40, "textColor": "#ffffff", "boxBorder": True, "boxBorderColor": "#ffffff", "boxBorderWidth": 2},
            "bubbleMaxChars": 20,
            "sentences": [{"text": "始めるのだ。", "start": 0, "end": 1}],
            "transition": "cut",
        })
        validate_story_v2(story, SCENES)

    def test_v2_accepts_caption(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["caption"] = {"text": "― 前日 ―", "x": 0.04, "y": 0.06, "size": 1.1}
        validate_story_v2(story, SCENES)

    def test_v2_accepts_framed_stage(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["displayMode"] = {
            "kind": "framedStage",
            "framedStage": {
                "background": "background/movie_player.png",
                "frame": {"x": 0.1, "y": 0.1, "width": 0.72},
                "frameTransition": "smooth",
            },
        }
        validate_story_v2(story, SCENES)

        story["script"][0]["displayMode"]["framedStage"]["frameTransition"] = "bounce"
        with self.assertRaisesRegex(ValueError, "frameTransition"):
            validate_story_v2(story, SCENES)

    def test_v2_accepts_empty_text_for_pause_only_turn(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["text"] = ""
        story["script"][0]["pause"] = 2.0
        validate_story_v2(story, SCENES)

    def test_v2_rejects_framed_stage_outside_background(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["displayMode"] = {
            "kind": "framedStage",
            "framedStage": {
                "background": "overlays/movie_player.png",
                "frame": {"x": 0.1, "y": 0.1, "width": 0.72},
            },
        }
        with self.assertRaisesRegex(ValueError, "framedStage.background"):
            validate_story_v2(story, SCENES)

    def test_v2_rejects_framed_stage_that_does_not_fit_16_by_9_canvas(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["displayMode"] = {
            "kind": "framedStage",
            "framedStage": {
                "background": "background/movie_player.png",
                "frame": {"x": 0.1, "y": 0.4, "width": 0.72},
            },
        }
        with self.assertRaisesRegex(ValueError, "framedStage.frame"):
            validate_story_v2(story, SCENES)

    def test_v2_rejects_invalid_caption(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["caption"] = {"text": "", "x": 1.2}
        with self.assertRaisesRegex(ValueError, "caption"):
            validate_story_v2(story, SCENES)

    def test_v2_accepts_lightweight_effects(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["effects"] = {"impactLines": True, "zoomPunch": True, "quoteFreeze": True}
        validate_story_v2(story, SCENES)

    def test_v2_accepts_lightweight_effect_parameters(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["effects"] = {
            "impactLines": {"enabled": True, "cx": 0.42, "cy": 0.55, "count": 96, "thickness": 1.5, "opacity": 0.8, "innerRadius": 0.2, "start": 0.1, "end": 1.2},
            "zoomPunch": {"enabled": True, "scale": 1.2, "duration": 0.24, "borderStrength": 1.2},
            "quoteFreeze": {"enabled": True, "fadeIn": 0.12, "fadeOutStart": 0.65, "fadeOutDuration": 0.2, "backdropOpacity": 0.3},
            "flashback": {"enabled": True},
            "visionNoise": {"enabled": True, "type": "vhs", "strength": 0.7, "scanline": 0.8, "glitch": 0.2, "flicker": 0.4, "tint": "#7dd3fc"},
            "irisOut": {"enabled": True, "cx": 0.5, "cy": 0.5, "startRadius": 1.05, "closeStart": 1.2, "closeEnd": 1.8, "color": "#000000"},
        }
        validate_story_v2(story, SCENES)

    def test_v2_rejects_invalid_effects(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["effects"] = {"impactLines": "yes"}
        with self.assertRaisesRegex(ValueError, "effects.impactLines"):
            validate_story_v2(story, SCENES)
        story = copy.deepcopy(STORY)
        story["script"][0]["effects"] = {"zoomPunch": {"scale": 9}}
        with self.assertRaisesRegex(ValueError, "effects.zoomPunch.scale"):
            validate_story_v2(story, SCENES)
        story = copy.deepcopy(STORY)
        story["script"][0]["effects"] = {"visionNoise": {"type": "bad"}}
        with self.assertRaisesRegex(ValueError, "effects.visionNoise.type"):
            validate_story_v2(story, SCENES)

    def test_v2_rejects_legacy_fields_instead_of_ignoring_them(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["flashback"] = True
        with self.assertRaisesRegex(ValueError, "未対応の項目"):
            validate_story_v2(story, SCENES)
        story = copy.deepcopy(STORY)
        story["script"][0]["transition"] = "fade-black"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "fade-white"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "wipe-left"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "wipe-right"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "slide-left"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "slide-right"
        validate_story_v2(story, SCENES)
        story["script"][0]["transition"] = "zoom"
        with self.assertRaisesRegex(ValueError, "transition"):
            validate_story_v2(story, SCENES)

    def test_scene_accepts_background_video(self):
        scenes = copy.deepcopy(SCENES)
        scene = scenes["scenes"]["office"]
        del scene["bg"]
        scene["bgVideo"] = "background/money.mp4"
        scene["bgVideoLoop"] = True
        validate_scene_library_v2(scenes)

    def test_scene_requires_background_image_or_video(self):
        scenes = copy.deepcopy(SCENES)
        del scenes["scenes"]["office"]["bg"]
        with self.assertRaisesRegex(ValueError, "bg または bgVideo"):
            validate_scene_library_v2(scenes)

    def test_scene_figure_allows_bust_or_full(self):
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["figure"] = "full"
        validate_scene_library_v2(scenes)
        scenes["scenes"]["office"]["figure"] = "portrait"
        with self.assertRaisesRegex(ValueError, "bust または full"):
            validate_scene_library_v2(scenes)

    def test_slot_preview_character_must_be_a_nonempty_string(self):
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["layouts"]["standard"]["slots"]["speakerLeft"]["previewCharacterId"] = ""
        with self.assertRaisesRegex(ValueError, "previewCharacterId"):
            validate_scene_library_v2(scenes)

    def test_scene_slot_rejects_unknown_keys_and_invalid_overlap_flag(self):
        scenes = copy.deepcopy(SCENES)
        slot = scenes["scenes"]["office"]["layouts"]["standard"]["slots"]["speakerLeft"]
        slot["visible"] = False
        with self.assertRaisesRegex(ValueError, "未対応の項目"):
            validate_scene_library_v2(scenes)
        del slot["visible"]
        slot["allowOverlap"] = "yes"
        with self.assertRaisesRegex(ValueError, "allowOverlap"):
            validate_scene_library_v2(scenes)

    def test_camera_frame_must_stay_within_editor_range(self):
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["cameraPresets"]["left"]["width"] = 0.2
        with self.assertRaisesRegex(ValueError, "0.35〜1.0"):
            validate_scene_library_v2(scenes)
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["cameraPresets"]["left"]["cx"] = 0.1
        with self.assertRaisesRegex(ValueError, "画面内"):
            validate_scene_library_v2(scenes)

    def test_speaker_does_not_implicitly_enter(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["stage"].pop("enter")
        with self.assertRaisesRegex(ValueError, "speaker framing"):
            validate_story_v2(story, SCENES)

    def test_camera_transition_accepts_smooth_or_cut(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["cameraTransition"] = "cut"
        validate_story_v2(story, SCENES)
        story["script"][1]["cameraTransition"] = "wipe"
        with self.assertRaisesRegex(ValueError, "cameraTransition"):
            validate_story_v2(story, SCENES)

    def test_standard_slot_rejects_overlap(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["stage"]["enter"][0]["placement"]["slotId"] = "speakerLeft"
        with self.assertRaisesRegex(ValueError, "複数個体"):
            validate_story_v2(story, SCENES)

    def test_overlap_z_index_is_inherited_on_following_turns(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["stage"]["enter"][0]["placement"]["slotId"] = "backgroundLeft"
        story["script"][1]["stage"]["enter"][0]["placement"]["slotId"] = "backgroundLeft"
        story["script"][1]["stage"]["update"] = {
            "zundamon": {"zIndex": 10},
            "metan": {"zIndex": 20},
        }
        story["script"].append({
            "id": "turn-0003",
            "speaker": "zundamon",
            "text": "重なりを維持するのだ。",
            "scene": "office",
        })
        validate_story_v2(story, SCENES)

    def test_voice_only_cannot_enter(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["stage"]["enter"][0]["instanceId"] = "narrator"
        with self.assertRaisesRegex(ValueError, "voiceOnly"):
            validate_story_v2(story, SCENES)

    def test_same_character_can_have_multiple_instances(self):
        story = copy.deepcopy(STORY)
        story["instances"]["zundamon-2"] = {"characterId": "zundamon", "voiceId": "zundamon", "label": "ずんだもんB"}
        story["script"][1]["stage"]["enter"].append({
            "instanceId": "zundamon-2",
            "placement": {"mode": "slot", "slotId": "backgroundLeft"},
        })
        validate_story_v2(story, SCENES)

    def test_whiteboard_uses_present_stage_instance(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["displayMode"] = {
            "kind": "whiteboard",
            "presenterId": "metan",
            "whiteboard": {
                "title": "整理します",
                "theme": "確認事項",
                "sections": [
                    {"heading": "原因", "bullets": ["共有不足"], "icon": "cause"},
                    {"heading": "影響", "bullets": ["手戻り"], "icon": "risk"},
                    {"heading": "対策", "bullets": ["記録する"], "icon": "solution"},
                ],
                "conclusion": "早めに共有する",
                "layout": "compact",
                "visibleSections": [True, False, True],
                "visibleArrows": [False, True],
                "showConclusion": True,
                "showConclusionArrow": True,
                "activeSection": 2,
                "style": {
                    "titleFontSize": 88,
                    "themeFontSize": 60,
                    "sectionHeadingFontSize": 48,
                    "sectionBodyFontSize": 40,
                    "conclusionFontSize": 64,
                    "conclusionBoxX": 245,
                    "conclusionBoxY": 700,
                    "conclusionBoxWidth": 1150,
                    "conclusionBoxHeight": 200,
                },
                "animation": {
                    "mode": "step",
                    "sectionPop": True,
                    "arrowPop": True,
                    "conclusionPop": True,
                    "underlineDraw": True,
                    "conclusionImpact": False,
                },
            },
        }
        validate_story_v2(story, SCENES)
        story["script"][1]["displayMode"]["presenterId"] = "narrator"
        with self.assertRaisesRegex(ValueError, "stage個体"):
            validate_story_v2(story, SCENES)
        story = copy.deepcopy(STORY)
        story["script"][1]["displayMode"] = {
            "kind": "whiteboard",
            "whiteboard": {
                "title": "整理します",
                "theme": "確認事項",
                "sections": [
                    {"heading": "原因", "bullets": ["共有不足"], "icon": "missing"},
                    {"heading": "影響", "bullets": ["手戻り"]},
                    {"heading": "対策", "bullets": ["記録する"]},
                ],
                "conclusion": "早めに共有する",
            },
        }
        with self.assertRaisesRegex(ValueError, "アイコン"):
            validate_story_v2(story, SCENES)

    def test_zun_meet_uses_explicit_participants(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["displayMode"] = {
            "kind": "zunMeet",
            "zunMeet": {
                "room": "定例会議",
                "layout": "focus",
                "activeSpeakerId": "metan",
                "participants": [{"instanceId": "zundamon", "name": "進行役"}, {"instanceId": "metan", "muted": True}],
            },
        }
        validate_story_v2(story, SCENES)
        story["script"][1]["displayMode"]["zunMeet"]["activeSpeakerId"] = "zundamon"
        story["script"][1]["displayMode"]["zunMeet"]["participants"][1]["instanceId"] = "missing"
        with self.assertRaisesRegex(ValueError, "instancesにありません"):
            validate_story_v2(story, SCENES)

    def test_zun_meet_rejects_non_string_room_and_name(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["displayMode"] = {
            "kind": "zunMeet",
            "zunMeet": {"room": 1, "participants": [{"instanceId": "zundamon"}]},
        }
        with self.assertRaisesRegex(ValueError, "room"):
            validate_story_v2(story, SCENES)
        story["script"][1]["displayMode"]["zunMeet"]["room"] = "定例会議"
        story["script"][1]["displayMode"]["zunMeet"]["participants"][0]["name"] = 1
        with self.assertRaisesRegex(ValueError, "name"):
            validate_story_v2(story, SCENES)

    def test_v2_accepts_insert_display_modes(self):
        story = copy.deepcopy(STORY)
        story["script"][0]["displayMode"] = {
            "kind": "zunMonitor",
            "monitor": {"kind": "warning", "title": "警告", "text": "CPU使用率が高い"},
        }
        story["script"][1]["displayMode"] = {
            "kind": "zunAi",
            "chat": {"kind": "chat", "user": "原因は？", "ai": ["ログを確認します"], "highlight": 0},
        }
        validate_story_v2(story, SCENES)

        story["script"][0]["displayMode"] = {
            "kind": "zunChat",
            "teamchat": {"kind": "teamchat", "channel": "障害対応", "messages": [{"from": "metan", "text": "確認します", "highlight": True}]},
        }
        story["script"][1]["displayMode"] = {
            "kind": "zunMail",
            "mailer": {"kind": "mailer", "from": "管理部", "subject": "連絡", "body": "至急確認してください"},
        }
        validate_story_v2(story, SCENES)

    def test_slot_camera_preset_must_exist(self):
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["layouts"]["standard"]["slots"]["speakerLeft"]["cameraPresetId"] = "missing"
        with self.assertRaisesRegex(ValueError, "cameraPresets"):
            validate_scene_library_v2(scenes)

    def test_face_allows_only_renderable_directions(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["stage"]["update"]["zundamon"]["face"] = "right"
        validate_story_v2(story, SCENES)
        story["script"][1]["stage"]["update"]["zundamon"]["face"] = "back"
        with self.assertRaisesRegex(ValueError, "left/right"):
            validate_story_v2(story, SCENES)

    def test_mob_expression_requires_existing_image_pair(self):
        story = copy.deepcopy(STORY)
        story["instances"]["zundamon"]["characterId"] = "boss"
        story["script"][1]["stage"]["update"]["zundamon"]["expression"] = "agitated"
        mobs = {"boss": {"images": {"normal": {"closed": "mobs/boss.png", "open": "mobs/boss.png"}}}}
        with self.assertRaisesRegex(ValueError, "素材がありません"):
            validate_story_v2(story, SCENES, mobs)
        story["script"][1]["stage"]["update"]["zundamon"]["expression"] = "normal"
        validate_story_v2(story, SCENES, mobs)

    def test_stage_rejects_unknown_patch_and_camera_motion_keys(self):
        story = copy.deepcopy(STORY)
        story["script"][1]["stage"]["update"]["zundamon"]["visible"] = False
        with self.assertRaisesRegex(ValueError, "未対応の項目"):
            validate_story_v2(story, SCENES)
        del story["script"][1]["stage"]["update"]["zundamon"]["visible"]
        story["script"][1]["stage"]["cameraMotion"] = {"zoom": 0.1, "speed": 2}
        with self.assertRaisesRegex(ValueError, "未対応の項目"):
            validate_story_v2(story, SCENES)

        story["script"][1]["stage"]["cameraMotion"] = {"inherit": True}
        validate_story_v2(story, SCENES)
        story["script"][1]["stage"]["cameraMotion"] = {"inherit": "yes"}
        with self.assertRaisesRegex(ValueError, "cameraMotion.inherit"):
            validate_story_v2(story, SCENES)

    def test_stage_character_requires_known_renderer_material(self):
        story = copy.deepcopy(STORY)
        story["instances"]["zundamon"]["characterId"] = "missing"
        with self.assertRaisesRegex(ValueError, "描画素材がありません"):
            validate_story_v2(story, SCENES)



if __name__ == "__main__":
    unittest.main()
