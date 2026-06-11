# USAGE — ツール実行リファレンス

各スクリプトの**起動方法とオプション**をまとめたチートシート。
制作の流れ・データ構造の説明は [WORKFLOW.md](WORKFLOW.md)、音声/間/演出の調整値は [docs/60ten-review.md](docs/60ten-review.md) を参照。

---

## 0. セットアップ（最初に1回）

```bash
# Python 依存（Docker環境はインストール済。Mac等は .venv 推奨）
pip install -r requirements.txt          # google-genai, pyyaml（画像保存時のみ pillow を追加）

# .env を作成（コミット禁止・無料枠のみ）
cat > .env <<'EOF'
GEMINI_API_KEY=...      # 台本生成（無料枠）
PEXELS_API_KEY=...      # ambient画像（無料登録）
PIXABAY_API_KEY=...     # ambient画像（無料登録）
EOF

# Remotion 側の依存
cd video && npm install && cd ..

# 立ち絵パーツ（本物PNGを assets/avatars/<char>/ に置く。PSDから書き出す場合のみ↓）
cd video && node scripts/psd-export.mjs build zundamon && node scripts/psd-export.mjs build metan && cd ..
```

**VOICEVOX**：ホスト(Mac)でアプリ/エンジンを起動（`:50021`）。コンテナから使う場合は `VOICEVOX_URL=http://host.docker.internal:50021`。

---

## 1. クイックスタート

### A. レビューを挟んで作る（推奨・本番フロー）
```bash
python main_story.py --stop-after-images                                  # [1] 台本+画像取得→停止
python review_server.py --dir docs/story                                  # [2] ブラウザで確認/編集/承認
python main_story.py --from-script docs/story/script.json --images-from-dir   # [3] 音声+meta
cd video && SRC_DIR=../docs/story npm run render                          # [4] 動画化 → out/video.mp4
```

### B. レビューなしで一気に通す（確認用）
```bash
python main_story.py                          # 台本→画像→音声→meta を一括
cd video && SRC_DIR=../docs/story npm run render
```

### C. 台本だけ先に見る（無料・VOICEVOX不要）
```bash
python main_story.py --script-only            # docs/story/script.json を出力して停止
```

---

## 2. やりたいこと別インデックス

| やりたいこと | コマンド |
|---|---|
| 台本だけ生成して読む | `python main_story.py --script-only` |
| 台本＋画像を取得してレビューに回す | `python main_story.py --stop-after-images` |
| ブラウザで台本・画像を確認/差し替え | `python review_server.py --dir docs/story` |
| 承認後に音声＋metaを作る | `python main_story.py --from-script docs/story/script.json --images-from-dir` |
| 画像なし（全プレースホルダ）で動作確認 | `python main_story.py --no-images` |
| 既存台本を手編集して作り直す | `python main_story.py --from-script docs/story/script.json` |
| 動画を反復確認（速い・HMR） | `cd video && SRC_DIR=../docs/story npm run dev` |
| 最終mp4を書き出す | `cd video && SRC_DIR=../docs/story npm run render` |
| テストを回す | 下記「テスト」 |

---

## 3. コマンドリファレンス

### `main_story.py` — パイプライン統合
```bash
python main_story.py [オプション]
```
| オプション | 既定 | 効果 |
|---|---|---|
| `--config PATH` | `config/config.story.yaml` | 設定ファイル |
| `--output-dir DIR` | `docs/story` | 出力先（script/review/mp3/meta/credits） |
| `--script-only` | off | 台本生成だけで停止（VOICEVOX不要） |
| `--stop-after-images` | off | 台本＋画像取得＋review.json/script.json を出力して停止（レビュー用） |
| `--from-script PATH` | — | 既存 script.json を使い Gemini 生成をskip |
| `--images-from-dir` | off | 画像取得をskipし review.json の承認結果から meta を生成（承認後の続行） |
| `--no-images` | off | 画像取得を無効化し全プレースホルダで続行 |

**よく使う組み合わせ**
- 通常生成：`python main_story.py`
- レビュー前停止：`python main_story.py --stop-after-images`
- 承認後続行：`python main_story.py --from-script docs/story/script.json --images-from-dir`
- 台本手直し後に再生成：`python main_story.py --from-script docs/story/script.json`（画像は取り直す）

### `review_server.py` — 台本＋画像の中身レビュー（ローカルWeb）
```bash
python review_server.py [--dir docs/story] [--port 8765]
```
| オプション | 既定 | 効果 |
|---|---|---|
| `--dir DIR` | `docs/story` | review.json・画像のあるディレクトリ |
| `--port N` | `8765` | 待受ポート（`127.0.0.1` のみ＝ローカル限定） |

