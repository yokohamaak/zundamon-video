# 表示種別UI 分析資料（第1段階・コード変更なし）

対象: story_editor の「基本タブ」と「インサート」の関係整理、および表示種別UIへの再構成方針。
調査日: 2026-07-14 / 基準HEAD: `f71598aa`

---

## 0. 結論サマリ

- **現存するインサートは7種すべてが「表示種別」**（画面構成の置き換え）だった。オーバーレイ型のインサートは1つも無い。
  根拠: `InsertOverlay` は不透明な全画面 `AbsoluteFill`（既定 `INSERT_BG = "#11151c"`, `insertBgOpacity` は表示中ずっと 1）でステージ（背景＋立ち絵＋モブ＋前景）を完全に覆う。
  → [StoryVideo.tsx:2919-2934](video/src/StoryVideo.tsx:2919), [StoryVideo.tsx:5092-5107](video/src/StoryVideo.tsx:5092)
- **プロンプトで例に挙がっていた「ミニ再現VTR」「ニュース画面」「比較表」は未実装**（`docs/effect-insert-proposals.md` の候補どまり）。
- **「監視カメラ風」は `visionNoise`（type: `vhs`）で、インサートではなく `ExtraEffectsLayer` のオーバーレイ**。インサートより前面に描かれるため、どの表示種別とも併用できる。→ [StoryVideo.tsx:1617](video/src/StoryVideo.tsx:1617), [StoryVideo.tsx:5113](video/src/StoryVideo.tsx:5113)
- **「漫画演出」（集中線 `impactLines` / `impactText` / `stampRain` 等）も同じく `ExtraEffectsLayer` のオーバーレイ**。表示種別ではない。
- 最大の問題は **インサートタブと基本タブの間に一切のガードが無いこと**。表情・ポーズ・カメラ・キャラ表示などをインサート中も自由に入力でき、その多くは描画時に見えない。
- もう一つの構造的問題は **ZunMeet(videocall) の暗黙継承**。`insert` を持たないターンでも直前から継承されて通話画面としてレンダリングされるが、エディタ上は「(なし)」と表示され、バッジも出ない。**「UI上は通常ターン、実際はビデオ会議画面」という最大の乖離。**

---

## 1. 現在のUI構造

右ペインは3タブ固定（[story_editor.html:1443-1447](story_editor.html:1443)）。

| タブ | id | 内容 |
|---|---|---|
| 基本 | `#tabBasic` | セリフ / キャラクター / 演出 / SE の4セクション |
| インサート | `#tabInsert` | `種別 (insert.kind)` プルダウン＋種別ごとの動的フォーム（`renderInsertFields()`） |
| 配置 | `#tabPlacement` | `manualPos` / `focusSpeaker` / `manualCameraFrame` / `zoomTarget` とミニプレビュー |

基本タブのセクション構成:

- **セリフ**: 話者 / ナレーション / セリフ / 吹き出し最大文字数 / pause / 声プリセット(speed,pitch,intonation) / 口パク / **キャラ表示(hideCharacters)** / 吹き出し(hideBubble, subtitleMode) / 字幕スタイル / 吹き出し連結 / 自動分割
- **キャラクター**: シーン / 立ち位置(speakerAnchor) / 表情 / ポーズ / 向き(face) / 向きの効き方 / 向き保持の解除 / 登場(enter) / 退場(exit)
- **演出**: シェイク / 回想 / ノイズ / カメラ効果(zoom,pan,tilt＋設定) / テロップ / 集中線・ズームパンチ・引用止め・通知洪水・キラッ・アイリスアウト / スタンプ雨 / この行だけ上書き
- **SE**: 手動SE配列

**インサート選択時に基本タブ側で無効化・非表示になる項目は一つも無い。** `renderDetail()` は `turn.insert` の有無を見ずに全項目を描画する。

---

## 2. 現在のデータフロー

