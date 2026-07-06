# CLAUDE.md

ずんだもん×四国めたんの**ストーリー会話劇動画**をローカル完結で作るツール群。
台本編集(ブラウザ) → VOICEVOX音声生成 → Remotion描画・書き出し、の一気通貫。

## 基本方針

- 個人開発。開発速度優先だが、既存仕様を壊さないことが最優先
- 過剰な抽象化・将来拡張のための設計はしない
- ユーザーは実装詳細より成果物を重視する
- 回答は日本語・結論から。長文の前置き・過剰な同調・反射的謝罪は不要
- 問題点は率直に指摘する。指摘や疑問にはまず事実を確認してから答える

## 実行の権限

**大規模変更・破壊的変更以外は、事前確認なしで実装・コミットまで進めてよい。**

以下だけは実装前に「変更内容・影響範囲・懸念」を提示して合意を得ること:

- データ削除・スキーマ変更（story-01.json等の既存フィールドの意味変更・削除）
- ファイル大量削除・ディレクトリ構成変更
- 既存JSONフォーマット・CLI引数・出力ファイル名の互換性を壊す変更
- **従量課金・情報漏洩リスクのある実装は選択肢にすら入れない。代替が無ければ「できない」と伝える**

## セキュリティ・課金【絶対条件】

- 従量課金APIは使用禁止。VOICEVOXはローカルエンジンなので無料
- 秘密情報は `.env` 管理・コミット禁止。コードへの直書き・外部送信は禁止
- 反復検証はCIや課金に頼らず、ローカルで回す

## 主要な処理フロー

```
story_editor.py (localhost:8771)
  └ story_editor.html … 台本編集SPA。Remotion Playerでライブプレビュー
  └ scene/expression/pose editor を iframe 同居配信
台本保存 → video/public/story-01.json
音声生成 → make_story_audio.py story-01 → VOICEVOX(localhost:50021)
  → story-01.wav/.mp3 生成 + start/end/sentences を story-01.json に書き戻し
書き出し → video/ (Remotion) → npx remotion render → video/out/<タイトル>.mp4
```

簡易起動: `./run-story`（番号メニュー）

## 重要ファイル

| ファイル | 役割 |
|---|---|
| `story_editor.py` | エディタHTTPサーバ（標準ライブラリのみ）。API・検証・プロンプト生成 |
| `story_editor.html` | エディタ本体（素のJS・7000行超）。UIロジックはすべてここ |
| `video/src/StoryVideo.tsx` | Remotion描画の心臓部。ターン→映像の全ロジック |
| `make_story_audio.py` | VOICEVOX音声生成・実尺書き戻し |
| `src/tts_voicevox.py` | TTS低レベル処理・読み辞書 |
| `video/public/story-01.json` | **ユーザーの編集中の台本。テストで汚染しないこと（下記）** |
| `video/public/story-scenes.json` | シーン定義（背景・アンカー・カメラ） |
| `video/public/mobs.json` | モブキャラ定義（画像+voice） |
| `config/voice_profiles.json` | 話者→VOICEVOX speaker ID |
| `docs/new-effect-checklist.md` | 新演出追加時の更新箇所チェックリスト |
| `docs/agent-handoff-notes.md` | 検証レシピ集・過去の事故・設計の暗黙知（迷ったら読む） |
| `ROADMAP.md` | 未対応の既知課題・保留項目 |
| `legacy-dialogue/` | **凍結。指示がない限り触らない** |

## よく使うコマンド

```bash
# TypeScript型チェック（StoryVideo.tsx変更後は必須・数秒で終わる）
cd video && npx tsc --noEmit -p .

# 特定フレームの静止画レンダ（映像検証。フレーム=秒×30fps）
cd video && npx remotion still src/index.ts StoryVideo /tmp/out.png --frame=N

# Python回帰テスト（VOICEVOX不要・数秒）
python3 test_make_story_audio.py
python3 test_story_editor.py
python3 test_tts_voicevox.py

# 音声生成（VOICEVOX起動が必要・数十秒〜）※勝手に実行しない
python3 make_story_audio.py story-01
```

構文チェック:
- Python: `python3 -c "import ast; ast.parse(open('FILE').read())"`
- story_editor.html内のJS: `<script>`部を抜き出して `node --check`

## 絶対に壊してはいけない仕様

- `story-01.json` のフィールド名・構造（`speaker/text/scene/start/end/sentences/enter/exit/...`）。
  既知フィールドは `story_editor.py` の `_KNOWN_TURN_FIELDS` が正
