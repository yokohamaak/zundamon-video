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


def chapter_image_name(chapter: int, cut: int = 0) -> str:
    """章番号・カット番号 → 画像ファイル名（prep.mjs が IMG_EXTS で拾う決め打ち名）。"""
    return f"ch_{chapter:02d}_{cut:02d}.png"


def build_chapter_topics(segments, turns, chapters, image_files=None, attributions=None, cut_opts=None):
    """章区間 → meta.topics（純関数）。

    各章区間を image_cuts の数で複数カットに分割し、[0, total] を隙間なく被覆する
    start/end を割り当てる（切替はターン境界・章内も均等割り）。画像が取得済なら image を、
    未取得（失敗/--no-images）なら placeholder 枠を置く。章バッジ情報(chapter/title/section)は全カットに付与。

    Args:
        segments: assign_sections_to_turns の出力 [{chapter, section, turns:[idx]}]（出現順）
        turns: TTS後のターン時刻 [{start, end, ...}]（script順）
        chapters: 章メタ [{title, section, image_cuts:[{image_query,image_kind}]}]
        image_files: {(chapter, cut): "ch_NN_MM.jpg"} 取得済画像の実ファイル名（無ければ全プレースホルダ）
        attributions: {(chapter, cut): "出典文字列"} 帰属（任意）
    Returns:
        meta.topics のリスト（時刻順・[0,total]被覆）
    """
    image_files = image_files or {}
    attributions = attributions or {}
    total = turns[-1]["end"] if turns else 0.0
    nseg = len(segments)
    trivia_total = sum(1 for c in chapters if c.get("section") == "trivia")
    trivia_seen = 0
    topics = []
    for si, seg in enumerate(segments):
        ch = seg["chapter"]
        idxs = seg["turns"]
        meta_ch = chapters[ch] if 0 <= ch < len(chapters) else {}
        sec = meta_ch.get("section") or seg["section"]  # chaptersの構造を真とする
        if sec == "trivia":
            trivia_seen += 1
        cuts = meta_ch.get("image_cuts") or [{}]
        # 章区間の時間範囲 [seg_start, seg_end)。章間も連結（[0,total]被覆）。
        seg_start = 0.0 if si == 0 else turns[idxs[0]]["start"]
        seg_end = total if si == nseg - 1 else turns[segments[si + 1]["turns"][0]]["start"]
        # 章内ターンを cut 数で分割（カット数は min(cuts, ターン数)＝ターンより多い画像は出さない）。
        ncut = max(1, min(len(cuts), len(idxs)))
        for ci in range(ncut):
            lo = ci * len(idxs) // ncut
            cstart = seg_start if ci == 0 else turns[idxs[lo]]["start"]
            if ci == ncut - 1:
                cend = seg_end
            else:
                hi = (ci + 1) * len(idxs) // ncut
                cend = turns[idxs[hi]]["start"]
            cut = cuts[ci] if ci < len(cuts) else {}
            topic = {
                "title": meta_ch.get("title"),
                "start": round(float(cstart), 3),
                "end": round(float(cend), 3),
                "section": sec,
                "chapter": ch,
                "chapterTotal": len(chapters),
            }
            if sec == "trivia":
                # 「実は」ネタの通し番号（章バッジ「実は ①②③」用）。
                topic["triviaIndex"] = trivia_seen
                topic["triviaTotal"] = trivia_total
            key = (ch, ci)
            opt = (cut_opts or {}).get(key, {})
            fname = image_files.get(key)
            if opt.get("hide"):
                # レビューで「画像なし」を選択＝中央ビジュアルを出さず黒板のみ。
                topic["blank"] = True
            elif fname:
                topic["image"] = fname
                # subject(ロゴ・記号・製品)は端が切れると意味を失うため contain で全体表示。
                # ambient(写真)は cover で枠を埋める（既定）。レビュー指定(opt.fit)があれば優先。
                if opt.get("fit"):
                    topic["fit"] = opt["fit"]
                elif cut.get("image_kind") == "subject":
                    topic["fit"] = "contain"
                if opt.get("crop"):
                    topic["crop"] = opt["crop"]
                if opt.get("filter"):
                    topic["filter"] = opt["filter"]
                if opt.get("pad"):
                    topic["pad"] = opt["pad"]
                if opt.get("bg"):
                    topic["bg"] = opt["bg"]
                if attributions.get(key):
                    topic["credit"] = attributions[key]
            else:
                # 未取得：動画側がプレースホルダカードを描く。差し替え先と検索語を案内。
                topic["note"] = cut.get("image_query") or meta_ch.get("title")
                topic["placeholder"] = chapter_image_name(ch, ci)
            topics.append(topic)
    return topics


