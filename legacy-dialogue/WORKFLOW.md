# 制作ワークフロー（実は〇〇雑学 動画）

ずんだもん×四国めたんの掛け合いで「実は〇〇」のテクノロジー雑学を作る動画パイプライン。
台本生成→画像取得→（人手レビュー）→音声→動画化、を半自動で回す。

## いちばん簡単な回し方：ラッパー `./run`

フラグを覚えなくてよい薄いラッパー（中身は下記の生コマンドを呼ぶだけ・Python無改変）。
```
./run            # 番号メニュー（引数なし）
./run help       # 一覧
本編:    ./run script → ./run review → ./run audio → ./run render
ショート: ./run shorts 1,2 → ./run review → ./run s-audio <slug> → ./run depth <slug> → ./run s-render <slug>
         （音声+深度+書き出しを一発: ./run s-build <slug>）
画像/見出しだけ直した時: ./run meta（音声据置で meta だけ再生成）
```
`RUN_DRY=1 ./run ...` で実行せずコマンド確認。以降は中身（生コマンド）の説明。

## 全体の流れ（生コマンド）

```
[1] 台本生成＋画像取得   python main_story.py --stop-after-images          （= ./run script）
        │                  Gemini台本 → 画像API取得 → docs/story/{script.json, review.json, 画像}
        │                  ※レビュー待ちで停止（音声は焼かない）
[2] レビュー（人手）      python review_server.py --dir docs/story          （= ./run review）
        │                  ブラウザ http://127.0.0.1:8765/ → /story で台本＋画像を編集
        │                  承認・調整・再取得など
[3] 音声＋meta            python main_story.py --from-script docs/story/script.json --images-from-dir  （= ./run audio）
        │                  VOICEVOX音声(digest.mp3)＋字幕タイムスタンプ → meta.json
[4] 動画化               cd video && npm run render        # 横16:9 → out/video.mp4   （= ./run render）
                           cd video && npm run render:short  # 縦9:16ショート → out/short.mp4
```

- **VOICEVOX**はホスト(Mac)でエンジン起動（:50021）。台本生成はGemini（無料枠）。画像はWikimedia/Pexels/Pixabay（取得にLLM不使用）。
- 反復確認は `npm run dev`（Remotion Studio・既存の digest.mp3 を再生）。**音声修正は再生成しないと反映されない**点に注意。
- prep（dev/render が必ず通す準備）の入力元は既定で `docs/story`。別ディレクトリは `SRC_DIR=/path npm run dev` で差し替え可。
- すべて無料枠・ローカル前提（従量課金なし）。

### ブラウザAIで台本を作る（Gemini枠を使わない）

Geminiの代わりに、ブラウザのAI（ChatGPT/Claude等）で台本JSONを作って取り込める。レビューツールの画面で完結：
- **本編**: パネル →「ブラウザAIで台本を作る」(`/compose`)。プロンプトをコピー→AIで生成→結果JSONを貼り付け「取り込む」→ `docs/story/script.json` 保存→（任意で「画像取得」）→ `/story` でレビュー。
- **ショート**: `/shorts` の「ブラウザAIで作る」。ネタをチェック→「プロンプトをコピー」→AIで生成→貼り付け「取り込む」→各 `docs/shorts/<slug>/` 保存→各カードの「画像取得」。
- 取り込みは **Geminiと同じパーサ**（JSON修復・正規化）。不正JSONはエラー表示。**Gemini呼び出しゼロ**。

### 画像レビューだけ直したとき（音声を作り直さない軽量ループ）

レビューで画像の差し替え/画像なし/クロップ/余白色などを直したら、`meta.json` を作り直さないと動画に反映されない（[3] と同じ。ただし音声は変わらないので作り直し不要）。VOICEVOX不要・課金なしの `--meta-only` を使う：

```
python main_story.py --from-script docs/story/script.json --meta-only   # 既存digest.mp3の尺を流用しmetaだけ再生成
cd video && npm run dev                                                  # Studioを再起動して反映を確認
```

