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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# レビュー対象ディレクトリ（既定 docs/story）。main()で上書き。
DIR = "docs/story"

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


def write_image_bytes(filename, data):
    with open(os.path.join(DIR, filename), "wb") as f:
        f.write(data)


def load_script():
    path = os.path.join(DIR, "script.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_script(data):
    with open(os.path.join(DIR, "script.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def do_fetch_cut(ch, ci, query, kind):
    """1カットを取得して review.json を更新（upsert）。検索のみ・Geminiは使わない。

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
    config = {}
    try:  # .env(Pexels/Pixabayキー)＋config読込はベストエフォート。失敗してもWikimediaは可
        import main_story
        main_story.load_dotenv()
        config = main_story.load_config("config/config.story.yaml")
    except Exception as e:  # noqa: BLE001
        print(f"[review] config/.env 読込失敗（Wikimediaのみで続行）: {e}")
    base = f"ch_{ch:02d}_{ci:02d}"
    try:
        fn, attr = image_fetch.fetch_one_cut(query, kind or "ambient", DIR, base, config)
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


def apply_import_url(review, key, url, attribution):
    """WebからD&DされたURLを取り込み、ch_NN_MM.<ext>で保存して review を更新。

    帰属は指定が無ければ出典URLを入れる（商用可かは人が要確認）。
    Returns: (ok, message, saved_filename)。ネットワークI/Oを伴う。
    """
    cut = find_cut(review, key)
    if not cut:
        return False, "unknown key", None
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
    return True, "ok", filename


def apply_replace(review, key, upload_name, data_b64, attribution):
    """差し替え：base64画像を ch_NN_MM.<ext> で保存し review を更新。
    Returns: (ok, message, saved_filename)。I/Oは write_image_bytes 経由。
    """
    cut = find_cut(review, key)
    if not cut:
        return False, "unknown key", None
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

IMAGE_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>画像レビュー</title>
<style>
  :root { --bg:#11151c; --card:#1b212c; --line:#2c3543; --fg:#e8edf4; --sub:#90a0b5;
          --ok:#3fa34d; --accent:#4a86ff; --warn:#d8863a; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Hiragino Sans','Yu Gothic',system-ui,sans-serif;
         background:var(--bg); color:var(--fg); }
  header { position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:16px;
           padding:14px 22px; background:#0d1117ee; backdrop-filter:blur(8px);
           border-bottom:1px solid var(--line); }
  header h1 { font-size:18px; margin:0; font-weight:700; }
  .count { color:var(--sub); font-size:14px; }
  .spacer { flex:1; }
  button { font:inherit; border:none; border-radius:8px; padding:8px 16px; cursor:pointer;
           font-weight:700; color:#fff; background:var(--line); }
  button.primary { background:var(--accent); }
  button.ok { background:var(--ok); }
  button:disabled { opacity:.5; cursor:default; }
  main { padding:22px; display:grid; gap:18px;
         grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          overflow:hidden; display:flex; flex-direction:column; }
  .card.approved { border-color:var(--ok); box-shadow:0 0 0 1px var(--ok); }
  .thumb { position:relative; aspect-ratio:16/9; background:#0c0f15; display:flex;
           align-items:center; justify-content:center; cursor:crosshair; user-select:none; }
  .thumb img { width:100%; height:100%; object-fit:contain; pointer-events:none; }
  .thumb .ph { color:var(--sub); font-size:14px; }
  .croprect { position:absolute; border:2px solid #ffd84d; background:rgba(255,216,77,.12);
              pointer-events:none; }
  .card.hidden .thumb { opacity:.35; }
  .body { padding:12px 14px; display:flex; flex-direction:column; gap:8px; }
  .title { font-weight:700; font-size:15px; }
  .meta { font-size:12px; color:var(--sub); display:flex; gap:8px; flex-wrap:wrap; }
  .kind { padding:1px 8px; border-radius:999px; background:var(--line); }
  input[type=text] { width:100%; font:inherit; font-size:12px; padding:6px 8px;
           background:#0c0f15; color:var(--fg); border:1px solid var(--line); border-radius:6px; }
  select { font:inherit; font-size:12px; padding:6px 8px; background:#0c0f15; color:var(--fg);
           border:1px solid var(--line); border-radius:6px; }
  .row { display:flex; gap:8px; align-items:center; }
  .row button { flex:1; padding:7px 10px; font-size:13px; }
  label.file { flex:1; }
  label.file input { display:none; }
  .chk { font-size:12px; color:var(--sub); display:flex; align-items:center; gap:5px; white-space:nowrap; }
  .filters { display:grid; grid-template-columns:auto 1fr; gap:4px 8px; align-items:center;
             font-size:11px; color:var(--sub); }
  .filters input[type=range] { width:100%; }
  .filters .frow { display:contents; }
  .tools button { background:var(--line); font-size:12px; padding:5px 10px; }
  .hint { color:var(--sub); font-size:12px; min-height:14px; }
  #done { display:none; padding:22px; }
  #done code { background:#0c0f15; padding:10px 14px; border-radius:8px; display:block;
           color:#bfe3c4; font-size:13px; white-space:pre-wrap; }
</style></head>
<body>
<header>
  <h1>画像レビュー</h1>
  <span class="count" id="count">…</span>
  <span class="spacer"></span>
  <button class="ok" id="approveAll">すべてOK</button>
  <button class="primary" id="continue">承認して続行</button>
</header>
<main id="grid"></main>
<div id="done">
  <p>承認しました。次のコマンドで続行してください（音声→meta生成）:</p>
  <code id="cmd"></code>
</div>
<script>
const grid = document.getElementById('grid');
let cuts = [];

function api(path, body){
  return fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)}).then(r=>r.json());
}

function refreshCount(){
  const a = cuts.filter(c=>c.approved).length;
  document.getElementById('count').textContent = `${a} / ${cuts.length} 承認`;
}

function setOpt(key, patch){ return api('/api/options', {key, patch}); }

// contain表示の画像内容矩形（letterbox込みの実描画領域）。クロップ座標を画像基準にするため。
function contentRect(img, box){
  const nw=img.naturalWidth, nh=img.naturalHeight;
  if(!nw||!nh) return {x:0,y:0,w:box.width,h:box.height};
  const s=Math.min(box.width/nw, box.height/nh);
  const w=nw*s, h=nh*s;
  return {x:(box.width-w)/2, y:(box.height-h)/2, w, h};
}
function cssFilter(f){
  if(!f) return '';
  return `brightness(${f.brightness??1}) contrast(${f.contrast??1}) grayscale(${f.grayscale??0})`;
}

function card(c){
  const key = `${c.ch}_${c.ci}`;
  const el = document.createElement('div');
  el.className = 'card' + (c.approved ? ' approved':'') + (c.hide ? ' hidden':'');
  el.dataset.key = key;
  const img = c.image
    ? `<img src="/img/${key}?v=${Date.now()}" alt="">`
    : `<span class="ph">画像なし（プレースホルダ）</span>`;
  el.innerHTML = `
    <div class="thumb">${img}<div class="croprect" style="display:none"></div></div>
    <div class="body">
      <div class="title">${c.title || '(無題)'}</div>
      <div class="meta"><span class="kind">${c.kind}</span><span>検索: ${c.query||'-'}</span></div>
      <input type="text" class="attr" placeholder="出典（任意・CC-BY等）" value="${c.attribution||''}">
      <div class="row">
        <button class="ok approve">${c.approved?'承認済み':'OK'}</button>
        <label class="file"><button type="button" class="repl">差し替え</button>
          <input type="file" accept="image/*"></label>
      </div>
      <div class="row">
        <select class="fit" title="枠への収め方">
          <option value="">fit:自動</option><option value="cover">cover(埋める)</option>
          <option value="contain">contain(全体)</option>
        </select>
        <label class="chk"><input type="checkbox" class="hide"> 画像なし</label>
      </div>
      <div class="filters">
        <span>明るさ</span><input type="range" class="fb" min="0.3" max="1.5" step="0.05" value="1">
        <span>コントラスト</span><input type="range" class="fc" min="0.5" max="1.5" step="0.05" value="1">
        <span>白黒</span><input type="range" class="fg" min="0" max="1" step="0.05" value="0">
      </div>
      <div class="row" title="containの余白(px)と余白色">
        <label class="chk">余白 <input type="number" class="pad" min="0" max="400" step="4" value="0" style="width:56px"></label>
        <label class="chk">色 <input type="color" class="bg" value="#eef1f5"></label>
        <button class="bgclear">色既定</button>
      </div>
      <div class="row tools">
        <button class="cropclear">クロップ解除</button>
        <button class="fclear">補正解除</button>
      </div>
      <div class="hint">ドラッグ=クロップ / 画像をドロップ=取り込み</div>
    </div>`;

  const thumb = el.querySelector('.thumb');
  const imgEl = thumb.querySelector('img');
  const rectEl = thumb.querySelector('.croprect');
  const hint = el.querySelector('.hint');

  // 初期状態を反映
  el.querySelector('.fit').value = c.fit || '';
  el.querySelector('.hide').checked = !!c.hide;
  if(c.filter){
    el.querySelector('.fb').value = c.filter.brightness??1;
    el.querySelector('.fc').value = c.filter.contrast??1;
    el.querySelector('.fg').value = c.filter.grayscale??0;
  }
  if(imgEl) imgEl.style.filter = cssFilter(c.filter);
  if(c.pad) el.querySelector('.pad').value = c.pad;
  if(c.bg) el.querySelector('.bg').value = c.bg;

  function drawCrop(){
    if(!c.crop || !imgEl){ rectEl.style.display='none'; return; }
    const box = thumb.getBoundingClientRect();
    const r = contentRect(imgEl, box);
    rectEl.style.display='block';
    rectEl.style.left = (r.x + c.crop.l*r.w)+'px';
    rectEl.style.top  = (r.y + c.crop.t*r.h)+'px';
    rectEl.style.width  = ((c.crop.r-c.crop.l)*r.w)+'px';
    rectEl.style.height = ((c.crop.b-c.crop.t)*r.h)+'px';
  }
  if(imgEl){ imgEl.complete ? drawCrop() : (imgEl.onload = drawCrop); }

  const approve = ()=>{ c.approved=true; el.classList.add('approved');
    el.querySelector('.approve').textContent='承認済み'; refreshCount(); };

  el.querySelector('.approve').onclick = async ()=>{
    const r = await api('/api/approve', {key, approved:true}); if(r.ok) approve();
  };
  el.querySelector('.attr').onchange = (e)=>{
    api('/api/attribution', {key, attribution:e.target.value}); c.attribution=e.target.value;
  };
  el.querySelector('.fit').onchange = (e)=>{
    setOpt(key, {fit: e.target.value || null}); c.fit = e.target.value || null;
  };
  el.querySelector('.hide').onchange = (e)=>{
    setOpt(key, {hide: e.target.checked}); c.hide=e.target.checked;
    el.classList.toggle('hidden', c.hide);
  };
  const sendFilter = ()=>{
    const f = {brightness:+el.querySelector('.fb').value,
               contrast:+el.querySelector('.fc').value,
               grayscale:+el.querySelector('.fg').value};
    c.filter = f; if(imgEl) imgEl.style.filter = cssFilter(f);
    setOpt(key, {filter: f});
  };
  ['fb','fc','fg'].forEach(k=>{
    const s = el.querySelector('.'+k);
    s.oninput = ()=>{ if(imgEl) imgEl.style.filter = cssFilter(
      {brightness:+el.querySelector('.fb').value, contrast:+el.querySelector('.fc').value,
       grayscale:+el.querySelector('.fg').value}); };
    s.onchange = sendFilter;
  });
  el.querySelector('.pad').onchange = (e)=>{
    const n = parseInt(e.target.value) || 0; c.pad = n || null; setOpt(key, {pad:n});
  };
  el.querySelector('.bg').onchange = (e)=>{
    c.bg = e.target.value; setOpt(key, {bg:e.target.value});
  };
  el.querySelector('.bgclear').onclick = ()=>{
    c.bg=null; el.querySelector('.bg').value='#eef1f5'; setOpt(key, {bg:null});
  };
  el.querySelector('.fclear').onclick = ()=>{
    el.querySelector('.fb').value=1; el.querySelector('.fc').value=1; el.querySelector('.fg').value=0;
    c.filter=null; if(imgEl) imgEl.style.filter=''; setOpt(key, {filter:null});
  };
  el.querySelector('.cropclear').onclick = ()=>{
    c.crop=null; rectEl.style.display='none'; setOpt(key, {crop:null});
  };

  // 差し替え
  const fileInput = el.querySelector('input[type=file]');
  el.querySelector('.repl').onclick = ()=> fileInput.click();
  fileInput.onchange = (e)=>{
    const f = e.target.files[0]; if(!f) return;
    hint.textContent='送信中…';
    const reader = new FileReader();
    reader.onload = async ()=>{
      const b64 = reader.result.split(',')[1];
      const r = await api('/api/replace', {key, filename:f.name, dataB64:b64,
        attribution: el.querySelector('.attr').value});
      if(r.ok){
        c.image=r.filename; c.crop=null;
        thumb.innerHTML = `<img src="/img/${key}?v=${Date.now()}"><div class="croprect" style="display:none"></div>`;
        approve(); hint.textContent='ドラッグ=クロップ / 画像をドロップ=取り込み';
        load();  // 簡易：再読込で新imgのハンドラ再付与
      } else { hint.textContent='失敗: '+(r.message||''); }
    };
    reader.readAsDataURL(f);
  };

  // D&D取り込み：Webサイトからの画像ドロップ(URL) / OSファイル / data:画像 に対応。
  function setImported(filename){
    c.image=filename; c.crop=null;
    thumb.innerHTML = `<img src="/img/${key}?v=${Date.now()}"><div class="croprect" style="display:none"></div>`;
    approve(); hint.textContent='取り込みました（出典/ライセンスを確認）';
    load();  // 新imgへハンドラ再付与
  }
  thumb.addEventListener('dragover', (e)=>{ e.preventDefault(); thumb.style.outline='2px dashed #ffd84d'; });
  thumb.addEventListener('dragleave', ()=>{ thumb.style.outline=''; });
  thumb.addEventListener('drop', async (e)=>{
    e.preventDefault(); thumb.style.outline='';
    const dt = e.dataTransfer;
    // ① OSファイル（ファイル本体）
    if(dt.files && dt.files.length){
      const f = dt.files[0]; hint.textContent='取り込み中…';
      const reader = new FileReader();
      reader.onload = async ()=>{
        const r = await api('/api/replace', {key, filename:f.name, dataB64:reader.result.split(',')[1],
          attribution: el.querySelector('.attr').value});
        r.ok ? setImported(r.filename) : (hint.textContent='失敗: '+(r.message||''));
      };
      reader.readAsDataURL(f); return;
    }
    // ② Webページからの画像ドロップ＝URL（data:はその場でデコード）
    let url = dt.getData('text/uri-list') || dt.getData('text/plain') || '';
    url = url.split('\\n').find(s=>s && !s.startsWith('#')) || '';
    if(!url){ hint.textContent='画像のURL/ファイルを取得できませんでした'; return; }
    hint.textContent='取り込み中…';
    if(url.startsWith('data:image')){
      const r = await api('/api/replace', {key, filename:'dropped.png', dataB64:url.split(',')[1],
        attribution: el.querySelector('.attr').value});
      r.ok ? setImported(r.filename) : (hint.textContent='失敗: '+(r.message||'')); return;
    }
    const r = await api('/api/import-url', {key, url, attribution: el.querySelector('.attr').value});
    if(r.ok){
      const a = el.querySelector('.attr'); if(!a.value){ a.value = url; c.attribution = url; }
      setImported(r.filename);
    } else hint.textContent='失敗: '+(r.message||'');
  });

  // クロップ：画像上をドラッグで矩形選択 → 画像内容基準で正規化して保存
  if(c.image){
    let drag=null;
    thumb.onmousedown = (e)=>{
      const box = thumb.getBoundingClientRect();
      drag = {box, r:contentRect(imgEl, box), x0:e.clientX-box.left, y0:e.clientY-box.top};
    };
    window.addEventListener('mousemove', (e)=>{
      if(!drag) return;
      const x=e.clientX-drag.box.left, y=e.clientY-drag.box.top;
      const L=Math.min(drag.x0,x), T=Math.min(drag.y0,y), W=Math.abs(x-drag.x0), H=Math.abs(y-drag.y0);
      rectEl.style.display='block';
      rectEl.style.left=L+'px'; rectEl.style.top=T+'px'; rectEl.style.width=W+'px'; rectEl.style.height=H+'px';
    });
    thumb.onmouseup = (e)=>{
      if(!drag) return;
      const r=drag.r;
      const x=e.clientX-drag.box.left, y=e.clientY-drag.box.top;
      const norm=(px,py)=>[ (px-r.x)/r.w, (py-r.y)/r.h ];
      let [l,t]=norm(Math.min(drag.x0,x), Math.min(drag.y0,y));
      let [rr,bb]=norm(Math.max(drag.x0,x), Math.max(drag.y0,y));
      const cl=v=>Math.max(0,Math.min(1,v));
      const crop={l:cl(l),t:cl(t),r:cl(rr),b:cl(bb)};
      drag=null;
      if(crop.r-crop.l<0.02 || crop.b-crop.t<0.02){ drawCrop(); return; } // 誤クリックは無視
      c.crop=crop; setOpt(key,{crop}); drawCrop();
    };
  }
  return el;
}

async function load(){
  const data = await (await fetch('/api/cuts')).json();
  cuts = data.cuts || [];
  grid.innerHTML=''; cuts.forEach(c=> grid.appendChild(card(c)));
  refreshCount();
}

document.getElementById('approveAll').onclick = async ()=>{
  for(const c of cuts){ await api('/api/approve', {key:`${c.ch}_${c.ci}`, approved:true}); c.approved=true; }
  document.querySelectorAll('.card').forEach(el=>{ el.classList.add('approved');
    el.querySelector('.approve').textContent='承認済み'; });
  refreshCount();
};

document.getElementById('continue').onclick = async ()=>{
  const r = await api('/api/continue', {});
  document.getElementById('cmd').textContent = r.command || '';
  document.getElementById('done').style.display='block';
  window.scrollTo(0, document.body.scrollHeight);
};

load();
</script>
</body></html>
"""


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
  {key:'review', t:'画像一覧（一括編集）', d:'全画像をグリッドで一括確認・承認', link:'/images',
   cmd:'(個別の編集は /story から)'},
  {key:'audio',  t:'③ 音声+meta', d:'VOICEVOXで音声・字幕生成', link:null,
   cmd:'python main_story.py --from-script DIR/script.json --images-from-dir'},
  {key:'meta',   t:'④ 仕上げ', d:'Remotionで動画書き出し', link:null,
   cmd:'cd video && SRC_DIR=../DIR npm run render'},
];
fetch('/api/status').then(r=>r.json()).then(st=>{
  document.getElementById('dir').textContent = '対象: '+st.dir;
  const m = document.getElementById('main'); m.innerHTML='';
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
  .cuts .row { display:flex; gap:6px; align-items:center; font-size:13px; flex-wrap:wrap; }
  .cuts .idx { color:var(--sub); width:28px; flex:none; }
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
  <a href="/images"><button class="primary">画像へ →</button></a>
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
      row.appendChild(q); row.appendChild(kind); row.appendChild(ja); row.appendChild(del);
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
  .sec { background:var(--card); border:1px solid var(--line); border-radius:12px;
         margin-bottom:12px; overflow:hidden; }
  .sec.open { border-color:var(--accent); }
  .sechead { display:flex; align-items:center; gap:12px; padding:14px 16px; cursor:pointer; }
  .sechead:hover { background:#222a37; }
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
  .imgrow .ph2 { display:flex; align-items:center; justify-content:center; color:var(--sub); font-size:11px; }
  .imgrow .fields { flex:1; display:flex; flex-direction:column; gap:7px; min-width:0; }
  .imgrow .fields .frow { display:flex; gap:8px; align-items:center; }
  .imgrow .fields input, .imgrow .fields select { width:100%; }
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
  .adjust .crop { position:relative; width:320px; height:180px; background:#11151c; cursor:crosshair;
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
  <button class="ok" id="save">保存</button>
</header>
<main id="main">読み込み中…</main>
<script>
let DATA=null, CUTS=[], cutMap={}, OPEN=new Set(), adjustOpen=new Set();
function api(p,b){ return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(b)}).then(r=>r.json()); }
function setOpt(key,patch){ return api('/api/options',{key,patch}); }
function speakerColor(n){ if(/ずんだ/.test(n))return '#3fa34d'; if(/めたん|メタン/.test(n))return '#d85a9c'; return '#90a0b5'; }
function autosize(t){ t.style.height='auto'; t.style.height=(t.scrollHeight+2)+'px'; }
function imgUrl(ci,k){ const c=cutMap[ci+'_'+k]; return (c&&c.image)?('/img/'+ci+'_'+k+'?v='+Date.now()):null; }
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
  const r2=document.createElement('div'); r2.className='row'; r2.innerHTML='<span class="hint">余白</span>';
  const pad=document.createElement('input'); pad.type='number'; pad.min=0; pad.max=400; pad.step=4; pad.value=cut.pad||0; pad.style.width='62px';
  pad.title='contain余白px'; pad.onchange=()=>{ const n=parseInt(pad.value)||0; cut.pad=n||null; setOpt(key,{pad:n}); };
  const bg=document.createElement('input'); bg.type='color'; bg.value=cut.bg||'#eef1f5'; bg.title='余白色';
  bg.onchange=()=>{ cut.bg=bg.value; setOpt(key,{bg:bg.value}); };
  const bgc=document.createElement('button'); bgc.className='mini'; bgc.textContent='色既定';
  bgc.onclick=()=>{ cut.bg=null; bg.value='#eef1f5'; setOpt(key,{bg:null}); };
  const hideL=document.createElement('label'); hideL.className='chk';
  const hide=document.createElement('input'); hide.type='checkbox'; hide.checked=!!cut.hide;
  hide.onchange=()=>{ cut.hide=hide.checked; setOpt(key,{hide:hide.checked}); };
  hideL.appendChild(hide); hideL.appendChild(document.createTextNode(' 画像なし'));
  r2.appendChild(pad); r2.appendChild(bg); r2.appendChild(bgc); r2.appendChild(hideL);

  // 出典
  const attr=document.createElement('input'); attr.type='text'; attr.placeholder='出典(任意・CC-BY等)'; attr.value=cut.attribution||'';
  attr.onchange=()=>{ cut.attribution=attr.value; api('/api/attribution',{key,attribution:attr.value}); };

  // 差し替え / クロップ解除
  const r3=document.createElement('div'); r3.className='row';
  const fileL=document.createElement('label'); fileL.className='mini'; fileL.style.cursor='pointer'; fileL.textContent='差し替え';
  const file=document.createElement('input'); file.type='file'; file.accept='image/*'; file.style.display='none'; fileL.appendChild(file);
  const onNew=(fn)=>{ cutMap[key]=Object.assign({},cutMap[key],{image:fn}); cut.image=fn; render(); };
  file.onchange=()=>{ const f=file.files[0]; if(!f)return; const rd=new FileReader();
    rd.onload=async()=>{ const r=await api('/api/replace',{key,filename:f.name,dataB64:rd.result.split(',')[1],attribution:attr.value}); r.ok?onNew(r.filename):alert(r.message||'失敗'); };
    rd.readAsDataURL(f); };
  const cclr=document.createElement('button'); cclr.className='mini'; cclr.textContent='クロップ解除';
  cclr.onclick=()=>{ cut.crop=null; rectEl.style.display='none'; setOpt(key,{crop:null}); };
  r3.appendChild(fileL); r3.appendChild(cclr); r3.appendChild(fclr);

  const hint=document.createElement('div'); hint.className='hint'; hint.textContent='画像をドラッグ＝クロップ / 画像をドロップ＝差し替え';
  ctl.appendChild(fr); ctl.appendChild(filt); ctl.appendChild(r2); ctl.appendChild(attr); ctl.appendChild(r3); ctl.appendChild(hint);
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
      if(c.r-c.l<0.02||c.b-c.t<0.02){ drawCrop(); return; } cut.crop=c; setOpt(key,{crop:c}); drawCrop(); };
    crop.addEventListener('dragover',e=>{e.preventDefault(); crop.style.outline='2px dashed #ffd84d';});
    crop.addEventListener('dragleave',()=>crop.style.outline='');
    crop.addEventListener('drop',async(e)=>{ e.preventDefault(); crop.style.outline='';
      const dt=e.dataTransfer;
      if(dt.files&&dt.files.length){ const f=dt.files[0],rd=new FileReader();
        rd.onload=async()=>{ const r=await api('/api/replace',{key,filename:f.name,dataB64:rd.result.split(',')[1],attribution:attr.value}); r.ok?onNew(r.filename):alert(r.message||'失敗'); };
        rd.readAsDataURL(f); return; }
      let url=(dt.getData('text/uri-list')||dt.getData('text/plain')||'').split('\\n').find(s=>s&&!s.startsWith('#'))||'';
      if(!url) return;
      if(url.startsWith('data:image')){ const r=await api('/api/replace',{key,filename:'drop.png',dataB64:url.split(',')[1],attribution:attr.value}); r.ok?onNew(r.filename):alert(r.message||'失敗'); return; }
      const r=await api('/api/import-url',{key,url,attribution:attr.value}); r.ok?onNew(r.filename):alert(r.message||'失敗'); });
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

function render(){
  const m=document.getElementById('main'); m.innerHTML='';
  // theme
  const th=document.createElement('div'); th.className='theme';
  const ti=document.createElement('input'); ti.type='text'; ti.value=DATA.theme||''; ti.placeholder='テーマ';
  ti.onchange=()=>DATA.theme=ti.value; th.innerHTML='<span class="badge">テーマ</span>'; th.appendChild(ti);
  m.appendChild(th);

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
        r.innerHTML = u?`<img src="${u}">`:`<div class="ph2">#${k} 未取得</div>`;
        const q=document.createElement('input'); q.type='text'; q.className='q'; q.placeholder='英語の検索語';
        q.value=cut.image_query||''; q.onchange=()=>cut.image_query=q.value;
        const kind=document.createElement('select');
        kind.innerHTML='<option value="subject">被写体(ロゴ/人物/製品)</option><option value="ambient">雰囲気(イメージ)</option>';
        kind.value=cut.image_kind||'ambient'; kind.onchange=()=>cut.image_kind=kind.value;
        const ja=document.createElement('input'); ja.type='text'; ja.className='ja'; ja.placeholder='日本語(意味)';
        ja.value=cut.image_query_ja||''; ja.onchange=()=>cut.image_query_ja=ja.value;
        const refetch=document.createElement('button'); refetch.className='mini'; refetch.textContent=u?'再取得':'取得';
        refetch.title='検索語で画像を取得/取り直し'; refetch.onclick=async()=>{
          if(!cut.image_query){ alert('検索語を入れてください'); return; }
          refetch.textContent='取得中…'; refetch.disabled=true;
          const r=await api('/api/fetch',{ch:ci,ci:k,query:cut.image_query,kind:cut.image_kind});
          refetch.disabled=false;
          if(r.ok){ cutMap[ci+'_'+k]=Object.assign({},cutMap[ci+'_'+k]||{ch:ci,ci:k},{image:r.image,query:cut.image_query,kind:cut.image_kind}); render(); }
          else { refetch.textContent=u?'再取得':'取得'; alert(r.message||'取得失敗'); }
        };
        const adj=document.createElement('button'); adj.className='mini'; adj.textContent=adjustOpen.has(ci+'_'+k)?'調整を閉じる':'調整';
        adj.onclick=()=>{ const ky=ci+'_'+k; adjustOpen.has(ky)?adjustOpen.delete(ky):adjustOpen.add(ky); render(); };
        const del=document.createElement('button'); del.className='mini'; del.style.color='#c66'; del.style.background='transparent'; del.textContent='×';
        del.onclick=()=>{ cuts.splice(k,1); render(); };
        // 画像の右にフィールドを縦積み：検索語 → 日本語 → [kindプルダウン＋ボタン]
        const fields=document.createElement('div'); fields.className='fields';
        const row3=document.createElement('div'); row3.className='frow';
        row3.appendChild(kind); row3.appendChild(refetch); row3.appendChild(adj); row3.appendChild(del);
        fields.appendChild(q); fields.appendChild(ja); fields.appendChild(row3);
        r.appendChild(fields);
        il.appendChild(r);
        if(adjustOpen.has(ci+'_'+k)) il.appendChild(buildAdjust(ci,k));
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
        const ta=document.createElement('textarea'); ta.value=tn.text; ta.oninput=()=>{tn.text=ta.value; autosize(ta);};
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

Promise.all([fetch('/api/script').then(r=>r.json()), fetch('/api/cuts').then(r=>r.json())])
.then(([s,rev])=>{
  if(s.error){ document.getElementById('main').textContent=s.error; return; }
  DATA=s; CUTS=rev.cuts||[]; cutMap={}; CUTS.forEach(c=>cutMap[c.ch+'_'+c.ci]=c);
  if(DATA.chapters&&DATA.chapters.length) OPEN.add(0);  // 先頭は開いておく
  render();
});
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
        if path == "/images":
            self._html(IMAGE_PAGE)
            return
        if path == "/script":
            self._html(SCRIPT_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/story":
            self._html(STORY_PAGE.replace("__CSS__", _BASE_CSS))
            return
        if path == "/api/status":
            self._json({"dir": DIR, "status": pipeline_status()})
            return
        if path == "/api/script":
            data = load_script()
            self._json(data if data else {"error": "script.json がありません（先に台本生成）"})
            return
        if path == "/api/cuts":
            review = load_review()
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
                                    body.get("query"), body.get("kind")))
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
    global DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="docs/story", help="レビュー対象（review.json/画像のあるディレクトリ）")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    DIR = args.dir
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
