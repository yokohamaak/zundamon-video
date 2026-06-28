"""
ストーリーエディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/story-01.json をブラウザで台本編集する。
turn の並び・話者・セリフ・場面・表情・演出・insertを編集できる。

使い方: python story_editor.py [--port 8771]
"""
import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
STORY_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-01.json")
SCENES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-scenes.json")
EXPRESSIONS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "expressions.json")

# StoryVideo.tsx の staticFile() が参照する video/public/ 配下のアセット。
# /preview-assets/<path> として配信する（パストラバーサル防止付き）。
# 許可するトップレベルディレクトリ名 or ファイル名の集合。
_PREVIEW_ASSET_DIRS = {"avatars", "background", "mobs", "bgm", "se", "fonts"}
_PREVIEW_ASSET_FILES = {"story-scenes.json", "noise.png", "story-01.wav", "story-01.mp3",
                        "story.wav", "story.mp3"}

# 話者一覧（StoryVideo.tsx の CHARACTERS / MOBS と二重管理。MVPのためハードコード）
SPEAKERS = ["zundamon", "metan", "営業", "部長", "AI"]

# 話者アイコン（/img/<path> でアクセスできる video/public 配下の相対パス）
SPEAKER_ICONS = {
    "zundamon": "avatars/zundamon/icon.png",
    "metan": "avatars/metan/icon.png",
    "営業": "mobs/mob_normal.png",
    "部長": "mobs/manager_normal.png",
    "AI": None,
}

# 組み込み5種（フォールバック用・expressions.json が読めない場合に使用）
EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]
# 組み込み5種（先頭順序固定用）
BUILTIN_EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]
INSERT_KINDS = ["warning", "ok", "chat", "teamchat", "mailer"]

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


def _load_scenes_detail():
    """story-scenes.json から [{key,label,figure}] を返す（プロンプト用）。"""
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
        out.append({
            "key": k,
            "label": v.get("label") or k,
            "figure": v.get("figure", "bust"),
        })
    return out


