# zundamon-video

ずんだもん×四国めたんの掛け合いで **「実は〇〇」のテクノロジー雑学動画** を自動生成するパイプライン。
1本=1小テーマに「実は」ネタを複数束ねる（例「実は知らないデジタルの名前の謎」）。各ネタは intro→各ネタ(trivia)→outro の章立てで、ネタごとに被写体が変わる。

```
Gemini（掛け合い台本） → VOICEVOX（音声） → フリー素材画像（Wikimedia/Pexels/Pixabay） → Remotion（描画）
```

- **いちばん簡単な実行** → `./run`（番号メニュー）/ `./run help`（一覧）。フラグを覚えず本編・ショートを回せる薄いラッパー
- **実行方法・全オプション** → **[USAGE.md](USAGE.md)**（コマンド/オプションのチートシート）
- 制作ワークフロー・データ構造 → **[WORKFLOW.md](WORKFLOW.md)**
- 設計・実装計画 → **[PLAN.md](PLAN.md)**

## 構成
- `src/story_script.py` … Gemini で「実は〇〇雑学」掛け合い台本を生成
- `src/tts_voicevox.py` … VOICEVOX で音声合成
- `src/manual_cuts.py` … 画像の手動差し替えフォールバック
- `src/image_fetch.py` … 画像取得の振り分け（Wikimedia / Pexels / Pixabay）※Phase2
- `main_story.py` … パイプライン統合
- `video/` … Remotion 描画プロジェクト

## 環境変数（`.env`）
| 変数 | 用途 |
|---|---|
| `GEMINI_API_KEY` | 台本生成 |
| `PEXELS_API_KEY` | ambient画像取得（Phase2） |
| `PIXABAY_API_KEY` | ambient画像取得（Phase2） |

無料枠のみ使用。秘密情報は `.env` 管理（コミット禁止）。画像は商用可ライセンス（PD/CC0/CC-BY/Pexels/Pixabay）のみ使用。
