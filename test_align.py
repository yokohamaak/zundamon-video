"""align_segments のロジックを Whisper 抜きでテストする。"""
import sys
from dataclasses import dataclass
sys.path.insert(0, "src")
import aligner


@dataclass
class Seg:
    text: str
    start: float
    end: float


def build_segments(pieces, cps=6.0, t0=256.0):
    """(認識テキスト) のリストから norm長さ/cps で連続セグメントを作る。"""
    segs, t = [], t0
    for text in pieces:
        nlen = max(len(aligner._normalize(text)), 1)
        dur = nlen / cps
        segs.append(Seg(text, round(t, 2), round(t + dur, 2)))
        t += dur
    return segs


def count_gaps(result):
    """ターン内で文と文の間にすき間（前の文end != 次の文start）がある数。"""
    g = 0
    for turn in result:
        ss = turn.get("sentences", [])
        for i in range(1, len(ss)):
            if abs(ss[i]["start"] - ss[i - 1]["end"]) > 1e-6:
                g += 1
    return g


def count_turn_gaps(result):
    """ターン間のすき間（前ターンend != 次ターンstart）の数。"""
    g = 0
    for i in range(1, len(result)):
        if abs(result[i]["start"] - result[i - 1]["end"]) > 1e-6:
            g += 1
    return g


def count_collapse(result):
    c = 0
    for turn in result:
        if turn["end"] - turn["start"] <= 0:
            c += 1
        for s in turn.get("sentences", []):
            if s["end"] - s["start"] <= 0:
                c += 1
    return c


def show(result):
    for ti, turn in enumerate(result):
        print(f"  ターン{ti} [{turn['start']:.2f}-{turn['end']:.2f}] {turn['speaker']}")
        for s in turn.get("sentences", []):
            print(f"      [{s['start']:6.2f}-{s['end']:6.2f}] {s['text'][:24]}")


fails = []

# --- シナリオ1: 重複フレーズの遷移（本来のバグ） ---
script1 = [
    {"speaker": "ケン", "text": "どういたしまして、アナさん。"},
    {"speaker": "アナ", "text": "さて、前半は日本のトップニュースをお伝えしましたが、後半はグローバルな視点に切り替えて、「バイブコーディング最前線」と題し、AIと開発ツールの最新動向を深掘りしていきます。特に、私たちの仕事や開発現場にどのような変化をもたらすのか、ケンさんに具体的な視点で解説していただきます。後半もどうぞお楽しみに！"},
    {"speaker": "アナ", "text": "「テック&トーク・ウェーブ」、後半は「バイブコーディング最前線」です。ケンさん、早速ですが、Microsoftから新しいAIモデルが発表されたというニュースが注目を集めていますね。"},
]
seg1 = build_segments([
    "どういたしましてアナさん",
    "さて前半は日本のトップニュースをお伝えしましたが",
    "後半はグローバルな視点に切り替えて",
    "バイブコーディング最前線と題し",
    "AIと開発ツールの最新動向を深掘りしていきます",
    "特に私たちの仕事や開発現場にどのような変化をもたらすのか",
    "ケンさんに具体的な視点で解説していただきます",
    "後半もどうぞお楽しみに",
    "テックアンドトークウェーブ後半はバイブコーディング最前線です",
    "ケンさん早速ですがマイクロソフトから新しいAIモデルが発表されたというニュースが注目を集めていますね",
])
ts1 = [{"start": 256.0, "end": 300.0} for _ in script1]
r1 = aligner.align_segments(seg1, script1, ts1)
print("=== シナリオ1: 重複フレーズの遷移 ===")
show(r1)
# turn2 は2個目の出現（>280）にマッチすべき。飛び越していれば264付近になる
if count_collapse(r1) != 0:
    fails.append("S1: 潰れ発生")
if r1[2]["start"] < 278:
    fails.append(f"S1: turn2が飛び越し（start={r1[2]['start']}、期待>278）")

# --- シナリオ2: 通常の順次（重複なし） ---
script2 = [
    {"speaker": "アナ", "text": "今日のニュースです。最初の話題は経済についてです。"},
    {"speaker": "ケン", "text": "はい、株価が上昇しました。市場は活気づいています。"},
]
seg2 = build_segments([
    "今日のニュースです",
    "最初の話題は経済についてです",
    "はい株価が上昇しました",
    "市場は活気づいています",
])
ts2 = [{"start": 256.0, "end": 300.0} for _ in script2]
r2 = aligner.align_segments(seg2, script2, ts2)
print("\n=== シナリオ2: 通常順次 ===")
show(r2)
if count_collapse(r2) != 0:
    fails.append("S2: 潰れ発生")
# 各文がほぼ対応セグメント時刻に乗っているか（最初の文が256付近）
if not (255 <= r2[0]["start"] <= 257):
    fails.append(f"S2: 先頭ずれ（start={r2[0]['start']}）")

