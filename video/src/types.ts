// meta.json の構造（main.py: update_web が出力する形式）
// 感情。立ち絵の目/効果の差分とオーバーアクションの種別に使う。
// 任意：未指定なら caption テキストから簡易推定（DialogueVideo側）。
export type Emotion = "normal" | "surprise" | "happy" | "sad" | "angry";

// 進行フェーズ。演出の強弱に使う。任意（未指定なら "fact" 扱い）。
export type Phase = "intro" | "fact" | "if" | "outro";

// セクション種別（実は〇〇雑学）。intro=導入 / trivia=各ネタ / outro=締め。任意。
export type Section = "intro" | "trivia" | "outro";

// 画面演出。ifパートの見せ場で強い演出を出す。任意（未指定なら "kenburns"）。
// kenburns=標準ズーム/パン / zoom_punch=寄り / shake=揺れ / flash=白転換 / glow_pulse=発光脈動。
export type Effect = "kenburns" | "zoom_punch" | "shake" | "flash" | "glow_pulse";

export type Sentence = {
  text: string;
  start: number;
  end: number;
  emotion?: Emotion;
};

export type Turn = {
  speaker: string;
  text: string;
  start: number;
  end: number;
  sentences?: Sentence[];
  emotion?: Emotion;
  // 進行フェーズ・画面演出（apod_script.py が付与）。Remotionが演出に使う。
  phase?: Phase;
  effect?: Effect;
  // IT技術史ストーリー: 所属章とsection（story_script が付与）。描画では未使用だが型整合用。
  chapter?: number;
  section?: Section;
};

// 中央ビジュアルのトピック。imageがあれば画像、無ければtitleでカード描画。
// 画像パスは public/ からの相対（staticFileで参照）。
export type Topic = {
  title?: string;
  image?: string;
  start: number;
  end: number;
  // manual想像イラストが未用意のときのプレースホルダ表示用（apod側が付与）。
  // note=情景プロンプト / placeholder=差し替え先ファイル名（例 manual_01.png）。
  note?: string;
  placeholder?: string;
  // 注目アノテーション（image_plan mode=focus）。元画像を切り出さず該当領域に枠を重ね、そこへ寄る。
  // l/t/r/b は画像左上を原点に 0..1 へ正規化した枠。image_aspect(=画像 w/h)で枠位置を実被写体に合わせる。
  focus?: { l: number; t: number; r: number; b: number };
  image_aspect?: number;
  // 画像の枠への収め方。"cover"(既定)=枠を埋めはみ出しを切る（写真向き）/
  // "contain"=全体を収め余白を入れる（ロゴ・アイコン・記号は端が切れると意味を失うため）。
  fit?: "cover" | "contain";
  // 実は〇〇雑学: 章メタ（main_story.build_chapter_topics が付与）。
  chapter?: number;       // 0始まりの章番号
  chapterTotal?: number;  // 全章数
  section?: Section;       // intro / trivia / outro
  triviaIndex?: number;   // 「実は」ネタの通し番号（1始まり・trivia章のみ。章バッジ「実は ①②③」用）
  triviaTotal?: number;   // ネタ総数
  credit?: string;        // この章の画像出典（CC-BY帰属など）。動画内には現状出さない。
};

export type Gender = "male" | "female";

// 話者ごとの属性。
// - avatar: パーツ分け立ち絵のフォルダ名（assets/avatars/<avatar>/）。未指定なら名前から既定解決(ずんだもん→zundamon等)、それも無ければgenderの単一画像にフォールバック。
// - expressive: trueなら驚き等でオーバーアクション（ずんだもん用）。
// - gender: パーツ未配置時の単一画像(male_/female_)フォールバック用。
export type Speaker = {
  name: string;
  gender: Gender;
  avatar?: string;
  expressive?: boolean;
  // 立ち絵を左右反転する（向きが逆の素材用）。
  flip?: boolean;
};

export type Meta = {
  generated_at: string;
  // 任意のタイトル（コンテンツ非依存。無ければヘッダー非表示）
  title?: string;
  // 左上のチャンネル名バッジ（任意。無ければDialogueVideo側の既定値）
  channel?: string;
  // ニュース固有フィールド（朝版/夜版）。汎用動画では未使用・任意
  session?: string;
  // 話者の性別など（任意。無ければ名前フォールバック→登場順で解決）
  speakers?: Speaker[];
  // 中央ビジュアルの切替（任意。無ければプレースホルダ表示）
  topics?: Topic[];
  // クレジット表記（VOICEVOX規約・画像出典など）。常時小さく表示する。
  credits?: string[];
  // 立ち絵パーツの一覧。prep.mjsが assets/avatars/<キャラ>/ を走査して生成し、
  // Root.tsx が public/avatars/manifest.json を読んで注入する。
  // 形: { "zundamon": { "base": "base.png", "mouth_open": "mouth_open.png", ... } }
  avatarManifest?: Record<string, Record<string, string>>;
  script: Turn[];
};
