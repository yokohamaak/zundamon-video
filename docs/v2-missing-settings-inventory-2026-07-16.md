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
| 一括編集 | V2用パネルを新設（scene / speaker / framing / caption / effects のON・OFF） | 対応済み | 適用前に `v2TurnOrderError` で舞台整合を検証し、壊れる変更は拒否する。scene一括は単体編集と同じ規則でstageを削除（確認あり）。 |
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

1. 残りの旧演出系（typingFlood / stampRain / sparkleBurst）のうち、次に戻すものを選ぶか。
2. 吹き出し最大文字数・横幅・段数の仕様を再定義するか。

## 実装順の推奨

1. 登場退場方向の仕様決定（完了）
2. 登場退場方向のUI・schema・renderer実装（完了）
3. テロップの扱いを決定（完了）
4. 旧演出系を必要なものだけ順に移植
5. V2用一括編集を新設（完了・2026-07-16）

吹き出し仕様は別件として扱う。文字数・横幅・段数・連結は混ざりやすいため、この棚卸しとは分離する。

## 2026-07-16 追記: 対応済みになった項目

`docs/v2-old-new-comparison-review-2026-07-16.md` の優先残課題のうち、以下を実装した。

- AI台本生成のV2対応: `_build_script_prompt_v2` がV2 schemaを案内し、`_script_prompt_builder()` が台本のschemaで切替。V2編集中の旧形式取込は `_import_script_text` が保存前に拒否する。
- V2一括編集: 上表のとおり新設。
- 吹き出し細部の旧挙動回帰: stacked時フォント縮小・単発の浮かせ(36/12px)・padding 28px・ズーム時の上移動を `StageVideoV2.tsx` へ移植。カメラsmooth遷移中は吹き出し位置を補間（旧のfollowKはエンファシス専用の重みだったため、遷移追従はV2独自の改善として採用）。
- 配置変更の移動補間: placementがターン間で変わった個体は旧と同じ0.6秒easeで補間移動。
- idleFace=normal の表情リセットを「イベント適用前・毎ターン」に統一（stage-v2.ts）し、エディタの `v2StateAt` にも同期。

## 2026-07-16 追補: シーンエディタの棚卸し

この文書の初版はストーリーエディタのターン設定だけを対象にしており、**シーンエディタ（[scene_editor.html](/Users/yokohamaak/ap/zundamon-video/scene_editor.html)）は未調査だった**。V2では `syncV2LayoutUi()` が旧UIをまとめて非表示にする（同関数内の `style.display = v2 ? 'none' : ''` 群）。

V2シーンの許可キーは [stage_schema.py](/Users/yokohamaak/ap/zundamon-video/stage_schema.py) の `validate_scene_library_v2()` が正。トップレベルは `schemaVersion` / `scenes` のみで、シーンは `label` / `bg` / `bgVideo` / `bgVideoLoop` / `bgBlur` / `front` / `figure` / `layouts` / `cameraPresets` のみ。

