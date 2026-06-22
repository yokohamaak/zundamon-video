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
import copy
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

# ショートの目標尺（秒）。本編8分でなくこの尺で文字数予算/警告を判定する。
SHORT_TARGET_SECONDS = 40


def _short_slug(title: str) -> str:
    """ショートの自動slug（タイトルから安全な短い名前）。"""
    import re
    s = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠]+", "-", (title or "").strip()).strip("-")
    return s[:20] or "short"


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


def build_chapter_topics(segments, turns, chapters, image_files=None, attributions=None,
                         cut_opts=None, turn_image=None):
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
        turn_image: editor権威時のみ。editor_model.resolve_turn_images の出力（turnsと同indexの配列）。
            指定時は画像のグルーピング/解決をこれで行う（cut/image_files/cut_opts は無視）。
            非指定(None)＝従来の legacy 経路（image_cuts × cut アンカー）。タイミング・章バッジ・
            演出オーバーレイは両経路で完全に同じコードを通す（＝画像の出所だけが違う）。
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
        # editor権威時は cut でなく「有効な imageCue（turn_image[idx]）」の連続塊で区切る。
        # tok=グループ識別子（legacy=cut番号 / editor=cueId）。以降の vizSeg分割・タイミングは共通。
        if turn_image is not None:
            groups, pos, m = [], 0, len(idxs)
            def _cid(p):
                r = turn_image[idxs[p]]
                return r.get("cueId") if r else None
            while pos < m:
                tok, s = _cid(pos), pos
                while pos < m and _cid(pos) == tok:
                    pos += 1
                groups.append((tok, s, pos))
        else:
            groups = _cut_groups(idxs, turns, len(cuts))
            if groups is None:
                ncut = max(1, min(len(cuts), len(idxs)))
                groups = [(ci, ci * len(idxs) // ncut, (ci + 1) * len(idxs) // ncut)
                          for ci in range(ncut)]
        # 各cutグループを vizSeg の切れ目でさらに分割：1 topic = 1カット(画像) かつ 1演出。
        # （同じカット内に複数演出があると、従来は時間が重なる最初の1つしか載らず、2つ目以降が消えていた。）
        split = []
        for ci, lo, hi in groups:
            s = lo
            for k in range(lo + 1, hi):
                if turns[idxs[k]].get("vizSeg") != turns[idxs[k - 1]].get("vizSeg"):
                    split.append((ci, s, k))
                    s = k
            split.append((ci, s, hi))
        groups = split
        # 演出セグメント（範囲ごとに種類＋設定。1章に複数可・重ならない前提）。
        # 新形式 vizList があればそれ、無ければ旧単一形式(章直下の panel/quiz/…)を1セグメントへ。
        viz_segments = _resolve_viz_segments(meta_ch, idxs, turns, seg_start, seg_end, image_files, ch)
        for gi, (ci, lo, hi) in enumerate(groups):
            cstart = seg_start if gi == 0 else turns[idxs[lo]]["start"]
            cend = seg_end if gi == len(groups) - 1 else turns[idxs[hi]]["start"]
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
            if turn_image is not None:
                # editor権威：有効cueの解決結果（editor_model._resolve_cue）をそのまま topic へ。
                # 各キー(blank/image/fit/crop/filter/pad/bg/credit/note/placeholder)は legacy と
                # 同じ条件で計算済み＝同入力なら legacy と等価になる（test_phase2 で実データ機械比較）。
                res = turn_image[idxs[lo]]
                if res is None or res.get("blank"):
                    topic["blank"] = True
                elif res.get("image"):
                    topic["image"] = res["image"]
                    for k in ("fit", "crop", "filter", "pad", "bg", "credit"):
                        if res.get(k) is not None:
                            topic[k] = res[k]
                else:
                    topic["note"] = res.get("note") or meta_ch.get("title")
                    pch, pci = res["placeholder"]
                    topic["placeholder"] = chapter_image_name(pch, pci)
            else:
                cut = cuts[ci] if ci < len(cuts) else {}
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
            # 演出セグメントのうち、このtopicの時間帯に重なるものを載せる（範囲は重ならない前提＝最初の1つ）。
            # 描画側で vizFrom/vizUntil の時刻ゲートをするので、窓の手前/後は通常画像になる。
            for seg in viz_segments:
                if topic["end"] > seg["start"] + 1e-6 and topic["start"] < seg["end"] - 1e-6:
                    topic["vizFrom"] = round(float(seg["start"]), 3)
                    topic["vizUntil"] = round(float(seg["end"]), 3)
                    if seg.get("panel"):
                        # パネル画像はカット毎に変わる＝このtopicの画像をパネルに載せる。
                        pr = dict(seg["panel"])
                        if topic.get("image"):
                            pr["image"] = topic["image"]
                        topic["panel"] = pr
                    for k in ("quiz", "compare", "stat", "callouts", "calloutStyle"):
                        if seg.get(k) is not None:
                            topic[k] = seg[k]
                    break
            topics.append(topic)
    return topics


def _viz_window(idxs, turns, seg_start, seg_end):
    """演出の表示範囲 [start, end] を発言の viz_start/viz_end から決める（純関数）。

    viz_start の発言の開始〜 viz_end の発言の終了。未指定側は章の境界（seg_start/seg_end）。
    起点が章の先頭セリフ（終点が末尾セリフ）のときは章境界まで広げる。topicは章境界
    [seg_start, seg_end) を被覆するため、先頭セリフのstartで窓を切ると章頭の無音/ページ
    めくり区間が窓の外になり、そこで一瞬だけ元画像が見える（「ここから」より手前で演出が
    始まる/終わるのと同種の境界ズレ）。先頭/末尾起点なら境界へスナップして隙間を無くす。
    """
    start = seg_start
    for pos, j in enumerate(idxs):
        if turns[j].get("viz_start"):
            start = seg_start if pos == 0 else float(turns[j].get("start", seg_start))
            break
    end = seg_end
    for pos, j in enumerate(idxs):
        if turns[j].get("viz_end"):
            # 終了セリフの「終わり」まで表示。末尾セリフ起点なら章末（無音含む）まで広げる。
            end = seg_end if pos == len(idxs) - 1 else float(turns[j].get("end", seg_end))
            break
    if end <= start:  # 不整合（終了が開始より前）なら章末まで
        end = seg_end
    return start, end


def _viz_windows(idxs, turns, seg_start, seg_end, n):
    """複数演出の表示範囲リストを viz_start/viz_end の出現順ペアで決める（純関数）。

    範囲は重ならない前提＝出現順に viz_start[i] と viz_end[i] を対にする。
    片側が無い時は章境界で補完。先頭startは章頭・末尾endは章末へスナップ（_viz_windowと同趣旨）。
    n=vizListの個数（最低この数の窓を返す）。
    """
    starts, ends = [], []
    for pos, j in enumerate(idxs):
        if turns[j].get("viz_start"):
            starts.append(seg_start if pos == 0 else float(turns[j].get("start", seg_start)))
        if turns[j].get("viz_end"):
            ends.append(seg_end if pos == len(idxs) - 1 else float(turns[j].get("end", seg_end)))
    wins = []
    for i in range(max(n, 1)):
        s = starts[i] if i < len(starts) else seg_start
        e = ends[i] if i < len(ends) else seg_end
        if e <= s:
            e = seg_end
        wins.append((float(s), float(e)))
    return wins


def _resolve_viz_segments(meta_ch, idxs, turns, seg_start, seg_end, image_files, ch):
    """章の演出を「範囲＋種類＋設定」のセグメント列へ解決（純関数）。

    新形式: meta_ch["vizList"]=[{"panel":..}|{"quiz":..}|... ]（範囲はviz_start/viz_endの順ペア）。
    旧形式: 章直下の panel/quiz/compare/stat/callouts（範囲は単一の_viz_window）＝1セグメント。
    各セグメントは {"start","end", panel?/quiz?/compare?/stat?/callouts?/calloutStyle?}。
    """
    vl = meta_ch.get("vizList")
    if vl:
        segs = []
        for entry in vl:
            sid = entry.get("id")
            # この演出に属する発言＝turn.vizSeg==id（1行1演出・split/deleteに強い）。
            sub = [j for j in idxs if turns[j].get("vizSeg") == sid]
            if not sub:
                continue
            # 範囲＝所属発言の最初のstart〜最後のend。章の先頭/末尾ならその境界へスナップ。
            ws = seg_start if sub[0] == idxs[0] else float(turns[sub[0]].get("start", seg_start))
            we = seg_end if sub[-1] == idxs[-1] else float(turns[sub[-1]].get("end", seg_end))
            seg = {"start": ws, "end": we}
            pr = _resolve_panel(entry.get("panel"), sub, turns, ws, we, image_files, ch)
            if pr:
                seg["panel"] = pr
            seg.update(_resolve_viz(entry, sub, turns, ws, we, image_files, ch))
            segs.append(seg)
        return segs
    # 旧・単一形式
    vw = _viz_window(idxs, turns, seg_start, seg_end)
    seg = {"start": vw[0], "end": vw[1]}
    pr = _resolve_panel(meta_ch.get("panel"), idxs, turns, seg_start, seg_end, image_files, ch)
    if pr:
        seg["panel"] = pr
    seg.update(_resolve_viz(meta_ch, idxs, turns, seg_start, seg_end, image_files, ch))
    return [seg] if (len(seg) > 2) else []


def _resolve_overlays(turn, assets_by):
    """turn.imageOverlays（文字位置start/end・assetId・dir・size）を meta 用へ解決（純関数）。

    start/end は本文文字位置→絶対秒（_point_time 按分）。assetId→assets の file。
    file 無し / 逆転（end<=start）は除外。Returns: [{image,start,end,dir,size?}] or None。
    """
    out = []
    for ov in (turn.get("imageOverlays") or []):
        a = assets_by.get(ov.get("assetId"))
        if not (a and a.get("file")):
            continue
        st = _point_time(turn, ov.get("start", 0))
        en = _point_time(turn, ov.get("end", ov.get("start", 0)))
        if en <= st:
            continue
        o = {"image": a["file"], "start": st, "end": en, "dir": ov.get("dir") or "left"}
        if ov.get("outDir"):           # 退場方向（未指定＝登場と同じ＝戻る）。既定は省略。
            o["outDir"] = ov["outDir"]
        if ov.get("size") is not None:
            o["size"] = float(ov["size"])
        if ov.get("frame") is False:   # 枠/影なし（透過画像向け）。既定(あり)は省略。
            o["frame"] = False
        if a.get("crop"):              # 素材ライブラリのクロップ/補正をオーバーレイにも適用
            o["crop"] = a["crop"]
        if a.get("filter"):
            o["filter"] = a["filter"]
        out.append(o)
    return out or None


def _point_time(turn, pos):
    """Turn内の文字位置 pos を絶対秒へ線形按分（純関数）。start〜end を text 長で割る。

    textEffectsの発話タイミング同期と同じ考え方。end<=start や空文字は start を返す。
    """
    s = float(turn.get("start", 0.0))
    e = float(turn.get("end", s))
    n = len(turn.get("text") or "")
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        pos = 0
    if n <= 0 or e <= s:
        return round(s, 3)
    frac = max(0.0, min(1.0, pos / n))
    return round(s + (e - s) * frac, 3)


def _viz_point_times(idxs, turns):
    """セリフ内文字位置の演出点(vizPoints)を {(type, value): 秒} へ解決（純関数）。

    既存のTurnフラグ方式(panel_item等)と共存し、vizPoints があれば出現時刻を上書きする。
    value を持たない reveal / panel_event は value=None で格納する。
    """
    out = {}
    for j in idxs:
        for p in (turns[j].get("vizPoints") or []):
            if not isinstance(p, dict) or not p.get("type"):
                continue
            v = p.get("value")
            v = v if (isinstance(v, int) and not isinstance(v, bool)) else None
            key = (p["type"], v)
            if key not in out:
                out[key] = _point_time(turns[j], p.get("pos", 0))
    return out


def _reveal_time(idxs, turns, seg_start, seg_end):
    """「実は」の答え/数字を出す時刻を発言timingから推定（純関数）。

    優先: reveal==True の発言 → effect=="zoom_punch" の発言 → 章の約60%地点。
    どれも無ければ seg の中盤を返す。
    vizPoints(セリフ内文字位置)に reveal があれば最優先で文字位置から秒を出す。
    """
    for j in idxs:
        for p in (turns[j].get("vizPoints") or []):
            if isinstance(p, dict) and p.get("type") == "reveal":
                return _point_time(turns[j], p.get("pos", 0))
    for j in idxs:
        if turns[j].get("reveal"):
            return float(turns[j].get("start", seg_start))
    for j in idxs:
        if turns[j].get("effect") == "zoom_punch":
            return float(turns[j].get("start", seg_start))
    return round(seg_start + (seg_end - seg_start) * 0.6, 3)


def _resolve_viz(meta_ch, idxs, turns, seg_start, seg_end, image_files, ch):
    """章の画像演出(quiz/compare/stat/callouts)を時刻・画像へ解決（純関数）。

    Returns: topicに載せる {quiz?,compare?,stat?,callouts?}（あるものだけ）。
    """
    out = {}
    pts = _viz_point_times(idxs, turns)  # セリフ内文字位置の演出点（あれば時刻を上書き）
    quiz = meta_ch.get("quiz")
    if quiz:
        # クイズは画像を使わない演出＝背後の通常画像/黒板をそのまま見せる。
        # 専用画像(quiz.image)は割り当てない（手書きで明示指定された分だけ後方互換で残る）。
        q = dict(quiz)
        q["revealAt"] = _reveal_time(idxs, turns, seg_start, seg_end)
        out["quiz"] = q
    compare = meta_ch.get("compare")
    if compare:
        def side(s):
            r = {"label": s["label"]}
            img = image_files.get((ch, s.get("cut", 0)))
            if img:
                r["image"] = img
            return r
        # 出現時刻: compare_item==0→左(at0) / ==1→右(at1)。
        # vizPoints があれば最優先。無ければ発言の compare_item フラグから。
        # at0未指定は章頭、at1未指定は at0 と同時（＝最初から2分割）。
        at0 = pts.get(("compare_item", 0))
        at1 = pts.get(("compare_item", 1))
        for j in idxs:
            ci = turns[j].get("compare_item")
            if not isinstance(ci, int) or isinstance(ci, bool):
                continue
            if ci == 0 and at0 is None:
                at0 = float(turns[j].get("start", seg_start))
            elif ci == 1 and at1 is None:
                at1 = float(turns[j].get("start", seg_start))
        if at0 is None:
            at0 = float(seg_start)
        if at1 is None:
            at1 = at0
        out["compare"] = {
            "left": side(compare["left"]),
            "right": side(compare["right"]),
            "at0": round(at0, 3),
            "at1": round(at1, 3),
        }
        # ラベル/分割線の見た目（あれば素通し）。
        for k in ("labelColor", "labelTextColor", "labelSize", "dividerColor"):
            if compare.get(k) is not None:
                out["compare"][k] = compare[k]
    stat = meta_ch.get("stat")
    if stat:
        st = dict(stat)
        st["showAt"] = _reveal_time(idxs, turns, seg_start, seg_end)
        # value が整数のときカウントアップの到達値を持たせる（描画が0→countToへ）。
        digits = st["value"].replace(",", "")
        if digits.isdigit():
            st["countTo"] = int(digits)
        out["stat"] = st
    callouts = meta_ch.get("callouts")
    if callouts:
        at_by_idx = {}
        for (typ, v), t in pts.items():  # vizPoints を最優先で反映（フラグ走査は未指定分のみ）
            if typ == "callout_item" and isinstance(v, int) and 0 <= v < len(callouts):
                at_by_idx[v] = t
        for j in idxs:
            ci = turns[j].get("callout_item")
            if isinstance(ci, int) and not isinstance(ci, bool) and 0 <= ci < len(callouts) and ci not in at_by_idx:
                at_by_idx[ci] = float(turns[j].get("start", seg_start))
        n = len(callouts)
        resolved = []
        for k, co in enumerate(callouts):
            at = at_by_idx[k] if k in at_by_idx else seg_start + (seg_end - seg_start) * (k + 1) / (n + 1)
            item = dict(co)
            item["at"] = round(float(at), 3)
            resolved.append(item)
        out["callouts"] = resolved
        if meta_ch.get("calloutStyle"):
            out["calloutStyle"] = meta_ch["calloutStyle"]
    return out


def _resolve_panel(panel, idxs, turns, seg_start, seg_end, image_files=None, ch=0):
    """章の panel 定義（{image?,cut?,bg?,items}）を時刻解決して topic.panel 形へ（純関数）。

    - shrinkAt: idxs 内で panel_event=="shrink" の最初の発言の start。無ければ seg_start（章頭）。
    - items[k].at: panel_item==k の発言の start。指定が無い項目は shrink後〜章末を均等割り。
    - image: panel.image があればそれ、無ければ panel.cut（既定0）の取得済画像。
    - bg: テキスト領域の背景色（任意）。
    Returns: {image?, bg?, items:[{text,arrow_from_prev?,at}], shrinkAt} または None
    """
    image_files = image_files or {}
    if not panel or not panel.get("items"):
        return None
    items = panel["items"]
    pts = _viz_point_times(idxs, turns)  # セリフ内文字位置の演出点（あれば時刻を上書き）
    shrink_at = pts.get(("panel_event", None))
    if shrink_at is None:
        shrink_at = float(seg_start)
        for j in idxs:
            if turns[j].get("panel_event") == "shrink":
                shrink_at = float(turns[j].get("start", seg_start))
                break
    at_by_idx = {}
    for (typ, v), t in pts.items():  # vizPoints を最優先（フラグ走査は未指定分のみ）
        if typ == "panel_item" and isinstance(v, int) and 0 <= v < len(items):
            at_by_idx[v] = t
    for j in idxs:
        pi = turns[j].get("panel_item")
        # panel_item は int（1項目）または int配列（複数項目を同時表示）。配列内の各項目にこの発言のstartを割当。
        pis = pi if isinstance(pi, list) else [pi]
        st = float(turns[j].get("start", seg_start))
        for p in pis:
            if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < len(items) and p not in at_by_idx:
                at_by_idx[p] = st
    n = len(items)
    out_items = []
    for k, it in enumerate(items):
        if k in at_by_idx:
            at = at_by_idx[k]
        else:  # 指定なし＝shrink後から章末まで均等に出現（フォールバック）
            at = shrink_at + (seg_end - shrink_at) * (k + 1) / (n + 1)
        ri = {"text": it["text"], "at": round(float(at), 3)}
        if it.get("arrow_from_prev"):
            ri["arrow_from_prev"] = True
        out_items.append(ri)
    # 画像はカット(セリフ)毎に変わるため、ここでは固定しない（attach時に各topicの画像を載せる）。
    resolved = {"items": out_items, "shrinkAt": round(float(shrink_at), 3)}
    if panel.get("bg"):
        resolved["bg"] = panel["bg"]
    if isinstance(panel.get("bgOpacity"), (int, float)) and not isinstance(panel.get("bgOpacity"), bool):
        resolved["bgOpacity"] = panel["bgOpacity"]
    if panel.get("heading"):
        resolved["heading"] = panel["heading"]
    # 項目マーカー/テキストの見た目＋overlay(縮小なし)を素通しでtopic.panelへ。
    for k in ("markerType", "markerColor", "markerSize", "textColor", "textSize", "overlay", "pos"):
        if panel.get(k) is not None:
            resolved[k] = panel[k]
    return resolved


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


def _resolve_bgm_segments(script, bgm_segments):
    """turnアンカーの bgmSegments を {file,start,end,fadeIn?,fadeOut?} の秒区間へ解決（純関数）。

    - startTurnId の start 〜 endTurnId の end。endTurnId未指定は次セグメント直前まで継続（imageCuesと同規則）。
    - bgm=None（未設定）区間は無音＝出力しない。
    - 並びは開始セリフ順。idが見つからないセグメントは無視。
    """
    if not bgm_segments:
        return None
    idx = {t.get("id"): i for i, t in enumerate(script) if t.get("id") is not None}
    arr = []
    for seg in bgm_segments:
        si = idx.get(seg.get("startTurnId"))
        if si is None:
            continue
        ei = idx.get(seg.get("endTurnId")) if seg.get("endTurnId") is not None else None
        arr.append((si, ei, seg))
    arr.sort(key=lambda x: x[0])
    n = len(script)
    out = []
    for k, (si, ei, seg) in enumerate(arr):
        next_start = arr[k + 1][0] if k + 1 < len(arr) else n
        end_i = ei if (ei is not None and ei >= si) else next_start - 1
        end_i = min(end_i, n - 1)
        if not seg.get("bgm"):
            continue                                  # 未設定＝無音（出さない）
        start_t = float(script[si].get("start", 0.0) or 0.0)
        end_t = float(script[end_i].get("end", start_t) or start_t)
        if end_t <= start_t:
            continue
        o = {"file": seg["bgm"], "start": round(start_t, 3), "end": round(end_t, 3)}
        if seg.get("fadeIn"):
            o["fadeIn"] = float(seg["fadeIn"])
        if seg.get("fadeOut"):
            o["fadeOut"] = float(seg["fadeOut"])
        out.append(o)
    return out or None


def build_audio(config, script, bgm_segments=None):
    """meta.audio を組み立てる（純関数）。BGM設定＋SEイベント列（発言timingに同期）。
    bgm_segments（turnアンカー・任意）があれば meta.audio.bgmSegments へ秒解決する（章/区間ごとBGM）。

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
    resolved_bgm = _resolve_bgm_segments(script, bgm_segments)
    if not ac and not resolved_bgm:
        return None                                   # config.audioもbgm区間も無ければ音声ブロックなし
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
        "bgm": ac.get("bgm"),                       # {file, volume, fade} or None（全体BGM＝音量/末尾fadeの基準・後方互換）
        "bgmSegments": resolved_bgm,                 # 区間ごとBGM [{file,start,end,fadeIn?,fadeOut?}] or None（あれば優先）
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


def is_managed_closing(turn):
    """append_closing_chorus が毎回作り直す締めターンか（純関数）。
    closing/chorus フラグ付き、または outro に書かれた定型CTA（高評価/チャンネル登録）。
    プレビュー状態判定（review_server.audio_affecting_changed）でも音声影響の比較から
    除外するために共有する＝ここはパイプラインが固定生成する枠で、手編集しても再生成で
    上書きされるため、差分が残り続けて永久に audio-stale になるのを防ぐ。"""
    txt = turn.get("text") or ""
    return bool(turn.get("closing") or turn.get("chorus")
                or (turn.get("section") == "outro"
                    and ("チャンネル登録" in txt or "高評価" in txt)))


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

    # 作り直し前に、締め行へ人手で付けた小演出(テロップ/リアクション/声/間)をテキストで控え、新行へ引き継ぐ。
    _CARRY = ("textEffects", "telop", "telopSize", "telopX", "telopY", "telopDur", "telopStart", "telopEnd",
              "telopColor", "telopBg", "telopBorder",
              "reaction", "reactionSize", "reactionX", "reactionY", "reactionDur", "voice", "pause")
    old_fx = {}
    for t in (script_result.get("script") or []):
        if is_managed_closing(t):   # 既存の締め＝固定CTA/ユニゾン/旧定型を作り直し対象に
            txt = (t.get("text") or "").strip()
            fx = {k: t[k] for k in _CARRY if k in t}
            if txt and fx:
                old_fx[txt] = fx
    script = [t for t in (script_result.get("script") or [])
              if not is_managed_closing(t)]
    last = script[-1] if script else {}
    ch, cut = last.get("chapter", 0), last.get("cut", 0)
    explainer = s.get("explainer", story_script.DEFAULT_EXPLAINER)

    def _line(speaker, text, emotion="happy", chorus=False):
        t = {"speaker": speaker, "text": text, "emotion": emotion, "section": "outro",
             "chapter": ch, "effect": "kenburns", "cut": cut, "closing": True}
        if chorus:
            t["chorus"] = True
        fx = old_fx.get((text or "").strip())  # 人手で付けた小演出を引き継ぐ
        if fx:
            t.update(fx)
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

    assets_by = {a.get("id"): a for a in (script_result.get("assets") or [])}
    script = []
    for turn, t in zip(base, turns):
        merged = {**turn, "start": t["start"], "end": t["end"], "sentences": t["sentences"]}
        # 範囲が特定できない(stale)文字演出は動画へ出さない（誤った位置の強調を防ぐ）。
        # staleはレビューUI用に script.json には残るが、meta には含めない。
        fx = merged.get("textEffects")
        if fx:
            active = [e for e in fx if not e.get("stale")]
            if active:
                merged["textEffects"] = active
            else:
                merged.pop("textEffects", None)
        # スライドイン画像オーバーレイ（小演出）：文字位置→絶対秒へ解決し meta へ。raw は meta から外す。
        ov = _resolve_overlays(merged, assets_by)   # merged は imageOverlays＋start/end/text を持つ
        merged.pop("imageOverlays", None)
        if ov:
            merged["overlays"] = ov
        script.append(merged)

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

    # editor権威なら assets/imageCues を「正」として画像を解決（legacy の image_cuts/cut は使わない）。
    # 非editor（legacy）は従来どおり image_files/cut_opts/cut アンカー経路。
    # クレジットも editor では「使用中cueが参照するassetのattribution」だけから作る（未使用は除外）。
    # 大演出も editor では visualSegments を「正」とし、legacy(vizList+turnフラグ)へ復元して
    # 既存 _resolve_viz_segments へ渡す（resolver再利用で等価保証）。topics 用にコピーへ復元する。
    turn_image = None
    credit_attr = attributions
    viz_turns, viz_chapters = script, chapters
    if script_result.get("editorModelAuthority") == "editor":
        from src import editor_model
        turn_image = editor_model.resolve_turn_images(
            script, script_result.get("assets") or [], script_result.get("imageCues") or [])
        credit_attr = editor_model.editor_attributions(
            script_result.get("assets") or [], script_result.get("imageCues") or [])
        viz_turns, viz_chapters = copy.deepcopy(script), copy.deepcopy(chapters)
        editor_model.reconstruct_legacy_viz(
            viz_turns, viz_chapters, script_result.get("visualSegments") or [])

    return {
        "generated_at": now_iso,
        "title": script_result.get("theme"),
        "speakers": speakers,
        # script(merged) は timing(start/end) と cut(台本由来)の両方を持つ。turns(TTS)は cut を
        # 持たないので必ず script を渡す（C-1のcutアンカーを効かせるため）。
        # editor時は大演出を visualSegments から復元した viz_turns/viz_chapters を使う。
        "topics": build_chapter_topics(segments, viz_turns, viz_chapters, image_files, attributions,
                                       cut_opts, turn_image=turn_image),
        "credits": build_credits(config, credit_attr),
        "audio": build_audio(config, script, script_result.get("bgmSegments")),
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
    parser.add_argument("--short-from", type=int, default=None,
                        help="本編(--from-script)の指定trivia章を縦ショート用の自己完結短尺台本に書き直す")
    parser.add_argument("--shorts-from", default=None,
                        help="本編の複数trivia章をGemini1回でまとめてショート化（例 \"1,2,3\"）。各 docs/shorts/<slug>/ へ")
    parser.add_argument("--slug", default=None,
                        help="ショートの出力名。指定時は出力先を docs/shorts/<slug>/ にする")
    args = parser.parse_args()

    load_dotenv()  # .env を自動読込（source忘れ対策・既存環境変数は優先）
    config = load_config(args.config)

    # --shorts-from: 本編の複数ネタを Gemini 1回でまとめてショート化（各 docs/shorts/<slug>/ へ）。
    # 画像取得まで実行してレビュー待ち（各ショートを /story で確認→ --images-from-dir で続行）。
    if args.shorts_from:
        if not args.from_script:
            parser.error("--shorts-from は --from-script（本編 script.json）が必須です")
        from src import image_fetch
        with open(args.from_script, encoding="utf-8") as f:
            main_script = json.load(f)
        main_script["chapters"] = story_script._clean_chapters(main_script.get("chapters"))
        story_script.normalize_turns(main_script["script"], main_script["chapters"])
        idxs = [int(x) for x in str(args.shorts_from).split(",") if x.strip()]
        logger.info(f"=== ショート一括生成: 第{idxs}章 を Gemini 1回でショート化 ===")
        shorts = story_script.generate_shorts_batch(config, main_script, idxs)
        for s in shorts:
            sr = s["script_result"]
            title = (sr.get("chapters") or [{}])[0].get("title") or sr.get("theme") or "short"
            slug = f"ch{s['source_chapter']}_{_short_slug(title)}"
            d = Path("docs/shorts") / slug
            d.mkdir(parents=True, exist_ok=True)
            image_files, attributions = image_fetch.fetch_images(sr["chapters"], str(d), config)
            review = build_review(sr["chapters"], image_files, attributions)
            with open(d / "review.json", "w", encoding="utf-8") as f:
                json.dump(review, f, ensure_ascii=False, indent=2)
            with open(d / "script.json", "w", encoding="utf-8") as f:
                json.dump(sr, f, ensure_ascii=False, indent=2)
            logger.info(f"  → {d}（画像{len(image_files)}件取得・レビュー待ち）")
        logger.info(f"=== ショート {len(shorts)}本の台本＋画像取得 完了。各 /story でレビュー後、"
                    f"--from-script docs/shorts/<slug>/script.json --images-from-dir で続行 ===")
        return

    # ショートは docs/shorts/<slug>/ へ独立出力（本編と混ざらない）。
    out_dir = Path("docs/shorts") / args.slug if args.slug else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== 実は〇〇雑学 動画パイプライン開始 ===")

    # 1. 台本：ショート化（--short-from）／既存script（--from-script）／Gemini新規生成
    if args.short_from is not None:
        if not args.from_script:
            parser.error("--short-from は --from-script（本編 script.json）が必須です")
        with open(args.from_script, encoding="utf-8") as f:
            main_script = json.load(f)
        main_script["chapters"] = story_script._clean_chapters(main_script.get("chapters"))
        story_script.normalize_turns(main_script["script"], main_script["chapters"])
        logger.info(f"ショート化: {args.from_script} の第{args.short_from}章を単体短尺台本へ")
        script_result = story_script.shortify_chapter(config, main_script, args.short_from)
    elif args.from_script:
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
        # 深掘りストーリーは「主題」単位で重複回避（過去に扱った主題と被らせない）。
        avoid = [{"title": t} for t in topic_history.used_themes(genre)]
        if avoid:
            logger.info(f"既出の主題 {len(avoid)}件を避けて生成（ジャンル: {genre}）")
        script_result = story_script.generate_story_script(config, also_avoid=avoid)

    # ショート判定（締めユニゾンを付けない／文字数予算を約40秒に）。
    # --short-from・docs/shorts 出力（compose取り込み短の --from-script 続行を含む）はショート扱い。
    is_short = args.short_from is not None or str(out_dir).startswith("docs/shorts")

    # 締めに二人同時(ユニゾン)の固定挨拶を足す（本編のみ。ショートは付けない＝CTAは動画側で出す）。
    if not is_short:
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
    # ショート（is_short・上で算出）は本編8分でなく約40秒の目標で判定する。
    s_cfg = config.get("story", {})
    if is_short:
        budget = int(SHORT_TARGET_SECONDS / 60 * story_script.CHARS_PER_MINUTE)
    else:
        budget = int(float(s_cfg.get("target_minutes", story_script.DEFAULT_MINUTES)) * story_script.CHARS_PER_MINUTE)
    total_chars = sum(len(t.get("text") or "") for t in script_result.get("script", []))
    est_min = total_chars / story_script.CHARS_PER_MINUTE if story_script.CHARS_PER_MINUTE else 0
    unit = "秒" if is_short else "分"
    est = est_min * 60 if is_short else est_min
    if budget and total_chars > budget * 1.25:
        logger.warning(f"台本が長すぎます: {total_chars}字（予算{budget}字・推定{est:.0f}{unit}）。"
                       f"レビューでターンを削るか作り直しを検討してください。")
    else:
        logger.info(f"台本 {total_chars}字（推定{est:.0f}{unit}・予算{budget}字）")

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

    # 動画を確定（meta生成＝採用）した時点で、この動画の主題を永続履歴に記録。
    # 深掘りストーリーは主題単位で重複回避する（章＝物語ビートは主題に従属するので facts は記録しない）。
    from src import topic_history
    genre = topic_history.genre_of(config)
    if topic_history.add_theme(genre, script_result.get("theme")):
        logger.info(f"採用主題を履歴に記録: 「{script_result.get('theme')}」（ジャンル: {genre}・以降の重複回避対象）")

    # 概要欄用クレジット（動画内には出さない。CC-BY帰属はここで要件を満たす）。
    # editor権威では使用中assetのattributionだけを使う（meta.creditsと同じ source）。
    credit_attr = attributions
    if script_result.get("editorModelAuthority") == "editor":
        from src import editor_model
        credit_attr = editor_model.editor_attributions(
            script_result.get("assets") or [], script_result.get("imageCues") or [])
    write_credits_txt(out_dir, config, credit_attr)

    dur = meta["topics"][-1]["end"] if meta["topics"] else 0.0
    logger.info(f"=== 完了: {out_dir} （{len(meta['script'])}ターン・{len(meta['topics'])}章・{dur:.1f}秒）===")
    logger.info(f"動画化: cd video && SRC_DIR=../{out_dir} npm run render")


if __name__ == "__main__":
    main()
