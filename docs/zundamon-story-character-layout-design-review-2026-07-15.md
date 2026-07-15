# zundamon-story キャラクター登場・配置・描画方式 設計レビュー

作成日: 2026-07-15
対象: `run-story` 系（`story_editor.py` / `story_editor.html` / `video/src/StoryVideo.tsx` / `video/public/*.json`）
対象外: `legacy-dialogue/`。実装変更は行っていない。

## 1. エグゼクティブサマリー

結論として、現在の方式は **1〜2人の通常会話を素早く作るには実用的** だが、キャラクターの「在席」「配置」「話者」「画面種別」「カメラ」が、ターンの任意フィールド群と暗黙継承で結合している。そのため80〜100ターンの編集では「この行を直すと、どこまで効くか」を予測しにくい。

最大の問題は `anchor` 単体ではない。`anchor` が配置スロット・画面座標・話者フォーカスのカメラ選択条件・登退場方向の既定判定を兼ね、さらに `speakerAnchor`、`manualPos`、シーンの `cast`、人数自動割当が優先順位で重なることである。`visible` は独立した状態として存在せず、`enter` / `exit` / 話者の暗黙登場 / `hideCharacters` / 全画面インサートによって間接的に決まる。

推奨は、全面的なレイアウトエンジン化ではなく、**案2「表示種別・シーンが配置スロットを持つ」へ段階移行すること**である。新スキーマでは既存の `speakerAnchor`（`left` / `center` / `right`）を持たず、`slot` として明示する。`manualPos` はスロットからのオフセット又は明示的な手動配置に置き換える。旧台本は必要時に外部AI（ChatGPT）で新JSONへ変換し、ローカル検証を通す運用とする。通常表示・ホワイトボード・ニュース等で異なる配置規則を自然に分けられる。

完全な「状態＋レイアウトエンジン」（案3）は、3人以上、複数個体、奥行き、レイアウト自動選択を本格採用する段階まで延期すべきである。現状の主用途（1〜2人会話）に対しては、先にスロットと明示的な状態を導入する方が、UX改善に対する実装・移行コストの比が良い。

## 2. 調査範囲と根拠

| 層 | 主な根拠 | 観察した責務 |
| --- | --- | --- |
| 台本・素材データ | `video/public/story-01.json`, `story-scenes.json`, `mobs.json` | ターン差分、シーン座標、モブ素材・既定配置 |
| 読込・保存・正規化 | `story_editor.py:281-313, 880-951, 1188-1206, 1332-1342` | JSON読込・最低限の保存検証・AI取込時のID再付与と警告 |
| UI編集 | `story_editor.html:1563-1626, 5015-5144, 5240-5375, 7990-8061, 9912-9937, 11247-11366` | 表示種別、配置ドラッグ、カメラ、保存前プルーニング |
| レンダリング状態解決 | `StoryVideo.tsx:782-1104, 3122-3234, 3388-3415` | セグメント、在席、anchor、手動座標、話者フォーカス、カメラ |
| Remotion描画 | `StoryVideo.tsx:4250-4478, 5080-5165` | Avatar/Mob描画、前景・背景、インサートとステージの重ね順 |
| 音声・時間化 | `make_story_audio.py:155-244` | `speaker` / `narrationVoice` から音声を作り、`start/end/sentences` を書戻す |
| テスト | `test_story_editor.py:12-197` | プロンプト・取込・最低限のinsert検証。配置・継承・矛盾状態の回帰テストはない |

## 3. 現在の仕組み（コードを知らない人向け）

台本は「セリフの行（turn）」の配列である。各行には必ず `speaker`、`text`、`scene` があり、必要な行だけ `enter`、`exit`、`speakerAnchor`、`manualPos`、表情、ポーズ、カメラ、表示種別（`insert.kind`）などを付ける。各行がキャラクターの完全な状態を持つのではなく、レンダラーが同じシーンの連続区間を遡って、現在の状態を組み立てる。

### キャラクターの識別と素材

- 主役キャラは `StoryVideo.tsx` のハードコードされた `CHARACTERS`（現状 `zundamon` / `metan`）。キャラクターIDは同時にアバター素材キー、話者候補の一部、配置対象IDでもある。
- モブは `mobs.json` のオブジェクトキー（例: `営業`、`部長`）で識別する。1枚絵の `normal` / `agitated` と口の開閉画像、既定scale、既定anchor、VOICEVOX speakerをまとめている（`mobs.json`、`StoryVideo.tsx:4423-4476`）。
- 話者の音声可否は別の `voice_profiles.json` とモブの `voice` から決まる。エディタの選択肢もここを合成している（`story_editor.py:215-227`）。従って「人物」「画面に出る個体」「音声の話者」が同じ文字列で重なりやすい。

### 登場・退場・在席

- 明示的に `enter: [id]` を付けると、当該ターン開始から登場する。`exit: [id]` は当該ターンの終端で退場する。`enterDir` / `exitDir` は画面端からのスライド方向である（`StoryVideo.tsx:1062-1093, 4301-4327, 4433-4453`）。
- 既知キャラまたはモブが通常の `speaker` になると、`enter` が無くても暗黙に登場する（`entranceTimesFor`、UIの `presentActorsAt` はともに話者を在席に追加）。ナレーション行は例外である。
- `visible: boolean` はない。現在の「見える」は、在席集合 − 退場済み − `hideCharacters` − モブのシーン設定 `hidden` − 全画面インサートで覆われる、という合成結果である。
- 同じsceneが連続する範囲が状態の境界であり、sceneを跨いで在席・手動配置・`speakerAnchor` は持ち越さない（`buildSegments`: `StoryVideo.tsx:782-801`）。

### 位置・anchor・微調整・サイズ

- シーンは `anchors: {name: {x,y}}` を持つ。主役キャラでは anchorは立ち絵ボックス中心の正規化座標として描画される（実装コメント `StoryVideo.tsx:4333-4339`）。一方、モブは画像の下端を `anchor` に合わせる（`StoryVideo.tsx:4456-4476`）。同じ「anchor」という語でも基準点が違う。
- 主役の自動配置は、(1) 人数に応じた `assignAnchors`、(2) シーンの `cast`、(3) それまでの `speakerAnchor` の順に上書きされる（`StoryVideo.tsx:858-891`）。1人=center、2人=left/right、3人=left/center/rightで、4人目以降は自動割当から落ちる。
- `speakerAnchor` は「その行の話者だけ」を指定アンカーへ置くが、同一セグメントの後続行にも継承される。モブには効かない。
- `manualPos[id] = {x,y}` はその時点以後の手動座標である。主役は顔位置基準で保存し、描画時にはボックス中心へ戻す。`null` は自動配置に戻す解除記号である。次の手動座標まで保持され、0.6秒で補間移動する（`StoryVideo.tsx:894-1041`）。
- 主役の大きさは主に `scene.scale`、モブは `scene.mobs[id].scale` → `mobs.json.scale`、ホワイトボードは専用レイアウト、ビデオ会議はタイルレイアウトにより決まる。ターン単位の共通「人物scale」はない。

### 表情・ポーズ・向き・描画順