| 旧項目 / 旧操作 | V2での現状 | 判定 | 今後の方針 |
|---|---|---|---|
| 背景ブラー `bgBlur` | V2シーンキーへ復活済み | 対応済み | `713f076a` で対応。 |
| 前景オーバーレイ `front` | V2シーンキーにあり、UIも表示される | 対応済み | 維持。 |
| シーン基本カメラ枠 | `cameraPresets`（`cx`/`cy`/`width`）へ置換。V2では構図の追加・改名・削除UIも出る | 置換済み | 維持。 |
| 立ち絵スケール | `layouts.standard.slots[].scale`（スロット単位） | 置換済み | シーン一律ではなくスロットごとに持つ。旧UIは戻さない。 |
| キャスト割当 `cast` | `instances`（台本側） | 置換済み | シーンではなく台本が登場人物を持つ。旧UIは戻さない。 |
| アンカー座標 | `layouts.standard.slots`（「通常表示の配置スロット」UI） | 置換済み | 維持。 |
| モブ配置 `mobs` | モブも `instances`（`characterId` が mobs.json を指す）＋ `placement` | 置換済み | シーン単位の既定配置は持たない。旧UIは戻さない。 |
| **カメラ共通設定（全シーン共通）**<br>ズーム量/時間・パン量/時間・傾き角度/時間・slow-zoomドリフト | **保存先ごと消滅**。`story-scenes.json` トップレベル `camera` を `validate_scene_library_v2()` が拒否する（許可は `schemaVersion` / `scenes` のみ）。現行ファイルにも `camera` キーは無い | 未決定 | V2はターン単位 `stage.cameraMotion` に実数値を直接書く方式なので「全シーン共通の既定量」という概念自体が無い。ただし後述の slow-zoom ドリフトだけは受け皿が無い。 |
| **カメラモード `camera: "static" / "slow-zoom"`** | V2シーンキーに無く保存不可 | 未決定 | renderer側は生きている（[StoryVideo.tsx](/Users/yokohamaak/ap/zundamon-video/video/src/StoryVideo.tsx) の `camera?: "static" \| "slow-zoom"` と `slowZoomDrift`）。区間中ずっと微速プッシュインする演出で、ターン単位の `cameraMotion` では代替できない。戻すならシーンキーとして復活させる。 |
| **ズームの強さ `focusZoom`（emphasis時の加算倍率）** | V2シーンキーに無く保存不可 | 未決定 | renderer側は生きているが、そもそも旧 `emphasis` 専用の調整値。`emphasis` をV2へ戻さないなら不要。 |
| **ズーム 顔からの縦オフセット `focusDy`** | V2シーンキーに無く保存不可 | 未決定 | 同上。 |

### 判断が要る点

`camera: "slow-zoom"` は「シーン全体にずっとかかる微速ズーム」で、V2の `stage.cameraMotion`（ターン単位の一時演出）とは別物。renderer には残っているので、**戻すならシーンキー、捨てるなら renderer 側も落とす**、のどちらかを決める必要がある。`focusZoom` / `focusDy` は旧 `emphasis` に従属するため、`emphasis` の扱いとセットで決める。

## 2026-07-17 追補: メニュー系画面の棚卸し

ここまでの棚卸しは台本画面（ターン設定・一括編集）とシーンエディタが対象だった。この追補では「≡ メニュー」から開く全画面とヘッダー操作を対象に、V2台本編集中の対応状況を調べた。