```
story_editor.html（編集）
  ├ collectBasic() → turn 直下フィールド
  ├ collectInsert() → turn.insert（kindごとに毎回オブジェクトを作り直す）
  └ 配置タブ → turn.manualPos / focusSpeaker / manualCameraFrame / zoomTarget
        ↓ POST /api/story
story_editor.py::_validate_story()   … script配列と speaker/text/scene(文字列)のみ検証。
                                       insert の中身は一切検証しない
        ↓
video/public/story-01.json
        ↓ 同じJSONを Remotion Player（エディタ内プレビュー）と本番レンダリングが共有
video/src/StoryVideo.tsx
  ├ effectiveInsertAt(script, idx)  … videocall だけ直前ターンから継承・マージ
  ├ ステージ描画（背景→立ち絵→モブ→前景）を仮想カメラ transform で撮る
  ├ InsertOverlay（不透明背景でステージを完全に覆う）   ← ここで表示種別が確定
  ├ StoryOverlayLayer（画像/テキストオーバーレイ）
  ├ ExtraEffectsLayer（集中線・ノイズ・スタンプ雨 等）  ← インサートより前面
  ├ 字幕 / 吹き出し
  └ テロップ / 場面転換カバー
```

`_KNOWN_TURN_FIELDS` / `_KNOWN_INSERT_KINDS`（[story_editor.py:815-824](story_editor.py:815)）はAI生成JSONの「未知フィールド検出」用であり、保存時バリデーションではない。

### レイヤ順（重要）

`ステージ(背景+キャラ) → 回想グレイン → **インサート** → 画像オーバーレイ → 集中線 → 字幕 → 吹き出し → その他エフェクト → テロップ → 場面転換カバー`

つまり **インサートより後ろに描かれるものは、インサートが有効な間まったく見えない**（背景・立ち絵・モブ・前景・回想グレイン）。逆に **インサートより前に描かれるものは、どの表示種別でも効く**（画像オーバーレイ・全ExtraEffects・字幕・吹き出し・テロップ・場面転換カバー）。

---

## 3. 現存する表示機能の一覧と分類

### 3-1. 表示種別（画面構成を置き換える） — インサート7種すべて

| kind | 表示名 | ステージ | 立ち絵 | 吹き出し | 独自キャラ設定 |
|---|---|---|---|---|---|
| `warning` | ZunMonitor（警告） | 完全に隠す | 出ない | **出る** | なし |
| `ok` | ZunMonitor（正常） | 完全に隠す | 出ない | **出る** | なし |
| `chat` | ZunAI（AIチャット） | 完全に隠す | 出ない | 出ない | なし |
| `teamchat` | ZunChat | 完全に隠す | 出ない | 出ない | なし |
| `mailer` | ZunMail | 完全に隠す | 出ない | **出る** | なし |
| `videocall` | ZunMeet | 完全に隠す | **タイル内に再配置** | **出る** | `participants[]`（speaker/bgStyle/feedX/feedY/feedScale/cameraOff/muted） |
| `whiteboard_explain` | ホワイトボード解説 | 完全に隠す | **ボード横に1体だけ再配置** | 出ない | `character{name,expression,pose}` |

- 吹き出しの出る/出ないは `isInsertLineKind()`（[StoryVideo.tsx:485](video/src/StoryVideo.tsx:485)）が決める。`chat`/`teamchat`/`whiteboard_explain` はセリフがパネル内に表示されるため吹き出しを出さない。
- `videocall` はターンの `speaker/expression/pose/noLipSync` を **タイル内の立ち絵に流用する**（[StoryVideo.tsx:4652-4699](video/src/StoryVideo.tsx:4652)）。
- `whiteboard_explain` は **ターンの表情・ポーズを無視し** `insert.character` を使う（[StoryVideo.tsx:4291-4305](video/src/StoryVideo.tsx:4291)）。リップシンクだけは「insert.character.name == turn.speaker」の時に有効。

**「オーバーレイ型のインサート」は現状ゼロ。** よって `insert` フィールド＝表示種別とみなして問題ない。

### 3-2. オーバーレイ（通常画面を維持して重ねる）

すべて `insert` とは独立に動き、インサートより前面に描かれる＝**全表示種別と併用可**。

