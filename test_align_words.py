"""align_words（単語単位アライメント）のロジックを Whisper 抜きでテストする。"""
import sys
from dataclasses import dataclass
sys.path.insert(0, "src")
import aligner


@dataclass
class Word:
    text: str
    start: float
    end: float


def collapse(result):
    c = 0
    for t in result:
        if t["end"] - t["start"] <= 0:
            c += 1
        for s in t.get("sentences", []):
            if s["end"] - s["start"] <= 0:
                c += 1
    return c


def gaps(result):
    g = 0
    for t in result:
        ss = t.get("sentences", [])
        for i in range(1, len(ss)):
            if abs(ss[i]["start"] - ss[i - 1]["end"]) > 1e-6:
                g += 1
    return g


def show(result):
    for ti, t in enumerate(result):
        print(f"  ターン{ti} [{t['start']:.2f}-{t['end']:.2f}] {t['speaker']}")
        for s in t.get("sentences", []):
            print(f"      [{s['start']:6.2f}-{s['end']:6.2f}] {s['text'][:20]}")


fails = []
LO = aligner.LEAD_OFFSET

script = [
    {"speaker": "アナ", "text": "今日のニュースです。最初の話題は経済についてです。"},
    {"speaker": "ケン", "text": "はい、株価が上昇しました。市場は活気づいています。"},
]
ts = [{"start": 0.0, "end": 6.0} for _ in script]
# 実境界: 文2終わり=文3始まり=8.7秒

# --- S1: クリーンな単語時刻。文境界が実測どおり（セグメント方式より精密） ---
words1 = [Word(*x) for x in [
    ("今日の", 0.0, 1.0), ("ニュースです", 1.0, 2.0),
    ("最初の話題は", 2.0, 3.5), ("経済についてです", 3.5, 5.5),
    ("はい", 5.5, 6.0), ("株価が上昇しました", 6.0, 8.7),
    ("市場は", 8.7, 9.5), ("活気づいています", 9.5, 12.0),
]]
r1 = aligner.align_words(words1, script, ts, total_duration=12.0)
print("=== S1: クリーン（単語境界が実測）===")
show(r1)
if collapse(r1) or gaps(r1):
    fails.append("S1: 潰れ/すき間")
s3 = r1[1]["sentences"][1]["start"]  # 文3の開始
if not (8.7 - LO - 0.3 <= s3 <= 8.7 - LO + 0.3):
    fails.append(f"S1: 文3開始が不正確 start={s3}（期待≈{8.7 - LO}）")

# --- S2: 誤認識（活気→下記）でも文字類似で吸収し境界は保たれる ---
words2 = [Word(*x) for x in [
    ("今日の", 0.0, 1.0), ("ニュースです", 1.0, 2.0),
    ("最初の話題は", 2.0, 3.5), ("経済についてです", 3.5, 5.5),
    ("はい", 5.5, 6.0), ("株価が上昇しました", 6.0, 8.7),
    ("市場は", 8.7, 9.5), ("下記づいています", 9.5, 12.0),
]]
r2 = aligner.align_words(words2, script, ts, total_duration=12.0)
print("\n=== S2: 誤認識（活気→下記）===")
show(r2)
if collapse(r2) or gaps(r2):
    fails.append("S2: 潰れ/すき間")
s3b = r2[1]["sentences"][1]["start"]
if not (8.7 - LO - 0.4 <= s3b <= 8.7 - LO + 0.4):
    fails.append(f"S2: 誤認識時の文3開始が不正確 start={s3b}")

# --- S3: dict入力（_transcribe→_flatten_words の形）でも動く ---
words3 = [{"text": t, "start": s, "end": e} for (t, s, e) in [
    ("今日のニュースです", 0.0, 2.0), ("最初の話題は経済についてです", 2.0, 5.5),
    ("はい株価が上昇しました", 5.5, 8.7), ("市場は活気づいています", 8.7, 12.0),
]]
r3 = aligner.align_words(words3, script, ts, total_duration=12.0)
print("\n=== S3: dict入力 ===")
show(r3)
if collapse(r3) or gaps(r3):
    fails.append("S3: 潰れ/すき間")

print("\n" + ("FAIL: " + "; ".join(fails) if fails else "ALL PASS"))
sys.exit(1 if fails else 0)
