"""
Gemini 深掘りストーリー台本生成モジュール

1つの主題（例「なぜ横浜は港町になったのか」）を、中心の問い「なぜそうなったのか」から
順を追って解き明かす掛け合い台本（intro＝問題提起 ＋ 本編ビート ＋ outro＝教訓）を生成する。
分野は問わない（歴史・地理・地域・社会・文化・自然・科学・技術・ビジネスなど何でも）。
役割: ずんだもん=聞き手/初心者 / 四国めたん=語り役・解説役（configで変更可）。
出力は tts_voicevox・動画が使う script 形式 [{"speaker","text",...}] ＋ 章メタ chapters。

設計: build_prompt / parse_script_json / normalize_turns / assign_sections_to_turns は
純関数でテスト可能。google.genai（新SDK）は generate_story_script 内で遅延importする
（テストに依存を持ち込まない）。

config（例）:
    story:
      theme: "なぜ横浜は港町になったのか"  # 空ならGeminiに主題選定させる（分野不問）
      topics: 4                            # 本編ビート（章）の目安数
      questioner: ずんだもん               # 聞き手
      explainer: 四国めたん                # 語り役・解説役
      target_minutes: 7
    models:
      text: gemini-3.5-flash
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONER = "ずんだもん"
DEFAULT_EXPLAINER = "四国めたん"
DEFAULT_TOPICS = 6  # 1本の物語の本編ビート（章）の目安数
DEFAULT_MINUTES = 11

# 読み上げ速度の実測換算（VOICEVOX・現行の話者speed設定下で約335字/分）。
CHARS_PER_MINUTE = 390  # 実測較正（VOICEVOX掛け合いの実効レート≒389字/分）。分→文字数予算の換算に使用

# 動画(video/src/types.ts)が解釈する感情enum。これ以外の値は normal に倒す。
VALID_EMOTIONS = {"normal", "surprise", "happy", "sad", "angry"}
DEFAULT_EMOTION = "normal"

# セクション種別。chapters[].section と script[].section に使う。不正値は trivia に倒す。
# intro=導入(問題提起) / trivia=本編ビート(物語の段階) / outro=締め(教訓)。
# ※キー "trivia" は内部識別子として流用（描画/正規化/履歴の互換維持のため改名しない）。
VALID_SECTIONS = {"intro", "trivia", "outro"}
DEFAULT_SECTION = "trivia"

# 事実の確度（収益化/誤情報対策の編集メタ。動画には出ない）。
# high=公式/一次資料 / medium=広く語られるが要確認 / low=諸説・逸話レベル。
VALID_CONFIDENCE = {"high", "medium", "low"}

# 演出effect enum（video/src/types.ts と一致）。不正値は kenburns に倒す。
# kenburns=標準のゆっくりズーム/パン / zoom_punch=寄る / shake=揺れ
# / flash=白フラッシュ転換（章境界向き）/ glow_pulse=発光脈動。
VALID_EFFECTS = {"kenburns", "zoom_punch", "shake", "flash", "glow_pulse"}
DEFAULT_EFFECT = "kenburns"

# 画像の取得先振り分け（image_fetch がPhase2で参照）。
# subject=実在の人物/製品/歴史的瞬間（Wikimedia向き）/ ambient=抽象・雰囲気（Pexels/Pixabay向き）。
VALID_IMAGE_KINDS = {"subject", "ambient"}
DEFAULT_IMAGE_KIND = "ambient"


def _rules_block(questioner: str, explainer: str, topics: int, regen: bool = False) -> str:
    """口調・各発言フィールド・章メタ・読み上げの共通ルール（build_prompt と再生成で共用）。

    regen=True のときは章メタ説明を「trivia章のみ（intro/outro無し）」に切り替える。
    """
    structure = (f"今回出すのは本編ビート(section=trivia) {topics}個前後のみ（intro/outro は作らない）。"
                 if regen else
                 f"章の構成 = intro(導入・問題提起) 1つ ＋ 本編ビート(section=trivia) {topics}個前後 ＋ outro(教訓・締め) 1つ。")
    return f"""## 登場人物と口調（語尾を混同しないこと・最重要）
- {questioner}（聞き手・ボケ役）: 好奇心旺盛。物語の各段階で素朴な疑問や「なんで？」を投げ、意外な事実に驚く。視聴者の代弁者。
  一人称「ぼく」、語尾は「〜なのだ」「〜のだ？」。リアクションは毎回同じにせず変化をつける
  （驚き・感心・脱線・ツッコミ・共感など）。
- {explainer}（語り役・解説役）: 落ち着いた大人の女性。1つの物語を順を追って語り、「なぜそうなったか」を解き明かす。
  一人称「わたし」、語尾は「〜よ」「〜わ」「〜なのよ」「〜ね」「〜だわ」など。
  **1つの説明は2〜4文**で、物語を前へ進める要点に絞る。長すぎる講義にしない。
- **【厳守】「〜のだ」「〜なのだ」「〜のだよ」は {questioner} 専用。{explainer} には絶対に使わせない。**
  逆に {explainer} の女性的語尾を {questioner} に使わせない。書く前に必ず誰の口調か確認すること。

## 各発言に必ず付けるフィールド
1. "chapter": その発言が属する章の番号（0始まりの整数）。章＝「導入(1つ)＋本編ビート(1つずつ)＋締め(1つ)」。
   発言は章順に並べ、章番号は飛ばさない。
2. "section": その章の種別。{sorted(VALID_SECTIONS)} のいずれか。intro=導入 / trivia=本編ビート / outro=締め。
   同じ章の発言は同じsectionにする。
3. "emotion": normal（基本）/ surprise（驚き・意外）/ happy（嬉しい・わくわく）/ sad / angry。
   {questioner} は驚き役なので surprise/happy が出やすい。迷ったら normal。
4. "effect": kenburns（基本）/ zoom_punch（核心や意外な事実を明かす瞬間に効く）/ shake / flash（章＝ビートの切替）/ glow_pulse。
   **基本は kenburns。ビートが切り替わる最初の発言に flash、核心を明かす所に zoom_punch** を使うと締まる。多用しない。
5. "cut": その発言の間に画面に映す画像が、その章の image_cuts の**何番目か（0始まりの整数）**。
   章の最初の発言は 0。話が進んで別の被写体に移る発言で 1, 2… と増やす（**戻さない・飛ばさない**）。
   画像の切替が**話の流れ（被写体が変わる所）に合う**ようにする。image_cuts の個数と対応させること。
6. "voice"（任意・声の演技）: その台詞だけ声を変える。{{"speed":速さ,"pitch":高さ,"intonation":抑揚,"volume":音量}}。
   既定は全部1.0（pitchは0.0）。範囲 speed/intonation/volume=0.5〜2.0、pitch=-0.15〜0.15。**多用しない**。
   例: 驚き=intonation 1.4・volume 1.2 / 焦り=speed 1.3 / しみじみ=speed 0.9・intonation 0.8。
7. "pause"（任意・間）: その台詞の**後に置く無音秒**（0〜2）。核心を明かす前のタメに少しだけ。**多用しない**。
8. 画像演出の合図（任意・「## 画像エリアの演出」で章に演出を置いた時だけ使う）:
   - "panel_event": "shrink" … この発言で画像を縮小しテキスト領域を開く（panel用・要点説明に入る発言に1つ）。
   - "panel_item": n（整数0始まり）… この発言で panel.items の n 番目を出す（0,1,2…順）。
   - "reveal": true … この発言で答え/数字を出す（quiz の答え・stat の数字の出現タイミング。各章1つ）。
   - "callout_item": n（整数0始まり）… この発言で callouts[n] を出す（0,1,2…順）。

## 章メタ（chapters・各章に1つ）
{structure}各章に次を出す（chapter番号の昇順）:
- "section": intro / trivia / outro のいずれか。
- "title": 画面に出す短い日本語の見出し（そのビートの核を10〜18文字で。例「世界シェア7割の絶頂」「見落とした転換点」）。
- "summary": そのビートの要点を1〜2文の日本語で（編集時の概要表示用。動画には出さない）。
  例「最盛期は携帯電話の世界シェア4割超で圧倒的だったが、スマホへの転換に乗り遅れた。」
- "confidence": （本編ビートのみ）"high"（公式発表・一次資料がある）/ "medium"（広く語られるが要確認）/ "low"（諸説・逸話レベル）。「## 事実の確度」の基準に従う。
- "source_hint": （本編ビートのみ）裏取りの手がかり（公式発表・関係者の発言・出典になりそうな年や媒体名など）。
- 画像演出（本編ビートのみ・任意・**1章につき多くても1種類**）。詳細と使いどころは「## 画像エリアの演出」を参照：
  - "panel": {{"heading": "お題(任意)", "items": [{{"text": "短い要点", "arrow_from_prev": true}}, ...]}}（縮小画像＋段階テキスト。arrow無し=並列✔／有り=流れ▼）
  - "quiz": {{"question": "問い", "answer": "答え"}}（？で溜めて答えを出す・核心の直前で1回）
  - "compare": {{"left": {{"label": "A", "cut": 0}}, "right": {{"label": "B", "cut": 1}}}}（2分割で対比）
  - "stat": {{"value": "70", "unit": "％", "label": "世界シェア"}}（大きな数字を重ねる）
  - "callouts": [{{"text": "ラベル", "x": 0.3, "y": 0.5, "arrow": true}}, ...]（画像の位置を指す注釈・最大4個）
- "image_cuts": その章で**順に映す画像を 2〜4個**。話の対象物が変わるよう別々の被写体にする。
  各要素に:
  - "image_kind": "subject"（実在の人物・製品・ロゴ・記号など特定物。例 "Bluetooth logo", "Larry Tesler"）
    / "ambient"（抽象・雰囲気。例 "wifi router", "old typewriter"）。
  - **略語・規格・技術用語そのもの（例 PNG, GIF, CAPTCHA, HTTP, Bluetooth）が題材のネタは
    必ず "subject" にし、image_query はそのロゴ/ワードマーク名（例 "PNG logo", "CAPTCHA"）にする。**
    抽象的なambient（"artificial intelligence", "robot" 等）にすると題材と無関係なストック画像になり逆効果。
    適切なロゴが無ければ取得失敗→プレースホルダで構わない（無関係画像より良い）。
  - "image_query": 英語の検索語。**subject は固有名詞のみ**（説明を足さない）。**ambient は情景キーワード**でよい。
  - "image_query_ja": image_query の**日本語訳**（人が確認するためのラベル。例 image_query="vintage radio"→image_query_ja="昔のラジオ"）。

