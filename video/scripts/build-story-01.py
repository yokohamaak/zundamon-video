#!/usr/bin/env python3
# 第1話「AIが大丈夫って言ったのだ」の台本(script.md)をStoryVideo用JSONに変換する。
# モブ(営業/部長/AI)は姿なし=声+吹き出しのみ。タイミングは仮値(音声化で実尺に上書き)。
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "story-01.json")

# 列: (speaker, text, scene, expression, enter, emphasis, shake, flashback, telop, pause)
# emphasis=True: 話者プッシュイン。shake=True: カメラシェイク。flashback=True: 回想区間。
# telop: 境界で短時間表示するテキスト(None=なし)。pause: 台詞後の無音秒(音声生成で使用)。
# ※ start/end/sentences は make_story_audio.py が実尺で上書きするため、
#   build-story-01.py 再実行は音声再生成を伴う場合のみ行うこと。
TURNS = [
    # ── コールドオープン(office) ──
    ("営業", "サイトに……つながりません。", "office", "panic", ["zundamon", "metan"], False, True, False, None, 0),
    ("営業", "お問い合わせ、300件です。", "office", "panic", None, False, True, False, None, 0),
    ("部長", "セール開始まで、あと20分だぞ！", "office", "panic", None, False, True, False, None, 0),
    ("zundamon", "ど、どどど、どうすればいいのだ〜！？", "office", "panic", None, False, True, False, None, 0),
    ("metan", "……ずんだもん。", "office", "normal", None, False, False, False, None, 0),
    ("metan", "昨日、何した？", "office", "normal", None, True, False, False, None, 0),
    ("zundamon", "昨日……？", "office", "trouble", None, False, False, False, None, 1.0),  # 回想への一拍
    # ── 前日の回想(office) ──
    ("metan", "今日はわたし外出だから。設定の変更作業は、しないでね。", "office", "normal", None, False, False, True, "― 前日 ―", 0),
    ("zundamon", "わかったのだ！ 留守番はまかせるのだ！", "office", "happy", None, False, False, True, None, 1.2),  # 後でめたん退場＋間
    # ── 一人になって気が緩む→通知で気づく（設定変更のきっかけ） ──
    ("zundamon", "さーて……今日はぼく一人で、るすばんなのだ♪", "office", "happy", None, False, False, True, None, 0.8),
    ("zundamon", "……ん？ なんか画面に出てるのだ。", "office", "normal", None, False, False, True, None, 0),
    ("zundamon", "むむ……『設定ファイルが古い』って出てるのだ。", "office", "normal", None, False, False, True, None, 0),
    ("zundamon", "古いなら、新しくした方がいいに決まってるのだ。", "office", "normal", None, False, False, True, None, 0),
    # ── AIに相談(office・回想中) ──
    ("zundamon", "こういう時は、AI（エーアイ）に聞くのだ！", "office", "happy", None, False, False, True, None, 0),
    ("AI", "一般的な構成では、問題ありません。", "office", None, None, False, False, True, None, 0),
    ("AI", "ただし、ご利用の環境によっては、異なる場合があります。", "office", None, None, False, False, True, None, 0),
    ("zundamon", "『問題ありません』！ AI（エーアイ）が大丈夫って言ってるのだ！", "office", "happy", None, False, False, True, None, 0),
    ("zundamon", "ぽちぽち……サービス再起動っと。", "office", "normal", None, False, False, True, None, 0),
    ("zundamon", "ヨシ！ 今日も平和なのだ！", "office", "happy", None, False, False, True, None, 0),
    # ── 帰宅(home・回想末尾) ──
    ("zundamon", "おつかれさまなのだ〜", "home", "happy", ["zundamon"], False, False, True, None, 1.0),  # 現在への一拍
    # ── 現在へ戻る(office・障害対応) ──
    ("metan", "で。昨日、何した？", "office", "normal", ["zundamon", "metan"], True, False, False, "― 現在 ―", 0),
    ("zundamon", "ええと……昨日、設定をひとつ、変えたのだ。", "office", "trouble", None, False, False, False, None, 0),
    ("metan", "それ、誰にレビューしてもらった？", "office", "normal", None, False, False, False, None, 0),
    ("zundamon", "AI（エーアイ）なのだ！", "office", "happy", None, False, False, False, None, 0),
    ("zundamon", "な、なんで黙るのだ〜！？", "office", "panic", None, False, False, False, None, 0),
    # ── 調査開始(office) ──
    ("metan", "とにかくログを見るわよ。落ち着いて。", "office", "normal", None, False, False, False, None, 0),
    ("zundamon", "書き方は、ちゃんと新しくなってるのだ……。なんで動かないのだ？", "office", "trouble", None, False, False, False, None, 0),
    ("metan", "……ねえ。AI（エーアイ）は、なんて言ってた？", "office", "normal", None, False, False, False, None, 0),
    ("zundamon", "『一般的な構成では問題ありません』なのだ！", "office", "normal", None, False, False, False, None, 0),
    ("metan", "……その続きは？ 最後まで読んだ？", "office", "normal", None, False, False, False, None, 0),
    ("zundamon", "……つ、続き？", "office", "panic", None, True, False, False, None, 0),
    ("zundamon", "……『環境によっては、異なる場合があります』……。", "office", "trouble", None, False, False, False, None, 0),
    ("metan", "うちの環境は、“一般的”？", "office", "normal", None, False, False, False, None, 0),
    ("zundamon", "……ちがう、のだ……。", "office", "trouble", None, False, False, False, None, 0),
    # ── 真相(server_room) ──
    ("metan", "うちはね、独自の社内設定が足してあるの。AI（エーアイ）は、それを知らない。", "server_room", "normal", ["zundamon", "metan"], False, False, False, None, 0),
    ("metan", "新しい書き方に変えたとき、その独自設定だけ、まるごと消えてたのよ。", "server_room", "normal", None, False, False, False, None, 0),
    ("zundamon", "あっ……！ だからサービスが止まってたのだ！", "server_room", "surprise", None, False, False, False, None, 0),
    # ── 復旧(office) ──
    ("metan", "原因が分かれば早いわ。元に戻すわよ。", "office", "normal", ["zundamon", "metan"], False, False, False, None, 0),
    ("zundamon", "了解なのだ！ えいっ。", "office", "normal", None, False, False, False, None, 0),
    ("営業", "サービス、復旧しました。", "office", None, None, False, False, False, None, 0),
    ("部長", "……助かった。ギリギリ間に合ったな。", "office", None, None, False, False, False, None, 0),
    ("zundamon", "ふ、ふええ……間に合ったのだ〜！", "office", "happy", None, False, False, False, None, 0),
    # ── 教訓(rooftop) ──
    ("zundamon", "……めたん。やっぱり、AI（エーアイ）が悪かったのだ？", "rooftop", "trouble", ["zundamon", "metan"], False, False, False, None, 0),
    ("metan", "ううん、違うわ。", "rooftop", "normal", None, False, False, False, None, 0),
    ("metan", "AI（エーアイ）は、相談相手。すごく頼りになる。", "rooftop", "normal", None, False, False, False, None, 0),
    ("metan", "でも、最後に責任を持つのは、人間なの。", "rooftop", "normal", None, False, False, False, None, 0),
    ("metan", "だから、レビューがあるのよ。", "rooftop", "normal", None, True, False, False, None, 0),
    ("zundamon", "……都合のいいとこだけ読んじゃ、ダメなのだ……。", "rooftop", "normal", None, False, False, False, None, 0),
    # ── オチ(rooftop) ──
    ("zundamon", "わかったのだ！ もう二度と、AI（エーアイ）は使わないのだ！", "rooftop", "panic", None, False, False, False, None, 0),
    ("metan", "いや、使いなさい。", "rooftop", "trouble", None, False, False, False, None, 0),
    ("metan", "ちゃんと最後まで読んで、確認してね。", "rooftop", "normal", None, False, False, False, None, 0),
    ("zundamon", "……うっ。善処するのだ……。", "rooftop", "happy", None, False, False, False, None, 0),
]

