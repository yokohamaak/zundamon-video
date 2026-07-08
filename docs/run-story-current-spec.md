# run-story 現在仕様メモ

`run-story` は **会話劇動画専用のローカル完結ツール**。主役は `story_editor.py` / `story_editor.html` と `video/src/StoryVideo.tsx` で、旧 `run` 系の掛け合い雑学パイプラインとは別系統。

## 1. いまの主な編集対象
- 保存元: `video/public/story-01.json`
- 主要UI: `./run-story story`
- 同居UI:
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
  "effectSettings": {},
  "script": []
}
```

`script[]` の必須:
- `speaker`
- `text`
- `scene`

`script[]` の主要任意項目:
- 演技/表示: `expression`, `pose`, `face`, `narrationVoice`, `voice`
- セリフ表示: `subtitleMode` (`"subtitle"` で下部字幕)
- 字幕見た目: `subtitleStyle`（`fontSize` / `textColor` / `boxBorder` / `boxBorderColor` / `boxBorderWidth`）
- 表示制御: `hideCharacters`（`true` でそのターンは立ち絵/モブを出さない）
- 口パク制御: `noLipSync`
- 場面演出: `transition`, `emphasis`, `shake`, `flashback`, `telop`, `impactText`
- カメラ: `cameraEffects`, `cameraEffectSettings`, `zoomTarget`, `cameraCenter`
- 尺/吹き出し: `pause`, `continueBubble`, `disableAutoBubbleSplit`
- 登退場: `enter`, `enterDir`, `exit`, `exitDir`
- 手動配置: `manualPos`, `speakerAnchor`
- 画面差し込み: `insert`
- 追加演出: `zoomPunch`, `quoteFreeze`, `stampRain`, `typingFlood`, `sparkleBurst`, `sparklePos`, `irisOut`, `effectSettings`
- 音: `se`
- 音声生成後に自動付与: `start`, `end`, `sentences`, `id`

## 3. カメラまわりの現仕様
### シーン単位
- `video/public/story-scenes.json` の各 scene は `cameraFrame` を持てる
  - `cx`, `cy`, `width`
  - 背景全体に対する既定の 16:9 仮想カメラ枠
- シーン側のカメラ共通値:
  - `zoomAmount`
  - `zoomDuration`
  - `panAmount`
  - `panDuration`
  - `tiltAngle`
  - `tiltDuration`
  - `slowZoomDrift`

### ターン単位
- `cameraEffects`
  - `zoom: "in" | "out"`
  - `pan: "left" | "right"`
  - `tilt: "left" | "right"`
- `cameraEffectSettings`
  - `zoom.amount`, `zoom.duration`
  - `pan.amount`, `pan.duration`
  - `tilt.angle`, `tilt.duration`
- `zoomTarget`
  - ズーム演出が狙う位置（配置タブの `フォーカス位置`）
- `cameraCenter`
  - 仮想カメラ中心そのもの（配置タブの `仮想カメラ中心`）

### 編集UI上の扱い
- `カメラ効果` は `ズーム / パン / 傾き` を併用できる
- 各効果を選ぶと、その下にそのターンだけの上書きスライダーが出る
- 未設定時はシーン共通値を使う
- 旧 `emphasis` の行単位上書きUIは表示しない

## 4. インサート演出の現仕様
`insert.kind` は現在この 7 種:
- `warning`
- `ok`
- `chat`
- `teamchat`
- `mailer`
- `videocall`
- `whiteboard_explain`

## 4.5 背景動画 / 字幕
- `video/public/story-scenes.json` の scene は `bgVideo` を持てる
  - 例: `"bgVideo": "background/loop.mp4"`
  - 指定時は静止画背景ではなく動画背景として再生する
- `bgVideoLoop: true`
  - 指定時だけ背景動画をループ再生する
- `script[]` のターンは `subtitleMode: "subtitle"` を持てる
  - 指定時は吹き出しを出さず、画面下の字幕帯で表示する
  - `sentences` があれば文単位の出し分けはそのまま使う
- `subtitleStyle`
  - 字幕モード時だけ使う見た目の上書き
  - `fontSize` / `boxBorderWidth` は px、`textColor` / `boxBorderColor` は `#rrggbb`
  - `boxBorder: false` で字幕エリア外枠なし

## 5. エディタで今できること
- 左ペインでターン並び替え・追加・削除
- 中央で Remotion Player プレビュー
- 下部タイムラインでセリフ確認、BGM区間編集、Overlay区間編集
- 右ペインで話者・ナレーション・表情・ポーズ・演出・手動SE・インサート編集
- `配置` タブで登場中キャラ/モブの手動配置、`フォーカス位置`、`仮想カメラ中心` を指定
- `台本生成` タブで AI に貼るプロンプトをローカル生成し、返ってきた JSON を取り込み
- `音` タブで SE 自動マップ、シーン別BGM、プレビュー再生

## 6. AI台本生成まわりの現仕様
- 外部API連携はしない。**プロンプト生成だけローカル**で行い、ChatGPT/Gemini/Claude 等への貼り付けは手動
- 取り込み時は ```json fenced block``` や前後の説明文つきでも読める
- 既知の項目はそのまま保存し、未対応の新フィールド / 新シーン / 新表情 / 新インサートだけを検出表示する
- AI向けプロンプトは `subtitleMode`, `manualPos`, `faceMode`, `clearFace`, `sparklePos`, `cameraEffects`, `cameraEffectSettings`, `zoomTarget`, `cameraCenter`, `whiteboard_explain` の詳細オプションまで案内する
- 既知ターン項目には `pose`, `transition`, `voice`, `narrationVoice`, `noLipSync`, `continueBubble`, `disableAutoBubbleSplit`, `manualPos`, `faceMode`, `clearFace`, `se`, `sparklePos`, `cameraEffects`, `cameraEffectSettings`, `zoomTarget`, `cameraCenter` を含む

## 7. 実行フロー
1. `./run-story story`
2. 台本編集 or 台本生成タブでドラフト取り込み
3. `🔊 音声生成` で `start/end/sentences/audio` を確定
4. 必要ならプレビューでカメラ・配置・BGM を調整
5. `./run-story render` で `video/out/<タイトル>.mp4`

## 8. 補足
- `story_editor.py` 起動時に `prep-story.mjs` と `build-story-player.mjs` が自動実行される
  - `./run-story prep` / `player-build` は素材反映やプレビューがおかしい時の手動フォールバック
- BGM は `story.bgm` があれば時間ベース編集を優先し、空ならシーン別BGMへフォールバック
- ナレーション行は `narrationVoice` を使い、吹き出し・立ち絵・カメラ効果を出さない
- `legacy-dialogue/` は凍結中で、`run-story` の主対象ではない
