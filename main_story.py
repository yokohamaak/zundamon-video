"""
IT技術史ストーリー動画パイプライン - メインスクリプト

テーマ → Geminiで章立て掛け合い台本 → VOICEVOX音声 → meta.json + digest.mp3 + 章画像
を生成する。出力ディレクトリは video 側 prep が読む docs/<dir> 形式。

VOICEVOXは自己ホストで課金なし、Gemini(text)・画像庫(Wikimedia/Pexels/Pixabay)は無料枠。
秘密情報は環境変数（GEMINI_API_KEY / PEXELS_API_KEY / PIXABAY_API_KEY）。

使い方:
    python3 main_story.py --config config/config.story.yaml --output-dir docs/story
    python3 main_story.py --script-only        # 台本JSONのみ
    python3 main_story.py --no-images          # 画像取得skip（全プレースホルダ）

段階実装:
  Phase 0（実装済）: 台本生成 / --script-only
  Phase 1（実装済）: VOICEVOX音声 + meta.json（--no-images でプレースホルダ動画）
  Phase 2（未実装）: 画像取得（Wikimedia/Pexels/Pixabay）→ image_fetch
"""
import argparse
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src import story_script, tts_voicevox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


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


def chapter_image_name(chapter: int) -> str:
    """章番号 → 画像ファイル名（prep.mjs が IMG_EXTS で拾う決め打ち名）。"""
    return f"ch_{chapter:02d}.png"


def build_chapter_topics(segments, turns, chapters, image_status=None, attributions=None):
    """章区間 → meta.topics（純関数）。

    各章区間に [0, total] を隙間なく被覆する start/end を割り当てる（切替はターン境界）。
    画像が ready なら image を、未取得（Phase1/失敗）なら placeholder 枠を置く。

    Args:
        segments: assign_sections_to_turns の出力 [{chapter, section, turns:[idx]}]（出現順）
        turns: TTS後のターン時刻 [{start, end, ...}]（script順）
        chapters: 章メタ [{title, image_query, image_kind, section}]
        image_status: {chapter: "ready"} 取得済みの章（無ければ全プレースホルダ）
        attributions: {chapter: "出典文字列"} Wikimedia帰属（任意）
    Returns:
        meta.topics のリスト（時刻順・[0,total]被覆）
    """
    image_status = image_status or {}
    attributions = attributions or {}
    total = turns[-1]["end"] if turns else 0.0
    n = len(segments)
    topics = []
    for i, seg in enumerate(segments):
        ch = seg["chapter"]
        idxs = seg["turns"]
        start = 0.0 if i == 0 else turns[idxs[0]]["start"]
        end = total if i == n - 1 else turns[segments[i + 1]["turns"][0]]["start"]
        meta_ch = chapters[ch] if 0 <= ch < len(chapters) else {}
        topic = {
            "title": meta_ch.get("title"),
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "section": meta_ch.get("section") or seg["section"],  # chaptersの構造を真とする
            "chapter": ch,
            "chapterTotal": len(chapters),
        }
        if image_status.get(ch) == "ready":
            topic["image"] = chapter_image_name(ch)
            if attributions.get(ch):
                topic["credit"] = attributions[ch]
        else:
            # 未取得：動画側がプレースホルダカードを描く。差し替え先と検索語を案内。
            topic["note"] = meta_ch.get("image_query") or meta_ch.get("title")
            topic["placeholder"] = chapter_image_name(ch)
        topics.append(topic)
    return topics


def build_credits(config, attributions=None):
    """動画に表示するクレジット（VOICEVOX規約＋画像出典）。

    Wikimedia由来の帰属(attributions)があれば列挙（CC-BYの帰属表示）。Pexels/Pixabayは帰属不要。
    """
    creds = [f"VOICEVOX:{name}" for name in config.get("tts_voicevox", {}).get("speakers", {})]
    seen = []
    for a in (attributions or {}).values():
        if a and a not in seen:
            seen.append(a)
    if seen:
        creds.append("画像出典: " + " / ".join(seen))
    else:
        creds.append("画像: Wikimedia Commons / Pexels / Pixabay（商用可ライセンス）")
    return creds


