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


def start_short_generate(slug, ch):
    """本編の第ch章を独立ショート台本へ生成し docs/shorts/<slug>/ に出力（画像取得まで→レビュー待ち）。

    Gemini＋画像取得を伴う（Mac/.env前提）。完了後 /story を docs/shorts/<slug> に向けてレビュー。
    """
    slug, ch = _slugify(slug), int(ch)
    src_script = os.path.join(BASE_DIR, "script.json")
    if not os.path.exists(src_script):
        return {"ok": False, "message": f"{src_script} がありません（本編の台本が必要）"}
    cmd = (f"python main_story.py --from-script {shlex.quote(src_script)} "
           f"--short-from {ch} --slug {shlex.quote(slug)} --stop-after-images")
    return _spawn(f"gen:{slug}", cmd)


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


def _load_image_config():
    """.env(Pexels/Pixabayキー)＋config をベストエフォートで読む。失敗時は空（Wikimediaのみ可）。"""
    try:
        import main_story
        main_story.load_dotenv()
        return main_story.load_config("config/config.story.yaml")
    except Exception as e:  # noqa: BLE001
        print(f"[review] config/.env 読込失敗（Wikimediaのみで続行）: {e}")
        return {}


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


def do_regenerate_chapters(indices):
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


def do_regenerate_all():
    """現在のテーマで台本を丸ごと作り直す（intro＋全trivia＋outro）。画像も全カット取り直す。

    章単位の再生成と違い intro/outro も新しくなるため整合性が保たれる（冒頭フックや締めが
    新しいネタと噛み合う）。旧triviaは却下履歴に積み、新生成はそれらを避ける。
    Gemini＋画像API（いずれも無料枠）を使う。Returns: {ok, theme?, chapters?, message?}
    """
    from src import image_fetch, story_script, topic_history
    config = _load_image_config()
    genre = topic_history.genre_of(config)
    old = load_script() or {}
    old_facts = [{"title": c.get("title", ""), "summary": c.get("summary", "")}
                 for c in old.get("chapters", []) if c.get("section") == "trivia"]
    avoid = topic_history.facts(genre)
    try:
        script_result = story_script.generate_story_script(config, also_avoid=avoid)
    except Exception as e:  # noqa: BLE001 - 生成失敗はメッセージで返す
        return {"ok": False, "message": f"台本生成に失敗: {e}"}
    save_script(script_result)
    if old_facts:
        topic_history.add(genre, old_facts, "rejected")  # 旧ネタは却下＝今後の生成で避ける

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
  :root { --bg:#11151c; --card:#1b212c; --line:#2c3543; --fg:#e8edf4; --sub:#90a0b5;
          --ok:#3fa34d; --accent:#4a86ff; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:'Hiragino Sans','Yu Gothic',system-ui,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:14px;
           padding:14px 22px; background:#0d1117ee; backdrop-filter:blur(8px);
           border-bottom:1px solid var(--line); }
  header h1 { font-size:18px; margin:0; font-weight:700; }
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
let NETAS=[], SHORTS=[];
function fmt(s){ if(s.state==='running')return['実行中…','st run'];
  if(s.state==='done')return['✓ 完了'+(s.out?': '+s.out:''),'st ok'];
  if(s.state==='failed')return['失敗(code '+s.code+')','st ng']; return['','st']; }
