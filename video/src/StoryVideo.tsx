import {
  AbsoluteFill,
  Audio,
  getRemotionEnvironment,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useWindowedAudioData } from "@remotion/media-utils";
import { Avatar } from "./Avatar";
import type { ExpressionCfg } from "./Avatar";
import type { Emotion, Gender } from "./types";

// ─── PC画面インサート型定義 ──────────────────────────────────
export type StoryInsert =
  | { kind: "warning"; title?: string; text: string }
  | { kind: "chat"; user: string; ai: string[]; highlight?: number }
  | { kind: "ok"; text?: string }
  | { kind: "teamchat"; channel?: string; messages: { from: string; text: string; highlight?: boolean }[] }
  | { kind: "mailer"; from?: string; fromAddr?: string; subject: string; body: string; time?: string };

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。DialogueVideo と同値。
const LIPSYNC_GAIN = 5;

// ─── 回想（flashback）演出の定数 ────────────────────────────
// 後で調整しやすいよう1箇所にまとめる。
const FB_SATURATE = 0.4;        // 回想中の彩度（1.0=元のまま・低いほど色が薄い）
const FB_BRIGHTNESS = 1.02;     // 回想中の輝度（微加）
const FB_GRAIN_OPACITY = 0.06;  // グレインの不透明度（0.0=なし・0.1で見えてくる）
const FB_DISSOLVE_SEC = 0.3;    // 白ディゾルブ片側の秒数（合計 2×FB_DISSOLVE_SEC）
const FB_TELOP_SEC = 1.2;       // テロップの表示秒数
const FB_TELOP_FADE = 0.25;     // テロップのフェードイン/アウト秒数

// ───────────────────────────────────────────────────────────
// ストーリー調 会話劇動画（新ツール Phase 1）の描画。
// 既存の DialogueVideo には触れず、立ち絵 Avatar だけ流用する。
// 入力は手書きの story-sample.json / story-scenes.json（固定入力）。
// 仕様: docs/next-animation-editor-spec.md §4。
// ───────────────────────────────────────────────────────────

// 台本の表情語彙（§4.3）。立ち絵 Emotion へマップする。
// trouble/panic は専用パーツ未配置のため当面は近い既存表情へ寄せる（Phase 5 でパーツ追加）。
// 任意の追加表情も許容するため string に緩める（組み込み5種以外はモーション無しの静的表情）。
export type StoryExpression = string;

// 組み込み5種のみ Emotion マップを持つ。未知キーは "normal" にフォールバック。
const EXPRESSION_TO_EMOTION: Record<string, Emotion> = {
  normal: "normal",
  happy: "happy",
  surprise: "surprise",
  trouble: "sad", // 困り：暫定（専用パーツが無ければ通常目にフォールバック）
  panic: "panic", // 焦り：汗＋見開き目＋小刻みな揺れ
};

export type StorySentence = { text: string; start: number; end: number };

export type StoryTurn = {
  id: string;
  speaker: string;
  text: string;
  scene: string;
  expression?: StoryExpression;
  enter?: string[];
  // キャラの向き（画面のどちらを向くか）の明示指定。省略時は立ち位置から自動（中央向き）。
  // 例: { "zundamon": "left", "metan": "right" }
  face?: Record<string, "left" | "right">;
  // 話者プッシュイン演出（emphasis=true のターン中、話者寄りへズームイン）。
  emphasis?: boolean;
  // カメラシェイク演出（shake=true のターン中、ターン開始からの減衰振動オフセットを加算）。
  shake?: boolean;
  // 回想フラグ（true のターンが回想区間）。
  flashback?: boolean;
  // テロップ（境界付近で短時間表示する時代テキスト。例「― 前日 ―」）。
  telop?: string;
  // 台詞後の無音秒（音声生成で使用。描画では参照しない）。
  pause?: number;
  // PC画面インサート演出（このターン中に全画面PC画面UIを重ねる）。
  insert?: StoryInsert;
  // 退場するキャラ。このターンの終わり（end）にスライドアウトして以後は非表示。
  exit?: string[];
  // 退場方向（"left"/"right"）。省略時は自分の居る側（近い画面端）へ。
  exitDir?: "left" | "right";
  start: number;
  end: number;
  sentences?: StorySentence[];
};

export type StoryScript = { title?: string; audio?: string; script: StoryTurn[] };

type Anchor = { x: number; y: number };

export type SceneDef = {
  label?: string;
  bg: string;
  front?: string | null;
  shot?: "solo" | "duo" | "split";
  camera?: "static" | "slow-zoom";
  scale?: number; // 立ち絵の拡大率（既定 1.9）
  anchors: Record<string, Anchor>;
  // どのキャラをどのアンカーに置くか（charId→アンカー名）。
  // 省略時は登場順で left/right を自動割当。
  cast?: Record<string, string>;
  // この場面に入るときの切り替え方式（省略時 fade-black）。今後 crossfade 等を追加予定。
  transition?: "fade-black" | "cut";
  // 立ち絵の画角。"bust"=バストアップ（既定・office）、"full"=全身（server_room/rooftop/home 等）。
  figure?: "bust" | "full";
  // モブ（1枚絵）の立ち位置と大きさ。省略時は既定（中央やや下・標準）。
  mobAnchor?: Anchor;
  mobHeight?: number; // モブ画像の高さ(px・frame基準でなく素の高さ)。既定 760。
  // モブ別の配置（scene_editor で D&D 編集）。x,y=正規化座標, scale=拡大率。
  // hidden=true で立ち絵を非表示（チャット/音声のみ登場にする）。
  mobs?: Record<string, { x: number; y: number; scale?: number; hidden?: boolean }>;
};

export type SceneLibrary = { scenes: Record<string, SceneDef> };

export type CharDef = {
  avatar: string; // パーツ立ち絵フォルダ名
  gender: Gender; // フォールバック用
  expressive?: boolean;
  bubbleColor?: string; // 吹き出し枠の色（話者で色分け）
};

// モブ／未定義キャラの既定枠色。
const DEFAULT_BUBBLE_COLOR = "#9aa0a6"; // グレー

// 主役キャラ定義（Phase 1 はインライン。将来はライブラリ化）。
const CHARACTERS: Record<string, CharDef> = {
  zundamon: { avatar: "zundamon", gender: "male", expressive: true, bubbleColor: "#5fb84f" }, // 緑系
  metan: { avatar: "metan", gender: "female", expressive: false, bubbleColor: "#e87bb0" }, // ピンク系
};