def build_meta(script_result, turns, config, now_iso, image_status=None, attributions=None):
    """動画(video/)が読む meta.json 構造を組み立てる（純関数・テスト可能）。

    - script に VOICEVOX のターン情報(start/end/sentences=字幕単位)を合流
    - speakers に性別を付与（config.characters_gender の定義順＝画面配置。無ければ登場順）
    - topics は章区間ごと（build_chapter_topics）。meta.title=theme。
    """
    base = script_result["script"]
    if len(turns) != len(base):
        raise ValueError(f"ターン数とタイムスタンプ数が不一致: {len(base)} != {len(turns)}")

    script = []
    for turn, t in zip(base, turns):
        script.append({**turn, "start": t["start"], "end": t["end"], "sentences": t["sentences"]})

    # speakers の並び順 = 動画の画面配置（[0]=左 / [1]=右）。
    # config.characters_gender の定義順で固定（台本の発話順に依存しない）。
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

    segments = story_script.assign_sections_to_turns(base)
    chapters = script_result.get("chapters", [])

    return {
        "generated_at": now_iso,
        "title": script_result.get("theme"),
        "speakers": speakers,
        "topics": build_chapter_topics(segments, turns, chapters, image_status, attributions),
        "credits": build_credits(config, attributions),
        "script": script,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.story.yaml")
    parser.add_argument("--output-dir", default="docs/story")
    parser.add_argument("--script-only", action="store_true",
                        help="台本生成までで停止（VOICEVOX不要。台本をJSON出力）")
    parser.add_argument("--from-script", default=None,
                        help="既存のscript.jsonを使いGemini生成をskip")
    parser.add_argument("--no-images", action="store_true",
                        help="画像取得を無効化し全プレースホルダで動画化する（Phase1検証用）")
    args = parser.parse_args()

    load_dotenv()  # .env を自動読込（source忘れ対策・既存環境変数は優先）
    config = load_config(args.config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== IT技術史ストーリー動画パイプライン開始 ===")

    # 1. 台本：既存scriptを使う（--from-script）か、Geminiで生成する
    if args.from_script:
        with open(args.from_script, encoding="utf-8") as f:
            script_result = json.load(f)
        story_script.normalize_turns(script_result["script"], script_result.get("chapters"))
        logger.info(f"既存台本を使用: {args.from_script}（{len(script_result['script'])}ターン）")
    else:
        script_result = story_script.generate_story_script(config)

    # --script-only はここまでで停止（音声/metaはskip）
    if args.script_only:
        out_path = out_dir / "script.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(script_result, f, ensure_ascii=False, indent=2)
        logger.info(
            f"=== 台本のみ生成完了: {out_path} "
            f"（{len(script_result['script'])}ターン・{len(script_result.get('chapters', []))}章）==="
        )
        return

    # 2. 画像取得（Phase2）。現状は未実装なので image_status は空＝全プレースホルダ。
    image_status, attributions = {}, {}
    if not args.no_images:
        logger.info("画像取得（Phase2）は未実装のため全章プレースホルダで続行します（--no-images相当）。")

    # 3. VOICEVOXで音声＋厳密タイムスタンプ（文単位字幕付き）
    mp3_path = out_dir / "digest.mp3"
    turns = tts_voicevox.generate_audio(script_result["script"], config, str(mp3_path))

    # 4. meta.json
    now_iso = datetime.now(JST).isoformat()
    meta = build_meta(script_result, turns, config, now_iso, image_status, attributions)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    dur = meta["topics"][-1]["end"] if meta["topics"] else 0.0
    logger.info(f"=== 完了: {out_dir} （{len(meta['script'])}ターン・{len(meta['topics'])}章・{dur:.1f}秒）===")
    logger.info(f"動画化: cd video && SRC_DIR=../{out_dir} npm run render")


if __name__ == "__main__":
    main()