- `expression` と `pose` は基本的にそのターンの話者に適用される。表情を省略すると、当該キャラが過去に話した最後の表情を引き継ぐ（`lastExpressionOf`: `StoryVideo.tsx:3140-3154`）。非話者の表情は `story.idleFace` により normal または保持になる。
- `face` / `faceMode: hold` / `clearFace` で向きを一時指定または継続できる。未指定時はx座標から中央向きに自動反転する。全身ずんだもんは後ろ姿等の専用素材にも分岐する（`StoryVideo.tsx:684-771, 4258-4265`）。
- ステージの描画順は **背景(back) → 主役キャラ配列順 → モブ配列順 → 前景(front)**。同じ層の主役同士には明示z-indexがなく、配列順が重なり順になる。前景は全人物の前で固定（`StoryVideo.tsx:5142-5164`）。

### speaker・カメラ・表示種別・背景との関係

- `speaker` は音声生成の話者であり、通常表示ではリップシンク、表情・ポーズ、吹き出しの対象にもなる。ナレーションは `narrationVoice` を付けることで、同じ `speaker` の声を使いつつ立ち絵・吹き出し・話者寄りを無効にする（`StoryVideo.tsx:3122-3124`、UI説明 `story_editor.html:7765-7772`）。
- `focusSpeaker` は話者の解決済みanchor名が `left` / `right` の時だけ、シーン側 `cameraFrames.leftFocus/rightFocus` を選ぶ。centerやモブではdefaultになる。`manualCameraFrame` はその行だけ完全に優先する（`StoryVideo.tsx:3388-3415`）。
- `cameraEffects`、`emphasis`、`zoomTarget`、sceneのslow-zoomも別途カメラ変換に積み重なる。配置はそのカメラで撮られるステージ上の座標である。
- 表示種別は `insert.kind` をUIで「通常 / ZunMonitor / Chat / TeamChat / Mail / ZunMeet / ホワイトボード」と見せ直したもの。通常以外は基本的に全画面パネルでステージを覆う。ホワイトボードだけは `insert.character` が専用の中央キャラを持ち、通常ターンの `expression/pose` とは別系統である（`story_editor.html:11247-11271`、`StoryVideo.tsx:4370-4416`）。
- 背景はシーンの `bg`、人物の前に隠す素材は `front`。カメラ変換は背景・人物・前景をまとめたステージに適用される（`StoryVideo.tsx:5125-5165`）。

## 4. データフロー

```text
story-01.json (turnの差分データ)
  ├─ GET /api/story → story_editor.html の storyData
  │    ├─ 基本タブ: speaker / scene / speakerAnchor / expression / pose / insert
  │    └─ 配置タブ: manualPos / zoomTarget / focusSpeaker / manualCameraFrame
  ├─ 保存前: normalizeStoryTransitions + 表示種別により無効なフィールドを削除
  └─ POST /api/story → _validate_story（必須文字列とinsertの形だけ）→ JSON保存

make_story_audio.py
  └─ speaker or narrationVoice → VOICEVOX → start/end/sentences/audioを書戻す

Remotion StoryVideo
  ├─ buildSegments(scene連続区間)
  ├─ roster / enter / exit / speaker暗黙登場 → presentNow, presentMobs
  ├─ assignAnchors + scene.cast + speakerAnchor + manualPos → 実座標
  ├─ manualCameraFrame / focusSpeaker / scene frames + emphasis/effects → stageTransform
  ├─ bg → Avatar → Mob → front をstageTransform内に描画
  └─ insertをステージ前に描画（多くは実質的にステージを覆う）
```

エディタは `StoryVideo.tsx` と同じ計算をJavaScriptで再実装している。例えば `presentActorsAt` / `assignAnchorsJS` / `resolveActorXYAt` / `effectiveCameraFrameAt` はTSX側のミラーである（`story_editor.html:5015-5144`）。これはWYSIWYGに必要だが、仕様変更を二重実装する維持コストと、プレビュー・書出しの乖離リスクを持つ。

## 5. anchorの責務評価

現在のanchorは以下の複数を兼務している。

| 候補の責務 | 判定 | 根拠・問題 |
| --- | --- | --- |
| 固定座標 | 該当 | `scene.anchors[name]` はx/y座標。`speakerAnchor`の未知名はcenterへフォールバックし、警告されない。 |
| レイアウト用スロット | 該当 | `left/center/right` は人数自動割当のスロットでもある。 |
| キャラクターの役割 | 部分的に該当 | `cast` がキャラID→anchorを固定し、暗黙に「この場面の役」を表す。 |
| 背景固有の立ち位置 | 該当 | anchor座標と `cast` はscene定義にある。 |
| カメラ用基準点 | 間接的に該当 | `focusSpeaker` はanchor名のleft/rightでcameraFrameを選ぶ。座標を見ず名前に依存する。 |
| UI上の簡易指定 | 該当 | 基本タブの `speakerAnchor` 選択肢はsceneのanchor名を直接列挙する（`story_editor.html:6472-6492`）。 |

この混在による具体的な問題は次の通りである。

1. `windowLeft` のような背景固有anchorを追加しても、話者フォーカスはleft/right以外をdefault扱いにする。名前を変えただけでカメラ仕様が壊れる。
2. 位置を変えたいだけでも、カメラの寄り先・登退場の既定方向・自動反転まで変わる。編集者は「配置変更」と「構図変更」を分離できない。
3. 1つのanchorへ複数人物を置くことを許すが、重なり順や衝突を定義していない。
4. 主役のanchorはボックス中心、モブのanchorは足元であり、同じUI用語が同じ座標意味を持たない。

従ってanchorは、今後は「背景・表示種別が提供する**名前付き配置スロット**」と定義し直すべきである。座標・カメラヒント・人物の基準点はスロット定義の属性であり、`left` という文字列そのものに意味を持たせない。

## 6. 状態継承ルール

現在は完全指定でも単純な差分指定でもなく、**フィールドごとに異なる差分継承モデル**である。これが編集上の最大の認知負荷になっている。

| 状態 | 現在の規則 | 境界 |
| --- | --- | --- |
| 在席 | `enter` と話者の暗黙登場を累積、`exit` まで表示 | 同一scene連続segment |
| 自動anchor | 人数自動割当 → scene.cast → 過去のspeakerAnchor | 同一segment |
| 手動位置 | 最後の`manualPos[id]`を保持、`null`で解除、0.6秒補間 | 同一segment |
| 表情 | 当該話者の最後の`expression`を時間全体で探索 | sceneを跨いでも探索する実装 |
| 向き | `faceMode: hold`で保持、`clearFace`で解除 | 時間全体で探索 |
| ポーズ | 原則当該ターンのみ | ターン |
| `focusSpeaker` | 当該ターンのみ | ターン |
| `manualCameraFrame` | 当該ターンのみ | ターン |
| `zoomTarget` | way pointとして保持、`null`で解除 | 同一segment |
| videocall | 同じscene内で前の設定を部分継承 | 同一scene、end markerまで |
| insertの表示 | 多くはターンのみ。空セリフ時は次turn開始まで表示 | ターン間の実効終端 |

特に重要なのは、`speakerAnchor` と `manualPos` は後続ターンに効くのに、`manualCameraFrame` は効かない点、`expression` / held `face` はscene境界を越えて探索し得る点である。ユーザーは「前ターンと同じ」が何を意味するかをフィールドごとに覚えなければならない。

推奨する仕様原則は次の二層である。

- ターンには「そのターンで変えた差分イベント」だけを保存する。
- レンダリング前に明示的な `ResolvedStageState` を作り、各フィールドの継承範囲を一か所で宣言する。

