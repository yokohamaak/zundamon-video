"""
Gemini IT技術史台本生成モジュール

1テーマ（例「なぜGitは世界を変えたのか」）を題材に、章立て時系列の掛け合い台本を生成する。
役割: ずんだもん=聞き手/初心者 / 四国めたん=解説役（configで変更可）。
出力は tts_voicevox・動画が使う script 形式 [{"speaker","text",...}] ＋ 章メタ chapters。

設計: build_prompt / parse_script_json / normalize_turns / assign_sections_to_turns は
純関数でテスト可能。google.genai（新SDK）は generate_story_script 内で遅延importする
（テストに依存を持ち込まない）。

config（例）:
    story:
      theme: "なぜGitは世界を変えたのか"   # 空ならGeminiにテーマ選定させる
      chapters: 5                          # 章数の目安
      questioner: ずんだもん               # 聞き手
      explainer: 四国めたん                # 解説役
      target_minutes: 7
    models:
      text: gemini-2.5-flash
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONER = "ずんだもん"
DEFAULT_EXPLAINER = "四国めたん"
DEFAULT_TOPICS = 5  # 1本に束ねる「実は」ネタの目安数
DEFAULT_MINUTES = 7

# 読み上げ速度の実測換算（VOICEVOX・現行の話者speed設定下で約335字/分）。
CHARS_PER_MINUTE = 335

# 動画(video/src/types.ts)が解釈する感情enum。これ以外の値は normal に倒す。
VALID_EMOTIONS = {"normal", "surprise", "happy", "sad", "angry"}
DEFAULT_EMOTION = "normal"

# セクション種別。chapters[].section と script[].section に使う。不正値は trivia に倒す。
# intro=導入 / trivia=各「実は」ネタ / outro=締め。
VALID_SECTIONS = {"intro", "trivia", "outro"}
DEFAULT_SECTION = "trivia"

# 演出effect enum（video/src/types.ts と一致）。不正値は kenburns に倒す。
# kenburns=標準のゆっくりズーム/パン / zoom_punch=寄る / shake=揺れ
# / flash=白フラッシュ転換（章境界向き）/ glow_pulse=発光脈動。
VALID_EFFECTS = {"kenburns", "zoom_punch", "shake", "flash", "glow_pulse"}
DEFAULT_EFFECT = "kenburns"

# 画像の取得先振り分け（image_fetch がPhase2で参照）。
# subject=実在の人物/製品/歴史的瞬間（Wikimedia向き）/ ambient=抽象・雰囲気（Pexels/Pixabay向き）。
VALID_IMAGE_KINDS = {"subject", "ambient"}
DEFAULT_IMAGE_KIND = "ambient"


def build_prompt(config: dict) -> str:
    """configから日本語のIT技術史・章立て掛け合い台本生成プロンプトを作る（純関数）。"""
    s = config.get("story", {})
    questioner = s.get("questioner", DEFAULT_QUESTIONER)
    explainer = s.get("explainer", DEFAULT_EXPLAINER)
    topics = int(s.get("topics", s.get("chapters", DEFAULT_TOPICS)))
    minutes = float(s.get("target_minutes", DEFAULT_MINUTES))
    total_chars = int(minutes * CHARS_PER_MINUTE)  # 実測約335字/分換算の総量目安
    theme = (s.get("theme") or "").strip()

    if theme:
        theme_line = f'今回の小テーマは「{theme}」。このテーマに沿った「実は」ネタを集めてください。'
    else:
        theme_line = (
            "今回の小テーマはあなたが選んでください。テクノロジー全般（IT・コンピュータ・科学・"
            "ガジェット・工学など）から、身近で意外な雑学が複数集まる小テーマを1つ選ぶ"
            "（例:「デジタルの名前の由来」「キーボードの謎」「身近な技術の意外な仕組み」"
            "「有名IT企業の創業秘話」「単位や記号の由来」）。"
        )

    return f"""
あなたはテクノロジー雑学を扱う教養系YouTubeの掛け合い台本ライターです。
1つの小テーマのもとに「実は〇〇なんです」という意外な雑学を複数集め、
「へぇ！」が連発する日本語の掛け合い台本を作ってください。視聴者が明日誰かに話したくなる動画にします。

## テーマ
{theme_line}

## 企画の骨子（実は〇〇雑学・ネタを束ねる）
- 小テーマに沿った**「実は」ネタを {topics} 個**集める。各ネタは独立して「へぇ」となる意外な事実にする。
- 各ネタは次のリズムで展開する（これが面白さの型）:
  1. {explainer}が問いを投げる（「〜って何の略か知ってる？」「なんで〜なんだと思う？」）。
  2. {questioner}が素朴に外した答えを言う（視聴者の予想を代弁）。
  3. {explainer}が「実はね、…」と意外な真実を明かす。
  4. {questioner}が驚く（「ええーっ！？」）。
  5. {explainer}が追い打ちの小ネタ・豆知識を1つ足して締める。
