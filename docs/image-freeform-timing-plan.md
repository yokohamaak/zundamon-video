# 背景画像の文(sentence)アンカー化（計画）

> ステータス: **未着手**（方針確定・2026-06-23）。実装指示が出るまで着手しない。
>
> ⚠️ 旧方針「文字位置で自由化（`_point_time`線形按分）」は**却下**。議論の結果 **文(sentence)アンカー**へ転換（理由は下記）。

## 目的

背景画像（メイン画像）を「セリフ単位の紐づけ」から、**文(sentence)単位で切り替えられる**ようにする。

- 1つのセリフ内で、文の区切りで画像を切り替えられる（例：「1枚目はこれです。｜2枚目はこちら。」で2枚）
- 隣のセリフの途中の文まで伸ばせる（セリフ跨ぎ可）
- **章跨ぎは不要**

## なぜ文字位置自由化をやめて文アンカーにしたか（議論の結論 2026-06-23）

決め手は **`sentences` 構造の発見**（`src/tts_voicevox.py` 407行）：

> sentences＝字幕単位。**文ごとに個別合成して実尺から算出**、長文は文字数比で細分

つまり既存構造で:
- 1つのセリフ(turn)は内部で**文(sentences)**に分割され、各文が **実際に合成した音声の実尺** で `start`/`end` を持つ（＝**実発話タイミング**。線形按分ではない）
- 字幕は `DialogueVideo.tsx` の `pickActive(activeTurn.sentences, t)` で**文単位で自動的に切り替わっている**（既に実装済み）

### 文字位置自由化（旧案）との比較

| 案 | 音声 | 切替の正確さ | 字幕 | 実装 |
|---|---|---|---|---|
| セリフ分割（現運用） | △ 文がturn境界に割れ間が入る | ◎ | ✕ 泣き別れ（字幕が2回切替） | 不要 |
| 文字位置自由化（旧案・却下） | ◎ 一息 | △ `_point_time`線形按分でズレる | ◎ | 大（4〜6セッション） |
| **文アンカー（採用）** | ◎ 一息 | **◎ `sentences[].start`の実タイミング＝ズレない** | ◎ | **中** |

- **ズレ防止**が最大の利点。文の境界には実発話タイミングがあるので、`_point_time`の線形按分を使わず正確に切り替わる。
- **字幕の泣き別れは起きない**。セリフを分割せず1ターンのままにすれば、字幕は文単位で勝手に切り替わる（音声も一息）。
- 文字位置の自由ドラッグUIも `_point_time` 按分も**不要**＝旧案より実装が軽い。

### 唯一のトレードオフ（ユーザー了承済み）

切り替えられる位置は **文の区切り** に限る（任意の文字位置は不可）。区切りは `tts_voicevox._split_sentences` が決める＝**句点(。)・！？・閉じ括弧**等。
- ✅ 「1枚目はこれです。｜2枚目はこちら。」… 句点で区切れる
- ❌ 「1枚目はこれと、2枚目はこちらです。」… 1文の途中では切れない

ユーザーの実需は文境界での切替なので問題なし、と合意。

## 確定した設計判断（ユーザー合意済み）

| 論点 | 決定 |
|------|------|
| アンカー粒度 | **文(sentence)単位**。`sentences[].start/end`（実タイミング）に乗る |
| 切替位置 | 文の区切りのみ（句点等。任意文字位置は不可） |
| 隙間（画像の無い時間帯） | **何も表示しない＝薄いグラデ背景のみ**（前画像を継続しない） |
| 同一セリフ内に複数画像 | **許す**（区間の重なり禁止で制約） |
| セリフ跨ぎ | **可**（開始セリフの文 → 終了セリフの文）。`endTurnGi`相当を流用 |
| 章跨ぎ | **不可** |
| 追従性（権威） | 文アンカー（turnId＋文index）。秒は焼き込まず毎回派生 |