この二層を導入しても既存JSONをただちに完全状態化する必要はない。重要なのは「何が継承されるか」を型・関数・UIで同じ表にすることである。

## 7. キャラクターとは何か（概念分離の評価）

現在は次の概念が十分に分離されていない。

| 概念 | 現状 | 問題 |
| --- | --- | --- |
| キャラクター定義 | 主役は`CHARACTERS`、モブは`mobs.json` | 定義場所・能力（表情/ポーズ/voice）が統一されない。 |
| シーン内の個体 | 文字列IDをそのまま在席IDに使用 | 同一キャラの複数個体を表せない。`営業A`と`営業B`は別キャラ定義を複製する必要がある。 |
| ターンの状態 | `expression`等は主にspeakerに属す、`manualPos`は任意ID map | 同じturnに「話者の表情」と「任意人物の位置」が混在する。 |
| 画面上の配置 | anchor名/座標とカメラを混在して解決 | 描画位置の理由を追いにくい。 |
| 画像素材 | 主役はmanifest/表情/ポーズ、モブは画像ペア | 後ろ姿・座り姿・複数ポーズを共通モデルで扱えない。 |
| 話者の役割 | `speaker`は音声、在席、リップシンク、吹き出し、表情、フォーカスの起点 | 「声だけの話者」「画面外の話者」「別人が読むナレーション」を素直に表せない。 |

最低限の分離単位は次である。

```ts
CharacterDefinition  // 素材・対応ポーズ・対応表情・voice候補
StageInstance        // scene内の個体。instanceId、characterId、在席、zOrder
InstanceState        // 表情、ポーズ、向き、可視性、配置参照
SpeakerRole          // このターンの音声を誰が担うか（instanceIdまたはnarrator）
Placement            // slot / offset / manual座標
```

主役の1〜2人運用では、`instanceId === characterId` を既定にしてUIへ露出しなくてよい。内部概念だけ先に分ければ、将来の複数モブや同一人物複数個体を破壊的変更なしに扱える。

## 8. UXレビュー（80〜100ターン編集）

### 自然にできること

- 話者を主役キャラにするだけで登場させる、既存の1〜2人を会話させる。
- その行だけ表情・ポーズ・カメラ効果を変える。
- 配置タブで現在見えている人物をドラッグし、手動位置に切替える。
- 通常表示と全画面表示種別を上部で切替える。UIは表示種別固有設定を上部に寄せており、以前より理解しやすい。

### 混乱しやすい編集フロー

**例1: 「めたんを次の10行だけ左へ移動したい」**
編集者は `speakerAnchor=left` を設定するか、ドラッグで`manualPos`を作るかを選ぶ必要がある。前者は同一sceneの後続へ暗黙継承し、後者も`null`を置くまで継承する。さらに左へ置くと `focusSpeaker` のcameraFrame選択や自動反転・登退場方向も変わり得る。終了地点で「元に戻す」操作を忘れると、数十行後の構図が変わる。

**例2: 「特殊表示の間に人物を退場させ、次の通常表示では消えていてほしい」**
UIは正しく「特殊表示中は登退場が画面には映らないが後続に反映」と注意する（`story_editor.html:8048-8053`）。しかし、画面で見えない操作が状態だけ変えるため、タイムラインを長く編集した後に通常表示へ戻った時、人物がいない理由を発見しにくい。

**例3: 「話者を寄りで見せたい」**
`focusSpeaker`、`manualCameraFrame`、`emphasis`、`cameraEffects.zoom`、`zoomTarget` の少なくとも5経路がある。各々の継承・優先度・適用時間が異なるため、カメラ変更後に人物の相対位置を理解しにくい。

**例4: 「同じ配置を複数ターンへ適用したい」**
手動カメラには一括適用UIがある一方、人物の`manualPos`は継続前提であり、明示的な「この範囲に同じレイアウトを適用」「この時点で解除」が第一級操作として提示されない。コピー作成時も`manualPos`・`zoomTarget`・手動カメラは意図的に削除される（`story_editor.html:9699-9722`）。安全だが、利用者からは同じ配置を複製できないように見える。

**例5: 「現在の舞台状態を確認したい」**
配置タブは現在ターンのマーカーを見せるが、「在席人物、由来（enter/暗黙speaker）、有効slot、手動か自動か、次に解除される状態、カメラ解決結果」を一覧表示しない。100ターンでは、行の個別フォームだけでは原因追跡に不足する。

### UX上の推奨

1. 常時表示する「このターンの解決済み舞台状態」カードを作る。人物ごとに `在席 / slot / offset / 手動 / 素材姿勢 / z-order / 由来`、構図に `camera preset / override / effects` を示す。
2. 「登場」「退場」「移動」「表情変更」「話す」「声のみ」を別コマンドにする。話者選択が登場を暗黙に起こす旧仕様は新スキーマへ持ち込まず、UIでも明示操作にする。
3. 範囲選択して「このレイアウトを適用」「この時点でレイアウトを解除」「人物の在席をこの範囲で固定」を行えるようにする。
4. 表示種別変更時は、現在見えないが後続に効く状態を折りたたみで可視化し、保存時に黙って削除するのではなく差分確認を出す。

## 9. 不正・矛盾・サイレント無視状態

`_validate_story` は必須の `speaker/text/scene` とinsert形だけを確認する（`story_editor.py:288-306`）。型・参照・相互整合性の検証は行わない。次の評価で「可」は、直接JSON・AI取込・将来のUI経路を含む。

| 状態 | 可否・現在の結果 | 重要度 |
| --- | --- | --- |
| `hideCharacters=true`なのにspeaker | 可。音声・吹き出しは出せるが、在席は残る。`focusSpeaker`なら見えない話者へカメラが動き得る。 | 高 |
| 存在しないキャラをspeaker | 可。保存可。音声profileがなければ音声生成時に全体失敗。profileだけあれば画面には出ない声だけの話者になる。 | 高 |
| 非表示キャラにfocusSpeaker | 可。`hideCharacters`ではカメラだけ動き得る。モブspeakerの場合はroster外なのでfocusはdefaultへ静かに無視。 | 高 |
| 同じanchorに複数キャラ | 可。`cast` / `speakerAnchor`で可能。重なり・z-order警告なし。 | 高 |
| 表示種別で無効なanchor | 可。特殊表示では画面に映らないが後続状態へ継承する。未知anchorは描画時centerへフォールバック。 | 高 |
| 表示種別側と基本タブ側の配置が競合 | 部分的に可。ホワイトボードは`insert.character`、通常側は`expression/pose`。前者が画面に出るが後者は後続表情に影響し得る。 | 高 |
| 手動座標とanchorが競合 | 可。`manualPos`が優先し、`speakerAnchor`は隠れた将来のフォールバックになる。解除時に突然そちらへ戻る。 | 中 |
| scaleが複数箇所 | 可。scene.scale、scene.mobs.scale、mob.scale、表示種別専用レイアウトが別々。優先・意図がUIで一望できない。 | 中 |
| カメラと配置が互いに補正 | 可。配置は顔位置基準、カメラは顔逆算・anchor名・手動枠・zoomTargetを併用する。 | 高 |
| 退場済み状態が残る | 同一segment内では退場後に手動位置/anchorの設定自体はデータに残り、再enter時の扱いは「最後の状態が再使用」になる。scene境界でのみ自然に切れる。 | 中 |
| モブ識別子衝突 | `mobs.json`内の重複キーはJSON上不可能だが、主役名・voice profile名・既存モブ名との意味衝突は保存時に禁止されない。 | 中 |
| 素材にない表情を指定 | 可。主役はnormalへフォールバック、モブはnormal/agitatedの二値へ丸める。画面で気付きにくい。 | 高 |
| 後ろ姿に通常表情を指定 | 可。全身ずんだもんの角度素材と表情・ポーズの互換性を検証しない。素材能力モデルがない。 | 中 |

