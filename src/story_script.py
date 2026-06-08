"""
Gemini if台本生成モジュール

NASA APOD（title + explanation）を素材に、「もしも〜だったら？」の掛け合い台本を生成する。
役割: ずんだもん=質問役 / 四国めたん=解説役（configで変更可）。
出力は tts_voicevox・動画が使う script 形式 [{"speaker","text"}, ...]。

設計: build_prompt / parse_script_json は純関数でテスト可能。
google.genai（新SDK）は generate_if_script 内で遅延importする（テストに依存を持ち込まない）。

config（例）:
    if_dialogue:
      questioner: ずんだもん    # 質問役
      explainer: 四国めたん     # 解説役
      target_turns: 12         # 会話ターン数の目安
    models:
      text: gemini-2.5-flash
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONER = "ずんだもん"
DEFAULT_EXPLAINER = "四国めたん"
DEFAULT_TURNS = 12
DEFAULT_MINUTES = 7

# 読み上げ速度の実測換算（VOICEVOX・現行の話者speed設定下で約335字/分）。
# 以前は450字/分で見積もっており、目標文字数が過大→実尺が想定の1.3倍以上に伸びていた。
CHARS_PER_MINUTE = 335

# 動画(video/src/types.ts)が解釈する感情enum。これ以外の値は normal に倒す。
VALID_EMOTIONS = {"normal", "surprise", "happy", "sad", "angry"}
DEFAULT_EMOTION = "normal"

# 台本上の進行フェーズ。Remotionが演出の強弱に使う。不正値は fact に倒す。
VALID_PHASES = {"intro", "fact", "if", "outro"}
DEFAULT_PHASE = "fact"

# 演出effect enum（video/src/types.ts と一致）。不正値は kenburns に倒す。
# kenburns=標準のゆっくりズーム/パン / zoom_punch=if突入で寄る / shake=揺れ
# / flash=白フラッシュ転換 / glow_pulse=発光脈動。
VALID_EFFECTS = {"kenburns", "zoom_punch", "shake", "flash", "glow_pulse"}
DEFAULT_EFFECT = "kenburns"


def build_prompt(apod: dict, config: dict) -> str:
    """APOD情報から日本語のif掛け合い台本生成プロンプトを作る（純関数）。"""
    d = config.get("if_dialogue", {})
    questioner = d.get("questioner", DEFAULT_QUESTIONER)
    explainer = d.get("explainer", DEFAULT_EXPLAINER)
    turns = int(d.get("target_turns", DEFAULT_TURNS))
    minutes = float(d.get("target_minutes", DEFAULT_MINUTES))
    total_chars = int(minutes * CHARS_PER_MINUTE)  # 実測約335字/分換算の総量目安
    exp_chars = int(total_chars / turns * 1.5)  # 解説役1発言の目安（質問役は短いので解説を厚めに）

    title = apod.get("title") or "(無題)"
    explanation = apod.get("explanation") or ""

    return f"""
あなたは宇宙系YouTubeの掛け合い台本ライターです。
本日のNASA APOD（天文写真）を題材に、日本語の「もしも〜だったら？」掛け合い台本を作ってください。

## 題材（NASA APOD・英語）
タイトル: {title}
解説: {explanation}

## 企画の骨子
- 上の天体・現象を入口に、「もしも〜だったら？」という想像（if）を1つ立てて掘り下げる。
  例:「もしもこの天体に立てたら空はどう見える？」→「ではその星の一日は？」と展開。
- **事実パートとifパートの分量は約1対1（半々）にする。** まず天体の事実をしっかり語ってから、その事実を土台にifを想像する。
- **ifを広げすぎてAPODの題材から離れないこと。** ifは今日の天体に直接ひもづく範囲にとどめ、文明論・人類論・哲学など題材と無関係な方向へ膨らませない。
- 事実パートは上の解説の範囲に忠実に。解説に無い事実を断定で創作しない。
- ifパート（想像）は科学的に無理のない範囲で。断定せず「もし〜なら」「〜かもしれない」と想像と分かる言い方にする。

## 登場人物と口調（語尾を混同しないこと・最重要）
- {questioner}（質問役）: 好奇心旺盛。素朴な疑問を次々ぶつける。一人称「ぼく」、語尾は「〜なのだ」「〜のだ？」。
- {explainer}（解説役）: 落ち着いた大人の女性。一人称「わたし」、語尾は「〜よ」「〜わ」「〜なのよ」「〜ね」「〜だわ」など。**各発言は3〜5文・{exp_chars}字程度としっかり語る**（短い相槌だけで終えない）。
- **【厳守】「〜のだ」「〜なのだ」「〜のだよ」は {questioner} 専用。{explainer} には絶対に使わせない。** 逆に {explainer} の女性的語尾を {questioner} に使わせない。各発言が誰の口調か、書く前に必ず確認すること。

