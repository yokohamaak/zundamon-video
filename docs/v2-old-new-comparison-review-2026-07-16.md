# zundamon-story 旧方式 / V2方式 新旧比較レビュー

作成日: 2026-07-16
対象: `run-story` 系（旧: `story_editor.html` 旧UI + `video/src/StoryVideo.tsx` / V2: `story_editor.html` V2 UI + `stage_schema.py` + `video/src/stage-v2.ts` + `video/src/StageVideoV2.tsx`）
対象外: `legacy-dialogue/`（凍結）
性質: コード調査・仕様比較のみ。実装・修正は行っていない。

## 1. エグゼクティブサマリー

**V2は設計目標（責務分離・差分の明確化・不正状態の排除）をほぼ達成しており、データモデル・validator・状態解決は旧方式より明確に良い。** 旧方式最大の問題だった「anchorの多重責務」「話者の暗黙登場」「フィールドごとにバラバラな継承境界」「設定できるが描画で無視される状態」は、`instances` / `stage.enter/exit/update/reset` / `placement(slot|manual)` / `framing` / `cameraMotion` / `displayMode` への分解と `validate_story_v2()`（`stage_schema.py`）で構造的に解消された。

一方で、**編集フロー全体としては旧に劣る箇所が残る**。最重要は次の3点。

1. **AI台本生成フローがV2に未対応**（`story_editor.py` `_build_script_prompt` は旧schemaのみを案内）。現在の `story-01.json` はすでに `schemaVersion: 2` なので、生成→取込の運用が断絶している。
2. **一括編集が全面無効**（`updateBulkSceneBar` / `applyBulk*` がV2で早期return）。80〜100ターン編集の主要効率化手段が失われている。
3. **吹き出し・キャラ移動の細部デグレ**（stacked時フォント縮小なし、ズーム時の吹き出し追従なし、配置変更の0.6秒補間消失など）。過去にデグレが繰り返された領域であり、意図的な簡略化か漏れかの判定が必要。

結論として **V2を主軸に進めてよい**。残課題は設計上の欠陥ではなく、ほぼ「移植漏れ・未着手」の範疇にある。

## 2. 旧方式の概要

- 台本は turn 配列。必須は `speaker/text/scene`、他は任意フィールドをturn直下へ平置き（`StoryVideo.tsx` `StoryTurn` 型、約50キー）。
- 在席は `enter/exit` + **話者の暗黙登場**の合成。`visible` は存在せず、`hideCharacters`・インサート・モブのscene設定から逆算。
- 配置は `assignAnchors`（人数自動）→ `scene.cast` → `speakerAnchor` → `manualPos` の4層上書き。anchor名（left/right）が `focusSpeaker` のカメラ選択条件を兼ねる。
- カメラは `focusSpeaker` / `manualCameraFrame` / `emphasis` / `cameraEffects`+`cameraEffectSettings` / `zoomTarget` / `cameraCenter` / scene側 `cameraFrames`・slow-zoom の重ね合わせ。
- 表示種別は `insert.kind`（7種: warning/ok/chat/teamchat/mailer/videocall/whiteboard_explain）。
- 演出はturn直下キー（`shake/flashback/visionNoise/impactLines/zoomPunch/quoteFreeze/stampRain/typingFlood/sparkleBurst/irisOut/telop*`）+ `effectSettings` の「動画全体→ターン上書き」2層マージ。
- 保存検証は `story_editor.py` `_validate_story` の最低限（必須文字列とinsertの形だけ）。
- 継承境界は `buildSegments()` の同一scene連続区間だが、表情・向きはscene跨ぎで全探索、`focusSpeaker` はターンのみ、と**フィールドごとに規則が違う**。

## 3. V2方式の概要

- `schemaVersion: 2` 必須。登場人物は story直下の `instances`（`characterId` / `voiceId` / `role: stage|voiceOnly` / `label`）。主役・モブ・声のみを同一モデルで扱う（`stage-v2.ts` `StoryInstanceV2`）。
- turnは差分イベントのみ持つ: `stage.enter[]`（placement+animation.direction）、`stage.exit[]`、`stage.update{}`（placement/expression/pose/face/flip/zIndex）、`stage.reset[]`、`stage.framing`、`stage.cameraMotion`。
- 状態解決は純粋関数 `resolveStageStateAtTurn()`（`stage-v2.ts:336`）に一元化。**scene変更で全状態リセット**という単一の継承境界。
- 配置は `placement: {mode:"slot", slotId, offset?} | {mode:"manual", origin, scale?, zIndex?}`。slotは scene定義 `layouts.standard.slots`（origin/scale/zIndex/cameraPresetId/allowOverlap/previewCharacterId）。
- カメラは構図 `framing: sceneDefault|speaker|slot|manual` と、非継承の演出 `cameraMotion: {zoom/pan/tilt/shake}` に分離。ターン間接続は `cameraTransition: smooth|cut`。
- 表示種別は `displayMode: standard | whiteboard | zunMeet`。
- 演出は `turn.effects` に集約（impactLines/zoomPunch/quoteFreeze/flashback/visionNoise/irisOut、boolean/object両対応）。テロップは `caption: {text,x?,y?,size?}`。
- 保存検証は `stage_schema.py` `validate_story_v2()` / `validate_scene_library_v2()`。未知キー拒否・参照検証・在席整合・slot重複・数値範囲まで検証。`test_stage_schema.py` に30本超の回帰テスト。
- 描画は `StageVideoV2.tsx`。`StoryVideoRouter.tsx` が `schemaVersion === 2` でV2 rendererへ振り分け、旧台本は旧rendererのまま。
- 音声は `make_story_audio.py` が `speaker`（個体ID）→ `instances.<id>.voiceId` を解決。turn単位の `voice` 上書きは明示的に拒否（`make_story_audio.py:156-166`）。