## 読み上げ（VOICEVOX）の注意
- **セリフ(text)中の英字を含む語には必ず直後に（カタカナ読み）を付ける**（例「Hi-Fi（ハイファイ）」「API（エーピーアイ）」）。
  付けないとVOICEVOXが英字を1文字ずつ不自然に読む。読みはカタカナで（漢字の訳語ではなく音の読み）。
- **既にカタカナ・ひらがなで書かれた語には読みを付けない**（カタカナはそのまま正しく読まれる）。
  特に**カタカナの固有名詞に同じカタカナの読みを重ねない**（×「ロバート・メトカーフ（ロバート・メトカーフ）」→○「ロバート・メトカーフ」）。冗長で字幕も音声も二重になる。"""


def _output_block(explainer: str, questioner: str) -> str:
    """出力JSON形式の指定（build_prompt と再生成で共用）。"""
    return f"""## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること。
**厳密に有効なJSONにすること**:
- 文字列の中でダブルクオート(")を使わない。セリフの強調・引用は必ず「」や『』を使う。
- 末尾カンマを付けない。各要素の区切りカンマを忘れない。
- 文字列内に生の改行を入れない（1つのtextは1行）。
- バックスラッシュや制御文字を入れない。
{{
  "theme": "動画の主題（日本語・なぜ〜のか型・meta.titleに使う。例「なぜ横浜は港町になったのか」）",
  "chapters": [
    {{"section": "intro", "title": "なぜ横浜は港町に", "summary": "今や日本有数の港町・横浜が、なぜ港として発展したのかを問う導入。", "image_cuts": [
      {{"image_query": "Yokohama port", "image_kind": "subject", "image_query_ja": "横浜港"}}
    ]}},
    {{"section": "trivia", "title": "開港前は小さな村", "summary": "開港前の横浜は戸数100戸ほどの半農半漁の小さな村だった。", "confidence": "high", "source_hint": "横浜開港資料・幕末の横浜村の記録", "image_cuts": [
      {{"image_query": "Edo period fishing village", "image_kind": "ambient", "image_query_ja": "江戸時代の漁村"}},
      {{"image_query": "Tokaido Kanagawa-juku ukiyoe", "image_kind": "subject", "image_query_ja": "東海道・神奈川宿の浮世絵"}}
    ]}},
    {{"section": "trivia", "title": "1859年の開港", "summary": "1859年、日米修好通商条約で開かれた港の一つに横浜が選ばれた。", "confidence": "high", "source_hint": "1858年日米修好通商条約・1859年横浜開港", "image_cuts": [
      {{"image_query": "Yokohama 1859 foreign settlement", "image_kind": "ambient", "image_query_ja": "開港期の横浜居留地"}}
    ]}},
    {{"section": "trivia", "title": "江戸に近い良港", "summary": "江戸に近く水深のある良港で、生糸輸出の拠点となり急成長したのが核心。", "confidence": "medium", "source_hint": "幕末の生糸輸出・横浜の港湾条件", "image_cuts": [
      {{"image_query": "silk trade Yokohama Meiji", "image_kind": "ambient", "image_query_ja": "横浜の生糸貿易"}}
    ]}},
    {{"section": "outro", "title": "まとめ", "summary": "小さな村が立地と時代の必要から国際港都へ育ったという話。", "image_cuts": [
      {{"image_query": "Yokohama Red Brick Warehouse", "image_kind": "subject", "image_query_ja": "横浜赤レンガ倉庫"}}
    ]}}
  ],
  "script": [
    {{"speaker": "{explainer}", "text": "今や日本を代表する港町の横浜。でも昔は小さな村だったって知ってる？", "emotion": "normal", "section": "intro", "chapter": 0, "effect": "kenburns", "cut": 0}},
    {{"speaker": "{questioner}", "text": "ええっ、あの大きな港が村だったなんて信じられないのだ！", "emotion": "surprise", "section": "intro", "chapter": 0, "effect": "kenburns", "cut": 0}},
    {{"speaker": "{explainer}", "text": "今日は『なぜ横浜は港町になったのか』を順に解き明かすわ。", "emotion": "happy", "section": "intro", "chapter": 0, "effect": "kenburns", "cut": 0}},
    {{"speaker": "{explainer}", "text": "開港前の横浜は、戸数100戸ほどの半農半漁の小さな村だったの。", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "flash", "cut": 0}},
    {{"speaker": "{questioner}", "text": "そんな小さな村が、どうして港になったのだ？", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "kenburns", "cut": 1}},
    {{"speaker": "{explainer}", "text": "ところが1859年、外国に開く港の一つに横浜が選ばれたの。", "emotion": "normal", "section": "trivia", "chapter": 2, "effect": "flash", "cut": 0}},
    {{"speaker": "{questioner}", "text": "なんで、わざわざその小さな村が選ばれたのだ？", "emotion": "surprise", "section": "trivia", "chapter": 2, "effect": "zoom_punch", "cut": 0}}
  ]
}}"""


def _avoid_block(also_avoid) -> str:
    """過去に出した/却下したネタの重複禁止セクション（指定時のみ）。build_prompt と再生成で共用。"""
    facts = [f for f in (also_avoid or []) if (f.get("title") or "").strip()]
    if not facts:
        return ""
    lines = "\n".join(f"- {f.get('title', '')}：{f.get('summary', '')}".rstrip("：") for f in facts)
    return ("\n## 既出の主題（重複禁止・過去動画分も含む）\n"
            "過去にこのジャンルで既に扱った/却下した主題は次の通り。**これらと対象も結論も重複しない**主題にすること。\n"
            f"{lines}\n")


def select_theme(config: dict, used_themes=None) -> str:
    """この動画で使う小テーマ文字列を決める（純関数）。

    優先順位:
      1. story.theme が非空 → それを固定で使う（従来動作）。
      2. story.theme_pool があれば、used_themes に無いものを先頭から1つ選ぶ
         （全て使用済みなら、最後に使った時期が最も古いものを選んで巡回）。
      3. どちらも無ければ "" を返す（呼び出し側でGeminiにテーマ自動選定させる）。
    used_themes=過去に使ったテーマ文字列の時系列リスト（古い→新しい）。
    """
    s = config.get("story", {})
    fixed = (s.get("theme") or "").strip()
    if fixed:
        return fixed
    pool = [str(t).strip() for t in (s.get("theme_pool") or []) if str(t).strip()]
    if not pool:
        return ""
    used = [u for u in (used_themes or []) if u]
    unused = [t for t in pool if t not in used]
    if unused:
        return unused[0]
    # 全て使用済み → プール各要素の「最後に使われた位置」が最も小さい＝最も昔のものを選ぶ。
    return min(pool, key=lambda t: max((i for i, u in enumerate(used) if u == t), default=-1))


def build_prompt(config: dict, also_avoid=None) -> str:
    """configから日本語の「実は〇〇雑学」掛け合い台本生成プロンプトを作る（純関数）。

    also_avoid=過去に出した/却下したネタ[{title,summary}]。指定すると重複禁止セクションを足す。
    """
    s = config.get("story", {})
    questioner = s.get("questioner", DEFAULT_QUESTIONER)
    explainer = s.get("explainer", DEFAULT_EXPLAINER)
    topics = int(s.get("topics", s.get("chapters", DEFAULT_TOPICS)))
    minutes = float(s.get("target_minutes", DEFAULT_MINUTES))
    total_chars = int(minutes * CHARS_PER_MINUTE)        # 狙う尺の総文字数（上限）
    min_chars = int(total_chars * 0.85)                  # 下限（これを下回ると薄い動画＝尺不足）
    # 本編ビート数の許容レンジ（主題に応じ可変・短すぎ長すぎを防ぐ目安）。
    beat_lo = max(3, topics - 1)
    beat_hi = topics + 2
    theme = (s.get("theme") or "").strip()
    avoid_block = _avoid_block(also_avoid)

    if theme:
        theme_line = f'今回の主題は「{theme}」。この1つの主題を、物語として深掘りしてください。'
    else:
        theme_line = (
            "今回の主題はあなたが選んでください。分野は問わない（歴史・地理・地域・社会・文化・自然・科学・技術など）。"
            "1つの対象を深掘りできる『なぜ〜のか』型の主題を1つ選ぶ"
            "（例:「なぜ横浜は港町になったのか」「なぜ〇〇は世界一になれたのか」「なぜ〇〇は消えたのか」）。"
        )

    return f"""
あなたは「なぜ〇〇は〇〇なのか」という1つの問いを深掘りする教養系YouTubeの掛け合い台本ライターです。
**分野は問いません**（歴史・地理・地域・社会・文化・自然・科学・技術・ビジネスなど何でもよい）。
1つの主題を {questioner} と {explainer} の掛け合いで順を追って解き明かす、日本語の深掘りストーリー台本を作ってください。
視聴者が「そういうことだったのか」と腑に落ち、誰かに話したくなる動画にします。

## 主題
{theme_line}
中心には必ず**1つの問い**（例「なぜ横浜は港町になったのか」「なぜ〇〇は〇〇なのか」）を置き、動画全体はその答えに向かって進む。
{avoid_block}
## 物語の骨子（1つの主題を順に深掘り）
- 動画全体で**1つの問いに答える**。独立した雑学を並べない。各章は前章から地続きの**物語の段階**にする。
- 次の骨格を基本に、主題に応じて **{topics} 個前後（{beat_lo}〜{beat_hi}）** の本編ビート（章）で構成する（型は目安・主題に合わせて自然に増減してよい）。**狙う尺が長いほどビートを増やし、各段階を具体例で厚く**する:
  1. **背景/前提** … その対象がどんな存在だったか（全盛期・前提・状況）。後の展開がなぜ意外なのかの土台を作る。
  2. **転換点** … 風向きが変わった瞬間・最初の兆候・きっかけ。
  3. **核心（なぜ）** … 中心の問いの答え。本質的な理由を解き明かす（ここが山場）。複数要因があれば2章に分けてよい。
  4. **結末/影響** … その後どうなったか・規模・余波。
- 各ビートは**前のビートから滑らかに繋ぐ**。繋ぎの言葉は毎回変える（「ところが」「そんな中で」「ここで問題が」「実はその裏で」等を使い分け、同型反復を避ける）。
- **中心の問い（なぜ）は核心ビートまで取っておく**。それまでは伏線・違和感・緊張を積み、視聴者に「なんで？」を持たせ続ける。途中で結論を割らない。
- **冒頭フックを強くする**：挨拶で始めない。**物語の一番意外な一点（結末や核心の片鱗）を先に見せて**「なぜそうなったと思う？」と問いを立てる→主題を提示→すぐ背景へ。前置きは短く、最初の数秒で掴む。
- 展開は時系列/因果でつなぎ、核心で問いを回収して気持ちよく落とす。
- {explainer}が物語を語り進め、{questioner}が素朴な疑問・驚き・「なんで？」で視聴者を代弁する。

## 事実の確度（収益化に直結・厳守）
史実・経緯を扱うため、誤りは信頼を一発で壊す。各主張の確からしさを明示し、運営者が公開前に裏取りできるようにする。
- 各本編章のメタに "confidence" と "source_hint" を付ける（書式は「## 章メタ」参照）。
- **"low"（諸説・逸話レベル）の主張を物語の核心に据えない。** 核心は一次資料・公式発表・広く確認された事実で支える。
- **"medium" の箇所は、セリフでも『〜と言われているわ』『諸説あるけれど』と断定を避ける。**
- 年号・前後関係・固有名詞・金額・シェアは正確に。曖昧なら盛らず断定を避け、confidence を下げる。

## 画像エリアの演出（画面に変化を付ける・推奨）
画像をただ映すだけの章が続くと退屈。**物語の各段階で、内容に合う演出を積極的に使う**。
- **演出はセリフの内容を視覚化するもの**。1つの章に複数の演出を重ねない（章につき多くても1種類）。合う型が無い章は画像のみでよい。
- **quiz は1本に1回まで**（核心の直前で「なぜだと思う？」と視聴者に問う使い方が効く）。
- 型と物語での使いどころ：
  1. **stat（数字を大きく）**：規模・売上・シェア・人数・倍率など、インパクトのある数字で凄さや落差を見せる段階。
     章メタに "stat": {{"value": "70", "unit": "％", "label": "世界シェア"}}。value が整数だけならカウントアップ表示。発言側: その数字を言う発言に "reveal": true。
  2. **compare（2分割で対比）**：最盛期↔凋落・before/after・自社↔競合など、対比で効く段階。
     章メタに "compare": {{"left": {{"label": "ラベルA", "cut": 0}}, "right": {{"label": "ラベルB", "cut": 1}}}}（**この章は image_cuts を2個以上**）。
     左右は**別々の発言に分け**、左に触れる発言に "compare_item": 0、右に触れる発言に "compare_item": 1。最初から両方見せるなら付けない。
  3. **panel（縮小＋段階テキスト）**：要因が複数あるビートや、段階を踏む経緯を順に積む段階。
     章メタに "panel": {{"heading": "お題(任意)", "items": [{{"text": "短い要点"}}, {{"text": "次の要点", "arrow_from_prev": true}}]}}。
     text は**体言止め10字以内**。因果/時系列でつながるなら2個目以降に "arrow_from_prev": true（▼）、並列の列挙なら付けない（✔）。
     画像はセリフ毎に切り替わる（cut追従）＝panelに画像は持たせない。発言側: 縮小し始める発言に "panel_event": "shrink"、各要点に触れる発言に "panel_item": 0,1,2…。
  4. **callouts（画像の位置を指す注釈）**：1枚の図・写真の複数箇所を順に指す段階。
     章メタに "callouts": [{{"text": "ラベル", "x": 0.3, "y": 0.5, "arrow": true}}, ...]（x,yは0..1・最大4個）。発言側: 各注釈に触れる発言に "callout_item": 0,1,2…。
- セリフは普通に書く（演出はセリフの内容を視覚化するだけ）。

{_rules_block(questioner, explainer, topics)}

## 構成・分量【厳守＝狙った尺に収める。短すぎ厳禁・超過も厳禁】
- **台本全体（script の text 合計）を {min_chars}〜{total_chars}字（読み上げ約{minutes:.0f}分）にする。**
  **{min_chars}字を下回らないこと**＝内容を端折って薄い動画にしない。同時に{total_chars}字は超えない。
- 尺が足りないときは、各ビートに**具体例・背景・数字・関係者の動き・エピソード**を足して深掘りする
  （同じ話の言い換えや無意味な脱線で水増ししない。あくまで中身を厚くして問いの解像度を上げる）。
- **1本編ビート（章）は3〜5往復程度**。物語を前へ進めつつ、要点を具体で支える。
- **intro は最大3往復、outro は最大2往復以内**。
- **outro は物語の教訓・余韻だけ**にする。**「高評価」「チャンネル登録」「また見てね/また次回/さようなら」等の
  定型CTA・別れの挨拶は書かない**（これらは固定で自動付与するので、書くと重複する）。
- **1ターンは最大2文・80字程度まで**。説明は要点だけに絞る。
- 専門用語は{explainer}が一言で噛み砕く。

{_output_block(explainer, questioner)}
""".strip()


def _repair_json(text: str) -> str:
    """Gemini応答JSONの軽微な崩れを修復（純関数）。末尾カンマ除去のみ（安全側）。"""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def parse_script_json(text: str) -> dict:
    """Geminiの応答テキストからJSONを取り出してdictを返す（純関数）。

    - ```json ... ``` のコードフェンスを除去
    - 前後に余分なテキストがあっても最初の '{' から復号
    - chapters/script を正規化（chaptersの構造を真として section を補完）
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    start = text.index("{")
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        # 末尾カンマ等の軽微な崩れを修復して再試行（直らなければ呼び出し側が再生成）。
        data, _ = json.JSONDecoder().raw_decode(_repair_json(text), start)
    if "script" not in data or not isinstance(data["script"], list) or not data["script"]:
        raise ValueError("応答に有効な script がありません")
    for i, turn in enumerate(data["script"]):
        if "speaker" not in turn or "text" not in turn:
            raise ValueError(f"script[{i}] に speaker/text がありません: {turn}")
    # 章メタを正規化（section/image_kind enum化・trim）。
    data["chapters"] = _clean_chapters(data.get("chapters"))
    # ターンを正規化（chaptersがあれば section をchapter由来で上書き＝構造を信頼）。
    normalize_turns(data["script"], data["chapters"])
    return data


def warn_role_voice(script, questioner, explainer):
    """役の語尾混同を検出して警告ログを出す（自動修正はしない＝不自然化を避ける）。

    解説役(explainer)が聞き手(questioner)の「のだ／なのだ」語尾を使っていないか確認する。
    音声合成の前に気づけるようにするための軽いガード。Returns: 混入文数。
    """
    import re

    pat = re.compile(r"(なのだ|のだ|のだよ|のだね)[。．、！？!?）」』]*$")
    hits = []
    for i, turn in enumerate(script):
        if turn.get("speaker") != explainer:
            continue
        for seg in re.split(r"(?<=[。！？!?])", turn.get("text", "")):
            seg = seg.strip()
            if seg and pat.search(seg):
                hits.append((i, seg))
    if hits:
        logger.warning(
            f"{explainer} に {questioner} の語尾(のだ/なのだ)が {len(hits)}文混入。"
            f"例: 「{hits[0][1]}」（必要なら台本を再生成してください）"
        )
    return len(hits)


def _clean_image_cuts(cuts, limit=8):
    """image_cuts を [{image_query, image_kind}] へ正規化（純関数）。

    image_kind はenum固定、image_query は trim。dict以外は除外・最大limit個。
    **query空のcutも残す**（レビューで「画像だけ手動割当・クエリ空」のカットを追加でき、
    これを落とすと script.image_cuts と review.json のカット番号がズレて別画像になるため）。
    """
    if not isinstance(cuts, list):
        return []
    out = []
    for c in cuts:
        if not isinstance(c, dict):
            continue
        q = (c.get("image_query") or "").strip()
        k = c.get("image_kind")
        if k not in VALID_IMAGE_KINDS:
            k = DEFAULT_IMAGE_KIND
        cut = {"image_query": q, "image_kind": k}  # q は空でも可（手動画像スロット）
        ja = (c.get("image_query_ja") or "").strip()
        if ja:  # 人が確認するための日本語ラベル（任意）
            cut["image_query_ja"] = ja
        out.append(cut)
    return out[:limit]


def _clean_opacity(v):
    """背景の不透明度を 0..1 に正規化（純関数）。不正/未指定は None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f < 0:
        f = 0.0
    elif f > 1:
        f = 1.0
    return round(f, 3)


def _clean_size(v, lo=0.3, hi=3.0):
    """演出の大きさ倍率を lo..hi に正規化（純関数）。不正/未指定は None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f < lo:
        f = lo
    elif f > hi:
        f = hi
    return round(f, 3)


def _clean_panel(panel):
    """章の解説パネル定義を正規化（純関数）。不正/空なら None。

    残すのは {image?:str, items:[{text:str, arrow_from_prev?:bool}]} のみ。
    items が無い/空なら None（パネル無し扱い＝後方互換）。
    """
    if not isinstance(panel, dict):
        return None
    raw = panel.get("items")
    if not isinstance(raw, list):
        return None
    items = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        text = strip_markdown((it.get("text") or "").strip())
        if not text:
            continue
        item = {"text": text}
        if it.get("arrow_from_prev"):
            item["arrow_from_prev"] = True
        items.append(item)
    if not items:
        return None
    out = {"items": items[:6]}  # 段階表示は最大6項目に制限（画面が破綻しない範囲）
    # 画像はセリフ(カット)毎に選ぶ＝panel固定の画像/cutは持たない。背景色・見出しのみ保持。
    bg = (panel.get("bg") or "").strip()
    if bg:
        out["bg"] = bg
    op = _clean_opacity(panel.get("bgOpacity"))
    if op is not None:
        out["bgOpacity"] = op
    if panel.get("overlay"):
        out["overlay"] = True
    mtype = panel.get("markerType")
    if mtype in ("check", "square", "dot"):
        out["markerType"] = mtype
    mcolor = (panel.get("markerColor") or "").strip()
    if mcolor:
        out["markerColor"] = mcolor
    msize = _clean_size(panel.get("markerSize"))
    if msize is not None:
        out["markerSize"] = msize
    tcolor = (panel.get("textColor") or "").strip()
    if tcolor:
        out["textColor"] = tcolor
    tsize = _clean_size(panel.get("textSize"))
    if tsize is not None:
        out["textSize"] = tsize
    heading = strip_markdown((panel.get("heading") or "").strip())
    if heading:
        out["heading"] = heading
    pos = (panel.get("pos") or "").strip()
    if pos in ("right", "left", "top", "bottom", "center"):
        out["pos"] = pos
    return out


def _clean_quiz(quiz):
    """章のクイズ定義を正規化（純関数）。question 必須・answer は任意（空＝問題だけ出す）。不正なら None。"""
    if not isinstance(quiz, dict):
        return None
    q = strip_markdown((quiz.get("question") or "").strip())
    a = strip_markdown((quiz.get("answer") or "").strip())
    if not q:  # answer は空でも可＝「問題だけ表示」（リビールなしで問いを出しっぱなし）
        return None
    out = {"question": q, "answer": a}
    img = (quiz.get("image") or "").strip()
    if img:
        out["image"] = img
    bg = (quiz.get("bg") or "").strip()
    if bg:
        out["bg"] = bg
    op = _clean_opacity(quiz.get("bgOpacity"))
    if op is not None:
        out["bgOpacity"] = op
    tc = (quiz.get("textColor") or "").strip()
    if tc:
        out["textColor"] = tc
    abg = (quiz.get("answerBg") or "").strip()
    if abg:
        out["answerBg"] = abg
    aop = _clean_opacity(quiz.get("answerBgOpacity"))
    if aop is not None:
        out["answerBgOpacity"] = aop
    atc = (quiz.get("answerTextColor") or "").strip()
    if atc:
        out["answerTextColor"] = atc
    bw = quiz.get("boxWidth")
    if isinstance(bw, (int, float)) and not isinstance(bw, bool) and 0.2 <= float(bw) <= 1.0:
        out["boxWidth"] = round(float(bw), 3)
    return out


def _clean_compare(compare):
    """章の比較(2分割)定義を正規化（純関数）。left/right に label 必須・不正なら None。

    各サイドは {label:str, cut?:int}（cut=参照する image_cuts 番号・build で画像へ解決）。
    """
    if not isinstance(compare, dict):
        return None

    def side(s, default_cut):
        if not isinstance(s, dict):
            return None
        label = strip_markdown((s.get("label") or "").strip())
        if not label:
            return None
        out = {"label": label}
        cut = s.get("cut")
        if isinstance(cut, int) and not isinstance(cut, bool) and cut >= 0:
            out["cut"] = cut
        else:
            out["cut"] = default_cut
        return out

    left = side(compare.get("left"), 0)
    right = side(compare.get("right"), 1)
    if not left or not right:
        return None
    out = {"left": left, "right": right}
    lc = (compare.get("labelColor") or "").strip()
    if lc:
        out["labelColor"] = lc
    ltc = (compare.get("labelTextColor") or "").strip()
    if ltc:
        out["labelTextColor"] = ltc
    lsz = _clean_size(compare.get("labelSize"))
    if lsz is not None:
        out["labelSize"] = lsz
    dc = (compare.get("dividerColor") or "").strip()
    if dc:
        out["dividerColor"] = dc
    return out


def _clean_stat(stat):
    """章の数字強調定義を正規化（純関数）。value 必須・不正なら None。"""
    if not isinstance(stat, dict):
        return None
    value = strip_markdown((str(stat.get("value")) if stat.get("value") is not None else "").strip())
    if not value:
        return None
    out = {"value": value}
    unit = strip_markdown((stat.get("unit") or "").strip())
    if unit:
        out["unit"] = unit
    label = strip_markdown((stat.get("label") or "").strip())
    if label:
        out["label"] = label
    color = (stat.get("color") or "").strip()
    if color:
        out["color"] = color
    size = _clean_size(stat.get("size"))
    if size is not None:
        out["size"] = size
    bg = (stat.get("bg") or "").strip()
    if bg:
        out["bg"] = bg
    op = _clean_opacity(stat.get("bgOpacity"))
    if op is not None:
        out["bgOpacity"] = op
    spd = stat.get("countSpeed")
    if spd in ("fast", "normal", "slow"):
        out["countSpeed"] = spd
    return out


def _clean_callouts(callouts):
    """章の注釈(吹き出し)定義を正規化（純関数）。text と 0..1 の x,y 必須。空なら None。"""
    if not isinstance(callouts, list):
        return None
    out = []
    for c in callouts:
        if not isinstance(c, dict):
            continue
        text = strip_markdown((c.get("text") or "").strip())
        try:
            x = float(c.get("x"))
            y = float(c.get("y"))
        except (TypeError, ValueError):
            continue
        if not text or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue
        item = {"text": text, "x": round(x, 3), "y": round(y, 3)}
        if c.get("arrow"):
            item["arrow"] = True
        # ラベルの置き場（任意・0..1）。両方そろって範囲内のときだけ保持。
        try:
            lx = float(c.get("lx"))
            ly = float(c.get("ly"))
            if 0.0 <= lx <= 1.0 and 0.0 <= ly <= 1.0:
                item["lx"] = round(lx, 3)
                item["ly"] = round(ly, 3)
        except (TypeError, ValueError):
            pass
        out.append(item)
    return out[:4] or None  # 注釈は最大4個（画面が煩雑にならない範囲）


def _clean_callout_style(style):
    """注釈の見た目（章共通）を正規化（純関数）。色＝CSS文字列 / 大きさ＝倍率。空なら None。"""
    if not isinstance(style, dict):
        return None
    out = {}
    mc = (style.get("markerColor") or "").strip()
    if mc:
        out["markerColor"] = mc
    ms = _clean_size(style.get("markerSize"))
    if ms is not None:
        out["markerSize"] = ms
    lc = (style.get("labelColor") or "").strip()
    if lc:
        out["labelColor"] = lc
    ltc = (style.get("labelTextColor") or "").strip()
    if ltc:
        out["labelTextColor"] = ltc
    lbc = (style.get("labelBorderColor") or "").strip()
    if lbc:
        out["labelBorderColor"] = lbc
    ls = _clean_size(style.get("labelSize"))
    if ls is not None:
        out["labelSize"] = ls
    asz = _clean_size(style.get("arrowSize"))
    if asz is not None:
        out["arrowSize"] = asz
    ashape = style.get("arrowShape")
    if ashape in ("normal", "sharp", "thick", "dot"):
        out["arrowShape"] = ashape
    return out or None


def _clean_viz_list(vl):
    """複数演出（新形式）を正規化（純関数）。各要素は1種類の演出設定。空なら None。

    要素例: {"panel":{...}} / {"quiz":{...}} / {"compare":{...}} /
            {"stat":{...}} / {"callouts":[...], "calloutStyle":{...}}。
    範囲は発言の viz_start/viz_end（出現順ペア）で別途対応づける。
    """
    if not isinstance(vl, list):
        return None
    out = []
    for i, e in enumerate(vl):
        if not isinstance(e, dict):
            continue
        seg = {}
        p = _clean_panel(e.get("panel"))
        if p:
            seg = {"type": "panel", "panel": p}
        elif _clean_quiz(e.get("quiz")):
            seg = {"type": "quiz", "quiz": _clean_quiz(e.get("quiz"))}
        elif _clean_compare(e.get("compare")):
            seg = {"type": "compare", "compare": _clean_compare(e.get("compare"))}
        elif _clean_stat(e.get("stat")):
            seg = {"type": "stat", "stat": _clean_stat(e.get("stat"))}
        elif _clean_callouts(e.get("callouts")):
            seg = {"type": "callouts", "callouts": _clean_callouts(e.get("callouts"))}
            cs = _clean_callout_style(e.get("calloutStyle"))
            if cs:
                seg["calloutStyle"] = cs
        if seg:
            # 所属タグ用のid（発言の vizSeg と対応）。無ければ採番。
            sid = e.get("id")
            seg["id"] = sid if (isinstance(sid, str) and sid.strip()) else "s" + str(i)
            out.append(seg)
    return out or None


def _clean_chapters(chapters, limit=12):
    """chapters を {section,title,image_cuts:[{image_query,image_kind}]} へ正規化（純関数）。

    section はenum固定、title は trim、image_cuts は _clean_image_cuts。
    旧形式（image_query/image_kind 単数）は 1cut へ変換（後方互換）。最低1cut（空query可）を保証。
    dict以外は除外。
    """
    if not isinstance(chapters, list):
        return []
    out = []
    for c in chapters:
        if not isinstance(c, dict):
            continue
        section = c.get("section")
        if section not in VALID_SECTIONS:
            section = DEFAULT_SECTION
        cuts = _clean_image_cuts(c.get("image_cuts"))
        if not cuts:
            # 後方互換: 旧 image_query/image_kind 単数 → 1cut
            q = (c.get("image_query") or "").strip()
            k = c.get("image_kind") if c.get("image_kind") in VALID_IMAGE_KINDS else DEFAULT_IMAGE_KIND
            cuts = [{"image_query": q, "image_kind": k}]
        chapter = {
            "section": section,
            "title": strip_markdown((c.get("title") or "").strip()),
            "summary": strip_markdown((c.get("summary") or "").strip()),
            "image_cuts": cuts,
        }
        # ショート固定見出し用フック（trivia章。任意・無ければ動画側がタイトルから仮生成）。
        hook = strip_markdown((c.get("hook") or "").strip())
        if hook:
            chapter["hook"] = hook
        # 事実の確度・裏取り手がかり（誤情報対策の編集用メタ。trivia章）。
        # 動画には出ない。レビュー画面で公開前の裏取りに使う。任意・あれば保持する。
        conf = (c.get("confidence") or "").strip().lower()
        if conf in VALID_CONFIDENCE:
            chapter["confidence"] = conf
        src_hint = strip_markdown((c.get("source_hint") or "").strip())
        if src_hint:
            chapter["source_hint"] = src_hint
        # 解説パネル（任意・案A）。あれば保持＝この章は縮小画像＋段階テキストで描画。
        panel = _clean_panel(c.get("panel"))
        if panel:
            chapter["panel"] = panel
        # 画像演出（任意・すべて後方互換）。quiz/compare=主モード、stat/callouts=重ね層。
        quiz = _clean_quiz(c.get("quiz"))
        if quiz:
            chapter["quiz"] = quiz
        compare = _clean_compare(c.get("compare"))
        if compare:
            chapter["compare"] = compare
        stat = _clean_stat(c.get("stat"))
        if stat:
            chapter["stat"] = stat
        callouts = _clean_callouts(c.get("callouts"))
        if callouts:
            chapter["callouts"] = callouts
            cstyle = _clean_callout_style(c.get("calloutStyle"))
            if cstyle:
                chapter["calloutStyle"] = cstyle
        # 複数演出（新形式）。あれば保持（範囲はviz_start/viz_endの順ペアで対応・1章複数可）。
        vlist = _clean_viz_list(c.get("vizList"))
        if vlist:
            chapter["vizList"] = vlist
        out.append(chapter)
    return out[:limit]


def strip_markdown(text: str) -> str:
    """台詞/見出しから Markdown 記法を除去（純関数）。

    Geminiが **強調** 等を混ぜると字幕に「**文字**」と出て音声でも読まれるため落とす。
    強調(**/__/*/_)・取り消し線(~~)・コード(`)・リンク[t](u)・見出し(#)・箇条書き(-/*)を除去。
    日本語台詞で *,_,`,# が正規に使われることはほぼ無い前提。
    """
    if not text:
        return text
    t = text
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", t)        # ***太字斜体***
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)            # **太字**
    t = re.sub(r"__(.+?)__", r"\1", t)                # __太字__
    t = re.sub(r"~~(.+?)~~", r"\1", t)                # ~~取り消し~~
    t = re.sub(r"`(.+?)`", r"\1", t)                  # `コード`
    t = re.sub(r"\*(.+?)\*", r"\1", t)                # *斜体*
    t = re.sub(r"(?<![\wぁ-んァ-ヶ一-龠])_(.+?)_(?![\wぁ-んァ-ヶ一-龠])", r"\1", t)  # _斜体_
    t = re.sub(r"\[(.+?)\]\([^)]*\)", r"\1", t)       # [表示](URL)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)   # 見出し #
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)        # 箇条書き
    t = t.replace("**", "").replace("`", "")              # 残った孤立マーカー
    return t.strip()


