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


def _rules_block(questioner: str, explainer: str, topics: int) -> str:
    """口調・各発言フィールド・章メタ・読み上げの共通ルール（build_prompt と再生成で共用）。"""
    return f"""## 登場人物と口調（語尾を混同しないこと・最重要）
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
5. "cut": その発言の間に画面に映す画像が、その章の image_cuts の**何番目か（0始まりの整数）**。
   章の最初の発言は 0。話が進んで別の被写体に移る発言で 1, 2… と増やす（**戻さない・飛ばさない**）。
   画像の切替が**話の流れ（被写体が変わる所）に合う**ようにする。image_cuts の個数と対応させること。
6. "voice"（任意・声の演技）: その台詞だけ声を変える。{{"speed":速さ,"pitch":高さ,"intonation":抑揚,"volume":音量}}。
   既定は全部1.0（pitchは0.0）。範囲 speed/intonation/volume=0.5〜2.0、pitch=-0.15〜0.15。**多用しない**。
   例: 驚き=intonation 1.4・volume 1.2 / 焦り=speed 1.3 / しみじみ=speed 0.9・intonation 0.8。
7. "pause"（任意・間）: その台詞の**後に置く無音秒**（0〜2）。「実は…」のタメや、オチ前の溜めに少しだけ。**多用しない**。

## 章メタ（chapters・各章に1つ）
章の構成 = intro(導入) 1つ ＋ trivia(各ネタ) {topics}個 ＋ outro(締め) 1つ。各章に次を出す（chapter番号の昇順）:
- "section": intro / trivia / outro のいずれか。
- "title": 画面に出す短い日本語の見出し（ネタの核を10〜18文字で。例「Wi-Fiは略語じゃない」）。
- "summary": そのセクションの要点を1〜2文の日本語で（編集時の概要表示用。動画には出さない）。
  例「JPEGの正式名称は『Joint Photographic Experts Group』。画像圧縮規格ではなく開発したグループの名前。」
- "image_cuts": その章で**順に映す画像を 2〜4個**。ネタの対象物が変わるよう別々の被写体にする。
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
  付けないとVOICEVOXが英字を1文字ずつ不自然に読む。読みはカタカナで（漢字の訳語ではなく音の読み）。"""


def _output_block(explainer: str, questioner: str) -> str:
    """出力JSON形式の指定（build_prompt と再生成で共用）。"""
    return f"""## 出力形式
マークダウンのコードブロックは使わず、以下のJSONだけを出力すること。
**厳密に有効なJSONにすること**:
- 文字列の中でダブルクオート(")を使わない。セリフの強調・引用は必ず「」や『』を使う。
- 末尾カンマを付けない。各要素の区切りカンマを忘れない。
- 文字列内に生の改行を入れない（1つのtextは1行）。
- バックスラッシュや制御文字を入れない。
{{{{
  "theme": "動画のテーマ（日本語・meta.titleに使う。例「実は知らないデジタルの名前の謎」）",
  "chapters": [
    {{{{"section": "intro", "title": "今日のテーマ", "image_cuts": [
      {{{{"image_query": "wifi router", "image_kind": "ambient"}}}}
    ]}}}},
    {{{{"section": "trivia", "title": "Wi-Fiは略語じゃない", "summary": "Wi-Fiは何かの略ではなく、Hi-Fiの響きに似せて作られた造語。", "image_cuts": [
      {{{{"image_query": "wifi symbol", "image_kind": "subject", "image_query_ja": "Wi-Fiのマーク"}}}},
      {{{{"image_query": "vintage hifi audio system", "image_kind": "ambient", "image_query_ja": "昔のオーディオ機器"}}}}
    ]}}}},
    {{{{"section": "outro", "title": "まとめ", "image_cuts": [
      {{{{"image_query": "technology gadgets flat lay", "image_kind": "ambient"}}}}
    ]}}}}
  ],
  "script": [
    {{{{"speaker": "{explainer}", "text": "今日は身近なのに意外と知らない…の話よ。へぇって言わせるわ。", "emotion": "happy", "section": "intro", "chapter": 0, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{questioner}", "text": "へぇを連発させてやるのだ！望むところなのだ！", "emotion": "happy", "section": "intro", "chapter": 0, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{explainer}", "text": "じゃあ最初。Wi-Fiって何の略か言える？", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "flash", "cut": 0}}}},
    {{{{"speaker": "{questioner}", "text": "ワイヤレス…なんとかなのだ？", "emotion": "normal", "section": "trivia", "chapter": 1, "effect": "kenburns", "cut": 0}}}},
    {{{{"speaker": "{explainer}", "text": "実はね、何の略でもないの。Hi-Fi（ハイファイ）の響きに似せた造語なのよ。", "emotion": "surprise", "section": "trivia", "chapter": 1, "effect": "zoom_punch", "cut": 1}}}}
  ]
}}}}"""


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
  1. （繋ぎ）前のネタから一言で滑らかに入る（「じゃあ次はね」「ところでさ」等）。唐突に問いから始めない。
  2. {explainer}が問いを投げる（「〜って何の略か知ってる？」「なんで〜なんだと思う？」）。
  3. {questioner}が素朴に外した答えを言う（視聴者の予想を代弁）。
  4. {explainer}が「実はね、…」と意外な真実を明かす。
  5. {questioner}が驚く（「ええーっ！？」）。
  6. {explainer}が追い打ちの小ネタ・豆知識を1つ足す。
  7. （締め）{questioner}がそのネタを自分の言葉で一言にまとめて締める（「つまり〇〇は〜ってことなのだ！」）。
     **各ネタは必ずこの {questioner} のまとめ一言で終える**（唐突に切らない）。次のネタへは1の繋ぎで入る。