## 4. 旧→V2で改善された点

| # | 改善 | 根拠 |
|---|---|---|
| 1 | **状態解決の一元化**。旧は `entranceTimesFor` / `lastExpressionOf` / `manualPosWaypoints` / `resolveAnchorMapAt` 等に分散していた遡り処理が、`resolveStageStateAtTurn()` 1関数の線形リプレイに集約 | `stage-v2.ts:336-404` |
| 2 | **継承境界の統一**。「scene変更で全リセット、それ以外は継承、cameraMotionだけ非継承」という3行で説明できる規則になった | `stage-v2.ts:355-365` |
| 3 | **暗黙登場の廃止**。`enter` なしの話者は登場しない。validatorが「在席していない個体の退場/更新」「二重登場」を保存時に拒否 | `stage_schema.py:624-666`、`test_speaker_does_not_implicitly_enter` |
| 4 | **個体モデル**。主役/モブ統一、同一キャラ複数個体（`test_same_character_can_have_multiple_instances`）、`voiceOnly` の分離と「登場不可」の強制 | `stage_schema.py:539-558,633-634` |
| 5 | **anchorのカメラ意味排除**。カメラヒントは `slot.cameraPresetId` の明示属性になり、「left という名前だから leftFocus」という名前依存が消えた | `stage-v2.ts:419-437` `resolveFraming` |
| 6 | **framing / cameraMotion の分離**。構図（継承）と一時演出（非継承）が別責務。カメラ経路が旧の5系統から2系統に減った | `stage-v2.ts:152-163` |
| 7 | **validatorの大幅強化**。未知キー拒否（旧turn直下キーの混入を検出）、色・数値範囲・slot/instance/preset参照・slot重複+zIndex要求・モブ表情素材の実在検証 | `stage_schema.py` 全体、`test_v2_rejects_legacy_fields_instead_of_ignoring_them` |
| 8 | **「設定したのに描画で化ける」経路の縮小**。モブに存在しない表情はUIで「素材なし」+validatorで拒否（旧は normal/agitated へ静かに丸め） | `stage_schema.py:688-695`、`story_editor.html:9141-9144` |
| 9 | **Undo/Redo新設**（旧エディタには無かった）。最大80履歴 | `story_editor.html:8093-8162` |
| 10 | **保存時プルーニングによるデータ破壊の廃止**。旧 `pruneAllTurnsForDisplayType()` は特殊表示ターンからカメラ系を黙って削除していた。V2は displayMode がturn上のオブジェクトで、stage状態を消さない | 設計レビュー9章 / `stage_schema.py:725-728` |
| 11 | **turn.id必須+重複禁止、overlayはturnId参照**。並べ替え・分割に強い | `stage_schema.py:743-750` |
| 12 | **turn単位voice上書きの廃止**。音声責務が `instances.voiceId` に一本化され、保存時にvoice profileの実在まで検証 | `story_editor.py:308-316`、`make_story_audio.py:156-166` |
| 13 | **登場退場方向のobject契約化**。`stage.enter[].animation.direction`（auto/left/right/up/down/instant）。turn直下 `enterDir/exitDir` を復活させなかった | `stage-v2.ts:165-174`、`stage_schema.py:120-125` |
| 14 | **シーンエディタとの責務分離**。scene側は slot/cameraPresets/previewCharacterId の事前設計、台本側は slot選択+offset/manual。`previewCharacterId` は「シーンエディタ確認用で台本の配置対象を決めない」と型コメントで明示 | `stage-v2.ts:238-246`、`scene_editor.html:749-830` |

## 5. 旧→V2でまだデグレしている点

「6. 意図的に戻していない点」と区別し、ここには**編集体験・描画結果として実際に落ちているもの**を挙げる。

### 5.1 フロー断絶（重大）

- **AI台本生成がV2非対応**: `_build_script_prompt`（`story_editor.py:587`）は `speakerAnchor` / `enterDir` / `telop*` / `insert` など旧schemaのみを案内し、`schemaVersion: 2` / `instances` / `stage` の記述がない。取込側 `_import_script_text`（`story_editor.py:885`）は `is_v2` 分岐を持つが、**旧schema JSONを取り込むと旧schemaとして保存される**。現在の `story-01.json` はV2なので、生成→取込を実行すると編集中のV2台本が旧schema台本で上書きされ、エディタUIも旧に戻る。運用事故リスクが高い。
- **一括編集の全面無効**: `updateBulkSceneBar`（`story_editor.html:6970`）がV2でバーを隠し、`applyBulkScene` / `applyBulkExpression` / `applyBulkTelop` / `applyBulkManualCamera` 等（`story_editor.html:6986-7173`）はすべて `if (isV2StoryEditor()) return;`。シーン一括適用すら不可。

### 5.2 吹き出し（過去デグレ多発領域）

