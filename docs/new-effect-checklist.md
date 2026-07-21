# 新しい演出を追加したときの更新箇所チェックリスト

台本生成プロンプト（story_editor の「台本生成」タブ）は、現在ツールが対応している
演出を AI に伝える。**新しい演出を実装したら、プロンプトと取り込み検出も更新する**こと。
更新しないと「AIがその演出を提案できない」「取り込み時に新演出扱いで毎回フラグされる」。

判定の早見表:
- **シーン追加** … プロンプト更新は **不要**（`/api/meta`・`_load_scenes_detail` で動的取得）。
- **表情追加** … プロンプト更新は **不要**（`expressions.json` から動的取得）。
- **ターン単位の新演出（新フィールド）追加** … 下記①〜④を更新（必須）。
- **新しいインサート種別（insert.kind）追加** … 下記①〜⑤を更新（必須）。

---

## ① 描画を実装（必須・本体）
- `video/src/StoryVideo.tsx` / `video/src/Avatar.tsx` に演出を実装。
- ターン型 `StoryTurn`（StoryVideo.tsx）に新フィールドを追加。
- インサートなら `StoryInsert` union と `InsertXxx` コンポーネントを追加。
- コード変更後は `./run-story player-build`（エディタ用 Player を再ビルド）。

## ② プロンプトの「使える演出」節に追記（story_editor.py）
- `story_editor.py` の `_build_script_prompt()` 内、
  `"━━━ 使える演出 ..."`（または `"━━━ インサート演出 ..."`）の配列に1行追加。
  例: `'- "zoomPunch": true … 一瞬強く寄って戻る強調',`
- これで AI が新演出を使えるようになる。

## ③ 取り込み検出の既知セットに追加（story_editor.py）
- `_KNOWN_TURN_FIELDS` に新フィールド名を追加（新演出フィールドの場合）。
- `_KNOWN_INSERT_KINDS` に新 kind を追加（新インサートの場合）。
- 追加しないと、取り込み時に毎回「新演出（未対応）」として一覧表示されてしまう。

## ④ 台本エディタの編集UI（任意・あると便利）
- `story_editor.html` の詳細ペインに、その演出のトグル/入力を追加（手編集用）。
- インサートなら `INSERT_KINDS`（story_editor.py）にも追加し、インサートフォームを用意。

## ⑤ インサート種別のみ: 種別リストにも追加（story_editor.py）
- `INSERT_KINDS = [...]` に新 kind を追加（`/api/meta` 経由でエディタのドロップダウンに出す）。

---

## V2表示種別（displayMode の kind）を追加したとき

漫画(comic)の追加で通った実際の更新箇所。新しい kind を足すときは同じ順に:

1. `video/src/stage-v2.ts` … `XxxDisplayV2` 型を作り `DisplayModeV2` union に追加
2. `video/src/StageVideoV2.tsx` … 表示種別の描画分岐チェーンに追加。
   **転換プレート `renderTransitionPreviousPlate` にも静的描画の分岐を足す**（忘れるとwipe/slide転換中だけ通常ステージが映る）
3. `stage_schema.py` … `DISPLAY_MODES` と `_display_mode()` に検証分岐（framedStage の粒度をミラー）
   → `test_stage_schema.py` に正常系・異常系を追加
4. `story_editor.html` … `fV2DisplayMode` の option / `v2XxxControls` フォーム / apply分岐 /
   `renderV2XxxControls` / `v2UpdateDisplayModeFields` / `v2DisplayModeLabel` / ターン一覧バッジ
5. `story_editor.py` … `_build_script_prompt_v2` の表示種別節に1行（AIに使わせるため必須）
6. `./run-story player-build` でエディタ用Playerを再ビルド。**起動中の story_editor.py も再起動**
   （メモリ上の旧 stage_schema が新kindの保存を400で弾く）

漫画の素材運用ルール: 漫画画像（background/ 配下）は**上書きで差し替えない**こと。
同じ画像パスの連続ターンを「同一漫画シーン」とみなす設計のため、上書きすると全利用箇所の絵が一斉に変わる。
差し替えたい時は別名で追加して該当ターンだけ選び直す。

---

## まとめ（最短手順）
新ターン演出 `fooBar` を足すなら:
1. StoryVideo/Avatar に実装 → `StoryTurn` に `fooBar?` 追加 → `player-build`
2. `_build_script_prompt` の演出節に1行
3. `_KNOWN_TURN_FIELDS` に `"fooBar"` 追加
4. （任意）エディタUIにトグル

この4箇所を直せば、プロンプト生成・AI提案・取り込み検出・手編集 が全部そろう。