※ 台本（台詞）を変えた場合は尺が変わるので、`--meta-only` ではなく通常の [3]（`--images-from-dir`・音声から作り直し）を使うこと。

## データファイル（docs/story/）

| ファイル | 役割 | 生成元 |
|---|---|---|
| `script.json` | 台本（theme/chapters[image_cuts]/script[turns]） | main_story（台本生成 or 手編集） |
| `review.json` | 画像の状態（カットごとの image/出典/調整オプション/承認） | 画像取得＋レビュー |
| `digest.mp3` | 掛け合い音声 | VOICEVOX |
| `meta.json` | 動画(Remotion)が読む最終構造（script＋timing＋topics＋credits） | build_meta |
| `credits.txt` | 概要欄用クレジット（CC-BY帰属はここで充足） | write_credits_txt |

## 何が調整可/不可/自動か（台本・レビュー・生成物の関係）

### ① 台本（script.json）— Geminiが生成、`/story`で編集

| 要素 | 生成 | `/story`編集 | 効く先 |
|---|---|---|---|
| theme（テーマ） | Gemini | ○ | meta.title |
| 章 title | Gemini | ○ | 章バッジ |
| 章 summary（要約） | Gemini | ○ | 概要表示のみ（動画に出ない） |
| 画像 image_query（英検索語） | Gemini | ○（追加/削除も） | 画像取得 |
| 画像 image_kind（被写体/雰囲気） | Gemini | ○ | 取得先＋fit既定 |
| 画像 image_query_ja（日本語） | Gemini | ○ | 表示のみ（検索に不使用） |
| 台詞 text | Gemini | ○（分割/削除も） | 音声・字幕 |
| 台詞 cut（どの画像） | Gemini | ○（サムネ選択） | 画像の切替タイミング |
| 台詞 speaker | Gemini | ✗ UIなし | 音声話者・立ち絵 |
| 台詞 emotion（表情） | Gemini | ✗ UIなし | 立ち絵の表情 |
| 台詞 effect（画面演出） | Gemini | ✗ UIなし | zoom/shake/flash等 |
| 台詞 voice（声の演技） | Gemini(任意) | ✗ UIなし | 速さ/高さ/抑揚/音量 |
| 台詞 pause（間） | Gemini(任意) | ✗ UIなし | 台詞後の無音 |
| chapter/section割当 | Gemini | ✗ UIなし | 構成 |

✗UIなし＝script.jsonを手編集 or 再生成で変更可（画面では未対応）。

### ② 画像の状態（review.json）— レビュー画面で決まる

| 要素 | 初期 | `/story`調整 |
|---|---|---|
| image（実画像） | 自動取得 | ○ 取得/再取得/差し替え(D&D) |
| fit（収め方） | kindで自動 | ○ 自動/cover/contain |
| crop（切り出し） | なし | ○ ドラッグ |
| filter（明/コ/白黒） | なし | ○ |
| pad/bg（余白px/色） | なし | ○ |
| hide（画像なし） | — | ○ |
| attribution（出典） | 取得時自動 | ○ 編集 |
| approved（承認） | — | ○ |

### ③ 完全自動（人は触らない）

| 生成物 | 算出元 |
|---|---|
| digest.mp3（音声） | VOICEVOX |
| 字幕 start/end | VOICEVOX実尺 |
| topics 表示区間 | cut＋音声タイミング |
| Ken Burns（画像の動き） | カット番号で自動 |
| credits.txt | 画像の帰属から自動 |
| meta.json | build_meta が組立 |

要点：台本＝Gemini生成→`/story`で大半を編集（話者/表情/演出/声/間だけUI未対応）。画像＝自動取得→レビュー調整。音声/タイミング/動き/クレジット＝完全自動。

## レビュー画面（review_server.py）

- **`/story`（メイン）**：物語軸の統合編集。
  - 概要：セクション（intro/trivia1…/outro）を縦に並べ、タイトル/要約/画像サムネを表示→クリックで展開。
  - 章詳細：タイトル/要約編集、画像カット（検索語/日本語/種別/取得・再取得/追加・削除）、台本（編集/分割/削除、サムネで画像割当=cut）。
  - 画像クリック or「調整」：fit・クロップ・補正・余白色・画像なし・差し替え(D&D)・出典 をインライン調整。