- 話者名の対応関係: `config/voice_profiles.json` + `mobs.json` の voice が音声生成の全話者。
  `make_story_audio.load_voice_profiles()` が唯一の判定基準
- 出力ファイル名: `story-01.wav/.mp3`、`video/out/<タイトル>.mp4`
- story_editor.py のAPIパス（`/api/story`, `/api/audio`, `/api/import-script` 等）
- エディタのポート既定 8771（`.claude/launch.json` を検証で変えたら必ず 8771 に戻す）
- `mobs.json`・シーン定義のJSON形式（scene_editor / StoryVideo 両方が読む）

## 変更時の注意（壊れやすいポイント）

1. **story_editor.html と StoryVideo.tsx のロジック二重実装**:
   吹き出しサイズ・登場退場判定・座標解決などはTSX側とJS側でミラー実装している。
   片方だけ直すと「プレビューと書き出しで見た目が違う」バグになる。両方直すこと
2. **横展開必須**: キャラ(renderAvatar)とモブ(renderMob)、登場(enter)と退場(exit)、
   文単位とターン単位など、対になる処理は必ず両方確認して直す
3. **新演出追加時**: `docs/new-effect-checklist.md` に従う。
   `_KNOWN_TURN_FIELDS` / AI生成プロンプト文面 / EFFECT_SETTING_SPECS の更新漏れが頻発ポイント
4. **story-01.json はユーザーの作業中データ**:
   テストで書き換える場合は必ず `cp` でスクラッチパッドへ退避→検証→復元。
   `git stash` は使わない（ユーザーの未コミット編集ごと巻き込む事故が過去にあった）
5. **grep はシェル関数が壊れていることがある**: 検索結果が0件でおかしいと思ったら
   `/usr/bin/grep` を直接使う（バイナリ扱いされたら `LC_ALL=C /usr/bin/grep -a`）
6. **`selectedIdx` 等のエディタ状態**: 再生追従(playbackFollowIdx)と編集対象(selectedIdx)は
   別変数。混ぜると「セリフが化ける」既知バグの再発になる

## Sonnetがやりがちな失敗を防ぐルール

- 頼まれていない機能を実装しない。気づいた改善は提案に留めるか ROADMAP.md に記録
- 既存のファイル名・コマンド名・JSONキー・出力先を「改善」と称して変えない
- 大規模リファクタをしない。最小差分で直す
- コメントは「なぜ」だけ書く。「何をしているか」「修正の経緯」は書かない
- 新規依存パッケージは原則追加しない（story_editor.py は標準ライブラリ縛り）
- エラーが原因不明のまま2回続いたら、試行錯誤を止めて報告する
- 検証で使った一時変更（launch.jsonのポート、story-01.jsonの仮データ）は必ず元に戻し、
  `git status` / `git diff` で漏れがないか最後に確認する

## コストを抑える調査ルール

- リポジトリ全体を読み直さない。この表と `/usr/bin/grep` で対象を絞る:
  - UI・エディタの挙動 → `story_editor.html`（該当関数だけ読む）
  - 映像・演出・配置 → `video/src/StoryVideo.tsx`
  - 音声・話者・尺 → `make_story_audio.py` → 必要なら `src/tts_voicevox.py`
  - API・検証・プロンプト → `story_editor.py`
- 大きいファイルは offset/limit で該当関数の周辺だけ読む
- 動画フル書き出し・音声全生成は検証目的で勝手に実行しない（数分〜かかる）

## 報告フォーマット

作業完了時は以下を簡潔に:

1. **結論**（直った/できた/できなかった）
2. 変更ファイルと要点（1行ずつ）
3. 実行した検証と結果 / 実行しなかった検証とその理由
4. コミットした場合はハッシュ。ユーザーが次にやるべきことがあれば明記

## コミット

- ステージは変更ファイルを明示指定（`git add -A` 禁止）
- ユーザーの編集中データ（story-01.json）は指示がない限りコミットに含めない
- メッセージは日本語で内容を端的に。`Co-Authored-By: Claude <noreply@anthropic.com>` を付ける

## Python環境

- Docker外では `.venv`。グローバルへのpip install禁止
- ただし現行ツールは標準ライブラリのみで動く（テストもpytest不要・直接実行）

## その他の方針

- UI追加時はフラットデザイン基調のミニマルでモダンなUI（既存エディタのCSS変数・部品を流用）
- 技術選定は要件次第で柔軟に。ただし新技術導入時は理由を簡潔に説明
- Playwright等のブラウザ自動化を書く場合は、ロード待ち・リトライ・エラーハンドリングを必ず入れる
