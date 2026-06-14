// meta.json の構造（main_story.py の build_meta が出力する形式）
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
  // 画面演出 effect（story_script が付与）。Remotionが演出に使う。
  // phase は旧APOD由来で「実は〇〇雑学」では未付与・未使用（型整合のため残置）。
  phase?: Phase;
  effect?: Effect;
  // 実は〇〇雑学: 所属章とsection（intro/trivia/outro・story_script が付与）。描画では未使用だが型整合用。
  chapter?: number;
  section?: Section;
  // C-1: その発言中に映す画像が章の image_cuts の何番目か（0始まり）。timing算出に使う（描画では未使用）。
  cut?: number;
  // 声の演技（任意・音声生成で使用／描画では未使用）。speed/pitch/intonation/volume の上書き。
  voice?: { speed?: number; pitch?: number; intonation?: number; volume?: number };
  // この台詞の後の無音秒（任意・音声生成で使用）。「実は…」のタメ等。
  pause?: number;
  // ユニゾン（二人同時発話）。trueなら音声を全話者で重ね、立ち絵も両方の口が動く（締めの挨拶等）。
  chorus?: boolean;
  // 解説パネル演出（任意・案A）。この発言の再生時にパネルを操作する。
  // panel_event="shrink"=画像を縮小しテキスト領域を開く / panel_item=n=items[n]を出現させる。
  // build_chapter_topics が時刻に解決して topic.panel へ載せるため、描画では未使用（型整合・デバッグ用）。
  panel_event?: "shrink";
  panel_item?: number;
  // 画像演出のタイミング合図（任意）。build_chapter_topics が時刻解決に使う（描画では未使用）。
  // reveal=この発言で「実は」の答え/数字を出す（quiz/stat の出現時刻）。
  // callout_item=この発言で callouts[n] を出す。
  // compare_item=この発言で比較の n 番目(0=左/1=右)を出す（右で分割）。
  reveal?: boolean;
  callout_item?: number;
  compare_item?: number;
};

// クイズ・リビール：「？」で溜めて答えを出す（掛け合いの問い→外し→実はと相性◎）。
export type Quiz = {
  question: string;       // 溜めの間に出す問い（短く）
  answer: string;         // リビールで出す答え（短く）
  // 画像を使わない演出＝背後の通常画像(あればそのまま)/黒板の上に「？・問い・答え」を重ねる。
  // image は基本未使用（手書きで明示指定された旧データのみ後方互換で残置・現状の描画では参照しない）。
  image?: string;
  revealAt?: number;      // 答えを出す絶対時刻（秒）。build が reveal発言/zoom_punch/章中盤から解決。
  // 「？・問い」を囲む土台パネルの背景色＋不透明度（任意）。無指定なら濃紺の半透明（既定）。
  // 背後の画像/黒板は暗転させない。bgOpacity=0 で土台なし（文字だけ・影で可読）。
  bg?: string;            // CSS color（例 "#0f141e"）
  bgOpacity?: number;     // 0..1（既定 0.62）
};

// 比較（2分割）：左右（縦は上下）にA対Bを並べる。before/after・対比ネタ向き。
export type CompareSide = { label: string; image?: string };
export type Compare = {
  left: CompareSide;
  right: CompareSide;
  showAt?: number;        // 後方互換（旧データ）。新規は at0/at1 を使う。
  // 出現の絶対時刻（秒）。build が発言の compare_item から解決。
  // at0=左が出る / at1=右が出る＝分割する。at0==at1 なら最初から2分割。
  at0?: number;
  at1?: number;
};

// 数字強調：大きな数字＋単位を画像に重ねる。インパクト重視の瞬間に。
export type Stat = {
  value: string;          // 表示する数字/文字（例 "1/8" "50万" "500000"）
  unit?: string;          // 単位（例 "時間" "倍"）
  label?: string;         // 補足ラベル（例 "故障率"）
  showAt?: number;        // 出現する絶対時刻（秒）。build が reveal発言/zoom_punch/章中盤から解決。
  countTo?: number;       // value が整数のときカウントアップの到達値（build が推定）。
};

// 注釈・吹き出し：画像上の位置(0..1正規化)を指して短いラベルを出す。
export type Callout = {
  text: string;
  x: number;              // 指し示す点の x（0..1・画像枠基準）
  y: number;              // 同 y
  arrow?: boolean;        // ラベルから点へ線/矢印を引く
  at?: number;            // 出現する絶対時刻（秒）。build が callout_item発言/均等割りで解決。
};

