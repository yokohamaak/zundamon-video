# ストーリー系（会話劇）仕様レビュー：問題点と改善点

作成日: 2026-07-03
対象: story_editor.py / story_editor.html / video/src/StoryVideo.tsx / make_story_audio.py / video/public/story-01.json ほか関連データ
対象外: 掛け合い雑学系（DialogueVideo.tsx / main_story.py）

全指摘はコード実物で裏取り済み（該当 file:line を併記）。

---

## 1. 現状仕様サマリ

| ファイル | 行数 | 役割 |
|---|---|---|
| story_editor.py | 1,222 | エディタバックエンド（stdlib HTTPサーバ・port 8771） |
| story_editor.html | 5,792 | エディタUI（3ペイン単一ファイル） |
| video/src/StoryVideo.tsx | 3,478 | Remotion 描画本体 |
| make_story_audio.py | 215 | VOICEVOX 合成・タイミング書き戻し |
| video/public/story-01.json | 2,399 | ストーリーデータ（唯一の編集対象） |

- データ形式: `{title, script[], overlays[], audio, idleFace, bgm[]}`。turn 必須は `speaker/text/scene`、他は任意演出（transition/pose/emphasis/shake/flashback/telop/insert/enter/exit/se/voice 等）。`start/end/sentences/id` は自動付与。
- ワークフロー: AIプロンプト生成（ローカル）→ 外部AIでJSON作成 → 取り込み → エディタ編集 → 音声生成（VOICEVOX）→ Remotion レンダリング。
- 演出の追加手順は docs/new-effect-checklist.md に規定（プロンプト・既知フィールド・エディタUIの手動同期が前提）。

---

## 2. 問題点

### A. データ破損・作業ロス系（優先度: 高）

**A-1. 保存・取り込みが無条件上書きでバックアップなし**
`POST /api/story` の保存（story_editor.py:137 `_save_story`）と AI 台本取り込み（story_editor.py:551）は story-01.json を即上書きする。取り込みは既存の全ターン・音声タイミングを丸ごと置き換えるが、確認も退避もない。`.backups/` は手動運用のみで保存経路からは一切使われていない。誤取り込み・誤保存からの復旧手段は git 頼み（story-01.json が未コミットのときは消失）。

**A-2. 音声生成時、保存失敗でも処理続行 → 編集内容がサイレント消失**
`generateAudio()`（story_editor.html:4609）は `await saveStory()` の成否を見ずに生成へ進む（saveStory は失敗を返さない設計、story_editor.html:4581）。保存が失敗すると、ディスク上の**旧台本**で音声が生成され、完了後に `/api/story` を再読込して `storyData` を差し替える（story_editor.html:4640-4641）ため、**ブラウザ上の未保存編集が旧データで上書きされて消える**。

**A-3. 音声生成中の保存が書き戻しで消える競合**
make_story_audio.py は開始時に story-01.json を読み（:154）、合成完了後にそのコピーへタイミングを付けて書き戻す（:203）。生成には数十秒〜数分かかるが、その間エディタの編集・保存はブロックされない。生成中に保存した内容は書き戻しで消える。

**A-4. 未保存のままタブを閉じても警告なし**
`beforeunload` ハンドラが存在しない（story_editor.html 全体で0件）。`isDirty` 管理はあるのに離脱警告に使われていない。

### B. 仕様と実装の乖離（サイレントに効かない演出）

**B-1. telop は回想境界でしか表示されない**
描画側の telop は flashback の切り替わり境界（fbBoundaries）に紐づく実装のみ（StoryVideo.tsx:3274-3299）。flashback の無いターンに telop を付けても**何も表示されない**。一方でプロンプトは「画面隅に短時間出る字幕（時代・場面ラベル）」と汎用機能として案内し（story_editor.py:410）、エディタUIも任意ターンで入力できる（story_editor.html:1075）。仕様と実装が食い違っている。

**B-2. 実装済みの新演出7種がエディタ・プロンプト・取り込み検出に未登録**
`impactText / zoomPunch / quoteFreeze / stampRain / typingFlood / sparkleBurst / irisOut` は StoryVideo.tsx で完全実装済み（型: :124-130、描画: :597-848）だが、
- `_KNOWN_TURN_FIELDS`（story_editor.py:482-487）に無い → 取り込みのたび「未対応の新演出」と誤報告
- プロンプトの「使える演出」節に無い → AI が使えない
- エディタUIに編集フィールドが無い → 手JSON編集でしか使えない
new-effect-checklist.md の手順②③④が実施されていない状態。