# --- シナリオ3: Whisperが1文取りこぼし ---
script3 = [
    {"speaker": "アナ", "text": "おはようございます。今日は晴れです。気温も上がります。"},
]
seg3 = build_segments([
    "おはようございます",
    # 「今日は晴れです」を取りこぼし
    "気温も上がります",
])
ts3 = [{"start": 256.0, "end": 300.0} for _ in script3]
r3 = aligner.align_segments(seg3, script3, ts3)
print("\n=== シナリオ3: 1文取りこぼし ===")
show(r3)
if count_collapse(r3) != 0:
    fails.append("S3: 潰れ発生")

# すき間ゼロ（文が連続）を全シナリオで確認
for name, r in [("S1", r1), ("S2", r2), ("S3", r3)]:
    g = count_gaps(r)
    if g:
        fails.append(f"{name}: 文間すき間{g}箇所")


# --- シナリオ4: ターン内に長い文が混在（尺の偏り→白の早送り） ---
script4 = [
    {"speaker": "ケン", "text": "これは重要です。" + "当初適切にライセンスされたデータと発表されたものが後にプロプライエタリなウェブクロールを使用しAI生成コンテンツのフィルタリングも行ったと詳細が明かされたことで開発者としては知的財産権やバイアスについてより一層注意を払う必要が出てきます。" + "注意しましょう。"},
]
# Whisper は長文を途中までしか拾えず、短い末尾文を大きく拾った想定
seg4 = build_segments(["これは重要です", "当初適切にライセンスされたデータと発表された"], t0=369.6)
# 末尾文を遅い時刻で拾う（長文の尺が不足するケースを作る）
seg4.append(Seg("注意しましょう", 388.0, 403.0))
ts4 = [{"start": 369.6, "end": 403.0} for _ in script4]
r4 = aligner.align_segments(seg4, script4, ts4)
print("\n=== シナリオ4: 長文混在の尺均し ===")
show(r4)
# 各文の cps が概ね一様か（最大/最小が2倍以内）
ss = r4[0]["sentences"]
cpss = []
for s in ss:
    import unicodedata as U, re as R
    n = len(R.sub(r'[^\w぀-ヿ一-鿿]', '', U.normalize("NFKC", s["text"])))
    d = s["end"] - s["start"]
    cpss.append(n / d if d > 0 else 999)
if max(cpss) > min(cpss) * 2:
    fails.append(f"S4: cpsが不均一 {[round(c,1) for c in cpss]}")
if count_gaps(r4) or count_collapse(r4):
    fails.append("S4: すき間/潰れ発生")

# --- シナリオ5: ターン境界のすき間（次ターンの表示遅れ） ---
script5 = [
    {"speaker": "アナ", "text": "テック&トーク・ウェーブ、後半はバイブコーディング最前線です。"},
    {"speaker": "ケン", "text": "はい、Microsoftが「Build 2026」で複数の新しいモデルを発表しました。"},
]
# turn1 の頭が未マッチで、開始セグメントが3秒遅れて拾われる想定
seg5 = [
    Seg("テックアンドトークウェーブ後半はバイブコーディング最前線です", 276.2, 286.6),
    # 286.6-289.6 が未マッチ（turn1 の頭の音声）
    Seg("はいマイクロソフトがビルド2026で複数の新しいモデルを発表しました", 289.6, 300.0),
]
ts5 = [{"start": 276.0, "end": 300.0} for _ in script5]
r5 = aligner.align_segments(seg5, script5, ts5)
print("\n=== シナリオ5: ターン境界のすき間 ===")
show(r5)
# turn1 の開始が前ターンend（286.6）に寄っているべき（289.6のままなら遅れ）
if r5[1]["start"] > 287:
    fails.append(f"S5: ターン境界に遅れ（turn1.start={r5[1]['start']}、期待≈286.6）")
if count_turn_gaps(r5):
    fails.append(f"S5: ターン間すき間{count_turn_gaps(r5)}箇所")

# --- シナリオ6: 末尾ターンのendを音声実尺へ寄せる ---
script6 = [
    {"speaker": "アナ", "text": "今日はここまでです。"},
    {"speaker": "ケン", "text": "それではまた来週。ホストのアナと、コメンテーターのケンでした。"},
]
# Whisperが末尾を取りこぼし、最後のセグメントが早く終わる想定（実尺は560秒）
seg6 = [
    Seg("今日はここまでです", 550.0, 552.0),
    Seg("それではまた来週", 552.0, 554.0),  # 末尾文「ホストの…でした」を取りこぼし
]
ts6 = [{"start": 550.0, "end": 560.0} for _ in script6]
r6 = aligner.align_segments(seg6, script6, ts6, total_duration=560.0)
print("\n=== シナリオ6: 末尾を実尺へ ===")
show(r6)
exp6 = round(560.0 - aligner.LEAD_OFFSET, 3)
if abs(r6[-1]["end"] - exp6) > 1e-6:
    fails.append(f"S6: 末尾endが実尺でない（end={r6[-1]['end']}、期待{exp6}）")

print("\n" + ("FAIL: " + "; ".join(fails) if fails else "ALL PASS"))
sys.exit(1 if fails else 0)
