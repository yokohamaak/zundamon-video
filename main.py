"""
ニュース自動音声生成システム - メインスクリプト（TTS版）
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from src.collector import (
    fetch_articles,
    filter_new_articles,
    load_cache,
    mark_as_used,
    merge_into_cache,
    save_cache,
)
from src.aligner import align_sentences
from src.gemini_client import generate_digest
from src.tts_client import generate_audio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def load_config(config_path: str) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def update_web(script: list, timestamps: list, docs_dir: Path):
    now = datetime.now(JST)
    session = "朝版" if now.hour < 12 else "夜版"

    # アライメント未実行/失敗でも字幕が最低限ターン単位で動くよう、
    # start/endが無いターンにはTTSの近似タイムスタンプを補う（既存値は尊重）。
    # これによりWhisperがクラッシュしても「字幕が全く動かない」配信を防ぐ。
    if timestamps and len(timestamps) == len(script):
        merged = []
        for turn, ts in zip(script, timestamps):
            t = dict(turn)
            if "start" not in t:
                t["start"] = round(ts.get("start", 0), 3)
            if "end" not in t:
                t["end"] = round(ts.get("end", 0), 3)
            merged.append(t)
        script = merged

    meta = {
        "generated_at": now.isoformat(),
        "session": session,
        "script": script,
    }
    meta_path = docs_dir / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(f"meta.json更新完了: {session}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output-dir", default="docs")
    parser.add_argument("--align-only", action="store_true", help="既存音声のアライメントのみ実行")
    parser.add_argument("--skip-align", action="store_true", help="アライメントをスキップして生成のみ実行")
    parser.add_argument("--use-words", action="store_true", help="単語単位タイムスタンプ方式でアライメント（試験）")
    args = parser.parse_args()

    docs_dir = Path(args.output_dir)
    mp3_path = docs_dir / "digest.mp3"
    config = load_config(args.config)

    if args.align_only:
        logger.info(f"=== アライメントのみ実行 ({docs_dir}) ===")
        meta_path = docs_dir / "meta.json"
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        script = meta["script"]
        timestamps = [{"start": t.get("start", 0), "end": t.get("end", 0)} for t in script]
        aligned_script = align_sentences(str(mp3_path), script, timestamps, use_words=args.use_words)
        update_web(aligned_script, timestamps, docs_dir)
        logger.info("=== 完了 ===")
        return

    logger.info(f"=== ニュースダイジェスト生成開始 ({docs_dir}) ===")

    sources = config["sources"]["rss"]
    lookback_hours = config["collection"]["lookback_hours"]
    cache_days = config["collection"]["cache_days"]

    cache = load_cache()
    articles = fetch_articles(sources, lookback_hours)

    if not articles:
        logger.warning("記事が収集できませんでした。処理を終了します。")
        sys.exit(0)

    merge_into_cache(articles, cache)
    new_articles = filter_new_articles(articles, cache)

    if not new_articles:
        logger.warning("新規記事がありません。処理を終了します。")
        save_cache(cache, cache_days)
        sys.exit(0)

    script, used_hashes = generate_digest(new_articles, config)

    docs_dir.mkdir(parents=True, exist_ok=True)
    timestamps = generate_audio(script, config, str(mp3_path))
    if args.skip_align:
        update_web(script, timestamps, docs_dir)
    else:
        aligned_script = align_sentences(str(mp3_path), script, timestamps, use_words=args.use_words)
        update_web(aligned_script, timestamps, docs_dir)

    mark_as_used(used_hashes, cache)
    save_cache(cache, cache_days)

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
