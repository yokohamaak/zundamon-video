"""
Whisperによる音声アライメントモジュール
セグメント単位アンカー方式：各Whisperセグメントを現在位置以降のスクリプトに
局所マッチさせることで、誤マッチの累積ドリフトを防ぐ。
単語単位方式（align_words）：Whisperの単語タイムスタンプを使い、台本文字列と
認識文字列をdifflibで文字単位アライメントして文境界を実測する（精度向上の試験実装）。

Whisper入出力（_transcribe）と純粋なマッチングロジック（align_segments /
align_words / _finalize）を分離しており、純粋関数はモックで単体テスト可能。
"""
import difflib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# 字幕全体の前倒し秒数（Whisperのオンセット遅れ補正。体感に合わせて調整）
LEAD_OFFSET = 0.5


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[^\w぀-ヿ一-鿿]', '', text)
    return text


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[。！？])', text)
    return [p for p in parts if p.strip()]


def _transcribe(audio_path: str, words: bool = False) -> list:
    """faster-whisperで音声を解析しセグメントのリストを返す。
    words=Trueで各セグメントに単語単位タイムスタンプ(.words)を付与する。"""
    from faster_whisper import WhisperModel  # 重い依存は遅延import
    logger.info("Whisperで音声解析中...")
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, language="ja", vad_filter=True,
                                      word_timestamps=words)
    segments = list(segments)
    logger.info(f"Whisper解析完了: {len(segments)} セグメント")
    return segments, getattr(info, "duration", None)


def _flatten_words(segments: list) -> list:
    """セグメント列から単語dict [{text,start,end}] のフラットリストを作る。"""
    words = []
    for seg in segments:
        for w in (getattr(seg, "words", None) or []):
            words.append({"text": w.word, "start": w.start, "end": w.end})
    return words


