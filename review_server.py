"""
画像レビュー承認ツール（ローカルWebアプリ・標準ライブラリのみ）。

パイプラインの画像チェックポイント:
  [A] python main_story.py --stop-after-images        # 取得して review.json を出力・停止
  [B] python review_server.py --dir docs/story         # ← これ。ブラウザで確認/差し替え/承認
  [C] python main_story.py --from-script docs/story/script.json --images-from-dir  # 承認後に続行

設計（将来 Cloudflare 等へ移行しやすいよう）:
  - 静的フロント + JSON API に分離（フロントは相対URLで /api/* を叩く＝ホスト非依存）。
  - クライアントにはファイルパスを出さず「キー(ch_ci)」で扱う。画像は /img/<key> で配信、
    アップロードは base64 JSON（Workers でも動く形）。
  - 状態(review.json)・画像ファイルの読み書きは下記の小関数に隔離（→ R2/KV へ差し替え可能）。
  - 「承認して続行」は review.json に status=approved を立てるだけ（移行可能な契約）。
    続行コマンドはUIに表示する（ローカルで人が実行）。
"""
import argparse
import base64
import datetime
import json
import mimetypes
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# レビュー対象ディレクトリ（既定 docs/story）。main()で上書き。/shorts から実行時に切替可能。
DIR = "docs/story"
# 基準（本編）ディレクトリ。ショートのネタ元・docs/shorts の親判定に使う（起動時に固定）。
BASE_DIR = "docs/story"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(ROOT_DIR, "video")
VIDEO_ASSETS_DIR = os.path.join(VIDEO_DIR, "assets")
VIDEO_PUBLIC_DIR = os.path.join(VIDEO_DIR, "public")


def _load_page(filename):
    """同梱されたUIテンプレートを実行場所に依存せず読み込む。"""
    with open(os.path.join(ROOT_DIR, filename), encoding="utf-8") as file:
        return file.read()


_CT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

_PREVIEW_ASSET_DIRS = {"avatars", "fonts", "background", "bgm", "se"}


def preview_asset_path(relative):
    """Remotion Player用アセットを許可ディレクトリ内だけから解決する。"""
    if not isinstance(relative, str):
        return None
    rel = unquote(relative).replace("\\", "/").strip("/")
    if not rel or rel.startswith(".") or "/../" in f"/{rel}/":
        return None
    parts = rel.split("/")
    if parts[0] in _PREVIEW_ASSET_DIRS:
        base = VIDEO_ASSETS_DIR
    elif len(parts) == 1 and (rel in {"meta.json", "digest.mp3"} or
                              rel.startswith("ch_") or rel.startswith("manual_")):
        base = os.path.abspath(DIR)
    else:
        return None
    path = os.path.abspath(os.path.join(base, *parts))
    try:
        if os.path.commonpath([path, os.path.abspath(base)]) != os.path.abspath(base):
            return None
    except ValueError:
        return None
    return path if os.path.isfile(path) else None


def avatar_manifest():
    """assets/avatars/<キャラ>/ のパーツ一覧をPlayerへ渡す。"""
    out = {}
    root = os.path.join(VIDEO_ASSETS_DIR, "avatars")
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        parts = {}
        for filename in sorted(os.listdir(directory)):
            ext = os.path.splitext(filename)[1].lower()
            if ext in {".png", ".webp", ".svg"}:
                parts[os.path.splitext(filename)[0]] = filename
        if parts:
            out[name] = parts
    return out


def depth_manifest():
    """レビュー対象内で深度マップを持つ画像名一覧を返す。"""
    if not os.path.isdir(DIR):
        return []
    files = os.listdir(DIR)
    depth_bases = {f[:-len(".depth.png")] for f in files if f.lower().endswith(".depth.png")}
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    return sorted(f for f in files if os.path.splitext(f)[1].lower() in image_exts and
                  not f.lower().endswith(".depth.png") and
                  os.path.splitext(f)[0] in depth_bases)


# ---- ストレージ層（ここだけ差し替えれば R2/KV 等へ移行できる） ----

