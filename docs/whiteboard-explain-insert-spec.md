# whiteboard_explain インサート 現行仕様（ChatGPT等での改修用まとめ）

run-story（このリポジトリ）に実装済みの「ホワイトボード解説」インサート演出の、現在の仕様と関連ファイル一覧。
外部LLMに改修を依頼する時の前提資料として使うためのドキュメント。

---

## 1. 何をする機能か

台本の1ターンに `insert.kind: "whiteboard_explain"` を付けると、そのターンの間、画面いっぱいにホワイトボードのイラストが表示され、
タイトル・テーマ・3項目（見出し＋箇条書き＋アイコン）・結論、そして解説キャラ（既定：めたん）の全身立ち絵が表示される。

- 話者・セリフ(`turn.speaker`/`turn.text`)は**通常のターンと同じ**に扱われる（TTSも通常通り生成）。
- ただし吹き出しは表示されない（`chat`/`teamchat` インサートと同じ抑制方式）。
- 話者がこのインサートの `character` と一致する間だけ、立ち絵が実音声でリップシンクする。
- 3項目のうちどれを見せるかはターンごとに個別指定できる（複数ターンに分けて1項目ずつ喋りながら見せる用途）。

---

## 2. 関連ファイル一覧

### 2.1 描画部品本体（Remotion/Reactコンポーネント。プロジェクト固有ロジックを知らない独立部品として作られた）

```
video/src/inserts/whiteboardExplain/
  index.ts                      … re-export
  whiteboardExplainTypes.ts     … 型定義（config形状・props形状）
  whiteboardExplainDefaults.ts  … 既定値・文字数制限・normalize関数
  whiteboardExplainLayout.ts    … 1920x1080基準のレイアウト矩形定義・アニメのフレーム範囲計算
  whiteboardExplainValidation.ts… 文字数制限チェック・fitText（省略記号付与）
  whiteboardExplainAssets.ts    … staticFile解決・キャラ画像/アイコン画像パス解決
  WhiteboardDoodleIcon.tsx      … アイコン未指定時のSVG簡易アイコン（9種）
  WhiteboardExplainInsert.tsx   … 本体コンポーネント（約420行）
```

このディレクトリは元々 `docs/reference/whiteboard-explain-insert/` に置かれていた「独立ZIP納品物」をそのままコピーしたもの。
`docs/reference/whiteboard-explain-insert/docs/WHITEBOARD_EXPLAIN_INSERT_組み込み手順.md` に元の設計意図・使用できるicon一覧などの説明があるが、
**プロジェクト側で挙動を変えた点がいくつかある（後述）ので、そちらは一次情報として鵜呑みにしないこと**。

### 2.2 プロジェクト側の統合（ここが「run-storyの実際の仕様」）

```
video/src/StoryVideo.tsx     … Remotion描画の心臓部。ここでwhiteboard_explainをinsert種別として組み込み
video/src/Avatar.tsx         … 立ち絵の表情/ポーズ描画（whiteboard専用ではないが、立ち絵をここ経由で描画）
video/src/fonts.ts           … Yusei Magic等のWOFF2フォントを明示ロード
video/src/story-player.tsx   … エディタ埋め込みPlayerのエントリ（fonts.tsのimportが必要）
```

### 2.3 エディタ（台本編集UI）側

```
story_editor.py    … AI台本生成プロンプトへの説明文、INSERT_KINDS/_KNOWN_INSERT_KINDSへの登録
story_editor.html  … インサートタブの入力フォーム（renderInsertFields/collectInsert/buildDefaultInsert）
```

### 2.4 フォント素材

```
video/assets/fonts/yusei-magic-400.woff2   … Google Fonts "Yusei Magic" (日本語+ラテン統合, OFLライセンス, v17)
video/public/fonts/yusei-magic-400.woff2   … 上記をprep-story.mjsが同期したもの（gitignore対象・自動生成）
```

---

## 3. データ形式（story-01.json 内の該当ターン）

