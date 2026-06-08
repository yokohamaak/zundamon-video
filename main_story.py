"""
APOD if動画パイプライン - メインスクリプト

NASA APOD → Geminiでif掛け合い台本 → VOICEVOX音声 → meta.json + digest.mp3 + 画像
を生成する。出力ディレクトリは video 側 prep が読む docs/<dir> 形式。

音声本番の news digest（main.py）とは独立。VOICEVOXは自己ホストで課金なし、
Gemini(text)・NASA APODは無料枠。秘密情報は環境変数（GEMINI_API_KEY / NASA_API_KEY）。

使い方:
    python3 main_apod.py --config config/config.apod.yaml --output-dir docs/apod [--date YYYY-MM-DD]
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src import apod_client, apod_crop, apod_script, manual_cuts, nasa_images, tts_voicevox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
IMAGE_FILENAME = "apod.jpg"


def load_dotenv(path=None):
    """.env を読んで os.environ に流す（標準ライブラリのみ・依存追加なし）。

    `set -a; source .env` のし忘れで GEMINI_API_KEY 等が未設定になる事故を防ぐ。
    既に環境にある値は上書きしない（実環境の値を優先）。スクリプトの隣の .env を見る。
    """
    import os

    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def load_config(config_path: str) -> dict:
    import yaml  # 遅延import（テストに依存を持ち込まない）

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_credits(apod: dict, config: dict, stocks: list = None) -> list:
    """動画に表示するクレジット（VOICEVOX規約＋APOD画像＋stock実写があれば出典）。"""
    creds = [f"VOICEVOX:{name}" for name in config.get("tts_voicevox", {}).get("speakers", {})]
    owner = apod.get("copyright")
    creds.append("画像: NASA APOD" + (f" / {owner}" if owner else "（Public Domain）"))
    if stocks:
        creds.append("実写: NASA images.nasa.gov（Public Domain）")
    return creds


def _cut_to_topic(c: dict) -> dict:
    """割当済みカット → meta.topic。manualで画像未用意(file=None)はプレースホルダ枠にする。"""
    topic = {
        "title": c.get("label") or c.get("title"),
        "start": c["start"],
        "end": c["end"],
    }
    if c.get("file"):
        topic["image"] = c["file"]
        # focus方式（切り出さず元画像＋枠）のときは枠座標と画像アスペクトも渡す。
        if c.get("focus"):
            topic["focus"] = c["focus"]
        if c.get("image_aspect"):
            topic["image_aspect"] = c["image_aspect"]
    elif c.get("prompt") or c.get("target"):
        # 想像イラスト未用意：動画側がプレースホルダカードを描く。差し替え先と案内を渡す。
        topic["note"] = c.get("prompt")
        topic["placeholder"] = c.get("target")
    return topic


def build_topics(script_result: dict, turns: list, image_filename: str,
                 cuts: list, stocks: list = None, manuals: list = None) -> list:
    """
    中央ビジュアルの topics を組み立てる（純関数）。
    - stock/manual があれば: phase連動で fact=stock / if=manual / 他=APODクロップ（assign_cuts_by_phase）。
    - 補助素材が無くcropだけあれば: 全ターンに均等割り（従来動作）。
    - どれも無ければ: APOD画像1枚を全尺に渡す（フォールバック）。
    画像ありの topic では video側が title を画面に出さないため、label(vision prose)は安全に保持できる。
    """
    total = turns[-1]["end"] if turns else 0.0
    cuts = cuts or []
    stocks = stocks or []
    manuals = manuals or []
    assigned = []
    if stocks or manuals:
        # ターンに phase を合流（台本順=turns順で一致。build_meta側でlen検証済み）。
        phased = [{**t, "phase": s.get("phase", "fact")}
                  for t, s in zip(turns, script_result["script"])]
        assigned = apod_crop.assign_cuts_by_phase(phased, cuts, stocks, manuals)
    elif cuts:
        assigned = apod_crop.assign_cut_times(cuts, turns)
    if assigned:
        return [_cut_to_topic(c) for c in assigned]
    return [{
        "title": script_result.get("topic_title"),
        "image": image_filename,
        "start": 0.0,
        "end": total,
    }]


def build_meta(apod: dict, script_result: dict, turns: list, config: dict,
               image_filename: str, now_iso: str, cuts: list = None,
               stocks: list = None, manuals: list = None) -> dict:
    """
    動画(video/)が読む meta.json 構造を組み立てる（純関数・テスト可能）。
    - script に VOICEVOX のターン情報(start/end/sentences=字幕単位)を合流
    - speakers に性別を付与（アバター割当用。configのcharacters_gender、無ければ登場順）
    - topics は画像プラン(cuts)があれば複数カット、無ければAPOD1枚を全尺（build_topics）
    """
    base = script_result["script"]
    if len(turns) != len(base):
        raise ValueError(f"ターン数とタイムスタンプ数が不一致: {len(base)} != {len(turns)}")

    script = []
    for turn, t in zip(base, turns):
        script.append({**turn, "start": t["start"], "end": t["end"], "sentences": t["sentences"]})

    # speakers の並び順 = 動画の画面配置（[0]=左 / [1]=右）。
    # config.characters_gender の定義順で固定（台本の発話順に依存しない）。
    # configに無い話者だけ登場順で後ろに付ける。
    gmap = config.get("characters_gender", {})
    seen = []
    for t in script:
        if t["speaker"] not in seen:
            seen.append(t["speaker"])
    order = [n for n in gmap if n in seen] + [n for n in seen if n not in gmap]
    speakers = [
        {"name": n, "gender": gmap.get(n, "female" if i == 0 else "male")}
        for i, n in enumerate(order)
    ]

    return {
        "generated_at": now_iso,
        "speakers": speakers,
        "topics": build_topics(script_result, turns, image_filename, cuts, stocks, manuals),
        "credits": build_credits(apod, config, stocks),
        "script": script,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.apod.yaml")
    parser.add_argument("--output-dir", default="docs/apod")
    parser.add_argument("--date", default=None, help="APODの日付 YYYY-MM-DD（未指定は当日）")
    parser.add_argument("--script-only", action="store_true",
                        help="台本生成までで停止（VOICEVOX不要。画像DL/音声/metaはskipし台本をJSON出力）")
    parser.add_argument("--from-script", default=None,
                        help="既存のscript.jsonを使いGemini生成をskip（今の台本のまま音声/動画化する用）")
    parser.add_argument("--no-image-plan", action="store_true",
                        help="Gemini visionの画像プラン(複数カット)を無効化しAPOD1枚で動画化する")
    parser.add_argument("--no-stock", action="store_true",
                        help="images.nasa.govのstock実写取得を無効化する")
    parser.add_argument("--no-manual", action="store_true",
                        help="ifパートのmanual想像イラスト枠/マニフェストを無効化する")
    args = parser.parse_args()

    load_dotenv()  # .env を自動読込（source忘れ対策・既存環境変数は優先）
    config = load_config(args.config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== APOD if動画パイプライン開始 ===")

    # 1. APOD取得
    apod = apod_client.fetch_apod(date=args.date or config.get("nasa", {}).get("date"))
    logger.info(f"APOD: {apod['date']} / {apod['title']} (media={apod['media_type']})")
    if not apod["image_url"]:
        raise SystemExit(
            "この日のAPODは画像がありません（動画日でサムネ無し）。--date で画像の日を指定してください。"
        )

    # 2. if台本：既存scriptを使う（--from-script）か、Geminiで生成する
    if args.from_script:
        with open(args.from_script, encoding="utf-8") as f:
            script_result = json.load(f)
        # 旧台本(phase/effect未付与)でも動画側が有効値を受け取れるよう補完。
        apod_script.normalize_turns(script_result["script"])
        logger.info(f"既存台本を使用: {args.from_script}（{len(script_result['script'])}ターン）")
    else:
        script_result = apod_script.generate_if_script(apod, config)

    # --script-only はここまでで停止（画像DL/音声/metaはskip）
    if args.script_only:
        out_path = out_dir / "script.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(script_result, f, ensure_ascii=False, indent=2)
        logger.info(f"=== 台本のみ生成完了: {out_path} （{len(script_result['script'])}ターン）===")
        return

    image_path = out_dir / IMAGE_FILENAME
    apod_client.download_image(apod["image_url"], str(image_path))

    # 3. 画像プラン（Gemini visionでクロップ→複数カット風＝単調さ解消）。失敗時は1枚運用へフォールバック。
    cuts = None
    ip = config.get("image_plan", {})
    if ip.get("enable", True) and not args.no_image_plan:
        # mode=focus（既定）: 切り出さず元画像＋注目枠でアノテーション。
        # mode=crop: 従来の切り出し（cut_NN.jpg）。
        mode = ip.get("mode", "focus")
        count = int(ip.get("count", apod_crop.DEFAULT_COUNT))
        vision_model = config.get("models", {}).get("vision", "gemini-2.5-flash")
        try:
            if mode == "crop":
                cuts = apod_crop.build_image_plan(str(image_path), apod, str(out_dir), count, vision_model)
            else:
                cuts = apod_crop.build_focus_plan(str(image_path), apod, str(out_dir), count, vision_model)
            logger.info(f"画像プラン({mode}): {len(cuts)}カット")
        except Exception as e:  # noqa: BLE001 - 失敗してもAPOD1枚で動画化は続行
            logger.warning(f"画像プラン生成に失敗、APOD1枚運用にフォールバック: {e}")
            cuts = None

    # 3.5 stock実写（images.nasa.gov・無料/PD）をfactパート補助に取得。検索語は台本のstock_queries。
    #     失敗・該当なしは空でスキップ（APODクロップのみで続行）。
    stocks = None
    sc = config.get("stock", {})
    queries = apod_script._clean_queries(script_result.get("stock_queries"))
    if sc.get("enable", True) and queries and not args.no_stock:
        try:
            stocks = nasa_images.fetch_stock_images(
                queries, str(out_dir),
                per_query=int(sc.get("per_query", 1)),
                max_total=int(sc.get("max_total", 4)),
            )
            logger.info(f"stock実写: {len(stocks)}枚（検索語{len(queries)}個）")
        except Exception as e:  # noqa: BLE001 - 失敗してもクロップのみで続行
            logger.warning(f"stock取得に失敗、クロップのみで続行: {e}")
            stocks = None

    # 3.6 manual想像イラスト（ifパートの空想画＝人間が差し込む穴）。画像生成はしない。
    #     既存のmanual_NN.pngがあれば使い、無ければプレースホルダ枠＋不足マニフェストを出す。
    manuals = None
    mc = config.get("manual", {})
    manual_specs = script_result.get("manual_cuts") or []
    if mc.get("enable", True) and manual_specs and not args.no_manual:
        manuals = manual_cuts.build_manual_cuts(manual_specs, str(out_dir))
        manual_cuts.write_manifest(str(out_dir), manuals)
        ready = sum(1 for m in manuals if m["status"] == "user")
        logger.info(f"想像イラスト: {len(manuals)}枠（用意済み{ready} / 未用意{len(manuals) - ready}）")

    # 4. VOICEVOXで音声＋厳密タイムスタンプ（文単位字幕付き）
    mp3_path = out_dir / "digest.mp3"
    turns = tts_voicevox.generate_audio(script_result["script"], config, str(mp3_path))

    # 5. meta.json
    now_iso = datetime.now(JST).isoformat()
    meta = build_meta(apod, script_result, turns, config, IMAGE_FILENAME, now_iso,
                      cuts=cuts, stocks=stocks, manuals=manuals)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"=== 完了: {out_dir} （{len(meta['script'])}ターン・{meta['topics'][0]['end']:.1f}秒）===")
    print_manual_summary(manuals, out_dir)
    logger.info("動画化: cd video && SRC_DIR=../docs/apod npm run render")


def print_manual_summary(manuals, out_dir):
    """準備が必要なIF想像イラストを実行の最後に人間向けに一覧表示する。"""
    missing = [m for m in (manuals or []) if m["status"] == "placeholder"]
    if not missing:
        if manuals:
            logger.info("IF想像イラストは全て用意済み。差し替え不要。")
        return
    lines = [
        "",
        "──────────────────────────────────────────",
        f"  IF用に準備すると良い画像: {len(missing)}枚（未用意なら自動でプレースホルダ表示）",
        f"  置き場所: {os.path.abspath(out_dir)}/",
        "──────────────────────────────────────────",
    ]
    for m in missing:
        lines.append(f"  ● {m['target']}  「{m['label']}」")
        if m.get("prompt"):
            lines.append(f"      内容: {m['prompt']}")
        if m.get("image_prompt"):
            lines.append(f"      画像AI用プロンプト(コピペ可): {m['image_prompt']}")
    lines += [
        "──────────────────────────────────────────",
        "  画像AI用プロンプトを好きな画像生成AIに貼って作成 → 上のファイル名で置いて再render",
        "  （差し替えにmain_apod再実行は不要）。一覧は "
        f"{os.path.join(out_dir, 'manifest.json')} にも出力済み。",
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
