# v2ステージ実装 監査（2026-07-16）

## 判定

**要修正。** v2の設計方針（個体・slot・構図の分離）は成立しているが、実装は「v2で設定できる値」と「Remotion/プレイヤーが実際に描画する値」の対応が未完成である。

特にBGM・SE・Overlay・リップシンクは既存編集機能として画面に残っているのに、v2レンダラーでは処理されない。これは意図的な機能縮小としてUIで明示されてもおらず、保存後に静かに失われるため、最優先で是正する。

この監査では実装は変更していない。`story_editor.html`、`scene_editor.html`、保存validator、v2 resolver、Remotion、プレイヤーを、設定値が保存・解決・描画まで到達するかで照合した。

## 監査対象と確認済み経路

```text
story_editor.html / scene_editor.html
  -> story_editor.py / scene_editor.py（保存時validator）
  -> stage_schema.py（v2整合性）
  -> video/src/stage-v2.ts（差分状態解決）
  -> video/src/StageVideoV2.tsx（プレイヤー / Remotion描画）
```

実行済みの回帰確認:

- `python3 test_stage_schema.py` — 15件成功
- `python3 test_story_editor.py` — 成功
- `python3 test_make_story_audio.py` — 成功
- `python3 test_tts_voicevox.py` — 成功（ローカルVOICEVOX辞書同期だけはsandbox上でスキップ）
- `cd video && npx tsc --noEmit -p .` — 成功
- `story_editor.html` / `scene_editor.html` の抽出JavaScript構文チェック — 成功

上記は既存テストの成功であり、以下の描画漏れを検出できていない。`StageVideoV2` を直接検証するテストはなく、`video/test-fixtures/stage-v2-props.json` もテストから参照されていない。

## 最優先（設定できるがv2で描画されない）

| ID | 問題 | 根拠 | 影響 | 修正方針 |
| --- | --- | --- | --- | --- |
| P0-1 | **BGM・手動SE・Overlayがv2で無音/非表示** | 台本UIは `storyData.bgm`、`turn.se`、`storyData.overlays` を編集・保存する（`story_editor.html:3974-3977`, `:12251-12256`, `:3733-3735`）。一方、`StoryV2` は `audio` 以外のこれらを型に持たず（`video/src/stage-v2.ts:94-100`）、`StageVideoV2` の音声は主音声の `<Audio>` 1本だけ（`:311`）である。`seMap` はプレイヤーが読み込むが、v2 propsは受け取らない（`video/src/story-player.tsx:162-179`, `video/src/StageVideoV2.tsx:18-26`）。 | エディタの「音（BGM/SE）」とタイムラインで設定しても、v2プレビュー・書き出しには反映されない。 | 旧 `StoryVideo` のBGM/SE/Overlay解決・描画を、共通部品化または最小移植してv2へ通す。対応までUIを出したままにしない。 |
| P0-2 | **v2の主役・モブともリップシンクしない** | v2の全`Avatar`呼び出しが `amplitude={0}`（`video/src/StageVideoV2.tsx:188,236,280`）。`Avatar` はこの値で口形状を選ぶ（`video/src/Avatar.tsx:357-365`）。モブも常に `closed` 画像を選ぶ（`video/src/StageVideoV2.tsx:299-304`）。 | 音声があっても口が閉じたまま。旧描画からの明確な視覚退行。 | 旧rendererの波形RMS計算を共有化してv2へ渡す。モブも発話中の開口素材を選ぶ。 |
| P0-3 | **`allowOverlap` slot が後続ターンでvalidatorに拒否される** | validatorは在席者の`placement`しか保持せず（`stage_schema.py:235-236,271-272,296-298`）、重なり時の`zIndex`を「そのターンの`update`」だけで再確認する（`:323-338`）。一方resolverは`zIndex`を差分状態として継承する（`video/src/stage-v2.ts:244-246`）。 | 背景slotで2人を重ね、次のターンへ進むと保存不能になる。つまり合意済みの「重なり許可 + zIndex」が実運用できない。 | validatorもresolverと同じ完全状態（placementとzIndexを含む）を追跡する。回帰テストは「重なりを設定した次ターンも保存できる」を追加する。 |

P0-3は再現済み。2人に同slotと`zIndex`を同じターンで指定するとそのターンは通るが、次ターンに何も指定しないだけで `slot speakerLeft の重なりには zundamon のzIndexが必要です` となる。

## 高優先（モブ・scene・特殊表示の取りこぼし）

