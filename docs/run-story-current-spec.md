# run-story 現在仕様メモ

`run-story` は **会話劇動画専用のローカル完結ツール**。主役は `story_editor.html` / `story_editor.py` と `video/src/StoryVideo.tsx` で、`run` 側の掛け合い雑学パイプラインとは別系統。

## 1. いまの編集対象
- 保存元: `video/public/story-01.json`
- 主要UI: `./run-story story`
- 補助UI:
  - シーン編集
  - 表情編集
  - ポーズ編集
  - 台本生成
  - 音（BGM/SE）

## 2. `story-01.json` の主な構造
トップレベル:
```json
{
  "title": "タイトル",
  "audio": "story-01.mp3",
  "idleFace": "normal",
  "bgm": [],
  "overlays": [],
  "script": []
}
```

`script[]` の必須:
- `speaker`
- `text`
- `scene`

`script[]` の主要任意項目:
- 演技/表示: `expression`, `pose`, `face`, `narrationVoice`, `voice`
- 口パク制御: `noLipSync`
- 場面演出: `transition`, `emphasis`, `shake`, `cameraEffect`, `flashback`, `telop`
- 尺/吹き出し: `pause`, `continueBubble`, `disableAutoBubbleSplit`
- 登退場: `enter`, `exit`, `exitDir`
- 画面差し込み: `insert`
- 音: `se`
- 音声生成後に自動付与: `start`, `end`, `sentences`, `id`

`insert.kind` は現在この5種:
- `warning`
- `ok`
- `chat`
- `teamchat`
- `mailer`

## 3. エディタで今できること
- 左ペインでターン並び替え・追加・削除
- 中央で Remotion Player プレビュー
- 下部タイムラインでセリフ確認、BGM区間編集、Overlay区間編集
- 右ペインで話者・ナレーション・表情・ポーズ・演出・手動SE・インサート編集
- `台本生成` タブで AI へ貼るプロンプトをローカル生成し、返ってきた JSON を取り込み
- `音` タブで SE 自動マップ、シーン別BGM、プレビュー再生

## 4. AI台本生成まわりの現仕様
- 外部API連携はしない。**プロンプト生成だけローカル**で行い、ChatGPT/Claude等への貼り付けは手動。
- 取り込み時は ```json fenced block``` や前後の説明文つきでも読める。
- 既知の項目はそのまま保存し、未対応の新フィールド / 新シーン / 新表情 / 新インサートだけを検出表示する。
- いまの既知ターン項目には `pose`, `transition`, `voice`, `narrationVoice`, `noLipSync`, `continueBubble`, `disableAutoBubbleSplit`, `se` も含む。

## 5. 実行フロー
1. `./run-story story`
2. 台本編集 or 台本生成タブでドラフト取り込み
3. `🔊 音声生成` で `start/end/sentences/audio` を確定
4. （必要なら）`./run-story dev` で確認
5. `./run-story render` で `video/out/<タイトル>.mp4`（エディタ内の書き出しボタンと同じ命名。ファイル名はタイトルから自動生成）

シーン編集・表情編集・ポーズ編集・台本生成・音(BGM/SE)設定は全て `./run-story story` の中のタブでできるため、
単体コマンドは用意していない。

## 6. 補足
- `story_editor.py` 起動時に `prep-story.mjs` と `build-story-player.mjs` が自動実行される
  （`./run-story prep` / `player-build` は素材反映やプレビューがおかしい時の手動フォールバック）。
- BGM は `story.bgm` があれば時間ベース編集を優先し、空ならシーン別BGMへフォールバックする。
- ナレーション行は `narrationVoice` を使い、吹き出し・立ち絵・話者ズームを出さない。
- `video/scripts/build-story-01.py` はエディタ登場前の初期生成用スクリプトで現在は不使用（実行すると台本が初期版で上書きされるため触らないこと）。
