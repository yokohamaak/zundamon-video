"""Story Stage Schema v2 の構造・参照検証。

HTTPやレンダラーに依存しない。変換済み台本を保存する前に同じ不変条件を確認するための
小さな純粋validatorとして置く。
"""
import math


DISPLAY_MODES = {"standard", "whiteboard", "zunMeet"}


def _error(path, message):
    raise ValueError(f"{path}: {message}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point(value, path):
    if not isinstance(value, dict) or not _is_number(value.get("x")) or not _is_number(value.get("y")):
        _error(path, "x/y が有限数値のobjectである必要があります")


def _frame(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    for key in ("cx", "cy", "width"):
        if not _is_number(value.get(key)):
            _error(f"{path}.{key}", "有限数値が必要です")
    if value["width"] <= 0:
        _error(f"{path}.width", "0より大きい値が必要です")


def _placement(value, path, slots):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    mode = value.get("mode")
    if mode == "slot":
        slot_id = value.get("slotId")
        if not isinstance(slot_id, str) or not slot_id:
            _error(f"{path}.slotId", "文字列が必要です")
        if slot_id not in slots:
            _error(f"{path}.slotId", f"sceneのslotにありません: {slot_id}")
        if "offset" in value:
            _point(value["offset"], f"{path}.offset")
        return
    if mode == "manual":
        _point(value.get("origin"), f"{path}.origin")
        for key in ("scale", "zIndex"):
            if key in value and not _is_number(value[key]):
                _error(f"{path}.{key}", "有限数値が必要です")
        if "scale" in value and value["scale"] <= 0:
            _error(f"{path}.scale", "0より大きい値が必要です")
        return
    _error(f"{path}.mode", "slot または manual が必要です")


def _framing(value, path, slots, present, speaker):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    mode = value.get("mode")
    if mode in {"sceneDefault", "speaker"}:
        if mode == "speaker" and speaker not in present:
            _error(path, "speaker framingには在席中の話者個体が必要です")
        return
    if mode == "slot":
        slot_id = value.get("slotId")
        if not isinstance(slot_id, str) or slot_id not in slots:
            _error(f"{path}.slotId", "sceneに存在するslotが必要です")
        return
    if mode == "manual":
        _frame(value.get("frame"), f"{path}.frame")
        return
    _error(f"{path}.mode", "sceneDefault/speaker/slot/manual のいずれかが必要です")


def _camera_motion(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    if "zoom" in value and not _is_number(value["zoom"]):
        _error(f"{path}.zoom", "有限数値が必要です")
    if "pan" in value:
        _point(value["pan"], f"{path}.pan")
    if "tilt" in value and not _is_number(value["tilt"]):
        _error(f"{path}.tilt", "有限数値が必要です")
    if "shake" in value:
        shake = value["shake"]
        if not isinstance(shake, dict):
            _error(f"{path}.shake", "objectである必要があります")
        for key in ("strength", "duration"):
            if not _is_number(shake.get(key)) or shake[key] < 0:
                _error(f"{path}.shake.{key}", "0以上の有限数値が必要です")


def _display_mode(value, path):
    if value is None:
        return "standard"
    if not isinstance(value, dict) or value.get("kind") not in DISPLAY_MODES:
        _error(path, f"kind は {', '.join(sorted(DISPLAY_MODES))} のいずれかが必要です")
    if value["kind"] == "whiteboard":
        board = value.get("whiteboard")
        if not isinstance(board, dict):
            _error(f"{path}.whiteboard", "objectが必要です")
        for key in ("title", "theme", "conclusion"):
            if not isinstance(board.get(key), str) or not board[key]:
                _error(f"{path}.whiteboard.{key}", "空でない文字列が必要です")
        sections = board.get("sections")
        if not isinstance(sections, list) or len(sections) != 3:
            _error(f"{path}.whiteboard.sections", "3件の配列が必要です")
        for index, section in enumerate(sections):
            section_path = f"{path}.whiteboard.sections[{index}]"
            if not isinstance(section, dict) or not isinstance(section.get("heading"), str):
                _error(section_path, "headingを持つobjectが必要です")
            if not isinstance(section.get("bullets"), list) or not all(isinstance(item, str) for item in section["bullets"]):
                _error(f"{section_path}.bullets", "文字列配列が必要です")
        if "character" in board:
            _error(f"{path}.whiteboard.character", "presenterIdを使用してください")
        presenter_id = value.get("presenterId")
        if presenter_id is not None and (not isinstance(presenter_id, str) or not presenter_id):
            _error(f"{path}.presenterId", "省略または個体ID文字列が必要です")
    if value["kind"] == "zunMeet":
        meeting = value.get("zunMeet")
        if not isinstance(meeting, dict):
            _error(f"{path}.zunMeet", "objectが必要です")
        if "room" in meeting and not isinstance(meeting["room"], str):
            _error(f"{path}.zunMeet.room", "文字列が必要です")
        participants = meeting.get("participants")
        if not isinstance(participants, list) or not (1 <= len(participants) <= 4):
            _error(f"{path}.zunMeet.participants", "1〜4件の配列が必要です")
        participant_ids = []
        for index, participant in enumerate(participants):
            participant_path = f"{path}.zunMeet.participants[{index}]"
            if not isinstance(participant, dict) or not isinstance(participant.get("instanceId"), str) or not participant["instanceId"]:
                _error(participant_path, "instanceIdを持つobjectが必要です")
            participant_ids.append(participant["instanceId"])
            if "name" in participant and not isinstance(participant["name"], str):
                _error(f"{participant_path}.name", "文字列が必要です")
            for key in ("cameraOff", "muted"):
                if key in participant and not isinstance(participant[key], bool):
                    _error(f"{participant_path}.{key}", "true/falseが必要です")
        if len(set(participant_ids)) != len(participant_ids):
            _error(f"{path}.zunMeet.participants", "同じ個体を重複指定できません")
        if meeting.get("layout") is not None and meeting["layout"] not in {"focus", "grid"}:
            _error(f"{path}.zunMeet.layout", "focus/grid のいずれかが必要です")
        active_speaker = meeting.get("activeSpeakerId")
        if active_speaker is not None and active_speaker not in participant_ids:
            _error(f"{path}.zunMeet.activeSpeakerId", "participantsに含まれる個体IDが必要です")
    return value["kind"]


def _scene_slots(scenes, scene_id, path):
    if not isinstance(scenes, dict) or not isinstance(scenes.get("scenes"), dict):
        _error("scenes", "scenes objectが必要です")
    scene = scenes["scenes"].get(scene_id)
    if not isinstance(scene, dict):
        _error(path, f"sceneが存在しません: {scene_id}")
    slots = ((scene.get("layouts") or {}).get("standard") or {}).get("slots")
    if not isinstance(slots, dict):
        _error(path, f"scene {scene_id} に layouts.standard.slots が必要です")
    return slots


def validate_scene_library_v2(data):
    """v2 scene定義だけを検証する。旧sceneファイルには呼ばない。"""
    if not isinstance(data, dict) or data.get("schemaVersion") != 2:
        _error("scene library", "schemaVersion: 2 が必要です")
    scenes = data.get("scenes")
    if not isinstance(scenes, dict):
        _error("scenes", "objectが必要です")
    for scene_id, scene in scenes.items():
        if not isinstance(scene_id, str) or not scene_id or not isinstance(scene, dict):
            _error("scenes", "scene IDとscene objectが必要です")
        bg = scene.get("bg")
        bg_video = scene.get("bgVideo")
        if bg is not None and (not isinstance(bg, str) or not bg):
            _error(f"scenes.{scene_id}.bg", "省略または空でない文字列が必要です")
        if bg_video is not None and (not isinstance(bg_video, str) or not bg_video):
            _error(f"scenes.{scene_id}.bgVideo", "省略または空でない文字列が必要です")
        if not bg and not bg_video:
            _error(f"scenes.{scene_id}", "bg または bgVideo のどちらかが必要です")
        if "bgVideoLoop" in scene and not isinstance(scene["bgVideoLoop"], bool):
            _error(f"scenes.{scene_id}.bgVideoLoop", "true/falseが必要です")
        if "figure" in scene and scene["figure"] not in {"bust", "full"}:
            _error(f"scenes.{scene_id}.figure", "bust または full が必要です")
        slots = ((scene.get("layouts") or {}).get("standard") or {}).get("slots")
        if not isinstance(slots, dict):
            _error(f"scenes.{scene_id}.layouts.standard.slots", "objectが必要です")
        for slot_id, slot in slots.items():
            if not isinstance(slot_id, str) or not slot_id or not isinstance(slot, dict):
                _error(f"scenes.{scene_id}.slots", "slot IDとobjectが必要です")
            _point(slot.get("origin"), f"scenes.{scene_id}.slots.{slot_id}.origin")
            if "scale" in slot and (not _is_number(slot["scale"]) or slot["scale"] <= 0):
                _error(f"scenes.{scene_id}.slots.{slot_id}.scale", "0より大きい有限数値が必要です")
            if "zIndex" in slot and not _is_number(slot["zIndex"]):
                _error(f"scenes.{scene_id}.slots.{slot_id}.zIndex", "有限数値が必要です")
            if "previewCharacterId" in slot and (
                not isinstance(slot["previewCharacterId"], str) or not slot["previewCharacterId"]
            ):
                _error(f"scenes.{scene_id}.slots.{slot_id}.previewCharacterId", "省略または空でない文字列が必要です")
        presets = scene.get("cameraPresets", {})
        if not isinstance(presets, dict):
            _error(f"scenes.{scene_id}.cameraPresets", "objectが必要です")
        for preset_id, frame in presets.items():
            _frame(frame, f"scenes.{scene_id}.cameraPresets.{preset_id}")
        for slot_id, slot in slots.items():
            preset_id = slot.get("cameraPresetId")
            if preset_id is not None and (not isinstance(preset_id, str) or preset_id not in presets):
                _error(
                    f"scenes.{scene_id}.slots.{slot_id}.cameraPresetId",
                    "cameraPresetsに存在するIDが必要です",
                )


def validate_story_v2(data, scenes, mobs=None):
    """Story Stage Schema v2の構造、参照、turnごとの舞台状態を検証する。"""
    if not isinstance(data, dict) or data.get("schemaVersion") != 2:
        _error("story", "schemaVersion: 2 が必要です")
    instances = data.get("instances")
    turns = data.get("script")
    if not isinstance(instances, dict) or not isinstance(turns, list):
        _error("story", "instances object と script array が必要です")
    for instance_id, definition in instances.items():
        path = f"instances.{instance_id}"
        if not isinstance(instance_id, str) or not instance_id or not isinstance(definition, dict):
            _error(path, "個体IDとobjectが必要です")
        if not isinstance(definition.get("voiceId"), str) or not definition["voiceId"]:
            _error(f"{path}.voiceId", "文字列が必要です")
        role = definition.get("role", "stage")
        if role not in {"stage", "voiceOnly"}:
            _error(f"{path}.role", "stage または voiceOnly が必要です")
        if role == "stage" and (not isinstance(definition.get("characterId"), str) or not definition["characterId"]):
            _error(f"{path}.characterId", "stage個体には文字列が必要です")
        if role == "voiceOnly" and definition.get("characterId") is not None:
            _error(f"{path}.characterId", "voiceOnly個体には指定できません")

    active_scene = None
    present = {}
    # slot重なりの前後関係は個体状態として継承する。各turnのupdateだけを
    # 見ると、重なり始めた次のturnでzIndexが「消えた」扱いになるため。
    present_z_indexes = {}
    for index, turn in enumerate(turns):
        path = f"script[{index}]"
        if not isinstance(turn, dict):
            _error(path, "objectが必要です")
        for key in ("speaker", "text", "scene"):
            if not isinstance(turn.get(key), str) or not turn[key]:
                _error(f"{path}.{key}", "文字列が必要です")
        speaker = turn["speaker"]
        if speaker not in instances:
            _error(f"{path}.speaker", f"instancesにありません: {speaker}")
        if "cameraTransition" in turn and turn["cameraTransition"] not in {"smooth", "cut"}:
            _error(f"{path}.cameraTransition", "smooth または cut が必要です")
        slots = _scene_slots(scenes, turn["scene"], f"{path}.scene")
        if turn["scene"] != active_scene:
            active_scene = turn["scene"]
            present = {}
            present_z_indexes = {}
        display_kind = _display_mode(turn.get("displayMode"), f"{path}.displayMode")
        event = turn.get("stage")
        if event is not None and not isinstance(event, dict):
            _error(f"{path}.stage", "objectである必要があります")
        event = event or {}

        for enter_index, item in enumerate(event.get("enter", [])):
            enter_path = f"{path}.stage.enter[{enter_index}]"
            if not isinstance(item, dict) or not isinstance(item.get("instanceId"), str):
                _error(enter_path, "instanceIdを持つobjectが必要です")
            instance_id = item["instanceId"]
            definition = instances.get(instance_id)
            if definition is None:
                _error(f"{enter_path}.instanceId", "instancesにありません")
            if definition.get("role", "stage") != "stage":
                _error(f"{enter_path}.instanceId", "voiceOnly個体は登場できません")
            if instance_id in present:
                _error(f"{enter_path}.instanceId", "すでに在席しています")
            _placement(item.get("placement"), f"{enter_path}.placement", slots)
            present[instance_id] = item["placement"]

        for exit_index, instance_id in enumerate(event.get("exit", [])):
            exit_path = f"{path}.stage.exit[{exit_index}]"
            if not isinstance(instance_id, str) or instance_id not in instances:
                _error(exit_path, "instancesにある個体IDが必要です")
            if instance_id not in present:
                _error(exit_path, "在席していない個体は退場できません")
            del present[instance_id]
            present_z_indexes.pop(instance_id, None)

        for instance_id in event.get("reset", []):
            if not isinstance(instance_id, str) or instance_id not in present:
                _error(f"{path}.stage.reset", "在席中の個体IDだけを指定できます")
            # resolverのresetと同じく、明示した見た目上書きは解除する。
            present_z_indexes.pop(instance_id, None)

        update = event.get("update", {})
        if not isinstance(update, dict):
            _error(f"{path}.stage.update", "objectが必要です")
        for instance_id, patch in update.items():
            update_path = f"{path}.stage.update.{instance_id}"
            if instance_id not in present:
                _error(update_path, "在席中の個体だけを更新できます")
            if not isinstance(patch, dict):
                _error(update_path, "objectが必要です")
            definition = instances[instance_id]
            if "placement" in patch:
                _placement(patch["placement"], f"{update_path}.placement", slots)
                present[instance_id] = patch["placement"]
            for key in ("expression", "pose"):
                if key in patch and not isinstance(patch[key], str):
                    _error(f"{update_path}.{key}", "文字列が必要です")
            character_id = definition.get("characterId")
            if "expression" in patch and isinstance(mobs, dict) and character_id in mobs:
                images = mobs[character_id].get("images", {}) if isinstance(mobs[character_id], dict) else {}
                supported = {
                    name for name, pair in images.items()
                    if isinstance(pair, dict) and pair.get("closed") and pair.get("open")
                }
                if patch["expression"] not in supported:
                    _error(f"{update_path}.expression", f"モブ {character_id} に素材がありません: {patch['expression']}")
            if "face" in patch and patch["face"] not in {"left", "right"}:
                _error(f"{update_path}.face", "left/right のいずれかが必要です")
            if "flip" in patch and not isinstance(patch["flip"], bool):
                _error(f"{update_path}.flip", "true/falseが必要です")
            if "zIndex" in patch and not _is_number(patch["zIndex"]):
                _error(f"{update_path}.zIndex", "有限数値が必要です")
            if "zIndex" in patch:
                present_z_indexes[instance_id] = patch["zIndex"]

        if "framing" in event:
            _framing(event["framing"], f"{path}.stage.framing", slots, present, speaker)
        if "cameraMotion" in event:
            _camera_motion(event["cameraMotion"], f"{path}.stage.cameraMotion")

        occupied = {}
        for instance_id, placement in present.items():
            if placement.get("mode") != "slot":
                continue
            slot_id = placement["slotId"]
            occupied.setdefault(slot_id, []).append(instance_id)
        for slot_id, instance_ids in occupied.items():
            slot = slots[slot_id]
            if len(instance_ids) > 1 and not slot.get("allowOverlap"):
                _error(path, f"slot {slot_id} は複数個体を許可しません: {', '.join(instance_ids)}")
            if len(instance_ids) > 1 and slot.get("allowOverlap"):
                for instance_id in instance_ids:
                    if instance_id not in present_z_indexes:
                        _error(path, f"slot {slot_id} の重なりには {instance_id} のzIndexが必要です")

        if display_kind != "standard" and "framing" in event:
            # 特殊表示中のstage更新は許可するが、通常stage用構図は画面に出ない。
            # 禁止せず、呼び出し元がUI上で注意を出せるよう構造だけを検証する。
            pass
        if display_kind == "whiteboard":
            presenter_id = turn["displayMode"].get("presenterId")
            if presenter_id is not None:
                definition = instances.get(presenter_id)
                if definition is None or definition.get("role", "stage") != "stage":
                    _error(f"{path}.displayMode.presenterId", "stage個体IDが必要です")
                if presenter_id not in present:
                    _error(f"{path}.displayMode.presenterId", "在席中の個体を指定してください")
        if display_kind == "zunMeet":
            for participant in turn["displayMode"]["zunMeet"]["participants"]:
                instance_id = participant["instanceId"]
                if instance_id not in instances:
                    _error(f"{path}.displayMode.zunMeet.participants", f"instancesにありません: {instance_id}")
