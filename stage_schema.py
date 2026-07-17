"""Story Stage Schema v2 の構造・参照検証。

HTTPやレンダラーに依存しない。変換済み台本を保存する前に同じ不変条件を確認するための
小さな純粋validatorとして置く。
"""
import math
import re


DISPLAY_MODES = {"standard", "whiteboard", "zunMeet", "zunMonitor", "zunAi", "zunChat", "zunMail"}
KNOWN_STAGE_CHARACTER_IDS = {"zundamon", "metan"}
CAMERA_FRAME_MIN = 0.35
CAMERA_FRAME_MAX = 1.0
STAGE_ANIMATION_DIRECTIONS = {"auto", "left", "right", "up", "down", "instant"}


def _error(path, message):
    raise ValueError(f"{path}: {message}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _only_keys(value, allowed, path):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        _error(path, f"未対応の項目があります: {', '.join(unknown)}")


def _point(value, path):
    if not isinstance(value, dict) or not _is_number(value.get("x")) or not _is_number(value.get("y")):
        _error(path, "x/y が有限数値のobjectである必要があります")
    _only_keys(value, {"x", "y"}, path)


def _frame(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"cx", "cy", "width"}, path)
    for key in ("cx", "cy", "width"):
        if not _is_number(value.get(key)):
            _error(f"{path}.{key}", "有限数値が必要です")
    if not CAMERA_FRAME_MIN <= value["width"] <= CAMERA_FRAME_MAX:
        _error(f"{path}.width", f"{CAMERA_FRAME_MIN}〜{CAMERA_FRAME_MAX}の値が必要です")
    half = value["width"] / 2
    for key in ("cx", "cy"):
        if not half <= value[key] <= 1 - half:
            _error(f"{path}.{key}", "構図が画面内に収まる値が必要です")


def _placement(value, path, slots):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    mode = value.get("mode")
    if mode == "slot":
        _only_keys(value, {"mode", "slotId", "offset"}, path)
        slot_id = value.get("slotId")
        if not isinstance(slot_id, str) or not slot_id:
            _error(f"{path}.slotId", "文字列が必要です")
        if slot_id not in slots:
            _error(f"{path}.slotId", f"sceneのslotにありません: {slot_id}")
        if "offset" in value:
            _point(value["offset"], f"{path}.offset")
        return
    if mode == "manual":
        _only_keys(value, {"mode", "origin", "scale", "zIndex"}, path)
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
        _only_keys(value, {"mode"}, path)
        if mode == "speaker" and speaker not in present:
            _error(path, "speaker framingには在席中の話者個体が必要です")
        return
    if mode == "slot":
        _only_keys(value, {"mode", "slotId"}, path)
        slot_id = value.get("slotId")
        if not isinstance(slot_id, str) or slot_id not in slots:
            _error(f"{path}.slotId", "sceneに存在するslotが必要です")
        return
    if mode == "manual":
        _only_keys(value, {"mode", "frame"}, path)
        _frame(value.get("frame"), f"{path}.frame")
        return
    _error(f"{path}.mode", "sceneDefault/speaker/slot/manual のいずれかが必要です")


def _camera_motion(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"zoom", "pan", "tilt", "shake"}, path)
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
        _only_keys(shake, {"strength", "duration"}, f"{path}.shake")
        for key in ("strength", "duration"):
            if not _is_number(shake.get(key)) or shake[key] < 0:
                _error(f"{path}.shake.{key}", "0以上の有限数値が必要です")


def _stage_animation(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"direction"}, path)
    if "direction" in value and value["direction"] not in STAGE_ANIMATION_DIRECTIONS:
        _error(f"{path}.direction", "auto/left/right/up/down/instant のいずれかが必要です")


