import React from "react";
import {AbsoluteFill, Audio, Img, staticFile, useCurrentFrame, useVideoConfig, Video} from "remotion";
import {Avatar} from "./Avatar";
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
import type {ExpressionsMap, MobsMap, PosesMap} from "./StoryVideo";

type Manifest = Record<string, Record<string, string>>;

export type StageVideoV2Props = {
  story: StoryV2;
  scenes: SceneLibraryV2;
  manifest?: Manifest;
  audio?: string;
  expressions?: ExpressionsMap;
  poses?: PosesMap;
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

export const StageVideoV2: React.FC<StageVideoV2Props> = ({
  story,
  scenes,
  manifest,
  audio,
  expressions,
  poses,
  mobs,
}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const seconds = frame / fps;
  const turnIndex = activeTurnIndex(story, seconds);
  const turn = story.script[turnIndex];
  const state = resolveStageStateAtTurn(story, turnIndex);
  const scene = scenes.scenes[state.scene];

  if (!scene) {
    return <AbsoluteFill style={{background: "#1b1b1f", color: "white", alignItems: "center", justifyContent: "center", fontSize: 42}}>
      {audio ? <Audio src={mediaStaticSrc(audio)} /> : null}
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
          amplitude={0}
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
              {!participant.cameraOff && main ? <div style={{transform: `scale(${isFocusTile ? ".98" : ".72"})`, transformOrigin: "bottom center", alignSelf: "end"}}><Avatar dir={main.avatar} manifest={manifest?.[main.avatar]} fallbackGender={main.gender} active={active} activatedAtFrame={Math.round((turn.start ?? 0) * fps)} amplitude={0} emotion="normal" emotionAtFrame={Math.round((turn.start ?? 0) * fps)} expressive={!!main.expressive} flip={resolvedFlip(stageInstance)} popScale={false} expressionCfg={expression} poseName={stageInstance?.pose as ExpressionCfg["pose"]} /></div> : <div style={{width: isFocusTile ? 180 : 140, height: isFocusTile ? 180 : 140, borderRadius: "50%", background: "#66758b", display: "grid", placeItems: "center", fontSize: isFocusTile ? 82 : 64}}>{participant.cameraOff ? "◉" : "?"}</div>}
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
                amplitude={0}
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
      const image = mob?.images[instance.expression ?? "normal"]?.closed ?? mob?.images.normal?.closed;
      if (!mob || !image) return null;
      return (
        <div key={instance.instanceId} style={{position: "absolute", left: origin.x * width, top: origin.y * height, zIndex, transform: `translate(-50%, -100%) scale(${mob.flip || instance.flip ? -1 : 1}, 1)`}}>
          <Img src={staticFile(image)} style={{height: 760 * (mob.scale ?? 1) * scale, width: "auto", display: "block"}} />
        </div>
      );
    });

  return (
    <AbsoluteFill style={{background: "#111", overflow: "hidden"}}>
      {audio ? <Audio src={mediaStaticSrc(audio)} /> : null}
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
      {state.displayMode.kind === "standard" && speakerPosition && currentSpeaker?.definition.role !== "voiceOnly" ? (
        <div style={{position: "absolute", zIndex: 30, left: `${speakerPosition.x * 100}%`, bottom: 36, maxWidth: width * 0.48, transform: "translateX(-50%)", padding: "14px 22px", borderRadius: 18, background: "white", color: "#222", fontSize: 42, fontWeight: 700, lineHeight: 1.3, textAlign: "center", boxShadow: "0 6px 18px rgba(0,0,0,.35)"}}>
          {turn.text}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
