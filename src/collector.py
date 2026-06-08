"""
RSS収集モジュール
指定されたRSSフィードから記事を収集する
"""
import feedparser
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path("articles_cache.json")


def fetch_articles(sources: list, lookback_hours: int) -> list:
    """RSSフィードから記事を収集する"""
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    for source in sources:
        name = source["name"]
        url = source["url"]
        logger.info(f"収集中: {name} ({url})")

        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                published = _parse_date(entry)
                if published and published < cutoff:
                    continue

                article = {
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "source": name,
                    "category": source.get("category", "general"),
                    "summary": entry.get("summary", ""),
                    "published_at": published.isoformat() if published else None,
                    "hash": _make_hash(entry.get("link", "") or entry.get("title", "")),
                }
                articles.append(article)

        except Exception as e:
            logger.warning(f"{name} の収集に失敗しました: {e}")

    logger.info(f"収集完了: {len(articles)} 記事")
    return articles


def load_cache() -> dict:
    """キャッシュファイルを読み込む"""
    if not CACHE_FILE.exists():
        return {"articles": []}
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache: dict, cache_days: int):
    """キャッシュを保存（古いエントリを削除）"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cache_days)
    cache["articles"] = [
        a for a in cache["articles"]
        if a.get("published_at") and datetime.fromisoformat(a["published_at"]) > cutoff
    ]
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    logger.info(f"キャッシュ保存: {len(cache['articles'])} 記事")


def filter_new_articles(articles: list, cache: dict) -> list:
    """キャッシュと照合して未使用記事のみ返す"""
    used_hashes = {a["hash"] for a in cache["articles"] if a.get("used")}
    new_articles = [a for a in articles if a["hash"] not in used_hashes]
    logger.info(f"新規記事: {len(new_articles)} 件（重複除外後）")
    return new_articles


def mark_as_used(hashes: list, cache: dict):
    """使用済みフラグを立てる"""
    hash_set = set(hashes)
    for article in cache["articles"]:
        if article["hash"] in hash_set:
            article["used"] = True


def merge_into_cache(articles: list, cache: dict):
    """新規記事をキャッシュにマージ（重複なし）"""
    existing_hashes = {a["hash"] for a in cache["articles"]}
    for article in articles:
        if article["hash"] not in existing_hashes:
            article["used"] = False
            cache["articles"].append(article)


def _parse_date(entry) -> datetime | None:
    """feedparserのエントリから日時をパース"""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import time
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _make_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
