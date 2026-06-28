# BGM / SE 設計（ストーリーツール）

方針確定（2026-06-28）:
- BGM = シーン連動（既定）＋タイムラインで自由区間上書き（option 3）。
- SE = 演出/表情/インサート/場面転換に自動連動（既定マッピング）＋ターン単位の手動ワンショット。
- 音源は **ローカルのフリー素材のみ**（従量課金/外部取得なし）。`video/assets/bgm/`・`video/assets/se/` に置き、prep-story が public へコピー。
- 既存の `assets/se/*`（surprise/flash/intro/outro）は既存ツール由来の仮素材。SEライブラリはユーザーが差し替える前提。マッピングは「イベント→ファイル名」参照で、ファイルが無ければ無音スキップ。
- voice(story-01.mp3) が尺の基準。BGM/SE はその上に Remotion `<Audio>` を重ねるだけ（自動ミックス）。

## データモデル

### BGM
- `story-scenes.json` の各 scene に任意:
  - `bgm?: string`（例 "bgm/daily.mp3"）/ `bgmVolume?: number`（既定 0.25）
  - 未設定のシーンは「無音」（連続させたい場合は同じ bgm を各シーンに設定 or 下記override）。
- `story-01.json` トップレベルに任意:
  - `bgm?: Array<{ from: number, to: number, file: string, volume?: number, fadeIn?: number, fadeOut?: number }>`
    - from/to = ターンindex(0始まり, both inclusive)。**override がシーン既定より優先**。
- 描画: 全ターンを走査し、各ターンの有効BGM = (覆っている override 区間) ?? (そのターンの scene.bgm)。
  連続して同一ファイルの区間をまとめ、`<Sequence from..dur><Audio src loop volume={envelope}/></Sequence>` で再生。
  区間境界は fadeIn/fadeOut（既定 0.6s）。隣接区間が別曲ならクロスフェード（重ねて逆フェード）。
  既定音量 0.25。

### SE
- `video/public/se-map.json`（自動連動マッピング・編集可）:
  ```
  {
    "expression": { "surprise": {"file":"se/surprise.mp3","volume":0.8,"enabled":true},
                    "panic":    {"file":"se/...","volume":0.8,"enabled":false} },
    "effect":     { "shake": {...}, "flash": {...}, "flashback": {...}, "emphasis": {...} },
    "insert":     { "warning": {...}, "ok": {...}, "chat": {...}, "teamchat": {...}, "mailer": {...} },
    "transition": { "fade-black": {...} }
  }
  ```
  - enabled=false の種類は鳴らさない（鳴りすぎ防止。初期は surprise/flash/warning など最小限ON）。
- 手動ワンショット: `story-01.json` の turn に任意 `se?: Array<{ file: string, at?: number, volume?: number }>`
  - at = ターン開始からの秒オフセット（既定0）。
- 描画: 各ターンについて SE イベントを収集:
  - 自動: turn.expression / 各演出フラグ(shake/flashback/emphasis/effect) / turn.insert.kind / 場面転換 が se-map で enabled なら、該当時刻にワンショット。
  - 手動: turn.se[] を turn.start + at に。
  - 各 SE = `<Sequence from={round((turn.start+at)*fps)}><Audio src volume/></Sequence>`（loopなし）。
  - ファイル未配置・マップ無しは無音スキップ（描画は壊さない）。

## 音量バランス（既定）
- voice 1.0 / BGM 0.25 / SE 0.6〜0.8。後で調整可能に。

## 段階
- Phase 1（描画側）: scene.bgm + override + se-map + 手動se を読み、Audio層を重ねる。verifyは
  esbuild/tsc＋（可能なら）短尺renderで音声ストリーム確認。実聴はMacで。
- Phase 2（エディタUI）:
  - 下タイムラインに BGM レーン（区間ドラッグ/曲割当/フェード）。SE は小マーカー表示（編集は手動seをターン詳細で）。
  - se-map 編集（イベント→ファイル, ON/OFF, 音量）。
  - 既存の preview-assets 配信(bgm/se許可済) と story-player の props 受け渡しを拡張。

## 注意
- prep-story は既に bgm/se を public へコピーする想定か要確認（無ければ追加）。
- story-player.tsx / Root.tsx に se-map・bgm の props 追加。
- Python(story_editor) は全角クォート禁止。
