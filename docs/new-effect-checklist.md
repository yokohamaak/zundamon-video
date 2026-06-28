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

## まとめ（最短手順）
新ターン演出 `fooBar` を足すなら:
1. StoryVideo/Avatar に実装 → `StoryTurn` に `fooBar?` 追加 → `player-build`
2. `_build_script_prompt` の演出節に1行
3. `_KNOWN_TURN_FIELDS` に `"fooBar"` 追加
4. （任意）エディタUIにトグル

この4箇所を直せば、プロンプト生成・AI提案・取り込み検出・手編集 が全部そろう。