def _hex(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        _error(path, "#RRGGBB 形式の色が必要です")


def _display_settings(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"bubble", "subtitle", "telop", "speakerColors"}, path)
    bubble = value.get("bubble")
    if bubble is not None:
        if not isinstance(bubble, dict):
            _error(f"{path}.bubble", "objectである必要があります")
        _only_keys(bubble, {"maxChars", "fontSize", "fontFamily", "textColor", "bgColor", "borderWidth", "radius"}, f"{path}.bubble")
        if "maxChars" in bubble and bubble["maxChars"] is not None and (not _is_number(bubble["maxChars"]) or bubble["maxChars"] <= 0):
            _error(f"{path}.bubble.maxChars", "nullまたは0より大きい有限数値が必要です")
        for key in ("fontSize", "borderWidth", "radius"):
            if key in bubble and not _is_number(bubble[key]):
                _error(f"{path}.bubble.{key}", "有限数値が必要です")
        if "fontFamily" in bubble and not isinstance(bubble["fontFamily"], str):
            _error(f"{path}.bubble.fontFamily", "文字列が必要です")
        for key in ("textColor", "bgColor"):
            if key in bubble:
                _hex(bubble[key], f"{path}.bubble.{key}")
    subtitle = value.get("subtitle")
    if subtitle is not None:
        if not isinstance(subtitle, dict):
            _error(f"{path}.subtitle", "objectである必要があります")
        _only_keys(subtitle, {"fontSize", "fontFamily", "textColor", "bgColor", "bgOpacity", "border", "borderColor", "borderWidth", "bottom", "width"}, f"{path}.subtitle")
        for key in ("fontSize", "bgOpacity", "borderWidth", "bottom", "width"):
            if key in subtitle and not _is_number(subtitle[key]):
                _error(f"{path}.subtitle.{key}", "有限数値が必要です")
        if "fontFamily" in subtitle and not isinstance(subtitle["fontFamily"], str):
            _error(f"{path}.subtitle.fontFamily", "文字列が必要です")
        if "border" in subtitle and not isinstance(subtitle["border"], bool):
            _error(f"{path}.subtitle.border", "true/falseが必要です")
        for key in ("textColor", "bgColor", "borderColor"):
            if key in subtitle:
                _hex(subtitle[key], f"{path}.subtitle.{key}")
    colors = value.get("speakerColors")
    if colors is not None:
        if not isinstance(colors, dict):
            _error(f"{path}.speakerColors", "objectである必要があります")
        for key, color in colors.items():
            if not isinstance(key, str) or not key:
                _error(f"{path}.speakerColors", "空でない話者IDが必要です")
            _hex(color, f"{path}.speakerColors.{key}")
    telop = value.get("telop")
    if telop is not None:
        if not isinstance(telop, dict):
            _error(f"{path}.telop", "objectである必要があります")
        _only_keys(telop, {"x", "y", "size"}, f"{path}.telop")
        for key in ("x", "y", "size"):
            if key in telop and not _is_number(telop[key]):
                _error(f"{path}.telop.{key}", "有限数値が必要です")
        for key in ("x", "y"):
            if key in telop and not 0 <= telop[key] <= 1:
                _error(f"{path}.telop.{key}", "0〜1の値が必要です")
        if "size" in telop and not 0.5 <= telop["size"] <= 3:
            _error(f"{path}.telop.size", "0.5〜3の値が必要です")


def _caption(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"text", "x", "y", "size"}, path)
    if not isinstance(value.get("text"), str) or not value["text"].strip():
        _error(f"{path}.text", "空でない文字列が必要です")
    for key in ("x", "y", "size"):
        if key in value and not _is_number(value[key]):
            _error(f"{path}.{key}", "有限数値が必要です")
    for key in ("x", "y"):
        if key in value and not 0 <= value[key] <= 1:
            _error(f"{path}.{key}", "0〜1の値が必要です")
    if "size" in value and not 0.5 <= value["size"] <= 3:
        _error(f"{path}.size", "0.5〜3の値が必要です")


