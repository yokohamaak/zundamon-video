"""ジャンル別の「避けるネタ」永続履歴。

却下したネタ(rejected)と採用して動画に使ったネタ(used)を**動画をまたいで**蓄積し、
初回生成・再生成の両方で重複回避に使う。ジャンル固定で動画を量産しても過去と被らせない。

- ジャンルごとに1ファイル（topic_history/<genre>.json）。将来ジャンルを増やすときは
  プロンプトだけ差し替え、履歴はジャンル別に自然に分かれる。
- 純粋にタイトルで重複排除。動画が1から作り直されてもこの履歴は消えない（review.json とは別）。
"""
import json
import os
import re

# 履歴の置き場所。環境変数で差し替え可（テスト用）。リポジトリ直下に蓄積する。
HISTORY_DIR = os.environ.get("TOPIC_HISTORY_DIR", "topic_history")


def genre_of(config: dict) -> str:
    """config から現在のジャンルIDを得る（未設定は 'tech'）。"""
    return (config.get("story", {}).get("genre") or "tech").strip() or "tech"


def _path(genre: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", genre or "default") or "default"
    return os.path.join(HISTORY_DIR, f"{safe}.json")


def load(genre: str) -> dict:
    """履歴を読む。無ければ空。Returns: {"topics": [{title,summary,status}]}。"""
    path = _path(genre)
    if not os.path.exists(path):
        return {"topics": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("topics"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"topics": []}


def facts(genre: str) -> list:
    """重複回避に渡す [{title, summary}] のリスト（却下＋採用済みの全部）。"""
    return [{"title": t.get("title", ""), "summary": t.get("summary", "")}
            for t in load(genre).get("topics", []) if (t.get("title") or "").strip()]


def add(genre: str, items: list, status: str) -> int:
    """items=[{title,summary}] を status('rejected'|'used') で追記する。

    既存タイトル（status問わず）と重複するものは無視。Returns: 追加件数。
    """
    data = load(genre)
    topics = data.setdefault("topics", [])
    seen = {(t.get("title") or "").strip() for t in topics}
    added = 0
    for it in items or []:
        title = (it.get("title") or "").strip()
        if not title or title in seen:
            continue
        topics.append({"title": title, "summary": (it.get("summary") or "").strip(),
                       "status": status})
        seen.add(title)
        added += 1
    if added:
        os.makedirs(os.path.dirname(_path(genre)) or ".", exist_ok=True)
        with open(_path(genre), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return added


def trivia_facts(chapters: list) -> list:
    """章リストから trivia 章の {title, summary} を取り出す（記録用ヘルパー）。"""
    return [{"title": c.get("title", ""), "summary": c.get("summary", "")}
            for c in (chapters or []) if c.get("section") == "trivia"]
