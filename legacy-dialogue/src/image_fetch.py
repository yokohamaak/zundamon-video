"""
画像取得の振り分け統合。

各章の image_cuts を image_kind で provider に振り分けて取得し、ch_NN_MM.<ext> を out_dir に保存。
- subject（実在の人物/製品/瞬間）→ Wikimedia 優先（実物が正確）。失敗時は Pexels→Pixabay へ。
- ambient（抽象/雰囲気）→ Pexels 優先→ Pixabay → Wikimedia。
失敗カットはスキップ（呼び出し側がプレースホルダ表示）。

Returns: (image_files, attributions)。どちらも {(章index, カットindex): 値}。
"""
import logging
import os

from src import pexels_client, pixabay_client, wikimedia_client

logger = logging.getLogger(__name__)


def _key(cfg, name, env_default):
    """provider設定が enable なら env からAPIキーを取る。無効/未設定は空文字。"""
    if not cfg.get("enable", True):
        return ""
    return os.environ.get(cfg.get("api_key_env", env_default), "") or ""


def _provider_lang(lang):
    """汎用 lang('ja'/'en'/None) を provider 別パラメータへ変換する。

    Returns: (pexels_locale, pixabay_lang)。日本語は ("ja-JP","ja")、それ以外は (None,None)＝既定の英語。
    Wikimedia は多言語検索のためクエリをそのまま使う（変換不要）。
    """
    if (lang or "").lower().startswith("ja"):
        return "ja-JP", "ja"
    return None, None


def fetch_one_cut(query, kind, out_dir, base, config, lang=None):
    """1カット分の画像を取得する（provider振り分け＋フォールバック）。

    - subject(実在の人物/製品/ロゴ)はWikimediaのみ。取れなければ stock の無関係画像に
      フォールバックせず None（呼び出し側でプレースホルダ＝別物の「嘘の絵」を防ぐ）。
    - ambient(雰囲気)は Pexels→Pixabay→Wikimedia の順でフォールバック可。
    - lang='ja' で Pexels/Pixabay を日本語クエリ解釈にする（手動レビューの日本語取得用）。
    Returns: (filename|None, attribution|None)
    """
    query = (query or "").strip()
    if not query:
        return None, None
    images_cfg = config.get("images", {})
    timeout = int(images_cfg.get("timeout", 30))
    wiki_on = images_cfg.get("wikimedia", {}).get("enable", True)
    px_key = _key(images_cfg.get("pexels", {}), "pexels", "PEXELS_API_KEY")
    pb_key = _key(images_cfg.get("pixabay", {}), "pixabay", "PIXABAY_API_KEY")
    px_locale, pb_lang = _provider_lang(lang)

    def fetch_wiki():
        return wikimedia_client.fetch_one(query, out_dir, base, timeout) if wiki_on else (None, None)

    def fetch_px():
        return pexels_client.fetch_one(query, out_dir, base, px_key, timeout, locale=px_locale) if px_key else (None, None)

    def fetch_pb():
        return pixabay_client.fetch_one(query, out_dir, base, pb_key, timeout, lang=pb_lang) if pb_key else (None, None)

    order = [fetch_wiki] if kind == "subject" else [fetch_px, fetch_pb, fetch_wiki]
    for fetch in order:
        fn, attr = fetch()
        if fn:
            return fn, attr
    return None, None


_SOURCE_LABELS = {"pexels": "Pexels", "pixabay": "Pixabay", "wikimedia": "Wikimedia"}


def available_sources(kind, config):
    """kind と設定(キー有無)から、使える取得先IDを優先順で返す。

    subject は Wikimedia のみ（実物の正確さ優先）。ambient は Pexels→Pixabay→Wikimedia。
    キー未設定の provider は除外する。Returns: [{"id","label"}]。
    """
    images_cfg = config.get("images", {})
    wiki_on = images_cfg.get("wikimedia", {}).get("enable", True)
    px_key = _key(images_cfg.get("pexels", {}), "pexels", "PEXELS_API_KEY")
    pb_key = _key(images_cfg.get("pixabay", {}), "pixabay", "PIXABAY_API_KEY")
    order = ["wikimedia"] if kind == "subject" else ["pexels", "pixabay", "wikimedia"]
    avail = {"wikimedia": wiki_on, "pexels": bool(px_key), "pixabay": bool(pb_key)}
    return [{"id": s, "label": _SOURCE_LABELS[s]} for s in order if avail[s]]


def fetch_candidates(query, kind, source, config, per_source=12, lang=None, page=1):
    """指定 source の候補画像リストを返す（DLしない・サムネ表示用）。追加課金なし。

    lang='ja' で Pexels/Pixabay を日本語クエリ解釈にする（手動の日本語取得ボタン用）。
    page は 1始まり（候補の「もっと見る」用）。
    Returns: [{"source","thumb","url","attribution"}]。空クエリ/未対応sourceは []。
    """
    query = (query or "").strip()
    if not query:
        return []
    images_cfg = config.get("images", {})
    timeout = int(images_cfg.get("timeout", 30))
    px_locale, pb_lang = _provider_lang(lang)
    if source == "pexels":
        return pexels_client.candidates(
            query, _key(images_cfg.get("pexels", {}), "pexels", "PEXELS_API_KEY"),
            per_source, timeout, locale=px_locale, page=page)
    if source == "pixabay":
        return pixabay_client.candidates(
            query, _key(images_cfg.get("pixabay", {}), "pixabay", "PIXABAY_API_KEY"),
            per_source, timeout, lang=pb_lang, page=page)
    if source == "wikimedia":
        if not images_cfg.get("wikimedia", {}).get("enable", True):
            return []
        return wikimedia_client.candidates(query, per_source, timeout, page=page)
    return []


def fetch_images(chapters, out_dir, config):
    image_files, attributions = {}, {}
    total = 0
    for ch, chapter in enumerate(chapters):
        for ci, cut in enumerate(chapter.get("image_cuts", [])):
            query = (cut.get("image_query") or "").strip()
            if not query:
                continue
            total += 1
            base = f"ch_{ch:02d}_{ci:02d}"
            kind = cut.get("image_kind", "ambient")
            fn, attr = fetch_one_cut(query, kind, out_dir, base, config)
            if fn:
                image_files[(ch, ci)] = fn
                if attr:
                    attributions[(ch, ci)] = attr
            else:
                logger.info(f"画像取得できず(プレースホルダ): 章{ch}カット{ci} '{query}'")

    logger.info(f"画像取得: {len(image_files)}/{total} 枚（残りはプレースホルダ）")
    return image_files, attributions