最重要なのは「設定できるがレンダリングで無視または別の意味になる」状態である。保存時の破壊的プルーニングも注意を要する。`pruneAllTurnsForDisplayType()` は特殊表示のターンからカメラ系を削除する（`story_editor.html:11295-11332, 9912-9923`）。UIが隠すだけでなく保存時にデータを消すので、表示種別を一時的に変更して保存すると、意図しない復元不能な情報損失になり得る。

## 10. データモデル上の問題

1. **IDの役割過多**: `speaker`文字列がキャラクター定義、在席個体、音声profile、描画対象を兼任する。
2. **可視性が派生しすぎる**: `visible`がなく、在席・退場・表示種別・scene設定を逆算しなければ現在状態が分からない。
3. **継承範囲がフィールドに散在**: `manualPosWaypoints`、`resolveAnchorMapAt`、`lastExpressionOf`、`effectiveInsertAt` が別々に状態を遡る。
4. **型が入力仕様を保証しない**: TypeScriptの`StoryTurn`は型を表すが、HTTP保存はPythonの最低限検証のみ。`speakerAnchor`、座標範囲、enter/exit参照、素材互換性を守れない。
5. **主役とモブの二系統**: 位置基準、素材、scale、表情、前景への入り方が違う。モブを増やすほど例外が増える。

## 11. レンダリング上の問題

- `buildSegments` がscene連続性を状態境界にするため、同じ背景でも「状態をリセットしたい」場合はtransitionを挿入する必要がある。一方、表情・向きは全scriptを遡るため、境界規則が一貫しない。
- `segmentRoster` は既知主役のみをレイアウト計算へ入れる。モブはカメラの人数・フォーカス対象にならず、3人構図にも参加しない（`StoryVideo.tsx:1044-1056`）。
- 3人超では`assignAnchors`が先頭3人だけを割り当てる。4人目以降はcenterフォールバックになり、衝突を起こす（`StoryVideo.tsx:858-870, 1020-1021`）。
- 同層の人物の重なり順が人物の奥行きではなく配列順に依存し、`zOrder`、前景の前後、遮蔽ルールがない。
- 通常ステージは `bg → chars → mobs → front` に固定される。人物ごとに「前景の後ろ/前」を指定できない。
- Remotionとしては、状態を毎フレーム純粋関数で解決する方向は正しい。ただしその純粋関数が巨大コンポーネント内に散在し、エディタで再実装されている。将来は共有可能な純粋なstate resolverへ抽出する価値がある。

## 12. カメラとの責務重複

カメラの「構図」と「演出」が分離されていない。

| 機構 | 本来の責務 | 現在の重複 |
| --- | --- | --- |
| scene `cameraFrames` | 背景・表示種別に対する標準構図プリセット | `focusSpeaker`がanchor名で選ぶ |
| `focusSpeaker` | 話者用プリセットを選ぶ意図 | true/falseだけでleft/rightに依存、モブ/特殊slotに拡張できない |
| `manualCameraFrame` | 例外的な明示構図 | ターンのみ有効で範囲適用が弱い |
| `emphasis` + `zoomTarget` | 話者を強調する演出 | 構図変更にも使われ、manual cameraとの合成結果が予測しにくい |
| `cameraEffects.zoom/pan/tilt` | 短時間の動的演出 | 同じscale/注視点を別経路で操作する |
| `manualPos` / scene.scale | 被写体配置とサイズ | 被写体を見せるために動かすと、カメラ側の問題を隠す |

推奨する責務は以下である。

- **Layout**: 人物のstage座標、scale、z-orderだけを決める。カメラを見て配置を補正しない。
- **Framing**: `cameraPresetId` または `manualFrame` がstageをどこまで映すか決める。`focusSpeaker`ではなく `framing: "speaker"` のように意図を表し、slot/instanceからプリセットを選ぶ。
- **Motion**: zoom/pan/tilt/shakeは確定したframingへ重ねる短時間演出だけにする。

## 13. 表示種別との責務重複

表示種別UIは改善されているが、データモデル上は `insert` が「全画面画面の内容」と「通常ステージを覆うか」を同時に担う。さらに特殊表示中も通常ステージの状態変更（enter/exit、表情、anchor）が後続へ効く。

- 通常表示は `StageLayout` を使用する。
- ホワイトボードは専用の `WhiteboardLayout` と`insert.character`を使用する。
- ビデオ会議は参加者タイルという別レイアウトを使用し、かつ前turnから部分継承する。
- ニュース、ミニ再現VTRなどを増やすと、通常ターンに置いた `speakerAnchor/manualPos/focusSpeaker` を「効かないが保持する」「消す」「内部状態だけ変える」のどれにするかを種別ごとに判断することになる。

推奨は、`DisplayMode` を明示して、各modeが以下を宣言することである。

```ts
type DisplayModeDefinition = {
  id: string;
  stage: "standard" | "replace" | "custom";
  supportedPlacementKinds: ("slot" | "manual")[];
  supportedSpeakerPresentation: "stage" | "tile" | "voice-only";
  layoutProfile: string;
};
```

この定義により、「このmodeでは通常ステージの人物配置は見えないが、在席状態は継続する」をデータを削除せずに表現できる。保存時プルーニングではなく、無効な設定への警告とUI無効化を基本にする。

## 14. 将来要件への耐性

| 要件 | 現在方式の耐性 | 理由 |
| --- | --- | --- |
| 3人同時表示 | 限定的 | left/center/rightまでは自動、カメラ・重なり・モブ含めた構図が未定義。 |
| 4人以上 | 不十分 | 自動anchorは3人で打止め。 |
| 同一キャラ複数個体 | 不可 | instance IDがない。 |
| 複数モブ | 限定的 | 定義追加は可能だが、固有座標と画像を個別作成し、配置・カメラから除外される。 |
| 後ろ姿 | 限定的 | ずんだもん全身の一部対応。素材能力・表情互換の検証なし。 |
| 座り姿・複数ポーズ | 限定的 | poseは話者ターン中心で、人物ごとの持続状態・サイズ基準がない。 |
| 前景の後ろ | 不可 | frontは全人物の前に固定。 |
| 奥行き・重なり | 不十分 | z-order/depthがない。 |
| 手動ドラッグ | 可能だが脆い | 実装済み。ただし顔/中心/足元基準が混在し、継承範囲が見えない。 |
| 背景別プリセット | 限定的 | scene anchors/castはあるが、表示種別・人数・役割別のprofileがない。 |
| 移動アニメ | 限定的 | manualPos waypointの0.6秒固定補間のみ。意図・イージング・経路を指定できない。 |
| 登場退場アニメ | 限定的 | 画面端slideの共通仕様のみ。 |
| 表示種別専用配置 | 個別実装 | ホワイトボード/ビデオ会議は個別に実装済みで、共通契約がない。 |
| 1/2/3人自動構図 | 限定的 | 主役のみの単純人数割当。 |
| 手動と自動の併用 | 限定的 | manualが全自動を覆い、slotからの相対offsetという中間表現がない。 |