**B-3. enterMode がプロンプトにあるのに既知フィールドに無い**
プロンプトは `"enterMode": "instant"` を案内し（story_editor.py:420）、描画も実装済み（StoryVideo.tsx:104, :539-548）だが、`_KNOWN_TURN_FIELDS` に `enterMode` が無い。プロンプト通りに書いた AI 出力が毎回「新演出（未対応）」と誤検出される。

**B-4. videocall がプロンプトのインサート一覧に無い**
INSERT_KINDS / `_KNOWN_INSERT_KINDS` / エディタUI / TSX 描画には追加済み（未コミット +891行）だが、プロンプトのインサート節（story_editor.py:427-433）は5種のまま。AI 台本生成で videocall が使われることはない。機能追加が中途半端な状態でコミットもされていない。

**B-5. transition はシーン先頭ターン以外では無視される**
セグメント化（StoryVideo.tsx:400-418）はシーン連続範囲の**先頭ターン**の transition しか採用しない。途中ターンに付けても黙って無視。プロンプトには「先頭行だけに付ける」と書いてある（story_editor.py:468）が、エディタ・検証での強制や警告はない（エディタの `normalizeStoryTransitions` は保存時に整えるが、UI上の説明はない）。

**B-6. 未知の表情は無警告で normal にフォールバック**
StoryVideo.tsx:2797-2798。取り込み時は新表情として検出される（story_editor.py:539-541）が、エディタで手入力した場合やタイポは検知されない。

### C. バリデーション不足

**C-1. 保存時検証が speaker/text/scene の文字列チェックのみ**
`_validate_story`（story_editor.py:123-134）。scene の未登録キーは受理される（描画は「未登録シーン」プレースホルダ表示になるのでレンダリングでは気付ける: StoryVideo.tsx:2477-2494）。start/end の重複・逆転・欠落も未検証。

**C-2. 話者は任意文字列で通るが、音声生成で全体失敗する**
voice_profiles に無い話者が1人でもいると `build_script_turns` が KeyError（make_story_audio.py:135-136）で**全ターンの生成が失敗**する。しかもエディタへの通知は一律「音声生成に失敗（VOICEVOX起動を確認）」（story_editor.py:1011）で、真の原因（どの話者が未定義か）が表示されない。KeyError メッセージ自体は進捗ストリームに乗るが、UIは `[N/total]` 形式以外を逐次表示で流してしまい最終エラーに残らない（story_editor.html:4634-4653）。

**C-3. insert 内フィールドは未検証**
kind ごとの必須フィールド（mailer の subject、chat の user/ai 等）は TSX の型定義のみで、保存・取り込み時のチェックがない。欠けているとレンダリング時に undefined 表示や描画崩れになる。

### D. 保守性

**D-1. 仕様定義の多重管理（同期は手作業・既に3件破綻）**
- 話者: `SPEAKERS`/`SPEAKER_ICONS`（story_editor.py:44-62）⇔ `CHARACTERS`/`MOBS`（StoryVideo.tsx:246-268）⇔ voice_profiles（make_story_audio.py:17-69 + config/voice_profiles.json）の3箇所
- インサート: `INSERT_KINDS`（py:68）+ `_KNOWN_INSERT_KINDS`（py:488)+ `StoryInsert` union（tsx:17-35）+ プロンプト文（py:427-433）の4箇所
- ターン演出: `_KNOWN_TURN_FIELDS`（py:482）+ `StoryTurn` 型（tsx:76-148）+ プロンプト文（py:403-424）+ エディタUIフォームの4箇所

checklist 運用でカバーする建前だが、B-2/B-3/B-4 の通り現時点で既に3系統が同期切れしている。人手同期は破綻済みと判断すべき。

**D-2. story-01.json 固定で複数ストーリーを扱えない**
ハードコード箇所: story_editor.py:25（STORY_JSON）、:40（プレビュー許可ファイル）、:1001（音声生成の引数 "story-01"）、Root.tsx:18、story-player.tsx:137。make_story_audio.py 自体は basename 引数対応済み（:210）なのに、上流が固定。docs/stories/ ディレクトリと `story -> stories/test` symlink の存在から、実運用では手動でファイルを差し替えて回している模様。話数が増えるほど事故リスク（上書き・取り違え）が上がる。

**D-3. story_editor.html が 5,792 行の単一ファイル**
UI・状態管理・API通信・タイムライン・オーバーレイ編集が1ファイル。機能追加のたびに肥大化しており、videocall 追加だけで +205 行。個人開発の初速優先なら許容範囲だが、限界が近い。

**D-4. videocall 機能一式が未コミット（4ファイル +891行）**
描画・エディタUI・検出は動く状態に見えるが、B-4（プロンプト未対応）と E-1（ドキュメント未反映)が残ったまま。中途半端な状態で放置するとdiffが腐る。