- **冒頭で強く掴む**：最初に今日の小テーマと「意外な話を連発するよ」と予告し、すぐ1ネタ目の問いに入る。前置きを長くしない。
- **意外性は“正確な意外な事実”で作る。** 面白くするために嘘や誇張をしない。
  確実でない逸話は「諸説あるけれど」「と言われている」と限定する。年号・前後関係も正確に。
- ネタの順序は、軽いもの→意外性の強いものへ。最後のネタを一番の山場にすると締まる。

{_rules_block(questioner, explainer, topics)}

## 構成・分量
- **台本全体の合計文字数が約{total_chars}字（読み上げ約{minutes:.0f}分相当）を目安**。大きく超えない。
- テンポよく。1ネタを長く語りすぎず、{topics}個を歯切れよく回す。
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
            cut = {"image_query": q, "image_kind": k}
            ja = (c.get("image_query_ja") or "").strip()
            if ja:  # 人が確認するための日本語ラベル（任意）
                cut["image_query_ja"] = ja
            out.append(cut)
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
            "summary": strip_markdown((c.get("summary") or "").strip()),
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
        # cut アンカー（その章の何番目の画像か）を整数化＋章のimage_cuts範囲にクランプ。
        # chapter確定後に判定。不正/範囲不明は削除（build側で均等割りフォールバック）。
        _normalize_cut(turn, chapters, n)
        _normalize_voice(turn)  # 声の上書き（任意）をクランプ
        _normalize_pause(turn)  # 台詞後の間（任意）をクランプ
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


def _generate_parsed(config: dict, prompt: str, log_label: str = "台本") -> dict:
    """Geminiで生成→parse_script_jsonを、モデルフォールバック付きで試す共通処理。

    全文生成と章単位の再生成で共用。JSON不正は同一モデルで再試行、API系エラーは次モデルへ。
    Returns: parse_script_json の結果。全滅時は最後の例外を送出。
    """
    from google import genai  # 遅延import（新SDK）

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    models_cfg = config.get("models", {})
    primary = models_cfg.get("text", "gemini-2.5-flash")
    # 503(高負荷)が続くモデルを見切って順に試すフォールバック（いずれも無料枠）。
    fallbacks = models_cfg.get("text_fallbacks", ["gemini-2.5-flash-lite", "gemini-2.0-flash"])
    candidates = [primary] + [m for m in fallbacks if m != primary]

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

## 各ネタの面白さの型（リズム）
1.（繋ぎ）滑らかに入る　2.{explainer}が問いを投げる　3.{questioner}が素朴に外す
4.{explainer}が「実はね、…」と明かす　5.{questioner}が驚く　6.{explainer}が追い打ちの小ネタ
7.（締め）{questioner}が自分の言葉で一言まとめ（「つまり〇〇は〜なのだ！」）。各ネタは必ずこのまとめで終える。
- 意外性は嘘や誇張でなく正確な事実で。確実でない逸話は「諸説あるけれど」と限定。年号・前後関係も正確に。

{_rules_block(questioner, explainer, topics)}

## 分量
- 各ネタはテンポよく簡潔に。1ネタあたり数往復で歯切れよく。

{_output_block(explainer, questioner)}
""".strip()


def regenerate_chapters(config: dict, script_result: dict, target_indices: list) -> dict:
    """既存台本の指定 trivia 章（target_indices）だけ、既出ネタと重複しない内容で再生成する。

    Gemini呼び出しは1回（選択章をまとめて生成し相互重複も防ぐ）。
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
    prompt = build_regen_prompt(config, theme, existing, len(targets))
    data = _generate_parsed(config, prompt, log_label=f"{len(targets)}章の差し替え台本")

    new_chapters = [c for c in data.get("chapters", []) if c.get("section") == "trivia"]
    new_script = data.get("script", [])
    if len(new_chapters) < len(targets):
        raise ValueError(f"再生成結果の章数が不足（要求{len(targets)}・取得{len(new_chapters)}）")
    # ローカル章番号(0..)→元の章番号 に対応付け。余剰章は捨てる。
    out_chapters, out_turns = {}, {}
    for local, orig in enumerate(targets):
        ch = new_chapters[local]
        ch["section"] = "trivia"
        out_chapters[orig] = ch
        turns = [dict(t, chapter=orig, section="trivia")
                 for t in new_script if t.get("chapter") == local]
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


def generate_story_script(config: dict) -> dict:
    """
    configからIT技術史の章立て掛け合い台本を生成する。
    Returns: {"theme": str|None, "chapters": [...], "script": [...]}
    """
    theme = (config.get("story", {}).get("theme") or "").strip() or "(Geminiが選定)"
    data = _generate_parsed(config, build_prompt(config), log_label=f"台本（テーマ: {theme}）")

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
