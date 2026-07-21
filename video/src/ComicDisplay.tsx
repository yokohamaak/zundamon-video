import React from "react";
import {AbsoluteFill, Img, staticFile} from "remotion";
import type {CameraFrameV2, ComicBubbleAlignV2, ComicBubbleFontV2, ComicBubbleRegistry, ComicBubbleTypeV2, StageTurnV2} from "./stage-v2";
import {stageTransformValues} from "./StageVideoV2";

// 縦書き吹き出しの既定の列長（画面高比）。エディタ側の同名定数と一致させる。
const COMIC_BUBBLE_DEFAULT_HEIGHT = 0.25;

function comicFontFamily(font: ComicBubbleFontV2 | undefined, fallback: string) {
  switch (font) {
    case "mincho": return '"Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif';
    case "gothic": return '"Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif';
    case "rounded": return '"Hiragino Maru Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif';
    case "handwriting": return '"Yusei Magic", "Hiragino Maru Gothic ProN", sans-serif';
    default: return fallback;
  }
}

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
  height?: number;
  fontSize?: number;
  font?: ComicBubbleFontV2;
  align?: ComicBubbleAlignV2;
  textOffsetX?: number;
  textOffsetY?: number;
  flipX?: boolean;
  text: string;
  /** 吹き出しを出したターンの話者。枠色の解決に使う。 */
  speaker: string;
  isCurrent: boolean;
  /** type="svg"の場合のみ使う、comic_bubbles.json 登録済み素材のID。 */
  svgShapeId?: string;
  color?: string;
  fillColor?: string;
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
    height: item.bubble.height,
    fontSize: item.bubble.fontSize,
    font: item.bubble.font,
    align: item.bubble.align,
    textOffsetX: item.bubble.textOffsetX,
    textOffsetY: item.bubble.textOffsetY,
    flipX: item.bubble.flipX,
    text: script[item.source].text,
    speaker: script[item.source].speaker,
    isCurrent: item.source === turnIndex,
    svgShapeId: item.bubble.svgShapeId,
    color: item.bubble.color,
    fillColor: item.bubble.fillColor,
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
  height?: number;
  fontSize?: number;
  font?: ComicBubbleFontV2;
  align?: ComicBubbleAlignV2;
  textOffsetX?: number;
  textOffsetY?: number;
  flipX?: boolean;
  text: string;
  /** 呼び出し側が話者から解決した枠色。 */
  borderColor: string;
  /** type="svg"の場合のみ使う。 */
  svgShapeId?: string;
  fillColor?: string;
};

const BUBBLE_SHADOW = "0 6px 18px rgba(0,0,0,.35)";

