// 立ち絵(Avatar)まわりの共通型。
// 掛け合い雑学動画(meta.json)用の型定義は legacy-dialogue/video/src/types.ts に凍結済み。

// 感情。立ち絵の表情スロット・演出の色味などに使う。
export type Emotion = "normal" | "surprise" | "happy" | "sad" | "angry" | "panic";

// 声・話者の性別（口調やデフォルト声選択に使用）。
export type Gender = "male" | "female";
