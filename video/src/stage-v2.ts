import type {WhiteboardExplainInsertConfig} from "./inserts/whiteboardExplain";
import type {BgmRegion, StoryOverlay, TurnSe} from "./StoryVideo";

export type Point = {x: number; y: number};
export type CameraFrameV2 = {cx: number; cy: number; width: number};

export type StorySentenceV2 = {text: string; start: number; end: number};
export type SubtitleStyleV2 = {
  fontSize?: number;
  textColor?: string;
  boxBorder?: boolean;
  boxBorderColor?: string;
  boxBorderWidth?: number;
};
export type StoryDisplaySettingsV2 = {
  bubble?: {
    maxChars?: number | null;
    fontSize?: number;
    fontFamily?: string;
    textColor?: string;
    bgColor?: string;
    borderWidth?: number;
    radius?: number;
  };
  subtitle?: {
    fontSize?: number;
    fontFamily?: string;
    textColor?: string;
    bgColor?: string;
    bgOpacity?: number;
    border?: boolean;
    borderColor?: string;
    borderWidth?: number;
    bottom?: number;
    width?: number;
  };
  telop?: {
    x?: number;
    y?: number;
    size?: number;
  };
  speakerColors?: Record<string, string | undefined>;
};

export type CaptionV2 = {
  text: string;
  x?: number;
  y?: number;
  size?: number;
};

export type EffectToggleV2<T extends object = Record<string, never>> = boolean | ({enabled?: boolean} & T);
export type ImpactLinesEffectV2 = {
  cx?: number;
  cy?: number;
  count?: number;
  thickness?: number;
  opacity?: number;
  innerRadius?: number;
  start?: number;
  end?: number;
};
export type ZoomPunchEffectV2 = {
  scale?: number;
  duration?: number;
  borderStrength?: number;
};
export type QuoteFreezeEffectV2 = {
  fadeIn?: number;
  fadeOutStart?: number;
  fadeOutDuration?: number;
  backdropOpacity?: number;
};
export type FlashbackEffectV2 = Record<string, never>;
export type VisionNoiseEffectV2 = {
  type?: "future" | "snow" | "vhs" | "glitch";
  strength?: number;
  scanline?: number;
  glitch?: number;
  flicker?: number;
  tint?: string;
};
export type IrisOutEffectV2 = {
  cx?: number;
  cy?: number;
  startRadius?: number;
  closeStart?: number;
  closeEnd?: number;
  color?: string;
};
export type StageEffectsV2 = {
  impactLines?: EffectToggleV2<ImpactLinesEffectV2>;
  zoomPunch?: EffectToggleV2<ZoomPunchEffectV2>;
  quoteFreeze?: EffectToggleV2<QuoteFreezeEffectV2>;
  flashback?: EffectToggleV2<FlashbackEffectV2>;
  visionNoise?: EffectToggleV2<VisionNoiseEffectV2>;
  irisOut?: EffectToggleV2<IrisOutEffectV2>;
};

export type WhiteboardDisplayV2 = {
  kind: "whiteboard";
  /** 画面に出す解説役。省略時は立ち絵を出さない。 */
  presenterId?: string;
  /** 通常stageの人物設定とは分離した、ホワイトボード固有の内容。 */
  whiteboard: Omit<WhiteboardExplainInsertConfig, "character">;
};

export type ZunMeetParticipantV2 = {
  instanceId: string;
  name?: string;
  cameraOff?: boolean;
  muted?: boolean;
};

export type ZunMeetDisplayV2 = {
  kind: "zunMeet";
  zunMeet: {
    room?: string;
    layout?: "focus" | "grid";
    activeSpeakerId?: string;
    participants: ZunMeetParticipantV2[];
  };
};

export type DisplayModeV2 =
  | {kind: "standard"}
  | WhiteboardDisplayV2
  | ZunMeetDisplayV2;

export type StoryInstanceV2 = {
  characterId?: string;
  voiceId: string;
  role?: "stage" | "voiceOnly";
  label?: string;
};

export type PlacementV2 =
  | {mode: "slot"; slotId: string; offset?: Point}
  | {mode: "manual"; origin: Point; scale?: number; zIndex?: number};

export type InstancePatchV2 = {
  placement?: PlacementV2;
  expression?: string;
  pose?: string;
  face?: FacingV2;
  flip?: boolean;
  zIndex?: number;
};

export type FacingV2 = "left" | "right";

export type FramingV2 =
  | {mode: "sceneDefault"}
  | {mode: "speaker"}
  | {mode: "slot"; slotId: string}
  | {mode: "manual"; frame: CameraFrameV2};

export type CameraMotionV2 = {
  zoom?: number;
  pan?: Point;
  tilt?: number;
  shake?: {strength: number; duration: number};
};