def _stage_effects(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"impactLines", "zoomPunch", "quoteFreeze", "flashback", "visionNoise", "irisOut"}, path)
    specs = {
        "flashback": {
            "keys": {"enabled"},
            "ranges": {},
        },
        "impactLines": {
            "keys": {"enabled", "cx", "cy", "count", "thickness", "opacity", "innerRadius", "start", "end"},
            "ranges": {
                "cx": (0, 1), "cy": (0, 1), "count": (12, 180), "thickness": (0.3, 5),
                "opacity": (0, 1), "innerRadius": (0, 0.5), "start": (0, 8), "end": (0, 8),
            },
        },
        "zoomPunch": {
            "keys": {"enabled", "scale", "duration", "borderStrength"},
            "ranges": {"scale": (1.02, 1.4), "duration": (0.08, 0.5), "borderStrength": (0, 2)},
        },
        "quoteFreeze": {
            "keys": {"enabled", "fadeIn", "fadeOutStart", "fadeOutDuration", "backdropOpacity"},
            "ranges": {"fadeIn": (0.05, 0.4), "fadeOutStart": (0, 1), "fadeOutDuration": (0.05, 0.4), "backdropOpacity": (0, 0.6)},
        },
        "visionNoise": {
            "keys": {"enabled", "type", "strength", "scanline", "glitch", "flicker", "tint"},
            "ranges": {"strength": (0, 1), "scanline": (0, 1), "glitch": (0, 1), "flicker": (0, 1)},
        },
        "irisOut": {
            "keys": {"enabled", "cx", "cy", "startRadius", "closeStart", "closeEnd", "color"},
            "ranges": {"cx": (0, 1), "cy": (0, 1), "startRadius": (0.15, 1.3), "closeStart": (0, 8), "closeEnd": (0, 8)},
        },
    }
    for key, spec in specs.items():
        if key not in value:
            continue
        item = value[key]
        item_path = f"{path}.{key}"
        if isinstance(item, bool):
            continue
        if not isinstance(item, dict):
            _error(item_path, "true/falseまたはobjectが必要です")
        _only_keys(item, spec["keys"], item_path)
        if "enabled" in item and not isinstance(item["enabled"], bool):
            _error(f"{item_path}.enabled", "true/falseが必要です")
        if key == "visionNoise" and "type" in item and item["type"] not in {"future", "snow", "vhs", "glitch"}:
            _error(f"{item_path}.type", "future/snow/vhs/glitch のいずれかが必要です")
        if key == "visionNoise" and "tint" in item:
            _hex(item["tint"], f"{item_path}.tint")
        if key == "irisOut" and "color" in item:
            _hex(item["color"], f"{item_path}.color")
        for param, bounds in spec["ranges"].items():
            if param not in item:
                continue
            if not _is_number(item[param]):
                _error(f"{item_path}.{param}", "有限数値が必要です")
            low, high = bounds
            if not low <= item[param] <= high:
                _error(f"{item_path}.{param}", f"{low}〜{high}の値が必要です")


def _subtitle_style(value, path):
    if not isinstance(value, dict):
        _error(path, "objectである必要があります")
    _only_keys(value, {"fontSize", "textColor", "boxBorder", "boxBorderColor", "boxBorderWidth"}, path)
    for key in ("fontSize", "boxBorderWidth"):
        if key in value and not _is_number(value[key]):
            _error(f"{path}.{key}", "有限数値が必要です")
    if "boxBorder" in value and not isinstance(value["boxBorder"], bool):
        _error(f"{path}.boxBorder", "true/falseが必要です")
    for key in ("textColor", "boxBorderColor"):
        if key in value:
            _hex(value[key], f"{path}.{key}")


def _sentences(value, path):
    if not isinstance(value, list):
        _error(path, "配列が必要です")
    previous_end = -float("inf")
    for index, sentence in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(sentence, dict):
            _error(item_path, "objectが必要です")
        _only_keys(sentence, {"text", "start", "end"}, item_path)
        if not isinstance(sentence.get("text"), str):
            _error(f"{item_path}.text", "文字列が必要です")
        if not _is_number(sentence.get("start")) or not _is_number(sentence.get("end")):
            _error(item_path, "start/end は有限数値が必要です")
        if sentence["end"] < sentence["start"] or sentence["start"] < previous_end:
            _error(item_path, "時刻は前の文以降で start <= end が必要です")
        previous_end = sentence["end"]


def _turn_se(value, path):
    if not isinstance(value, list):
        _error(path, "配列が必要です")
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            _error(item_path, "objectが必要です")
        _only_keys(item, {"file", "at", "volume"}, item_path)
        if not isinstance(item.get("file"), str) or not item["file"]:
            _error(f"{item_path}.file", "空でない文字列が必要です")
        for key in ("at", "volume"):
            if key in item and not _is_number(item[key]):
                _error(f"{item_path}.{key}", "有限数値が必要です")


