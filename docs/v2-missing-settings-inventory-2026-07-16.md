# V2で設定できない／旧方式から変わった項目の棚卸し

作成日: 2026-07-16

## 結論

V2では、旧方式の項目をそのまま移植するのではなく、`instances`、`stage.enter/exit/update`、`placement`、`framing`、`cameraMotion`、`displayMode` に分解している。そのため、旧UIにあった項目は次の4種類に分ける。

1. **V2でも必要なので戻す**
2. **V2の別概念へ置き換え済み**
3. **意図的に未実装として保留**
4. **V2では廃止または非対応にする**

現時点で編集者目線の不足として残っている最重要項目は、**残りの旧演出系の扱い方針**、**V2用一括編集の再設計**、**吹き出し仕様の再整理**である。登場退場方向は `stage.enter/exit[].animation.direction`、テロップは `caption`、軽量演出の一部は `effects` として対応済み。

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
| 登場方向 `enterDir` | `stage.enter[].animation.direction` | 対応済み | `auto/left/right/up/down/instant`。旧のturn直下キーは戻さない。 |
| 退場方向 `exitDir` | `stage.exit[]` のobject形式で `animation.direction` | 対応済み | 旧 `string[]` も読み取り可能。UIで方向を設定するとobject形式へ正規化する。 |
| 登場・退場アニメ速度 | 固定0.5秒 | 保留 | まず方向だけ戻す。速度・イージング・遅延は必要になるまで増やさない。 |
| フォーカス `focusSpeaker` | `framing: speaker` へ置換 | 置換済み | 旧名では戻さない。 |
| 旧手動カメラ `manualCameraFrame` | `framing: manual` へ置換 | 置換済み | 維持。 |
| カメラ効果 `cameraEffects.zoom/pan/tilt` | `cameraMotion.zoom/pan/tilt` へ置換 | 置換済み | 維持。 |
| シェイク `shake` | `cameraMotion.shake` | 対応済み | 維持。旧 `shake` は戻さない。 |
| 構図の滑らか切替 | `cameraTransition: smooth/cut` | 対応済み | 維持。音声時刻が必要な点はUIで案内済み。 |
| トランジション `fade/wipe` | V2では `cut` のみ許可 | 保留 | 必要になるまで戻さない。場面転換演出として別契約にする。 |
| テロップ `telop/telopX/telopY/telopSize` | `caption: { text, x?, y?, size? }` | 対応済み | 旧のturn直下 `telop*` は戻さず、V2用の短い場面ラベルとして分離。既定値は `displaySettings.telop` を使う。 |
| 集中線 `impactLines` | `effects.impactLines` | 対応済み | 旧のturn直下キーは戻さず、V2用effectsへ分離。 |
| ズームパンチ `zoomPunch` | `effects.zoomPunch` | 対応済み | stageを短時間拡大し、白枠フラッシュを重ねる。 |
| 引用止め `quoteFreeze` | `effects.quoteFreeze` | 対応済み | 現在turnの本文を引用カードとして重ねる。 |
| 通知洪水 `typingFlood` | V2では設定不可 | 未決定 | ZunChat等の表示種別と役割が近い。必要時に再設計。 |
| スタンプ雨 `stampRain` | V2では設定不可 | 未決定 | 同上。 |
| キラッ `sparkleBurst` | V2では設定不可 | 未決定 | 同上。 |
| アイリスアウト `irisOut` | `effects.irisOut` | 対応済み | V2用演出レイヤーとして移植。中心クリック指定・色・閉じ始め/終わりを設定可能。 |
| 回想 `flashback` | `effects.flashback` | 対応済み | V2用画面フィルタとして移植。連続ターンでは区間として扱い、境界だけ白ディゾルブする。 |
| 映像ノイズ `visionNoise` | `effects.visionNoise` | 対応済み | V2用演出レイヤーとして移植。種類・強さ・走査線・グリッチ・ちらつき・色を設定可能。 |
| `effectSettings` のターン別上書き | V2では設定不可 | 未決定 | 旧演出を移植する場合だけ必要。 |
| 配置タブの旧手動配置 | V2の在席中状態とステージマップへ置換 | 置換済み | 旧配置タブはV2で使わない。 |
| 一括編集 | V2では危険な旧操作を流用しない | 未対応 | V2用に「scene」「slot」「framing」「speaker」など安全な対象だけ再設計する。 |
| 表示種別 monitor/chat/teamchat/mailer/videocall/whiteboard_explain | V2は `standard/whiteboard/zunMeet` のみ | 保留 | 新規表示モードは要求が出るまで追加しない。旧表示種別はそのまま戻さない。 |
| ホワイトボード | V2 `displayMode.whiteboard` | 対応済み | 維持。 |
| ZunMeet | V2 `displayMode.zunMeet` | 対応済み | 維持。 |
| BGM | story-level `bgm` をV2 rendererが読む | 対応済み | 維持。 |
| Overlay | story-level `overlays` をV2 rendererが読む | 対応済み | 維持。 |