def _build_script_prompt(theme, length, notes):
    """AI(ChatGPT/Claude)に投げる台本生成プロンプトを組み立てて返す。

    現在ツールが対応しているシーン/キャラ/表情/演出/インサートと、
    読み込み可能なJSONスキーマ＋例を埋め込む。ローカル生成のみ（外部送信なし）。
    """
    theme = (theme or "").strip() or "（ここに主題を入れてください）"
    length = (length or "").strip() or "10分前後・全体で30〜60ターンほど（1主題を深掘り）"
    notes = (notes or "").strip() or "特になし"

    scenes = _load_scenes_detail()
    if scenes:
        scene_lines = "\n".join(
            "- %s … %s（%s）" % (
                s["key"], s["label"], "全身" if s["figure"] == "full" else "バスト"
            ) for s in scenes
        )
        first_scene = scenes[0]["key"]
    else:
        scene_lines = "-（シーン未登録。先にシーンエディタで作成してください）"
        first_scene = "office"

    expr_list = ", ".join(_load_expression_keys())

    example = (
        '{\n'
        '  "title": "なぜ〇〇は△△なのか",\n'
        '  "script": [\n'
        '    { "speaker": "zundamon", "text": "ねえめたん、〇〇ってなんで△△なのだ?",'
        ' "scene": "FIRST", "expression": "surprise" },\n'
        '    { "speaker": "metan", "text": "いい質問ね。結論から言うと、…だからなのよ。",'
        ' "scene": "FIRST", "expression": "normal", "emphasis": true },\n'
        '    { "speaker": "zundamon", "text": "へぇ〜、知らなかったのだ!",'
        ' "scene": "FIRST", "expression": "happy", "pause": 0.4 }\n'
        '  ]\n'
        '}'
    ).replace("FIRST", first_scene)

    parts = [
        "あなたは「ずんだもん」と「四国めたん」の掛け合い解説動画の台本作家です。",
        "下記の【主題】について、専用ツールでそのまま読み込めるJSON台本を作成してください。",
        "",
        "━━━ 入力 ━━━",
        "【主題】",
        theme,
        "",
        "【長さ・構成の目安】",
        length,
        "",
        "【トーン・補足】",
        notes,
        "",
        "━━━ 動画の方針 ━━━",
        "- ずんだもん（聞き役・素朴な疑問/驚き）と四国めたん（解説役・落ち着いた大人びた口調）の対話で、1つの主題を深掘りする。",
        "- 「なぜ〇〇なのか」を 導入(つかみ)→結論→理由→具体例→まとめ の流れで。雑学の羅列は避け、1本の筋を通す。",
        "- 1往復＝両者がそれぞれ1回ずつ話す程度。テンポよく、1ターンは1〜2文。",
        "- ずんだもん: 一人称「ボク」、語尾「〜のだ/〜なのだ」。四国めたん: 一人称「わたくし」寄りの丁寧語、解説役。",
        "",
        "━━━ 登場キャラ（speaker に使う値）━━━",
        "- zundamon … ずんだもん（聞き役）",
        "- metan … 四国めたん（解説役）",
        "- 営業 / 部長 / AI … 脇役。画面に立ち絵は出さず、チャットや声のみで登場（主にインサートと併用）。メインの掛け合いは zundamon と metan で進める。",
        "",
        "━━━ 使えるシーン（scene に使う値。リスト以外は使用不可）━━━",
        scene_lines,
        "※ scene は各ターンの背景。場面転換は話の区切りで行う。",
        "",
        "━━━ 表情（expression に使う値・各ターンに付ける）━━━",
        expr_list,
        "※ normal=通常 / happy=笑顔 / surprise=驚き / trouble=困り / panic=焦り。流れに合うものを選ぶ。",
        "",
        "━━━ 使える演出（任意。付けると良くなる）━━━",
        '- "emphasis": true … 話者にズームイン（強調したい一言で）',
        '- "shake": true … 画面を揺らす（衝撃・驚き）',
        '- "flashback": true … 回想（彩度が落ちる。"telop" と併用推奨）',
        '- "telop": "― 前日 ―" … 画面隅に短時間出る字幕（時代・場面ラベル）',
        '- "pause": 0.5 … その台詞の後に入れる無音秒（間）',
        '- "enter": ["metan"] … そのターンでキャラを登場させる',
        '- "exit": ["metan"], "exitDir": "right" … キャラを退場させる',
        '- "face": {"zundamon":"left"} … 向きの明示（通常は不要）',
        "",
        "━━━ インサート演出（\"insert\"。全画面にPC画面/チャット等を重ねる・任意）━━━",
        '- {"kind":"warning","title":"...","text":"..."} … 警告画面',
        '- {"kind":"ok","text":"..."} … OK/成功画面',
        '- {"kind":"chat","user":"質問文","ai":["返答1","返答2"]} … AIチャット風',
        '- {"kind":"teamchat","channel":"#障害対応","messages":[{"from":"営業","text":"..."}]} … Slack風チャット',
        '- {"kind":"mailer","from":"差出人","subject":"件名","body":"本文","time":"10:00"} … メール画面',
        "※ チャット系インサート中は、そのターンの内容をチャット内に書く。",
        "",
        "━━━ 出力フォーマット（厳守）━━━",
        "- 出力は JSON のみ（```json ... ``` で囲ってよい）。JSON 以外の説明文は書かない。",
        '- トップレベル: { "title": "動画タイトル", "script": [ ターン, ... ] }',
        "- 各ターンの必須キー: speaker, text, scene。expression は推奨。その他の演出キーは任意。",
        "- start / end / sentences / audio / id は書かない（ツールが自動生成する）。",
        "- scene は必ず上記リストのキーから選ぶ。speaker も上記の値のみ。",
        "",
        "━━━ 出力例（最小）━━━",
        example,
        "",
        "では、上記の【主題】に沿って台本JSONを作成してください。まずタイトルを決め、導入から結論・まとめまで一本の流れで構成すること。",
    ]
    return "\n".join(parts)