# カタカナ語（中黒・長音含む）の直後に続く（カタカナ読み）。読み仮名グロスは英字/漢字向けで、
# 既にカタカナの語に付くと冗長＝字幕も音声も二重になる（例「ロバート・メトカーフ（ロバート・メトカーフ）」）。
_REDUNDANT_KANA_GLOSS_RE = re.compile(r"([ァ-ヶー・]{2,})（([ァ-ヶー・]{2,})）")


def strip_redundant_kana_gloss(text: str) -> str:
    """カタカナ語の直後の冗長な（同じカタカナ読み）を除去（純関数）。

    前のカタカナ列と括弧内が一致／一方が他方を含むときだけ落とす（別語の補足説明は残す）。
    例「ロバート・メトカーフ（ロバート・メトカーフ）博士」→「ロバート・メトカーフ博士」。
    英字（かな）グロス（USB（ユーエスビー）等）や、ひらがな読み（ハーラル1世（いちせい））には作用しない。
    """
    if not text:
        return text

    def _repl(m):
        head, inner = m.group(1), m.group(2)
        if inner == head or inner in head or head in inner:
            return head
        return m.group(0)

    return _REDUNDANT_KANA_GLOSS_RE.sub(_repl, text)


# 台詞ごとの声上書きの安全範囲（VOICEVOXのscale値）。範囲外はクランプ。
_VOICE_RANGE = {"speed": (0.5, 2.0), "pitch": (-0.15, 0.15),
                "intonation": (0.0, 2.0), "volume": (0.0, 2.0)}