V2 `bubbleMetricsV2` / 吹き出し描画（`StageVideoV2.tsx:379-403, 1117-1140`）と旧 `bubbleMetrics` / `renderBubble(Group)`（`StoryVideo.tsx:3230-3342, 4495-4603`）の差分:

| 項目 | 旧 | V2 | 判定 |
|---|---|---|---|
| stacked時フォント | `bubbleFontSize`: −2px（最小20） | 縮小なし（固定 `displaySettings.bubble.fontSize`） | 漏れの可能性 |
| 縦位置 | `bubbleBottomOffset`: 通常36px浮かせ、continue時12px、次がcontinueなら112px予約 | 固定 `top: height * 0.95`、オフセットなし | 漏れの可能性 |
| カメラズーム追従 | ズーム量に応じ吹き出しを上へ移動（`zoomBubbleK` 0.95→0.87）+ `followK` でカメラ移動に連続追従 | 追従なし。`bubbleTransform` は遷移前/後スナップのみで、smooth遷移中は補間されない（`StageVideoV2.tsx:1126-1129`）→ ターン開始時に吹き出しX位置がジャンプし得る | 漏れの可能性 |
| padding | `14px 28px` | `14px 22px` | 微差 |
| 幅上限 | `bubbleBaseMaxWidth`（turn設定連動） | `width * 0.48` 固定 | 簡略化 |
| 文単位分割・読点結合 | `、，,` 末尾で結合 | `sentenceGroups` 同ロジック | 同等 |
| continueBubble 段積み・visibility予約 | あり | あり（`continuedBubbleRange`） | 同等 |

### 5.3 演出・カメラ

- **配置変更の移動補間が消失**: 旧 `manualPos` は0.6秒補間で移動（`StoryVideo.tsx:894-1041` waypoints）。V2は `placementOrigin()` の即値のみで、slot変更・manual移動は**瞬間テレポート**（`StageVideoV2.tsx:984-1075` に補間なし）。
- **カメラ演出の時間制御が粗い**: 旧 `cameraEffectSettings` は `zoom.duration` 等でターン内の動きの時間を指定できた。V2 `cameraMotion.zoom/pan/tilt` は構図フレームへの静的オフセット（`applyCameraMotion`: `StageVideoV2.tsx:118-126`）で、動きの表現は `cameraTransition` の固定0.8秒補間（`StageVideoV2.tsx:809-818`）に依存する。shakeのみ duration を持つ。
- **scene共通カメラ値の消失**: 旧sceneの `zoomAmount/zoomDuration/panAmount/panDuration/tiltAngle/tiltDuration/slowZoomDrift` はV2 scene schemaに存在しない（`validate_scene_library_v2` 許可キー: `stage_schema.py:459`）。特に常時演出 `slowZoomDrift` の代替がない。
- **テロップの表示時間の意味変更**: 旧 `telop` は短時間表示+回想境界との自動連携（`StoryVideo.tsx:4694-4995`）。V2 `caption` はターン（連続ブロック）全体表示+境界0.25秒フェード（`captionVisualFor`: `StageVideoV2.tsx:299-316`）。再定義としては妥当だが、「回想に入る瞬間だけ『― 前日 ―』を出す」旧の使い方は、captionを回想ターンの先頭turnだけに付ける運用で代替する必要がある。

### 5.4 音まわり

- **シーン別BGMフォールバック消失**: 旧は `story.bgm` が空ならシーン別BGMへフォールバック（`docs/run-story-current-spec.md` 8節）。`V2BgmLayer`（`StageVideoV2.tsx:429-456`)は時間ベースregionのみで、V2 scene schemaに bgm キーもない。
- **SE自動マップの縮小**: `V2SeLayer`（`StageVideoV2.tsx:458-480`）は `stage.update` の表情変更と `cameraMotion.shake` のみ自動SE対象。旧は `seMap.transition` 等も持つ（`StoryVideo.tsx:639`）。transitionがcutのみの現状では実害は小さい。

### 5.5 エディタ内部

- **ミラー実装の部分不一致**: `v2StateAt`（`story_editor.html:7983`）は `idleFace === "normal"` の非話者表情リセット（`stage-v2.ts:390-394`）を実装していない。在席カードの表情表示が実描画とズレ得る（プレビュー自体はRemotion Player経由なので映像は正しい）。
- **既定値の焼き込み**: `collectV2EffectsFromUi`（`story_editor.html:8845-8873`）は演出を有効化した時点で全パラメータの現在値（=既定値）をJSONへ保存する。旧の「既定値と同じ値は保存時に削除」（デフォルト値プルーニング）がなく、後から既定値を調整しても過去ターンに反映されない・JSONが冗長になる。
- **退場ターンでの状態変更不可**: validatorは `exit` した個体をそのターンの `present` から即削除するため、退場するターンに表情変更を同時指定できない（`stage_schema.py:645-659,673-676`）。旧は退場ターンでも表情指定が効いた。小さいが「驚いて去る」演出で気づく差。

## 6. 意図的に戻していない点（デグレではない）

`docs/v2-missing-settings-inventory-2026-07-16.md` の方針と一致し、コード上も混入していないことを確認した。

