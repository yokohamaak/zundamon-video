# V2で設定できない／旧方式から変わった項目の棚卸し

作成日: 2026-07-16

## 結論

V2では、旧方式の項目をそのまま移植するのではなく、`instances`、`stage.enter/exit/update`、`placement`、`framing`、`cameraMotion`、`displayMode` に分解している。そのため、旧UIにあった項目は次の4種類に分ける。

1. **V2でも必要なので戻す**
2. **V2の別概念へ置き換え済み**
3. **意図的に未実装として保留**
4. **V2では廃止または非対応にする**

現時点で編集者目線の不足として残っている最重要項目は、**登場退場方向**、**旧演出系の扱い方針**、**V2用一括編集の再設計**、**吹き出し仕様の再整理**である。

## 現在のV2基本契約

コード上のV2データ契約は [video/src/stage-v2.ts](/Users/yokohamaak/ap/zundamon-video/video/src/stage-v2.ts) にある。

- 台本全体の登場人物は `instances`
- 各ターンの話者は `speaker`、ただし `instances` のIDを参照する
- 登場は `stage.enter`
- 退場は `stage.exit`
- 表情・ポーズ・向き・配置は `stage.update`
- 配置は `placement: slot | manual`
- 構図は `stage.framing`
- 一時的なカメラ演出は `stage.cameraMotion`
- 表示種別は `displayMode`

保存検証は [stage_schema.py](/Users/yokohamaak/ap/zundamon-video/stage_schema.py) の `validate_story_v2()` が行う。V2 turn直下で許可されるキーは限定されており、旧方式の `speakerAnchor`、`enterDir`、`exitDir`、`cameraEffects`、`telop`、`voice` などは通さない。

## 棚卸し表

