import React from "react";
import {AbsoluteFill, Audio, Img, Sequence, staticFile, useCurrentFrame, useVideoConfig, Video} from "remotion";
import {useWindowedAudioData} from "@remotion/media-utils";
import {Avatar, MOUTH_HALF} from "./Avatar";
import type {ExpressionCfg} from "./Avatar";
import type {Gender} from "./types";
import {WhiteboardExplainInsert, getWhiteboardExplainLayout} from "./inserts/whiteboardExplain";
import {InsertOverlay} from "./StoryVideo";
import {
  placementOrigin,
  resolveFraming,
  resolveStageStateAtTurn,
  type ResolvedInstanceV2,
  type SceneLibraryV2,
  type StoryDisplaySettingsV2,
  type StoryV2,
  type StageAnimationDirectionV2,
  type StageEnterV2,
  type StageExitV2,
  type StageTurnV2,
} from "./stage-v2";
import type {BgmRegion, ExpressionsMap, MobDef, MobsMap, PosesMap, SeMap, SeMapEntry, StoryInsert, StoryOverlay, TurnSe} from "./StoryVideo";

type Manifest = Record<string, Record<string, string>>;

export type StageVideoV2Props = {
  story: StoryV2;
  scenes: SceneLibraryV2;
  manifest?: Manifest;
  audio?: string;
  expressions?: ExpressionsMap;
  poses?: PosesMap;
  seMap?: SeMap;
  mobs?: MobsMap;
};

const MAIN_CHARACTERS: Record<string, {avatar: string; gender: Gender; expressive?: boolean; color: string}> = {
  zundamon: {avatar: "zundamon", gender: "male", expressive: true, color: "#5fb84f"},
  metan: {avatar: "metan", gender: "female", color: "#e87bb0"},
};

const FULL_CANVAS = {
  zundamon: {w: 783, h: 1473},
  metan: {w: 858, h: 1769},
} as const;
const FULL_BOX_WIDTH = 445;
const LIPSYNC_GAIN = 5;
const ENTER_EXIT_ANIMATION_SECONDS = 0.5;
// 手動配置(placement)がターン間で変わったときの移動遷移秒数。旧StoryVideoのMANUAL_POS_TRANSと同値。
const MANUAL_POS_TRANSITION_SECONDS = 0.6;
// 単発吹き出しを話者の足元より少し上へ浮かせるオフセット。旧bubbleBottomOffsetと同値。
const BUBBLE_BOTTOM_OFFSET = 36;
const BUBBLE_CONTINUE_BOTTOM_OFFSET = 12;
const EXTRA_EFFECT_FONT = '"Arial Black", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif';
const FLASHBACK_SATURATE = 0.4;
const FLASHBACK_BRIGHTNESS = 1.02;
const FLASHBACK_GRAIN_OPACITY = 0.06;
const FLASHBACK_DISSOLVE_SECONDS = 0.3;
const SCENE_TRANSITION_FADE_SECONDS = 0.9;
const DEFAULT_STAGE_EFFECT_SETTINGS = {
  zoomPunch: {scale: 1.14, duration: 0.18, borderStrength: 1},
  quoteFreeze: {fadeIn: 0.14, fadeOutStart: 0.72, fadeOutDuration: 0.18, backdropOpacity: 0.22},
  impactLines: {cx: 0.5, cy: 0.48, count: 72, thickness: 1.25, opacity: 0.72, innerRadius: 0.17, start: 0, end: 0},
  visionNoise: {type: "future" as "future" | "snow" | "vhs" | "glitch", strength: 0.68, scanline: 0.78, glitch: 0.36, flicker: 0.42, tint: "#7dd3fc"},
  irisOut: {cx: 0.5, cy: 0.5, startRadius: 1.05, color: "#000000", closeStart: 1.7, closeEnd: 2.0},
};

