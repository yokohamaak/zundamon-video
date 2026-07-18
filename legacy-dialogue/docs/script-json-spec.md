# 台本JSON仕様（script.json / 取り込み用）

`review_server.py` の「台本を取り込む」（`import_main_script`）が受け付ける台本JSONの仕様。
本来は Gemini（`src/story_script.py` の `build_prompt`）が生成する形式だが、**同じ形式であれば
他の生成手段（別ツール・手書き・別LLM）で作ったJSONも取り込み可能**。検証・正規化は
`src/story_script.parse_script_json()` が行う（このドキュメントの正でもある実装）。

- 現行の story ツール（リポジトリ直下 `story-01.json`）とは**別スキーマ**。フィールド名・意味が異なるので流用不可（`speaker/text/scene/enter/exit`等は無い）。
- 取り込み先: `docs/story/script.json`（`POST` 経由で保存。手で置いてもよい）
- 取り込み後の流れ: 台本 → 画像取得（`--stop-after-images`）→ 音声+meta生成（`--images-from-dir`）→ Remotion書き出し

```bash
# 取り込み後、画像取得〜書き出しまで
cd legacy-dialogue
./run images     # 画像取得（review.json 生成・レビューUIで承認）
./run audio       # VOICEVOX音声 + meta.json 生成
./run render      # video/out/*.mp4 書き出し
```

---

## 0. 全体構造

```jsonc
{
  "theme": "動画の主題（文字列・任意だがmeta.titleに使われる）",
  "chapters": [ /* 章メタの配列。0始まり・出現順が章番号 */ ],
  "script":   [ /* 発言(ターン)の配列。出現順がそのまま再生順 */ ]
}
```

- 必須トップレベルキーは **`script`のみ**（非空配列・各要素に `speaker`/`text` が必須）。無いとパース時に例外で弾かれる。
- `chapters` は省略可能だが、**省略すると `script[].section` はenum検証のみでchapter由来の上書きをされない**。演出（quiz/panel等）は章に紐づくため、通常は `chapters` を用意する。
- `theme` 以外の未知のトップレベルキーは無視されず**そのまま保持**される（正規化対象外）が、後続処理（`build_meta`等）が読むのは `theme`/`chapters`/`script` のみ。

---

## 1. `chapters[]`（章メタ）

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `section` | `"intro"\|"trivia"\|"outro"` | 任意（既定`trivia`） | 章の種別。不正値は`trivia`に矯正 |
| `title` | string | 任意 | 章見出し（画面表示）。Markdown記法は自動除去 |
| `summary` | string | 任意 | 章要旨（編集用・動画には出ない） |
| `confidence` | `"high"\|"medium"\|"low"` | 任意 | 事実確度（編集メタ・動画には出ない）。不正値は保持されず削除 |
| `source_hint` | string | 任意 | 裏取りの手がかり（編集メタ） |
| `hook` | string | 任意 | ショート生成用の固定見出しフック |
| `image_cuts` | array | 任意（無ければ1個生成） | 下記2参照 |
| `panel` / `quiz` / `compare` / `stat` / `callouts` / `calloutStyle` / `vizList` | object/array | 任意 | 画像エリア演出。下記3参照。**1章につき`panel`系は基本1種類** |

- 配列は**最大12章**（超過分は切り捨て）。
- 旧形式互換: `image_cuts` が無い場合、章直下の `image_query`/`image_kind`（単数）を1個のcutへ変換。

### 2. `image_cuts[]`（章内の画像カット）

```jsonc
{ "image_query": "Yokohama port", "image_kind": "subject", "image_query_ja": "横浜港（任意・人間確認用ラベル）" }
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `image_query` | string | 任意（空文字可） | 画像検索語（英語推奨）。**空でも許容**＝レビューUIで手動割当する枠として使える |
| `image_kind` | `"subject"\|"ambient"` | 任意（既定`ambient`） | `subject`=実在の人物/製品/ロゴ等（Wikimedia向き）、`ambient`=抽象・雰囲気（Pexels/Pixabay向き） |
| `image_query_ja` | string | 任意 | `image_query`の日本語訳（レビュー時の確認ラベル。空なら保持されない） |

- 1章あたり**最大8個**（超過は切り捨て）。dict以外の要素は除外。
- `script[].cut` はこの配列のインデックス（0始まり）を指す。**カット番号は`review.json`と対応するため、query空でも要素を消してはいけない**（消すと画像がズレる）。

### 3. 章の画像エリア演出（任意・1章につき基本1種類）

いずれも省略可。`vizList` は複数演出を1章に並べる新形式（`viz_start`/`viz_end`で範囲対応）。

**`quiz`**（問い→答えの溜め）
```jsonc
{ "question": "問い（必須）", "answer": "答え（空可＝問いを出しっぱなし）",
  "image": "任意", "bg": "CSS色任意", "bgOpacity": 0.0-1.0,
  "textColor": "CSS色任意", "answerBg": "CSS色任意", "answerBgOpacity": 0.0-1.0,
  "answerTextColor": "CSS色任意", "boxWidth": 0.2-1.0,
  "questionSize": 0.3-3.0, "answerSize": 0.3-3.0 }