| 旧項目 / 旧操作 | V2での現状 | 判定 | 今後の方針 |
|---|---|---|---|
| 話者 `speaker` | `fV2Speaker` で `instances` のIDを選ぶ | 置換済み | 維持。音声は `instances.<id>.voiceId` へ解決する。 |
| ナレーション / 声だけ `narrationVoice` | `voiceOnly` instance を話者にする | 置換済み | 維持。UI文言は「声のみ」で統一する。 |
| 個別音声上書き `voice.speed/pitch/intonation` | V2では禁止。`make_story_audio.py` が拒否する | 廃止寄り | ターン単位では戻さない。必要なら `instances` 側または声プリセット管理として別設計する。 |
| セリフ本文 | `fV2Text` | 対応済み | 維持。 |
| セリフ分割 | `btnV2SplitDialogue` | 対応済み | 維持。後半turnへstageイベントを複製しない方針。 |
| 間 `pause` | `fV2Pause` | 対応済み | 維持。 |
| 口パク停止 `noLipSync` | `fV2NoLipSync` | 対応済み | 維持。 |
| 吹き出し非表示 `hideBubble` | `fV2HideBubble` | 対応済み | 維持。 |
| キャラ非表示 `hideCharacters` | `fV2HideCharacters` | 対応済み | 維持。ただし在席と可視性の違いをUIで誤解させない。 |
| 吹き出し連結 `continueBubble` | `fV2ContinueBubble` | 対応済み | 維持。複数段配置は旧挙動へ寄せた。 |
| 自動分割無効 `disableAutoBubbleSplit` | `fV2DisableAutoBubbleSplit` | 対応済み | 維持。 |
| 吹き出し最大文字数 `bubbleMaxChars` | `fV2BubbleMaxChars` | 要再整理 | 後回し。文字数、横幅、行数、連結時の意味を再定義してから触る。 |
| 字幕帯 `subtitleMode` | `fV2SubtitleMode` | 対応済み | 維持。 |
| ターン別字幕スタイル | `v2SubtitleStyleControls` | 対応済み | 維持。 |
| 手動SE `se` | `fV2SeList` | 対応済み | 維持。 |
| シーン `scene` | `fV2Scene` | 対応済み | 維持。scene変更時はstageを消す。 |
| 立ち位置 `speakerAnchor` | slot選択、manual placementへ置換 | 置換済み | 旧名では戻さない。V2では在席中の人物ごとにslot/offset/manualを設定する。 |
| 表情 `expression` | `stage.update.<id>.expression` | 対応済み | 維持。 |
| ポーズ `pose` | `stage.update.<id>.pose`、プルダウン化済み | 対応済み | 維持。モブでは現状描画に使わない。 |
| 向き `face` | `left/right` のみ | 部分対応 | 後ろ姿・座り姿は保留。素材能力モデルが必要。 |
| 向き保持 `faceMode` / `clearFace` | V2では状態継承とresetで扱う | 置換済み | 旧UIは戻さない。必要なら「状態をリセット」を改善する。 |
| 左右反転 `flip` | `stage.update.<id>.flip` | 対応済み | 維持。 |
| 登場 `enter` | `stage.enter` | 対応済み | 維持。話者選択では自動登場しない。 |
| 退場 `exit` | `stage.exit` | 対応済み | 維持。 |
| 登場方向 `enterDir` | V2では設定不可 | 戻す候補 | 旧のturn直下ではなく、`stage.enter` の animation/direction として設計する。 |
| 退場方向 `exitDir` | V2では設定不可 | 戻す候補 | `stage.exit` が今は `string[]` なので、追加時はスキーマ変更が必要。 |
| 登場・退場アニメ速度 | V2では設定不可 | 保留 | directionを戻す時に一緒に契約を決める。 |
| フォーカス `focusSpeaker` | `framing: speaker` へ置換 | 置換済み | 旧名では戻さない。 |
| 旧手動カメラ `manualCameraFrame` | `framing: manual` へ置換 | 置換済み | 維持。 |
| カメラ効果 `cameraEffects.zoom/pan/tilt` | `cameraMotion.zoom/pan/tilt` へ置換 | 置換済み | 維持。 |
| シェイク `shake` | `cameraMotion.shake` | 対応済み | 維持。旧 `shake` は戻さない。 |
| 構図の滑らか切替 | `cameraTransition: smooth/cut` | 対応済み | 維持。音声時刻が必要な点はUIで案内済み。 |
| トランジション `fade/wipe` | V2では `cut` のみ許可 | 保留 | 必要になるまで戻さない。場面転換演出として別契約にする。 |
| テロップ `telop/telopX/telopY/telopSize` | V2では設定不可 | 未決定 | 旧と同じ単純テロップを戻すか、overlayへ統合するか決める。 |
| 集中線 `impactLines` | V2では設定不可 | 未決定 | 旧演出をV2へ移植するか、演出共通のoverlay系へ寄せるか決める。 |
| ズームパンチ `zoomPunch` | V2では設定不可 | 未決定 | 同上。 |
| 引用止め `quoteFreeze` | V2では設定不可 | 未決定 | 同上。 |
| 通知洪水 `typingFlood` | V2では設定不可 | 未決定 | ZunChat等の表示種別と役割が近い。必要時に再設計。 |
| スタンプ雨 `stampRain` | V2では設定不可 | 未決定 | 同上。 |
| キラッ `sparkleBurst` | V2では設定不可 | 未決定 | 同上。 |
| アイリスアウト `irisOut` | V2では設定不可 | 未決定 | 場面転換/演出レイヤーとして設計する。 |
| 回想 `flashback` | V2では設定不可 | 未決定 | 画面全体フィルタとしてV2に移植するか判断が必要。 |
| 映像ノイズ `visionNoise` | V2では設定不可 | 未決定 | 画面全体フィルタとしてV2に移植するか判断が必要。 |
| `effectSettings` のターン別上書き | V2では設定不可 | 未決定 | 旧演出を移植する場合だけ必要。 |
| 配置タブの旧手動配置 | V2の在席中状態とステージマップへ置換 | 置換済み | 旧配置タブはV2で使わない。 |
| 一括編集 | V2では危険な旧操作を流用しない | 未対応 | V2用に「scene」「slot」「framing」「speaker」など安全な対象だけ再設計する。 |
| 表示種別 monitor/chat/teamchat/mailer/videocall/whiteboard_explain | V2は `standard/whiteboard/zunMeet` のみ | 保留 | 新規表示モードは要求が出るまで追加しない。旧表示種別はそのまま戻さない。 |
| ホワイトボード | V2 `displayMode.whiteboard` | 対応済み | 維持。 |
| ZunMeet | V2 `displayMode.zunMeet` | 対応済み | 維持。 |
| BGM | story-level `bgm` をV2 rendererが読む | 対応済み | 維持。 |
| Overlay | story-level `overlays` をV2 rendererが読む | 対応済み | 維持。 |

