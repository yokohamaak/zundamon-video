# ストーリーツール 引き継ぎ仕様書（新ツール側のみ）

対象は **ずんだもん/四国めたんのストーリー（会話劇）動画ツール**。
既存の掛け合いダイジェスト系（DialogueVideo / review_* / digest）は対象外。
リポジトリ: `zundamon-video`（main）。`/workspace` は Mac ディスクのマウント＝編集は即 Mac に反映。

---

## 1. 概要
- 台本(JSON)→VOICEVOX音声→Remotion描画 で、立ち絵の会話劇動画を作る。
- 世界観: 株式会社ずんだシステムズ(IT何でも屋)。ずんだもん=新人/善意の暴走、四国めたん=冷静な先輩。
  障害対応ストーリーが主。詳細は `docs/story-world.md`。
- ローカル完結・無料のみ。**従量課金/外部送信は禁止**（AIへの台本依頼はユーザーが手動コピペ）。

## 2. ファイルマップ
- 描画(Remotion):
  - `video/src/StoryVideo.tsx` … 本体。シーン/立ち絵/カメラ/回想/インサート/BGM/SE 全部ここ。
  - `video/src/Avatar.tsx` … 立ち絵パーツ合成(口パク/まばたき/表情/オーバーアクション)。
  - `video/src/story-player.tsx` … エディタ用 Remotion Player ラッパ(window.storyPlayer API)。
  - `video/src/Root.tsx` … render用エントリ(Composition登録)。
- ローカルツール(Python標準ライブラリのみ・http.server):
  - `story_editor.py`(:8771)+`story_editor.html` … 台本エディタ(中心)。
  - `scene_editor.py`(:8770)+`scene_editor.html` … シーン(背景/アンカー/モブ)編集。
  - `expression_editor.py`(:8772)+`expression_editor.html` … 表情パーツ編集。
- パイプライン(node):
  - `video/scripts/psd-export.mjs` … PSD→パーツPNG書き出し(build/build-full/candidates)。
  - `video/scripts/prep-story.mjs` … assets/→public/ コピー＋manifest.json生成。
    **story エディタ起動時に自動実行**(`./run-story prep` でも手動可)。素材を足したら反映される。
  - `video/scripts/build-story-player.mjs` … story-player.tsx を esbuild→`public/story-player.js`。
  - `video/scripts/expression-catalog.mjs` … 表情一覧PNG生成(確認用)。
- 音声: `make_story_audio.py`(VOICEVOX)→`src/tts_voicevox.py`(synthesize_dialogue)。
- データ(`video/public/`・gitignoreだが force-add で追跡):
  - `story-01.json`(台本) / `story-scenes.json`(シーン) / `expressions.json`(表情) / `se-map.json`(SE自動連動)。
  - `story-01.wav/.mp3`(音声) / `avatars/manifest.json` / `avatars/<char>/...`(パーツ) / `bgm/` `se/`。
- 設計書: `docs/expression-editor-plan.md` `docs/bgm-se-plan.md` `docs/story-world.md`
  `docs/new-effect-checklist.md`(演出追加時の更新箇所)。

## 3. データモデル

### story-01.json（StoryScript）
```
{ "title": str, "audio": "story-01.mp3",
  "idleFace": "normal"|"hold",     // 聞き役表情の扱い(任意)
  "bgm": [ BgmRegion ... ],        // 時間ベースBGM区間(任意)
  "overlays": [ StoryOverlay ... ],// 画像/字幕Overlay(任意)
  "script": [ StoryTurn ... ] }
```
**StoryTurn**（必須: speaker,text,scene。start/end/sentencesは音声生成で自動付与）:
```
id, speaker("zundamon"|"metan"|"営業"|"部長"|"AI"), text, scene,
expression?(expressions.jsonのキー), pose?, enter?([charId...]), exit?([charId...]), exitDir?("left"|"right"),
face?({charId:"left"|"right"}), transition?, emphasis?(bool=話者ズーム), shake?(bool), flashback?(bool),
cameraEffect?, telop?(str 例"― 前日 ―"), pause?(秒), insert?(StoryInsert), se?([{file,at?,volume?}]),
narrationVoice?("棒読み男"|"棒読み女"), voice?({speed,pitch,intonation,volume}),
noLipSync?(bool=この行だけ口パク停止),
continueBubble?(bool), disableAutoBubbleSplit?(bool),
start,end,sentences (←音声生成で自動)
```
**StoryInsert**（全画面PC画面風オーバーレイ・Zunブランド）:
```
{kind:"warning", title?, text}          // ZunMonitor(ダーク/緑枠/⚠amber)
{kind:"ok", text?}                       // ZunMonitor(緑)
{kind:"chat", user, ai:[...], highlight?}// ZunAI(ダーク/緑)
{kind:"teamchat", channel?, messages:[{from,text,highlight?}]} // ZunChat(ライトテーマ)
{kind:"mailer", from?, fromAddr?, subject, body, time?}        // ZunMail(ライト/緑)
```
**BgmRegion**（時間ベース・タイムラインD&D編集）:
```
{ start:秒, end:秒, file:"bgm/x.mp3", volume?(既定0.25), fadeIn?(既定0.6), fadeOut?(既定0.6) }
```
story.bgm が非空ならBGMの唯一の真実(区間の隙間=無音)。空なら scene.bgm(シーン連動)にフォールバック。