## 15. 代替案

### 案1: 現在方式を整理して維持

`left/center/right`、`speakerAnchor`、`manualPos`、`enter/exit`を残す。まず明示的な `visible` を足さず、現行の派生モデルを仕様化する。

- `ResolvedStageState` を1関数に集約し、TSXとエディタで共有可能な純粋ロジックへ寄せる。
- `speakerAnchor`を「主役キャラのlayout anchor」、`manualPos`を「明示override」と文書化する。
- 参照整合性・anchor存在・同一anchor衝突・素材能力・無効表示種別を保存前に警告する。
- UIで解決済み在席/位置/カメラを可視化し、範囲操作を足す。

利点は最小移行で既存JSONがそのまま使えること。欠点はanchor名がカメラ意味を持つ根本問題、モブ別系統、特殊表示の独自レイアウトを温存すること。

### 案2: スロットベースのレイアウト（推奨）

各表示種別またはsceneが利用可能なslotを持ち、人物の配置は原則 `slotId` で指定する。slotは座標だけでなく、基準点・scale・z-order・カメラヒントを持てる。

```ts
type LayoutSlot = {
  id: "speakerLeft" | "speakerRight" | "presenter" | "center" | "backgroundLeft";
  anchor: {x: number; y: number};
  anchorPoint: "center" | "feet" | "face";
  scale?: number;
  zIndex?: number;
  cameraPreset?: "speakerLeft" | "speakerRight" | "default";
};

type InstancePlacement =
  | {mode: "slot"; slotId: string; offset?: {x: number; y: number}}
  | {mode: "manual"; point: {x: number; y: number}; anchorPoint: "face"};
```

- 通常表示: `speakerLeft`, `speakerRight`, `center`, `backgroundLeft/right`。
- ホワイトボード: `presenter` のみ、または専用character slot。
- ニュース: `anchor`, `presenter`, `guest`。
- ミニ再現VTR: `foregroundLeft/right`, `backgroundActor` 等。
- 新スキーマでは `slotId` を直接保存する。旧 `speakerAnchor` の読込・保存は行わず、旧台本は必要時に明示変換する。

重要なのは、slotを「キャラの役割」ではなく**画面構図内の場所**として扱うこと、`speaker`がslotを直接決めないこと、カメラはslotの`cameraPreset`を参照することだ。

### 案3: ステージ・レイアウトエンジン

人物状態、レイアウト文脈、配置結果を完全に分ける。

```ts
CharacterInstance + InstanceState + LayoutContext -> PlacementResult[]

LayoutContext = {
  displayMode, sceneId, backgroundVariant, activeInstances,
  speakerInstanceId, composition: "auto" | "solo" | "duo" | "trio",
  cameraIntent
}
```

`PlacementResult`はx/y/scale/z/layer/facing/camera subjectを返し、Remotionは結果だけを描く。自動構図と手動overrideはレイアウト計算の入力として統一できる。

これは複数個体・奥行き・3人以上・自動構図・アニメーションに最も強い。しかし表示種別ごとのルールを設計・テストする必要があり、現在の1〜2人会話に対して初期コストが高い。案2のslot仕様が安定してから必要部分を育てるべきである。

### 補足案4: 完全状態スナップショット方式

各turnに全人物の完全状態を保存する方式。読みやすく、任意ターン編集は予測しやすいが、80〜100ターンでJSONが肥大し、前ターンと同じ配置の編集が冗長になる。差分編集・範囲適用が必要な本プロジェクトには主案にしない。UI上だけ解決済みスナップショットを見せる用途には適する。

## 16. 比較表

評価: ◎ 強い / ○ 実用的 / △ 制約あり / × 不向き。実装規模は小さいほど◎。

| 観点 | 現状維持案 | スロット案 | レイアウトエンジン案 |
| --- | --- | --- | --- |
| UIの分かりやすさ | ○（状態可視化が前提） | ◎ | ○（UI設計次第） |
| 実装規模 | ◎ | ○ | △ |
| データ移行 | ◎ | ○（明示変換が必要） | △ |
| 既存JSON互換性 | ◎ | △（ランタイム互換なし） | △（明示変換が必要） |
| 通常表示 | ◎ | ◎ | ◎ |
| ホワイトボード | △（別実装維持） | ◎ | ◎ |
| 3人以上 | △ | ○ | ◎ |
| モブ対応 | △ | ○ | ◎ |
| 手動調整 | ○ | ◎（slot+offset） | ◎ |
| カメラとの分離 | △ | ○ | ◎ |
| 将来拡張性 | △ | ◎ | ◎ |
| 保守性 | ○（共有resolver化が前提） | ◎ | ○（規模相応の規律が必要） |
| 過剰設計リスク | ◎ | ○ | △ |

## 17. 推奨案

**案2を採用し、案1の安全化を第1段階として先に行う。旧JSONのランタイム互換は持たない。**

理由:

- 主に1〜2人会話ならslotは `speakerLeft/speakerRight/center` 程度で十分で、編集者にとって「leftという座標」より「話者左枠」の方が理解しやすい。
- ホワイトボード、ニュース、ミニ再現VTRはそれぞれ異なるslot集合を持てるため、通常表示のanchor規則を無理に流用しない。
- slot + offsetなら自動配置を保ちつつ、背景固有の微調整やドラッグを残せる。
- 新形式を一貫して実装できる。旧台本が必要な時だけChatGPTで変換し、ローカル検証が変換結果を受け入れるか判断する。
- Remotionでは、先に純粋な `resolveLayout()` が配置結果を返し、描画はその結果を使う形にすると、プレビューと書出しを同じ解決器で揃えやすい。

案3へ即時移行しない理由は、「同一人物複数個体・任意の奥行き・自動3人構図・高度な移動アニメ」が現時点で必須ではないためである。案2の内部表現を `instanceId` / `slotId` 中心にしておけば、必要になった時だけ案3へ拡張できる。

### 採用する移行方針

- 新しい台本は `schemaVersion` を必須とし、新レンダラー・新エディタは新スキーマだけを読む。
- 旧 `speakerAnchor` / `manualPos` / `focusSpeaker` / `manualCameraFrame` を新実装へ持ち込む互換分岐は作らない。
- 旧台本が必要になった時は、その都度ChatGPTで新スキーマへ変換する。変換前のJSONは原本として残す。
- 変換後にはローカルの構造・参照・競合バリデーションを必ず実行する。AI変換の結果を無検証では保存・レンダリングしない。
- 変換で意図が一意に決まらない箇所（例: 旧カスタムanchorのslot意味、手動座標をslot+offsetにするかmanualにするか）は、検証結果で要確認として返す。

## 18. 段階的移行手順

### 第1段階: 新スキーマと検証を決める（旧JSON互換は作らない）

1. `schemaVersion`、CharacterDefinition、StageInstance、InstanceState、slot、framing、DisplayModeの新JSON仕様を確定する。
2. 旧フィールドとの一対一変換が可能な項目と、人の確認が必要な項目を変換仕様として文書化する。
3. 新JSONだけを検証するローカルvalidatorを作る。存在しないinstance/slot/表示種別、同slot競合、素材非対応、無効な構図指定をエラーまたは要確認として返す。
4. `test_story_editor.py`相当のfixtureを新スキーマ用に用意する。旧スキーマの互換テストは追加しない。
5. 旧台本は、必要になった時だけChatGPTで新形式へ変換し、原本と変換結果を別ファイルとして管理する。