| 機能 | 保存先 | 実装 |
|---|---|---|
| 画像/テキストオーバーレイ | `story.overlays[]`（`layer: "over-insert"` 可） | `StoryOverlayLayer` |
| 集中線 | `impactLines` | `ExtraEffectsLayer` |
| インパクト文字 | `impactText` | 同上 |
| ズームパンチ | `zoomPunch` | 同上 |
| 引用止め | `quoteFreeze` | 同上 |
| スタンプ雨 | `stampRain` | 同上 |
| 通知洪水 | `typingFlood` | 同上 |
| キラッ | `sparkleBurst` / `sparklePos` | 同上 |
| アイリスアウト | `irisOut` | 同上 |
| 映像ノイズ（未来視/砂嵐/**VHS・防犯カメラ**/グリッチ） | `visionNoise` | 同上 |
| テロップ | `telop` / `telopX/Y/Size` | 最前面 |
| 字幕帯 | `subtitleMode` / `subtitleStyle` | 吹き出し層（※インサート種別に依存、後述） |

**判断の根拠（監視カメラ風・漫画演出）**: どちらも `ExtraEffectsLayer` 内で描かれ、この層は `InsertOverlay` の**後**にマウントされる（[StoryVideo.tsx:5113](video/src/StoryVideo.tsx:5113), [StoryVideo.tsx:5152](video/src/StoryVideo.tsx:5152)）。背景・キャラを置き換えず上に重ねるだけなので**オーバーレイ**に分類する。

### 3-3. 共通演出・音響（表示種別と独立）

`se` / `story.bgm` / `story.seMap` / `pause` / `voice` / `narrationVoice` / `noLipSync` / `shake`（インサートにも `insertShakeTransform` が適用される。[StoryVideo.tsx:4048](video/src/StoryVideo.tsx:4048)） / `transition` の `fade-black` `fade-white`（全画面カバーなのでインサート上でも効く）。

**例外**: `transition` の `wipe-*` / `slide-*` はステージのクリップ／プレート移動で表現するため、**インサートターンでは見えない**。

---

## 4. 基本タブ項目との対応表

「その他インサート」= `warning` / `ok` / `chat` / `teamchat` / `mailer`。

