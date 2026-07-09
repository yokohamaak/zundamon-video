# run-story系 改善・懸念点 監査メモ（2026-07-10調査）

`./run-story` とその実体（`story_editor.py` / `story_editor.html` / `make_story_audio.py` / 同居エディタ）を
コードリーディングで監査した結果。**実装を依頼されたAI（Sonnet等）はこのファイルを起点に作業すること。**

## 実装AIへの前提（必読）

- **CLAUDE.md のルールが最優先**。特に:
  - `story-01.json` はユーザーの作業中データ。テストで触るなら `cp` で退避→検証→復元（`git stash` 禁止）
  - `_KNOWN_TURN_FIELDS`・APIパス・出力ファイル名・ポート8771 などの互換性を壊さない
  - story_editor.py は標準ライブラリ縛り。新規依存の追加禁止
  - 最小差分で直す。頼まれていない改善を混ぜない
- 各課題は独立に着手できる。**1課題=1コミット**を推奨
- 修正後の共通検証:
  - Python: `python3 test_story_editor.py` / `python3 -c "import ast; ast.parse(open('story_editor.py').read())"`
  - HTML内JS: `<script>` 部を抜き出して `node --check`
  - bash: `bash -n run-story`
- 本メモの行番号は2026-07-10時点。ズレていたら記載のシンボル名で `/usr/bin/grep -n` して特定し直すこと

## 課題一覧（優先度順）

| ID | 区分 | 優先度 | 概要 | 規模 |
|----|------|--------|------|------|
| S1 | セキュリティ | **高** | run-story render の `eval` でタイトル経由コマンドインジェクション | 小 |
| R1 | 堅牢性 | **高** | story-01.json 等の保存が非アトミック（破損リスク） | 小 |
| U1 | UX | **高** | 未保存のままタブを閉じても警告なし（beforeunload なし） | 小 |
| S2 | セキュリティ | 中 | HTTPサーバに Origin/Host 検証なし（CSRF・DNSリバインディング） | 中 |
| S3 | セキュリティ | 中 | review-ts（0.0.0.0）はLAN全体に無認証公開 | 小〜中 |
| R2 | 堅牢性 | 中 | /api/audio・/api/export にサーバ側の同時実行ガードなし | 小 |
| U2 | UX | 中 | Ctrl/Cmd+S の保存ショートカットなし | 小 |
| U3 | UX | 中 | 音声生成前に VOICEVOX 死活チェックがない（失敗が遅くて不親切） | 小 |
| C1 | CLI | 中 | run-story audio も VOICEVOX 事前チェックなし | 小 |
| C2 | CLI | 中 | run-story still のフレーム番号を数値検証していない / prep を通らない | 小 |
| R3 | 堅牢性 | 低 | 2クライアント同時編集（PC+スマホ）で last-write-wins の黙殺上書き | 中 |
| S4 | セキュリティ | 低 | アップロードAPIにサイズ上限なし | 小 |
| U4 | UX | 低 | ターン削除の Undo がない（confirm のみ） | 中〜大 |
| U5 | UX | 低 | 書き出し完了後の mp4 への導線がステータス文字列のみ | 小 |
| C3 | CLI | 低 | メニューが1回実行で終了・node_modules 未導入時の案内なし | 小 |
| C4 | CLI | 低 | `STORY` 変数は audio にしか効かない（誤解を招く） | 極小 |

ROADMAP.md 記載済みの既知課題（複数選択の一括編集拡張、表情/ポーズの実在チェック+話者別絞り込み）は
本メモでは重複させない。着手する場合は ROADMAP.md を参照。

---

## S1【高】run-story render のコマンドインジェクション

- **場所**: [run-story:20](../run-story)（`run() { echo "▶ $*"; ... eval "$*"; }`）と
  [run-story:40-44](../run-story)（`cmd_render` が `${filename}` を eval 対象文字列に埋め込む）
- **現状**: `filename` は `story_editor._safe_export_filename(title)` 由来。この関数
  （[story_editor.py:252](../story_editor.py)）は `\ / : * ? " < > |` と改行タブしか除去せず、
  **`$` とバッククォートが残る**。`run` は `eval` なので、二重引用符内でも `$(...)` は実行される。
