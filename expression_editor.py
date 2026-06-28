"""
表情エディタ（ローカルWebアプリ・標準ライブラリのみ）。

video/public/expressions.json をブラウザでビジュアル編集する。
各スロット(cheek/brow/eye/mouth/fx)の候補をクリックで割当し、
合成プレビューを即時更新→保存→書き出しまで。

使い方: python expression_editor.py [--port 8772]
"""
import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(ROOT_DIR, "video")
VIDEO_PUBLIC_DIR = os.path.join(ROOT_DIR, "video", "public")
EXPRESSIONS_JSON = os.path.join(VIDEO_PUBLIC_DIR, "expressions.json")
AVATARS_ASSETS_DIR = os.path.join(ROOT_DIR, "video", "assets", "avatars")
AVATARS_PUBLIC_DIR = os.path.join(VIDEO_PUBLIC_DIR, "avatars")

# ─── スロットID → ラベル（psd-export.mjs の SLOTS 定義と一致） ──────────────
SLOT_LABELS = {
    "zundamon": {
        "cheek": {
            "hoppe":     "ほっぺ",
            "hoppe2":    "ほっぺ2",
            "hoppe_red": "ほっぺ赤め",
            "pale":      "青ざめ",
        },
        # タスクA: shadow を cheek から独立
        "shadow": {
            "kageri": "かげり",
        },
        "brow": {
            "normal":    "普通眉",
            "worry1":    "困り眉1",
            "worry2":    "困り眉2",
            "up":        "上がり眉",
            "angry":     "怒り眉",
        },
        # タスクC: happy/smile 削除 → nikkori に統一
        "eye": {
            "open":     "目セット普通",
            "close":    "UU",
            "surprise": "〇〇",
            "nikkori":  "にっこり",
        },
        # タスクC: smile_close/smile_open 削除 → mufu に統一
        "mouth": {
            "close": "むー",
            "half":  "ほあ",
            "open":  "ほあー",
            "mufu":  "むふ",
        },
        "fx": {
            "sweat1": "汗1",
            "sweat2": "汗2",
            "sweat3": "汗3",
        },
    },
    "metan": {
        "cheek": {
            "normal":   "普通",
            "normal2":  "普通2",
            "blush":    "赤面",
            "pale":     "青ざめ",
        },
        # タスクA: shadow を cheek から独立
        "shadow": {
            "kageri": "かげり",
        },
        "brow": {
            "gokigen":      "ごきげん",
            "komari":       "こまり",
            "oko":          "おこ",
            "yayaoko":      "ややおこ",
            "futo_gokigen": "太眉ごきげん",
            "futo_komari":  "太眉こまり",
            "futo_oko":     "太眉おこ",
        },
        # タスクC: happy(=close重複)/smile(未使用) 削除
        "eye": {
            "open":     "目セット普通",
            "close":    "目閉じ",
            "surprise": "○○",
        },
        # タスクC: smile_close/smile_open 削除
        "mouth": {
            "close": "ほほえみ",
            "half":  "お",
            "open":  "わあー",
        },
        "fx": {
            "sweat": "汗",
        },
    },
}

# スロット列挙順（タスクA: shadow 追加）
SLOTS_ORDER = ["cheek", "shadow", "brow", "eye", "mouth", "fx"]

# 表情キー一覧
EXPRESSIONS = ["normal", "happy", "surprise", "trouble", "panic"]

