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

- 吹き出し幅見積もり(`bubbleMetrics`)の`maxWidth`引数が使われておらず、読点の無い長い1文だと吹き出しが画面外にはみ出る(turn36/40相当で実際に確認済み)。折り返し方針(自動改行 or 縮小)を決めてから対応。