def _normalize_voice(turn):
    """turn["voice"]（速さ/高さ/抑揚/音量の上書き）を数値化＋範囲クランプ（in-place）。

    dict以外/有効キー無しは削除。各キーは _VOICE_RANGE にクランプ。
    """
    v = turn.get("voice")
    if not isinstance(v, dict):
        turn.pop("voice", None)
        return
    out = {}
    for k, (lo, hi) in _VOICE_RANGE.items():
        if k in v and v[k] is not None:
            try:
                out[k] = round(max(lo, min(hi, float(v[k]))), 3)
            except (TypeError, ValueError):
                pass
    if out:
        turn["voice"] = out
    else:
        turn.pop("voice", None)


def _normalize_pause(turn):
    """turn["pause"]（この台詞の後の無音秒）を 0..2 にクランプ（in-place）。0/不正は削除。"""
    if "pause" not in turn:
        return
    try:
        p = max(0.0, min(2.0, float(turn["pause"])))
    except (TypeError, ValueError):
        turn.pop("pause", None)
        return
    if p > 0:
        turn["pause"] = round(p, 3)
    else:
        turn.pop("pause", None)


def _normalize_cut(turn, chapters, n):
    """turn["cut"] を整数化し、その章の image_cuts 範囲 [0, ncuts-1] にクランプ（in-place）。

    chapter は normalize_turns で先に確定している前提。不正/範囲外で章情報が無ければ削除。
    """
    if "cut" not in turn:
        return
    try:
        c = int(turn["cut"])
    except (TypeError, ValueError):
        turn.pop("cut", None)
        return
    ch = turn.get("chapter")
    ncuts = 0
    if isinstance(ch, int) and 0 <= ch < n:
        ncuts = len(chapters[ch].get("image_cuts") or [])
    if ncuts <= 0:
        turn.pop("cut", None)  # 章のカット数不明＝判定不能なので捨てる
    else:
        turn["cut"] = max(0, min(c, ncuts - 1))