```
`question`が空なら演出全体が破棄される。

**`compare`**（2分割対比。`image_cuts`が2個以上必要）
```jsonc
{ "left": {"label": "必須", "cut": 0}, "right": {"label": "必須", "cut": 1},
  "labelColor": "任意", "labelTextColor": "任意", "labelSize": 0.3-3.0, "dividerColor": "任意" }
```
`left`/`right`どちらか`label`欠如で演出全体が破棄。`cut`省略時は既定0/1。

**`stat`**（大きな数字強調）
```jsonc
{ "value": "70（必須・整数のみならカウントアップ表示）", "unit": "％", "label": "世界シェア",
  "color": "任意", "size": 0.3-3.0, "bg": "任意", "bgOpacity": 0.0-1.0,
  "countSpeed": "fast" }  // "fast" | "normal" | "slow"
```
`value`が空なら破棄。

**`panel`**（縮小画像＋段階テキスト、最大6項目）
```jsonc
{ "heading": "任意", "items": [ {"text": "必須・体言止め推奨", "arrow_from_prev": true} ],
  "bg": "任意", "bgOpacity": 0.0-1.0, "overlay": true,
  "markerType": "check", "markerColor": "任意", "markerSize": 0.3-3.0,  // markerType: "check" | "square" | "dot"
  "textColor": "任意", "textSize": 0.3-3.0, "pos": "right" }  // pos: "right" | "left" | "top" | "bottom" | "center"
```
`items`が空/textが全滅なら破棄。画像はcut追従のため`panel`自体は画像を持たない。

**`callouts[]`**（画像上の位置注釈。最大4個）＋任意`calloutStyle`
```jsonc
[{ "text": "必須", "x": 0.0-1.0, "y": 0.0-1.0, "arrow": true, "lx": 0.0-1.0, "ly": 0.0-1.0 }]
```
`calloutStyle`: `{ "markerColor","markerSize":0.3-3.0,"labelColor","labelTextColor","labelBorderColor","labelSize":0.3-3.0,"arrowSize":0.3-3.0,"arrowShape":"normal|sharp|thick|dot" }`（すべて任意）。
`text`欠如または`x`/`y`が範囲外の要素は個別に除外（他の要素は残る）。

**`vizList[]`**（複数演出・新形式）
```jsonc
[{ "id": "任意（無ければ自動採番 s0,s1,...）", "panel": {...} },   // panel/quiz/compare/stat/callouts のいずれか1キー
 { "callouts": [...], "calloutStyle": {...} }]