| 項目 | 通常 | ホワイトボード | ZunMeet(videocall) | その他インサート | 保存元 | 実際の適用元 | 問題 | 改善方針 |
|---|---|---|---|---|---|---|---|---|
| 話者 (`speaker`) | 有効 | 有効(音声・リップシンク判定) | 有効(発話タイル判定) | 有効(音声) | turn | turn | なし | **共通** |
| セリフ (`text`) | 有効 | 有効(パネル外・音声のみ) | 有効 | 有効 | turn | turn | なし | **共通** |
| 声(`voice`/`narrationVoice`/`noLipSync`) | 有効 | 有効 | 有効 | 有効 | turn | turn | なし | **共通** |
| `pause` / `start` / `end` / `sentences` | 有効 | 有効 | 有効 | 有効 | turn | 音声生成が自動書戻し | 尺は自動算出 | **共通**（尺は編集不可のまま） |
| SE (`se`) | 有効 | 有効 | 有効 | 有効 | turn | turn | なし | **共通** |
| BGM (`story.bgm`) | 有効 | 有効 | 有効 | 有効 | story | story | なし | **共通**（音タブのまま） |
| シーン (`scene`) | 背景として見える | **背景は見えない** | **背景は見えない** | **背景は見えない** | turn | turn | 見えないが、セグメント境界・`transition`・**videocall継承の打ち切り条件**として効き続ける | **共通のまま**＋「この表示種別では背景は映りません」の注記。削除・非表示は不可 |
| 立ち位置 (`speakerAnchor`) | 有効 | 無視 | 無視(タイル位置は`feedX/Y`) | **吹き出しX座標にだけ効く**(chat/teamchatは無効) | turn | turn | 立ち絵は見えないのに吹き出し位置だけ動く＝挙動が不可解 | 種別固有扱い。通常のみ表示。インサート時は非表示（値は保持） |
| 表情 (`expression`) | 有効 | **無視**(`insert.character.expression`が使われる) | 有効(タイル内) | 無視 | turn / insert.character | 種別で分岐 | **二重管理**（ホワイトボードは同じ意味の設定が2箇所） | 通常/ZunMeet=共通位置に表示、ホワイトボード=種別固有(`insert.character`)へ一本化、その他=非表示 |
| ポーズ (`pose`) | 有効 | **無視**(`insert.character.pose`) | 有効(タイル内) | 無視 | turn / insert.character | 種別で分岐 | 同上 | 同上 |
| 向き (`face`/`faceMode`/`clearFace`) | 有効 | 無視 | 無視 | 無視 | turn | turn | 入力できるが効かない | 通常のみ表示 |
| 登場/退場 (`enter`/`exit`/`*Dir`) | 有効 | 見えない | 見えない | 見えない | turn | turn | **見えないが状態は変わる**（後続の通常ターンの在席に影響）＝無視ではない | 表示は残す＋「この行では見えません（以降の通常画面に反映されます）」注記 |
| キャラ表示OFF (`hideCharacters`) | 有効 | **完全に無意味** | **完全に無意味** | **完全に無意味** | turn | turn | 完全な無視項目 | インサート時は非表示 |
| 吹き出し非表示 (`hideBubble`) | 有効 | 冗長(元々出ない) | 有効 | warning/ok/mailer=有効、chat/teamchat=冗長 | turn | turn | 冗長 | 吹き出しが出る種別でのみ表示 |
| 字幕モード (`subtitleMode`/`subtitleStyle`) | 有効 | **無視** | 有効 | warning/ok/mailer=有効、chat/teamchat=**無視** | turn | turn | 入力できるが効かない種別あり | 吹き出しが出る種別でのみ表示 |
| 吹き出し連結/最大文字数/自動分割 | 有効 | 無視 | 有効 | 同上 | turn | turn | 同上 | 同上 |
| `focusSpeaker` / `manualCameraFrame` / `cameraTransition` | 有効 | 見えない | 見えない | 見えない(※吹き出しXが微妙に動く) | turn | turn | 実質無視 | 通常のみ表示（配置タブ） |
| カメラ効果 zoom/pan/tilt | 有効 | 見えない | 見えない | 見えない(※吹き出し位置に副作用) | turn | turn | 実質無視 | 通常のみ表示 |
| カメラ効果 shake | 有効 | **有効**(パネルが揺れる) | 有効 | 有効 | turn | turn | なし | **共通** |
| 回想 (`flashback`) | 有効 | ほぼ無視(白ディゾルブのみ) | ほぼ無視 | ほぼ無視 | turn | turn | ステージ彩度フィルタもグレインもインサート裏 | 通常のみ表示（要判断・下記Q4） |
| 映像ノイズ (`visionNoise`) | 有効 | 有効 | 有効 | 有効 | turn | turn | なし | **共通（オーバーレイ）** |
| テロップ (`telop`ほか) | 有効 | 有効 | 有効 | 有効 | turn | turn | なし | **共通（オーバーレイ）** |
| 集中線/ズームパンチ/引用止め/通知洪水/キラッ/アイリスアウト/スタンプ雨 | 有効 | 有効 | 有効 | 有効 | turn | turn | なし | **共通（オーバーレイ）** |
| 場面転換 (`transition`) | 有効 | fade系のみ有効 | 同左 | 同左 | turn | turn | wipe/slideが見えない | **共通**＋wipe/slide選択時に注記 |
| `manualPos`（配置タブ） | 有効 | 見えない | 見えない | 見えない(吹き出しXに副作用) | turn | turn | 実質無視 | 通常のみ |
| インサート `width`/`fontScale`/`bg`/`backdropBg`/`backdropImage` | – | 非表示(正しい) | width のみ | 有効 | insert | insert | 現状も種別で出し分け済み＝OK | **種別固有**のまま |
| インサート固有本文（title/text/user/ai/messages/subject/body/sections…） | – | 有効 | 有効 | 有効 | insert | insert | なし | **種別固有** |

---

## 5. 現在発生している問題（確認済みのもの）

1. **ZunMeet継承の不可視化【最重要】**
   `effectiveInsertAt()`（[StoryVideo.tsx:586-610](video/src/StoryVideo.tsx:586)）は、同一 `scene` 内で直前に `videocall` があり `end:true` が無ければ、**`insert` を持たないターンも通話画面としてレンダリングする**。
   一方エディタは `turn.insert` しか見ないため、インサート種別は「(なし)」、ターンリストのバッジも無し（[story_editor.html:6738](story_editor.html:6738)）。
   → ユーザーには通常ターンに見えるが、実際はステージが完全に隠れている。背景・表情・カメラをいくら設定しても映らない。

2. **`hideCharacters` が全インサートで完全な無視項目**（不透明な背景に隠れているため）。

3. **ホワイトボードの表情・ポーズが二重管理**。基本タブの表情/ポーズと `insert.character.expression/pose` が併存し、**後者が勝つ**。基本タブ側を変えても何も起きない。