## 感情（必須・各発言に1つ付与）
- 各発言に、その発言の感情を表す "emotion" を **必ず** 付ける。値は次のいずれか1つ:
  - normal: 通常の説明・相槌（基本はこれ）
  - surprise: 驚き・意外（「ええっ」「まさか」など強い反応）
  - happy: 楽しい・嬉しい・わくわく
  - sad: 残念・しんみり
  - angry: 怒り（この企画ではほぼ使わない）
- 迷ったら normal。{questioner} は驚き役なので surprise/happy が出やすい。

## 進行フェーズ（必須・各発言に "phase" を1つ付与）
- 各発言が台本のどの段階かを示す "phase" を **必ず** 付ける。値は次のいずれか1つ:
  - intro: 冒頭の写真紹介・導入
  - fact: 天体の事実解説
  - if: 「もしも〜だったら？」の想像パート（山場）
  - outro: まとめ・締め
- 前半=fact中心、後半=if中心になるよう、おおむね intro→fact→if→outro の順で付ける。

## 演出（必須・各発言に "effect" を1つ付与）
- 各発言に画面演出 "effect" を **必ず** 付ける。値は次のいずれか1つ:
  - kenburns: 標準（ゆっくりズーム/パン）。基本はこれ。
  - zoom_punch: ifに突入する瞬間など、グッと寄せたい所
  - shake: 激しい現象・衝撃の描写
  - flash: 場面転換・「もしも」の世界に切り替わる瞬間
  - glow_pulse: 幻想的・神秘的な強調
- **fact/intro/outro は原則 kenburns。** 強い演出（zoom_punch/shake/flash/glow_pulse）は **if パートの見せ場に限って**使い、多用しない。迷ったら kenburns。

## 構成・分量（重要：尺を満たすこと）
- {explainer}が今日の一枚を紹介して始め、{questioner}が食いついて質問していく流れ。
- **全体で約{turns}ターン（発言）。台本全体の合計文字数が約{total_chars}字（読み上げ約{minutes:.0f}分相当）になるよう、各発言を十分な長さで書くこと。短くまとめて早く終わらせない。**
- 導入（写真の紹介）→事実の解説→ifの想像→まとめ、と展開する。**事実の解説とifの想像はおおむね同じ分量（前半=事実／後半=if）**にして、どちらかに偏らせない。
- 専門用語は{explainer}が噛み砕く。

## 補助素材の検索語（stock_queries）
- 事実パートの実写補助として、NASAの無料画像庫(images.nasa.gov)を検索する **英語の検索語を2〜4個** 出すこと。
- 実写が見つかりやすい語にする（探査機・望遠鏡・ミッション名・関連天体の固有名など）。抽象語や日本語は避ける。
  例: "Hubble Space Telescope", "Alpha Centauri", "Proxima Centauri"

## ifパートの想像イラスト案（manual_cuts）
- ifパート（想像）の山場で見せたい「想像イラスト」を **1〜3個** 提案すること（実写が無い空想の情景）。
- 各案に次の3つを付ける:
  - label: 短い日本語の見出し
  - prompt: その絵の情景描写（日本語1〜2文。動画のプレースホルダにも表示する）
  - image_prompt: **画像生成AIにそのまま貼れる英語プロンプト**。被写体・構図・光・色・雰囲気を具体的に。
    宇宙/天文の幻想的でシネマティックな作風、横長16:9を想定。人物の顔のクローズアップは避ける。
- 例:
  - label="赤い空の浜辺"
  - prompt="二重星に照らされ、空が赤紫に染まった惑星の海岸線。砂浜に二つの影が伸びる。"
  - image_prompt="A wide cinematic view of an alien coastline under a binary star system, sky glowing in red and violet hues, two long shadows cast on the sand, calm ocean reflecting twin suns, dreamy sci-fi concept art, ultra detailed, 16:9"

## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること:
{{
  "topic_title": "中央に表示する短い日本語タイトル（10〜18文字程度）",
  "if_premise": "今回のifの一行要約（日本語）",
  "stock_queries": ["English search term", "..."],
  "manual_cuts": [{{"label": "短い見出し", "prompt": "情景描写(日本語)", "image_prompt": "English prompt for image AI"}}],
  "script": [
    {{"speaker": "{explainer}", "text": "今日の一枚はこれよ。とても綺麗な写真なの。", "emotion": "normal", "phase": "intro", "effect": "kenburns"}},
    {{"speaker": "{questioner}", "text": "わあ、これは何なのだ？", "emotion": "surprise", "phase": "fact", "effect": "kenburns"}},
    ...
  ]
}}
""".strip()


def parse_script_json(text: str) -> dict:
    """Geminiの応答テキストからJSONを取り出してdictを返す（純関数）。

    - ```json ... ``` のコードフェンスを除去
    - 前後に余分なテキストがあっても最初の '{' から復号
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
    normalize_turns(data["script"])
    # stock_queries は任意。文字列のみ・空白除去・最大4個に整える（無ければ空リスト）。
    data["stock_queries"] = _clean_queries(data.get("stock_queries"))
    # manual_cuts も任意。label/prompt を持つものだけ・最大3個（無ければ空リスト）。
    data["manual_cuts"] = _clean_manual_cuts(data.get("manual_cuts"))
    return data


def warn_role_voice(script, questioner, explainer):
    """役の語尾混同を検出して警告ログを出す（自動修正はしない＝不自然化を避ける）。

    解説役(explainer)が質問役(ずんだもん)の「のだ／なのだ」語尾を使っていないか確認する。
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


def _clean_queries(queries, limit=4):
    """stock_queries を文字列のみ・trim・重複/空除去・最大limit個へ正規化（純関数）。"""
    if not isinstance(queries, list):
        return []
    out = []
    for q in queries:
        if isinstance(q, str) and q.strip() and q.strip() not in out:
            out.append(q.strip())
    return out[:limit]


def _clean_manual_cuts(cuts, limit=3):
    """manual_cuts を {label, prompt} を持つものだけ・trim・最大limit個へ正規化（純関数）。"""
    if not isinstance(cuts, list):
        return []
    out = []
    for c in cuts:
        if not isinstance(c, dict):
            continue
        label = (c.get("label") or "").strip()
        prompt = (c.get("prompt") or "").strip()
        image_prompt = (c.get("image_prompt") or "").strip()
        if label or prompt:
            out.append({"label": label, "prompt": prompt, "image_prompt": image_prompt})
    return out[:limit]


def normalize_turns(script: list) -> list:
    """各ターンの emotion / phase / effect をenum固定する（欠落・不正値はデフォルト補完）。

    Gemini生成・--from-scriptの旧台本どちらにも適用し、phase/effect未付与の台本でも
    動画側が必ず有効値を受け取れるようにする（破壊的・in-place）。
    """
    for turn in script:
        if turn.get("emotion") not in VALID_EMOTIONS:
            turn["emotion"] = DEFAULT_EMOTION
        if turn.get("phase") not in VALID_PHASES:
            turn["phase"] = DEFAULT_PHASE
        if turn.get("effect") not in VALID_EFFECTS:
            turn["effect"] = DEFAULT_EFFECT
    return script


def _generate_with_retry(client, model_name, prompt, max_attempts=3):
    import re
    import time

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=model_name, contents=prompt).text
        except Exception as e:  # noqa: BLE001
            if attempt == max_attempts:
                raise
            match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", str(e), re.IGNORECASE)
            wait = max(int(float(match.group(1))) + 10, 65) if match else 65
            logger.warning(f"生成失敗（試行{attempt}/{max_attempts}）、{wait}秒後にリトライ: {e}")
            time.sleep(wait)


def generate_if_script(apod: dict, config: dict) -> dict:
    """
    APODからif掛け合い台本を生成する。
    Returns: {"script": [...], "topic_title": str|None, "if_premise": str|None}
    """
    from google import genai  # 遅延import（新SDK。tts_clientと同じ）

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model_name = config.get("models", {}).get("text", "gemini-2.5-flash")
    logger.info(f"if台本を生成（モデル: {model_name}・題材: {apod.get('title')}）")

    text = _generate_with_retry(client, model_name, build_prompt(apod, config))
    try:
        data = parse_script_json(text)
    except Exception as e:
        logger.error(f"応答のパースに失敗: {e}\n{text}")
        raise

    d = config.get("if_dialogue", {})
    warn_role_voice(data["script"],
                    d.get("questioner", DEFAULT_QUESTIONER),
                    d.get("explainer", DEFAULT_EXPLAINER))

    logger.info(f"if台本生成完了: {len(data['script'])}ターン・テーマ「{data.get('topic_title')}」")
    return {
        "script": data["script"],
        "topic_title": data.get("topic_title"),
        "if_premise": data.get("if_premise"),
        "stock_queries": data.get("stock_queries", []),
        "manual_cuts": data.get("manual_cuts", []),
    }
