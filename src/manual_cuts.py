"""
manual（人間が用意する想像イラスト）カットとマニフェストの生成モジュール

ifパートの空想イラストは自動では埋まらない＝「人間が差し込む穴」。本モジュールは
その穴を構造化する：各 manual カットに決まったドロップ先 manual_NN.png を割り当て、
- 画像が既に置いてあれば（人間が用意済み）→ それを使う（status=user）
- 無ければ → 画像なし扱い（status=placeholder）。動画側がプレースホルダカードを描く。
未準備でも動画は完成し（案A）、差し替えたい時は manual_NN.png を置いて再renderするだけ。

不足分は manifest.json（不足リスト＋プロンプト案＋ドロップ先）として書き出し、
人間が「何を・どこに置けばよいか」を一目で分かるようにする。

画像生成・描画はしない（プレースホルダ描画は動画側=日本語フォント同梱のRemotionが担当）。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DROP_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def find_existing_drop(out_dir, stem):
    """stem(例 manual_01)に対し、置かれている画像ファイル名を返す（無ければNone・純粋にFS参照）。"""
    for ext in DROP_EXTS:
        if os.path.exists(os.path.join(out_dir, stem + ext)):
            return stem + ext
    return None


def build_manual_cuts(specs, out_dir, prefix="manual"):
    """manual_cuts 仕様からカット情報を組み立てる。

    各 spec={label, prompt} に manual_NN.png のドロップ先を割り当て、既存画像の有無を見る。
    画像生成は一切しない（穴の宣言のみ）。

    Returns: [{label, prompt, target, file|None, status}]
      - target: 人間が画像を置くべきファイル名（例 manual_01.png）
      - file:   実在する画像ファイル名（無ければ None）
      - status: "user"（用意済み）/ "placeholder"（未用意）
    """
    out = []
    for i, spec in enumerate(specs, start=1):
        stem = f"{prefix}_{i:02d}"
        target = f"{stem}.png"
        existing = find_existing_drop(out_dir, stem)
        out.append({
            "label": spec.get("label", ""),
            "prompt": spec.get("prompt", ""),
            "image_prompt": spec.get("image_prompt", ""),
            "target": target,
            "file": existing,
            "status": "user" if existing else "placeholder",
        })
    return out


def write_manifest(out_dir, manuals, filename="manifest.json"):
    """不足画像マニフェストを書き出す（人間向け）。未用意(placeholder)のみ列挙。

    Returns: 未用意カット数。
    """
    missing = [m for m in manuals if m["status"] == "placeholder"]
    manifest = {
        "description": "ifパートの想像イラストの不足リスト。image_prompt を画像AIに貼って生成し、target のファイル名で drop_dir に置いて再renderすると差し替わります。",
        "drop_dir": os.path.abspath(out_dir),
        "missing": [
            {"target": m["target"], "label": m["label"], "prompt": m["prompt"],
             "image_prompt": m.get("image_prompt", "")}
            for m in missing
        ],
        "ready": [
            {"target": m["target"], "file": m["file"], "label": m["label"]}
            for m in manuals if m["status"] == "user"
        ],
    }
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    if missing:
        logger.info(f"想像イラスト未用意 {len(missing)}件 → {path}（target に置けば差し替わる）")
    return len(missing)