| ID | 問題 | 根拠 | 影響 | 修正方針 |
| --- | --- | --- | --- | --- |
| P1-1 | **v2シーンエディタでslot確認用にモブを選べず、描画もできない** | slotの`previewCharacterId`候補は `assets.characters` だけ（`scene_editor.html:1282-1287`）。モブ一覧は別の `assets.mobs` だが、v2では旧モブ描画全体を除外している（`:1613-1618`）。さらにv2プレビューはモブIDをavatarパスとして読もうとする（`:1543-1584`）。 | 「このslotにモブを置いた時の大きさ・カメラ」をシーンエディタで確認できない。 | preview用の素材種別を主役/モブで分け、同じslot previewとして描画する。台本の個体配置そのものは保存しない現在の責務は維持する。 |
| P1-2 | **モブの`face`が無視される** | UIは全在席個体に向き選択を出す（`story_editor.html:8597-8617`）、validatorも`left/right`を許可する（`stage_schema.py:311-312`）。主役は`resolvedFlip()`を使うが（`StageVideoV2.tsx:284`）、モブは`mob.flip || instance.flip`だけで`face`を見ない（`:303`）。 | モブに「向き」を設定しても描画が変わらない。 | モブにも`resolvedFlip(instance, fallback)`を適用する。素材固有の既定flipとの合成規則を明文化する。 |
| P1-3 | **モブをホワイトボードの解説役にすると消える** | UIはstage個体ならモブも解説役候補にする（`story_editor.html:8532-8537`）、validatorも許可する（`stage_schema.py:344-351`）。描画は主役定義にない場合に`return undefined`する（`StageVideoV2.tsx:168-174`）。 | 選択できる解説役が出ない。 | モブ用の1枚絵presenterを描くか、未対応ならUI/validatorで主役だけに制限する。推奨は前者。 |
| P1-4 | **モブをZunMeet参加者にすると「?」表示になる** | 参加者UIは全instanceを候補にする（`story_editor.html:8458-8488`）。描画は主役以外をプレースホルダにする（`StageVideoV2.tsx:225-237`）。 | 「主役・モブ共通instance」という契約が特殊表示で崩れる。 | モブ画像をタイルへ描く。未対応なら候補から外し、仕様として制限する。 |
| P1-5 | **新規v2シーンが意図に反してバスト表示になる** | 新規v2 scene生成に`figure: "full"`がない（`scene_editor.html:2067-2085`）。v2 renderer/scene previewは`figure === "full"`だけを全身として扱い、未指定はバストになる（`StageVideoV2.tsx:263-266`, `scene_editor.html:1543-1552`）。しかもv2 UIにはfigure編集欄がない。 | 新しい背景を作った時だけ、既存シーンと異なる素材・接地点になり、シーンエディタでは直せない。 | 新規v2 sceneに`figure: "full"`を入れ、v2用の表示種別（full/bust）を明示編集可能にする。 |
| P1-6 | **カスタムcamera presetをscene editorで作成・編集できない** | slotは任意の`cameraPresetId`を参照でき、validatorも許可する（`stage-v2.ts:102-110`, `stage_schema.py:199-210`）。しかしscene editorの枠UIは`default/left/right`に固定（`scene_editor.html:764-768`, `:430-440`）で、preset一覧は既存値をslotへ割り当てるだけ（`:1272,1320-1329`）。 | `presenter`や背景用slotなど、3枠を超える構図をscene editorで設計できない。 | presetの追加・名前変更・削除・選択を実装する。slotに使われているpresetの削除は拒否する。 |

## 中優先（カメラ・validator・プレビューの乖離）