export type StageAnimationDirectionV2 = "auto" | "left" | "right" | "up" | "down" | "instant";
export type StageEnterV2 = {
  instanceId: string;
  placement: PlacementV2;
  animation?: {direction?: StageAnimationDirectionV2};
};
export type StageExitV2 = string | {
  instanceId: string;
  animation?: {direction?: StageAnimationDirectionV2};
};

export type StageEventV2 = {
  enter?: StageEnterV2[];
  exit?: StageExitV2[];
  update?: Record<string, InstancePatchV2>;
  reset?: string[];
  framing?: FramingV2;
  cameraMotion?: CameraMotionV2;
};

export type StageTurnV2 = {
  id: string;
  speaker: string;
  text: string;
  scene: string;
  start?: number;
  end?: number;
  pause?: number;
  /** 心の声など、発話中でも口パクを止めるターン。 */
  noLipSync?: boolean;
  /** 通常stageの人物だけを隠し、台詞表示は維持する。 */
  hideCharacters?: boolean;
  /** 話者吹き出しを隠す。 */
  hideBubble?: boolean;
  /** 吹き出しではなく字幕帯として表示する。 */
  subtitleMode?: "subtitle";
  subtitleStyle?: SubtitleStyleV2;
  /** 同じ話者・同じsceneの直前ターンと吹き出しを連結する。 */
  continueBubble?: boolean;
  bubbleMaxChars?: number;
  disableAutoBubbleSplit?: boolean;
  /** 音声生成済みの文ごとの絶対時刻。 */
  sentences?: StorySentenceV2[];
  /** v2では即時切替のみ対応する旧場面転換指定。 */
  transition?: "cut";
  /** ターン開始からの相対時刻で鳴らす手動SE。 */
  se?: TurnSe[];
  /** そのターンに画面上へ重ねる短い場面ラベル。 */
  caption?: CaptionV2;
  /** V2用の軽量画面演出。旧turn直下キーとは分離する。 */
  effects?: StageEffectsV2;
  /** 構図が変わるターンのカメラ接続。省略時は smooth。 */
  cameraTransition?: "smooth" | "cut";
  displayMode?: DisplayModeV2;
  stage?: StageEventV2;
};

export type StoryV2 = {
  schemaVersion: 2;
  title?: string;
  audio?: string;
  /** 既存タイムラインと共通の時間ベースBGM区間。 */
  bgm?: BgmRegion[];
  /** 既存タイムラインと共通の補助画像・テキスト。 */
  overlays?: StoryOverlay[];
  /** 旧と共通の動画全体表示設定。 */
  displaySettings?: StoryDisplaySettingsV2;
  /** 非話者の表情を保持するか、各ターンで通常状態へ戻すか。 */
  idleFace?: "normal" | "hold";
  instances: Record<string, StoryInstanceV2>;
  script: StageTurnV2[];
};

export type SlotV2 = {
  origin: Point;
  scale?: number;
  zIndex?: number;
  cameraPresetId?: string;
  allowOverlap?: boolean;
  /** シーンエディタだけで使う確認用立ち絵。主役IDまたは`mob:<mobId>`で、台本の配置対象は決めない。 */
  previewCharacterId?: string;
};

export type StandardLayoutV2 = {slots: Record<string, SlotV2>};
export type SceneV2 = {
  bg?: string;
  bgVideo?: string;
  bgVideoLoop?: boolean;
  front?: string | null;
  figure?: "bust" | "full";
  layouts: {standard: StandardLayoutV2};
  cameraPresets?: Record<string, CameraFrameV2>;
};
export type SceneLibraryV2 = {scenes: Record<string, SceneV2>};

export type ResolvedInstanceV2 = {
  instanceId: string;
  definition: StoryInstanceV2;
  present: true;
  visible: boolean;
  placement: PlacementV2;
  expression?: string;
  pose?: string;
  face?: FacingV2;
  flip?: boolean;
  zIndex?: number;
};

export type ResolvedStageStateV2 = {
  scene: string;
  displayMode: DisplayModeV2;
  instances: Record<string, ResolvedInstanceV2>;
  framing: FramingV2;
  cameraMotion?: CameraMotionV2;
};

type MutableInstance = Omit<ResolvedInstanceV2, "visible">;

const standardMode: DisplayModeV2 = {kind: "standard"};
const defaultFraming: FramingV2 = {mode: "sceneDefault"};

function clonePoint(point: Point): Point {
  return {x: point.x, y: point.y};
}

function clonePlacement(placement: PlacementV2): PlacementV2 {
  if (placement.mode === "slot") {
    return {
      mode: "slot",
      slotId: placement.slotId,
      ...(placement.offset ? {offset: clonePoint(placement.offset)} : {}),
    };
  }
  return {
    mode: "manual",
    origin: clonePoint(placement.origin),
    ...(placement.scale == null ? {} : {scale: placement.scale}),
    ...(placement.zIndex == null ? {} : {zIndex: placement.zIndex}),
  };
}