def _normalize_panel_fields(turn):
    """turn の解説パネル操作 panel_event / panel_item を正規化（in-place）。

    panel_event は "shrink" のみ許可（他は削除）。panel_item は整数化（不正は削除）。
    どちらも任意＝無ければ何もしない（後方互換）。
    """
    if turn.get("panel_event") != "shrink":
        turn.pop("panel_event", None)
    for key in ("panel_item", "callout_item", "compare_item"):
        if key in turn:
            v = turn[key]
            if isinstance(v, bool):
                turn.pop(key, None)
            else:
                try:
                    turn[key] = int(v)
                except (TypeError, ValueError):
                    turn.pop(key, None)
    # bool合図（真のときだけ残す）。reveal=答え/数字を出す / viz_start=演出開始 / viz_end=演出終了。
    for flag in ("reveal", "viz_start", "viz_end"):
        if turn.get(flag):
            turn[flag] = True
        else:
            turn.pop(flag, None)
    # 複数演出の所属タグ（新形式）。空でない文字列のみ保持。
    seg = turn.get("vizSeg")
    if not (isinstance(seg, str) and seg.strip()):
        turn.pop("vizSeg", None)
    # キーワードテロップ（任意・重ねがけ小演出）。要所の単語をポップ表示。空は削除。改行可。
    tl = turn.get("telop")
    if isinstance(tl, str):
        tl = strip_markdown(tl).strip()
        if tl:
            turn["telop"] = tl
        else:
            turn.pop("telop", None)
    else:
        turn.pop("telop", None)


def normalize_turns(script: list, chapters: list = None) -> list:
    """各ターンの emotion / effect / section / chapter をenum・整数固定する（破壊的・in-place）。

    - emotion / effect: enum外はデフォルト補完。
    - chapter: 整数化。chapters があれば [0, len-1] にclamp、無ければ 0以上にclamp。
    - section: chapters があれば chapters[chapter].section で上書き（構造を真とする＝proseを信頼しない）。
      chapters が無ければ enum 正規化のみ。
    """
    n = len(chapters) if chapters else 0
    for turn in script:
        # 字幕/音声からMarkdown崩れを除去＋カタカナ語への冗長な同一カタカナ読みを除去。
        turn["text"] = strip_redundant_kana_gloss(strip_markdown(turn.get("text", "")))
        if turn.get("emotion") not in VALID_EMOTIONS:
            turn["emotion"] = DEFAULT_EMOTION
        if turn.get("effect") not in VALID_EFFECTS:
            turn["effect"] = DEFAULT_EFFECT
        try:
            ch = int(turn.get("chapter"))
        except (TypeError, ValueError):
            ch = 0
        ch = max(0, min(ch, n - 1)) if n else max(0, ch)
        turn["chapter"] = ch
        # cut アンカー（その章の何番目の画像か）を整数化＋章のimage_cuts範囲にクランプ。
        # chapter確定後に判定。不正/範囲不明は削除（build側で均等割りフォールバック）。
        _normalize_cut(turn, chapters, n)
        _normalize_voice(turn)  # 声の上書き（任意）をクランプ
        _normalize_pause(turn)  # 台詞後の間（任意）をクランプ
        _normalize_panel_fields(turn)  # 解説パネル操作（任意・shrink/item）を正規化
        if n:
            turn["section"] = chapters[ch].get("section", DEFAULT_SECTION)
        elif turn.get("section") not in VALID_SECTIONS:
            turn["section"] = DEFAULT_SECTION
    return script


def assign_sections_to_turns(script: list) -> list:
    """ターン列を chapter の連続塊（章セグメント）に分ける純関数。

    Returns: [{"chapter": int, "section": str, "turns": [turn_index, ...]}, ...]（出現順）。
    同じchapterが非連続で再登場したら別セグメントになる（連続塊で切る）。
    Phase1の build_chapter_topics がこの区間へ時間割当する。
    """
    segs = []
    for i, t in enumerate(script):
        ch = t.get("chapter", 0)
        if segs and segs[-1]["chapter"] == ch:
            segs[-1]["turns"].append(i)
        else:
            segs.append({"chapter": ch, "section": t.get("section", DEFAULT_SECTION), "turns": [i]})
    return segs


def _is_daily_quota(msg: str, retry_secs=None) -> bool:
    """日次クォータ(RPD)枯渇＝待っても回復しない（同一モデルを再試行せず即フォールバックすべき）か。

    RESOURCE_EXHAUSTED/quota系のうち PerDay/日次 のものだけTrue。分次レート制限(PerMinute)・
    503・一過性エラーは対象外（従来通り待ってリトライ）。判別できないクォータ系は、待機指示が
    長い(>=5分)なら日次相当とみなす。
    """
    m = msg.lower()
    if "resource_exhausted" not in m and "quota" not in m:
        return False  # クォータ系でなければ対象外（待ってリトライ）
    flat = m.replace(" ", "").replace("_", "")
    if "perminute" in flat:
        return False  # 分次レート制限は待てば回復する
    if "perday" in flat or "daily" in m:
        return True   # 日次上限＝待っても無駄→即フォールバック
    return retry_secs is not None and retry_secs >= 300  # 長い待機指示は日次相当