- **攻撃経路が現実的**: タイトルは `/api/import-script`（AI出力の貼り付け取り込み、
  [story_editor.py:796-883](../story_editor.py)）で無検証のまま story-01.json に保存される。
  つまり「AIが出力した title に `$(...)` が含まれる」→「ユーザーが `./run-story render` を実行」で任意コマンド実行。
- **修正案（推奨）**: `cmd_render` だけ eval を通さず直接実行する。
  ```bash
  cmd_render() {
    local filename
    filename="$($PY -c "import story_editor as s; print(s._safe_export_filename(s._load_story().get('title','')))")"
    echo "▶ cd video && npm run prep:story && npx remotion render StoryVideo \"out/${filename}\""
    [ "${RUN_DRY:-}" = "1" ] && return 0
    (cd video && npm run prep:story && npx remotion render StoryVideo "out/${filename}")
  }
  ```
  引数として渡せば変数値が再パースされないので安全。RUN_DRY の挙動は維持すること。
- **やらないこと**: `_safe_export_filename` 側で `$` 等を除去する案は、エディタの書き出しファイル名
  （壊してはいけない仕様）も変わるため、やるなら別途ユーザー合意を取る。エディタ側の
  `/api/export`（[story_editor.py:1474-1475](../story_editor.py)）はリスト引数の subprocess で
  シェルを通らないため**現状でも安全**（修正不要）。
- **検証**: story-01.json を退避してから title を `test$(echo INJECTED)` に書き換え、
  `RUN_DRY=1 ./run-story render` の表示と、修正後は実行しても `INJECTED` が出ない・
  `out/test$(echo INJECTED).mp4` という名前で扱われることを確認（フル render は不要、
  RUN_DRY と `bash -n` で十分）。**終わったら必ず story-01.json を復元**。

## R1【高】保存の非アトミック書き込み

- **場所**:
  - [story_editor.py:245-249](../story_editor.py) `_save_story`
  - [make_story_audio.py:241-242](../make_story_audio.py) 音声生成後の timings 書き戻し
  - 同型パターン: `_save_mobs` / `_save_se_map` / `_save_readings` / `_save_kanji_readings`
    （story_editor.py）、`_save_poses` / `_save_scenes` / `_save_expressions`（pose/scene/expression_editor.py）
- **問題**: `open(path, "w")` 直書きなので、書き込み途中のクラッシュ・ディスクフルで
  **ユーザーの作業中データ（story-01.json）が壊れて空/半端なJSONになる**。バックアップ機構もない。
- **修正案**: 共通ヘルパー（例 `_atomic_write_json(path, data)`）を story_editor.py に追加し、
  「同ディレクトリの一時ファイルに書く → `os.replace(tmp, path)`」に置き換える。
  make_story_audio.py にも同じ処理を入れる（import 方向の都合上、関数を重複定義してよい。
  2ファイルで数行のヘルパーなので抽象化しすぎない）。
- **注意**: 出力のJSON形式（`ensure_ascii=False, indent=2`）は変えない。
  一時ファイルは同じディレクトリに作ること（別FSだと os.replace が失敗する）。
- **検証**: `python3 test_story_editor.py` と `python3 test_make_story_audio.py`。
  加えてエディタを起動して保存→ `git diff video/public/story-01.json` が意図通りか目視。

## U1【高】未保存クローズ警告（beforeunload）がない

- **場所**: story_editor.html。`isDirty` フラグと `markDirty()`
  （[story_editor.html:8383-8386](../story_editor.html)）は既にあるが、
  `beforeunload` ハンドラがどこにもない（grep で0件を確認済み）。
- **問題**: 編集後に保存し忘れてタブを閉じる/リロードすると黙って消える。
- **修正案**: 数行で済む。
  ```js
  window.addEventListener("beforeunload", (e) => {
    if (isDirty) { e.preventDefault(); e.returnValue = ""; }
  });
  ```
  init 処理（`btnSave` のリスナー登録付近、[story_editor.html:8739](../story_editor.html)）に追加。