## 「戻す」優先候補

### 1. 登場退場方向

編集体験として旧から明確に落ちている。V2では現在、登場退場は状態変更だけで、アニメーション契約を持たない。

ただし旧の `enterDir/exitDir` をturn直下に戻すのは避ける。V2では個体ごとに登場・退場するため、保存先は `stage.enter` / `stage.exit` 側が自然。

検討案:

```ts
type StageEnter = {
  instanceId: string;
  placement: PlacementV2;
  animation?: { type: "slide" | "instant"; from?: "left" | "right" };
};

type StageExit =
  | string
  | { instanceId: string; animation?: { type: "slide" | "instant"; to?: "left" | "right" } };
```

懸念:

- `exit` は現状 `string[]` なので互換形にするか、別キーにするか決める必要がある。
- 方向を「画面端基準」「stage基準」「カメラ後の見た目基準」のどれにするか決める必要がある。

推奨:

- まず `instant / auto / left / right` だけ戻す。
- 方向は旧と同じく画面端基準にする。
- V2内部では個体単位のanimationとして保存する。

### 2. テロップ

旧のテロップは単純で、V2にも戻しやすい。ただしOverlayと役割が重なる。

推奨:

- すぐ戻すなら `turn.telop` 系ではなく、V2用の軽い `displayMode` 補助または `overlay` 生成補助として扱う。
- 旧のturn直下キーをV2 schemaへ安易に追加しない。

### 3. 旧演出系

集中線、ズームパンチ、引用止め、通知洪水、スタンプ雨、キラッ、アイリスアウト、回想、映像ノイズは、旧rendererに強く結びついている。

推奨:

- 全部一括で戻さない。
- よく使うものからV2 rendererへ個別に移植する。
- `effectSettings` も同時に移植対象を絞る。

## 「戻さない」方がよいもの

### speakerAnchor

V2のslot/placementと責務が重複する。戻すと、anchor問題を再発させる。

### focusSpeaker

V2では `framing: speaker` が同じ役割。旧名で戻す必要はない。

### manualCameraFrame

V2では `framing: manual` が同じ役割。旧名で戻す必要はない。

### turn.voice

V2では話者個体の `voiceId` を使う。ターン単位の声パラメータ上書きを戻すと、`instances` と音声管理の責務が混ざる。

必要なら別途「このターンだけ声色」機能として設計する。

## 意図的に保留するもの

- 後ろ姿
- 座り姿
- 素材ごとの接地点メタデータ
- 登場退場アニメーションの詳細速度・イージング
- 3人以上の自動構図
- ニュース表示
- 再現VTR
- 前景の後ろに人物を置く高度なレイヤー指定

これらは素材能力や表示種別の契約が必要なので、今の1〜2人会話劇編集では無理に入れない。

## 次に決めるべきこと

1. 登場退場方向をV2へ戻すか。
2. 戻す場合、`stage.exit` を互換形にするか、別キーにするか。
3. テロップをV2へ戻すか、Overlayへ統合するか。
4. 旧演出系のうち、最初に戻すものを選ぶか。
5. V2用一括編集で必要な対象を決めるか。

## 実装順の推奨

1. 登場退場方向の仕様決定
2. 登場退場方向のUI・schema・renderer実装
3. テロップの扱いを決定
4. 旧演出系を必要なものだけ順に移植
5. V2用一括編集を新設

吹き出し仕様は別件として扱う。文字数・横幅・段数・連結は混ざりやすいため、この棚卸しとは分離する。

