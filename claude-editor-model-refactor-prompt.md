# Claude Code用プロンプト

`docs/editor-model-refactor-plan.md`を最初から最後まで読み、記載された方針に従って、人間向け編集モデルへの段階移行を実装してください。

## 目的

Geminiは今までどおり章単位で生成します。一方、レビュー画面では生成形式を直接編集せず、以下の人間向けモデルへ変換して扱います。

- 章: 見出し・物語構造・ページめくり境界のみ
- 画像素材: 動画全体のassetライブラリ
- 画像配置: セリフにアンカーされたimageCue
- 大演出: 動画全体のvisualSegment
- 変化点: visualSegment配下のkeyframe

AI生成上の都合をUIへ漏らさず、「どのセリフから何を表示するか」を直接編集できる状態がゴールです。

## 重要制約

- Gemini生成処理は変更しない。
- 動画生成ロジックの結果とRemotion連携を維持する。
- `meta.json`の公開形式は当面維持する。
- フルリライト禁止。後方互換アダプターを使って段階移行する。
- 既存の`script.json`を編集データの正とし、別の編集ファイルを増やして二重管理しない。
- 従量課金APIや外部サービスを追加しない。
- ユーザーの既存未コミットファイルを変更・削除しない。

## 作業順序

一度に全Phaseを実装しないでください。まずPhase 1だけを実装し、テスト・セルフレビュー・コミットまで行って停止してください。

### Phase 1

1. 現在の`script.json`、`review.json`、meta生成、レビューUIの読み書き経路を確認する。
2. `schemaVersion`、安定したturn ID、assets、imageCues、visualSegmentsの正規化関数を追加する。
3. 旧`chapters[].image_cuts`、`turn.cut`、`review.json`、`chapters[].vizList`、`turn.vizSeg`、各フラグ、`vizPoints`から新形式へ変換する。
4. 変換を冪等にし、2回実行してもID・asset・cue・segmentが増えないようにする。
5. UI表示とmeta出力はPhase 1では変えない。既存経路を維持する。
6. 新形式が無い旧データを引き続き読めるようにする。
7. 変換前のバックアップを既存`.backups`方針に沿って作る。ユーザーデータを上書き破壊しない。

## 実装前チェック

`dev-thoroughness-checklist.md`を適用し、次をテストケース名として先に列挙してください。

- 全経路: 追加・移動・削除・分割・保存・再読込・旧形式移行
- ライフサイクル: flag / vizPoints / vizSeg / visualSegment、cut / review / asset / imageCue
- エッジ: null、空、0件、重複、章ローカルID再利用、未取得画像、pos:0
- 永続化: 変換後の保存・再読込、変換2回、meta-only、音声再生成

検証・ID発行・アンカー修復・移行処理は共通関数へ集約し、UI経路ごとに重複実装しないでください。

## Phase 1必須テスト

- 全セリフへ安定turn IDを付与
- 既存turn IDを維持
- セリフ分割時のID規則
- 章ごとに同じcut番号があってもassetが衝突しない
- 章ごとに同じvizSeg IDがあってもvisualSegmentが衝突しない
- 連続する同じ`chapter + cut`を1つのimageCueへ変換
- cut変更位置にimageCueを追加
- reviewのcrop/fit/filter/pad/bg/hideをcueへ移行
- attribution/query/kind/fileをassetへ移行
- vizListとvizPointsをvisualSegment/keyframeへ移行
- 未取得画像、画像なし、演出なし章
- 変換を2回実行して結果不変
- 保存・再読込後も結果不変
- 既存meta関連テストが全件成功

## セルフレビュー

実装後は以下を報告してください。

- 変更ファイル
- 新スキーマの実例
- 旧形式からの変換例
- 後方互換の読み込み経路
- 実行したテストと結果
- 未実装のPhase 2以降
- 既知のリスク

Phase 1だけをコミットし、Phase 2へは進まずユーザーの確認を待ってください。