`insert` オブジェクトはターンの他フィールド（`speaker`/`text`/`scene`/`expression`/`pose`等）と併存する。
`config`のような入れ子ラッパーは使わず、内容をすべて `insert` 直下にフラットに置く（他のinsert種別と同じ流儀）。

```jsonc
{
  "speaker": "metan",
  "text": "見えない仕事って、実は評価されにくいのだ。",   // 通常のセリフ。TTS対象。吹き出しだけ出ない
  "scene": "office",
  "insert": {
    "kind": "whiteboard_explain",
    "title": "めたんの解説コーナー",           // 18文字以内推奨
    "theme": "見えない仕事が評価されにくい理由", // 30文字以内推奨
    "sections": [
      { "heading": "やったことが残らない", "bullets": ["突発対応", "調整", "共有"], "icon": "confused" },
      { "heading": "数字で見えにくい", "bullets": ["助かったで終わる", "成果に変換されない"], "icon": "scribble" },
      { "heading": "可視化すると伝わる", "bullets": ["メモ化", "一覧化", "上司に共有"], "icon": "checklist" }
    ],
    // heading:14文字以内推奨 / bullets:各16文字以内推奨・最大3件 / sectionsは常に3件固定
    "conclusion": "便利屋で終わらず、成果として見せるのが大事！", // 40文字以内推奨
    "visibleSections": [true, false, false],   // ★ このターンで見せるsectionのon/off（省略時は3件とも表示）
    "character": {
      "pose": "point",        // 通常ターンのturn.poseと同じ語彙（idle/cheer/recoil/lean/droop/flustered/proud/
                               // step_in/step_back/listening/sneak/wobble/point）。省略可（省略時="自動"=idle相当）
      "expression": "happy"   // expressions.jsonのキー（既定5種: normal/happy/surprise/trouble/panic、
                               // カスタム追加分も可）。省略可（省略時="自動"=normal相当）
    },
    "animation": { "mode": "step" }  // step(推奨・1項目ずつフェード) / all(最初から全部) / none(アニメ無し)
  }
}
```

### 補足フィールド（`WhiteboardExplainInsertConfig` 由来、run-storyでは通常未使用）

- `character.name`: 既定 `"metan"`。`CHARACTERS`（`zundamon`/`metan`）に存在する名前ならそちらを使う（zundamonを解説役にすることも可能）。
- `character.image`: 明示画像パス指定用。**run-storyでは使わない**（後述4.3の通り、立ち絵は必ずAvatarコンポーネント経由で描画されるため、この値は無視される）。
- `style.*`: フォント・配色。現状run-story側では未編集（既定値のまま）。カスタムしたければ`insert.style`にオーバーライドを足せる。
- `assets.backgroundImage` / `assets.whiteboardImage`: 背景・ホワイトボード自体の画像を差し替えたい場合。
  `video/assets/background/` に置いて `video/public/background/xxx.png` として同期させ、`"background/xxx.png"`（先頭スラッシュ無し）で参照する。
- `assets.iconImages`: sectionのicon（9種のうちどれか）をSVG簡易アイコンではなく画像に差し替えたい場合のマップ。

---

## 4. run-story側で「元の独立部品」から変えた重要な点

ChatGPT等が `docs/reference/whiteboard-explain-insert/docs/WHITEBOARD_EXPLAIN_INSERT_組み込み手順.md` を読むと、
以下の点で実装と食い違って見えるはずなので、**このセクションを優先**すること。

### 4.1 立ち絵は独自のpose/expression enumを使わない

元の部品は `character.pose: "explain"|"pointing"|"confident"|"thinking"` / `character.expression: "normal"|"smile"|"serious"|"surprised"|"troubled"` という専用の4種/5種のenumで、
`public/characters/metan/{pose}_{expression}.png` という単一画像を探す設計だった。

**run-storyではこれを使っていない。** 代わりに、通常のセリフターンと全く同じ「表情システム(`expressions.json`)」「ポーズシステム(`poses.json`)」をそのまま流用し、
`video/src/Avatar.tsx` の `<Avatar>` コンポーネント（パーツ合成式の立ち絵）で描画する。そのため:

