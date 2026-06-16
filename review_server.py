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
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# レビュー対象ディレクトリ（既定 docs/story）。main()で上書き。/shorts から実行時に切替可能。
DIR = "docs/story"
# 基準（本編）ディレクトリ。ショートのネタ元・docs/shorts の親判定に使う（起動時に固定）。
BASE_DIR = "docs/story"

_CT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


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
    return True, "ok", out


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
  .stage { display:flex; align-items:center; gap:14px; background:var(--card);
           border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin-bottom:12px; }
  .dot { width:12px; height:12px; border-radius:50%; background:var(--line); flex:none; }
  .dot.done { background:var(--ok); }
  .stage .t { font-weight:700; }
  .stage .d { color:var(--sub); font-size:13px; }
  .stage .go { margin-left:auto; }
  .cmd { color:var(--sub); font-size:12px; margin-top:6px; }
</style></head>
<body>
<header><h1>制作パネル</h1><span class="spacer"></span><span class="d" id="dir"></span></header>
<main id="main">読み込み中…</main>
<script>
const STAGES = [
  {key:'script', t:'① ストーリー編集', d:'台本＋画像を一体で確認/編集（概要→章を開く）', link:'/story',
   cmd:'python main_story.py --stop-after-images'},
  {key:'script', t:'台本ファクトチェック', d:'フル台本／概要を読み取り専用で表示（事実確認用）', link:'/read',
   cmd:'(読むだけ・編集は /story から)'},
  {key:'script', t:'ブラウザAIで台本を作る', d:'Geminiの代わりにプロンプトをコピー→他AIで生成→貼り付け取り込み', link:'/compose',
   cmd:'(Gemini枠を使わず台本JSONを取り込む)'},
  {key:'audio',  t:'③ 音声+meta', d:'VOICEVOXで音声・字幕生成', link:null,
   cmd:'python main_story.py --from-script DIR/script.json --images-from-dir'},
  {key:'meta',   t:'④ 仕上げ', d:'Remotionで動画書き出し', link:null,
   cmd:'cd video && SRC_DIR=../DIR npm run render'},
  {key:'meta',   t:'⑤ ショート（縦9:16）', d:'本編ネタから独立ショートを生成→台本レビュー→書き出し', link:'/shorts',
   cmd:'python main_story.py --from-script DIR/script.json --short-from N --slug NAME --stop-after-images'},
];
fetch('/api/status').then(r=>r.json()).then(st=>{
  document.getElementById('dir').textContent = '対象: '+st.dir;
  const m = document.getElementById('main'); m.innerHTML='';
  if(st.dir!==st.base){
    const sw=document.createElement('div'); sw.className='stage';
    sw.innerHTML='<span class="dot done"></span><div><div class="t">ショートを編集中: '+st.dir+'</div>'+
      '<div class="d">レビュー対象がショートに切り替わっています</div></div>';
    const b=document.createElement('button'); b.className='go'; b.textContent='本編に戻す';
    b.onclick=async()=>{ await fetch('/api/set-dir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dir:st.base})}); location.reload(); };
    sw.appendChild(b); m.appendChild(sw);
  }
  for(const s of STAGES){
    const done = st.status[s.key];
    const el = document.createElement('div'); el.className='stage';
    el.innerHTML = `<span class="dot ${done?'done':''}"></span>
      <div><div class="t">${s.t} ${done?'<span class="d">✓ 生成済</span>':''}</div>
        <div class="d">${s.d}</div>
        <div class="cmd"><code>${s.cmd.replace('DIR', st.dir)}</code></div></div>
      ${s.link?`<a class="go" href="${s.link}"><button class="primary">開く</button></a>`:''}`;
    m.appendChild(el);
  }
  const note = document.createElement('p'); note.className='d';
  note.style.color='var(--sub)'; note.style.fontSize='13px';
  note.innerHTML='※ 生成/書き出しは今はコマンドで実行（ボタン起動は今後対応）。台本・画像は「開く」で確認/編集。';
  m.appendChild(note);
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

STORY_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ストーリー編集</title>
<style>__CSS__
  .theme { display:flex; gap:10px; align-items:center; margin-bottom:14px; }
  .theme input { flex:1; font-size:16px; font-weight:700; background:#0c0f15; color:var(--fg);
                 border:1px solid var(--line); border-radius:8px; padding:8px 12px; }
  .estbar { background:#0c0f15; border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin-bottom:14px; }
  .estbar b { font-size:15px; } .estbar .es { color:var(--sub); font-size:12px; margin-left:8px; }
  .estbar .es2 { margin-top:6px; display:flex; flex-wrap:wrap; gap:6px; }
  .estbar .es3 { font-size:11px; color:var(--sub); background:var(--line); padding:2px 9px; border-radius:999px; }
  .sec { background:var(--card); border:1px solid var(--line); border-radius:12px;
         margin-bottom:12px; overflow:hidden; }
  .sec.open { border-color:var(--accent); }
  .sechead { display:flex; align-items:center; gap:12px; padding:14px 16px; cursor:pointer; }
  .sechead:hover { background:#222a37; }
  .sechead .selcb { width:17px; height:17px; flex:none; cursor:pointer; accent-color:#ffd84d; }
  #regen { background:var(--line); color:var(--fg); }
  #regen:not(:disabled) { background:#ffd84d; color:#1a1a1a; }
  #regen:disabled { opacity:.45; cursor:default; }
  #lockov { position:fixed; inset:0; z-index:9999; display:none; align-items:center; justify-content:center;
            background:rgba(8,10,14,.78); backdrop-filter:blur(1px); }
  .lockbox { display:flex; flex-direction:column; align-items:center; gap:14px; color:var(--fg); font-size:15px;
             background:#11151c; border:1px solid var(--line); border-radius:12px; padding:28px 38px; text-align:center; }
  .spin { width:30px; height:30px; border:3px solid var(--line); border-top-color:#ffd84d;
          border-radius:50%; animation:spin .8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .badge { font-size:12px; padding:2px 10px; border-radius:999px; background:var(--line);
           color:var(--sub); flex:none; }
  .conf { font-size:11px; padding:2px 8px; border-radius:999px; flex:none; font-weight:700; }
  .conf.high { background:#173a25; color:#5fd08a; }       /* 確度: 高=採用OK */
  .conf.medium { background:#3a3217; color:#ffcc4d; }     /* 中=要裏取り・断定回避 */
  .conf.low { background:#3a1d1d; color:#ff6b6b; }        /* 低=原則不採用 */
  .vizb { font-size:11px; padding:2px 8px; border-radius:999px; flex:none; background:#2a2440; color:#c4a8ff; font-weight:700; }
  .sechead .ttl { font-weight:700; flex:none; max-width:34%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sechead .sum { color:var(--sub); font-size:13px; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .thumbs { display:flex; gap:4px; flex:none; }
  .thumbs .th { width:40px; height:24px; border-radius:4px; object-fit:cover; background:#0c0f15;
                border:1px solid var(--line); }
  .thumbs .ph { width:40px; height:24px; border-radius:4px; background:#0c0f15; border:1px dashed var(--line);
                display:flex; align-items:center; justify-content:center; color:var(--sub); font-size:10px; }
  .body { padding:0 16px 16px; }
  input[type=text], textarea, select { font:inherit; background:#0c0f15; color:var(--fg);
          border:1px solid var(--line); border-radius:6px; padding:6px 9px; }
  textarea { width:100%; resize:vertical; overflow:hidden; }
  .lbl { color:var(--sub); font-size:12px; margin:10px 0 4px; }
  .imgrow { display:flex; gap:8px; align-items:center; padding:6px; background:#0c0f15;
            border-radius:8px; margin-bottom:6px; flex-wrap:wrap; }
  .imgrow { align-items:flex-start; }
  .imgrow img, .imgrow .ph2 { width:320px; height:180px; object-fit:contain; background:#11151c;
            border:1px solid var(--line); border-radius:6px; flex:none; }
  /* クロップ反映サムネ：枠内にクロップ後の領域だけを表示（render結果に近い見た目） */
  .imgrow .imgthumb { width:320px; height:180px; position:relative; overflow:hidden; background:#11151c;
            border:1px solid var(--line); border-radius:6px; flex:none; }
  .imgrow .imgthumb img { border:none; border-radius:0; }
  /* 透過余白の表現＝市松模様（背景は固定でないため特定色を出さない） */
  .imgrow .imgthumb.transbg { background-color:#fff; background-image:
    linear-gradient(45deg,#d6dae0 25%,transparent 0),linear-gradient(-45deg,#d6dae0 25%,transparent 0),
    linear-gradient(45deg,transparent 75%,#d6dae0 0),linear-gradient(-45deg,transparent 75%,#d6dae0 0);
    background-size:16px 16px; background-position:0 0,0 8px,8px -8px,-8px 0; }
  .imgrow .ph2 { display:flex; align-items:center; justify-content:center; color:var(--sub); font-size:11px; }
  .imgrow .fields { flex:1; display:flex; flex-direction:column; gap:7px; min-width:0; }
  .imgrow .fields .frow { display:flex; gap:8px; align-items:center; }
  .imgrow .fields input, .imgrow .fields select { width:100%; }
  .imgrow .fields .szinfo { font-size:11px; color:var(--sub); font-variant-numeric:tabular-nums; min-height:13px; }
  .fl { display:flex; flex-direction:column; gap:2px; }
  .fll { font-size:10px; color:var(--sub); }
  .imgrow .fields .frow { align-items:flex-end; }
  .candpanel { background:#0c0f15; border:1px solid var(--line); border-radius:8px; padding:8px; margin:2px 0 8px; }
  .ctabs { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
  .ctab { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--line);
          background:transparent; color:var(--sub); cursor:pointer; }
  .ctab.on { background:#ffd84d; color:#1a1a1a; border-color:#ffd84d; }
  .chint { font-size:12px; color:var(--sub); padding:8px 2px; }
  .cgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:8px; }
  .ccell { cursor:pointer; border:2px solid transparent; border-radius:6px; overflow:hidden; background:#11151c; }
  .ccell:hover { border-color:#ffd84d; }
  .ccell.busy { opacity:.5; pointer-events:none; }
  .ccell img { width:100%; height:90px; object-fit:cover; display:block; }
  .ccap { font-size:9px; color:var(--sub); padding:2px 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cfoot { display:flex; justify-content:center; padding:8px 0 2px; color:var(--sub); font-size:12px; }
  /* 候補の拡大確認オーバーレイ */
  .cprev { position:fixed; inset:0; background:rgba(0,0,0,.78); display:flex; align-items:center;
           justify-content:center; z-index:50; padding:24px; }
  .cprevbox { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px;
              display:flex; flex-direction:column; gap:10px; max-width:90vw; max-height:90vh; }
  .cprevbox img { max-width:84vw; max-height:72vh; object-fit:contain; border-radius:6px; background:#0c0f15; }
  .cprevcap { font-size:12px; color:var(--sub); word-break:break-all; }
  .cprevrow { display:flex; gap:10px; justify-content:flex-end; }
  .cprevrow button { font-size:14px; padding:8px 18px; border-radius:8px; border:none; cursor:pointer;
                     background:var(--line); color:#fff; font-weight:700; }
  .cprevrow button.primary { background:var(--accent); }
  .imgrow .fields .frow select { flex:1; }
  .imgrow .fields .q { font-size:14px; }
  button.mini { font-size:12px; padding:5px 9px; background:var(--line); color:#fff; border:none;
                border-radius:6px; cursor:pointer; font-weight:700; }
  main { max-width:1500px; }
  .turn { display:grid; grid-template-columns:120px 1fr 200px auto; gap:10px; align-items:start;
          padding:6px 0 6px 12px; border-top:1px solid var(--line); border-left:4px solid transparent; }
  .turn .sp { font-size:14px; font-weight:700; padding-top:8px; display:flex; align-items:center; gap:6px; }
  .turn .sp .dot { width:9px; height:9px; border-radius:50%; flex:none; }
  .adjust { display:flex; gap:16px; flex-wrap:wrap; margin:2px 0 10px; padding:12px;
            background:#0c0f15; border:1px solid var(--accent); border-radius:8px; }
  .adjust .crop { position:relative; width:560px; height:315px; max-width:100%; background:#11151c; cursor:crosshair;
            flex:none; border-radius:6px; overflow:hidden; user-select:none; }
  .adjust .crop img { width:100%; height:100%; object-fit:contain; pointer-events:none; }
  .adjust .croprect { position:absolute; border:2px solid #ffd84d; background:rgba(255,216,77,.12); pointer-events:none; }
  .adjust .ctl { display:flex; flex-direction:column; gap:8px; min-width:260px; flex:1; }
  .adjust .filters { display:grid; grid-template-columns:auto 1fr; gap:4px 8px; align-items:center; font-size:12px; color:var(--sub); }
  .adjust .filters input[type=range] { width:100%; }
  .adjust .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .adjust .chk { font-size:12px; color:var(--sub); display:flex; align-items:center; gap:5px; }
  .adjust input[type=text] { flex:1; min-width:120px; }
  .adjust select { background:#11151c; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:5px 8px; }
  .adjust .hint { color:var(--sub); font-size:12px; min-height:14px; }
  .cutpick { display:flex; flex-wrap:wrap; gap:5px; }
  .copt { width:88px; height:50px; border:2px solid transparent; border-radius:6px; overflow:hidden;
          cursor:pointer; background:#0c0f15; flex:none; position:relative; }
  .copt.sel { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent); }
  .copt img { width:100%; height:100%; object-fit:cover; }
  .copt .ph3 { display:flex; width:100%; height:100%; align-items:center; justify-content:center;
          color:var(--sub); font-size:11px; }
  .copt .num { position:absolute; left:2px; top:1px; font-size:10px; color:#fff; background:rgba(0,0,0,.55);
          padding:0 4px; border-radius:3px; }
  .turn .acts { display:flex; flex-direction:column; gap:4px; }
  .turn .acts button { font-size:11px; padding:4px 8px; background:var(--line); color:#fff; border:none; border-radius:6px; cursor:pointer; }
  .turn .acts button.del { background:transparent; color:#c66; }
  .vchips { display:flex; gap:5px; flex-wrap:wrap; padding:0 0 8px 30px; margin-top:-2px; }
  .vchip { font-size:11px; padding:2px 10px; border-radius:999px; border:1px solid var(--line);
           background:transparent; color:var(--sub); cursor:pointer; }
  .vchip.on { background:#2a2440; color:#c4a8ff; border-color:#5a4a8a; font-weight:700; }
  .vchip.dis { opacity:.38; cursor:not-allowed; }
  .vchip.edit { border-color:#5a4a8a; color:#c4a8ff; }
  .vchip.range { font-size:10px; padding:1px 7px; }
  .vizcontent { margin:0 0 10px 30px; padding:10px; background:#0c0f15; border:1px solid #5a4a8a; border-radius:8px; }
  /* 演出の表示範囲バー：開始カード＋範囲内の行を紫の左帯でつなぐ */
  .vizrow { border-left:5px solid #7a5cff !important; }
  .vizhdr { display:flex; align-items:center; gap:8px; margin:8px 0 0; padding:5px 12px;
            border-left:5px solid #7a5cff; background:rgba(122,92,255,0.12); border-radius:0 6px 6px 0; }
  .vizhdr-t { font-size:13px; font-weight:800; color:#c4a8ff; }
  .cutpick.vizmuted { color:var(--sub); font-size:11px; font-style:italic; align-items:center; }
  /* === 二画面（台本左＋編集右）：Notion/Discord/Linear 参考・可読性優先 === */
  .tp { display:grid; grid-template-columns:1fr 480px; gap:24px; align-items:start; }
  .tp-left { min-width:0; }
  /* 右ペイン＝補助。背景に寄せ視覚優先度を下げる（細い罫線・低コントラスト） */
  .tp-right { position:sticky; top:74px; max-height:calc(100vh - 90px); overflow:auto;
              background:#10141b; border:1px solid #1c232e; border-radius:12px; padding:14px; }
  /* 広げる：左に被さる大きいパネル */
  .tp-right.wide { position:fixed; top:64px; right:14px; bottom:14px; width:min(960px,84vw);
                   max-height:none; z-index:30; box-shadow:0 12px 48px rgba(0,0,0,.55); }
  /* 右ペイン上端に固定するツールバー（スクロールしても消えない） */
  .rwbar { position:sticky; top:0; z-index:5; display:flex; justify-content:flex-end; align-items:center;
           background:#10141b; margin:-14px -14px 10px; padding:9px 14px; border-bottom:1px solid #1c232e; }
  .rwbtn { font-size:12px; font-weight:700; padding:6px 12px; border-radius:7px; border:none;
           background:#1a212c; color:var(--sub); cursor:pointer; }
  .rwbtn:hover { color:var(--fg); background:#222a37; }
  .rwbtn.wide { background:#ffd84d; color:#1a1f2b; }  /* 拡大中の「戻す」は目立たせる */
  /* 章＝セクション。章間は大きく空ける。選択中の章だけ淡く強調 */
  .chsec { margin:30px 0 0; padding:2px 0; border-radius:14px; border-left:3px solid transparent; }
  .chsec.active { background:#12171f; border-left-color:#39424f; padding:2px 10px 6px; }
  .chsec:first-child { margin-top:8px; }
  .chdiv { display:flex; align-items:center; gap:9px; margin:0 0 10px; padding:6px 2px; }
  .chdiv .ttl { font-weight:700; font-size:14.5px; color:#fff; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chdiv .selcb { width:16px; height:16px; flex:none; accent-color:#ffd84d; }
  .chdiv .chev { background:transparent; border:none; color:var(--sub); font-size:13px; padding:2px 4px; cursor:pointer; flex:none; border-radius:5px; }
  .chdiv .chev:hover { background:#1c232e; color:var(--fg); }
  .chdiv .chcnt { font-size:11px; color:var(--sub); flex:none; display:none; }
  .chsec.collapsed .chcnt { display:inline-block; }
  .chsec.collapsed .line { display:none; }
  /* セリフカード＝Discord風（アイコン＋名前＋本文）。余白広め・罫線なし・6px左カラー */
  .line { display:flex; gap:12px; align-items:flex-start; padding:13px 15px; margin-bottom:10px; border-radius:10px;
          cursor:pointer; background:#161b24; border-left:6px solid transparent; }
  .line:hover { background:#1a212c; }
  .line.sel { background:#1f2835; }
  .line .av { width:30px; height:30px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center;
              font-size:14px; font-weight:800; color:#fff; }
  .line .lc { flex:1; min-width:0; }
  .line .nm { font-weight:700; font-size:12.5px; margin-bottom:3px; }
  .line .tx { font-size:14.5px; line-height:1.8; color:var(--fg); white-space:pre-wrap; word-break:break-word; }
  .line .tx.empty { color:var(--sub); }
  .line textarea { width:100%; font-size:14.5px; line-height:1.7; min-height:46px; }
  .line .mk { flex:none; display:flex; gap:4px; align-items:center; }
  .line .mk img { width:40px; height:24px; object-fit:cover; border-radius:4px; }
  .line .mk .vz { font-size:11px; color:#a99adf; }
  .line .lacts { display:none; gap:5px; flex:none; align-items:center; }
  .line.sel .lacts { display:flex; }
  .line .lacts button { font-size:11px; padding:4px 9px; background:#2a323e; color:var(--fg); border:none; border-radius:6px; cursor:pointer; }
  .line .lacts button.del { background:transparent; color:#c97; }
  /* 演出範囲レール：各セグメントが自分の開始/終了行にハンドルを常時持つ＋範囲の縦帯。viz章のみ */
  .line .rail { width:62px; flex:none; align-self:stretch; position:relative; margin:-13px 0; }  /* カード上下padding(13px)を打ち消し枠に揃える */
  .line .rail .seg { position:absolute; left:29px; width:3px; background:#6b6ae0; top:-5px; bottom:-13px; }
  .line .rail .seg.s { top:21px; border-radius:3px 3px 0 0; }
  .line .rail .seg.e { bottom:21px; border-radius:0 0 3px 3px; }
  /* 境界ハンドル：ラベル(開始/終了)＋1行ずつ動かす ↑↓ ボタン。常時表示。 */
  .line .rail .bh { position:absolute; left:1px; width:58px; display:flex; align-items:center; gap:2px; z-index:1; }
  .line .rail .bh.s { top:1px; }      /* 開始＝行の上端 */
  .line .rail .bh.e { bottom:1px; }   /* 終了＝行の下端 */
  .line .rail .bh .lab { font-size:9px; font-weight:800; color:#fff; border-radius:4px; padding:1px 3px; line-height:1.3; }
  .line .rail .bh button { width:15px; height:16px; border:1px solid #313a47; border-radius:4px; background:#141a23;
                           color:#9aa6ba; cursor:pointer; font-size:10px; line-height:1; padding:0; box-sizing:border-box; }
  .line .rail .bh button:hover:not(:disabled) { border-color:#6b6ae0; color:#dbe1ec; }
  .line .rail .bh button:disabled { opacity:.2; cursor:default; }
  .rtabs { display:flex; gap:6px; margin-bottom:10px; }
  .rtab { font-size:13px; font-weight:700; padding:7px 13px; border-radius:8px; border:none;
          background:#1a212c; color:var(--sub); cursor:pointer; }
  .rtab.on { background:#2a2440; color:#c4a8ff; }
  .rtab.imgon { background:#23354f; color:#cfe0ff; }
  @media (max-width:920px){ .tp { grid-template-columns:1fr; } .tp-right { position:static; max-height:none; } }
</style></head>
<body>
<header>
  <a href="/">← パネル</a>
  <h1>ストーリー編集</h1>
  <span class="spacer"></span>
  <a href="/read"><button title="フル台本/概要を読み取り専用で表示（事実確認）">台本を読む</button></a>
  <button id="regenall" title="テーマで台本を丸ごと作り直す（intro+全ネタ+outroを新規生成・整合性が保たれる）">全体を作り直す</button>
  <button id="regen" disabled style="display:none" title="（深掘りストーリーでは無効：章は物語が連続するため章単位の作り直しは不可。全体を作り直すを使う）">選択章を再生成</button>
  <label id="fblabel" title="ON=primaryモデルが503/枯渇なら別の無料モデルへ自動切替。OFF=primaryのみ（品質固定・失敗を即把握）" style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#9fb0c5;cursor:pointer;"><input type="checkbox" id="fallback" checked> フォールバック</label>
  <button class="ok" id="save">保存</button>
</header>
<main id="main">読み込み中…</main>
<script>
let DATA=null, CUTS=[], cutMap={}, OPEN=new Set(), adjustOpen=new Set(), candOpen=new Set(), candState={}, selChs=new Set();
let selGi=-1, rtab=null, dirty=false, collapsed=new Set(), rwide=false, selSeg=null;  // 二画面：選択行 / 右タブ / 未保存 / 畳んだ章 / 右ペイン拡大 / 選択中演出セグメント
function api(p,b){ return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b)}).then(r=>r.json()); }
function setOpt(key,patch){ return api('/api/options',{key,patch}); }
function speakerColor(n){ if(/ずんだ/.test(n))return '#3fa34d'; if(/めたん|メタン/.test(n))return '#d85a9c'; return '#90a0b5'; }
function autosize(t){ t.style.height='auto'; t.style.height=(t.scrollHeight+2)+'px'; }
function imgUrl(ci,k){ const c=cutMap[ci+'_'+k]; return (c&&c.image)?('/img/'+ci+'_'+k+'?v='+Date.now()):null; }
// review.json からカット情報(画像/出典/調整)を取り直して cutMap を更新（差し替え後にクレジット等を反映）。
async function refreshCuts(){ const rev=await (await fetch('/api/cuts')).json(); CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c); }
function fmtKB(b){ if(b==null) return '?'; return b<1024? b+'B' : b<1048576? Math.round(b/1024)+'KB' : (b/1048576).toFixed(1)+'MB'; }
// 入力/プルダウンに小さなラベルを上付けする（何の項目か分かるように）
function fl(text, el){ const w=document.createElement('label'); w.className='fl';
  const t=document.createElement('span'); t.className='fll'; t.textContent=text;
  w.appendChild(t); w.appendChild(el); return w; }
// D&D取り込み共通：OSファイル / data:画像 / WebのURL を ch_NN_MM に保存。行サムネと調整パネルで共用。
async function dropImport(ky, dt, attribution){
  if(dt.files && dt.files.length){
    const f=dt.files[0]; let b64, fn=f.name;
    if(f.size > 8*1024*1024){  // 大きい画像は手元で縮小してから登録（15MB制限/重さ対策）
      try{ const durl=await shrinkImage(URL.createObjectURL(f), 1920, 5*1024*1024); b64=durl.split(',')[1]; fn='resized.jpg'; }
      catch(e){ b64=await new Promise(res=>{ const rd=new FileReader(); rd.onload=()=>res(rd.result.split(',')[1]); rd.readAsDataURL(f); }); }
    } else {
      b64=await new Promise(res=>{ const rd=new FileReader(); rd.onload=()=>res(rd.result.split(',')[1]); rd.readAsDataURL(f); });
    }
    return api('/api/replace', {key:ky, filename:fn, dataB64:b64, attribution});
  }
  const url=(dt.getData('text/uri-list')||dt.getData('text/plain')||'').split('\\n').find(s=>s&&!s.startsWith('#'))||'';
  if(!url) return null;
  if(url.startsWith('data:image')) return api('/api/replace', {key:ky, filename:'drop.png', dataB64:url.split(',')[1], attribution});
  return api('/api/import-url', {key:ky, url, attribution});
}
// 候補画像の取得（取得先別・DLせずサムネ表示）。1検索=複数件で追加課金なし。
// lang/query は前回値を引き継ぐ（取得先タブ切替で言語が変わらないように）。
async function loadCand(ci,k,cut,source,lang,query,page,append){ const ky=ci+'_'+k; const prev=candState[ky]||{};
  lang = lang || prev.lang || 'en';
  query = (query!=null) ? query : (prev.query!=null ? prev.query : cut.image_query);
  page = page || 1;
  candState[ky]=Object.assign({},prev,{loading:true,error:null,lang,query}); render();
  const r=await api('/api/candidates',{query:query,kind:cut.image_kind,source:source,lang:lang,page:page});
  const base = (append && prev.candidates) ? prev.candidates : [];
  if(r.ok){ const got=r.candidates||[];
    candState[ky]={loading:false,sources:r.sources,source:r.source,candidates:base.concat(got),
                   error:null,lang,query,page:r.page||page,noMore:got.length===0};
  } else {
    candState[ky]=Object.assign({},prev,{loading:false,error:r.message||'取得失敗',
                   sources:r.sources||prev.sources||[],candidates:base,lang,query});
  }
  render();
}
// 取得ボタン（言語別）：パネルを開いてその言語で候補ロード。同言語で開いてたら閉じる。
function openCand(ci,k,cut,lang){ const ky=ci+'_'+k;
  const query = lang==='ja' ? (cut.image_query_ja||'') : (cut.image_query||'');
  if(!query){ alert(lang==='ja'?'日本語の検索語を入れてください':'英語の検索語を入れてください'); return; }
  candOpen.add(ky); loadCand(ci,k,cut,null,lang,query);  // 押したら常に再取得（前状態に戻さない・閉じるは×）
}
// 画像を縮小してJPEG dataURLにする（巨大画像の登録対策＝720p動画には十分）。crossOrigin対応URL/Fileを受ける。
function shrinkImage(src, maxDim, maxBytes){
  return new Promise((resolve,reject)=>{
    const img=new Image(); img.crossOrigin='anonymous';
    img.onload=()=>{ let w=img.naturalWidth,h=img.naturalHeight;
      const s=Math.min(1, maxDim/Math.max(w,h)); w=Math.max(1,Math.round(w*s)); h=Math.max(1,Math.round(h*s));
      const cv=document.createElement('canvas'); cv.width=w; cv.height=h;
      cv.getContext('2d').drawImage(img,0,0,w,h);
      try{ let q=0.88, u=cv.toDataURL('image/jpeg',q);
        while(u.length*0.75>maxBytes && q>0.4){ q-=0.1; u=cv.toDataURL('image/jpeg',q); }
        resolve(u);
      }catch(e){ reject(e); } };
    img.onerror=()=>reject(new Error('画像読み込み失敗'));
    img.src=src;
  });
}
// ── クリップボード(スクショ)から画像を取り込む ──
function blobToB64(blob){ return new Promise(res=>{ const rd=new FileReader(); rd.onload=()=>res(rd.result.split(',')[1]); rd.readAsDataURL(blob); }); }
async function importImageBlob(ky, ci, k, blob){
  let b64, fn='clipboard.png';
  if(blob.size > 8*1024*1024){
    try{ const durl=await shrinkImage(URL.createObjectURL(blob), 1920, 5*1024*1024); b64=durl.split(',')[1]; fn='clipboard.jpg'; }
    catch(e){ b64=await blobToB64(blob); }
  } else { b64=await blobToB64(blob); }
  const r=await api('/api/replace', {key:ky, filename:fn, dataB64:b64, attribution:''});
  if(r.ok){ cutMap[ky]=Object.assign({},cutMap[ky]||{ch:ci,ci:k},
    {image:r.filename,crop:null,filter:null,fit:null,pad:null,bg:null,hide:false}); render(); return true; }
  alert(r.message||'登録に失敗'); return false;
}
let pasteTarget=null;  // フォールバック貼り付け先
async function pasteClipboard(ky, ci, k){
  // ① Clipboard API で直接読む（Chrome等・localhost＋ユーザー操作下なら可）
  if(navigator.clipboard && navigator.clipboard.read){
    try{
      const items=await navigator.clipboard.read();
      for(const it of items){ const ty=(it.types||[]).find(t=>t.startsWith('image/'));
        if(ty){ const blob=await it.getType(ty); await importImageBlob(ky,ci,k,blob); return; } }
      alert('クリップボードに画像がありません（先に スクショ Cmd+Ctrl+Shift+4 を撮ってください）'); return;
    }catch(e){ /* 権限拒否/非対応 → ②フォールバックへ */ }
  }
  // ② フォールバック：貼り付け先を記憶し、Cmd+V を促す（全ブラウザ対応）
  pasteTarget={ky,ci,k};
  alert('このカットを貼り付け先にしました。そのまま Cmd+V を押してください。');
}
// ページ全体の paste（Cmd+V）で画像を取り込む。pasteTarget か、調整パネルが1つだけ開いてればそこへ。
document.addEventListener('paste', async (e)=>{
  const items=(e.clipboardData&&e.clipboardData.items)||[];
  for(const it of items){ if(it.type && it.type.startsWith('image/')){
    let tgt=pasteTarget;
    if(!tgt){ const open=[...adjustOpen]; if(open.length===1){ const [a,b]=open[0].split('_').map(Number); tgt={ky:open[0],ci:a,k:b}; } }
    if(tgt){ e.preventDefault(); const blob=it.getAsFile(); pasteTarget=null; await importImageBlob(tgt.ky,tgt.ci,tgt.k,blob); }
    else { alert('貼り付け先が未選択です。カットの『📋貼付』を押してから Cmd+V してください。'); }
    return;
  } }
});
// 候補を採用：まずサーバ取得、大きすぎる時だけ手元で縮小して登録。
async function applyCandidate(ci,k,cut,ky,c){
  const st=candState[ky]||{};
  const setImg=(fn)=>{ cutMap[ky]=Object.assign({},cutMap[ky]||{ch:ci,ci:k},
    {image:fn,attribution:c.attribution,query:(st.query!=null?st.query:cut.image_query),kind:cut.image_kind});
    candOpen.delete(ky); render(); };
  const r=await api('/api/import-url',{key:ky,url:c.url,attribution:c.attribution});
  if(r.ok){ setImg(r.filename); return; }
  if(/大き|15MB|too large/i.test(r.message||'')){
    try{ const durl=await shrinkImage(c.url, 1600, 3*1024*1024);
      const r2=await api('/api/replace',{key:ky,filename:'cand.jpg',dataB64:durl.split(',')[1],attribution:c.attribution});
      if(r2.ok){ setImg(r2.filename); return; }
      alert(r2.message||'縮小登録に失敗'); return;
    }catch(e){ alert('画像が大きく自動縮小もできませんでした（手元でリサイズしてD&Dしてください）'); return; }
  }
  alert(r.message||'採用失敗');
}
// 候補クリック：すぐ反映せず大きいサイズで確認させてから採用。
function previewCandidate(ci,k,cut,ky,c){
  const ov=document.createElement('div'); ov.className='cprev';
  const box=document.createElement('div'); box.className='cprevbox';
  const im=document.createElement('img'); im.src=c.url||c.thumb; box.appendChild(im);
  if(c.attribution){ const cap=document.createElement('div'); cap.className='cprevcap'; cap.textContent=c.attribution; box.appendChild(cap); }
  const row=document.createElement('div'); row.className='cprevrow';
  const ok=document.createElement('button'); ok.className='primary'; ok.textContent='これにする';
  ok.onclick=async()=>{ ok.disabled=true; ok.textContent='登録中…'; document.body.removeChild(ov); await applyCandidate(ci,k,cut,ky,c); };
  const ng=document.createElement('button'); ng.textContent='やめる'; ng.onclick=()=>{ if(ov.parentNode) document.body.removeChild(ov); };
  row.appendChild(ok); row.appendChild(ng); box.appendChild(row);
  ov.appendChild(box);
  ov.onclick=(e)=>{ if(e.target===ov && ov.parentNode) document.body.removeChild(ov); };
  document.body.appendChild(ov);
}
function buildCand(ci,k,cut){ const ky=ci+'_'+k; const st=candState[ky]||{};
  const wrap=document.createElement('div'); wrap.className='candpanel';
  const tabs=document.createElement('div'); tabs.className='ctabs';
  const ll=document.createElement('span'); ll.className='fll';
  ll.textContent='検索: '+(st.lang==='ja'?'日本語「'+(st.query||'')+'」':'英語')+' ／ 取得先：';
  tabs.appendChild(ll);
  (st.sources||[]).forEach(s=>{ const b=document.createElement('button');
    b.className='ctab'+(s.id===st.source?' on':''); b.textContent=s.label;
    b.onclick=()=>loadCand(ci,k,cut,s.id,st.lang,st.query); tabs.appendChild(b); });
  const cx=document.createElement('button'); cx.className='ctab'; cx.textContent='× 閉じる';
  cx.onclick=()=>{ candOpen.delete(ky); render(); }; tabs.appendChild(cx);
  wrap.appendChild(tabs);
  const has=(st.candidates||[]).length;
  if(st.loading && !has){ wrap.insertAdjacentHTML('beforeend','<div class="chint">検索中…</div>'); return wrap; }
  if(st.error && !has){ const d=document.createElement('div'); d.className='chint'; d.textContent=st.error; wrap.appendChild(d); return wrap; }
  if(!has){ wrap.insertAdjacentHTML('beforeend','<div class="chint">候補なし（検索語や取得先を変えてください）</div>'); return wrap; }
  const grid=document.createElement('div'); grid.className='cgrid';
  st.candidates.forEach(c=>{ const cell=document.createElement('div'); cell.className='ccell'; cell.title=c.attribution||'';
    const im=document.createElement('img'); im.src=c.thumb; im.loading='lazy'; cell.appendChild(im);
    const cap=document.createElement('div'); cap.className='ccap'; cap.textContent=c.attribution||''; cell.appendChild(cap);
    cell.title='クリックで大きく確認 → 採用';
    cell.onclick=()=>previewCandidate(ci,k,cut,ky,c);  // すぐ反映せず大きいサイズで確認
    grid.appendChild(cell); });
  wrap.appendChild(grid);
  // ページング：もっと見る / 読み込み中 / これ以上なし
  const foot=document.createElement('div'); foot.className='cfoot';
  if(st.loading){ foot.textContent='読み込み中…'; }
  else if(st.noMore){ foot.textContent='これ以上ありません'; }
  else { const mb=document.createElement('button'); mb.className='ctab'; mb.textContent='もっと見る（'+has+'枚表示中）';
    mb.onclick=()=>loadCand(ci,k,cut, st.source, st.lang, st.query, (st.page||1)+1, true); foot.appendChild(mb); }
  wrap.appendChild(foot); return wrap;
}
function cssFilter(f){ return f?`brightness(${f.brightness??1}) contrast(${f.contrast??1}) grayscale(${f.grayscale??0})`:''; }
function contentRect(img,box){ const nw=img.naturalWidth,nh=img.naturalHeight; if(!nw||!nh) return {x:0,y:0,w:box.width,h:box.height};
  const s=Math.min(box.width/nw,box.height/nh),w=nw*s,h=nh*s; return {x:(box.width-w)/2,y:(box.height-h)/2,w,h}; }
function mkrange(min,max,step,val){ const s=document.createElement('input'); s.type='range'; s.min=min; s.max=max; s.step=step; s.value=val; return s; }
// スライダーに −/＋ ボタンを付けて返す（ボタンは input/change を発火＝既存ハンドラを流用）。
function stepWrap(slider){
  const w=document.createElement('span'); w.style.cssText='display:inline-flex;align-items:center;gap:3px;vertical-align:middle';
  const mk=(txt,dir)=>{ const b=document.createElement('button'); b.type='button'; b.className='mini'; b.textContent=txt; b.tabIndex=-1;
    b.style.cssText='padding:2px 7px;font-size:14px;line-height:1';
    b.onclick=()=>{ const step=parseFloat(slider.step)||1, mn=parseFloat(slider.min), mx=parseFloat(slider.max);
      let v=(parseFloat(slider.value)||0)+dir*step;
      if(!isNaN(mn)) v=Math.max(mn,v); if(!isNaN(mx)) v=Math.min(mx,v);
      v=Math.round(v/step)*step; v=Math.round(v*1000)/1000;
      slider.value=v; slider.dispatchEvent(new Event('input')); slider.dispatchEvent(new Event('change')); };
    return b; };
  w.appendChild(mk('−',-1)); w.appendChild(slider); w.appendChild(mk('＋',1));
  return w;
}

function splitTurn(tn,ta){
  const text=tn.text||'';
  let pos=(ta&&ta.selectionStart)||0;
  if(!(pos>0&&pos<text.length)){
    // カーソル未指定：中央に最も近い句読点(、。！？)で割る。無ければ中央。
    // （句点が末尾だけのセリフでも空にならないよう「、」も区切り候補にする）
    const mid=text.length/2; let best=-1,bestD=1e9;
    for(let i=1;i<text.length;i++){ if('、。！？'.includes(text[i-1])){ const d=Math.abs(i-mid); if(d<bestD){bestD=d;best=i;} } }
    pos = best>0 ? best : Math.floor(text.length/2);
  }
  const a=text.slice(0,pos).trim(), b=text.slice(pos).trim();
  if(!a||!b){ alert('分割位置が不正'); return; }
  // 後半の新ターン。継続属性(話者/章/画像/感情等)のみ引き継ぎ、一点を指すflag(compare_item等)や
  // 尺(start/end/sentences)は引き継がない＝タイミングが二重にならないようにする。
  const nt={speaker:tn.speaker, text:b};
  ['chapter','section','cut','emotion','effect','voice'].forEach(k=>{ if(tn[k]!=null) nt[k]=tn[k]; });
  // 末尾に属する属性(後の無音/締め/範囲の終了)は後半へ移す（先頭からは外す）。
  ['pause','closing','chorus','viz_end'].forEach(k=>{ if(tn[k]!=null){ nt[k]=tn[k]; delete tn[k]; } });
  // 一点flag(compare_item/panel_item/panel_event/reveal/callout_item)とviz_startは先頭に残す。
  // 後半に何を出すかは下のタイミングchip（左/右(分割)など）で指定する。
  tn.text=a; ['start','end','sentences'].forEach(k=>delete tn[k]);
  DATA.script.splice(DATA.script.indexOf(tn)+1,0,nt); render();
}
function delTurn(tn){ const i=DATA.script.indexOf(tn); if(i>=0&&confirm('この発言を削除？')){ DATA.script.splice(i,1); render(); } }

// 画像のインライン調整パネル（fit/クロップ/補正/余白色/画像なし/差し替え/出典）。既存APIを使う。
function buildAdjust(ci,k){
  const key=ci+'_'+k;
  const cut=cutMap[key]||(cutMap[key]={ch:ci,ci:k});
  const wrap=document.createElement('div'); wrap.className='adjust';
  const crop=document.createElement('div'); crop.className='crop';
  const u=imgUrl(ci,k);
  crop.innerHTML=u?`<img src="${u}">`:'<div class="hint" style="display:flex;height:100%;align-items:center;justify-content:center">画像なし（取得 or 差し替え）</div>';
  const rectEl=document.createElement('div'); rectEl.className='croprect'; rectEl.style.display='none'; crop.appendChild(rectEl);
  const imgEl=crop.querySelector('img');
  const ctl=document.createElement('div'); ctl.className='ctl';

  // fit
  const fr=document.createElement('div'); fr.className='row'; fr.innerHTML='<span class="hint">収め方</span>';
  const fit=document.createElement('select');
  fit.innerHTML='<option value="">自動</option><option value="cover">cover(埋める)</option><option value="contain">contain(全体)</option>';
  fit.value=cut.fit||''; fit.onchange=()=>{ cut.fit=fit.value||null; setOpt(key,{fit:fit.value||null}); };
  fr.appendChild(fit);

  // filters
  const filt=document.createElement('div'); filt.className='filters';
  const fb=mkrange(0.3,1.5,0.05,(cut.filter&&cut.filter.brightness)||1);
  const fc=mkrange(0.5,1.5,0.05,(cut.filter&&cut.filter.contrast)||1);
  const fg=mkrange(0,1,0.05,(cut.filter&&cut.filter.grayscale)||0);
  filt.innerHTML='<span>明るさ</span>'; filt.appendChild(stepWrap(fb));
  filt.insertAdjacentHTML('beforeend','<span>コントラスト</span>'); filt.appendChild(stepWrap(fc));
  filt.insertAdjacentHTML('beforeend','<span>白黒</span>'); filt.appendChild(stepWrap(fg));
  const curFilter=()=>({brightness:+fb.value,contrast:+fc.value,grayscale:+fg.value});
  const liveFilter=()=>{ if(imgEl) imgEl.style.filter=cssFilter(curFilter()); };
  [fb,fc,fg].forEach(s=>{ s.oninput=liveFilter; s.onchange=()=>{ cut.filter=curFilter(); setOpt(key,{filter:curFilter()}); }; });
  if(imgEl) imgEl.style.filter=cssFilter(cut.filter);
  const fclr=document.createElement('button'); fclr.className='mini'; fclr.textContent='補正解除';
  fclr.onclick=()=>{ fb.value=1; fc.value=1; fg.value=0; cut.filter=null; if(imgEl) imgEl.style.filter=''; setOpt(key,{filter:null}); };

  // 余白(contain) + 画像なし
  const r2=document.createElement('div'); r2.className='row'; r2.innerHTML='<span class="hint">余白(px)</span>';
  const pad=document.createElement('input'); pad.type='number'; pad.min=0; pad.max=400; pad.step=4; pad.value=cut.pad||0; pad.style.width='62px';
  pad.title='contain時、画像の周りに空ける余白(px)'; pad.onchange=()=>{ const n=parseInt(pad.value)||0; cut.pad=n||null; setOpt(key,{pad:n}); render(); };
  const bg=document.createElement('input'); bg.type='color'; bg.value=cut.bg||'#eef1f5'; bg.title='余白の背景色';
  bg.onchange=()=>{ cut.bg=bg.value; setOpt(key,{bg:bg.value}); render(); };
  const bgc=document.createElement('button'); bgc.className='mini'; bgc.textContent='余白色クリア'; bgc.title='余白の背景色を消す＝透過（動画では背景が透けて見える）';
  bgc.onclick=()=>{ cut.bg=null; bg.value='#eef1f5'; setOpt(key,{bg:null}); render(); };
  const bgState=document.createElement('span'); bgState.className='hint';
  bgState.textContent=cut.bg?('色 '+cut.bg):'透過';  // 今の状態を明示
  const hideL=document.createElement('label'); hideL.className='chk'; hideL.title='中央画像を出さない（背景＋立ち絵だけ）';
  const hide=document.createElement('input'); hide.type='checkbox'; hide.checked=!!cut.hide;
  hide.onchange=()=>{ cut.hide=hide.checked; setOpt(key,{hide:hide.checked}); render(); };
  hideL.appendChild(hide); hideL.appendChild(document.createTextNode(' 画像なし'));
  r2.appendChild(pad); r2.appendChild(bg); r2.appendChild(bgc); r2.appendChild(bgState); r2.appendChild(hideL);

  // 出典・クレジット（ラベル付き・1行）
  const ar=document.createElement('div'); ar.className='row'; ar.innerHTML='<span class="hint">出典・クレジット</span>';
  const attr=document.createElement('input'); attr.type='text'; attr.placeholder='例: 作者名 / CC-BY 4.0（CC-BYは必須）'; attr.value=cut.attribution||'';
  attr.style.flex='1'; attr.style.height='34px';
  attr.onchange=()=>{ cut.attribution=attr.value; api('/api/attribution',{key,attribution:attr.value}); };
  ar.appendChild(attr);

  // 差し替え / クロップ解除
  const r3=document.createElement('div'); r3.className='row';
  const fileL=document.createElement('label'); fileL.className='mini'; fileL.style.cursor='pointer'; fileL.textContent='差し替え';
  const file=document.createElement('input'); file.type='file'; file.accept='image/*'; file.style.display='none'; fileL.appendChild(file);
  const onNew=async(fn)=>{ await refreshCuts(); render(); };  // 差し替え後はサーバ値(出典/調整リセット)で更新
  file.onchange=()=>{ const f=file.files[0]; if(!f)return; const rd=new FileReader();
    rd.onload=async()=>{ const r=await api('/api/replace',{key,filename:f.name,dataB64:rd.result.split(',')[1],attribution:''}); r.ok?onNew(r.filename):alert(r.message||'失敗'); };
    rd.readAsDataURL(f); };
  const cclr=document.createElement('button'); cclr.className='mini'; cclr.textContent='クロップ解除';
  cclr.onclick=()=>{ cut.crop=null; setOpt(key,{crop:null}); render(); };  // 行サムネのクロップも解除
  r3.appendChild(fileL); r3.appendChild(cclr); r3.appendChild(fclr);

  const hint=document.createElement('div'); hint.className='hint'; hint.textContent='画像をドラッグ＝クロップ / 画像をドロップ＝差し替え';
  ctl.appendChild(fr); ctl.appendChild(filt); ctl.appendChild(r2); ctl.appendChild(ar); ctl.appendChild(r3); ctl.appendChild(hint);
  wrap.appendChild(crop); wrap.appendChild(ctl);

  // クロップ枠描画＋ドラッグ
  function drawCrop(){ if(!cut.crop||!imgEl){ rectEl.style.display='none'; return; }
    const box=crop.getBoundingClientRect(), rr=contentRect(imgEl,box);
    rectEl.style.display='block'; rectEl.style.left=(rr.x+cut.crop.l*rr.w)+'px'; rectEl.style.top=(rr.y+cut.crop.t*rr.h)+'px';
    rectEl.style.width=((cut.crop.r-cut.crop.l)*rr.w)+'px'; rectEl.style.height=((cut.crop.b-cut.crop.t)*rr.h)+'px'; }
  if(imgEl){ imgEl.complete?drawCrop():(imgEl.onload=drawCrop);
    let drag=null;
    crop.onmousedown=(e)=>{ const box=crop.getBoundingClientRect(); drag={box,r:contentRect(imgEl,box),x0:e.clientX-box.left,y0:e.clientY-box.top}; };
    window.addEventListener('mousemove',(e)=>{ if(!drag)return; const x=e.clientX-drag.box.left,y=e.clientY-drag.box.top;
      rectEl.style.display='block'; rectEl.style.left=Math.min(drag.x0,x)+'px'; rectEl.style.top=Math.min(drag.y0,y)+'px';
      rectEl.style.width=Math.abs(x-drag.x0)+'px'; rectEl.style.height=Math.abs(y-drag.y0)+'px'; });
    crop.onmouseup=(e)=>{ if(!drag)return; const rr=drag.r,x=e.clientX-drag.box.left,y=e.clientY-drag.box.top;
      const nm=(px,py)=>[(px-rr.x)/rr.w,(py-rr.y)/rr.h], cl=v=>Math.max(0,Math.min(1,v));
      let [l,t]=nm(Math.min(drag.x0,x),Math.min(drag.y0,y)), [rr2,bb]=nm(Math.max(drag.x0,x),Math.max(drag.y0,y));
      const c={l:cl(l),t:cl(t),r:cl(rr2),b:cl(bb)}; drag=null;
      if(c.r-c.l<0.02||c.b-c.t<0.02){ drawCrop(); return; } cut.crop=c; setOpt(key,{crop:c}); render(); };  // render()で行サムネにもクロップ反映
    crop.addEventListener('dragover',e=>{e.preventDefault(); crop.style.outline='2px dashed #ffd84d';});
    crop.addEventListener('dragleave',()=>crop.style.outline='');
    crop.addEventListener('drop',async(e)=>{ e.preventDefault(); crop.style.outline='';
      const r=await dropImport(key, e.dataTransfer, '');  // 差し替え＝クレジット引き継がない（URLは出典URLが入る）
      if(r) r.ok?onNew(r.filename):alert(r.message||'失敗'); });
  }
  return wrap;
}

function sectionLabel(ch, ci){
  if(ch.section==='intro') return '導入';
  if(ch.section==='outro') return '締め';
  // 本編ビート通し番号（内部キーはtrivia流用）
  let n=0; for(let i=0;i<=ci;i++){ if((DATA.chapters[i].section)==='trivia') n++; }
  return 'ビート'+n;
}
function confLabel(c){ return {high:'確度 高', medium:'確度 中', low:'確度 低'}[c]||c; }

// ===== 画像エリアの演出（panel/quiz/compare/stat/callouts）のレビュー編集 =====
const VIZ_KEYS=['panel','quiz','compare','stat','callouts'];
const VIZ_LABEL={panel:'解説パネル',quiz:'クイズ',compare:'比較',stat:'数字強調',callouts:'注釈'};
function vizOf(ch){ return VIZ_KEYS.find(k=>ch[k]); }       // 章に付いている演出キー（無ければundefined）
function clearAllVizFlags(ci){ (DATA.script||[]).forEach(t=>{ if(t.chapter===ci){
  delete t.panel_event; delete t.panel_item; delete t.reveal; delete t.compare_item; delete t.callout_item;
  delete t.viz_start; delete t.viz_end; } }); }
// 章の演出表示範囲（viz_start/viz_endの発言・無ければ章の最初/最後の発言）の通し番号。
function vizRange(ci){ let first=-1,last=-1,sGi=-1,eGi=-1;
  (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter!==ci) return;
    if(first<0)first=gi; last=gi; if(t.viz_start)sGi=gi; if(t.viz_end)eGi=gi; });
  if(sGi<0)sGi=first; if(eGi<0||eGi<sGi)eGi=last;
  return {startGi:sGi, endGi:eGi, first, last}; }
function clampItemFlags(ci,flag,n){ (DATA.script||[]).forEach(t=>{
  if(t.chapter===ci && typeof t[flag]==='number' && t[flag]>=n) delete t[flag]; }); }
// 小道具
function vRow(labelText){ const r=document.createElement('div');
  r.style.cssText='display:flex;gap:6px;align-items:center;margin:4px 0;flex-wrap:wrap;';
  if(labelText){ const l=document.createElement('span'); l.style.cssText='font-size:12px;color:var(--sub);min-width:118px'; l.textContent=labelText; r.appendChild(l); } return r; }
function vText(val,ph,oninput){ const i=document.createElement('input'); i.type='text'; i.value=val||''; i.placeholder=ph||'';
  i.style.cssText='flex:1;min-width:160px'; i.oninput=()=>oninput(i.value); return i; }
function vMini(txt,onclick){ const b=document.createElement('button'); b.className='mini'; b.textContent=txt; b.onclick=onclick; return b; }
// 演出のタイミングflagをセリフ単位で付け外し（同一値は章内で1発言に限定＝重複を外す）。
function setUniqueFlag(ci,flag,value,tn,turnOn){
  (DATA.script||[]).forEach(t=>{ if(t.chapter===ci && t[flag]===value) delete t[flag]; });
  if(turnOn) tn[flag]=value;
}
let vizOpenGi=null;  // 中身エディタを開いている発言の通し番号（章単位の演出内容を編集）
let calloutSel=0;    // 注釈の「クリック配置」で動かす対象の注釈index
let calloutMode='point'; // クリックで動かす対象：'point'=指す点 / 'label'=文字の置き場
// この発言が章の演出イベントを担っているか（✎中身ボタンを出す判定）。
function turnHasViz(tn, ch){
  if(ch.panel) return tn.panel_event==='shrink'||typeof tn.panel_item==='number';
  if(ch.quiz||ch.stat) return tn.reveal===true;
  if(ch.compare) return tn.compare_item===0||tn.compare_item===1;
  if(ch.callouts) return typeof tn.callout_item==='number';
  return false;
}
// 演出が無い章で、この発言を起点に演出を追加するメニュー。
function vizAddMenu(ctrl, tn, ci){
  const sel=document.createElement('select'); sel.className='vchip'; sel.style.cursor='pointer';
  const o0=document.createElement('option'); o0.value=''; o0.textContent='＋演出'; sel.appendChild(o0);
  [['panel','パネル'],['quiz','クイズ'],['compare','比較'],['stat','数字'],['callouts','注釈']].forEach(([v,t])=>{
    const o=document.createElement('option'); o.value=v; o.textContent=t; sel.appendChild(o); });
  sel.onchange=()=>{ const ch=DATA.chapters[ci], v=sel.value; if(!v) return;
    VIZ_KEYS.forEach(k=>delete ch[k]); clearAllVizFlags(ci);
    if(v==='panel'){ ch.panel={items:[{text:''}]}; tn.panel_event='shrink'; }
    else if(v==='quiz'){ ch.quiz={question:'',answer:''}; tn.reveal=true; }
    else if(v==='compare'){ ch.compare={left:{label:'',cut:0},right:{label:'',cut:1}}; tn.compare_item=0; }
    else if(v==='stat'){ ch.stat={value:''}; tn.reveal=true; }
    else if(v==='callouts'){ ch.callouts=[{text:'',x:0.5,y:0.5}]; tn.callout_item=0; }
    tn.viz_start=true;  // この発言を演出の開始＝範囲の起点に
    vizOpenGi=DATA.script.indexOf(tn); render(); };
  ctrl.appendChild(sel);
}
// 演出の開始行に出すカード（種類・✎中身・削除）。範囲バーの先頭ラベル。
function vizHeaderCard(ch, ci, startGi){
  const h=document.createElement('div'); h.className='vizhdr';
  const t=document.createElement('span'); t.className='vizhdr-t'; t.textContent='▣ '+(VIZ_LABEL[vizOf(ch)]||'');
  const e=document.createElement('button'); e.type='button'; const eopen=(vizOpenGi===startGi);
  // 目立つ黄色ボタン（開いている時は枠線スタイルに切替＝閉じるトグルと分かるように）。
  e.style.cssText=eopen
    ? 'font-size:12px;font-weight:800;padding:4px 12px;border-radius:6px;border:2px solid #ffd84d;background:transparent;color:#ffd84d;cursor:pointer'
    : 'font-size:12px;font-weight:800;padding:5px 13px;border-radius:6px;border:none;background:#ffd84d;color:#1a1f2b;cursor:pointer;box-shadow:0 1px 6px rgba(255,216,77,.35)';
  e.textContent=eopen?'✕ 閉じる':'🎨 演出を編集'; e.onclick=()=>{ vizOpenGi=(eopen?null:startGi); render(); };
  const x=document.createElement('button'); x.type='button'; x.className='vchip'; x.textContent='✕削除';
  x.onclick=()=>{ VIZ_KEYS.forEach(k=>delete ch[k]); clearAllVizFlags(ci); vizOpenGi=null; render(); };
  h.appendChild(t); h.appendChild(e); h.appendChild(x); return h;
}
// フラグをセグメント内でユニークに設定（同一値は同セグメントの他行から外す＝重複防止）。
function setSegFlag(ci,segId,flag,value,tn,on){
  (DATA.script||[]).forEach(t=>{ if(t.chapter===ci && t.vizSeg===segId && t[flag]===value) delete t[flag]; });
  if(on) tn[flag]=value;
}
// 選択中セグメント(seg)の「この発言で何を出すか」タイミングchip。tnはこのセグメント内の発言。
function turnVizControl(tn, gi, seg, ci){
  const ctrl=document.createElement('div'); ctrl.className='vchips';
  if(!seg) return ctrl;
  const sid=seg.id;
  const chip=(label,flag,value,title)=>{ const on=tn[flag]===value;
    const b=document.createElement('button'); b.type='button'; b.className='vchip'+(on?' on':''); b.textContent=label; if(title)b.title=title;
    b.onclick=()=>{ setSegFlag(ci,sid,flag,value,tn,!on); render(); }; ctrl.appendChild(b); };
  if(seg.panel){
    const ov=!!seg.panel.overlay;
    if(!ov){ const son=tn.panel_event==='shrink';
      const sb=document.createElement('button'); sb.type='button'; sb.className='vchip'+(son?' on':''); sb.textContent='縮小'; sb.title='この発言で画像を縮小（パネルを開く）';
      sb.onclick=()=>{ setSegFlag(ci,sid,'panel_event','shrink',tn,!son);
        if(!son){ (DATA.script||[]).forEach((t,g)=>{ if(t.chapter===ci && t.vizSeg===sid && g<gi && typeof t.panel_item==='number') delete t.panel_item; }); }
        render(); }; ctrl.appendChild(sb); }
    let shrinkGi=null; (DATA.script||[]).forEach((t,g)=>{ if(t.chapter===ci && t.vizSeg===sid && t.panel_event==='shrink' && shrinkGi==null) shrinkGi=g; });
    (seg.panel.items||[]).forEach((it,k)=>{ const on=tn.panel_item===k; const dis=(!ov && shrinkGi!=null && gi<shrinkGi);
      const b=document.createElement('button'); b.type='button'; b.className='vchip'+(on?' on':'')+(dis?' dis':''); b.textContent='項目'+k;
      b.title=dis?'縮小より前には置けません（先に縮小を設定）':(it.text||'');
      b.onclick=()=>{ if(dis) return; setSegFlag(ci,sid,'panel_item',k,tn,!on); render(); }; ctrl.appendChild(b); });
  }
  else if(seg.quiz){ chip('答えを出す','reveal',true); }
  else if(seg.compare){ chip('左','compare_item',0); chip('右(分割)','compare_item',1); }
  else if(seg.stat){ chip('数字を出す','reveal',true); }
  else if(seg.callouts){ (seg.callouts||[]).forEach((c,k)=>chip('注'+k,'callout_item',k,c.text||'')); }
  return ctrl;
}
// 演出の中身エディタ（章単位の内容）。セリフ行の下にインライン展開する。
// 背景色＋不透明度の編集行（panel/quiz共用）。bgOpacity<1で裏(黒板/画像)が透ける。
// 背景は bg(色)が指定された時だけ上書きされる。色未指定で透明度だけ動かしても効かないため、
// スライダー操作時は色が無ければ既定色を補う（＝透明度＝背景の濃さとして直感的に効く）。
// defOp=未指定時のスライダー表示値（描画側の既定不透明度に合わせる）。
// bgKey/opKey=編集するフィールド名（既定 bg/bgOpacity。答えバナー等は別キーを渡す）。
function vBgRow(obj, label, defColor, defOp, bgKey, opKey){
  defOp=(defOp!=null?defOp:1); bgKey=bgKey||'bg'; opKey=opKey||'bgOpacity';
  const br=vRow(label);
  const cp=document.createElement('input'); cp.type='color'; cp.value=obj[bgKey]||defColor; cp.title='背景色';
  cp.oninput=()=>{ obj[bgKey]=cp.value; if(obj[opKey]==null)obj[opKey]=defOp; }; cp.onchange=()=>render();
  br.appendChild(cp);
  const op=document.createElement('input'); op.type='range'; op.min='0'; op.max='1'; op.step='0.05';
  op.value=(obj[opKey]!=null?obj[opKey]:defOp); op.style.cssText='width:96px;vertical-align:middle'; op.title='不透明度（左ほど透ける）';
  const ov=document.createElement('span'); ov.style.cssText='font-size:11px;color:var(--sub);min-width:36px;display:inline-block;text-align:right';
  const showOv=()=>{ ov.textContent=Math.round((obj[opKey]!=null?obj[opKey]:defOp)*100)+'%'; };
  showOv();
  op.oninput=()=>{ obj[opKey]=parseFloat(op.value); if(obj[bgKey]==null){ obj[bgKey]=cp.value||defColor; } showOv(); }; op.onchange=()=>render();
  br.appendChild(document.createTextNode(' 透過')); br.appendChild(stepWrap(op)); br.appendChild(ov);
  br.appendChild(vMini('既定に戻す',()=>{ delete obj[bgKey]; delete obj[opKey]; render(); }));
  return br;
}

// vh=演出ホルダ（旧:章ch / 新:セグメントseg。どちらも panel/quiz/… のキーを持つ）。
// ch=章（image_cuts等の参照用）。onDel=削除時の処理。
function vizContent(box, vh, ch, ci, onDel){
  const top=document.createElement('div'); top.style.cssText='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px';
  const tl=document.createElement('span'); tl.style.cssText='font-size:12px;font-weight:700;color:#c4a8ff'; tl.textContent='演出：'+(VIZ_LABEL[vizOf(vh)]||'');
  const del=document.createElement('button'); del.className='mini'; del.textContent='演出を削除';
  del.onclick=()=>{ if(onDel) onDel(); else { VIZ_KEYS.forEach(k=>delete vh[k]); clearAllVizFlags(ci); } render(); };
  top.appendChild(tl); top.appendChild(del); box.appendChild(top);
  const ncuts=(ch.image_cuts||[]).length;

  if(vh.panel){ const p=vh.panel; if(!Array.isArray(p.items))p.items=[];
    // ライブプレビュー（最終の縮小レイアウトを再現＝render(DialoguePanel)に合わせたHTMLモック）。
    const isPar=!p.items.some(it=>it.arrow_from_prev);
    const pmType=(p.markerType||'check'), pmSym=(pmType==='square'?'■':pmType==='dot'?'●':'✔');
    const pmColor=p.markerColor||'#ffd84d', pmSize=(p.markerSize!=null?p.markerSize:1);
    const ptColor=p.textColor||'#ffffff', ptSize=(p.textSize!=null?p.textSize:1);
    // 画像は選択行のcut。overlay=縮小なし(画像フル＋下部テロップ)。プレビューは選択行時点の段階を再現。
    const _st=DATA.script[selGi]; const pcut=(_st&&_st.chapter===ci&&typeof _st.cut==='number')?_st.cut:0;
    const u=imgUrl(ci,pcut); const overlay=!!p.overlay; const selInCh=_st&&_st.chapter===ci;
    let shrinkGi=null; const itemGi={};
    (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter!==ci) return;
      if(t.panel_event==='shrink'&&shrinkGi==null) shrinkGi=gi;
      if(typeof t.panel_item==='number'&&!(t.panel_item in itemGi)) itemGi[t.panel_item]=gi; });
    const opened = overlay ? true : (!selInCh ? true : (shrinkGi==null ? true : selGi>=shrinkGi));
    const itemShown=(k)=>{ if(!selInCh) return true; if(!overlay&&!opened) return false; return (k in itemGi)? selGi>=itemGi[k] : true; };
    const ppos=p.pos||'';            // 配置プリセット
    const onLeft=(ppos==='left');    // 縮小モードでテキストを左に開く（画像は右）
    const prev=document.createElement('div'); prev.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:#11151c;margin-bottom:6px';
    const imgSide=(!overlay&&opened);  // 横に縮小して開く
    const iw=document.createElement('div');
    // 画像位置：縮小時はテキストの逆側（既定=左/onLeft=右）。未縮小は全面。
    const iwAnchor=imgSide ? (onLeft?'right:0;':'left:0;')+'width:44%;margin:10px;border-radius:8px;' : 'left:0;right:0;';
    iw.style.cssText='position:absolute;top:0;bottom:0;'+iwAnchor+'overflow:hidden;background:rgba(255,255,255,.04);display:flex;align-items:center;justify-content:center';
    if(u){ const im=document.createElement('img'); im.src=u; im.style.cssText='width:100%;height:100%;object-fit:cover'; iw.appendChild(im); }
    else { const ph=document.createElement('span'); ph.style.cssText='color:var(--sub);font-size:11px'; ph.textContent='画像(各行のcut)'; iw.appendChild(ph); }
    prev.appendChild(iw);
    if(overlay || opened){
      const txt=document.createElement('div');
      // overlay帯の縦位置：上/中央/下(既定)。縮小モードは左右(既定=右)。
      const ovPos = ppos==='top' ? 'top:0;justify-content:flex-start;padding:10px 12px 8px'
                  : ppos==='center' ? 'top:28%;justify-content:center;padding:8px 12px'
                  : 'bottom:0;justify-content:flex-end;padding:8px 12px 10px';
      txt.style.cssText=overlay
        ? 'position:absolute;left:0;right:0;height:44%;display:flex;flex-direction:column;'+ovPos+';box-sizing:border-box;overflow:hidden'
        : 'position:absolute;'+(onLeft?'left:0;right:54%;':'left:46%;right:0;')+'top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;padding:8px 10px;box-sizing:border-box;overflow:hidden';
      if(p.bg){ const bgl=document.createElement('div'); bgl.style.cssText='position:absolute;inset:0;border-radius:8px;background:'+p.bg+';opacity:'+(p.bgOpacity!=null?p.bgOpacity:1); txt.appendChild(bgl); }
      if(p.heading){ const hd=document.createElement('div'); hd.style.cssText='position:relative;display:flex;align-items:center;gap:6px;margin-bottom:6px';
        const bar=document.createElement('span'); bar.style.cssText='width:5px;height:16px;background:#ffd84d;border-radius:2px;display:inline-block;flex:none';
        const ht=document.createElement('span'); ht.style.cssText='color:#fff;font-weight:900;font-size:'+pv(14)+'px'; ht.textContent=p.heading;
        hd.appendChild(bar); hd.appendChild(ht); txt.appendChild(hd); }
      p.items.forEach((it,i)=>{ if(!it.text||!itemShown(i)) return;
        const w=document.createElement('div'); w.style.cssText='position:relative;margin:2px 0';
        if(!isPar && it.arrow_from_prev && i>0){ const ar=document.createElement('div'); ar.style.cssText='color:rgba(255,255,255,.5);font-size:'+pv(11)+'px;line-height:1;margin:1px 0'; ar.textContent='▼'; w.appendChild(ar); }
        const row=document.createElement('div'); row.style.cssText='display:flex;align-items:center;gap:6px';
        if(isPar){ const ck=document.createElement('span'); ck.style.cssText='color:'+pmColor+';font-weight:900;font-size:'+pv(13*pmSize)+'px;flex:none'; ck.textContent=pmSym; row.appendChild(ck); }
        const chip=document.createElement('span'); chip.style.cssText='display:inline-block;background:rgba(20,26,38,.85);color:'+ptColor+';font-weight:800;font-size:'+pv(13*ptSize)+'px;padding:4px 9px;border-radius:7px;line-height:1.3'; chip.textContent=it.text; row.appendChild(chip);
        w.appendChild(row); txt.appendChild(w); });
      prev.appendChild(txt);
    }
    box.appendChild(prev);
    const note=document.createElement('div'); note.style.cssText='font-size:11px;color:var(--sub);margin:2px 0 4px';
    note.textContent='プレビューは選択行の時点を再現（縮小/項目の出方が連動）。画像は各行のcut・矢印で流れ(▼)/無しは並列(✔)。';
    box.appendChild(note);
    // 表示モード：縮小（横に開く）／縮小なし（画像フル＋下部テロップ）
    const mdr=vRow('表示');
    [['shrink','縮小して横に開く'],['overlay','縮小なし（テロップ）']].forEach(([v,t])=>{ const b=document.createElement('button'); b.type='button';
      const on=(v==='overlay')===(!!p.overlay); b.className='vchip'+(on?' on':''); b.textContent=t;
      b.onclick=()=>{ if(v==='overlay')p.overlay=true; else delete p.overlay; delete p.pos; render(); }; mdr.appendChild(b); });
    box.appendChild(mdr);
    // パネル位置プリセット。overlay=帯の縦位置(下/上/中央)、縮小=開く側(右/左)。既定はクリアで戻す。
    const por=vRow('位置');
    const popts = (!!p.overlay)
      ? [['','下(既定)'],['top','上'],['center','中央']]
      : [['','右(既定)'],['left','左']];
    popts.forEach(([v,t])=>{ const b=document.createElement('button'); b.type='button';
      const on=(p.pos||'')===v; b.className='vchip'+(on?' on':''); b.textContent=t;
      b.onclick=()=>{ if(v)p.pos=v; else delete p.pos; render(); }; por.appendChild(b); });
    box.appendChild(por);
    // 見出し（テキスト領域の上に出すお題。並列項目で特に有効）。
    const hr=vRow('見出し'); const hi=vText(p.heading,'例: マップ撮影の3つの方法（任意）',v=>{ if(v.trim())p.heading=v; else delete p.heading; }); hi.onchange=()=>render(); hr.appendChild(hi); box.appendChild(hr);
    // テキスト領域（縮小画像の横）の背景色＋不透明度。クリアで透過（黒板が見える）。
    box.appendChild(vBgRow(p,'文字側の背景','#1a2740'));
    // 色＋大きさ倍率の編集行ヘルパー（マーカー/テキスト共用）。
    const csRow=(label,colorKey,defColor,sizeKey)=>{ const r=vRow(label);
      const cp=document.createElement('input'); cp.type='color'; cp.value=p[colorKey]||defColor; cp.title='色';
      cp.oninput=()=>{ p[colorKey]=cp.value; }; cp.onchange=()=>render(); r.appendChild(document.createTextNode('色')); r.appendChild(cp);
      const sl=document.createElement('input'); sl.type='range'; sl.min='0.3'; sl.max='2'; sl.step='0.1';
      sl.value=(p[sizeKey]!=null?p[sizeKey]:1); sl.style.cssText='width:90px;vertical-align:middle'; sl.title='大きさ倍率';
      const sv=document.createElement('span'); sv.style.cssText='font-size:11px;color:var(--sub);min-width:34px;display:inline-block;text-align:right';
      const show=()=>{ sv.textContent=(p[sizeKey]!=null?p[sizeKey]:1).toFixed(1)+'倍'; }; show();
      sl.oninput=()=>{ p[sizeKey]=parseFloat(sl.value); show(); }; sl.onchange=()=>render();
      r.appendChild(document.createTextNode(' 大きさ')); r.appendChild(stepWrap(sl)); r.appendChild(sv);
      r.appendChild(vMini('既定',()=>{ delete p[colorKey]; delete p[sizeKey]; render(); })); return r; };
    // マーカー：記号(✔/■/●)＋色＋大きさ（並列項目のとき表示）。
    const mr=vRow('マーカー');
    [['check','✔'],['square','■'],['dot','●']].forEach(([v,sym])=>{ const b=document.createElement('button'); b.type='button';
      b.className='vchip'+((p.markerType||'check')===v?' on':''); b.textContent=sym; b.style.fontSize='14px';
      b.onclick=()=>{ if(v==='check') delete p.markerType; else p.markerType=v; render(); }; mr.appendChild(b); });
    box.appendChild(mr);
    box.appendChild(csRow('マーカー色/大','markerColor','#ffd84d','markerSize'));
    box.appendChild(csRow('テキスト色/大','textColor','#ffffff','textSize'));
    p.items.forEach((it,i)=>{
      const r=vRow('項目'+i);
      const ti=vText(it.text,'体言止め10字以内',v=>it.text=v); ti.onchange=()=>render(); r.appendChild(ti);
      if(i>0){ const al=document.createElement('label'); al.style.cssText='font-size:12px;display:inline-flex;gap:3px;align-items:center';
        const ac=document.createElement('input'); ac.type='checkbox'; ac.checked=!!it.arrow_from_prev;
        ac.onchange=()=>{ if(ac.checked)it.arrow_from_prev=true; else delete it.arrow_from_prev; render(); };
        al.appendChild(ac); al.appendChild(document.createTextNode('矢印')); r.appendChild(al); }
      r.appendChild(vMini('削除',()=>{ p.items.splice(i,1); clampItemFlags(ci,'panel_item',p.items.length); render(); }));
      box.appendChild(r);
    });
    box.appendChild(vMini('＋項目',()=>{ p.items.push({text:''}); render(); }));
  }
  else if(vh.quiz){ const q=vh.quiz;
    // ライブプレビュー（背後の画像＋「？・問い」土台＋答えバナー。動画では問い→答えに切替）。
    // クイズは自前の画像を持たず通常画像の上に重なる＝背後は「選択行のcut」（本番と一致させる）。
    const _qsel=DATA.script[selGi]; const qcut=(_qsel&&_qsel.chapter===ci&&typeof _qsel.cut==='number')?_qsel.cut:0;
    const u=imgUrl(ci,qcut); const co0=cutMap[ci+'_'+qcut]||{}; const cut0=(ch.image_cuts&&ch.image_cuts[qcut])||{};
    const pfit=co0.fit||(cut0.image_kind==='subject'?'contain':'cover'); const pbg=(pfit==='contain'?(co0.bg||'#1a2230'):'#222');
    // reveal（答えを出す）行で問い→答えに切替。プレビューも選択行時点を再現。
    const _qst=DATA.script[selGi]; const qSel=_qst&&_qst.chapter===ci; let revGi=null;
    (DATA.script||[]).forEach((t,g)=>{ if(t.chapter===ci&&t.reveal===true&&revGi==null) revGi=g; });
    const revealed = !qSel ? true : (revGi==null ? true : selGi>=revGi);
    const qbw=q.boxWidth?(Math.round(q.boxWidth*100)+'%'):null;  // 枠幅（任意）
    const prev=document.createElement('div'); prev.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:'+pbg+';margin-bottom:6px';
    if(u){ const im=document.createElement('img'); im.src=u; im.style.cssText='position:absolute;inset:0;width:100%;height:100%;object-fit:'+pfit; prev.appendChild(im); }
    // ？・問い 土台パネル（中央）
    const pnl=document.createElement('div'); pnl.style.cssText='position:absolute;left:50%;top:42%;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;gap:4px;border-radius:12px;padding:10px 18px;box-sizing:border-box;'+(qbw?('width:'+qbw+';max-width:96%'):'max-width:86%');
    const pbgl=document.createElement('div'); pbgl.style.cssText='position:absolute;inset:0;border-radius:12px;background:'+(q.bg||'#0f141e')+';opacity:'+(q.bgOpacity!=null?q.bgOpacity:0.62); pnl.appendChild(pbgl);
    const qm=document.createElement('div'); qm.style.cssText='position:relative;color:#ffd84d;font-weight:900;font-size:'+pv(34)+'px;line-height:1'; qm.textContent='？'; pnl.appendChild(qm);
    const qt=document.createElement('div'); qt.style.cssText='position:relative;color:'+(q.textColor||'#ffffff')+';font-weight:800;font-size:'+pv(14)+'px;text-align:center'; qt.textContent=q.question||'問い'; pnl.appendChild(qt);
    if(!revealed) prev.appendChild(pnl);   // 答え前＝問いだけ
    // 答えバナー（下部）
    const ans=document.createElement('div'); ans.style.cssText='position:absolute;left:50%;bottom:8%;transform:translateX(-50%);border-radius:9px;padding:5px 14px;box-sizing:border-box;text-align:center;box-shadow:0 3px 10px rgba(0,0,0,.4);'+(qbw?('width:'+qbw+';max-width:96%'):'max-width:90%');
    const abgl=document.createElement('div'); abgl.style.cssText='position:absolute;inset:0;border-radius:9px;background:'+(q.answerBg||'#ffd84d')+';opacity:'+(q.answerBgOpacity!=null?q.answerBgOpacity:0.96); ans.appendChild(abgl);
    const at=document.createElement('div'); at.style.cssText='position:relative;color:'+(q.answerTextColor||'#1a1f2b')+';font-weight:900;font-size:'+pv(16)+'px;text-align:center'; at.textContent=q.answer||'答え'; ans.appendChild(at);
    if(revealed) prev.appendChild(ans);   // 答え以降＝答えバナー
    // リビール後：問いは消えず上部の章バッジ枠へ昇格（本番=「実はN＋問題」）。プレビューも上バーで再現。
    if(revealed){ const qb=document.createElement('div'); qb.style.cssText='position:absolute;left:3%;top:5%;display:flex;align-items:stretch;border-radius:5px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.5);max-width:86%';
      const acc=document.createElement('div'); acc.style.cssText='width:4px;background:#ffd84d;flex:none'; qb.appendChild(acc);
      const qbody=document.createElement('div'); qbody.style.cssText='display:flex;align-items:center;gap:6px;background:rgba(18,30,58,.9);padding:'+pv(4)+'px '+pv(9)+'px';
      const lab=document.createElement('span'); lab.style.cssText='color:#ffd84d;font-weight:800;font-size:'+pv(12)+'px;flex:none'; lab.textContent='Q.'; qbody.appendChild(lab);
      const qtx=document.createElement('span'); qtx.style.cssText='color:#fff;font-weight:800;font-size:'+pv(12)+'px;line-height:1.2'; qtx.textContent=q.question||'問い'; qbody.appendChild(qtx);
      qb.appendChild(qbody); prev.appendChild(qb); }
    box.appendChild(prev);
    const r1=vRow('問い'); const qi=vText(q.question,'画面に出す問い',v=>q.question=v); qi.onchange=()=>render(); r1.appendChild(qi); box.appendChild(r1);
    const r2=vRow('答え'); const ai=vText(q.answer,'リビールで出す答え',v=>q.answer=v); ai.onchange=()=>render(); r2.appendChild(ai); box.appendChild(r2);
    // 色の編集行ヘルパー（文字色など）。
    const colRow=(label,key,defColor)=>{ const r=vRow(label); const cp=document.createElement('input'); cp.type='color'; cp.value=q[key]||defColor; cp.title='色';
      cp.oninput=()=>{ q[key]=cp.value; }; cp.onchange=()=>render(); r.appendChild(cp);
      r.appendChild(vMini('既定',()=>{ delete q[key]; render(); })); return r; };
    const nt=document.createElement('div'); nt.style.cssText='font-size:11px;color:var(--sub);margin:2px 0';
    nt.textContent='プレビューは選択行を再現：「答えを出す」行より前＝中央に問い／以降＝問いは上部バー(Q.)へ縮小＋答えバナー。画像は背後にそのまま（暗転しない）。';
    box.appendChild(nt);
    box.appendChild(vBgRow(q,'文字の背景','#0f141e',0.62));
    box.appendChild(colRow('問いの文字色','textColor','#ffffff'));
    box.appendChild(vBgRow(q,'答えの背景','#ffd84d',0.96,'answerBg','answerBgOpacity'));
    box.appendChild(colRow('答えの文字色','answerTextColor','#1a1f2b'));
    // ボックス幅（問い土台/答えバナーの横幅・画面比）。未指定=内容に応じ自動。
    const wr=vRow('ボックス幅'); const wsl=document.createElement('input'); wsl.type='range'; wsl.min='0.3'; wsl.max='1'; wsl.step='0.05';
    wsl.value=(q.boxWidth!=null?q.boxWidth:0.8); wsl.style.cssText='width:120px;vertical-align:middle'; wsl.title='問い土台/答えバナーの横幅（画面比）';
    const wsv=document.createElement('span'); wsv.style.cssText='font-size:11px;color:var(--sub);min-width:40px;display:inline-block;text-align:right';
    const wshow=()=>{ wsv.textContent=(q.boxWidth!=null?Math.round(q.boxWidth*100)+'%':'自動'); }; wshow();
    wsl.oninput=()=>{ q.boxWidth=parseFloat(wsl.value); wshow(); }; wsl.onchange=()=>render();
    wr.appendChild(stepWrap(wsl)); wr.appendChild(wsv); wr.appendChild(vMini('自動',()=>{ delete q.boxWidth; render(); })); box.appendChild(wr);
  }
  else if(vh.compare){ const c=vh.compare; c.left=c.left||{label:'',cut:0}; c.right=c.right||{label:'',cut:1};
    // ライブプレビュー（左右2分割の最終分割状態＝render(CompareVisual)に合わせたモック）。
    const lblBg=c.labelColor||'rgba(20,26,38,.82)', lblTx=c.labelTextColor||'#ffffff';
    const lblSz=(c.labelSize!=null?c.labelSize:1), divC=c.dividerColor||'rgba(255,255,255,.85)';
    // 分割タイミング連動：compare_item 0=左(at0)/1=右(at1で分割)。選択行時点を再現。
    const _cst=DATA.script[selGi]; const cSel=_cst&&_cst.chapter===ci; let a0=null,a1=null;
    (DATA.script||[]).forEach((t,g)=>{ if(t.chapter!==ci) return; if(t.compare_item===0&&a0==null)a0=g; if(t.compare_item===1&&a1==null)a1=g; });
    const leftShown = !cSel ? true : (a0==null || selGi>=a0);
    const split = !cSel ? true : (a1!=null ? selGi>=a1 : (a0==null || selGi>=a0));
    const half=(side,def,w)=>{ const u=imgUrl(ci,(side.cut!=null?side.cut:def));
      const h=document.createElement('div'); h.style.cssText='position:relative;width:'+w+';height:100%;overflow:hidden;background:linear-gradient(135deg,#324a5f,#25323f)';
      if(u){ const im=document.createElement('img'); im.src=u; im.style.cssText='width:100%;height:100%;object-fit:cover'; h.appendChild(im); }
      const lb=document.createElement('div'); lb.style.cssText='position:absolute;left:0;right:0;bottom:0;background:'+lblBg+';color:'+lblTx+';font-weight:800;font-size:'+pv(13*lblSz)+'px;text-align:center;padding:4px 4px'; lb.textContent=side.label||''; h.appendChild(lb);
      return h; };
    const prev=document.createElement('div'); prev.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:#222;margin-bottom:6px;display:flex';
    if(!leftShown){ /* 演出開始前＝何も出さない */ }
    else if(!split){ prev.appendChild(half(c.left,0,'100%')); }   // 左フル（分割前）
    else { prev.appendChild(half(c.left,0,'50%')); prev.appendChild(half(c.right,1,'50%'));
      const dv=document.createElement('div'); dv.style.cssText='position:absolute;top:0;bottom:0;left:50%;width:3px;transform:translateX(-1.5px);background:'+divC; prev.appendChild(dv); }
    box.appendChild(prev);
    // 画像はサムネで選ぶ（台本のcut選択と統一）。
    const mkCut=(side,def)=>{ const cur=(side.cut??def); const pick=document.createElement('div'); pick.className='cutpick';
      const n=Math.max(ncuts,2);
      for(let k=0;k<n;k++){ const u=imgUrl(ci,k);
        const o=document.createElement('div'); o.className='copt'+(k===cur?' sel':''); o.title='画像'+k;
        o.innerHTML=(u?'<img src="'+u+'">':'<span class="ph3">#'+k+'</span>')+'<span class="num">'+k+'</span>';
        o.onclick=()=>{ side.cut=k; render(); };  // 再描画＝セリフ行の「演出で表示」サムネも更新
        pick.appendChild(o); }
      return pick; };
    const rl=vRow('左'); const li=vText(c.left.label,'左ラベル',v=>c.left.label=v); li.onchange=()=>render(); rl.appendChild(li); rl.appendChild(mkCut(c.left,0)); box.appendChild(rl);
    const rr=vRow('右'); const ri=vText(c.right.label,'右ラベル',v=>c.right.label=v); ri.onchange=()=>render(); rr.appendChild(ri); rr.appendChild(mkCut(c.right,1)); box.appendChild(rr);
    // ラベル/分割線の見た目。色の編集行ヘルパー。
    const colRow=(label,key,defColor,extra)=>{ const r=vRow(label); const cp=document.createElement('input'); cp.type='color'; cp.value=c[key]||defColor; cp.title='色';
      cp.oninput=()=>{ c[key]=cp.value; }; cp.onchange=()=>render(); r.appendChild(cp);
      if(extra) extra(r);
      r.appendChild(vMini('既定',()=>{ delete c[key]; render(); })); return r; };
    box.appendChild(colRow('ラベル背景','labelColor','#141a26'));
    box.appendChild(colRow('ラベル文字','labelTextColor','#ffffff',(r)=>{
      // ラベル大きさスライダーも同じ行に。
      const sl=document.createElement('input'); sl.type='range'; sl.min='0.3'; sl.max='2'; sl.step='0.1';
      sl.value=(c.labelSize!=null?c.labelSize:1); sl.style.cssText='width:90px;vertical-align:middle'; sl.title='ラベル大きさ倍率';
      const sv=document.createElement('span'); sv.style.cssText='font-size:11px;color:var(--sub);min-width:34px;display:inline-block;text-align:right';
      const show=()=>{ sv.textContent=(c.labelSize!=null?c.labelSize:1).toFixed(1)+'倍'; }; show();
      sl.oninput=()=>{ c.labelSize=parseFloat(sl.value); show(); }; sl.onchange=()=>render();
      r.appendChild(document.createTextNode(' 大きさ')); r.appendChild(stepWrap(sl)); r.appendChild(sv); }));
    box.appendChild(colRow('分割線','dividerColor','#ffffff'));
  }
  else if(vh.stat){ const s=vh.stat;
    // ライブプレビュー（画像の上に数字カードを重ねる＝render(StatOverlay)に合わせたモック）。
    const sz=(s.size!=null?s.size:1);
    const u=imgUrl(ci,0); const co0=cutMap[ci+'_0']||{}; const cut0=(ch.image_cuts&&ch.image_cuts[0])||{};
    const pfit=co0.fit||(cut0.image_kind==='subject'?'contain':'cover'); const pbg=(pfit==='contain'?(co0.bg||'#1a2230'):'#222');
    const prev=document.createElement('div'); prev.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:'+pbg+';margin-bottom:6px;display:flex;align-items:center;justify-content:center';
    if(u){ const im=document.createElement('img'); im.src=u; im.style.cssText='position:absolute;inset:0;width:100%;height:100%;object-fit:'+pfit; prev.appendChild(im); }
    // reveal（数字を出す）行で出現。選択行が前なら数字カードを出さない。
    const _sst=DATA.script[selGi]; const sSel=_sst&&_sst.chapter===ci; let sRev=null;
    (DATA.script||[]).forEach((t,g)=>{ if(t.chapter===ci&&t.reveal===true&&sRev==null) sRev=g; });
    const statShown = !sSel ? true : (sRev==null ? true : selGi>=sRev);
    if(statShown){
    const card=document.createElement('div'); card.style.cssText='position:relative;display:flex;flex-direction:column;align-items:center;border-radius:'+Math.round(14*sz)+'px;padding:'+Math.round(8*sz)+'px '+Math.round(18*sz)+'px';
    const bgl=document.createElement('div'); bgl.style.cssText='position:absolute;inset:0;border-radius:'+Math.round(14*sz)+'px;background:'+(s.bg||'#0f141e')+';opacity:'+(s.bgOpacity!=null?s.bgOpacity:0.5); card.appendChild(bgl);
    if(s.label){ const lb=document.createElement('div'); lb.style.cssText='position:relative;color:rgba(255,255,255,.85);font-weight:700;font-size:'+pv(13*sz)+'px;margin-bottom:'+Math.round(3*sz)+'px'; lb.textContent=s.label; card.appendChild(lb); }
    const vr=document.createElement('div'); vr.style.cssText='position:relative;display:flex;align-items:baseline;gap:4px;line-height:1';
    const vv=document.createElement('span'); vv.style.cssText='color:'+(s.color||'#ffd84d')+';font-weight:900;font-size:'+pv(44*sz)+'px;text-shadow:0 2px 8px rgba(0,0,0,.55)'; vv.textContent=(s.value||'0'); vr.appendChild(vv);
    if(s.unit){ const us=document.createElement('span'); us.style.cssText='color:#fff;font-weight:900;font-size:'+pv(20*sz)+'px'; us.textContent=s.unit; vr.appendChild(us); }
    card.appendChild(vr); prev.appendChild(card);
    }
    box.appendChild(prev);
    const r=vRow('数字'); const vi=vText(s.value,'例 8 / 50万 / 500000',v=>s.value=v); vi.onchange=()=>render(); r.appendChild(vi);
    const u2=document.createElement('input'); u2.type='text'; u2.value=s.unit||''; u2.placeholder='単位'; u2.style.width='90px';
    u2.oninput=()=>{ if(u2.value.trim())s.unit=u2.value; else delete s.unit; }; u2.onchange=()=>render(); r.appendChild(u2); box.appendChild(r);
    const r2=vRow('ラベル'); const li=vText(s.label,'例 故障率（任意）',v=>{ if(v.trim())s.label=v; else delete s.label; }); li.onchange=()=>render(); r2.appendChild(li); box.appendChild(r2);
    // 強調色（数字の色）
    const rc=vRow('強調色'); const cp=document.createElement('input'); cp.type='color'; cp.value=s.color||'#ffd84d'; cp.title='数字の色';
    cp.oninput=()=>{ s.color=cp.value; }; cp.onchange=()=>render(); rc.appendChild(cp);
    rc.appendChild(vMini('既定に戻す',()=>{ delete s.color; render(); })); box.appendChild(rc);
    // 大きさ（全体の倍率）
    const rs=vRow('大きさ'); const sl=document.createElement('input'); sl.type='range'; sl.min='0.3'; sl.max='2'; sl.step='0.1';
    sl.value=(s.size!=null?s.size:1); sl.style.cssText='width:120px;vertical-align:middle'; sl.title='数字演出の大きさ倍率';
    const sv=document.createElement('span'); sv.style.cssText='font-size:11px;color:var(--sub);min-width:36px;display:inline-block;text-align:right';
    const showSv=()=>{ sv.textContent=(s.size!=null?s.size:1).toFixed(1)+'倍'; }; showSv();
    sl.oninput=()=>{ s.size=parseFloat(sl.value); showSv(); }; sl.onchange=()=>render();
    rs.appendChild(stepWrap(sl)); rs.appendChild(sv); rs.appendChild(vMini('既定に戻す',()=>{ delete s.size; render(); })); box.appendChild(rs);
    // 背景色＋透過（土台）
    box.appendChild(vBgRow(s,'背景','#0f141e',0.5));
    // カウントアップ速度（数字が整数のとき有効）。3段階＝速い/標準(既定)/ゆっくり。
    const rsp=vRow('カウント速度');
    [['fast','速い'],['normal','標準'],['slow','ゆっくり']].forEach(([v,t])=>{
      const cur=(s.countSpeed||'normal');
      const b=document.createElement('button'); b.type='button'; b.className='vchip'+(cur===v?' on':''); b.textContent=t;
      b.onclick=()=>{ if(v==='normal') delete s.countSpeed; else s.countSpeed=v; render(); }; rsp.appendChild(b);
    });
    box.appendChild(rsp);
  }
  else if(vh.callouts){ let cs=vh.callouts; if(!Array.isArray(cs)){cs=vh.callouts=[];}
    if(calloutSel>=cs.length) calloutSel=0;
    const st=ch.calloutStyle||(ch.calloutStyle={});
    const mColor=st.markerColor||'#ff5a6a', lColor=st.labelColor||'#14233a';
    const lText=st.labelTextColor||'#ffffff', lBorder=st.labelBorderColor||'';
    const mSize=(st.markerSize!=null?st.markerSize:1), lSize=(st.labelSize!=null?st.labelSize:1);
    const aSize=(st.arrowSize!=null?st.arrowSize:1), aShape=(st.arrowShape||'normal');
    const aDot=(aShape==='dot');
    const aBase=(aShape==='sharp'?{len:16,half:6,shaft:3}:aShape==='thick'?{len:13,half:11,shaft:5}:aDot?{len:8,half:8,shaft:3}:{len:14,half:8,shaft:4});
    const aHL=pv(aBase.len*aSize), aHW=pv(aBase.half*aSize*2), aSH=Math.max(2,pv(aBase.shaft*aSize));
    const aR=pv(aBase.half*aSize); // dot半径
    // 自動ラベル位置（lx/ly未指定時）：点の上/下に正規化0.1*aSizeずらす（renderと共通）。
    const gapN=0.1*aSize;
    const lpos=(c)=>({x:(c.lx!=null?c.lx:c.x), y:(c.ly!=null?c.ly:(c.y<0.25?c.y+gapN:c.y-gapN))});
    // callout_item（各注釈を出す行）連動：選択行で未到達の注釈は薄く表示（配置はできる）。
    const _cst=DATA.script[selGi]; const cSel=_cst&&_cst.chapter===ci; const coGi={};
    (DATA.script||[]).forEach((t,g)=>{ if(t.chapter===ci && typeof t.callout_item==='number' && !(t.callout_item in coGi)) coGi[t.callout_item]=g; });
    const reached=(k)=> !cSel ? true : (coGi[k]==null ? true : selGi>=coGi[k]);
    // 配置モード切替（点 / 文字）。
    const mrow=document.createElement('div'); mrow.style.cssText='display:flex;gap:6px;align-items:center;margin-bottom:4px';
    const ml=document.createElement('span'); ml.style.cssText='font-size:12px;color:var(--sub)'; ml.textContent='クリックで動かす：'; mrow.appendChild(ml);
    [['point','◯ 点'],['label','文字']].forEach(([v,t])=>{ const b=document.createElement('button'); b.type='button';
      b.className='vchip'+(calloutMode===v?' on':''); b.textContent=t; b.onclick=()=>{ calloutMode=v; render(); }; mrow.appendChild(b); });
    box.appendChild(mrow);
    // クリック配置プレビュー：章の画像(cut0)を出し、クリックで選択中注釈の点/文字位置を設定。
    // render と座標を合わせるため、画像の収め方(fit)を build と同じに揃える
    // （subject=contain / それ以外=cover。レビューでfit上書きがあれば優先）。
    const u=imgUrl(ci,0);
    const co0=cutMap[ci+'_0']||{}; const cut0=(ch.image_cuts&&ch.image_cuts[0])||{};
    const pfit=co0.fit||(cut0.image_kind==='subject'?'contain':'cover');
    const pbg=(pfit==='contain'?(co0.bg||'#1a2230'):'#222');
    const prev=document.createElement('div'); prev.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:'+pbg+';cursor:crosshair;margin-bottom:6px;user-select:none';
    if(u){ const im=document.createElement('img'); im.src=u; im.style.cssText='width:100%;height:100%;object-fit:'+pfit+';pointer-events:none'; prev.appendChild(im); }
    else { const ph=document.createElement('div'); ph.style.cssText='display:flex;height:100%;align-items:center;justify-content:center;color:var(--sub);font-size:12px'; ph.textContent='画像なし（cut0を取得してください）'; prev.appendChild(ph); }
    // 矢印（SVG・arrowのみ）。文字位置→点へ・先端に矢じり(marker)。
    const svgns='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(svgns,'svg'); svg.setAttribute('style','position:absolute;inset:0;width:100%;height:100%;pointer-events:none');
    // 矢じりマーカー（userSpaceOnUse＝px指定／orient=auto で向き自動）。
    const defs=document.createElementNS(svgns,'defs'); const mk=document.createElementNS(svgns,'marker');
    mk.setAttribute('id','cah'); mk.setAttribute('markerUnits','userSpaceOnUse'); mk.setAttribute('orient','auto');
    if(aDot){ const d=aR*2+4;
      mk.setAttribute('markerWidth',String(d)); mk.setAttribute('markerHeight',String(d)); mk.setAttribute('refX',String(d/2)); mk.setAttribute('refY',String(d/2));
      const ci2=document.createElementNS(svgns,'circle'); ci2.setAttribute('cx',String(d/2)); ci2.setAttribute('cy',String(d/2)); ci2.setAttribute('r',String(aR)); ci2.setAttribute('fill',mColor); ci2.setAttribute('stroke','#fff'); ci2.setAttribute('stroke-width','2');
      mk.appendChild(ci2);
    } else {
      mk.setAttribute('markerWidth',String(aHL)); mk.setAttribute('markerHeight',String(aHW)); mk.setAttribute('refX',String(aHL)); mk.setAttribute('refY',String(aHW/2));
      const tri=document.createElementNS(svgns,'path'); tri.setAttribute('d','M0,0 L'+aHL+','+(aHW/2)+' L0,'+aHW+' Z'); tri.setAttribute('fill',mColor);
      mk.appendChild(tri);
    }
    defs.appendChild(mk); svg.appendChild(defs);
    cs.forEach((c,i)=>{ if(!c.arrow) return; const L=lpos(c);
      const ln=document.createElementNS(svgns,'line'); ln.setAttribute('x1',(L.x*100)+'%'); ln.setAttribute('y1',(L.y*100)+'%');
      ln.setAttribute('x2',(c.x*100)+'%'); ln.setAttribute('y2',(c.y*100)+'%'); ln.setAttribute('stroke',mColor); ln.setAttribute('stroke-width',String(aSH)); ln.setAttribute('marker-end','url(#cah)');
      if(!reached(i)) ln.setAttribute('opacity','0.28'); svg.appendChild(ln); });
    prev.appendChild(svg);
    cs.forEach((c,i)=>{ const sel=(i===calloutSel);
      // 点マーカー（矢印OFFのときだけ。ON時は矢じり/ドットが点を示す＝renderと一致）。
      // ただし配置中は点の位置が見えるよう、選択中＆pointモードのときは薄く出す。
      const dim=(!reached(i)?';opacity:.28':'');
      if(!c.arrow || (sel&&calloutMode==='point')){
        const m=document.createElement('div'); const md=pv((sel?9:8)*mSize); const ghost=(c.arrow&&sel);
        m.style.cssText='position:absolute;width:'+md+'px;height:'+md+'px;border-radius:50%;background:'+mColor+';border:2px solid #fff;transform:translate(-50%,-50%);box-shadow:0 0 0 2px rgba(0,0,0,.4)'+(ghost?';opacity:.4':dim)+(sel&&calloutMode==='point'?';outline:2px solid #ffd84d;outline-offset:2px':'');
        m.style.left=(c.x*100)+'%'; m.style.top=(c.y*100)+'%'; m.title='注釈'+i+'の点'; prev.appendChild(m);
      }
      // 文字ラベル（プレビュー・大きさ＝labelSize連動）
      const L=lpos(c); const lab=document.createElement('div');
      lab.style.cssText='position:absolute;transform:translate(-50%,-50%);white-space:nowrap;background:'+lColor+';color:'+lText+';font-weight:800;font-size:'+pv(12*lSize)+'px;padding:3px 7px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.4)'+(lBorder?';border:2px solid '+lBorder:'')+dim+(sel&&calloutMode==='label'?';outline:2px solid #ffd84d;outline-offset:2px':'');
      lab.style.left=(L.x*100)+'%'; lab.style.top=(L.y*100)+'%'; lab.textContent=c.text||('注釈'+i); prev.appendChild(lab); });
    prev.onclick=(e)=>{ if(!cs.length) return; const r=prev.getBoundingClientRect();
      const x=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)), y=Math.max(0,Math.min(1,(e.clientY-r.top)/r.height));
      const rx=Math.round(x*1000)/1000, ry=Math.round(y*1000)/1000;
      if(calloutMode==='label'){ cs[calloutSel].lx=rx; cs[calloutSel].ly=ry; } else { cs[calloutSel].x=rx; cs[calloutSel].y=ry; }
      render(); };
    box.appendChild(prev);
    const hint=document.createElement('div'); hint.style.cssText='font-size:11px;color:var(--sub);margin-bottom:4px';
    hint.textContent='「点/文字」を選び画像をクリックで、選択中(黄枠)の注釈のその位置を設定。行の◉で注釈切替。文字位置は「文字を既定に」で自動(点の上下)へ。';
    box.appendChild(hint);
    cs.forEach((c,i)=>{
      const r=vRow('注釈'+i);
      // 選択（クリック配置の対象）
      const selb=document.createElement('button'); selb.type='button'; selb.className='vchip'+(i===calloutSel?' on':''); selb.textContent=(i===calloutSel?'◉':'○'); selb.title='クリック配置の対象にする';
      selb.onclick=()=>{ calloutSel=i; render(); }; r.appendChild(selb);
      r.appendChild(vText(c.text,'ラベル',v=>c.text=v));
      const mkNum=(key)=>{ const n=document.createElement('input'); n.type='number'; n.step='0.05'; n.min='0'; n.max='1';
        n.value=(c[key]!=null?c[key]:0.5); n.style.width='62px'; n.title=key+'（0..1）';
        n.oninput=()=>{ const v=parseFloat(n.value); if(!isNaN(v)){ c[key]=Math.max(0,Math.min(1,v)); } };
        n.onchange=()=>render(); return n; };
      r.appendChild(document.createTextNode('x')); r.appendChild(mkNum('x'));
      r.appendChild(document.createTextNode('y')); r.appendChild(mkNum('y'));
      const al=document.createElement('label'); al.style.cssText='font-size:12px;display:inline-flex;gap:3px;align-items:center';
      const ac=document.createElement('input'); ac.type='checkbox'; ac.checked=!!c.arrow;
      ac.onchange=()=>{ if(ac.checked)c.arrow=true; else delete c.arrow; render(); }; al.appendChild(ac); al.appendChild(document.createTextNode('矢印')); r.appendChild(al);
      if(c.lx!=null||c.ly!=null) r.appendChild(vMini('文字自動',()=>{ delete c.lx; delete c.ly; render(); }));
      r.appendChild(vMini('削除',()=>{ cs.splice(i,1); clampItemFlags(ci,'callout_item',cs.length); if(calloutSel>=cs.length)calloutSel=Math.max(0,cs.length-1); render(); }));
      box.appendChild(r);
    });
    if(cs.length<4) box.appendChild(vMini('＋注釈',()=>{ cs.push({text:'',x:0.5,y:0.5}); calloutSel=cs.length-1; render(); }));
    // 見た目（章共通）：マーカー/ラベルの色・大きさ。
    const styRow=(label,colorKey,defColor,sizeKey)=>{ const r=vRow(label);
      const cp=document.createElement('input'); cp.type='color'; cp.value=st[colorKey]||defColor; cp.title='色';
      cp.oninput=()=>{ st[colorKey]=cp.value; }; cp.onchange=()=>render(); r.appendChild(cp);
      const sl=document.createElement('input'); sl.type='range'; sl.min='0.3'; sl.max='2'; sl.step='0.1';
      sl.value=(st[sizeKey]!=null?st[sizeKey]:1); sl.style.cssText='width:96px;vertical-align:middle'; sl.title='大きさ倍率';
      const sv=document.createElement('span'); sv.style.cssText='font-size:11px;color:var(--sub);min-width:34px;display:inline-block;text-align:right';
      const show=()=>{ sv.textContent=(st[sizeKey]!=null?st[sizeKey]:1).toFixed(1)+'倍'; }; show();
      sl.oninput=()=>{ st[sizeKey]=parseFloat(sl.value); show(); };
      sl.onchange=()=>render();  // 離したらプレビューに反映
      r.appendChild(document.createTextNode(' 大きさ')); r.appendChild(stepWrap(sl)); r.appendChild(sv);
      r.appendChild(vMini('既定',()=>{ delete st[colorKey]; delete st[sizeKey]; render(); })); return r; };
    box.appendChild(styRow('マーカー','markerColor','#ff5a6a','markerSize'));
    box.appendChild(styRow('ラベル','labelColor','#14233a','labelSize'));
    // ラベルの文字色・外枠色（背景と同化して見づらい時に）。
    const lcr=vRow('ラベル文字/枠');
    const tcp=document.createElement('input'); tcp.type='color'; tcp.value=lText; tcp.title='文字色';
    tcp.oninput=()=>{ st.labelTextColor=tcp.value; }; tcp.onchange=()=>render();
    lcr.appendChild(document.createTextNode('文字')); lcr.appendChild(tcp);
    const bcp=document.createElement('input'); bcp.type='color'; bcp.value=lBorder||'#ffffff'; bcp.title='外枠色';
    bcp.oninput=()=>{ st.labelBorderColor=bcp.value; }; bcp.onchange=()=>render();
    lcr.appendChild(document.createTextNode(' 外枠')); lcr.appendChild(bcp);
    lcr.appendChild(vMini('枠なし',()=>{ delete st.labelBorderColor; render(); }));
    lcr.appendChild(vMini('既定',()=>{ delete st.labelTextColor; delete st.labelBorderColor; render(); }));
    box.appendChild(lcr);
    // 矢印：大きさ（スライダー）＋形（プリセット）。色はマーカー色を流用。
    const ar=vRow('矢印');
    const asl=document.createElement('input'); asl.type='range'; asl.min='0.3'; asl.max='3'; asl.step='0.1';
    asl.value=aSize; asl.style.cssText='width:96px;vertical-align:middle'; asl.title='矢印の大きさ倍率';
    const asv=document.createElement('span'); asv.style.cssText='font-size:11px;color:var(--sub);min-width:34px;display:inline-block;text-align:right';
    const ashow=()=>{ asv.textContent=(st.arrowSize!=null?st.arrowSize:1).toFixed(1)+'倍'; }; ashow();
    asl.oninput=()=>{ st.arrowSize=parseFloat(asl.value); ashow(); }; asl.onchange=()=>render();
    ar.appendChild(document.createTextNode('大きさ')); ar.appendChild(stepWrap(asl)); ar.appendChild(asv);
    [['normal','標準'],['sharp','シャープ'],['thick','太め'],['dot','ドット']].forEach(([v,t])=>{ const b=document.createElement('button'); b.type='button';
      b.className='vchip'+((st.arrowShape||'normal')===v?' on':''); b.textContent=t;
      b.onclick=()=>{ if(v==='normal') delete st.arrowShape; else st.arrowShape=v; render(); }; ar.appendChild(b); });
    ar.appendChild(vMini('既定',()=>{ delete st.arrowSize; delete st.arrowShape; render(); }));
    box.appendChild(ar);
  }
}

// 喋り文字数→推定分。英字（かな）は読み仮名だけ喋る＝畳んで数える（実測較正305字/分）。
// 目標(TARGET_CHARS/LABEL)は対象がショートか本編かで /api/status から切替（ショート=約40秒）。
const SPOKEN_CPM=305; let TARGET_CHARS=8*305, TARGET_LABEL='8分';
function spokenLen(text){
  if(!text) return 0;
  let s=String(text).replace(/[0-9A-Za-z][0-9A-Za-z._\\-]*（([^（）]+)）/g,'$1');
  s=s.replace(/[\\s\\u3000（）「」『』、。！？・,.!?]/g,'');
  return [...s].length;
}
function secLbl(ch,i,all){ if(ch.section==='intro')return '導入'; if(ch.section==='outro')return '締め';
  let n=0; for(let j=0;j<=i;j++) if((all[j]||{}).section==='trivia')n++; return '実は'+n; }
function buildEstimate(){
  const sc=DATA.script||[], ch=DATA.chapters||[]; const per=ch.map(()=>0); let total=0;
  sc.forEach(t=>{ const n=spokenLen(t.text); total+=n; if(typeof t.chapter==='number'&&per[t.chapter]!=null) per[t.chapter]+=n; });
  const minE=total/SPOKEN_CPM, tgtChars=TARGET_CHARS, diff=tgtChars-total;
  const w=document.createElement('div'); w.className='estbar'; w.id='estbar';
  const parts=ch.map((c,i)=>'<span class="es3">'+secLbl(c,i,ch)+' '+per[i]+'</span>').join('');
  w.innerHTML='<b>喋り '+total+'字 ≈ 推定 '+minE.toFixed(1)+'分</b>'
    +' <span class="es">目標'+TARGET_LABEL+'≈'+tgtChars+'字（'+(diff>=0?'あと'+diff+'字':(-diff)+'字オーバー')+'）</span>'
    +'<div class="es2">'+parts+'</div>';
  return w;
}
function refreshEst(){ const o=document.getElementById('estbar'); if(o) o.replaceWith(buildEstimate()); }

function renderLegacy(){  // 旧アコーディオン版（未使用・参考保存）。実体は下の二画面render()。
  const m=document.getElementById('main'); m.innerHTML='';
  // theme
  const th=document.createElement('div'); th.className='theme';
  const ti=document.createElement('input'); ti.type='text'; ti.value=DATA.theme||''; ti.placeholder='テーマ';
  ti.onchange=()=>DATA.theme=ti.value; th.innerHTML='<span class="badge">テーマ</span>'; th.appendChild(ti);
  m.appendChild(th);
  m.appendChild(buildEstimate());  // 喋り字数・推定分のライブゲージ

  (DATA.chapters||[]).forEach((ch,ci)=>{
    const cuts=ch.image_cuts||(ch.image_cuts=[]);
    const sec=document.createElement('div'); sec.className='sec'+(OPEN.has(ci)?' open':'');
    // head
    const head=document.createElement('div'); head.className='sechead';
    let thumbs='';
    cuts.forEach((c,k)=>{ const u=imgUrl(ci,k);
      thumbs += u?`<img class="th" src="${u}">`:`<span class="ph">#${k}</span>`; });
    const confTag = (ch.section==='trivia'&&ch.confidence)
      ? `<span class="conf ${ch.confidence}" title="事実の確度（${confLabel(ch.confidence)}）">${confLabel(ch.confidence)}</span>` : '';
    const vk = vizOf(ch);
    const vizTag = vk ? `<span class="vizb" title="画像演出: ${VIZ_LABEL[vk]}">▣ ${VIZ_LABEL[vk]}</span>` : '';
    head.innerHTML=`<span class="badge">${sectionLabel(ch,ci)}</span>${confTag}${vizTag}
      <span class="ttl">${ch.title||'(無題)'}</span>
      <span class="sum">${ch.summary||''}</span>
      <span class="thumbs">${thumbs}</span>`;
    head.onclick=()=>{ OPEN.has(ci)?OPEN.delete(ci):OPEN.add(ci); render(); };
    if((ch.section||'')==='trivia'){  // ネタ章だけ再生成の対象に選べる
      const cb=document.createElement('input'); cb.type='checkbox'; cb.className='selcb';
      cb.checked=selChs.has(ci); cb.title='再生成の対象に選ぶ';
      cb.onclick=(e)=>{ e.stopPropagation();
        selChs.has(ci)?selChs.delete(ci):selChs.add(ci); cb.checked=selChs.has(ci); updateRegenBtn(); };
      head.insertBefore(cb, head.firstChild);
    }
    sec.appendChild(head);
    if(OPEN.has(ci)){
      const body=document.createElement('div'); body.className='body';
      // title / summary
      const tt=document.createElement('input'); tt.type='text'; tt.value=ch.title||''; tt.placeholder='章タイトル';
      tt.style.width='100%'; tt.onchange=()=>ch.title=tt.value;
      const sm=document.createElement('textarea'); sm.value=ch.summary||''; sm.placeholder='要約';
      sm.oninput=()=>{ch.summary=sm.value; autosize(sm);};
      body.innerHTML='<div class="lbl">タイトル / 要約</div>'; body.appendChild(tt); body.appendChild(sm);
      // 固定見出し(hook)：縦ショートの上部に出し続ける掴み。trivia章のみ。
      if(ch.section==='trivia'){
        const hl=document.createElement('div'); hl.className='lbl'; hl.textContent='固定見出し（ショート上部・掴み）';
        const hk=document.createElement('input'); hk.type='text'; hk.value=ch.hook||''; hk.style.width='100%';
        hk.placeholder='例: その「ロボット認証」、実はAIを無料で鍛えてる（空欄ならタイトルから仮生成）';
        hk.onchange=()=>{ ch.hook=hk.value; };
        body.appendChild(hl); body.appendChild(hk);
        // 事実の確度 / 裏取り手がかり（公開前チェック）。動画には出ない。
        const cl=document.createElement('div'); cl.className='lbl'; cl.textContent='事実の確度 / 裏取り手がかり';
        const crow=document.createElement('div'); crow.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;';
        const cs=document.createElement('select');
        [['','(未設定)'],['high','high 公式/一次資料'],['medium','medium 要確認'],['low','low 諸説']].forEach(([v,t])=>{
          const o=document.createElement('option'); o.value=v; o.textContent=t; if((ch.confidence||'')===v) o.selected=true; cs.appendChild(o); });
        cs.onchange=()=>{ if(cs.value) ch.confidence=cs.value; else delete ch.confidence; render(); };
        const sh=document.createElement('input'); sh.type='text'; sh.style.flex='1'; sh.style.minWidth='240px';
        sh.value=ch.source_hint||''; sh.placeholder='裏取りの手がかり（公式発表・開発者発言・年・媒体名など）';
        sh.onchange=()=>{ if(sh.value.trim()) ch.source_hint=sh.value.trim(); else delete ch.source_hint; };
        crow.appendChild(cs); crow.appendChild(sh);
        body.appendChild(cl); body.appendChild(crow);
      }
      // images
      const il=document.createElement('div'); const lb=document.createElement('div'); lb.className='lbl'; lb.textContent='画像（台本に対応）'; body.appendChild(lb);
      cuts.forEach((cut,k)=>{
        const r=document.createElement('div'); r.className='imgrow';
        const u=imgUrl(ci,k);
        // 調整（クロップ/補正）を反映したサムネにする＝render結果に近い見た目を確認できる（非破壊）。
        const co=cutMap[ci+'_'+k]||{}; const cr=co.crop;
        const flt=co.filter?(' style="filter:'+cssFilter(co.filter)+'"'):'';
        if(u && cr){ const w=100/(cr.r-cr.l), h=100/(cr.b-cr.t);
          r.innerHTML = '<div class="imgthumb"><img src="'+u+'" style="position:absolute;width:'+w+'%;height:'+h+'%;left:'+(-cr.l*w)+'%;top:'+(-cr.t*h)+'%;object-fit:fill;'+(co.filter?'filter:'+cssFilter(co.filter):'')+'"></div>';
        } else if(u && (co.pad || co.bg)){ const padT=Math.round((co.pad||0)*320/1100);  // 余白px(動画≒1100幅)をサムネ320幅へ縮尺
          const tcls=co.bg?'imgthumb':'imgthumb transbg';  // 色指定=その色 / 透過=市松模様
          r.innerHTML = '<div class="'+tcls+'" style="'+(co.bg?('background:'+co.bg):'')+';padding:'+padT+'px"><img src="'+u+'" style="width:100%;height:100%;object-fit:contain;'+(co.filter?'filter:'+cssFilter(co.filter):'')+'"></div>';
        } else if(u){ r.innerHTML = '<img src="'+u+'"'+flt+'>';
        } else { r.innerHTML = '<div class="ph2">#'+k+' 未取得</div>'; }
        // サムネクリックで調整パネルを開閉。サムネに直接D&Dで差し替えも可（調整を開かなくてよい）。
        const preview=r.querySelector('.imgthumb')||r.querySelector('img')||r.querySelector('.ph2');
        if(preview){ preview.style.cursor='pointer'; preview.title='クリックで調整 / 画像をドロップで差し替え';
          preview.onclick=()=>{ const ky=ci+'_'+k; adjustOpen.has(ky)?adjustOpen.delete(ky):adjustOpen.add(ky); render(); }; }
        preview.addEventListener('dragover', e=>{ e.preventDefault(); preview.style.outline='2px dashed #ffd84d'; });
        preview.addEventListener('dragleave', ()=>{ preview.style.outline=''; });
        preview.addEventListener('drop', async e=>{ e.preventDefault(); preview.style.outline='';
          const ky=ci+'_'+k;
          // 差し替えなのでクレジットは引き継がない（''）→ URL取り込みは出典URLが入る。差し替え後はサーバ値で更新。
          const res=await dropImport(ky, e.dataTransfer, '');
          if(res && res.ok){ await refreshCuts(); render(); }
          else if(res){ alert(res.message||'取り込み失敗'); } });
        const q=document.createElement('input'); q.type='text'; q.className='q'; q.placeholder='英語の検索語';
        q.value=cut.image_query||''; q.onchange=()=>cut.image_query=q.value;
        const kind=document.createElement('select');
        kind.innerHTML='<option value="subject">被写体(ロゴ/人物/製品)</option><option value="ambient">雰囲気(イメージ)</option>';
        kind.value=cut.image_kind||'ambient'; kind.onchange=()=>cut.image_kind=kind.value;
        const ja=document.createElement('input'); ja.type='text'; ja.className='ja'; ja.placeholder='日本語(意味)';
        ja.value=cut.image_query_ja||''; ja.onchange=()=>cut.image_query_ja=ja.value;
        const enBtn=document.createElement('button'); enBtn.className='mini'; enBtn.textContent='取得'; enBtn.title='英語の検索語で候補を表示';
        enBtn.onclick=()=>openCand(ci,k,cut,'en');
        const jaBtn=document.createElement('button'); jaBtn.className='mini'; jaBtn.textContent='日本語で取得'; jaBtn.title='日本語の検索語で候補を表示（Pexels/Pixabay・自分で翻訳しなくてよい）';
        jaBtn.onclick=()=>openCand(ci,k,cut,'ja');
        const clip=document.createElement('button'); clip.className='mini'; clip.textContent='📋貼付';
        clip.title='クリップボードの画像(スクショ)をこのカットに取り込む（Cmd+Ctrl+Shift+4で撮影→これ）';
        clip.onclick=()=>pasteClipboard(ci+'_'+k, ci, k);
        const adj=document.createElement('button'); adj.className='mini'; adj.textContent=adjustOpen.has(ci+'_'+k)?'調整を閉じる':'調整';
        adj.onclick=()=>{ const ky=ci+'_'+k; adjustOpen.has(ky)?adjustOpen.delete(ky):adjustOpen.add(ky); render(); };
        const del=document.createElement('button'); del.className='mini'; del.style.color='#c66'; del.style.background='transparent'; del.textContent='×';
        del.onclick=async()=>{
          if(!confirm('この画像カットを削除しますか？（以降のカットは前に詰まり、画像も整列します）')) return;
          cuts.splice(k,1); const newLen=cuts.length;
          DATA.script.forEach(tn=>{ if(tn.chapter!==ci||typeof tn.cut!=='number') return;
            if(tn.cut>k) tn.cut--; else if(tn.cut===k) tn.cut=Math.min(k,Math.max(0,newLen-1)); });
          await api('/api/script', DATA);              // script.json(image_cuts/turn cut)を整合保存
          await api('/api/delete-cut',{ch:ci,ci:k});   // review.jsonを再番号＋画像リネーム
          const rev=await (await fetch('/api/cuts')).json(); CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c);
          render();
        };
        // 画像の右にフィールドを縦積み：検索語 → 日本語 → [種別＋ボタン]（各ラベル付き）
        const fields=document.createElement('div'); fields.className='fields';
        // 検索語の各箱の隣に取得ボタン（英語box→英語取得 / 日本語box→日本語取得）。
        const enWrap=document.createElement('div'); enWrap.style.cssText='display:flex;gap:6px;align-items:center';
        q.style.flex='1'; enWrap.appendChild(q); enWrap.appendChild(enBtn);
        const jaWrap=document.createElement('div'); jaWrap.style.cssText='display:flex;gap:6px;align-items:center';
        ja.style.flex='1'; jaWrap.appendChild(ja); jaWrap.appendChild(jaBtn);
        const row3=document.createElement('div'); row3.className='frow';
        row3.appendChild(fl('取得の種別', kind)); row3.appendChild(clip); row3.appendChild(adj); row3.appendChild(del);
        const rc=cutMap[ci+'_'+k];
        const sz=document.createElement('div'); sz.className='szinfo';
        sz.textContent = (u&&rc&&rc.w) ? (rc.w+'×'+rc.h+'px・'+fmtKB(rc.bytes)) : (u?'サイズ不明':'');
        fields.appendChild(fl('検索語（英語）', enWrap)); fields.appendChild(fl('意味（日本語・取得可）', jaWrap));
        fields.appendChild(row3); fields.appendChild(sz);
        r.appendChild(fields);
        il.appendChild(r);
        if(adjustOpen.has(ci+'_'+k)) il.appendChild(buildAdjust(ci,k));
        if(candOpen.has(ci+'_'+k)) il.appendChild(buildCand(ci,k,cut));
      });
      const add=document.createElement('button'); add.className='mini'; add.textContent='＋画像を追加';
      add.style.cssText='background:transparent;border:1px dashed var(--line);color:var(--sub);width:100%;margin-top:4px;';
      add.onclick=()=>{ cuts.push({image_query:'',image_kind:'ambient'}); render(); };
      il.appendChild(add); body.appendChild(il);
      // dialogue
      const dl=document.createElement('div'); const lb2=document.createElement('div'); lb2.className='lbl'; lb2.textContent='台本'; body.appendChild(lb2);
      const vrng = vizOf(ch) ? vizRange(ci) : null;  // 演出の表示範囲（バー表示用）
      DATA.script.forEach((tn,gi)=>{ if(tn.chapter!==ci) return;
        // 演出の開始行の直前に範囲バーの先頭カードを出す。
        if(vrng && gi===vrng.startGi) dl.appendChild(vizHeaderCard(ch, ci, gi));
        const inViz = vrng && gi>=vrng.startGi && gi<=vrng.endGi;
        const row=document.createElement('div'); row.className='turn'+(inViz?' vizrow':'');
        const col=speakerColor(tn.speaker); if(!inViz) row.style.borderLeftColor=col;  // 範囲内は紫バー優先
        const sp=document.createElement('div'); sp.className='sp'; sp.style.color=col;
        sp.innerHTML=`<span class="dot" style="background:${col}"></span>${tn.speaker}`;
        const ta=document.createElement('textarea'); ta.value=tn.text; ta.oninput=()=>{tn.text=ta.value; autosize(ta); refreshEst();};
        // cut選択＝サムネをクリックして選ぶ。パネル/クイズ/比較は画像を上書きするので、範囲内では
        // 選択を畳み「演出で表示」＋演出が使う画像サムネを出す（stat/注釈は画像に重ねるだけ＝選択は残す）。
        const pick=document.createElement('div'); pick.className='cutpick';
        const roThumb=(k)=>{ const u=imgUrl(ci,k); const o=document.createElement('div'); o.className='copt sel'; o.title='画像'+k;
          o.innerHTML=(u?`<img src="${u}">`:`<span class="ph3">#${k}</span>`)+`<span class="num">${k}</span>`; return o; };
        // 比較は左右固定画像で上書き→範囲内は読み取りサムネ。クイズは画像を通常扱い（背後に
        // そのまま表示）＝セリフ毎にcut選択を残す。パネルもセリフ毎に画像が変わる→選択を残す
        // （stat/注釈も画像に重ねるだけなので選択を残す）。
        if(inViz && ch.compare){
          pick.classList.add('vizmuted');
          const lab=document.createElement('span'); lab.textContent='演出で表示'; lab.style.marginRight='4px'; pick.appendChild(lab);
          pick.appendChild(roThumb(ch.compare.left?.cut??0)); pick.appendChild(roThumb(ch.compare.right?.cut??1));
        } else {
          const cur=(typeof tn.cut==='number'?tn.cut:0);
          (cuts.length?cuts:[{}]).forEach((c,k)=>{
            const u=imgUrl(ci,k);
            const o=document.createElement('div'); o.className='copt'+(k===cur?' sel':''); o.title='画像'+k;
            o.innerHTML=(u?`<img src="${u}">`:`<span class="ph3">#${k} 未取得</span>`)+`<span class="num">${k}</span>`;
            o.onclick=()=>{ tn.cut=k; pick.querySelectorAll('.copt').forEach((e,j)=>e.classList.toggle('sel',j===k)); };
            pick.appendChild(o);
          });
        }
        const acts=document.createElement('div'); acts.className='acts';
        const bs=document.createElement('button'); bs.textContent='分割'; bs.onclick=()=>splitTurn(tn,ta);
        const bd=document.createElement('button'); bd.className='del'; bd.textContent='削除'; bd.onclick=()=>delTurn(tn);
        acts.appendChild(bs); acts.appendChild(bd);
        row.appendChild(sp); row.appendChild(ta); row.appendChild(pick); row.appendChild(acts);
        dl.appendChild(row);
        // 演出操作ライン（チップ=タイミング / ✎中身 / 演出なし章は＋演出）。
        if(ch.section==='trivia') dl.appendChild(turnVizControl(tn, gi, ch, ci));
        // 中身エディタ：この発言の✎中身が開いていればインライン展開。
        if(vizOpenGi===gi && vizOf(ch)){ const ce=document.createElement('div'); ce.className='vizcontent';
          vizContent(ce, ch, ci); dl.appendChild(ce); }
      });
      body.appendChild(dl);
      sec.appendChild(body);
    }
    m.appendChild(sec);
  });
  document.querySelectorAll('#main textarea').forEach(autosize);
}

// ===== 二画面（台本左＋プレビュー右）。hoistで後勝ち＝こちらが実体 =====
function lblDiv(t){ const d=document.createElement('div'); d.className='lbl'; d.textContent=t; return d; }
function firstGiOfChapter(ci){ const i=(DATA.script||[]).findIndex(t=>t.chapter===ci); return i<0?selGi:i; }
function markDirty(){ if(!dirty){ dirty=true; updateSaveBtn(); } }
function updateSaveBtn(){ const b=document.getElementById('save'); if(b) b.textContent=dirty?'● 保存':'保存'; }
function setWide(on){ rwide=on; const r=document.getElementById('rpane'); if(r){ r.classList.toggle('wide',on); renderRight(); } }
// プレビュー内の文字/要素サイズ。広げた時は拡大率に合わせて一緒に大きくする（画像だけでなくラベル等も）。
function pv(n){ return Math.round(n*(rwide?1.9:1)); }
// 折りたたみをCSSクラスで反映（全再描画しない＝スクロール位置を保つ）。
function applyCollapse(){
  document.querySelectorAll('.chsec').forEach(sec=>{
    const ci=+sec.dataset.ci; const col=collapsed.has(ci);
    sec.classList.toggle('collapsed',col);
    const cv=sec.querySelector('.chev'); if(cv){ cv.textContent=col?'▸':'▾'; cv.title=col?'開く':'畳む'; }
  });
}
function markSel(){
  const ci=(DATA.script[selGi]||{}).chapter;
  document.querySelectorAll('.line').forEach(el=>el.classList.toggle('sel', +el.dataset.gi===selGi));
  document.querySelectorAll('.chsec').forEach(el=>el.classList.toggle('active', +el.dataset.ci===ci));
}
// ===== 複数演出（セグメント）モデル =====
const SEGCOL=['#6b6ae0','#3fa3c0','#caa23f','#b05ab0','#4fa86a','#c0683f'];
function chSegs(ch){ if(!Array.isArray(ch.vizList)) ch.vizList=[]; return ch.vizList; }
function newSegId(ch){ const u=new Set(chSegs(ch).map(s=>s.id)); let i=1; while(u.has('s'+i)) i++; return 's'+i; }
function segOf(tn,ch){ if(!tn||!tn.vizSeg) return null; return chSegs(ch).find(s=>s.id===tn.vizSeg)||null; }
function segRange(ci,id){ let s=-1,e=-1; (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter===ci&&t.vizSeg===id){ if(s<0)s=gi; e=gi; } }); return {s,e}; }
// 旧・単一演出形式を vizList へ移行（描画時に一度だけ）。行に vizSeg を付け旧flagを掃除。
function migrateViz(ch,ci){
  if(Array.isArray(ch.vizList)) return;
  const k=VIZ_KEYS.find(x=>ch[x]); if(!k){ ch.vizList=[]; return; }
  const id=newSegId(ch); const seg={id:id,type:k}; seg[k]=ch[k];
  if(k==='callouts'&&ch.calloutStyle) seg.calloutStyle=ch.calloutStyle;
  ch.vizList=[seg];
  let first=-1,last=-1,rs=-1,re=-1;
  (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter!==ci) return; if(first<0)first=gi; last=gi; if(t.viz_start)rs=gi; if(t.viz_end)re=gi; });
  if(rs<0)rs=first; if(re<0||re<rs)re=last;
  (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter!==ci) return; if(gi>=rs&&gi<=re) t.vizSeg=id; delete t.viz_start; delete t.viz_end; });
  VIZ_KEYS.forEach(x=>delete ch[x]); delete ch.calloutStyle;
}
// セグメントの範囲を s..e の連続行に再タグ。隣の演出の行も境界で奪える（1行1演出）。
// 奪われて空になった演出は自動削除（pruneEmptySegs）。
function retagSeg(ci,id,s,e){
  if(s>e){ const t=s; s=e; e=t; }
  (DATA.script||[]).forEach((t,gi)=>{ if(t.chapter!==ci) return;
    if(gi>=s&&gi<=e){ if(t.vizSeg && t.vizSeg!==id){
        // 別演出から奪う＝その演出のタイミングflagは外す（別演出のものなので）。
        delete t.panel_event; delete t.panel_item; delete t.reveal; delete t.compare_item; delete t.callout_item; }
      t.vizSeg=id; }
    else if(t.vizSeg===id) delete t.vizSeg; });
  pruneEmptySegs(ci);
}
function pruneEmptySegs(ci){
  const ch=(DATA.chapters||[])[ci]; if(!ch) return; const vl=chSegs(ch);
  for(let i=vl.length-1;i>=0;i--){ const id=vl[i].id;
    const has=(DATA.script||[]).some(t=>t.chapter===ci && t.vizSeg===id);
    if(!has){ if(selSeg===id) selSeg=null; vl.splice(i,1); } }
}
function removeSeg(ch,ci,id){ const vl=chSegs(ch); const i=vl.findIndex(s=>s.id===id); if(i>=0)vl.splice(i,1);
  (DATA.script||[]).forEach(t=>{ if(t.chapter===ci&&t.vizSeg===id){ delete t.vizSeg; delete t.panel_event; delete t.panel_item; delete t.reveal; delete t.compare_item; delete t.callout_item; } });
  if(selSeg===id) selSeg=null; }

function render(){
  const m=document.getElementById('main');
  // スクロール保持：左=window / 右ペイン=内部scrollTop（操作で最上部へ飛ぶのを防ぐ）。
  const sy=window.scrollY;
  const _oldrp=document.getElementById('rpane'); const rsy=_oldrp?_oldrp.scrollTop:0;
  m.innerHTML='';
  if(!DATA||!DATA.script||!DATA.script.length){ m.textContent='台本がありません'; return; }
  if(selGi<0||selGi>=DATA.script.length) selGi=0;
  const tp=document.createElement('div'); tp.className='tp';
  const left=document.createElement('div'); left.className='tp-left';
  const right=document.createElement('div'); right.className='tp-right'+(rwide?' wide':''); right.id='rpane';
  tp.appendChild(left); tp.appendChild(right); m.appendChild(tp);
  // 左上：テーマ＋推定ゲージ
  const th=document.createElement('div'); th.className='theme';
  const ti=document.createElement('input'); ti.type='text'; ti.value=DATA.theme||''; ti.placeholder='テーマ';
  ti.onchange=()=>{ DATA.theme=ti.value; }; th.innerHTML='<span class="badge">テーマ</span>'; th.appendChild(ti);
  left.appendChild(th); left.appendChild(buildEstimate());
  const bar=document.createElement('div'); bar.style.cssText='display:flex;align-items:center;gap:8px;margin:2px 0 8px;flex-wrap:wrap';
  const hint=document.createElement('div'); hint.style.cssText='font-size:11px;color:var(--sub);flex:1;min-width:160px';
  hint.textContent='行をクリック→右で画像/演出を編集　｜　本文をダブルクリック→セリフを編集';
  bar.appendChild(hint);
  bar.appendChild(vMini('全て展開',()=>{ collapsed.clear(); applyCollapse(); }));
  bar.appendChild(vMini('全て畳む',()=>{ collapsed=new Set((DATA.chapters||[]).map((_,i)=>i)); applyCollapse(); }));
  left.appendChild(bar);
  // 章＝セクション。選択行のある章を active 表示。intro→ネタ→outro 連続。
  const selCi=(DATA.script[selGi]||{}).chapter;
  (DATA.chapters||[]).forEach((ch,ci)=>{
    migrateViz(ch,ci);   // 旧単一形式→vizListへ（初回のみ）
    const sec=document.createElement('div'); sec.className='chsec'+(ci===selCi?' active':'')+(collapsed.has(ci)?' collapsed':''); sec.dataset.ci=ci;
    sec.appendChild(chapterDivider(ch,ci));
    // 行は常に生成し、折りたたみはCSSで隠す（全再描画せずスクロール位置を保つため）。
    (DATA.script||[]).forEach((tn,gi)=>{ if(tn.chapter!==ci) return;
      sec.appendChild(lineRow(tn,gi,ch,ci,!!tn.vizSeg));
    });
    left.appendChild(sec);
  });
  renderRight();
  const _newrp=document.getElementById('rpane'); if(_newrp) _newrp.scrollTop=rsy;
  document.querySelectorAll('#main textarea').forEach(autosize);
  window.scrollTo(0,sy);
}

function chapterDivider(ch,ci){
  const d=document.createElement('div'); d.className='chdiv';
  const isCol=collapsed.has(ci);
  // 折りたたみ▸/▾（タイトルクリックでも開閉）。全再描画せずCSSクラスで開閉＝スクロール維持。
  const cv=document.createElement('button'); cv.className='chev'; cv.textContent=isCol?'▸':'▾'; cv.title=isCol?'開く':'畳む';
  const toggle=(e)=>{ e.stopPropagation(); collapsed.has(ci)?collapsed.delete(ci):collapsed.add(ci); applyCollapse(); };
  cv.onclick=toggle; d.appendChild(cv);
  // 章単位の再生成は廃止（深掘りストーリーは章＝物語ビートが連続し前後依存するため）。作り直しは「全体を作り直す」のみ。
  const badge=document.createElement('span'); badge.className='badge'; badge.textContent=sectionLabel(ch,ci); d.appendChild(badge);
  if(ch.section==='trivia'&&ch.confidence){ const c=document.createElement('span'); c.className='conf '+ch.confidence; c.textContent=confLabel(ch.confidence); d.appendChild(c); }
  const ns=chSegs(ch).length; if(ns){ const v=document.createElement('span'); v.className='vizb'; v.textContent='▣ 演出'+(ns>1?('×'+ns):''); d.appendChild(v); }
  const t=document.createElement('span'); t.className='ttl'; t.textContent=ch.title||'(無題)'; t.style.cursor='pointer'; t.onclick=toggle; d.appendChild(t);
  // 行数（畳んだ時だけCSSで表示）。常に生成しておく。
  const n=(DATA.script||[]).filter(x=>x.chapter===ci).length;
  const cnt=document.createElement('span'); cnt.className='chcnt'; cnt.textContent=n+'行'; d.appendChild(cnt);
  const e=document.createElement('button'); e.className='mini'; e.textContent='章を編集'; e.style.marginLeft='auto';
  e.onclick=()=>{ selGi=firstGiOfChapter(ci); rtab='chapter'; renderRight(); markSel(); }; d.appendChild(e);
  return d;
}

function lineRow(tn,gi,ch,ci,inViz){
  const row=document.createElement('div'); row.className='line'+(gi===selGi?' sel':'')+(inViz?' vizrow':''); row.dataset.gi=gi;
  const col=speakerColor(tn.speaker); row.style.borderLeftColor=col;
  // 演出セグメントのレール（所属＝色帯＋各演出が自分の開始/終了行に常時ハンドル）。全章（intro/outro含む）で可。
  const seg=segOf(tn,ch); const segIdx=seg?chSegs(ch).indexOf(seg):-1;
  {
    const rail=document.createElement('div'); rail.className='rail';
    if(seg){
      const c2=SEGCOL[segIdx%SEGCOL.length]; const rng=segRange(ci,seg.id);
      const sg=document.createElement('div'); sg.className='seg'+(gi===rng.s?' s':'')+(gi===rng.e?' e':''); sg.style.background=c2; rail.appendChild(sg);
      const prevSame=(gi>0 && DATA.script[gi-1] && DATA.script[gi-1].chapter===ci);
      const nextSame=(gi<DATA.script.length-1 && DATA.script[gi+1] && DATA.script[gi+1].chapter===ci);
      const multi=(rng.e>rng.s);  // 2行以上なら縮められる
      // 1行ずつ動かすハンドル（↑↓）。↑↓の向き=範囲を広げる/狭める。広げる時は隣の演出から1行奪う。
      const mkBh=(cls,label,btns)=>{ const bh=document.createElement('div'); bh.className='bh '+cls;
        const lab=document.createElement('span'); lab.className='lab'; lab.style.background=c2; lab.textContent=label; bh.appendChild(lab);
        btns.forEach(([sym,title,enabled,fn])=>{ const b=document.createElement('button'); b.textContent=sym; b.title=title; b.disabled=!enabled;
          b.onclick=(e)=>{ e.stopPropagation(); if(!enabled)return; fn(); render(); }; bh.appendChild(b); });
        rail.appendChild(bh); };
      if(gi===rng.s) mkBh('s','開始',[
        ['↑','1行上へ広げる（前の演出から奪う）', prevSame, ()=>retagSeg(ci,seg.id,gi-1,rng.e)],
        ['↓','1行下へ狭める',                  multi,    ()=>retagSeg(ci,seg.id,gi+1,rng.e)],
      ]);
      if(gi===rng.e) mkBh('e','終了',[
        ['↑','1行上へ狭める',                  multi,    ()=>retagSeg(ci,seg.id,rng.s,gi-1)],
        ['↓','1行下へ広げる（次の演出から奪う）', nextSame, ()=>retagSeg(ci,seg.id,rng.s,gi+1)],
      ]);
    }
    row.appendChild(rail);
  }
  // アイコン（話者色の丸＋頭文字）
  const av=document.createElement('div'); av.className='av'; av.style.background=col; av.textContent=(tn.speaker||'?').slice(0,1);
  // 名前＋本文（縦並び）
  const lc=document.createElement('div'); lc.className='lc';
  const nm=document.createElement('div'); nm.className='nm'; nm.style.color=col; nm.textContent=tn.speaker||'';
  const tx=document.createElement('div'); tx.className='tx'+(tn.text?'':' empty'); tx.textContent=tn.text||'(空・ダブルクリックで入力)';
  tx.ondblclick=(e)=>{ e.stopPropagation(); startEditLine(row,tn,tx); };
  lc.appendChild(nm); lc.appendChild(tx);
  // 右肩：演出マーク＋今出る画像のミニサムネ
  const mk=document.createElement('div'); mk.className='mk';
  const u=imgUrl(ci,(typeof tn.cut==='number'?tn.cut:0)); if(u){ const im=document.createElement('img'); im.src=u; mk.appendChild(im); }
  // 選択行に出る操作（分割・削除）。分割は編集中ならカーソル位置、読み表示なら文中の句読点で割る。
  const la=document.createElement('div'); la.className='lacts';
  const bs=document.createElement('button'); bs.textContent='分割'; bs.title='セリフを2つに分ける（編集中はカーソル位置）';
  bs.onclick=(e)=>{ e.stopPropagation(); splitTurn(tn, row.querySelector('textarea')); };
  const bd=document.createElement('button'); bd.className='del'; bd.textContent='削除';
  bd.onclick=(e)=>{ e.stopPropagation(); delTurn(tn); };
  la.appendChild(bs); la.appendChild(bd);
  row.onclick=()=>{
    const chg=(selGi!==gi);
    if(chg){ selGi=gi; rtab=null; }
    if(seg) selSeg=seg.id;   // 演出セグメントの行を選んだらそのセグメントを選択
    markSel();
    if(rwide){ setWide(false); } else { renderRight(); }
  };
  row.appendChild(av); row.appendChild(lc); row.appendChild(mk); row.appendChild(la);
  return row;
}

function startEditLine(row,tn,tx){
  const ta=document.createElement('textarea'); ta.value=tn.text||'';
  tx.replaceWith(ta); autosize(ta); ta.focus();
  // カーソルを末尾へ
  const n=ta.value.length; try{ ta.setSelectionRange(n,n); }catch(e){}
  ta.oninput=()=>{ tn.text=ta.value; autosize(ta); refreshEst(); markDirty(); };
  // 抜ける時は全再描画せず、その行だけ表示へ戻す（全DOM作り直しによるスクロール/フォーカス飛びを防ぐ）。
  ta.onblur=()=>{
    const nd=document.createElement('div'); nd.className='tx'+(tn.text?'':' empty'); nd.textContent=tn.text||'(空・ダブルクリックで入力)';
    nd.ondblclick=(e)=>{ e.stopPropagation(); startEditLine(row,tn,nd); };
    ta.replaceWith(nd);
  };
}

function renderRight(){
  const r=document.getElementById('rpane'); if(!r) return;
  const _rsy=r.scrollTop;  // 右ペインのスクロール位置を保持（編集で最上部へ飛ぶのを防ぐ）
  r.innerHTML='';
  // 拡大トグル（左に被さる大きいパネルへ）
  const wbar=document.createElement('div'); wbar.className='rwbar';
  const wb=document.createElement('button'); wb.className='rwbtn'+(rwide?' wide':'');
  wb.textContent=rwide?'⤡ 元のサイズに戻す':'⤢ 広げる'; wb.title='右ペインを広げる/戻す';
  wb.onclick=()=>setWide(!rwide); wbar.appendChild(wb); r.appendChild(wbar);
  const tn=DATA.script[selGi]; if(!tn){ r.appendChild(document.createTextNode('左で行を選択してください')); return; }
  const ci=tn.chapter; const ch=(DATA.chapters||[])[ci]||{};
  const inViz=!!tn.vizSeg; const nseg=chSegs(ch).length;
  const tabs=[['image','画像']];
  tabs.push(['viz', nseg?('演出×'+nseg):'＋演出']);  // 全章（intro/outro含む）で演出を付けられる
  tabs.push(['chapter','章']);
  let cur=rtab; if(!cur||!tabs.some(t=>t[0]===cur)) cur=inViz?'viz':'image';
  const tb=document.createElement('div'); tb.className='rtabs';
  tabs.forEach(([k,label])=>{ const b=document.createElement('button');
    b.className='rtab'+(cur===k?(k==='image'?' imgon':' on'):''); b.textContent=label;
    b.onclick=()=>{ rtab=k; renderRight(); const rp=document.getElementById('rpane'); if(rp) rp.scrollTop=0; }; tb.appendChild(b); });
  r.appendChild(tb);
  const info=document.createElement('div'); info.style.cssText='font-size:11px;color:var(--sub);margin-bottom:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
  info.textContent=(ch.title||'')+'｜'+(tn.speaker||'')+': '+(tn.text||'');
  r.appendChild(info);
  if(cur==='image') renderImageTab(r,tn,ch,ci);
  else if(cur==='viz') renderVizTab(r,tn,ch,ci);
  else renderChapterTab(r,ch,ci);
  r.scrollTop=_rsy;  // スクロール位置を復元
}

function framePrev(ci,k,cut){
  const co=cutMap[ci+'_'+k]||{}; const u=imgUrl(ci,k);
  const box=document.createElement('div'); box.style.cssText='position:relative;width:100%;aspect-ratio:16/9;border-radius:8px;overflow:hidden;background:#0c0f15;border:1px solid var(--line);margin-bottom:8px';
  if(co.hide){ box.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;color:var(--sub);font-size:12px">画像なし（黒板＋立ち絵のみ）</div>'; return box; }
  if(!u){ box.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;color:var(--sub);font-size:12px">画像 未取得</div>'; return box; }
  const fit=co.fit||(((cut||{}).image_kind==='subject')?'contain':'cover');
  const im=document.createElement('img');
  im.style.cssText='width:100%;height:100%;object-fit:'+fit+';'+(co.filter?('filter:'+cssFilter(co.filter)+';'):'')+((fit==='contain'&&co.bg)?('background:'+co.bg+';'):'');
  im.src=u; box.appendChild(im);
  return box;
}
function renderImageTab(r,tn,ch,ci){
  const cuts=ch.image_cuts||(ch.image_cuts=[]);
  const cur=(typeof tn.cut==='number'?tn.cut:0);
  if(!cuts[cur]) cuts[cur]={image_query:'',image_kind:'ambient'};
  const cut=cuts[cur]; const ky=ci+'_'+cur;
  // フレームプレビュー（この行で視聴者が見る画像）。クロップ精密反映は下の調整パネルで確認。
  r.appendChild(framePrev(ci,cur,cut));
  r.appendChild(lblDiv('この行で出す画像（クリックで選択）'));
  const pick=document.createElement('div'); pick.className='cutpick';
  (cuts.length?cuts:[{}]).forEach((c,k)=>{ const u=imgUrl(ci,k);
    const o=document.createElement('div'); o.className='copt'+(k===cur?' sel':'');
    o.innerHTML=(u?'<img src="'+u+'">':'<span class="ph3">#'+k+'</span>')+'<span class="num">'+k+'</span>';
    o.onclick=()=>{ tn.cut=k; markDirty(); render(); }; pick.appendChild(o); });
  const add=document.createElement('button'); add.className='mini'; add.textContent='＋画像追加'; add.style.marginLeft='4px';
  add.onclick=()=>{ cuts.push({image_query:'',image_kind:'ambient'}); render(); }; pick.appendChild(add);
  r.appendChild(pick);
  // 画像を探す：検索語(英/日)→取得ボタンで候補表示。差し替え/クロップは🔧へ。
  r.appendChild(lblDiv('画像を探す（検索語を入れて「取得」）'));
  const enRow=document.createElement('div'); enRow.style.cssText='display:flex;gap:6px;margin:2px 0';
  const en=document.createElement('input'); en.type='text'; en.placeholder='英語の検索語（例: street view car）'; en.value=cut.image_query||''; en.style.flex='1';
  en.onchange=()=>{ cut.image_query=en.value; markDirty(); };
  enRow.appendChild(en); enRow.appendChild(vMini('取得',()=>{ cut.image_query=en.value; openCand(ci,cur,cut,'en'); }));
  r.appendChild(enRow);
  const jaRow=document.createElement('div'); jaRow.style.cssText='display:flex;gap:6px;margin:2px 0';
  const ja=document.createElement('input'); ja.type='text'; ja.placeholder='日本語(意味)＝自分で英訳しなくてよい'; ja.value=cut.image_query_ja||''; ja.style.flex='1';
  ja.onchange=()=>{ cut.image_query_ja=ja.value; markDirty(); };
  jaRow.appendChild(ja); jaRow.appendChild(vMini('日本語で取得',()=>{ cut.image_query_ja=ja.value; openCand(ci,cur,cut,'ja'); }));
  r.appendChild(jaRow);
  // 種別（cover/contain の既定や検索の傾向に影響）
  const kr=vRow('種別'); const ks=document.createElement('select');
  [['ambient','ambient（写真・風景）'],['subject','subject（ロゴ・製品・記号）']].forEach(([v,t])=>{ const o=document.createElement('option'); o.value=v; o.textContent=t; if((cut.image_kind||'ambient')===v)o.selected=true; ks.appendChild(o); });
  ks.onchange=()=>{ cut.image_kind=ks.value; markDirty(); render(); }; kr.appendChild(ks); r.appendChild(kr);
  // サイズ表示＋クリップボード貼付＋カット削除
  const orow=document.createElement('div'); orow.style.cssText='display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap';
  const rc=cutMap[ky]; const sz=document.createElement('span'); sz.style.cssText='font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums';
  sz.textContent=(rc&&rc.w)?(rc.w+'×'+rc.h+'px・'+fmtKB(rc.bytes)):'';
  orow.appendChild(sz); const sp=document.createElement('span'); sp.style.flex='1'; orow.appendChild(sp);
  orow.appendChild(vMini('📋貼付',()=>pasteClipboard(ky,ci,cur)));
  const delc=vMini('× カット削除',async()=>{
    if(!confirm('この画像カットを削除しますか？（以降のカットは前に詰まり、画像も整列します）')) return;
    cuts.splice(cur,1); const newLen=cuts.length;
    DATA.script.forEach(t=>{ if(t.chapter!==ci||typeof t.cut!=='number') return;
      if(t.cut>cur) t.cut--; else if(t.cut===cur) t.cut=Math.min(cur,Math.max(0,newLen-1)); });
    await api('/api/script', DATA); await api('/api/delete-cut',{ch:ci,ci:cur});
    await refreshCuts(); render();
  }); delc.style.color='#c66'; orow.appendChild(delc);
  r.appendChild(orow);
  // 調整（クロップ/補正/差し替え/D&D）はトグル
  const adj=document.createElement('button'); adj.className='mini'; adj.style.marginTop='6px';
  adj.textContent=adjustOpen.has(ky)?'調整を閉じる':'🔧 クロップ/補正/差し替え（D&Dも）';
  adj.onclick=()=>{ adjustOpen.has(ky)?adjustOpen.delete(ky):adjustOpen.add(ky); renderRight(); };
  r.appendChild(adj);
  if(adjustOpen.has(ky)) r.appendChild(buildAdjust(ci,cur));
  if(candOpen.has(ky)) r.appendChild(buildCand(ci,cur,cut));
}

// 演出を追加する選択メニュー（この行を起点に新規セグメント）。
function vizAddRow(tn,ch,ci){
  const row=vRow('演出を追加'); const sel=document.createElement('select'); sel.className='vchip'; sel.style.cursor='pointer';
  const o0=document.createElement('option'); o0.value=''; o0.textContent='＋演出を追加'; sel.appendChild(o0);
  [['panel','パネル'],['quiz','クイズ'],['compare','比較'],['stat','数字'],['callouts','注釈']].forEach(([v,t])=>{
    const o=document.createElement('option'); o.value=v; o.textContent=t; sel.appendChild(o); });
  sel.onchange=()=>{ const v=sel.value; if(!v) return; const id=newSegId(ch); const seg={id:id,type:v};
    if(v==='panel'){ seg.panel={items:[{text:''}]}; tn.panel_event='shrink'; }
    else if(v==='quiz'){ seg.quiz={question:'',answer:''}; tn.reveal=true; }
    else if(v==='compare'){ seg.compare={left:{label:'',cut:0},right:{label:'',cut:1}}; tn.compare_item=0; }
    else if(v==='stat'){ seg.stat={value:''}; tn.reveal=true; }
    else if(v==='callouts'){ seg.callouts=[{text:'',x:0.5,y:0.5}]; tn.callout_item=0; }
    chSegs(ch).push(seg); tn.vizSeg=id; selSeg=id; markDirty(); render(); };
  row.appendChild(sel); return row;
}
function renderVizTab(r,tn,ch,ci){
  const segs=chSegs(ch);
  // セグメント一覧（複数演出を切替）。
  if(segs.length){ const lr=vRow('演出'); segs.forEach((s,i)=>{ const b=document.createElement('button'); b.type='button';
    b.className='vchip'+(selSeg===s.id?' on':''); b.textContent=(i+1)+'.'+(VIZ_LABEL[s.type]||s.type);
    b.style.borderLeft='4px solid '+SEGCOL[i%SEGCOL.length];
    b.onclick=()=>{ selSeg=s.id; const rng=segRange(ci,s.id); if(rng.s>=0) selGi=rng.s; renderRight(); markSel(); }; lr.appendChild(b); });
    r.appendChild(lr); }
  r.appendChild(vizAddRow(tn,ch,ci));
  // 選択中セグメント（無ければ選択行の所属セグメント）。
  let cur=segs.find(s=>s.id===selSeg); if(!cur){ const ls=segOf(tn,ch); if(ls){ cur=ls; selSeg=ls.id; } }
  if(cur){
    const n=document.createElement('div'); n.style.cssText='font-size:11px;color:var(--sub);margin:6px 0';
    n.textContent='範囲は左レールの「開始/終了」の↑↓で1行ずつ調整（'+(VIZ_LABEL[cur.type]||'')+'）。広げると隣の演出から奪う。タイミングはこのセグメント内のセリフを選んで設定。';
    r.appendChild(n);
    if(tn.vizSeg===cur.id) r.appendChild(turnVizControl(tn, selGi, cur, ci));
    const ce=document.createElement('div'); ce.className='vizcontent'; ce.style.cssText='margin:8px 0 0;';
    vizContent(ce, cur, ch, ci, ()=>{ removeSeg(ch,ci,cur.id); }); r.appendChild(ce);
  } else {
    const n=document.createElement('div'); n.style.cssText='font-size:11px;color:var(--sub);margin-top:6px';
    n.textContent='左でセリフを選んで「＋演出を追加」、または上の一覧から選択。'; r.appendChild(n);
  }
}

function renderChapterTab(r,ch,ci){
  r.appendChild(lblDiv('タイトル / 要約'));
  const tt=document.createElement('input'); tt.type='text'; tt.value=ch.title||''; tt.placeholder='章タイトル'; tt.style.width='100%';
  tt.onchange=()=>{ ch.title=tt.value; markDirty(); }; r.appendChild(tt);
  const sm=document.createElement('textarea'); sm.value=ch.summary||''; sm.placeholder='要約';
  sm.oninput=()=>{ ch.summary=sm.value; autosize(sm); markDirty(); }; r.appendChild(sm); autosize(sm);
  if(ch.section==='trivia'){
    r.appendChild(lblDiv('固定見出し（ショート上部・掴み）'));
    const hk=document.createElement('input'); hk.type='text'; hk.value=ch.hook||''; hk.style.width='100%';
    hk.placeholder='空欄ならタイトルから仮生成'; hk.onchange=()=>{ ch.hook=hk.value; markDirty(); }; r.appendChild(hk);
    r.appendChild(lblDiv('事実の確度 / 裏取り手がかり'));
    const cs=document.createElement('select');
    [['','(未設定)'],['high','high 公式/一次資料'],['medium','medium 要確認'],['low','low 諸説']].forEach(([v,t])=>{
      const o=document.createElement('option'); o.value=v; o.textContent=t; if((ch.confidence||'')===v)o.selected=true; cs.appendChild(o); });
    cs.onchange=()=>{ if(cs.value)ch.confidence=cs.value; else delete ch.confidence; markDirty(); render(); };
    const sh=document.createElement('input'); sh.type='text'; sh.style.cssText='width:100%;margin-top:4px';
    sh.value=ch.source_hint||''; sh.placeholder='裏取りの手がかり（公式発表・年・媒体名など）';
    sh.onchange=()=>{ if(sh.value.trim())ch.source_hint=sh.value.trim(); else delete ch.source_hint; markDirty(); };
    r.appendChild(cs); r.appendChild(sh);
  }
}

document.getElementById('save').onclick=async()=>{
  const r=await api('/api/script', DATA);
  const b=document.getElementById('save');
  if(r.ok){ dirty=false; b.textContent='保存✓'; } else { b.textContent='失敗'; }
  setTimeout(()=>updateSaveBtn(),1500);
};
// 未保存ガード＋Cmd/Ctrl+S＋編集でdirty
window.addEventListener('beforeunload',(e)=>{ if(dirty){ e.preventDefault(); e.returnValue=''; } });
window.addEventListener('keydown',(e)=>{ if((e.metaKey||e.ctrlKey)&&(e.key==='s'||e.key==='S')){ e.preventDefault(); document.getElementById('save').click(); } });
(function(){ const m=document.getElementById('main'); if(m){ m.addEventListener('input',markDirty); m.addEventListener('change',markDirty); } })();

// 再生成ボタンの活性/表示更新（選択ネタ章数を反映）
function updateRegenBtn(){
  const b=document.getElementById('regen');
  const n=selChs.size; b.disabled=!n;
  b.textContent = n ? `選択章を再生成（${n}）` : '選択章を再生成';
}
// 生成中のロック（全操作をブロック・編集消失を防ぐ）
function showLock(msg){
  let o=document.getElementById('lockov');
  if(!o){ o=document.createElement('div'); o.id='lockov';
    o.innerHTML='<div class="lockbox"><div class="spin"></div><span></span></div>';
    document.body.appendChild(o); }
  o.querySelector('span').textContent=msg; o.style.display='flex';
}
function hideLock(){ const o=document.getElementById('lockov'); if(o) o.style.display='none'; }

const fbBox=document.getElementById('fallback');
if(localStorage.getItem('fallbackEnabled')==='0') fbBox.checked=false;  // 前回のON/OFFを復元
fbBox.onchange=()=>localStorage.setItem('fallbackEnabled', fbBox.checked?'1':'0');
function fbEnabled(){ return fbBox.checked; }

document.getElementById('regenall').onclick=async()=>{
  if(!confirm('現在のテーマで台本を丸ごと作り直します（intro＋全ネタ＋outro）。\\n\\n章単位の再生成と違い冒頭/締めも新しいネタに合わせて作り直すので整合性が保たれます。\\n既存の台本・画像はすべて破棄し、画像も全カット取り直します（Gemini＋画像API・1〜2分）。\\n※生成中はパネルを操作できません。よろしいですか？')) return;
  showLock('全体を生成中… Gemini＋画像取得のため操作できません（1〜2分）');
  const r=await api('/api/regenerate-all', {fallback: fbEnabled()});
  if(r.ok){
    selChs.clear(); OPEN.clear();
    const [s,rev]=await Promise.all([fetch('/api/script').then(x=>x.json()), fetch('/api/cuts').then(x=>x.json())]);
    DATA=s; CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c);
    if(DATA.chapters&&DATA.chapters.length) OPEN.add(0);
    render(); updateRegenBtn(); hideLock();
    alert('テーマ「'+(r.theme||'')+'」で全体を作り直しました（'+(r.chapters||0)+'章）。');
  } else {
    hideLock(); alert('全体生成に失敗: '+(r.message||''));
  }
};

document.getElementById('regen').onclick=async()=>{
  const idx=[...selChs].sort((a,c)=>a-c); if(!idx.length) return;
  const names=idx.map(i=>'・'+((DATA.chapters[i]||{}).title||('第'+i+'章'))).join('\\n');
  if(!confirm(`次の ${idx.length} 章の台本と画像を再生成します。\\n${names}\\n\\nメインテーマは維持し、既存ネタと重複しない内容を新規生成します（Gemini 1回・既存の台本/画像は破棄）。\\n※生成中はパネルを操作できません。よろしいですか？`)) return;
  // 開始時に自動保存（他章の編集を確定し、再生成の基準に含める）→ ロック → 再生成
  showLock('再生成の前に保存中…');
  const sv=await api('/api/script', DATA);
  if(!sv.ok){ hideLock(); alert('保存に失敗したため中止しました: '+(sv.message||'')); return; }
  showLock('再生成中… Gemini生成のため操作できません（20〜40秒）');
  const r=await api('/api/regenerate', {indices: idx, fallback: fbEnabled()});
  if(r.ok){
    selChs.clear();
    const [s,rev]=await Promise.all([fetch('/api/script').then(x=>x.json()), fetch('/api/cuts').then(x=>x.json())]);
    DATA=s; CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c);
    (r.regenerated||[]).forEach(i=>OPEN.add(i));  // 再生成した章を開いて確認しやすく
    render(); updateRegenBtn(); hideLock();
    alert('再生成しました: '+ (r.titles||[]).map(t=>'「'+t+'」').join(' '));
  } else {
    hideLock(); updateRegenBtn(); alert('再生成に失敗: '+(r.message||''));
  }
};

Promise.all([fetch('/api/script').then(r=>r.json()), fetch('/api/cuts').then(r=>r.json()),
             fetch('/api/status').then(r=>r.json())])
.then(([s,rev,st])=>{
  if(st&&st.target){ TARGET_CHARS=st.target.chars; TARGET_LABEL=st.target.label; }  // ショート/本編で目標切替
  if(s.error){ document.getElementById('main').textContent=s.error; return; }
  DATA=s; CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c);
  if(DATA.chapters&&DATA.chapters.length) OPEN.add(0);  // 先頭は開いておく
  render();
});
</script>
</body></html>
"""


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
  return '実は '+n;
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
                        "target": target_for_dir()})
            return
        if path == "/api/script":
            data = load_script()
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
