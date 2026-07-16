"""
ポーズエディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/poses.json をブラウザでビジュアル編集する。
各ポーズの腕差分をクリックで割当し、アニメ付きプレビューを即時更新する。

使い方: python pose_editor.py [--port 8773]
"""
import argparse
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(ROOT_DIR, "video")
VIDEO_ASSETS_DIR = os.path.join(ROOT_DIR, "video", "assets")
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
POSES_JSON = os.path.join(VIDEO_PUBLIC_DIR, "poses.json")
POSES_ASSETS_JSON = os.path.join(VIDEO_ASSETS_DIR, "poses.json")
EXPRESSIONS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "expressions.json")
AVATARS_PUBLIC_DIR = os.path.join(VIDEO_PUBLIC_DIR, "avatars")

CHARS = ["zundamon", "metan"]
POSES = [
    "idle", "cheer", "recoil", "lean", "droop", "flustered",
    "proud", "step_in", "step_back", "listening", "sneak", "wobble",
    "point",
]

POSE_LABELS = {
    "idle": "通常",
    "cheer": "喜ぶ",
    "recoil": "のけぞる",
    "lean": "身を乗り出す",
    "droop": "しょんぼり",
    "flustered": "あたふた",
    "proud": "得意げ",
    "step_in": "一歩前へ",
    "step_back": "一歩引く",
    "listening": "聞き耳",
    "sneak": "こそこそ",
    "wobble": "ぐらぐら",
    "point": "指差し",
}

ARM_LABELS = {
    "zundamon": {
        "arm_normal": "通常",
        "arm_raise": "手を挙げる",
        "arm_mouth": "口元",
        "arm_suffering": "苦しむ",
        "arm_waist": "腰",
        "arm_whisper": "ひそひそ",
        "arm_think": "考える",
        "arm_point": "指差し",
        "arm_mic": "マイク",
    },
    "metan": {
        "arm_normal": "通常",
        "arm_hush": "ひそひそ",
        "arm_mouth": "口元に指",
        "arm_hold": "抱える",
        "arm_point": "指差す",
        "arm_present": "手をかざす",
        "arm_mic": "マイク",
        "arm_manju": "まんじゅう",
    },
}

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _load_page():
    with open(os.path.join(ROOT_DIR, "pose_editor.html"), encoding="utf-8") as f:
        return f.read()


def _default_poses():
    return {
        "zundamon": {
            "idle": {"arm": "arm_normal", "speed": 1.0, "strength": 1.0},
            "cheer": {"arm": "arm_raise", "speed": 1.0, "strength": 1.0},
            "recoil": {"arm": "arm_raise", "speed": 1.0, "strength": 1.0},
            "lean": {"arm": "arm_think", "speed": 1.0, "strength": 1.0},
            "droop": {"arm": "arm_waist", "speed": 1.0, "strength": 1.0},
            "flustered": {"arm": "arm_mouth", "speed": 1.0, "strength": 1.0},
            "proud": {"arm": "arm_point", "speed": 1.0, "strength": 1.0},
            "step_in": {"arm": "arm_point", "speed": 1.0, "strength": 1.0},
            "step_back": {"arm": "arm_waist", "speed": 1.0, "strength": 1.0},
            "listening": {"arm": "arm_think", "speed": 1.0, "strength": 1.0},
            "sneak": {"arm": "arm_whisper", "speed": 1.0, "strength": 1.0},
            "wobble": {"arm": "arm_suffering", "speed": 1.0, "strength": 1.0},
            "point": {"arm": "arm_point", "speed": 1.0, "strength": 1.0},
        },
        "metan": {
            "idle": {"arm": "arm_normal", "speed": 1.0, "strength": 1.0},
            "cheer": {"arm": "arm_present", "speed": 1.0, "strength": 1.0},
            "recoil": {"arm": "arm_mouth", "speed": 1.0, "strength": 1.0},
            "lean": {"arm": "arm_hold", "speed": 1.0, "strength": 1.0},
            "droop": {"arm": "arm_hold", "speed": 1.0, "strength": 1.0},
            "flustered": {"arm": "arm_mouth", "speed": 1.0, "strength": 1.0},
            "proud": {"arm": "arm_present", "speed": 1.0, "strength": 1.0},
            "step_in": {"arm": "arm_point", "speed": 1.0, "strength": 1.0},
            "step_back": {"arm": "arm_hold", "speed": 1.0, "strength": 1.0},
            "listening": {"arm": "arm_hush", "speed": 1.0, "strength": 1.0},
            "sneak": {"arm": "arm_hush", "speed": 1.0, "strength": 1.0},
            "wobble": {"arm": "arm_manju", "speed": 1.0, "strength": 1.0},
            "point": {"arm": "arm_point", "speed": 1.0, "strength": 1.0},
        },
    }