- **注意**: `audioDirty`（音声未生成）では警告しない。あくまで台本未保存（isDirty）のみ。
- **検証**: エディタ起動→セリフを1文字変えて（保存せず）リロード→警告ダイアログが出ること。
  保存後はダイアログなしで閉じられること。**検証で変えたセリフは元に戻す**。

## S2【中】Origin/Host 検証なし（CSRF・DNSリバインディング）

- **場所**: [story_editor.py:936-](../story_editor.py) `StoryEditorHandler`。
  `Access-Control-*` も Origin/Host チェックも一切ない（grep で0件を確認済み）。
- **問題**: エディタ起動中にブラウザで悪意あるサイトを開くと、そのページから
  `http://localhost:8771` へ POST が届く（CORSはレスポンスを読めなくするだけで送信は防がない）。
  `/api/story`（台本上書き）、`/api/import-script`、各アップロード、`/api/export`（レンダ起動）まで叩ける。
  DNSリバインディングなら GET のレスポンス読み取りも可能。
- **修正案**: ハンドラ冒頭で軽量チェックを入れる（標準ライブラリで可能）:
  1. `Origin` ヘッダが存在する場合、そのホスト部が `Host` ヘッダと一致しなければ POST を 403
  2. `Host` ヘッダのホスト部が `localhost` / `127.0.0.1` / `[::1]` /（0.0.0.0起動時は）自マシンの
     Tailscale名・IP以外なら 403（DNSリバインディング対策）。
     Tailscale名は起動時に判定できないため、「`--host 0.0.0.0` 時は Host チェックを緩める or
     `--allow-host` 引数で追加許可」を提案し、実装前にユーザーに方式を確認するのが安全
- **注意**: エディタ本体は同一オリジンの fetch しか使わないので、正しく実装すれば既存動作に影響しない。
  ただし **review-ts（スマホから Tailscale 名でアクセス）を壊さないこと**が受け入れ条件。
- **検証**: `python3 test_story_editor.py`。手動で
  `curl -s -X POST -H "Origin: http://evil.example" -H "Content-Type: application/json" -d '{}' http://localhost:8771/api/story`
  が 403、Origin なし（または一致）なら従来どおり動くこと。

## S3【中】review-ts は LAN 全体に無認証公開

- **場所**: [run-story:33-34](../run-story) `cmd_story_ts`（`--host 0.0.0.0`）、
  [story_editor.py:1712-1716](../story_editor.py)
- **問題**: 「Tailscale経由でスマホから」という想定だが、0.0.0.0 バインドは
  **同一Wi-Fi等の物理LANにも開く**。認証はないので、LAN内の誰でも台本改変・ファイル
  アップロード・レンダ起動（CPU占有）ができる。
- **修正案**（小さい順、S2と併せて検討）:
  1. まず `cmd_help` と `cmd_story_ts` 実行時に「LANにも公開される」旨の警告を出す（1行、即やってよい）
  2. `tailscale ip -4` が取れる環境ではその IP にバインドする
     （`--host "$(tailscale ip -4 2>/dev/null | head -1)"`、失敗時は現行動作にフォールバック）
  3. 簡易トークン認証（起動時に乱数を生成してURLに付与）— 大きめなので提案止まり
- **注意**: 案2以降は既存の使い勝手（URLの形）が変わるため、**実装前にユーザーへ方式確認**。案1は即実装可。
- **検証**: `./run-story review-ts` → 表示メッセージ確認、スマホ or `curl http://<tailscale-ip>:8771/` で従来どおり開けること。

## R2【中】音声生成・書き出しのサーバ側同時実行ガードなし

- **場所**: [story_editor.py:1382-1438](../story_editor.py)（/api/audio）、
  [story_editor.py:1439-1516](../story_editor.py)（/api/export）
- **問題**: ThreadingHTTPServer なので同時 POST で `make_story_audio.py` や remotion render が
  並走できる。クライアントはボタンを disable する（[story_editor.html:8274,8340](../story_editor.html)）が、
  **複数タブ・PC+スマホ併用だと防げない**。並走すると story-01.json / wav への同時書き込みで破損しうる。
- **修正案**: モジュールレベルに `threading.Lock` を1個置き、/api/audio と /api/export の冒頭で
  `acquire(blocking=False)` に失敗したら `__DONE__ err 既に実行中です` を返して終了。finally で release。