### story-scenes.json（SceneLibrary）
```
{ "scenes": { "<key>": SceneDef } }
```
**SceneDef**:
```
label?, bg("background/x.png"), front?(前景PNG・任意), figure?("bust"|"full"・既定full),
shot?("solo"|"duo"|"split"), camera?("static"|"slow-zoom"),
scale?(立ち絵倍率), transition?("fade-black"|"cut"),
anchors:{ center/left/right:{x,y(0..1・立ち絵中心)} }, cast?({charId:アンカー名}),
mobs?:{ "営業"|"部長":{x,y,scale?,hidden?} }, mobAnchor?, mobHeight?,
bgm?("bgm/x.mp3"), bgmVolume?    // シーン連動BGM(時間ベースbgm[]が無い時のみ有効)
```

### expressions.json（ExpressionsMap）
```
{ "zundamon":{ "<expr>": ExpressionCfg }, "metan":{...} }
```
**ExpressionCfg**: `{ brow, cheek, shadow?, eye, mouth_close, mouth_half, mouth_open, fx }`
- 値は各スロットの id(例 brow="worry1")。Avatarが `<slot>_<id>` のstemを manifest から引く。
- 組み込み表情: normal/happy/surprise/trouble/panic。任意追加可(表情エディタ)。
- 重ね順: **base→shadow→cheek→arm→eye→mouth→bangs→brow→fx**（眉は髪より前面）。
  顔色グループ内の z は かげり(shadow)→青ざめ(cheek) の順(shadowが下)。

### se-map.json（SE自動連動）
```
{ "expression":{"surprise":{file,volume,enabled},...},
  "effect":{"shake":{...},"flashback":{...},"emphasis":{...}},
  "insert":{"warning":{...},...}, "transition":{"fade-black":{...}} }
```
enabled & file非空 のものを、該当イベント発生時刻にワンショット再生。

## 4. ローカルツール

### story_editor（:8771）台本エディタ＝中心
- 6系統: **台本 / シーン(iframe:8770) / 表情(iframe:8772) / ポーズ / 台本生成 / 音(BGM/SE)**。
- 中央=Remotion Playerプレビュー(`/story-player.js`)。プレビュー上に**ズーム**(フィット/100-400%)。
- プレビュー下に**タイムライン(canvas)**: 上=セリフ(話者色), 中=BGM, 下=Overlay。クリックでシーク・再生ヘッド追従・
  横ズーム(フィット/2-16x)。**BGMレーンはD&D編集**(空きドラッグ=区間作成/本体=移動/端=リサイズ・
  0.05秒スナップ)。Overlayも区間選択/移動/リサイズ可。直下のエディタで start/end/音量/フェード/削除/試聴▶ まで編集する。
- 右=詳細(基本タブ: 話者/ナレーション/セリフ/シーン/表情/ポーズ/演出/吹き出し/enter-exit/手動SE、インサートタブ)。
- 左=ターン一覧。保存=💾(POST /api/story)。音声生成=🔊(VOICEVOX必要・進捗ストリーム)。
- 「台本生成」: 主題等→ローカルでプロンプト生成(現対応のシーン/キャラ/表情/演出/インサート/スキーマ＋
  `docs/story-world.md`込み)。実験版(新演出も提案)あり。AI出力JSONを貼付→取り込み(未対応演出を検出表示)。
  現行プロンプトは `transition` / `pose` / `narrationVoice` / `voice` / `noLipSync` / `continueBubble` /
  `disableAutoBubbleSplit` / `se` まで案内する。