def _load_poses():
    if not os.path.exists(POSES_JSON):
        if os.path.exists(POSES_ASSETS_JSON):
            with open(POSES_ASSETS_JSON, encoding="utf-8") as f:
                return json.load(f)
        return _default_poses()
    with open(POSES_JSON, encoding="utf-8") as f:
        return json.load(f)


def _load_expressions():
    if not os.path.exists(EXPRESSIONS_JSON):
        return {}
    with open(EXPRESSIONS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_poses(data):
    if not isinstance(data, dict):
        raise ValueError("poses は object である必要があります")
    for char_key, pose_map in data.items():
        if char_key not in CHARS:
            raise ValueError(f"未知のキャラです: {char_key}")
        if not isinstance(pose_map, dict):
            raise ValueError(f"{char_key} は object である必要があります")
        for pose_name, cfg in pose_map.items():
            if pose_name not in POSES:
                raise ValueError(f"未知のポーズです: {char_key}.{pose_name}")
            if not isinstance(cfg, dict):
                raise ValueError(f"{char_key}.{pose_name} が不正です")
    for path in (POSES_JSON, POSES_ASSETS_JSON):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _build_catalog():
    catalog = {}
    for char in CHARS:
        cand_dir = os.path.join(AVATARS_PUBLIC_DIR, char, "candidates")
        parts_dir = os.path.join(AVATARS_PUBLIC_DIR, char)
        arms = []
        for stem, label in ARM_LABELS.get(char, {}).items():
            cand_path = os.path.join(cand_dir, f"{stem}.png")
            part_path = os.path.join(parts_dir, f"{stem}.png")
            if os.path.isfile(cand_path):
                arms.append({
                    "id": stem,
                    "label": label,
                    "file": f"avatars/{char}/candidates/{stem}.png",
                    "runtimeFile": f"avatars/{char}/{stem}.png" if os.path.isfile(part_path) else None,
                })
        catalog[char] = {
            "base": f"avatars/{char}/base.png" if os.path.isfile(os.path.join(parts_dir, "base.png")) else None,
            "bangs": f"avatars/{char}/bangs.png" if os.path.isfile(os.path.join(parts_dir, "bangs.png")) else None,
            "arms": arms,
        }
    return catalog


def _safe_path(base_dir, relative):
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


class PoseEditorHandler(BaseHTTPRequestHandler):
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
        ct = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            body = _load_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/catalog" or path == "/api/pose-catalog":
            self._send_json(_build_catalog())
        elif path == "/api/poses":
            self._send_json(_load_poses())
        elif path == "/api/expressions":
            self._send_json(_load_expressions())
        elif path.startswith("/img/"):
            safe = _safe_path(VIDEO_PUBLIC_DIR, path[len("/img/"):])
            if safe is None:
                self._send_error_json(400, "不正なパスです")
                return
            self._send_image(safe)
        else:
            self._send_error_json(404, "Not Found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/poses":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_poses(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))
            return

        if path == "/api/export" or path == "/api/pose-export":
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
                for cmd in cmds:
                    label = " ".join(cmd)
                    if not emit(f"=== {label} ===\n"):
                        break
                    proc = subprocess.Popen(
                        cmd,
                        cwd=VIDEO_DIR,
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
            return

        self._send_error_json(404, "Not Found")


def _public_candidates_missing():
    missing = []
    for char in CHARS:
        cand_dir = os.path.join(AVATARS_PUBLIC_DIR, char, "candidates")
        if not os.path.isdir(cand_dir):
            missing.append(char)
            continue
        if not any(f.startswith("arm_") and f.endswith(".png") for f in os.listdir(cand_dir)):
            missing.append(char)
    return missing


def _ensure_pose_assets():
    missing = _public_candidates_missing()
    if not missing:
        return
    print(f"[pose] 腕候補PNG未生成のキャラ: {missing} -> 自動生成します...")
    try:
        for char in missing:
            r = subprocess.run(
                ["node", "scripts/psd-export.mjs", "candidates", char],
                cwd=VIDEO_DIR, capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"[pose] candidates {char} 失敗:\n{r.stderr.strip()}")
                return
        r = subprocess.run(
            ["node", "scripts/prep-story.mjs"],
            cwd=VIDEO_DIR, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"[pose] prep-story 失敗:\n{r.stderr.strip()}")
            return
        print("[pose] 候補PNGの自動生成が完了しました。")
    except FileNotFoundError:
        print("[pose] node が見つからず候補を自動生成できませんでした。")


def main():
    parser = argparse.ArgumentParser(description="ポーズエディタ")
    parser.add_argument("--port", type=int, default=8773)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    _ensure_pose_assets()

    server = ThreadingHTTPServer((args.host, args.port), PoseEditorHandler)
    print(f"ポーズエディタ起動: http://{args.host}:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