4. **`subtitleMode` / 吹き出し系が種別によって黙って無効化される**（chat/teamchat/whiteboard）。

5. **カメラ・立ち位置・manualPos の「半分だけ効く」副作用**。立ち絵は見えないのに、吹き出しの表示されるインサート（warning/ok/mailer/videocall）では **吹き出しのX座標がカメラ変換と話者位置から計算される**（[StoryVideo.tsx:4440-4456](video/src/StoryVideo.tsx:4440)）。「無視」でも「有効」でもない中間状態。

6. **種別変更でインサートデータが無告知で消える**。`collectInsert()` は毎回 `let ins = { kind }` から作り直す（[story_editor.html:8840](story_editor.html:8840)）。ホワイトボードの sections を作り込んだ後で kind を変えると全消滅、Undo なし。

7. **保存時バリデーションが実質ゼロ**。`_validate_story()` は speaker/text/scene の型しか見ない。`insert.kind` が未知でも保存できる。

---

## 6. 採用する新UI構成（案）

タブ構成は「基本 / 配置」の2つ（＋種別に応じて動的に増える種別固有セクション）へ寄せる。**インサートタブは廃止せず、中身を基本タブ下部の「表示種別固有設定」へ移設**する形を提案する（プロンプトの方針に合わせるとインサートタブは不要になるが、`配置` タブは通常表示専用として残す）。

### 基本タブ

```
┌ 表示種別 ────────────────────────────┐
│ [ 通常表示 ▾ ]                          │
│  通常表示 / ZunMonitor / ZunAI / ZunChat  │
│  ZunMail / ZunMeet / ホワイトボード解説   │
│  ※ZunMonitorは固有欄で「警告/正常」を切替 │
│  ※ZunMeet継承中は「ZunMeet（前のターンから継続）」を表示 │
└──────────────────────────────────┘
┌ 共通設定（種別によらず同じ意味） ──────┐
│ 話者 / セリフ / 声 / 口パク / pause     │
│ シーン（※インサート時は「背景は映りません」注記）│
│ SE                                      │
│ 場面転換                                │
│ シェイク / 映像ノイズ / テロップ / 単発演出群 │
└──────────────────────────────────┘
┌ 「通常表示」固有 ─────────────────────┐
│ 立ち位置 / 表情 / ポーズ / 向き / 登場 / 退場 │
│ 吹き出し設定 / 字幕モード                │
│ 回想 / カメラ効果(zoom,pan,tilt)          │
└──────────────────────────────────┘
┌ 「ホワイトボード解説」固有 ────────────┐
│ title / theme / sections / conclusion …   │
│ キャラ（name / 表情 / ポーズ）← ここに一本化 │
└──────────────────────────────────┘
（他の種別も同様に、現行 renderInsertFields のフォームをそのまま移設）
```

- 登場/退場は**インサート時も表示**し、「この行では見えません。以降の通常画面に反映されます」と注記（挙動を変えずに誤解だけ潰す）。
- 吹き出しが出る種別（warning/ok/mailer/videocall）では、吹き出し設定・字幕モードを共通側に出す。出ない種別（chat/teamchat/whiteboard）では非表示。

### 実装構造

`story_editor.html` に `DISPLAY_TYPES` 定義テーブルを1つ置き、各エントリに
`{ id, label, insertKind, showsBubble, showsStageCharacters, usesTurnExpression, fields }`
を持たせる。`renderDetail()` はこのテーブルを引いて共通セクションの各行の表示/非表示を決め、種別固有セクションは既存 `renderInsertFields()` を流用する。**新しい抽象化レイヤやコンポーネント分割は導入しない**（素のJS・既存関数の再利用にとどめる）。

---

## 7. データモデルの変更方針

**JSONスキーマは変更しない。** `turn.insert` をそのまま「表示種別」の保存先として使う。

- 表示種別 `通常` = `insert` 無し（かつ videocall 継承下でない）。
- 表示種別 `ZunMeet` = `insert.kind === "videocall"`、または継承中。
- UI上の「表示種別」は `effectiveDisplayTypeAt(idx)`（＝ `effectiveInsertAt` のJS版・既存 `resolveEffectiveVideoCallInsert` を流用）から算出する読み取り値。
- 新フィールドの追加はしない（＝`_KNOWN_TURN_FIELDS` の変更なし、旧JSONも新JSONも同じ形）。