def build_review(chapters, image_files=None, attributions=None):
    """画像レビュー用のマニフェスト（カット単位）を作る。

    fetch_images と同じ順（chapters × image_cuts）でカットを列挙し、
    各カットに「章タイトル・検索語・種別・取得画像・帰属・承認フラグ」を持たせる。
    レビュー画面(review_server)が読み、人が差し替え/帰属編集/承認する。
    """
    image_files = image_files or {}
    attributions = attributions or {}
    cuts = []
    for ch, chapter in enumerate(chapters):
        for ci, cut in enumerate(chapter.get("image_cuts", [])):
            key = (ch, ci)
            cuts.append({
                "ch": ch,
                "ci": ci,
                "title": chapter.get("title") or "",
                "query": (cut.get("image_query") or "").strip(),
                "kind": cut.get("image_kind", "ambient"),
                "image": image_files.get(key),          # None=未取得(プレースホルダ)
                "attribution": attributions.get(key),    # None=帰属不要/無し
                "approved": False,
                # レビューで人が決める描画オプション（既定=自動/なし）
                "fit": None,        # None=自動(kindで決定) / "cover" / "contain"
                "crop": None,       # None=なし / {l,t,r,b}(0..1)
                "filter": None,     # None=なし / {brightness,contrast,grayscale}
                "hide": False,      # True=画像を出さない(黒板のみ)
                "pad": None,        # contain時の余白px(全方向)。None/0=なし
                "bg": None,         # contain時の余白背景色(CSS color)。None=既定
            })
    return {"cuts": cuts}


def load_images_from_review(out_dir):
    """review.json から image_files/attributions を復元する（レビュー承認後の続行用）。

    人が差し替え/編集した結果を真として meta を作るため、fetch を再実行しない。
    Returns: (image_files{(ch,ci):filename}, attributions{(ch,ci):attr},
              cut_opts{(ch,ci):{fit?,crop?,filter?,hide?}})
    """
    path = Path(out_dir) / "review.json"
    if not path.exists():
        return {}, {}, {}
    with open(path, encoding="utf-8") as f:
        review = json.load(f)
    image_files, attributions, cut_opts = {}, {}, {}
    for c in review.get("cuts", []):
        key = (c["ch"], c["ci"])
        if c.get("image"):
            image_files[key] = c["image"]
        if c.get("attribution"):
            attributions[key] = c["attribution"]
        # 描画オプション（自動/なし以外だけ持たせる）
        opt = {}
        if c.get("fit"):
            opt["fit"] = c["fit"]
        if c.get("crop"):
            opt["crop"] = c["crop"]
        if c.get("filter"):
            opt["filter"] = c["filter"]
        if c.get("hide"):
            opt["hide"] = True
        if c.get("pad"):
            opt["pad"] = c["pad"]
        if c.get("bg"):
            opt["bg"] = c["bg"]
        if opt:
            cut_opts[key] = opt
    return image_files, attributions, cut_opts


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


