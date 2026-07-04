#!/usr/bin/env python3
"""動画用の深度マップを生成する（2.5Dパララックス用・ローカル/無料）。

docs/story の各トピック画像 ch_*.{jpg,png,...} に対し、深度推定で <base>.depth.png
（grayscale・明=近/暗=遠）を出力する。video側の prep.mjs がこれを public へコピーし、
縦ショート(DialogueVideoShort)が ParallaxImage で「動画らしい」奥行きカメラ移動に使う。

- モデル: Depth Anything V2 Small（軽量。M1/MPSで数秒/枚）。
- 従量課金なし・ネット生成なし（初回だけモデルDL）。GPU(MPS)が無ければCPUにフォールバック。

使い方:
    pip install torch transformers pillow      # 初回のみ（Mac/ローカル）
    python3 make_depth.py --dir docs/story      # 既定。--force で再生成、--overwriteで既存上書き

依存（torch/transformers）は重いが、すべてローカル実行で無料。SVDのような動画生成より遥かに軽い。
"""
import argparse
import sys
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="docs/story", help="画像のあるディレクトリ")
    ap.add_argument("--model", default="depth-anything/Depth-Anything-V2-Small-hf")
    ap.add_argument("--overwrite", action="store_true", help="既存の .depth.png を上書き")
    args = ap.parse_args()

    try:
        import torch
        from PIL import Image
        from transformers import pipeline
    except ImportError:
        sys.exit("依存が不足: pip install torch transformers pillow を実行してください（Mac/ローカル）")

    src = Path(args.dir)
    if not src.is_dir():
        sys.exit(f"ディレクトリが見つかりません: {src}")

    # デバイス選択：Apple Silicon(MPS) > CUDA > CPU。
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    print(f"[depth] device={device} model={args.model}")

    pipe = pipeline("depth-estimation", model=args.model, device=device)

    # 対象＝トピック画像（.depth.png は除外）。同名 base の重複（.jpg/.png両方）も各々生成。
    targets = [
        p for p in sorted(src.iterdir())
        if p.suffix.lower() in IMG_EXTS and not p.name.lower().endswith(".depth.png")
    ]
    if not targets:
        sys.exit(f"画像が見つかりません: {src}")

    done = 0
    for p in targets:
        out = p.with_name(p.stem + ".depth.png")
        if out.exists() and not args.overwrite:
            continue
        try:
            img = Image.open(p).convert("RGB")
            result = pipe(img)
            depth = result["depth"]  # PIL Image（モデル出力。明=近の想定）
            # 元画像サイズに合わせ、グレースケールで保存（near=明/far=暗）。
            depth = depth.convert("L").resize(img.size)
            # 正規化（コントラストを最大化＝視差が効く）。
            import numpy as np
            arr = np.asarray(depth, dtype="float32")
            lo, hi = arr.min(), arr.max()
            if hi > lo:
                arr = (arr - lo) / (hi - lo) * 255.0
            Image.fromarray(arr.astype("uint8")).save(out)
            done += 1
            print(f"[depth] {p.name} -> {out.name}")
        except Exception as e:
            print(f"[depth] skip {p.name}: {e}")

    print(f"[depth] 完了: {done}枚生成（{src}）。video側は npm run prep が depth-manifest.json を作る。")


if __name__ == "__main__":
    main()