# 退場：turn番号(1始まり) → そのターンの end で退場するキャラ。
EXIT_AT = {9: ["metan"]}  # 「留守番はまかせるのだ」の後、めたんが右へ退場
EXIT_DIR = {9: "right"}   # 退場方向

# PC画面インサート：turn番号(1始まり) → 全画面PC UI の内容。
_WARN = {"kind": "warning", "title": "システム警告", "text": "設定ファイルが古い形式です"}
_CHAT = {
    "kind": "chat",
    "user": "この設定を新しい書き方に変えても大丈夫？",
    "ai": ["一般的な構成では、問題ありません。", "ただし、ご利用の環境によっては、異なる場合があります。"],
}
_OK = {"kind": "ok", "text": "正常"}
INSERT_AT = {
    11: _WARN,                       # 「ん？ なんか画面に出てるのだ。」
    12: _WARN,                       # 「設定ファイルが古い」
    15: _CHAT,                       # AI「一般的な構成では問題ありません」
    16: {**_CHAT, "highlight": 1},   # AI「ただし…」を強調
    19: _OK,                         # 「ヨシ！ 今日も平和なのだ！」
}

script = []
cursor = 0.0
prev_scene = None
for i, row in enumerate(TURNS, 1):
    sp, text, scene, expr, enter, emphasis, shake, flashback, telop, pause = row
    if prev_scene is not None and scene != prev_scene:
        cursor += 0.5  # 場面転換ぶんの間
    dur = max(1.3, min(6.0, len(text) * 0.13 + 0.7))
    turn = {"id": f"turn-{i:04d}", "speaker": sp, "text": text, "scene": scene}
    if expr:
        turn["expression"] = expr
    if enter:
        turn["enter"] = enter
    if emphasis:
        turn["emphasis"] = True
    if shake:
        turn["shake"] = True
    if flashback:
        turn["flashback"] = True
    if telop:
        turn["telop"] = telop
    if pause:
        turn["pause"] = pause
    if i in EXIT_AT:
        turn["exit"] = EXIT_AT[i]
    if i in EXIT_DIR:
        turn["exitDir"] = EXIT_DIR[i]
    if i in INSERT_AT:
        turn["insert"] = INSERT_AT[i]
    turn["start"] = round(cursor, 2)
    turn["end"] = round(cursor + dur, 2)
    script.append(turn)
    cursor += dur + 0.35 + (pause or 0)
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
