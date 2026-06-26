import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Avatar } from "./Avatar";
import type { Emotion, Gender } from "./types";

// ───────────────────────────────────────────────────────────
// ストーリー調 会話劇動画（新ツール Phase 1）の描画。
// 既存の DialogueVideo には触れず、立ち絵 Avatar だけ流用する。
// 入力は手書きの story-sample.json / story-scenes.json（固定入力）。
// 仕様: docs/next-animation-editor-spec.md §4。
// ───────────────────────────────────────────────────────────

// 台本の表情語彙（§4.3）。立ち絵 Emotion へマップする。
// trouble/panic は専用パーツ未配置のため当面は近い既存表情へ寄せる（Phase 5 でパーツ追加）。
export type StoryExpression =
  | "normal"
  | "happy"
  | "surprise"
  | "trouble"
  | "panic";

const EXPRESSION_TO_EMOTION: Record<StoryExpression, Emotion> = {
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

type Manifest = Record<string, Record<string, string>>;

export type StoryVideoProps = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Manifest;
  audio?: string; // 音声ファイル（public配下・任意）
};

// 立ち絵 1 体ぶんの素の箱サイズ（Avatar の wrap と同じ）。
const AVATAR_BOX = 445;

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
function presentChars(seg: Segment, activeTurn: StoryTurn): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (!order.includes(c)) order.push(c);
    }
    if (turn.id === activeTurn.id) break;
  }
  // enter 指定が無くても、話者は必ず画面にいる。
  if (!order.includes(activeTurn.speaker)) order.push(activeTurn.speaker);
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
function segmentRoster(seg: Segment): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) if (!order.includes(c)) order.push(c);
    if (!order.includes(turn.speaker)) order.push(turn.speaker);
  }
  return order;
}

// 各キャラが segment 内で初めて画面に出る時刻（秒）。
function entranceTimes(seg: Segment): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) if (!(c in e)) e[c] = turn.start;
    if (!(turn.speaker in e)) e[turn.speaker] = turn.start;
  }
  return e;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const easeInOutCubic = (x: number) =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);

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
  const presentNow = roster.filter((c) => entrance[c] <= t + 1e-6);

  // ── 仮想カメラ：登場のたびに「寄り↔引き」を滑らかに遷移（カットしない） ──
  const TRANS = 0.8; // 遷移にかける秒数
  const times = [...new Set(roster.map((c) => entrance[c]))].sort((a, b) => a - b);
  let idx = 0;
  for (let i = 0; i < times.length; i++) if (times[i] <= t + 1e-6) idx = i;
  const presentAt = (tb: number) => roster.filter((c) => entrance[c] <= tb + 1e-6);
  const Tcur = targetCam(presentAt(times[idx]), anchorOf, sceneDef);
  const Tprev = idx > 0 ? targetCam(presentAt(times[idx - 1]), anchorOf, sceneDef) : Tcur;
  const k = idx > 0 ? easeInOutCubic(clamp((t - times[idx]) / TRANS, 0, 1)) : 1;
  const cam: Cam = {
    s: lerp(Tprev.s, Tcur.s, k),
    cx: lerp(Tprev.cx, Tcur.cx, k),
    cy: lerp(Tprev.cy, Tcur.cy, k),
  };
  // ステージ変換（端が見切れて黒が出ないようクランプ）。
  const tx = clamp(width / 2 - cam.cx * width * cam.s, width * (1 - cam.s), 0);
  const ty = clamp(height / 2 - cam.cy * height * cam.s, height * (1 - cam.s), 0);
  const stageTransform = `translate(${tx}px, ${ty}px) scale(${cam.s})`;

  // ── 話者のフェイク音量（Phase1は音声無し→口パクに生気だけ与える） ──
  const speakerAmp = 0.16 + 0.1 * Math.sin(frame * 0.8);

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
    const emotion = EXPRESSION_TO_EMOTION[active.expression ?? "normal"];

    // 途中で登場するキャラ（区間の頭からいる人ではない）は、自分の側からスライドイン。
    const entered = entrance[charId] ?? seg.start;
    const isInitial = entered <= seg.start + 1e-6;
    let slideOffsetPx = 0;
    if (!isInitial) {
      const sp = clamp((t - entered) / 0.5, 0, 1); // 0.5秒で着地
      const e = easeOutCubic(sp);
      const fromXNorm = anchor.x < 0.5 ? -0.35 : 1.35; // 画面外（自分側）から
      slideOffsetPx = (1 - e) * (fromXNorm - anchor.x) * width;
    }

    return (
      <div
        key={charId}
        style={{
          position: "absolute",
          left: anchor.x * width + slideOffsetPx,
          top: anchor.y * height,
          transform: "translate(-50%, -100%)",
        }}
      >
        <div style={{ transform: `scale(${avScale})`, transformOrigin: "bottom center" }}>
          <Avatar
            dir={cdef.avatar}
            manifest={manifest?.[cdef.avatar]}
            fallbackGender={cdef.gender}
            active={isSpeaker}
            activatedAtFrame={Math.round(active.start * fps)}
            amplitude={isSpeaker ? speakerAmp : 0}
            emotion={isSpeaker ? emotion : "normal"}
            emotionAtFrame={Math.round(active.start * fps)}
            expressive={!!cdef.expressive}
            flip={flip}
            popScale={false}
          />
        </div>
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

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {audio ? <Audio src={staticFile(audio)} /> : null}
      {/* ステージ（背景＋キャラ＋前景を1枚として仮想カメラで撮る） */}
      <AbsoluteFill style={{ transform: stageTransform, transformOrigin: "0 0" }}>
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

      {/* 吹き出し。基本は話者の1つ。話者交代の直後だけ直前のセリフを少し残す（一瞬2つ）。
          位置は最終カメラ基準で固定＝移動中も消えず動かない。 */}
      {showPrev ? renderBubble(prevTurn as StoryTurn, "bubble-prev") : null}
      {renderBubble(active, "bubble-active")}
    </AbsoluteFill>
  );
};