- **`/images`**：全画像をグリッドで一括確認・承認（`/story` と同じ review.json を共有）。
- **`/`**：制作パネル（工程と状態・各画面リンク）。

## main_story.py のフラグ

| フラグ | 効果 |
|---|---|
| `--script-only` | 台本生成だけで停止（VOICEVOX不要） |
| `--stop-after-images` | 台本＋画像取得＋review.json/script.json を出力して停止（レビュー用） |
| `--from-script PATH` | 既存 script.json を使いGemini生成をskip |
| `--images-from-dir` | 画像取得をskipし review.json の人手結果から meta を生成（レビュー承認後の続行） |
| `--meta-only` | 音声を作り直さず既存 digest.mp3 の尺を流用し meta.json だけ再生成（VOICEVOX不要・課金なし。画像レビュー微修正の反映用。`--from-script` 必須） |
| `--short-from N` | 本編(`--from-script`)の第N章(trivia)を自己完結ショート台本に書き直す（縦9:16用・約40秒） |
| `--shorts-from "N,M"` | 本編の複数trivia章を **Gemini 1回でまとめて** ショート化（各 `docs/shorts/<自動slug>/` へ・画像取得まで）。Gemini枠節約 |
| `--slug NAME` | ショートの出力名。指定時は出力先を `docs/shorts/NAME/` にする（単発 `--short-from` 用） |
| `--no-images` | 画像取得を無効化（全プレースホルダ） |

## C-1：カット割り（画像の切替タイミング）

各台詞に `cut`（その章の何番目の画像か・0始まり・非減少）を持たせ、画像切替を話の流れに合わせる。
Geminiが付与し、`/story` のサムネ選択で人が直せる。`cut` が無い章は均等割りにフォールバック。
※ build_meta は cut+timing を併せ持つ merged script を build_chapter_topics に渡す（TTSターンは cut を持たないため）。

## 音声のテキスト整形（src/tts_voicevox.py `_spoken_text`）

字幕は原文のまま、**音声に渡すテキストだけ**を整える：
- 読み仮名「英字（かな）」→かな（二重読み防止。空白入りも対応）
- 語末の促音「っ」（句末直前）を除去（囁き化防止）
- 感嘆「へぇ/ええ」系を正規形（へえ〜/ええ〜）に
- Markdown崩れ（**強調**等）の除去（normalize_turns側）
- **英字→カタカナ読み辞書**：`config/readings.json`（組み込み辞書を上書き/追記・`_`始まりキーは無視）。Hi-Fi→ハイファイ等の1字読みを解消。

## 既知の注意点（gotcha）

- **サーバ再起動が必要**：review_server.py は自動リロードしない。コード変更後は Ctrl+C → 再起動＋ブラウザのハードリロード。
- **画像カットを“途中”で削除するとずれる**：review.json は (章,カット番号) の位置でキーされるため、中間カットを削除すると以降の画像割当がずれる。削除後は周辺カットを**再取得**して整合させること（末尾カットの削除は問題なし）。
- **docs/story と video/public は生成物**（gitignore/未追跡）。コードのみコミット対象。
- 音声修正は **音声再生成（[3]）** しないと `npm run dev` に反映されない（既存 mp3 を再生するため）。
- **画像レビューの修正は review.json に入るだけ**。`meta.json` を再生成（`--meta-only` か [3]）→ `npm run dev` 再起動で初めて動画に反映される。「レビューで直したのに動画が変わらない」時はこれが原因。
- **ショート（縦9:16）**：`npm run render:short` で1ネタ（既定=最初のtrivia章）を縦動画化。章指定は `remotion render DialogueVideoShort out/s.mp4 --props='{"clipChapter":2}'`。縦レイアウトの数値調整は `video/src/DialogueVideo.tsx` の `BOARD_PORTRAIT` と `layoutFor()` の portrait 側。詳細は下記「ショート設計」。

