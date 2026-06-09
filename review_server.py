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


# ---- 純ロジック（テスト可能） ----

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

PAGE = """<!doctype html>
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
      <div class="hint">画像をドラッグで範囲選択＝クロップ</div>
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
        approve(); hint.textContent='画像をドラッグで範囲選択＝クロップ';
        load();  // 簡易：再読込で新imgのハンドラ再付与
      } else { hint.textContent='失敗: '+(r.message||''); }
    };
    reader.readAsDataURL(f);
  };

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

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