// 解説パネルの1項目（縮小画像の横/下に段階表示する要点テキスト）。
export type PanelItem = {
  text: string;
  // 直前の項目から矢印でつなぐ（簡易フロー表現）。
  arrow_from_prev?: boolean;
  // 出現する絶対時刻（秒）。build_chapter_topics が panel_item の発言timing or 均等割りで算出。
  at?: number;
};

// 章の解説パネル定義（縮小画像＋段階表示テキスト）。build_chapter_topics が解決して topic に載せる。
export type Panel = {
  // パネルに出すメイン画像のファイル名（public/相対）。build が cut から解決。無ければテキストのみ。
  image?: string;
  // 画像に使う image_cut の番号（レビューで選択）。build が image へ解決。
  cut?: number;
  // テキスト領域（縮小画像の横）の背景色（CSS color）。無指定なら透過（黒板が見える）。
  bg?: string;
  // 背景色の不透明度 0..1（任意・既定 1）。bg 指定時に裏を透かす量を調整する。
  bgOpacity?: number;
  // テキスト領域上部の見出し（任意・お題）。並列項目のときに特に有効。
  heading?: string;
  items: PanelItem[];
  // 画像を縮小しテキスト領域を開く絶対時刻（秒）。無指定時は章頭。
  shrinkAt?: number;
};

// 中央ビジュアルのトピック。imageがあれば画像、無ければtitleでカード描画。
// 画像パスは public/ からの相対（staticFileで参照）。
export type Topic = {
  title?: string;
  image?: string;
  start: number;
  end: number;
  // 旧APOD由来：manual想像イラスト未用意時のプレースホルダ表示用（雑学では未使用・型整合のため残置）。
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
  // 手動クロップ（画像レビューで指定）。元画像の {l,t,r,b}(0..1正規化)の矩形だけを枠に表示。
  crop?: { l: number; t: number; r: number; b: number };
  // 補正フィルタ（画像レビューで指定）。CSS filterに変換。brightness/contrast=1が等倍, grayscale=0..1。
  filter?: { brightness?: number; contrast?: number; grayscale?: number };
  // true=中央ビジュアルを出さない（黒板＋立ち絵のみ）。画像レビューで「画像なし」を選んだカット。
  blank?: boolean;
  // contain時の余白（px・全方向）。ロゴが枠いっぱいの素材に内側マージンを足す。
  pad?: number;
  // contain時の余白背景色（CSS color）。未指定なら既定の淡いグレーグラデ。
  bg?: string;
  // 実は〇〇雑学: 章メタ（main_story.build_chapter_topics が付与）。
  chapter?: number;       // 0始まりの章番号
  chapterTotal?: number;  // 全章数
  section?: Section;       // intro / trivia / outro
  triviaIndex?: number;   // 「実は」ネタの通し番号（1始まり・trivia章のみ。章バッジ「実は ①②③」用）
  triviaTotal?: number;   // ネタ総数
  // ショート用フック（掴み）。スクロールを止める自己完結した問い/断言。
  // Gemini生成（無ければタイトルから仮生成）。冒頭数秒だけ縦ショートに大テロップで重ねる。
  hook?: string;
  credit?: string;        // この章の画像出典（CC-BY帰属など）。動画内には現状出さない。
  // 解説パネル（任意）。build_chapter_topics が章の panel 定義を時刻解決して載せる。
  // これがある章は中央ビジュアルをパネルレイアウト（縮小画像＋段階テキスト）で描画する。
  panel?: Panel;
  // 画像演出（任意・すべて build_chapter_topics が時刻解決して載せる）。
  // quiz / compare は中央ビジュアルを置き換える主モード。stat / callouts は画像に重ねる層。
  quiz?: Quiz;
  compare?: Compare;
  stat?: Stat;
  callouts?: Callout[];
  // 演出の表示窓（絶対秒）。topicがこの窓をまたぐ時、窓内だけ演出を出し手前/後は通常画像にする。
  // （「ここから」のセリフ時刻より前から演出が始まる、カット境界への丸め込みを防ぐ）
  vizFrom?: number;
  vizUntil?: number;
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
  // 2.5Dパララックス用：深度マップ(<base>.depth.png)が用意できている画像ファイル名の一覧。
  // prep.mjs が public を走査して生成。縦ショートでこの画像は ParallaxImage で動かす。
  depthMaps?: string[];
  // BGM / 効果音(SE)。main_story.build_audio が出力し、prep.mjs が未配置ファイルを除去（無ければ無音）。
  audio?: {
    bgm?: { file: string; volume?: number; fade?: number } | null;  // 全体ループBGM
    se_volume?: number;                                              // SE全体の音量
    se?: Record<string, string>;                                    // トリガー名→ファイル名（se/配下）
    events?: { t: number; se: string }[];                          // 鳴らすSEイベント（時刻順）
  } | null;
  script: Turn[];
};