def _generate_with_retry(client, model_name, prompt, max_attempts=5):
    import re
    import time

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=model_name, contents=prompt).text
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", msg, re.IGNORECASE)
            retry_secs = float(match.group(1)) if match else None
            # 日次クォータ(RPD)枯渇は待っても回復しない→再試行せず即送出（呼び出し側が次モデルへ）。
            if _is_daily_quota(msg, retry_secs):
                logger.warning(f"日次クォータ上限のため再試行せず即フォールバック（{model_name}）: {e}")
                raise
            if attempt == max_attempts:
                raise
            if retry_secs is not None:
                wait = max(int(retry_secs) + 10, 65)  # 分次レート制限: 指示秒+余裕
            elif re.search(r"503|UNAVAILABLE|overloaded|high demand", msg, re.IGNORECASE):
                wait = 20  # 一時的な高負荷は短間隔で再試行
            else:
                wait = 65
            logger.warning(f"生成失敗（試行{attempt}/{max_attempts}）、{wait}秒後にリトライ: {e}")
            time.sleep(wait)


def _generate_parsed(config: dict, prompt: str, log_label: str = "台本") -> dict:
    """Geminiで生成→parse_script_jsonを、モデルフォールバック付きで試す共通処理。

    全文生成と章単位の再生成で共用。JSON不正は同一モデルで再試行、API系エラーは次モデルへ。
    Returns: parse_script_json の結果。全滅時は最後の例外を送出。
    """
    from google import genai  # 遅延import（新SDK）

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    models_cfg = config.get("models", {})
    primary = models_cfg.get("text", "gemini-3.5-flash")
    # 503(高負荷)が続くモデルを見切って順に試すフォールバック（いずれも無料枠のStableモデル）。
    # fallback_enabled=false なら primary 1本のみ（品質を固定したい時・失敗を早く知りたい時）。
    fallbacks = models_cfg.get("text_fallbacks", ["gemini-2.5-flash", "gemini-3.1-flash-lite"])
    if models_cfg.get("fallback_enabled", True):
        candidates = [primary] + [m for m in fallbacks if m != primary]
    else:
        candidates = [primary]
        logger.info("フォールバック無効（primaryのみ使用）")

    data = None
    last_err = None
    for model_name in candidates:
        for attempt in range(1, 3):
            logger.info(f"{log_label}を生成（モデル: {model_name}・試行{attempt}/2）")
            try:
                text = _generate_with_retry(client, model_name, prompt, max_attempts=3)
            except Exception as e:  # noqa: BLE001 - API系はこのモデルを諦め次候補へ
                last_err = e
                logger.warning(f"モデル {model_name} で生成できず、次の候補へ: {e}")
                break
            try:
                data = parse_script_json(text)
                break
            except (json.JSONDecodeError, ValueError) as e:  # 不正な応答→同一モデルで再生成
                last_err = e
                logger.warning(f"応答JSONが不正（モデル {model_name}・試行{attempt}/2）、再生成: {e}")
                try:  # デバッグ用に生応答を残す
                    with open("last_bad_script.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError:
                    pass
        if data is not None:
            break
    if data is None:
        raise last_err
    return data


def _regen_output_block(explainer: str, questioner: str, n_targets: int) -> str:
    """再生成専用の出力形式＋例（trivia章のみ・挨拶/締めなし・中立的な繋ぎで完結）。

    通常の _output_block は intro/outro を含む例なので、Geminiがそれをコピーして
    挨拶だけの章を作る事故が起きる。再生成では trivia 完結の実例を見せる。
    """
    return f"""## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること。
**厳密に有効なJSONにすること**:
- 文字列の中でダブルクオート(")を使わない。セリフの強調・引用は必ず「」や『』を使う。
- 末尾カンマを付けない。各要素の区切りカンマを忘れない。文字列内に生の改行を入れない。
- chapters は **trivia を {n_targets} 個だけ**（intro/outro を入れない）。下は2個の例（実際は {n_targets} 個）:
{{{{
  "theme": "（動画のテーマをそのまま・日本語）",
  "chapters": [
    {{{{"section": "trivia", "title": "QRコードのQの意味", "summary": "QRコードのQはQuick（速い）の意味で、高速に読み取れることに由来する。", "confidence": "high", "source_hint": "デンソーウェーブ公式・QR開発の経緯", "image_cuts": [
      {{{{"image_query": "QR code", "image_kind": "subject", "image_query_ja": "QRコード"}}}},
      {{{{"image_query": "smartphone scanning qr code", "image_kind": "ambient", "image_query_ja": "QRを読むスマホ"}}}}
    ]}}}},
    {{{{"section": "trivia", "title": "（2つ目のネタの見出し）", "summary": "（要点を1〜2文）", "confidence": "high", "source_hint": "（裏取りの手がかり）", "image_cuts": [
      {{{{"image_query": "（固有名詞）", "image_kind": "subject", "image_query_ja": "（日本語）"}}}}
    ]}}}}
  ],
  "script": [
    {{{{"speaker": "{explainer}", "text": "ところでね、QRコード（キューアールコード）の『Q』が何の略か知ってる？", "emotion": "normal", "section": "trivia", "chapter": 0, "effect": "flash", "cut": 0}}}},
    {{{{"speaker": "{questioner}", "text": "うーん、Quick（クイック）…早いってことなのだ？", "emotion": "normal", "section": "trivia", "chapter": 0, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{explainer}", "text": "実はその通りで、Quick Response（クイックレスポンス）の略なのよ。一瞬で読み取れる速さが名前の由来なの。", "emotion": "surprise", "section": "trivia", "chapter": 0, "effect": "zoom_punch", "cut": 1}}}},
    {{{{"speaker": "{questioner}", "text": "へぇー！つまりQRコードは『素早く読めるコード』ってことなのだ！", "emotion": "happy", "section": "trivia", "chapter": 0, "effect": "kenburns", "cut": 1}}}},
    {{{{"speaker": "{explainer}", "text": "次はね、（2つ目のネタの問い）…", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "flash", "cut": 0}}}}
  ]
}}}}
※ 例のように各章は「ところでね」「次はね」等の**中立的な繋ぎ**で始め、いきなり問い→実は→驚き→まとめ で完結させる。"""


def build_regen_prompt(config: dict, theme: str, existing_facts: list, n_targets: int) -> str:
    """選択した trivia 章だけを差し替え再生成するプロンプト（純関数）。

    theme は固定。existing_facts（既出の全ネタ）と重複しない新ネタを n_targets 個作らせる。
    出力は通常と同じ {theme, chapters, script} だが chapters は trivia を n_targets 章のみ、
    各発言の chapter は 0..n_targets-1 のローカル連番（呼び出し側で元の章番号へ振り直す）。
    """
    s = config.get("story", {})
    questioner = s.get("questioner", DEFAULT_QUESTIONER)
    explainer = s.get("explainer", DEFAULT_EXPLAINER)
    topics = n_targets
    facts = "\n".join(f"- {f.get('title', '')}：{f.get('summary', '')}".rstrip("：")
                      for f in existing_facts) or "（なし）"
    return f"""
あなたはテクノロジー雑学を扱う教養系YouTubeの掛け合い台本ライターです。
既存動画の一部の「実は」ネタだけを**差し替え再生成**します。

## テーマ（固定・変更しない）
動画のメインテーマは「{theme}」。このテーマに沿った「実は」ネタを作ります。
**ネタはテーマの「切り口」に厳密に沿わせる。** テーマが"名前・由来"系でない限り、「社名・略語・ロゴ・サービス名の由来」ネタに逃げないこと（別テーマ）。「裏側・仕組み・ビジネス・歴史・トラブル」等、テーマの角度の意外な事実にする。

## 最重要：既出ネタと重複させない
この動画には既に以下の「実は」ネタがあります。**これらと題材もオチも重複しない、全く新しい
「実は」ネタを {n_targets} 個**作ってください（同じ対象・同じ結論を避ける。切り口を変えるだけでもダメ）。
### 既出ネタ一覧（重複禁止）
{facts}

## 作るもの（章の構成・厳守）
- **trivia（各ネタ）の章を {n_targets} 個だけ**出す。**intro / outro は出さない。**
- chapters[] は trivia の章を {n_targets} 個。script[] の各発言の "chapter" は **0 から始まる連番**
  （0, 1, …, {n_targets - 1}）にする。"section" は全て "trivia"。
- 各ネタは独立して「へぇ」となる意外な“正確な事実”。軽い→意外性の強い順。

## 各ネタの面白さの型（リズム・構造の骨格。**毎回同じ言い回し・同じ締め方にしない**）
1.（繋ぎ）中立的に入る。**繋ぎ言葉は毎ネタ変える**（「ところでね」を連発しない）　2.{explainer}が問いを投げる（聞き方を変える）　3.{questioner}が素朴に外す
4.{explainer}が意外な真実を明かす（**毎回「実はね」で始めない**・切り出しを変える）　5.{questioner}が驚く（驚き方を変える）　6.{explainer}が追い打ちの小ネタ
7.（締め）自然に締める。**毎回「つまり〇〇は〜なのだ！」の要約型にしない**（要約／素直な感想／軽いツッコミ等で変化を付ける）。唐突に切らない。
- 意外性は嘘や誇張でなく正確な事実で。確実でない逸話は「諸説あるけれど」と限定。年号・前後関係も正確に。

{_rules_block(questioner, explainer, topics, regen=True)}

## 分量【厳守】
- **1ネタ（trivia章）は最大3往復＝6ターン以内**。問い→外し→実はね→驚き→締め を簡潔に。冗長な追い打ち・繰り返しを入れない。
- **1ターンは最大2文・80字程度まで**。長く語りすぎない。

{_regen_output_block(explainer, questioner, n_targets)}

## この再生成での出力（最重要・上の説明より優先）
- 全ての section は "trivia"。intro/outro や、番組全体の**挨拶・導入・締めを絶対に入れない**。
- **この章が動画の何番目に置かれるかは不明**。順番・位置を示す言葉を使わず、どこに挿入されても成立する
  中立的な繋ぎ（「ところでね」「次はね」「こんなのもあるのよ」等）で始める。
- 次のような番組全体向け・順番を示すフレーズは**禁止**：「皆さんこんにちは」「今日は〜の話」「今日のテーマは」
  「最初のネタ」「そして最後は」「今日のラスト」「一つ目／二つ目」「締めに」「準備はいい」「また次の動画で」など
  （**最初・最後・ラスト・〇つ目 のような順序の語は一切使わない**）。
- **各 trivia 章には必ずネタ本体（問い→実は→まとめ）を入れる。挨拶だけ・前置きだけの空の章を作らない。**
  タイトルに掲げた題材の「実は」を必ずその章の台詞で説明すること。
- "chapter" は 0 から {n_targets - 1} までの連番のみ。""".strip()


def regenerate_chapters(config: dict, script_result: dict, target_indices: list,
                        also_avoid: list = None) -> dict:
    """既存台本の指定 trivia 章（target_indices）だけ、既出ネタと重複しない内容で再生成する。

    Gemini呼び出しは1回（選択章をまとめて生成し相互重複も防ぐ）。
    also_avoid=過去のレビューで却下したネタ[{title,summary}]。現存ネタに加えて重複回避に渡す
    （同じ章を振り直しても捨てたネタが復活しないように）。
    Returns: {"chapters": {idx: chapter_meta}, "turns": {idx: [turn,...]}}（元の章番号付き）。
    呼び出し側がこれで script_result を差し替える。
    """
    chapters = script_result.get("chapters", [])
    targets = sorted(i for i in target_indices
                     if 0 <= i < len(chapters) and chapters[i].get("section") == "trivia")
    if not targets:
        raise ValueError("再生成できる trivia 章が選択されていません")
    theme = (script_result.get("theme")
             or config.get("story", {}).get("theme") or "").strip() or "テクノロジー雑学"
    # 既出ネタ＝全 trivia 章のタイトル＋要約（差し替え対象も含めて重複回避の母集合にする）。
    existing = [{"title": c.get("title", ""), "summary": c.get("summary", "")}
                for c in chapters if c.get("section") == "trivia"]
    # 過去に却下したネタもタイトルで重複排除しつつ加える。
    seen = {(f.get("title") or "").strip() for f in existing}
    for f in (also_avoid or []):
        t = (f.get("title") or "").strip()
        if t and t not in seen:
            existing.append({"title": t, "summary": f.get("summary", "")})
            seen.add(t)
    prompt = build_regen_prompt(config, theme, existing, len(targets))
    data = _generate_parsed(config, prompt, log_label=f"{len(targets)}章の差し替え台本")

    chapters_out = data.get("chapters", [])
    new_script = data.get("script", [])
    # Geminiが指示に反して intro/outro を混ぜても、trivia の「実章index」で拾う。
    # （フィルタ後リストの並び順で拾うと、intro等で番号がずれ intro/outro のターンを取り込む）
    trivia_idx = [i for i, c in enumerate(chapters_out) if c.get("section") == "trivia"]
    if len(trivia_idx) < len(targets):
        raise ValueError(f"再生成結果のネタ章が不足（要求{len(targets)}・取得{len(trivia_idx)}）")
    out_chapters, out_turns = {}, {}
    for n, orig in enumerate(targets):
        src = trivia_idx[n]                       # 返答内での実章index（intro/outroを除いた本物）
        ch = chapters_out[src]
        ch["section"] = "trivia"
        out_chapters[orig] = ch
        turns = [dict(t, chapter=orig, section="trivia")
                 for t in new_script if t.get("chapter") == src]
        if not turns:
            raise ValueError(f"再生成結果にネタ章の台詞が見つかりません（章{orig}）")
        out_turns[orig] = turns
    return {"chapters": out_chapters, "turns": out_turns}


def splice_regenerated(script_result: dict, regen: dict) -> dict:
    """regenerate_chapters の結果を script_result にインプレース反映する（純関数・I/Oなし）。

    指定章の chapters[i] を差し替え、その章の発言（chapter==i）を新ターンに丸ごと置換。
    章番号・章数は不変なので他章とreview.jsonの (章,カット) キーは保たれる。
    """
    new_chapters = regen["chapters"]
    new_turns = regen["turns"]
    for i, ch in new_chapters.items():
        script_result["chapters"][i] = ch
    # 旧スクリプトから対象章の発言を除き、章順を保って新ターンを差し込む。
    rebuilt, inserted = [], set()
    for t in script_result["script"]:
        c = t.get("chapter")
        if c in new_turns:
            if c not in inserted:  # その章の最初の旧ターン位置に新ターン群を挿入
                rebuilt.extend(new_turns[c])
                inserted.add(c)
            continue  # 旧ターンは捨てる
        rebuilt.append(t)
    # 旧スクリプトに発言が無かった対象章（まれ）は末尾の同章付近に追補
    for c, turns in new_turns.items():
        if c not in inserted:
            rebuilt.extend(turns)
    script_result["script"] = rebuilt
    normalize_turns(script_result["script"], script_result["chapters"])
    return script_result


def _short_output_block(explainer: str, questioner: str) -> str:
    """ショート(縦・1ネタ単体)の出力形式＋例。trivia章1つ・hook付き・自己完結。"""
    return f"""## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること。
**厳密に有効なJSONにすること**（"を文字列内で使わない＝引用は「」『』、末尾カンマ無し、生改行無し）:
{{{{
  "theme": "（このショートのテーマ＝ネタの核を日本語で短く）",
  "chapters": [
    {{{{"section": "trivia", "title": "QRコードのQの意味",
      "hook": "QRコードの『Q』、実は意外な意味",
      "summary": "QRのQはQuick（速い）。高速に読み取れることが名前の由来。",
      "image_cuts": [
        {{{{"image_query": "QR code", "image_kind": "subject", "image_query_ja": "QRコード"}}}},
        {{{{"image_query": "smartphone scanning qr code", "image_kind": "ambient", "image_query_ja": "QRを読むスマホ"}}}}
      ]}}}}
  ],
  "script": [
    {{{{"speaker": "{explainer}", "text": "QRコード（キューアールコード）の『Q』が何の略か、実は知らない人が多いの。", "emotion": "normal", "section": "trivia", "chapter": 0, "effect": "flash", "cut": 0}}}},
    {{{{"speaker": "{questioner}", "text": "えっ、言われてみると考えたことないのだ…", "emotion": "surprise", "section": "trivia", "chapter": 0, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{explainer}", "text": "答えはQuick（クイック）。Quick Response、つまり『素早い反応』の頭文字なのよ。", "emotion": "happy", "section": "trivia", "chapter": 0, "effect": "zoom_punch", "cut": 1}}}},
    {{{{"speaker": "{questioner}", "text": "へぇー！一瞬で読み取れる速さがそのまま名前になってたのだ！", "emotion": "happy", "section": "trivia", "chapter": 0, "effect": "kenburns", "cut": 1}}}}
  ]
}}}}
- chapters は **trivia を1つだけ**。"hook" は必須（縦ショートの固定見出し＝スクロールを止める自己完結の一行・15〜26字）。
- script の "chapter" は全て 0、"section" は全て "trivia"。"""


def build_short_prompt(config: dict, source: dict) -> str:
    """本編のネタ1つを、縦ショート用の自己完結した短尺台本に書き直すプロンプト（純関数）。

    source = {"title","summary","lines"}（元ネタの見出し・要点・元台詞）。
    事実は source を真とし、新しい事実をでっち上げない。掴み先頭・30〜45秒・自己完結に再構成する。
    """
    s = config.get("story", {})
    questioner = s.get("questioner", DEFAULT_QUESTIONER)
    explainer = s.get("explainer", DEFAULT_EXPLAINER)
    lines = (source.get("lines") or "").strip() or "（なし）"
    return f"""
あなたはテクノロジー雑学を扱う教養系YouTubeの掛け合い台本ライターです。
既存動画の中の「実は」ネタ1つを、**縦のショート動画（単体で完結する30〜45秒）**用に書き直します。

## 元ネタ（この事実だけを使う・新しい事実を足さない）
- 見出し: {source.get('title', '')}
- 要点: {source.get('summary', '')}
- 元の台詞（参考・言い回しは作り直してよい）:
{lines}

## ショート台本の作り方【厳守】
- **冒頭で掴む（コールドオープン）**：1ターン目でいきなり意外な問い／断言を出す。「ところでね」「さっきの」「今日は」等の前置き・参照は禁止。
- **完全に自己完結**：本編や他のネタを前提にしない。順序を示す語（最初／次／ラスト／〇つ目）も使わない。単体で意味が通ること。
- **テンポ重視・短く**：合計 **6〜8ターン・全体で200〜260字程度**。問い→素朴な外し→「実は」の明かし→驚き→軽い追い打ち、で完結。冗長な繰り返しを入れない。
- **1ターンは最大2文・70字程度**まで。
- 締めは唐突に切らず自然に（要約／感想／軽いツッコミ等）。CTAや「チャンネル登録」は**台詞に入れない**（動画側で出す）。
- 事実は正確に。確実でない逸話は「諸説あるけれど」と限定。年号・前後関係も正確に。

{_rules_block(questioner, explainer, 1, regen=True)}

{_short_output_block(explainer, questioner)}

## このショートでの出力（最重要）
- chapters は trivia を1つだけ。script の chapter は全て 0、section は全て trivia。
- "hook"（固定見出し）は必ず入れる。タイトルより刺さる自己完結の一行にする。
- 挨拶・導入・締めの定型（「皆さんこんにちは」「また次の動画で」等）や順序語は一切入れない。""".strip()


def shortify_chapter(config: dict, script_result: dict, chapter_index: int) -> dict:
    """本編 script_result の指定 trivia 章を、縦ショート用の自己完結した短尺台本へ書き直す。

    Gemini 1回。Returns: 単体で完結する {theme, chapters:[1 trivia(hook付)], script:[chapter=0...]}。
    画像取得・音声・meta は本編と同じ下流で処理できる形。
    """
    chapters = script_result.get("chapters", [])
    if not (0 <= chapter_index < len(chapters)):
        raise ValueError(f"章index {chapter_index} が範囲外")
    src_ch = chapters[chapter_index]
    if src_ch.get("section") != "trivia":
        raise ValueError("trivia 章を指定してください（intro/outro は不可）")
    lines = "\n".join(
        f"{t.get('speaker', '')}: {t.get('text', '')}"
        for t in script_result.get("script", [])
        if t.get("chapter") == chapter_index and t.get("section") == "trivia" and not t.get("closing")
    )
    source = {"title": src_ch.get("title", ""), "summary": src_ch.get("summary", ""), "lines": lines}
    data = _generate_parsed(config, build_short_prompt(config, source),
                            log_label=f"ショート台本（{source['title']}）")
    # trivia 1章だけに正規化（Geminiが余計な章を出しても先頭triviaを採用）。
    trivia = [c for c in data.get("chapters", []) if c.get("section") == "trivia"]
    if not trivia:
        raise ValueError("ショート台本に trivia 章がありません")
    data["chapters"] = trivia[:1]
    # script は chapter==0 の発言のみ（先頭trivia）。section/chapterを 0/trivia に正規化。
    turns = [t for t in data.get("script", []) if t.get("chapter") in (0, None)]
    for t in turns:
        t["chapter"] = 0
        t["section"] = "trivia"
    data["script"] = turns
    append_short_closing(data, config)  # 本編誘導の固定締めを焼き込む
    normalize_turns(data["script"], data["chapters"])
    if not data.get("theme"):
        data["theme"] = source["title"]
    return data


def _shorts_batch_output_block(explainer: str, questioner: str, n: int) -> str:
    """ショート複数本を1回で出す出力形式。trivia章をn個（各=1ショート・hook必須・自己完結）。"""
    return f"""## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること。
**厳密に有効なJSON**（"を文字列内で使わない＝引用は「」『』、末尾カンマ無し、生改行無し）:
- chapters は **trivia を {n} 個**（各章＝独立した1本のショート）。intro/outro は出さない。
- script の "chapter" は 0..{n - 1}（その章＝そのショートの台詞）。"section" は全て "trivia"。
- 各章に "hook"（縦ショート上部の固定見出し・自己完結の一行・15〜26字）を必ず付ける。
{{{{
  "chapters": [
    {{{{"section": "trivia", "title": "QRコードのQの意味", "hook": "QRコードの『Q』、実は意外な意味",
      "summary": "QのQはQuick。高速読み取りが名前の由来。",
      "image_cuts": [
        {{{{"image_query": "QR code", "image_kind": "subject", "image_query_ja": "QRコード"}}}},
        {{{{"image_query": "smartphone scanning qr code", "image_kind": "ambient", "image_query_ja": "QRを読むスマホ"}}}}
      ]}}}}
  ],
  "script": [
    {{{{"speaker": "{explainer}", "text": "QRコード（キューアールコード）の『Q』、実は知らない人が多いの。", "emotion": "normal", "section": "trivia", "chapter": 0, "effect": "flash", "cut": 0}}}},
    {{{{"speaker": "{questioner}", "text": "えっ、考えたことなかったのだ…", "emotion": "surprise", "section": "trivia", "chapter": 0, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{explainer}", "text": "答えはQuick。Quick Response、素早い反応の頭文字なのよ。", "emotion": "happy", "section": "trivia", "chapter": 0, "effect": "zoom_punch", "cut": 1}}}}
  ]
}}}}"""


def build_shorts_batch_prompt(config: dict, sources: list) -> str:
    """選択した本編ネタ群を、各「自己完結ショート（縦・約40秒）」へ1回でまとめて書き直すプロンプト。

    sources=[{title,summary,lines}]（順番＝出力の章0..N-1に対応）。事実は各sourceを真とする。
    """
    s = config.get("story", {})
    questioner = s.get("questioner", DEFAULT_QUESTIONER)
    explainer = s.get("explainer", DEFAULT_EXPLAINER)
    n = len(sources)
    blocks = "\n".join(
        f"### ショート{i}（出力の chapter {i}）\n- 見出し: {src.get('title', '')}\n"
        f"- 要点: {src.get('summary', '')}\n- 元の台詞(参考・言い回しは作り直してよい):\n{(src.get('lines') or '（なし）')}"
        for i, src in enumerate(sources)
    )
    return f"""
あなたはテクノロジー雑学を扱う教養系YouTubeの掛け合い台本ライターです。
既存動画の「実は」ネタ {n} 本を、それぞれ**縦のショート動画（単体で完結する30〜45秒）**用に書き直します。

## 元ネタ（各ショートの事実。新しい事実を足さない・取り違えない）
{blocks}

## 各ショートの作り方【厳守】
- **冒頭で掴む（コールドオープン）**：1ターン目でいきなり意外な問い／断言。「ところでね」「さっきの」「今日は」等の前置き・参照は禁止。
- **完全に自己完結**：他のショートや本編を前提にしない。順序語（最初／次／ラスト／〇つ目）も使わない。
- **短く**：1本あたり **6〜8ターン・合計200〜260字**。問い→素朴な外し→「実は」の明かし→驚き→軽い追い打ちで完結。
- **1ターン最大2文・70字**まで。締めは自然に（CTAや登録誘導は台詞に入れない）。
- 事実は正確に。確実でない逸話は「諸説あるけれど」と限定。

{_rules_block(questioner, explainer, 1, regen=True)}

{_shorts_batch_output_block(explainer, questioner, n)}

## このバッチでの出力（最重要）
- chapters は **trivia を {n} 個**（{sources and "ショート0.." + str(n - 1)}に対応・順番厳守）。各章に "hook" 必須。
- script の chapter は 0..{n - 1}、section は全て trivia。挨拶・導入・締めの定型や順序語は入れない。""".strip()


def append_short_closing(script_result: dict, config: dict) -> None:
    """ショート末尾に固定締め（config.story.short_closing・本編誘導）を普通の台詞として足す。

    生成時に script.json へ焼き込む＝/storyで個別編集可・ショートは自動再付与しないので上書きされない。
    """
    lines = (config.get("story", {}) or {}).get("short_closing") or []
    if not lines:
        return
    script = script_result.get("script") or []
    last = script[-1] if script else {}
    ch, cut = last.get("chapter", 0), last.get("cut", 0)
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        script.append({
            "speaker": line.get("speaker") or config.get("story", {}).get("explainer", DEFAULT_EXPLAINER),
            "text": text, "emotion": line.get("emotion") or "happy",
            "section": "trivia", "chapter": ch, "effect": "kenburns", "cut": cut,
        })
    script_result["script"] = script


def shorts_sources(script_result: dict, chapter_indices: list):
    """選択章から、ショート化プロンプト用の sources [{title,summary,lines}] と対象index列を返す。"""
    chapters = script_result.get("chapters", [])
    targets = [i for i in chapter_indices
               if 0 <= i < len(chapters) and chapters[i].get("section") == "trivia"]
    sources = [{
        "title": chapters[i].get("title", ""),
        "summary": chapters[i].get("summary", ""),
        # ネタ本体(trivia)の台詞だけ。末尾に付く締めCTA/ユニゾン(同じ章番号・section outro/closing)は除外。
        "lines": "\n".join(f"{t.get('speaker', '')}: {t.get('text', '')}"
                           for t in script_result.get("script", [])
                           if t.get("chapter") == i and t.get("section") == "trivia"
                           and not t.get("closing")),
    } for i in targets]
    return sources, targets


def shorts_from_parsed(data: dict, n: int, config: dict = None) -> list:
    """{chapters:[n trivia(hook)], script} を n本の独立ショート script_result に分解（純関数）。

    Gemini自動・ブラウザAI貼り付け取り込みの両方で共用。config を渡すと末尾に固定締めを焼き込む。
    Returns: [script_result,...]（順番＝章順）。
    """
    out_chapters = data.get("chapters", [])
    new_script = data.get("script", [])
    trivia_idx = [i for i, c in enumerate(out_chapters) if c.get("section") == "trivia"]
    if len(trivia_idx) < n:
        raise ValueError(f"ショート章が不足（要求{n}・取得{len(trivia_idx)}）")
    results = []
    for ci in trivia_idx[:n]:
        ch = out_chapters[ci]
        turns = [dict(t) for t in new_script if t.get("chapter") == ci]
        for t in turns:
            t["chapter"] = 0
            t["section"] = "trivia"
        sr = {"theme": ch.get("title") or "ショート",
              "chapters": [{**ch, "section": "trivia"}], "script": turns}
        if config is not None:
            append_short_closing(sr, config)  # 本編誘導の固定締めを焼き込む
        normalize_turns(sr["script"], sr["chapters"])
        results.append(sr)
    return results


def generate_shorts_batch(config: dict, script_result: dict, chapter_indices: list) -> list:
    """選択した trivia 章群を、各自己完結ショート台本へ **Gemini 1回で** まとめて書き直す。

    Returns: [{"source_chapter": 元章index, "script_result": {theme,chapters:[1 trivia(hook)],script}}]
    """
    sources, targets = shorts_sources(script_result, chapter_indices)
    if not targets:
        raise ValueError("ショート化できる trivia 章が選択されていません")
    data = _generate_parsed(config, build_shorts_batch_prompt(config, sources),
                            log_label=f"{len(targets)}本のショート台本")
    results = shorts_from_parsed(data, len(targets), config)
    return [{"source_chapter": targets[i], "script_result": results[i]} for i in range(len(targets))]


def generate_story_script(config: dict, also_avoid=None) -> dict:
    """
    configから「実は〇〇雑学」の掛け合い台本を生成する。
    also_avoid=過去動画で出した/却下したネタ[{title,summary}]。指定すると重複回避に渡す。
    Returns: {"theme": str|None, "chapters": [...], "script": [...]}
    """
    theme = (config.get("story", {}).get("theme") or "").strip() or "(Geminiが選定)"
    data = _generate_parsed(config, build_prompt(config, also_avoid),
                            log_label=f"台本（テーマ: {theme}）")

    s = config.get("story", {})
    warn_role_voice(data["script"],
                    s.get("questioner", DEFAULT_QUESTIONER),
                    s.get("explainer", DEFAULT_EXPLAINER))

    logger.info(
        f"台本生成完了: {len(data['script'])}ターン・{len(data['chapters'])}章・"
        f"テーマ「{data.get('theme')}」"
    )
    return {
        "theme": data.get("theme"),
        "chapters": data["chapters"],
        "script": data["script"],
    }
