# story_editor スマホ縦UI 改善提案（2026-07-10 デザインレビュー）

`./run-story review-ts`（Tailscale経由スマホアクセス）の縦持ちUIの改善案。
コードリーディングで裏取り済みの問題のみ記載。行番号は2026-07-10時点、ズレたらシンボル名でgrepし直すこと。

## 設計方針（先に合意しておくこと）

スマホの役割は **「レビュー + 軽い直し」** に最適化する:
プレビュー視聴 / セリフ確認 / テキスト・表情・ポーズの微修正 / 音声再生成 が主。
タイムラインのBGM区間編集や配置ドラッグのような精密操作はPC主体とし、
スマホでは「できるが主役ではない」扱いにする。この前提で下記の優先度を付けている。

既存のモバイル実装（900px以下のmedia query、セリフ一覧/詳細編集の2パネル切替、
リスト行・フォームの拡大）は良くできているので活かす。壊さないこと。

## 課題一覧（優先度順）

| ID | 優先度 | 概要 | 規模 |
|----|--------|------|------|
| M1 | **高** | ヘッダーが非レスポンシブで保存/音声生成等が画面外に切れる | 中 |
| M2 | **高** | `height:100vh` でiOS Safariの下端が隠れる | 小 |
| M3 | **高** | ドラッグ操作が mousedown のみでタッチ不能（9箇所） | 中 |
| M4 | 中 | ターンタップ後に手動で「詳細編集」タブを押す必要（毎回2タップ） | 小 |
| M5 | 中 | 複数選択が Shift/Ctrl+クリック前提でタッチでは不可能 | 中 |
| M6 | 中 | タイムラインが縦画面で常時 min120px を専有 | 中 |
| M7 | 中 | ステータス表示がヘッダー右端でスマホでは見えない | 小 |
| M8 | 低 | プレビュー/タイムラインの小ボタンがタップターゲット44px未満 | 小 |
| M9 | 低 | ホームインジケータ回避（safe-area-inset）未対応 | 小 |

---

## M1【高】ヘッダーのモバイル対応（最重要）

- **現状**: `.header`（[story_editor.html:27-31](../story_editor.html)）は `flex-wrap` なし・
  モバイル用CSSなし。中身は h1 + タブ + タイトルinput(min-width:200px) + 聞き役select +
  保存/音声生成/キャッシュ無視/書き出し + status(min-width:100px)。
  390px幅では収まらず、`body{overflow:hidden}` なので**はみ出した保存・音声生成・書き出し
  ボタンとステータスが画面外に切れて操作不能**。
- **提案**: `@media (max-width:900px) and (orientation:portrait)` で:
  1. ヘッダー1段目 = タイトルinput（flex:1）+ ≡メニュー のみ。h1 は非表示、
     聞き役select・キャッシュ無視チェックは ≡メニューのドロップダウンへ移す
  2. **保存 / 音声生成 / 書き出し は画面下部の固定アクションバーへ**（親指到達圏。
     `.main` グリッドの最下段に `grid-area` を1行足す方式なら position:fixed 不要で
     100vh問題とも干渉しない）。ボタン高さ 48px 以上
  3. 既存の `needs-audio`（黄色点滅）等のクラスはボタンを移設してもIDを変えずに
     そのまま効くようにする（`btnSave`/`btnAudio`/`btnExportVideo` のIDは変更禁止。
     JSから `getElementById` で参照されているため、**DOM移動はCSSのorder/grid配置で行い、
     要素の複製・ID変更はしない**）
- **注意**: PC表示（>900px）には一切影響を出さないこと。

## M2【高】100vh → 100dvh

- **現状**: `body { height: 100vh; overflow: hidden; }`（[story_editor.html:24](../story_editor.html)）。
  iOS Safari はアドレスバー表示中 100vh がビューポートより大きく、**最下段（タイムライン/
  アクションバー）がバーの裏に隠れる**。
- **提案**: `height: 100vh;` の直後に `height: 100dvh;` を追加（非対応ブラウザは前行に
  フォールバック）。2行の変更で済む。

## M3【高】ドラッグ操作の Pointer Events 化

- **現状**: `mousedown` が9箇所（`/usr/bin/grep -n "mousedown" story_editor.html` で特定）。
  タイムラインのBGM/Overlay区間ドラッグ、プレビュー上のOverlay配置・手動配置ドラッグ等が
  すべてマウス専用。**スマホではこれらが一切操作できない**。touch/pointer系ハンドラは0件。
- **提案**: `mousedown/mousemove/mouseup` → `pointerdown/pointermove/pointerup` へ置換し、
  ドラッグ対象要素に CSS `touch-action: none;`（ドラッグ開始要素のみ。スクロール領域には
  付けない）と `setPointerCapture` を追加する。マウス動作は同一APIでそのまま維持される。
- **注意**: 9箇所を一括置換せず1箇所ずつ動作確認（PCマウスでの回帰が最優先）。
  `e.clientX/Y` は PointerEvent にもあるので座標計算ロジックは変更不要のはず。
- **検証**: PCで既存ドラッグ全種（BGM区間・Overlay移動/リサイズ・配置タブのドラッグ）が
  従来どおり動くこと。スマホ実機（またはDevToolsのタッチエミュレーション）で同操作ができること。

## M4【中】ターンタップで詳細編集へ自動遷移

- **現状**: `setMobilePanel`（[story_editor.html:8553](../story_editor.html)）はモバイル用タブ
  2つのクリックからしか呼ばれない。リストでターンを選んでも一覧のまま → 毎回
  「ターンをタップ→詳細編集タブをタップ」の2アクション。
