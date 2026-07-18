"""2026-07-16: 現行storyをv2へ一回だけ移すための変換記録。"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKUP = Path(__file__).resolve().parent
SOURCE_STORY = BACKUP / "story-01.legacy.json"
SOURCE_SCENES = BACKUP / "story-scenes.legacy.json"
MOBS = ROOT / "video/public/mobs.json"
DEST_STORY = ROOT / "video/public/story-01.json"
DEST_SCENES = ROOT / "video/public/story-scenes.json"
REPORT = BACKUP / "conversion-report.json"

MAIN_CHARACTERS = {"zundamon", "metan"}
VOICE_ONLY = {"AI", "音声なし"}


def copy_frame(frame: object, fallback: dict[str, float]) -> dict[str, float]:
    if not isinstance(frame, dict):
        return fallback.copy()
    try:
        width = float(frame["width"])
        if width <= 0:
            raise ValueError
        return {"cx": float(frame["cx"]), "cy": float(frame["cy"]), "width": width}
    except (KeyError, TypeError, ValueError):
        return fallback.copy()


def slot_id(anchor_id: str) -> str:
    return {"left": "speakerLeft", "right": "speakerRight", "center": "center"}.get(
        anchor_id, f"anchor-{anchor_id}"
    )


def slot_for_actor(scene: dict, actor_id: str) -> str:
    cast = scene.get("cast") if isinstance(scene.get("cast"), dict) else {}
    anchor_id = cast.get(actor_id, "center")
    return slot_id(anchor_id if isinstance(anchor_id, str) else "center")


def build_scenes(source: dict) -> dict:
    result: dict[str, object] = {"schemaVersion": 2, "scenes": {}}
    for scene_id, old in source.get("scenes", {}).items():
        if not isinstance(old, dict):
            continue
        anchors = old.get("anchors") if isinstance(old.get("anchors"), dict) else {}
        default_anchor = {"x": 0.5, "y": 0.68}
        slots: dict[str, dict] = {}
        for anchor_id in ("left", "center", "right"):
            anchor = anchors.get(anchor_id, default_anchor)
            if not isinstance(anchor, dict):
                anchor = default_anchor
            try:
                x = float(anchor.get("x", default_anchor["x"]))
            except (TypeError, ValueError):
                x = default_anchor["x"]
            # 旧anchorは立ち絵中央、v2のoriginは足元。既存の全身座標をそのまま使わず、
            # v2のバスト立ち絵で扱いやすい足元位置へ正規化する。
            y = 0.94
            slot = {"origin": {"x": x, "y": y}, "scale": float(old.get("scale", 1.1)), "zIndex": 20}
            if anchor_id == "left":
                slot["cameraPresetId"] = "left"
            elif anchor_id == "right":
                slot["cameraPresetId"] = "right"
            else:
                slot["cameraPresetId"] = "default"
            slots[slot_id(anchor_id)] = slot
        for anchor_id, anchor in anchors.items():
            if anchor_id in {"left", "center", "right"} or not isinstance(anchor, dict):
                continue
            try:
                slots[slot_id(anchor_id)] = {
                    "origin": {"x": float(anchor["x"]), "y": 0.94},
                    "scale": float(old.get("scale", 1.1)),
                    "zIndex": 20,
                    "cameraPresetId": "default",
                }
            except (KeyError, TypeError, ValueError):
                continue

        frame_default = copy_frame(old.get("cameraFrame"), {"cx": 0.5, "cy": 0.5, "width": 1.0})
        frames = old.get("cameraFrames") if isinstance(old.get("cameraFrames"), dict) else {}
        camera_presets = {
            "default": copy_frame(frames.get("default"), frame_default),
            "left": copy_frame(frames.get("leftFocus"), frame_default),
            "right": copy_frame(frames.get("rightFocus"), frame_default),
        }
        migrated = {
            "label": old.get("label", scene_id),
            "layouts": {"standard": {"slots": slots}},
            "cameraPresets": camera_presets,
        }
        if old.get("figure") in {"bust", "full"}:
            migrated["figure"] = old["figure"]
        if isinstance(old.get("bg"), str):
            migrated["bg"] = old["bg"]
        if isinstance(old.get("bgVideo"), str):
            migrated["bgVideo"] = old["bgVideo"]
        if isinstance(old.get("bgVideoLoop"), bool):
            migrated["bgVideoLoop"] = old["bgVideoLoop"]
        if old.get("front") is None or isinstance(old.get("front"), str):
            migrated["front"] = old.get("front")
        result["scenes"][scene_id] = migrated
    return result


def display_mode(turn: dict) -> dict:
    insert = turn.get("insert") if isinstance(turn.get("insert"), dict) else {}
    if insert.get("kind") != "whiteboard_explain":
        return {"kind": "standard"}
    sections = insert.get("sections") if isinstance(insert.get("sections"), list) else []
    converted_sections = []
    for index in range(3):
        item = sections[index] if index < len(sections) and isinstance(sections[index], dict) else {}
        converted_sections.append({
            "heading": str(item.get("heading") or f"項目{index + 1}"),
            "bullets": [str(bullet) for bullet in item.get("bullets", []) if isinstance(bullet, str)],
        })
    conclusion = str(insert.get("conclusion") or "まとめ")
    board = {
        "title": str(insert.get("title") or "ポイントを整理します"),
        "theme": str(insert.get("theme") or "テーマ"),
        "sections": converted_sections,
        "conclusion": conclusion,
        "layout": "compact" if insert.get("layout") == "compact" else "default",
    }
    return {"kind": "whiteboard", "presenterId": turn["speaker"], "whiteboard": board}


def camera_event(turn: dict, stage: dict, speaker_is_stage: bool) -> None:
    manual = turn.get("manualCameraFrame")
    if isinstance(manual, dict):
        try:
            stage["framing"] = {"mode": "manual", "frame": copy_frame(manual, {"cx": 0.5, "cy": 0.5, "width": 1.0})}
        except (TypeError, ValueError):
            pass
    elif turn.get("focusSpeaker") and speaker_is_stage:
        stage["framing"] = {"mode": "speaker"}
    else:
        stage["framing"] = {"mode": "sceneDefault"}

    old_motion = turn.get("cameraEffects") if isinstance(turn.get("cameraEffects"), dict) else {}
    motion = {}
    if old_motion.get("zoom") == "in":
        motion["zoom"] = 0.1
    elif old_motion.get("zoom") == "out":
        motion["zoom"] = -0.1
    if old_motion.get("pan") == "left":
        motion["pan"] = {"x": -0.03, "y": 0}
    elif old_motion.get("pan") == "right":
        motion["pan"] = {"x": 0.03, "y": 0}
    if old_motion.get("tilt") == "left":
        motion["tilt"] = -2
    elif old_motion.get("tilt") == "right":
        motion["tilt"] = 2
    if motion:
        stage["cameraMotion"] = motion


def run() -> None:
    source_story = json.loads(SOURCE_STORY.read_text(encoding="utf-8"))
    source_scenes = json.loads(SOURCE_SCENES.read_text(encoding="utf-8"))
    mob_definitions = json.loads(MOBS.read_text(encoding="utf-8"))
    scenes = build_scenes(source_scenes)
    old_scenes = source_scenes.get("scenes", {})
    speakers = {str(turn.get("speaker")) for turn in source_story.get("script", []) if turn.get("speaker")}
    instances = {}
    for speaker in sorted(speakers):
        if speaker in VOICE_ONLY:
            instances[speaker] = {"voiceId": speaker, "role": "voiceOnly"}
        else:
            instances[speaker] = {"characterId": speaker, "voiceId": speaker}

    script = []
    present: set[str] = set()
    active_scene = None
    pending_exit: set[str] = set()
    normalized_mob_expressions = []
    for ordinal, old in enumerate(source_story.get("script", []), start=1):
        scene_id = old.get("scene")
        speaker = old.get("speaker")
        if not isinstance(scene_id, str) or scene_id not in old_scenes or not isinstance(speaker, str):
            raise ValueError(f"turn {ordinal}: scene/speakerを変換できません")
        old_scene = old_scenes[scene_id]
        scene_changed = scene_id != active_scene
        if scene_changed:
            active_scene = scene_id
            present.clear()
            pending_exit.clear()

        stage: dict = {}
        exits = [instance_id for instance_id in sorted(pending_exit) if instance_id in present]
        if exits:
            stage["exit"] = exits
            present.difference_update(exits)
        pending_exit.clear()

        entering = []
        for instance_id in old.get("enter", []) if isinstance(old.get("enter"), list) else []:
            if instance_id in instances and instance_id not in VOICE_ONLY and instance_id not in present:
                entering.append(instance_id)
        if speaker in instances and speaker not in VOICE_ONLY and speaker not in present:
            entering.append(speaker)
        entering = list(dict.fromkeys(entering))
        if entering:
            stage["enter"] = []
            for instance_id in entering:
                if instance_id in MAIN_CHARACTERS:
                    placement = {"mode": "slot", "slotId": slot_for_actor(old_scene, instance_id)}
                else:
                    mob_places = old_scene.get("mobs") if isinstance(old_scene.get("mobs"), dict) else {}
                    mob_place = mob_places.get(instance_id) if isinstance(mob_places.get(instance_id), dict) else {}
                    placement = {
                        "mode": "manual",
                        "origin": {"x": float(mob_place.get("x", 0.5)), "y": float(mob_place.get("y", 0.96))},
                        "scale": float(mob_place.get("scale", 1.0)),
                        "zIndex": 15,
                    }
                stage["enter"].append({"instanceId": instance_id, "placement": placement})
                present.add(instance_id)

        if speaker in present:
            patch = {}
            if isinstance(old.get("expression"), str) and old["expression"]:
                expression = old["expression"]
                if speaker in mob_definitions:
                    images = mob_definitions[speaker].get("images", {})
                    supported = {
                        name for name, pair in images.items()
                        if isinstance(pair, dict) and pair.get("closed") and pair.get("open")
                    }
                    if expression not in supported:
                        normalized_mob_expressions.append({
                            "turnId": old.get("id"),
                            "instanceId": speaker,
                            "from": expression,
                            "to": "normal",
                        })
                        expression = "normal"
                patch["expression"] = expression
            if isinstance(old.get("pose"), str) and old["pose"]:
                patch["pose"] = old["pose"]
            if patch:
                stage["update"] = {speaker: patch}

        camera_event(old, stage, speaker in present)
        if scene_changed and "framing" not in stage:
            stage["framing"] = {"mode": "sceneDefault"}

        turn = {
            "id": str(old.get("id") or f"turn-{ordinal:04d}"),
            "speaker": speaker,
            "text": str(old.get("text") or " "),
            "scene": scene_id,
            "displayMode": display_mode(old),
            "stage": stage,
        }
        for key in ("start", "end", "pause", "sentences", "voice"):
            if key in old:
                turn[key] = old[key]
        script.append(turn)
        pending_exit.update(
            instance_id
            for instance_id in old.get("exit", []) if isinstance(old.get("exit"), list) and instance_id in instances
        )

    result = {"schemaVersion": 2, "instances": instances, "script": script}
    for key in ("title", "audio"):
        if key in source_story:
            result[key] = source_story[key]
    DEST_STORY.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DEST_SCENES.write_text(json.dumps(scenes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps({
        "sourceTurns": len(source_story.get("script", [])),
        "convertedTurns": len(script),
        "unsupportedDisplayModesConvertedToStandard": ["chat", "teamchat"],
        "normalizedMobExpressions": normalized_mob_expressions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
