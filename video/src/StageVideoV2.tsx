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
  type SceneLibraryV2,
  type StoryV2,
} from "./stage-v2";
import type {BgmRegion, ExpressionsMap, MobsMap, PosesMap, SeMap, SeMapEntry, StoryOverlay, TurnSe} from "./StoryVideo";

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

function mediaStaticSrc(path: string): string {
  const qidx = path.indexOf("?");
  if (qidx < 0) return staticFile(path);
  return `${staticFile(path.slice(0, qidx))}${path.slice(qidx)}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
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
  const motion = state.cameraMotion;
  const currentSpeaker = state.instances[turn.speaker];
  const speakerPosition = currentSpeaker
    ? placementOrigin(currentSpeaker.placement, scene.layouts.standard)
    : undefined;
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
    if (!instance || !main) return undefined;
    const layout = getWhiteboardExplainLayout(width, height, state.displayMode.whiteboard.layout === "compact" ? "compact" : "default");
    const expression: ExpressionCfg | null = expressions?.[main.avatar]?.[instance.expression ?? "normal"]
      ?? expressions?.[main.avatar]?.normal
      ?? null;
    const pose = instance.pose ? poses?.[main.avatar]?.[instance.pose] : undefined;
    const isSpeaker = instance.instanceId === turn.speaker;
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
            const main = definition?.characterId ? MAIN_CHARACTERS[definition.characterId] : undefined;
            const active = participant.instanceId === (meeting.activeSpeakerId ?? turn.speaker);
            const expression: ExpressionCfg | null = main && stageInstance
              ? expressions?.[main.avatar]?.[stageInstance.expression ?? "normal"] ?? expressions?.[main.avatar]?.normal ?? null
              : null;
            const isFocusTile = meeting.layout === "focus" && index === 0;
            const isFullRow = isFocusTile || (meeting.layout === "focus" && ordered.length === 2 && index === 1);
            return <div key={participant.instanceId} style={{position: "relative", overflow: "hidden", minHeight: 0, gridColumn: isFullRow ? "1 / -1" : undefined, borderRadius: 18, border: active ? "5px solid #4e9cff" : "2px solid #3a4352", background: participant.cameraOff ? "#303846" : "linear-gradient(135deg,#496985,#172534)", display: "flex", alignItems: "center", justifyContent: "center"}}>
              {!participant.cameraOff && main ? <div style={{transform: `scale(${isFocusTile ? ".98" : ".72"})`, transformOrigin: "bottom center", alignSelf: "end"}}><Avatar dir={main.avatar} manifest={manifest?.[main.avatar]} fallbackGender={main.gender} active={active} activatedAtFrame={Math.round((turn.start ?? 0) * fps)} amplitude={participant.instanceId === turn.speaker ? speakerMouthAmplitude : 0} emotion="normal" emotionAtFrame={Math.round((turn.start ?? 0) * fps)} expressive={!!main.expressive} flip={resolvedFlip(stageInstance)} popScale={false} expressionCfg={expression} poseName={stageInstance?.pose as ExpressionCfg["pose"]} /></div> : <div style={{width: isFocusTile ? 180 : 140, height: isFocusTile ? 180 : 140, borderRadius: "50%", background: "#66758b", display: "grid", placeItems: "center", fontSize: isFocusTile ? 82 : 64}}>{participant.cameraOff ? "◉" : "?"}</div>}
              <div style={{position: "absolute", left: 14, right: 14, bottom: 14, display: "flex", justifyContent: "space-between", fontSize: 24, fontWeight: 700, textShadow: "0 2px 4px #000"}}><span>{participant.name || definition?.label || participant.instanceId}</span><span>{participant.muted ? "🔇" : "🎙"}</span></div>
            </div>;
          })}
        </div>
      </AbsoluteFill>
    );
  };

  const people = Object.values(state.instances)
    .filter((instance) => instance.visible)
    .map((instance) => {
      const origin = placementOrigin(instance.placement, scene.layouts.standard);
      if (!origin) return null;
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
          <div key={instance.instanceId} style={{position: "absolute", left: origin.x * width, top: origin.y * height, zIndex, transform: "translate(-50%, -100%)"}}>
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
      const expressionImages = mob?.images[instance.expression ?? "normal"] ?? mob?.images.normal;
      const image = isSpeaker && speakerMouthAmplitude > MOUTH_HALF
        ? expressionImages?.open ?? expressionImages?.closed
        : expressionImages?.closed ?? expressionImages?.open;
      if (!mob || !image) return null;
      return (
        <div key={instance.instanceId} style={{position: "absolute", left: origin.x * width, top: origin.y * height, zIndex, transform: `translate(-50%, -100%) scale(${mob.flip || instance.flip ? -1 : 1}, 1)`}}>
          <Img src={staticFile(image)} style={{height: 760 * (mob.scale ?? 1) * scale, width: "auto", display: "block"}} />
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
        <AbsoluteFill style={{transform, transformOrigin: "0 0", overflow: "hidden", rotate: motion?.tilt ? `${motion.tilt}deg` : undefined}}>
          {scene.bgVideo ? (
            <Video src={staticFile(scene.bgVideo)} muted loop={scene.bgVideoLoop === true} style={{position: "absolute", inset: 0, zIndex: 0, width: "100%", height: "100%", objectFit: "cover"}} />
          ) : scene.bg ? (
            <Img src={staticFile(scene.bg)} style={{position: "absolute", inset: 0, zIndex: 0, width: "100%", height: "100%", objectFit: "cover"}} />
          ) : null}
          <AbsoluteFill style={{zIndex: 10}}>{people}</AbsoluteFill>
          {scene.front ? <Img src={staticFile(scene.front)} style={{position: "absolute", inset: 0, zIndex: 20, width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none"}} /> : null}
        </AbsoluteFill>
      )}
      {overlays.length > 0 ? <V2OverlayLayer overlays={overlays} /> : null}
      {state.displayMode.kind === "standard" && speakerPosition && currentSpeaker?.definition.role !== "voiceOnly" ? (
        <div style={{position: "absolute", zIndex: 30, left: `${speakerPosition.x * 100}%`, bottom: 36, maxWidth: width * 0.48, transform: "translateX(-50%)", padding: "14px 22px", borderRadius: 18, background: "white", color: "#222", fontSize: 42, fontWeight: 700, lineHeight: 1.3, textAlign: "center", boxShadow: "0 6px 18px rgba(0,0,0,.35)"}}>
          {turn.text}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
