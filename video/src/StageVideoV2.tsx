import React from "react";
import {AbsoluteFill, Audio, Img, Sequence, staticFile, useCurrentFrame, useVideoConfig, Video} from "remotion";
import {useWindowedAudioData} from "@remotion/media-utils";
import {Avatar, MOUTH_HALF} from "./Avatar";
import type {ExpressionCfg} from "./Avatar";
import type {Gender} from "./types";
import {WhiteboardExplainInsert, getWhiteboardExplainLayout} from "./inserts/whiteboardExplain";
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
import type {BgmRegion, ExpressionsMap, MobDef, MobsMap, PosesMap, SeMap, SeMapEntry, StoryOverlay, TurnSe} from "./StoryVideo";

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

const DEFAULT_DISPLAY_SETTINGS = {
  bubble: {maxChars: null as number | null, fontSize: 54, fontFamily: "sans-serif", textColor: "#1b1b1f", bgColor: "#ffffff", borderWidth: 5, radius: 18},
  subtitle: {fontSize: 46, fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif', textColor: "#ffffff", bgColor: "#080a0e", bgOpacity: 0.84, border: true, borderColor: "#ffffff", borderWidth: 2, bottom: 42, width: 0.84},
  speakerColors: {zundamon: "#5fb84f", metan: "#e87bb0", default: "#9aa0a6"},
};

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

function displaySettingsOf(settings?: StoryDisplaySettingsV2) {
  const bubble = settings?.bubble ?? {};
  const subtitle = settings?.subtitle ?? {};
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
  };
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

function mobImageForState(mob: MobDef | undefined, expression: string | undefined, speaking: boolean, amplitude: number) {
  const expressionImages = mob?.images[expression ?? "normal"] ?? mob?.images.normal;
  return speaking && amplitude > MOUTH_HALF
    ? expressionImages?.open ?? expressionImages?.closed
    : expressionImages?.closed ?? expressionImages?.open;
}

const V2BgmLayer: React.FC<{regions?: BgmRegion[]; fps: number}> = ({regions, fps}) => (
  <>
    {(regions ?? []).filter((region) => (
      region.file
      && Number.isFinite(region.start)
      && Number.isFinite(region.end)
      && region.end > region.start
    )).map((region, index) => {
      const durationInFrames = Math.max(1, Math.round((region.end - region.start) * fps));
      const fadeInFrames = Math.max(0, Math.round((region.fadeIn ?? 0.6) * fps));
      const fadeOutFrames = Math.max(0, Math.round((region.fadeOut ?? 0.6) * fps));
      const volume = region.volume ?? 0.25;
      return <Sequence key={`${region.file}-${index}`} from={Math.round(region.start * fps)} durationInFrames={durationInFrames}>
        <Audio
          src={staticFile(region.file)}
          loop
          volume={(localFrame) => volume * Math.max(
            0,
            Math.min(
              fadeInFrames > 0 ? localFrame / fadeInFrames : 1,
              fadeOutFrames > 0 ? (durationInFrames - localFrame) / fadeOutFrames : 1,
            ),
          )}
        />
      </Sequence>;
    })}
  </>
);

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
    if (turn.stage?.cameraMotion?.shake) tryAdd(seMap?.effect?.shake);
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
    && previousCameraTurn.displayMode?.kind !== "whiteboard"
    && previousCameraTurn.displayMode?.kind !== "zunMeet"
    && cameraTurn.displayMode?.kind !== "whiteboard"
    && cameraTurn.displayMode?.kind !== "zunMeet"
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
  const previousStateForExit = turnIndex > 0 ? resolveStageStateAtTurn(story, turnIndex - 1) : undefined;
  const exitingInstances: ResolvedInstanceV2[] = previousStateForExit
    ? Object.entries(previousStateForExit.instances)
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
          <div key={instance.instanceId} style={{position: "absolute", left: origin.x * width, top: origin.y * height, zIndex, transform: baseTransform}}>
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
        <div key={instance.instanceId} style={{position: "absolute", left: origin.x * width, top: origin.y * height, zIndex, transform: baseTransform}}>
          <div style={{transform: `scale(${flip ? -1 : 1}, 1)`, transformOrigin: "bottom center"}}>
            <Img src={staticFile(image)} style={{height: 760 * (mob.scale ?? 1) * scale, width: "auto", display: "block"}} />
          </div>
        </div>
      );
    });

  return (
    <AbsoluteFill style={{background: "#111", overflow: "hidden"}}>
      <Audio src={mediaStaticSrc(audioSrc)} />
      <V2BgmLayer regions={story.bgm} fps={fps} />
      <V2SeLayer script={story.script} seMap={seMap} fps={fps} />
      {state.displayMode.kind === "zunMeet" ? renderZunMeet() : state.displayMode.kind === "whiteboard" ? (
        <WhiteboardExplainInsert
          config={state.displayMode.whiteboard}
          width={width}
          height={height}
          durationInFrames={Math.max(1, Math.round(((turn.end ?? turn.start ?? 0) - (turn.start ?? 0)) * fps))}
          localFrame={Math.max(0, frame - Math.round((turn.start ?? 0) * fps))}
          characterSlot={renderWhiteboardPresenter()}
        />
      ) : (
        <AbsoluteFill style={{transform: shakeOffset.x || shakeOffset.y ? `translate(${shakeOffset.x}px, ${shakeOffset.y}px)` : undefined, overflow: "hidden"}}>
          <AbsoluteFill style={{transform: tiltedTransform, transformOrigin: "0 0", overflow: "hidden"}}>
            {scene.bgVideo ? (
              <Video src={staticFile(scene.bgVideo)} muted loop={scene.bgVideoLoop === true} style={{position: "absolute", inset: 0, zIndex: 0, width: "100%", height: "100%", objectFit: "cover"}} />
            ) : scene.bg ? (
              <Img src={staticFile(scene.bg)} style={{position: "absolute", inset: 0, zIndex: 0, width: "100%", height: "100%", objectFit: "cover"}} />
            ) : null}
            <AbsoluteFill style={{zIndex: 10}}>{people}</AbsoluteFill>
            {scene.front ? <Img src={staticFile(scene.front)} style={{position: "absolute", inset: 0, zIndex: 20, width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none"}} /> : null}
          </AbsoluteFill>
        </AbsoluteFill>
      )}
      {overlays.length > 0 ? <V2OverlayLayer overlays={overlays} /> : null}
      {isStandardDialogue && isSubtitle ? (() => {
        const style = turn.subtitleStyle ?? {};
        const fontSize = Number.isFinite(Number(style.fontSize)) ? clamp(Math.round(Number(style.fontSize)), 24, 96) : displaySettings.subtitle.fontSize;
        const textColor = validHex(style.textColor, displaySettings.subtitle.textColor);
        const borderColor = validHex(style.boxBorderColor, displaySettings.subtitle.borderColor);
        const borderWidth = Number.isFinite(Number(style.boxBorderWidth)) ? clamp(Number(style.boxBorderWidth), 0.5, 6) : displaySettings.subtitle.borderWidth;
        const texts = continuedTurns.map((item) => bubbleTextAt(item, seconds));
        return <div style={{position: "absolute", zIndex: 30, left: "50%", bottom: displaySettings.subtitle.bottom, width: Math.min(width * displaySettings.subtitle.width, 1360), transform: "translateX(-50%)", padding: "16px 28px 18px", borderRadius: 18, background: hexToRgba(displaySettings.subtitle.bgColor, displaySettings.subtitle.bgOpacity), border: (style.boxBorder ?? displaySettings.subtitle.border) ? `${borderWidth}px solid ${borderColor}` : "none", boxShadow: "0 14px 34px rgba(0,0,0,.4)", color: textColor, fontSize, lineHeight: 1.45, fontWeight: 700, fontFamily: displaySettings.subtitle.fontFamily, textAlign: "center", whiteSpace: "pre-wrap", textShadow: "0 2px 6px rgba(0,0,0,.4)"}}>{texts.join("\n")}</div>;
      })() : null}
      {isStandardDialogue && !isSubtitle && speakerPosition && speakerDefinition?.role !== "voiceOnly" ? (() => {
        const autoGroups = sentenceGroups(turn);
        const usingContinuation = continuedTurns.length > 1;
        const texts = usingContinuation ? continuedTurns.map((item) => bubbleTextAt(item, seconds)) : autoGroups.map((group) => group.text);
        const visibleCount = usingContinuation ? turnIndex - continueRange.start + 1 : visibleSentenceGroupCount(turn, seconds);
        const charLimit = turn.bubbleMaxChars ?? displaySettings.bubble.maxChars;
        const metrics = texts.map((text) => bubbleMetricsV2(text, displaySettings.bubble.fontSize, width * 0.48, charLimit));
        const stacked = texts.length > 1;
        const groupWidth = metrics.reduce((largest, item) => Math.max(largest, item.width), 120);
        const bubbleTransform = cameraTurnIndex !== turnIndex ? previousTransform : targetTransform;
        const screenX = bubbleTransform
          ? bubbleTransform.tx + speakerPosition.x * width * bubbleTransform.scale
          : speakerPosition.x * width;
        const side = screenX >= width * 0.52 ? "right" : "left";
        const groupCenterX = clamp(screenX, groupWidth / 2 + 20, width - groupWidth / 2 - 20);
        const bubbleStepX = side === "right" ? -18 : 18;
        const singleWidth = metrics[0].width;
        const singleLeft = side === "right" ? groupCenterX + singleWidth / 2 : groupCenterX - singleWidth / 2;
        return <div style={stacked
          ? {position: "absolute", zIndex: 30, left: groupCenterX, top: height * 0.95, width: groupWidth, transform: "translate(-50%, -100%)", display: "flex", flexDirection: "column", alignItems: side === "right" ? "flex-end" : "flex-start", gap: 6}
          : {position: "absolute", zIndex: 30, left: singleLeft, top: height * 0.95, width: singleWidth, transform: side === "right" ? "translate(-100%, -100%)" : "translate(0, -100%)", display: "flex", flexDirection: "column", alignItems: side === "right" ? "flex-end" : "flex-start"}}>
          {texts.map((text, index) => <div key={`${turn.id}-bubble-${index}`} style={{visibility: index < visibleCount ? "visible" : "hidden", width: metrics[index].width, boxSizing: "border-box", padding: "14px 22px", borderRadius: displaySettings.bubble.radius, background: displaySettings.bubble.bgColor, color: displaySettings.bubble.textColor, border: `${displaySettings.bubble.borderWidth}px solid ${speakerBubbleColor}`, fontSize: displaySettings.bubble.fontSize, fontWeight: 700, lineHeight: 1.3, fontFamily: displaySettings.bubble.fontFamily, textAlign: side, whiteSpace: "pre", boxShadow: "0 6px 18px rgba(0,0,0,.35)", transform: stacked ? `translateX(${index * bubbleStepX}px)` : undefined}}>{metrics[index].text}</div>)}
        </div>;
      })() : null}
    </AbsoluteFill>
  );
};
