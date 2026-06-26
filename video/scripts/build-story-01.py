#!/usr/bin/env python3
# 第1話「AIが大丈夫って言ったのだ」の台本(script.md)をStoryVideo用JSONに変換する。
# モブ(営業/部長/AI)は姿なし=声+吹き出しのみ。タイミングは仮値(音声化で実尺に上書き)。
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "story-01.json")

# (speaker, text, scene, expression, enter, emphasis, shake)
# emphasis=True: 話者プッシュイン演出。shake=True: カメラシェイク演出。
# ※ start/end/sentences は make_story_audio.py が実尺で上書きするため、
#   build-story-01.py 再実行は音声再生成を伴う場合のみ行うこと。
TURNS = [
    # ── コールドオープン(office) ──
    ("営業", "サイトに……つながりません。", "office", None, ["zundamon", "metan"], False, True),
    ("営業", "お問い合わせ、300件です。", "office", None, None, False, True),
    ("部長", "セール開始まで、あと20分だぞ！", "office", None, None, False, True),
    ("zundamon", "ど、どどど、どうすればいいのだ〜！？", "office", "panic", None, False, True),
    ("metan", "……ずんだもん。", "office", "normal", None, False, False),
    ("metan", "昨日、何した？", "office", "normal", None, True, False),
    ("zundamon", "昨日……？", "office", "trouble", None, False, False),
    # ── 前日の回想(office) ──
    ("metan", "今日はわたし外出だから。設定の変更作業は、しないでね。", "office", "normal", None, False, False),
    ("zundamon", "わかったのだ！ 留守番はまかせるのだ！", "office", "happy", None, False, False),
    ("zundamon", "むむ……『設定ファイルが古い』って出てるのだ。", "office", "normal", None, False, False),
    ("zundamon", "古いなら、新しくした方がいいに決まってるのだ。", "office", "normal", None, False, False),
    # ── AIに相談(office) ──
    ("zundamon", "こういう時は、AI（エーアイ）に聞くのだ！", "office", "happy", None, False, False),
    ("AI", "一般的な構成では、問題ありません。", "office", None, None, False, False),
    ("AI", "ただし、ご利用の環境によっては、異なる場合があります。", "office", None, None, False, False),
    ("zundamon", "『問題ありません』！ AI（エーアイ）が大丈夫って言ってるのだ！", "office", "happy", None, False, False),
    ("zundamon", "ぽちぽち……サービス再起動っと。", "office", "normal", None, False, False),
    ("zundamon", "ヨシ！ 今日も平和なのだ！", "office", "happy", None, False, False),
    # ── 帰宅(home) ──
    ("zundamon", "おつかれさまなのだ〜", "home", "happy", ["zundamon"], False, False),
    # ── 現在へ戻る(office・障害対応) ──
    ("metan", "で。昨日、何した？", "office", "normal", ["zundamon", "metan"], True, False),
    ("zundamon", "ええと……昨日、設定をひとつ、変えたのだ。", "office", "trouble", None, False, False),
    ("metan", "それ、誰にレビューしてもらった？", "office", "normal", None, False, False),
    ("zundamon", "AI（エーアイ）なのだ！", "office", "happy", None, False, False),
    ("zundamon", "な、なんで黙るのだ〜！？", "office", "panic", None, False, False),
    # ── 調査開始(office) ──
    ("metan", "とにかくログを見るわよ。落ち着いて。", "office", "normal", None, False, False),
    ("zundamon", "書き方は、ちゃんと新しくなってるのだ……。なんで動かないのだ？", "office", "trouble", None, False, False),
    ("metan", "……ねえ。AI（エーアイ）は、なんて言ってた？", "office", "normal", None, False, False),
    ("zundamon", "『一般的な構成では問題ありません』なのだ！", "office", "normal", None, False, False),
    ("metan", "……その続きは？ 最後まで読んだ？", "office", "normal", None, False, False),
    ("zundamon", "……つ、続き？", "office", "panic", None, True, False),
    ("zundamon", "……『環境によっては、異なる場合があります』……。", "office", "trouble", None, False, False),
    ("metan", "うちの環境は、“一般的”？", "office", "normal", None, False, False),
    ("zundamon", "……ちがう、のだ……。", "office", "trouble", None, False, False),
    # ── 真相(server_room) ──
    ("metan", "うちはね、独自の社内設定が足してあるの。AI（エーアイ）は、それを知らない。", "server_room", "normal", ["zundamon", "metan"], False, False),
    ("metan", "新しい書き方に変えたとき、その独自設定だけ、まるごと消えてたのよ。", "server_room", "normal", None, False, False),
    ("zundamon", "あっ……！ だからサービスが止まってたのだ！", "server_room", "surprise", None, False, False),
    # ── 復旧(office) ──
    ("metan", "原因が分かれば早いわ。元に戻すわよ。", "office", "normal", ["zundamon", "metan"], False, False),
    ("zundamon", "了解なのだ！ えいっ。", "office", "normal", None, False, False),
    ("営業", "サービス、復旧しました。", "office", None, None, False, False),
    ("部長", "……助かった。ギリギリ間に合ったな。", "office", None, None, False, False),
    ("zundamon", "ふ、ふええ……間に合ったのだ〜！", "office", "happy", None, False, False),
    # ── 教訓(rooftop) ──
    ("zundamon", "……めたん。やっぱり、AI（エーアイ）が悪かったのだ？", "rooftop", "trouble", ["zundamon", "metan"], False, False),
    ("metan", "ううん、違うわ。", "rooftop", "normal", None, False, False),
    ("metan", "AI（エーアイ）は、相談相手。すごく頼りになる。", "rooftop", "normal", None, False, False),
    ("metan", "でも、最後に責任を持つのは、人間なの。", "rooftop", "normal", None, False, False),
    ("metan", "だから、レビューがあるのよ。", "rooftop", "normal", None, True, False),
    ("zundamon", "……都合のいいとこだけ読んじゃ、ダメなのだ……。", "rooftop", "normal", None, False, False),
    # ── オチ(rooftop) ──
    ("zundamon", "わかったのだ！ もう二度と、AI（エーアイ）は使わないのだ！", "rooftop", "panic", None, False, False),
    ("metan", "いや、使いなさい。", "rooftop", "trouble", None, False, False),
    ("metan", "ちゃんと最後まで読んで、確認してね。", "rooftop", "normal", None, False, False),
    ("zundamon", "……うっ。善処するのだ……。", "rooftop", "happy", None, False, False),
]

