"""
ストーリーエディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/story-01.json をブラウザで台本編集する。
turn の並び・話者・セリフ・場面・表情・演出・insertを編集できる。

使い方: python story_editor.py [--port 8771]
"""
import argparse
import atexit
import base64
import binascii
import json
import os
import re
import signal
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import expression_editor as expression_editor_module
import pose_editor as pose_editor_module
import scene_editor as scene_editor_module

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
from tts_voicevox import sync_kanji_readings_to_voicevox  # noqa: E402
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
STORY_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-01.json")
SCENES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-scenes.json")
EXPRESSIONS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "expressions.json")
SE_MAP_JSON = os.path.join(VIDEO_PUBLIC_DIR, "se-map.json")
READINGS_JSON = os.path.join(ROOT_DIR, "config", "readings.json")
READINGS_COMMENT = (
    "英字→カタカナ読み(音声専用・字幕は英字のまま)。ここに書くと組み込み辞書を上書き/追記します。"
    "キーは英字語、値はカタカナ。"
)
KANJI_READINGS_JSON = os.path.join(ROOT_DIR, "config", "kanji_readings.json")
KANJI_READINGS_COMMENT = (
    "漢字の文脈依存の読み修正（例: 「あの方」が「あのほう」と誤読される場合の是正）。"
    "音声生成時にVOICEVOXのユーザー辞書へ同期される。キーは表層形、値はカタカナ発音。"
)
BGM_DIR = os.path.join(VIDEO_PUBLIC_DIR, "bgm")
SE_DIR = os.path.join(VIDEO_PUBLIC_DIR, "se")
OVERLAYS_DIR = os.path.join(VIDEO_PUBLIC_DIR, "overlays")
BACKGROUND_DIR = os.path.join(VIDEO_PUBLIC_DIR, "background")
_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# StoryVideo.tsx の staticFile() が参照する video/public/ 配下のアセット。
# /preview-assets/<path> として配信する（パストラバーサル防止付き）。
# 許可するトップレベルディレクトリ名 or ファイル名の集合。
_PREVIEW_ASSET_DIRS = {"avatars", "background", "mobs", "bgm", "se", "fonts", "overlays"}
_PREVIEW_ASSET_FILES = {"story-scenes.json", "expressions.json", "poses.json", "se-map.json",
                        "mobs.json", "noise.png", "story-01.wav", "story-01.mp3",
                        "story.wav", "story.mp3"}

MOBS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "mobs.json")
# mobs.json が無いときのフォールバック（video/src/StoryVideo.tsx の DEFAULT_MOBS と同値）。
DEFAULT_MOBS = {
    "営業": {
        "scale": 0.85,
        "anchor": {"x": 0.5, "y": 0.99},
        "images": {
            "normal": {"closed": "mobs/mob_normal.png", "open": "mobs/mob_normal.png"},
            "agitated": {"closed": "mobs/mob_panic.png", "open": "mobs/mob_panic.png"},
        },
    },
    "部長": {
        "scale": 0.62,
        "anchor": {"x": 0.5, "y": 0.82},
        "images": {
            "normal": {"closed": "mobs/manager_normal.png", "open": "mobs/manager_normal.png"},
            "agitated": {"closed": "mobs/manager_angry.png", "open": "mobs/manager_angry.png"},
        },
    },
}


def _load_mobs():
    try:
        with open(MOBS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_MOBS)


def _save_mobs(data):
    if not isinstance(data, dict):
        raise ValueError("mobs はオブジェクトである必要があります")
    for name, d in data.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("モブ名が不正です")
        if not isinstance(d, dict) or not isinstance(d.get("images"), dict):
            raise ValueError(f"モブ定義が不正です: {name}")
        for state, pair in d["images"].items():
            if not isinstance(pair, dict) or not pair.get("closed") or not pair.get("open"):
                raise ValueError(f"{name}/{state} の画像(closed/open)が不正です")
    with open(MOBS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# 話者一覧（zundamon/metan は StoryVideo.tsx の CHARACTERS 側でハードコード。
# モブは mobs.json から動的に取得し、新規追加が即座に選択肢へ反映されるようにする）
BASE_SPEAKERS = [
    "zundamon", "metan", "AI",
    "troublemaker_male_normal", "troublemaker_male_creepy",
    "troublemaker_female_normal", "troublemaker_female_creepy",
]
NARRATION_SPEAKERS = ["棒読み男", "棒読み女"]

# 話者アイコン（/preview-assets/<path> でアクセスできる video/public 配下の相対パス）
BASE_SPEAKER_ICONS = {
    "zundamon": "avatars/zundamon/icon.png",
    "metan": "avatars/metan/icon.png",
    "AI": None,
    "troublemaker_male_normal": None,
    "troublemaker_male_creepy": None,
    "troublemaker_female_normal": None,
    "troublemaker_female_creepy": None,
}


def _current_speakers_and_icons():
    mobs = _load_mobs()
    speakers = list(BASE_SPEAKERS)
    icons = dict(BASE_SPEAKER_ICONS)
    for name, d in mobs.items():
        speakers.append(name)
        normal = (d.get("images") or {}).get("normal") or {}
        icons[name] = normal.get("closed")
    return speakers, icons

# 組み込み5種（フォールバック用・expressions.json が読めない場合に使用）
EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]
# 組み込み5種（先頭順序固定用）
BUILTIN_EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]
INSERT_KINDS = ["warning", "ok", "chat", "teamchat", "mailer", "videocall"]

