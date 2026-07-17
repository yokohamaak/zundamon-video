# ロードマップ / 今後やること

## 台本編集(story_editor.html) - 複数選択での一括編集

シーンの一括変更は実装済み(`selectedIndices` + シーン一括適用バー、Shift/Ctrl(⌘)クリックで複数選択)。
同じ仕組みに乗せて、以下のフィールドも一括変更できるようにする候補:

- 表情(expression)
- ポーズ(pose)
- 口パクOFF(noLipSync)
- シェイク/回想(shake/flashback)のON・OFF一括切り替え
- カメラ演出(cameraEffect)
- 話者(speaker) ※吹き出し/立ち絵も変わるため要注意
- セリフ後の間(pause)の一括数値変更
- 選択ターンの一括削除

## 未対応の既知バグ・改善候補

- シーンエディタの16:9カメラ枠と StoryVideo 側の「自動寄せ」の責務が混ざりやすい。
  `story_editor` 側にある自動寄せON/OFFを前提に、キャラの立ち位置は変えず構図だけを調整する形へ見直す。
- 表情選択(`fExpression`)・ポーズ選択(`fPose`)は、シーン/話者と違いファイル実在チェックをしていない。
  表情はキャラごとにパーツ画像(brow/cheek/eye/mouth等)の組み合わせなので単純な1ファイルチェックでは
  済まず、かつ現在の実装はキャラ非依存の単一グローバル一覧(`_buildExpressionOptions`/`buildPoseSelect`)
  のため、ターン選択中の話者に応じて選択肢を絞り込む作りへの変更も必要。対応する場合は
  expression_editor.py/pose_editor.pyの`_build_catalog()`(スロット単位でos.path.isfileチェック済み)を
  流用する方針で検討する。