- `speakerAnchor` / `focusSpeaker` / `manualCameraFrame` / `manualPos` / `faceMode` / `clearFace` / `zoomTarget` / `cameraCenter` / `emphasis` → slot/placement/framing/cameraMotion へ置換済み。validatorが未知キーとして拒否。
- `turn.voice`（速度/ピッチ上書き）→ 廃止。`make_story_audio.py` が明示拒否。
- `typingFlood` / `stampRain` / `sparkleBurst` → 未移植（未決定）。旧rendererに強依存のため個別判断。
- `transition: fade/wipe/slide` → V2はcutのみ（`stage_schema.py:602-603` がエラー文言で代替案まで案内）。エディタの選択肢もV2ではcutのみ（`transitionOptionsHtml`: `story_editor.html:6563`）。
- 表示種別 `monitor/chat/teamchat/mailer/videocall` → 保留（zunMeetがvideocall後継、他は要求が出るまで追加しない方針）。
- 登場退場アニメの速度・イージング・遅延 → 方向のみ対応、固定0.5秒（`ENTER_EXIT_ANIMATION_SECONDS`）。
- 後ろ姿・座り姿 → `face: left|right` のみ。素材能力モデル待ち。
- 3人以上の自動構図、前景の後ろレイヤー → 保留明示。
- `effectSettings` の動画全体共通UI → V2では renderer 内ハードコード既定（`DEFAULT_STAGE_EFFECT_SETTINGS`: `StageVideoV2.tsx:53-59`）+ ターン保存値。共通設定の管理UIは未設計。

## 7. 単なる漏れ・バグの可能性が高い点

優先度順。

1. **AI台本生成プロンプトの旧schema案内**（5.1）。V2運用開始済みの現状では、機能欠落ではなく事故導線。
2. **吹き出しのstackedフォント縮小・bottomオフセット・ズーム/遷移追従の欠落**（5.2）。旧仕様を「必要な編集体験」と定義していた領域であり、意図の記録がない。
3. **配置変更の0.6秒補間の欠落**（5.3）。「歩いて移動」が表現できなくなった。意図的テレポート仕様なら文書化が必要。
4. **v2StateAt の idleFace 非対応**（5.5）。ミラー実装の同期漏れ。
5. **シーン別BGMフォールバックの黙った消失**（5.4）。inventory棚卸し表では「BGM: 対応済み」とあるが、時間ベースregionのみで scene BGM は言及がない。
6. **effects既定値の焼き込み**（5.5）。プルーニング方針の未移植。
7. **stage-v2.ts resolver のユニットテスト不在**。`test_stage_schema.py` はPython validatorのみで、TS側 `resolveStageStateAtTurn` / `resolveFraming` の回帰テストがない（`video/package.json` にtestスクリプトなし、`video/test-fixtures/stage-v2-props.json` は静止画fixture用）。ミラー実装divergenceを検出する仕組みがない。

## 8. データモデル比較

- **分離達成度**: キャラ定義（`characterId`→素材）/ 個体（instanceId）/ 状態（InstancePatch累積）/ 配置（placement）/ 話者（turn.speaker→instanceId）/ 音声（voiceId）が分離された。設計レビュー7章の最低限分離単位（CharacterDefinition/StageInstance/InstanceState/SpeakerRole/Placement）をほぼ充足。ただし**キャラ定義自体は依然ハードコード**（`KNOWN_STAGE_CHARACTER_IDS`: `stage_schema.py:11`、`MAIN_CHARACTERS`/`FULL_CANVAS`: `StageVideoV2.tsx:36-44`）で、主役追加は複数ファイル修正が必要。
- **instancesとstageの責務**: instances=不変の定義、stage=時系列イベント、と明確。`present`（resolverが導く）と `visible`（displayMode/hideCharactersが導く）の分離も `ResolvedInstanceV2.present/visible` として型に現れている（`stage-v2.ts:260-271`）。
- **差分と継承**: 「scene境界で全リセット / stage eventは累積 / cameraMotion・displayModeは非継承」で一貫。旧の表情scene跨ぎ探索のような例外がない。`reset` で見た目上書きだけ解除でき、placementは維持される（`resetInstance`: `stage-v2.ts:318-325`）。
- **effects設計**: turn直下平置きより明確に良い。`EffectToggleV2<T> = boolean | ({enabled?} & T)` で軽量指定と詳細指定が両立し、validatorに範囲表が集中（`_stage_effects`: `stage_schema.py:206-264`）。演出追加は「型+validator spec+UI spec+renderer」の4点更新で、旧の `_KNOWN_TURN_FIELDS`+プロンプト+EFFECT_SETTING_SPECS 散在よりは追いやすいが、依然チェックリスト運用（`docs/new-effect-checklist.md`）は必要。effectsが10種を超えるなら `effects` の下をカテゴリ分けする余地はあるが、現状6種では破綻しない。
- **displayModeとstageの衝突**: なし。「特殊表示中もstage eventは通常stageの状態だけを更新し、描画は表示種別固有」という規則がresolver（visible判定: `stage-v2.ts:398`）とvalidatorコメント（`stage_schema.py:725-728`）で一致。ただし **displayModeは非継承**なので、連続する会議ターンは毎ターン `zunMeet.participants` 全体を持つ必要がある（旧videocallはscene内部分継承）。データ冗長性とターン挿入時の編集コストに注意。
- **caption**: 旧telopの代替として妥当。既定値は `displaySettings.telop` を継続利用し、turn側は差分のみ。表示時間の意味変更（5.3）だけ文書化が要る。
- **cameraMotion/framing分離**: 妥当。validatorが「speaker framingには在席話者が必要」まで検証する（`stage_schema.py:84-85`）。resolveFramingは話者がslot配置でない場合defaultへ静かにフォールバックする（`stage-v2.ts:429-436`）が、これは検証済み状態からの実行時保険なので許容範囲。