## 現状アーキテクチャ（変更前）

背景画像は `topic` に内包され、`topic` が4つの役割を同時に担う密結合。

1. 背景画像（`image`/`fit`/`crop`/`filter`/`pad`/`bg`/`credit`）
2. 大演出（`panel`/`quiz`/`compare`/`stat`/`callouts`）＝画像エリアを上書き
3. 章メタ（`section`/`chapter`/`triviaIndex`/`hook`/章バッジ）
4. 演出窓（`vizFrom`/`vizUntil`）

データの流れ:

```
imageCue(turnId/endTurnId＝セリフ参照)
  → resolve_turn_images()  … 各セリフに1枚を割当（turn_image配列・1セリフ1画像前提）
  → build_chapter_topics() … 同一cueの連続セリフをまとめ、切替時刻は必ずセリフ境界(turn.start)
  → meta.topics[]（各start秒・連続・常に1枚がactive）
  → DialogueVideo.tsx  pickActive(topics, t) で時刻選択
```

字幕側（流用する既存資産）:
```
turn.sentences[] = [{text, start, end}, …]  ← tts_voicevoxが文ごと個別合成・実尺で算出
  → DialogueVideo.tsx  pickActive(activeTurn.sentences, t)  ← 字幕は既に文単位で切替
```

**根本制約**: `resolve_turn_images` が返すのは「セリフ数ぶんの配列＝1セリフ1画像」。文単位の切替（1セリフ内で複数画像）を表現できない。ここを**区間リスト(imageSpans)**へ作り替えるのが本丸（文字位置案と共通の難所）。

## 実装方針：画像を別レイヤー(imageSpans)に分離し、文アンカーで解決

`topic` から背景画像だけを切り出し、文アンカーの独立レイヤーにする。`topic` は大演出・章メタ・演出窓を引き続き担当。

### 1. データモデル（`imageCue` 拡張）＝文アンカー

- 開始：`turnId` ＋ `startSentence`（開始セリフ内の文index。省略時=先頭=0）
- 終了：`endTurnId` ＋ `endSentence`（終了セリフ内の文index。省略時=セリフ全体）
- 位置未指定の既存cueは「セリフ全体」＝**従来互換**（マイグレーション不要）。Geminiは文indexを出さない＝この未指定状態を作る
- 権威はこのアンカー。秒は持たせず、レンダリング時に `sentences[index].start/end` から毎回派生

### 2. Python（`main_story.py` / `editor_model`）

- cue群から `meta.imageSpans = [{start, end, image, fit, crop, filter, pad, bg, credit, ...}]` を生成
- start/end は **`turn.sentences[startSentence].start` / `endTurn.sentences[endSentence].end`**（実タイミング。`_point_time`は使わない）
- **隙間は span を生成しない**＝その時間帯は画像なし（blank）
- `editor_model`：開始位置の一意制約（`_assert_no_start_collision`）を撤廃し、**区間の重なり禁止**へ置換（`overlayOverlaps`流用）。文index・跨ぎの正規化（クランプ・逆転除外）を追加
- 既存テスト(`test_editor_phase2`)で「文index未指定時は従来と完全等価」を担保

### 3. 描画（`DialogueVideo.tsx`）

- 背景画像を `activeTopic.image` ではなく `imageSpans` から時刻選択（`pickActive`相当、**gapでは null → 薄いグラデ背景**）
- `activeTopic.image` 直接参照（約29箇所：focus/depth/containBg/章めくりflipImage/Ken Burnsカットindex）を `imageSpans` ベースへ付け替え
- 大演出（panel/quiz/compare）は従来通り `topic` 由来で**画像の上レイヤー**に乗る。画像gap×大演出の重なりは大演出のみ表示

### 4. タイムラインUI（`review_story_page.html`）

