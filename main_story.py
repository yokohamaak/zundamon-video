"""
実は〇〇雑学 動画パイプライン - メインスクリプト

小テーマ → Geminiで掛け合い台本（intro＋各ネタ＋outro） → VOICEVOX音声 → meta.json + digest.mp3 + 章画像
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


def _cut_groups(idxs, turns, ncuts):
    """章内ターン列を、各ターンの cut アンカー（その章の何番目の画像か）でグループ化する。

    cut が一つも無ければ None（呼び出し側が均等割りにフォールバック）。
    cut アンカーはそのまま尊重する（人手レビューで自由に並べられるよう・逆戻りも可）。
    欠落（cut無し）のターンは直前のcutを引き継ぐ。
    ※Geminiの自動生成はプロンプトで前進（戻さない）を促すが、強制はしない＝レビューで人が決める。
    Returns: [(cut_index, lo, hi)]（lo..hi は idxs 内の位置・hi排他・連続被覆）または None
    """
    if ncuts <= 0:
        return None
    vals, cur, any_anchor = [], 0, False
    for j in idxs:
        c = turns[j].get("cut")
        if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < ncuts:
            any_anchor = True       # アンカー値をそのまま採用（逆戻りも許可＝手動割当を尊重）
        else:
            c = cur                # 欠落/不正は直前のcutを継続
        vals.append(c)
        cur = c
    if not any_anchor:
        return None
    groups, pos, n = [], 0, len(vals)
    while pos < n:
        ci, start = vals[pos], pos
        while pos < n and vals[pos] == ci:
            pos += 1
        groups.append((ci, start, pos))
    return groups


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
        # 章(セグメント)の境目は「前章の最後の発話の終わり（＝章間の無音の始まり）」に置く。
        # こうすると章切替の演出(ページめくり)とSEを無音の中で行え、新章の声はその後に始まる
        # （切替が終わる前に声が出る唐突さを解消）。発話間に無音が無いとき(テスト等)は従来と同値。
        seg_start = 0.0 if si == 0 else turns[segments[si - 1]["turns"][-1]]["end"]
        seg_end = total if si == nseg - 1 else turns[idxs[-1]]["end"]
        # カット割り当て: ターンの cut アンカー（その章の何番目の画像か）があれば章内をそれで
        # 区切る（話の流れで切替）。無ければ均等割り（後方互換）。
        groups = _cut_groups(idxs, turns, len(cuts))
        if groups is None:
            ncut = max(1, min(len(cuts), len(idxs)))
            groups = [(ci, ci * len(idxs) // ncut, (ci + 1) * len(idxs) // ncut)
                      for ci in range(ncut)]
        for gi, (ci, lo, hi) in enumerate(groups):
            cstart = seg_start if gi == 0 else turns[idxs[lo]]["start"]
            cend = seg_end if gi == len(groups) - 1 else turns[idxs[hi]]["start"]
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
                # ショート固定見出し用フック（Gemini生成・任意）。動画側が章の代表hookとして使う。
                if meta_ch.get("hook"):
                    topic["hook"] = meta_ch["hook"]
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


def build_audio(config, script):
    """meta.audio を組み立てる（純関数）。BGM設定＋SEイベント列（発言timingに同期）。

    SEイベントは既存の effect/emotion/section と timing から導出する（追加データ不要）:
      - intro:    動画冒頭(t=0)
      - flash:    effect=="flash" の発言の開始（各ネタ切替の頭）
      - outro:    section=="outro" の最初の発言の開始
      - surprise: questioner(ずんだもん) かつ emotion=="surprise" の発言の開始だけ
                  （解説役の驚きは鳴らさない＝連発防止）
    直前に採用したSEと se_min_gap 秒以内に重なるイベントは抑制する（先勝ち・優先度順）。
    config.audio が無ければ None（=BGM/SEなし）。
    """
    ac = config.get("audio") or {}
    if not ac:
        return None
    se_files = ac.get("se") or {}
    questioner = config.get("story", {}).get("questioner", "ずんだもん")
    min_gap = float(ac.get("se_min_gap", 0.8))
    # ネタ切替/締めSEを「章境界の無音」に置く＝直前発話の終わり＋se_lead（発話頭は越えない）。
    # 視覚のページめくり開始(FLIP_HOLD)と揃え、切替演出とSEを同時に・声の前に鳴らす。
    se_lead = float(ac.get("se_lead", 0.15))

    raw = []  # (t, priority, se_type)。priorityが小さいほど衝突時に優先。
    if se_files.get("intro"):
        raw.append((0.0, 0, "intro"))
    outro_done = False
    prev_end = 0.0
    for turn in script:
        t = float(turn.get("start", 0.0) or 0.0)
        cue_t = min(t, prev_end + se_lead)  # 章境界の無音内（直前発話末＋lead・声は越えない）
        if se_files.get("flash") and turn.get("effect") == "flash":
            raw.append((cue_t, 1, "flash"))
        if se_files.get("outro") and not outro_done and turn.get("section") == "outro":
            raw.append((cue_t, 0, "outro"))  # outro章頭はflashも持つが、締めSEを優先（prio小）
            outro_done = True
        if (se_files.get("surprise") and turn.get("speaker") == questioner
                and turn.get("emotion") == "surprise"):
            raw.append((t, 2, "surprise"))  # 驚きは反応なので発話に同期
        prev_end = float(turn.get("end", t) or t)

    raw.sort(key=lambda x: (x[0], x[1]))
    events = []
    for t, _prio, se in raw:
        if events and t - events[-1]["t"] < min_gap:
            continue  # 直前SEと近すぎ→抑制（連発防止）
        events.append({"t": round(t, 3), "se": se})

    return {
        "bgm": ac.get("bgm"),                       # {file, volume, fade} or None（prepが欠損を除去）
        "se_volume": float(ac.get("se_volume", 0.5)),
        "se": se_files,                             # トリガー名 → ファイル名
        "events": events,                           # [{t, se}]（時刻順）
    }


def select_closing_lines(config, rotation):
    """締めCTAのセットを選ぶ（純関数）。closing_lines_pool があれば rotation で巡回、無ければ closing_lines。

    rotation=これまでに作った動画数（0始まり）。pool=[A,C,…] なら 動画ごとに A→C→A… と交互。
    """
    s = config.get("story", {})
    pool = [p for p in (s.get("closing_lines_pool") or []) if p]
    if pool:
        return pool[int(rotation) % len(pool)]
    return s.get("closing_lines") or []


def append_closing_chorus(script_result, config, rotation=None):
    """締めに固定エンディングを足す：締めCTA(コメント誘導/高評価/登録) → 二人同時(closing_chorus)。

    CTAはGeminiに生成させず固定＝入れ忘れ防止・毎回一貫。closing_lines_pool があれば動画ごとに巡回。
    **既存の締め（過去に付けた closing/chorus ターン）を除去してから付け直す**＝重複防止
    （--from-script の再実行や、旧マーカーの台本でも二重にならない）。chorus=True は tts が重ねて合成。
    rotation 未指定なら topic_history の動画数から決める。
    """
    s = config.get("story", {})
    if rotation is None:
        from src import topic_history
        rotation = len(topic_history.used_themes(topic_history.genre_of(config)))

    def _is_cta(t):
        # Geminiがoutroに書いてしまった定型CTA（高評価/チャンネル登録）も除去＝固定CTAと重複させない。
        txt = t.get("text") or ""
        return t.get("section") == "outro" and ("チャンネル登録" in txt or "高評価" in txt)

    script = [t for t in (script_result.get("script") or [])
              if not (t.get("closing") or t.get("chorus") or _is_cta(t))]
    last = script[-1] if script else {}
    ch, cut = last.get("chapter", 0), last.get("cut", 0)
    explainer = s.get("explainer", story_script.DEFAULT_EXPLAINER)

    def _line(speaker, text, emotion="happy", chorus=False):
        t = {"speaker": speaker, "text": text, "emotion": emotion, "section": "outro",
             "chapter": ch, "effect": "kenburns", "cut": cut, "closing": True}
        if chorus:
            t["chorus"] = True
        return t

    # ① 締めCTA（巡回プール or 固定。コメント誘導/高評価/登録）
    for line in select_closing_lines(config, rotation):
        text = (line.get("text") or "").strip()
        if text:
            script.append(_line(line.get("speaker") or explainer, text, line.get("emotion") or "happy"))
    # ② 二人同時の締め
    chorus_text = (s.get("closing_chorus") or "").strip()
    if chorus_text:
        script.append(_line(explainer, chorus_text, chorus=True))

    script_result["script"] = script


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
        # script(merged) は timing(start/end) と cut(台本由来)の両方を持つ。turns(TTS)は cut を
        # 持たないので必ず script を渡す（C-1のcutアンカーを効かせるため）。
        "topics": build_chapter_topics(segments, script, chapters, image_files, attributions, cut_opts),
        "credits": build_credits(config, attributions),
        "audio": build_audio(config, script),
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
    parser.add_argument("--meta-only", action="store_true",
                        help="音声を作り直さず既存digest.mp3の尺を流用してmeta.jsonだけ再生成"
                             "（VOICEVOX不要・課金なし。画像レビューの微修正反映用。--from-script必須）")
    args = parser.parse_args()

    load_dotenv()  # .env を自動読込（source忘れ対策・既存環境変数は優先）
    config = load_config(args.config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== 実は〇〇雑学 動画パイプライン開始 ===")

    # 1. 台本：既存scriptを使う（--from-script）か、Geminiで生成する
    if args.from_script:
        with open(args.from_script, encoding="utf-8") as f:
            script_result = json.load(f)
        # 旧/手書き台本でも image_cuts・enum を整える（旧 image_query 単数→1cut／sectionはchapters由来で補完）。
        script_result["chapters"] = story_script._clean_chapters(script_result.get("chapters"))
        story_script.normalize_turns(script_result["script"], script_result["chapters"])
        logger.info(f"既存台本を使用: {args.from_script}（{len(script_result['script'])}ターン）")
    else:
        from src import topic_history
        genre = topic_history.genre_of(config)
        # テーマ選定: story.theme(固定) > theme_pool(使用済みを避けて巡回) > Gemini自動選定。
        chosen_theme = story_script.select_theme(config, topic_history.used_themes(genre))
        config.setdefault("story", {})["theme"] = chosen_theme  # build_prompt がこの値を使う
        logger.info(f"テーマ: 「{chosen_theme}」" if chosen_theme else "テーマ: Gemini自動選定")
        avoid = topic_history.facts(genre)  # 過去動画の採用済み＋却下を避ける
        if avoid:
            logger.info(f"既出ネタ {len(avoid)}件を避けて生成（ジャンル: {genre}）")
        script_result = story_script.generate_story_script(config, also_avoid=avoid)

    # 締めに二人同時(ユニゾン)の固定挨拶を足す（生成・既存どちらの台本にも・空設定なら無効）。
    append_closing_chorus(script_result, config)

    # --meta-only: 音声を作り直さず、既存 meta.json の尺(start/end/sentences)を turns として流用し、
    # review.json の人手結果（画像差し替え/hide/crop/bg等）だけ反映して meta.json を再生成する。
    # 画像レビューの微修正をVOICEVOXなし・課金なしで素早く動画へ反映するための軽量ループ。
    if args.meta_only:
        if not args.from_script:
            parser.error("--meta-only は --from-script が必須です（既存script.jsonの台本を使うため）")
        meta_path = out_dir / "meta.json"
        if not meta_path.exists():
            parser.error(f"--meta-only には既存の {meta_path} が必要です（尺を流用するため）")
        with open(meta_path, encoding="utf-8") as f:
            old_meta = json.load(f)
        # 既存 meta の script から尺(turns)を復元（順序は script.json と一致）。
        turns = [
            {"start": t.get("start", 0), "end": t.get("end", 0), "sentences": t.get("sentences", [])}
            for t in old_meta.get("script", [])
        ]
        if len(turns) != len(script_result["script"]):
            parser.error(
                f"既存metaのターン数({len(turns)})とscript.json({len(script_result['script'])})が不一致。"
                "台本が変わっている場合は通常の再生成（--images-from-dir）を使ってください。")
        image_files, attributions, cut_opts = load_images_from_review(out_dir)
        now_iso = datetime.now(JST).isoformat()
        meta = build_meta(script_result, turns, config, now_iso, image_files, attributions, cut_opts)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info(f"=== --meta-only: 既存尺を流用して meta.json を再生成しました: {meta_path} "
                    f"（画像{len(image_files)}件・オプション{len(cut_opts)}件）===")
        return

    # 尺チェック：台本文字数が予算を大きく超えたら警告（Geminiが長く書きすぎた時に気づけるように）。
    s_cfg = config.get("story", {})
    budget = int(float(s_cfg.get("target_minutes", story_script.DEFAULT_MINUTES)) * story_script.CHARS_PER_MINUTE)
    total_chars = sum(len(t.get("text") or "") for t in script_result.get("script", []))
    est_min = total_chars / story_script.CHARS_PER_MINUTE if story_script.CHARS_PER_MINUTE else 0
    if budget and total_chars > budget * 1.15:
        logger.warning(f"台本が長すぎます: {total_chars}字（予算{budget}字・推定{est_min:.1f}分）。"
                       f"レビューでネタ/ターンを削るか『全体を作り直す』で短く作り直すのを検討してください。")
    else:
        logger.info(f"台本 {total_chars}字（推定{est_min:.1f}分・予算{budget}字）")

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

    # 動画を確定（meta生成＝採用）した時点で、この動画のネタを永続履歴に「採用済み」で記録。
    # 以降の動画生成・再生成で重複回避に使う（ジャンル別・動画をまたいで残る）。
    from src import topic_history
    genre = topic_history.genre_of(config)
    used = topic_history.trivia_facts(script_result.get("chapters", []))
    n_added = topic_history.add(genre, used, "used")
    logger.info(f"採用ネタ {n_added}件を履歴に記録（ジャンル: {genre}・累計回避対象に追加）")
    # 採用テーマも記録（theme_pool 巡回用。Geminiが選んだ場合も実テーマを残す）。
    if topic_history.add_theme(genre, script_result.get("theme")):
        logger.info(f"採用テーマを履歴に記録: 「{script_result.get('theme')}」")

    # 概要欄用クレジット（動画内には出さない。CC-BY帰属はここで要件を満たす）。
    write_credits_txt(out_dir, config, attributions)

    dur = meta["topics"][-1]["end"] if meta["topics"] else 0.0
    logger.info(f"=== 完了: {out_dir} （{len(meta['script'])}ターン・{len(meta['topics'])}章・{dur:.1f}秒）===")
    logger.info(f"動画化: cd video && SRC_DIR=../{out_dir} npm run render")


if __name__ == "__main__":
    main()