| メニュー画面 / 操作 | V2での現状 | 判定 | 今後の方針 |
|---|---|---|---|
| シーン編集 | 上の「シーンエディタの棚卸し」参照 | 調査済み | 同上。 |
| 表情編集 | パーツ・腕・fx等は `Avatar.tsx` 共通なのでV2でも有効 | 対応済み | 維持。 |
| **表情編集の「聞き役時の表情（引き継ぎ先 holdAs）」** | 旧rendererのみ参照。`stage-v2.ts` / `StageVideoV2.tsx` に `holdAs` の参照が無い | **未対応** | V2の聞き役は `idleFace: hold/normal` の2択のみ。表情ごとの引き継ぎ先（旧の surprise/panic→normal 既定含む）を移植するか、V2では廃止と明記するか要判断。 |
| ポーズ編集 | `poses.json` の arm/speed/strength を `StageVideoV2` が読む（`poseArmStem` 等） | 対応済み | 維持。 |
| 台本生成（プロンプト生成・AI取込） | V2プロンプト・取込ガード・実験版（`_proposals`）ともV2分岐あり | 対応済み | 維持。日本語表示は話者を個体IDのまま表示し（`instances.label` 不使用）、`displayMode` の「画面：」表示が無い（旧 `insert` のみ参照）。軽微。 |
| 登場人物 | V2専用画面。表示名・素材・声の変更、参照チェック付き削除まで実装。旧schema時は案内文を出して無効化 | 対応済み | 維持。 |
| 音: プレビュー再生 | プレイヤー経由でV2でも再生される | 対応済み | 維持。 |
| 音: 声 共通設定 | `voice_profiles.json` 編集は共通で、instancesの `voiceId`（派生プリセット含む）もここが正 | 対応済み | 説明文「各ターンの speed / pitch / intonation が空欄の場合…」はV2で廃止済みのターン単位上書きへの言及で、文言だけ旧仕様の残骸。 |
| **音: SE自動マッピング** | V2 `V2SeLayer` が読むのは `expression.*` と `effect.shake` のみ | **大半が死に設定** | `effect.flashback`（V2に演出はあるがSEトリガー未実装）、`effect.emphasis` / `insert.*` / `transition.*`（V2に概念が無い）は編集できても鳴らない。V2で効く項目だけ表示するか、注記を出す。 |
| 音: 読み替え辞書 / 漢字の読み修正 | 音声生成側の処理でschema非依存 | 対応済み | 維持。 |
| モブキャラ | 定義編集（`mobs.json`）は共通 | 対応済み | 維持。poseが描画未使用の件は既出。 |
| 表示共通設定: 吹き出し・字幕・話者色 | `StageVideoV2` が `displaySettings` を読む | 対応済み | 維持（吹き出し細部のデグレは既出）。 |
| **表示共通設定: テロップ既定値** | `collectDisplaySettings()` がV2では `telop` を書かない（story_editor.html の `if (!isV2StoryEditor())` ガード）。一方 schema（`_display_settings`）と renderer（caption既定値）は `displaySettings.telop` を受け付ける | **同期漏れバグ** | V2でこの画面を編集すると、テロップ欄の入力が無効なだけでなく、既存の `displaySettings.telop` が黙って消える（displaySettings全体を再構築して代入するため）。caption既定値の受け皿は生きているので、ガードを外すのが正しい可能性が高い。 |
| **表示共通設定: 見本プレビュー** | `buildDisplayPreviewStory()` が旧schema形turn（`enter`/`expression`/`pose` 直置き、`stage` なし）を生成。storyDataをspreadするため `schemaVersion: 2` のままV2 rendererへ渡る | **未対応** | V2ではキャラが登場しないプレビューになる（`stage.enter` が無いため在席ゼロ。吹き出し・字幕のみ確認可能）。話者選択「その他」も voice_profiles の話者名を使い instances 非対応。V2形のプレビューturn生成が必要。 |
| **演出共通設定** | (a) `validate_story_v2()` が story直下 `effectSettings` を拒否（許可キーに無い・実測でエラー確認済み）。(b) 仮に保存できてもV2 rendererは story-level effectSettings を読まない（`DEFAULT_STAGE_EFFECT_SETTINGS` ハードコード）。(c) スペックの過半（entrance/emphasis/stampRain/typingFlood/sparkleBurst）はV2に存在しない演出 | **保存不能の事故導線** | V2編集中にこの画面でスライダーを既定値以外へ動かすと `storyData.effectSettings` が作られ、以後の保存が「story: 未対応の項目があります: effectSettings」で失敗する。V2では画面ごと隠すのが最小対処。共通既定値の管理をV2で持つなら別設計（既定値焼き込み問題とセット）。 |
| エクスポート | storyDataをそのまま書き出し | 対応済み | 維持。 |
| **インポート（JSONファイル）** | `importStoryFromFile()` は speaker/text/scene の形しか見ず、schema判定・警告なし | **旧schema上書きの事故導線** | V2編集中に旧schema JSONを取り込むとUIごと旧モードに切り替わり、保存すると story-01.json が旧schemaで上書きされる（サーバ側も schemaVersion 無しなら旧として受理）。AI取込側は2026-07-16にガード済みだが、この経路は未ガード。同等のブロック/警告が必要。 |
| ヘッダー: 聞き役表情 / キャッシュ無視 / 保存 / 音声生成 / 書き出し | V2対応済み（`idleFace` はV2 schema許可キー） | 対応済み | 維持。 |
| タイムライン: トランジション編集 | `transitionOptionsHtml()` がV2ではcutのみ提示 | 対応済み | 維持。 |