script = []
cursor = 0.0
prev_scene = None
for i, (sp, text, scene, expr, enter, emphasis, shake) in enumerate(TURNS, 1):
    if prev_scene is not None and scene != prev_scene:
        cursor += 0.5  # 場面転換ぶんの間
    dur = max(1.3, min(6.0, len(text) * 0.13 + 0.7))
    turn = {
        "id": f"turn-{i:04d}",
        "speaker": sp,
        "text": text,
        "scene": scene,
    }
    if expr:
        turn["expression"] = expr
    if enter:
        turn["enter"] = enter
    if emphasis:
        turn["emphasis"] = True
    if shake:
        turn["shake"] = True
    turn["start"] = round(cursor, 2)
    turn["end"] = round(cursor + dur, 2)
    script.append(turn)
    cursor += dur + 0.35
    prev_scene = scene

data = {
    "title": "AIが大丈夫って言ったのだ",
    "_note": "第1話。仮タイミング。make_story_audio.py で実尺を start/end/sentences に上書きする。モブ(営業/部長/AI)は姿なし=声+吹き出しのみ。",
    "script": script,
}
json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"OK: {OUT} / {len(script)}ターン / 仮尺 {cursor:.1f}s")
print("speakers:", sorted(set(t['speaker'] for t in script)))
print("scenes:", sorted(set(t['scene'] for t in script)))