## 9. UI/UX比較（80〜100ターン前提）

| 操作 | 旧 | V2 | 評価 |
|---|---|---|---|
| 登場させる | speaker選択で暗黙 or enter | 個体リストの「登場」ボタン+方向選択（`renderV2Detail`: `story_editor.html:9092-9122`） | V2が明確 |
| 退場させる | exit + exitDir | 「退場」ボタン+方向選択 | 同等以上 |
| 再登場 | speaker指定で暗黙再登場 | 明示「登場」。validatorが二重登場を拒否 | V2が安全 |
| 表情/ポーズだけ変える | turnの expression/pose（話者のみ対象） | 在席カードで**任意の在席個体**の表情/ポーズ/向き/反転/zIndexを個別変更 | V2が強い |
| slot変更・同配置維持 | speakerAnchor（暗黙継承）で事故りやすい | slot選択は状態として継承、変更は明示update | V2が予測可能 |
| カメラだけ変える | 5経路の合成 | framing（継承）+cameraMotion（単発）+transition | V2が明確 |
| 演出だけ付ける | チェック+effectSettingsタブ | チェック+パラメータカード+中心クリック指定（`armV2EffectPositionPick`: `story_editor.html:8817`） | 同等 |
| **複数ターンへ同適用** | 一括編集バー（scene/表情/カメラ/telop等） | **不可**（5.1） | **旧が優位** |
| 現在の登場状態の把握 | 配置タブのマーカーのみ | 在席/不在/声のみのラベル+在席カード一覧 | V2が優位（設計レビュー8章の推奨1を部分実現） |
| 表示種別変更時の維持/無効の理解 | 保存時プルーニングで暗黙破壊 | 「特殊表示中も在席状態は維持され、通常表示へ戻ると復帰します」の説明文+ドラッグ無効化とtitle説明（`story_editor.html:1673,9188-9190`） | V2が優位 |
| プレビュークリック指定 | 効果中心クリックあり | 効果中心クリック+手動配置ドラッグ（身体中央掴み）+手動構図の枠ドラッグ（`renderV2ManualCameraMap`: `story_editor.html:8572`） | 同等以上 |
| Undo/Redo | なし | あり（80履歴） | V2のみ |
| セリフ分割 | あり | あり（`btnV2SplitDialogue`、stageイベントは後半へ複製しない方針） | 同等 |
| スマホ操作 | 長押し複数選択等あり | 未検証。一括編集自体が無効のためモバイル複数選択の意味が薄い | 要確認 |

注意点:

- **scene変更でそのturnの `stage` を黙って削除**する（`collectV2Basic`: `story_editor.html:9280-9282`）。slot参照が壊れるため必然だが、登場イベントごと消えることをユーザーが気づきにくい。確認またはトースト表示が望ましい。
- `v2TurnOrderError`（`story_editor.html:8013`）が並べ替え時に在席整合を事前検査し、壊れる並べ替えを防ぐのは良い設計。
- 吹き出し仕様（`bubbleMaxChars` の意味）は inventory どおり「要再整理」のまま。

## 10. Remotion描画比較

- **描画順**（`StageVideoV2.tsx:1077-1167`）: Audio/BGM/SE → flashbackフィルタ層（displayMode分岐全体を包む）→ stageShell（shake+zoomPunch scale）→ カメラ変換 → bg(z0) → people(z10) → front(z20) → overlays → effects(impactLinesのみ) → 字幕帯/吹き出し(z30) → effects(その他) → caption(z40) → 回想白ディゾルブ(z60)。旧の「ステージ→インサート→吹き出し→ExtraEffects→テロップ→フェード」（`StoryVideo.tsx:5104-5302`）と同型のサンドイッチ構造で、**前景の上にキャラが出るデグレはない**（people z10 < front z20 固定）。
- **flashbackフィルタの適用範囲**: V2ではwhiteboard/zunMeetにも `saturate/brightness` フィルタがかかる（フィルタ層が分岐の外: `StageVideoV2.tsx:1082-1083`）。旧は `stageFilter` がステージのみでインサートにはかからなかった（`StoryVideo.tsx:5138`）。「回想中の全画面表示」の見え方が変わる。妥当な変更に見えるが意図の明文化推奨。グレイン・境界白ディゾルブは旧と同じ定数（0.06 / 0.3秒）。
- **visionNoise/irisOut/impactLines のz順**: impactLines は吹き出しの下、visionNoise/irisOut/quoteFreeze は吹き出しの上。caption(z40)は irisOut より上に残る。旧telopも ExtraEffectsLayer より後で同じ。整合。
- **zoomPunch と cameraMotion.zoom の責務**: 重複なし。zoomPunchは `stageShellTransform` の瞬間スケール+白枠（`StageVideoV2.tsx:880-895`）、cameraMotion.zoomは構図フレームの変形。役割が「打撃感」と「構図」で分かれている。
- **cameraTransition**: smoothは前ターンend→現ターンstart+0.8秒を `easeInOutCubic` 補間（`StageVideoV2.tsx:809-818`）。scene跨ぎ・特殊表示前後・cut指定では補間しない条件が揃っており、カクつき経路は見当たらない。前フレーム側にも前ターンのcameraMotionを織り込むため、motion付きターンからの遷移も連続。既知の制約は「音声時刻未生成だと動かない」で、UIに案内あり（`story_editor.html:9044-9048`）。ただし**吹き出しは遷移補間に追従しない**（5.2）。
- **音声・リップシンク**: `useWindowedAudioData` + RMS + `LIPSYNC_GAIN 5`、mp3再生時のwav解析優先も旧と同じ（`StageVideoV2.tsx:748-762`）。`noLipSync` 対応。話者のみ口パク。モブの開閉画像切替（`mobImageForState`）も旧 `mobImage` 相当。
- **BGM/SE**: 時間ベースBGM regionはフェード含め旧相当。scene BGMフォールバックと一部自動SEは縮小（5.4）。