- 「音」: SE自動マッピング編集 / シーン別BGM割当 / プレビュー再生。各ファイルに試聴▶。
- API: `/api/story`(GET/POST) `/api/meta` `/api/audio`(SSE的ストリーム) `/api/script-prompt`(POST)
  `/api/import-script`(POST) `/api/audio-assets` `/api/se-map`(GET/POST) `/api/scene-bgm`(GET/POST)。
- 配信: `/story-player.js`(no-store) `/preview-assets/<path>`(許可: avatars/background/mobs/bgm/se/fonts
  ＋ story-scenes.json/expressions.json/se-map.json/noise.png/story-01.*) `/img/<path>`。

### scene_editor（:8770）
- 背景プルダウン(assets/background→public同期)、アンカーD&D、モブD&D/ON-OFF、figure/scale。
- プレビューは manifest から base/cheek/arm/eye/mouth/bangs/brow を重ねて表示。

### expression_editor（:8772）
- キャラ×表情ごとに 目/眉/顔色/影/口/fx を候補サムネから選択→合成プレビュー→保存→「書き出し」。
- 候補PNGは `psd-export candidates` が `assets/avatars/<char>/candidates/`(gitignore) に出力、起動時自動生成。
- 表情の追加/削除可(両キャラに反映・組み込み5種は削除不可)。

## 5. 描画(StoryVideo)の主な機能
- シーン区間化(連続同scene)、場面転換(fade-black)、カメラ(人数で寄り引き＋slow-zoomドリフトはrender時のみ＋
  emphasisズーム＋surprise/panicリアクション寄り＋shake)、回想(白ディゾルブ＋彩度0.4＋グレイン＋左上テロップ)、
  登場/退場スライド、立ち絵口パク(useWindowedAudioData)/まばたき/オーバーアクション、
  インサート(全画面・**背景は即カバーしフェードしない＝通常画面のチラ見え防止**・パネルのみフェード)、
  BGM/SE(Audio層・loop/fade)。
- インサートは active ターンの[start,end]＋後続無音中も表示(activeTurnAtは開始時刻基準)。

## 6. ビルド/実行（`./run-story <cmd>`）
- `player-build` … エディタ用Playerをesbuild(**StoryVideo/Avatar/story-player を変更したら必須**)。
- `story` / `scene` / `expr` … 各エディタ起動(story起動時にscene/exprを自動起動)。
- `audio` … VOICEVOX(:50021起動必須)で音声生成＋start/end書き戻し。
- `dev` … Remotion Studio(HMR)。 `render` … out/story.mp4 書き出し(最終確認)。 `still <frame>` … 静止画。
- パーツ書き出し(手動): `cd video && node scripts/psd-export.mjs build|build-full|candidates <zundamon|metan>`
  → `node scripts/prep-story.mjs` → `node scripts/build-story-player.mjs`。

## 7. 反映の注意（重要）
- コード(StoryVideo/Avatar/story-player)変更 → `player-build` → ブラウザ**リロード**(story-player.js は
  キャッシュバスター付き)。render(`npx remotion still/render`)はソース直バンドルなので player-build 不要。
- expressions/scenes/se-map は `/preview-assets/` 配信。**許可リストに無いと404→反映されない**(過去バグ)。
- Pythonコード変更 → ツール**再起動**必須(HTMLは都度読み直すので再起動不要)。
- ターン位置・尺は**音声生成**で確定。空セリフのインサートは音声生成時に最低2.5秒の無音(尺)を付与して表示。

## 8. 既知の制約・gotcha
- 立ち絵パーツは PSD由来。**PSDは再配布NG**(公開時は履歴ごと除去)。`assets/avatars/<char>/source/`。
- Python は**全角クォート禁止**(過去に事故・`grep -nP '[“”‘’]'` で確認)。
- 全身立ち絵キャンバス=zunda 783x1473 / metan 858x1769(`FULL_CANVAS`定数と `_box.json` 一致が前提)。
- `make_story_audio` は先頭が空セリフでも動くよう ref_params 未確定時の無音を遅延付与(修正済)。
- VOICEVOX話者ID: zundamon3/metan2/営業11/部長13/AI8。

## 9. 次の候補(未着手/検討)
- SEもタイムライン編集(現状はマッピング＋手動se)。BGMクロスフェードの作り込み。
- 書き出し(render)ボタンのエディタ統合。表情カタログのUI化。困り/焦り顔の作り込み(brow/cheek活用)。
