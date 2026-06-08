# アーキテクチャ概要

RSSから収集したニュースをGemini TTSで男女掛け合い音声に変換し、Whisperで字幕タイミングを付与してCloudflare Pagesで再生するシステム。

全体の流れ：
`Cloudflare Worker（定時トリガー）→ GitHub Actions → RSS収集 → スクリプト生成(Gemini) → 音声生成(Gemini TTS) → 字幕アライメント(Whisper) → meta.json出力 → Cloudflare Pagesデプロイ → ブラウザで再生`

実行は朝夕の定時（GitHub Actionsのcron、またはCloudflare Worker経由）と手動（workflow_dispatch）。

---

## 1. スケジューリング（Cloudflare Worker）

- **入力 / 出力**: 入力なし（Cron Triggerで起動）／ 出力：GitHub APIへのPOST（`workflow_dispatch`、body `{"ref":"main"}`）
- **言語・ライブラリ・API・外部サービス**: JavaScript（Cloudflare Workers）、`crons = ["0 20 * * *", "0 8 * * *"]`、GitHub REST API（`actions/workflows/digest.yml/dispatches`）、認証は環境変数 `GITHUB_PAT`
- **処理概要**: 定時にGitHubの `digest.yml` ワークフローをディスパッチして起動するだけのスケジューラ（`worker/index.js`）。失敗時は例外を投げる。

## 2. CI/CDワークフロー（GitHub Actions）

- **入力 / 出力**: 入力：`workflow_dispatch` または schedule（cron `0 21 * * *` / `0 9 * * *`）／ 出力：リポジトリへのコミット（`articles_cache.json`, `docs/main/`）とCloudflare Pagesデプロイ
- **言語・ライブラリ・API・外部サービス**: GitHub Actions（YAML）、Python 3.11、ffmpeg、Secrets（`GEMINI_API_KEY` / `HF_TOKEN` / `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`）、`actions/cache`（Whisperモデル）、`cloudflare/wrangler-action`
- **処理概要**: `digest.yml` は generate（`--skip-align`でGemini生成のみ）→ align（Whisperで字幕付与）→ deploy を順に実行。`align.yml` はアライメントのみ、`deploy.yml` はデプロイのみを手動実行できる。Whisperモデルは `~/.cache/huggingface` にキャッシュし、HFの429対策にリトライとキャッシュヒット時の `HF_HUB_OFFLINE` を併用。

## 3. メインオーケストレーション（main.py）

- **入力 / 出力**: 入力：CLI引数（`--config`, `--output-dir`, `--align-only`, `--skip-align`）と設定YAML／ 出力：`docs/<dir>/digest.mp3` と `docs/<dir>/meta.json`、`articles_cache.json`
- **言語・ライブラリ・API・外部サービス**: Python、`argparse`, `pyyaml`, 標準ライブラリ
- **処理概要**: 収集→生成→音声化→アライメント→`meta.json`出力を統括する。`--skip-align`はWhisperを呼ばず生成のみ、`--align-only`は既存の`meta.json`と`digest.mp3`からアライメントだけ再実行する（Gemini/TTSを使わない）。

## 4. RSS収集（src/collector.py）

- **入力 / 出力**: 入力：RSSフィードのURLリスト（config）と `articles_cache.json`／ 出力：記事dictのリスト `{title, url, source, category, summary, published_at, hash, used}` と更新後キャッシュ
- **言語・ライブラリ・API・外部サービス**: Python、`feedparser`、`hashlib`(md5)。外部APIなし
- **処理概要**: 各RSSを取得して `lookback_hours` 以内の記事を抽出し、URL/タイトルのmd5ハッシュで重複・使用済みを判定して新規記事のみ返す。キャッシュは `cache_days` で古いものを削除。

## 5. スクリプト生成（src/gemini_client.py）

- **入力 / 出力**: 入力：新規記事リスト＋config／ 出力：`(script, used_hashes)`。`script` は `[{"speaker", "text"}, ...]`、`used_hashes` は採用記事のハッシュ配列（モデルにJSON `{"selected_hashes", "script"}` で出力させてパース）
- **言語・ライブラリ・API・外部サービス**: Python、`google-generativeai`、Gemini API（既定 `gemini-2.5-flash`、無料枠）、`GEMINI_API_KEY`
- **処理概要**: 記事を `category`（japan / vibe）で分け、両方あれば「前半=日本ニュース・後半=バイブコーディング」の2部構成プロンプト、片方のみなら通常プロンプトでラジオ台本を生成する。429時はエラーメッセージのretry秒数（最低65秒）で最大3回リトライ。

## 6. 音声生成（src/tts_client.py）

- **入力 / 出力**: 入力：`script`＋config＋出力パス／ 出力：`digest.mp3` と各ターンの暫定タイムスタンプ `[{"start","end"}, ...]`
- **言語・ライブラリ・API・外部サービス**: Python、`google-genai`（Multi-speaker TTS、既定 `gemini-2.5-flash-preview-tts`）、`pykakasi`（漢字→かな）、`wave`、`ffmpeg`（libmp3lame）、`GEMINI_API_KEY`
- **処理概要**: 全ターンを「話者名: セリフ」に連結し、host/guestの音声を割り当てたMulti-speaker TTSを**1リクエスト**で生成（RPD節約）。返却PCMをWAV保存後ffmpegでMP3化。タイムスタンプはこの段階では音節数（句読点に重み）比の近似で、後段のWhisperアライメントで上書きされるフォールバック。

