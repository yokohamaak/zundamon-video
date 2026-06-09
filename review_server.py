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
  .thumb { aspect-ratio:16/9; background:#0c0f15; display:flex; align-items:center;
           justify-content:center; }
  .thumb img { width:100%; height:100%; object-fit:contain; }
  .thumb .ph { color:var(--sub); font-size:14px; }
  .body { padding:12px 14px; display:flex; flex-direction:column; gap:8px; }
  .title { font-weight:700; font-size:15px; }
  .meta { font-size:12px; color:var(--sub); display:flex; gap:8px; flex-wrap:wrap; }
  .kind { padding:1px 8px; border-radius:999px; background:var(--line); }
  input[type=text] { width:100%; font:inherit; font-size:12px; padding:6px 8px;
           background:#0c0f15; color:var(--fg); border:1px solid var(--line); border-radius:6px; }
  .row { display:flex; gap:8px; }
  .row button { flex:1; padding:7px 10px; font-size:13px; }
  label.file { flex:1; }
  label.file input { display:none; }
  .hint { color:var(--sub); font-size:12px; }
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

function card(c){
  const key = `${c.ch}_${c.ci}`;
  const el = document.createElement('div');
  el.className = 'card' + (c.approved ? ' approved':'');
  el.dataset.key = key;
  const img = c.image
    ? `<img src="/img/${key}?v=${Date.now()}" alt="">`
    : `<span class="ph">画像なし（プレースホルダ）</span>`;
  el.innerHTML = `
    <div class="thumb">${img}</div>
    <div class="body">
      <div class="title">${c.title || '(無題)'}</div>
      <div class="meta"><span class="kind">${c.kind}</span><span>検索: ${c.query||'-'}</span></div>
      <input type="text" class="attr" placeholder="出典（任意・CC-BY等）" value="${c.attribution||''}">
      <div class="row">
        <button class="ok approve">${c.approved?'承認済み':'OK'}</button>
        <label class="file"><button type="button" class="repl">差し替え</button>
          <input type="file" accept="image/*"></label>
      </div>
      <div class="hint"></div>
    </div>`;
  // OK
  el.querySelector('.approve').onclick = async ()=>{
    const r = await api('/api/approve', {key, approved:true});
    if(r.ok){ c.approved=true; el.classList.add('approved');
      el.querySelector('.approve').textContent='承認済み'; refreshCount(); }
  };
  // 出典編集（blurで保存）
  el.querySelector('.attr').onchange = async (e)=>{
    await api('/api/attribution', {key, attribution:e.target.value});
    c.attribution = e.target.value;
  };
  // 差し替え（base64でPOST）
  const fileInput = el.querySelector('input[type=file]');
  el.querySelector('.repl').onclick = ()=> fileInput.click();
  fileInput.onchange = (e)=>{
    const f = e.target.files[0]; if(!f) return;
    const hint = el.querySelector('.hint'); hint.textContent = '送信中…';
    const reader = new FileReader();
    reader.onload = async ()=>{
      const b64 = reader.result.split(',')[1];
      const attr = el.querySelector('.attr').value;
      const r = await api('/api/replace', {key, filename:f.name, dataB64:b64, attribution:attr});
      if(r.ok){
        c.image=r.filename; c.approved=true;
        el.querySelector('.thumb').innerHTML = `<img src="/img/${key}?v=${Date.now()}">`;
        el.classList.add('approved'); el.querySelector('.approve').textContent='承認済み';
        hint.textContent=''; refreshCount();
      } else { hint.textContent='失敗: '+(r.message||''); }
    };
    reader.readAsDataURL(f);
  };
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