起動後ブラウザで **http://127.0.0.1:8765/** 。画面:
| ルート | 内容 |
|---|---|
| `/` | 制作パネル（工程・状態・各画面リンク） |
| `/story` | ★メイン。台本＋画像を一体で確認/編集（行サムネにD&Dで画像差し替え・章再生成・出典編集） |
| `/images` | 全画像をグリッドで一括確認・承認 |

> 注意：自動リロードしない。コード変更後は Ctrl+C→再起動＋ブラウザのハードリロード。
> **render前の静止画＋台本のレビュー専用**。動き・音声は確認できない（→ mp4 か `npm run dev`）。

### npm スクリプト（`video/` 内で実行）
```bash
cd video && SRC_DIR=../docs/story npm run <script>
```
| スクリプト | 内容 |
|---|---|
| `dev` | prep → Remotion Studio 起動（反復確認・HMR） |
| `render` | prep → `out/video.mp4` を書き出し |
| `prep` | `SRC_DIR` の meta/画像を public へ取り込む（dev/renderが内部で実行） |
| `compositions` | コンポジション一覧 |
| `typecheck` | `tsc --noEmit` |
| `parts` | 立ち絵パーツのプレースホルダSVGを生成（本物PNGが無い時の動作確認用） |
| `psd:preview` / `psd:build` | 立ち絵PSDからパーツPNGを書き出し（`node scripts/psd-export.mjs preview|build <zundamon|metan>`） |

- **`SRC_DIR`**（環境変数）＝入力元。省略すると既定の入力を見るため、雑学動画では必ず `SRC_DIR=../docs/story` を付ける。
- 音声を直したら **音声再生成（[3]）しないと dev/render に反映されない**（既存 mp3 を再生するため）。

### テスト（無料・ローカル・VOICEVOX/API不要）
```bash
python3 test_story_script.py        # 台本生成プロンプト/正規化
python3 test_tts_voicevox.py        # 音声合成の字幕タイミング・間（章境界gap含む）
python3 test_story_meta.py          # meta 組み立て
python3 test_image_clients.py       # Wikimedia/Pexels/Pixabay クライアント
python3 test_topic_history.py       # ネタ重複回避の履歴
python3 test_manual_cuts.py         # 手動カット差し替え
python3 test_review.py              # レビュー純ロジック
python3 test_pipeline_integration.py# 結合
# 一括:
for t in test_*.py; do echo "== $t =="; python3 "$t" || break; done
```

---

## 4. 環境変数

| 変数 | 用途 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | 台本生成（Gemini無料枠） | 台本生成時 |
| `PEXELS_API_KEY` | ambient画像取得 | 画像取得時 |
| `PIXABAY_API_KEY` | ambient画像取得 | 画像取得時 |
| `VOICEVOX_URL` | 音声合成の接続先（既定 `http://localhost:50021`） | 任意 |
| `SRC_DIR` | Remotion の入力元ディレクトリ | render/dev時 |
| `TOPIC_HISTORY_DIR` | ネタ履歴の保存先（既定 `topic_history`） | 任意 |

`.env` に書けば `main_story.py` が自動読込（既存の環境変数を優先）。**コミット禁止**。

---

## 5. 生成ファイル（`docs/story/`・gitignore対象）

| ファイル | 役割 |
|---|---|
| `script.json` | 台本（theme/chapters/script） |
| `review.json` | 画像の状態・出典・調整・承認 |
| `digest.mp3` | 掛け合い音声 |
| `meta.json` | Remotion が読む最終構造（台本＋timing＋topics＋credits） |
| `credits.txt` | 概要欄用クレジット（CC-BY帰属を充足） |

---

## 6. 調整したいとき

| 調整対象 | 場所 |
|---|---|
| ネタ数・尺・話者・テーマ | `config/config.story.yaml`（`story:`） |
| 間・速度・章境界gap・字幕文字数 | `config/config.story.yaml`（`tts_voicevox:`）→ 早見表は [docs/60ten-review.md](docs/60ten-review.md) |
| BGM・効果音(SE)・音量 | `config/config.story.yaml`（`audio:`）。音源は `video/assets/bgm/`・`video/assets/se/` に置く（[README](video/assets/se/README.md)・未配置は無音でスキップ） |
| 英字→カタカナ読み辞書 | `config/readings.json` |
| 台本の作風（型・フック） | `src/story_script.py` のプロンプト |
| 立ち絵の動き・うなずき・演出 | `video/src/Avatar.tsx` / `video/src/DialogueVideo.tsx` |

トラブル時の注意点（カット削除のズレ・サーバ再起動など）は [WORKFLOW.md](WORKFLOW.md) の「既知の注意点」を参照。