def write_credits_txt(out_dir, config, attributions):
    """概要欄に貼るクレジットを credits.txt に出力（CC-BY帰属はここで必須要件を満たす）。

    動画内には出さない方針。画像帰属は重複除去して列挙（PD/CC0/Pexels/Pixabayは任意だが併記）。
    """
    lines = ["【使用素材クレジット】", "", "■ 音声（VOICEVOX）"]
    for name in config.get("tts_voicevox", {}).get("speakers", {}):
        lines.append(f"  VOICEVOX:{name}")
    lines += ["", "■ 画像"]
    seen = []
    for a in (attributions or {}).values():
        if a and a not in seen:
            seen.append(a)
    if seen:
        lines += [f"  {a}" for a in seen]
    else:
        lines.append("  Wikimedia Commons / Pexels / Pixabay（商用可ライセンス）")
    lines += ["", "※ CC-BY画像は上記表記により帰属。PD/CC0/Pexels/Pixabayは帰属不要。"]
    (out_dir / "credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_meta(script_result, turns, config, now_iso, image_files=None, attributions=None, cut_opts=None):
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
        "topics": build_chapter_topics(segments, turns, chapters, image_files, attributions, cut_opts),
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
    parser.add_argument("--stop-after-images", action="store_true",
                        help="画像取得まで実行しreview.json/script.jsonを出力して停止（人手レビュー用）")
    parser.add_argument("--images-from-dir", action="store_true",
                        help="画像取得をskipしreview.jsonの承認結果から meta を生成（レビュー承認後の続行用）")
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
        # 旧/手書き台本でも image_cuts・enum を整える（旧 image_query 単数→1cut／sectionはchapters由来で補完）。
        script_result["chapters"] = story_script._clean_chapters(script_result.get("chapters"))
        story_script.normalize_turns(script_result["script"], script_result["chapters"])
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

    # 2. 画像取得（image_kindで Wikimedia / Pexels / Pixabay に振り分け）。失敗カットはプレースホルダ。
    image_files, attributions, cut_opts = {}, {}, {}
    if args.images_from_dir:
        # レビュー承認後の続行：fetchせず review.json の人手結果（差し替え/帰属/描画オプション込み）を真とする。
        image_files, attributions, cut_opts = load_images_from_review(out_dir)
        logger.info(f"--images-from-dir: review.json から画像{len(image_files)}件・オプション{len(cut_opts)}件を使用（再取得なし）")
    elif args.no_images:
        logger.info("--no-images: 画像取得をskipし全章プレースホルダで続行します。")
    else:
        from src import image_fetch
        image_files, attributions = image_fetch.fetch_images(
            script_result["chapters"], str(out_dir), config)

    # 画像レビュー用マニフェストを出力（人手チェックポイント。--images-from-dir時は人手結果を維持）。
    if not args.images_from_dir:
        review = build_review(script_result["chapters"], image_files, attributions)
        with open(out_dir / "review.json", "w", encoding="utf-8") as f:
            json.dump(review, f, ensure_ascii=False, indent=2)

    # --stop-after-images: 画像レビューのため音声/meta生成の手前で停止。
    # script.json も保存し、承認後に `--from-script ... --images-from-dir` で続行できるようにする。
    if args.stop_after_images:
        with open(out_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(script_result, f, ensure_ascii=False, indent=2)
        logger.info(
            f"=== 画像取得まで完了・レビュー待ちで停止: {out_dir}/review.json ===\n"
            f"レビュー: python review_server.py --dir {out_dir}\n"
            f"承認後の続行: python main_story.py --from-script {out_dir}/script.json --images-from-dir"
        )
        return

    # 3. VOICEVOXで音声＋厳密タイムスタンプ（文単位字幕付き）
    mp3_path = out_dir / "digest.mp3"
    turns = tts_voicevox.generate_audio(script_result["script"], config, str(mp3_path))

    # 4. meta.json
    now_iso = datetime.now(JST).isoformat()
    meta = build_meta(script_result, turns, config, now_iso, image_files, attributions, cut_opts)
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 概要欄用クレジット（動画内には出さない。CC-BY帰属はここで要件を満たす）。
    write_credits_txt(out_dir, config, attributions)

    dur = meta["topics"][-1]["end"] if meta["topics"] else 0.0
    logger.info(f"=== 完了: {out_dir} （{len(meta['script'])}ターン・{len(meta['topics'])}章・{dur:.1f}秒）===")
    logger.info(f"動画化: cd video && SRC_DIR=../{out_dir} npm run render")


if __name__ == "__main__":
    main()
