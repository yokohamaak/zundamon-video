# v2移行バックアップ（2026-07-16）

- `story-01.legacy.json` と `story-scenes.legacy.json` は変換前の原本。
- `convert_to_v2.py` は今回の変換規則の記録。実行すると `video/public/story-01.json` と `video/public/story-scenes.json` をv2へ置き換える。
- `whiteboard_explain` はv2の`whiteboard`へ移した。`chat`と`teamchat`はv2に対応する表示種別がないため`standard`へ移した。AIは画面に出ない音声専用個体となる。
- 旧anchorはキャラクターの中心座標、v2 slotのoriginは足元座標で意味が異なる。x座標は引き継ぎ、y座標は`0.94`へ正規化した。scene editorで背景ごとに調整する。sceneの`figure`（`bust`/`full`）と既定scaleはv2にも引き継ぐ。
- 旧のフラッシュバック、チャット画面、登退場スライド、トランジション、旧演出はv2では再現しない。旧音声時間・文章・話者・ホワイトボード本文は維持する。
- モブに実在しない表情を指定していた旧データは、v2変換で`normal`へ明示的に置換する。対象は`conversion-report.json`へ記録する。