def load_review():
    path = os.path.join(DIR, "review.json")
    if not os.path.exists(path):
        return {"cuts": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_review(review):
    path = os.path.join(DIR, "review.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2)


def read_image_bytes(filename):
    path = os.path.join(DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def image_dims(filename):
    """画像の(幅,高さ)をヘッダから読む。Pillow不要・PNG/GIF/JPEG/WEBP/BMP対応。失敗時None。"""
    path = os.path.join(DIR, filename)
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if len(head) < 24:
                return None
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"BM":
                w, h = struct.unpack("<ii", head[18:26])
                return (abs(w), abs(h))
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                fmt = head[12:16]
                f.seek(0)
                d = f.read(30)
                if fmt == b"VP8X":
                    w = 1 + (d[24] | d[25] << 8 | d[26] << 16)
                    h = 1 + (d[27] | d[28] << 8 | d[29] << 16)
                    return (w, h)
                if fmt == b"VP8 ":
                    return (struct.unpack("<H", d[26:28])[0] & 0x3FFF,
                            struct.unpack("<H", d[28:30])[0] & 0x3FFF)
                if fmt == b"VP8L":
                    b0, b1, b2, b3 = d[21], d[22], d[23], d[24]
                    return (1 + (((b1 & 0x3F) << 8) | b0),
                            1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6)))
                return None
            if head[:2] == b"\xff\xd8":  # JPEG: SOFマーカーまで読み進める
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if not marker:
                        return None
                    m = marker[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        f.read(3)
                        h = struct.unpack(">H", f.read(2))[0]
                        w = struct.unpack(">H", f.read(2))[0]
                        return (w, h)
                    seg = f.read(2)
                    if len(seg) < 2:
                        return None
                    f.seek(struct.unpack(">H", seg)[0] - 2, 1)
    except Exception:
        return None
    return None


def write_image_bytes(filename, data):
    with open(os.path.join(DIR, filename), "wb") as f:
        f.write(data)


def remove_file(name):
    if not name:  # 未取得カット(image=None)で os.path.join が落ちるのを防ぐ
        return
    p = os.path.join(DIR, name)
    if os.path.exists(p):
        os.remove(p)


def rename_file(old, new):
    if not (old and new):  # None を join する前に弾く
        return
    a, b = os.path.join(DIR, old), os.path.join(DIR, new)
    if os.path.exists(a):
        os.replace(a, b)


def reindex_review_after_cut_delete(ch, ci):
    """章 ch のカット ci を削除した整合を review.json に反映（位置キーのずれを防ぐ）。

    - (ch,ci) のカットと画像ファイルを削除。
    - (ch, ci'>ci) のカットを ci'-1 へ詰め、画像ファイルも ch_ch_(ci'-1).ext へリネーム。
    ※ 昇順で処理（削除でci枠が空くので衝突しない）。Returns: {ok, removed, shifted}
    """
    review = load_review()
    cuts = review.get("cuts", [])
    removed_img, kept = None, []
    for c in cuts:
        if c.get("ch") == ch and c.get("ci") == ci:
            removed_img = c.get("image")
            continue
        kept.append(c)
    review["cuts"] = kept
    if removed_img:
        remove_file(removed_img)
    shifts = sorted([c for c in kept if c.get("ch") == ch and c.get("ci", 0) > ci],
                    key=lambda c: c["ci"])
    for c in shifts:
        new_ci = c["ci"] - 1
        img = c.get("image")
        if img:
            new_name = f"ch_{ch:02d}_{new_ci:02d}{os.path.splitext(img)[1]}"
            rename_file(img, new_name)
            c["image"] = new_name
        c["ci"] = new_ci
    save_review(review)
    return {"ok": True, "removed": removed_img, "shifted": len(shifts)}


def load_script():
    path = os.path.join(DIR, "script.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_script(data):
    with open(os.path.join(DIR, "script.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_meta():
    path = os.path.join(DIR, "meta.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_meta(meta):
    with open(os.path.join(DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ---- ショート（縦9:16）：本編ネタから独立ショートを作る制作ハブ ----
# 本編 docs/story の trivia ネタを選んで「ショート台本を別生成」→ docs/shorts/<slug>/ に独立した
# script/review/meta/digest/画像 を作る。レビューは対象ディレクトリを切替えて既存 /story を使う。

SHORTS_ROOT = "docs/shorts"
SPOKEN_CPM = 305          # 喋り実測較正（/storyゲージと共通）
SHORT_TARGET_SECONDS = 40  # ショートの目標尺（秒）


def is_short_dir():
    """現在の対象がショート（docs/shorts/配下）か。目標文字数の出し分けに使う。"""
    d = os.path.normpath(DIR)
    return d.startswith(os.path.normpath(SHORTS_ROOT) + os.sep)


def target_for_dir():
    """対象に応じた目標 {chars,label}。ショートは約40秒、本編は8分。"""
    if is_short_dir():
        return {"chars": round(SPOKEN_CPM * SHORT_TARGET_SECONDS / 60), "label": f"約{SHORT_TARGET_SECONDS}秒"}
    return {"chars": 8 * SPOKEN_CPM, "label": "8分"}


def _slugify(s):
    s = re.sub(r"[^0-9A-Za-z_\-ぁ-んァ-ヶ一-龠]+", "-", (s or "").strip()).strip("-")
    return s[:40] or "short"


def set_active_dir(d):
    """レビュー/編集の対象ディレクトリを実行時に切替（本編 or ショート）。docs/配下のみ許可。"""
    global DIR
    d = os.path.normpath(d)
    if not (d == BASE_DIR or d.startswith(SHORTS_ROOT + os.sep) or d.startswith("docs" + os.sep)):
        return {"ok": False, "message": "対象が不正です"}
    if not os.path.isdir(d):
        return {"ok": False, "message": f"{d} がありません"}
    DIR = d
    return {"ok": True, "dir": DIR}


def main_trivia_netas():
    """本編(BASE_DIR)の trivia ネタ一覧 [{ch,title,summary}]（ショート化の元・選択用）。"""
    path = os.path.join(BASE_DIR, "script.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        script = json.load(f)
    out = []
    for i, c in enumerate(script.get("chapters", [])):
        if c.get("section") == "trivia":
            out.append({"ch": i, "title": c.get("title", ""), "summary": c.get("summary", "")})
    return out


def list_shorts():
    """docs/shorts/* の一覧 [{slug,hasScript,hasMeta,hasVideo,hook,title}]。"""
    if not os.path.isdir(SHORTS_ROOT):
        return []
    out = []
    for slug in sorted(os.listdir(SHORTS_ROOT)):
        d = os.path.join(SHORTS_ROOT, slug)
        if not os.path.isdir(d):
            continue
        has_script = os.path.exists(os.path.join(d, "script.json"))
        has_meta = os.path.exists(os.path.join(d, "meta.json"))
        has_video = os.path.exists(os.path.join("video", "out", f"short_{slug}.mp4"))
        title = hook = ""
        if has_script:
            try:
                with open(os.path.join(d, "script.json"), encoding="utf-8") as f:
                    chs = json.load(f).get("chapters", [])
                tri = next((c for c in chs if c.get("section") == "trivia"), {})
                title, hook = tri.get("title", ""), tri.get("hook", "")
            except (OSError, ValueError):
                pass
        out.append({"slug": slug, "dir": d, "hasScript": has_script, "hasMeta": has_meta,
                    "hasVideo": has_video, "title": title, "hook": hook})
    return out


# 進行中ジョブ（id文字列→{proc,log,out?}）。生成/書き出しで共用。
JOBS = {}


def _spawn(job_id, cmd, cwd=None, out=None):
    log_path = os.path.join("video", "out", f"job_{re.sub(r'[^0-9A-Za-z_-]', '_', job_id)}.log")
    os.makedirs(os.path.join("video", "out"), exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(["bash", "-lc", cmd], cwd=cwd, stdout=logf, stderr=subprocess.STDOUT)
    JOBS[job_id] = {"proc": proc, "log": log_path, "out": out}
    return {"ok": True, "job": job_id, "out": out}


def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return {"state": "idle"}
    tail = ""
    try:
        with open(job["log"], encoding="utf-8", errors="replace") as f:
            tail = "".join(f.readlines()[-16:])
    except OSError:
        pass
    rc = job["proc"].poll()
    if rc is None:
        return {"state": "running", "log": tail}
    if rc == 0:
        return {"state": "done", "out": job.get("out"), "log": tail}
    return {"state": "failed", "code": rc, "log": tail}


def start_short_generate(chapters):
    """選択した本編ネタ(複数)を Gemini 1回でまとめてショート化（各 docs/shorts/<slug>/ へ）。

    Gemini＋画像取得を伴う（Mac/.env前提）。完了後 /story を各ショートに向けてレビュー。
    """
    src_script = os.path.join(BASE_DIR, "script.json")
    if not os.path.exists(src_script):
        return {"ok": False, "message": f"{src_script} がありません（本編の台本が必要）"}
    idxs = [int(c) for c in (chapters or []) if str(c).strip() != ""]
    if not idxs:
        return {"ok": False, "message": "ネタを1つ以上選んでください"}
    spec = ",".join(str(i) for i in idxs)
    py = shlex.quote(sys.executable)  # 実行中のpython（環境差を回避）
    cmd = (f"{py} main_story.py --from-script {shlex.quote(src_script)} "
           f"--shorts-from {shlex.quote(spec)}")
    return _spawn("gen:batch", cmd)


def start_short_render_dir(slug, cta=None):
    """docs/shorts/<slug> の独立metaから縦ショートを書き出す（1ネタ=全体）。"""
    slug = _slugify(slug)
    d = os.path.join(SHORTS_ROOT, slug)
    if not os.path.exists(os.path.join(d, "meta.json")):
        return {"ok": False, "message": "meta.json がありません（音声+meta生成が先）"}
    props = {}
    if cta is not None:
        props["ctaText"] = cta
    out = f"out/short_{slug}.mp4"
    src = os.path.relpath(d, "video")
    proparg = f" --props={shlex.quote(json.dumps(props, ensure_ascii=False))}" if props else ""
    cmd = (f"SRC_DIR={shlex.quote(src)} npm run prep && "
           f"npx remotion render DialogueVideoShort {out}{proparg}")
    return _spawn(f"render:{slug}", cmd, cwd="video", out=os.path.join("video", out))


# ---- ブラウザAIで台本を作る（compose）: プロンプト表示→結果貼り付け取り込み ----
# Gemini自動の代わりに、ブラウザのAIにプロンプトを投げて作った台本JSONを貼り付けて取り込む。

def _load_main_script():
    path = os.path.join(BASE_DIR, "script.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compose_main_prompt():
    """本編台本のプロンプト（Geminiに投げているのと同じ build_prompt）＋選定テーマを返す。"""
    from src import story_script, topic_history
    cfg = _load_image_config() or {}
    try:
        genre = topic_history.genre_of(cfg)
        theme = story_script.select_theme(cfg, topic_history.used_themes(genre))
        cfg.setdefault("story", {})["theme"] = theme
        avoid = [{"title": t} for t in topic_history.used_themes(genre)]  # 主題単位で重複回避
    except Exception:  # noqa: BLE001 - 履歴が無い等でも素のプロンプトは出す
        theme, avoid = "", None
    return {"prompt": story_script.build_prompt(cfg, also_avoid=avoid), "theme": theme or "(AIにおまかせ)"}


def import_main_script(text):
    """貼り付けた台本JSONを Geminiと同じパーサで検証し docs/story/script.json に保存。"""
    from src import story_script
    try:
        data = story_script.parse_script_json(text or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"取り込み失敗（JSONを確認）: {e}"}
    if not data.get("script"):
        return {"ok": False, "message": "script が空です"}
    with open(os.path.join(BASE_DIR, "script.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tri = sum(1 for c in data.get("chapters", []) if c.get("section") == "trivia")
    return {"ok": True, "theme": data.get("theme"), "chapters": tri, "turns": len(data["script"])}


def compose_shorts_prompt(chapters):
    """選択ネタのショート一括プロンプト（build_shorts_batch_prompt）を返す。"""
    from src import story_script
    main = _load_main_script()
    if not main:
        return {"ok": False, "message": "本編 script.json がありません（先に本編台本を用意）"}
    sources, targets = story_script.shorts_sources(main, [int(c) for c in (chapters or [])])
    if not targets:
        return {"ok": False, "message": "ネタを1つ以上選んでください"}
    cfg = _load_image_config() or {}
    return {"ok": True, "prompt": story_script.build_shorts_batch_prompt(cfg, sources), "chapters": targets}


def import_shorts_script(text, chapters):
    """貼り付けたショート一括JSONを分解し、各 docs/shorts/<slug>/script.json に保存。"""
    from src import story_script
    idxs = [int(c) for c in (chapters or [])]
    try:
        data = story_script.parse_script_json(text or "")
        results = story_script.shorts_from_parsed(data, len(idxs), _load_image_config() or {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"取り込み失敗: {e}"}
    slugs = []
    for i, sr in enumerate(results):
        title = (sr.get("chapters") or [{}])[0].get("title") or "short"
        slug = f"ch{idxs[i]}_{_slugify(title)}" if i < len(idxs) else f"short{i}_{_slugify(title)}"
        d = os.path.join(SHORTS_ROOT, slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "script.json"), "w", encoding="utf-8") as f:
            json.dump(sr, f, ensure_ascii=False, indent=2)
        slugs.append(slug)
    return {"ok": True, "slugs": slugs}


def start_fetch(target_dir):
    """既存 script.json のディレクトリで画像取得まで実行（--from-script --stop-after-images）。

    本編=docs/story / ショート=docs/shorts/<slug>。締めユニゾンは docs/shorts 出力では付かない。
    """
    sp = os.path.join(target_dir, "script.json")
    if not os.path.exists(sp):
        return {"ok": False, "message": f"{sp} がありません"}
    py = shlex.quote(sys.executable)
    cmd = (f"{py} main_story.py --from-script {shlex.quote(sp)} "
           f"--stop-after-images --output-dir {shlex.quote(target_dir)}")
    return _spawn(f"fetch:{target_dir}", cmd)


def start_short_audio(slug):
    """ショートの音声+meta を生成（VOICEVOX・--images-from-dir）。画像取得(review.json)済みが前提。"""
    slug = _slugify(slug)
    d = os.path.join(SHORTS_ROOT, slug)
    if not os.path.exists(os.path.join(d, "script.json")):
        return {"ok": False, "message": "script.json がありません"}
    if not os.path.exists(os.path.join(d, "review.json")):
        return {"ok": False, "message": "先に「画像取得」をしてください（review.json が必要）"}
    py = shlex.quote(sys.executable)
    cmd = (f"{py} main_story.py --from-script {shlex.quote(os.path.join(d, 'script.json'))} "
           f"--images-from-dir --output-dir {shlex.quote(d)}")
    return _spawn(f"audio:{slug}", cmd)


def _load_image_config():
    """.env(Pexels/Pixabayキー)＋config をベストエフォートで読む。失敗時は空（Wikimediaのみ可）。"""
    try:
        import main_story
        main_story.load_dotenv()
        return main_story.load_config("config/config.story.yaml")
    except Exception as e:  # noqa: BLE001
        print(f"[review] config/.env 読込失敗（Wikimediaのみで続行）: {e}")
        return {}


def _apply_fallback(config, fallback):
    """レビュー画面のフォールバックON/OFFを config に反映（None=configの既定値のまま）。"""
    if fallback is None:
        return
    config.setdefault("models", {})["fallback_enabled"] = bool(fallback)


def do_fetch_cut(ch, ci, query, kind, lang=None):
    """1カットを取得して review.json を更新（upsert）。検索のみ・Geminiは使わない。

    lang='ja' で日本語クエリ解釈（手動の日本語取得ボタン用）。
    Returns: {ok, image?, attribution?, message?}
    """
    try:
        ch, ci = int(ch), int(ci)
    except (TypeError, ValueError):
        return {"ok": False, "message": "ch/ci が不正"}
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "検索語が空です"}
    from src import image_fetch  # yaml不要。Wikimediaはキー不要で動く
    config = _load_image_config()
    base = f"ch_{ch:02d}_{ci:02d}"
    try:
        fn, attr = image_fetch.fetch_one_cut(query, kind or "ambient", DIR, base, config, lang=lang)
    except Exception as e:  # noqa: BLE001 - 取得失敗はメッセージで返す
        return {"ok": False, "message": f"取得エラー: {e}"}
    if not fn:
        return {"ok": False, "message": "該当画像が見つかりませんでした（検索語を変えて再取得）"}
    review = load_review()
    cut = find_cut(review, f"{ch}_{ci}")
    if not cut:
        cut = {"ch": ch, "ci": ci, "approved": False}
        review.setdefault("cuts", []).append(cut)
    cut["image"] = fn
    cut["query"] = query
    cut["kind"] = kind
    cut["attribution"] = attr
    save_review(review)
    return {"ok": True, "image": fn, "attribution": attr}


def do_candidates(query, kind, source, lang=None, page=1):
    """検索語の候補画像を取得先別に返す（DLしない・サムネ表示用）。追加課金なし。

    source 未指定/不適合なら kind に許される先頭の取得先を使う。lang='ja' で日本語クエリ解釈。
    page は 1始まり（「もっと見る」用）。
    Returns: {ok, sources:[{id,label}], source, page, candidates:[{source,thumb,url,attribution}], message?}
    """
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "検索語が空です", "sources": [], "candidates": []}
    from src import image_fetch
    config = _load_image_config()
    kind = kind or "ambient"
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    sources = image_fetch.available_sources(kind, config)
    if not sources:
        return {"ok": False, "message": "利用できる取得先がありません（APIキー未設定）",
                "sources": [], "candidates": []}
    ids = [s["id"] for s in sources]
    src = source if source in ids else ids[0]
    try:
        cands = image_fetch.fetch_candidates(query, kind, src, config, lang=lang, page=page)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"候補取得エラー: {e}",
                "sources": sources, "source": src, "candidates": []}
    return {"ok": True, "sources": sources, "source": src, "page": page, "candidates": cands}


def _blank_review_cut(ch, ci, chapter, cut, image, attribution):
    """review.json の1カット分の辞書を作る（main_story.build_review と同形・章再生成用）。"""
    return {"ch": ch, "ci": ci, "title": chapter.get("title") or "",
            "query": (cut.get("image_query") or "").strip(),
            "kind": cut.get("image_kind", "ambient"),
            "image": image, "attribution": attribution, "approved": False,
            "fit": None, "crop": None, "filter": None, "hide": False, "pad": None, "bg": None}


def do_regenerate_chapters(indices, fallback=None):
    """選択した trivia 章だけ、既出ネタと重複しない台本＋画像で再生成する（Gemini 1回）。

    台本(script.json)を差し替え、再生成章の画像を取り直して review.json を更新する。
    章番号・章数は不変なので他章のレビュー（承認/調整）はそのまま保たれる。
    Returns: {ok, regenerated:[章番号], titles:[新タイトル], message?}
    """
    script = load_script()
    if not script:
        return {"ok": False, "message": "script.json がありません（先に台本生成）"}
    try:
        indices = sorted({int(i) for i in indices})
    except (TypeError, ValueError):
        return {"ok": False, "message": "章番号が不正です"}
    if not indices:
        return {"ok": False, "message": "再生成する章が選択されていません"}
    from src import image_fetch, story_script, topic_history
    config = _load_image_config()
    _apply_fallback(config, fallback)
    genre = topic_history.genre_of(config)
    avoid = topic_history.facts(genre)  # 過去動画の採用済み＋却下を避ける（永続・ジャンル別）
    # 置き換える前の対象 trivia 章の内容を控える（成功したら却下履歴に積む）。
    chs = script.get("chapters", [])
    old_facts = [{"title": chs[i].get("title", ""), "summary": chs[i].get("summary", "")}
                 for i in indices if 0 <= i < len(chs) and chs[i].get("section") == "trivia"]
    try:
        regen = story_script.regenerate_chapters(config, script, indices, also_avoid=avoid)
    except Exception as e:  # noqa: BLE001 - 生成失敗はメッセージで返す
        return {"ok": False, "message": f"台本の再生成に失敗: {e}"}
    story_script.splice_regenerated(script, regen)
    save_script(script)
    # 捨てたネタを永続履歴に「却下」で記録。次回以降の生成/再生成で除外される。
    topic_history.add(genre, old_facts, "rejected")

    # 再生成した章の画像を取り直し、review.json を更新（他章のレビューは保持）。
    review = load_review()
    cuts = review.get("cuts", [])
    regen_chs = set(regen["chapters"].keys())
    for c in [c for c in cuts if c.get("ch") in regen_chs]:
        remove_file(c.get("image"))  # 旧画像ファイルを掃除
    cuts = [c for c in cuts if c.get("ch") not in regen_chs]
    for ch in sorted(regen_chs):
        chapter = script["chapters"][ch]
        for ci, cut in enumerate(chapter.get("image_cuts", [])):
            query = (cut.get("image_query") or "").strip()
            base = f"ch_{ch:02d}_{ci:02d}"
            fn = attr = None
            if query:
                try:
                    fn, attr = image_fetch.fetch_one_cut(
                        query, cut.get("image_kind", "ambient"), DIR, base, config)
                except Exception:  # noqa: BLE001 - 取得失敗はプレースホルダ
                    fn = attr = None
            cuts.append(_blank_review_cut(ch, ci, chapter, cut, fn, attr))
    review["cuts"] = cuts
    save_review(review)
    titles = [script["chapters"][i].get("title", "") for i in sorted(regen_chs)]
    return {"ok": True, "regenerated": sorted(regen_chs), "titles": titles}


def do_regenerate_all(fallback=None):
    """現在のテーマで台本を丸ごと作り直す（intro＋全trivia＋outro）。画像も全カット取り直す。

    章単位の再生成と違い intro/outro も新しくなるため整合性が保たれる（冒頭フックや締めが
    新しいネタと噛み合う）。旧triviaは却下履歴に積み、新生成はそれらを避ける。
    Gemini＋画像API（いずれも無料枠）を使う。Returns: {ok, theme?, chapters?, message?}
    """
    from src import image_fetch, story_script, topic_history
    config = _load_image_config()
    _apply_fallback(config, fallback)
    genre = topic_history.genre_of(config)
    # 深掘りストーリーは主題単位で重複回避（過去に扱った主題と被らせない）。
    avoid = [{"title": t} for t in topic_history.used_themes(genre)]
    try:
        script_result = story_script.generate_story_script(config, also_avoid=avoid)
    except Exception as e:  # noqa: BLE001 - 生成失敗はメッセージで返す
        return {"ok": False, "message": f"台本生成に失敗: {e}"}
    save_script(script_result)

    # 旧画像を掃除し、新カットで全取得＋review.json を再構築。
    for c in load_review().get("cuts", []):
        remove_file(c.get("image"))
    cuts = []
    for ch, chapter in enumerate(script_result.get("chapters", [])):
        for ci, cut in enumerate(chapter.get("image_cuts", []) or [{}]):
            query = (cut.get("image_query") or "").strip()
            base = f"ch_{ch:02d}_{ci:02d}"
            fn = attr = None
            if query:
                try:
                    fn, attr = image_fetch.fetch_one_cut(
                        query, cut.get("image_kind", "ambient"), DIR, base, config)
                except Exception:  # noqa: BLE001 - 取得失敗はプレースホルダ
                    fn = attr = None
            cuts.append(_blank_review_cut(ch, ci, chapter, cut, fn, attr))
    save_review({"cuts": cuts})
    return {"ok": True, "theme": script_result.get("theme"),
            "chapters": len(script_result.get("chapters", []))}


def pipeline_status():
    """各工程の成果物の有無からステージ完了状況を推定。"""
    def ex(name):
        return os.path.exists(os.path.join(DIR, name))
    return {"script": ex("script.json"), "review": ex("review.json"),
            "audio": ex("digest.mp3"), "meta": ex("meta.json")}


# ---- 純ロジック（テスト可能） ----

def apply_save_script(data):
    """台本編集の保存内容を検証して dict を返す（純ロジック・I/Oは呼び出し側）。

    script は [{speaker,text,...}] の非空リスト必須。chapters/theme はあればそのまま。
    Returns: (ok, message, normalized_or_None)
    """
    if not isinstance(data, dict):
        return False, "形式が不正", None
    script = data.get("script")
    if not isinstance(script, list) or not script:
        return False, "script が空", None
    for i, t in enumerate(script):
        if not isinstance(t, dict) or "speaker" not in t or "text" not in t:
            return False, f"script[{i}] に speaker/text が無い", None
    out = {"script": script}
    if "theme" in data:
        out["theme"] = data["theme"]
    if "chapters" in data:
        out["chapters"] = data["chapters"]
    # 編集モデルのトップレベルフィールドは保存時に保持（Phase 1 では UI 未送出だが前方互換）。
    # idCounters は ID 再利用防止の発番カウンタ（削除後も再利用しないため必ず持ち越す）。
    for k in ("schemaVersion", "assets", "imageCues", "visualSegments", "idCounters"):
        if k in data:
            out[k] = data[k]
    # 移行の「正」フラグ。editor のとき編集モデルを正とし再導出で上書きしない（Phase 2以降）。
    # 不正値で legacy 扱いに化けると人手編集を壊すため、既知の値のみ通す。
    if data.get("editorModelAuthority") in ("legacy", "editor"):
        out["editorModelAuthority"] = data["editorModelAuthority"]
    return True, "ok", out


def _norm_text_for_audio(t):
    """音声影響判定用のテキスト正規化（meta側は normalize_turns 済みなので揃える）。"""
    from src import story_script
    return story_script.strip_redundant_kana_gloss(story_script.strip_markdown(t or ""))


def audio_affecting_changed(old_script, new_script):
    """保存済みmetaのscriptと新scriptで、音声(VOICEVOX)に影響する差分があるか（純関数）。
    比較対象：ターン数/順序・speaker・text・voice・pause・chapter・chorus。
    演出/vizPoints/textEffects/画像設定/色/配置だけの差なら False（meta-only更新で足りる）。"""
    if len(old_script) != len(new_script):
        return True
    for o, n in zip(old_script, new_script):
        if _norm_text_for_audio(o.get("text")) != _norm_text_for_audio(n.get("text")):
            return True
        for k in ("speaker", "voice", "pause", "chapter", "chorus"):
            if o.get(k) != n.get(k):
                return True
    return False


def compute_preview_state(dir_path=None):
    """初期プレビュー状態を返す。audio-stale / visual-stale / synced。
    音声差分は script配列の内容比較で判定。演出(chapters.vizList)・review.json・画像差し替えは
    script配列に現れないため、script.json/review.json/対象画像のmtimeが meta.json より新しければ
    visual-stale とみなす（mtime比較で堅実に）。"""
    d = dir_path or DIR
    meta_path = os.path.join(d, "meta.json")
    script_path = os.path.join(d, "script.json")
    if not (os.path.exists(meta_path) and os.path.exists(script_path)):
        return "synced"
    try:
        with open(meta_path, encoding="utf-8") as f:
            old = json.load(f).get("script", [])
        with open(script_path, encoding="utf-8") as f:
            new = json.load(f).get("script", [])
    except Exception:
        return "synced"
    if audio_affecting_changed(old, new):
        return "audio-stale"
    meta_m = os.path.getmtime(meta_path)
    targets = [script_path, os.path.join(d, "review.json")]
    try:
        for fn in os.listdir(d):
            if fn.startswith("ch_") and not fn.endswith(".json"):
                targets.append(os.path.join(d, fn))   # 章画像（差し替え検出）
    except OSError:
        pass
    for p in targets:
        if os.path.exists(p) and os.path.getmtime(p) > meta_m:
            return "visual-stale"
    return "synced"


def _restore_meta(meta_path, backup_text):
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(backup_text)


def do_preview_refresh(body):
    """script.jsonを保存し、音声に影響しなければ meta-only で meta.json を再生成する。
    Returns: {ok, metaUpdated?, message}。meta生成失敗時は既存meta.jsonを維持する。"""
    meta_path = os.path.join(DIR, "meta.json")
    script_path = os.path.join(DIR, "script.json")
    if not os.path.exists(meta_path):
        return {"ok": False, "message": "meta.json がありません（先に音声＋meta生成が必要です）"}
    with open(meta_path, encoding="utf-8") as f:
        backup = f.read()                       # 失敗時に書き戻す生バックアップ
    old_meta = json.loads(backup)
    ok, msg, norm = apply_save_script(body)
    if not ok:
        return {"ok": False, "message": msg}
    save_script(norm)                           # script.json は常に保存
    if audio_affecting_changed(old_meta.get("script", []), norm["script"]):
        return {"ok": True, "metaUpdated": False,
                "message": "台本が変更されています。プレビュー更新には音声の再生成が必要です"}
    # meta-only 再生成（shellを使わず sys.executable＋引数配列で実行）。
    try:
        proc = subprocess.run(
            [sys.executable, "main_story.py", "--from-script", script_path, "--meta-only", "--output-dir", DIR],
            cwd=ROOT_DIR, capture_output=True, text=True, timeout=120)
    except Exception as e:
        _restore_meta(meta_path, backup)
        return {"ok": False, "metaUpdated": False, "message": "meta再生成に失敗: " + str(e)}
    if proc.returncode != 0:
        _restore_meta(meta_path, backup)
        return {"ok": False, "metaUpdated": False,
                "message": "meta再生成に失敗: " + ((proc.stderr or proc.stdout or "").strip()[-300:])}
    try:
        with open(meta_path, encoding="utf-8") as f:
            json.load(f)                        # 生成後のJSON妥当性を確認
    except Exception as e:
        _restore_meta(meta_path, backup)
        return {"ok": False, "metaUpdated": False, "message": "生成された meta.json が不正です: " + str(e)}
    return {"ok": True, "metaUpdated": True, "message": "プレビューを更新しました"}


def _atomic_write_json(path, obj):
    """同ディレクトリの一時ファイルへ書き→fsync→os.replace で原子的に置換する。

    途中で失敗しても元ファイルは壊れない（os.replace は同一FS上でアトミック）。一時ファイルは
    失敗時に掃除する。ディレクトリエントリも fsync して replace を確実に永続化する。
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)              # アトミック置換（成功するとtmpは消える）
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass                           # ディレクトリfsync非対応FSでも本体は置換済み
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)                 # 失敗時は一時ファイルを残さない（元ファイルは無傷）
        raise


def do_switch_to_editor(backups_root=".backups"):
    """編集モデルを「正」へ明示切替（自動では呼ばない）。変換・整合成功時のみ editor を立てて保存。

    旧形式(image_cuts/cut/review.json)から assets/imageCues を確定し、整合検証に通ったら
    editorModelAuthority="editor" にして script.json へ保存する。失敗時は legacy のまま据え置く。
    保存前に backups_root（既定 .backups）へ退避し、本体書き込みは一時ファイル＋fsync＋os.replace
    で原子的に行う。backups_root はテストで一時ディレクトリへ差し替えられる（本番の .backups を汚さない）。
    Returns: {ok, message, switched?}
    """
    from src import editor_model
    sp = os.path.join(DIR, "script.json")
    if not os.path.exists(sp):
        return {"ok": False, "message": "script.json がありません"}
    with open(sp, encoding="utf-8") as f:
        script_data = json.load(f)
    if script_data.get("editorModelAuthority") == "editor":
        return {"ok": True, "switched": False, "message": "すでに editor 権威です"}
    try:
        switched = editor_model.switch_to_editor(script_data, load_review())
    except Exception as e:
        return {"ok": False, "message": "切替に失敗（legacyのまま）: " + str(e)}
    bdir = os.path.join(backups_root, "editor-authority-pre-"
                        + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(sp, os.path.join(bdir, "script.json"))
    try:
        _atomic_write_json(sp, switched)   # 原子的保存（途中失敗でも元のlegacyが残る）
    except Exception as e:
        return {"ok": False, "message": "保存に失敗（元のlegacyを維持）: " + str(e)}
    return {"ok": True, "switched": True,
            "message": f"editor 権威へ切替（assets {len(switched.get('assets', []))} / "
                       f"imageCues {len(switched.get('imageCues', []))}）"}


def _save_editor_data(data):
    """編集モデルの全体dictを検証して script.json へ原子的に保存する。Returns: (ok, msg, norm)。"""
    ok, msg, norm = apply_save_script(data)
    if not ok:
        return False, msg, None
    _atomic_write_json(os.path.join(DIR, "script.json"), norm)
    return True, "ok", norm


def do_asset_add(body):
    """素材ライブラリへ asset を1件追加（editor権威のときのみ）。

    クライアントから現在の編集モデル全体(data)を受け取り、画像(dataB64 か url)があれば DIR へ
    保存して file をひも付け、editor_model.add_asset で追加してから全体を原子的に保存する。
    画像が無い場合は query 必須（後から差し替える素材枠）。Returns: {ok, data, assetId} など。
    """
    from src import editor_model
    data = body.get("data") or {}
    if data.get("editorModelAuthority") != "editor":
        return {"ok": False, "message": "editor権威ではありません（先に編集モデルへ移行）"}
    raw, ext = None, None
    if body.get("dataB64"):
        try:
            raw = base64.b64decode(body["dataB64"])
        except Exception:
            return {"ok": False, "message": "invalid base64"}
        if not raw:
            return {"ok": False, "message": "空のデータ"}
        ext = safe_ext(body.get("filename") or "")
    elif body.get("url"):
        if not valid_http_url(body["url"]):
            return {"ok": False, "message": "http(s)のURLのみ取り込めます"}
        try:
            ok, ext_or_msg, raw = download_image(body["url"])
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"取得失敗: {e}"}
        if not ok:
            return {"ok": False, "message": ext_or_msg}
        ext = ext_or_msg
    if raw is None and not (body.get("query") or "").strip():
        return {"ok": False, "message": "画像（ファイル/URL）かキーワードが必要です"}
    asset = editor_model.add_asset(
        data, file=None, query=body.get("query"), queryJa=body.get("queryJa"),
        kind=body.get("kind") or "ambient",
        attribution=(body.get("attribution") or body.get("url") or None))
    if raw is not None:
        fname = f"{asset['id']}{ext or '.jpg'}"   # 素材ファイル名は asset ID 基準で一意
        write_image_bytes(fname, raw)
        asset["file"] = fname
    ok, msg, norm = _save_editor_data(data)
    if not ok:
        return {"ok": False, "message": msg}
    return {"ok": True, "data": norm, "assetId": asset["id"]}


def do_asset_delete(body):
    """素材を削除（editor権威のときのみ）。使用中は force 指定がなければ拒否（使用cueを返す）。

    画像ファイル本体は消さない（移行元 ch_NN_MM は legacy UI と共有しているため）。
    Returns: {ok, data?, used?, removedCues?}。
    """
    from src import editor_model
    data = body.get("data") or {}
    if data.get("editorModelAuthority") != "editor":
        return {"ok": False, "message": "editor権威ではありません"}
    aid = body.get("assetId")
    try:
        removed = editor_model.delete_asset(data, aid, force=bool(body.get("force")))
    except ValueError as e:
        return {"ok": False, "used": editor_model.asset_usage(data, aid), "message": str(e)}
    ok, msg, norm = _save_editor_data(data)
    if not ok:
        return {"ok": False, "message": msg}
    return {"ok": True, "data": norm, "removedCues": removed}


def do_cue_op(body):
    """imageCue の編集操作を editor_model の共通opへ集約して適用・保存する（editor権威のみ）。

    クライアントは現在の編集モデル全体(data)と op/引数を送る。検証はすべて editor_model 側
    （不正assetId・開始位置衝突・範囲逆転を拒否）。成功時のみ原子的に保存し正規化結果を返す。
    op: place|add|replace|move|range|setOpts|delete。Returns: {ok, data?} か {ok:False,message}。
    """
    from src import editor_model as em
    data = body.get("data") or {}
    if data.get("editorModelAuthority") != "editor":
        return {"ok": False, "message": "editor権威ではありません"}
    op = body.get("op")
    try:
        if op == "place":
            em.place_image(data, body["turnId"], body.get("assetId"), **(body.get("opts") or {}))
        elif op == "add":
            em.add_cue(data, body["turnId"], body.get("assetId"),
                       end_turn_id=body.get("endTurnId"), **(body.get("opts") or {}))
        elif op == "replace":
            em.replace_cue_asset(data, body["cueId"], body.get("assetId"))
        elif op == "move":
            em.move_cue(data, body["cueId"], body["turnId"])
        elif op == "range":
            kw = {}
            if "startTurnId" in body:
                kw["start_turn_id"] = body["startTurnId"]
            if body.get("clearEnd"):
                kw["end_turn_id"] = None
            elif "endTurnId" in body:
                kw["end_turn_id"] = body["endTurnId"]
            em.set_cue_range(data, body["cueId"], **kw)
        elif op == "setOpts":
            em.set_cue_opts(data, body["cueId"], **(body.get("opts") or {}))
        elif op == "delete":
            em.delete_cue(data, body["cueId"])
        else:
            return {"ok": False, "message": f"unknown op: {op}"}
    except (ValueError, KeyError) as e:
        return {"ok": False, "message": str(e)}
    ok, msg, norm = _save_editor_data(data)
    if not ok:
        return {"ok": False, "message": msg}
    return {"ok": True, "data": norm}


def cut_key(cut):
    return f"{cut['ch']}_{cut['ci']}"


def find_cut(review, key):
    for c in review.get("cuts", []):
        if cut_key(c) == key:
            return c
    return None


def safe_ext(filename, default=".png"):
    """アップロードファイル名から許可拡張子のみ採用（パス事故防止）。"""
    ext = os.path.splitext(filename or "")[1].lower()
    return ext if ext in _CT else default


# Content-Type → 拡張子（URL取り込み時の保存名決め）。
_CT_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
           "image/gif": ".gif", "image/webp": ".webp"}
_MAX_IMG = 15 * 1024 * 1024  # 取り込み上限15MB


def valid_http_url(url):
    """http/httpsのみ許可（file://やjs:等を弾く）。純関数。"""
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url.strip())
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def download_image(url, timeout=20):
    """画像URLをダウンロード。Returns: (ok, ext_or_msg, data)。ネットワークI/O。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (zundamon-video review tool)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = r.read(_MAX_IMG + 1)
    if len(data) > _MAX_IMG:
        return False, "画像が大きすぎます(15MB超)", None
    ext = _CT_EXT.get(ctype)
    if not ext:  # Content-Typeが当てにならない時はURL拡張子で補完
        ext = safe_ext(urlparse(url).path, default="")
    if ext not in _CT_EXT.values():
        return False, f"画像ではない可能性(Content-Type={ctype or '不明'})", None
    return True, ext, data


def _reset_adjust(cut):
    """画像差し替え時、前画像用のクロップ/補正/フィット/余白/非表示をクリアする（純関数・破壊的）。"""
    for k in ("crop", "filter", "fit", "pad", "bg"):
        cut[k] = None
    cut["hide"] = False


def apply_import_url(review, key, url, attribution):
    """WebからD&DされたURLを取り込み、ch_NN_MM.<ext>で保存して review を更新。

    帰属は指定が無ければ出典URLを入れる（商用可かは人が要確認）。
    Returns: (ok, message, saved_filename)。ネットワークI/Oを伴う。
    """
    cut = find_cut(review, key)
    if not cut:  # 未取得カット（review.json未登録）でも key=ch_ci から作って取り込めるように
        try:
            ch, ci = (int(x) for x in str(key).split("_"))
        except (ValueError, TypeError):
            return False, "unknown key", None
        cut = {"ch": ch, "ci": ci, "approved": False}
        review.setdefault("cuts", []).append(cut)
    if not valid_http_url(url):
        return False, "http(s)のURLのみ取り込めます", None
    try:
        ok, ext_or_msg, data = download_image(url)
    except Exception as e:  # noqa: BLE001 - 取得失敗は呼び出し側にメッセージ返す
        return False, f"取得失敗: {e}", None
    if not ok:
        return False, ext_or_msg, None
    filename = f"ch_{cut['ch']:02d}_{cut['ci']:02d}{ext_or_msg}"
    write_image_bytes(filename, data)
    cut["image"] = filename
    cut["attribution"] = (attribution or "").strip() or url  # 既定は出典URL（要ライセンス確認）
    cut["approved"] = True
    _reset_adjust(cut)       # 旧画像用のクロップ/補正を持ち越さない
    return True, "ok", filename


def apply_replace(review, key, upload_name, data_b64, attribution):
    """差し替え：base64画像を ch_NN_MM.<ext> で保存し review を更新。
    Returns: (ok, message, saved_filename)。I/Oは write_image_bytes 経由。
    """
    cut = find_cut(review, key)
    if not cut:  # 未取得カット（review.json未登録）でも key=ch_ci から作って差し替えられるように
        try:
            ch, ci = (int(x) for x in str(key).split("_"))
        except (ValueError, TypeError):
            return False, "unknown key", None
        cut = {"ch": ch, "ci": ci, "approved": False}
        review.setdefault("cuts", []).append(cut)
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        return False, "invalid base64", None
    if not data:
        return False, "empty data", None
    ext = safe_ext(upload_name)
    filename = f"ch_{cut['ch']:02d}_{cut['ci']:02d}{ext}"
    write_image_bytes(filename, data)
    cut["image"] = filename
    cut["attribution"] = (attribution or "").strip() or None
    cut["approved"] = True  # 差し替え＝人が選んだ＝承認扱い
    _reset_adjust(cut)       # 旧画像用のクロップ/補正を持ち越さない
    return True, "ok", filename


def apply_attribution(review, key, attribution):
    cut = find_cut(review, key)
    if not cut:
        return False
    cut["attribution"] = (attribution or "").strip() or None
    return True


def apply_approve(review, key, approved=True):
    cut = find_cut(review, key)
    if not cut:
        return False
    cut["approved"] = bool(approved)
    return True


def _clean_crop(v):
    """crop パッチを検証して {l,t,r,b}(0..1, l<r,t<b) に整える。不正/Noneは None。"""
    if not isinstance(v, dict):
        return None
    try:
        l, t, r, b = (float(v["l"]), float(v["t"]), float(v["r"]), float(v["b"]))
    except (KeyError, TypeError, ValueError):
        return None
    l, t = max(0.0, min(1.0, l)), max(0.0, min(1.0, t))
    r, b = max(0.0, min(1.0, r)), max(0.0, min(1.0, b))
    if r - l < 0.02 or b - t < 0.02:  # 極小は無効（誤クリック）
        return None
    return {"l": round(l, 4), "t": round(t, 4), "r": round(r, 4), "b": round(b, 4)}


def _clean_filter(v):
    """filter パッチを brightness/contrast/grayscale の数値のみに整える。既定等倍だけなら None。"""
    if not isinstance(v, dict):
        return None
    out = {}
    for k, default in (("brightness", 1.0), ("contrast", 1.0), ("grayscale", 0.0)):
        if k in v and v[k] is not None:
            try:
                fv = round(float(v[k]), 3)
            except (TypeError, ValueError):
                continue
            if abs(fv - default) > 1e-6:  # 既定値は持たない（=なし扱い）
                out[k] = fv
    return out or None


def apply_options(review, key, patch):
    """描画オプション(fit/crop/filter/hide)を1カットへ適用（検証込み・純ロジック）。

    patch に含まれるキーだけ更新。fit は cover/contain/None、crop/filter は専用バリデータ、hide は bool。
    Returns: (ok, applied_dict)
    """
    cut = find_cut(review, key)
    if not cut:
        return False, {}
    applied = {}
    if "fit" in patch:
        v = patch["fit"]
        cut["fit"] = v if v in ("cover", "contain") else None
        applied["fit"] = cut["fit"]
    if "crop" in patch:
        cut["crop"] = _clean_crop(patch["crop"])
        applied["crop"] = cut["crop"]
    if "filter" in patch:
        cut["filter"] = _clean_filter(patch["filter"])
        applied["filter"] = cut["filter"]
    if "hide" in patch:
        cut["hide"] = bool(patch["hide"])
        applied["hide"] = cut["hide"]
    if "pad" in patch:
        cut["pad"] = _clean_pad(patch["pad"])
        applied["pad"] = cut["pad"]
    if "bg" in patch:
        cut["bg"] = _clean_color(patch["bg"])
        applied["bg"] = cut["bg"]
    return True, applied


def _clean_pad(v):
    """contain余白px。0..400にクランプ、0/不正は None。"""
    try:
        n = round(float(v))
    except (TypeError, ValueError):
        return None
    n = max(0, min(400, n))
    return n or None


def _clean_color(v):
    """余白背景色。CSS color文字列を素朴に検証（#hex / rgb()/rgba() / 英数の色名）。不正/空は None。"""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > 32:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", s):
        return s
    if re.fullmatch(r"(rgb|rgba|hsl|hsla)\([0-9.,%\s/]+\)", s):
        return s
    if re.fullmatch(r"[a-zA-Z]+", s):  # 色名(white等)
        return s
    return None


def review_summary(review):
    cuts = review.get("cuts", [])
    return {
        "total": len(cuts),
        "approved": sum(1 for c in cuts if c.get("approved")),
        "status": review.get("status", "reviewing"),
    }


# ---- HTTP ----



# 共通スタイル（各ページで使い回す）
_BASE_CSS = """
  :root { --bg:#11151c; --card:#1b212c; --line:#2c3543; --fg:#d8dde6; --sub:#8693a5;
          --ok:#3fa34d; --accent:#4a86ff; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:'Hiragino Sans','Yu Gothic',system-ui,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:14px;
           padding:14px 22px; background:#0d1117ee; backdrop-filter:blur(8px);
           border-bottom:1px solid var(--line); }
  header h1 { font-size:18px; margin:0; font-weight:700; color:#fff; }
  header a { color:var(--sub); text-decoration:none; font-size:14px; }
  header a:hover { color:var(--fg); }
  .spacer { flex:1; }
  button { font:inherit; border:none; border-radius:8px; padding:8px 16px; cursor:pointer;
           font-weight:700; color:#fff; background:var(--line); }
  button.primary { background:var(--accent); }
  button.ok { background:var(--ok); }
  main { padding:22px; max-width:1000px; margin:0 auto; }
  code { background:#0c0f15; padding:2px 7px; border-radius:5px; color:#bfe3c4; font-size:13px; }
"""

LANDING_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>制作パネル</title>
<style>__CSS__
  :root { --accent:#8b5cf6; }
  body { min-height:100vh; background:
    radial-gradient(circle at 75% -20%,rgba(139,92,246,.16),transparent 38%),var(--bg); }
  header { height:58px; padding:0 28px; }
  .brand { display:flex; align-items:center; gap:10px; color:#fff; font-weight:800; letter-spacing:.01em; }
  .brand-mark { display:grid; place-items:center; width:28px; height:28px; border-radius:8px;
                background:linear-gradient(145deg,#9b6dff,#6d3de5); font-size:13px; }
  .target { padding:5px 10px; border:1px solid var(--line); border-radius:999px; color:var(--sub); font-size:11px; }
  main.dashboard { max-width:1080px; padding:34px 24px 48px; }
  .hero { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:center; padding:30px;
          border:1px solid #302a42; border-radius:18px; background:linear-gradient(135deg,#1c2130,#181722); }
  .eyebrow { margin-bottom:8px; color:#a78bfa; font-size:11px; font-weight:800; letter-spacing:.12em; }
  .hero h2 { margin:0; color:#fff; font-size:clamp(22px,3vw,34px); line-height:1.25; }
  .hero-status { margin:10px 0 0; color:var(--sub); font-size:13px; }
  .primary-action { display:inline-flex; align-items:center; justify-content:center; min-width:156px; padding:12px 18px;
                    border-radius:10px; background:#7c4dff; color:#fff; text-decoration:none; font-weight:800; }
  .primary-action:hover { background:#8d63ff; }
  .short-notice { display:none; align-items:center; gap:12px; margin-top:12px; padding:12px 16px;
                  border:1px solid #3e3557; border-radius:12px; background:#1c1928; font-size:12px; }
  .short-notice.show { display:flex; }
  .short-notice span { flex:1; color:#c8bddf; }
  .progress { display:grid; grid-template-columns:repeat(4,1fr); margin:26px 0; padding:18px 20px;
              border:1px solid var(--line); border-radius:14px; background:rgba(27,33,44,.78); }
  .progress-step { position:relative; display:flex; align-items:center; gap:9px; color:#677386; font-size:12px; font-weight:700; }
  .progress-step:not(:last-child)::after { content:""; position:absolute; left:30px; right:10px; top:8px;
                                           height:1px; background:#343d4b; }
  .progress-dot { position:relative; z-index:1; width:17px; height:17px; border:4px solid #252d39;
                  border-radius:50%; background:#596577; }
  .progress-step.done { color:#cfd6e1; }
  .progress-step.done .progress-dot { border-color:#24472d; background:#52bd67; }
  .progress-step.done:not(:last-child)::after { background:#365b41; }
  .section-title { margin:0 0 12px; color:#e9edf5; font-size:14px; }
  .actions { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  .action-card { display:block; min-height:118px; padding:18px; border:1px solid var(--line); border-radius:14px;
                 background:var(--card); color:inherit; text-decoration:none; transition:.15s ease; }
  .action-card:hover { transform:translateY(-2px); border-color:#584783; background:#202635; }
  .action-card b { display:block; margin-bottom:7px; color:#f1f3f7; font-size:14px; }
  .action-card span { color:var(--sub); font-size:12px; line-height:1.55; }
  details { margin-top:22px; border-top:1px solid var(--line); color:var(--sub); }
  summary { padding:16px 2px; cursor:pointer; font-size:12px; font-weight:700; }
  .commands { display:grid; gap:8px; padding-bottom:14px; }
  .command { display:grid; grid-template-columns:110px 1fr; gap:12px; align-items:center; font-size:11px; }
  .command code { overflow:auto; white-space:nowrap; }
  @media (max-width:760px){
    header { padding:0 16px; } main.dashboard { padding:20px 14px 36px; }
    .hero { grid-template-columns:1fr; padding:22px; } .primary-action { width:100%; }
    .progress { grid-template-columns:repeat(2,1fr); gap:16px; }
    .progress-step::after { display:none; } .actions { grid-template-columns:repeat(2,1fr); }
  }
</style></head>
<body>
<header><a class="brand" href="/"><span class="brand-mark">▶</span>Zundamon Video</a><span class="spacer"></span><span class="target" id="dir">読み込み中</span></header>
<main class="dashboard">
  <section class="hero">
    <div><div class="eyebrow">CURRENT PROJECT</div><h2 id="projectTitle">制作データを確認中…</h2><p class="hero-status" id="projectStatus"></p></div>
    <a class="primary-action" id="primaryAction" href="/story">制作を続ける</a>
  </section>
  <div class="short-notice" id="shortNotice"><span id="shortText"></span><button id="backToMain">本編に戻す</button></div>
  <section class="progress" aria-label="制作進捗">
    <div class="progress-step" data-key="script"><span class="progress-dot"></span>台本</div>
    <div class="progress-step" data-key="review"><span class="progress-dot"></span>画像</div>
    <div class="progress-step" data-key="audio"><span class="progress-dot"></span>音声</div>
    <div class="progress-step" data-key="meta"><span class="progress-dot"></span>動画準備</div>
  </section>
  <h3 class="section-title">制作ツール</h3>
  <section class="actions">
    <a class="action-card" href="/story"><b>ストーリー編集</b><span>台本、画像、演出をまとめて編集</span></a>
    <a class="action-card" href="/read"><b>ファクトチェック</b><span>台本と概要を読み取り専用で確認</span></a>
    <a class="action-card" href="/compose"><b>台本を取り込む</b><span>ブラウザAI用プロンプトとJSON取込</span></a>
    <a class="action-card" href="/shorts"><b>ショート制作</b><span>本編から縦型ショートを作成</span></a>
  </section>
  <details><summary>技術情報とコマンド</summary><div class="commands" id="commands"></div></details>
</main>
<script>
const commandRows=[
  ['台本・画像生成','python main_story.py --stop-after-images'],
  ['音声・meta生成','python main_story.py --from-script DIR/script.json --images-from-dir'],
  ['動画書き出し','cd video && SRC_DIR=../DIR npm run render']
];
Promise.all([fetch('/api/status').then(r=>r.json()),fetch('/api/script').then(r=>r.json())]).then(([st,script])=>{
  document.getElementById('dir').textContent=st.dir;
  document.getElementById('projectTitle').textContent=script.theme||st.dir.split('/').pop()||'名称未設定';
  const status=st.status||{};
  const next=!status.script?'台本の準備が必要です':!status.review?'画像の準備が必要です':
    !status.audio?'レビュー後、音声を生成できます':!status.meta?'音声生成済み・動画準備待ち':'動画を書き出せます';
  document.getElementById('projectStatus').textContent=next;
  const primary=document.getElementById('primaryAction');
  if(!status.script){ primary.textContent='台本を取り込む'; primary.href='/compose'; }
  document.querySelectorAll('.progress-step').forEach(el=>el.classList.toggle('done',!!status[el.dataset.key]));
  if(st.dir!==st.base){
    document.getElementById('shortNotice').classList.add('show');
    document.getElementById('shortText').textContent='ショートを編集中: '+st.dir;
  }
  document.getElementById('backToMain').onclick=async()=>{ await fetch('/api/set-dir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dir:st.base})}); location.reload(); };
  const commands=document.getElementById('commands');
  commandRows.forEach(([label,cmd])=>{ const row=document.createElement('div'); row.className='command';
    const name=document.createElement('span'); name.textContent=label; const code=document.createElement('code'); code.textContent=cmd.replaceAll('DIR',st.dir);
    row.appendChild(name); row.appendChild(code); commands.appendChild(row); });
});
</script>
</body></html>
"""

SHORTS_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ショート制作</title>
<style>__CSS__
  .card2 { background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:14px 16px; margin-bottom:12px; }
  .row2 { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .ttl { font-weight:700; } .meta2 { color:var(--sub); font-size:12px; }
  select,input.txt { background:#0e131b; color:var(--fg); border:1px solid var(--line);
           border-radius:8px; padding:8px 10px; font-size:14px; }
  .badge { font-size:11px; border:1px solid var(--line); border-radius:6px; padding:2px 7px; color:var(--sub); }
  .badge.on { color:var(--ok); border-color:var(--ok); }
  .st { font-size:12px; margin-left:auto; color:var(--sub); }
  .st.run{color:var(--accent);} .st.ok{color:var(--ok);} .st.ng{color:#ff6b6b;}
  pre.log { background:#0b0f15; border:1px solid var(--line); border-radius:8px; color:#aeb9c7;
            font-size:11px; padding:8px 10px; margin:8px 0 0; max-height:150px; overflow:auto;
            white-space:pre-wrap; display:none; }
  h2 { font-size:15px; margin:18px 0 8px; } code.cmd2 { color:var(--sub); font-size:12px; }
</style></head>
<body>
<header><a href="/">← パネル</a><h1>ショート制作（縦9:16）</h1><span class="spacer"></span>
  <span class="meta2" id="dir"></span></header>
<main id="main">読み込み中…</main>
<script>
function api(p,b){return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json());}
// ログをレベル別に色分け（ERROR=赤 / WARNING=黄 / INFO=青 / その他=既定）。HTMLエスケープ込み。
function colorizeLog(s){ return (s||'').split('\\n').map(l=>{
  const e=l.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  let col=''; if(/\\[(ERROR|CRITICAL)\\]|Traceback|Error:/.test(l))col='#ff6b6b';
  else if(/\\[WARN(ING)?\\]/.test(l))col='#ffcc4d'; else if(/\\[INFO\\]/.test(l))col='#8fb7ff';
  return col?('<span style="color:'+col+'">'+e+'</span>'):e; }).join('<br>'); }
let NETAS=[], SHORTS=[];
function fmt(s){ if(s.state==='running')return['実行中…','st run'];
  if(s.state==='done')return['✓ 完了'+(s.out?': '+s.out:''),'st ok'];
  if(s.state==='failed')return['失敗(code '+s.code+')','st ng']; return['','st']; }
function poll(job, stId, logId, btns){
  fetch('/api/shorts/jobstatus?job='+encodeURIComponent(job)).then(r=>r.json()).then(s=>{
    const [t,c]=fmt(s); const st=document.getElementById(stId); if(st){st.textContent=t; st.className=c;}
    const lg=document.getElementById(logId); if(lg&&s.log){lg.style.display='block'; lg.innerHTML=colorizeLog(s.log); lg.scrollTop=lg.scrollHeight;}
    if(s.state==='running') setTimeout(()=>poll(job,stId,logId,btns),1500);
    // ボタン起点のpoll(btnsあり)のみ完了時に1回だけ再読み込み（バッジ更新）。
    // load時の受動poll(btns=null)は再読み込みしない＝完了済みジョブで無限リロード(ちらつき)を防ぐ。
    else { (btns||[]).forEach(b=>b.disabled=false); if(btns && s.state==='done') setTimeout(load,400); }
  });
}
function render(){
  const m=document.getElementById('main'); m.innerHTML='';
  // 新規作成（複数ネタを選んで Gemini 1回でまとめて生成）
  const mk=document.createElement('div'); mk.className='card2';
  mk.innerHTML='<h2 style="margin-top:0">本編ネタからショートを作る（まとめて生成）</h2>'+
    '<div class="meta2">作るネタにチェック→「選択ネタをまとめて生成」。選んだ分を Gemini 1回で各「自己完結・掴み先頭・約40秒」台本に書き直し、docs/shorts/&lt;自動名&gt;/ へ（台本→画像取得まで）。slugはネタ名から自動。</div>';
  if(NETAS.length){ const selrow=document.createElement('div'); selrow.className='row2'; selrow.style.margin='6px 0';
    const ba=document.createElement('button'); ba.textContent='全選択'; ba.onclick=()=>document.querySelectorAll('.netachk').forEach(c=>c.checked=true);
    const bn=document.createElement('button'); bn.textContent='全解除'; bn.onclick=()=>document.querySelectorAll('.netachk').forEach(c=>c.checked=false);
    selrow.appendChild(ba); selrow.appendChild(bn); mk.appendChild(selrow); }
  const list=document.createElement('div'); list.style.margin='6px 0 10px';
  if(!NETAS.length){ list.innerHTML='<div class="meta2">本編の台本がありません</div>'; }
  NETAS.forEach(n=>{ const lb=document.createElement('label'); lb.style.cssText='display:block;margin:4px 0;cursor:pointer;';
    lb.innerHTML='<input type="checkbox" class="netachk" value="'+n.ch+'"> 第'+n.ch+'章 '+(n.title||'');
    list.appendChild(lb); });
  mk.appendChild(list);
  const row=document.createElement('div'); row.className='row2';
  const gen=document.createElement('button'); gen.className='primary'; gen.textContent='選択ネタをまとめて生成';
  const gst=document.createElement('span'); gst.className='st'; gst.id='gen-st';
  row.appendChild(gen); row.appendChild(gst); mk.appendChild(row);
  const glog=document.createElement('pre'); glog.className='log'; glog.id='gen-log'; mk.appendChild(glog);
  gen.onclick=async()=>{
    const chs=[...document.querySelectorAll('.netachk:checked')].map(c=>parseInt(c.value));
    if(!chs.length){ alert('ネタを1つ以上選んでください'); return; }
    gen.disabled=true; const r=await api('/api/shorts/generate',{chapters:chs});
    if(!r.ok){ alert(r.message||'起動失敗'); gen.disabled=false; return; }
    poll(r.job,'gen-st','gen-log',[gen]); };
  m.appendChild(mk);

  // ブラウザAIで作る（上のチェックを使う・Gemini枠を使わない）
  const ai=document.createElement('div'); ai.className='card2';
  ai.innerHTML='<h2 style="margin-top:0">ブラウザAIで作る（Gemini枠なし）</h2>'+
    '<div class="meta2">上でネタにチェック→「プロンプトをコピー」→ブラウザのAIで生成→結果JSONを貼り付け→「取り込む」。各ショートが docs/shorts/ に作られます（画像は各カードの「画像取得」で）。</div>';
  const arow=document.createElement('div'); arow.className='row2'; arow.style.marginTop='8px';
  const pbtn=document.createElement('button'); pbtn.textContent='選択ネタのプロンプトをコピー';
  const ast=document.createElement('span'); ast.className='st'; ast.id='ai-st';
  arow.appendChild(pbtn); arow.appendChild(ast); ai.appendChild(arow);
  const pta=document.createElement('textarea'); pta.id='ai-prompt'; pta.rows=6; pta.placeholder='（プロンプトをコピーするとここに表示）';
  pta.style.cssText='width:100%;box-sizing:border-box;background:#0e131b;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;margin-top:8px;';
  ai.appendChild(pta);
  const xta=document.createElement('textarea'); xta.id='ai-paste'; xta.rows=6; xta.placeholder='AIの結果(JSON)を貼り付け';
  xta.style.cssText=pta.style.cssText; ai.appendChild(xta);
  const xrow=document.createElement('div'); xrow.className='row2'; xrow.style.marginTop='8px';
  const ibtn=document.createElement('button'); ibtn.className='primary'; ibtn.textContent='取り込む';
  const ist=document.createElement('span'); ist.className='st'; ist.id='ai-ist'; xrow.appendChild(ibtn); xrow.appendChild(ist); ai.appendChild(xrow);
  const checkedChs=()=>[...document.querySelectorAll('.netachk:checked')].map(c=>parseInt(c.value));
  pbtn.onclick=async()=>{ const chs=checkedChs(); if(!chs.length){alert('ネタを選んでください');return;}
    const r=await api('/api/shorts/prompt',{chapters:chs});
    if(!r.ok){ document.getElementById('ai-st').textContent='× '+(r.message||'失敗'); document.getElementById('ai-st').className='st ng'; return; }
    pta.value=r.prompt; navigator.clipboard.writeText(r.prompt).then(()=>{ast.textContent='コピーした！';ast.className='st ok';}); };
  ibtn.onclick=async()=>{ const chs=checkedChs(); if(!chs.length){alert('プロンプトに使ったネタにチェックを入れてください');return;}
    ist.textContent='取り込み中…'; ist.className='st run';
    const r=await api('/api/shorts/import',{text:xta.value,chapters:chs});
    if(r.ok){ ist.textContent='✓ '+r.slugs.length+'本取り込み: '+r.slugs.join(', '); ist.className='st ok'; setTimeout(load,500); }
    else { ist.textContent='× '+(r.message||'失敗'); ist.className='st ng'; } };
  m.appendChild(ai);

  // 一覧
  const h=document.createElement('h2'); h.textContent='作成済みショート'; m.appendChild(h);
  if(!SHORTS.length){ const p=document.createElement('p'); p.className='meta2'; p.textContent='まだありません。上で作成してください。'; m.appendChild(p); }
  SHORTS.forEach(sh=>{
    const c=document.createElement('div'); c.className='card2';
    const head=document.createElement('div'); head.className='row2';
    head.innerHTML='<span class="ttl">'+sh.slug+'</span>'+
      '<span class="meta2">'+(sh.title||'')+'</span>'+
      '<span class="badge '+(sh.hasScript?'on':'')+'">台本</span>'+
      '<span class="badge '+(sh.hasMeta?'on':'')+'">音声/meta</span>'+
      '<span class="badge '+(sh.hasVideo?'on':'')+'">動画</span>';
    const st=document.createElement('span'); st.className='st'; st.id='st-'+sh.slug; head.appendChild(st);
    c.appendChild(head);
    if(sh.hook){ const hk=document.createElement('div'); hk.className='meta2'; hk.style.marginTop='6px'; hk.textContent='見出し: '+sh.hook; c.appendChild(hk); }
    const row=document.createElement('div'); row.className='row2'; row.style.marginTop='10px';
    const rev=document.createElement('button'); rev.textContent='台本レビュー'; rev.disabled=!sh.hasScript;
    rev.onclick=async()=>{ const r=await api('/api/set-dir',{dir:sh.dir}); if(r.ok) location.href='/story'; else alert(r.message); };
    const fch=document.createElement('button'); fch.textContent='画像取得'; fch.disabled=!sh.hasScript;
    fch.onclick=async()=>{ fch.disabled=true; const r=await api('/api/shorts/fetch',{slug:sh.slug});
      if(!r.ok){ alert(r.message||'起動失敗'); fch.disabled=false; return; } poll(r.job,'st-'+sh.slug,'log-'+sh.slug,[fch]); };
    const aud=document.createElement('button'); aud.textContent='音声+meta'; aud.disabled=!sh.hasScript;
    aud.title='VOICEVOXで音声と字幕タイミングを生成（画像取得後）';
    aud.onclick=async()=>{ aud.disabled=true; const r=await api('/api/shorts/audio',{slug:sh.slug});
      if(!r.ok){ alert(r.message||'起動失敗'); aud.disabled=false; return; } poll(r.job,'st-'+sh.slug,'log-'+sh.slug,[aud]); };
    const rnd=document.createElement('button'); rnd.className='primary'; rnd.textContent='書き出し';
    rnd.title=sh.hasMeta?'':'先に「音声+meta」を生成してください';
    rnd.onclick=async()=>{ rnd.disabled=true; const r=await api('/api/shorts/render',{slug:sh.slug});
      if(!r.ok){ alert(r.message||'起動失敗'); rnd.disabled=false; return; } poll(r.job,'st-'+sh.slug,'log-'+sh.slug,[rnd]); };
    row.appendChild(rev); row.appendChild(fch); row.appendChild(aud); row.appendChild(rnd);
    const cmd=document.createElement('div'); cmd.className='meta2'; cmd.style.marginTop='8px';
    cmd.innerHTML='深度（任意・パララックス）: <code class="cmd2">python make_depth.py --dir '+sh.dir+'</code>';
    c.appendChild(row); c.appendChild(cmd);
    const log=document.createElement('pre'); log.className='log'; log.id='log-'+sh.slug; c.appendChild(log);
    m.appendChild(c);
  });
}
function load(){
  Promise.all([fetch('/api/status').then(r=>r.json()), fetch('/api/shorts/list').then(r=>r.json())])
  .then(([st,d])=>{ document.getElementById('dir').textContent='対象: '+st.dir;
    NETAS=d.netas||[]; SHORTS=d.shorts||[]; render();
    SHORTS.forEach(sh=>poll('render:'+sh.slug,'st-'+sh.slug,'log-'+sh.slug,null)); });
}
load();
</script>
</body></html>
"""

COMPOSE_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>本編台本をブラウザAIで作る</title>
<style>__CSS__
  .card2 { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:14px; }
  .meta2 { color:var(--sub); font-size:12px; }
  textarea.big { width:100%; box-sizing:border-box; background:#0e131b; color:var(--fg);
    border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:13px; }
  .st { font-size:12px; margin-left:auto; } .st.ok{color:var(--ok);} .st.ng{color:#ff6b6b;} .st.run{color:var(--accent);}
  pre.log { background:#0b0f15;border:1px solid var(--line);border-radius:8px;color:#aeb9c7;font-size:11px;
    padding:8px 10px;margin:8px 0 0;max-height:140px;overflow:auto;white-space:pre-wrap;display:none; }
  h2{font-size:15px;margin:0 0 8px;}
</style></head>
<body>
<header><a href="/">← パネル</a><h1>本編台本をブラウザAIで作る</h1><span class="spacer"></span></header>
<main id="main">
  <div class="card2">
    <h2>① プロンプト（コピーしてブラウザのAIへ）</h2>
    <div class="meta2">Geminに投げるのと同じ指示。編集してからコピーしてもOK。<span id="theme"></span></div>
    <textarea class="big" id="prompt" rows="14" style="margin-top:8px">読み込み中…</textarea>
    <div class="row" style="margin-top:8px"><button class="primary" id="copy">プロンプトをコピー</button>
      <button id="reload">テーマ選び直し</button></div>
  </div>
  <div class="card2">
    <h2>② AIの結果(JSON)を貼り付けて取り込む</h2>
    <div class="meta2">AIが出力した台本JSONを貼付→「取り込む」で docs/story/script.json に保存。</div>
    <textarea class="big" id="paste" rows="10" placeholder='{ "theme": "...", "chapters": [...], "script": [...] }' style="margin-top:8px"></textarea>
    <div class="row" style="margin-top:8px"><button class="primary" id="imp">取り込む</button>
      <span class="st" id="ist"></span></div>
  </div>
  <div class="card2">
    <h2>③ 画像取得(任意・取り込み後)</h2>
    <div class="meta2">取り込んだ台本で画像取得。完了後 /story でレビュー。</div>
    <div class="row" style="margin-top:8px"><button id="fetch">画像取得</button><span class="st" id="fst"></span>
      <a href="/story" style="margin-left:10px"><button>レビューへ(/story)</button></a></div>
    <pre class="log" id="flog"></pre>
  </div>
</main>
<script>
function api(p,b){return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json());}
function colorizeLog(s){ return (s||'').split('\\n').map(l=>{
  const e=l.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  let col=''; if(/\\[(ERROR|CRITICAL)\\]|Traceback|Error:/.test(l))col='#ff6b6b';
  else if(/\\[WARN(ING)?\\]/.test(l))col='#ffcc4d'; else if(/\\[INFO\\]/.test(l))col='#8fb7ff';
  return col?('<span style="color:'+col+'">'+e+'</span>'):e; }).join('<br>'); }
function loadPrompt(){ fetch('/api/compose/prompt').then(r=>r.json()).then(d=>{
  document.getElementById('prompt').value=d.prompt||''; document.getElementById('theme').textContent='  テーマ: '+(d.theme||''); }); }
document.getElementById('copy').onclick=()=>{ const t=document.getElementById('prompt');
  navigator.clipboard.writeText(t.value).then(()=>{const b=document.getElementById('copy');b.textContent='コピーした！';
    setTimeout(()=>b.textContent='プロンプトをコピー',1200);}); };
document.getElementById('reload').onclick=loadPrompt;
document.getElementById('imp').onclick=async()=>{ const st=document.getElementById('ist');
  st.textContent='取り込み中…'; st.className='st run';
  const r=await api('/api/compose/import',{text:document.getElementById('paste').value});
  if(r.ok){ st.textContent='✓ 取込完了: '+(r.theme||'')+' / trivia'+r.chapters+'章・'+r.turns+'ターン'; st.className='st ok'; }
  else { st.textContent='× '+(r.message||'失敗'); st.className='st ng'; } };
document.getElementById('fetch').onclick=async()=>{ const st=document.getElementById('fst'); const log=document.getElementById('flog');
  const r=await api('/api/compose/fetch',{}); if(!r.ok){ st.textContent='× '+(r.message||'失敗'); st.className='st ng'; return; }
  const poll=()=>fetch('/api/shorts/jobstatus?job='+encodeURIComponent(r.job)).then(x=>x.json()).then(s=>{
    st.textContent=s.state==='running'?'取得中…':(s.state==='done'?'✓ 取得完了':'失敗'); st.className='st '+(s.state==='running'?'run':s.state==='done'?'ok':'ng');
    if(s.log){log.style.display='block';log.innerHTML=colorizeLog(s.log);log.scrollTop=log.scrollHeight;}
    if(s.state==='running')setTimeout(poll,1500); }); poll(); };
loadPrompt();
</script>
</body></html>
"""

SCRIPT_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台本レビュー</title>
<style>__CSS__
  .chap { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:14px 16px; margin-bottom:16px; }
  .chap .head { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .badge { font-size:12px; padding:2px 10px; border-radius:999px; background:var(--line); color:var(--sub); }
  .cuts { display:flex; flex-direction:column; gap:6px; margin:8px 0 12px; padding:8px 10px;
          background:#0c0f15; border-radius:8px; }
  .cuts .row { display:flex; gap:6px; align-items:flex-end; font-size:13px; flex-wrap:wrap; }
  .cuts .idx { color:var(--sub); width:28px; flex:none; }
  .cuts .fl { display:flex; flex-direction:column; gap:2px; flex:2; min-width:120px; }
  .cuts .fll { font-size:10px; color:var(--sub); }
  .cuts .fl .qInput, .cuts .fl .jaInput, .cuts .fl select { width:100%; }
  .qInput { flex:2; min-width:140px; }
  .jaInput { flex:2; min-width:120px; }
  button.mini { font-size:12px; padding:5px 9px; background:var(--line); }
  button.mini.add { background:transparent; border:1px dashed var(--line); color:var(--sub); width:100%; margin-top:4px; }
  button.mini.del { background:transparent; color:#c66; padding:4px 8px; }
  input[type=text], textarea, select { font:inherit; background:#0c0f15; color:var(--fg);
          border:1px solid var(--line); border-radius:6px; padding:6px 9px; }
  textarea { width:100%; resize:vertical; min-height:38px; font-size:15px; overflow:hidden; }
  .turn { display:grid; grid-template-columns:130px 1fr 88px auto; gap:10px; align-items:start;
          padding:8px 0 8px 12px; border-top:1px solid var(--line); border-left:4px solid transparent; }
  .turn .sp { font-size:14px; font-weight:700; padding-top:8px; display:flex; align-items:center; gap:6px; }
  .turn .sp .dot { width:9px; height:9px; border-radius:50%; flex:none; }
  .turn .acts { display:flex; flex-direction:column; gap:4px; }
  .turn .acts button { font-size:11px; padding:4px 8px; background:var(--line); }
  .turn .acts button.del { background:transparent; color:#c66; }
  .turn .cutsel { font-size:13px; }
  .titleInput { font-size:15px; font-weight:700; flex:1; }
  .qInput { flex:1; }
</style></head>
<body>
<header>
  <a href="/">← パネル</a>
  <h1>台本レビュー</h1>
  <span class="spacer"></span>
  <button class="ok" id="save">保存</button>
  <a href="/story"><button class="primary">ストーリー編集へ →</button></a>
</header>
<main id="main">読み込み中…</main>
<script>
let DATA = null;
function api(path, body){ return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(body)}).then(r=>r.json()); }

function speakerColor(name){
  if(/ずんだ/.test(name)) return '#3fa34d';
  if(/めたん|メタン/.test(name)) return '#d85a9c';
  return '#90a0b5';
}
function autosize(ta){ ta.style.height='auto'; ta.style.height=(ta.scrollHeight+2)+'px'; }

// 長いセリフを分割：カーソル位置（無ければ最初の文末/中央）で2つの発言に割る。
function splitTurn(tn, ta){
  const text = tn.text || '';
  let pos = ta.selectionStart;
  if(!(pos > 0 && pos < text.length)){
    const m = text.slice(1).search(/[。！？]/);
    pos = m >= 0 ? m + 2 : Math.floor(text.length / 2);
  }
  const a = text.slice(0, pos).trim(), b = text.slice(pos).trim();
  if(!a || !b){ alert('分割位置が不正です（カーソルを文の途中に置いてください）'); return; }
  tn.text = a;
  const nt = Object.assign({}, tn, {text: b});  // speaker/chapter/section/emotion/effect/cut を継承
  ['start','end','sentences'].forEach(k=> delete nt[k]);  // 時刻はTTSで再算出
  DATA.script.splice(DATA.script.indexOf(tn) + 1, 0, nt);
  render();
}
function delTurn(tn){
  const i = DATA.script.indexOf(tn);
  if(i >= 0 && confirm('この発言を削除しますか？')){ DATA.script.splice(i, 1); render(); }
}

function fl(text, el){ const w=document.createElement('label'); w.className='fl';
  const t=document.createElement('span'); t.className='fll'; t.textContent=text;
  w.appendChild(t); w.appendChild(el); return w; }

function render(){
  const m = document.getElementById('main'); m.innerHTML='';
  const chapters = DATA.chapters || [];
  // theme
  const th = document.createElement('div'); th.className='chap';
  th.innerHTML = `<div class="head"><span class="badge">テーマ</span></div>`;
  const ti = document.createElement('input'); ti.type='text'; ti.className='titleInput';
  ti.value = DATA.theme||''; ti.style.width='100%';
  ti.onchange = ()=> DATA.theme = ti.value; th.appendChild(ti); m.appendChild(th);

  chapters.forEach((ch, ci)=>{
    const box = document.createElement('div'); box.className='chap';
    box.innerHTML = `<div class="head"><span class="badge">${ch.section||'-'}</span></div>`;
    // title
    const title = document.createElement('input'); title.type='text'; title.className='titleInput';
    title.value = ch.title||''; title.placeholder='章タイトル';
    title.onchange = ()=> ch.title = title.value;
    box.querySelector('.head').appendChild(title);
    // image_cuts（追加/削除・日本語訳・kindは日本語表示）
    const cb = document.createElement('div'); cb.className='cuts';
    const cutList = ch.image_cuts || (ch.image_cuts = []);
    cutList.forEach((cut, k)=>{
      const row = document.createElement('div'); row.className='row';
      row.innerHTML = `<span class="idx">#${k}</span>`;
      const q = document.createElement('input'); q.type='text'; q.className='qInput';
      q.placeholder='英語の検索語'; q.value = cut.image_query||''; q.onchange=()=> cut.image_query=q.value;
      const kind = document.createElement('select');
      kind.innerHTML = `<option value="subject">被写体(ロゴ/人物/製品)</option><option value="ambient">雰囲気(イメージ)</option>`;
      kind.value = cut.image_kind||'ambient'; kind.onchange=()=> cut.image_kind=kind.value;
      const ja = document.createElement('input'); ja.type='text'; ja.className='jaInput';
      ja.placeholder='日本語(意味)'; ja.value = cut.image_query_ja||''; ja.onchange=()=> cut.image_query_ja=ja.value;
      const del = document.createElement('button'); del.className='mini del'; del.textContent='×';
      del.title='この画像を削除'; del.onclick = ()=>{ cutList.splice(k,1); render(); };
      row.appendChild(fl('検索語（英語）', q)); row.appendChild(fl('種別', kind));
      row.appendChild(fl('意味（日本語）', ja)); row.appendChild(del);
      cb.appendChild(row);
    });
    const add = document.createElement('button'); add.className='mini add'; add.textContent='＋画像を追加';
    add.onclick = ()=>{ cutList.push({image_query:'', image_kind:'ambient'}); render(); };
    cb.appendChild(add);
    box.appendChild(cb);
    // turns of this chapter
    DATA.script.forEach((tn)=>{
      if(tn.chapter !== ci) return;
      const row = document.createElement('div'); row.className='turn';
      const col = speakerColor(tn.speaker);
      row.style.borderLeftColor = col;
      const sp = document.createElement('div'); sp.className='sp'; sp.style.color = col;
      sp.innerHTML = `<span class="dot" style="background:${col}"></span>${tn.speaker}`;
      const ta = document.createElement('textarea'); ta.value = tn.text;
      ta.oninput = ()=>{ tn.text = ta.value; autosize(ta); };
      const sel = document.createElement('select'); sel.className='cutsel';
      const n = Math.max(1, cutList.length);
      for(let i=0;i<n;i++){ const o=document.createElement('option'); o.value=i; o.textContent='画像'+i; sel.appendChild(o); }
      sel.value = (typeof tn.cut==='number'?tn.cut:0);
      sel.onchange = ()=> tn.cut = parseInt(sel.value);
      const acts = document.createElement('div'); acts.className='acts';
      const bSplit = document.createElement('button'); bSplit.textContent='分割';
      bSplit.title='カーソル位置でセリフを2つに分ける'; bSplit.onclick = ()=> splitTurn(tn, ta);
      const bDel = document.createElement('button'); bDel.className='del'; bDel.textContent='削除';
      bDel.onclick = ()=> delTurn(tn);
      acts.appendChild(bSplit); acts.appendChild(bDel);
      row.appendChild(sp); row.appendChild(ta); row.appendChild(sel); row.appendChild(acts);
      box.appendChild(row);
    });
    m.appendChild(box);
  });
  // DOM反映後にテキスト全文が見えるよう高さを内容に合わせる（見切れ防止）。
  document.querySelectorAll('#main textarea').forEach(autosize);
}

document.getElementById('save').onclick = async ()=>{
  const r = await api('/api/script', DATA);
  document.getElementById('save').textContent = r.ok ? '保存✓' : '失敗:'+(r.message||'');
  setTimeout(()=>document.getElementById('save').textContent='保存', 1500);
};

fetch('/api/script').then(r=>r.json()).then(d=>{
  if(d.error){ document.getElementById('main').textContent = d.error; return; }
  DATA = d; render();
});
</script>
</body></html>
"""

STORY_PAGE = _load_page("review_story_page.html")


# 台本ファクトチェック用：読み取り専用ビュー（フル台本／概要のみ）。編集はしない。
READ_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台本ファクトチェック</title>
<style>__CSS__
  .toggle { display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }
  .toggle button { background:transparent; color:var(--sub); border:none; border-radius:0; padding:7px 16px; font-weight:700; }
  .toggle button.on { background:var(--accent); color:#fff; }
  .doc { max-width:860px; margin:0 auto; }
  .hint { color:var(--sub); font-size:13px; margin:0 0 16px; }
  .chaphead { display:flex; align-items:baseline; gap:10px; margin:22px 0 8px; padding-bottom:6px; border-bottom:1px solid var(--line); }
  .chaphead .t { font-size:18px; font-weight:700; }
  .badge { font-size:12px; padding:2px 10px; border-radius:999px; background:var(--line); color:var(--sub); flex:none; }
  .sum { color:var(--sub); font-size:14px; line-height:1.7; margin:2px 0 0; }
  .line { display:flex; gap:12px; padding:5px 0; line-height:1.8; }
  .line .sp { flex:none; width:80px; font-weight:700; font-size:13px; padding-top:3px; }
  .line .sp .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  .line .tx { font-size:16px; }
  .sumitem { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; }
  .sumitem .th { display:flex; align-items:center; gap:8px; margin-bottom:4px; }
  .sumitem .th .t { font-weight:700; }
</style></head>
<body>
<header>
  <a href="/">← パネル</a>
  <h1>台本ファクトチェック</h1>
  <span class="spacer"></span>
  <div class="toggle"><button id="mFull" class="on">フル台本</button><button id="mSum">概要のみ</button></div>
  <button id="copyBtn" title="表示中の内容をプレーンテキストでコピー（ファクトチェック貼り付け用）">📋 コピー</button>
  <a href="/story"><button class="primary">編集へ →</button></a>
</header>
<main><div id="doc" class="doc">読み込み中…</div></main>
<script>
let DATA=null, MODE='full';
function speakerColor(n){ if(/ずんだ/.test(n))return '#3fa34d'; if(/めたん|メタン/.test(n))return '#d85a9c'; return '#90a0b5'; }
function esc(s){ const d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }
function secLabel(ch, idx, all){
  if(ch.section==='intro') return '導入';
  if(ch.section==='outro') return '締め';
  let n=0; for(let i=0;i<=idx;i++){ if((all[i].section)==='trivia') n++; }
  return 'チャプター'+n;
}
function render(){
  const root=document.getElementById('doc'); root.innerHTML='';
  if(!DATA){ root.textContent='台本がありません'; return; }
  const chapters=DATA.chapters||[];
  if(DATA.theme){ const h=document.createElement('div'); h.className='chaphead';
    h.innerHTML='<span class="t">テーマ：'+esc(DATA.theme)+'</span>'; root.appendChild(h); }
  if(MODE==='sum'){
    const hint=document.createElement('div'); hint.className='hint';
    hint.textContent='各ネタの要点(summary)だけを一覧。事実確認の素早いスキャン用。'; root.appendChild(hint);
    chapters.forEach((ch,i)=>{ if(!ch.summary && ch.section!=='trivia') return;
      const it=document.createElement('div'); it.className='sumitem';
      const th=document.createElement('div'); th.className='th';
      th.innerHTML='<span class="badge">'+secLabel(ch,i,chapters)+'</span><span class="t">'+esc(ch.title||'')+'</span>';
      it.appendChild(th);
      if(ch.summary){ const s=document.createElement('div'); s.className='sum'; s.textContent=ch.summary; it.appendChild(s); }
      root.appendChild(it); });
    return;
  }
  // フル台本：章ごとに見出し＋要点＋全台詞
  chapters.forEach((ch,i)=>{
    const head=document.createElement('div'); head.className='chaphead';
    head.innerHTML='<span class="badge">'+secLabel(ch,i,chapters)+'</span><span class="t">'+esc(ch.title||'')+'</span>';
    root.appendChild(head);
    if(ch.summary){ const s=document.createElement('div'); s.className='sum'; s.textContent='（要点）'+ch.summary; root.appendChild(s); }
    (DATA.script||[]).filter(t=>t.chapter===i).forEach(t=>{
      const ln=document.createElement('div'); ln.className='line';
      const sp=document.createElement('div'); sp.className='sp';
      sp.innerHTML='<span class="dot" style="background:'+speakerColor(t.speaker)+'"></span>'+esc((t.speaker||'').slice(0,4));
      const tx=document.createElement('div'); tx.className='tx'; tx.textContent=t.text||'';
      ln.appendChild(sp); ln.appendChild(tx); root.appendChild(ln);
    });
  });
}
function setMode(m){ MODE=m; document.getElementById('mFull').className=m==='full'?'on':'';
  document.getElementById('mSum').className=m==='sum'?'on':''; render(); }
// 表示中モードの内容をプレーンテキストで組み立ててコピー（ファクトチェック貼り付け用）。
function buildPlain(){
  if(!DATA) return '';
  const ch=DATA.chapters||[], sc=DATA.script||[]; const out=[];
  if(DATA.theme){ out.push('テーマ：'+DATA.theme, ''); }
  if(MODE==='sum'){
    ch.forEach((c,i)=>{ if(!c.summary && c.section!=='trivia') return;
      out.push('【'+secLabel(c,i,ch)+'】'+(c.title||'')); if(c.summary) out.push(c.summary); out.push(''); });
  } else {
    ch.forEach((c,i)=>{ out.push('【'+secLabel(c,i,ch)+'】'+(c.title||''));
      if(c.summary) out.push('（要点）'+c.summary);
      sc.filter(t=>t.chapter===i).forEach(t=>out.push(t.text||'')); out.push(''); });
  }
  return out.join('\\n').replace(/\\n{3,}/g,'\\n\\n').trim();
}
function copyFeedback(){ const b=document.getElementById('copyBtn'); const o=b.textContent;
  b.textContent='✓ コピー済'; setTimeout(()=>{ b.textContent=o; }, 1500); }
function copyContent(){
  const text=buildPlain(); if(!text){ alert('内容がありません'); return; }
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(copyFeedback).catch(fallbackCopy.bind(null,text));
  } else { fallbackCopy(text); }
}
function fallbackCopy(text){
  const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
  document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); copyFeedback(); }catch(e){ alert('コピーに失敗しました'); }
  document.body.removeChild(ta);
}
document.getElementById('copyBtn').onclick=copyContent;
document.getElementById('mFull').onclick=()=>setMode('full');
document.getElementById('mSum').onclick=()=>setMode('sum');
fetch('/api/script').then(r=>r.json()).then(d=>{ if(d.error){ document.getElementById('doc').textContent=d.error; return; } DATA=d; render(); });
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 既定の逐次ログを抑制
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _html(self, s):
        body = s.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, content_type=None):
        """ローカル静的ファイルをRange対応で配信する（音声シーク用）。"""
        if not path or not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return
        size = os.path.getsize(path)
        start, end = 0, max(0, size - 1)
        status = 200
        range_header = self.headers.get("Range") or ""
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            elif match.group(2):
                length = min(size, int(match.group(2)))
                start, end = size - length, size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        length = max(0, end - start + 1)
        mime = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break  # Playerがシーク時に古いRange要求を破棄しただけなので正常扱い。
                remaining -= len(chunk)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._html(LANDING_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/script":
            self._html(SCRIPT_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/read":
            self._html(READ_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/story":
            self._html(STORY_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/preview/player.js":
            self._file(os.path.join(VIDEO_PUBLIC_DIR, "review-player.js"), "text/javascript; charset=utf-8")
            return
        if path == "/preview-assets/avatars/manifest.json":
            self._json(avatar_manifest())
            return
        if path == "/preview-assets/depth-manifest.json":
            self._json(depth_manifest())
            return
        if path.startswith("/preview-assets/"):
            self._file(preview_asset_path(path[len("/preview-assets/"):]))
            return
        if path == "/shorts":
            self._html(SHORTS_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/compose":
            self._html(COMPOSE_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/api/compose/prompt":
            self._json(compose_main_prompt())
            return
        if path == "/api/shorts/list":
            self._json({"netas": main_trivia_netas(), "shorts": list_shorts()})
            return
        if path == "/api/shorts/jobstatus":
            qs = parse_qs(urlparse(self.path).query)
            job = (qs.get("job") or [""])[0]
            self._json(job_status(job))
            return
        if path == "/api/status":
            self._json({"dir": DIR, "base": BASE_DIR, "status": pipeline_status(),
                        "target": target_for_dir(), "previewState": compute_preview_state()})
            return
        if path == "/api/script":
            data = load_script()
            if data:
                # 編集モデル（schemaVersion/assets/imageCues/visualSegments＋turn ID）を
                # 読み込み時に変換して付与する。非破壊・冪等で、旧フィールドはそのまま残る。
                from src import editor_model
                data = editor_model.migrate(data, load_review())
            self._json(data if data else {"error": "script.json がありません（先に台本生成）"})
            return
        if path == "/api/cuts":
            review = load_review()
            for c in review.get("cuts", []):
                img = c.get("image")
                if not img:
                    continue
                dims = image_dims(img)
                if dims:
                    c["w"], c["h"] = dims
                p = os.path.join(DIR, img)
                if os.path.exists(p):
                    c["bytes"] = os.path.getsize(p)
            review.update({"summary": review_summary(review)})
            self._json(review)
            return
        if path.startswith("/img-file/"):
            # 素材ライブラリ用：ファイル名で DIR 内の画像を配信（ch_ci キーに依らない）。
            name = os.path.basename(unquote(path[len("/img-file/"):]))   # basenameでパス遡上を防ぐ
            data = read_image_bytes(name) if name else None
            if not data:
                self.send_response(404)
                self.end_headers()
                return
            ext = os.path.splitext(name)[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", _CT.get(ext, "application/octet-stream"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/img/"):
            key = path[len("/img/"):]
            cut = find_cut(load_review(), key)
            data = read_image_bytes(cut["image"]) if cut and cut.get("image") else None
            if not data:
                self.send_response(404)
                self.end_headers()
                return
            ext = os.path.splitext(cut["image"])[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", _CT.get(ext, "application/octet-stream"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()
        review = load_review()
        if path == "/api/fetch":
            self._json(do_fetch_cut(body.get("ch"), body.get("ci"),
                                    body.get("query"), body.get("kind"), body.get("lang")))
            return
        if path == "/api/candidates":
            self._json(do_candidates(body.get("query"), body.get("kind"),
                                     body.get("source"), body.get("lang"), body.get("page")))
            return
        if path == "/api/regenerate":
            self._json(do_regenerate_chapters(body.get("indices") or [],
                                               fallback=body.get("fallback")))
            return
        if path == "/api/regenerate-all":
            self._json(do_regenerate_all(fallback=body.get("fallback")))
            return
        if path == "/api/delete-cut":
            try:
                self._json(reindex_review_after_cut_delete(int(body["ch"]), int(body["ci"])))
            except (KeyError, TypeError, ValueError):
                self._json({"ok": False, "message": "ch/ci が不正"})
            return
        if path == "/api/script":
            ok, msg, norm = apply_save_script(body)
            if ok:
                save_script(norm)
            self._json({"ok": ok, "message": msg})
            return
        if path == "/api/preview-refresh":
            self._json(do_preview_refresh(body))
            return
        if path == "/api/migrate-to-editor":
            # 編集モデルを「正」へ明示切替（自動では行わない・ユーザー操作のみ）。
            self._json(do_switch_to_editor())
            return
        if path == "/api/editor/asset-add":
            self._json(do_asset_add(body))
            return
        if path == "/api/editor/asset-delete":
            self._json(do_asset_delete(body))
            return
        if path == "/api/editor/cue-op":
            self._json(do_cue_op(body))
            return
        if path == "/api/approve":
            ok = apply_approve(review, body.get("key"), body.get("approved", True))
            if ok:
                save_review(review)
            self._json({"ok": ok})
            return
        if path == "/api/attribution":
            ok = apply_attribution(review, body.get("key"), body.get("attribution"))
            if ok:
                save_review(review)
            self._json({"ok": ok})
            return
        if path == "/api/options":
            ok, applied = apply_options(review, body.get("key"), body.get("patch") or {})
            if ok:
                save_review(review)
            self._json({"ok": ok, "applied": applied})
            return
        if path == "/api/import-url":
            ok, msg, fn = apply_import_url(
                review, body.get("key"), body.get("url"), body.get("attribution"))
            if ok:
                save_review(review)
            self._json({"ok": ok, "message": msg, "filename": fn})
            return
        if path == "/api/replace":
            ok, msg, fn = apply_replace(
                review, body.get("key"), body.get("filename"),
                body.get("dataB64"), body.get("attribution"))
            if ok:
                save_review(review)
            self._json({"ok": ok, "message": msg, "filename": fn})
            return
        if path == "/api/set-dir":
            self._json(set_active_dir(body.get("dir") or ""))
            return
        if path == "/api/compose/import":
            self._json(import_main_script(body.get("text")))
            return
        if path == "/api/compose/fetch":
            self._json(start_fetch(BASE_DIR))
            return
        if path == "/api/shorts/prompt":
            self._json(compose_shorts_prompt(body.get("chapters")))
            return
        if path == "/api/shorts/import":
            self._json(import_shorts_script(body.get("text"), body.get("chapters")))
            return
        if path == "/api/shorts/fetch":
            slug = _slugify(body.get("slug") or "")
            self._json(start_fetch(os.path.join(SHORTS_ROOT, slug)))
            return
        if path == "/api/shorts/audio":
            self._json(start_short_audio(body.get("slug") or ""))
            return
        if path == "/api/shorts/generate":
            try:
                self._json(start_short_generate(body.get("chapters")))
            except (TypeError, ValueError):
                self._json({"ok": False, "message": "chapters が不正"})
            return
        if path == "/api/shorts/render":
            self._json(start_short_render_dir(body.get("slug"), body.get("cta")))
            return
        if path == "/api/continue":
            review["status"] = "approved"
            save_review(review)
            cmd = (f"python main_story.py --from-script {os.path.join(DIR, 'script.json')} "
                   f"--images-from-dir --output-dir {DIR}\n"
                   f"cd video && SRC_DIR=../{DIR} npm run render")
            self._json({"ok": True, "command": cmd})
            return
        self.send_response(404)
        self.end_headers()


def main():
    global DIR, BASE_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="docs/story", help="レビュー対象（review.json/画像のあるディレクトリ）")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    DIR = args.dir
    BASE_DIR = args.dir  # 本編＝ショートのネタ元（起動時の対象を基準にする）
    if not os.path.exists(os.path.join(DIR, "review.json")):
        print(f"[review] {DIR}/review.json がありません。先に "
              f"`python main_story.py --stop-after-images` を実行してください。")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[review] http://127.0.0.1:{args.port}/  （対象: {DIR}） Ctrl+C で停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review] 停止")


if __name__ == "__main__":
    main()