_CT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _load_page():
    path = os.path.join(ROOT_DIR, "story_editor.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_pose_page():
    html = pose_editor_module._load_page()
    inject = (
        "<script>"
        'window.POSE_EDITOR_EXPORT_ENDPOINT="/api/pose-export";'
        "</script>"
    )
    return html.replace("</head>", inject + "\n</head>", 1)


def _load_expression_page():
    html = expression_editor_module._load_page()
    inject = (
        "<script>"
        'window.EXPRESSION_EDITOR_API_BASE="/api/expression";'
        'window.EXPRESSION_EDITOR_EXPORT_ENDPOINT="/api/expression-export";'
        "</script>"
    )
    return html.replace("</head>", inject + "\n</head>", 1)


def _load_scene_page():
    html = scene_editor_module._load_page()
    inject = (
        "<script>"
        'window.SCENE_EDITOR_API_BASE="/api/scene";'
        "</script>"
    )
    return html.replace("</head>", inject + "\n</head>", 1)


def _load_story():
    if not os.path.exists(STORY_JSON):
        return {"title": "", "script": []}
    with open(STORY_JSON, encoding="utf-8") as f:
        return json.load(f)


def _validate_story(data):
    """最低限の検証。script 配列必須・各 turn に speaker/text/scene。"""
    if not isinstance(data, dict):
        raise ValueError("story は object である必要があります")
    if "script" not in data or not isinstance(data["script"], list):
        raise ValueError("script 配列が必要です")
    for i, turn in enumerate(data["script"]):
        if not isinstance(turn, dict):
            raise ValueError(f"turn[{i}] が不正")
        for field in ("speaker", "text", "scene"):
            if field not in turn or not isinstance(turn[field], str):
                raise ValueError(f"turn[{i}]: {field}(文字列)が必要です")


def _save_story(data):
    """検証してから story-01.json に書き戻す。ensure_ascii=False。"""
    _validate_story(data)
    with open(STORY_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _safe_export_filename(title):
    """タイトルから書き出しファイル名を作る。OSで問題になりうる文字だけ除去する。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "", (title or "")).strip()
    cleaned = cleaned or "story"
    return cleaned + ".mp4"


def _safe_image_filename(name, default_stem="image"):
    """画像アップロード用のファイル名サニタイズ。
    OSで問題になりうる記号だけ除去し、日本語名はそのまま残す
    （全角文字を"_"に潰すと別名同士が同じファイル名に衝突し、
    一方の画像がもう一方で上書きされるバグになるため）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "", (name or "")).strip(". ")
    cleaned = cleaned or default_stem
    ext = os.path.splitext(cleaned)[1].lower()
    if ext not in _IMAGE_EXTS:
        cleaned += ".png"
    return cleaned


def _save_base64_image(dest_dir, filename, data_url):
    """base64 dataURL をデコードして dest_dir/filename に保存する。"""
    m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", data_url or "", re.S)
    if not m:
        raise ValueError("dataUrl の形式が不正です")
    raw = base64.b64decode(m.group(1))
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, filename), "wb") as f:
        f.write(raw)