### 第2段階: anchorをslotへ再定義する

1. 新形式のscene定義へ `layouts.standard.slots` を導入する。既存`anchors`は新方式で使用しない。
2. 新規UIは `配置: スロット`、`微調整: X/Y offset`、`手動配置に切替` を出す。
3. 新形式の台本では `speakerAnchor` を保存・読込しない。
4. slotに `anchorPoint`、`zIndex`、`cameraPreset` を持たせ、主役・モブの基準点差をslot/asset側で吸収する。
5. カメラUIを `構図プリセット` / `話者を主役にする` / `手動構図` / `短時間演出` に分離し、旧 `focusSpeaker` は新スキーマでは廃止する。台本には `framing`（`sceneDefault` / `speaker` / `slot` / `manual`）だけを構図指定として保存し、`zoom` / `pan` / `tilt` / `shake` は `cameraMotion` として構図に後から重ねる。

### 第3段階: 表示種別ごとのレイアウトを統一する

1. `DisplayModeDefinition`を導入し、通常・ホワイトボード・ZunMeet・ニュース・ミニ再現VTRが利用する`layoutProfile`を宣言する。
2. 表示種別では見えない通常stage状態を、削除せず「後続に持ち越す状態」として状態カードに表示する。
3. ホワイトボードの`insert.character`、ビデオ会議のparticipantsを、可能な範囲で`instanceId`と共通の素材能力参照へ寄せる。
4. 「現在の表示種別で使えない設定」は入力不可にしつつ、既存データは警告・保持する。意味を失わせる移行だけは明示操作にする。

### 第4段階: 必要になった機能だけ拡張する

- 背景ごとのslot profile、3人用composition、前景後ろレイヤー、z-order。
- `instanceId`導入によるモブA/B・同一キャラ複数個体。
- 移動/登退場を `from/to/duration/easing` の明示animationへ発展。
- ドラッグはslot offsetを基本にし、手動座標は例外overrideとして維持する。

## 19. 実装前に決めるべき仕様

1. **在席と可視性**: **決定済み。** `present`（登場・退場からresolverが導く在席）と `visible`（表示種別・slot・一時非表示から導く描画結果）を分離する。台本へ毎ターン両方を保存せず、通常は登場・退場のみを操作する。ナレーションはspeaker roleの一種とする。
2. **状態の保存形式**: **決定済み。** 台本JSONは差分イベントとして保存する。レンダラー・プレビュー・検証用resolverが、任意ターンの完全な `ResolvedStageState` を出力する。完全状態は必要時の出力・デバッグ・検証に使い、台本へ重複保存しない。
3. **継承境界**: **決定済み。** scene変更でstage状態をリセットする。表示種別変更では在席を維持し、明示resetイベントで個別状態を解除できるようにする。transitionは状態境界にしない。
4. **特殊表示中の状態変更**: **決定済み。** 登場・退場・表情・slot変更を許可し、通常stageの状態だけを更新する。専用表示に出す人物は表示種別固有の規則で別途描く。通常stageに見えない変更はUIで明示する。
5. **同一slotの重なり**: **決定済み。** 通常slotは1人までにする。背景用slotだけ複数を許可し、`zIndex` を明示指定する。
6. **人物モデルの初期範囲**: **決定済み。** 主役とモブを同じ `StageInstance` モデルへ載せる。第1段階のUIは主役2人と既存モブ1体程度から始めるが、型・resolver・validatorは全人物に共通化する。
7. **登場・退場**: **決定済み。** 話者選択による暗黙登場は廃止し、登場・退場は明示イベントとして保存・編集する。
8. **手動配置の単位と操作点**: **決定済み。** 台本・slotの座標は人物の論理接地点 `stageOrigin` として保存する。素材側は画像内の `stageOrigin` とカメラ用 `faceOrigin` を持ち、既定は下端中央とする。座り姿等は素材メタデータで接地点を上書きできる。なお、ドラッグで掴む位置は `stageOrigin` に固定しない。選択済み人物は本体の可視領域を直接ドラッグでき、内部ではポインタ移動量を `stageOrigin` の移動量へ変換する。選択ハンドルを出す場合も、可視領域の身体中心付近に置く。画面外・カメラ外・前景に隠れた人物は、人物一覧から選択し、数値入力・矢印キー・カメラを外したステージ全体表示で操作できるようにする。
9. **カメラのデータ形式**: **決定済み。** `framing` を構図の唯一の入口とする。`{ mode: "sceneDefault" }`、`{ mode: "speaker" }`、`{ mode: "slot", slotId }`、`{ mode: "manual", frame: { cx, cy, width } }` のいずれかを保存する。`speaker` は現在の話者そのものではなく「現在の話者のslotに紐づく構図プリセットを使う」という構図指定であり、話者や在席を変更しない。`cameraMotion?: { zoom?, pan?, tilt?, shake? }` は確定済みの構図へ時間限定で重ねる演出とし、配置・在席・話者を補正しない。UIも「構図プリセット」「話者を主役にする」「手動構図」「動き」の別操作にする。
10. **第1段階の表示種別範囲**: **決定済み。** 通常表示だけを新方式へ移す。ホワイトボード・ZunMeet等は新しい`DisplayMode`契約だけ先に定義し、実装移行は第2段階にする。
11. **変換運用**: 旧台本はChatGPTで都度変換し、原本を残す。変換後JSONを保存・レンダリングする前に、ローカルvalidatorがどの条件をエラー／要確認として扱うか。

## 20. リスクと対策

| リスク | 対策 |
| --- | --- |
| 旧台本の変換結果が意図と違う | 旧JSONは原本として残し、変換後JSONをローカルvalidatorと構図プレビューで確認する。ランタイムの旧形式互換は持たない。 |
| エディタとRemotionの乖離 | 座標・在席・カメラ解決を共有可能な純粋ロジックに切り出し、双方が同じテストfixtureを読む。 |
| 新モデルが重すぎる | 第2段階では1〜2人用slotのみを実装し、instance複数化・auto compositionは第4段階まで実装しない。 |
| JSONが冗長になる | 通常はslot+optional offsetのみ。manual座標・animationは必要なturnだけ差分保存する。 |
| 特殊表示追加ごとに例外が増える | DisplayModeDefinitionの契約（stage/slot/speaker presentation）を先に埋め、未宣言のmodeは警告する。 |
| 保存時の自動削除で作業を失う | 無効値は原則保持、明示的な「このmode向けに不要設定を消す」操作だけで削除する。 |

## 21. 未確認事項

以下はコード上の経路を確認したが、実素材・実フレームで網羅確認していないため、実装判断前にfixtureと静止画で確認が必要である。

1. 全身・後ろ姿素材で、表情/pose指定がどの組合せまで期待通りに描画されるか。
2. `InsertVideoCall`内部で参加者の表情・ポーズ・話者との関連をどこまで独自に持つか。通常stageとの共通化範囲。
3. `story-scenes.json`に現在定義される全sceneの`anchors`、`cast`、`mobs.hidden`が実動画でどのように使われているか。
4. 現在編集中の`story-01.json`で、manualPos / speakerAnchor / enter/exitの実使用頻度と、新スキーマへの変換時に判断が必要になる箇所。
5. ニュース表示・ミニ再現VTRを、全画面置換にするか、通常stage上のレイアウトprofileにするかというプロダクト判断。
6. 画面外話者を「声のみ」で見せる需要と、ナレーションとのUI上の区別。