- **検証**: `python3 test_story_editor.py`。手動なら curl で /api/audio を2連発し、
  2本目が即座に「既に実行中」を返すこと（VOICEVOX 未起動でも1本目がプロセス起動する間に確認可能）。

## U2【中】Ctrl/Cmd+S 保存ショートカット

- **場所**: story_editor.html。現状キーボードショートカットは実質なし
  （`ctrlKey` の使用はターン複数選択のみ、[story_editor.html:5980](../story_editor.html)）。
- **修正案**: document の keydown で `(e.ctrlKey||e.metaKey) && e.key==="s"` を捕まえ
  `e.preventDefault(); saveStory();`。input/textarea フォーカス中でも保存でよい（ブラウザの保存ダイアログ抑止が主目的）。
- **検証**: エディタで Cmd+S → ステータスが「保存しました」になり、ブラウザの保存ダイアログが出ないこと。

## U3【中】音声生成前の VOICEVOX 死活チェック / C1: run-story audio も同様

- **場所**: エディタ側 `generateAudio`（[story_editor.html:8271-8334](../story_editor.html)）は
  confirm を出すだけで、未起動だと数十秒相当の待ちの後に tail 1行のエラーで失敗する。
  既に `/api/voicevox/speakers`（[story_editor.py:1230-1231](../story_editor.py)、timeout=3）があるので流用できる。
- **修正案（エディタ）**: `generateAudio` の confirm 前に `/api/voicevox/speakers` を fetch し、
  空/エラーなら「VOICEVOX が起動していません（http://localhost:50021）」を即表示して中断。
- **修正案（CLI）**: [run-story:54-57](../run-story) `cmd_audio` の echo を
  `curl -s -m 2 http://localhost:50021/version` チェックに変え、失敗なら中断してメッセージ表示。
  ※ VOICEVOX の接続先は `make_story_audio.py:197` が `VOICEVOX_URL` 環境変数を見るので、
  チェック側も `"${VOICEVOX_URL:-http://localhost:50021}"` を使うこと。
- **検証**: VOICEVOX 停止状態で `./run-story audio` → 即中断すること。
  エディタの音声生成ボタン → 即エラー表示すること。（起動状態での生成テストは不要。ユーザーに委ねる）

## C2【中】run-story still の入力検証と prep

- **場所**: [run-story:47-50](../run-story) `cmd_still`
- **問題**: ① フレーム番号が非数値でも remotion までそのまま渡り、分かりにくいエラーになる。
  ② `render` と違い `prep:story` を通らないため、assets を更新した直後だと古い素材で静止画が出る。
- **修正案**: `[[ "$f" =~ ^[0-9]+$ ]] || { echo "フレーム番号は数字で: $f"; exit 1; }` を追加。
  prep は render と同様に `npm run prep:story &&` を前置してよい（コピーだけなので軽い）。
- **検証**: `RUN_DRY=1 ./run-story still 10` / `./run-story still abc`（エラー中断）/
  `RUN_DRY=1 ./run-story still １０`（全角→10 になること）。

## R3【低】2クライアント同時編集の黙殺上書き

- **場所**: `/api/story` POST（[story_editor.py:1248-1258](../story_editor.py)）と
  `saveStory()`（[story_editor.html:8228](../story_editor.html)）
- **問題**: review-ts の想定使い方（PC とスマホで同じ台本を開く）で、後から保存した側が
  相手の変更を丸ごと消す。検知手段がない。
- **修正案（提案止まり・実装前にユーザー確認推奨）**: GET /api/story のレスポンスに
  ファイル mtime を含め、POST 時に一致しなければ 409 を返してフロントで
  「他の画面で更新されています。リロードしてください」を表示する。
  story-01.json のフィールドを増やさず、API レスポンスのラッパー追加で済む形を検討
  （既存GETの形を変えるとプレイヤー側取り込みに影響がないか要確認）。

## S4【低】アップロードAPIにサイズ上限なし

- **場所**: `/api/mobs/upload-image` 等3系統（[story_editor.py:1308-1348](../story_editor.py)）、
  `_save_base64_image`（[story_editor.py:272-280](../story_editor.py)）。
  そもそも全 POST が `Content-Length` を無制限に `rfile.read()` する。