## 11. Schema/validation比較

旧 `_validate_story` は「必須文字列+insertの形」のみ。V2は前述の通り大幅強化。残る検証の穴:

| 項目 | 現状 | リスク |
|---|---|---|
| manual placement `origin` の範囲 | 有限数値のみ（画面外可） | ドラッグ用に意図的なら低。極端値で見えない配置が作れる |
| `cameraMotion.zoom/pan/tilt` の範囲 | 有限数値のみ | 極端値で構図破綻。`_frame` は0.35-1.0を強制しているのと非対称 |
| slot `origin` の範囲 | 有限数値のみ | scene editor経由なら実質問題なし |
| 主役の `expression` / `pose` 名 | 文字列のみ（モブだけ素材実在検証） | 旧と同じ「normalへフォールバック」経路が主役側に残る |
| `previewCharacterId` の実在 | 空でない文字列のみ | プレビュー専用なので低 |
| turn間の `start/end` 単調性 | 未検証（turn内とsentences内のみ） | 音声生成が書くため実質低 |
| `validate_story_v2(mobs=None)` 呼び出し | モブ素材検証がスキップされる | `story_editor.py:310` は渡しているため現状問題なし。将来の呼び出し追加時の罠 |

良い点として特筆すべきは、slot重なりの検証が**zIndexの継承を考慮**していること（`present_z_indexes`: `stage_schema.py:562-564,720-723`、`test_overlap_z_index_is_inherited_on_following_turns`）。「turnごとのupdateだけ見るとzIndexが消えた扱いになる」というresolver仕様との整合をコメント付きで守っている。

`enabled: false` の扱いは3者で一貫: validator は boolean/objectの形だけ検証、renderer は `stageEffectEnabled`（`StageVideoV2.tsx:153-155`）、UI は `v2EffectIsEnabled`（`story_editor.html:8805-8807`）で、いずれも「object かつ enabled !== false → 有効」。ただしUIはチェックを外すと `effects` からキーごと削除するため、`enabled: false` のデータは実質AI/手書きJSON経由のみ。

## 12. 演出の移植状況一覧

| 旧演出 | V2 | パラメータ | 中心クリック | 状態 |
|---|---|---|---|---|
| `impactLines` | `effects.impactLines` | cx/cy/count/thickness/opacity/innerRadius/start/end（範囲検証あり） | あり | 移植済み |
| `zoomPunch` | `effects.zoomPunch` | scale/duration/borderStrength | — | 移植済み（ステージスケール+白枠） |
| `quoteFreeze` | `effects.quoteFreeze` | fadeIn/fadeOutStart/fadeOutDuration/backdropOpacity | — | 移植済み（現在turn本文の引用カード） |
| `flashback` | `effects.flashback` | enabledのみ | — | 移植済み（彩度0.4/明度1.02/グレイン/境界白ディゾルブ0.3s、連続ターンは区間扱い: `StageVideoV2.tsx:884-891`） |
| `visionNoise` | `effects.visionNoise` | type(future/snow/vhs/glitch)/strength/scanline/glitch/flicker/tint | — | 移植済み |
| `irisOut` | `effects.irisOut` | cx/cy/startRadius/closeStart/closeEnd/color | あり | 移植済み |
| `shake` | `stage.cameraMotion.shake` | strength/duration | — | 置換済み |
| `telop/telopX/telopY/telopSize` | `caption {text,x,y,size}` | 位置/サイズ | — | 置換済み（表示時間の意味は変更） |
| `typingFlood` | — | — | — | 未移植（未決定） |
| `stampRain` | — | — | — | 未移植（未決定） |
| `sparkleBurst` / `sparklePos` | — | — | — | 未移植（未決定） |
| `impactText` | — | — | — | 未移植（旧はimpactLinesのトリガー別名） |
| `effectSettings`（動画全体共通） | rendererハードコード既定のみ | — | — | 未移植（ターン別値はeffects内に保存） |
| `emphasis` | — | — | — | 廃止（framing/cameraMotionで代替） |

## 13. キャラクター配置/登場退場の比較

- 旧: 人数自動割当→cast→speakerAnchor→manualPos の4層 + 暗黙登場 + segment境界。主役はボックス中心基準、モブは足元基準で座標意味が不統一。
- V2: enter時に placement 必須、以後 update で変更、常に足元origin基準（ドラッグ掴み点は身体中央: `v2BodyCenterOffset`）。主役/モブとも同じ `placementOrigin()` で解決し、scale/zIndexも slot既定→個体上書きの一貫規則（`StageVideoV2.tsx:1013-1019`）。
- 登場退場アニメ: 方向6種（auto は旧同様の左右自動: `stageAnimationOffset`: `StageVideoV2.tsx:176-214`）、0.5秒固定、turn尺が短い場合は半分に丸め。退場は前ターン状態から描画し、instant退場はturn末尾で消える（`exitingInstances`: `StageVideoV2.tsx:827-839`）。
- 残る制約: 人数自動構図なし（slotを都度選ぶ）、配置変更の補間なし（5.3）、退場ターンでの状態変更不可（5.5）。