function poll(job, stId, logId, btns){
  fetch('/api/shorts/jobstatus?job='+encodeURIComponent(job)).then(r=>r.json()).then(s=>{
    const [t,c]=fmt(s); const st=document.getElementById(stId); if(st){st.textContent=t; st.className=c;}
    const lg=document.getElementById(logId); if(lg&&s.log){lg.style.display='block'; lg.textContent=s.log; lg.scrollTop=lg.scrollHeight;}
    if(s.state==='running') setTimeout(()=>poll(job,stId,logId,btns),1500);
    else { (btns||[]).forEach(b=>b.disabled=false); if(s.state==='done') setTimeout(load,400); }
  });
}
function render(){
  const m=document.getElementById('main'); m.innerHTML='';
  // 新規作成
  const mk=document.createElement('div'); mk.className='card2';
  mk.innerHTML='<h2 style="margin-top:0">本編ネタから新しいショートを作る</h2>'+
    '<div class="meta2">選んだネタを「自己完結・掴み先頭・30〜45秒」の単体台本に書き直し、docs/shorts/&lt;名前&gt;/ に作ります（台本→画像取得まで）。</div>';
  const row=document.createElement('div'); row.className='row2'; row.style.marginTop='10px';
  const sel=document.createElement('select');
  if(!NETAS.length){ sel.innerHTML='<option>本編の台本がありません</option>'; sel.disabled=true; }
  NETAS.forEach(n=>{ const o=document.createElement('option'); o.value=n.ch; o.textContent='第'+n.ch+'章 '+(n.title||''); sel.appendChild(o); });
  const slug=document.createElement('input'); slug.className='txt'; slug.placeholder='出力名(slug 例: captcha)'; slug.style.width='180px';
  const gen=document.createElement('button'); gen.className='primary'; gen.textContent='ショート台本を生成';
  const gst=document.createElement('span'); gst.className='st'; gst.id='gen-st';
  row.appendChild(sel); row.appendChild(slug); row.appendChild(gen); row.appendChild(gst);
  mk.appendChild(row);
  const glog=document.createElement('pre'); glog.className='log'; glog.id='gen-log'; mk.appendChild(glog);
  gen.onclick=async()=>{ const s=slug.value.trim()|| (NETAS.find(n=>n.ch==sel.value)||{}).title || '';
    gen.disabled=true; const r=await api('/api/shorts/generate',{ch:parseInt(sel.value),slug:s});
    if(!r.ok){ alert(r.message||'起動失敗'); gen.disabled=false; return; }
    poll(r.job,'gen-st','gen-log',[gen]); };
  m.appendChild(mk);

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
    const rnd=document.createElement('button'); rnd.className='primary'; rnd.textContent='書き出し'; rnd.disabled=!sh.hasMeta;
    rnd.onclick=async()=>{ rnd.disabled=true; const r=await api('/api/shorts/render',{slug:sh.slug});
      if(!r.ok){ alert(r.message||'起動失敗'); rnd.disabled=false; return; } poll(r.job,'st-'+sh.slug,'log-'+sh.slug,[rnd]); };
    row.appendChild(rev); row.appendChild(rnd);
    const cmd=document.createElement('div'); cmd.className='meta2'; cmd.style.marginTop='8px';
    cmd.innerHTML='音声+meta（Mac/VOICEVOX）: <code class="cmd2">python main_story.py --from-script '+sh.dir+'/script.json --images-from-dir --output-dir '+sh.dir+'</code><br>'+
      '深度（任意・パララックス）: <code class="cmd2">python make_depth.py --dir '+sh.dir+'</code>';
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
</style></head>
<body>
<header>
  <a href="/">← パネル</a>
  <h1>ストーリー編集</h1>
  <span class="spacer"></span>
  <a href="/read"><button title="フル台本/概要を読み取り専用で表示（事実確認）">台本を読む</button></a>
  <button id="regenall" title="テーマで台本を丸ごと作り直す（intro+全ネタ+outroを新規生成・整合性が保たれる）">全体を作り直す</button>
  <button id="regen" disabled title="チェックしたネタ章を、既存と重複しない内容で作り直す（Gemini1回）">選択章を再生成</button>
  <button class="ok" id="save">保存</button>
</header>
<main id="main">読み込み中…</main>
<script>
let DATA=null, CUTS=[], cutMap={}, OPEN=new Set(), adjustOpen=new Set(), candOpen=new Set(), candState={}, selChs=new Set();
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

function splitTurn(tn,ta){
  const text=tn.text||''; let pos=ta.selectionStart;
  if(!(pos>0&&pos<text.length)){ const m=text.slice(1).search(/[。！？]/); pos=m>=0?m+2:Math.floor(text.length/2); }
  const a=text.slice(0,pos).trim(), b=text.slice(pos).trim();
  if(!a||!b){ alert('分割位置が不正'); return; }
  tn.text=a; const nt=Object.assign({},tn,{text:b}); ['start','end','sentences'].forEach(k=>delete nt[k]);
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
  filt.innerHTML='<span>明るさ</span>'; filt.appendChild(fb);
  filt.insertAdjacentHTML('beforeend','<span>コントラスト</span>'); filt.appendChild(fc);
  filt.insertAdjacentHTML('beforeend','<span>白黒</span>'); filt.appendChild(fg);
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
  if(ch.section==='intro') return 'intro';
  if(ch.section==='outro') return 'outro';
  // trivia通し番号
  let n=0; for(let i=0;i<=ci;i++){ if((DATA.chapters[i].section)==='trivia') n++; }
  return 'trivia'+n;
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

function render(){
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
    head.innerHTML=`<span class="badge">${sectionLabel(ch,ci)}</span>
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
      DATA.script.forEach((tn)=>{ if(tn.chapter!==ci) return;
        const row=document.createElement('div'); row.className='turn';
        const col=speakerColor(tn.speaker); row.style.borderLeftColor=col;
        const sp=document.createElement('div'); sp.className='sp'; sp.style.color=col;
        sp.innerHTML=`<span class="dot" style="background:${col}"></span>${tn.speaker}`;
        const ta=document.createElement('textarea'); ta.value=tn.text; ta.oninput=()=>{tn.text=ta.value; autosize(ta); refreshEst();};
        // cut選択＝サムネをクリックして選ぶ（どの画像が出るか一目で分かる）
        const pick=document.createElement('div'); pick.className='cutpick';
        const cur=(typeof tn.cut==='number'?tn.cut:0);
        (cuts.length?cuts:[{}]).forEach((c,k)=>{
          const u=imgUrl(ci,k);
          const o=document.createElement('div'); o.className='copt'+(k===cur?' sel':''); o.title='画像'+k;
          o.innerHTML=(u?`<img src="${u}">`:`<span class="ph3">#${k} 未取得</span>`)+`<span class="num">${k}</span>`;
          o.onclick=()=>{ tn.cut=k; pick.querySelectorAll('.copt').forEach((e,j)=>e.classList.toggle('sel',j===k)); };
          pick.appendChild(o);
        });
        const acts=document.createElement('div'); acts.className='acts';
        const bs=document.createElement('button'); bs.textContent='分割'; bs.onclick=()=>splitTurn(tn,ta);
        const bd=document.createElement('button'); bd.className='del'; bd.textContent='削除'; bd.onclick=()=>delTurn(tn);
        acts.appendChild(bs); acts.appendChild(bd);
        row.appendChild(sp); row.appendChild(ta); row.appendChild(pick); row.appendChild(acts);
        dl.appendChild(row);
      });
      body.appendChild(dl);
      sec.appendChild(body);
    }
    m.appendChild(sec);
  });
  document.querySelectorAll('#main textarea').forEach(autosize);
}

