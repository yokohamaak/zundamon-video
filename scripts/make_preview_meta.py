"""
既存meta.jsonから「演出入りプレビュー用meta」をオフライン生成する（Gemini/VOICEVOX不要＝課金ゼロ）。

目的: phase/effect・重ねエフェクト・manual想像イラストのプレースホルダを、音声を作り直さずに
Remotion Studio(ブラウザ)で確認するためのデモデータを作る。既存の digest.mp3 と画像を再利用する。

使い方（コンテナ可・無料）:
  python3 scripts/make_preview_meta.py --in docs/apod --out docs/apod-preview
確認（Mac側・描画はしない＝スタジオでスクラブ）:
  cd video && SRC_DIR=../docs/apod-preview npm run dev

注意: これはデモ用。phase/effectは位置ベースで機械割当（台本の意味は見ていない）。
本番の演出はGeminiが台本生成時に付ける（main_apod.py）。
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import apod_crop  # noqa: E402

# fact区間のいくつかの画像カットに割り当てるデモ用effect（画像transform系を見せる）。
DEMO_IMAGE_EFFECTS = ["zoom_punch", "shake", "glow_pulse", "flash"]


def assign_phases(n):
    """ターン数 n に対し intro/fact/if/outro を位置ベースで割り当てる（デモ用）。"""
    phases = []
    for i in range(n):
        r = i / max(1, n - 1)
        if i <= 0:
            phases.append("intro")
        elif r < 0.55:
            phases.append("fact")
        elif r < 0.9:
            phases.append("if")
        else:
            phases.append("outro")
    return phases


def build_preview(in_dir, out_dir):
    meta = json.load(open(os.path.join(in_dir, "meta.json"), encoding="utf-8"))
    script = meta["script"]
    n = len(script)
    phases = assign_phases(n)

    # 1) script に phase/effect を付与。
    #    - 既定は kenburns。fact区間の数ターンにデモeffectを散らす（画像transform系を見せる）。
    fact_idxs = [i for i, p in enumerate(phases) if p == "fact"]
    demo_at = set(fact_idxs[1 : 1 + len(DEMO_IMAGE_EFFECTS)])  # factの2番目以降に散らす
    eff_iter = iter(DEMO_IMAGE_EFFECTS)
    for i, turn in enumerate(script):
        turn["phase"] = phases[i]
        turn["effect"] = next(eff_iter) if i in demo_at else "kenburns"

    # 2) 既存topicsの画像カットを crop プールとして復元（file+label）。
    crops = [
        {"file": t["image"], "label": t.get("title")}
        for t in meta.get("topics", []) if t.get("image")
    ]
    # 3) manual想像イラストのプレースホルダを1枠（if区間に入る）。画像は置かない＝カード表示。
    manuals = [{
        "file": None,
        "label": "もしもの世界（想像イラスト）",
        "prompt": "ここに想像イラストが入ります。二重星に照らされ赤紫に染まった惑星の空と地平線。",
        "target": "manual_01.png",
    }]
    # 4) phase付きturns（start/end付き）で phase連動割当。
    phased = [{"start": t["start"], "end": t["end"], "phase": t["phase"]} for t in script]
    cuts = apod_crop.assign_cuts_by_phase(phased, crops, [], manuals)

    def to_topic(c):
        topic = {"title": c.get("label") or c.get("title"), "start": c["start"], "end": c["end"]}
        if c.get("file"):
            topic["image"] = c["file"]
        elif c.get("prompt") or c.get("target"):
            topic["note"] = c.get("prompt")
            topic["placeholder"] = c.get("target")
        return topic

    meta["topics"] = [to_topic(c) for c in cuts]
    meta["title"] = None  # ヘッダー無し運用に合わせる

    # 5) 出力dirへ meta + メディア（digest.mp3 / 画像）をコピー。
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    copied = 0
    for fn in os.listdir(in_dir):
        if fn == "digest.mp3" or fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            shutil.copy2(os.path.join(in_dir, fn), os.path.join(out_dir, fn))
            copied += 1

    placeholders = sum(1 for t in meta["topics"] if t.get("placeholder"))
    print(f"[preview] meta生成: {out_dir}/meta.json")
    print(f"[preview] phase配分: " + ", ".join(f"{p}={phases.count(p)}" for p in ("intro", "fact", "if", "outro")))
    print(f"[preview] デモeffect: {sorted({script[i]['effect'] for i in demo_at})} / カット{len(meta['topics'])}（プレースホルダ{placeholders}）")
    print(f"[preview] メディア{copied}件コピー")
    print(f"[preview] 確認: cd video && SRC_DIR=../{out_dir} npm run dev")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default="docs/apod")
    ap.add_argument("--out", dest="out_dir", default="docs/apod-preview")
    a = ap.parse_args()
    build_preview(a.in_dir, a.out_dir)