## 22. 最終判断

- **現在方式の最大の問題**: 人物の在席・配置・話者・カメラ・表示種別が同じターン差分と暗黙継承に混在し、anchor名までがカメラ意味を持つため、長編編集で結果を予測しにくいこと。
- **現状維持で済むか**: バリデーション、解決済み状態の可視化、責務表の整備までは現状維持で対応可能。ただし特殊表示の増加、複数モブ、3人以上を安定運用するには不十分。
- **推奨する設計**: 新スキーマ専用の、表示種別・sceneごとの名前付きスロット + offset/manual overrideへ移行する案2。旧台本は必要時にChatGPTで変換し、ローカルvalidatorで確認する。
- **最初に直すべき3点**: (1) 保存時の参照・矛盾バリデーションと破壊的プルーニング見直し、(2) 解決済み舞台状態の一元化・UI可視化、(3) `left/right/center`をカメラ意味から切り離すslot契約の導入。
- **実装前にユーザーが決める必要があること**: 在席/可視性の分離、特殊表示中に通常stage状態を変更する扱い、同slot重複/z-order、モブを主役と同じ人物モデルへ統合する範囲。

---

# 追補: モブキャラ編集・シーンエディタのレビュー（2026-07-15 追記）

本編（1〜22節）は台本編集とレンダリングを対象とした。ここでは関連ツールである **モブキャラ定義編集**（`story_editor.html` 「モブキャラ」タブ + `story_editor.py /api/mobs`）と **シーンエディタ**（`scene_editor.html` / `scene_editor.py`）を、同じ観点で追加調査した結果を示す。実装変更は行っていない。

## 23. この2ツールの位置づけと調査根拠

配置に関わる設定は、実は **3つのファイル・3つのUI** に分散している。同じ「モブの位置」でも編集場所が3層あり、優先順位は本編5.51・9.212で述べた通り `turn.manualPos` > `scene.mobs[id]` > `mobs.json.anchor` > シーン既定である。

| 対象 | 何を編集するか | UI | 保存先 | 保存検証 |
| --- | --- | --- | --- | --- |
| モブ定義 | scale・既定anchor（足元）・口開閉画像・VOICEVOX話者 | `story_editor.html:12400-12542`（`makeMobCard`） | `mobs.json` | `story_editor.py:100-128`（`_save_mobs`） |
| モブのシーン別配置 | `scene.mobs[id] = {x,y,scale,hidden}`（D&D） | `scene_editor.html:1147-1200` | `story-scenes.json` | `scene_editor.py:106-127`（`_save_scenes`） |
| シーン構図 | 背景・front・figure・`anchors`・`cameraFrames`(default/leftFocus/rightFocus)・`cast` | `scene_editor.html` | `story-scenes.json` | 同上 |

観察した主な責務境界:

- **cast は主役キャラ専用**。`buildCastRows` は `assets.characters`（manifestのアバターキー＝zundamon/metan等）だけを列挙し、モブは対象外（`scene_editor.html:1082-1113`）。モブの立ち位置は `cast`/`anchors` ではなく `scene.mobs` の座標系で完全に別管理。本編の「主役とモブの二系統」が、UI階層でも物理的に分離していることを確認した。
- **カメラ枠の3枠は正しくWYSIWYG化されている**。`cameraFrames.default/leftFocus/rightFocus` をD&Dで編集し、`focusSpeaker` の切替をプレビューできる（`scene_editor.html:444-635, 743-773`）。ヒント文も「話者フォーカスONのターンでは話者の立ち位置に応じて枠が切り替わる」と正しい（631行）。

## 24. 追加で判明した設計上の問題

### 24.1 anchor名の自由追加が、カメラ・自動配置と整合しない【重要・本編5章を裏づける実経路】

`addAnchor()`（`scene_editor.html:1864-1875`）は `^[a-zA-Z0-9_-]+$` を満たす任意名のanchorを追加できる。しかし追加したanchor（例 `windowLeft`）は次のいずれにも乗らない:

- `focusSpeaker` は `left`/`right` のときだけ `leftFocus`/`rightFocus` を選ぶ。それ以外は無言で `default`（`StoryVideo.tsx:3380-3385`）。
- 自動人数割当 `assignAnchors` が生成するのは `center`/`left`/`right` のみ（`StoryVideo.tsx:859-871`）。カスタムanchorは `cast` か `speakerAnchor` 経由でしか有効にならない。
- `speakerAnchor` のプルダウンはシーンのanchor名を全列挙する（`story_editor.html:6472-6492`）ので、カスタムanchorを選べてしまう。選ぶと立ち位置は変わるがカメラフォーカスは効かない。

つまり本編5章「anchor名がカメラ意味を兼ねる」問題は理論上の懸念ではなく、**シーンエディタの正規操作で実際に到達できる不整合**である。UIは作れるが、レンダラーが名前を特別扱いしているため半分しか機能しない。

### 24.2 三重ミラー実装（保守リスクの増幅）

本編4章で「エディタとRemotionの二重実装」を挙げたが、シーンエディタが加わり **三重** になっている。`faceCyOf` / `FACE_RATIO` / `fullBoxSize` / `previewBaseCamTf`（顔中心・カメラ変換の同式）が `StoryVideo.tsx`・`story_editor.html`・`scene_editor.html:900-950` の3か所に個別実装されている。座標・カメラ仕様を1つ変えるたびに3ファイルを同期させる必要があり、片方だけ直すと「シーンエディタのプレビュー・台本エディタのプレビュー・本番書出し」の三者で見た目がずれる。本編の共有 `resolveLayout()` 抽出は、この3ツール全てが同じ純粋関数を読む形にするのが本来の狙いになる。

### 24.3 モブ定義のクロスファイル整合性がない【重要】

`makeMobCard` はモブ名（＝`mobs.json` のキー）をテキスト入力で編集でき、削除ボタンもある（`story_editor.html:12409-12473`）。しかし:

- **リネーム伝播がない**。モブ名を変えて保存すると `mobs.json` のキーだけ変わり、`story-01.json` 側の `speaker`/`enter`/`exit`、`story-scenes.json` 側の `scene.mobs[oldName]` は旧名のまま残る。結果、台本の話者が「音声未設定」になり、シーン配置は宙に浮く。
- **削除も同様**にダングリング参照を残す。`_save_mobs`（`story_editor.py:100-128`）は画像とvoice.speakerの型しか見ず、参照整合性を検証しない。
- **名前衝突を止めない**。主役名（zundamon/metan）や `voice_profiles.json` の話者名と同じモブ名を保存できる。`known_actors` 判定はAI取込経路（`story_editor.py:932-934`）にしかなく、通常保存では働かない。
- `mobs.json` に定義がある `flip`（`StoryVideo.tsx:457`、描画で `scaleX(m.flip?-1:1)` として使用 `4462`）を編集するUIが存在しない。**孤立フィールド**で、左右反転はJSON直書きでしか設定できない。

### 24.4 表情モデルがモブと主役で不一致