### E. ドキュメント乖離

**E-1. docs/run-story-current-spec.md が実態より古い**
「insert.kind は現在この5種」と記載（videocall 未反映）。B-2 の新演出7種も未記載。「現在仕様メモ」を名乗る文書が現在仕様でない。

### F. その他（軽微）

- **F-1. 毎回フルリビルド**: エディタ経由の音声生成は常に `STORY_AUDIO_FORCE_REBUILD=1`（story_editor.py:999）でキャッシュを全削除（make_story_audio.py:168-170）。1行直しただけでも全ターン再合成となり、長編ほど待ち時間が線形に増える。キャッシュ機構は存在するのに実質無効化されている。
- **F-2. audioDirty がメモリのみ**: ブラウザ再読込で「音声生成が必要」フラグが消える（story_editor.html:4671）。タイミング欠落ターンの検出等、データからの復元がない。
- **F-3. `--host 0.0.0.0` 時は認証なしで書き込みAPIが開く**（story_editor.py:1201）。Tailscale 内前提なら許容だが、仕様として明記されていない。

---

## 3. 改善提案（優先度順）

### P1: 作業ロス防止（すぐやる価値あり・各数十行）

1. **保存前バックアップ**: `_save_story` で上書き前に `.backups/story/story-01-<timestamp>.json` へ退避（直近N世代のみ保持）。取り込み（`_import_script_text`）も同経路を通す。→ A-1 解消
2. **saveStory の成否を返し、失敗時は音声生成を中断**: `generateAudio` 冒頭で保存失敗なら即 return。→ A-2 解消
3. **beforeunload 追加**: `isDirty` 時に離脱警告。3行。→ A-4 解消
4. **同期切れ3件の修正**: `_KNOWN_TURN_FIELDS` に `enterMode` と新演出7種を追加、プロンプトに videocall と新演出を追記。checklist 通りの作業のみ。→ B-2/B-3/B-4 解消。あわせて videocall 一式をコミットして D-4 も解消

### P2: サイレント破綻の可視化（中規模）

5. **telop の仕様確定**: (a) 回想と無関係でも表示されるよう描画を拡張する、または (b)「回想専用」とプロンプト・エディタUIに明記する。現状は騙し討ち。→ B-1
6. **保存時の警告付きバリデーション**: 未登録 scene・未知 expression・voice_profiles に無い speaker・insert 必須フィールド欠落を「保存は通すが警告リストで返す」形にする（`_load_scenes_keys` 等の既存ヘルパで判定可能）。エディタは保存結果に警告を表示。→ B-6/C-1/C-2/C-3 を低コストで検知
7. **音声生成の失敗理由を伝える**: 進捗ストリームの最後の非進捗行を保持して `__DONE__ err` 時に表示。話者プロファイル欠如は生成前にチェックして即エラーにする。→ C-2
8. **編集ロック**: 音声生成中は保存ボタンを無効化（生成ボタンは既に無効化済み）。→ A-3

### P3: 構造改善（効果は大きいが急がない）

9. **定義の一元化**: 話者・インサート種・演出フィールドを JSON（例: `video/public/story-spec.json`）に集約し、story_editor.py（プロンプト生成・検出）と StoryVideo.tsx（型は維持しつつランタイム参照）双方が読む。checklist の①以外を自動化できる。→ D-1 の根治
10. **マルチストーリー対応**: `?story=<basename>` で編集対象を切替（STORY_JSON をリクエスト単位で解決、音声生成へ basename を引き渡し、プレビュー許可ファイルを動的化）。make_story_audio.py は対応済みなので改修は editor 側中心。→ D-2
11. **差分再生成**: キャッシュキーに voice params と fx を含め、FORCE_REBUILD をやめてターン単位でキャッシュ再利用。→ F-1
12. **story_editor.html の分割**: 現状維持でも回るが、次の大型機能追加の前に JS をモジュール分割しておくと安全。→ D-3

### ドキュメント

13. **run-story-current-spec.md の更新**: videocall・新演出7種・telop の実際の挙動（P2-5 の決定後）を反映。→ E-1

---

## 4. 検証メモ

- 全指摘は 2026-07-03 時点のワーキングツリー（未コミットの videocall 変更を含む）に対して file:line で確認した。
- 未検証のまま除外した事項: 音声解析用 wav が欠落した場合の `useWindowedAudioData` の挙動（StoryVideo.tsx:2453）、text 空ターンの VOICEVOX 合成挙動（make_story_audio.py:131）。問題の可能性はあるが再現確認していないため本レビューには含めていない。
