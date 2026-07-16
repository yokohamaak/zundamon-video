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
                "left": {"cx": 0.3, "cy": 0.5, "width": 0.7},
                "right": {"cx": 0.7, "cy": 0.5, "width": 0.7},
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

    def test_slot_preview_character_must_be_a_nonempty_string(self):
        scenes = copy.deepcopy(SCENES)
        scenes["scenes"]["office"]["layouts"]["standard"]["slots"]["speakerLeft"]["previewCharacterId"] = ""
        with self.assertRaisesRegex(ValueError, "previewCharacterId"):
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
                    {"heading": "原因", "bullets": ["共有不足"]},
                    {"heading": "影響", "bullets": ["手戻り"]},
                    {"heading": "対策", "bullets": ["記録する"]},
                ],
                "conclusion": "早めに共有する",
            },
        }
        validate_story_v2(story, SCENES)
        story["script"][1]["displayMode"]["presenterId"] = "narrator"
        with self.assertRaisesRegex(ValueError, "stage個体"):
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



if __name__ == "__main__":
    unittest.main()
