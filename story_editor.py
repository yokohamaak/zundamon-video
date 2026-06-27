"""
ストーリーエディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/story-01.json をブラウザで台本編集する。
turn の並び・話者・セリフ・場面・表情・演出・insertを編集できる。

使い方: python story_editor.py [--port 8771]
"""
import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
STORY_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-01.json")
SCENES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-scenes.json")

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

EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]
INSERT_KINDS = ["warning", "ok", "chat", "teamchat"]

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
        """任意のファイルを返す。path が None または存在しない場合は 404。"""
        if path is None or not os.path.isfile(path):
            self._send_error_json(404, "Not Found")
            return
        if content_type is None:
            ext = os.path.splitext(path)[1].lower()
            content_type = self._MIME.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                    "expressions": EXPRESSIONS,
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
        elif path == "/api/audio":
            # VOICEVOX で音声生成（make_story_audio.py）。事前に保存済みの story-01.json を読む。
            # 要 VOICEVOX 起動（http://localhost:50021）。
            try:
                result = subprocess.run(
                    [sys.executable, "make_story_audio.py", "story-01"],
                    cwd=ROOT_DIR, capture_output=True, text=True, timeout=600,
                )
                log = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if result.returncode == 0:
                    self._send_json({"ok": True, "message": "音声生成 完了", "log": log[-3000:]})
                else:
                    self._send_json({"ok": False,
                                     "message": "音声生成 失敗（VOICEVOX起動を確認）",
                                     "log": log[-3000:]})
            except subprocess.TimeoutExpired:
                self._send_error_json(504, "音声生成がタイムアウトしました")
            except Exception as e:
                self._send_error_json(500, str(e))
        else:
            self._send_error_json(404, "Not Found")


def main():
    parser = argparse.ArgumentParser(description="ストーリーエディタ")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("localhost", args.port), StoryEditorHandler)
    print(f"ストーリーエディタ起動: http://localhost:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