// モブ定義（いらすとや風の1枚絵・口パク無し）。話している間だけ立つ。
// images: 状態キー→ファイル（normal / agitated）。表情で出し分ける。
// 画像は public/mobs/<file>（assets/mobs を prep-story が public へコピー）。
type MobDef = { images: Record<string, string>; scale?: number; flip?: boolean; anchor?: Anchor };
const MOBS: Record<string, MobDef> = {
  // 営業=ノートPC作業姿（画像下端=机／机frontで隠れる）。anchor.y を下げて机裏に。
  営業: {
    images: { normal: "mobs/mob_normal.png", agitated: "mobs/mob_panic.png" },
    scale: 0.85,
    anchor: { x: 0.5, y: 0.99 },
  },
  // 部長=バスト肖像（画像下端=胸）。anchor.y を上げて胸が机の高さに来るように。
  部長: {
    images: { normal: "mobs/manager_normal.png", agitated: "mobs/manager_angry.png" },
    scale: 0.62,
    anchor: { x: 0.5, y: 0.82 },
  },
};
const isMob = (id: string): boolean => id in MOBS;
// セリフがインサート画面（チャット/AIチャット）に出ているターンか。
// true のターンは吹き出しを出さない（画面と内容が重複するため）。
function lineShownInInsert(turn: StoryTurn): boolean {
  return (
    !!turn.insert &&
    (turn.insert.kind === "teamchat" || turn.insert.kind === "chat")
  );
}
// 取り乱し系の表情なら agitated（焦り/怒り）、それ以外は normal。
// 未知の追加表情は normal 扱い（組み込み5種と比較してフォールバック）。
function mobImage(mobId: string, expression?: StoryExpression): string {
  const m = MOBS[mobId];
  const agitated =
    expression === "panic" || expression === "surprise" || expression === "trouble";
  const key = agitated && m.images.agitated ? "agitated" : "normal";
  return m.images[key] ?? Object.values(m.images)[0];
}

type Manifest = Record<string, Record<string, string>>;

// expressions.json の型（キャラ→表情名→ExpressionCfg）
export type ExpressionsMap = Record<string, Record<string, ExpressionCfg>>;

export type StoryVideoProps = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Manifest;
  audio?: string; // 音声ファイル（public配下・任意）
  expressions?: ExpressionsMap; // expressions.json（省略時は旧来の emotion ベース）
};

// 立ち絵ボックスサイズ。バスト用は 445×445（Avatar の既定値と同じ）。
// 全身用はキャンバスのアスペクト比をバスト幅445に合わせた高さ。
const AVATAR_BOX = 445;
// 全身キャンバスサイズ（PSD書き出し時の共通bbox＝assets/avatars/<char>/full/_box.json と一致）。
const FULL_CANVAS = {
  zundamon: { w: 783, h: 1473 },
  metan: { w: 858, h: 1769 },
} as const;
const FULL_BOX_W = 445; // 全身Avatar表示幅（px）。scene_editor.html と同値にする（WYSIWYG）。sceneのavScaleで最終サイズが決まる。
function fullBoxSize(charId: string): { w: number; h: number } {
  const c = FULL_CANVAS[charId as keyof typeof FULL_CANVAS];
  if (!c) return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * 1.8) };
  return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * (c.h / c.w)) };
}

// ─── モブ判定 ──────────────────────────────────────────────
// CHARACTERS に定義されていないキャラ＝モブ。
// モブはレイアウト(ロスター/アンカー/カメラ)から除外し、吹き出し＋声だけで表現する。
function isKnownChar(charId: string): boolean {
  return charId in CHARACTERS;
}

// scene 名が連続する範囲を 1 区間（segment）にまとめる（§4.1）。
type Segment = { scene: string; turns: StoryTurn[]; start: number; end: number };

function buildSegments(script: StoryTurn[]): Segment[] {
  const segs: Segment[] = [];
  for (const t of script) {
    const last = segs[segs.length - 1];
    if (last && last.scene === t.scene) {
      last.turns.push(t);
      last.end = Math.max(last.end, t.end);
    } else {
      segs.push({ scene: t.scene, turns: [t], start: t.start, end: t.end });
    }
  }
  return segs;
}

// 現在時刻 t（秒）でアクティブな turn を返す（区間内＝そのturn、隙間＝直前のturn）。
function activeTurnAt(script: StoryTurn[], t: number): StoryTurn {
  let cur = script[0];
  for (const turn of script) {
    if (turn.start <= t) cur = turn;
    else break;
  }
  return cur;
}

// segment 内で、現時点までに登場したキャラ（enter の累積・登場順を保持）。
// モブ（CHARACTERS 未定義）はレイアウトから除外する。
function presentChars(seg: Segment, activeTurn: StoryTurn): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !order.includes(c)) order.push(c);
    }
    if (turn.id === activeTurn.id) break;
  }
  // 既知キャラが話者の場合のみ追加（モブ話者は追加しない）。
  if (isKnownChar(activeTurn.speaker) && !order.includes(activeTurn.speaker)) {
    order.push(activeTurn.speaker);
  }
  return order;
}

// 登場キャラを anchor 名へ割り当てる（1人=center / 2人=left,right / 3人=left,center,right）。
function assignAnchors(chars: string[]): Record<string, string> {
  const map: Record<string, string> = {};
  if (chars.length <= 1) {
    if (chars[0]) map[chars[0]] = "center";
  } else if (chars.length === 2) {
    map[chars[0]] = "left";
    map[chars[1]] = "right";
  } else {
    const names = ["left", "center", "right"];
    chars.slice(0, 3).forEach((c, i) => (map[c] = names[i]));
  }
  return map;
}

// segment 全体で登場する全キャラ（登場順）。立ち位置を区間中ずっと固定するため最終集合で決める。
// モブ（CHARACTERS 未定義）はレイアウトから除外する。
function segmentRoster(seg: Segment): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !order.includes(c)) order.push(c);
    }
    if (isKnownChar(turn.speaker) && !order.includes(turn.speaker)) {
      order.push(turn.speaker);
    }
  }
  return order;
}

// 各キャラが segment 内で初めて画面に出る時刻（秒）。
// モブ（CHARACTERS 未定義）は除外（アバターを持たないのでレイアウト計算に不要）。
function entranceTimes(seg: Segment): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !(c in e)) e[c] = turn.start;
    }
    if (isKnownChar(turn.speaker) && !(turn.speaker in e)) e[turn.speaker] = turn.start;
  }
  return e;
}

// 各キャラの退場時刻（秒）。turn.exit で指定されたキャラは、そのターンの end で退場する。
function exitTimes(seg: Segment): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.exit ?? []) {
      if (isKnownChar(c)) e[c] = turn.end;
    }
  }
  return e;
}

// 各キャラの退場方向（turn.exitDir）。省略時は undefined（＝自分の居る側へ）。
function exitDirs(seg: Segment): Record<string, "left" | "right"> {
  const d: Record<string, "left" | "right"> = {};
  for (const turn of seg.turns) {
    if (!turn.exitDir) continue;
    for (const c of turn.exit ?? []) {
      if (isKnownChar(c)) d[c] = turn.exitDir;
    }
  }
  return d;
}

// スライドイン/アウトにかける秒数。
const SLIDE_DUR = 0.5;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const easeInOutCubic = (x: number) =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);

// ─── PC画面インサートコンポーネント ─────────────────────────

const INSERT_BG = "#11151c";