- **修正案**: do_POST 冒頭で `length > 50MB` 程度なら 413 を返す。ローカル専用なので過剰にしない。
- **備考**: 拡張子は `_safe_image_filename` で画像系に強制され、配信も画像 Content-Type 固定なので
  「画像以外を置かれて実行される」類のリスクは低いことを確認済み。

## U4【低】ターン削除の Undo

- **現状**: 削除は confirm のみ（[story_editor.html:8121](../story_editor.html)）。Undo スタックは存在しない。
- **提案**: フル Undo/Redo は大きいので、まず「直前に削除したターン（複数可）を1段階だけ復元する」
  トースト（『削除しました [元に戻す]』）程度が費用対効果が高い。
  ROADMAP の「選択ターンの一括削除」を実装する際に併せて入れるのが良い。
- **注意**: `selectedIdx` / `selectedIndices` / `playbackFollowIdx` の整合に既知バグの温床がある
  （CLAUDE.md 注意6、memory: story_editor_playback_idx_bug）。復元時は必ず `renderList()` 系の既存フローを通すこと。

## U5【低】書き出し完了後の導線

- **現状**: 完了時に `書き出し 完了: video/out/<タイトル>.mp4` の文字列表示のみ
  （[story_editor.html:8368](../story_editor.html)）。
- **提案**: パスをクリックでクリップボードへコピー、程度に留める。
  サーバ側で `open` コマンドを叩いて Finder を開く案は、リモート（review-ts）から叩かれると
  ホスト側で勝手にウィンドウが開くため**採用しない**。

## C3【低】run-story メニューの小改善

- メニュー（[run-story:103-126](../run-story)）は1コマンド実行で終了する。ループ化は好みの問題なので
  ユーザーに要否を確認してから。
- `video/node_modules` が無い場合の失敗が分かりにくい。dev/render/still の前に
  `[ -d video/node_modules ] || { echo "先に: cd video && npm install"; exit 1; }` を入れる価値はある。

## C4【低】`STORY` 変数の誤解

- [run-story:17](../run-story) の `STORY="story-01"` は **cmd_audio にしか効かない**。
  render・エディタ・/api/audio は story-01 固定（[story_editor.py:34](../story_editor.py)、
  [story_editor.py:1416](../story_editor.py)）。変数を変えれば全部切り替わるように見えるのが罠。
- **修正案**: コメントを「※audio のみ。エディタ/render は story-01 固定」に直すだけでよい。
  マルチ台本対応は大改修なのでやらない。

---

## 調査済みで「対応不要」と判断したこと（再調査・過剰修正の防止）

- **サーバ側の subprocess はすべてリスト引数で shell を通らない**
  （/api/audio, /api/export, /api/pose-export, /api/expression-export, prep/build 起動時実行）。
  シェルインジェクションは S1（run-story の eval）のみ。
- **パストラバーサル対策は実装済み**: `_safe_path`（[story_editor.py:886](../story_editor.py)）と
  `_story_preview_asset_path`（allowlist 方式、[story_editor.py:900](../story_editor.py)）。
  `/img/` と `/preview-assets/` は video/public 外に出られないことを確認した。
- **画像配信の Cache-Control: no-store は意図的**（同名上書き運用のため）。「改善」で消さないこと。
- **/api/export の残留プロセス対策（killpg）や Range 対応（206）は事故対応の結果**。
  コメントに経緯があるので簡略化しないこと。
- **書き出し中・音声生成中の編集ロック**（lockScriptEditing）は既知バグ対策。外さないこと。
- `.env` は .gitignore 済み。コード内に秘密情報の直書きは見当たらない。
- 従量課金要素なし（VOICEVOX ローカル、外部API連携はプロンプト手動貼り付け方式）。

## 推奨着手順

1. S1（インジェクション修正・小） → 2. R1（アトミック書き込み・小） → 3. U1（beforeunload・小）
2. その後 U2 / U3+C1 / C2 / R2 を各個撃破
3. S2 / S3 / R3 は方式にユーザー判断が要るため、**実装前に方式を1度確認**してから
