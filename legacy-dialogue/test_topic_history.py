"""topic_history（ジャンル別の永続「避けるネタ」履歴）のテスト。実ファイルは一時dirに。"""
import os
import tempfile

os.environ["TOPIC_HISTORY_DIR"] = tempfile.mkdtemp()
from src import topic_history as th  # noqa: E402


def test_genre_of():
    assert th.genre_of({"story": {"genre": "science"}}) == "science"
    assert th.genre_of({"story": {}}) == "tech"  # 既定
    assert th.genre_of({}) == "tech"
    print("  genre_of: 既定tech/上書き OK")


def test_add_and_facts_dedup():
    g = "g1"
    n = th.add(g, [{"title": "ネタA", "summary": "a"}, {"title": "ネタB", "summary": "b"}], "used")
    assert n == 2
    # 重複タイトルは status 問わず無視
    n2 = th.add(g, [{"title": "ネタA", "summary": "別要約"}, {"title": "ネタC", "summary": "c"}], "rejected")
    assert n2 == 1, "Aは既存で無視・Cだけ追加"
    titles = sorted(f["title"] for f in th.facts(g))
    assert titles == ["ネタA", "ネタB", "ネタC"], titles
    # 空タイトルは無視
    assert th.add(g, [{"title": "  ", "summary": "x"}], "used") == 0
    print("  add/facts: 追記・タイトル重複排除・空無視 OK")


def test_genre_separation():
    th.add("ga", [{"title": "X", "summary": ""}], "used")
    th.add("gb", [{"title": "Y", "summary": ""}], "used")
    assert [f["title"] for f in th.facts("ga")] == ["X"]
    assert [f["title"] for f in th.facts("gb")] == ["Y"]
    print("  ジャンル別ファイル分離 OK")


def test_persist_across_load():
    th.add("gp", [{"title": "永続ネタ", "summary": "s"}], "used")
    # load し直しても残る（＝review.jsonと違い動画をまたいで残る）
    assert any(t["title"] == "永続ネタ" for t in th.load("gp")["topics"])
    print("  ロードし直しても残る（永続）OK")


def test_trivia_facts():
    chapters = [{"section": "intro", "title": "i"},
                {"section": "trivia", "title": "T1", "summary": "s1"},
                {"section": "outro", "title": "o"}]
    f = th.trivia_facts(chapters)
    assert f == [{"title": "T1", "summary": "s1"}], f
    print("  trivia_facts: trivia章のみ抽出 OK")


def test_used_themes_and_add_theme():
    g = "themes1"
    assert th.used_themes(g) == []
    assert th.add_theme(g, "テーマA") is True
    assert th.add_theme(g, "  ") is False, "空は無視"
    assert th.add_theme(g, "テーマB") is True
    assert th.used_themes(g) == ["テーマA", "テーマB"], "時系列で蓄積"
    # 同テーマの再採用も時系列に追記（巡回の鮮度判定用）
    th.add_theme(g, "テーマA")
    assert th.used_themes(g) == ["テーマA", "テーマB", "テーマA"]
    # ネタ履歴(topics)とテーマ履歴(themes)は同一ファイルで共存
    th.add(g, [{"title": "ネタX", "summary": "x"}], "used")
    assert [f["title"] for f in th.facts(g)] == ["ネタX"]
    assert th.used_themes(g) == ["テーマA", "テーマB", "テーマA"], "ネタ追記でテーマは不変"
    print("  used_themes/add_theme: テーマ時系列蓄積・ネタと共存 OK")


if __name__ == "__main__":
    print("test_topic_history:")
    test_genre_of()
    test_add_and_facts_dedup()
    test_genre_separation()
    test_persist_across_load()
    test_trivia_facts()
    test_used_themes_and_add_theme()
    print("ALL PASS")
