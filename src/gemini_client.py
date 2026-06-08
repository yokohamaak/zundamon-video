"""
Gemini Flash連携モジュール
記事の選定・要約・スクリプト生成を行う
"""
import json
import logging
import os
import google.generativeai as genai

logger = logging.getLogger(__name__)


def _build_article_list(articles: list) -> str:
    return json.dumps(
        [{"title": a["title"], "summary": a["summary"], "source": a["source"], "hash": a["hash"]}
         for a in articles],
        ensure_ascii=False,
        indent=2,
    )


def _build_prompt_two_part(japan: list, vibe: list, config: dict) -> str:
    target_minutes = config["digest"]["target_minutes"]
    half_minutes = target_minutes // 2
    chars_per_minute = 300
    half_chars = half_minutes * chars_per_minute
    host = config["characters"]["host"]
    guest = config["characters"]["guest"]
    exclude = config.get("exclude_keywords", [])

    return f"""
あなたはニュースラジオのスクリプトライターです。
以下の記事リストから、約{target_minutes}分の音声放送用スクリプトを2部構成で作成してください。

## 重要な事実確認ルール
- スクリプトに含める事実は、必ず以下の「記事リスト」に書かれている内容だけを使うこと
- 記事に書かれていない情報を追加・推測・創作しないこと
- 特にAI・テクノロジー企業に関しては正確に:
  - Claude → Anthropic社が開発
  - ChatGPT → OpenAI社が開発
  - Gemini → Google社が開発
  - これらを混同しないこと

## 番組構成
【前半 約{half_minutes}分】
- 「前半の記事リスト」の記事に共通するテーマを汲み取り、それに沿って前半を構成すること
- 重要なニュースを2〜3本選んで紹介する
- 合計文字数が約{half_chars}文字以上になるように書くこと
- 各ニュースについて最低5〜8往復の会話をすること

【後半 約{half_minutes}分】バイブコーディング最前線
- 「後半の記事リスト」からAI・開発ツール関連のニュースを2〜3本選んで紹介する
- 合計文字数が約{half_chars}文字以上になるように書くこと
- 各記事について最低5〜8往復の会話をすること
- 実際に開発で使えるような具体的な観点でコメントすること

## 共通条件
- 除外キーワード: {', '.join(exclude)}（これらを含む記事は選ばない）
- ゲストは一回の発言で十分な説明をすること（短すぎる返答にならないよう）
- ホストは好奇心旺盛に追加質問をして会話を深めること
- 前半から後半への自然なつなぎのセリフを入れること
- 選んだ記事のhashリストも出力すること

## キャラクター
- ホスト（{host['gender']}）: {host['name']} - {host['style']}
- ゲスト（{guest['gender']}）: {guest['name']} - {guest['style']}

## 出力形式
以下のJSON形式で出力してください（マークダウンコードブロック不要）:
{{
  "selected_hashes": ["hash1", "hash2", ...],
  "script": [
    {{"speaker": "{host['name']}", "text": "冒頭の挨拶"}},
    {{"speaker": "{guest['name']}", "text": "返し"}},
    ...
  ]
}}

## 前半の記事リスト
{_build_article_list(japan)}

## 後半の記事リスト（バイブコーディング）
{_build_article_list(vibe)}
"""


def _build_prompt_single(articles: list, config: dict) -> str:
    target_minutes = config["digest"]["target_minutes"]
    topics = config.get("topics", [])
    exclude = config.get("exclude_keywords", [])
    host = config["characters"]["host"]
    guest = config["characters"]["guest"]
    chars_per_minute = 300
    target_chars = target_minutes * chars_per_minute

    return f"""
あなたはニュースラジオのスクリプトライターです。
以下の記事リストから、約{target_minutes}分の音声放送用スクリプトを作成してください。

## 重要な事実確認ルール
- スクリプトに含める事実は、必ず以下の「記事リスト」に書かれている内容だけを使うこと
- 記事に書かれていない情報を追加・推測・創作しないこと
- 特にAI・テクノロジー企業に関しては正確に:
  - Claude → Anthropic社が開発
  - ChatGPT → OpenAI社が開発
  - Gemini → Google社が開発
  - これらを混同しないこと

## 条件
- 興味トピック: {', '.join(topics)}
- 除外キーワード: {', '.join(exclude)}（これらを含む記事は選ばない）
- 合計文字数が約{target_chars}文字以上になるように書くこと（1分あたり約{chars_per_minute}文字が目安）
- 各記事のトピックについて、ホストとゲストが最低5〜8往復の会話をすること
- ゲストは一回の発言で十分な説明をすること（短すぎる返答にならないよう）
- ホストは好奇心旺盛に追加質問をして会話を深めること
- 選んだ記事のhashリストも出力すること

## キャラクター
- ホスト（{host['gender']}）: {host['name']} - {host['style']}
- ゲスト（{guest['gender']}）: {guest['name']} - {guest['style']}

## 出力形式
以下のJSON形式で出力してください（マークダウンコードブロック不要）:
{{
  "selected_hashes": ["hash1", "hash2", ...],
  "script": [
    {{"speaker": "{host['name']}", "text": "冒頭の挨拶"}},
    {{"speaker": "{guest['name']}", "text": "返し"}},
    ...
  ]
}}

## 記事リスト
{_build_article_list(articles)}
"""


def generate_digest(articles: list, config: dict) -> tuple[str, list]:
    """
    記事リストからダイジェストスクリプトを生成する
    Returns: (script, used_article_hashes)
    """
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    text_model = config.get("models", {}).get("text", "gemini-2.5-flash")
    model = genai.GenerativeModel(text_model)
    logger.info(f"使用モデル: {text_model}")

    japan_articles = [a for a in articles if a.get("category") == "japan"]
    vibe_articles = [a for a in articles if a.get("category") == "vibe"]
    has_two_parts = bool(japan_articles and vibe_articles)

    if has_two_parts:
        prompt = _build_prompt_two_part(japan_articles, vibe_articles, config)
        logger.info(f"2部構成で生成（日本: {len(japan_articles)}件、バイブ: {len(vibe_articles)}件）")
    else:
        prompt = _build_prompt_single(articles, config)
        logger.info(f"通常構成で生成（{len(articles)}件）")

    import re
    import time
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = model.generate_content(prompt)
            break
        except Exception as e:
            if attempt == max_attempts:
                raise
            err_str = str(e)
            match = re.search(r'retry[^\d]*(\d+(?:\.\d+)?)\s*s', err_str, re.IGNORECASE)
            wait = max(int(float(match.group(1))) + 10, 65) if match else 65
            logger.warning(f"生成失敗（試行{attempt}/{max_attempts}）、{wait}秒後にリトライ: {e}")
            time.sleep(wait)

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        start = text.index("{")
        data, _ = json.JSONDecoder().raw_decode(text, start)
        script = data["script"]
        used_hashes = data["selected_hashes"]
        logger.info(f"スクリプト生成完了: {len(used_hashes)} 記事を使用")
        return script, used_hashes
    except Exception as e:
        logger.error(f"レスポンスのパースに失敗: {e}\n{text}")
        raise