function applyPatch(instance: MutableInstance, patch: InstancePatchV2): MutableInstance {
  return {
    ...instance,
    ...(patch.placement ? {placement: clonePlacement(patch.placement)} : {}),
    ...(patch.expression == null ? {} : {expression: patch.expression}),
    ...(patch.pose == null ? {} : {pose: patch.pose}),
    ...(patch.face == null ? {} : {face: patch.face}),
    ...(patch.flip == null ? {} : {flip: patch.flip}),
    ...(patch.zIndex == null ? {} : {zIndex: patch.zIndex}),
  };
}

function resetInstance(instance: MutableInstance): MutableInstance {
  return {
    instanceId: instance.instanceId,
    definition: instance.definition,
    present: true,
    placement: instance.placement,
  };
}

function resetIdleExpression(instance: MutableInstance): MutableInstance {
  const {expression: _expression, ...rest} = instance;
  return rest;
}

/**
 * 指定turnの開始時点における完全な舞台状態を返す。sceneが変わると在席状態は必ずリセットする。
 * 入力の整合性は保存時validatorが保証する想定であり、この関数は描画・プレビュー用の純粋解決器である。
 */
export function resolveStageStateAtTurn(
  story: StoryV2,
  turnIndex: number,
): ResolvedStageStateV2 {
  if (story.schemaVersion !== 2) {
    throw new Error("stage v2 resolver requires schemaVersion: 2");
  }
  if (turnIndex < 0 || turnIndex >= story.script.length) {
    throw new Error(`turn index out of range: ${turnIndex}`);
  }

  let activeScene = "";
  let instances: Record<string, MutableInstance> = {};
  let framing: FramingV2 = defaultFraming;
  let cameraMotion: CameraMotionV2 | undefined;
  let displayMode: DisplayModeV2 = standardMode;

  for (let index = 0; index <= turnIndex; index += 1) {
    const turn = story.script[index];
    if (turn.scene !== activeScene) {
      activeScene = turn.scene;
      instances = {};
      framing = defaultFraming;
      cameraMotion = undefined;
    }

    displayMode = turn.displayMode ?? standardMode;
    const event = turn.stage;
    // 動きはそのターンだけに重ねる演出であり、次ターンへ状態として継承しない。
    cameraMotion = event?.cameraMotion;
    if (!event) continue;

    for (const item of event.enter ?? []) {
      const definition = story.instances[item.instanceId];
      if (!definition) continue;
      instances[item.instanceId] = {
        instanceId: item.instanceId,
        definition,
        present: true,
        placement: clonePlacement(item.placement),
      };
    }
    for (const item of event.exit ?? []) {
      const instanceId = typeof item === "string" ? item : item.instanceId;
      delete instances[instanceId];
    }
    for (const instanceId of event.reset ?? []) {
      const instance = instances[instanceId];
      if (instance) instances[instanceId] = resetInstance(instance);
    }
    for (const [instanceId, patch] of Object.entries(event.update ?? {})) {
      const instance = instances[instanceId];
      if (instance) instances[instanceId] = applyPatch(instance, patch);
    }
    if (story.idleFace === "normal") {
      for (const [instanceId, instance] of Object.entries(instances)) {
        if (instanceId !== turn.speaker) instances[instanceId] = resetIdleExpression(instance);
      }
    }
    if (event.framing) framing = event.framing;
  }

  const visible = displayMode.kind === "standard" && !story.script[turnIndex].hideCharacters;
  const resolved: Record<string, ResolvedInstanceV2> = {};
  for (const [instanceId, instance] of Object.entries(instances)) {
    resolved[instanceId] = {...instance, visible};
  }
  return {scene: activeScene, displayMode, instances: resolved, framing, cameraMotion};
}

export function placementOrigin(
  placement: PlacementV2,
  layout: StandardLayoutV2,
): Point | undefined {
  if (placement.mode === "manual") return placement.origin;
  const slot = layout.slots[placement.slotId];
  if (!slot) return undefined;
  return {
    x: slot.origin.x + (placement.offset?.x ?? 0),
    y: slot.origin.y + (placement.offset?.y ?? 0),
  };
}

export function resolveFraming(
  framing: FramingV2,
  scene: SceneV2,
  state: ResolvedStageStateV2,
  speakerId: string,
): CameraFrameV2 | undefined {
  if (framing.mode === "manual") return framing.frame;
  const presets = scene.cameraPresets ?? {};
  if (framing.mode === "sceneDefault") return presets.default;

  const slotId = framing.mode === "slot"
    ? framing.slotId
    : state.instances[speakerId]?.placement.mode === "slot"
      ? state.instances[speakerId].placement.slotId
      : undefined;
  if (!slotId) return presets.default;
  const presetId = scene.layouts.standard.slots[slotId]?.cameraPresetId;
  return (presetId ? presets[presetId] : undefined) ?? presets.default;
}
