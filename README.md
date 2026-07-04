# zundamon-video

ずんだもん×四国めたんの **ストーリー調 会話劇動画** をローカル完結で制作するツール群。
台本編集・演出・音声生成(VOICEVOX)・書き出し(Remotion)までブラウザのエディタで一気通貫。

```
ストーリーエディタ（台本/演出/シーン/表情/ポーズ/音） → VOICEVOX（音声） → Remotion（描画）
```

- **いちばん簡単な実行** → `./run-story`（番号メニュー）/ `./run-story help`（一覧）
- 現在仕様の詳細 → **[docs/run-story-current-spec.md](docs/run-story-current-spec.md)**

## 構成
- `story_editor.py` / `story_editor.html` … ストーリーエディタ本体（台本・演出・シーン・表情・ポーズ・台本生成・音・書き出し）
- `scene_editor.py` / `expression_editor.py` / `pose_editor.py` … エディタ内タブとして同居配信されるサブエディタ
- `make_story_audio.py` + `src/tts_voicevox.py` … VOICEVOX 音声生成と実尺の台本書き戻し
- `video/` … Remotion 描画プロジェクト（`StoryVideo` コンポジション）
- `config/` … 読み仮名辞書・声プロファイル
- `legacy-dialogue/` … **凍結中**の旧・掛け合い雑学動画パイプライン（下記）

## 旧・掛け合い雑学動画（凍結）
「実は〇〇雑学」の掛け合い動画パイプライン（Gemini台本 → フリー素材画像 → Remotion）は開発凍結し、
共有コードのスナップショットごと `legacy-dialogue/` に隔離済み。詳細・再開手順は
**[legacy-dialogue/README.md](legacy-dialogue/README.md)** を参照。

## 環境変数（`.env`）
現行のストーリーツールは外部APIを使わない（VOICEVOXはローカルエンジン）。
`.env` は凍結中の掛け合いパイプライン用（`legacy-dialogue/README.md` 参照）。
秘密情報は `.env` 管理（コミット禁止）。
