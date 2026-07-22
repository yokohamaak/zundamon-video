# ロードマップ / 今後やること

## 台本編集(story_editor.html) - 複数選択での一括編集

シーンの一括変更は実装済み(`selectedIndices` + シーン一括適用バー、Shift/Ctrl(⌘)クリックで複数選択)。
同じ仕組みに乗って **表情(expression)・カメラ演出(cameraEffect)・話者(speaker, V2)・選択ターンの一括削除は実装済み**
(`applyBulkExpression`/`applyBulkCameraEffect`/`v2ApplyBulkSpeaker`/`bulkDeleteSelectedTurns`)。
残りの候補:

- ポーズ(pose)
- 口パクOFF(noLipSync)
- シェイク/回想(shake/flashback)のON・OFF一括切り替え
- セリフ後の間(pause)の一括数値変更

## 未対応の既知バグ・改善候補

- 表情選択(`fExpression`)・ポーズ選択(`fPose`)は、シーン/話者と違いファイル実在チェックをしていない。
  表情はキャラごとにパーツ画像(brow/cheek/eye/mouth等)の組み合わせなので単純な1ファイルチェックでは
  済まず、かつ現在の実装はキャラ非依存の単一グローバル一覧(`_buildExpressionOptions`/`buildPoseSelect`)
  のため、ターン選択中の話者に応じて選択肢を絞り込む作りへの変更も必要。対応する場合は
  expression_editor.py/pose_editor.pyの`_build_catalog()`(スロット単位でos.path.isfileチェック済み)を
  流用する方針で検討する。

## expressions.json / poses.json の命名整理（未着手・2026-07-21記録）

事前設計なしで使う表情を都度足していった結果、命名が統一されていない。

- expressions.json（ずんだもん/めたん共通・17種）: `normal, happy, surprise, trouble, panic, niyari,
  angry, question, cry, nae, doubt, tired, ecstasy, Blurred, sleep, proud, serious`
  - `niyari`だけローマ字、`Blurred`だけ大文字始まりで表記が不統一
  - `question`と`doubt`、`tired`と`sleep`は役割が近く重複気味
  - `nae`は名称から意味が読み取りにくい
- poses.json（13種）: `idle, cheer, recoil, lean, droop, flustered, proud, listening, sneak, wobble,
  point, smartphone, thinking`
  - `proud`がexpressionsとposesの両方にあり二重管理気味

対応する場合はstory_editor.html・StoryVideo.tsx・既存story-01.jsonの参照箇所すべてに影響するため、
命名確定後にリネームスクリプト等での一括移行を検討する。

## モブキャラの表情バリエーション拡充（検討中・2026-07-21記録）

モブは現在`normal`/`agitated`の2状態固定（`story_editor.py`の`_save_mobs`検証・エディタUIの
「通常表情」「取り乱し表情」2セクション・`StoryVideo.tsx`参照ロジックが2状態前提）。

検討中の案:
- **モブ**（都度使い捨ての背景キャラ）: 表情1〜2種のまま
- **準レギュラー**（営業・部長・豆山など再登場するキャラ）: 表情を拡充
  - 命名確定済み: `normal`(ノーマル) / `smile`(笑顔) / `pale`(青ざめ、旧トラブル) /
    `shaken`(動揺、旧焦り) / `wry`(苦笑い、旧たじたじ)
  - 追加候補（優先度順）: `angry`(怒り) / `exasperated`(呆れ) / `cry`(泣き)
- モブと準レギュラーを区別する仕組み（現状`mobs.json`は全キャラ同列）が別途必要
- 画像素材の準備が先。素材ができてから実装着手

## 漫画吹き出しの画面端クランプ（未対応・2026-07-21記録）

漫画(comic)の縦書き吹き出しは長文で左右に自動拡張するため、x を画面端近くに置いた長文バブルは
正しい幅の箱が画面外にはみ出し、端の列が見切れる（描画は正しくサイズ欠けなし。配置起因の見切れ）。
対応するなら「箱が画面内に収まるよう中心xを自動クランプ」だが、エディタのミニマップ表示・
ドラッグ座標のミラー実装も同時に必要なため保留。当面は長文バブルを端に寄せない運用で回避。