### 優先度

1. **演出共通設定**: 触ると保存不能になる。V2では画面を隠す or 開いた時に警告（最小差分は画面非表示）。
2. **インポート**: V2台本を旧schemaで上書きする事故導線。AI取込と同じガードを入れる。
3. **表示共通設定のテロップ既定値**: 編集無効＋既存値の黙った削除。ガード撤去で直る見込み（schema/rendererは対応済み）。
4. **見本プレビューのV2対応**: V2形turnの生成に置き換える。
5. SE自動マッピングの死に設定の整理、表情編集 holdAs の方針決定、声共通設定の説明文修正は次点。

### 2026-07-17 追記: 対応結果

上記はすべて同日中に対応した。

- 演出共通設定の非表示化・インポートガード・テロップ既定値ガード撤去・声共通設定の文言修正・初期ロード時のV2 UI同期: `80bfed06`
- 見本プレビューのV2対応: `buildDisplayPreviewStoryV2()` を新設。見本用instancesを合成し、シーンの右寄りslot（無ければmanual配置）で `stage.enter` する。稼働エディタでキャラ表示を確認済み。
- SE自動マッピング: ユーザー判断で**表情連動のみ残し他は廃止**。音画面のUIセクションを削除し、`V2SeLayer` から shake トリガーを削除（表情トリガーとse-map.json・旧renderer・/api/se-map は温存）。表情エディタへの表情連動SE編集UIの移設は ROADMAP.md に記録。
- holdAs（聞き役時の表情）: ユーザー判断で**V2では廃止**。表情エディタのUIを削除。expressions.json 内の既存 holdAs データは旧renderer互換のため温存（保存してもデータは消えない方式を確認済み）。

## 2026-07-16 追補: 一括編集で旧から落ちている操作

上表では一括編集を「対応済み」としたが、V2用パネルを旧と突き合わせると次の4つは受け皿が無い。旧の適用関数は [story_editor.html](/Users/yokohamaak/ap/zundamon-video/story_editor.html) 側で `if (isV2StoryEditor()) return;` により無効化されている。

| 旧の一括操作 | V2での現状 | 判定 | 今後の方針 |
|---|---|---|---|
| シーン | `v2ApplyBulkScene()` | 対応済み | 維持。 |
| 話者フォーカス `applyBulkFocusSpeaker` | 構図一括の「話者を主役にする」/「構図指定を削除」でカバー | 置換済み | 旧名では戻さない。 |
| **表情 `applyBulkExpression`** | 無効化。V2一括に表情が無い | 未決定 | V2の表情は `stage.update.<id>.expression` で個体単位のため、一括では対象個体の指定方法を決める必要がある。 |
| **カメラ演出 `applyBulkCameraEffect`**（ズーム/パン/傾き/揺れ） | 無効化。V2一括は `effects` 6種のON/解除のみで `cameraMotion` を扱えない | 未決定 | 効果（effects）とカメラ動き（cameraMotion）はV2で別概念になったが、一括は effects 側しか用意していない。 |
| **手動カメラ枠 `applyBulkManualCamera`** | 無効化。構図一括は `speaker` / `sceneDefault` / `clear` のみで `manual` が無い | 未決定 | ターン単位では framing `manual` で設定できる。一括に必要かは要判断。 |
| **テロップ位置・サイズ `applyBulkTelop`** | 部分。V2一括の caption は文字のみで `x` / `y` / `size` が無い | 未決定 | ターン単位では `fV2CaptionX/Y/Size` で設定できる。 |
| 声 `applyBulkVoice` | 無効化。個別音声上書き自体をV2で廃止 | 廃止寄り | 上表の方針どおり戻さない。 |
