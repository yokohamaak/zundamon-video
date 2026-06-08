# zundamon-video

ずんだもん達の掛け合いで **IT技術史を語る動画** を自動生成するパイプライン。
1本=1テーマ深掘り（例「なぜGitは世界を変えたのか」「Unixはどう生まれたか」）。章立て時系列で構成し、章ごとに被写体が変わる。

```
Gemini（章立て掛け合い台本） → VOICEVOX（音声） → フリー素材画像（Wikimedia/Pexels/Pixabay） → Remotion（描画）
```

設計・実装計画の詳細は **[PLAN.md](PLAN.md)** を参照。

## 構成
- `src/story_script.py` … Gemini で章立て掛け合い台本を生成
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