/** 警告ダイアログ */
const InsertWarning: React.FC<{ insert: Extract<StoryInsert, { kind: "warning" }> }> = ({ insert }) => {
  const title = insert.title ?? "警告";
  return (
    <div
      style={{
        background: "#1a1d24",
        border: "3px solid #9ed957",
        borderRadius: 16,
        padding: "48px 64px",
        maxWidth: 860,
        width: "100%",
        boxShadow: "0 0 0 1px rgba(95,184,79,0.25), 0 24px 64px rgba(0,0,0,0.6)",
        display: "flex",
        flexDirection: "column",
        gap: 28,
      }}
    >
      {/* アプリ名（ZunMonitor=監視ツール） */}
      <div
        style={{
          fontSize: 26,
          color: "#5fb84f",
          fontFamily: "sans-serif",
          fontWeight: 700,
          letterSpacing: "0.12em",
        }}
      >
        ZunMonitor
      </div>
      {/* タイトルバー */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 20,
        }}
      >
        <span style={{ fontSize: 56, lineHeight: 1, color: "#e87b3a" }}>⚠</span>
        <span
          style={{
            fontSize: 44,
            fontWeight: 700,
            color: "#e87b3a",
            fontFamily: "sans-serif",
            letterSpacing: "0.04em",
          }}
        >
          {title}
        </span>
      </div>
      {/* 区切り */}
      <div style={{ height: 2, background: "rgba(95,184,79,0.35)", borderRadius: 1 }} />
      {/* 本文 */}
      <div
        style={{
          fontSize: 48,
          color: "#e8e0d6",
          fontFamily: "sans-serif",
          lineHeight: 1.5,
          fontWeight: 400,
          letterSpacing: "0.03em",
        }}
      >
        {insert.text}
      </div>
    </div>
  );
};