def _import_script_text(raw):
    """AIが出力したテキスト（```json フェンスや前後の文を含みうる）から台本を取り出し保存する。

    戻り値: (ok: bool, message: str, turns: int)
    """
    if not raw or not raw.strip():
        return False, "貼り付けが空です", 0
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
        return False, "JSONとして解釈できません: %s" % ex, 0
    if not isinstance(data, dict) or not isinstance(data.get("script"), list):
        return False, "script 配列が見つかりません", 0
    # 自動生成フィールドを除去し id を振り直す
    for i, turn in enumerate(data["script"]):
        if not isinstance(turn, dict):
            return False, "turn[%d] が不正です" % i, 0
        for k in ("start", "end", "sentences"):
            turn.pop(k, None)
        turn["id"] = "turn-%04d" % (i + 1)
    data.pop("audio", None)
    try:
        _save_story(data)  # ここで speaker/text/scene の検証も走る
    except ValueError as ex:
        return False, str(ex), 0
    return True, "ok", len(data["script"])


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

    def _send_file(self, path, content_type=None):
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
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/story-player.js":
            self._send_file(
                os.path.join(VIDEO_PUBLIC_DIR, "story-player.js"),
                "text/javascript; charset=utf-8",
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
                meta = {
                    # 話者一覧（StoryVideo.tsx の CHARACTERS/MOBS と二重管理）
                    "speakers": SPEAKERS,
                    "speakerIcons": SPEAKER_ICONS,
                    "scenes": _load_scenes_keys(),
                    # expressions.json のキーから動的生成。読み込み失敗時は定数にフォールバック。
                    "expressions": _load_expression_keys(),
                    "insertKinds": INSERT_KINDS,
                }
                self._send_json(meta)
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
        elif path == "/api/script-prompt":
            # 入力(主題等)から、AIに投げる台本生成プロンプトを組み立てて返す（ローカル生成）。
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                params = json.loads(body.decode("utf-8")) if body else {}
                prompt = _build_script_prompt(
                    params.get("theme"), params.get("length"), params.get("notes")
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
                ok, msg, turns = _import_script_text(params.get("raw", ""))
                if ok:
                    self._send_json({"ok": True, "turns": turns})
                else:
                    self._send_error_json(400, msg)
            except Exception as e:
                self._send_error_json(500, str(e))
        elif path == "/api/audio":
            # VOICEVOX で音声生成（make_story_audio.py）。進捗をストリーミングで逐次返す。
            # make_story_audio は [N/total] 話者: ... をターン毎に出力する。-u で即時flush。
            # 末尾に "__DONE__ ok" / "__DONE__ err..." のセンチネル行を送って結果を伝える。
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
                proc = subprocess.Popen(
                    [sys.executable, "-u", "make_story_audio.py", "story-01"],
                    cwd=ROOT_DIR, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
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
        else:
            self._send_error_json(404, "Not Found")


def _port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _maybe_start_scene_editor():
    # シーンタブの iframe 用に scene_editor(8770) を自動起動する（既に起動済みなら何もしない）。
    scene_py = os.path.join(ROOT_DIR, "scene_editor.py")
    if not os.path.isfile(scene_py):
        return
    if _port_in_use(8770):
        print("[story] scene_editor は既に起動済み (http://localhost:8770)")
        return
    try:
        proc = subprocess.Popen(
            [sys.executable, scene_py],
            cwd=ROOT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(lambda: proc.terminate())
        print("[story] scene_editor を自動起動しました (http://localhost:8770)")
    except Exception as e:
        print(f"[story] scene_editor の自動起動に失敗: {e}（シーンタブは手動起動が必要）")


def _maybe_start_expression_editor():
    # 表情タブの iframe 用に expression_editor(8772) を自動起動する（既に起動済みなら何もしない）。
    expr_py = os.path.join(ROOT_DIR, "expression_editor.py")
    if not os.path.isfile(expr_py):
        return
    if _port_in_use(8772):
        print("[story] expression_editor は既に起動済み (http://localhost:8772)")
        return
    try:
        proc = subprocess.Popen(
            [sys.executable, expr_py],
            cwd=ROOT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(lambda: proc.terminate())
        print("[story] expression_editor を自動起動しました (http://localhost:8772)")
    except Exception as e:
        print(f"[story] expression_editor の自動起動に失敗: {e}（表情タブは手動起動が必要）")


def main():
    parser = argparse.ArgumentParser(description="ストーリーエディタ")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()

    _maybe_start_scene_editor()
    _maybe_start_expression_editor()

    server = ThreadingHTTPServer(("localhost", args.port), StoryEditorHandler)
    print(f"ストーリーエディタ起動: http://localhost:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