const DEFAULT_DISPLAY_SETTINGS = {
  bubble: {maxChars: null as number | null, fontSize: 54, fontFamily: "sans-serif", textColor: "#1b1b1f", bgColor: "#ffffff", borderWidth: 5, radius: 18},
  subtitle: {fontSize: 46, fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif', textColor: "#ffffff", bgColor: "#080a0e", bgOpacity: 0.84, border: true, borderColor: "#ffffff", borderWidth: 2, bottom: 42, width: 0.84},
  telop: {x: 0.045, y: 0.06, size: 1},
  speakerColors: {zundamon: "#5fb84f", metan: "#e87bb0", default: "#9aa0a6"},
};
const CAPTION_FADE_SECONDS = 0.25;

function fullBoxSize(avatar: string) {
  const canvas = FULL_CANVAS[avatar as keyof typeof FULL_CANVAS];
  return canvas
    ? {w: FULL_BOX_WIDTH, h: Math.round(FULL_BOX_WIDTH * (canvas.h / canvas.w))}
    : {w: FULL_BOX_WIDTH, h: Math.round(FULL_BOX_WIDTH * 1.8)};
}

function resolvedFlip(instance: {face?: "left" | "right"; flip?: boolean} | undefined, fallback = false) {
  if (instance?.face === "left") return false;
  if (instance?.face === "right") return true;
  return instance?.flip ?? fallback;
}

function activeTurnIndex(story: StoryV2, seconds: number): number {
  let index = 0;
  for (let i = 0; i < story.script.length; i += 1) {
    if ((story.script[i].start ?? 0) <= seconds) index = i;
    else break;
  }
  return index;
}

function sceneTransitionVisual(turn: StageTurnV2, seconds: number, stageWidth: number) {
  if (
    turn.transition !== "fade-black"
    && turn.transition !== "fade-white"
    && turn.transition !== "wipe-left"
    && turn.transition !== "wipe-right"
    && turn.transition !== "slide-left"
    && turn.transition !== "slide-right"
  ) return null;
  const start = turn.start ?? seconds;
  const progress = easeInOutCubic((seconds - start) / SCENE_TRANSITION_FADE_SECONDS);
  const amount = clamp(progress, 0, 1);
  switch (turn.transition) {
    case "fade-black":
    case "fade-white":
      return {
        cover: {
          color: turn.transition === "fade-white" ? "#fff" : "#000",
          opacity: 1 - amount,
        },
      };
    case "wipe-left":
      return {contentStyle: {clipPath: `inset(0 ${(1 - amount) * 100}% 0 0)`}};
    case "wipe-right":
      return {contentStyle: {clipPath: `inset(0 0 0 ${(1 - amount) * 100}%)`}};
    case "slide-left":
      return {contentStyle: {transform: `translateX(${-stageWidth * (1 - amount)}px)`}};
    case "slide-right":
      return {contentStyle: {transform: `translateX(${stageWidth * (1 - amount)}px)`}};
    default:
      return null;
  }
}

function stageTransform(width: number, height: number, frame: {cx: number; cy: number; width: number} | undefined) {
  const values = stageTransformValues(width, height, frame);
  return values ? `translate(${values.tx}px, ${values.ty}px) scale(${values.scale})` : undefined;
}

function stageTransformValues(width: number, height: number, frame: {cx: number; cy: number; width: number} | undefined) {
  if (!frame) return undefined;
  const scale = Math.max(1, 1 / frame.width);
  const tx = Math.min(0, Math.max(width * (1 - scale), width / 2 - frame.cx * width * scale));
  const ty = Math.min(0, Math.max(height * (1 - scale), height / 2 - frame.cy * height * scale));
  return {tx, ty, scale};
}

function easeInOutCubic(value: number) {
  const t = Math.max(0, Math.min(1, value));
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function easeOutCubic(value: number) {
  const t = Math.max(0, Math.min(1, value));
  return 1 - Math.pow(1 - t, 3);
}

function lerp(a: number, b: number, k: number) {
  return a + (b - a) * k;
}

function applyCameraMotion(frame: {cx: number; cy: number; width: number} | undefined, motion: ReturnType<typeof resolveStageStateAtTurn>["cameraMotion"]) {
  if (!frame || !motion) return frame;
  return {
    ...frame,
    cx: frame.cx + (motion.pan?.x ?? 0),
    cy: frame.cy + (motion.pan?.y ?? 0),
    width: frame.width / Math.max(0.2, 1 + (motion.zoom ?? 0)),
  };
}

function cameraShakeOffset(
  shake: {strength: number; duration: number} | undefined,
  seconds: number,
  turnStart: number,
) {
  if (!shake || shake.duration <= 0) return {x: 0, y: 0};
  const elapsed = seconds - turnStart;
  if (elapsed < 0 || elapsed >= shake.duration) return {x: 0, y: 0};
  const strength = shake.strength * (1 - elapsed / shake.duration);
  return {
    x: strength * Math.sin(elapsed * Math.PI * 2 * 16),
    y: strength * Math.sin(elapsed * Math.PI * 2 * 20 + 1),
  };
}

function mediaStaticSrc(path: string): string {
  const qidx = path.indexOf("?");
  if (qidx < 0) return staticFile(path);
  return `${staticFile(path.slice(0, qidx))}${path.slice(qidx)}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function stageEffectEnabled(raw: unknown) {
  return raw === true || (!!raw && typeof raw === "object" && (raw as {enabled?: unknown}).enabled !== false);
}

function stageEffectConfig<T extends Record<string, number>>(raw: unknown, defaults: T): T {
  if (!raw || typeof raw !== "object") return defaults;
  const merged = {...defaults};
  for (const key of Object.keys(defaults) as Array<keyof T>) {
    const value = (raw as Record<string, unknown>)[String(key)];
    if (Number.isFinite(Number(value))) merged[key] = Number(value) as T[keyof T];
  }
  return merged;
}

function stageExitId(item: StageExitV2) {
  return typeof item === "string" ? item : item.instanceId;
}

function stageAnimationDirection(item: StageEnterV2 | StageExitV2 | undefined): StageAnimationDirectionV2 {
  if (!item || typeof item === "string") return "auto";
  return item.animation?.direction ?? "auto";
}

function stageAnimationOffset({
  direction,
  phase,
  originX,
  seconds,
  turn,
  width,
  height,
}: {
  direction: StageAnimationDirectionV2;
  phase: "enter" | "exit";
  originX: number;
  seconds: number;
  turn: StageTurnV2;
  width: number;
  height: number;
}) {
  if (direction === "instant") return {x: 0, y: 0};
  const start = turn.start;
  const end = turn.end;
  if (typeof start !== "number") return {x: 0, y: 0};
  const turnDuration = typeof end === "number" ? Math.max(0, end - start) : ENTER_EXIT_ANIMATION_SECONDS;
  const duration = Math.max(0.001, Math.min(ENTER_EXIT_ANIMATION_SECONDS, turnDuration / 2 || ENTER_EXIT_ANIMATION_SECONDS));
  let amount = 0;
  if (phase === "enter") {
    amount = 1 - easeInOutCubic((seconds - start) / duration);
  } else {
    if (typeof end !== "number") return {x: 0, y: 0};
    amount = easeInOutCubic((seconds - (end - duration)) / duration);
  }
  if (amount <= 0) return {x: 0, y: 0};
  const resolvedDirection = direction === "auto" ? (originX < 0.5 ? "left" : "right") : direction;
  const distance = Math.max(width, height) * 1.2;
  if (resolvedDirection === "left") return {x: -distance * amount, y: 0};
  if (resolvedDirection === "right") return {x: distance * amount, y: 0};
  if (resolvedDirection === "up") return {x: 0, y: -distance * amount};
  if (resolvedDirection === "down") return {x: 0, y: distance * amount};
  return {x: 0, y: 0};
}

function animationTranslate(offset: {x: number; y: number}) {
  return offset.x || offset.y ? ` translate(${offset.x}px, ${offset.y}px)` : "";
}

function validHex(value: unknown, fallback: string) {
  return /^#([0-9a-fA-F]{6})$/.test(String(value || "")) ? String(value) : fallback;
}

function stageVisionNoiseConfig(raw: unknown) {
  const base = DEFAULT_STAGE_EFFECT_SETTINGS.visionNoise;
  if (!raw || typeof raw !== "object") return base;
  const data = raw as Record<string, unknown>;
  const type = data.type === "snow" || data.type === "vhs" || data.type === "glitch" || data.type === "future"
    ? data.type
    : base.type;
  const number = (key: string, fallback: number) => Number.isFinite(Number(data[key])) ? Number(data[key]) : fallback;
  return {
    type,
    strength: number("strength", base.strength),
    scanline: number("scanline", base.scanline),
    glitch: number("glitch", base.glitch),
    flicker: number("flicker", base.flicker),
    tint: validHex(data.tint, base.tint),
  };
}

function stageIrisOutConfig(raw: unknown) {
  const base = DEFAULT_STAGE_EFFECT_SETTINGS.irisOut;
  if (!raw || typeof raw !== "object") return base;
  const data = raw as Record<string, unknown>;
  const number = (key: string, fallback: number) => Number.isFinite(Number(data[key])) ? Number(data[key]) : fallback;
  return {
    cx: number("cx", base.cx),
    cy: number("cy", base.cy),
    startRadius: number("startRadius", base.startRadius),
    closeStart: number("closeStart", base.closeStart),
    closeEnd: number("closeEnd", base.closeEnd),
    color: validHex(data.color, base.color),
  };
}

function displaySettingsOf(settings?: StoryDisplaySettingsV2) {
  const bubble = settings?.bubble ?? {};
  const subtitle = settings?.subtitle ?? {};
  const telop = settings?.telop ?? {};
  const colors = settings?.speakerColors ?? {};
  const number = (value: unknown, fallback: number, min: number, max: number) => Number.isFinite(Number(value)) ? clamp(Number(value), min, max) : fallback;
  const maxChars = Number(bubble.maxChars);
  return {
    bubble: {
      maxChars: Number.isFinite(maxChars) && maxChars > 0 ? Math.round(maxChars) : DEFAULT_DISPLAY_SETTINGS.bubble.maxChars,
      fontSize: Math.round(number(bubble.fontSize, DEFAULT_DISPLAY_SETTINGS.bubble.fontSize, 24, 96)),
      fontFamily: bubble.fontFamily || DEFAULT_DISPLAY_SETTINGS.bubble.fontFamily,
      textColor: validHex(bubble.textColor, DEFAULT_DISPLAY_SETTINGS.bubble.textColor),
      bgColor: validHex(bubble.bgColor, DEFAULT_DISPLAY_SETTINGS.bubble.bgColor),
      borderWidth: number(bubble.borderWidth, DEFAULT_DISPLAY_SETTINGS.bubble.borderWidth, 1, 12),
      radius: Math.round(number(bubble.radius, DEFAULT_DISPLAY_SETTINGS.bubble.radius, 4, 40)),
    },
    subtitle: {
      fontSize: Math.round(number(subtitle.fontSize, DEFAULT_DISPLAY_SETTINGS.subtitle.fontSize, 24, 96)),
      fontFamily: subtitle.fontFamily || DEFAULT_DISPLAY_SETTINGS.subtitle.fontFamily,
      textColor: validHex(subtitle.textColor, DEFAULT_DISPLAY_SETTINGS.subtitle.textColor),
      bgColor: validHex(subtitle.bgColor, DEFAULT_DISPLAY_SETTINGS.subtitle.bgColor),
      bgOpacity: number(subtitle.bgOpacity, DEFAULT_DISPLAY_SETTINGS.subtitle.bgOpacity, 0, 1),
      border: subtitle.border !== false,
      borderColor: validHex(subtitle.borderColor, DEFAULT_DISPLAY_SETTINGS.subtitle.borderColor),
      borderWidth: number(subtitle.borderWidth, DEFAULT_DISPLAY_SETTINGS.subtitle.borderWidth, 0.5, 6),
      bottom: Math.round(number(subtitle.bottom, DEFAULT_DISPLAY_SETTINGS.subtitle.bottom, 0, 200)),
      width: number(subtitle.width, DEFAULT_DISPLAY_SETTINGS.subtitle.width, 0.4, 1),
    },
    speakerColors: {
      zundamon: validHex(colors.zundamon, DEFAULT_DISPLAY_SETTINGS.speakerColors.zundamon),
      metan: validHex(colors.metan, DEFAULT_DISPLAY_SETTINGS.speakerColors.metan),
      default: validHex(colors.default, DEFAULT_DISPLAY_SETTINGS.speakerColors.default),
    },
    telop: {
      x: number(telop.x, DEFAULT_DISPLAY_SETTINGS.telop.x, 0, 1),
      y: number(telop.y, DEFAULT_DISPLAY_SETTINGS.telop.y, 0, 1),
      size: number(telop.size, DEFAULT_DISPLAY_SETTINGS.telop.size, 0.5, 3),
    },
  };
}

function captionOfTurn(turn: StageTurnV2 | undefined) {
  const caption = turn?.caption;
  if (caption?.text?.trim()) return caption;
  if (!turn) return null;
  const legacyText = turn.telop?.trim();
  if (!legacyText) return null;
  return {
    text: legacyText,
    ...(typeof turn.telopX === "number" ? {x: turn.telopX} : {}),
    ...(typeof turn.telopY === "number" ? {y: turn.telopY} : {}),
    ...(typeof turn.telopSize === "number" ? {size: turn.telopSize} : {}),
  };
}

function captionVisualFor(script: StageTurnV2[], activeIndex: number, seconds: number) {
  const active = script[activeIndex];
  const activeCaption = captionOfTurn(active);
  if (!activeCaption) return null;
  let startIndex = activeIndex;
  while (startIndex > 0 && !!captionOfTurn(script[startIndex - 1])) startIndex -= 1;
  let endIndex = activeIndex;
  while (endIndex < script.length - 1 && !!captionOfTurn(script[endIndex + 1])) endIndex += 1;
  const start = script[startIndex]?.start ?? active.start;
  const last = script[endIndex] ?? active;
  const end = Math.max(last.end ?? active.end ?? seconds, script[endIndex + 1]?.start ?? last.end ?? active.end ?? seconds);
  let opacity = 1;
  if (typeof start === "number" && typeof end === "number" && end > start) {
    const fade = Math.min(CAPTION_FADE_SECONDS, Math.max(0.001, (end - start) / 2));
    if (seconds < start + fade) opacity = clamp((seconds - start) / fade, 0, 1);
    else if (seconds >= end - fade) opacity = clamp((end - seconds) / fade, 0, 1);
  }
  return {caption: activeCaption, opacity};
}

function hexToRgba(hex: string, opacity: number) {
  const raw = hex.slice(1);
  const value = parseInt(raw, 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${opacity})`;
}

function sentenceGroups(turn: StageTurnV2) {
  if (turn.disableAutoBubbleSplit || /\r?\n/.test(turn.text) || !turn.sentences?.length) return [{text: turn.text, start: -Infinity, end: Infinity}];
  const joined = turn.sentences.map((sentence) => sentence.text).join("");
  if (joined !== turn.text.replace(/\s+/g, "")) return [{text: turn.text, start: -Infinity, end: Infinity}];
  const groups: Array<{text: string; start: number; end: number}> = [];
  for (const sentence of turn.sentences) {
    const previous = groups[groups.length - 1];
    if (previous && /[、，,]$/.test(previous.text.trim())) {
      previous.text += sentence.text;
      previous.end = sentence.end;
    } else {
      groups.push({text: sentence.text, start: sentence.start, end: sentence.end});
    }
  }
  return groups.length ? groups : [{text: turn.text, start: -Infinity, end: Infinity}];
}

function visibleSentenceGroupCount(turn: StageTurnV2, seconds: number) {
  const groups = sentenceGroups(turn);
  if (groups.length <= 1) return 1;
  const active = groups.findIndex((group) => group.start <= seconds && seconds < group.end);
  return active >= 0 ? active + 1 : groups.length;
}

function bubbleTextAt(turn: StageTurnV2, seconds: number) {
  const groups = sentenceGroups(turn);
  const active = groups.find((group) => group.start <= seconds && seconds < group.end);
  return active?.text ?? groups[groups.length - 1]?.text ?? turn.text;
}

function subtitleProgressiveText(turn: StageTurnV2, seconds: number) {
  const groups = sentenceGroups(turn);
  if (groups.length <= 1) return bubbleTextAt(turn, seconds);
  const visible = visibleSentenceGroupCount(turn, seconds);
  return groups.slice(0, Math.max(1, visible)).map((group) => group.text).join("\n");
}

function continuedBubbleRange(script: StageTurnV2[], activeIndex: number) {
  let start = activeIndex;
  let end = activeIndex;
  while (start > 0) {
    const current = script[start];
    const previous = script[start - 1];
    if (!current.continueBubble || current.scene !== previous.scene || current.speaker !== previous.speaker) break;
    start -= 1;
  }
  while (end < script.length - 1) {
    const current = script[end];
    const next = script[end + 1];
    if (!next.continueBubble || next.scene !== current.scene || next.speaker !== current.speaker) break;
    end += 1;
  }
  return {start, end};
}

function characterDisplayWidth(fontSize: number, character: string) {
  if (character === "…") return fontSize * 1.02;
  if (/[?!！？]/.test(character)) return fontSize * 0.9;
  if (/[?!！？、。，．,.・…「」『』（）()[\]【】]/.test(character)) return fontSize * 0.74;
  return fontSize * (/[\u0000-\u00ff\uff61-\uff9f\uffe8-\uffee]/.test(character) ? 0.58 : 1.03);
}

function bubbleFontSize(stacked: boolean, baseFontSize: number) {
  return stacked ? Math.max(20, baseFontSize - 2) : baseFontSize;
}

function bubbleWrapCharLimit(turn: StageTurnV2, defaultMaxChars: number | null) {
  const raw = Number(turn.bubbleMaxChars);
  if (Number.isFinite(raw) && raw > 0) return Math.round(raw);
  return defaultMaxChars;
}

function bubbleMaxWidthForTurn(
  turn: StageTurnV2,
  stageWidth: number,
  stacked: boolean,
  fallbackWidth: number,
  bubbleSettings: ReturnType<typeof displaySettingsOf>["bubble"],
) {
  const charLimit = bubbleWrapCharLimit(turn, bubbleSettings.maxChars);
  if (!charLimit) return fallbackWidth;
  const fontSize = bubbleFontSize(stacked, bubbleSettings.fontSize);
  const desiredWidth = characterDisplayWidth(fontSize, "あ") * charLimit + 66 + 12;
  return clamp(desiredWidth, 120, stageWidth - 40);
}

function bubbleMetricsV2(text: string, fontSize: number, maxWidth: number, charLimit: number | null) {
  let budget = Math.max(40, maxWidth - 66);
  if (charLimit) budget = Math.min(budget, Math.max(40, characterDisplayWidth(fontSize, "あ") * charLimit));
  const lines: string[] = [];
  let widest = 0;
  for (const paragraph of String(text || "").split("\n").map((item) => item.replace(/\s+/g, ""))) {
    let line = "";
    let lineWidth = 0;
    for (const character of paragraph) {
      const characterWidth = characterDisplayWidth(fontSize, character);
      if (line && lineWidth + characterWidth > budget) {
        lines.push(line);
        widest = Math.max(widest, lineWidth);
        line = character;
        lineWidth = characterWidth;
      } else {
        line += character;
        lineWidth += characterWidth;
      }
    }
    lines.push(line);
    widest = Math.max(widest, lineWidth);
  }
  return {text: lines.join("\n"), width: Math.min(maxWidth, Math.max(120, widest + 86))};
}

function overlayAnchorTime(story: StoryV2, anchor: StoryOverlay["start"] | undefined) {
  if (!anchor?.turnId) return undefined;
  const turn = story.script.find((item) => item.id === anchor.turnId);
  if (!turn || typeof turn.start !== "number") return undefined;
  return turn.start + (typeof anchor.at === "number" ? anchor.at : 0);
}

function activeOverlays(story: StoryV2, seconds: number) {
  return (story.overlays ?? [])
    .filter((overlay) => {
      const start = overlayAnchorTime(story, overlay.start);
      const end = overlayAnchorTime(story, overlay.end);
      return start != null && end != null && end > start && start <= seconds && seconds < end;
    })
    .sort((a, b) => (a.z ?? 0) - (b.z ?? 0));
}

function isStandardDisplayMode(kind: string | undefined) {
  return !kind || kind === "standard";
}

function displayModeInsert(displayMode: ReturnType<typeof resolveStageStateAtTurn>["displayMode"]): StoryInsert | null {
  switch (displayMode.kind) {
    case "zunMonitor":
      return displayMode.monitor;
    case "zunAi":
      return displayMode.chat;
    case "zunChat":
      return displayMode.teamchat;
    case "zunMail":
      return displayMode.mailer;
    default:
      return null;
  }
}

function mobImageForState(mob: MobDef | undefined, expression: string | undefined, speaking: boolean, amplitude: number) {
  const expressionImages = mob?.images[expression ?? "normal"] ?? mob?.images.normal;
  return speaking && amplitude > MOUTH_HALF
    ? expressionImages?.open ?? expressionImages?.closed
    : expressionImages?.closed ?? expressionImages?.open;
}

const V2BgmLayer: React.FC<{regions?: BgmRegion[]; fps: number}> = ({regions, fps}) => {
  const BGM_DEFAULT_VOL = 0.1;
  const BGM_DEFAULT_FADE = 0.6;
  const isFiniteNumber = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);

  type BgmSegment = {
    file: string;
    volume: number;
    fadeIn: number;
    fadeOut: number;
    startSec: number;
    endSec: number;
  };

  const validSegs: BgmSegment[] = (regions || [])
    .filter((r) => r.file && isFiniteNumber(r.start) && isFiniteNumber(r.end) && r.end > r.start)
    .map((r) => ({
      file: r.file,
      volume: r.volume ?? BGM_DEFAULT_VOL,
      fadeIn: r.fadeIn ?? BGM_DEFAULT_FADE,
      fadeOut: r.fadeOut ?? BGM_DEFAULT_FADE,
      startSec: r.start,
      endSec: r.end,
    }));

  return (
    <>
      {validSegs.map((seg, index) => {
        const startFrame = Math.round(seg.startSec * fps);
        const durationInFrames = Math.max(1, Math.round((seg.endSec - seg.startSec) * fps));
        const fadeInFrames = Math.round(seg.fadeIn * fps);
        const fadeOutFrames = Math.round(seg.fadeOut * fps);
        const volume = seg.volume;
        const volumeFn = (localFrame: number): number => {
          const inK = fadeInFrames > 0 ? Math.min(localFrame / fadeInFrames, 1) : 1;
          const outK =
            fadeOutFrames > 0
              ? Math.min((durationInFrames - localFrame) / fadeOutFrames, 1)
              : 1;
          return volume * Math.max(0, Math.min(inK, outK));
        };
        const audioKey = `${seg.file}-${index}-${seg.startSec}-${seg.endSec}-${seg.volume}-${seg.fadeIn}-${seg.fadeOut}`;
        const src = `${staticFile(seg.file)}?v2bgm=${encodeURIComponent(audioKey)}`;
        return (
          <Sequence key={audioKey} from={startFrame} durationInFrames={durationInFrames}>
            <Audio
              src={src}
              loop
              volume={volumeFn}
            />
          </Sequence>
        );
      })}
    </>
  );
};

const V2SeLayer: React.FC<{script: StoryV2["script"]; seMap?: SeMap; fps: number}> = ({script, seMap, fps}) => {
  const events = script.flatMap((turn) => {
    const automatic = [] as Array<{file: string; volume: number; at: number}>;
    const tryAdd = (entry: SeMapEntry | undefined) => {
      if (!entry || !entry.enabled || !entry.file || typeof turn.start !== "number") return;
      automatic.push({file: entry.file, volume: entry.volume, at: turn.start});
    };
    for (const patch of Object.values(turn.stage?.update ?? {})) {
      if (patch.expression) tryAdd(seMap?.expression?.[patch.expression]);
    }
    const manual = (turn.se ?? []).flatMap((se: TurnSe) => {
    if (!se.file || typeof turn.start !== "number" || !Number.isFinite(se.at ?? 0)) return [];
    return [{file: se.file, volume: se.volume ?? 0.7, at: turn.start + (se.at ?? 0)}];
    });
    return [...automatic, ...manual];
  });
  return <>{events.map((event, index) => (
    <Sequence key={`${event.file}-${index}`} from={Math.round(event.at * fps)} durationInFrames={Math.round(6 * fps)}>
      <Audio src={staticFile(event.file)} volume={event.volume} />
    </Sequence>
  ))}</>;
};

const V2OverlayLayer: React.FC<{overlays: StoryOverlay[]}> = ({overlays}) => {
  const colorWithOpacity = (color: string | undefined, opacity: number | undefined, fallback: string) => {
    const source = String(color || fallback).trim();
    const alpha = clamp(opacity ?? 1, 0, 1);
    const hex = source.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (!hex) return source;
    const raw = hex[1].length === 3 ? hex[1].split("").map((char) => char + char).join("") : hex[1];
    const number = parseInt(raw, 16);
    return `rgba(${(number >> 16) & 255}, ${(number >> 8) & 255}, ${number & 255}, ${alpha})`;
  };
  return <AbsoluteFill style={{pointerEvents: "none"}}>
    {overlays.map((overlay) => {
      const width = clamp(overlay.w || 0.2, 0.04, 1) * 100;
      const left = clamp(overlay.kind === "text" && overlay.centerX ? 0.5 : (overlay.x || 0.5), 0, 1) * 100;
      const top = clamp(overlay.y || 0.5, 0, 1) * 100;
      if (overlay.kind === "text") {
        return <div key={overlay.id} style={{position: "absolute", left: `${left}%`, top: `${top}%`, width: `${width}%`, transform: "translate(-50%, -50%)", opacity: clamp(overlay.opacity ?? 1, 0, 1), padding: "10px 18px", borderRadius: 16, border: `4px solid ${colorWithOpacity(overlay.borderColor, overlay.borderOpacity, "#ffffff")}`, background: colorWithOpacity(overlay.bgColor, overlay.bgOpacity, "#0f1117"), color: overlay.textColor || "#ffffff", fontSize: overlay.fontSize ?? 34, lineHeight: 1.35, fontWeight: 700, whiteSpace: "pre-wrap", wordBreak: "break-word", textAlign: "center", boxShadow: "0 10px 24px rgba(0,0,0,0.28)"}}>{overlay.text || ""}</div>;
      }
      return overlay.src ? <Img key={overlay.id} src={staticFile(overlay.src)} style={{position: "absolute", left: `${left}%`, top: `${top}%`, width: `${width}%`, height: "auto", transform: "translate(-50%, -50%)", opacity: clamp(overlay.opacity ?? 1, 0, 1), objectFit: "contain", filter: "drop-shadow(0 10px 24px rgba(0,0,0,0.28))"}} /> : null;
    })}
  </AbsoluteFill>;
};

const V2EffectsLayer: React.FC<{
  turn: StageTurnV2;
  elapsed: number;
  progress: number;
  width: number;
  height: number;
  onlyImpactLines?: boolean;
  hideImpactLines?: boolean;
}> = ({turn, elapsed, progress, width, height, onlyImpactLines, hideImpactLines}) => {
  const effects = turn.effects ?? {};
  const layers: React.ReactNode[] = [];
  const hasImpactLines = stageEffectEnabled(effects.impactLines);
  if (onlyImpactLines && !hasImpactLines) return null;

  if (!hideImpactLines && hasImpactLines) {
    const impactLinesCfg = stageEffectConfig(effects.impactLines, DEFAULT_STAGE_EFFECT_SETTINGS.impactLines);
    const lineStart = Math.max(0, impactLinesCfg.start);
    const lineEnd = Math.max(0, impactLinesCfg.end);
    const hasManualWindow = lineEnd > lineStart;
    const localProgress = hasManualWindow ? clamp((elapsed - lineStart) / Math.max(lineEnd - lineStart, 0.001), 0, 1) : progress;
    const burstIn = clamp(localProgress / DEFAULT_STAGE_EFFECT_SETTINGS.zoomPunch.duration, 0, 1);
    const burstOut = hasManualWindow
      ? 1 - clamp((elapsed - (lineEnd - 0.22)) / 0.22, 0, 1)
      : 1 - clamp((progress - 0.58) / 0.22, 0, 1);
    const opacity = clamp(Math.min(burstIn * 1.1, burstOut), 0, 1);
    const count = clamp(Math.round(impactLinesCfg.count), 12, 180);
    const gapDeg = 360 / count;
    const lineDeg = Math.min(gapDeg * 0.72, Math.max(0.35, impactLinesCfg.thickness));
    const originX = clamp(impactLinesCfg.cx, 0, 1) * 100;
    const originY = clamp(impactLinesCfg.cy, 0, 1) * 100;
    const inner = clamp(impactLinesCfg.innerRadius, 0, 0.8) * 100;
    const scale = lerp(1.08, 1, easeOutCubic(burstIn));
    layers.push(
      <AbsoluteFill key="impactLines" style={{pointerEvents: "none", overflow: "hidden"}}>
        <AbsoluteFill
          style={{
            opacity: hasManualWindow && (elapsed < lineStart || elapsed >= lineEnd) ? 0 : opacity * clamp(impactLinesCfg.opacity, 0, 1),
            background:
              `repeating-conic-gradient(from -8deg at ${originX}% ${originY}%, rgba(5,7,12,0.9) 0deg ${lineDeg}deg, transparent ${lineDeg}deg ${gapDeg}deg)`,
            WebkitMaskImage:
              `radial-gradient(circle at ${originX}% ${originY}%, transparent 0 ${inner}%, rgba(0,0,0,0.28) ${inner + 6}%, #000 ${inner + 18}% 100%)`,
            maskImage:
              `radial-gradient(circle at ${originX}% ${originY}%, transparent 0 ${inner}%, rgba(0,0,0,0.28) ${inner + 6}%, #000 ${inner + 18}% 100%)`,
            transform: `scale(${scale})`,
            mixBlendMode: "multiply",
          }}
        />
      </AbsoluteFill>,
    );
  }

  if (!onlyImpactLines && stageEffectEnabled(effects.zoomPunch)) {
    const zoomPunchCfg = stageEffectConfig(effects.zoomPunch, DEFAULT_STAGE_EFFECT_SETTINGS.zoomPunch);
    const local = clamp(elapsed / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
    const punch = Math.sin(local * Math.PI);
    layers.push(
      <AbsoluteFill
        key="zoomPunch"
        style={{
          pointerEvents: "none",
          boxShadow: `inset 0 0 0 ${Math.round(18 * punch * zoomPunchCfg.borderStrength)}px rgba(255,255,255,${0.11 * punch * zoomPunchCfg.borderStrength})`,
        }}
      />,
    );
  }

  if (!onlyImpactLines && stageEffectEnabled(effects.quoteFreeze)) {
    const quoteFreezeCfg = stageEffectConfig(effects.quoteFreeze, DEFAULT_STAGE_EFFECT_SETTINGS.quoteFreeze);
    const hold = clamp(progress / Math.max(quoteFreezeCfg.fadeIn, 0.001), 0, 1);
    const fade = 1 - clamp((progress - quoteFreezeCfg.fadeOutStart) / Math.max(quoteFreezeCfg.fadeOutDuration, 0.001), 0, 1);
    const opacity = clamp(Math.min(hold, fade), 0, 1);
    layers.push(
      <AbsoluteFill
        key="quoteFreeze"
        style={{
          pointerEvents: "none",
          background: `rgba(10, 12, 18, ${quoteFreezeCfg.backdropOpacity * opacity})`,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            width: Math.min(width * 0.8, 1180),
            transform: `scale(${lerp(0.94, 1, easeOutCubic(hold))})`,
            opacity,
            borderLeft: "10px solid #f5c54c",
            background: "rgba(18, 24, 35, 0.84)",
            color: "#f8fafc",
            padding: "28px 38px 24px",
            borderRadius: 24,
            boxShadow: "0 24px 64px rgba(0,0,0,0.46)",
          }}
        >
          <div
            style={{
              fontFamily: EXTRA_EFFECT_FONT,
              fontSize: 28,
              letterSpacing: "0.12em",
              color: "#f5c54c",
              marginBottom: 14,
            }}
          >
            PROBLEM QUOTE
          </div>
          <div
            style={{
              fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif',
              fontWeight: 800,
              fontSize: Math.round(Math.min(width * 0.042, 62)),
              lineHeight: 1.45,
              whiteSpace: "pre-wrap",
            }}
          >
            「{turn.text}」
          </div>
        </div>
      </AbsoluteFill>,
    );
  }

  if (!onlyImpactLines && stageEffectEnabled(effects.flashback)) {
    const grainOffsetX = Math.round((elapsed * 31) % 64);
    const grainOffsetY = Math.round((elapsed * 47) % 64);
    layers.push(
      <AbsoluteFill
        key="flashbackGrain"
        style={{
          pointerEvents: "none",
          backgroundImage: `url(${staticFile("noise.png")})`,
          backgroundRepeat: "repeat",
          backgroundPosition: `${grainOffsetX}px ${grainOffsetY}px`,
          backgroundSize: "64px 64px",
          opacity: FLASHBACK_GRAIN_OPACITY,
          mixBlendMode: "luminosity",
        }}
      />,
    );
  }

  if (!onlyImpactLines && stageEffectEnabled(effects.irisOut)) {
    const irisOutCfg = stageIrisOutConfig(effects.irisOut);
    const dur = Math.max((turn.end ?? 0) - (turn.start ?? 0), 0.001);
    const diag = Math.sqrt(width * width + height * height) / 2;
    const baseRadius = diag * clamp(irisOutCfg.startRadius, 0.15, 1.3);
    const coverRadius = diag * 1.3;
    const window = dur;
    const rawCloseEnd = Math.max(irisOutCfg.closeEnd, 0.05);
    const rawCloseStart = clamp(irisOutCfg.closeStart, 0, rawCloseEnd);
    const closeDuration = Math.max(rawCloseEnd - rawCloseStart, 0.05);
    const closeEnd = Math.min(rawCloseEnd, window);
    const closeStart = Math.max(0, closeEnd - closeDuration);
    const appearDur = Math.min(0.6, closeStart);
    let radius: number;
    if (appearDur > 0 && elapsed < appearDur) {
      radius = lerp(coverRadius, baseRadius, easeInOutCubic(clamp(elapsed / appearDur, 0, 1)));
    } else if (elapsed < closeStart) {
      radius = baseRadius;
    } else {
      const p = clamp((elapsed - closeStart) / Math.max(closeEnd - closeStart, 0.05), 0, 1);
      radius = lerp(baseRadius, 0, easeInOutCubic(p));
    }
    const cxPct = clamp(irisOutCfg.cx, 0, 1) * 100;
    const cyPct = clamp(irisOutCfg.cy, 0, 1) * 100;
    const color = validHex(irisOutCfg.color, "#000000");
    layers.push(
      <AbsoluteFill
        key="irisOut"
        style={{
          pointerEvents: "none",
          background: color,
          maskImage: `radial-gradient(circle ${radius}px at ${cxPct}% ${cyPct}%, transparent 0, transparent ${Math.max(radius - 1, 0)}px, ${color} ${radius}px, ${color} 100%)`,
          WebkitMaskImage: `radial-gradient(circle ${radius}px at ${cxPct}% ${cyPct}%, transparent 0, transparent ${Math.max(radius - 1, 0)}px, ${color} ${radius}px, ${color} 100%)`,
        }}
      />,
    );
  }

  if (!onlyImpactLines && stageEffectEnabled(effects.visionNoise)) {
    const visionNoiseCfg = stageVisionNoiseConfig(effects.visionNoise);
    const strength = clamp(visionNoiseCfg.strength, 0, 1);
    const scanline = clamp(visionNoiseCfg.scanline, 0, 1);
    const glitch = clamp(visionNoiseCfg.glitch, 0, 1);
    const flicker = clamp(visionNoiseCfg.flicker, 0, 1);
    const noiseType = visionNoiseCfg.type || "future";
    const jitter = Math.sin(elapsed * 53.7) * glitch;
    const glitchBoost = noiseType === "glitch" ? 1.55 : noiseType === "vhs" ? 1.15 : 1;
    const glitchShift = Math.round(jitter * 34 * glitchBoost);
    const flickerOpacity = clamp(0.9 + Math.sin(elapsed * 41.0) * 0.12 * flicker + Math.sin(elapsed * 97.0) * 0.08 * flicker, 0.7, 1);
    const noiseX = Math.round((elapsed * 97) % 48);
    const noiseY = Math.round((elapsed * 131) % 48);
    const tint = validHex(visionNoiseCfg.tint, DEFAULT_STAGE_EFFECT_SETTINGS.visionNoise.tint);
    const showTint = noiseType !== "snow";
    const scanlineOpacity = noiseType === "snow" ? 0.08 : noiseType === "vhs" ? 0.3 : 0.2;
    const darkScanlineOpacity = noiseType === "snow" ? 0.04 : noiseType === "vhs" ? 0.24 : 0.16;
    const dotWhite = noiseType === "snow" ? 0.92 : noiseType === "vhs" ? 0.32 : 0.55;
    const dotBlack = noiseType === "snow" ? 0.78 : noiseType === "vhs" ? 0.36 : 0.48;
    const dotSizeA = noiseType === "snow" ? "3px 3px" : noiseType === "vhs" ? "10px 10px" : "7px 7px";
    const dotSizeB = noiseType === "snow" ? "4px 4px" : noiseType === "vhs" ? "14px 14px" : "9px 9px";
    const scanlineSize = noiseType === "vhs" ? "100% 3px" : "100% 4px";
    layers.push(
      <AbsoluteFill key="visionNoise" style={{pointerEvents: "none", opacity: flickerOpacity}}>
        {showTint ? <AbsoluteFill style={{background: hexToRgba(tint, (noiseType === "glitch" ? 0.34 : 0.28) * strength), mixBlendMode: "screen"}} /> : null}
        <AbsoluteFill
          style={{
            backgroundImage: [
              `repeating-linear-gradient(0deg, rgba(255,255,255,${scanlineOpacity * scanline}) 0px, rgba(255,255,255,${scanlineOpacity * scanline}) 1px, rgba(0,0,0,${darkScanlineOpacity * scanline}) 2px, transparent 4px)`,
              `radial-gradient(circle at ${20 + noiseX}% ${30 + noiseY}%, rgba(255,255,255,${dotWhite * strength}) 0 1px, transparent 2px)`,
              `radial-gradient(circle at ${80 - noiseY}% ${70 - noiseX}%, rgba(0,0,0,${dotBlack * strength}) 0 1px, transparent 2px)`,
              `radial-gradient(circle at ${45 + noiseY}% ${15 + noiseX}%, rgba(0,0,0,${0.32 * strength}) 0 1px, transparent 2px)`,
              noiseType === "snow"
                ? `radial-gradient(circle at ${10 + noiseY}% ${85 - noiseX}%, rgba(255,255,255,${0.75 * strength}) 0 1px, transparent 2px)`
                : `linear-gradient(90deg, transparent 0%, rgba(255,255,255,${0.08 * glitch}) 50%, transparent 100%)`,
            ].join(", "),
            backgroundSize: `${scanlineSize}, ${dotSizeA}, ${dotSizeB}, 13px 13px, ${noiseType === "snow" ? "5px 5px" : "100% 100%"}`,
            backgroundPosition: `0 ${noiseY}px, ${noiseX}px ${noiseY}px, ${-noiseX}px ${noiseY}px, ${noiseY}px ${-noiseX}px, ${-noiseY}px ${noiseX}px`,
            opacity: 0.55 + strength * 0.45,
          }}
        />
        {glitch > 0 ? <>
          <div style={{position: "absolute", left: -width * 0.02 + glitchShift, top: height * (0.18 + (Math.sin(elapsed * 3.1) + 1) * 0.08), width: width * 1.04, height: 24 + glitch * (noiseType === "glitch" ? 72 : 42), background: noiseType === "snow" ? `rgba(255,255,255,${0.18 * glitch})` : hexToRgba(tint, (noiseType === "glitch" ? 0.62 : 0.45) * glitch), transform: `translateX(${glitchShift}px)`, mixBlendMode: "screen"}} />
          <div style={{position: "absolute", left: -width * 0.02 - glitchShift, top: height * (0.58 + (Math.sin(elapsed * 4.7) + 1) * 0.06), width: width * 1.04, height: 16 + glitch * (noiseType === "glitch" ? 58 : 34), background: noiseType === "vhs" ? `rgba(0,0,0,${0.28 * glitch})` : `rgba(255,80,130,${(noiseType === "glitch" ? 0.44 : 0.3) * glitch})`, transform: `translateX(${-glitchShift}px)`, mixBlendMode: noiseType === "vhs" ? "multiply" : "screen"}} />
        </> : null}
      </AbsoluteFill>,
    );
  }

  return layers.length ? <>{layers}</> : null;
};

export const StageVideoV2: React.FC<StageVideoV2Props> = ({
  story,
  scenes,
  manifest,
  audio,
  expressions,
  poses,
  seMap,
  mobs,
}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const seconds = frame / fps;
  const audioSrc = audio ?? story.audio ?? "story-01.wav";
  const audioQueryIndex = audioSrc.indexOf("?");
  const audioBase = audioQueryIndex >= 0 ? audioSrc.slice(0, audioQueryIndex) : audioSrc;
  const audioSuffix = audioQueryIndex >= 0 ? audioSrc.slice(audioQueryIndex) : "";
  // 再生が圧縮音声でも、解析は同名wavを優先する。既存描画と同じく、
  // プレビュー中の再生音声と解析音声の干渉を避けるためである。
  const analysisAudio = !/\.wav$/i.test(audioBase)
    ? `${audioBase.replace(/\.[^.]+$/i, ".wav")}${audioSuffix}`
    : audioSrc;
  const {audioData, dataOffsetInSeconds} = useWindowedAudioData({
    src: mediaStaticSrc(analysisAudio),
    frame,
    fps,
    windowInSeconds: 1,
  });
  const overlays = activeOverlays(story, seconds);
  const turnIndex = activeTurnIndex(story, seconds);
  const turn = story.script[turnIndex];
  const sceneTransition = sceneTransitionVisual(turn, seconds, width);
  const state = resolveStageStateAtTurn(story, turnIndex);
  const scene = scenes.scenes[state.scene];

  if (!scene) {
    return <AbsoluteFill style={{background: "#1b1b1f", color: "white", alignItems: "center", justifyContent: "center", fontSize: 42}}>
      <Audio src={mediaStaticSrc(audioSrc)} />
      未登録シーン: {state.scene}
    </AbsoluteFill>;
  }

  const nextTurn = story.script[turnIndex + 1];
  const cameraTurnIndex = nextTurn
    && typeof turn.end === "number"
    && typeof nextTurn.start === "number"
    && seconds >= turn.end
    && seconds < nextTurn.start
    && nextTurn.scene === turn.scene
    && nextTurn.cameraTransition !== "cut"
    ? turnIndex + 1
    : turnIndex;
  const cameraTurn = story.script[cameraTurnIndex];
  const cameraState = cameraTurnIndex === turnIndex ? state : resolveStageStateAtTurn(story, cameraTurnIndex);
  const cameraScene = scenes.scenes[cameraState.scene];
  const framing = cameraScene
    ? resolveFraming(cameraState.framing, cameraScene, cameraState, cameraTurn.speaker)
    : undefined;
  const motionFrame = applyCameraMotion(framing, cameraState.cameraMotion);
  const previousCameraTurn = cameraTurnIndex > 0 ? story.script[cameraTurnIndex - 1] : undefined;
  const previousCameraState = cameraTurnIndex > 0 ? resolveStageStateAtTurn(story, cameraTurnIndex - 1) : undefined;
  const previousCameraScene = previousCameraState ? scenes.scenes[previousCameraState.scene] : undefined;
  const previousFrame = previousCameraState && previousCameraScene
    ? applyCameraMotion(
      resolveFraming(previousCameraState.framing, previousCameraScene, previousCameraState, previousCameraTurn!.speaker),
      previousCameraState.cameraMotion,
    )
    : undefined;
  const canSmoothCamera = !!previousCameraTurn
    && previousCameraTurn.scene === cameraTurn.scene
    && isStandardDisplayMode(previousCameraTurn.displayMode?.kind)
    && isStandardDisplayMode(cameraTurn.displayMode?.kind)
    && cameraTurn.cameraTransition !== "cut";
  const transitionStart = previousCameraTurn?.end ?? cameraTurn.start ?? seconds;
  const transitionEnd = (cameraTurn.start ?? transitionStart) + 0.8;
  const previousTransform = stageTransformValues(width, height, previousFrame);
  const targetTransform = stageTransformValues(width, height, motionFrame);
  const transitionProgress = canSmoothCamera && previousTransform && targetTransform
    ? easeInOutCubic((seconds - transitionStart) / Math.max(transitionEnd - transitionStart, 0.001))
    : 1;
  const transform = canSmoothCamera && previousTransform && targetTransform
    ? `translate(${previousTransform.tx + (targetTransform.tx - previousTransform.tx) * transitionProgress}px, ${previousTransform.ty + (targetTransform.ty - previousTransform.ty) * transitionProgress}px) scale(${previousTransform.scale + (targetTransform.scale - previousTransform.scale) * transitionProgress})`
    : stageTransform(width, height, motionFrame);
  const cameraTilt = canSmoothCamera
    ? (previousCameraState?.cameraMotion?.tilt ?? 0) + ((cameraState.cameraMotion?.tilt ?? 0) - (previousCameraState?.cameraMotion?.tilt ?? 0)) * transitionProgress
    : (cameraState.cameraMotion?.tilt ?? 0);
  const motion = state.cameraMotion;
  const shakeOffset = cameraShakeOffset(motion?.shake, seconds, turn.start ?? 0);
  const tiltedTransform = `${transform ?? ""}${cameraTilt ? ` rotate(${cameraTilt}deg)` : ""}` || undefined;
  const enterById = new Map((turn.stage?.enter ?? []).map((item) => [item.instanceId, item]));
  const exitById = new Map((turn.stage?.exit ?? []).map((item) => [stageExitId(item), item]));
  const previousState = turnIndex > 0 ? resolveStageStateAtTurn(story, turnIndex - 1) : undefined;
  const exitingInstances: ResolvedInstanceV2[] = previousState
    ? Object.entries(previousState.instances)
      .filter(([instanceId]) => {
        const exitItem = exitById.get(instanceId);
        if (!exitItem || state.instances[instanceId]) return false;
        return stageAnimationDirection(exitItem) !== "instant" || typeof turn.end !== "number" || seconds < turn.end;
      })
      .map(([, instance]) => ({
        ...instance,
        visible: state.displayMode.kind === "standard" && !turn.hideCharacters,
      }))
    : [];
  const stagePeople = [...Object.values(state.instances), ...exitingInstances];
  const currentSpeaker = state.instances[turn.speaker] ?? exitingInstances.find((instance) => instance.instanceId === turn.speaker);
  const speakerDefinition = story.instances[turn.speaker];
  const speakerPosition = currentSpeaker
    ? placementOrigin(currentSpeaker.placement, scene.layouts.standard)
    : undefined;
  const displaySettings = displaySettingsOf(story.displaySettings);
  const captionVisual = captionVisualFor(story.script, turnIndex, seconds);
  const speakerCharacterId = speakerDefinition?.characterId;
  const speakerBubbleColor = speakerCharacterId === "zundamon"
    ? displaySettings.speakerColors.zundamon
    : speakerCharacterId === "metan"
      ? displaySettings.speakerColors.metan
      : displaySettings.speakerColors.default;
  const continueRange = continuedBubbleRange(story.script, turnIndex);
  const continuedTurns = story.script.slice(continueRange.start, continueRange.end + 1);
  const hasBubbleText = !!turn.text.trim();
  const isSubtitle = turn.subtitleMode === "subtitle";
  const isStandardDialogue = state.displayMode.kind === "standard" && !turn.hideBubble && hasBubbleText;
  let speakerAmp = 0;
  if (audioData) {
    const wave = audioData.channelWaveforms[0];
    const sampleRate = audioData.sampleRate;
    const center = Math.floor((seconds - dataOffsetInSeconds) * sampleRate);
    const windowSize = Math.floor(sampleRate / fps);
    let sum = 0;
    let samples = 0;
    for (let index = center - windowSize / 2; index < center + windowSize / 2; index += 1) {
      if (index >= 0 && index < wave.length) {
        sum += wave[index] * wave[index];
        samples += 1;
      }
    }
    speakerAmp = Math.min(1, Math.sqrt(sum / Math.max(1, samples)) * LIPSYNC_GAIN);
  }
  const speakerMouthAmplitude = turn.noLipSync ? 0 : speakerAmp;
  const effectStart = turn.start ?? seconds;
  const effectEnd = typeof turn.end === "number" ? turn.end : effectStart + 1;
  const effectElapsed = Math.max(0, seconds - effectStart);
  const effectProgress = clamp(effectElapsed / Math.max(effectEnd - effectStart, 0.001), 0, 1);
  const hasZoomPunch = stageEffectEnabled(turn.effects?.zoomPunch);
  const zoomPunchCfg = stageEffectConfig(turn.effects?.zoomPunch, DEFAULT_STAGE_EFFECT_SETTINGS.zoomPunch);
  const zoomPunchLocal = hasZoomPunch ? clamp(effectElapsed / Math.max(zoomPunchCfg.duration, 0.001), 0, 1) : 1;
  const zoomPunchScale = hasZoomPunch ? 1 + Math.sin(zoomPunchLocal * Math.PI) * Math.max(0, zoomPunchCfg.scale - 1) : 1;
  const flashbackAt = (item: StageTurnV2 | undefined) => stageEffectEnabled(item?.effects?.flashback);
  const hasFlashback = flashbackAt(turn);
  const flashbackFilter = hasFlashback ? `saturate(${FLASHBACK_SATURATE}) brightness(${FLASHBACK_BRIGHTNESS})` : undefined;
  const prevFlashback = flashbackAt(story.script[turnIndex - 1]);
  const nextFlashback = flashbackAt(story.script[turnIndex + 1]);
  const flashbackInFade = hasFlashback !== prevFlashback ? 1 - clamp(effectElapsed / FLASHBACK_DISSOLVE_SECONDS, 0, 1) : 0;
  const flashbackOutFade = hasFlashback !== nextFlashback ? 1 - clamp(((effectEnd - seconds) / FLASHBACK_DISSOLVE_SECONDS), 0, 1) : 0;
  const flashbackWhiteFadeOpacity = clamp(Math.max(flashbackInFade, flashbackOutFade), 0, 1);
  const stageShellTransform = [
    shakeOffset.x || shakeOffset.y ? `translate(${shakeOffset.x}px, ${shakeOffset.y}px)` : "",
    zoomPunchScale !== 1 ? `scale(${zoomPunchScale})` : "",
  ].filter(Boolean).join(" ") || undefined;
  const insertDisplay = displayModeInsert(state.displayMode);

  const renderWhiteboardPresenter = () => {
    if (state.displayMode.kind !== "whiteboard") return undefined;
    const instance = state.instances[state.displayMode.presenterId ?? ""];
    const characterId = instance?.definition.characterId;
    const main = characterId ? MAIN_CHARACTERS[characterId] : undefined;
    const mob = characterId ? mobs?.[characterId] : undefined;
    if (!instance || (!main && !mob)) return undefined;
    const layout = getWhiteboardExplainLayout(width, height, state.displayMode.whiteboard.layout === "compact" ? "compact" : "default");
    const isSpeaker = instance.instanceId === turn.speaker;
    if (mob) {
      const image = mobImageForState(mob, instance.expression, isSpeaker, speakerMouthAmplitude);
      if (!image) return undefined;
      const flip = resolvedFlip(instance, mob.flip ?? false);
      return <Img src={staticFile(image)} style={{position: "absolute", left: "50%", bottom: 0, width: "100%", height: "100%", objectFit: "contain", transform: `translateX(-50%) scaleX(${flip ? -1 : 1})`, transformOrigin: "bottom center"}} />;
    }
    if (!main) return undefined;
    const expression: ExpressionCfg | null = expressions?.[main.avatar]?.[instance.expression ?? "normal"]
      ?? expressions?.[main.avatar]?.normal
      ?? null;
    const pose = instance.pose ? poses?.[main.avatar]?.[instance.pose] : undefined;
    return (
      <div style={{position: "absolute", left: "50%", bottom: 0, transform: `translateX(-50%) scale(${layout.character.width / 445})`, transformOrigin: "bottom center"}}>
        <Avatar
          dir={main.avatar}
          manifest={manifest?.[main.avatar]}
          fallbackGender={main.gender}
          active={isSpeaker}
          activatedAtFrame={Math.round((turn.start ?? 0) * fps)}
          amplitude={isSpeaker ? speakerMouthAmplitude : 0}
          emotion="normal"
          emotionAtFrame={Math.round((turn.start ?? 0) * fps)}
          expressive={!!main.expressive}
          flip={resolvedFlip(instance)}
          popScale={false}
          expressionCfg={expression}
          poseName={instance.pose as ExpressionCfg["pose"]}
          poseArmStem={pose?.arm ?? null}
          poseSpeed={pose?.speed ?? null}
          poseStrength={pose?.strength ?? null}
        />
      </div>
    );
  };

  const renderZunMeet = () => {
    if (state.displayMode.kind !== "zunMeet") return null;
    const meeting = state.displayMode.zunMeet;
    const requestedFocusId = meeting.activeSpeakerId ?? turn.speaker;
    const focusedId = meeting.layout === "focus"
      ? meeting.participants.some((participant) => participant.instanceId === requestedFocusId)
        ? requestedFocusId
        : meeting.participants[0]?.instanceId
      : undefined;
    const ordered = focusedId
      ? [...meeting.participants].sort((a, b) => Number(b.instanceId === focusedId) - Number(a.instanceId === focusedId))
      : meeting.participants;
    const columns = meeting.layout === "focus"
      ? Math.max(2, ordered.length - 1)
      : ordered.length <= 2 ? ordered.length : 2;
    return (
      <AbsoluteFill style={{background: "#171b24", padding: 42, gap: 22, color: "white", fontFamily: "sans-serif"}}>
        <div style={{height: 58, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px", borderRadius: 12, background: "#242b38", fontSize: 28, fontWeight: 700}}>
          <span>{meeting.room || "ZunMeet"}</span><span style={{color: "#9fb2cf", fontSize: 20}}>● 録画中</span>
        </div>
        <div style={{flex: 1, display: "grid", gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: 22}}>
          {ordered.map((participant, index) => {
            const definition = story.instances[participant.instanceId];
            const stageInstance = state.instances[participant.instanceId];
            const characterId = definition?.characterId;
            const main = characterId ? MAIN_CHARACTERS[characterId] : undefined;
            const mob = characterId ? mobs?.[characterId] : undefined;
            const active = participant.instanceId === (meeting.activeSpeakerId ?? turn.speaker);
            const expression: ExpressionCfg | null = main && stageInstance
              ? expressions?.[main.avatar]?.[stageInstance.expression ?? "normal"] ?? expressions?.[main.avatar]?.normal ?? null
              : null;
            const isFocusTile = meeting.layout === "focus" && index === 0;
            const isFullRow = isFocusTile || (meeting.layout === "focus" && ordered.length === 2 && index === 1);
            return <div key={participant.instanceId} style={{position: "relative", overflow: "hidden", minHeight: 0, gridColumn: isFullRow ? "1 / -1" : undefined, borderRadius: 18, border: active ? "5px solid #4e9cff" : "2px solid #3a4352", background: participant.cameraOff ? "#303846" : "linear-gradient(135deg,#496985,#172534)", display: "flex", alignItems: "center", justifyContent: "center"}}>
              {!participant.cameraOff && main ? <div style={{transform: `scale(${isFocusTile ? ".98" : ".72"})`, transformOrigin: "bottom center", alignSelf: "end"}}><Avatar dir={main.avatar} manifest={manifest?.[main.avatar]} fallbackGender={main.gender} active={active} activatedAtFrame={Math.round((turn.start ?? 0) * fps)} amplitude={participant.instanceId === turn.speaker ? speakerMouthAmplitude : 0} emotion="normal" emotionAtFrame={Math.round((turn.start ?? 0) * fps)} expressive={!!main.expressive} flip={resolvedFlip(stageInstance)} popScale={false} expressionCfg={expression} poseName={stageInstance?.pose as ExpressionCfg["pose"]} /></div> : !participant.cameraOff && mob ? <Img src={staticFile(mobImageForState(mob, stageInstance?.expression, participant.instanceId === turn.speaker, speakerMouthAmplitude) ?? mob.images.normal?.closed)} style={{width: "auto", height: isFocusTile ? 560 : 260, maxWidth: "70%", objectFit: "contain", alignSelf: "end", transform: `scaleX(${resolvedFlip(stageInstance, mob.flip ?? false) ? -1 : 1})`, transformOrigin: "bottom center"}} /> : <div style={{width: isFocusTile ? 180 : 140, height: isFocusTile ? 180 : 140, borderRadius: "50%", background: "#66758b", display: "grid", placeItems: "center", fontSize: isFocusTile ? 82 : 64}}>{participant.cameraOff ? "◉" : "?"}</div>}
              <div style={{position: "absolute", left: 14, right: 14, bottom: 14, display: "flex", justifyContent: "space-between", fontSize: 24, fontWeight: 700, textShadow: "0 2px 4px #000"}}><span>{participant.name || definition?.label || participant.instanceId}</span><span>{participant.muted ? "🔇" : "🎙"}</span></div>
            </div>;
          })}
        </div>
      </AbsoluteFill>
    );
  };

  const people = stagePeople
    .filter((instance) => instance.visible)
    .map((instance) => {
      const origin = placementOrigin(instance.placement, scene.layouts.standard);
      if (!origin) return null;
      const exitItem = exitById.get(instance.instanceId);
      const enterItem = enterById.get(instance.instanceId);
      // 配置(manualPos相当)がターン間で変わった個体だけ、旧レンダラーと同じ0.6秒補間で新位置へ寄せる。
      // 新規登場・退場中の個体は対象外（!exitItem && !enterItemで自然に排他になる。登場/退場アニメと二重にしない）。
      const previousInstance = !exitItem && !enterItem && previousState?.scene === state.scene
        ? previousState.instances[instance.instanceId]
        : undefined;
      const previousOrigin = previousInstance ? placementOrigin(previousInstance.placement, scene.layouts.standard) : undefined;
      const displayOrigin = previousOrigin && typeof turn.start === "number" && (previousOrigin.x !== origin.x || previousOrigin.y !== origin.y)
        ? {
          x: lerp(previousOrigin.x, origin.x, easeInOutCubic((seconds - turn.start) / MANUAL_POS_TRANSITION_SECONDS)),
          y: lerp(previousOrigin.y, origin.y, easeInOutCubic((seconds - turn.start) / MANUAL_POS_TRANSITION_SECONDS)),
        }
        : origin;
      const animationOffset = exitItem
        ? stageAnimationOffset({
          direction: stageAnimationDirection(exitItem),
          phase: "exit",
          originX: origin.x,
          seconds,
          turn,
          width,
          height,
        })
        : enterItem
          ? stageAnimationOffset({
            direction: stageAnimationDirection(enterItem),
            phase: "enter",
            originX: origin.x,
            seconds,
            turn,
            width,
            height,
          })
          : {x: 0, y: 0};
      const baseTransform = `translate(-50%, -100%)${animationTranslate(animationOffset)}`;
      const slot = instance.placement.mode === "slot" ? scene.layouts.standard.slots[instance.placement.slotId] : undefined;
      const scale = instance.placement.mode === "manual"
        ? instance.placement.scale ?? 1
        : slot?.scale ?? 1;
      const zIndex = instance.zIndex ?? (instance.placement.mode === "manual"
        ? instance.placement.zIndex ?? 0
        : slot?.zIndex ?? 0);
      const characterId = instance.definition.characterId;
      if (!characterId) return null;
      const main = MAIN_CHARACTERS[characterId];
      const isSpeaker = instance.instanceId === turn.speaker;

      if (main) {
        const fullFigure = scene.figure === "full";
        const avatarDir = fullFigure ? `${main.avatar}/full` : main.avatar;
        const manifestKey = fullFigure ? `${main.avatar}_full` : main.avatar;
        const box = fullFigure ? fullBoxSize(main.avatar) : undefined;
        const expression: ExpressionCfg | null = expressions?.[main.avatar]?.[instance.expression ?? "normal"]
          ?? expressions?.[main.avatar]?.normal
          ?? null;
        const pose = instance.pose ? poses?.[main.avatar]?.[instance.pose] : undefined;
        return (
          <div key={instance.instanceId} style={{position: "absolute", left: displayOrigin.x * width, top: displayOrigin.y * height, zIndex, transform: baseTransform}}>
            <div style={{transform: `scale(${scale})`, transformOrigin: "bottom center"}}>
              <Avatar
                dir={avatarDir}
                manifest={manifest?.[manifestKey]}
                fallbackGender={main.gender}
                active={isSpeaker}
                activatedAtFrame={Math.round((turn.start ?? 0) * fps)}
                amplitude={isSpeaker ? speakerMouthAmplitude : 0}
                emotion="normal"
                emotionAtFrame={Math.round((turn.start ?? 0) * fps)}
                expressive={!!main.expressive}
                flip={resolvedFlip(instance, origin.x < 0.5)}
                popScale={false}
                expressionCfg={expression}
                poseName={instance.pose as ExpressionCfg["pose"]}
                poseArmStem={pose?.arm ?? null}
                poseSpeed={pose?.speed ?? null}
                poseStrength={pose?.strength ?? null}
                boxWidth={box?.w}
                boxHeight={box?.h}
              />
            </div>
          </div>
        );
      }

      const mob = mobs?.[characterId];
      const image = mobImageForState(mob, instance.expression, isSpeaker, speakerMouthAmplitude);
      if (!mob || !image) return null;
      // face は向きの明示指定として最優先。未指定なら個体のflip、さらに未指定なら
      // モブ素材の既定flip（なければ主役と同じ左右slotの既定）を使う。
      const flip = resolvedFlip(instance, mob.flip ?? (origin.x < 0.5));
      return (
        <div key={instance.instanceId} style={{position: "absolute", left: displayOrigin.x * width, top: displayOrigin.y * height, zIndex, transform: baseTransform}}>
          <div style={{transform: `scale(${flip ? -1 : 1}, 1)`, transformOrigin: "bottom center"}}>
            <Img src={staticFile(image)} style={{height: 760 * (mob.scale ?? 1) * scale, width: "auto", display: "block"}} />
          </div>
        </div>
      );
    });
  const bgBlur = clamp(Number(scene.bgBlur ?? 0), 0, 64);
  const bgStyle = {
    position: "absolute" as const,
    inset: 0,
    zIndex: 0,
    width: "100%",
    height: "100%",
    objectFit: "cover" as const,
    filter: bgBlur > 0 ? `blur(${bgBlur}px)` : undefined,
    transform: bgBlur > 0 ? `scale(${1 + Math.min(bgBlur, 32) / 180})` : undefined,
    transformOrigin: "center center",
  };

  return (
    <AbsoluteFill style={{background: "#111", overflow: "hidden"}}>
      <Audio src={mediaStaticSrc(audioSrc)} />
      <V2BgmLayer regions={story.bgm} fps={fps} />
      <V2SeLayer script={story.script} seMap={seMap} fps={fps} />
      <AbsoluteFill style={sceneTransition?.contentStyle}>
        <AbsoluteFill style={{filter: flashbackFilter}}>
          {insertDisplay ? (
            <InsertOverlay insert={insertDisplay} bgOpacity={1} opacity={1} />
          ) : state.displayMode.kind === "zunMeet" ? renderZunMeet() : state.displayMode.kind === "whiteboard" ? (
            <WhiteboardExplainInsert
              config={state.displayMode.whiteboard}
              width={width}
              height={height}
              durationInFrames={Math.max(1, Math.round(((turn.end ?? turn.start ?? 0) - (turn.start ?? 0)) * fps))}
              localFrame={Math.max(0, frame - Math.round((turn.start ?? 0) * fps))}
              characterSlot={renderWhiteboardPresenter()}
            />
          ) : (
            <AbsoluteFill style={{transform: stageShellTransform, transformOrigin: "50% 50%", overflow: "hidden"}}>
              <AbsoluteFill style={{transform: tiltedTransform, transformOrigin: "0 0", overflow: "hidden"}}>
                {scene.bgVideo ? (
                  <Video src={staticFile(scene.bgVideo)} muted loop={scene.bgVideoLoop === true} style={bgStyle} />
                ) : scene.bg ? (
                  <Img src={staticFile(scene.bg)} style={bgStyle} />
                ) : null}
                <AbsoluteFill style={{zIndex: 10}}>{people}</AbsoluteFill>
                {scene.front ? <Img src={staticFile(scene.front)} style={{position: "absolute", inset: 0, zIndex: 20, width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none"}} /> : null}
              </AbsoluteFill>
            </AbsoluteFill>
          )}
        </AbsoluteFill>
        {overlays.length > 0 ? <V2OverlayLayer overlays={overlays} /> : null}
        <V2EffectsLayer turn={turn} elapsed={effectElapsed} progress={effectProgress} width={width} height={height} onlyImpactLines />
        {isStandardDialogue && isSubtitle ? (() => {
          const style = turn.subtitleStyle ?? {};
          const fontSize = Number.isFinite(Number(style.fontSize)) ? clamp(Math.round(Number(style.fontSize)), 24, 96) : displaySettings.subtitle.fontSize;
          const textColor = validHex(style.textColor, displaySettings.subtitle.textColor);
          const borderColor = validHex(style.boxBorderColor, displaySettings.subtitle.borderColor);
          const borderWidth = Number.isFinite(Number(style.boxBorderWidth)) ? clamp(Number(style.boxBorderWidth), 0.5, 6) : displaySettings.subtitle.borderWidth;
          const texts = continuedTurns.length > 1
            ? continuedTurns.map((item) => subtitleProgressiveText(item, seconds))
            : [subtitleProgressiveText(turn, seconds)];
          return <div style={{position: "absolute", zIndex: 30, left: "50%", bottom: displaySettings.subtitle.bottom, width: Math.min(width * displaySettings.subtitle.width, 1360), transform: "translateX(-50%)", padding: "16px 28px 18px", borderRadius: 18, background: hexToRgba(displaySettings.subtitle.bgColor, displaySettings.subtitle.bgOpacity), border: (style.boxBorder ?? displaySettings.subtitle.border) ? `${borderWidth}px solid ${borderColor}` : "none", boxShadow: "0 14px 34px rgba(0,0,0,.4)", color: textColor, fontSize, lineHeight: 1.45, fontWeight: 700, fontFamily: displaySettings.subtitle.fontFamily, textAlign: "center", whiteSpace: "pre-wrap", textShadow: "0 2px 6px rgba(0,0,0,.4)"}}>{texts.join("\n")}</div>;
        })() : null}
        {isStandardDialogue && !isSubtitle && speakerPosition && speakerDefinition?.role !== "voiceOnly" ? (() => {
          const autoGroups = sentenceGroups(turn);
          const usingContinuation = continuedTurns.length > 1;
          const texts = usingContinuation ? continuedTurns.map((item) => bubbleTextAt(item, seconds)) : autoGroups.map((group) => group.text);
          const visibleCount = usingContinuation ? turnIndex - continueRange.start + 1 : visibleSentenceGroupCount(turn, seconds);
          const stacked = texts.length > 1;
          // 複数段（連結/自動分割）のときは旧と同じく少し縮小する。箱幅の見積もりと実描画で必ず同じ値を使うこと。
          const bubbleFontSizeValue = bubbleFontSize(stacked, displaySettings.bubble.fontSize);
          const bubbleBaseMaxWidth = width * 0.72;
          const bubbleMaxWidth = bubbleMaxWidthForTurn(turn, width, stacked, bubbleBaseMaxWidth, displaySettings.bubble);
          const charLimit = bubbleWrapCharLimit(turn, displaySettings.bubble.maxChars);
          const metrics = texts.map((text) => bubbleMetricsV2(text, bubbleFontSizeValue, bubbleMaxWidth, charLimit));
          const groupWidth = metrics.reduce((largest, item) => Math.max(largest, item.width), 120);
          // カメラ遷移中はステージ本体のtransformと同じ式でtx/ty/scaleを補間し、吹き出しを追従させる（旧のfollowK相当）。
          const bubbleTransform = canSmoothCamera && previousTransform && targetTransform
            ? {
              tx: previousTransform.tx + (targetTransform.tx - previousTransform.tx) * transitionProgress,
              ty: previousTransform.ty + (targetTransform.ty - previousTransform.ty) * transitionProgress,
              scale: previousTransform.scale + (targetTransform.scale - previousTransform.scale) * transitionProgress,
            }
            : cameraTurnIndex !== turnIndex ? previousTransform : targetTransform;
          const screenX = bubbleTransform
            ? bubbleTransform.tx + speakerPosition.x * width * bubbleTransform.scale
            : speakerPosition.x * width;
          const side = screenX >= width * 0.52 ? "right" : "left";
          const groupCenterX = clamp(screenX, groupWidth / 2 + 20, width - groupWidth / 2 - 20);
          const bubbleStepX = side === "right" ? -18 : 18;
          const singleWidth = metrics[0].width;
          const singleLeft = side === "right" ? groupCenterX + singleWidth / 2 : groupCenterX - singleWidth / 2;
          // ズームが深いほど旧のzoomBubbleKと同じ式で吹き出しを上へ逃がす。
          const zoomBubbleK = bubbleTransform ? clamp((bubbleTransform.scale - 1) / 0.6, 0, 1) : 0;
          const bubbleTop = height * lerp(0.95, 0.9, zoomBubbleK);
          const singleBottomOffset = turn.continueBubble ? BUBBLE_CONTINUE_BOTTOM_OFFSET : BUBBLE_BOTTOM_OFFSET;
          return <div style={stacked
            ? {position: "absolute", zIndex: 30, left: groupCenterX, top: bubbleTop, width: groupWidth, transform: "translate(-50%, -100%)", display: "flex", flexDirection: "column", alignItems: side === "right" ? "flex-end" : "flex-start", gap: 6}
            : {position: "absolute", zIndex: 30, left: singleLeft, top: bubbleTop - singleBottomOffset, width: singleWidth, transform: side === "right" ? "translate(-100%, -100%)" : "translate(0, -100%)", display: "flex", flexDirection: "column", alignItems: side === "right" ? "flex-end" : "flex-start"}}>
            {texts.map((text, index) => <div key={`${turn.id}-bubble-${index}`} style={{visibility: index < visibleCount ? "visible" : "hidden", width: metrics[index].width, boxSizing: "border-box", padding: "14px 28px", borderRadius: displaySettings.bubble.radius, background: displaySettings.bubble.bgColor, color: displaySettings.bubble.textColor, border: `${displaySettings.bubble.borderWidth}px solid ${speakerBubbleColor}`, fontSize: bubbleFontSizeValue, fontWeight: 700, lineHeight: 1.3, fontFamily: displaySettings.bubble.fontFamily, textAlign: side, whiteSpace: "pre", boxShadow: "0 6px 18px rgba(0,0,0,.35)", transform: stacked ? `translateX(${index * bubbleStepX}px)` : undefined}}>{metrics[index].text}</div>)}
          </div>;
        })() : null}
        <V2EffectsLayer turn={turn} elapsed={effectElapsed} progress={effectProgress} width={width} height={height} hideImpactLines />
        {captionVisual && captionVisual.opacity > 0 ? (() => {
          const caption = captionVisual.caption;
          const telopX = typeof caption.x === "number" ? clamp(caption.x, 0, 1) : displaySettings.telop.x;
          const telopY = typeof caption.y === "number" ? clamp(caption.y, 0, 1) : displaySettings.telop.y;
          const telopSize = typeof caption.size === "number" ? clamp(caption.size, 0.5, 3) : displaySettings.telop.size;
          return <AbsoluteFill style={{pointerEvents: "none", zIndex: 40}}>
            <div style={{
              position: "absolute",
              left: Math.round(width * telopX),
              top: Math.round(height * telopY),
              background: "rgba(8, 8, 8, 0.6)",
              color: "#f4f0e8",
              fontSize: 84 * telopSize,
              fontWeight: 700,
              fontFamily: "sans-serif",
              letterSpacing: "0.12em",
              padding: `${Math.round(18 * telopSize)}px ${Math.round(56 * telopSize)}px`,
              borderRadius: 8,
              borderLeft: "10px solid #f4f0e8",
              boxShadow: "0 6px 20px rgba(0,0,0,0.4)",
              opacity: captionVisual.opacity,
            }}>{caption.text}</div>
          </AbsoluteFill>;
        })() : null}
        {flashbackWhiteFadeOpacity > 0 ? <AbsoluteFill style={{background: "#fff", opacity: flashbackWhiteFadeOpacity, pointerEvents: "none", zIndex: 60}} /> : null}
      </AbsoluteFill>
      {sceneTransition?.cover && sceneTransition.cover.opacity > 0 ? (
        <AbsoluteFill
          style={{
            background: sceneTransition.cover.color,
            opacity: sceneTransition.cover.opacity,
            pointerEvents: "none",
            zIndex: 70,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