## ショート（縦9:16）

縦化コンポジション `DialogueVideoShort`。**本編の切り抜きではなく、本編の1ネタを「自己完結した短尺台本」に書き直して別生成**する（"さっき"参照や尺の不自然さを避けるため）。出力は `docs/shorts/<slug>/` に本編とは独立した script/review/meta/digest/画像。

### 作り方（`./run` が簡単。または `/shorts` ハブ／生コマンド）

`./run` 版：`./run shorts 1,2` → `./run review`（/shorts で台本レビュー）→ `./run s-build <slug>`（音声+深度+書き出し）。

```
# 1) 本編の複数ネタを Gemini 1回でまとめてショート化（各 docs/shorts/<自動slug>/ へ・画像取得まで）
python main_story.py --from-script docs/story/script.json --shorts-from "N,M"          （= ./run shorts N,M）
#    /shorts ハブなら：作るネタにチェック→「選択ネタをまとめて生成」（Gemini呼び出しは1回）
#    （1本だけ・名前指定なら --short-from N --slug NAME --stop-after-images）
# 2) 台本レビュー（/shorts の「台本レビュー」= 対象を docs/shorts/NAME に切替えた /story で編集。hookもここで編集）
python review_server.py --dir docs/story      # /shorts ハブから操作（生成・レビュー・書き出し） （= ./run review）
# 3) 音声+meta（VOICEVOX）
python main_story.py --from-script docs/shorts/NAME/script.json --images-from-dir --output-dir docs/shorts/NAME  （= ./run s-audio NAME）
# 4) 任意: 深度生成（パララックス）   python make_depth.py --dir docs/shorts/NAME          （= ./run depth NAME）
# 5) 書き出し                         cd video && SRC_DIR=../docs/shorts/NAME npm run render:short  （= ./run s-render NAME）
```

- `/shorts` ハブ＝①本編ネタ選択→生成 ②作成済み一覧 ③各ショートの台本レビュー/書き出し。生成(Gemini)・音声(VOICEVOX)はMac前提。
- **固定見出し(hook)を編集だけ**したら meta 再生成が要る：`python main_story.py --from-script docs/shorts/NAME/script.json --meta-only --output-dir docs/shorts/NAME`（音声据置）。
- ショートの**目標尺は約40秒**（レビューの推定ゲージ・文字数予算もショート対象なら自動でこの基準に切替）。

### 画面構成・演出

- 上＝**固定見出し(hook)**（黄/黒で字幕より目立たせ視線誘導・出しっぱなし）／中＝画像（主役・大きく）／下＝ライブ字幕。**キャラ立ち絵・章バッジは出さない**。
- **セーフゾーン**: 上=iPhone Dynamic Island、下〜26%=YouTube ShortsのUIを避けて配置。
- **終盤CTA**: 末尾約3.5秒に「続きは本編で／登録」。`--props='{"ctaText":"..."}'` で上書き、`""` で非表示。
- **末尾**: 声をフェードアウト＋映像は章末カットで固定（ブツ切り防止）。
- 縦レイアウトの数値調整は `video/src/DialogueVideo.tsx` の `BOARD_PORTRAIT` / `layoutFor()` の portrait 側。

### 2.5Dパララックス（静止画を"動画らしく"・ローカル/無料）

写真カットは深度マップで奥行きカメラ移動。ロゴ/スクショ(contain)は全体表示にフォールバック。

```
pip install torch transformers pillow numpy   # 初回のみ（Mac/ローカル）
python make_depth.py --dir docs/shorts/NAME    # 各画像の <base>.depth.png を生成（Depth Anything V2・数秒/枚）
```

- 従量課金なし・全ローカル（初回だけモデルDL）。深度が無い画像は通常のKen Burnsにフォールバック（必須ではない）。
- コンテンツを変えて**同じ slug を作り直す**ときは古い `*.depth.png` を消すか `--overwrite`（既存はスキップされるため）。