## 「戻す」優先候補

### 1. 登場退場方向（対応済み）

編集体験として旧から明確に落ちていたため、V2でも個体単位のアニメーション契約を持つようにした。

ただし旧の `enterDir/exitDir` をturn直下には戻していない。V2では個体ごとに登場・退場するため、保存先は `stage.enter` / `stage.exit` 側にする。

現在の契約:

```ts
type StageEnterV2 = {
  instanceId: string;
  placement: PlacementV2;
  animation?: { direction?: "auto" | "left" | "right" | "up" | "down" | "instant" };
};

type StageExitV2 =
  | string
  | { instanceId: string; animation?: { direction?: "auto" | "left" | "right" | "up" | "down" | "instant" } };
```

仕様:

- UIは `自動 / 左 / 右 / 上 / 下 / 即時` を出す。
- `auto` は上下を自動選択せず、旧に近い左右自動にする。
- 登場はturn冒頭、退場はturn末尾に0.5秒で動かす。
- 速度・イージング・遅延などの追加パラメータは保留。

### 2. テロップ（対応済み）

旧のテロップは単純で、V2にも戻しやすい。ただしOverlayと役割が重なるため、V2では時間範囲overlayへ統合せず、turnに紐づく短い場面ラベルとして `caption` を追加した。

現在の契約:

```ts
type CaptionV2 = {
  text: string;
  x?: number;
  y?: number;
  size?: number;
};
```

仕様:

- UIは基本タブの台詞表示セクションに置く。
- 位置・サイズ未指定時は `displaySettings.telop` を使う。
- 連続するcaptionターンは1ブロックとして扱い、境界でフェードがちらつかないようにする。
- 旧の `telop/telopX/telopY/telopSize` はV2 schemaには追加しない。旧台本からV2へ変換する時に `caption` へ寄せる。

### 3. 旧演出系

集中線、ズームパンチ、引用止め、回想、映像ノイズ、アイリスアウトは、V2の `effects` として対応済み。

```ts
type StageEffectsV2 = {
  impactLines?: EffectToggleV2<ImpactLinesEffectV2>;
  zoomPunch?: EffectToggleV2<ZoomPunchEffectV2>;
  quoteFreeze?: EffectToggleV2<QuoteFreezeEffectV2>;
  flashback?: EffectToggleV2<FlashbackEffectV2>;
  visionNoise?: EffectToggleV2<VisionNoiseEffectV2>;
  irisOut?: EffectToggleV2<IrisOutEffectV2>;
};
```

通知洪水、スタンプ雨、キラッは、旧rendererに強く結びついている。

推奨:

- 残りを全部一括で戻さない。
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
- 登場退場アニメーションの詳細速度・イージング・遅延
- 3人以上の自動構図
- ニュース表示
- 再現VTR
- 前景の後ろに人物を置く高度なレイヤー指定

これらは素材能力や表示種別の契約が必要なので、今の1〜2人会話劇編集では無理に入れない。

## 次に決めるべきこと

1. 残りの旧演出系のうち、次に戻すものを選ぶか。
2. V2用一括編集で必要な対象を決めるか。
3. 吹き出し最大文字数・横幅・段数の仕様を再定義するか。

## 実装順の推奨

1. 登場退場方向の仕様決定（完了）
2. 登場退場方向のUI・schema・renderer実装（完了）
3. テロップの扱いを決定（完了）
4. 旧演出系を必要なものだけ順に移植
5. V2用一括編集を新設

吹き出し仕様は別件として扱う。文字数・横幅・段数・連結は混ざりやすいため、この棚卸しとは分離する。