| ID | 問題 | 根拠 | 影響 | 修正方針 |
| --- | --- | --- | --- | --- |
| P2-1 | **`cameraMotion.shake`は保存検証されるが描画されない** | 型とvalidatorは`shake`を受け入れる（`stage-v2.ts:67-71`, `stage_schema.py:83-97`）。v2 rendererが利用するのはzoom/pan/tiltだけ（`StageVideoV2.tsx:79-87,322`）。 | JSONで設定しても無反応。AI変換時も見逃しやすい。 | shakeを実装するか、v2契約からUI/型/validatorごと外す。 |
| P2-2 | **tiltは書き出しには効くが、v2編集UIと配置編集のプレビューにはない** | rendererは`motion.tilt`をrotateへ適用する（`StageVideoV2.tsx:322`）。v2 UIはzoom/panだけを保存する（`story_editor.html:8524-8526,8721-8727`）、その独自プレビュー変換もtiltを扱わない（`:8179-8192`）。現行確認台本にも`tilt: -2`が入っている。 | 台本を見ながらの配置・構図確認と最終レンダリングが一致しない。 | tilt UIと同じプレビュー変換を追加するか、v2ではtiltを禁止する。 |
| P2-3 | **v2 validatorが未知キー・未知素材を通す** | `StageEvent`/`InstancePatch`の許可キーを検査していない（`stage_schema.py:254-257,286-316`）。実測で`stage.update.zundamon.visible=false`、未知`characterId`、`allowOverlap: "yes"`はいずれもvalidatorを通った。描画側は未知updateを読まず、未知素材は`null`を返す（`stage-v2.ts:160-177`, `StageVideoV2.tsx:257-301`）。 | 変換JSONのタイプミスが「保存成功・描画だけ無反応」になる。 | v2だけはstrict validatorにする。少なくともstage/update/cameraMotion/displayMode/scene slotの未知キー、characterId実在性、`allowOverlap`のbooleanを拒否する。 |
| P2-4 | **カメラ枠の範囲をvalidatorが保証しない** | `_frame()`は`width > 0`だけを確認する（`stage_schema.py:25-33`）。rendererは`width > 1`を実質等倍に丸める（`StageVideoV2.tsx:66-73`）が、UIは0.1〜1へ正規化している。 | 外部変換で不正な手動構図を入れると、保存値と見た目が一致しない。 | v2 schemaでwidth/cx/cyの操作可能範囲を明示し、scene editorと共通定数にする。 |
| P2-5 | **カメラの滑らか接続は、音声時刻が無い台本では発動しない** | 接続判定は前turnの`end`と次turnの`start`を必須にしている（`StageVideoV2.tsx:120-128`）。 | 音声生成前に「滑らか」を選んでも、プレイヤーではカットに見える。 | UIに「音声時刻生成後に有効」と明示するか、未確定時のプレビュー用時間を決める。 |

## 仕様として維持できているもの

- `present`はenter/exit差分から解決し、特殊表示中は標準stageだけ`visible=false`になる（`video/src/stage-v2.ts:212-256`）。特殊表示の後に通常表示へ戻すと在席状態が復帰する。
- scene変更で舞台状態をリセットする。
- 通常slotの重複禁止、slot/手動配置、offset、z-index、speaker/slot/manual framing、前景が人物より前という基本契約は実装されている。
- 今回追加した構図接続は、同一sceneかつ通常表示で未指定時smooth、turn単位でcutへ切替可能である（`video/src/StageVideoV2.tsx:120-161`）。
- 登場・退場アニメーション、3人自動構図、新表示種別は、現時点で「未実装」と明記された範囲であり、この監査では漏れ扱いにしない。

## 根本原因

v2実装で**状態解決器だけを新設し、既存の映像・音声・タイムライン機能を「v2でも通すか」「UIから隠すか」まで棚卸ししていない**。そのため、以下が同時に起きている。

1. `StoryV2`が既存top-level機能（BGM/Overlay）を表現しない。
2. `StageVideoV2`が既存rendererから必要な共通機能（リップシンク、SE、BGM、Overlay、モブ特殊表示）を持ち込まない。
3. UIはv2モードでも共通タイムライン・音タブを見せ、保存APIは未知キーを通す。
4. validatorが状態を完全解決せず、rendererとの状態継承規則が分岐している。

## 修正の優先順

1. **P0を先に直す。** BGM/SE/Overlayとリップシンクをv2 rendererへ通し、重なりslot validatorをrendererと同じ完全状態へ揃える。ここが直るまで、v2を長編編集用の本運用には使わない。
2. **モブを共通instanceとして最後まで描画する。** 通常・scene preview・whiteboard・ZunMeetで、対応するか候補から明示的に外すかを揃える。推奨は対応。
3. **scene editorをslot設計画面として完成させる。** full/bust、モブpreview、camera preset CRUDを追加する。
4. **strict validatorと描画fixtureを追加する。** 「設定できるが無視」を保存時に止め、最低でもstandard（主役・モブ）、whiteboard、ZunMeet、BGM/SE/Overlay、allowOverlap、camera motionのRemotion静止画/音声経路をfixture化する。

## 実装前に決める必要があること

- モブをwhiteboard presenter / ZunMeet participantとして**画像表示まで対応するか**。推奨は対応。
- v2で既存のBGM/SE/Overlayを**完全維持するか**。現在のUIを残すなら完全維持以外は不可。
- `cameraMotion.shake`を実装するか、v2から削除するか。中途半端に許可しない。
- 個体ごとの一時非表示（presentのままvisible=false）を、特殊表示以外にも必要とするか。現実装のvisibleは表示種別単位だけで、個体単位のhiddenイベントはない。