def _finalize(script: list, sent_times: dict, matched_set: set,
              turn_timestamps: list, total_duration: float = None) -> list:
    """sent_times（(ti,si)->(start,end)）から最終スクリプトを組み立てる共通後処理。
    未マッチ文の補間・ターン連続化・末尾実尺補正・文配置・LEAD_OFFSET前倒しを行う。
    align_segments / align_words のどちらからも使う。"""
    # スクリプトを文単位に分割してフラットリストにする
    all_sents = []
    for ti, turn in enumerate(script):
        sents = _split_sentences(turn["text"]) or [turn["text"]]
        for si, sent in enumerate(sents):
            all_sents.append((ti, si, sent, _normalize(sent)))

    # マッチしなかった文を前後補間で埋める
    def interpolate(key):
        idx = next(i for i, x in enumerate(all_sents) if x[0] == key[0] and x[1] == key[1])
        prev_t = next_t = None
        for di in range(1, len(all_sents)):
            if idx - di >= 0:
                pk = (all_sents[idx - di][0], all_sents[idx - di][1])
                if pk in sent_times:
                    prev_t = sent_times[pk][1]
                    break
        for di in range(1, len(all_sents)):
            if idx + di < len(all_sents):
                nk = (all_sents[idx + di][0], all_sents[idx + di][1])
                if nk in sent_times:
                    next_t = sent_times[nk][0]
                    break
        if prev_t is not None and next_t is not None:
            return (prev_t, next_t)
        elif prev_t is not None:
            return (prev_t, prev_t + 1.0)
        elif next_t is not None:
            return (max(0, next_t - 1.0), next_t)
        return (turn_timestamps[key[0]]["start"], turn_timestamps[key[0]]["end"])

    for ti, si, _, _ in all_sents:
        if (ti, si) not in sent_times:
            sent_times[(ti, si)] = interpolate((ti, si))

    # --- パス1: 各ターンの素のマッチ区間（先頭文start〜末尾文end）を求める ---
    turn_sents = []
    spans = []
    for ti, (turn, ts) in enumerate(zip(script, turn_timestamps)):
        sents = _split_sentences(turn["text"]) or [turn["text"]]
        t = [sent_times.get((ti, si), (ts["start"], ts["end"])) for si in range(len(sents))]
        turn_sents.append(sents)
        spans.append([t[0][0], max(e for _, e in t)])

    # --- パス2: ターン間を連続させる（次ターンのstartを前ターンのendに合わせる） ---
    # 境界のすき間（未マッチ音声）は次ターンの読み上げなので次ターンに寄せ、
    # 重なりも解消する。これでターン表示の遅れ／前ターンの居座りを防ぐ。
    for ti in range(1, len(spans)):
        prev_end = spans[ti - 1][1]
        spans[ti][0] = prev_end
        if spans[ti][1] < spans[ti][0]:
            spans[ti][1] = spans[ti][0]

    # 末尾ターンのendを音声の実尺へ寄せる（Whisperは末尾を取りこぼしやすく、
    # 末尾は伸ばす先のターンが無いため、確実な total_duration で補う）。
    if spans and total_duration and total_duration > spans[-1][1]:
        spans[-1][1] = total_duration

    # --- パス3: ターン区間内に各文を配置 ---
    # マッチが健全なターンは、マッチ時刻の相対位置をターン区間へ線形マップ
    # （実際の読み上げ速度の偏りを反映）。壊れたターンは文字数比にフォールバック。
    def _healthy(ti, sents):
        n = len(sents)
        if any((ti, si) not in matched_set for si in range(n)):
            return False
        raw = [sent_times[(ti, si)] for si in range(n)]
        if raw[-1][1] <= raw[0][0]:
            return False
        for si in range(n):
            s, e = raw[si]
            if e < s or (si > 0 and s < raw[si - 1][0]):
                return False
            nlen = max(len(_normalize(sents[si])), 1)
            dur = e - s
            cps = nlen / dur if dur > 0 else 999
            if cps > 13 or cps < 2:  # 速すぎ/遅すぎ＝マッチ破綻
                return False
        return True

    updated = []
    for ti, turn in enumerate(script):
        sents = turn_sents[ti]
        turn_start, turn_end = spans[ti]
        n = len(sents)

        if n <= 1 or turn_end <= turn_start:
            times = [(turn_start, turn_end)]
        elif _healthy(ti, sents):
            # マッチ相対位置をターン区間へ線形マップ
            raw = [sent_times[(ti, si)] for si in range(n)]
            lo, hi = raw[0][0], raw[-1][1]
            scale = (turn_end - turn_start) / (hi - lo)
            times = [(turn_start + (s - lo) * scale, turn_start + (e - lo) * scale)
                     for s, e in raw]
            # 連続化（すき間は次の文へ寄せる）
            for i in range(1, n):
                times[i] = (times[i - 1][1], max(times[i][1], times[i - 1][1]))
        else:
            # 文字数比フォールバック
            lengths = [max(len(_normalize(x)), 1) for x in sents]
            total = sum(lengths)
            cur = turn_start
            times = []
            for L in lengths:
                d = (turn_end - turn_start) * L / total
                times.append((cur, cur + d))
                cur += d

        entry = dict(turn)
        entry.pop("sentences", None)  # 入力に残る古い（混入した）sentencesを必ず破棄
        entry["start"] = round(turn_start, 3)
        entry["end"] = round(turn_end, 3)

        if len(sents) > 1:
            entry["sentences"] = [
                {"text": s, "start": round(t[0], 3), "end": round(t[1], 3)}
                for s, t in zip(sents, times)
            ]

        updated.append(entry)

    # 全体を一定秒だけ前倒し（Whisperの発話オンセット遅れ＝字幕が一律遅れる分を補正）。
    if LEAD_OFFSET:
        for entry in updated:
            entry["start"] = round(max(0.0, entry["start"] - LEAD_OFFSET), 3)
            entry["end"] = round(max(0.0, entry["end"] - LEAD_OFFSET), 3)
            for s in entry.get("sentences", []):
                s["start"] = round(max(0.0, s["start"] - LEAD_OFFSET), 3)
                s["end"] = round(max(0.0, s["end"] - LEAD_OFFSET), 3)

    return updated