- **冒頭で強く掴む**：最初に今日の小テーマと「意外な話を連発するよ」と予告し、すぐ1ネタ目の問いに入る。前置きを長くしない。
- **意外性は“正確な意外な事実”で作る。** 面白くするために嘘や誇張をしない。
  確実でない逸話は「諸説あるけれど」「と言われている」と限定する。年号・前後関係も正確に。
- ネタの順序は、軽いもの→意外性の強いものへ。最後のネタを一番の山場にすると締まる。

## 登場人物と口調（語尾を混同しないこと・最重要）
- {questioner}（聞き手・ボケ役）: 好奇心旺盛。問いに素朴に外した答えを出し、真実に驚く。視聴者の代弁者。
  一人称「ぼく」、語尾は「〜なのだ」「〜のだ？」。リアクションは毎回同じにせず変化をつける
  （驚き・感心・脱線・ツッコミ・共感など）。
- {explainer}（解説役・語り役）: 落ち着いた大人の女性。意外な事実を楽しげに明かす。
  一人称「わたし」、語尾は「〜よ」「〜わ」「〜なのよ」「〜ね」「〜だわ」など。
  **各ネタの“実は”の説明は2〜4文**で、意外な核心＋なぜそうなったかを簡潔に。長すぎる講義にしない。
- **【厳守】「〜のだ」「〜なのだ」「〜のだよ」は {questioner} 専用。{explainer} には絶対に使わせない。**
  逆に {explainer} の女性的語尾を {questioner} に使わせない。書く前に必ず誰の口調か確認すること。

## 各発言に必ず付けるフィールド
1. "chapter": その発言が属する章の番号（0始まりの整数）。章＝「導入(1つ)＋各ネタ(1つずつ)＋締め(1つ)」。
   発言は章順に並べ、章番号は飛ばさない。
2. "section": その章の種別。{sorted(VALID_SECTIONS)} のいずれか。intro=導入 / trivia=各ネタ / outro=締め。
   同じ章の発言は同じsectionにする。
3. "emotion": normal（基本）/ surprise（驚き・意外）/ happy（嬉しい・わくわく）/ sad / angry。
   {questioner} は驚き役なので surprise/happy が出やすい。迷ったら normal。
4. "effect": kenburns（基本）/ zoom_punch（“実は”の真実を明かす瞬間に効く）/ shake / flash（ネタの切替）/ glow_pulse。
   **基本は kenburns。ネタが切り替わる最初の発言に flash、真実を明かす所に zoom_punch** を使うと締まる。多用しない。

## 章メタ（chapters・各章に1つ）
章の構成 = intro(導入) 1つ ＋ trivia(各ネタ) {topics}個 ＋ outro(締め) 1つ。各章に次を出す（chapter番号の昇順）:
- "section": intro / trivia / outro のいずれか。
- "title": 画面に出す短い日本語の見出し（ネタの核を10〜18文字で。例「Wi-Fiは略語じゃない」）。
- "image_cuts": その章で**順に映す画像を 2〜4個**。ネタの対象物が変わるよう別々の被写体にする。
  各要素に:
  - "image_kind": "subject"（実在の人物・製品・ロゴ・記号など特定物。例 "Bluetooth logo", "Larry Tesler"）
    / "ambient"（抽象・雰囲気。例 "wifi router", "old typewriter"）。
  - **略語・規格・技術用語そのもの（例 PNG, GIF, CAPTCHA, HTTP, Bluetooth）が題材のネタは
    必ず "subject" にし、image_query はそのロゴ/ワードマーク名（例 "PNG logo", "CAPTCHA"）にする。**
    抽象的なambient（"artificial intelligence", "robot" 等）にすると題材と無関係なストック画像になり逆効果。
    適切なロゴが無ければ取得失敗→プレースホルダで構わない（無関係画像より良い）。
  - "image_query": 英語の検索語。**subject は固有名詞のみ**（説明を足さない）。**ambient は情景キーワード**でよい。

## 構成・分量
- **台本全体の合計文字数が約{total_chars}字（読み上げ約{minutes:.0f}分相当）を目安**。大きく超えない。
- テンポよく。1ネタを長く語りすぎず、{topics}個を歯切れよく回す。
- 専門用語は{explainer}が一言で噛み砕く。

## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること:
{{
  "theme": "動画のテーマ（日本語・meta.titleに使う。例「実は知らないデジタルの名前の謎」）",
  "chapters": [
    {{"section": "intro", "title": "今日のテーマ", "image_cuts": [
      {{"image_query": "wifi router", "image_kind": "ambient"}}
    ]}},
    {{"section": "trivia", "title": "Wi-Fiは略語じゃない", "image_cuts": [
      {{"image_query": "wifi symbol", "image_kind": "subject"}},
      {{"image_query": "vintage hifi audio system", "image_kind": "ambient"}}
    ]}},
    {{"section": "outro", "title": "まとめ", "image_cuts": [
      {{"image_query": "technology gadgets flat lay", "image_kind": "ambient"}}
    ]}}
  ],
  "script": [
    {{"speaker": "{explainer}", "text": "今日は身近なのに意外と知らない…の話よ。へぇって言わせるわ。", "emotion": "happy", "section": "intro", "chapter": 0, "effect": "kenburns"}},
    {{"speaker": "{questioner}", "text": "へぇを連発させてやるのだ！望むところなのだ！", "emotion": "happy", "section": "intro", "chapter": 0, "effect": "kenburns"}},
    {{"speaker": "{explainer}", "text": "じゃあ最初。Wi-Fiって何の略か言える？", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "flash"}},
    {{"speaker": "{questioner}", "text": "ワイヤレス…なんとかなのだ？", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "kenburns"}},
    {{"speaker": "{explainer}", "text": "実はね、何の略でもないの。語呂で作った造語なのよ。", "emotion": "surprise", "section": "trivia", "chapter": 1, "effect": "zoom_punch"}}
  ]
}}
""".strip()


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
    data, _ = json.JSONDecoder().raw_decode(text, start)
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


def _clean_image_cuts(cuts, limit=4):
    """image_cuts を [{image_query, image_kind}] へ正規化（純関数）。

    image_kind はenum固定、image_query は trim。dict以外・query空は除外・最大limit個。
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
        if q:
            out.append({"image_query": q, "image_kind": k})
    return out[:limit]


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
        out.append({
            "section": section,
            "title": strip_markdown((c.get("title") or "").strip()),
            "image_cuts": cuts,
        })
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


def normalize_turns(script: list, chapters: list = None) -> list:
    """各ターンの emotion / effect / section / chapter をenum・整数固定する（破壊的・in-place）。

    - emotion / effect: enum外はデフォルト補完。
    - chapter: 整数化。chapters があれば [0, len-1] にclamp、無ければ 0以上にclamp。
    - section: chapters があれば chapters[chapter].section で上書き（構造を真とする＝proseを信頼しない）。
      chapters が無ければ enum 正規化のみ。
    """
    n = len(chapters) if chapters else 0
    for turn in script:
        turn["text"] = strip_markdown(turn.get("text", ""))  # 字幕/音声からMarkdown崩れを除去
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


def _generate_with_retry(client, model_name, prompt, max_attempts=5):
    import re
    import time

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=model_name, contents=prompt).text
        except Exception as e:  # noqa: BLE001
            if attempt == max_attempts:
                raise
            msg = str(e)
            match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", msg, re.IGNORECASE)
            if match:
                wait = max(int(float(match.group(1))) + 10, 65)  # 429(レート制限): 指示秒+余裕
            elif re.search(r"503|UNAVAILABLE|overloaded|high demand", msg, re.IGNORECASE):
                wait = 20  # 一時的な高負荷は短間隔で再試行
            else:
                wait = 65
            logger.warning(f"生成失敗（試行{attempt}/{max_attempts}）、{wait}秒後にリトライ: {e}")
            time.sleep(wait)


def generate_story_script(config: dict) -> dict:
    """
    configからIT技術史の章立て掛け合い台本を生成する。
    Returns: {"theme": str|None, "chapters": [...], "script": [...]}
    """
    from google import genai  # 遅延import（新SDK）

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    models_cfg = config.get("models", {})
    primary = models_cfg.get("text", "gemini-2.5-flash")
    # 503(高負荷)が続くモデルを見切って順に試すフォールバック（いずれも無料枠）。
    fallbacks = models_cfg.get("text_fallbacks", ["gemini-2.5-flash-lite", "gemini-2.0-flash"])
    candidates = [primary] + [m for m in fallbacks if m != primary]
    theme = (config.get("story", {}).get("theme") or "").strip() or "(Geminiが選定)"
    prompt = build_prompt(config)

    text = None
    last_err = None
    for model_name in candidates:
        logger.info(f"IT技術史台本を生成（モデル: {model_name}・テーマ: {theme}）")
        try:
            text = _generate_with_retry(client, model_name, prompt, max_attempts=3)
            break
        except Exception as e:  # noqa: BLE001 - 次のモデルへフォールバック
            last_err = e
            logger.warning(f"モデル {model_name} で生成できず、次の候補へフォールバック: {e}")
    if text is None:
        raise last_err

    try:
        data = parse_script_json(text)
    except Exception as e:
        logger.error(f"応答のパースに失敗: {e}\n{text}")
        raise

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