/** AIチャット（ZunAI） */
const InsertChat: React.FC<{ insert: Extract<StoryInsert, { kind: "chat" }> }> = ({ insert }) => {
  return (
    <div
      style={{
        background: "#1c2620",
        border: "3px solid #9ed957",
        borderRadius: 20,
        width: 920,
        overflow: "hidden",
        boxShadow: "0 24px 64px rgba(0,0,0,0.65)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ヘッダー */}
      <div
        style={{
          background: "#bdf08a",
          padding: "20px 32px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          borderBottom: "2px solid #9ed957",
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: "linear-gradient(135deg, #4fa83f, #2c7d28)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 34,
            fontWeight: 700,
            color: "#1f4012",
            fontFamily: "sans-serif",
            letterSpacing: "0.04em",
          }}
        >
          ZunAI
        </span>
      </div>
      {/* メッセージ本体 */}
      <div
        style={{
          padding: "28px 32px",
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        {/* ユーザーメッセージ（右寄せ） */}
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <div
            style={{
              background: "#4a9e44",
              color: "#fff",
              borderRadius: "18px 4px 18px 18px",
              padding: "16px 24px",
              fontSize: 38,
              fontFamily: "sans-serif",
              lineHeight: 1.5,
              maxWidth: "76%",
              fontWeight: 500,
            }}
          >
            {insert.user}
          </div>
        </div>
        {/* AI返答（左寄せ）。複数行。highlight 番号の吹き出しを強調。 */}
        {insert.ai.map((msg, i) => {
          const isHighlighted = typeof insert.highlight === "number" && insert.highlight === i;
          return (
            <div key={i} style={{ display: "flex", justifyContent: "flex-start" }}>
              <div
                style={{
                  background: isHighlighted ? "#2c1a08" : "#232a38",
                  color: isHighlighted ? "#f0e0c0" : "#c8d0e0",
                  borderRadius: "4px 18px 18px 18px",
                  padding: "16px 24px",
                  fontSize: 38,
                  fontFamily: "sans-serif",
                  lineHeight: 1.5,
                  maxWidth: "76%",
                  fontWeight: 400,
                  border: isHighlighted ? "2.5px solid #e07840" : "2px solid #2f3a50",
                  boxShadow: isHighlighted ? "0 0 0 4px rgba(224,120,64,0.18)" : "none",
                }}
              >
                {msg}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** ステータスOK画面 */
const InsertOk: React.FC<{ insert: Extract<StoryInsert, { kind: "ok" }> }> = ({ insert }) => {
  const text = insert.text ?? "正常";
  return (
    <div
      style={{
        background: "#141e18",
        border: "3px solid #2ea86a",
        borderRadius: 20,
        padding: "56px 80px",
        maxWidth: 640,
        width: "100%",
        boxShadow: "0 0 0 1px rgba(46,168,106,0.2), 0 24px 64px rgba(0,0,0,0.6)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 32,
      }}
    >
      {/* アプリ名（ZunMonitor=監視ツール・正常） */}
      <div
        style={{
          fontSize: 26,
          color: "#5fae84",
          fontFamily: "sans-serif",
          fontWeight: 700,
          letterSpacing: "0.12em",
        }}
      >
        ZunMonitor
      </div>
      <div
        style={{
          fontSize: 96,
          color: "#2ea86a",
          lineHeight: 1,
          fontFamily: "sans-serif",
          fontWeight: 700,
        }}
      >
        ✓
      </div>
      <div
        style={{
          fontSize: 56,
          color: "#a0e8c0",
          fontFamily: "sans-serif",
          fontWeight: 600,
          letterSpacing: "0.06em",
        }}
      >
        {text}
      </div>
    </div>
  );
};

// teamchat 用: キャラID → 表示名。
const TEAMCHAT_DISPLAY: Record<string, string> = {
  zundamon: "ずんだもん",
  metan: "四国めたん",
};

// teamchat 用: 発言者情報（アイコン画像URL・名前・名前色）を解決するヘルパー。
function resolveTeamChatSender(from: string): {
  imgSrc: string | null;
  name: string;
  nameColor: string;
} {
  if (from in CHARACTERS) {
    const cdef = CHARACTERS[from];
    return {
      // 顔だけのアイコン（base は目/口/腕が別レイヤーでのっぺらぼうになるため専用 icon を使う）。
      imgSrc: staticFile(`avatars/${cdef.avatar}/icon.png`),
      name: TEAMCHAT_DISPLAY[from] ?? from,
      nameColor: cdef.bubbleColor ?? DEFAULT_BUBBLE_COLOR,
    };
  }
  if (from in MOBS) {
    const m = MOBS[from];
    return {
      imgSrc: staticFile(m.images.normal),
      name: from,
      nameColor: DEFAULT_BUBBLE_COLOR,
    };
  }
  return { imgSrc: null, name: from, nameColor: DEFAULT_BUBBLE_COLOR };
}

/** 社内チャット（ZunChat・Slack風） */
const InsertTeamChat: React.FC<{ insert: Extract<StoryInsert, { kind: "teamchat" }> }> = ({
  insert,
}) => {
  const channel = insert.channel ?? "general";
  return (
    <div
      style={{
        background: "#1c2620",
        border: "3px solid #9ed957",
        borderRadius: 22,
        width: 1480,
        overflow: "hidden",
        boxShadow: "0 24px 64px rgba(0,0,0,0.65)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ヘッダー */}
      <div
        style={{
          background: "#bdf08a",
          padding: "18px 32px",
          display: "flex",
          alignItems: "center",
          gap: 18,
          borderBottom: "2px solid #9ed957",
        }}
      >
        {/* ワークスペースアイコン（汎用グリッドロゴ風） */}
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: "linear-gradient(135deg, #4fa83f 0%, #2c7d28 100%)",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 4,
              width: 20,
              height: 20,
            }}
          >
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 2,
                  background: "rgba(255,255,255,0.9)",
                }}
              />
            ))}
          </div>
        </div>
        {/* チャンネル名 */}
        <span
          style={{
            fontSize: 48,
            fontWeight: 700,
            color: "#1f4012",
            fontFamily: "sans-serif",
            letterSpacing: "0.03em",
          }}
        >
          # {channel}
        </span>
        {/* 仕切り */}
        <div
          style={{ width: 1.5, height: 28, background: "#7cb84a", marginLeft: 4, marginRight: 4 }}
        />
        <span style={{ fontSize: 28, color: "#3f6a2a", fontFamily: "sans-serif", fontWeight: 600 }}>
          ZunChat
        </span>
      </div>

      {/* メッセージ一覧 */}
      <div
        style={{
          padding: "24px 32px",
          display: "flex",
          flexDirection: "column",
          gap: 0,
        }}
      >
        {insert.messages.map((msg, i) => {
          const { imgSrc, name, nameColor } = resolveTeamChatSender(msg.from);
          return (
            <div
              key={i}
              style={{
                display: "flex",
                flexDirection: "row",
                alignItems: "flex-start",
                gap: 26,
                padding: "18px 22px",
                borderRadius: 12,
                background: msg.highlight
                  ? "rgba(255,210,100,0.06)"
                  : "transparent",
                borderLeft: msg.highlight
                  ? "4px solid #f0b840"
                  : "4px solid transparent",
                marginBottom: 4,
              }}
            >
              {/* 丸アバターアイコン */}
              <div
                style={{
                  width: 88,
                  height: 88,
                  borderRadius: "50%",
                  overflow: "hidden",
                  flexShrink: 0,
                  background: imgSrc ? "transparent" : nameColor,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {imgSrc ? (
                  <img
                    src={imgSrc}
                    onError={(e) => {
                      const el = e.currentTarget as HTMLImageElement;
                      el.style.display = "none";
                      // 親の div を nameColor で塗りつぶす（onError 後は img 非表示）
                      if (el.parentElement) {
                        el.parentElement.style.background = nameColor;
                      }
                    }}
                    style={{
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      objectPosition: "center 10%",
                      display: "block",
                    }}
                  />
                ) : null}
              </div>

              {/* 名前 ＋ 本文 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1 }}>
                <span
                  style={{
                    fontSize: 40,
                    fontWeight: 700,
                    color: nameColor,
                    fontFamily: "sans-serif",
                    letterSpacing: "0.02em",
                  }}
                >
                  {name}
                </span>
                <span
                  style={{
                    fontSize: 54,
                    color: "#d0d8ec",
                    fontFamily: "sans-serif",
                    lineHeight: 1.45,
                    fontWeight: 400,
                  }}
                >
                  {msg.text}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** メーラー（メール閲覧画面・Gmail風ライトテーマ） */
const InsertMailer: React.FC<{ insert: Extract<StoryInsert, { kind: "mailer" }> }> = ({ insert }) => {
  // 差出人の頭文字とアバター色
  const fromName = insert.from ?? "送信者";
  const initial = fromName.charAt(0).toUpperCase() || "?";
  // アバター色は headletter のコードポイントから決定論的に選ぶ（固定色・ランダムでない）
  const AVATAR_COLORS = ["#1a73e8", "#0f9d58", "#f4511e", "#8430ce", "#188038", "#d50000"];
  const avatarColor = AVATAR_COLORS[fromName.charCodeAt(0) % AVATAR_COLORS.length];

  return (
    <div
      style={{
        background: "#f6f8fc",
        borderRadius: 12,
        width: 1380,
        overflow: "hidden",
        boxShadow: "0 4px 24px rgba(60,64,67,0.18), 0 1px 6px rgba(60,64,67,0.1)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "sans-serif",
      }}
    >
      {/* ツールバー（汎用メーラー感・ブランドなし） */}
      <div
        style={{
          background: "#ffffff",
          padding: "14px 28px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          borderBottom: "1px solid #e0e0e0",
        }}
      >
        <span style={{ fontSize: 34, color: "#5fb84f", lineHeight: 1 }}>✉</span>
        <span
          style={{
            fontSize: 32,
            fontWeight: 700,
            color: "#5fb84f",
            letterSpacing: "0.01em",
          }}
        >
          ZunMail
        </span>
      </div>

      {/* メールカード本体 */}
      <div
        style={{
          background: "#ffffff",
          margin: "24px 28px",
          borderRadius: 8,
          padding: "36px 48px",
          boxShadow: "0 2px 8px rgba(60,64,67,0.12)",
          display: "flex",
          flexDirection: "column",
          gap: 24,
        }}
      >
        {/* 件名 */}
        <div
          style={{
            fontSize: 56,
            fontWeight: 700,
            color: "#202124",
            lineHeight: 1.3,
          }}
        >
          {insert.subject}
        </div>

        {/* 差出人行 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 18,
          }}
        >
          {/* 丸アバター（頭文字） */}
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              background: avatarColor,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ffffff",
              fontSize: 34,
              fontWeight: 700,
            }}
          >
            {initial}
          </div>
          {/* 差出人名・アドレス */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span style={{ fontSize: 38, fontWeight: 600, color: "#202124" }}>
                {fromName}
              </span>
              {insert.fromAddr ? (
                <span style={{ fontSize: 30, color: "#80868b" }}>
                  {"<"}{insert.fromAddr}{">"}
                </span>
              ) : null}
            </div>
          </div>
          {/* 時刻（右端） */}
          {insert.time ? (
            <div style={{ fontSize: 32, color: "#80868b", flexShrink: 0 }}>
              {insert.time}
            </div>
          ) : null}
        </div>

        {/* 区切り線 */}
        <div style={{ height: 1, background: "#e0e0e0" }} />

        {/* 本文 */}
        <div
          style={{
            fontSize: 42,
            color: "#3c4043",
            lineHeight: 1.65,
            fontWeight: 400,
            whiteSpace: "pre-wrap",
          }}
        >
          {insert.body}
        </div>

        {/* 返信ボタン飾り（雰囲気用・非機能） */}
        <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
          {["↩ 返信", "→ 転送"].map((label) => (
            <div
              key={label}
              style={{
                border: "1px solid #dadce0",
                borderRadius: 20,
                padding: "10px 28px",
                fontSize: 28,
                color: "#5f6368",
              }}
            >
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/**
 * PC画面インサートのルートオーバーレイ。
 * opacity でフェードイン/アウトする（外側でアニメーション値を渡す）。
 */
const InsertOverlay: React.FC<{ insert: StoryInsert; opacity: number }> = ({ insert, opacity }) => {
  // mailer だけライトテーマ（白背景）。それ以外はダーク背景。
  const isLight = insert.kind === "mailer";
  return (
    <AbsoluteFill
      style={{
        background: isLight ? "#e8eaf0" : INSERT_BG,
        opacity,
        alignItems: "center",
        justifyContent: "center",
        // ごく薄いビネット（ライトは薄い暗縁・ダークはそのまま）
        boxShadow: isLight
          ? "inset 0 0 120px rgba(0,0,0,0.08)"
          : "inset 0 0 120px rgba(0,0,0,0.4)",
        pointerEvents: "none",
      }}
    >
      {insert.kind === "warning" && <InsertWarning insert={insert} />}
      {insert.kind === "chat" && <InsertChat insert={insert} />}
      {insert.kind === "ok" && <InsertOk insert={insert} />}
      {insert.kind === "teamchat" && <InsertTeamChat insert={insert} />}
      {insert.kind === "mailer" && <InsertMailer insert={insert} />}
    </AbsoluteFill>
  );
};

// その時刻に吹き出しへ出す文字列（sentences があれば文単位で小出し・§4.4）。
function bubbleTextAt(turn: StoryTurn, t: number): string {
  if (turn.sentences && turn.sentences.length) {
    const s =
      turn.sentences.find((x) => x.start <= t && t < x.end) ??
      turn.sentences[turn.sentences.length - 1];
    return s.text;
  }
  return turn.text;
}

// 仮想カメラの目標（s=ズーム / cx,cy=注視点・ステージ正規化座標）。
type Cam = { s: number; cx: number; cy: number };

function targetCam(
  chars: string[],
  anchorOf: Record<string, string>,
  sceneDef: SceneDef
): Cam {
  const ax = (c: string) =>
    (sceneDef.anchors[anchorOf[c] ?? "center"] ?? { x: 0.5 }).x;
  if (chars.length <= 1) {
    // 単独：その人に寄る（背景もアップ）。cy を下げ気味にして胴まで見せ、
    // 顔を画面上側に置く＝足元の吹き出しスペースを確保する。
    return { s: 1.4, cx: chars[0] ? ax(chars[0]) : 0.5, cy: 0.58 };
  }
  // 複数：全員が収まる引き。
  const xs = chars.map(ax);
  return { s: 1.0, cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: 0.5 };
}

export const StoryVideo: React.FC<StoryVideoProps> = ({
  story,
  scenes,
  manifest,
  audio,
  expressions,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;

  const script = story.script;
  const segments = buildSegments(script);
  const active = activeTurnAt(script, t);
  const seg =
    segments.find((s) => s.turns.some((x) => x.id === active.id)) ??
    segments[0];
  const sceneDef = scenes.scenes[active.scene];

  // ── 未登録シーンのフォールバック（§4.1：エラーで止めず分かるように出す） ──
  if (!sceneDef) {
    return (
      <AbsoluteFill
        style={{
          background: "#1b1b1f",
          color: "#fff",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 48,
          fontFamily: "sans-serif",
        }}
      >
        未登録シーン: {active.scene}
      </AbsoluteFill>
    );
  }

  const avScale = sceneDef.scale ?? 1.9;

  // ── 立ち位置は区間中ずっと固定（後から登場する人ぶんも最初から確保） ──
  const roster = segmentRoster(seg);
  // 登場順の自動割当を土台に、シーンの cast（charId→アンカー名）で上書き。
  const anchorOf = { ...assignAnchors(roster), ...(sceneDef.cast ?? {}) };
  const entrance = entranceTimes(seg);
  const exit = exitTimes(seg);
  const exitDir = exitDirs(seg);
  // 表示中＝登場済み かつ（退場していない or 退場スライド中）。
  const presentNow = roster.filter(
    (c) =>
      entrance[c] <= t + 1e-6 &&
      (exit[c] === undefined || t < exit[c] + SLIDE_DUR)
  );

  // ── 仮想カメラ：登場/退場のたびに「寄り↔引き」を滑らかに遷移（カットしない） ──
  const TRANS = 0.8; // 遷移にかける秒数
  // 境界時刻＝登場時刻＋退場時刻（退場で人数が減ればカメラも寄りへ遷移する）。
  const times = [
    ...new Set([
      ...roster.map((c) => entrance[c]),
      ...Object.values(exit),
    ]),
  ].sort((a, b) => a - b);
  let idx = 0;
  for (let i = 0; i < times.length; i++) if (times[i] <= t + 1e-6) idx = i;
  // tb 時点で「画面にいる」キャラ＝登場済み かつ 退場時刻前。
  const presentAt = (tb: number) =>
    roster.filter(
      (c) => entrance[c] <= tb + 1e-6 && (exit[c] === undefined || tb + 1e-6 < exit[c])
    );
  const Tcur = targetCam(presentAt(times[idx]), anchorOf, sceneDef);
  const Tprev = idx > 0 ? targetCam(presentAt(times[idx - 1]), anchorOf, sceneDef) : Tcur;
  const k = idx > 0 ? easeInOutCubic(clamp((t - times[idx]) / TRANS, 0, 1)) : 1;
  // cam(s,cx,cy) → クランプ済みステージ変換(tx,ty,s)。
  // ★「補間してからクランプ」ではなく「クランプ済み変換同士を補間」する。
  //   こうしないと、ズーム率が低い間は pan が clamp で中央に固定され、ズームが進むにつれ
  //   pan が解放されて目標へ動く＝「一度中央に寄ってからズーム」の二段モーションになる。
  //   端点はどちらも有効枠で、その線形補間も有効枠内に収まる（黒縁は出ない）。
  const toTf = (c: Cam) => {
    const s = c.s;
    return {
      tx: clamp(width / 2 - c.cx * width * s, width * (1 - s), 0),
      ty: clamp(height / 2 - c.cy * height * s, height * (1 - s), 0),
      s,
    };
  };

  // 1. 常時スロードリフト（slow-zoom）: sceneDef.camera !== "static" のとき区間中ずっと微速プッシュイン。
  // ★ドリフトはステージ全体に毎フレーム違う scale を掛ける＝全画面再ラスタライズが毎フレーム走る。
  //   Studioプレビューだとこれが重く再生が詰まる（音切れ/リップシンク停止の主因）。
  //   最終renderでだけ効かせ、プレビューでは無効にして軽くする。
  let driftS = 1.0;
  if (sceneDef.camera !== "static" && getRemotionEnvironment().isRendering) {
    const segDur = Math.max(seg.end - seg.start, 0.001);
    const p = clamp((t - seg.start) / segDur, 0, 1);
    driftS = 1 + 0.05 * p;
  }

  // 人数変化の寄り↔引き：キーフレーム(Tprev/Tcur)それぞれをクランプ済み変換にしてから補間。
  const tfPrev = toTf({ ...Tprev, s: Tprev.s * driftS });
  const tfCur = toTf({ ...Tcur, s: Tcur.s * driftS });
  let tf = {
    tx: lerp(tfPrev.tx, tfCur.tx, k),
    ty: lerp(tfPrev.ty, tfCur.ty, k),
    s: lerp(tfPrev.s, tfCur.s, k),
  };

  // 2 & 3. 話者プッシュイン（emphasis）＋リアクション寄り（surprise/panic）。
  // focus のクランプ済み変換へ「変換ごと」補間する＝まっすぐ寄る。
  const isFocusTurn =
    isKnownChar(active.speaker) &&
    (active.emphasis === true ||
      active.expression === "surprise" ||
      active.expression === "panic");
  if (isFocusTurn) {
    const anchorName = anchorOf[active.speaker] ?? "center";
    const speakerAnchor = sceneDef.anchors[anchorName] ?? { x: 0.5 };
    const focusTf = toTf({ s: tf.s + 0.3, cx: speakerAnchor.x, cy: 0.46 });
    // ターン開始から 0.5s でイーズイン、ターン終了 0.5s 前からイーズアウト（台形）。
    const turnDur = active.end - active.start;
    const elapsed = t - active.start;
    const fadeInDur = Math.min(0.5, turnDur * 0.5);
    const fadeOutDur = Math.min(0.5, turnDur * 0.5);
    const inK = clamp(elapsed / fadeInDur, 0, 1);
    const outK = clamp((active.end - t) / fadeOutDur, 0, 1);
    const focusK = easeInOutCubic(Math.min(inK, outK));
    tf = {
      tx: lerp(tf.tx, focusTf.tx, focusK),
      ty: lerp(tf.ty, focusTf.ty, focusK),
      s: lerp(tf.s, focusTf.s, focusK),
    };
  }

  // 4. カメラシェイク（shake===true のターン中、減衰振動オフセットを translate に加算）。
  // s=1.0 など余裕ゼロのシーンでも振幅が出るよう、shake 中はスケールを最低 1.02 に嵩上げ。
  let stageS = tf.s;
  let stageTx = tf.tx;
  let stageTy = tf.ty;
  let shakeX = 0;
  let shakeY = 0;
  if (active.shake) {
    const shakeS = Math.max(tf.s, 1.02);
    const turnDur = Math.max(active.end - active.start, 0.001);
    const elapsed = t - active.start;
    const decayRaw = 1 - clamp(elapsed / turnDur, 0, 1);
    const availX = (shakeS - 1) * width * 0.5;
    const availY = (shakeS - 1) * height * 0.5;
    const maxAmp = Math.min(7, availX, availY);
    const amp = maxAmp * decayRaw;
    shakeX = amp * Math.sin(t * 2 * Math.PI * 16);
    shakeY = amp * Math.sin(t * 2 * Math.PI * 16 * 1.3 + 1);
    // 嵩上げ後の s に合わせて translate を再クランプ（中心維持・範囲が広がるだけ）。
    stageS = shakeS;
    stageTx = clamp(tf.tx, width * (1 - shakeS), 0);
    stageTy = clamp(tf.ty, height * (1 - shakeS), 0);
  }

  // shakeX/Y を加算した最終値をスケール clamp 内に収める（黒縁が出ないよう）。
  const sfx = clamp(stageTx + shakeX, width * (1 - stageS), 0);
  const sfy = clamp(stageTy + shakeY, height * (1 - stageS), 0);

  const stageTransform = `translate(${sfx}px, ${sfy}px) scale(${stageS})`;

  // ── 場面切り替え演出（今は fade-black＝一瞬暗くする。後で方式を追加可能） ──
  // 区間境界＝場面切替なので、各区間の頭で暗→明、次区間の直前で明→暗にして黒で隠す。
  const FADE = 0.3; // 片側の秒数（総遷移 = 2×FADE）
  const segIndex = segments.findIndex((s) => s === seg);
  const nextSeg = segments[segIndex + 1];
  let fadeOpacity = 0;
  if ((sceneDef.transition ?? "fade-black") !== "cut" && seg.start > 1e-6) {
    // この場面に入った直後：黒→明
    fadeOpacity = Math.max(fadeOpacity, 1 - (t - seg.start) / FADE);
  }
  if (nextSeg) {
    const nextTrans = scenes.scenes[nextSeg.scene]?.transition ?? "fade-black";
    if (nextTrans !== "cut") {
      // 次の場面の直前：明→黒
      fadeOpacity = Math.max(fadeOpacity, 1 - (nextSeg.start - t) / FADE);
    }
  }
  fadeOpacity = clamp(fadeOpacity, 0, 1);

  // ── 話者の音量（実音声の波形RMS）→ リップシンク。 ──
  // useAudioData は音声全体（166秒≒16MBのPCM）をブラウザで丸ごと展開するため、
  // Studioプレビューで読込中に null を返し続けリップシンクが止まる/重い。
  // useWindowedAudioData は現フレーム周辺の窓だけ読むので軽く安定する。
  // ※ windowInSeconds は動的変更不可（固定値）。フックは無条件呼び出し。
  const { audioData, dataOffsetInSeconds } = useWindowedAudioData({
    src: staticFile(audio ?? "story-01.wav"),
    frame,
    fps,
    windowInSeconds: 1,
  });
  let speakerAmp = 0;
  if (audio && audioData) {
    const wave = audioData.channelWaveforms[0];
    const sr = audioData.sampleRate;
    // 窓の先頭が dataOffsetInSeconds。現在時刻 t をその窓内のサンプル位置へ変換。
    const center = Math.floor((t - dataOffsetInSeconds) * sr);
    const win = Math.floor(sr / fps); // 1フレーム分の窓
    let sum = 0;
    let n = 0;
    for (let i = center - win / 2; i < center + win / 2; i++) {
      if (i >= 0 && i < wave.length) {
        sum += wave[i] * wave[i];
        n++;
      }
    }
    const rms = Math.sqrt(sum / Math.max(1, n));
    speakerAmp = Math.min(1, rms * LIPSYNC_GAIN);
  }

  const renderAvatar = (charId: string) => {
    const cdef = CHARACTERS[charId];
    if (!cdef) return null;
    const anchorName = anchorOf[charId] ?? "center";
    const anchor = sceneDef.anchors[anchorName] ?? { x: 0.5, y: 1.02 };
    const isSpeaker = charId === active.speaker;
    // 向き: 台本の face 指定 > x座標からの自動（中央を向く）。
    // 立ち絵素材は「画面左向き」が素なので、右を向かせるときだけ反転する。
    // 画面左半分(x<0.5)のキャラは右＝中央向き、右半分は左＝中央向き。x を動かせば向きも自動追従。
    const want: "left" | "right" =
      active.face?.[charId] ?? (anchor.x < 0.5 ? "right" : "left");
    const flip = want === "right";
    // 未知の表情キーは "normal" にフォールバック（組み込み5種のモーションを維持）。
    const resolvedExpr = active.expression ?? "normal";
    const emotion = EXPRESSION_TO_EMOTION[resolvedExpr] ?? EXPRESSION_TO_EMOTION["normal"];

    // expressions.json が渡されていれば該当表情の ExpressionCfg を解決する。
    // 未知キーは "normal" にフォールバック（クラッシュ防止）。
    const exprKey = active.expression ?? "normal";
    const charKey = cdef.avatar; // "zundamon" / "metan"
    const charExprs = expressions?.[charKey];
    const expressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;

    // 全身 or バスト の判定。figure="full" のとき全身パーツを使う。
    const isFull = (sceneDef.figure ?? "bust") === "full";
    const avatarDir = isFull ? `${cdef.avatar}/full` : cdef.avatar;
    const manifestKey = isFull ? `${cdef.avatar}_full` : cdef.avatar;
    const avatarManifest = manifest?.[manifestKey];
    const box = isFull ? fullBoxSize(cdef.avatar) : { w: AVATAR_BOX, h: AVATAR_BOX };

    // 途中で登場するキャラ（区間の頭からいる人ではない）は、自分の側からスライドイン。
    const entered = entrance[charId] ?? seg.start;
    const isInitial = entered <= seg.start + 1e-6;
    let slideOffsetPx = 0;
    if (!isInitial) {
      const sp = clamp((t - entered) / SLIDE_DUR, 0, 1); // 0.5秒で着地
      const e = easeOutCubic(sp);
      const fromXNorm = anchor.x < 0.5 ? -0.35 : 1.35; // 画面外（自分側）から
      slideOffsetPx = (1 - e) * (fromXNorm - anchor.x) * width;
    }
    // 退場：exit 時刻になったら自分の側へスライドアウト（0.5秒で画面外へ）。
    const leaving = exit[charId];
    if (leaving !== undefined && t >= leaving) {
      const sp = clamp((t - leaving) / SLIDE_DUR, 0, 1);
      const e = easeInOutCubic(sp);
      // 退場方向：明示指定があればそちら、無ければ自分の居る側（近い端）へ。
      const dir = exitDir[charId];
      const toXNorm =
        dir === "right" ? 1.35 : dir === "left" ? -0.35 : anchor.x < 0.5 ? -0.35 : 1.35;
      slideOffsetPx = e * (toXNorm - anchor.x) * width;
    }

    return (
      <div
        key={charId}
        style={{
          position: "absolute",
          left: anchor.x * width + slideOffsetPx,
          top: anchor.y * height,
          // anchor.y は立ち絵の「中央(体の中心)」位置（足元ではない）。
          // 全身は背が高く足元基準だと初期表示で画面外に出て配置しづらいため中央基準。
          transform: "translate(-50%, -50%)",
        }}
      >
        <div style={{ transform: `scale(${avScale})`, transformOrigin: "center" }}>
          <Avatar
            dir={avatarDir}
            manifest={avatarManifest}
            fallbackGender={cdef.gender}
            active={isSpeaker}
            activatedAtFrame={Math.round(active.start * fps)}
            amplitude={isSpeaker ? speakerAmp : 0}
            emotion={isSpeaker ? emotion : "normal"}
            emotionAtFrame={Math.round(active.start * fps)}
            expressive={!!cdef.expressive}
            flip={flip}
            popScale={false}
            boxWidth={box.w}
            boxHeight={box.h}
            expressionCfg={isSpeaker ? expressionCfg : (charExprs?.["normal"] ?? null)}
          />
        </div>
      </div>
    );
  };

  // モブ（1枚絵）描画：話者がモブのとき、その間だけ立たせる（フェードイン）。
  // 画像が無ければ onError で非表示にし、render を壊さない（素材未配置でも安全）。
  const renderMob = (mobId: string) => {
    const m = MOBS[mobId];
    if (!m) return null;
    // 配置はシーンデータ(scene.mobs[mob])優先 → MobDef既定 → シーン既定。
    const place = sceneDef.mobs?.[mobId];
    if (place?.hidden) return null; // 立ち絵を非表示（チャット/音声のみ登場）
    const a = place ?? m.anchor ?? sceneDef.mobAnchor ?? { x: 0.5, y: 1.0 };
    const sc = place?.scale ?? m.scale ?? 1;
    const h = (sceneDef.mobHeight ?? 760) * sc;
    const inP = clamp((t - active.start) / 0.3, 0, 1); // 話し始めでフェードイン
    return (
      <div
        key={`mob-${mobId}`}
        style={{
          position: "absolute",
          left: a.x * width,
          top: a.y * height,
          transform: `translate(-50%, -100%) scaleX(${m.flip ? -1 : 1})`,
          transformOrigin: "bottom center",
          opacity: inP,
        }}
      >
        <img
          src={staticFile(mobImage(mobId, active.expression))}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
          style={{ display: "block", height: h, width: "auto" }}
        />
      </div>
    );
  };

  // 吹き出しは「移動後の最終カメラ(Tcur)」基準で位置を決め、移動中も固定表示する。
  // → 移動中に位置が動かない＝変形・ガタつき無し・途中で消えない。
  const stx = clamp(
    width / 2 - Tcur.cx * width * Tcur.s,
    width * (1 - Tcur.s),
    0
  );
  // 1つの吹き出しを描く（話者の足元・話者色）。
  // 下端を固定(translateY -100%)して上に伸ばす＝行が増えても画面下にはみ出ない。
  const renderBubble = (turn: StoryTurn, key: string) => {
    const aName = anchorOf[turn.speaker] ?? "center";
    const a = sceneDef.anchors[aName] ?? { x: 0.5, y: 1.02 };
    // 横幅が広いので、左右端で見切れないよう中心xを内側にクランプする。
    const sx = clamp(stx + a.x * width * Tcur.s, width * 0.31, width * 0.69);
    const color = CHARACTERS[turn.speaker]?.bubbleColor ?? DEFAULT_BUBBLE_COLOR;
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: sx,
          top: height * 0.95, // 吹き出しの「下端」をこの位置に置き、上方向に伸ばす
          transform: "translate(-50%, -100%)",
          maxWidth: width * 0.56, // 横幅広め＝2行に収まりやすく
          background: "#ffffff",
          color: "#1b1b1f",
          padding: "18px 30px",
          borderRadius: 16,
          border: `5px solid ${color}`,
          fontSize: 42,
          lineHeight: 1.35,
          fontWeight: 700,
          fontFamily: "sans-serif",
          boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
          textAlign: "center",
        }}
      >
        {bubbleTextAt(turn, t)}
      </div>
    );
  };

  // 直前のセリフ（別話者・同一シーン）は、相手が喋り出してから OVERLAP 秒だけ残して消す。
  const OVERLAP = 0.6;
  const activeIdx = script.findIndex((x) => x.id === active.id);
  const prevTurn = activeIdx > 0 ? script[activeIdx - 1] : null;
  const showPrev =
    !!prevTurn &&
    prevTurn.scene === active.scene &&
    prevTurn.speaker !== active.speaker &&
    t - active.start < OVERLAP;

  // ── 回想（flashback）演出 ────────────────────────────────────
  const isFlashback = !!active.flashback;

  // flashback が切り替わる境界時刻を全ターンから列挙する。
  // 境界 = 前のターンと flashback 状態が違う最初のターンの start。
  type FbBoundary = { at: number; entering: boolean; telop?: string };
  const fbBoundaries: FbBoundary[] = [];
  for (let i = 1; i < script.length; i++) {
    const prev = script[i - 1];
    const cur = script[i];
    if (!!prev.flashback !== !!cur.flashback) {
      fbBoundaries.push({
        at: cur.start,
        entering: !!cur.flashback,
        // 回想に入るターンと戻るターンに telop を付ける。
        telop: cur.telop,
      });
    }
  }

  // 現在時刻から最も近い境界を探す（白ディゾルブの基準）。
  const nearestBoundary = fbBoundaries.reduce<FbBoundary | null>((best, b) => {
    if (best === null) return b;
    return Math.abs(t - b.at) < Math.abs(t - best.at) ? b : best;
  }, null);

  // 白ディゾルブのオーバーレイ opacity（三角波: 境界中心で1、±FB_DISSOLVE_SEC で 0）。
  let whiteFadeOpacity = 0;
  if (nearestBoundary !== null) {
    const dt = Math.abs(t - nearestBoundary.at);
    if (dt < FB_DISSOLVE_SEC) {
      whiteFadeOpacity = clamp(1 - dt / FB_DISSOLVE_SEC, 0, 1);
    }
  }
  // 白ディゾルブが出る間は黒フェードを抑制する（両立させると汚くなるため）。
  const suppressBlackFade = whiteFadeOpacity > 0;

  // グレインのシードをフレームごとに変えてちらつきを出す（軽量: 数px の位置オフセット）。
  const grainOffsetX = (frame * 7) % 64;
  const grainOffsetY = (frame * 13) % 64;

  // テロップ表示: 境界から FB_TELOP_SEC の間、フェードイン/アウトして出す。
  // 回想に入る境界: その境界の telop を使う。
  // 「現在」へ戻る境界: 戻り先のターンの telop。
  // 回想中は左上に「前日」ラベルを固定表示（大きめ・回想の間ずっと）。入った所でフェードイン。
  // 「現在」へ戻る境界など回想以外の telop は、従来どおり短時間だけ出す。
  let telopText: string | null = null;
  let telopOpacity = 0;
  if (isFlashback) {
    const entered = [...fbBoundaries]
      .filter((b) => b.entering && b.at <= t + 1e-6)
      .pop();
    if (entered?.telop) {
      telopText = entered.telop;
      telopOpacity = clamp((t - entered.at) / FB_TELOP_FADE, 0, 1);
    }
  } else if (nearestBoundary?.telop && !nearestBoundary.entering) {
    const dt = t - nearestBoundary.at;
    if (dt >= -FB_TELOP_FADE && dt < FB_TELOP_SEC) {
      telopText = nearestBoundary.telop;
      if (dt < FB_TELOP_FADE) {
        telopOpacity = clamp((dt + FB_TELOP_FADE) / FB_TELOP_FADE, 0, 1);
      } else if (dt >= FB_TELOP_SEC - FB_TELOP_FADE) {
        telopOpacity = clamp((FB_TELOP_SEC - dt) / FB_TELOP_FADE, 0, 1);
      } else {
        telopOpacity = 1;
      }
    }
  }

  // 回想中はステージに彩度ダウン＋輝度微加の CSS filter を掛ける。
  const stageFilter = isFlashback
    ? `saturate(${FB_SATURATE}) brightness(${FB_BRIGHTNESS})`
    : undefined;

  // ── PC画面インサート フェード計算 ─────────────────────────
  // INSERT_FADE: フェードイン/アウトの片側秒数。
  const INSERT_FADE = 0.2;
  const activeInsert = active.insert ?? null;
  // 前のターンの insert を取得（連続同種かどうかの判定）。
  const activeIdx2 = script.findIndex((x) => x.id === active.id);
  const prevInsertKind = activeIdx2 > 0 ? (script[activeIdx2 - 1].insert?.kind ?? null) : null;
  // 次のターンの insert を取得（消え際フェードアウト用）。
  const nextTurn2 = activeIdx2 < script.length - 1 ? script[activeIdx2 + 1] : null;
  const nextInsertKind = nextTurn2?.insert?.kind ?? null;
  let insertOpacity = 0;
  if (activeInsert) {
    // フェードイン: ターン開始直後。直前ターンと同種、または最初のターン（冒頭がインサート）
    // なら即 1（背景を透けさせない＝冒頭の一瞬オフィスが見える問題の解消）。
    const isContinuous = prevInsertKind === activeInsert.kind || activeIdx2 === 0;
    const fadeInProgress = isContinuous
      ? 1
      : clamp((t - active.start) / INSERT_FADE, 0, 1);
    // フェードアウト: ターン終了直前。次のターンも同種なら即消えない（そのまま 1）。
    const isContinuousNext = nextInsertKind === activeInsert.kind;
    const fadeOutProgress = isContinuousNext
      ? 1
      : clamp((active.end - t) / INSERT_FADE, 0, 1);
    insertOpacity = Math.min(fadeInProgress, fadeOutProgress);
  }

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {audio ? <Audio src={staticFile(audio)} /> : null}
      {/* ステージ（背景＋キャラ＋前景を1枚として仮想カメラで撮る） */}
      <AbsoluteFill
        style={{
          transform: stageTransform,
          transformOrigin: "0 0",
          filter: stageFilter,
        }}
      >
        {/* 背景（back） */}
        <Img
          src={staticFile(sceneDef.bg)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />

        {/* キャラ（back と front の間） */}
        {presentNow.map(renderAvatar)}

        {/* モブ（話者がモブのとき1枚絵を立たせる。素材未配置なら自動で非表示） */}
        {isMob(active.speaker) ? renderMob(active.speaker) : null}

        {/* 前景（front）。指定があれば back→キャラ→front の順で机等の手前要素を重ねる。 */}
        {sceneDef.front ? (
          <Img
            src={staticFile(sceneDef.front)}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              pointerEvents: "none",
            }}
          />
        ) : null}
      </AbsoluteFill>

      {/* 回想中グレイン（タイル状ノイズを低opacity＋毎フレームずれ）。回想中のみ表示。 */}
      {isFlashback ? (
        <AbsoluteFill
          style={{
            backgroundImage: `url(${staticFile("noise.png")})`,
            backgroundRepeat: "repeat",
            backgroundPosition: `${grainOffsetX}px ${grainOffsetY}px`,
            backgroundSize: "64px 64px",
            opacity: FB_GRAIN_OPACITY,
            pointerEvents: "none",
            mixBlendMode: "luminosity",
          }}
        />
      ) : null}

      {/* PC画面インサート（ステージより前面・吹き出しより後面）。
          z順: ステージ → インサート → 吹き出し */}
      {activeInsert && insertOpacity > 0 ? (
        <InsertOverlay insert={activeInsert} opacity={insertOpacity} />
      ) : null}

      {/* 吹き出し。基本は話者の1つ。話者交代の直後だけ直前のセリフを少し残す（一瞬2つ）。
          位置は最終カメラ基準で固定＝移動中も消えず動かない。インサートより前面。 */}
      {/* セリフがチャット/AIチャット画面に出ているターンは吹き出しを出さない。
          前後ターンも各自の insert 種別で判定し、遷移時のチラ見え漏れを防ぐ。 */}
      {showPrev && !lineShownInInsert(prevTurn as StoryTurn)
        ? renderBubble(prevTurn as StoryTurn, "bubble-prev")
        : null}
      {!lineShownInInsert(active) ? renderBubble(active, "bubble-active") : null}

      {/* テロップ（回想境界付近：「― 前日 ―」「― 現在 ―」等）。ローワーサード風の帯。 */}
      {telopText && telopOpacity > 0 ? (
        <AbsoluteFill
          style={{
            alignItems: "flex-start",
            justifyContent: "flex-start",
            padding: `${Math.round(height * 0.06)}px ${Math.round(width * 0.045)}px`,
            pointerEvents: "none",
          }}
        >
          <div
            style={{
              background: "rgba(8, 8, 8, 0.6)",
              color: "#f4f0e8",
              fontSize: 84,
              fontWeight: 700,
              fontFamily: "sans-serif",
              letterSpacing: "0.12em",
              padding: "18px 56px",
              borderRadius: 8,
              borderLeft: "10px solid #f4f0e8",
              boxShadow: "0 6px 20px rgba(0,0,0,0.4)",
              opacity: telopOpacity,
            }}
          >
            {telopText}
          </div>
        </AbsoluteFill>
      ) : null}

      {/* 場面切り替えの暗転（fade-black）。白ディゾルブ中は抑制。 */}
      {fadeOpacity > 0 && !suppressBlackFade ? (
        <AbsoluteFill
          style={{ background: "#000", opacity: fadeOpacity, pointerEvents: "none" }}
        />
      ) : null}

      {/* 白ディゾルブ（flashback境界の出入り）。黒fadeより手前に重ねる。 */}
      {whiteFadeOpacity > 0 ? (
        <AbsoluteFill
          style={{ background: "#fff", opacity: whiteFadeOpacity, pointerEvents: "none" }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