def align_segments(segments: list, script: list, turn_timestamps: list,
                   total_duration: float = None, _diag: dict = None) -> list:
    """
    Whisperセグメント（.text/.start/.end を持つ）とスクリプトを照合し、
    各ターン・各文にタイムスタンプを付与する。Whisper非依存の純粋関数。
    _diag: 診断データ収集用のout-param。渡した場合はsegment/sentマッチ情報を書き込む。
    """
    # スクリプトを文単位に分割してフラットリストにする
    all_sents = []
    for ti, turn in enumerate(script):
        sents = _split_sentences(turn["text"]) or [turn["text"]]
        for si, sent in enumerate(sents):
            all_sents.append((ti, si, sent, _normalize(sent)))

    sent_times = {}      # (ti, si) -> (start, end)
    matched_set = set()  # 実際にセグメントがマッチした文（補間でない）

    script_pos = 0       # スクリプトの探索開始位置（後戻りしない）
    MAX_JUMP = 2         # 1セグメントで前方へ飛べる最大文数（重複フレーズへの飛び越し防止）

    for seg in segments:
        seg_norm = _normalize(seg.text)
        if not seg_norm:
            continue

        # 現在位置以降のスクリプト文の中から最もマッチする文を探す
        best_score = -1.0
        best_ratio = 0.0
        best_idx = None
        window = all_sents[script_pos:script_pos + 10]  # 前方10文の窓

        for wi, (ti, si, sent, norm) in enumerate(window):
            ratio = difflib.SequenceMatcher(None, seg_norm, norm, autojunk=False).ratio()
            # 近接ペナルティ：同じフレーズが複数ある場合に遠い候補へ飛ぶのを防ぐ
            score = ratio - 0.05 * wi
            if score > best_score:
                best_score = score
                best_ratio = ratio
                best_idx = script_pos + wi

        # 採用条件：類似度が閾値以上、かつ前方への飛び越しがMAX_JUMP以内。
        if best_idx is not None and best_ratio > 0.3 and (best_idx - script_pos) <= MAX_JUMP:
            ti, si, _, _ = all_sents[best_idx]
            key = (ti, si)
            if key not in sent_times:
                sent_times[key] = (seg.start, seg.end)
            else:
                # 同じ文に複数セグメントがマッチした場合はendを延ばす
                sent_times[key] = (sent_times[key][0], seg.end)
            matched_set.add(key)
            script_pos = best_idx  # 探索位置を進める（後戻りしない）

    if _diag is not None:
        whisper_text = "".join(_normalize(s.text) for s in segments)
        script_norm = "".join(norm for _, _, _, norm in all_sents)
        overall_ratio = (
            difflib.SequenceMatcher(None, script_norm, whisper_text, autojunk=False).ratio()
            if whisper_text else 0.0
        )
        _diag.update({
            "method": "segments",
            "script_norm": script_norm,
            "whisper_norm": whisper_text,
            "overall_ratio": round(overall_ratio, 4),
            "sents": [
                {
                    "key": f"{ti}-{si}",
                    "text": sent,
                    "matched": (ti, si) in matched_set,
                    "match_chars": None,
                    "total_chars": len(norm),
                }
                for ti, si, sent, norm in all_sents
            ],
        })

    return _finalize(script, sent_times, matched_set, turn_timestamps, total_duration)