これにより **既存JSONは無変換で読める／レンダリング結果も1ピクセルも変わらない**。マイグレーション処理は不要。

---

## 8. 既存JSON互換方針

- 旧JSONの優先ルール（インサート側が勝つ）は**そのまま踏襲**。レンダリング分岐（`StoryVideo.tsx`）は原則変更しない。
- 二重管理・無視項目の解消は **エディタUI（入力側）だけ**で行う。既存JSONに残っている「効かない値」は削除せず放置する（消すとレンダリング差分リスクがあるため）。
- 未知の `insert.kind` は「不明な表示種別（未対応）」としてプルダウンに読み取り専用で表示し、勝手に落とさない。

---

## 9. 実装対象ファイル

| ファイル | 変更内容 |
|---|---|
| `story_editor.html` | 表示種別テーブル追加、基本タブ最上部にプルダウン、種別による項目の出し分け、インサートフォームの移設、種別変更時の確認ダイアログ、ターンリストのバッジを「継承中ZunMeet」にも付ける |
| `story_editor.py` | `_validate_story()` に `insert.kind` の軽い検証を追加（任意）。プロンプト文面は変更しない |
| `test_story_editor.py` | 既存JSON読み込み・kind検証のテスト追加 |
| `video/src/StoryVideo.tsx` | **原則変更しない**（Q4の判断次第） |

## 10. 実装しない範囲

- 新しいインサート種別の追加（ミニ再現VTR・ニュース画面・比較表など）
- `StoryVideo.tsx` のレンダリング分岐の再設計
- JSONスキーマ変更・マイグレーション
- 配置タブの再構成（通常表示専用である旨の注記のみ）

---

## 11. リスク

| リスク | 対策 |
|---|---|
| 項目を非表示にすると、既存JSONに入っている値が編集不能になる | 「その種別で無効な値が既に入っている」場合だけ、理由付きの読み取り専用行として表示する |
| `story_editor.html` は7000行超の単一ファイル。`renderDetail()` の改変で既存フィールドの読み書きが壊れる | 段階コミット＋各段階で `node --check`、および `story-01.json` をスクラッチパッドへ退避してから手動確認 |
| プレビュー(Remotion Player)と本番の二重実装 | 今回はレンダリング側を触らないため、二重実装の増加はない |

---

## 12. 判断が必要だった点と決定（2026-07-14 ユーザー確定）

| # | 論点 | 決定 |
|---|---|---|
| Q1 | ZunMeet継承ターンの扱い | **継承を読み取り専用で表示**。プルダウンに「ZunMeet（前のターンから継続）」を出し、「通常表示に戻す」を選んだら `insert:{kind:"videocall", end:true}` を書く。データ構造・レンダリング不変 |
| Q2 | `warning` / `ok` の統合 | **プルダウンは「ZunMonitor」1項目に統合**し、種別固有欄に「警告 / 正常」の切替を置く。保存される `insert.kind` は従来どおり `warning` / `ok` の2値を維持（互換） |
| Q3 | 表示種別変更時のデータ | **保持しない。破棄前に確認ダイアログを出す**。データ構造不変。なお通常⇔インサートの往復では背景・立ち位置・カメラは `turn` 直下に残るため元々失われない（`insert` を消すだけ） |
| Q4 | インサート中に無視される項目（回想・カメラ効果・立ち位置・向き・manualPos・`hideCharacters`） | **その表示種別では非表示にする**。レンダリング側は変更しないため、既存JSONの見た目は不変 |
| Q5 | 吹き出しXへのカメラ副作用 | Q4に含めて **非表示**（実害が微差のため。レンダリング挙動はそのまま） |

### 決定を踏まえた表示種別プルダウン（7項目）

1. 通常表示
2. ZunMonitor（警告 / 正常を固有欄で切替 → `warning` / `ok`）
3. ZunAI（`chat`）
4. ZunChat（`teamchat`）
5. ZunMail（`mailer`）
6. ZunMeet（`videocall`。継承中は「前のターンから継続」表記）
7. ホワイトボード解説（`whiteboard_explain`）

＋ 未知の `kind` は「不明な表示種別（未対応）」として読み取り専用表示。
