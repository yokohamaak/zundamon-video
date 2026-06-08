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

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONER = "ずんだもん"
DEFAULT_EXPLAINER = "四国めたん"
DEFAULT_CHAPTERS = 5
DEFAULT_MINUTES = 7

# 読み上げ速度の実測換算（VOICEVOX・現行の話者speed設定下で約335字/分）。
CHARS_PER_MINUTE = 335

# 動画(video/src/types.ts)が解釈する感情enum。これ以外の値は normal に倒す。
VALID_EMOTIONS = {"normal", "surprise", "happy", "sad", "angry"}
DEFAULT_EMOTION = "normal"

# 章の種別（時系列）。chapters[].section と script[].section に使う。不正値は background に倒す。
VALID_SECTIONS = {"intro", "background", "turning_point", "impact", "outro"}
DEFAULT_SECTION = "background"

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
    chapters = int(s.get("chapters", DEFAULT_CHAPTERS))
    minutes = float(s.get("target_minutes", DEFAULT_MINUTES))
    total_chars = int(minutes * CHARS_PER_MINUTE)  # 実測約335字/分換算の総量目安
    theme = (s.get("theme") or "").strip()

    if theme:
        theme_line = f'今回のテーマは「{theme}」。このテーマで台本を作ってください。'
    else:
        theme_line = (
            "今回のテーマはあなたが選んでください。IT・コンピュータ技術史の中で、"
            "「誕生の物語」や「なぜ歴史を塗り替えたのか」が語れる題材を1つ選ぶ"
            "（例: Unix / TCP-IP / Git / WWW / リレーショナルデータベース / トランジスタ）。"
        )

    return f"""
あなたはIT技術史を扱う教養系YouTubeの掛け合い台本ライターです。
1つの技術・出来事を題材に、その「誕生の物語」「なぜ歴史を塗り替えたのか」を、
時系列の章立てで掘り下げる日本語の掛け合い台本を作ってください。

## テーマ
{theme_line}

## 企画の骨子（章立て時系列）
- 全体を時系列の「章」で構成し、章ごとに語る対象（＝映す画像）が必ず変わるようにする。
- 章の流れは原則この順:
  - intro: つかみ。今日の主役を一言で紹介し、なぜ重要かを予告する。
  - background: その技術が生まれる前の課題・時代背景（なぜ必要だったか）。
  - turning_point: 誕生・ブレイクスルーの瞬間（誰が・いつ・どう作ったか）。
  - impact: それが何を変えたか（歴史を塗り替えた点・その後の広がり）。
  - outro: まとめ。動画全体（背景→転機→影響）を簡潔に振り返り、**最後に全体を通しての締めの言葉**で結ぶ。
- background〜impact は内容に応じて複数章に分けてよい。**全体で約{chapters}章**にする。
- **史実に忠実に。** 年・人物・経緯は事実の範囲で語り、不確かな逸話を断定で創作しない。
  確実でない所は「諸説あるけれど」「とされている」と限定する。
- **年号と出来事の前後関係を正確に。** 「いつ始まり・いつ終わったか」「Aの後にB」という順序や期間を
  取り違えないこと。年を述べるときは、それが「出来事が起きた年」か「続いた期間」かを区別する
  （例:「2002年から利用していた」と「2002年まで利用していた」は意味が逆。混同しない）。

## 登場人物と口調（語尾を混同しないこと・最重要）
- {questioner}（聞き手・初心者役）: 好奇心旺盛。素朴な疑問を投げ、視聴者の代弁をする。
  一人称「ぼく」、語尾は「〜なのだ」「〜のだ？」。
- {explainer}（解説役）: 落ち着いた大人の女性。技術と歴史を噛み砕いて語る。
  一人称「わたし」、語尾は「〜よ」「〜わ」「〜なのよ」「〜ね」「〜だわ」など。
  **各発言は4〜6文としっかり語る**（短い相槌だけで終えない。具体例・背景・理由を一つずつ足して厚くする）。
- **【厳守】「〜のだ」「〜なのだ」「〜のだよ」は {questioner} 専用。{explainer} には絶対に使わせない。**
  逆に {explainer} の女性的語尾を {questioner} に使わせない。書く前に必ず誰の口調か確認すること。

## 各発言に必ず付けるフィールド
1. "chapter": その発言が属する章の番号（0始まりの整数）。0=最初の章。発言は章順に並べ、章番号は飛ばさない。
2. "section": その章の種別。{sorted(VALID_SECTIONS)} のいずれか1つ。同じ章の発言は同じsectionにする。
3. "emotion": 感情。次のいずれか1つ:
   - normal（基本）/ surprise（驚き・意外）/ happy（嬉しい・わくわく）/ sad（残念・しんみり）/ angry（ほぼ使わない）
   - {questioner} は驚き役なので surprise/happy が出やすい。迷ったら normal。
4. "effect": 画面演出。次のいずれか1つ:
   - kenburns（標準・基本はこれ）/ zoom_punch（強調したい所）/ shake（衝撃）
   - flash（章の切り替わり＝場面転換）/ glow_pulse（神秘的な強調）
   - **基本は kenburns。章が切り替わる最初の発言に flash を使うと転換が締まる。** 強い演出は多用しない。

## 章メタ（chapters・各章に1つ）
各章について次を出すこと（chapter番号の昇順）:
- "section": 上のenumのいずれか（その章の種別）。
- "title": 画面に出す短い日本語の章見出し（10〜18文字程度）。
- "image_cuts": その章で**順に映す画像を 2〜4個** 並べた配列。章の話の展開（時間経過・登場物や
  場面の変化）に沿って絵が切り替わるよう、**それぞれ別の被写体**にする（同じ章でも1枚で済ませない）。
  各要素に次の2つ:
  - "image_query": フリー素材庫を検索する**英語の検索語**（具体的な固有名や被写体にする）。
  - "image_kind": 画像の種類。
    - "subject": 実在の人物・製品・歴史的な物/瞬間（例: "Linus Torvalds", "first Macintosh computer"）。
    - "ambient": 抽象・雰囲気・概念の画像（例: "source code on screen", "server room"）。
    - 実在の特定物を見せたいcutは subject、つなぎ・概念のcutは ambient にする。

## 構成・分量（重要：尺を満たすこと）
- {explainer}が主役を紹介して始め、{questioner}が食いついて質問していく流れ。
- **台本全体の合計文字数が {total_chars}字以上（読み上げ約{minutes:.0f}分相当）** になるよう書く。
  これを下回らないこと。短くまとめて早く終わらせない。足りなければ各章の解説に具体例・経緯・影響を足す。
- 専門用語は{explainer}が噛み砕く。

## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること:
{{
  "theme": "動画のテーマ（日本語・meta.titleと章全体の見出しに使う）",
  "chapters": [
    {{"section": "intro", "title": "短い章見出し", "image_cuts": [
      {{"image_query": "source code on screen", "image_kind": "ambient"}},
      {{"image_query": "software developers collaborating", "image_kind": "ambient"}}
    ]}},
    {{"section": "turning_point", "title": "誕生の瞬間", "image_cuts": [
      {{"image_query": "Linus Torvalds portrait", "image_kind": "subject"}},
      {{"image_query": "Linux kernel source code", "image_kind": "ambient"}}
    ]}}
  ],
  "script": [
    {{"speaker": "{explainer}", "text": "今日の主役はこれよ。…", "emotion": "normal", "section": "intro", "chapter": 0, "effect": "kenburns"}},
    {{"speaker": "{questioner}", "text": "へえ、それは何なのだ？", "emotion": "surprise", "section": "intro", "chapter": 0, "effect": "kenburns"}}
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
            "title": (c.get("title") or "").strip(),
            "image_cuts": cuts,
        })
    return out[:limit]


def normalize_turns(script: list, chapters: list = None) -> list:
    """各ターンの emotion / effect / section / chapter をenum・整数固定する（破壊的・in-place）。

    - emotion / effect: enum外はデフォルト補完。
    - chapter: 整数化。chapters があれば [0, len-1] にclamp、無ければ 0以上にclamp。
    - section: chapters があれば chapters[chapter].section で上書き（構造を真とする＝proseを信頼しない）。
      chapters が無ければ enum 正規化のみ。
    """
    n = len(chapters) if chapters else 0
    for turn in script:
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
    model_name = config.get("models", {}).get("text", "gemini-2.5-flash")
    theme = (config.get("story", {}).get("theme") or "").strip() or "(Geminiが選定)"
    logger.info(f"IT技術史台本を生成（モデル: {model_name}・テーマ: {theme}）")

    text = _generate_with_retry(client, model_name, build_prompt(config))
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
