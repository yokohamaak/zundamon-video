"""
シーンライブラリ編集ツール（ローカルWebアプリ・標準ライブラリのみ）。

video/public/story-scenes.json をブラウザでビジュアル編集する。
キャラのscale・anchorをドラッグ/スライダーで調整できる。

使い方: python scene_editor.py [--port 8770]
"""
import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
SCENES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "story-scenes.json")
MANIFEST_JSON = os.path.join(VIDEO_PUBLIC_DIR, "avatars", "manifest.json")
BG_DIR = os.path.join(VIDEO_PUBLIC_DIR, "background")
AVATARS_DIR = os.path.join(VIDEO_PUBLIC_DIR, "avatars")
MOBS_DIR = os.path.join(VIDEO_PUBLIC_DIR, "mobs")

# モブ一覧（StoryVideo.tsx の MOBS 登録と二重管理。MVPのためハードコード）
MOBS_LIST = [
    {"id": "営業", "image": "mobs/mob_normal.png"},
    {"id": "部長", "image": "mobs/manager_normal.png"},
]

_CT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _load_page():
    path = os.path.join(ROOT_DIR, "scene_editor.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_scenes():
    if not os.path.exists(SCENES_JSON):
        return {"scenes": {}}
    with open(SCENES_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_scenes(data):
    """検証してから story-scenes.json に書き戻す。既存フィールドを保持。"""
    if not isinstance(data, dict) or "scenes" not in data:
        raise ValueError("scenes キーが必要です")
    if not isinstance(data["scenes"], dict):
        raise ValueError("scenes は object である必要があります")
    for scene_id, scene in data["scenes"].items():
        if not isinstance(scene, dict):
            raise ValueError(f"シーン {scene_id} が不正")
        if "bg" not in scene or not isinstance(scene.get("bg"), str):
            raise ValueError(f"シーン {scene_id}: bg(文字列)が必要です")
    with open(SCENES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _list_assets():
    backgrounds = []
    if os.path.isdir(BG_DIR):
        for fn in sorted(os.listdir(BG_DIR)):
            ext = os.path.splitext(fn)[1].lower()
            if ext in _CT:
                backgrounds.append(f"background/{fn}")

    characters = []
    if os.path.exists(MANIFEST_JSON):
        with open(MANIFEST_JSON, encoding="utf-8") as f:
            manifest = json.load(f)
        # _full サフィックスは内部用キーのため除外。ベースcharIdのみ返す
        characters = sorted(k for k in manifest.keys() if not k.endswith('_full'))
    else:
        if os.path.isdir(AVATARS_DIR):
            for entry in sorted(os.listdir(AVATARS_DIR)):
                if os.path.isdir(os.path.join(AVATARS_DIR, entry)):
                    characters.append(entry)

    return {"backgrounds": backgrounds, "characters": characters, "mobs": MOBS_LIST}


def _safe_path(base_dir, relative):
    """パストラバーサルを防いで安全なパスを返す。"""
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


class SceneEditorHandler(BaseHTTPRequestHandler):
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
                self._send_error_json(500, "scene_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/scenes":
            try:
                data = _load_scenes()
                self._send_json(data)
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/list-assets":
            try:
                self._send_json(_list_assets())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path.startswith("/img/"):
            rel = path[len("/img/"):]
            safe = _safe_path(VIDEO_PUBLIC_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_image(safe)

        elif path.startswith("/avatars/"):
            rel = path[len("/avatars/"):]
            safe = _safe_path(AVATARS_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_image(safe)

        elif path.startswith("/mobs/"):
            rel = path[len("/mobs/"):]
            safe = _safe_path(MOBS_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_image(safe)

        else:
            self._send_error_json(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/scenes":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_scenes(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
        else:
            self._send_error_json(404, "Not Found")


def main():
    parser = argparse.ArgumentParser(description="シーンライブラリ編集ツール")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("localhost", args.port), SceneEditorHandler)
    print(f"シーンエディタ起動: http://localhost:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