def _bgm_regions(value, path):
    if not isinstance(value, list):
        _error(path, "配列が必要です")
    for index, region in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(region, dict):
            _error(item_path, "objectが必要です")
        _only_keys(region, {"start", "end", "file", "volume", "fadeIn", "fadeOut"}, item_path)
        if not isinstance(region.get("file"), str) or not region["file"]:
            _error(f"{item_path}.file", "空でない文字列が必要です")
        if not _is_number(region.get("start")) or not _is_number(region.get("end")) or region["end"] <= region["start"]:
            _error(item_path, "start < end の有限数値が必要です")
        for key in ("volume", "fadeIn", "fadeOut"):
            if key in region and (not _is_number(region[key]) or region[key] < 0):
                _error(f"{item_path}.{key}", "0以上の有限数値が必要です")


def _overlays(value, path):
    if not isinstance(value, list):
        _error(path, "配列が必要です")
    for index, overlay in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(overlay, dict):
            _error(item_path, "objectが必要です")
        _only_keys(overlay, {"id", "kind", "layer", "src", "text", "textColor", "bgColor", "bgOpacity", "borderColor", "borderOpacity", "fontSize", "centerX", "x", "y", "w", "opacity", "z", "start", "end"}, item_path)
        if not isinstance(overlay.get("id"), str) or not overlay["id"]:
            _error(f"{item_path}.id", "空でない文字列が必要です")
        if overlay.get("kind") not in {"image", "text"}:
            _error(f"{item_path}.kind", "image または text が必要です")
        if overlay.get("layer", "normal") != "normal":
            _error(f"{item_path}.layer", "v2では normal のみ対応しています")
        if overlay["kind"] == "image" and (not isinstance(overlay.get("src"), str) or not overlay["src"]):
            _error(f"{item_path}.src", "imageには空でないsrcが必要です")
        if overlay["kind"] == "text" and not isinstance(overlay.get("text"), str):
            _error(f"{item_path}.text", "textには文字列が必要です")
        for key in ("x", "y", "w"):
            if not _is_number(overlay.get(key)):
                _error(f"{item_path}.{key}", "有限数値が必要です")
        for key in ("bgOpacity", "borderOpacity", "fontSize", "opacity", "z"):
            if key in overlay and not _is_number(overlay[key]):
                _error(f"{item_path}.{key}", "有限数値が必要です")
        if "centerX" in overlay and not isinstance(overlay["centerX"], bool):
            _error(f"{item_path}.centerX", "true/falseが必要です")
        for key in ("textColor", "bgColor", "borderColor"):
            if key in overlay:
                _hex(overlay[key], f"{item_path}.{key}")
        for key in ("start", "end"):
            anchor = overlay.get(key)
            if not isinstance(anchor, dict):
                _error(f"{item_path}.{key}", "turnId/atを持つobjectが必要です")
            _only_keys(anchor, {"turnId", "at"}, f"{item_path}.{key}")
            if not isinstance(anchor.get("turnId"), str) or not anchor["turnId"] or not _is_number(anchor.get("at")):
                _error(f"{item_path}.{key}", "turnIdと有限数値atが必要です")