モブの画像状態は `makeMobCard` が `normal` / `agitated` の2キーをハードコードで生成する（`story_editor.html:12527-12539`）。一方で台本の `expression` は主役向けの多値（normal/surprise/happy/...）。`mobImage`（`StoryVideo.tsx:613-663`）はこれを2値へ丸めるため、モブに `happy` を指定しても静かに `normal` として描かれる。本編9章「素材にない表情を指定→サイレント無視」の、モブ側の具体経路である。

### 24.5 配置既定値が3層で微妙に食い違う

同じ「モブの初期位置」でも既定値が層ごとに違う: シーンエディタの新規配置は `y:0.98`（`scene_editor.html:1155,1175`）、`mobs.json` の既定anchorは `y:0.95`〜`0.99`、`StoryVideo.resolveMobXY` のフォールバックは `y:1.0`（`StoryVideo.tsx:1036`）。実害は小さいが、「どこで位置が決まっているか」をさらに追いにくくしている。

### 24.6 保存検証は3ファイルとも最小限（本編9章と同じ弱さ）

`_save_scenes`（`scene_editor.py:106-127`）は bg/bgVideo の文字列有無しか見ず、`anchors` の座標範囲・`cast` の参照先・`cameraFrames` の値・`scene.mobs` が指すモブの実在を検証しない。`_save_mobs` も同様。3つのエディタのどれも「他ファイルとの参照整合性」を持たないため、モブ・シーン・台本のどれか一つを編集すると他ファイルの前提が静かに壊れ得る。

### 24.7 シーンエディタのプレビューは「シーン既定値」のみで、台本の実際の状態は再現しない【重要】

シーンエディタは有用なプリセット編集器だが、台本プレビューではない。`renderPreview()` はsceneの `cast` と `scene.mobs` を描く（`scene_editor.html:1079-1113, 1149-1204, 1241-1370`）だけで、次を入力に取らない。

- 同一scene内の登場順から決まる `segmentRoster` / `assignAnchors`
- ターン上の `speakerAnchor` と `manualPos`
- `enter` / `exit` によるその時点の在席
- 現在の話者、`hideCharacters`、表示種別、モブの実際の話者状態

さらに「＋ズームも重ねる」プレビューは、現在話している人物ではなく `cast` のソート先頭キャラクターを選ぶ（`currentPreviewTf`: `scene_editor.html:930-950`）。本番の `focusSpeaker` はターンのspeaker・在席・解決済みanchorを参照するため、これは同じ構図を保証しない。

したがって、このツールを **「背景別のslot・素材・標準カメラ枠を設計する場所」** と明示し、台本の実構図確認はstory editor側のRemotion Playerで行う、という役割分担をUIにも明記すべきである。将来共有resolverを導入する際は、シーンエディタにも「代表speaker/代表rosterを選ぶプレビューコンテキスト」を渡せるようにすると、WYSIWYGの主張を限定的に成立させられる。

### 24.8 シーン削除は警告のみで、台本参照を残せる

`deleteScene()` は台本で使用中だと書出しが壊れる旨を確認ダイアログに表示するが、参照を検索・置換・保存拒否しない（`scene_editor.html:1835-1858`）。`_save_scenes`も同じ参照を検証しないため、削除を確定保存すると、`StoryVideo`は該当turnで「未登録シーン」プレースホルダを描画する。モブのリネーム/削除と同じく、これはクロスファイル参照整合性の問題として扱うべきである。

## 25. この2ツールで作れる不正・矛盾状態（本編9章の追補）

| 状態 | 可否・結果 | 重要度 |
| --- | --- | --- |
| カスタムanchorに `speakerAnchor` を向ける | 可。立ち位置は動くが `focusSpeaker` はdefaultに無言フォールバック（24.1） | 高 |
| モブをリネーム/削除して台本参照を残す | 可。話者が音声未設定化・シーン配置が宙に浮く。検証なし（24.3） | 高 |
| モブ名を主役名/voice profile名と衝突させる | 可。通常保存では警告なし（24.3） | 中 |
| `scene.mobs` に削除済みモブのエントリが残る | 可。UIに出ず放置される（stale） | 低 |
| モブに主役用の多値表情を指定 | 可。2値へ丸めて別表情に静かに化ける（24.4） | 中 |
| `cameraFrames.width` を過小/過大に設定 | 可。範囲検証なし。極端値で構図破綻 | 低 |
| モブ左右反転を設定したい | UI不可。`flip` は孤立フィールドでJSON直書きのみ（24.3） | 低 |
| sceneを削除して台本が旧sceneを参照し続ける | 可。確認文だけで保存可。レンダリング時に未登録scene表示となる（24.8） | 高 |

## 26. 推奨（本編の段階移行への差し込み）

本編の第1〜3段階に、この2ツール分を次のように織り込むのが最小コストで効果が高い。

- **第1段階（新スキーマと安全化）に追加**:
  1. 保存前クロスファイル検証を1か所に集約する。`mobs.json` 保存時に「削除/リネームされるモブが台本・シーンで参照されていないか」、`story-scenes.json` 保存時に「`cast`/`scene.mobs` の参照先が実在するか」「カスタムanchorが `focusSpeaker` 非対応である旨」を警告として返す。
  2. モブのリネームは、キー変更ではなく「参照を一括置換する明示操作」として提供する（`speaker`/`enter`/`exit`/`scene.mobs` を横断更新）。
  3. シーン削除は参照turn一覧を表示し、「削除を中止」「台本のsceneを一括置換」「参照を残したまま削除」の明示選択にする。
  4. `flip` にUIを与えるか、使わないなら型・データから外す（孤立解消）。
- **第2段階（anchor→slot）に追加**:
  5. シーンエディタの `addAnchor` を「自由名anchor追加」から「slot定義（座標＋anchorPoint＋cameraPreset＋zIndex）」へ格上げする。`speakerLeft` / `speakerRight` / `center` などを新スキーマの正式slotとして定義し、カスタムslotにも `cameraPreset` を持たせれば24.1の不整合が原理的に消える。
  6. モブの立ち位置を `cast`/slot と同じ配置モデルに寄せ、主役／モブの座標基準点の差はslot側の `anchorPoint`（center/feet/face）で吸収する（本編7章のStageInstance統合の第一歩）。
- **第2〜3段階を通して**: `faceCyOf`/カメラ変換の三重実装を共有 `resolveLayout()` へ集約し、3ツールが同一の純粋関数＋同一fixtureテストを読む形にする（24.2の恒久対策）。
- **シーンエディタの役割を明確化**: 第1段階では「scene既定値のみを表示」と明記する。第2段階以降は代表speaker・代表rosterを選択して共有resolverで確認できるプレビューにする（24.7）。

## 27. この追補の最終判断

- **モブ編集・シーンエディタ単体は、1〜2人＋少数モブの現行運用では実用的**。特にカメラ3枠のWYSIWYGは良くできている。
- ただし **3ツール（台本・モブ・シーン）が参照整合性を持たず、配置ロジックが三重実装** である点が、本編で指摘した「配置・カメラ・表示種別の責務混在」を運用面でさらに増幅している。
- **この2ツールで最初に直すべき3点**: (1) クロスファイル参照検証（モブのリネーム/削除、scene削除を含む）、(2) カスタムanchorとカメラ／自動配置の整合（slot化で恒久解決）、(3) 顔中心・カメラ変換の三重ミラーと、台本状態を再現しないシーンプレビューの責務整理。
- いずれも本編推奨の「案2＝slot化＋共有resolver＋保存時バリデーション」の射程内にあり、別設計を新たに起こす必要はない。
