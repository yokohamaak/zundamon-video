# 制作ワークフロー（実は〇〇雑学 動画）

ずんだもん×四国めたんの掛け合いで「実は〇〇」のテクノロジー雑学を作る動画パイプライン。
台本生成→画像取得→（人手レビュー）→音声→動画化、を半自動で回す。

## 全体の流れ

```
[1] 台本生成＋画像取得   python main_story.py --stop-after-images
        │                  Gemini台本 → 画像API取得 → docs/story/{script.json, review.json, 画像}
        │                  ※レビュー待ちで停止（音声は焼かない）
[2] レビュー（人手）      python review_server.py --dir docs/story
        │                  ブラウザ http://127.0.0.1:8765/ → /story で台本＋画像を編集
        │                  承認・調整・再取得など
[3] 音声＋meta            python main_story.py --from-script docs/story/script.json --images-from-dir
        │                  VOICEVOX音声(digest.mp3)＋字幕タイムスタンプ → meta.json
[4] 動画化               cd video && SRC_DIR=../docs/story npm run render
                           Remotion で out/video.mp4
```

- **VOICEVOX**はホスト(Mac)でエンジン起動（:50021）。台本生成はGemini（無料枠）。画像はWikimedia/Pexels/Pixabay（取得にLLM不使用）。
- 反復確認は `npm run dev`（Remotion Studio・既存の digest.mp3 を再生）。**音声修正は再生成しないと反映されない**点に注意。
- すべて無料枠・ローカル前提（従量課金なし）。

## データファイル（docs/story/）

| ファイル | 役割 | 生成元 |
|---|---|---|
| `script.json` | 台本（theme/chapters[image_cuts]/script[turns]） | main_story（台本生成 or 手編集） |
| `review.json` | 画像の状態（カットごとの image/出典/調整オプション/承認） | 画像取得＋レビュー |
| `digest.mp3` | 掛け合い音声 | VOICEVOX |
| `meta.json` | 動画(Remotion)が読む最終構造（script＋timing＋topics＋credits） | build_meta |
| `credits.txt` | 概要欄用クレジット（CC-BY帰属はここで充足） | write_credits_txt |

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
