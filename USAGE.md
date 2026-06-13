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
cd video && npm run render                                                # [4] 横16:9 → out/video.mp4
cd video && npm run render:short                                         # [4'] 縦9:16ショート(1ネタ) → out/short.mp4
```

> 画像レビューだけ直した場合は [3] の代わりに `python main_story.py --from-script docs/story/script.json --meta-only`（音声を作り直さずmetaだけ再生成・VOICEVOX不要）。

### B. レビューなしで一気に通す（確認用）
```bash
python main_story.py                          # 台本→画像→音声→meta を一括
cd video && npm run render
```

> `npm run dev`/`render` の入力元は既定で `docs/story`（prepが自動でpublicへコピー）。別ディレクトリは `SRC_DIR=/path npm run dev`。

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
| 画像レビューだけ直して反映（音声据置・速い） | `python main_story.py --from-script docs/story/script.json --meta-only` |
| 画像なし（全プレースホルダ）で動作確認 | `python main_story.py --no-images` |
| 既存台本を手編集して作り直す | `python main_story.py --from-script docs/story/script.json` |
| 動画を反復確認（速い・HMR） | `cd video && npm run dev` |
| 最終mp4を書き出す（横16:9） | `cd video && npm run render` |
| ショート台本を生成（本編ネタ→自己完結短尺・約40秒） | `python main_story.py --from-script docs/story/script.json --short-from N --slug NAME --stop-after-images` |
| ショートの音声+meta | `python main_story.py --from-script docs/shorts/NAME/script.json --images-from-dir --output-dir docs/shorts/NAME` |
| ショート書き出し（縦9:16） | `cd video && SRC_DIR=../docs/shorts/NAME npm run render:short` |
| ショートの画像を“動画らしく”動かす深度生成 | `python make_depth.py --dir docs/shorts/NAME`（要 `pip install torch transformers pillow numpy`・ローカル/無料） |
| ショートの制作ハブ（生成→レビュー→書き出し） | `python review_server.py --dir docs/story` → ブラウザ `/shorts` |
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
| `--meta-only` | off | 音声を作り直さず既存 digest.mp3 の尺を流用し meta.json だけ再生成（VOICEVOX不要・課金なし。画像レビュー微修正の反映用。`--from-script` 必須） |
| `--short-from N` | — | 本編(`--from-script`)の第N章(trivia)を自己完結ショート台本に書き直す（縦9:16・約40秒） |
| `--slug NAME` | — | ショートの出力名。指定時は出力先を `docs/shorts/NAME/` にする |
| `--no-images` | off | 画像取得を無効化し全プレースホルダで続行 |

**よく使う組み合わせ**
- 通常生成：`python main_story.py`
- レビュー前停止：`python main_story.py --stop-after-images`
- 承認後続行：`python main_story.py --from-script docs/story/script.json --images-from-dir`
- 画像レビューだけ反映（音声据置・速い）：`python main_story.py --from-script docs/story/script.json --meta-only`
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
| `topic_history/<genre>.json` | **避けるネタの永続履歴**（採用済み＋却下・動画をまたいで残る） |

---

## 5-1. 台本を作り直す・履歴をリセット

**台本は新規生成（Gemini呼び出し）のときだけ作り直される。** `--from-script` は既存 `script.json` を再利用＝作り直さない。

| やりたいこと | 方法 |
|---|---|
| 台本を作り直す（短さ等の修正を効かせる） | `python main_story.py --stop-after-images`（常に新規生成）／レビュー画面「全体を作り直す」 |
| 今の台本を確実に使わせない | `docs/story/script.json` を削除（`--from-script` で再利用されなくなる） |
| 今のネタを“避けずに”引き直したい | `topic_history/<genre>.json` を削除 or 空に（`cat /dev/null >` でOK＝コードは空を「履歴なし」扱い）。⚠️過去の回避も全部消える |

注意:
- `topic_history/<genre>.json`：空ファイル/削除のどちらも安全（無し扱い）。
- `script.json`：空にした後 `--from-script` を使うとエラー（新規生成なら問題なし）。
- `review.json`：空にするとレビューツールがクラッシュ。消すなら丸ごと削除（どうせ再生成で上書き）。

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