def _display_mode(value, path):
    if value is None:
        return "standard"
    if not isinstance(value, dict) or value.get("kind") not in DISPLAY_MODES:
        _error(path, f"kind は {', '.join(sorted(DISPLAY_MODES))} のいずれかが必要です")
    if value["kind"] == "standard":
        _only_keys(value, {"kind"}, path)
    if value["kind"] == "zunMonitor":
        _only_keys(value, {"kind", "monitor"}, path)
        monitor = value.get("monitor")
        if not isinstance(monitor, dict) or monitor.get("kind") not in {"warning", "ok"}:
            _error(f"{path}.monitor", "kind=warning/ok のobjectが必要です")
        _only_keys(monitor, {"kind", "width", "fontScale", "bg", "backdropBg", "backdropImage", "title", "text"}, f"{path}.monitor")
        if monitor["kind"] == "warning" and "text" not in monitor:
            _error(f"{path}.monitor.text", "warningにはtextが必要です")
        for key in ("title", "text", "bg", "backdropBg", "backdropImage"):
            if key in monitor and not isinstance(monitor[key], str):
                _error(f"{path}.monitor.{key}", "文字列が必要です")
        for key in ("width", "fontScale"):
            if key in monitor and not _is_number(monitor[key]):
                _error(f"{path}.monitor.{key}", "有限数値が必要です")
    if value["kind"] == "zunAi":
        _only_keys(value, {"kind", "chat"}, path)
        chat = value.get("chat")
        if not isinstance(chat, dict) or chat.get("kind") != "chat":
            _error(f"{path}.chat", "kind=chat のobjectが必要です")
        _only_keys(chat, {"kind", "width", "fontScale", "bg", "backdropBg", "backdropImage", "user", "ai", "highlight"}, f"{path}.chat")
        if not isinstance(chat.get("user"), str):
            _error(f"{path}.chat.user", "文字列が必要です")
        if not isinstance(chat.get("ai"), list) or not all(isinstance(item, str) for item in chat["ai"]):
            _error(f"{path}.chat.ai", "文字列配列が必要です")
        if "highlight" in chat and (not isinstance(chat["highlight"], int) or chat["highlight"] < 0):
            _error(f"{path}.chat.highlight", "0以上の整数が必要です")
        for key in ("bg", "backdropBg", "backdropImage"):
            if key in chat and not isinstance(chat[key], str):
                _error(f"{path}.chat.{key}", "文字列が必要です")
        for key in ("width", "fontScale"):
            if key in chat and not _is_number(chat[key]):
                _error(f"{path}.chat.{key}", "有限数値が必要です")
    if value["kind"] == "zunChat":
        _only_keys(value, {"kind", "teamchat"}, path)
        teamchat = value.get("teamchat")
        if not isinstance(teamchat, dict) or teamchat.get("kind") != "teamchat":
            _error(f"{path}.teamchat", "kind=teamchat のobjectが必要です")
        _only_keys(teamchat, {"kind", "width", "fontScale", "bg", "backdropBg", "backdropImage", "channel", "messages"}, f"{path}.teamchat")
        if "channel" in teamchat and not isinstance(teamchat["channel"], str):
            _error(f"{path}.teamchat.channel", "文字列が必要です")
        messages = teamchat.get("messages")
        if not isinstance(messages, list):
            _error(f"{path}.teamchat.messages", "配列が必要です")
        for index, message in enumerate(messages):
            message_path = f"{path}.teamchat.messages[{index}]"
            if not isinstance(message, dict):
                _error(message_path, "objectが必要です")
            _only_keys(message, {"from", "text", "highlight"}, message_path)
            if not isinstance(message.get("from"), str) or not isinstance(message.get("text"), str):
                _error(message_path, "from/text文字列が必要です")
            if "highlight" in message and not isinstance(message["highlight"], bool):
                _error(f"{message_path}.highlight", "true/falseが必要です")
        for key in ("bg", "backdropBg", "backdropImage"):
            if key in teamchat and not isinstance(teamchat[key], str):
                _error(f"{path}.teamchat.{key}", "文字列が必要です")
        for key in ("width", "fontScale"):
            if key in teamchat and not _is_number(teamchat[key]):
                _error(f"{path}.teamchat.{key}", "有限数値が必要です")
    if value["kind"] == "zunMail":
        _only_keys(value, {"kind", "mailer"}, path)
        mailer = value.get("mailer")
        if not isinstance(mailer, dict) or mailer.get("kind") != "mailer":
            _error(f"{path}.mailer", "kind=mailer のobjectが必要です")
        _only_keys(mailer, {"kind", "width", "fontScale", "bg", "backdropBg", "backdropImage", "from", "fromAddr", "subject", "body", "time"}, f"{path}.mailer")
        for key in ("subject", "body"):
            if not isinstance(mailer.get(key), str):
                _error(f"{path}.mailer.{key}", "文字列が必要です")
        for key in ("from", "fromAddr", "time", "bg", "backdropBg", "backdropImage"):
            if key in mailer and not isinstance(mailer[key], str):
                _error(f"{path}.mailer.{key}", "文字列が必要です")
        for key in ("width", "fontScale"):
            if key in mailer and not _is_number(mailer[key]):
                _error(f"{path}.mailer.{key}", "有限数値が必要です")
    if value["kind"] == "whiteboard":
        _only_keys(value, {"kind", "presenterId", "whiteboard"}, path)
        board = value.get("whiteboard")
        if not isinstance(board, dict):
            _error(f"{path}.whiteboard", "objectが必要です")
        _only_keys(board, {"title", "theme", "sections", "conclusion", "layout"}, f"{path}.whiteboard")
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
            _only_keys(section, {"heading", "bullets"}, section_path)
            if not isinstance(section.get("bullets"), list) or not all(isinstance(item, str) for item in section["bullets"]):
                _error(f"{section_path}.bullets", "文字列配列が必要です")
        if "character" in board:
            _error(f"{path}.whiteboard.character", "presenterIdを使用してください")
        if "layout" in board and board["layout"] not in {"default", "compact"}:
            _error(f"{path}.whiteboard.layout", "default/compact のいずれかが必要です")
        presenter_id = value.get("presenterId")
        if presenter_id is not None and (not isinstance(presenter_id, str) or not presenter_id):
            _error(f"{path}.presenterId", "省略または個体ID文字列が必要です")
    if value["kind"] == "zunMeet":
        _only_keys(value, {"kind", "zunMeet"}, path)
        meeting = value.get("zunMeet")
        if not isinstance(meeting, dict):
            _error(f"{path}.zunMeet", "objectが必要です")
        _only_keys(meeting, {"room", "layout", "activeSpeakerId", "participants"}, f"{path}.zunMeet")
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
            _only_keys(participant, {"instanceId", "name", "cameraOff", "muted"}, participant_path)
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
    _only_keys(data, {"schemaVersion", "scenes"}, "scene library")
    for scene_id, scene in scenes.items():
        if not isinstance(scene_id, str) or not scene_id or not isinstance(scene, dict):
            _error("scenes", "scene IDとscene objectが必要です")
        scene_path = f"scenes.{scene_id}"
        _only_keys(scene, {"label", "bg", "bgVideo", "bgVideoLoop", "bgBlur", "front", "figure", "layouts", "cameraPresets"}, scene_path)
        if "label" in scene and (not isinstance(scene["label"], str) or not scene["label"]):
            _error(f"{scene_path}.label", "省略または空でない文字列が必要です")
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
        if "bgBlur" in scene and (not _is_number(scene["bgBlur"]) or scene["bgBlur"] < 0):
            _error(f"scenes.{scene_id}.bgBlur", "0以上の有限数値が必要です")
        if "front" in scene and scene["front"] is not None and (not isinstance(scene["front"], str) or not scene["front"]):
            _error(f"scenes.{scene_id}.front", "省略、null、または空でない文字列が必要です")
        if "figure" in scene and scene["figure"] not in {"bust", "full"}:
            _error(f"scenes.{scene_id}.figure", "bust または full が必要です")
        layouts = scene.get("layouts")
        if not isinstance(layouts, dict):
            _error(f"scenes.{scene_id}.layouts", "objectが必要です")
        _only_keys(layouts, {"standard"}, f"scenes.{scene_id}.layouts")
        standard = layouts.get("standard")
        if not isinstance(standard, dict):
            _error(f"scenes.{scene_id}.layouts.standard", "objectが必要です")
        _only_keys(standard, {"slots"}, f"scenes.{scene_id}.layouts.standard")
        slots = standard.get("slots")
        if not isinstance(slots, dict):
            _error(f"scenes.{scene_id}.layouts.standard.slots", "objectが必要です")
        for slot_id, slot in slots.items():
            if not isinstance(slot_id, str) or not slot_id or not isinstance(slot, dict):
                _error(f"scenes.{scene_id}.slots", "slot IDとobjectが必要です")
            slot_path = f"scenes.{scene_id}.slots.{slot_id}"
            _only_keys(slot, {"origin", "scale", "zIndex", "cameraPresetId", "allowOverlap", "previewCharacterId"}, slot_path)
            _point(slot.get("origin"), f"scenes.{scene_id}.slots.{slot_id}.origin")
            if "scale" in slot and (not _is_number(slot["scale"]) or slot["scale"] <= 0):
                _error(f"scenes.{scene_id}.slots.{slot_id}.scale", "0より大きい有限数値が必要です")
            if "zIndex" in slot and not _is_number(slot["zIndex"]):
                _error(f"scenes.{scene_id}.slots.{slot_id}.zIndex", "有限数値が必要です")
            if "allowOverlap" in slot and not isinstance(slot["allowOverlap"], bool):
                _error(f"scenes.{scene_id}.slots.{slot_id}.allowOverlap", "true/falseが必要です")
            if "previewCharacterId" in slot and (
                not isinstance(slot["previewCharacterId"], str) or not slot["previewCharacterId"]
            ):
                _error(f"scenes.{scene_id}.slots.{slot_id}.previewCharacterId", "省略または空でない文字列が必要です")
        presets = scene.get("cameraPresets", {})
        if not isinstance(presets, dict):
            _error(f"scenes.{scene_id}.cameraPresets", "objectが必要です")
        for preset_id, frame in presets.items():
            if not isinstance(preset_id, str) or not preset_id:
                _error(f"scenes.{scene_id}.cameraPresets", "構図IDは空でない文字列が必要です")
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
    _only_keys(data, {"schemaVersion", "title", "audio", "bgm", "overlays", "displaySettings", "idleFace", "instances", "script"}, "story")
    for key in ("title", "audio"):
        if key in data and not isinstance(data[key], str):
            _error(f"story.{key}", "文字列が必要です")
    if "displaySettings" in data:
        _display_settings(data["displaySettings"], "story.displaySettings")
    if "idleFace" in data and data["idleFace"] not in {"normal", "hold"}:
        _error("story.idleFace", "normal または hold が必要です")
    if "bgm" in data:
        _bgm_regions(data["bgm"], "story.bgm")
    if "overlays" in data:
        _overlays(data["overlays"], "story.overlays")
    instances = data.get("instances")
    turns = data.get("script")
    if not isinstance(instances, dict) or not isinstance(turns, list):
        _error("story", "instances object と script array が必要です")
    for instance_id, definition in instances.items():
        path = f"instances.{instance_id}"
        if not isinstance(instance_id, str) or not instance_id or not isinstance(definition, dict):
            _error(path, "個体IDとobjectが必要です")
        _only_keys(definition, {"characterId", "voiceId", "role", "label"}, path)
        if not isinstance(definition.get("voiceId"), str) or not definition["voiceId"]:
            _error(f"{path}.voiceId", "文字列が必要です")
        role = definition.get("role", "stage")
        if role not in {"stage", "voiceOnly"}:
            _error(f"{path}.role", "stage または voiceOnly が必要です")
        if role == "stage" and (not isinstance(definition.get("characterId"), str) or not definition["characterId"]):
            _error(f"{path}.characterId", "stage個体には文字列が必要です")
        if role == "stage" and definition["characterId"] not in KNOWN_STAGE_CHARACTER_IDS and (
            not isinstance(mobs, dict) or definition["characterId"] not in mobs
        ):
            _error(f"{path}.characterId", f"描画素材がありません: {definition['characterId']}")
        if role == "voiceOnly" and definition.get("characterId") is not None:
            _error(f"{path}.characterId", "voiceOnly個体には指定できません")
        if "label" in definition and (not isinstance(definition["label"], str) or not definition["label"]):
            _error(f"{path}.label", "省略または空でない文字列が必要です")

    active_scene = None
    present = {}
    # slot重なりの前後関係は個体状態として継承する。各turnのupdateだけを
    # 見ると、重なり始めた次のturnでzIndexが「消えた」扱いになるため。
    present_z_indexes = {}
    for index, turn in enumerate(turns):
        path = f"script[{index}]"
        if not isinstance(turn, dict):
            _error(path, "objectが必要です")
        _only_keys(turn, {
            "id", "speaker", "text", "scene", "start", "end", "pause", "noLipSync", "se",
            "hideCharacters", "hideBubble", "subtitleMode", "subtitleStyle", "continueBubble",
            "bubbleMaxChars", "disableAutoBubbleSplit", "sentences", "transition", "caption", "effects", "cameraTransition",
            "displayMode", "stage",
        }, path)
        for key in ("speaker", "text", "scene"):
            if not isinstance(turn.get(key), str) or not turn[key]:
                _error(f"{path}.{key}", "文字列が必要です")
        if not isinstance(turn.get("id"), str) or not turn["id"]:
            _error(f"{path}.id", "空でない文字列が必要です")
        for key in ("start", "end", "pause"):
            if key in turn and (not _is_number(turn[key]) or (key == "pause" and turn[key] < 0)):
                _error(f"{path}.{key}", "0以上の有限数値が必要です")
        if "start" in turn and "end" in turn and turn["end"] < turn["start"]:
            _error(path, "start <= end が必要です")
        for key in ("noLipSync", "hideCharacters", "hideBubble", "continueBubble", "disableAutoBubbleSplit"):
            if key in turn and not isinstance(turn[key], bool):
                _error(f"{path}.{key}", "true/falseが必要です")
        if "subtitleMode" in turn and turn["subtitleMode"] != "subtitle":
            _error(f"{path}.subtitleMode", "subtitle が必要です")
        if "subtitleStyle" in turn:
            _subtitle_style(turn["subtitleStyle"], f"{path}.subtitleStyle")
        if "bubbleMaxChars" in turn and (not _is_number(turn["bubbleMaxChars"]) or turn["bubbleMaxChars"] <= 0):
            _error(f"{path}.bubbleMaxChars", "0より大きい有限数値が必要です")
        if "sentences" in turn:
            _sentences(turn["sentences"], f"{path}.sentences")
        if "se" in turn:
            _turn_se(turn["se"], f"{path}.se")
        if "caption" in turn:
            _caption(turn["caption"], f"{path}.caption")
        if "effects" in turn:
            _stage_effects(turn["effects"], f"{path}.effects")
        if "transition" in turn and turn["transition"] not in {"cut", "fade-black", "fade-white"}:
            _error(f"{path}.transition", "cut / fade-black / fade-white のいずれかが必要です")
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
        _only_keys(event, {"enter", "exit", "update", "reset", "framing", "cameraMotion"}, f"{path}.stage")

        enter = event.get("enter", [])
        if not isinstance(enter, list):
            _error(f"{path}.stage.enter", "配列が必要です")
        for enter_index, item in enumerate(enter):
            enter_path = f"{path}.stage.enter[{enter_index}]"
            if not isinstance(item, dict) or not isinstance(item.get("instanceId"), str):
                _error(enter_path, "instanceIdを持つobjectが必要です")
            _only_keys(item, {"instanceId", "placement", "animation"}, enter_path)
            instance_id = item["instanceId"]
            definition = instances.get(instance_id)
            if definition is None:
                _error(f"{enter_path}.instanceId", "instancesにありません")
            if definition.get("role", "stage") != "stage":
                _error(f"{enter_path}.instanceId", "voiceOnly個体は登場できません")
            if instance_id in present:
                _error(f"{enter_path}.instanceId", "すでに在席しています")
            _placement(item.get("placement"), f"{enter_path}.placement", slots)
            if "animation" in item:
                _stage_animation(item["animation"], f"{enter_path}.animation")
            present[instance_id] = item["placement"]

        exit_ids = event.get("exit", [])
        if not isinstance(exit_ids, list):
            _error(f"{path}.stage.exit", "配列が必要です")
        for exit_index, item in enumerate(exit_ids):
            exit_path = f"{path}.stage.exit[{exit_index}]"
            if isinstance(item, dict):
                _only_keys(item, {"instanceId", "animation"}, exit_path)
                instance_id = item.get("instanceId")
                if "animation" in item:
                    _stage_animation(item["animation"], f"{exit_path}.animation")
            else:
                instance_id = item
            if not isinstance(instance_id, str) or instance_id not in instances:
                _error(exit_path, "instancesにある個体IDが必要です")
            if instance_id not in present:
                _error(exit_path, "在席していない個体は退場できません")
            del present[instance_id]
            present_z_indexes.pop(instance_id, None)

        reset_ids = event.get("reset", [])
        if not isinstance(reset_ids, list):
            _error(f"{path}.stage.reset", "配列が必要です")
        for instance_id in reset_ids:
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
            _only_keys(patch, {"placement", "expression", "pose", "face", "flip", "zIndex"}, update_path)
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

    turn_ids = [turn.get("id") for turn in turns]
    if len(turn_ids) != len(set(turn_ids)):
        _error("script", "turn.id を重複指定できません")
    for index, overlay in enumerate(data.get("overlays", [])):
        for key in ("start", "end"):
            turn_id = overlay[key]["turnId"]
            if turn_id not in turn_ids:
                _error(f"story.overlays[{index}].{key}.turnId", "scriptに存在するturn.idが必要です")