```
各要素はpanel/quiz/compare/stat/calloutsのうち最初に有効なもの1つだけが採用される。

---

## 4. `script[]`（発言＝ターン）

```jsonc
{
  "speaker": "四国めたん", "text": "セリフ本文",
  "emotion": "normal", "section": "trivia", "chapter": 1,
  "effect": "kenburns", "cut": 0
}
```

### 必須
| フィールド | 型 | 説明 |
|---|---|---|
| `speaker` | string | 話者名。**`config/config.story.yaml` の `tts_voicevox.speakers` キーと一致必須**（既定`ずんだもん`/`四国めたん`）。不一致は取り込み自体は通るが**音声生成時にエラー**になる |
| `text` | string | セリフ本文。取り込み時にMarkdown記法除去・冗長カタカナ読みグロス除去が自動適用される |

### 任意（enum・整数フィールド）
| フィールド | 型 | 既定/挙動 |
|---|---|---|
| `emotion` | `normal\|surprise\|happy\|sad\|angry` | 不正値は`normal`に矯正 |
| `effect` | `kenburns\|zoom_punch\|shake\|flash\|glow_pulse` | 不正値は`kenburns`に矯正 |
| `chapter` | int | `chapters`があれば`[0, len-1]`にクランプ。無ければ0以上にクランプ（既定0） |
| `section` | `intro\|trivia\|outro` | **`chapters`があれば無視され、`chapters[chapter].section`で上書きされる**（章の構造が真）。`chapters`が無い場合のみenum検証のみ |
| `cut` | int | その章`image_cuts`の範囲にクランプ。章のcut数が不明なら削除される |

### 任意（声・間）
| フィールド | 型 | 範囲 |
|---|---|---|
| `voice` | `{speed?,pitch?,intonation?,volume?}` | speed/intonation/volume: 0.5-2.0、pitch: -0.15-0.15。範囲外はクランプ、無効キーは削除、空になれば`voice`ごと削除 |
| `pause` | number | 0-2秒。0または不正値は削除 |

### 任意（画像演出の合図。対応する章の演出と組み合わせて使う）
| フィールド | 型 | 用途 |
|---|---|---|
| `reveal` | bool(true) | quiz/statの答え・数字を出す瞬間。false相当は削除 |
| `panel_event` | `"shrink"`のみ | 画像を縮小しパネルを開く瞬間。他の値は削除 |
| `panel_item` | int または int配列 | panel.itemsの何番目を出すか（配列で複数同時表示可）。bool/不正は削除 |
| `callout_item` | int | callouts[n]を出す。bool/不正は削除 |
| `compare_item` | int | compareの左(0)/右(1)を出す。bool/不正は削除 |
| `viz_start` / `viz_end` | bool(true) | vizList演出の開始/終了（`vizSeg`と組み合わせ） |
| `vizSeg` | string | 発言が属するvizList要素の`id` |

### 任意（重ねがけ小演出：テロップ・リアクション）
| フィールド | 型 | 説明 |
|---|---|---|
| `telop` | string | ポップ表示するキーワード。Markdown除去適用。空なら削除 |
| `reaction` | string | 絵文字等の一瞬表示。最大12文字に切り詰め |
| `telopSize`/`reactionSize` | number 0.3-3.0 | 大きさ倍率 |
| `telopX`/`telopY`/`reactionX`/`reactionY` | number 0.0-1.0 | 位置（クランプ） |
| `telopDur`/`reactionDur` | `short\|normal\|long` | 表示時間。それ以外は削除 |
| `telopColor`/`telopBg`/`telopBorder` | string(CSS色) | 空なら削除 |

### 任意（字幕内の範囲演出・文字位置演出）
```jsonc
"textEffects": [ { "id": "任意", "type": "emphasis"|"color", "start": 0, "end": 10, "color": "任意(typeがcolorの時)" } ]
"vizPoints":   [ { "id": "任意", "type": "panel_item"|"callout_item"|"compare_item"|"reveal"|"panel_event", "pos": 0, "value": 0 } ]
```
- `textEffects`: `start<0` または `end<=start` または `end>10000` は除外。`type`が`emphasis|color`以外も除外。
- `vizPoints`: `pos`は0-10000。`type`が`panel_item`/`callout_item`/`compare_item`の場合は`value`(0-999)必須。

---

## 5. 取り込み時に弾かれる/警告が出るケース

- `script`が無い・空配列・非配列 → **取り込み失敗**（例外）
- `script[i]`に`speaker`または`text`が無い → **取り込み失敗**（例外）
- JSONとして壊れている（末尾カンマ等の軽微な崩れは自動修復を試みるが、直らなければ失敗）
- `speaker`が`config.story.yaml`の`tts_voicevox.speakers`に無い → 取り込みは成功するが**音声生成（`./run audio`）でエラー**
- 解説役(`explainer`)のセリフに聞き手(`questioner`)特有の語尾（「〜のだ」等）が混入 → 取り込み自体は通るが`warn_role_voice()`が警告ログを出すのみ（自動修正なし）

---

## 6. 最小構成の例

```json
{
  "theme": "なぜ横浜駅は迷宮なのか",
  "chapters": [
    { "section": "intro", "title": "迷宮の謎", "image_cuts": [
      { "image_query": "Yokohama station", "image_kind": "subject" }
    ]},
    { "section": "trivia", "title": "戦後の闇市が起源", "confidence": "medium", "image_cuts": [
      { "image_query": "postwar black market Japan", "image_kind": "ambient" }
    ]},
    { "section": "outro", "title": "まとめ", "image_cuts": [
      { "image_query": "Yokohama station", "image_kind": "subject" }
    ]}
  ],
  "script": [
    { "speaker": "四国めたん", "text": "横浜駅は日本一の『迷宮駅』って呼ばれてるの、知ってる？",
      "emotion": "normal", "chapter": 0, "effect": "kenburns", "cut": 0 },
    { "speaker": "ずんだもん", "text": "ええっ、なんでそんなことになったのだ！",
      "emotion": "surprise", "chapter": 0, "effect": "kenburns", "cut": 0 },
    { "speaker": "四国めたん", "text": "実は戦後の闇市が元になって、増改築を繰り返した結果なのよ。",
      "emotion": "normal", "chapter": 1, "effect": "flash", "cut": 0 },
    { "speaker": "四国めたん", "text": "そんな成り立ちから今の姿になった、というお話でした。",
      "emotion": "normal", "chapter": 2, "effect": "kenburns", "cut": 0 }
  ]
}
```

`chapter`のインデックスは`chapters`配列の位置（0始まり）と一致させること。`section`は入れても`chapters[chapter].section`で上書きされるため省略可。

---

## 関連ファイル

- 実装（検証・正規化の正）: [`../src/story_script.py`](../src/story_script.py)（`parse_script_json`/`_clean_chapters`/`normalize_turns`ほか）
- 取り込みAPI: [`../review_server.py`](../review_server.py) の `import_main_script`
- 生成プロンプト（Geminiに渡す仕様書＝この仕様の背景説明）: `src/story_script.py` の `build_prompt`/`_output_block`
- 描画側（`meta.json`・型定義）: [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) §4.2 / `video/src/types.ts`
- 話者→VOICEVOX ID対応: `config/config.story.yaml` の `tts_voicevox.speakers`