def align_words(words: list, script: list, turn_timestamps: list,
                total_duration: float = None, _diag: dict = None) -> list:
    """
    Whisperの単語単位タイムスタンプで各文に時刻を付与する。Whisper非依存の純粋関数。
    words: .text/.start/.end を持つオブジェクト、または {text,start,end} dict のリスト。
    台本の正規化文字列Sと、認識単語の正規化文字列Wをdifflibで文字単位アライメントし、
    各文の文字範囲に対応する単語時刻の最小start/最大endを文の (start,end) とする。
    （複数文を含むセグメント内の境界を"推定"せず、単語時刻から"実測"できる）
    _diag: 診断データ収集用のout-param。渡した場合はS/W/matched_setを書き込む。
    """
    def _wtext(w): return w.text if hasattr(w, "text") else w["text"]
    def _wstart(w): return w.start if hasattr(w, "start") else w["start"]
    def _wend(w): return w.end if hasattr(w, "end") else w["end"]

    # 台本側：正規化文字列Sと、各文の文字範囲 [c0, c1)
    S_chars = []
    sent_range = {}
    sent_texts = {}
    for ti, turn in enumerate(script):
        sents = _split_sentences(turn["text"]) or [turn["text"]]
        for si, sent in enumerate(sents):
            c0 = len(S_chars)
            S_chars.extend(_normalize(sent))
            sent_range[(ti, si)] = (c0, len(S_chars))
            sent_texts[(ti, si)] = sent
    S = "".join(S_chars)

    # 単語側：正規化文字列Wと、各文字→(start,end)
    W_chars = []
    w_time = []
    for w in words:
        norm = _normalize(_wtext(w))
        if not norm:
            continue
        ws, we = _wstart(w), _wend(w)
        W_chars.extend(norm)
        w_time.extend([(ws, we)] * len(norm))
    W = "".join(W_chars)

    # S↔W を文字単位でアライメント（一致ブロックから S→W の位置対応を作る）
    s2w = [-1] * len(S)
    if W:
        sm = difflib.SequenceMatcher(None, S, W, autojunk=False)
        for a, b, size in sm.get_matching_blocks():
            for k in range(size):
                s2w[a + k] = b + k

    sent_times = {}
    matched_set = set()
    for key, (c0, c1) in sent_range.items():
        js = [s2w[i] for i in range(c0, c1) if s2w[i] >= 0]
        if js:
            sent_times[key] = (min(w_time[j][0] for j in js),
                               max(w_time[j][1] for j in js))
            matched_set.add(key)

    if _diag is not None:
        overall_ratio = (sum(1 for v in s2w if v >= 0) / len(s2w)) if s2w else 0.0
        _diag.update({
            "method": "words",
            "script_norm": S,
            "whisper_norm": W,
            "overall_ratio": round(overall_ratio, 4),
            "sents": [
                {
                    "key": f"{ti}-{si}",
                    "text": sent_texts[(ti, si)],
                    "matched": (ti, si) in matched_set,
                    "match_chars": sum(1 for i in range(c0, c1) if s2w[i] >= 0),
                    "total_chars": c1 - c0,
                }
                for (ti, si), (c0, c1) in sorted(sent_range.items())
            ],
        })

    return _finalize(script, sent_times, matched_set, turn_timestamps, total_duration)


_JST = timezone(timedelta(hours=9))
_DIAG_LOG = Path("logs/align_diag.jsonl")


def _append_diag(diag: dict) -> None:
    """診断データをJSONLファイルへ追記する。エラーが出ても本処理は止めない。"""
    try:
        _DIAG_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(_JST).isoformat(), **diag}
        with open(_DIAG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        matched = sum(1 for s in diag.get("sents", []) if s["matched"])
        total = len(diag.get("sents", []))
        logger.info(f"診断ログ追記: {matched}/{total}文マッチ ratio={diag.get('overall_ratio', '?')} → {_DIAG_LOG}")
    except Exception as e:
        logger.warning(f"診断ログ書き込み失敗（本処理は継続）: {e}")


def align_sentences(audio_path: str, script: list, turn_timestamps: list,
                    use_words: bool = False) -> list:
    """音声を解析し、スクリプトにタイムスタンプを付与する。
    use_words=Trueで単語単位タイムスタンプ方式（試験）を使う。取得できなければ
    セグメント方式へフォールバックする。実行のたびにlogs/align_diag.jsonlへ診断を追記。"""
    diag: dict = {}
    if use_words:
        segments, total_duration = _transcribe(audio_path, words=True)
        words = _flatten_words(segments)
        if words:
            logger.info(f"単語単位アライメント方式（{len(words)} 単語）")
            result = align_words(words, script, turn_timestamps, total_duration, _diag=diag)
            _append_diag(diag)
            return result
        logger.warning("単語タイムスタンプが取得できず、セグメント方式にフォールバック")
        result = align_segments(segments, script, turn_timestamps, total_duration, _diag=diag)
        _append_diag(diag)
        return result
    segments, total_duration = _transcribe(audio_path)
    result = align_segments(segments, script, turn_timestamps, total_duration, _diag=diag)
    _append_diag(diag)
    return result
