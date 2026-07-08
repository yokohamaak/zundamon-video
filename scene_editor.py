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
import shutil
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

# 背景・モブのソースは video/assets/ 配下（git追跡）。public/ は生成物(gitignore)で
# 描画・render はこちらを staticFile で読む。ユーザーは assets/ に画像を置くため、
# 一覧取得時に assets→public を差分コピーして「置いたら即プルダウンに出る」を実現する。
ASSETS_DIR = os.path.join(ROOT_DIR, "video", "assets")
_SYNC_SUBS = [
    ("background", (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov")),
    ("mobs", (".png", ".webp")),
]


def _sync_assets_to_public():
    """assets/<sub> → public/<sub> を差分コピー（無い or サイズ/更新時刻が違うもの）。"""
    for sub, exts in _SYNC_SUBS:
        src = os.path.join(ASSETS_DIR, sub)
        dst = os.path.join(VIDEO_PUBLIC_DIR, sub)
        if not os.path.isdir(src):
            continue
        os.makedirs(dst, exist_ok=True)
        for fn in os.listdir(src):
            if os.path.splitext(fn)[1].lower() not in exts:
                continue
            s = os.path.join(src, fn)
            if not os.path.isfile(s):
                continue
            d = os.path.join(dst, fn)
            try:
                if (not os.path.exists(d)
                        or os.path.getsize(d) != os.path.getsize(s)
                        or os.path.getmtime(d) < os.path.getmtime(s)):
                    shutil.copy2(s, d)
            except OSError:
                pass

MOBS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "mobs.json")


def _list_mobs():
    """モブ管理画面の保存先 mobs.json からモブ一覧を作る。
    サムネイル/ステージ表示には normal.closed(口閉じ通常)を代表画像として使う。"""
    if not os.path.exists(MOBS_JSON):
        return []
    try:
        with open(MOBS_JSON, encoding="utf-8") as f:
            mobs = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for mob_id, cfg in mobs.items():
        images = (cfg or {}).get("images") or {}
        image = (images.get("normal") or {}).get("closed") or ""
        out.append({"id": mob_id, "image": image})
    return out

_CT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
_BG_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_BG_VIDEO_EXTS = (".mp4", ".webm", ".mov")


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
        bg = scene.get("bg")
        bg_video = scene.get("bgVideo")
        if bg is not None and not isinstance(bg, str):
            raise ValueError(f"シーン {scene_id}: bg は文字列である必要があります")
        if bg_video is not None and not isinstance(bg_video, str):
            raise ValueError(f"シーン {scene_id}: bgVideo は文字列である必要があります")
        if not ((isinstance(bg, str) and bg) or (isinstance(bg_video, str) and bg_video)):
            raise ValueError(f"シーン {scene_id}: bg または bgVideo のどちらかが必要です")
        bg_video_loop = scene.get("bgVideoLoop")
        if bg_video_loop is not None and not isinstance(bg_video_loop, bool):
            raise ValueError(f"シーン {scene_id}: bgVideoLoop は true/false である必要があります")
    with open(SCENES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _list_assets():
    _sync_assets_to_public()
    backgrounds = []
    background_videos = []
    if os.path.isdir(BG_DIR):
        for fn in sorted(os.listdir(BG_DIR)):
            ext = os.path.splitext(fn)[1].lower()
            if ext in _BG_IMAGE_EXTS:
                backgrounds.append(f"background/{fn}")
            elif ext in _BG_VIDEO_EXTS:
                background_videos.append(f"background/{fn}")

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

    return {
        "backgrounds": backgrounds,
        "backgroundVideos": background_videos,
        "characters": characters,
        "mobs": _list_mobs(),
    }


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

    def _send_static(self, path):
        if not os.path.isfile(path):
            self._send_error_json(404, "ファイルが見つかりません")
            return
        ext = os.path.splitext(path)[1].lower()
        ct = _CT.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")
        start, end = 0, file_size - 1
        is_partial = False
        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes="):].split(",")[0].strip()
            try:
                if spec.startswith("-"):
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
        self.send_header("Content-Type", ct)
        self.send_header("Accept-Ranges", "bytes")
        # 同名ファイル上書き(モブ画像等)でURLが変わらないため、キャッシュ禁止にしないと
        # 差し替え後も古い画像が表示され続ける。
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        if is_partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, file_size))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
            self._send_static(safe)

        elif path.startswith("/avatars/"):
            rel = path[len("/avatars/"):]
            safe = _safe_path(AVATARS_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_static(safe)

        elif path.startswith("/mobs/"):
            rel = path[len("/mobs/"):]
            safe = _safe_path(MOBS_DIR, rel)
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_static(safe)

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
    parser.add_argument("--host", default="localhost",
                        help="待受ホスト。Tailscale等でスマホから開くには 0.0.0.0")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), SceneEditorHandler)
    print(f"シーンエディタ起動: http://{args.host}:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