function ComicBubble({
  bubble,
  width,
  height,
  settings,
  comicBubbles,
}: {
  bubble: ComicRenderBubble;
  width: number;
  height: number;
  settings: ComicBubbleSettings;
  comicBubbles?: ComicBubbleRegistry;
}) {
  const fontSize = bubble.fontSize ?? settings.fontSize;
  const lineHeight = 1.4;
  // 縦書き: 列長(inline方向)を height比×キャンバス高で固定し、横は列数から実幅を明示計算する。
  const columnHeight = (bubble.height ?? COMIC_BUBBLE_DEFAULT_HEIGHT) * height;
  const padY = bubble.type === "svg" ? 28 : 20;
  const padX = bubble.type === "svg" ? 32 : bubble.type === "narration" ? 22 : 24;
  // Chrome の直交フローでは vertical-rl + max-content が先頭列ぶん過小評価し、
  // 先頭列(右端)が枠外に出て描画されない。列数から必要幅を明示計算して回避する。
  const columnAdvance = fontSize * lineHeight; // 縦書き1列の太さ(ブロック方向)≈fontSize×line-height
  const usableColumn = Math.max(fontSize, columnHeight - padY * 2);
  const charsPerColumn = Math.max(1, Math.floor(usableColumn / fontSize)); // 1文字送り≈fontSize(全角前提)
  const columnCount = bubble.text
    .split("\n")
    .reduce((sum, line) => sum + Math.max(1, Math.ceil(Array.from(line).length / charsPerColumn)), 0);
  // 禁則処理などで実列数が見積りより増える場合に備え1/4列ぶん余白を足す。過大評価は許容・欠けは不許容。
  const textColumnWidth = Math.ceil(columnCount * columnAdvance + columnAdvance * 0.25);
  // vertical-rl の実際の文字ブロックは右端に寄る。列間隔を含む文字の見た目の幅と、
  // 描画領域の余裕ぶんを分けて扱わないと、center 指定でも文字が右へずれる。
  const textBlockWidth = fontSize + Math.max(0, columnCount - 1) * columnAdvance;
  const textRightInset = textColumnWidth - textBlockWidth;
  const resolvedWidth = textColumnWidth + padX * 2;
  const frameWidth = Math.max(resolvedWidth, bubble.width * width);
  const textFrameLeft = bubble.align === "left"
    ? padX - textRightInset
    : bubble.align === "center"
      ? (frameWidth - textBlockWidth) / 2 - textRightInset
      : frameWidth - textColumnWidth - padX;
  const wrapperStyle: React.CSSProperties = {
    position: "absolute",
    left: bubble.x * width,
    top: bubble.y * height,
    transform: "translate(-50%, -50%)",
    boxSizing: "border-box",
  };
  const textStyle: React.CSSProperties = {
    position: "absolute",
    left: textFrameLeft + (bubble.textOffsetX ?? 0) * width,
    top: padY + (bubble.textOffsetY ?? 0) * height,
    fontSize,
    fontFamily: comicFontFamily(bubble.font, settings.fontFamily),
    lineHeight,
    whiteSpace: "pre-wrap",
    overflowWrap: "anywhere",
    writingMode: "vertical-rl",
    textOrientation: "mixed",
    height: usableColumn,
    width: textColumnWidth,
  };
  const frameStyle: React.CSSProperties = {
    position: "relative",
    boxSizing: "border-box",
    width: frameWidth,
    height: columnHeight,
  };

  if (bubble.type === "svg") {
    const shape = bubble.svgShapeId ? comicBubbles?.[bubble.svgShapeId] : undefined;
    if (shape) {
      const maskStyle = (file: string): React.CSSProperties => ({
        position: "absolute",
        inset: 0,
        transform: bubble.flipX ? "scaleX(-1)" : undefined,
        maskImage: `url(${staticFile(file)})`,
        maskSize: "100% 100%",
        maskRepeat: "no-repeat",
        WebkitMaskImage: `url(${staticFile(file)})`,
        WebkitMaskSize: "100% 100%",
        WebkitMaskRepeat: "no-repeat",
      });
      return (
        <div style={wrapperStyle}>
          <div style={frameStyle}>
            <div style={{...maskStyle(shape.outline), background: bubble.borderColor}} />
            <div style={{...maskStyle(shape.fill), background: bubble.fillColor ?? "#ffffff"}} />
            <div style={{...textStyle, zIndex: 1, color: settings.textColor, fontWeight: 700}}>{bubble.text}</div>
          </div>
        </div>
      );
    }
    // 未登録・削除済みの素材IDは吹き出し欠落より通常枠での代替表示を優先する。
  }

  if (bubble.type === "narration") {
    return (
      <div style={wrapperStyle}>
        <div style={{...frameStyle, borderRadius: 4, background: "rgba(0,0,0,0.65)", color: "#ffffff"}}><div style={textStyle}>{bubble.text}</div></div>
      </div>
    );
  }

  const isThought = bubble.type === "thought";
  return (
    <div style={wrapperStyle}>
      <div style={{
        ...frameStyle,
        transform: bubble.flipX ? "scaleX(-1)" : undefined,
        borderRadius: isThought ? Math.max(28, settings.radius) : settings.radius,
        background: settings.bgColor,
        color: settings.textColor,
        border: `${settings.borderWidth}px ${isThought ? "dashed" : "solid"} ${bubble.borderColor}`,
        fontWeight: 700,
        boxShadow: BUBBLE_SHADOW,
      }} />
      <div style={{...textStyle, color: settings.textColor, fontWeight: 700}}>{bubble.text}</div>
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
  comicBubbles,
}: {
  width: number;
  height: number;
  image: string;
  bubbles: ComicRenderBubble[];
  cameraFrame: CameraFrameV2;
  settings: ComicBubbleSettings;
  comicBubbles?: ComicBubbleRegistry;
}) {
  const values = stageTransformValues(width, height, cameraFrame);
  const transform = values ? `translate(${values.tx}px, ${values.ty}px) scale(${values.scale})` : undefined;
  return (
    <AbsoluteFill style={{background: "#111", overflow: "hidden"}}>
      <div style={{position: "absolute", left: 0, top: 0, width, height, transform, transformOrigin: "0 0"}}>
        <Img src={staticFile(image)} style={{position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover"}} />
        {bubbles.map((bubble, index) => (
          <ComicBubble key={index} bubble={bubble} width={width} height={height} settings={settings} comicBubbles={comicBubbles} />
        ))}
      </div>
    </AbsoluteFill>
  );
}