- `character.pose`/`character.expression` の値は、**通常ターンの `turn.pose`/`turn.expression` と同じ語彙**を使う。
- 素材は `public/avatars/<char>/...` のパーツ画像（既存の立ち絵システムと共通）。`public/characters/...` は参照しない。
- `character.image` は無視される。

実装箇所: `video/src/StoryVideo.tsx` の `renderWhiteboardCharacterSlot()` 関数（`whiteboard_explain`専用）。
`WhiteboardExplainInsert.tsx` 側は `characterSlot` という追加propを受け取り、渡されていればそちらを最優先で描画する
（元のimg/プレースホルダー分岐より前で分岐。`whiteboardExplainTypes.ts` の `WhiteboardExplainInsertProps.characterSlot` として型追加済み）。

### 4.2 吹き出し無し・リップシンクあり

`whiteboard_explain` のターンは「chat/teamchatと同じ扱い」で吹き出しを抑制する（`StoryVideo.tsx` の `isInsertLineKind()` に `"whiteboard_explain"` を追加）。
ナレーション化(`narrationVoice`)は**使わない**（ナレーション扱いにするとリップシンクの元になる話者判定自体が無効化されてしまうため）。

`renderWhiteboardCharacterSlot()` は `insert.character`（既定metan）が `turn.speaker` と一致する間だけ
`isSpeaker=true` とし、実音声の波形RMS(`speakerAmp`)でリップシンクする。一致しなければ静止（idle呼吸のみ）。

### 4.3 ターン単位でのセクション表示切り替え（`visibleSections`）

元の部品にはない、run-story側の追加仕様。`WhiteboardExplainInsertProps.visibleSections?: [boolean, boolean, boolean]` を追加し、
`WhiteboardExplainInsert.tsx` の該当sectionラッパーdivで `opacity: sectionHidden ? 0 : visibility.opacity` として上書きする実装。
矢印(`<Arrow>`)は同じラッパーdivの子なので、自動的に一緒に非表示になる（追加の分岐不要）。

これにより、1ターンで全部見せる代わりに「①のターン→②のターン→③のターン」のように複数ターンへ分割し、各ターンで喋りながら1項目ずつ見せる使い方ができる。

**現状の制約**: `title`/`theme`/`sections`の中身/`conclusion`/`character`/`animation` は、`videocall`インサートのような
「前のターンから差分継承する」仕組みを持たない。複数ターンに分けて使う場合、**同じ内容を毎ターン repeat して書く必要がある**
（`visibleSections`だけ変える）。継承の仕組みは未実装（必要なら追加要望として検討）。

### 4.4 durationInFrames の決め方

元の部品は `durationInFrames` propが無ければComposition全体の尺を使う設計だが、run-storyでは
「このインサートが画面に出ている実効時間」を渡している（`StoryVideo.tsx` 内、`InsertOverlay`呼び出し箇所）:

```ts
durationInFrames={Math.round((dispEnd - active.start) * fps)}
```

`dispEnd` は次ターンの開始時刻（無ければ自分の`end`）。ターンの尺自体は通常のセリフ同様、
TTS生成時間（`make_story_audio.py`）で決まる（`whiteboard_explain`専用の尺指定フィールドは無い）。

### 4.5 背景の装飾矩形を削除済み

元の部品には、背景画像未指定時のフォールバック背景に謎の茶色い装飾矩形があったが、見た目上ノイズだったため削除済み
（`WhiteboardExplainInsert.tsx` 内、`assets.backgroundImage`未指定時の分岐）。

### 4.6 画像読み込み失敗時のフォールバック

元の部品は `<Img>` にonErrorが無く、素材未配置だとRemotionのレンダリングごとクラッシュした
（Remotionの`<Img>`はロード失敗時に例外を投げる仕様のため）。run-story側で
`characterImage`/`bgImage`/`boardImage`/各sectionの`iconImage`すべてに `onError` ハンドラを追加し、
失敗時はプレースホルダー（またはSVG簡易アイコン／ベクター背景）にフォールバックするよう修正済み。