## 14. カメラの比較

| 観点 | 旧 | V2 |
|---|---|---|
| 構図の入口 | focusSpeaker / manualCameraFrame / emphasis / zoomTarget / cameraCenter / scene frames | `framing` 1系統4モード |
| 話者フォーカス | anchor名がleft/rightの時だけleftFocus/rightFocus | 話者のslot→`slot.cameraPresetId`→preset。カスタムslotでも機能する |
| 手動構図 | 数値入力+ターン限り | ステージマップ枠ドラッグ+継承 |
| 動き | cameraEffects(zoom/pan/tilt)+個別duration | cameraMotion（静的オフセット+shake）。時間制御はtransition固定0.8sに集約 |
| ターン間接続 | セグメント/演出ごとに個別 | cameraTransition smooth/cut の明示 |
| scene常時演出 | slowZoomDrift | なし（消失） |

旧の「配置とカメラが互いに補正し合う」問題（設計レビュー12章）は、framingがslot参照で配置座標を直接見ないことで解消。デグレは時間制御の粗さとslowZoomDrift（5.3）。

## 15. 吹き出し/セリフ分割の比較

5.2の表を参照。ロジックの骨格（文単位グループ、読点結合、continueBubble連結範囲、visibility予約、字幕帯スタイル上書き）は忠実に移植されている。落ちているのは**見た目の微調整系**（stacked縮小・bottomオフセット・ズーム/遷移追従・padding）で、いずれも `bubbleMetricsV2` / 吹き出しJSX（`StageVideoV2.tsx:1117-1140`）へ局所的に足せる。`bubbleMaxChars` の仕様再整理は未着手（意図的保留）。字幕帯は連結ターンを改行結合する点も旧相当。ナレーション（voiceOnly話者）は吹き出きを出さない判定が `speakerDefinition?.role !== "voiceOnly"`（`StageVideoV2.tsx:1117`）で明示化され、旧の `isNarrationTurn` 相当より素直。

## 16. シーンエディタの比較

- 旧: anchors自由追加（カメラと不整合の温床: 設計レビュー24.1）、cast、cameraFrames3枠、mob座標を別体系で管理。
- V2: `layouts.standard.slots`（origin/scale/zIndex/cameraPresetId/allowOverlap/previewCharacterId）+ `cameraPresets`（任意個数・slotから参照・存在検証あり: `stage_schema.py:510-516`）。previewCharacterId で確認用立ち絵を出しつつ「台本の配置対象を決めない」責務が明確。
- 旧の「カスタムanchorがfocusSpeakerに乗らない」問題は、slotごとの cameraPresetId で原理的に解消。
- 残る点: シーンエディタは引き続き「シーン既定値の設計場所」でありターンWYSIWYGではない（設計レビュー24.7の役割分担のまま）。scene削除・リネーム時の台本参照整合（24.8）はV2でも横断置換なし（validatorが保存時に「sceneが存在しません」で検出する分、旧より早く気づける）。

## 17. モブ編集の比較

- 定義編集（`mobs.json` タブ）は共通。V2での改善は取り扱い側にある: モブも instance として主役と同じ配置・表情・flip・zIndex 操作を受け、**存在しない表情はUI表示+validator拒否**（`stage_schema.py:688-695`）。旧の「happyを指定するとnormalに化ける」経路が塞がれた。
- 旧で孤立フィールドだった `mob.flip` は、V2では既定値として尊重されつつ（`resolvedFlip(instance, mob.flip ?? ...)`: `StageVideoV2.tsx:1067`）、個体側 flip/face で上書き可能になり実質解消。
- モブのリネーム/削除の参照整合（設計レビュー24.3）は未対応のまま。ただしV2は `instances.characterId` 経由の間接参照なので、モブ名変更の影響は instances 1箇所で済む構造にはなった（保存時に「描画素材がありません」で検出）。
- モブの `pose` は保存できるが描画では使われない（inventory明記済み）。「設定できるが無視」系が1つ残っている点は留意。

## 18. 今後の拡張性

