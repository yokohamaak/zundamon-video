"""
ストーリーエディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/story-01.json をブラウザで台本編集する。
turn の並び・話者・セリフ・場面・表情・演出・insertを編集できる。

使い方: python story_editor.py [--port 8771]
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
STORY_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-01.json")
SCENES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-scenes.json")

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