def _load_scenes_keys():
    """story-scenes.json の scenes キー一覧を返す。読み込み失敗時は空リスト。"""
    if not os.path.exists(SCENES_JSON):
        return []
    try:
        with open(SCENES_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return list(d.get("scenes", {}).keys())
    except Exception:
        return []


def _load_expression_keys():
    """expressions.json のキー一覧を返す。
    組み込み5種を先頭に固定し、追加表情をアルファベット順で続ける。
    ファイルが読めない場合は EXPRESSIONS 定数にフォールバック。
    """
    if not os.path.exists(EXPRESSIONS_JSON):
        return list(EXPRESSIONS)
    try:
        with open(EXPRESSIONS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        # 全キャラのキーを集約（重複排除）
        all_keys = set()
        for char_data in data.values():
            if isinstance(char_data, dict):
                all_keys.update(char_data.keys())
        # 組み込み5種を先頭に、残りをアルファベット順で追加
        result = [k for k in BUILTIN_EXPRESSIONS if k in all_keys]
        extras = sorted(k for k in all_keys if k not in BUILTIN_EXPRESSIONS)
        result.extend(extras)
        return result if result else list(EXPRESSIONS)
    except Exception:
        return list(EXPRESSIONS)


def _list_audio_assets():
    """public/bgm・public/se の音源ファイル一覧を返す（プレフィックス付き相対パス）。"""
    def listdir(d, prefix):
        out = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(_AUDIO_EXTS):
                    out.append(prefix + "/" + fn)
        return out
    return {"bgm": listdir(BGM_DIR, "bgm"), "se": listdir(SE_DIR, "se")}


def _list_overlay_assets():
    """public/overlays 配下の画像一覧を返す（overlays/ 付き相対パス）。"""
    out = []
    if not os.path.isdir(OVERLAYS_DIR):
        return out
    for root, _dirs, files in os.walk(OVERLAYS_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith(_IMAGE_EXTS):
                continue
            abs_path = os.path.join(root, fn)
            rel = os.path.relpath(abs_path, VIDEO_PUBLIC_DIR).replace("\\", "/")
            out.append(rel)
    return sorted(out)


def _list_background_assets():
    """public/background 配下の画像一覧を返す（background/ 付き相対パス）。"""
    out = []
    if not os.path.isdir(BACKGROUND_DIR):
        return out
    for root, _dirs, files in os.walk(BACKGROUND_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith(_IMAGE_EXTS):
                continue
            abs_path = os.path.join(root, fn)
            rel = os.path.relpath(abs_path, VIDEO_PUBLIC_DIR).replace("\\", "/")
            out.append(rel)
    return sorted(out)


def _load_se_map():
    """se-map.json を返す。無ければ空dict。"""
    if not os.path.exists(SE_MAP_JSON):
        return {}
    try:
        with open(SE_MAP_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_se_map(data):
    if not isinstance(data, dict):
        raise ValueError("se-map は object である必要があります")
    with open(SE_MAP_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_readings():
    """config/readings.json を返す（_comment 等の先頭アンダースコアキーは除く）。"""
    if not os.path.exists(READINGS_JSON):
        return {}
    try:
        with open(READINGS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and not k.startswith("_")}


def _save_readings(entries):
    if not isinstance(entries, dict):
        raise ValueError("entries は object である必要があります")
    cleaned = {}
    for k, v in entries.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        cleaned[k.strip().lower()] = v.strip()
    out = {"_comment": READINGS_COMMENT}
    out.update(dict(sorted(cleaned.items())))
    os.makedirs(os.path.dirname(READINGS_JSON), exist_ok=True)
    with open(READINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _load_kanji_readings():
    """config/kanji_readings.json を返す（_comment 等の先頭アンダースコアキーは除く）。"""
    if not os.path.exists(KANJI_READINGS_JSON):
        return {}
    try:
        with open(KANJI_READINGS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and not k.startswith("_")}


def _save_kanji_readings(entries):
    if not isinstance(entries, dict):
        raise ValueError("entries は object である必要があります")
    cleaned = {}
    for k, v in entries.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        cleaned[k.strip()] = v.strip()
    out = {"_comment": KANJI_READINGS_COMMENT}
    out.update(dict(sorted(cleaned.items())))
    os.makedirs(os.path.dirname(KANJI_READINGS_JSON), exist_ok=True)
    with open(KANJI_READINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _load_scenes_detail():
    """story-scenes.json からプロンプト用のシーン情報を返す。"""
    if not os.path.exists(SCENES_JSON):
        return []
    try:
        with open(SCENES_JSON, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    out = []
    for k, v in d.get("scenes", {}).items():
        if not isinstance(v, dict):
            continue
        anchors = v.get("anchors") if isinstance(v.get("anchors"), dict) else {}
        cast = v.get("cast") if isinstance(v.get("cast"), dict) else {}
        out.append({
            "key": k,
            "label": v.get("label") or k,
            "figure": v.get("figure", "bust"),
            "shot": v.get("shot", "duo"),
            "anchors": list(anchors.keys()),
            "cast": cast,
            "soloZoom": bool(v.get("soloZoom", True)),
            "camera": v.get("camera", "static"),
        })
    return out


def _load_story_world():
    """docs/story-world.md（世界観・キャラ設定）の本文を返す。無ければ空文字。

    プロンプト用にコメント行（※/先頭の見出し説明）は軽く除き、本文をそのまま使う。
    世界観を変えたいときはこのファイルを編集すればプロンプトに反映される。
    """
    path = os.path.join(ROOT_DIR, "docs", "story-world.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""
    out = []
    for ln in lines:
        s = ln.rstrip("\n")
        if s.strip().startswith("※"):  # ファイル運用上の注記は除く
            continue
        out.append(s)
    return "\n".join(out).strip()


def _build_script_prompt(theme, length, notes, mode="safe",
                         custom_world="", custom_example="", extra_rules=""):
    """AI(ChatGPT/Claude)に投げる台本生成プロンプトを組み立てて返す。

    現在ツールが対応しているシーン/キャラ/表情/演出/インサートと、
    読み込み可能なJSONスキーマ＋例を埋め込む。ローカル生成のみ（外部送信なし）。
    mode="experimental" のときは、新しい演出/新シーンの考案を促す版を返す。
    """
    experimental = (mode == "experimental")
    world = (custom_world or "").strip() or _load_story_world()
    theme = (theme or "").strip() or "（ここに物語の題材・あらすじを入れてください）"
    length = (length or "").strip() or "5〜10分・全体で30〜60ターンほど（起承転結のある一話）"
    notes = (notes or "").strip() or "特になし"
    extra_rules = (extra_rules or "").strip()

    scenes = _load_scenes_detail()
    if scenes:
        scene_lines = "\n".join(
            "- %s … %s（%s / %s / %s / anchors: %s%s）" % (
                s["key"],
                s["label"],
                "全身" if s["figure"] == "full" else "バスト",
                "1人向け" if s["shot"] == "solo" else "2人向け",
                "ゆっくりズーム" if s.get("camera") == "slow-zoom" else "固定カメラ",
                ", ".join(s["anchors"]) if s["anchors"] else "なし",
                (
                    " / 既定配置: " +
                    ", ".join("%s=%s" % (name, anchor) for name, anchor in s["cast"].items())
                ) if s["cast"] else ""
            ) for s in scenes
        )
        first_scene = scenes[0]["key"]
    else:
        scene_lines = "-（シーン未登録。先にシーンエディタで作成してください）"
        first_scene = "office"

    expr_list = ", ".join(_load_expression_keys())

    mob_list = "、".join(_load_mobs().keys()) or "（未登録）"

    example = (custom_example or "").strip() or (
        '{\n'
        '  "title": "【スカッと】開発ベンダーのモンスター社員にブチギレた結果【アニメ】",\n'
        '  "script": [\n'
        '    { "speaker": "zundamon", "text": "はぁ……新しい基幹システム開発、\\nベンダーとの定例会議の時間なのだ……。",'
        ' "scene": "FIRST", "expression": "trouble", "pose": "droop" },\n'
        '    { "speaker": "zundamon", "text": "毎回進捗が怪しいから、今日はビシッと言うのだ！ オンライン会議室に入るのだ！",'
        ' "scene": "FIRST", "expression": "normal", "pose": "step_in" },\n'
        '    { "speaker": "zundamon", "text": "お疲れ様なのだ！ \\nさっそく今週の進捗状況を教えてほしいのだ！",'
        ' "scene": "meeting_room", "expression": "normal", "transition": "fade-black", "pose": "proud",'
        ' "insert": {"kind":"videocall","room":"開発定例会議","layout":"focus",'
        '"participants":[{"speaker":"zundamon","bgStyle":"office"},{"speaker":"営業","bgStyle":"office"},{"speaker":"AI","bgStyle":"ai","name":"ZunAI"}]} },\n'
        '    { "speaker": "営業", "text": "あ、ずんだもんさん。お疲れ様です。えーっと、今週の進捗ですが……『ゼロ』です！",'
        ' "scene": "meeting_room", "expression": "normal" },\n'
        '    { "speaker": "zundamon", "text": "えええーっ！？ ゼロって何なのだ！？ もうすぐテスト工程に入るスケジュールなのだ！",'
        ' "scene": "meeting_room", "expression": "surprise", "shake": true, "pose": "recoil" },\n'
        '    { "speaker": "metan", "text": "最近、我が社のシステム部に導入された最新鋭のAI……\\n『ZunAI』の存在を忘れたの？",'
        ' "scene": "rooftop", "expression": "happy", "emphasis": true, "pose": "proud", "transition": "wipe-left" }\n'
        '  ]\n'
        '}'
    ).replace("FIRST", first_scene)

    parts = [
        "あなたは「ずんだもん」と「四国めたん」が登場するストーリー（会話劇）動画の脚本家です。",
        "下記の【題材】をもとに、専用ツールでそのまま読み込めるJSON台本（物語）を作成してください。",
        "",
        "━━━ 入力 ━━━",
        "【題材・あらすじ】",
        theme,
        "",
        "【長さ・構成の目安】",
        length,
        "",
        "【トーン・補足】",
        notes,
        "",
        "━━━ 世界観・キャラクター・シリーズ設定（厳守）━━━",
        world if world else
        "- 株式会社ずんだシステムズ（IT何でも屋）。ずんだもん(新人)＝主人公、四国めたん(先輩)＝導く役。",
        "",
        "━━━ 書き方の方針 ━━━",
        "- これは解説動画ではなく『ストーリー（会話劇）』。上の世界観・キャラ設定・お約束に沿って一話完結の物語を書く。",
        "- 上記キャラの口調・性格を厳守（ずんだもん=「〜のだ」善意の暴走、めたん=冷静・答えは教えずヒント）。",
        "- 場面転換(scene)・回想(flashback)・登場/退場(enter/exit)・インサート・表情で物語を演出する。",
        "- 1ターンは1〜2文。地の文は使わず、すべてセリフ（text）で進める。教訓は短く・最後は軽いオチ。",
        "",
        "━━━ speaker に使う値 ━━━",
        "- zundamon … ずんだもん（主人公）",
        "- metan … 四国めたん（先輩）",
        "- " + mob_list + " … モブキャラ（1枚絵・口パク簡易）。videocall/teamchat等の脇役として登場できる。",
        "- AI … 社内AI「ZunAI」。セリフのspeakerは必ず\"AI\"。",
        "- troublemaker_male_normal / troublemaker_male_creepy / troublemaker_female_normal / troublemaker_female_creepy … 画面に姿を出さない声のみのキャラ（電話越し・チャットのみ等）。",
        "",
        ("━━━ シーン（scene の値・既存。新規も可）━━━" if experimental
         else "━━━ 使えるシーン（scene に使う値。リスト以外は使用不可）━━━"),
        scene_lines,
        ("※ 上は既存シーン。話に必要なら新しい scene 名を作って使ってもよい"
         "（新シーンは後で手作業で用意される。下記 _proposals に列挙すること）。"
         if experimental
         else "※ scene は各ターンの背景。リストのキーのみ使う。anchors は speakerAnchor で使える立ち位置名。場面転換は話の区切りで行う。"),
        "",
        "━━━ 表情（expression に使う値・各ターンに付ける）━━━",
        expr_list,
        "※ normal=通常 / happy=笑顔 / surprise=驚き / trouble=困り / panic=焦り。流れに合うものを選ぶ。",
        "",
        # 【重要】新しい演出を実装したら、この節に1行追記すること。
        #   併せて _KNOWN_TURN_FIELDS / _KNOWN_INSERT_KINDS も更新。
        #   詳細手順: docs/new-effect-checklist.md
        "━━━ 使える演出（任意。付けると良くなる）━━━",
        '- "transition": "cut" / "fade-black" / "fade-white" / "wipe-left" / "wipe-right" / "slide-left" / "slide-right" … シーンが切り替わる最初の行だけに付ける場面転換',
        '- "pose": "idle" / "cheer" / "recoil" / "lean" / "droop" / "flustered" / "proud" / "step_in" / "step_back" / "listening" / "sneak" / "wobble" … その行の話者ポーズ',
        '- "emphasis": true … 話者にズームイン（強調したい一言で）',
        '- "shake": true … 画面を揺らす（衝撃・驚き）',
        '- "cameraEffect": "pull-out" / "pan-left" / "pan-right" / "tilt-left" / "tilt-right" … その行だけカメラに追加の動きを付ける',
        '- "flashback": true … 回想（彩度が落ちる。"telop" と併用推奨）',
        '- "telop": "― 前日 ―" … 画面隅に短時間出る字幕（時代・場面ラベル）',
        '- "telopX": 0.05, "telopY": 0.06, "telopSize": 1.0 … テロップの位置と大きさを微調整',
        '- "pause": 0.5 … その台詞の後に入れる無音秒（間）',
        '- "narrationVoice": "棒読み男" / "棒読み女" … この行だけナレーション化。speaker は元の登場人物のままでよい',
        '- "voice": {"speed":0.88,"pitch":0.0,"intonation":0.0} … この行だけ声色を上書き（例: 棒読み演出）',
        '- "noLipSync": true … この行だけ口パクしない（心の声・モノローグ向け）',
        '- "continueBubble": true … 直前の同話者セリフを上段に残して2段吹き出しにする',
        '- "disableAutoBubbleSplit": true … 句点や ! ? があってもこの行だけ吹き出し自動分割を止める',
        '- "speakerAnchor": "left" … その行の話者を指定アンカー位置へ立たせる。以後そのシーン中はその位置を使う',
        '- "enter": ["metan"] … そのターンでキャラを登場させる',
        '- "enter": ["metan"], "enterMode": "instant" … スライドなしで即時登場させる（回想の切り替え向け）',
        '- "exit": ["metan"], "exitDir": "right" … キャラを右へスライド退場させる',
        '- "exit": ["metan"], "exitDir": "instant" … シーン境界まで立たせたまま即時退場させる',
        '- "face": {"zundamon":"left"} … 向きの明示（通常は不要）',
        '- "se": [{"file":"se/alarm.mp3","at":0.0,"volume":0.9}] … この行だけ鳴らす手動SE',
        '- "impactText": "ドン！" … 漫画風の大きな一撃テキストをバーンと出す（ここぞという一言に）',
        '- "zoomPunch": true … 話し始めに一瞬強く寄って戻る（縁が光る）強調。emphasisより短く鋭い',
        '- "quoteFreeze": true … 画面を暗くしてそのターンのtextを大きな引用カードで見せる（名言・宣言の一文向け）',
        '- "stampRain": "完了！" … 指定文字の判子（スタンプ）が降ってくる（達成・完了の演出）',
        '- "typingFlood": true … チャット通知風のカードが次々流れる（通知殺到・炎上感）',
        '- "sparkleBurst": true … キラッと光の粒が弾ける（閃き・良い知らせ）',
        '- "irisOut": true … 円が閉じて（または開いて）暗転する古典的な締め演出。話の最後のターンでのみ使う。'
        ' 位置・速さ等は effectSettings.irisOut で調整可能だが、基本は true だけで良い',
        '- "effectSettings": {"irisOut":{"closeStart":1.0,"closeEnd":2.5}} … 上記演出の細かい調整（このターンだけ上書き）。'
        ' 通常は不要（既定値で自然に動く）',
        "",
        "━━━ インサート演出（\"insert\"。全画面にPC画面/チャット等を重ねる・任意）━━━",
        '- {"kind":"warning","title":"...","text":"..."} … ZunMonitor 警告画面(監視アラート)',
        '- {"kind":"ok","text":"..."} … ZunMonitor 正常/OK画面',
        '- {"kind":"chat","user":"質問文","ai":["返答1","返答2"]} … ZunAI（社内AIチャット）',
        '- {"kind":"teamchat","channel":"#障害対応","messages":[{"from":"営業","text":"..."}]} … ZunChat（社内チャット・複数人）',
        '- {"kind":"mailer","from":"差出人","subject":"件名","body":"本文","time":"10:00"} … ZunMail（メール）',
        '- {"kind":"videocall","room":"定例会議","layout":"focus","participants":[{"speaker":"zundamon","bgStyle":"office"},{"speaker":"metan","bgStyle":"home"}]}'
        ' … ZunMeet（ビデオ会議画面）。一度出すと同シーン内の後続ターンへ自動継続するので、後続ターンには insert を書かない。'
        ' bgStyle は office / meeting_room / home / ai / green。話者のタイルが自動で大きく表示される。'
        ' 参加者ごとに bgImage で用意済みの背景画像(例: "background/ベンダー会議室.png")を bgStyle の代わりに指定してもよい',
        '- {"kind":"videocall","end":true} … 進行中のZunMeet通話をこのターンで終了して通常画面に戻す',
        "※ チャット系インサート中は、そのターンの内容をチャット内に書く。",
        "※ 社内システム(Zun○○)とインサートの対応: ZunMail=mailer / ZunChat=teamchat / ZunMonitor=warning / ZunAI=chat / ZunMeet=videocall。",
        '※ どのkindも共通で "width"(パネル幅倍率) / "bg"(パネル自体の背景色) / "backdropBg"(画面全体の背景色)'
        ' / "backdropImage"(画面全体の背景画像) を上書きできるが、通常は既定のままでよい。',
    ]

    if extra_rules:
        parts += [
            "",
            "━━━ 追加ルール（ユーザー指定・優先）━━━",
            extra_rules,
        ]

    if experimental:
        parts += [
            "",
            "━━━ 新しい演出の提案（このモードの主目的）━━━",
            "- 既存の演出/インサートだけでなく、この動画に効果的だと思う『今は無い新しい演出』を自由に考案してよい。",
            '- 新演出はターンに任意のキー名で付ける（例: "zoomPunch": true / "colorFlash": "red" / "splitScreen": {...} 等）。',
            "- 新しい場面が必要なら、リストに無い新しい scene 名を使ってよい。",
            "- ただし既存で表現できるものは無理に新演出にしない。新演出は『あると良いが今は無い』ものに限る。",
            '- 使った新演出・新シーンは、トップレベルの "_proposals" 配列に必ずまとめること:',
            '    "_proposals": [',
            '      { "type": "effect", "name": "zoomPunch", "desc": "一瞬強く寄って戻る強調", "example": {"zoomPunch": true} },',
            '      { "type": "scene",  "name": "night_street", "desc": "夜の街並み（全身）" }',
            '    ]',
            "- ツールは未対応の新演出を無視して描画するが、読込時に一覧表示されるので、開発の足がかりになる。",
        ]

    parts += [
        "",
        "━━━ 出力フォーマット（厳守）━━━",
        "- 出力は JSON のみ（```json ... ``` で囲ってよい）。JSON 以外の説明文は書かない。",
        '- トップレベル: { "title": "動画タイトル", "script": [ ターン, ... ]'
        + (', "_proposals": [ ... ] }' if experimental else " }"),
        "- 各ターンの必須キー: speaker, text, scene。expression は推奨。その他の演出キーは任意。",
        "- start / end / sentences / audio / id は書かない（ツールが自動生成する）。",
        '- narrationVoice / voice / noLipSync / continueBubble / disableAutoBubbleSplit / speakerAnchor / telopX / telopY / telopSize / se / pose / transition'
        ' / impactText / zoomPunch / quoteFreeze / stampRain / typingFlood / sparkleBurst / irisOut / effectSettings も使ってよい。',
        '- transition は「scene が切り替わる先頭行」にだけ付ける。連続する同sceneの後続行には付けない。',
        ("- 新演出は任意キーで自由に。新シーン名も可。新規分は必ず _proposals に列挙する。"
         if experimental
         else "- scene は必ず上記リストのキーから選ぶ。speaker も上記の値のみ。"),
        "",
        "━━━ 出力例（最小）━━━",
        example,
        "",
        "では、上記の【題材】をもとに物語の台本JSONを作成してください。まずタイトルを決め、導入から山場・結末まで一本の物語として構成すること。",
    ]
    return "\n".join(parts)


# ツールが現在対応しているターンのキー（これ以外＝新演出として検出）
_KNOWN_TURN_FIELDS = {
    "id", "speaker", "text", "scene", "expression", "pose", "enter", "enterMode", "face",
    "emphasis", "shake", "cameraEffect", "flashback", "telop", "pause", "transition", "insert",
    "exit", "exitDir", "se", "voice", "narrationVoice", "noLipSync", "continueBubble", "speakerAnchor",
    "disableAutoBubbleSplit", "telopSize", "telopX", "telopY", "start", "end", "sentences",
    "impactText", "zoomPunch", "quoteFreeze", "stampRain", "typingFlood", "sparkleBurst",
    "irisOut", "effectSettings", "audioFx", "chorus", "closing",
}
_KNOWN_INSERT_KINDS = {"warning", "ok", "chat", "teamchat", "mailer", "videocall"}


def _import_script_text(raw):
    """AIが出力したテキスト（```json フェンスや前後の文を含みうる）から台本を取り出し保存する。

    未対応の演出/シーン/表情/インサートを検出し report にまとめて返す。
    戻り値: (ok: bool, message: str, info: dict)
      info = {turns, report:{newFields, newScenes, newExpr, newInserts, proposals}}
    """
    if not raw or not raw.strip():
        return False, "貼り付けが空です", {}
    text = raw.strip()
    # ```json ... ``` フェンスを除去
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        if m:
            text = m.group(1).strip()
    # 最初の { から最後の } までを抽出（前後の説明文を許容）
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        text = text[s:e + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        return False, "JSONとして解釈できません: %s" % ex, {}
    if not isinstance(data, dict) or not isinstance(data.get("script"), list):
        return False, "script 配列が見つかりません", {}

    existing_scenes = set(_load_scenes_keys())
    existing_expr = set(_load_expression_keys())
    new_fields = {}    # フィールド名 -> [turn番号...]
    new_scenes = {}    # scene名 -> [turn番号...]
    new_expr = {}      # 表情名 -> [turn番号...]
    new_inserts = {}   # kind -> [turn番号...]

    for i, turn in enumerate(data["script"]):
        if not isinstance(turn, dict):
            return False, "turn[%d] が不正です" % i, {}
        # 自動生成フィールドを除去し id を振り直す（未対応の新演出キーは保持する）
        for k in ("start", "end", "sentences"):
            turn.pop(k, None)
        turn["id"] = "turn-%04d" % (i + 1)
        n = i + 1
        for k in turn:
            if k not in _KNOWN_TURN_FIELDS:
                new_fields.setdefault(k, []).append(n)
        sc = turn.get("scene")
        if isinstance(sc, str) and existing_scenes and sc not in existing_scenes:
            new_scenes.setdefault(sc, []).append(n)
        ex_ = turn.get("expression")
        if isinstance(ex_, str) and existing_expr and ex_ not in existing_expr:
            new_expr.setdefault(ex_, []).append(n)
        ins = turn.get("insert")
        if isinstance(ins, dict):
            kind = ins.get("kind")
            if kind and kind not in _KNOWN_INSERT_KINDS:
                new_inserts.setdefault(kind, []).append(n)

    proposals = data.get("_proposals") if isinstance(data.get("_proposals"), list) else []
    data.pop("audio", None)
    try:
        _save_story(data)  # ここで speaker/text/scene の検証も走る
    except ValueError as ex:
        return False, str(ex), {}

    report = {
        "newFields": new_fields,
        "newScenes": new_scenes,
        "newExpr": new_expr,
        "newInserts": new_inserts,
        "proposals": proposals,
    }
    return True, "ok", {"turns": len(data["script"]), "report": report}


def _safe_path(base_dir, relative):
    """パストラバーサルを防いで安全な絶対パスを返す。不正なら None。"""
    rel = unquote(relative).replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return None
    path = os.path.abspath(os.path.join(base_dir, rel))
    try:
        if os.path.commonpath([path, os.path.abspath(base_dir)]) != os.path.abspath(base_dir):
            return None
    except ValueError:
        return None
    return path


def _story_preview_asset_path(relative):
    """Remotion Player 用アセットを video/public/ 内の許可パスから解決する。

    StoryVideo.tsx の staticFile() が参照するファイルをすべてカバーする:
      background/*.png  avatars/**  mobs/*.png  noise.png
      story-scenes.json  story-01.wav/.mp3  bgm/*  se/*  fonts/*

    パストラバーサル防止: video/public/ 外を指せない。
    戻り値: 実ファイルパス or None（不正 / 存在しない場合）。
    """
    if not isinstance(relative, str):
        return None
    rel = unquote(relative).replace("\\", "/").strip("/")
    if not rel or rel.startswith(".") or "/../" in ("/" + rel + "/"):
        return None
    parts = rel.split("/")
    # 単ファイル（トップレベル）
    if len(parts) == 1 and rel in _PREVIEW_ASSET_FILES:
        pass  # 許可
    # サブディレクトリ
    elif parts[0] in _PREVIEW_ASSET_DIRS:
        pass  # 許可
    # avatars/manifest.json はトップレベルではないが許可
    elif rel == "avatars/manifest.json":
        pass
    else:
        return None
    path = os.path.abspath(os.path.join(VIDEO_PUBLIC_DIR, rel))
    try:
        if os.path.commonpath([path, os.path.abspath(VIDEO_PUBLIC_DIR)]) != os.path.abspath(VIDEO_PUBLIC_DIR):
            return None
    except ValueError:
        return None
    return path if os.path.isfile(path) else None


class StoryEditorHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, msg):
        self._send_json({"error": msg}, status)

    def _send_image(self, path):
        if not os.path.isfile(path):
            self._send_error_json(404, "画像が見つかりません")
            return
        ext = os.path.splitext(path)[1].lower()
        ct = _CT.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _MIME = {
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
    }

    def _send_file(self, path, content_type=None, no_cache=False):
        """任意のファイルを返す。path が None または存在しない場合は 404。

        音声/動画のシーク再生にはブラウザが HTTP Range リクエストを使うため、
        Range ヘッダ(206 Partial Content)に対応する。未対応だと <audio>/<Audio>
        が数百ms再生して破綻する（プレビュー音声が止まる原因）。
        """
        if path is None or not os.path.isfile(path):
            self._send_error_json(404, "Not Found")
            return
        if content_type is None:
            ext = os.path.splitext(path)[1].lower()
            content_type = self._MIME.get(ext, "application/octet-stream")

        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")
        start, end = 0, file_size - 1
        is_partial = False

        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes="):].split(",")[0].strip()
            try:
                if spec.startswith("-"):
                    # bytes=-N → 末尾 N バイト
                    suffix = int(spec[1:])
                    if suffix > 0:
                        start = max(0, file_size - suffix)
                        end = file_size - 1
                        is_partial = True
                else:
                    s, _, e = spec.partition("-")
                    start = int(s)
                    end = int(e) if e else file_size - 1
                    end = min(end, file_size - 1)
                    if start <= end and start < file_size:
                        is_partial = True
            except ValueError:
                is_partial = False

        if is_partial and start > end:
            # 範囲不正
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % file_size)
            self.end_headers()
            return

        length = (end - start + 1) if is_partial else file_size
        with open(path, "rb") as f:
            if is_partial:
                f.seek(start)
            body = f.read(length)

        self.send_response(206 if is_partial else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if no_cache:
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        if is_partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, file_size))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # メディアのシークでブラウザが接続を切るのは正常。ログを汚さない。
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            try:
                body = _load_page().encode("utf-8")
            except FileNotFoundError:
                self._send_error_json(500, "story_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/pose-editor":
            try:
                body = _load_pose_page().encode("utf-8")
            except FileNotFoundError:
                self._send_error_json(500, "pose_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/expression-editor":
            try:
                body = _load_expression_page().encode("utf-8")
            except FileNotFoundError:
                self._send_error_json(500, "expression_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/scene-editor":
            try:
                body = _load_scene_page().encode("utf-8")
            except FileNotFoundError:
                self._send_error_json(500, "scene_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/story-player.js":
            self._send_file(
                os.path.join(VIDEO_PUBLIC_DIR, "story-player.js"),
                "text/javascript; charset=utf-8",
                no_cache=True,
            )

        elif path.startswith("/preview-assets/"):
            rel = path[len("/preview-assets/"):]
            self._send_file(_story_preview_asset_path(rel))

        elif path == "/api/story":
            try:
                self._send_json(_load_story())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/meta":
            try:
                speakers, speaker_icons = _current_speakers_and_icons()
                meta = {
                    "speakers": speakers,
                    "narrationSpeakers": NARRATION_SPEAKERS,
                    "speakerIcons": speaker_icons,
                    "scenes": _load_scenes_keys(),
                    # expressions.json のキーから動的生成。読み込み失敗時は定数にフォールバック。
                    "expressions": _load_expression_keys(),
                    "insertKinds": INSERT_KINDS,
                }
                self._send_json(meta)
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/catalog":
            try:
                self._send_json(pose_editor_module._build_catalog())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/poses":
            try:
                self._send_json(pose_editor_module._load_poses())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/expression/catalog":
            try:
                self._send_json(expression_editor_module._build_catalog())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/expression/expressions":
            try:
                self._send_json(expression_editor_module._load_expressions())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/scene/scenes":
            try:
                self._send_json(scene_editor_module._load_scenes())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/scene/list-assets":
            try:
                self._send_json(scene_editor_module._list_assets())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/expressions":
            try:
                if os.path.exists(EXPRESSIONS_JSON):
                    with open(EXPRESSIONS_JSON, encoding="utf-8") as f:
                        self._send_json(json.load(f))
                else:
                    self._send_json({})
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/audio-assets":
            try:
                self._send_json(_list_audio_assets())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/overlay-assets":
            try:
                self._send_json({"images": _list_overlay_assets()})
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/background-assets":
            try:
                self._send_json({"images": _list_background_assets()})
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/se-map":
            try:
                self._send_json(_load_se_map())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/readings":
            try:
                self._send_json(_load_readings())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/kanji-readings":
            try:
                self._send_json(_load_kanji_readings())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/mobs":
            try:
                self._send_json(_load_mobs())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path.startswith("/img/"):
            rel = path[len("/img/"):]
            safe = _safe_path(VIDEO_PUBLIC_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_image(safe)

        else:
            self._send_error_json(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/story":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_story(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/se-map":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_se_map(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/readings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_readings(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/kanji-readings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_kanji_readings(data)
                try:
                    synced = sync_kanji_readings_to_voicevox(path=KANJI_READINGS_JSON)
                    self._send_json({"ok": True, "synced": synced})
                except Exception as e:
                    # VOICEVOX未起動等でも保存自体は成功しているので警告として返す。
                    self._send_json({"ok": True, "synced": 0, "syncWarning": str(e)})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/mobs":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_mobs(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/mobs/upload-image":
            # 画像アップロード（multipartは標準ライブラリのみ方針だと煩雑なため、
            # base64 dataURLをJSONで受け取る簡易方式にする）。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                filename = _safe_image_filename(data.get("filename"), "mob_image")
                mobs_dir = os.path.join(VIDEO_PUBLIC_DIR, "mobs")
                _save_base64_image(mobs_dir, filename, data.get("dataUrl", ""))
                self._send_json({"ok": True, "path": f"mobs/{filename}"})
            except (json.JSONDecodeError, ValueError, binascii.Error) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/overlay-assets/upload":
            # インサート周りの背景画像等、汎用の画像アップロード先(public/overlays/)。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                filename = _safe_image_filename(data.get("filename"), "overlay_image")
                _save_base64_image(OVERLAYS_DIR, filename, data.get("dataUrl", ""))
                self._send_json({"ok": True, "path": f"overlays/{filename}"})
            except (json.JSONDecodeError, ValueError, binascii.Error) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/background-assets/upload":
            # ZunMeet等の参加者背景に使う画像アップロード先(public/background/)。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                filename = _safe_image_filename(data.get("filename"), "background_image")
                _save_base64_image(BACKGROUND_DIR, filename, data.get("dataUrl", ""))
                self._send_json({"ok": True, "path": f"background/{filename}"})
            except (json.JSONDecodeError, ValueError, binascii.Error) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/script-prompt":
            # 入力(主題等)から、AIに投げる台本生成プロンプトを組み立てて返す（ローカル生成）。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                params = json.loads(body.decode("utf-8")) if body else {}
                prompt = _build_script_prompt(
                    params.get("theme"), params.get("length"), params.get("notes"),
                    params.get("mode", "safe"),
                    params.get("customWorld", ""),
                    params.get("customExample", ""),
                    params.get("extraRules", ""),
                )
                self._send_json({"prompt": prompt})
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/import-script":
            # AI出力テキスト(raw)を取り込み、story-01.json として保存する。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                params = json.loads(body.decode("utf-8")) if body else {}
                ok, msg, info = _import_script_text(params.get("raw", ""))
                if ok:
                    self._send_json({
                        "ok": True,
                        "turns": info.get("turns", 0),
                        "report": info.get("report", {}),
                    })
                else:
                    self._send_error_json(400, msg)
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/audio":
            # VOICEVOX で音声生成（make_story_audio.py）。進捗をストリーミングで逐次返す。
            # make_story_audio は [N/total] 話者: ... をターン毎に出力する。-u で即時flush。
            # 末尾に "__DONE__ ok" / "__DONE__ err..." のセンチネル行を送って結果を伝える。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            force_rebuild = False
            if body:
                try:
                    force_rebuild = bool(json.loads(body.decode("utf-8")).get("forceRebuild"))
                except (json.JSONDecodeError, ValueError):
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(text):
                try:
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            try:
                env = os.environ.copy()
                if force_rebuild:
                    # 辞書(読み替え/漢字読み)更新がキャッシュに反映されないケースの回避策。
                    # 通常は不要（生成が遅くなる）。make_story_audio.py側でキャッシュ全削除する。
                    env["STORY_AUDIO_FORCE_REBUILD"] = "1"
                proc = subprocess.Popen(
                    [sys.executable, "-u", "make_story_audio.py", "story-01"],
                    cwd=ROOT_DIR, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
                )
                for line in proc.stdout:
                    if not emit(line):
                        proc.kill()
                        break
                proc.wait()
                emit("__DONE__ ok\n" if proc.returncode == 0
                     else "__DONE__ err 音声生成に失敗（VOICEVOX起動を確認）\n")
            except Exception as e:
                emit("__DONE__ err " + str(e) + "\n")
        elif path == "/api/export":
            # 動画書き出し（prep-story → remotion render）。進捗をストリーミングで逐次返す。
            # remotion render はプログレスバーを \r で更新するため、\n だけでなく \r も
            # 行区切りとして扱い、進捗を都度クライアントへ流す。
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit2(text):
                try:
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            try:
                title = _load_story().get("title", "")
                filename = _safe_export_filename(title)
                video_dir = os.path.join(ROOT_DIR, "video")
                out_dir = os.path.join(video_dir, "out")
                os.makedirs(out_dir, exist_ok=True)
                out_rel = os.path.join("out", filename)

                emit2("[prep] アセット準備中...\n")
                prep = subprocess.run(
                    ["node", "scripts/prep-story.mjs"],
                    cwd=video_dir, capture_output=True, text=True,
                )
                if prep.returncode != 0:
                    emit2("__DONE__ err prep-story 失敗: " + (prep.stderr or "").strip()[:300] + "\n")
                else:
                    remotion_bin = os.path.join(video_dir, "node_modules", ".bin", "remotion")
                    proc = subprocess.Popen(
                        [remotion_bin, "render", "StoryVideo", out_rel],
                        cwd=video_dir, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                        start_new_session=True,  # 独立プロセスグループ化（下記killpgに必要）
                    )
                    buf = ""
                    killed = False
                    while True:
                        chunk = proc.stdout.read(256)
                        if not chunk:
                            break
                        buf += chunk
                        while True:
                            idx_n = buf.find("\n")
                            idx_r = buf.find("\r")
                            candidates = [i for i in (idx_n, idx_r) if i != -1]
                            if not candidates:
                                break
                            idx = min(candidates)
                            line = buf[:idx].strip()
                            buf = buf[idx + 1:]
                            if line and not emit2(line + "\n"):
                                killed = True
                                break
                        if killed:
                            break
                    if killed:
                        # remotionはレンダリング用にchrome-headless-shellを複数子プロセスとして
                        # 起動する。proc.kill()は直接の子(remotion本体)しか殺せず、孫プロセスの
                        # chrome-headless-shellが残留してCPU/メモリを食い続けるため、
                        # プロセスグループごとkillする。
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    proc.wait()
                    if proc.returncode == 0:
                        emit2("__DONE__ ok " + out_rel + "\n")
                    else:
                        emit2("__DONE__ err 書き出しに失敗しました（詳細はターミナルのログを確認）\n")
            except Exception as e:
                emit2("__DONE__ err " + str(e) + "\n")
        elif path == "/api/poses":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                pose_editor_module._save_poses(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/scene/scenes":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                scene_editor_module._save_scenes(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/expression/expressions":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                expression_editor_module._save_expressions(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/pose-export":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(text):
                try:
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            cmds = [
                ["node", "scripts/psd-export.mjs", "candidates", "zundamon"],
                ["node", "scripts/psd-export.mjs", "candidates", "metan"],
                ["node", "scripts/psd-export.mjs", "build", "zundamon"],
                ["node", "scripts/psd-export.mjs", "build-full", "zundamon"],
                ["node", "scripts/psd-export.mjs", "build", "metan"],
                ["node", "scripts/psd-export.mjs", "build-full", "metan"],
                ["node", "scripts/prep-story.mjs"],
                ["node", "scripts/build-story-player.mjs"],
            ]
            try:
                video_dir = os.path.join(ROOT_DIR, "video")
                for cmd in cmds:
                    label = " ".join(cmd)
                    if not emit(f"=== {label} ===\n"):
                        break
                    proc = subprocess.Popen(
                        cmd,
                        cwd=video_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    for line in proc.stdout:
                        if not emit(line):
                            proc.kill()
                            break
                    proc.wait()
                    if proc.returncode != 0:
                        emit(f"__DONE__ err コマンド失敗: {label} (exit={proc.returncode})\n")
                        return
                emit("__DONE__ ok\n")
            except Exception as e:
                emit(f"__DONE__ err {e}\n")
        elif path == "/api/expression-export":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(text):
                try:
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            cmds = [
                ["node", "scripts/psd-export.mjs", "build", "zundamon"],
                ["node", "scripts/psd-export.mjs", "build-full", "zundamon"],
                ["node", "scripts/psd-export.mjs", "build", "metan"],
                ["node", "scripts/psd-export.mjs", "build-full", "metan"],
                ["node", "scripts/prep-story.mjs"],
                ["node", "scripts/build-story-player.mjs"],
            ]
            try:
                video_dir = os.path.join(ROOT_DIR, "video")
                for cmd in cmds:
                    label = " ".join(cmd)
                    if not emit(f"=== {label} ===\n"):
                        break
                    proc = subprocess.Popen(
                        cmd,
                        cwd=video_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    for line in proc.stdout:
                        if not emit(line):
                            proc.kill()
                            break
                    proc.wait()
                    if proc.returncode != 0:
                        emit(f"__DONE__ err コマンド失敗: {label} (exit={proc.returncode})\n")
                        return
                emit("__DONE__ ok\n")
            except Exception as e:
                emit(f"__DONE__ err {e}\n")
        else:
            self._send_error_json(404, "Not Found")


def _port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _run_prep_story():
    """起動時に assets/ → public/ を同期(prep-story)。BGM/SE/背景/立ち絵/manifest を最新化。
    これで「assets に素材を置く → 手動で prep-story」を不要にする。
    node が無い等で失敗しても起動は続行する。"""
    video_dir = os.path.join(ROOT_DIR, "video")
    script = os.path.join(video_dir, "scripts", "prep-story.mjs")
    if not os.path.isfile(script):
        return
    try:
        r = subprocess.run(
            ["node", "scripts/prep-story.mjs"],
            cwd=video_dir, capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("[story] prep-story 完了（assets→public 同期: 背景/BGM/SE/立ち絵）")
        else:
            print("[story] prep-story 失敗（続行）:\n" + (r.stderr or "").strip()[:300])
    except FileNotFoundError:
        print("[story] node が無いため prep-story をスキップ（手動: cd video && node scripts/prep-story.mjs）")


def _run_build_story_player():
    """起動時に story-player.js を再ビルドして、StoryVideo 側の変更をプレビューへ反映する。"""
    video_dir = os.path.join(ROOT_DIR, "video")
    script = os.path.join(video_dir, "scripts", "build-story-player.mjs")
    if not os.path.isfile(script):
        return
    try:
        r = subprocess.run(
            ["node", "scripts/build-story-player.mjs"],
            cwd=video_dir, capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("[story] story-player ビルド完了（StoryVideoの変更をプレビューへ反映）")
        else:
            print("[story] story-player ビルド失敗（続行）:\n" + (r.stderr or "").strip()[:300])
    except FileNotFoundError:
        print("[story] node が無いため story-player ビルドをスキップ（手動: cd video && node scripts/build-story-player.mjs）")


def main():
    parser = argparse.ArgumentParser(description="ストーリーエディタ")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--host", default="localhost",
                        help="待受ホスト。Tailscale等でスマホから開くには 0.0.0.0")
    args = parser.parse_args()

    _run_prep_story()  # 起動時に assets→public を同期(手動 prep-story 不要に)
    _run_build_story_player()  # 起動時に player bundle を最新化
    # シーン/表情/ポーズは story_editor.py に統合済み。

    server = ThreadingHTTPServer((args.host, args.port), StoryEditorHandler)
    print(f"ストーリーエディタ起動: http://{args.host}:{args.port}")
    if args.host == "0.0.0.0":
        print("[story] Tailscale経由でスマホから開く場合: "
              f"http://<このPCのTailscale名 or IP>:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
