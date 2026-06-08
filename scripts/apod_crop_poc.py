"""
APODクロップ領域 Gemini vision 単発実証（POC / 動作確認用CLI）

ロジックは src/apod_crop.py に集約済み。本スクリプトは画像1枚を渡して
クロップ案の取得→切り出し→保存を単体で試すための薄いCLI。
（本番パイプラインは main_apod.py が同モジュールを使う）

実行:
    set -a; source .env; set +a
    python3 scripts/apod_crop_poc.py [--image docs/apod/apod.jpg] [--n 5] [--out docs/apod/crops]
依存: google-genai, Pillow（pip --break-system-packages）
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import apod_crop  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("apod_crop_poc")


def load_apod_meta(image_path):
    """同ディレクトリの meta.json / script.json から題材情報を拾う（あれば）。"""
    d = os.path.dirname(image_path)
    for name in ("meta.json", "script.json"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
                title = m.get("title") or m.get("topic_title")
                expl = m.get("explanation") or m.get("if_premise")
                if title or expl:
                    return title, expl
            except Exception:  # noqa: BLE001
                pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="docs/apod/apod.jpg")
    ap.add_argument("--n", type=int, default=apod_crop.DEFAULT_COUNT)
    ap.add_argument("--out", default=None, help="クロップ出力先（既定: <image>のディレクトリ/crops）")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--title", default=None)
    ap.add_argument("--explanation", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.image):
        logger.error(f"画像がありません: {args.image}")
        sys.exit(1)

    title, expl = args.title, args.explanation
    if not title and not expl:
        title, expl = load_apod_meta(args.image)
    logger.info(f"題材: title={title!r}")

    crops = apod_crop.plan_crops(args.image, title, expl, args.n, args.model)
    out_dir = args.out or os.path.join(os.path.dirname(args.image), "crops")
    rendered = apod_crop.render_crops(args.image, crops, out_dir)

    with open(os.path.join(out_dir, "crops.json"), "w", encoding="utf-8") as f:
        json.dump({"image": args.image, "crops": rendered}, f, ensure_ascii=False, indent=2)
    logger.info(f"プラン保存: {out_dir}/crops.json（有効{len(rendered)}件）")
    print(json.dumps(rendered, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
