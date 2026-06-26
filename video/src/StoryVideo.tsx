import {
  AbsoluteFill,
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
  panic: "surprise", // 焦り：暫定（驚きで代用）
};

export type StorySentence = { text: string; start: number; end: number };

export type StoryTurn = {
  id: string;
  speaker: string;
  text: string;
  scene: string;
  expression?: StoryExpression;
  enter?: string[];
  start: number;
  end: number;
  sentences?: StorySentence[];
};

export type StoryScript = { title?: string; script: StoryTurn[] };

type Anchor = { x: number; y: number };

export type SceneDef = {
  label?: string;
  bg: string;
  front?: string | null;
  shot?: "solo" | "duo" | "split";
  camera?: "static" | "slow-zoom";
  scale?: number; // 立ち絵の拡大率（既定 1.9）
  anchors: Record<string, Anchor>;
};

export type SceneLibrary = { scenes: Record<string, SceneDef> };

export type CharDef = {
  avatar: string; // パーツ立ち絵フォルダ名
  gender: Gender; // フォールバック用
  expressive?: boolean;
};

// 主役キャラ定義（Phase 1 はインライン。将来はライブラリ化）。
const CHARACTERS: Record<string, CharDef> = {
  zundamon: { avatar: "zundamon", gender: "male", expressive: true },
  metan: { avatar: "metan", gender: "female", expressive: false },
};

type Manifest = Record<string, Record<string, string>>;

export type StoryVideoProps = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Manifest;
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

export const StoryVideo: React.FC<StoryVideoProps> = ({
  story,
  scenes,
  manifest,
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

  // ── 背景の Ken Burns（slow-zoom はゆっくり寄る / static は静止） ──
  const segDur = Math.max(0.001, seg.end - seg.start);
  const p = interpolate(t, [seg.start, seg.end], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const isZoom = (sceneDef.camera ?? "slow-zoom") === "slow-zoom";
  const bgScale = isZoom ? 1.0 + 0.06 * p : 1.0;
  const bgX = isZoom ? interpolate(p, [0, 1], [-1.2, 1.2]) : 0; // ごく弱いパン(%)
  const bgTransform = `scale(${bgScale}) translateX(${bgX}%)`;

  // ── 登場キャラと立ち位置 ──
  const present = presentChars(seg, active);
  const anchorOf = assignAnchors(present);
  const avScale = sceneDef.scale ?? 1.9;

  // ── 吹き出しテキスト（sentences があれば文単位で小出し・§4.4） ──
  let bubbleText = active.text;
  if (active.sentences && active.sentences.length) {
    const s =
      active.sentences.find((x) => x.start <= t && t < x.end) ??
      active.sentences[active.sentences.length - 1];
    bubbleText = s.text;
  }

  // ── 話者のフェイク音量（Phase1は音声無し→口パクに生気だけ与える） ──
  const speakerAmp = 0.16 + 0.1 * Math.sin(frame * 0.8);

  const renderAvatar = (charId: string) => {
    const cdef = CHARACTERS[charId];
    if (!cdef) return null;
    const anchorName = anchorOf[charId] ?? "center";
    const anchor = sceneDef.anchors[anchorName] ?? { x: 0.5, y: 1.02 };
    const isSpeaker = charId === active.speaker;
    // 右側のキャラは中央を向くよう左右反転（素材の向きに応じて調整）。
    const flip = anchorName === "right";
    const emotion = EXPRESSION_TO_EMOTION[active.expression ?? "normal"];
    return (
      <div
        key={charId}
        style={{
          position: "absolute",
          left: anchor.x * width,
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
          />
        </div>
      </div>
    );
  };

  // 吹き出しは話者の足元（anchor.x を基準に下寄り）。
  const speakerAnchorName = anchorOf[active.speaker] ?? "center";
  const speakerAnchor =
    sceneDef.anchors[speakerAnchorName] ?? { x: 0.5, y: 1.02 };

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {/* 背景（back） */}
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={staticFile(sceneDef.bg)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: bgTransform,
            transformOrigin: "center center",
          }}
        />
      </AbsoluteFill>

      {/* キャラ（back と front の間） */}
      {present.map(renderAvatar)}

      {/* 前景（front）。指定があれば back→キャラ→front の順で机等の手前要素を重ねる。 */}
      {sceneDef.front ? (
        <AbsoluteFill style={{ overflow: "hidden", pointerEvents: "none" }}>
          <Img
            src={staticFile(sceneDef.front)}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              transform: bgTransform,
              transformOrigin: "center center",
            }}
          />
        </AbsoluteFill>
      ) : null}

      {/* 吹き出し（話者の足元・小型ボックス・名前ラベルなし） */}
      <div
        style={{
          position: "absolute",
          left: speakerAnchor.x * width,
          top: height * 0.66,
          transform: "translateX(-50%)",
          maxWidth: width * 0.42,
          background: "rgba(255,255,255,0.96)",
          color: "#1b1b1f",
          padding: "16px 24px",
          borderRadius: 16,
          fontSize: 34,
          lineHeight: 1.35,
          fontWeight: 700,
          fontFamily: "sans-serif",
          boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
          textAlign: "center",
        }}
      >
        {bubbleText}
      </div>
    </AbsoluteFill>
  );
};