- 画像トラックのバーを、**文の境界に割り当てる**操作へ（現 `startCueResize` のセリフ境界スナップを文境界スナップへ）
- セリフ跨ぎは `startOverlayCrossEndResize` のロジックを流用
- バー端のドラッグは「文の区切り」にスナップ（文字位置の連続ドラッグより単純）
- `cueSpans()` / `computeImagePlan()` を文index対応へ

## 主なリスク・確認点

- **章めくり（page flip）**: 影響は **(A) めくる絵の取得元の付け替えのみ**。`flipImage` を `prevTopic.image` → 「前章末の時刻にactiveな`imageSpan`」へ。
  - 章端gap（章末/章頭に画像なし）はコードで**禁止しない＝許容**（運用で画像を入れる）。`flipImage`無→既存条件 `isChapterFlip = … && !!flipImage` で**自動フェードへフォールバック**。追加実装ほぼ不要。
  - 章切替トランジションのパターン拡張は**別課題**。今回は踏み込まない。
- **画像境界の丸め規則**: 画像Aの終わりとBの始まりが隣接した境界フレームで `pickActive`(`start <= t`)の選択・秒丸めを統一。**背景全面なので境界の半端が目立つ**。文アンカーは実タイミングに乗るぶん、線形按分よりズレ要因が少ない。
- **文index追従**: 本文編集で文の数・区切りが変わると文indexがズレる。`reconcile` でクランプ（文字位置案と同種の追従課題だが、文単位のぶんロバスト）。
- **大演出との重なり**: 大演出は画像より上レイヤー＝重なり区間は大演出のみ表示で確定。
- **Ken Burns**: カットindexは `imageSpans` 順で振り直す。gapで連番がズレて動きが変わっても可（破綻しなければ良い）。
- **左サムネ/プレビュー連携**: サムネは雰囲気確認用。1セリフ複数画像時はどれを出すか適当でよい（先頭span等）。`refreshLineThumbs` は最小対応。

## 工数の見直し（文アンカー＝文字位置案よりやや軽い）

私の作業＋MacでのGUI確認往復の体感。

| 層 | 規模 | 備考 |
|---|---|---|
| ① データ＋Python解決 | 中 | `sentences[].start`に乗るだけ＝按分ロジック不要。純関数＋テストで固める |
| ② topic統合(`build_chapter_topics`) | 中 | 1セリフ1画像前提→区間化（本丸・文字位置案と共通） |
| ③ 描画(`DialogueVideo.tsx`) | 中〜大 | 約29箇所の取得元付け替え＋gap/章めくり。画像レイヤー分離が本体 |
| ④ UI(`review_story_page.html`) | 小〜中 | 文境界スナップ＝文字位置の連続ドラッグより単純。跨ぎはオーバーレイ流用 |

**合計おおむね3〜5セッション**＋Mac実機確認。段階：①②（meta検証）→③（描画・Studio目視）→④（UI・Mac確認）。

## 段階実装（推奨順）

CLAUDE.md方針（まず動く・ローカルで反復検証・重い依存はモック）に沿う。

1. **①②（描画なし）**: `imageCue`に文index追加＋`imageSpans`生成を、ローカル単体テストで固める（`sentences[].start`参照・gap生成・重なり禁止・文index未指定の従来等価）。レンダリングは触らず meta 比較で検証
2. **③ 描画**: `DialogueVideo.tsx` を `imageSpans` ベースへ。`npm run dev`(Studio/HMR) で見た目確認
3. **④ UI**: タイムラインの文境界スナップ＋跨ぎ。Mac実機で通し確認

## 関連

- [[image-overlay-plan]]（オーバーレイ＝セリフ跨ぎUI/レイヤー分離の参照元）
- [[editor-model-phase2-progress]]（assets/imageCues/visualSegments の編集モデル）
- `src/tts_voicevox.py`（`sentences`＝文単位・実タイミングの生成元）
- `docs/architecture.md` / `docs/review-tool-spec.md`（topic/cue の既存仕様）
