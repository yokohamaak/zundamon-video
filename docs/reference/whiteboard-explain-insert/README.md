# Whiteboard Explain Insert for run-story

めたんがホワイトボードで解説する `whiteboard_explain` インサート用の Remotion/React 部品です。

## 中身

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

docs/
  WHITEBOARD_EXPLAIN_INSERT_組み込み手順.md
```

## 使い方

詳しくは `docs/WHITEBOARD_EXPLAIN_INSERT_組み込み手順.md` を見てください。

基本的には以下で呼び出します。

```tsx
import { WhiteboardExplainInsert } from './inserts/whiteboardExplain';

<WhiteboardExplainInsert
  config={insert.config}
  durationInFrames={insert.duration}
/>
```