# キャラ一覧
CHARS = ["zundamon", "metan"]

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _load_page():
    path = os.path.join(ROOT_DIR, "expression_editor.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_expressions():
    if not os.path.exists(EXPRESSIONS_JSON):
        return {}
    with open(EXPRESSIONS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_expressions(data):
    """検証してから expressions.json に書き戻す。"""
    if not isinstance(data, dict):
        raise ValueError("expressions は object である必要があります")
    for char_key, exprs in data.items():
        if not isinstance(exprs, dict):
            raise ValueError(f"{char_key} は object である必要があります")
        for expr_name, cfg in exprs.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"{char_key}.{expr_name} が不正")
    with open(EXPRESSIONS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _build_catalog():
    """
    各キャラ・各スロットの候補一覧を返す。
    candidates/ に存在するファイルのみ列挙する。

    返却形式:
    {
      "zundamon": {
        "slots": {
          "cheek": [{"id":"hoppe","label":"ほっぺ","file":"avatars/zundamon/candidates/cheek_hoppe.png"}, ...],
          ...
        },
        "base": "avatars/zundamon/base.png",
        "arm": "avatars/zundamon/arm_normal.png"
      },
      "metan": { ... }
    }
    """
    catalog = {}
    for char in CHARS:
        cand_pub = os.path.join(AVATARS_PUBLIC_DIR, char, "candidates")
        char_labels = SLOT_LABELS.get(char, {})
        slots = {}
        missing_slots = []

        for slot in SLOTS_ORDER:
            items = []
            slot_labels = char_labels.get(slot, {})

            # mouth スロットは mouth_close/mouth_half/mouth_open として返す
            # ただし内部 slot 名は "mouth" なのでキーは mouth_close 等にまとめる
            # ここでは slot="mouth" として candidates の mouth_*.png を列挙
            for cand_id, label in slot_labels.items():
                fname = f"{slot}_{cand_id}.png"
                fpath_pub = os.path.join(cand_pub, fname)
                # public側に存在するかチェック
                if os.path.isfile(fpath_pub):
                    file_url = f"avatars/{char}/candidates/{fname}"
                    items.append({"id": cand_id, "label": label, "file": file_url})

            if not items:
                missing_slots.append(slot)
            slots[slot] = items

        # base.png の存在チェック
        base_path = os.path.join(AVATARS_PUBLIC_DIR, char, "base.png")
        base_url = f"avatars/{char}/base.png" if os.path.isfile(base_path) else None

        # arm_normal.png の存在チェック
        arm_path = os.path.join(AVATARS_PUBLIC_DIR, char, "arm_normal.png")
        arm_url = f"avatars/{char}/arm_normal.png" if os.path.isfile(arm_path) else None

        # タスクB: bangs.png の存在チェック（metan のみ存在）
        bangs_path = os.path.join(AVATARS_PUBLIC_DIR, char, "bangs.png")
        bangs_url = f"avatars/{char}/bangs.png" if os.path.isfile(bangs_path) else None

        if missing_slots:
            print(f"[catalog] WARN: {char} の以下スロットに candidates がありません: {missing_slots}")
            print(f"  -> node scripts/psd-export.mjs candidates {char} を実行してください")

        catalog[char] = {
            "slots": slots,
            "base": base_url,
            "arm": arm_url,
            "bangs": bangs_url,
        }

    return catalog


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


class ExpressionEditorHandler(BaseHTTPRequestHandler):
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
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            try:
                body = _load_page().encode("utf-8")
            except FileNotFoundError:
                self._send_error_json(500, "expression_editor.html が見つかりません")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/catalog":
            try:
                self._send_json(_build_catalog())
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/expressions":
            try:
                self._send_json(_load_expressions())
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

        if path == "/api/expressions":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                _save_expressions(data)
                self._send_json({"ok": True})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_error_json(400, str(e))
            except Exception as e:
                self._send_error_json(500, str(e))

        elif path == "/api/export":
            # パーツ再生成: psd-export build/build-full + prep-story + build-story-player を順に実行。
            # stdoutをストリームで返す。末尾に __DONE__ ok / __DONE__ err... を送る。
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

            # 実行するコマンド列（順に実行）
            cmds = [
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

        else:
            self._send_error_json(404, "Not Found")


def _public_candidates_missing():
    """public 側(描画/カタログが参照)に候補PNGが無いキャラを返す。"""
    missing = []
    for char in CHARS:
        cand_dir = os.path.join(AVATARS_PUBLIC_DIR, char, "candidates")
        if not os.path.isdir(cand_dir) or not any(
            f.endswith(".png") for f in os.listdir(cand_dir)
        ):
            missing.append(char)
    return missing


def _ensure_candidates():
    """起動時に candidates(public) が無ければ自動生成する。

    node scripts/psd-export.mjs candidates <char>(assetsへ書き出し) を未生成キャラ分、
    続けて prep-story.mjs(assets->public コピー) を実行する。ローカル node 実行のみ。
    node や PSD が無い等で失敗しても起動は続行し、画面側で「候補なし」表示になる。
    """
    missing = _public_candidates_missing()
    if not missing:
        return
    print(f"[expr] 候補PNG未生成のキャラ: {missing} -> 自動生成します...")
    try:
        for char in missing:
            print(f"[expr] candidates {char} ...")
            r = subprocess.run(
                ["node", "scripts/psd-export.mjs", "candidates", char],
                cwd=VIDEO_DIR, capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"[expr] candidates {char} 失敗:\n{r.stderr.strip()}")
                return
        print("[expr] prep-story (public へコピー) ...")
        r = subprocess.run(
            ["node", "scripts/prep-story.mjs"],
            cwd=VIDEO_DIR, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"[expr] prep-story 失敗:\n{r.stderr.strip()}")
            return
        print("[expr] 候補PNGの自動生成が完了しました。")
    except FileNotFoundError:
        print("[expr] node が見つからず候補を自動生成できませんでした。")
        print("       手動: cd video && node scripts/psd-export.mjs candidates zundamon && "
              "node scripts/psd-export.mjs candidates metan && node scripts/prep-story.mjs")


def main():
    parser = argparse.ArgumentParser(description="表情エディタ")
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()

    _ensure_candidates()

    server = ThreadingHTTPServer(("localhost", args.port), ExpressionEditorHandler)
    print(f"表情エディタ起動: http://localhost:{args.port}")
    print("終了: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")


if __name__ == "__main__":
    main()
