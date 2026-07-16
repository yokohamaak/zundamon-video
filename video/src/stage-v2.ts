import type {WhiteboardExplainInsertConfig} from "./inserts/whiteboardExplain";

export type Point = {x: number; y: number};
export type CameraFrameV2 = {cx: number; cy: number; width: number};

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

export type StageEventV2 = {
  enter?: Array<{instanceId: string; placement: PlacementV2}>;
  exit?: string[];
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
  displayMode?: DisplayModeV2;
  stage?: StageEventV2;
};

export type StoryV2 = {
  schemaVersion: 2;
  title?: string;
  audio?: string;
  instances: Record<string, StoryInstanceV2>;
  script: StageTurnV2[];
};

export type SlotV2 = {
  origin: Point;
  scale?: number;
  zIndex?: number;
  cameraPresetId?: string;
  allowOverlap?: boolean;
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
    for (const instanceId of event.exit ?? []) {
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
    if (event.framing) framing = event.framing;
  }

  const visible = displayMode.kind === "standard";
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