## 7. 字幕アライメント（src/aligner.py）

- **入力 / 出力**: 入力：`digest.mp3`、`script`、暫定タイムスタンプ／ 出力：各ターンに `start`/`end`、複数文ターンに `sentences:[{text,start,end}]` を付与した `script`
- **言語・ライブラリ・API・外部サービス**: Python、`faster-whisper`（`base`/CPU/int8、`vad_filter=True`）、`difflib`、`unicodedata`、`re`。HuggingFace Hub（モデルDL、`HF_TOKEN`）
- **処理概要**: Whisperで音声認識し、各セグメントを正規化テキストの類似度（近接ペナルティ＋飛び越し上限 `MAX_JUMP`）で台本の文に単調マッチさせる。ターン/文の境界すき間は次の単位へ寄せて連続化、マッチが健全なターンはマッチ時刻の相対位置をターン区間へ線形マップ、壊れたターンは文字数比で配分。末尾ターンは音声実尺（`total_duration`）へ補正し、全体を `LEAD_OFFSET`（0.5秒）前倒しする。Whisper入出力（`_transcribe`）と純粋ロジック（`align_segments`）を分離し、`test_align.py` でモック単体テスト可能。

## 8. meta.json出力（main.py: update_web）

- **入力 / 出力**: 入力：タイムスタンプ付き`script`／ 出力：`docs/<dir>/meta.json` `{generated_at, session, script}`
- **言語・ライブラリ・API・外部サービス**: Python、`json`、`datetime`（JST）
- **処理概要**: 生成時刻・版（朝版/夜版を時刻で判定）・スクリプトをJSONで書き出す。フロントエンドはこれを読んで字幕同期する。

## 9. デプロイ（Cloudflare Pages）

- **入力 / 出力**: 入力：`docs/` ディレクトリ／ 出力：`news-digest-tts.pages.dev` への静的サイト公開
- **言語・ライブラリ・API・外部サービス**: `cloudflare/wrangler-action`（`pages deploy docs/`）、`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`
- **処理概要**: `docs/`（index.html・各版の digest.mp3 / meta.json）をCloudflare Pagesにアップロードして配信する。

## 10. フロントエンド再生UI（docs/index.html）

- **入力 / 出力**: 入力：`<version>/meta.json` と `<version>/digest.mp3`（fetch）／ 出力：プレイヤー表示・字幕の追従ハイライト（DOM）
- **言語・ライブラリ・API・外部サービス**: 静的HTML/CSS/JS、`js-yaml`（CDN）、HTML5 `<audio>`、`localStorage`（GitHub Token・大人/子供版はタブ切替）
- **処理概要**: `meta.json` を読み込み、`audio` の `timeupdate` で現在時刻に対応するターン/文をハイライト（「startが現在時刻以下の最後の単位」を選択）。10秒送り/戻しボタン、書き起こしの追従スクロール・全文展開を備える。

## 11. 設定編集（フロントエンド → GitHub）

- **入力 / 出力**: 入力：設定フォーム（RSS・トピック・キャラクター・音声・モデル等）と `localStorage` のGitHub Token／ 出力：`config/config.yaml`（または `config.children.yaml`）へのコミット
- **言語・ライブラリ・API・外部サービス**: JavaScript、GitHub REST API（`contents` をGET/PUT）、`js-yaml`、`localStorage`（個人ブラウザのみ）
- **処理概要**: ブラウザから設定YAMLを取得・編集し、ユーザーのPATでGitHubに直接コミットする。秘密情報はLocalStorage（自分のブラウザ）にのみ保存し、サーバーには送らない。

---

## 設定・データファイル

- `config/config.yaml` / `config/config.children.yaml`: RSSソース・収集設定・トピック・除外語・キャラクター・モデル・TTS音声を定義
- `articles_cache.json`: 収集記事と使用済みフラグのキャッシュ（重複防止）
- `docs/main/`（・`docs/children/`）: `digest.mp3`（音声）・`meta.json`（字幕＋タイムスタンプ）
- `requirements.txt`: `feedparser`, `google-generativeai`, `google-genai`, `pyyaml`, `pykakasi`, `faster-whisper`
- `Dockerfile`: 開発コンテナ（node:22-slim + git/curl/python3/pip、Claude Code CLI）

## 設計上のポイント（実装に基づく）

- **無料枠・無課金前提**: Gemini無料枠、Whisperはローカル実行（faster-whisper）、Cloudflare Pages無料、GitHub Actions。
- **Gemini RPD節約**: TTSは1リクエスト生成。generate と align を分離し、アライメント再実行ではGeminiを消費しない。
- **字幕タイミング**: TTSは単語時刻を返さないため、別途Whisperで音声を解析して台本にアラインする方式。
