import React from "react";
import {AbsoluteFill, Img, staticFile} from "remotion";
import type {CameraFrameV2, ComicBubbleTypeV2, StageTurnV2} from "./stage-v2";
import {stageTransformValues} from "./StageVideoV2";

const FULL_FRAME: CameraFrameV2 = {cx: 0.5, cy: 0.5, width: 1};
const DEFAULT_ZOOM_FRAME: CameraFrameV2 = {cx: 0.5, cy: 0.5, width: 0.7};

function easeInOutCubic(value: number) {
  const t = Math.max(0, Math.min(1, value));
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function lerpFrame(a: CameraFrameV2, b: CameraFrameV2, k: number): CameraFrameV2 {
  return {
    cx: a.cx + (b.cx - a.cx) * k,
    cy: a.cy + (b.cy - a.cy) * k,
    width: a.width + (b.width - a.width) * k,
  };
}

export type ComicVisualBubble = {
  type: ComicBubbleTypeV2;
  x: number;
  y: number;
  width: number;
  fontSize?: number;
  text: string;
  /** 吹き出しを出したターンの話者。枠色の解決に使う。 */
  speaker: string;
  isCurrent: boolean;
};

export type ComicVisual = {
  image: string;
  bubbles: ComicVisualBubble[];
  cameraFrame: CameraFrameV2;
};

// scriptで隣接する2ターンが同一漫画シーン（連続comic・同一image・同一scene）かを判定する。
function sameComicScene(script: StageTurnV2[], a: number, b: number) {
  const ta = script[a];
  const tb = script[b];
  if (!ta || !tb) return false;
  const da = ta.displayMode;
  const db = tb.displayMode;
  if (da?.kind !== "comic" || db?.kind !== "comic") return false;
  return da.comic.image === db.comic.image && ta.scene === tb.scene;
}

// 同一漫画シーン内で、そのターンまでに表示される吹き出しの蓄積を遡って収集する。
function accumulateComicBubbles(
  script: StageTurnV2[],
  index: number,
): {source: number; bubble: NonNullable<Extract<StageTurnV2["displayMode"], {kind: "comic"}>["comic"]["bubble"]>}[] {
  const turn = script[index];
  const display = turn?.displayMode;
  if (display?.kind !== "comic") return [];
  const bubble = display.comic.bubble;
  const hasText = !!turn.text.trim();
  const own = bubble && hasText ? [{source: index, bubble}] : [];
  if (bubble?.keepPrevious && index > 0 && sameComicScene(script, index, index - 1)) {
    return [...accumulateComicBubbles(script, index - 1), ...own];
  }
  return own;
}

/**
 * 漫画ターンの表示状態（画像・蓄積吹き出し・補間済みカメラ枠）を解決する。
 * 状態と時間の解決はここで完結し、ComicDisplayは受け取った値を描くだけにする。
 */
export function resolveComicVisual(
  script: StageTurnV2[],
  turnIndex: number,
  seconds: number,
  opts?: {atEnd?: boolean},
): ComicVisual | null {
  const turn = script[turnIndex];
  const display = turn?.displayMode;
  if (display?.kind !== "comic") return null;
  const comic = display.comic;

  const bubbles: ComicVisualBubble[] = accumulateComicBubbles(script, turnIndex).map((item) => ({
    type: item.bubble.type,
    x: item.bubble.x,
    y: item.bubble.y,
    width: item.bubble.width,
    fontSize: item.bubble.fontSize,
    text: script[item.source].text,
    speaker: script[item.source].speaker,
    isCurrent: item.source === turnIndex,
  }));

  const cameraType = comic.camera?.type ?? "fixed";
  const target = comic.camera?.frame ?? DEFAULT_ZOOM_FRAME;
  const start = turn.start;
  const end = turn.end;
  const progress = opts?.atEnd
    ? 1
    : typeof start === "number" && typeof end === "number" && end > start
      ? easeInOutCubic(clamp01((seconds - start) / (end - start)))
      : 0;

  let cameraFrame: CameraFrameV2;
  if (cameraType === "zoomIn") {
    cameraFrame = lerpFrame(FULL_FRAME, target, progress);
  } else if (cameraType === "zoomOut") {
    cameraFrame = lerpFrame(target, FULL_FRAME, progress);
  } else {
    cameraFrame = comic.camera?.frame ?? FULL_FRAME;
  }

  return {image: comic.image, bubbles, cameraFrame};
}

export type ComicBubbleSettings = {
  fontSize: number;
  fontFamily: string;
  textColor: string;
  bgColor: string;
  borderWidth: number;
  radius: number;
};

export type ComicRenderBubble = {
  type: ComicBubbleTypeV2;
  x: number;
  y: number;
  width: number;
  fontSize?: number;
  text: string;
  /** 呼び出し側が話者から解決した枠色。 */
  borderColor: string;
};

// clip-pathでは枠線が描けないため、叫び枠はギザギザ多角形を外側=枠色・内側=白で二重に敷く。
function shoutClipPath() {
  const teeth = 8;
  const depth = 6;
  const pts: string[] = [];
  const push = (x: number, y: number) => pts.push(`${x.toFixed(1)}% ${y.toFixed(1)}%`);
  for (let i = 0; i <= teeth; i += 1) push((100 * i) / teeth, i % 2 === 0 ? 0 : depth);
  for (let i = 1; i <= teeth; i += 1) push(i % 2 === 0 ? 100 : 100 - depth, (100 * i) / teeth);
  for (let i = 1; i <= teeth; i += 1) push(100 - (100 * i) / teeth, i % 2 === 0 ? 100 : 100 - depth);
  for (let i = 1; i < teeth; i += 1) push(i % 2 === 0 ? 0 : depth, 100 - (100 * i) / teeth);
  return `polygon(${pts.join(", ")})`;
}

const SHOUT_CLIP = shoutClipPath();
const BUBBLE_SHADOW = "0 6px 18px rgba(0,0,0,.35)";

function ComicBubble({
  bubble,
  width,
  height,
  settings,
}: {
  bubble: ComicRenderBubble;
  width: number;
  height: number;
  settings: ComicBubbleSettings;
}) {
  const fontSize = bubble.fontSize ?? settings.fontSize;
  const wrapperStyle: React.CSSProperties = {
    position: "absolute",
    left: bubble.x * width,
    top: bubble.y * height,
    width: bubble.width * width,
    transform: "translate(-50%, -50%)",
    boxSizing: "border-box",
  };
  const textStyle: React.CSSProperties = {
    fontSize,
    fontFamily: settings.fontFamily,
    lineHeight: 1.3,
    whiteSpace: "pre-wrap",
    overflowWrap: "anywhere",
    textAlign: "center",
  };

  if (bubble.type === "shout") {
    return (
      <div style={wrapperStyle}>
        <div style={{position: "relative", clipPath: SHOUT_CLIP, background: bubble.borderColor}}>
          <div style={{position: "absolute", inset: 3, clipPath: SHOUT_CLIP, background: "#ffffff"}} />
          <div style={{...textStyle, position: "relative", padding: "22px 34px", color: settings.textColor, fontWeight: 900}}>{bubble.text}</div>
        </div>
      </div>
    );
  }

  if (bubble.type === "narration") {
    return (
      <div style={wrapperStyle}>
        <div style={{...textStyle, boxSizing: "border-box", padding: "14px 24px", borderRadius: 4, background: "rgba(0,0,0,0.65)", color: "#ffffff"}}>{bubble.text}</div>
      </div>
    );
  }

  const isThought = bubble.type === "thought";
  return (
    <div style={wrapperStyle}>
      <div style={{
        ...textStyle,
        boxSizing: "border-box",
        padding: "14px 28px",
        borderRadius: isThought ? Math.max(28, settings.radius) : settings.radius,
        background: settings.bgColor,
        color: settings.textColor,
        border: `${settings.borderWidth}px ${isThought ? "dashed" : "solid"} ${bubble.borderColor}`,
        fontWeight: 700,
        boxShadow: BUBBLE_SHADOW,
      }}>{bubble.text}</div>
    </div>
  );
}

export function ComicDisplay({
  width,
  height,
  image,
  bubbles,
  cameraFrame,
  settings,
}: {
  width: number;
  height: number;
  image: string;
  bubbles: ComicRenderBubble[];
  cameraFrame: CameraFrameV2;
  settings: ComicBubbleSettings;
}) {
  const values = stageTransformValues(width, height, cameraFrame);
  const transform = values ? `translate(${values.tx}px, ${values.ty}px) scale(${values.scale})` : undefined;
  return (
    <AbsoluteFill style={{background: "#111", overflow: "hidden"}}>
      <div style={{position: "absolute", left: 0, top: 0, width, height, transform, transformOrigin: "0 0"}}>
        <Img src={staticFile(image)} style={{position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover"}} />
        {bubbles.map((bubble, index) => (
          <ComicBubble key={index} bubble={bubble} width={width} height={height} settings={settings} />
        ))}
      </div>
    </AbsoluteFill>
  );
}