| 要件 | V2での見通し | 根拠 |
|---|---|---|
| 3人以上 | ○ slot追加で可。自動構図はなし（意図的） | slots任意個数 |
| 同一キャラ複数個体 | ◎ 対応済み | `test_same_character_can_have_multiple_instances` |
| モブ複数 | ◎ instanceモデルで対応済み | 同上 |
| 後ろ姿/座り姿 | △ face契約がleft/right固定。素材能力モデルが前提（保留明示） | `stage_schema.py:696-697` |
| 複数ポーズ | ○ 主役はpose per個体。モブは描画未対応 | `InstancePatchV2.pose` |
| 背景ごとの立ち位置プリセット | ◎ slotがまさにこれ | `SlotV2` |
| 手動ドラッグ配置 | ◎ 実装済み | `v2BeginManualPlacementDrag` |
| 前景の後ろ/手前 | × front z20固定。slot/個体zIndexはキャラ間のみ | `StageVideoV2.tsx:1100-1101` |
| 奥行き | △ zIndex+scaleの擬似表現まで | depthモデルなし |
| 移動アニメ | △ 現状テレポート。placement変更に補間を足す拡張点は明確（resolverではなくrenderer側） | 5.3 |
| 登場退場アニメ拡張 | ○ `animation` objectに速度/easing追加可能な契約 | `StageEnterV2.animation` |
| 表示種別ごとの専用配置 | ○ `layouts` はキー拡張可能な構造（現状validatorはstandardのみ許可） | `stage_schema.py:479` |
| 新演出追加 | ○ effects+spec表+チェックリスト運用 | 12節 |
| 一括編集 | 要新設計（現状ゼロ） | 5.1 |
| 旧台本からの変換 | ○ 外部AI変換+validator受入の運用が確立。自動変換ツールは意図的に持たない | 設計レビュー17章 |

## 19. リスク

1. **二重実装の残存**: `v2StateAt` / `v2CameraFrameForEditor` / `v2PreviewTransform`（`story_editor.html`）は `resolveStageStateAtTurn` / `resolveFraming` / `stageTransformValues`（TS）のJSミラー。旧の三重実装（scene_editor含む）より縮小したが、divergence検出手段（共有fixtureテスト）がない。idleFace不一致（5.5）が既に1件ある。
2. **キャラ定義のハードコード分散**: `KNOWN_STAGE_CHARACTER_IDS`（stage_schema.py）、`MAIN_CHARACTERS`・`FULL_CANVAS`（StageVideoV2.tsx）、エディタ側の主役判定。主役追加時に更新漏れしやすい。
3. **AI生成フローの旧schema導線**（5.1）は、単なる機能不足でなく**V2台本を旧schemaで上書きする事故導線**。
4. **displayMode非継承**による会議・ホワイトボード連続ターンのデータ冗長。ターン挿入・参加者変更時の編集コストが増える。
5. **effects既定値の焼き込み**により、将来「既定値を変えたら全動画が変わる」挙動を期待しても過去ターンには効かない（逆に安定性と見ることもできる。方針の明文化が必要）。
6. **吹き出し領域の回帰検証手段の不足**: 過去デグレ多発領域なのに、V2吹き出しの視覚回帰fixtureがない。

## 20. 優先対応すべき残課題

1. **AI台本生成のV2対応**（プロンプトのV2 schema化、または最低限「V2台本編集中は旧schema取込をブロック/警告」するガード）。
2. **V2用一括編集の最小セット新設**（scene / speaker / framing / caption / effects の範囲適用。旧の危険な操作は流用しない方針どおり再設計）。
3. **吹き出し細部の意図確定と回帰**（stacked縮小・bottomオフセット・ズーム/遷移追従・padding。戻すなら `bubbleMetricsV2` 周辺の局所修正、戻さないならinventoryへ「廃止」と記録）。
4. **配置変更の移動補間**（旧0.6秒相当）を戻すか、テレポートを正式仕様として文書化。
5. **シーン別BGMフォールバックの方針決定**（V2で廃止と明記するか、`V2BgmLayer` にフォールバック移植）。
6. （次点）`v2StateAt` の idleFace 同期、stage-v2 resolver の共有fixtureテスト、`cameraMotion` の範囲検証追加。

## 21. 実装前に確認すべき仕様

実装に着手する前にユーザー判断が必要な点:

1. 吹き出しの旧挙動4点（5.2）は「戻す」か「V2仕様として確定」か。
2. 配置変更テレポートは意図か。補間を戻す場合、slot→slot / slot→manual / manual→manual のどれを対象にするか。
3. caption の「ターン全体表示」への意味変更を確定とするか（回想境界との自動連携は復活させない、で良いか）。
4. scene別BGM・`slowZoomDrift`・`seMap.transition` は廃止確定か。
5. flashbackフィルタが whiteboard/zunMeet にもかかる現挙動は意図か。
6. AI台本生成のV2対応形（プロンプト全面V2化 / 旧生成→外部AI変換の2段運用 / 取込ガードのみ）。
7. `effects` 既定値の焼き込みを続けるか、旧同様のデフォルト値プルーニングへ寄せるか。
8. displayMode（zunMeet等）の連続ターン継承を導入するか（データ契約変更になるため要合意）。

## 最終まとめ

- V2は旧方式より**明確に良くなっている**。責務分離・validator・状態解決・Undo/Redoは旧に対して質的な改善で、設計意図（差分保存・明示操作・slot契約）はコードに一貫して反映されている。
- まだ旧に劣る重要点は、(1) AI台本生成→取込フローの断絶、(2) 一括編集の全面無効、(3) 吹き出し・移動補間・scene BGMなどの細部デグレ。
- これらは**設計上の問題ではなく、ほぼ移植漏れ・未着手**。V2のデータ契約を変えずに追加できる（唯一displayMode継承だけは契約変更を伴う）。
- 次に直すべき上位5件: ①AI生成のV2対応（事故導線の遮断）、②V2一括編集の最小セット、③吹き出し細部の意図確定と回帰、④配置移動補間の方針決定、⑤scene別BGMの方針決定。
- **V2はこのまま進めてよい**。ただし吹き出しと演出の視覚回帰fixture、TS resolverのテストを先に整えると、以降の移植作業のデグレ検出が効くようになる。