---

## 5. フォント（Yusei Magic）

`whiteboardExplainDefaults.ts` の既定 `style.fontFamily` は `'Yusei Magic, "Yu Gothic", "Hiragino Sans", sans-serif'`。
Google Fontsの "Yusei Magic"（日本語+ラテン統合woff2、OFLライセンス）を `video/assets/fonts/yusei-magic-400.woff2` として同梱し、
`video/src/fonts.ts` で他の同梱フォント（Noto Sans JP）と同じ方式（`FontFace` API + `delayRender`/`continueRender`）でロードする。
外部CDNへの実行時アクセスは発生しない（ローカル完結）。

**ハマりどころ**: `video/src/story-player.tsx`（エディタPlayerのエントリ）は `import "./fonts"` の副作用でフォントを読むが、
このモジュール評価は `staticFile()` を即座に呼ぶため、Remotionの `staticFile` の向き先を `/preview-assets/` に切り替える
`window.remotion_staticBase` の設定（`story_editor.html`側で行っている）より**先に実行されてしまう**タイミング問題があった。
現在は `story_editor.html` の `initPlayer()` 内、`story-player.js` のscriptタグを追加する直前で
`window.remotion_staticBase = "/preview-assets"` を設定することで解消済み（`story_editor.html` 内 `initPlayer()` 関数）。
今後フォントやその他の同梱アセットを増やす際、同じ罠に注意。

---

## 6. エディタUI（story_editor.html）の入力フォーム

`story_editor.html` の「インサート」タブ、`kind: "whiteboard_explain"` を選ぶと出るフォーム
（`renderInsertFields()` 内、`kind === "whiteboard_explain"` 分岐）:

| フィールド | UI要素 | DOM id |
|---|---|---|
| title | テキスト入力 | `fWTitle` |
| theme | テキスト入力 | `fWTheme` |
| sections[i].heading (i=0..2) | テキスト入力 | `fWSecHeading{i}` |
| sections[i].bullets[0..2] | テキスト入力×3 | `fWSecBullet{i}_{0,1,2}` |
| sections[i].icon | セレクト（9種） | `fWSecIcon{i}` |
| visibleSections[i] | チェックボックス「このターンで表示する」 | `fWSecVisible{i}` |
| conclusion | テキストエリア | `fWConclusion` |
| character.pose | セレクト（POSE_KEYSと同じ12種+自動） | `fWCharPose` |
| character.expression | セレクト（`meta.expressions`と同じ・+自動） | `fWCharExpression` |
| animation.mode | セレクト（step/all/none） | `fWAnimationMode` |

保存処理は `collectInsert()`、新規作成時の既定値は `buildDefaultInsert()`（どちらも `story_editor.html` 内）。
AI向け台本生成プロンプトの説明文は `story_editor.py` の `_build_script_prompt()` 内、
`"━━━ インサート演出 ..."` の配列に記載（`whiteboard_explain` の1行）。

---

## 7. 改修時の注意（このプロジェクト共通のルール）

- `video/src/StoryVideo.tsx` を変更したら `cd video && npx tsc --noEmit -p .` で型チェック（数秒で終わる）。
- `story_editor.html`/`story_editor.py` を変更したら `python3 test_story_editor.py` を実行。
- TSXを変更したら、エディタのプレビューに反映させるには `node video/scripts/build-story-player.mjs` の再実行（またはstory_editor.py再起動）が必要
  （`story-player.js` は事前ビルドされたバンドルで、TSX変更を自動検知しない）。
- 新しいインサート種別のフィールドを増やす場合は `docs/new-effect-checklist.md` の「新しいインサート種別追加時」の手順（①〜⑤）に従う。
- `video/public/` 配下は生成物でgit管理外。フォント等の新規アセットは `video/assets/` に置き、`video/scripts/prep-story.mjs` で同期する。
