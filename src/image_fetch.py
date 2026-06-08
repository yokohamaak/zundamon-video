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


def fetch_images(chapters, out_dir, config):
    images_cfg = config.get("images", {})
    timeout = int(images_cfg.get("timeout", 30))
    wiki_on = images_cfg.get("wikimedia", {}).get("enable", True)
    px_key = _key(images_cfg.get("pexels", {}), "pexels", "PEXELS_API_KEY")
    pb_key = _key(images_cfg.get("pixabay", {}), "pixabay", "PIXABAY_API_KEY")

    def fetch_wiki(q, base):
        return wikimedia_client.fetch_one(q, out_dir, base, timeout) if wiki_on else (None, None)

    def fetch_px(q, base):
        return pexels_client.fetch_one(q, out_dir, base, px_key, timeout) if px_key else (None, None)

    def fetch_pb(q, base):
        return pixabay_client.fetch_one(q, out_dir, base, pb_key, timeout) if pb_key else (None, None)

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
            # subject(実在の人物/製品/ロゴ)はWikimediaのみ。取れなければ stock の無関係画像に
            # フォールバックせずプレースホルダにする（別人/別物の「嘘の絵」を防ぐ）。
            # ambient(雰囲気)は Pexels→Pixabay→Wikimedia の順でフォールバック可。
            order = [fetch_wiki] if kind == "subject" else [fetch_px, fetch_pb, fetch_wiki]
            fn = attr = None
            for fetch in order:
                fn, attr = fetch(query, base)
                if fn:
                    break
            if fn:
                image_files[(ch, ci)] = fn
                if attr:
                    attributions[(ch, ci)] = attr
            else:
                logger.info(f"画像取得できず(プレースホルダ): 章{ch}カット{ci} '{query}'")

    logger.info(f"画像取得: {len(image_files)}/{total} 枚（残りはプレースホルダ）")
    return image_files, attributions