- **提案**: モバイル表示（`window.matchMedia("(max-width:900px)")`）でターン行タップ時に
  `setMobilePanel("detail")` を呼ぶ。ただし**複数選択操作中（M5の選択モード中）は遷移しない**。
  「セリフ一覧」タブで戻る動線は既存のまま。
- **注意**: `selectTurn` は再生追従（`playbackFollowIdx`）からも呼ばれる。自動遷移は
  **行タップのイベントハンドラ側にだけ**入れ、`selectTurn` 本体には入れないこと
  （再生中に勝手に詳細画面へ飛ぶ事故を防ぐ。CLAUDE.md 注意6の既知バグ領域）。

## M5【中】タッチでの複数選択（選択モード）

- **現状**: 複数選択は `e.shiftKey` / `e.ctrlKey||e.metaKey`（[story_editor.html:5973-5993](../story_editor.html)）
  のみ。タッチに修飾キーはないため、シーン一括変更などの一括編集機能がスマホで使えない。
- **提案**: モバイル標準パターンの「選択モード」を追加:
  1. ターン行の**長押し（pointerdown 500ms、移動閾値10px以内）**で選択モードON、
     その行を選択に追加
  2. 選択モード中は各行タップがトグル動作（詳細遷移=M4は無効化）
  3. 既存の `bulkSceneBar` を表示し「選択解除」で選択モード終了
  4. ツールバーに小さな「選択」ボタンを置き、長押しに気づかない人の入口にする
- **注意**: 既存の `selectedIndices` / `selectionAnchorIdx` / `selectedIdx` の状態管理に
  そのまま乗せる（新しい状態変数は選択モードフラグ1つに留める）。
  `bulkSceneBar` のボタン6個はモバイルでは横スクロール（`overflow-x:auto; flex-wrap:nowrap`）にする。

## M6【中】タイムラインの折りたたみ（縦画面）

- **現状**: 縦画面グリッド（[story_editor.html:825-834](../story_editor.html)）で
  タイムラインが常時 `minmax(120px, .65fr)` を専有。390×740px級の画面では作業領域を
  圧迫する割に、幅390pxでの区間編集はM3を直しても精密操作として厳しい。
- **提案**: 縦画面ではデフォルト折りたたみ:
  1. 折りたたみ時は再生シークバー（現在位置スライダー＋時間表示）だけの1行（44px）にする
  2. 展開ハンドル（シェブロン）タップで従来のタイムライン全体を表示
  3. 状態は `localStorage` に保持（既存の `GEN_SETTINGS_KEY` と同様の方式で別キー）
- **注意**: 折りたたみはCSSクラス切替（`display:none` + grid-template-rows変更）で行い、
  タイムライン描画ロジック本体には触らない。横画面・PCは現状維持。

## M7【中】ステータス表示のトースト化（モバイル）

- **現状**: 保存結果・音声生成進捗・エラーは `#statusMsg`（ヘッダー右端、
  [story_editor.html:1059](../story_editor.html)）に出る。M1の通りスマホでは画面外になりがちで、
  **保存できたのか・生成が進んでいるのかが見えない**。
- **提案**: `setStatus()` を変更せず、モバイル表示のときだけ同内容を画面下部の
  トースト（アクションバー直上、`ok`は2秒で消える・`busy`は出続ける・`err`はタップで閉じる）
  にミラー表示する。実装は `setStatus` 内で `matchMedia` を見てトーストDOMを更新する形が最小。
- **注意**: `setStatus` の呼び出しシグネチャ・既存クラス名（ok/err/busy/dirty）は変えない。

## M8【低】タップターゲットの44px化

- **現状**: 縦画面media queryはリスト行・フォームは拡大済みだが、
  プレビューツールバー（zoom select、＋画像Overlay、＋字幕）と timeline-header の
  ボタン群は `font-size:12px; padding:2px 8px` のまま（[story_editor.html:1092-1135](../story_editor.html)
  のインラインstyle）。
- **提案**: 縦画面media query内で `.preview-toolbar .btn, .preview-toolbar select,
  .timeline-header .btn, .timeline-header select` に `min-height:44px; font-size:15px;` を当てる。
  インラインstyleに勝つ必要がある箇所は `!important` 許容（既存media queryも同様の手を使っている）。

## M9【低】safe-area-inset 対応

- **提案**: `<meta name="viewport">` に `viewport-fit=cover` を追加し、
  下部アクションバー（M1）に `padding-bottom: env(safe-area-inset-bottom);` を付ける。
  iPhoneのホームインジケータとボタンの重なりを防ぐ。M1とセットで実装。

---

## 実装AIへの注意（共通）

- **PC表示のレイアウト・挙動を変えないことが最優先の受け入れ条件**。全変更を
  `@media (max-width:900px)`（必要なら `and (orientation:portrait)`）内に閉じる。
  JSはモバイル判定（matchMedia）でガードする
- `story_editor.html` 内のJS検証: `<script>` 部を抜き出して `node --check`
- ID変更・DOM複製は禁止（`btnSave` 等はJSが `getElementById` で参照）
- `selectedIdx` / `playbackFollowIdx` / `selectedIndices` の扱いは既知バグの温床
  （CLAUDE.md 注意6、docs/agent-handoff-notes.md 参照）。M4/M5では既存フローを経由すること
- 動作確認はDevToolsのデバイスエミュレーション（390×844 縦）でよいが、
  M2/M9はiOS実機（Tailscale経由）でないと確認できない旨をユーザーに報告すること
- 推奨着手順: M2（2行）→ M1+M9 → M7 → M4 → M3 → M5 → M6 → M8。
  M1とM6はレイアウト変更が重なるため同時に触らない