document.getElementById('save').onclick=async()=>{
  const r=await api('/api/script', DATA);
  const b=document.getElementById('save'); b.textContent=r.ok?'保存✓':'失敗'; setTimeout(()=>b.textContent='保存',1500);
};

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

document.getElementById('regenall').onclick=async()=>{
  if(!confirm('現在のテーマで台本を丸ごと作り直します（intro＋全ネタ＋outro）。\\n\\n章単位の再生成と違い冒頭/締めも新しいネタに合わせて作り直すので整合性が保たれます。\\n既存の台本・画像はすべて破棄し、画像も全カット取り直します（Gemini＋画像API・1〜2分）。\\n※生成中はパネルを操作できません。よろしいですか？')) return;
  showLock('全体を生成中… Gemini＋画像取得のため操作できません（1〜2分）');
  const r=await api('/api/regenerate-all', {});
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
  const r=await api('/api/regenerate', {indices: idx});
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
            self._json(do_regenerate_chapters(body.get("indices") or []))
            return
        if path == "/api/regenerate-all":
            self._json(do_regenerate_all())
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
        if path == "/api/shorts/generate":
            try:
                self._json(start_short_generate(body.get("slug"), body.get("ch")))
            except (TypeError, ValueError):
                self._json({"ok": False, "message": "slug/ch が不正"})
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
