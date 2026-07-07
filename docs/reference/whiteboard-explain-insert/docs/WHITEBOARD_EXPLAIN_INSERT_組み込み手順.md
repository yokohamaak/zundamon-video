# whiteboard_explain インサート部品 組み込み手順

## 目的

`run-story` に、めたんがホワイトボードで解説するインサート演出を追加するための部品です。

このZIPに含まれるのは、設定画面から呼ばれる側の描画処理です。
設定画面そのものは別途 Claude Code に修正させる想定です。

---

## 追加されるインサート種別

```text
whiteboard_explain
```

設定画面側は、既存のインサート保存処理に以下のようなデータを保存してください。

```json
{
  "type": "insert",
  "insertType": "whiteboard_explain",
  "duration": 420,
  "config": {
    "title": "めたんの解説コーナー",
    "theme": "見えない仕事が評価されにくい理由",
    "sections": [
      {
        "heading": "やったことが残らない",
        "bullets": ["突発対応", "調整", "共有"],
        "icon": "confused"
      },
      {
        "heading": "数字で見えにくい",
        "bullets": ["助かったで終わる", "成果に変換されない"],
        "icon": "scribble"
      },
      {
        "heading": "可視化すると伝わる",
        "bullets": ["メモ化", "一覧化", "上司に共有"],
        "icon": "checklist"
      }
    ],
    "conclusion": "便利屋で終わらず、成果として見せるのが大事！",
    "character": {
      "name": "metan",
      "pose": "pointing",
      "expression": "smile"
    },
    "animation": {
      "mode": "step"
    }
  }
}
```

---

## ファイル配置

ZIP内の以下を、run-story の `src/inserts/whiteboardExplain/` にコピーしてください。

```text
src/inserts/whiteboardExplain/
  WhiteboardExplainInsert.tsx
  WhiteboardDoodleIcon.tsx
  whiteboardExplainTypes.ts
  whiteboardExplainDefaults.ts
  whiteboardExplainLayout.ts
  whiteboardExplainValidation.ts
  whiteboardExplainAssets.ts
  index.ts
```

既存プロジェクトのパス構成が違う場合は、`inserts` 配下の近い場所に置いてください。

---

## Remotion側への登録例

既存のインサート描画処理に、以下のような分岐を追加します。

```tsx
import { WhiteboardExplainInsert } from './inserts/whiteboardExplain';

export const RenderInsert = ({ insert }) => {
  switch (insert.insertType) {
    case 'whiteboard_explain':
      return (
        <WhiteboardExplainInsert
          config={insert.config}
          durationInFrames={insert.duration}
        />
      );

    default:
      return null;
  }
};
```

既存で `insertRegistry` のような仕組みがある場合は、以下のように登録してください。

```ts
import { WhiteboardExplainInsert } from './inserts/whiteboardExplain';

insertRegistry.register('whiteboard_explain', WhiteboardExplainInsert);
```

---

## 設定画面側で編集できるようにする項目

MVPでは以下だけで十分です。

```text
title
theme
sections[0].heading
sections[0].bullets[0..2]
sections[0].icon
sections[1].heading
sections[1].bullets[0..2]
sections[1].icon
sections[2].heading
sections[2].bullets[0..2]
sections[2].icon
conclusion
character.pose
character.expression
animation.mode
```

---

## 使用できる icon

```text
none
confused
scribble
checklist
memo
conversation
warning
idea
table
```

外部画像を使わない場合は、部品内の簡易SVG落書きアイコンが描画されます。

透過PNG素材を使いたい場合は、config.assets.iconImages に渡してください。

```json
{
  "assets": {
    "iconImages": {
      "confused": "/icons/whiteboard/confused.png",
      "checklist": "/icons/whiteboard/checklist.png"
    }
  }
}
```

---

## めたん立ち絵の配置

デフォルトでは、以下のパスを探します。

```text
public/characters/metan/{pose}_{expression}.png
```

例：

```text
public/characters/metan/pointing_smile.png
public/characters/metan/explain_normal.png
public/characters/metan/confident_smile.png
public/characters/metan/thinking_troubled.png
```

まだ立ち絵がない場合でも、仮のプレースホルダーが表示されます。

明示的に画像を指定したい場合は、config.character.image を使います。

```json
{
  "character": {
    "name": "metan",
    "pose": "pointing",
    "expression": "smile",
    "image": "/characters/metan/custom_pointing.png"
  }
}
```

---

## 背景・ホワイトボード画像の扱い

画像を指定しない場合は、部品内でベクターのホワイトボードを描画します。

背景画像やホワイトボード画像を使いたい場合は、以下を指定します。

```json
{
  "assets": {
    "backgroundImage": "/backgrounds/office_whiteboard_bg.png",
    "whiteboardImage": "/backgrounds/whiteboard_only.png"
  }
}
```

Remotionの `staticFile()` を通すため、`public/` からの相対パスとして扱われます。

---

## フォント

デフォルトは以下です。

```text
Yusei Magic, Yu Gothic, Hiragino Sans, sans-serif
```

Google Fonts の油性マジックを使う場合は、アプリ側でフォントを読み込んでください。

例：Remotion root や CSS で読み込み。

```css
@import url('https://fonts.googleapis.com/css2?family=Yusei+Magic&display=swap');
```

ローカルにフォントを置いている場合は、既存のフォント読み込み方式に合わせてください。

---

## アニメーション

`animation.mode` は以下です。

```text
step  : タイトル → テーマ → 項目1 → 項目2 → 項目3 → 結論 の順に表示
all   : 最初から全部表示
none  : all とほぼ同じ。アニメーションなし
```

MVPでは `step` 推奨です。

---

## 文字量の目安

描画側で極端に長い文字は省略しますが、設定画面側でも制限したほうが安全です。

```text
タイトル：18文字以内
テーマ：30文字以内
項目見出し：14文字以内
箇条書き：16文字以内
結論：40文字以内
項目数：3固定
箇条書き：各項目3つまで
```

スマホ視聴前提なら、この制限はかなり重要です。

---

## Claude Codeへの依頼文サンプル

```text
run-storyに whiteboard_explain という新しいインサート種別を追加したいです。

このZIPの src/inserts/whiteboardExplain/ をプロジェクトにコピーし、既存のインサート描画処理から呼び出せるようにしてください。

設定画面側では、以下の config を編集できるようにしてください。

- title
- theme
- sections[0..2].heading
- sections[0..2].bullets[0..2]
- sections[0..2].icon
- conclusion
- character.pose
- character.expression
- animation.mode

保存される insert は以下の形式にしてください。

{
  type: "insert",
  insertType: "whiteboard_explain",
  duration: 420,
  config: { ... }
}

描画側では以下のように呼んでください。

<WhiteboardExplainInsert
  config={insert.config}
  durationInFrames={insert.duration}
/>

既存の insert registry がある場合は、whiteboard_explain に WhiteboardExplainInsert を登録してください。

なお、めたん立ち絵は public/characters/metan/{pose}_{expression}.png を参照する設計です。
素材がない場合は仮表示されるので、まずは描画が動く状態を優先してください。
```

---

## 注意

この部品は、既存の run-story の正確なファイル構成を知らない状態で作った独立部品です。
そのため、import path や insert registry の接続部分は、既存構成に合わせて Claude Code 側で微調整してください。

描画部品自体は、Remotion + React 前提です。
