# legacy-dialogue（凍結中）

ずんだもん×四国めたんの掛け合いで「実は〇〇」テクノロジー雑学動画を自動生成する旧パイプライン。
**開発凍結中**。現行のストーリーツール（リポジトリ直下）とはコードを共有しない方針で、
共有していたファイルはこのディレクトリ内にスナップショットとしてコピー済み。

```
Gemini（掛け合い台本） → VOICEVOX（音声） → フリー素材画像（Wikimedia/Pexels/Pixabay） → Remotion（描画）
```

## ディレクトリ構成（リポジトリ直下と同じレイアウトを維持）
- `run` … 番号メニュー付きラッパー（`./run help`）
- `main_story.py` … パイプライン統合
- `review_server.py` / `review_story_page.html` … 画像レビュー承認ツール
- `make_depth.py` … 深度マップ推定（縦ショートのパララックス用）
- `src/` … 台本生成・画像取得・TTS（`tts_voicevox.py` は現行側からのスナップショット）
- `config/` … `config.story.yaml`（専用）＋ `readings.json` / `kanji_readings.json`（スナップショット）
- `docs/stories/` `docs/shorts/` `docs/story`(シムリンク) … 生成済みプロジェクトデータ
- `topic_history/` … 出題済みトピック履歴
- `video/` … Remotion ソースのスナップショット（`DialogueVideo` / `DialogueChapter` / `DialogueVideoShort`）
- `test_*.py` … 掛け合い系テスト一式
- `USAGE.md` / `WORKFLOW.md` / `PLAN.md` / `docs/` … 当時のドキュメント

## 再開手順
1. `cd legacy-dialogue/video && npm install`（node_modules はコミットしていない）
2. 立ち絵・BGM・SE 素材を現行側からコピー: `cp -R ../../video/assets video/assets`
   （凍結時点と同一である必要はないが、`avatars/` のパーツ構成は manifest 生成に必要）
3. `legacy-dialogue/.env` に `GEMINI_API_KEY`（必須）、`PEXELS_API_KEY` / `PIXABAY_API_KEY`（任意）を設定
   （`main_story.py` は自分と同じディレクトリの `.env` を読む）
4. `./run` で従来どおり操作（Python側は `legacy-dialogue/` をルートとして無改変で動く）

## 凍結時点
2026-07-04。以後、現行ストーリーツール側の `Avatar.tsx` / `tts_voicevox.py` 等の改良は
ここには反映されない（意図的な分離）。
