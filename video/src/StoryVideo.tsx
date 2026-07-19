import {
  AbsoluteFill,
  Audio,
  getRemotionEnvironment,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Video,
} from "remotion";
import { useWindowedAudioData } from "@remotion/media-utils";
import { Avatar, MOUTH_HALF } from "./Avatar";
import type { ExpressionCfg } from "./Avatar";
import type { Emotion, Gender } from "./types";
import { WhiteboardExplainInsert, getWhiteboardExplainLayout } from "./inserts/whiteboardExplain";
import type { WhiteboardExplainInsertConfig, WhiteboardExplainPopTargets } from "./inserts/whiteboardExplain";

// ─── PC画面インサート型定義 ──────────────────────────────────
export type StoryInsert =
  | { kind: "warning"; width?: number; fontScale?: number; bg?: string; backdropBg?: string; backdropImage?: string; title?: string; text: string }
  | { kind: "chat"; width?: number; fontScale?: number; bg?: string; backdropBg?: string; backdropImage?: string; user: string; ai: string[]; highlight?: number }
  | { kind: "ok"; width?: number; fontScale?: number; bg?: string; backdropBg?: string; backdropImage?: string; text?: string }
  | { kind: "teamchat"; width?: number; fontScale?: number; bg?: string; backdropBg?: string; backdropImage?: string; channel?: string; messages: { from: string; text: string; highlight?: boolean }[] }
  | { kind: "mailer"; width?: number; fontScale?: number; bg?: string; backdropBg?: string; backdropImage?: string; from?: string; fromAddr?: string; subject: string; body: string; time?: string }
  | {
    kind: "videocall";
    width?: number;
    // end=true のターンで通話を終了する（そのターンから通常画面に戻り、以降へ継承しない）。
    end?: boolean;
    room?: string;
    layout?: "focus" | "grid";
    activeSpeaker?: string;
    // 省略時は同シーン内の直前 videocall から継承する（差分パッチ運用）。
    participants?: Array<{
      speaker: string;
      name?: string;
      bgStyle?: "office" | "meeting_room" | "home" | "ai" | "green";
      // 上記5種のプリセット以外に、自分で追加した画像(public/background/配下)を直接指定したい場合に使う。
      // 指定されていればbgStyleより優先する。
      bgImage?: string;
      // ZunMeetタイル内のキャラ絵調整。0..1基準で、未指定なら既定位置。
      feedX?: number;
      feedY?: number;
      feedScale?: number;
      cameraOff?: boolean;
      muted?: boolean;
    }>;
  }
  | ({ kind: "whiteboard_explain"; visibleSections?: [boolean, boolean, boolean]; visibleArrows?: [boolean, boolean]; showConclusion?: boolean } & WhiteboardExplainInsertConfig);

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。DialogueVideo と同値。
const LIPSYNC_GAIN = 5;

// ─── 回想（flashback）演出の定数 ────────────────────────────
// 後で調整しやすいよう1箇所にまとめる。
const FB_SATURATE = 0.4;        // 回想中の彩度（1.0=元のまま・低いほど色が薄い）
const FB_BRIGHTNESS = 1.02;     // 回想中の輝度（微加）
const FB_GRAIN_OPACITY = 0.06;  // グレインの不透明度（0.0=なし・0.1で見えてくる）
const FB_TELOP_SEC = 1.2;       // テロップの表示秒数
const FB_TELOP_FADE = 0.25;     // テロップのフェードイン/アウト秒数
const CAMERA_EFFECT_SETTLE_SEC = 0.4; // 旧データ向けの既定値。未設定時は camera.*Duration にフォールバック

type VisionNoiseSettings = {
  type?: "future" | "snow" | "vhs" | "glitch";
  strength?: number;
  scanline?: number;
  glitch?: number;
  flicker?: number;
  tint?: string;
};

// ───────────────────────────────────────────────────────────
// ストーリー調 会話劇動画（新ツール Phase 1）の描画。
// 既存の DialogueVideo には触れず、立ち絵 Avatar だけ流用する。
// 入力は手書きの story-sample.json / story-scenes.json（固定入力）。
// 仕様: docs/next-animation-editor-spec.md §4。
// ───────────────────────────────────────────────────────────

// 台本の表情語彙（§4.3）。立ち絵 Emotion へマップする。
// trouble/panic は専用パーツ未配置のため当面は近い既存表情へ寄せる（Phase 5 でパーツ追加）。
// 任意の追加表情も許容するため string に緩める（組み込み5種以外はモーション無しの静的表情）。
export type StoryExpression = string;

// 組み込み5種のみ Emotion マップを持つ。未知キーは "normal" にフォールバック。
const EXPRESSION_TO_EMOTION: Record<string, Emotion> = {
  normal: "normal",
  happy: "happy",
  surprise: "surprise",
  trouble: "sad", // 困り：暫定（専用パーツが無ければ通常目にフォールバック）
  panic: "panic", // 焦り：汗＋見開き目＋小刻みな揺れ
};

export type StorySentence = { text: string; start: number; end: number };

// 手動ワンショット SE（ターン単位）。
export type TurnSe = { file: string; at?: number; volume?: number };
type LegacyCameraEffect = "pull-out" | "pan-left" | "pan-right" | "tilt-left" | "tilt-right";
type CameraEffects = {
  zoom?: "in" | "out";
  pan?: "left" | "right";
  tilt?: "left" | "right";
  shake?: boolean;
};
type CameraEffectSettingGroup = {
  amount?: number;
  duration?: number;
  angle?: number;
};
type CameraEffectSettings = {
  zoom?: CameraEffectSettingGroup;
  pan?: CameraEffectSettingGroup;
  tilt?: CameraEffectSettingGroup;
  shake?: CameraEffectSettingGroup;
};
type SubtitleStyle = {
  fontSize?: number;
  textColor?: string;
  boxBorder?: boolean;
  boxBorderColor?: string;
  boxBorderWidth?: number;
};
type BubbleDisplaySettings = {
  maxChars?: number | null;
  fontSize?: number;
  fontFamily?: string;
  textColor?: string;
  bgColor?: string;
  borderWidth?: number;
  radius?: number;
};
type SubtitleDisplaySettings = {
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
type SpeakerColorSettings = Record<string, string | undefined> & {
  zundamon?: string;
  metan?: string;
  default?: string;
};
type StoryDisplaySettings = {
  bubble?: BubbleDisplaySettings;
  subtitle?: SubtitleDisplaySettings;
  telop?: { x?: number; y?: number; size?: number };
  speakerColors?: SpeakerColorSettings;
};

export type StoryTurn = {
  id: string;
  speaker: string;
  text: string;
  scene: string;
  transition?:
    | "fade-black"
    | "fade-white"
    | "cut"
    | "wipe-left"
    | "wipe-right"
    | "slide-left"
    | "slide-right";
  expression?: StoryExpression;
  pose?:
    | "idle"
    | "cheer"
    | "recoil"
    | "lean"
    | "droop"
    | "flustered"
    | "proud"
    | "listening"
    | "sneak"
    | "wobble"
    | "point"
    | "smartphone"
    | "thinking";
  // 登場するキャラ/モブ。このターンの頭からスライドインして以後表示される。
  enter?: string[];
  // 登場方向（"left"/"right"）または即時登場（"instant"）。省略時は自分の居る側（近い画面端）から。
  // exitDirと対称の仕様（キャラ・モブ共通）。
  enterDir?: "left" | "right" | "instant";
  speakerAnchor?: string;
  // キャラの向き（画面のどちらを向くか）の明示指定。省略時は立ち位置から自動（中央向き）。
  // 例: { "zundamon": "left", "metan": "right" }
  face?: Record<string, FaceDirection>;
  // face の効き方。hold は以後のターンでも維持、turn/省略はそのターンだけ。
  faceMode?: "turn" | "hold";
  // 保持中の向きを解除して自動向きへ戻す対象。
  clearFace?: string[];
  // 話者プッシュイン演出の開始トリガー。
  // emphasis=true のターンで寄りを開始し、同じ話者が続く間は維持する。
  emphasis?: boolean;
  focusSpeaker?: boolean;
  cameraTransition?: "smooth" | "cut";
  manualCameraFrame?: CameraFrame;
  // その行だけ付ける追加カメラ効果。カテゴリごとに1つずつ併用できる。
  cameraEffects?: CameraEffects;
  cameraEffectSettings?: CameraEffectSettings;
  cameraEffect?: LegacyCameraEffect;
  // カメラシェイク演出（shake=true のターン中、ターン開始からの減衰振動オフセットを加算）。
  shake?: boolean;
  // 回想フラグ（true のターンが回想区間）。
  flashback?: boolean;
  // 未来視ノイズ。true ならプリセット、object ならその行だけの手動値。
  visionNoise?: boolean | VisionNoiseSettings;
  // テロップ（境界付近で短時間表示する時代テキスト。例「― 前日 ―」）。
  telop?: string;
  telopSize?: number;
  telopX?: number;
  telopY?: number;
  // 追加の単発演出。主に行頭〜行末の短い強調演出として使う。
  impactText?: string;
  impactLines?: boolean;
  zoomPunch?: boolean;
  quoteFreeze?: boolean;
  stampRain?: string;
  typingFlood?: boolean;
  sparkleBurst?: boolean;
  // キラッ(sparkleBurst)の中心位置。未指定なら既定値(画面中央よりやや上)。
  sparklePos?: { x: number; y: number } | null;
  irisOut?: boolean;
  effectSettings?: StoryEffectSettings;
  // 台詞後の無音秒（音声生成で使用。描画では参照しない）。
  pause?: number;
  subtitleMode?: "subtitle";
  subtitleStyle?: SubtitleStyle;
  hideCharacters?: boolean;
  hideBubble?: boolean;
  // PC画面インサート演出（このターン中に全画面PC画面UIを重ねる）。
  insert?: StoryInsert;
  // 退場するキャラ。このターンの終わり（end）にスライドアウトして以後は非表示。
  exit?: string[];
  // 退場方向（"left"/"right"）または即時退場（"instant"）。省略時は自分の居る側（近い画面端）へ。
  exitDir?: "left" | "right" | "instant";
  // 手動ワンショット SE（ターン単位・at=ターン開始からの秒オフセット）。
  se?: TurnSe[];
  start: number;
  end: number;
  narrationVoice?: string;
  continueBubble?: boolean;
  bubbleMaxChars?: number;
  disableAutoBubbleSplit?: boolean;
  noLipSync?: boolean;
  sentences?: StorySentence[];
  // ターン単位の自由配置(キャラ・モブ共通)。key=charId(zundamon等)またはmobId(営業等)。
  // 値がオブジェクト: この時刻からその座標に手動配置(登場中ずっと固定、次の指定 or 退場まで有効)。
  // 値がnull: この時刻から自動配置(名前付きアンカー/シーン既定)に戻す(手動配置の解除)。
  manualPos?: Record<string, { x: number; y: number } | null>;
  // cameraEffects.zoom / emphasis が寄る先の手動指定。
  zoomTarget?: { x: number; y: number } | null;
};

export type StoryOverlayAnchor = {
  turnId: string;
  at: number;
};

export type StoryOverlay = {
  id: string;
  kind: "image" | "text";
  layer?: "normal" | "over-insert";
  src?: string;
  text?: string;
  textColor?: string;
  bgColor?: string;
  bgOpacity?: number;
  borderColor?: string;
  borderOpacity?: number;
  fontSize?: number;
  centerX?: boolean;
  x: number;
  y: number;
  w: number;
  opacity?: number;
  z?: number;
  start: StoryOverlayAnchor;
  end: StoryOverlayAnchor;
};

export type StoryEffectSettings = {
  // 登場/退場スライドにかける秒数（キャラ・モブ共通）。
  entrance?: { duration?: number };
  // 話者プッシュイン(emphasis)が寄りきるまでの秒数(duration)・追加ズーム倍率(scale)。
  emphasis?: { duration?: number; scale?: number };
  zoomPunch?: { scale?: number; duration?: number; borderStrength?: number };
  quoteFreeze?: { fadeIn?: number; fadeOutStart?: number; fadeOutDuration?: number; backdropOpacity?: number };
  stampRain?: { count?: number; fallDuration?: number; stagger?: number; spread?: number };
  typingFlood?: { rows?: number; flowDuration?: number; stagger?: number };
  sparkleBurst?: { count?: number; spread?: number; duration?: number };
  impactLines?: { cx?: number; cy?: number; count?: number; thickness?: number; opacity?: number; innerRadius?: number; start?: number; end?: number };
  visionNoise?: VisionNoiseSettings;
  // cx/cy: 円の中心(0-1)。startRadius: 出現時の半径（画面対角線の半分=1）。
  // closeStart/closeEnd: ターン開始からの経過秒数（絶対値。ターン長による自動調整はしない）。
  irisOut?: { cx?: number; cy?: number; startRadius?: number; closeStart?: number; closeEnd?: number; color?: string };
};

type ResolvedEffectSettings = {
  entrance: { duration: number };
  emphasis: { duration: number; scale: number };
  zoomPunch: { scale: number; duration: number; borderStrength: number };
  quoteFreeze: { fadeIn: number; fadeOutStart: number; fadeOutDuration: number; backdropOpacity: number };
  stampRain: { count: number; fallDuration: number; stagger: number; spread: number };
  typingFlood: { rows: number; flowDuration: number; stagger: number };
  sparkleBurst: { count: number; spread: number; duration: number };
  impactLines: { cx: number; cy: number; count: number; thickness: number; opacity: number; innerRadius: number; start: number; end: number };
  visionNoise: { type: "future" | "snow" | "vhs" | "glitch"; strength: number; scanline: number; glitch: number; flicker: number; tint: string };
  irisOut: { cx: number; cy: number; startRadius: number; color: string; closeStart: number; closeEnd: number };
};

// BGM 区間。時間ベース（start/end=秒）。タイムラインでD&D編集する。
// この配列があれば BGM はこれが唯一の真実（区間の隙間=無音）。空ならシーン連動にフォールバック。
export type BgmRegion = {
  start: number; // 秒
  end: number;   // 秒
  file: string;
  volume?: number;
  fadeIn?: number;
  fadeOut?: number;
};
// 後方互換の別名（旧名）。
export type BgmOverride = BgmRegion;

export type StoryScript = {
  title?: string;
  audio?: string;
  // BGM override 区間（シーン既定より優先）。
  bgm?: BgmOverride[];
  // 聞き役(非話者)の表情: "normal"=常に真顔(既定) / "hold"=直前に自分が話した表情を保持
  // （surprise/panic は除外して normal）。
  idleFace?: "normal" | "hold";
  displaySettings?: StoryDisplaySettings;
  effectSettings?: StoryEffectSettings;
  overlays?: StoryOverlay[];
  script: StoryTurn[];
};

type Anchor = { x: number; y: number };
type CameraFrame = { cx?: number; cy?: number; width?: number };
type CameraFrameKey = "default" | "leftFocus" | "rightFocus";

export type SceneDef = {
  label?: string;
  bg: string;
  bgVideo?: string;
  bgVideoLoop?: boolean;
  // 背景(back)だけに掛ける被写界深度風のブラー量(px)。
  bgBlur?: number;
  front?: string | null;
  camera?: "static" | "slow-zoom";
  // 話者プッシュイン(emphasis)の調整。focusZoom=現在倍率への加算量。
  focusZoom?: number;
  focusCy?: number; // 廃止(旧・縦の絶対注視点)。focusDy に置き換え。読み込みはしない。
  focusDy?: number; // プッシュイン 顔からの縦オフセット（既定 0.12）
  // 背景全体に対する既定の16:9カメラ領域。anchors はこの枠内の相対位置として解釈する。
  cameraFrame?: CameraFrame;
  cameraFrames?: Partial<Record<CameraFrameKey, CameraFrame>>;
  scale?: number; // 立ち絵の拡大率（既定 1.9）
  anchors: Record<string, Anchor>;
  // どのキャラをどのアンカーに置くか（charId→アンカー名）。
  // 省略時は登場順で left/right を自動割当。
  cast?: Record<string, string>;
  // 立ち絵の画角。"bust"=バストアップ（既定・office）、"full"=全身（server_room/rooftop/home 等）。
  figure?: "bust" | "full";
  // モブ（1枚絵）の立ち位置と大きさ。省略時は既定（中央やや下・標準）。
  mobAnchor?: Anchor;
  mobHeight?: number; // モブ画像の高さ(px・frame基準でなく素の高さ)。既定 760。
  // モブ別の配置（scene_editor で D&D 編集）。x,y=正規化座標, scale=拡大率。
  // hidden=true で立ち絵を非表示（チャット/音声のみ登場にする）。
  mobs?: Record<string, { x: number; y: number; scale?: number; hidden?: boolean }>;
};

// カメラ共通設定（全シーン共通・story-scenes.json トップレベルの camera）。
// cameraEffects(zoom/pan/tilt) と slow-zoom の量を調整する。未設定は従来の固定値。
export type CameraSettings = {
  zoomAmount?: number; // ズーム量（既定 0.12）
  zoomDuration?: number; // ズーム到達時間（既定 0.4秒）
  panAmount?: number; // pan-left/right の最大移動量（画面幅比・既定 0.08）
  panDuration?: number; // パン到達時間（既定 0.4秒）
  tiltAngle?: number; // tilt-left/right の回転角（度・既定 2.4）
  tiltDuration?: number; // 傾き到達時間（既定 0.4秒）
  pullOut?: number; // 旧互換。zoomAmount として読む
  panMax?: number; // 旧互換。panAmount として読む
  tiltDeg?: number; // 旧互換。tiltAngle として読む
  slowZoomDrift?: number; // slow-zoom シーンの区間ドリフト量（既定 0.05 = 1.00→1.05倍）
};

export type SceneLibrary = { scenes: Record<string, SceneDef>; camera?: CameraSettings };
type FaceDirection =
  | "left"
  | "right"
  | "front_left"
  | "front_right"
  | "side_left"
  | "side_right"
  | "back_left"
  | "back_right"
  | "back";

export type CharDef = {
  avatar: string; // パーツ立ち絵フォルダ名
  gender: Gender; // フォールバック用
  expressive?: boolean;
  bubbleColor?: string; // 吹き出し枠の色（話者で色分け）
};

const DEFAULT_BUBBLE_DISPLAY_SETTINGS = {
  maxChars: null as number | null,
  fontSize: 54,
  fontFamily: "sans-serif",
  textColor: "#1b1b1f",
  bgColor: "#ffffff",
  borderWidth: 5,
  radius: 18,
};
const DEFAULT_SUBTITLE_DISPLAY_SETTINGS = {
  fontSize: 46,
  fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif',
  textColor: "#ffffff",
  bgColor: "#080a0e",
  bgOpacity: 0.84,
  border: true,
  borderColor: "#ffffff",
  borderWidth: 2,
  bottom: 42,
  width: 0.84,
};
const DEFAULT_TELOP_DISPLAY_SETTINGS = {
  x: 0.045,
  y: 0.06,
  size: 1,
};
// モブ／未定義キャラの既定枠色。
const DEFAULT_BUBBLE_COLOR = "#9aa0a6"; // グレー

// 主役キャラ定義（Phase 1 はインライン。将来はライブラリ化）。
const CHARACTERS: Record<string, CharDef> = {
  zundamon: { avatar: "zundamon", gender: "male", expressive: true, bubbleColor: "#5fb84f" }, // 緑系
  metan: { avatar: "metan", gender: "female", expressive: false, bubbleColor: "#e87bb0" }, // ピンク系
};

// モブ定義（いらすとや風の1枚絵）。話している間だけ立ち、発話中は口パク（closed/open差し替え）。
// images: 状態キー(normal/agitated)→口の開閉2枚。表情とアンプで出し分ける。
// 画像は public/mobs/<file>（assets/mobs を prep-story が public へコピー）。
// 口の開閉画像を用意していないモブは closed/open に同じファイルを指定する（=口パクなしと同じ見た目）。
export type MobImagePair = { closed: string; open: string };
export type MobDef = {
  images: Record<string, MobImagePair>;
  scale?: number;
  flip?: boolean;
  anchor?: Anchor;
};
export type MobsMap = Record<string, MobDef>;
// mobs.json 未配置時のフォールバック既定値（旧ハードコード値を踏襲）。
const DEFAULT_MOBS: MobsMap = {
  営業: {
    images: {
      normal: { closed: "mobs/mob_normal.png", open: "mobs/mob_normal.png" },
      agitated: { closed: "mobs/mob_panic.png", open: "mobs/mob_panic.png" },
    },
    scale: 0.85,
    anchor: { x: 0.5, y: 0.99 },
  },
  部長: {
    images: {
      normal: { closed: "mobs/manager_normal.png", open: "mobs/manager_normal.png" },
      agitated: { closed: "mobs/manager_angry.png", open: "mobs/manager_angry.png" },
    },
    scale: 0.62,
    anchor: { x: 0.5, y: 0.82 },
  },
};
// StoryVideo コンポーネント冒頭で props.mobs から差し替える（module-level・単一同期render前提）。
let MOBS: MobsMap = DEFAULT_MOBS;
const isMob = (id: string): boolean => id in MOBS;
// セリフ文字がインサートUI内に表示される種別（吹き出しを抑制する）。
// videocall はUI内にセリフが出ないため対象外＝通常の吹き出しを通話画面の手前に出す。
function isInsertLineKind(insert: StoryInsert | null | undefined): boolean {
  return !!insert && (insert.kind === "teamchat" || insert.kind === "chat" || insert.kind === "whiteboard_explain");
}

function mergeVideoCallInsert(
  base: Extract<StoryInsert, { kind: "videocall" }> | null,
  patch: Extract<StoryInsert, { kind: "videocall" }>
): Extract<StoryInsert, { kind: "videocall" }> {
  return {
    kind: "videocall",
    width: patch.width ?? base?.width,
    room: patch.room ?? base?.room,
    layout: patch.layout ?? base?.layout,
    activeSpeaker: patch.activeSpeaker ?? base?.activeSpeaker,
    participants:
      patch.participants && patch.participants.length > 0
        ? patch.participants
        : (base?.participants ?? []),
  };
}

function normalizeCameraFrame(src?: CameraFrame): { cx: number; cy: number; width: number; x: number; y: number } {
  const width = clamp(typeof src?.width === "number" ? src.width : 1, 0.35, 1);
  const cx = clamp(typeof src?.cx === "number" ? src.cx : 0.5, width / 2, 1 - width / 2);
  const cy = clamp(typeof src?.cy === "number" ? src.cy : 0.5, width / 2, 1 - width / 2);
  return { cx, cy, width, x: cx - width / 2, y: cy - width / 2 };
}

function sceneCameraFrame(sceneDef: SceneDef, key: CameraFrameKey): ReturnType<typeof normalizeCameraFrame> {
  const src = sceneDef.cameraFrames?.[key] ?? (key === "default" ? sceneDef.cameraFrame : undefined);
  return normalizeCameraFrame(src ?? sceneDef.cameraFrames?.default ?? sceneDef.cameraFrame);
}

function normalizedCameraFrame(sceneDef: SceneDef): ReturnType<typeof normalizeCameraFrame> {
  return sceneCameraFrame(sceneDef, "default");
}

function anchorToScene(anchor: Anchor, sceneDef: SceneDef): Anchor {
  return anchor;
}

function normalizedCameraEffects(turn?: StoryTurn | null): CameraEffects {
  const next: CameraEffects = {};
  const src = turn?.cameraEffects;
  if (src?.zoom === "in" || src?.zoom === "out") next.zoom = src.zoom;
  if (src?.pan === "left" || src?.pan === "right") next.pan = src.pan;
  if (src?.tilt === "left" || src?.tilt === "right") next.tilt = src.tilt;
  if (src?.shake === true) next.shake = true;
  if (!turn?.cameraEffect) return next;
  switch (turn.cameraEffect) {
    case "pull-out":
      next.zoom = "out";
      break;
    case "pan-left":
      next.pan = "left";
      break;
    case "pan-right":
      next.pan = "right";
      break;
    case "tilt-left":
      next.tilt = "left";
      break;
    case "tilt-right":
      next.tilt = "right";
      break;
  }
  return next;
}

function cameraEffectCommonValue(
  cam: CameraSettings | undefined,
  category: keyof CameraEffects,
  key: "amount" | "duration" | "angle",
): number {
  if (category === "zoom") {
    if (key === "amount") return cam?.zoomAmount ?? cam?.pullOut ?? 0.12;
    if (key === "duration") return cam?.zoomDuration ?? CAMERA_EFFECT_SETTLE_SEC;
  }
  if (category === "pan") {
    if (key === "amount") return cam?.panAmount ?? cam?.panMax ?? 0.08;
    if (key === "duration") return cam?.panDuration ?? CAMERA_EFFECT_SETTLE_SEC;
  }
  if (category === "tilt") {
    if (key === "angle") return cam?.tiltAngle ?? cam?.tiltDeg ?? 2.4;
    if (key === "duration") return cam?.tiltDuration ?? CAMERA_EFFECT_SETTLE_SEC;
  }
  return CAMERA_EFFECT_SETTLE_SEC;
}

function cameraEffectTurnValue(
  turn: StoryTurn | undefined,
  cam: CameraSettings | undefined,
  category: keyof CameraEffects,
  key: "amount" | "duration" | "angle",
): number {
  const raw = turn?.cameraEffectSettings?.[category]?.[key];
  return typeof raw === "number" && Number.isFinite(raw)
    ? raw
    : cameraEffectCommonValue(cam, category, key);
}

function effectiveInsertAt(script: StoryTurn[], idx: number): StoryInsert | null {
  const cur = script[idx];
  if (!cur) return null;
  if (cur.insert && cur.insert.kind !== "videocall") return cur.insert;
  const own = cur.insert?.kind === "videocall" ? cur.insert : null;
  // 終了マーカー: このターンから通常画面に戻す。
  if (own?.end) return null;
  let merged: Extract<StoryInsert, { kind: "videocall" }> | null = own;
  for (let i = idx - 1; i >= 0; i -= 1) {
    const prev = script[i];
    if (!prev || prev.scene !== cur.scene) break;
    if (prev.insert && prev.insert.kind !== "videocall") break;
    if (prev.insert?.kind === "videocall") {
      // 終了マーカーより前へは遡らない＝終了済みの通話は継承されない。
      if (prev.insert.end) break;
      merged = mergeVideoCallInsert(prev.insert, merged ?? prev.insert);
      if ((merged.participants?.length ?? 0) > 0 && merged.layout && merged.room) break;
    }
  }
  if (!merged) return null;
  // activeSpeaker だけは継承しない：そのターン自身の指定のみ有効。
  // 継承すると話者が交代してもフォーカスが付いてこないため、
  // 未指定なら描画側（InsertVideoCall）が現在の話者へ自動追従する。
  return { ...merged, activeSpeaker: own?.activeSpeaker };
}
// 取り乱し系の表情なら agitated（焦り/怒り）、それ以外は normal。
// 未知の追加表情は normal 扱い（組み込み5種と比較してフォールバック）。
function mobImage(mobId: string, expression?: StoryExpression, mouthOpen?: boolean): string {
  const m = MOBS[mobId];
  const agitated =
    expression === "panic" || expression === "surprise" || expression === "trouble";
  const key = agitated && m.images.agitated ? "agitated" : "normal";
  const pair = m.images[key] ?? Object.values(m.images)[0];
  return (mouthOpen ? pair.open : pair.closed) ?? pair.closed ?? pair.open;
}

type Manifest = Record<string, Record<string, string>>;

// expressions.json の型（キャラ→表情名→ExpressionCfg）
export type ExpressionsMap = Record<string, Record<string, ExpressionCfg>>;
export type PoseCfg = {
  arm?: string | null;
  speed?: number | null;
  strength?: number | null;
};
export type PosesMap = Record<string, Record<string, PoseCfg>>;

// se-map.json の型
export type SeMapEntry = { file: string; volume: number; enabled: boolean };
export type SeMap = {
  expression?: Record<string, SeMapEntry>;
  effect?: Record<string, SeMapEntry>;
  insert?: Record<string, SeMapEntry>;
  transition?: Record<string, SeMapEntry>;
};

export type StoryVideoProps = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Manifest;
  audio?: string; // 音声ファイル（public配下・任意）
  expressions?: ExpressionsMap; // expressions.json（省略時は旧来の emotion ベース）
  poses?: PosesMap; // poses.json（省略時はAvatar側の自動腕割当へフォールバック）
  seMap?: SeMap; // se-map.json（省略時はSE再生なし）
  mobs?: MobsMap; // mobs.json（省略時は組み込みの既定モブ定義）
};

// 立ち絵ボックスサイズ。バスト用は 445×445（Avatar の既定値と同じ）。
// 全身用はキャンバスのアスペクト比をバスト幅445に合わせた高さ。
const AVATAR_BOX = 445;
// 全身キャンバスサイズ（PSD書き出し時の共通bbox＝assets/avatars/<char>/full/_box.json と一致）。
const FULL_CANVAS = {
  zundamon: { w: 783, h: 1473 },
  metan: { w: 858, h: 1769 },
} as const;
const ZUNDAMON_ANGLE_CANVAS = { w: 1082, h: 1574 } as const;
const ZUNDAMON_ANGLE_SCALE = ZUNDAMON_ANGLE_CANVAS.w / FULL_CANVAS.zundamon.w;
const FULL_BOX_W = 445; // 全身Avatar表示幅（px）。scene_editor.html と同値にする（WYSIWYG）。sceneのavScaleで最終サイズが決まる。
function fullBoxSize(charId: string): { w: number; h: number } {
  const c = FULL_CANVAS[charId as keyof typeof FULL_CANVAS];
  if (!c) return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * 1.8) };
  return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * (c.h / c.w)) };
}

const ANGLE_FACE_STEM: Partial<Record<FaceDirection, string>> = {
  front_left: "front_left",
  front_right: "front_right",
  side_left: "left",
  side_right: "right",
  back_left: "back_left",
  back_right: "back_right",
  back: "back",
};

function normalizeFaceDirection(value: unknown): FaceDirection | undefined {
  switch (value) {
    case "left":
    case "right":
    case "front_left":
    case "front_right":
    case "side_left":
    case "side_right":
    case "back_left":
    case "back_right":
    case "back":
      return value;
    default:
      return undefined;
  }
}

function normalizeFaceDirectionForChar(charId: string, value: unknown): FaceDirection | undefined {
  const face = normalizeFaceDirection(value);
  if (!face) return undefined;
  if (charId !== "metan") return face;
  if (face === "left" || face === "right") return face;
  if (face.endsWith("_left")) return "left";
  if (face.endsWith("_right")) return "right";
  return undefined;
}

function facingFlipFor(face: FaceDirection | undefined, anchorX: number): boolean {
  if (face === "left") return false;
  if (face === "right") return true;
  return anchorX < 0.5;
}

function angleFaceSrc(
  charId: string,
  sceneDef: SceneDef,
  face: FaceDirection | undefined,
): string | null {
  if (charId !== "zundamon") return null;
  if ((sceneDef.figure ?? "bust") !== "full") return null;
  const stem = face ? ANGLE_FACE_STEM[face] : null;
  return stem ? staticFile(`avatars/zundamon/full/${stem}.png`) : null;
}

function angleFaceScale(charId: string, face: FaceDirection | undefined): number {
  if (charId === "zundamon" && face && ANGLE_FACE_STEM[face]) return ZUNDAMON_ANGLE_SCALE;
  return 1;
}

function heldFaceOf(script: StoryTurn[], charId: string, t: number): FaceDirection | undefined {
  let held: FaceDirection | undefined;
  for (const turn of script) {
    if ((turn.start ?? 0) > t) break;
    if ((turn.clearFace ?? []).includes(charId)) held = undefined;
    const face = normalizeFaceDirectionForChar(charId, turn.face?.[charId]);
    if (!face) continue;
    if (turn.faceMode === "hold") held = face;
  }
  return held;
}

// ─── モブ判定 ──────────────────────────────────────────────
// CHARACTERS に定義されていないキャラ＝モブ。
// モブはレイアウト(ロスター/アンカー/カメラ)から除外し、吹き出し＋声だけで表現する。
function isKnownChar(charId: string): boolean {
  return charId in CHARACTERS;
}

type TransitionKind =
  | "fade-black"
  | "fade-white"
  | "cut"
  | "wipe-left"
  | "wipe-right"
  | "slide-left"
  | "slide-right";

function normalizeTransition(transition?: string): TransitionKind {
  switch (transition) {
    case "fade-black":
    case "fade-white":
    case "cut":
    case "wipe-left":
    case "wipe-right":
    case "slide-left":
    case "slide-right":
      return transition;
    default:
      return "cut";
  }
}

// scene 名が連続する範囲を 1 区間（segment）にまとめる。
// ただし transition が明示されたターンは、同じ scene でも手動境界として区切る。
type Segment = {
  scene: string;
  turns: StoryTurn[];
  start: number;
  end: number;
  transition: TransitionKind;
};

function buildSegments(script: StoryTurn[]): Segment[] {
  const segs: Segment[] = [];
  for (const t of script) {
    const last = segs[segs.length - 1];
    const hasManualBoundary = t.transition !== undefined;
    if (last && last.scene === t.scene && !hasManualBoundary) {
      last.turns.push(t);
      last.end = Math.max(last.end, t.end);
    } else {
      segs.push({
        scene: t.scene,
        turns: [t],
        start: t.start,
        end: t.end,
        transition: normalizeTransition(t.transition),
      });
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

function resolveOverlayAnchorTime(script: StoryTurn[], anchor?: StoryOverlayAnchor | null): number | null {
  if (!anchor?.turnId) return null;
  const turn = script.find((item) => item.id === anchor.turnId);
  if (!turn || typeof turn.start !== "number") return null;
  return turn.start + (typeof anchor.at === "number" ? anchor.at : 0);
}

function activeOverlaysAt(script: StoryTurn[], overlays: StoryOverlay[] | undefined, t: number): StoryOverlay[] {
  if (!overlays || overlays.length === 0) return [];
  return overlays
    .filter((overlay) => {
      const start = resolveOverlayAnchorTime(script, overlay.start);
      const end = resolveOverlayAnchorTime(script, overlay.end);
      if (start == null || end == null || end <= start) return false;
      return start <= t && t < end;
    })
    .sort((a, b) => {
      const za = a.z ?? 0;
      const zb = b.z ?? 0;
      if (za !== zb) return za - zb;
      return overlays.indexOf(a) - overlays.indexOf(b);
    });
}

function isOverInsertOverlay(overlay: StoryOverlay): boolean {
  return overlay.layer === "over-insert";
}

// segment 内で、現時点までに登場したキャラ（enter の累積・登場順を保持）。
// モブ（CHARACTERS 未定義）はレイアウトから除外する。
function presentChars(seg: Segment, activeTurn: StoryTurn): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !order.includes(c)) order.push(c);
    }
    if (turn === activeTurn) break;
  }
  // 既知キャラが話者の場合のみ追加（モブ話者は追加しない）。
  if (isKnownChar(activeTurn.speaker) && !order.includes(activeTurn.speaker)) {
    order.push(activeTurn.speaker);
  }
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

function resolveAnchorMapAt(
  seg: Segment,
  roster: string[],
  sceneDef: SceneDef,
  tb: number
): Record<string, string> {
  const map: Record<string, string> = { ...assignAnchors(roster), ...(sceneDef.cast ?? {}) };
  for (const turn of seg.turns) {
    if (turn.start > tb + 1e-6) break;
    if (
      !isNarrationTurn(turn) &&
      isKnownChar(turn.speaker) &&
      typeof turn.speakerAnchor === "string" &&
      turn.speakerAnchor
    ) {
      map[turn.speaker] = turn.speakerAnchor;
    }
  }
  return map;
}

// 手動配置(turn.manualPos)の遷移にかける秒数。登場退場のslideDur/カメラのTRANSと同系統の定数。
const MANUAL_POS_TRANS = 0.6;

type PosWaypoint = { time: number; x: number; y: number };

// segment内で、対象id(charId/mobId)のmanualPosウェイポイント列を時刻順に返す。
// manualPos[id]===null は「その時刻から自動配置に戻す」境界で、以降のウェイポイントには影響しない
// （そこで一旦区切り、nullより後に指定があればそこから新たな手動配置として扱う）。
function manualPosWaypoints(seg: Segment, id: string): PosWaypoint[] {
  const points: PosWaypoint[] = [];
  for (const turn of seg.turns) {
    const entry = turn.manualPos?.[id];
    if (entry === undefined) continue;
    if (entry === null) {
      points.length = 0; // 解除：これより前のウェイポイントは以後の解決に使わない
      continue;
    }
    points.push({ time: turn.start, x: entry.x, y: entry.y });
  }
  return points;
}

// 時刻tにおける手動配置座標。直近のウェイポイントへ、開始から MANUAL_POS_TRANS 秒かけて
// easeInOutCubicでなめらかに遷移し、以降は固定。ウェイポイントが無ければundefined(自動フォールバック)。
// fallbackAt: 手動配置区間より前の時刻・初回遷移時の「自動計算座標」を得るための関数
// （最初のウェイポイントへ移動する際の遷移元として使う）。
function resolveManualPosAt(
  seg: Segment,
  id: string,
  t: number,
  fallbackAt: () => { x: number; y: number }
): { x: number; y: number } | undefined {
  const points = manualPosWaypoints(seg, id);
  if (points.length === 0) return undefined;
  let idx = -1;
  for (let i = 0; i < points.length; i++) {
    if (points[i].time <= t + 1e-6) idx = i; else break;
  }
  if (idx < 0) return undefined; // 最初のウェイポイントより前＝まだ自動配置
  const cur = points[idx];
  const from = idx > 0 ? points[idx - 1] : fallbackAt();
  const k = easeInOutCubic(clamp((t - cur.time) / MANUAL_POS_TRANS, 0, 1));
  return { x: lerp(from.x, cur.x, k), y: lerp(from.y, cur.y, k) };
}

// segment内で、ズーム寄り位置(turn.zoomTarget)のウェイポイント列を時刻順に返す。
// manualPosWaypointsと同じ規約（zoomTarget===nullは解除の境界）。
function zoomTargetWaypoints(seg: Segment): PosWaypoint[] {
  const points: PosWaypoint[] = [];
  for (const turn of seg.turns) {
    const entry = turn.zoomTarget;
    if (entry === undefined) continue;
    if (entry === null) {
      points.length = 0;
      continue;
    }
    points.push({ time: turn.start, x: entry.x, y: entry.y });
  }
  return points;
}

// 時刻tにおける手動ズーム位置。resolveManualPosAtと同じ遷移方式・規約
// （ウェイポイントが無ければundefined＝ソロズーム/話者プッシュインの自動計算にフォールバック）。
function resolveZoomTargetAt(
  seg: Segment,
  t: number,
  fallbackAt: () => { x: number; y: number }
): { x: number; y: number } | undefined {
  const points = zoomTargetWaypoints(seg);
  if (points.length === 0) return undefined;
  let idx = -1;
  for (let i = 0; i < points.length; i++) {
    if (points[i].time <= t + 1e-6) idx = i; else break;
  }
  if (idx < 0) return undefined;
  const cur = points[idx];
  const from = idx > 0 ? points[idx - 1] : fallbackAt();
  const k = easeInOutCubic(clamp((t - cur.time) / MANUAL_POS_TRANS, 0, 1));
  return { x: lerp(from.x, cur.x, k), y: lerp(from.y, cur.y, k) };
}

// 時刻tb時点で有効な手動ズーム位置（遷移なしの生値）。
// ソロズームのカメラキーフレーム(targetCamのtimes[])はentrance/exit時刻のスナップショットで
// 評価されるため、resolveZoomTargetAtの自前easingはここでは使えない
// （tb=キーフレーム時刻がちょうどwaypoint時刻と一致し、常にk=0＝未遷移になってしまう）。
// 代わりにzoomTargetのwaypoint時刻もキーフレームに含め、既存のTprev/Tcur crossfade(TRANS)で
// 遷移を表現する。emphasis側は毎フレーム呼ばれるため resolveZoomTargetAt(遷移あり)を使う。
function zoomTargetValueAt(seg: Segment, tb: number): { x: number; y: number } | undefined {
  const points = zoomTargetWaypoints(seg);
  let result: { x: number; y: number } | undefined;
  for (const p of points) {
    if (p.time <= tb + 1e-6) result = { x: p.x, y: p.y };
  }
  return result;
}

function explicitZoomTarget(turn: StoryTurn | undefined): { x: number; y: number } | undefined {
  const entry = turn?.zoomTarget;
  return entry && typeof entry.x === "number" && typeof entry.y === "number"
    ? { x: entry.x, y: entry.y }
    : undefined;
}

// 既知キャラの実座標。turn.manualPos があればそれ（遷移込み）を優先し、無ければ
// 名前付きアンカー(sceneDef.anchors[anchorOf[charId]])にフォールバックする。
// カメラ/顔位置/描画位置/吹き出し位置など、アンカー参照箇所すべてがこの関数経由になるよう統一する。
// アンカー(ボックス中心)⇔顔位置の変換オフセット。faceCyOf と同じ式の逆算。
// 手動配置はドラッグ操作の直感に合わせて「顔の位置」を指定してもらうため、
// 保存/補間はfaceY基準で行い、実際の描画基準点(ボックス中心)へはここで変換する。
function charFaceOffset(charId: string, sceneDef: SceneDef): number {
  const isFull = (sceneDef.figure ?? "bust") === "full";
  const avatar = CHARACTERS[charId]?.avatar ?? charId;
  const boxH = isFull ? fullBoxSize(avatar).h : AVATAR_BOX;
  const avScale = sceneDef.scale ?? 1.9;
  const ratio = isFull ? FACE_RATIO.full : FACE_RATIO.bust;
  return (0.5 - ratio) * ((boxH * avScale) / 1080);
}

function resolveCharXY(
  charId: string,
  anchorOf: Record<string, string>,
  sceneDef: SceneDef,
  seg: Segment,
  tb: number,
  fallbackAnchor: { x: number; y: number } = { x: 0.5, y: 0.5 }
): { x: number; y: number } {
  const autoAnchor = sceneDef.anchors[anchorOf[charId] ?? "center"];
  const auto = autoAnchor ? anchorToScene(autoAnchor, sceneDef) : fallbackAnchor;
  const faceOffset = charFaceOffset(charId, sceneDef);
  const autoFace = { x: auto.x, y: auto.y - faceOffset };
  const manualFace = resolveManualPosAt(seg, charId, tb, () => autoFace);
  if (!manualFace) return auto;
  return { x: manualFace.x, y: manualFace.y + faceOffset };
}

// モブの実座標。turn.manualPos があればそれ（遷移込み）を優先し、無ければ従来通り
// シーン個別配置(sceneDef.mobs) → モブ既定(mobs.json) → シーン共通既定の順でフォールバックする。
function resolveMobXY(
  mobId: string,
  seg: Segment,
  tb: number,
  sceneDef: SceneDef,
  fallbackAnchor: { x: number; y: number } = { x: 0.5, y: 1.0 }
): { x: number; y: number } {
  const m = MOBS[mobId];
  const place = sceneDef.mobs?.[mobId];
  const auto = place ?? m?.anchor ?? sceneDef.mobAnchor ?? fallbackAnchor;
  return resolveManualPosAt(seg, mobId, tb, () => auto) ?? auto;
}

// segment 全体で登場する全キャラ（登場順）。立ち位置を区間中ずっと固定するため最終集合で決める。
// モブ（CHARACTERS 未定義）はレイアウトから除外する。
function segmentRoster(seg: Segment): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !order.includes(c)) order.push(c);
    }
    if (!isNarrationTurn(turn) && isKnownChar(turn.speaker) && !order.includes(turn.speaker)) {
      order.push(turn.speaker);
    }
  }
  return order;
}

// 対象(isTarget)が segment 内で初めて画面に出る時刻（秒）。
// isKnownChar を渡せばキャラ用、isMob を渡せばモブ用として同じロジックを再利用できる
// （以前はキャラ用/モブ用で別々の実装だったため、片方だけ機能を足し忘れる事故があった）。
function entranceTimesFor(seg: Segment, isTarget: (id: string) => boolean): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isTarget(c) && !(c in e)) e[c] = turn.start;
    }
    if (!isNarrationTurn(turn) && isTarget(turn.speaker) && !(turn.speaker in e)) e[turn.speaker] = turn.start;
  }
  return e;
}

// 各対象の登場方向（turn.enterDir）。省略時は undefined（＝自分の居る側から）。
function enterDirsFor(seg: Segment, isTarget: (id: string) => boolean): Record<string, "left" | "right" | "instant"> {
  const d: Record<string, "left" | "right" | "instant"> = {};
  for (const turn of seg.turns) {
    if (!turn.enterDir) continue;
    for (const c of turn.enter ?? []) {
      if (isTarget(c)) d[c] = turn.enterDir;
    }
  }
  return d;
}

// 各対象の退場時刻（秒）。turn.exit で指定された対象は、そのターンの end で退場する。
function exitTimesFor(seg: Segment, isTarget: (id: string) => boolean): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.exit ?? []) {
      if (isTarget(c)) e[c] = turn.end;
    }
  }
  return e;
}

// 各対象の退場方向（turn.exitDir）。省略時は undefined（＝自分の居る側へ）。
function exitDirsFor(seg: Segment, isTarget: (id: string) => boolean): Record<string, "left" | "right" | "instant"> {
  const d: Record<string, "left" | "right" | "instant"> = {};
  for (const turn of seg.turns) {
    if (!turn.exitDir) continue;
    for (const c of turn.exit ?? []) {
      if (isTarget(c)) d[c] = turn.exitDir;
    }
  }
  return d;
}


const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const easeInOutCubic = (x: number) =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);
const easeOutBack = (x: number) => {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
};

const EXTRA_EFFECT_FONT = '"Arial Black", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif';
const DEFAULT_EFFECT_SETTINGS: ResolvedEffectSettings = {
  entrance: { duration: 0.5 },
  emphasis: { duration: 0.5, scale: 0.3 },
  zoomPunch: { scale: 1.14, duration: 0.18, borderStrength: 1 },
  quoteFreeze: { fadeIn: 0.14, fadeOutStart: 0.72, fadeOutDuration: 0.18, backdropOpacity: 0.22 },
  stampRain: { count: 8, fallDuration: 0.46, stagger: 0.05, spread: 1 },
  typingFlood: { rows: 9, flowDuration: 0.38, stagger: 0.035 },
  sparkleBurst: { count: 10, spread: 260, duration: 0.32 },
  impactLines: { cx: 0.5, cy: 0.48, count: 72, thickness: 1.25, opacity: 0.72, innerRadius: 0.17, start: 0, end: 0 },
  visionNoise: { type: "future", strength: 0.68, scanline: 0.78, glitch: 0.36, flicker: 0.42, tint: "#7dd3fc" },
  // startRadius=1.0で対角線(=四隅)にちょうど触れる大きさ。1.0未満だと閉じ始める前から
  // 常に黒い縁が見えてしまうため、既定値は少し余裕を持たせて画面を完全に覆う値にする。
  irisOut: { cx: 0.5, cy: 0.5, startRadius: 1.05, color: "#000000", closeStart: 1.7, closeEnd: 2.0 },
};

function hexToRgba(hex: string, opacity: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(String(hex || ""));
  if (!m) return `rgba(8, 10, 14, ${clamp(opacity, 0, 1)})`;
  const raw = m[1];
  const r = parseInt(raw.slice(0, 2), 16);
  const g = parseInt(raw.slice(2, 4), 16);
  const b = parseInt(raw.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${clamp(opacity, 0, 1)})`;
}

function resolveDisplaySettings(story: StoryScript | null | undefined) {
  const bubble = story?.displaySettings?.bubble || {};
  const subtitle = story?.displaySettings?.subtitle || {};
  const telop = story?.displaySettings?.telop || {};
  const speakerColors = story?.displaySettings?.speakerColors || {};
  const bubbleMaxCharsNum = Number(bubble.maxChars);
  const bubbleFontSizeNum = Number(bubble.fontSize);
  const bubbleBorderWidthNum = Number(bubble.borderWidth);
  const bubbleRadiusNum = Number(bubble.radius);
  const subtitleFontSizeNum = Number(subtitle.fontSize);
  const subtitleBgOpacityNum = Number(subtitle.bgOpacity);
  const subtitleBorderWidthNum = Number(subtitle.borderWidth);
  const subtitleBottomNum = Number(subtitle.bottom);
  const subtitleWidthNum = Number(subtitle.width);
  const telopXNum = Number(telop.x);
  const telopYNum = Number(telop.y);
  const telopSizeNum = Number(telop.size);
  return {
    bubble: {
      maxChars: Number.isFinite(bubbleMaxCharsNum) && bubbleMaxCharsNum > 0
        ? Math.round(bubbleMaxCharsNum)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.maxChars,
      fontSize: Number.isFinite(bubbleFontSizeNum)
        ? clamp(Math.round(bubbleFontSizeNum), 24, 96)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.fontSize,
      fontFamily: bubble.fontFamily || DEFAULT_BUBBLE_DISPLAY_SETTINGS.fontFamily,
      textColor: /^#([0-9a-fA-F]{6})$/.test(String(bubble.textColor || ""))
        ? String(bubble.textColor)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.textColor,
      bgColor: /^#([0-9a-fA-F]{6})$/.test(String(bubble.bgColor || ""))
        ? String(bubble.bgColor)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.bgColor,
      borderWidth: Number.isFinite(bubbleBorderWidthNum)
        ? clamp(bubbleBorderWidthNum, 1, 12)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.borderWidth,
      radius: Number.isFinite(bubbleRadiusNum)
        ? clamp(Math.round(bubbleRadiusNum), 4, 40)
        : DEFAULT_BUBBLE_DISPLAY_SETTINGS.radius,
    },
    subtitle: {
      fontSize: Number.isFinite(subtitleFontSizeNum)
        ? clamp(Math.round(subtitleFontSizeNum), 24, 96)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.fontSize,
      fontFamily: subtitle.fontFamily || DEFAULT_SUBTITLE_DISPLAY_SETTINGS.fontFamily,
      textColor: /^#([0-9a-fA-F]{6})$/.test(String(subtitle.textColor || ""))
        ? String(subtitle.textColor)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.textColor,
      bgColor: /^#([0-9a-fA-F]{6})$/.test(String(subtitle.bgColor || ""))
        ? String(subtitle.bgColor)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.bgColor,
      bgOpacity: Number.isFinite(subtitleBgOpacityNum)
        ? clamp(subtitleBgOpacityNum, 0, 1)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.bgOpacity,
      border: subtitle.border !== false,
      borderColor: /^#([0-9a-fA-F]{6})$/.test(String(subtitle.borderColor || ""))
        ? String(subtitle.borderColor)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.borderColor,
      borderWidth: Number.isFinite(subtitleBorderWidthNum)
        ? clamp(subtitleBorderWidthNum, 0.5, 6)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.borderWidth,
      bottom: Number.isFinite(subtitleBottomNum)
        ? clamp(Math.round(subtitleBottomNum), 0, 200)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.bottom,
      width: Number.isFinite(subtitleWidthNum)
        ? clamp(subtitleWidthNum, 0.4, 1)
        : DEFAULT_SUBTITLE_DISPLAY_SETTINGS.width,
    },
    speakerColors: {
      zundamon: /^#([0-9a-fA-F]{6})$/.test(String(speakerColors.zundamon || ""))
        ? String(speakerColors.zundamon)
        : "#5fb84f",
      metan: /^#([0-9a-fA-F]{6})$/.test(String(speakerColors.metan || ""))
        ? String(speakerColors.metan)
        : "#e87bb0",
      default: /^#([0-9a-fA-F]{6})$/.test(String(speakerColors.default || ""))
        ? String(speakerColors.default)
        : DEFAULT_BUBBLE_COLOR,
    },
    telop: {
      x: Number.isFinite(telopXNum) ? clamp(telopXNum, 0, 1) : DEFAULT_TELOP_DISPLAY_SETTINGS.x,
      y: Number.isFinite(telopYNum) ? clamp(telopYNum, 0, 1) : DEFAULT_TELOP_DISPLAY_SETTINGS.y,
      size: Number.isFinite(telopSizeNum) ? clamp(telopSizeNum, 0.5, 3) : DEFAULT_TELOP_DISPLAY_SETTINGS.size,
    },
  };
}

function speakerBubbleColor(
  speaker: string,
  displaySettings: ReturnType<typeof resolveDisplaySettings>
): string {
  if (speaker === "zundamon") return displaySettings.speakerColors.zundamon;
  if (speaker === "metan") return displaySettings.speakerColors.metan;
  return CHARACTERS[speaker]?.bubbleColor ?? displaySettings.speakerColors.default ?? DEFAULT_BUBBLE_COLOR;
}

function resolveEffectSettings(settings?: StoryEffectSettings): ResolvedEffectSettings {
  return {
    entrance: { ...DEFAULT_EFFECT_SETTINGS.entrance, ...(settings?.entrance || {}) },
    emphasis: { ...DEFAULT_EFFECT_SETTINGS.emphasis, ...(settings?.emphasis || {}) },
    zoomPunch: { ...DEFAULT_EFFECT_SETTINGS.zoomPunch, ...(settings?.zoomPunch || {}) },
    quoteFreeze: { ...DEFAULT_EFFECT_SETTINGS.quoteFreeze, ...(settings?.quoteFreeze || {}) },
    stampRain: { ...DEFAULT_EFFECT_SETTINGS.stampRain, ...(settings?.stampRain || {}) },
    typingFlood: { ...DEFAULT_EFFECT_SETTINGS.typingFlood, ...(settings?.typingFlood || {}) },
    sparkleBurst: { ...DEFAULT_EFFECT_SETTINGS.sparkleBurst, ...(settings?.sparkleBurst || {}) },
    impactLines: { ...DEFAULT_EFFECT_SETTINGS.impactLines, ...(settings?.impactLines || {}) },
    visionNoise: { ...DEFAULT_EFFECT_SETTINGS.visionNoise, ...(settings?.visionNoise || {}) },
    irisOut: { ...DEFAULT_EFFECT_SETTINGS.irisOut, ...(settings?.irisOut || {}) },
  };
}

function mergeEffectSettings(
  base?: StoryEffectSettings,
  override?: StoryEffectSettings
): StoryEffectSettings | undefined {
  if (!base && !override) return undefined;
  return {
    entrance: { ...(base?.entrance || {}), ...(override?.entrance || {}) },
    emphasis: { ...(base?.emphasis || {}), ...(override?.emphasis || {}) },
    zoomPunch: { ...(base?.zoomPunch || {}), ...(override?.zoomPunch || {}) },
    quoteFreeze: { ...(base?.quoteFreeze || {}), ...(override?.quoteFreeze || {}) },
    stampRain: { ...(base?.stampRain || {}), ...(override?.stampRain || {}) },
    typingFlood: { ...(base?.typingFlood || {}), ...(override?.typingFlood || {}) },
    sparkleBurst: { ...(base?.sparkleBurst || {}), ...(override?.sparkleBurst || {}) },
    impactLines: { ...(base?.impactLines || {}), ...(override?.impactLines || {}) },
    visionNoise: { ...(base?.visionNoise || {}), ...(override?.visionNoise || {}) },
    irisOut: { ...(base?.irisOut || {}), ...(override?.irisOut || {}) },
  };
}

function mergeVisionNoiseTurnSettings(
  base: ResolvedEffectSettings["visionNoise"],
  turnVisionNoise?: StoryTurn["visionNoise"]
): ResolvedEffectSettings["visionNoise"] {
  if (!turnVisionNoise || turnVisionNoise === true) return base;
  if (typeof turnVisionNoise !== "object") return base;
  return { ...base, ...turnVisionNoise };
}

function mediaStaticSrc(path: string): string {
  const qidx = path.indexOf("?");
  if (qidx < 0) return staticFile(path);
  const base = path.slice(0, qidx);
  const suffix = path.slice(qidx);
  return `${staticFile(base)}${suffix}`;
}

const ExtraEffectsLayer: React.FC<{
  active: StoryTurn;
  progress: number;
  width: number;
  height: number;
  settings?: StoryEffectSettings;
  // このターン開始からの実経過秒数（クランプ無し）。pause秒による次ターンまでの
  // 間（gap）でもiris-outが動き続けられるよう、progress(0-1に固定)とは別に渡す。
  rawElapsedSinceStart?: number;
  // セリフ実尺(dur)+次ターン開始までのgapを含めた、このターンに使える最大秒数（固定値）。
  availableWindow?: number;
  onlyImpactLines?: boolean;
  hideImpactLines?: boolean;
}> = ({ active, progress, width, height, settings, rawElapsedSinceStart, availableWindow, onlyImpactLines, hideImpactLines }) => {
  const layers: React.ReactNode[] = [];
  const effectSettings = resolveEffectSettings(mergeEffectSettings(settings, active.effectSettings));
  const zoomPunchCfg = effectSettings.zoomPunch;
  const quoteFreezeCfg = effectSettings.quoteFreeze;
  const stampRainCfg = effectSettings.stampRain;
  const typingFloodCfg = effectSettings.typingFlood;
  const sparkleBurstCfg = effectSettings.sparkleBurst;
  const impactLinesCfg = effectSettings.impactLines;
  const irisOutCfg = effectSettings.irisOut;
  const visionNoiseCfg = mergeVisionNoiseTurnSettings(effectSettings.visionNoise, active.visionNoise);
  const dur = Math.max(active.end - active.start, 0.001);
  const burstIn = clamp(progress / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
  const burstOut = 1 - clamp((progress - 0.58) / 0.22, 0, 1);
  const showImpactLines = !!(active.impactLines || active.impactText);

  if (onlyImpactLines && !showImpactLines) return null;

  if (!onlyImpactLines && active.zoomPunch) {
    // 実際のステージ拡大は StoryVideo 本体側の stageS 計算に組み込み済み（下記参照）。
    // ここでは重ねて光る縁取りフラッシュだけを描く（透明レイヤーなのでtransform/scaleは無意味）。
    // duration は「秒」指定なので、progress*dur で発動(ターン開始)からの経過秒数に変換する。
    const local = clamp((progress * dur) / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
    const punch = Math.sin(local * Math.PI);
    layers.push(
      <AbsoluteFill
        key="zoomPunch"
        style={{
          pointerEvents: "none",
          boxShadow: `inset 0 0 0 ${Math.round(18 * punch * zoomPunchCfg.borderStrength)}px rgba(255,255,255,${0.11 * punch * zoomPunchCfg.borderStrength})`,
        }}
      />
    );
  }

  if (!hideImpactLines && showImpactLines) {
    const elapsed = rawElapsedSinceStart ?? progress * dur;
    const lineStart = Math.max(0, impactLinesCfg.start);
    const lineEnd = Math.max(0, impactLinesCfg.end);
    const hasManualWindow = lineEnd > lineStart;
    const localLineProgress = hasManualWindow
      ? clamp((elapsed - lineStart) / Math.max(lineEnd - lineStart, 0.001), 0, 1)
      : progress;
    const lineBurstIn = clamp(localLineProgress / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
    const lineBurstOut = hasManualWindow
      ? 1 - clamp((elapsed - (lineEnd - 0.22)) / 0.22, 0, 1)
      : burstOut;
    const opacity = hasManualWindow && (elapsed < lineStart || elapsed >= lineEnd)
      ? 0
      : clamp(Math.min(lineBurstIn * 1.1, lineBurstOut), 0, 1);
    const count = Math.max(12, Math.min(180, Math.round(impactLinesCfg.count)));
    const gapDeg = 360 / count;
    const lineDeg = Math.min(gapDeg * 0.72, Math.max(0.35, impactLinesCfg.thickness));
    const originX = clamp(impactLinesCfg.cx, 0, 1) * 100;
    const originY = clamp(impactLinesCfg.cy, 0, 1) * 100;
    const inner = clamp(impactLinesCfg.innerRadius, 0, 0.8) * 100;
    const scale = lerp(1.08, 1, easeOutCubic(lineBurstIn));
    layers.push(
      <AbsoluteFill
        key="impactLines"
        style={{
          pointerEvents: "none",
          overflow: "hidden",
        }}
      >
        <AbsoluteFill
          style={{
            opacity: opacity * clamp(impactLinesCfg.opacity, 0, 1),
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
      </AbsoluteFill>
    );
  }

  if (!onlyImpactLines && active.quoteFreeze) {
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
            「{active.text}」
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  if (!onlyImpactLines && active.stampRain) {
    const text = active.stampRain;
    const opacity = clamp(Math.min(progress / 0.16, 1 - Math.max(0, progress - 0.76) / 0.18), 0, 1);
    const baseXs = [0.12, 0.28, 0.44, 0.62, 0.78, 0.2, 0.55, 0.84, 0.35, 0.7, 0.9, 0.08];
    const count = Math.max(1, Math.min(12, Math.round(stampRainCfg.count)));
    const spread = clamp(stampRainCfg.spread, 0.4, 1.4);
    const stamps = Array.from({ length: count }, (_, i) => {
      const x = clamp(0.5 + (baseXs[i % baseXs.length] - 0.5) * spread, 0.04, 0.96);
      const delay = i * stampRainCfg.stagger;
      const p = clamp((progress - delay) / Math.max(stampRainCfg.fallDuration, 0.05), 0, 1);
      const y = lerp(-0.16, 1.08, easeOutCubic(p));
      const rot = [-14, 8, -6, 12, -10, 6, -12, 10][i];
      const scale = 0.86 + (i % 3) * 0.12;
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: `${x * 100}%`,
            top: `${y * 100}%`,
            transform: `translate(-50%, -50%) rotate(${rot}deg) scale(${scale})`,
            opacity: opacity * clamp(p * 1.2, 0, 1),
            padding: "10px 22px",
            borderRadius: 999,
            background: "rgba(255,255,255,0.92)",
            border: "4px solid #5fb84f",
            color: "#1f4e1a",
            fontFamily: EXTRA_EFFECT_FONT,
            fontSize: 42,
            fontWeight: 900,
            boxShadow: "0 10px 24px rgba(0,0,0,0.22)",
          }}
        >
          {text}
        </div>
      );
    });
    layers.push(<AbsoluteFill key="stampRain" style={{ pointerEvents: "none", overflow: "hidden" }}>{stamps}</AbsoluteFill>);
  }

  if (!onlyImpactLines && active.typingFlood) {
    const opacity = clamp(Math.min(progress / 0.12, 1 - Math.max(0, progress - 0.82) / 0.16), 0, 1);
    const rowCount = Math.max(2, Math.min(12, Math.round(typingFloodCfg.rows)));
    const rows = Array.from({ length: rowCount }, (_, i) => {
      const x = i % 2 === 0 ? 0.06 : 0.48;
      const delay = i * typingFloodCfg.stagger;
      const p = clamp((progress - delay) / Math.max(typingFloodCfg.flowDuration, 0.05), 0, 1);
      const y = lerp(-0.12, 0.92, easeOutCubic(p));
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: `${x * 100}%`,
            top: `${y * 100}%`,
            width: `${(i % 2 === 0 ? 0.4 : 0.46) * 100}%`,
            transform: "translateY(-50%)",
            opacity: opacity * clamp(p * 1.1, 0, 1),
            background: i % 3 === 0 ? "rgba(247, 193, 76, 0.92)" : "rgba(255,255,255,0.92)",
            color: "#172032",
            borderRadius: 16,
            padding: "14px 18px",
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <div
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              background: i % 3 === 0 ? "#ad4f00" : "#5fb84f",
              flexShrink: 0,
            }}
          />
          <div style={{ fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif', fontWeight: 800, fontSize: 28 }}>
            {["新着メッセージ", "追加通知", "返信が増加", "全社チャット更新", "コメント集中"][i % 5]}
          </div>
        </div>
      );
    });
    layers.push(<AbsoluteFill key="typingFlood" style={{ pointerEvents: "none", overflow: "hidden" }}>{rows}</AbsoluteFill>);
  }

  if (!onlyImpactLines && active.sparkleBurst) {
    const opacity = clamp(Math.min(progress / 0.14, 1 - Math.max(0, progress - 0.68) / 0.2), 0, 1);
    const sparkCount = Math.max(4, Math.min(20, Math.round(sparkleBurstCfg.count)));
    // 中心位置。turn.sparklePos があればそれを使い、無ければ既定(画面中央よりやや上)。
    const originX = active.sparklePos?.x ?? 0.5;
    const originY = active.sparklePos?.y ?? 0.45;
    const sparks = Array.from({ length: sparkCount }, (_, i) => {
      const angle = (Math.PI * 2 * i) / sparkCount;
      const dist = lerp(40, sparkleBurstCfg.spread, easeOutCubic(clamp(progress / Math.max(sparkleBurstCfg.duration, 0.05), 0, 1)));
      const x = width * originX + Math.cos(angle) * dist;
      const y = height * originY + Math.sin(angle) * dist * 0.72;
      const scale = 0.7 + (i % 4) * 0.18;
      return (
        <div
          key={i}
          style={{
            position: "absolute",
            left: x,
            top: y,
            transform: `translate(-50%, -50%) scale(${scale}) rotate(${i * 18}deg)`,
            opacity,
            color: i % 2 === 0 ? "#fff2a3" : "#baf7df",
            fontSize: 58,
            textShadow: "0 0 24px rgba(255,255,255,0.4)",
          }}
        >
          ✦
        </div>
      );
    });
    layers.push(<AbsoluteFill key="sparkleBurst" style={{ pointerEvents: "none" }}>{sparks}</AbsoluteFill>);
  }

  if (!onlyImpactLines && active.irisOut) {
    // 経過秒数(elapsed)ベースの3フェーズ：
    // 1) 登場（ターン開始時点では画面を覆う大きさ＝見た目には何も起きていない状態から、
    //    固定の速度で少しゆっくりめに「初期の◯」(startRadius)まで縮む）
    // 2) 保持（初期の◯のまま）
    // 3) 収縮（closeStart→closeEndで初期の◯→0へ連続的に縮む。この間に顔サイズなどを通過する）
    // closeStart/closeEndの2値だけがユーザー指定＝ターン開始からの絶対秒数。
    const diag = Math.sqrt(width * width + height * height) / 2;
    const baseRadius = diag * irisOutCfg.startRadius;
    // 登場アニメーションの開始半径（画面を確実に覆う大きさ。対角線の1.3倍）。
    const coverRadius = diag * 1.3;
    const APPEAR_DUR = 0.6; // 固定・少しゆっくりめ（設定不可）
    // このターン開始からの実経過秒数（クランプ無し）。pause秒による次ターンまでの
    // 間（gap）も含めて動けるよう、progress(セリフ実尺で1.0固定)ではなくこちらを使う。
    // 未指定時のみ従来どおり progress*dur にフォールバック。
    const elapsed = rawElapsedSinceStart ?? progress * dur;
    const window = Math.max(availableWindow ?? dur, dur);
    // 設定値が「セリフ実尺(dur)+pauseの間」を超えていても、その末尾では必ず閉じきった
    // 状態にする。closeEndを上限に収めた上で、指定していた「閉じる所要時間」は変えずに
    // 開始側もスライドする（開始だけclampすると閉区間の幅が潰れて閉じきらずに止まって見えるため）。
    const rawCloseEnd = Math.max(irisOutCfg.closeEnd, 0.05);
    const rawCloseStart = clamp(irisOutCfg.closeStart, 0, rawCloseEnd);
    const closeDuration = Math.max(rawCloseEnd - rawCloseStart, 0.05);
    const closeEnd = Math.min(rawCloseEnd, window);
    const closeStart = Math.max(0, closeEnd - closeDuration);
    const appearDur = Math.min(APPEAR_DUR, closeStart);
    let radius: number;
    if (appearDur > 0 && elapsed < appearDur) {
      radius = lerp(coverRadius, baseRadius, easeInOutCubic(clamp(elapsed / appearDur, 0, 1)));
    } else if (elapsed < closeStart) {
      radius = baseRadius;
    } else {
      const cp = clamp((elapsed - closeStart) / Math.max(closeEnd - closeStart, 0.05), 0, 1);
      radius = lerp(baseRadius, 0, easeInOutCubic(cp));
    }
    const cxPct = clamp(irisOutCfg.cx, 0, 1) * 100;
    const cyPct = clamp(irisOutCfg.cy, 0, 1) * 100;
    const color = irisOutCfg.color || "#000000";
    layers.push(
      <AbsoluteFill
        key="irisOut"
        style={{
          pointerEvents: "none",
          background: color,
          maskImage: `radial-gradient(circle ${radius}px at ${cxPct}% ${cyPct}%, transparent 0, transparent ${Math.max(
            radius - 1,
            0
          )}px, ${color} ${radius}px, ${color} 100%)`,
          WebkitMaskImage: `radial-gradient(circle ${radius}px at ${cxPct}% ${cyPct}%, transparent 0, transparent ${Math.max(
            radius - 1,
            0
          )}px, ${color} ${radius}px, ${color} 100%)`,
        }}
      />
    );
  }

  if (!onlyImpactLines && active.visionNoise) {
    const elapsed = rawElapsedSinceStart ?? progress * dur;
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
    const tint = visionNoiseCfg.tint || "#7dd3fc";
    const showTint = noiseType !== "snow";
    const scanlineOpacity = noiseType === "snow" ? 0.08 : noiseType === "vhs" ? 0.3 : 0.2;
    const darkScanlineOpacity = noiseType === "snow" ? 0.04 : noiseType === "vhs" ? 0.24 : 0.16;
    const dotWhite = noiseType === "snow" ? 0.92 : noiseType === "vhs" ? 0.32 : 0.55;
    const dotBlack = noiseType === "snow" ? 0.78 : noiseType === "vhs" ? 0.36 : 0.48;
    const dotSizeA = noiseType === "snow" ? "3px 3px" : noiseType === "vhs" ? "10px 10px" : "7px 7px";
    const dotSizeB = noiseType === "snow" ? "4px 4px" : noiseType === "vhs" ? "14px 14px" : "9px 9px";
    const scanlineSize = noiseType === "vhs" ? "100% 3px" : "100% 4px";
    layers.push(
      <AbsoluteFill
        key="visionNoise"
        style={{
          pointerEvents: "none",
          opacity: flickerOpacity,
        }}
      >
        {showTint ? (
          <AbsoluteFill
            style={{
              background: hexToRgba(tint, (noiseType === "glitch" ? 0.34 : 0.28) * strength),
              mixBlendMode: "screen",
            }}
          />
        ) : null}
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
        {glitch > 0 ? (
          <>
            <div
              style={{
                position: "absolute",
                left: -width * 0.02 + glitchShift,
                top: height * (0.18 + (Math.sin(elapsed * 3.1) + 1) * 0.08),
                width: width * 1.04,
                height: 24 + glitch * (noiseType === "glitch" ? 72 : 42),
                background: noiseType === "snow" ? `rgba(255,255,255,${0.18 * glitch})` : hexToRgba(tint, (noiseType === "glitch" ? 0.62 : 0.45) * glitch),
                transform: `translateX(${glitchShift}px)`,
                mixBlendMode: "screen",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: -width * 0.02 - glitchShift,
                top: height * (0.58 + (Math.sin(elapsed * 4.7) + 1) * 0.06),
                width: width * 1.04,
                height: 16 + glitch * (noiseType === "glitch" ? 58 : 34),
                background: noiseType === "vhs" ? `rgba(0,0,0,${0.28 * glitch})` : `rgba(255,80,130,${(noiseType === "glitch" ? 0.44 : 0.3) * glitch})`,
                transform: `translateX(${-glitchShift}px)`,
                mixBlendMode: noiseType === "vhs" ? "multiply" : "screen",
              }}
            />
          </>
        ) : null}
      </AbsoluteFill>
    );
  }

  return layers.length ? <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden" }}>{layers}</AbsoluteFill> : null;
};

// ─── PC画面インサートコンポーネント ─────────────────────────

const INSERT_BG = "#11151c";
const DEFAULT_INSERT_WIDTH = 1;
const DEFAULT_INSERT_FONT_SCALE = 1;

function insertWidthScale(insert: { width?: number } | null | undefined): number {
  return clamp(insert?.width ?? DEFAULT_INSERT_WIDTH, 0.6, 2.2);
}

// 文字サイズの倍率（幅と同じ0.6〜1.6の範囲）。各要素のfontSizeに一律で掛ける。
function insertFontScale(insert: { fontScale?: number } | null | undefined): number {
  return clamp(insert?.fontScale ?? DEFAULT_INSERT_FONT_SCALE, 0.6, 1.6);
}

// 背景色（未指定ならkindごとの既定色を使う）。
function insertBg(insert: { bg?: string } | null | undefined, fallback: string): string {
  return insert?.bg || fallback;
}

// インサート周り（パネルの外側全画面）の背景色/画像。videocallには無い。
function insertBackdropBg(insert: StoryInsert | null | undefined, fallback: string): string {
  if (!insert || insert.kind === "videocall" || insert.kind === "whiteboard_explain") return fallback;
  return insert.backdropBg || fallback;
}
function insertBackdropImage(insert: StoryInsert | null | undefined): string | undefined {
  if (!insert || insert.kind === "videocall" || insert.kind === "whiteboard_explain") return undefined;
  return insert.backdropImage || undefined;
}

/** 警告ダイアログ */
const InsertWarning: React.FC<{ insert: Extract<StoryInsert, { kind: "warning" }> }> = ({ insert }) => {
  const title = insert.title ?? "警告";
  const panelWidth = Math.round(860 * insertWidthScale(insert));
  const fs = insertFontScale(insert);
  return (
    <div
      style={{
        background: insertBg(insert, "#1a1d24"),
        border: "3px solid #9ed957",
        borderRadius: 16,
        padding: "48px 64px",
        width: panelWidth,
        boxShadow: "0 0 0 1px rgba(95,184,79,0.25), 0 24px 64px rgba(0,0,0,0.6)",
        display: "flex",
        flexDirection: "column",
        gap: 28,
      }}
    >
      {/* アプリ名（ZunMonitor=監視ツール） */}
      <div
        style={{
          fontSize: 26 * fs,
          color: "#5fb84f",
          fontFamily: "sans-serif",
          fontWeight: 700,
          letterSpacing: "0.12em",
        }}
      >
        ZunMonitor
      </div>
      {/* タイトルバー */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 20,
        }}
      >
        <span style={{ fontSize: 56 * fs, lineHeight: 1, color: "#e87b3a" }}>⚠</span>
        <span
          style={{
            fontSize: 44 * fs,
            fontWeight: 700,
            color: "#e87b3a",
            fontFamily: "sans-serif",
            letterSpacing: "0.04em",
          }}
        >
          {title}
        </span>
      </div>
      {/* 区切り */}
      <div style={{ height: 2, background: "rgba(95,184,79,0.35)", borderRadius: 1 }} />
      {/* 本文 */}
      <div
        style={{
          fontSize: 48 * fs,
          color: "#e8e0d6",
          fontFamily: "sans-serif",
          lineHeight: 1.5,
          fontWeight: 400,
          letterSpacing: "0.03em",
          whiteSpace: "pre-wrap",
        }}
      >
        {insert.text}
      </div>
    </div>
  );
};

/** AIチャット（ZunAI） */
const InsertChat: React.FC<{ insert: Extract<StoryInsert, { kind: "chat" }> }> = ({ insert }) => {
  const panelWidth = Math.round(920 * insertWidthScale(insert));
  const fs = insertFontScale(insert);
  const userText = String(insert.user ?? "").trim();
  const aiMessages = (insert.ai ?? []).filter((msg) => String(msg ?? "").trim() !== "");
  return (
    <div
      style={{
        background: insertBg(insert, "#181c24"),
        border: "2px solid #3e8b39",
        borderRadius: 20,
        width: panelWidth,
        overflow: "hidden",
        boxShadow: "0 24px 64px rgba(0,0,0,0.65)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ヘッダー */}
      <div
        style={{
          background: "#18241a",
          padding: "20px 32px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          borderBottom: "1.5px solid #2f5a33",
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: "linear-gradient(135deg, #7bd267, #3e9e3a)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 34 * fs,
            fontWeight: 700,
            color: "#bdf08a",
            fontFamily: "sans-serif",
            letterSpacing: "0.04em",
          }}
        >
          ZunAI
        </span>
      </div>
      {/* メッセージ本体 */}
      <div
        style={{
          padding: "28px 32px",
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        {/* ユーザーメッセージ（右寄せ） */}
        {userText ? (
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <div
              style={{
                background: "#4a9e44",
                color: "#fff",
                borderRadius: "18px 4px 18px 18px",
                padding: "16px 24px",
                fontSize: 38 * fs,
                fontFamily: "sans-serif",
                lineHeight: 1.5,
                maxWidth: "76%",
                fontWeight: 500,
                whiteSpace: "pre-wrap",
              }}
            >
              {userText}
            </div>
          </div>
        ) : null}
        {/* AI返答（左寄せ）。複数行。highlight 番号の吹き出しを強調。 */}
        {aiMessages.map((msg, i) => {
          const isHighlighted = typeof insert.highlight === "number" && insert.highlight === i;
          return (
            <div key={i} style={{ display: "flex", justifyContent: "flex-start" }}>
              <div
                style={{
                  background: isHighlighted ? "#2c1a08" : "#232a38",
                  color: isHighlighted ? "#f0e0c0" : "#c8d0e0",
                  borderRadius: "4px 18px 18px 18px",
                  padding: "16px 24px",
                  fontSize: 38 * fs,
                  fontFamily: "sans-serif",
                  lineHeight: 1.5,
                  maxWidth: "76%",
                  fontWeight: 400,
                  border: isHighlighted ? "2.5px solid #e07840" : "2px solid #2f3a50",
                  boxShadow: isHighlighted ? "0 0 0 4px rgba(224,120,64,0.18)" : "none",
                  whiteSpace: "pre-wrap",
                }}
              >
                {msg}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** ステータスOK画面 */
const InsertOk: React.FC<{ insert: Extract<StoryInsert, { kind: "ok" }> }> = ({ insert }) => {
  const text = insert.text ?? "正常";
  const panelWidth = Math.round(640 * insertWidthScale(insert));
  const fs = insertFontScale(insert);
  return (
    <div
      style={{
        background: insertBg(insert, "#141e18"),
        border: "3px solid #2ea86a",
        borderRadius: 20,
        padding: "56px 80px",
        width: panelWidth,
        boxShadow: "0 0 0 1px rgba(46,168,106,0.2), 0 24px 64px rgba(0,0,0,0.6)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 32,
      }}
    >
      {/* アプリ名（ZunMonitor=監視ツール・正常） */}
      <div
        style={{
          fontSize: 26 * fs,
          color: "#5fae84",
          fontFamily: "sans-serif",
          fontWeight: 700,
          letterSpacing: "0.12em",
        }}
      >
        ZunMonitor
      </div>
      <div
        style={{
          fontSize: 96 * fs,
          color: "#2ea86a",
          lineHeight: 1,
          fontFamily: "sans-serif",
          fontWeight: 700,
        }}
      >
        ✓
      </div>
      <div
        style={{
          fontSize: 56 * fs,
          color: "#a0e8c0",
          fontFamily: "sans-serif",
          fontWeight: 600,
          letterSpacing: "0.06em",
          whiteSpace: "pre-wrap",
          textAlign: "center",
        }}
      >
        {text}
      </div>
    </div>
  );
};

// teamchat 用: キャラID → 表示名。
const TEAMCHAT_DISPLAY: Record<string, string> = {
  zundamon: "ずんだもん",
  metan: "四国めたん",
};

// teamchat 用: 発言者情報（アイコン画像URL・名前・名前色）を解決するヘルパー。
function resolveTeamChatSender(from: string): {
  imgSrc: string | null;
  name: string;
  nameColor: string;
} {
  if (from in CHARACTERS) {
    const cdef = CHARACTERS[from];
    return {
      // 顔だけのアイコン（base は目/口/腕が別レイヤーでのっぺらぼうになるため専用 icon を使う）。
      imgSrc: staticFile(`avatars/${cdef.avatar}/icon.png`),
      name: TEAMCHAT_DISPLAY[from] ?? from,
      nameColor: cdef.bubbleColor ?? DEFAULT_BUBBLE_COLOR,
    };
  }
  if (from in MOBS) {
    const m = MOBS[from];
    return {
      imgSrc: staticFile(m.images.normal.closed),
      name: from,
      nameColor: DEFAULT_BUBBLE_COLOR,
    };
  }
  return { imgSrc: null, name: from, nameColor: DEFAULT_BUBBLE_COLOR };
}

function teamChatInitial(name: string): string {
  return Array.from((name || "?").trim())[0] ?? "?";
}

/** 社内チャット（ZunChat・Slack風） */
const InsertTeamChat: React.FC<{ insert: Extract<StoryInsert, { kind: "teamchat" }> }> = ({
  insert,
}) => {
  const channel = insert.channel ?? "general";
  const panelWidth = Math.round(1480 * insertWidthScale(insert));
  const fs = insertFontScale(insert);
  return (
    <div
      style={{
        background: insertBg(insert, "#f6f8fc"),
        border: "3px solid #9ed957",
        borderRadius: 22,
        width: panelWidth,
        overflow: "hidden",
        boxShadow: "0 24px 64px rgba(0,0,0,0.45)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ヘッダー */}
      <div
        style={{
          background: "#bdf08a",
          padding: "18px 32px",
          display: "flex",
          alignItems: "center",
          gap: 18,
          borderBottom: "2px solid #9ed957",
        }}
      >
        {/* ワークスペースアイコン（汎用グリッドロゴ風） */}
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: "linear-gradient(135deg, #4fa83f 0%, #2c7d28 100%)",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 4,
              width: 20,
              height: 20,
            }}
          >
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 2,
                  background: "rgba(255,255,255,0.9)",
                }}
              />
            ))}
          </div>
        </div>
        {/* チャンネル名 */}
        <span
          style={{
            fontSize: 48 * fs,
            fontWeight: 700,
            color: "#1f4012",
            fontFamily: "sans-serif",
            letterSpacing: "0.03em",
          }}
        >
          # {channel}
        </span>
        {/* 仕切り */}
        <div
          style={{ width: 1.5, height: 28, background: "#7cb84a", marginLeft: 4, marginRight: 4 }}
        />
        <span style={{ fontSize: 28 * fs, color: "#3f6a2a", fontFamily: "sans-serif", fontWeight: 600 }}>
          ZunChat
        </span>
      </div>

      {/* メッセージ一覧 */}
      <div
        style={{
          padding: "24px 32px",
          display: "flex",
          flexDirection: "column",
          gap: 0,
        }}
      >
        {insert.messages.map((msg, i) => {
          const { imgSrc, name, nameColor } = resolveTeamChatSender(msg.from);
          return (
            <div
              key={i}
              style={{
                display: "flex",
                flexDirection: "row",
                alignItems: "flex-start",
                gap: 26,
                padding: "18px 22px",
                borderRadius: 12,
                background: msg.highlight
                  ? "rgba(245,185,40,0.16)"
                  : "transparent",
                borderLeft: msg.highlight
                  ? "4px solid #f0b840"
                  : "4px solid transparent",
                marginBottom: 4,
              }}
            >
              {/* 丸アバターアイコン */}
              <div
                style={{
                  width: 88,
                  height: 88,
                  borderRadius: "50%",
                  overflow: "hidden",
                  position: "relative",
                  flexShrink: 0,
                  background: nameColor,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#fff",
                  fontFamily: "sans-serif",
                  fontSize: 42 * fs,
                  fontWeight: 800,
                  lineHeight: 1,
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    inset: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {teamChatInitial(name)}
                </span>
                {imgSrc ? (
                  <img
                    src={imgSrc}
                    onError={(e) => {
                      const el = e.currentTarget as HTMLImageElement;
                      el.style.display = "none";
                    }}
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      objectPosition: "center 10%",
                      display: "block",
                    }}
                  />
                ) : null}
              </div>

              {/* 名前 ＋ 本文 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1 }}>
                <span
                  style={{
                    fontSize: 40 * fs,
                    fontWeight: 700,
                    color: nameColor,
                    fontFamily: "sans-serif",
                    letterSpacing: "0.02em",
                  }}
                >
                  {name}
                </span>
                <span
                  style={{
                    fontSize: 54 * fs,
                    color: "#2a2e38",
                    fontFamily: "sans-serif",
                    lineHeight: 1.45,
                    fontWeight: 400,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {msg.text}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/** メーラー（メール閲覧画面・Gmail風ライトテーマ） */
const InsertMailer: React.FC<{ insert: Extract<StoryInsert, { kind: "mailer" }> }> = ({ insert }) => {
  // 差出人の頭文字とアバター色
  const fromName = insert.from ?? "送信者";
  const initial = fromName.charAt(0).toUpperCase() || "?";
  const panelWidth = Math.round(1380 * insertWidthScale(insert));
  const fs = insertFontScale(insert);
  // アバター色は headletter のコードポイントから決定論的に選ぶ（固定色・ランダムでない）
  const AVATAR_COLORS = ["#1a73e8", "#0f9d58", "#f4511e", "#8430ce", "#188038", "#d50000"];
  const avatarColor = AVATAR_COLORS[fromName.charCodeAt(0) % AVATAR_COLORS.length];

  return (
    <div
      style={{
        background: insertBg(insert, "#f6f8fc"),
        borderRadius: 12,
        width: panelWidth,
        overflow: "hidden",
        boxShadow: "0 4px 24px rgba(60,64,67,0.18), 0 1px 6px rgba(60,64,67,0.1)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "sans-serif",
      }}
    >
      {/* ツールバー（汎用メーラー感・ブランドなし） */}
      <div
        style={{
          background: "#ffffff",
          padding: "14px 28px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          borderBottom: "1px solid #e0e0e0",
        }}
      >
        <span style={{ fontSize: 34 * fs, color: "#5fb84f", lineHeight: 1 }}>✉</span>
        <span
          style={{
            fontSize: 32 * fs,
            fontWeight: 700,
            color: "#5fb84f",
            letterSpacing: "0.01em",
          }}
        >
          ZunMail
        </span>
      </div>

      {/* メールカード本体 */}
      <div
        style={{
          background: "#ffffff",
          margin: "24px 28px",
          borderRadius: 8,
          padding: "36px 48px",
          boxShadow: "0 2px 8px rgba(60,64,67,0.12)",
          display: "flex",
          flexDirection: "column",
          gap: 24,
        }}
      >
        {/* 件名 */}
        <div
          style={{
            fontSize: 56 * fs,
            fontWeight: 700,
            color: "#202124",
            lineHeight: 1.3,
          }}
        >
          {insert.subject}
        </div>

        {/* 差出人行 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 18,
          }}
        >
          {/* 丸アバター（頭文字） */}
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              background: avatarColor,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ffffff",
              fontSize: 34 * fs,
              fontWeight: 700,
            }}
          >
            {initial}
          </div>
          {/* 差出人名・アドレス */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span style={{ fontSize: 38 * fs, fontWeight: 600, color: "#202124" }}>
                {fromName}
              </span>
              {insert.fromAddr ? (
                <span style={{ fontSize: 30 * fs, color: "#80868b" }}>
                  {"<"}{insert.fromAddr}{">"}
                </span>
              ) : null}
            </div>
          </div>
          {/* 時刻（右端） */}
          {insert.time ? (
            <div style={{ fontSize: 32 * fs, color: "#80868b", flexShrink: 0 }}>
              {insert.time}
            </div>
          ) : null}
        </div>

        {/* 区切り線 */}
        <div style={{ height: 1, background: "#e0e0e0" }} />

        {/* 本文 */}
        <div
          style={{
            fontSize: 42 * fs,
            color: "#3c4043",
            lineHeight: 1.65,
            fontWeight: 400,
            whiteSpace: "pre-wrap",
          }}
        >
          {insert.body}
        </div>

        {/* 返信ボタン飾り（雰囲気用・非機能） */}
        <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
          {["↩ 返信", "→ 転送"].map((label) => (
            <div
              key={label}
              style={{
                border: "1px solid #dadce0",
                borderRadius: 20,
                padding: "10px 28px",
                fontSize: 28 * fs,
                color: "#5f6368",
              }}
            >
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

function videoParticipantAsset(speaker: string): {
  kind: "char" | "mob" | "ai" | "unknown";
  src?: string;
  label: string;
  accent: string;
} {
  if (speaker === "AI") {
    return { kind: "ai", label: "ZunAI", accent: "#9ed957" };
  }
  if (speaker in CHARACTERS) {
    const cdef = CHARACTERS[speaker];
    return {
      kind: "char",
      src: staticFile(`avatars/${cdef.avatar}/icon.png`),
      label: TEAMCHAT_DISPLAY[speaker] ?? speaker,
      accent: cdef.bubbleColor ?? "#9ed957",
    };
  }
  if (speaker in MOBS) {
    const m = MOBS[speaker];
    return {
      kind: "mob",
      src: staticFile(m.images.normal.closed),
      label: speaker,
      accent: "#d7e56d",
    };
  }
  return { kind: "unknown", label: speaker, accent: "#9ed957" };
}

// participant.bgImage（自分で追加した画像）用。5種のプリセットと同じ暗め加工で重ねる。
function videoBgImageStyle(imagePath: string): React.CSSProperties {
  return {
    background: "linear-gradient(180deg, rgba(6,10,14,0.16), rgba(6,10,14,0.34))",
    backgroundImage: `linear-gradient(180deg, rgba(8,12,16,0.08), rgba(8,12,16,0.34)), url(${staticFile(imagePath)})`,
    backgroundSize: "cover",
    backgroundPosition: "center center",
  };
}

function videoBgStyle(style: string | undefined): React.CSSProperties {
  switch (style) {
    case "home":
      return {
        background:
          "linear-gradient(180deg, rgba(6,10,14,0.16), rgba(6,10,14,0.3))",
        backgroundImage: `linear-gradient(180deg, rgba(8,12,16,0.08), rgba(8,12,16,0.34)), url(${staticFile("background/room.png")})`,
        backgroundSize: "cover",
        backgroundPosition: "center center",
      };
    case "meeting_room":
      return {
        background:
          "linear-gradient(180deg, rgba(6,10,14,0.18), rgba(6,10,14,0.34))",
        backgroundImage: `linear-gradient(180deg, rgba(8,12,16,0.08), rgba(8,12,16,0.34)), url(${staticFile("background/kaigisitsu.png")})`,
        backgroundSize: "cover",
        backgroundPosition: "center center",
      };
    case "ai":
      return {
        background:
          "radial-gradient(circle at 30% 30%, rgba(158,217,87,0.28), transparent 24%), radial-gradient(circle at 75% 22%, rgba(52,164,118,0.2), transparent 20%), linear-gradient(135deg, #18281d 0%, #122018 44%, #0d1612 100%)",
      };
    case "green":
      return {
        background:
          "linear-gradient(135deg, #234129 0%, #1a3322 48%, #13231a 100%)",
      };
    case "office":
    default:
      return {
        background:
          "linear-gradient(180deg, rgba(6,10,14,0.16), rgba(6,10,14,0.34))",
        backgroundImage: `linear-gradient(180deg, rgba(8,12,16,0.08), rgba(8,12,16,0.34)), url(${staticFile("background/office_bg.png")})`,
        backgroundSize: "cover",
        backgroundPosition: "center center",
      };
  }
}

// ZunMeet パネルのレイアウト定数。
// タイル幅は fr 比率でなく px で確定させ、フィード（立ち絵）のスケール計算と共有する。
function getVideoCallLayoutMetrics(scale: number) {
  const panelW = Math.round(1500 * scale);      // パネル全体の幅
  const gridPad = Math.round(22 * scale);       // タイルグリッドの padding
  const gridGap = Math.round(18 * scale);       // タイル間の gap
  const tileH = Math.round(760 * scale);        // タイルの高さ（focus時はグリッド高さを固定）
  const focusTileW = Math.round(920 * scale);   // focus時の大タイル幅
  const smallTileW = panelW - gridPad * 2 - gridGap - focusTileW;
  const gridTileW = Math.round((panelW - gridPad * 2 - gridGap) / 2);
  return { panelW, gridPad, gridGap, tileH, focusTileW, smallTileW, gridTileW };
}
// タイル幅に対する立ち絵の表示幅の割合。大小タイルで同一＝「同じ映像がサイズだけ変わる」。
const VC_FEED_AVATAR_FRAC = 0.8;
// 立ち絵を下へずらす割合（立ち絵高さ基準）。胸元をクロップして顔に寄せる（Webカメラのアップ感）。
const VC_FEED_BOTTOM_SHIFT = 0.12;
// モブ（1枚絵）の表示幅割合。立ち絵より横長の素材が多いので控えめにする。
const VC_FEED_MOB_FRAC = 0.62;

type VideoCallParticipant = NonNullable<
  Extract<StoryInsert, { kind: "videocall" }>["participants"]
>[number];

// タイルの中身（カメラ映像風の描画）は大小共通・固定。
// フォーカス切替は「どのタイルを大きく表示するか」だけを変え、描画方法は変えない。
// renderFeed: 参加者のライブフィード（立ち絵アニメ合成）。StoryVideo 本体から渡される。
const InsertVideoCall: React.FC<{
  insert: Extract<StoryInsert, { kind: "videocall" }>;
  activeSpeaker?: string;
  renderFeed?: (
    participant: VideoCallParticipant,
    opts: { large: boolean; tileW: number }
  ) => React.ReactNode;
}> = ({ insert, activeSpeaker, renderFeed }) => {
  const vcScale = insertWidthScale(insert);
  const vc = getVideoCallLayoutMetrics(vcScale);
  const room = insert.room || "定例会議";
  const layout = insert.layout || "focus";
  const participants = (insert.participants || []).slice(0, 6);
  const currentSpeaker = insert.activeSpeaker || activeSpeaker || participants[0]?.speaker || "";
  const focus = participants.find((p) => p.speaker === currentSpeaker) || participants[0];
  const others = participants.filter((p) => p !== focus);

  const renderTile = (
    participant: VideoCallParticipant,
    opts?: { large?: boolean }
  ) => {
    const asset = videoParticipantAsset(participant.speaker);
    const name = participant.name || asset.label;
    const isActive = participant.speaker === currentSpeaker;
    const large = !!opts?.large;
    const bg = participant.bgImage
      ? videoBgImageStyle(participant.bgImage)
      : videoBgStyle(participant.bgStyle || (participant.speaker === "AI" ? "ai" : "office"));
    const tileW = large ? vc.focusTileW : layout === "grid" ? vc.gridTileW : vc.smallTileW;
    const feedNode =
      !participant.cameraOff && asset.kind !== "ai" && renderFeed
        ? renderFeed(participant, { large, tileW })
        : null;
    return (
      <div
        key={`${participant.speaker}-${name}`}
        style={{
          position: "relative",
          height: "100%",
          minHeight: large ? 720 : 0,
          overflow: "hidden",
          borderRadius: large ? 24 : 20,
          border: isActive ? `4px solid ${asset.accent}` : "2px solid rgba(255,255,255,0.14)",
          boxShadow: isActive
            ? `0 0 0 6px rgba(158,217,87,0.18), 0 18px 36px rgba(0,0,0,0.34)`
            : "0 14px 30px rgba(0,0,0,0.24)",
          ...bg,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02) 22%, rgba(0,0,0,0.14) 100%)",
          }}
        />
        {asset.kind !== "ai" && !participant.cameraOff ? (
          <>
            <div
              style={{
                position: "absolute",
                inset: large ? "10% 12% 22%" : "12% 10% 24%",
                borderRadius: large ? 20 : 16,
                background: "rgba(255,255,255,0.06)",
                boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.05)",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                bottom: 0,
                height: large ? "19%" : "22%",
                background: "linear-gradient(180deg, rgba(10,14,18,0.12), rgba(8,11,15,0.42) 32%, rgba(8,11,15,0.78) 100%)",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: large ? "18%" : "14%",
                right: large ? "18%" : "14%",
                bottom: large ? "16%" : "18%",
                height: large ? 10 : 8,
                borderRadius: 999,
                background: "rgba(8,11,15,0.55)",
              }}
            />
          </>
        ) : null}
        {participant.cameraOff ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ecf7d4",
              fontFamily: EXTRA_EFFECT_FONT,
              fontSize: large ? 42 : 28,
              letterSpacing: "0.08em",
            }}
          >
            CAMERA OFF
          </div>
        ) : asset.kind === "ai" ? (
          <>
            <div
              style={{
                position: "absolute",
                inset: "18% 20%",
                borderRadius: 26,
                border: "2px solid rgba(158,217,87,0.38)",
                background:
                  "linear-gradient(180deg, rgba(20,33,23,0.62), rgba(8,15,11,0.36))",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  position: "relative",
                  width: "74%",
                  height: large ? 84 : 56,
                }}
              >
                {Array.from({ length: 9 }, (_, i) => {
                  const h = [0.22, 0.46, 0.68, 0.42, 0.88, 0.5, 0.74, 0.38, 0.26][i];
                  return (
                    <div
                      key={i}
                      style={{
                        position: "absolute",
                        left: `${i * 11}%`,
                        bottom: 0,
                        width: large ? 22 : 15,
                        height: `${h * 100}%`,
                        borderRadius: 999,
                        background: i % 2 ? "#9ed957" : "#7de0b0",
                        boxShadow: "0 0 18px rgba(158,217,87,0.3)",
                      }}
                    />
                  );
                })}
              </div>
            </div>
            <div
              style={{
                position: "absolute",
                top: large ? 20 : 14,
                right: large ? 20 : 14,
                padding: large ? "8px 14px" : "6px 10px",
                borderRadius: 999,
                background: "rgba(12,24,15,0.72)",
                border: "1px solid rgba(158,217,87,0.42)",
                color: "#dff8bc",
                fontFamily: EXTRA_EFFECT_FONT,
                fontSize: large ? 20 : 14,
                letterSpacing: "0.06em",
              }}
            >
              ZunAI
            </div>
          </>
        ) : feedNode ? (
          feedNode
        ) : asset.src ? (
          <Img
            src={asset.src}
            style={{
              position: "absolute",
              left: "50%",
              bottom: large ? "4%" : "2%",
              width: asset.kind === "char"
                ? (large ? "34%" : "28%")
                : (large ? "42%" : "46%"),
              maxHeight: large ? "58%" : "54%",
              transform: "translateX(-50%)",
              objectFit: "contain",
              filter: "drop-shadow(0 12px 20px rgba(0,0,0,0.34))",
            }}
          />
        ) : (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#f6f7f9",
              fontFamily: EXTRA_EFFECT_FONT,
              fontSize: large ? 54 : 36,
            }}
          >
            {name.charAt(0)}
          </div>
        )}
        <div
          style={{
            position: "absolute",
            left: large ? 18 : 14,
            right: large ? 18 : 14,
            bottom: large ? 16 : 12,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <div
            style={{
              padding: large ? "8px 14px" : "6px 10px",
              borderRadius: 999,
              background: "rgba(9,13,20,0.68)",
              color: "#ffffff",
              fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif',
              fontWeight: 700,
              fontSize: large ? 26 : 18,
              lineHeight: 1,
            }}
          >
            {name}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {participant.muted ? (
              <div
                style={{
                  width: large ? 34 : 26,
                  height: large ? 34 : 26,
                  borderRadius: "50%",
                  background: "rgba(158,46,46,0.85)",
                  color: "#fff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: large ? 18 : 14,
                  fontWeight: 700,
                }}
              >
                M
              </div>
            ) : null}
            {isActive ? (
              <div
                style={{
                  width: large ? 14 : 10,
                  height: large ? 14 : 10,
                  borderRadius: "50%",
                  background: asset.accent,
                  boxShadow: `0 0 ${large ? 14 : 10}px ${asset.accent}`,
                }}
              />
            ) : null}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div
      style={{
        background: "#101822",
        border: "3px solid #9ed957",
        borderRadius: 24,
        width: vc.panelW,
        overflow: "hidden",
        boxShadow: "0 24px 64px rgba(0,0,0,0.5)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          background: "linear-gradient(180deg, #243827 0%, #1c2d20 100%)",
          padding: "16px 28px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid rgba(158,217,87,0.28)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: 14,
              background: "linear-gradient(135deg, #9ed957, #5fb84f)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#173017",
              fontFamily: EXTRA_EFFECT_FONT,
              fontWeight: 900,
              fontSize: 22,
            }}
          >
            ZM
          </div>
          <div>
            <div style={{ color: "#f4ffe0", fontFamily: EXTRA_EFFECT_FONT, fontSize: 30, letterSpacing: "0.05em" }}>
              ZunMeet
            </div>
            <div style={{ color: "#c4d8bc", fontFamily: '"Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif', fontSize: 20 }}>
              {room}
            </div>
          </div>
        </div>
        <div style={{ color: "#cce7b4", fontFamily: EXTRA_EFFECT_FONT, fontSize: 18, letterSpacing: "0.08em" }}>
          {participants.length} PARTICIPANTS
        </div>
      </div>

      <div
        style={{
          padding: vc.gridPad,
          display: "grid",
          // focus時は大タイル幅・グリッド高さをpx固定（プレビュー座標計算と一致させる）。
          // grid時は従来どおり最小高さのみ（参加者数で伸びてよい）。
          gridTemplateColumns: layout === "focus" && focus ? `${vc.focusTileW}px 1fr` : "1fr",
          gap: vc.gridGap,
          ...(layout === "focus" && focus
            ? { height: vc.tileH }
            : { minHeight: vc.tileH }),
          alignItems: "stretch",
          background: "linear-gradient(180deg, #121b24 0%, #0b1218 100%)",
        }}
      >
        {layout === "focus" && focus ? (
          <>
            <div style={{ height: "100%" }}>{renderTile(focus, { large: true })}</div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr",
                height: "100%",
                gridAutoRows: "1fr",
                gap: 16,
              }}
            >
              {others.map((p) => renderTile(p))}
            </div>
          </>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 16,
            }}
          >
            {participants.map((p) => renderTile(p))}
          </div>
        )}
      </div>

      <div
        style={{
          background: "#0c1319",
          padding: "14px 24px 18px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 18,
          borderTop: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {[
          { label: "mic", bg: "#24303b" },
          { label: "cam", bg: "#24303b" },
          { label: "share", bg: "#24303b" },
          { label: "end", bg: "#a33e3e" },
        ].map((ctl) => (
          <div
            key={ctl.label}
            style={{
              width: 60,
              height: 60,
              borderRadius: "50%",
              background: ctl.bg,
              color: "#eef7e5",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: EXTRA_EFFECT_FONT,
              fontSize: 18,
              letterSpacing: "0.04em",
            }}
          >
            {ctl.label}
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * PC画面インサートのルートオーバーレイ。
 * opacity でフェードイン/アウトする（外側でアニメーション値を渡す）。
 */
// bgOpacity = 背景（シーンを隠す全画面）の不透明度。opacity = パネル本体の不透明度。
// 背景はフェードイン無しで即カバーし「インサート中に通常画面が一瞬透ける」のを防ぐ。
export const InsertOverlay: React.FC<{
  insert: StoryInsert;
  bgOpacity: number;
  opacity: number;
  transform?: string;
  activeSpeaker?: string;
  durationInFrames?: number;
  localFrame?: number;
  whiteboardPopTargets?: WhiteboardExplainPopTargets;
  whiteboardCharacterSlot?: React.ReactNode;
  renderVideoCallFeed?: (
    participant: VideoCallParticipant,
    opts: { large: boolean; tileW: number }
  ) => React.ReactNode;
}> = ({
  insert,
  bgOpacity,
  opacity,
  transform,
  activeSpeaker,
  durationInFrames,
  localFrame,
  whiteboardPopTargets,
  whiteboardCharacterSlot,
  renderVideoCallFeed,
}) => {
  // mailer だけライトテーマ（白背景）。それ以外はダーク背景。
  const isLight = insert.kind === "mailer";
  const backdropImage = insertBackdropImage(insert);
  return (
    <AbsoluteFill
      style={{
        background: insertBackdropBg(insert, isLight ? "#e8eaf0" : INSERT_BG),
        opacity: bgOpacity,
        alignItems: "center",
        justifyContent: "center",
        transform,
        transformOrigin: "center center",
        // ごく薄いビネット（ライトは薄い暗縁・ダークはそのまま）
        boxShadow: isLight
          ? "inset 0 0 120px rgba(0,0,0,0.08)"
          : "inset 0 0 120px rgba(0,0,0,0.4)",
        pointerEvents: "none",
      }}
    >
      {backdropImage ? (
        <Img
          src={staticFile(backdropImage)}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      ) : null}
      <AbsoluteFill
        style={{ opacity, alignItems: "center", justifyContent: "center" }}
      >
        {insert.kind === "warning" && <InsertWarning insert={insert} />}
        {insert.kind === "chat" && <InsertChat insert={insert} />}
        {insert.kind === "ok" && <InsertOk insert={insert} />}
        {insert.kind === "teamchat" && <InsertTeamChat insert={insert} />}
        {insert.kind === "mailer" && <InsertMailer insert={insert} />}
        {insert.kind === "videocall" && <InsertVideoCall insert={insert} activeSpeaker={activeSpeaker} renderFeed={renderVideoCallFeed} />}
        {insert.kind === "whiteboard_explain" && (
          <WhiteboardExplainInsert
            config={insert}
            durationInFrames={durationInFrames}
            localFrame={localFrame}
            characterSlot={whiteboardCharacterSlot}
            visibleSections={insert.visibleSections}
            visibleArrows={insert.visibleArrows}
            showConclusion={insert.showConclusion}
            popTargets={whiteboardPopTargets}
          />
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const StoryOverlayLayer: React.FC<{ overlays: StoryOverlay[] }> = ({ overlays }) => {
  if (overlays.length === 0) return null;
  const colorWithOpacity = (color: string | undefined, opacity: number | undefined, fallback: string) => {
    const src = String(color || fallback).trim();
    const alpha = clamp(opacity ?? 1, 0, 1);
    const hex = src.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (!hex) return src;
    const raw = hex[1].length === 3
      ? hex[1].split("").map((c) => c + c).join("")
      : hex[1];
    const n = parseInt(raw, 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  };
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {overlays.map((overlay) => {
        const widthPct = clamp(overlay.w || 0.2, 0.04, 1) * 100;
        const leftPct = clamp(
          overlay.kind === "text" && overlay.centerX ? 0.5 : (overlay.x || 0.5),
          0,
          1
        ) * 100;
        const topPct = clamp(overlay.y || 0.5, 0, 1) * 100;
        if (overlay.kind === "text") {
          return (
            <div
              key={overlay.id}
              style={{
                position: "absolute",
                left: `${leftPct}%`,
                top: `${topPct}%`,
                width: `${widthPct}%`,
                transform: "translate(-50%, -50%)",
                opacity: clamp(overlay.opacity ?? 1, 0, 1),
                padding: "10px 18px",
                borderRadius: 16,
                border: `4px solid ${colorWithOpacity(overlay.borderColor, overlay.borderOpacity, "#ffffff")}`,
                background: colorWithOpacity(overlay.bgColor, overlay.bgOpacity, "#0f1117"),
                color: overlay.textColor || "#ffffff",
                fontSize: overlay.fontSize ?? 34,
                lineHeight: 1.35,
                fontWeight: 700,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                textAlign: "center",
                boxShadow: "0 10px 24px rgba(0,0,0,0.28)",
              }}
            >
              {overlay.text || ""}
            </div>
          );
        }
        return overlay.src ? (
          <Img
            key={overlay.id}
            src={staticFile(overlay.src)}
            style={{
              position: "absolute",
              left: `${leftPct}%`,
              top: `${topPct}%`,
              width: `${widthPct}%`,
              height: "auto",
              transform: "translate(-50%, -50%)",
              opacity: clamp(overlay.opacity ?? 1, 0, 1),
              objectFit: "contain",
              filter: "drop-shadow(0 10px 24px rgba(0,0,0,0.28))",
            }}
          />
        ) : null;
      })}
    </AbsoluteFill>
  );
};

// その時刻に吹き出しへ出す文字列（sentences があれば文単位で小出し・§4.4）。
function bubbleTextAt(turn: StoryTurn, t: number): string {
  if (hasManualBubbleLineBreak(turn.text)) return turn.text;
  if (turn.disableAutoBubbleSplit) return turn.text;
  const sentenceText = turn.sentences?.map((x) => x.text).join("") ?? "";
  if (turn.text && sentenceText && sentenceText !== turn.text.replace(/\s+/g, "")) {
    return turn.text;
  }
  if (turn.sentences && turn.sentences.length) {
    const groups = bubbleSentenceGroups(turn);
    const sentenceIdx = turn.sentences.findIndex((x) => x.start <= t && t < x.end);
    if (sentenceIdx >= 0) {
      return groups.find((group) => group.startIdx <= sentenceIdx && sentenceIdx <= group.endIdx)?.text
        ?? turn.sentences[sentenceIdx].text;
    }
    return groups[groups.length - 1]?.text ?? turn.sentences[turn.sentences.length - 1].text;
  }
  return turn.text;
}

function bubbleSentenceGroups(turn: StoryTurn): Array<{ text: string; startIdx: number; endIdx: number }> {
  if (!turn.sentences?.length) return [{ text: turn.text, startIdx: 0, endIdx: 0 }];
  const groups: Array<{ text: string; startIdx: number; endIdx: number }> = [];
  const shouldMergeWithNext = (text: string) => /[、，,]$/.test(String(text || "").trim());
  for (let i = 0; i < turn.sentences.length; i++) {
    const cur = turn.sentences[i];
    const prev = groups[groups.length - 1];
    if (prev && shouldMergeWithNext(prev.text)) {
      prev.text += cur.text;
      prev.endIdx = i;
      continue;
    }
    groups.push({ text: cur.text, startIdx: i, endIdx: i });
  }
  return groups;
}

function hasManualBubbleLineBreak(text: string | null | undefined): boolean {
  return /\r?\n/.test(String(text ?? ""));
}

function bubbleSentenceTexts(turn: StoryTurn): string[] {
  if (turn.disableAutoBubbleSplit) return [turn.text];
  if (hasManualBubbleLineBreak(turn.text)) return [turn.text];
  const sentenceText = turn.sentences?.map((x) => x.text).join("") ?? "";
  if (
    turn.sentences &&
    turn.sentences.length > 1 &&
    turn.text &&
    sentenceText === turn.text.replace(/\s+/g, "")
  ) {
    return bubbleSentenceGroups(turn).map((group) => group.text);
  }
  return [turn.text];
}

function bubbleSentenceVisibleCount(turn: StoryTurn, t: number): number {
  if (hasManualBubbleLineBreak(turn.text)) return 1;
  if (!turn.sentences?.length) return 1;
  const groups = bubbleSentenceGroups(turn);
  if (groups.length <= 1) return 1;
  const idx = turn.sentences.findIndex((x) => x.start <= t && t < x.end);
  if (idx >= 0) {
    const groupIdx = groups.findIndex((group) => group.startIdx <= idx && idx <= group.endIdx);
    return groupIdx >= 0 ? groupIdx + 1 : 1;
  }
  return groups.length;
}

function isNarrationTurn(turn: StoryTurn | null | undefined): boolean {
  return !!turn?.narrationVoice;
}

function isSubtitleTurn(turn: StoryTurn | null | undefined): boolean {
  return turn?.subtitleMode === "subtitle";
}

function subtitleProgressiveText(turn: StoryTurn, t: number): string {
  const groups = bubbleSentenceTexts(turn);
  if (groups.length <= 1) return bubbleTextAt(turn, t);
  const visible = bubbleSentenceVisibleCount(turn, t);
  return groups.slice(0, Math.max(1, visible)).join("\n");
}

// セリフ無し（間・待機用）のターンかどうか。空の吹き出しを出さないための判定。
function hasBubbleText(turn: StoryTurn | null | undefined): boolean {
  return !!turn?.text?.trim();
}

// キャラの「直前の表情」: 現時刻以前でそのキャラが最後に expression 指定付きで話した表情。
// 表情未指定のターンは真顔に戻さず直前の自分の表情を引き継ぐ
// （真顔に戻したいときは明示的に normal を指定する）。ZunMeet タイルも同じ扱い。
function lastExpressionOf(
  script: StoryTurn[],
  charId: string,
  t: number
): string | undefined {
  let last: string | undefined;
  for (const tn of script) {
    if ((tn.start ?? 0) > t) break;
    if (!isNarrationTurn(tn) && tn.speaker === charId && tn.expression) last = tn.expression;
  }
  return last;
}

// 聞き役(非話者)になったときに表示する表情キー。
// expressions.json の holdAs(表情ごとの引き継ぎ先・表情エディタで設定)を優先し、
// 未設定なら従来既定＝surprise/panic は一瞬の反応なので normal、他はそのまま保持。
function holdExpressionKey(
  exprKey: string,
  charExprs: Record<string, ExpressionCfg> | undefined
): string {
  const holdAs = charExprs?.[exprKey]?.holdAs;
  if (holdAs) return holdAs;
  return exprKey === "surprise" || exprKey === "panic" ? "normal" : exprKey;
}

function canContinueBubble(prevTurn: StoryTurn | null, activeTurn: StoryTurn): boolean {
  return !!(
    prevTurn &&
    !isNarrationTurn(prevTurn) &&
    !isNarrationTurn(activeTurn) &&
    activeTurn.continueBubble &&
    prevTurn.scene === activeTurn.scene &&
    prevTurn.speaker === activeTurn.speaker
  );
}

function continueBubbleGroupRange(script: StoryTurn[], activeIdx: number) {
  let start = activeIdx;
  let end = activeIdx;
  while (start > 0 && canContinueBubble(script[start - 1], script[start])) start -= 1;
  while (end < script.length - 1 && canContinueBubble(script[end], script[end + 1])) end += 1;
  return { start, end };
}

function resolveSpeakerFocusStart(script: StoryTurn[], activeIdx: number): number | null {
  const activeTurn = script[activeIdx];
  if (!activeTurn || isNarrationTurn(activeTurn) || !isKnownChar(activeTurn.speaker)) return null;
  let blockStart = activeIdx;
  while (blockStart > 0) {
    const prevTurn = script[blockStart - 1];
    if (!prevTurn || isNarrationTurn(prevTurn) || prevTurn.speaker !== activeTurn.speaker) break;
    blockStart -= 1;
  }
  let focusStart: number | null = null;
  for (let i = blockStart; i <= activeIdx; i += 1) {
    const turn = script[i];
    if (isNarrationTurn(turn) || turn.speaker !== activeTurn.speaker) continue;
    if (turn.emphasis === false) {
      focusStart = null;
      continue;
    }
    if (turn.emphasis === true && focusStart === null) {
      // 既に寄っている区間で emphasis:true が連続しても開始時刻を上書きしない。
      // 上書きすると寄り→引き→寄りの縮小アニメが毎ターン再生されてしまう。
      focusStart = turn.start;
    }
  }
  return focusStart;
}

function resolveCameraEffectRange(
  script: StoryTurn[],
  activeIdx: number,
  category: keyof CameraEffects,
): { effect: NonNullable<CameraEffects[keyof CameraEffects]>; start: number; end: number } | null {
  const activeTurn = script[activeIdx];
  const effect = normalizedCameraEffects(activeTurn)[category];
  if (!activeTurn || !effect || isNarrationTurn(activeTurn)) return null;
  return {
    effect,
    start: activeTurn.start,
    end: activeTurn.end,
  };
}

function bubbleBottomOffset(turn: StoryTurn, hasNextContinue: boolean): number {
  if (hasNextContinue) return 112;
  return turn.continueBubble ? 12 : 36;
}

function bubbleFontSize(text: string, stacked: boolean, baseFontSize = DEFAULT_BUBBLE_DISPLAY_SETTINGS.fontSize): number {
  return stacked ? Math.max(20, baseFontSize - 2) : baseFontSize;
}

function bubbleWrapCharLimit(turn: StoryTurn, defaultMaxChars: number | null): number | null {
  const raw = Number(turn?.bubbleMaxChars);
  if (Number.isFinite(raw) && raw > 0) return Math.round(raw);
  return defaultMaxChars;
}

function bubbleMaxWidthForTurn(
  turn: StoryTurn,
  stageWidth: number,
  stacked: boolean,
  fallbackWidth: number,
  bubbleSettings: ReturnType<typeof resolveDisplaySettings>["bubble"]
): number {
  const charLimit = bubbleWrapCharLimit(turn, bubbleSettings.maxChars);
  if (!charLimit) return fallbackWidth;
  const fontSize = bubbleFontSize(turn.text ?? "", stacked, bubbleSettings.fontSize);
  const desiredWidth = charDisplayWidth(fontSize, "あ") * charLimit + 66 + 12;
  return clamp(desiredWidth, 120, stageWidth - 40);
}

function bubbleSide(x: number, width: number): "left" | "right" {
  if (x >= width * 0.52) return "right";
  return "left";
}

// 半角(英数・半角カナ等)は全角の半分程度の見た目幅しかないため、全文字を全角扱いで
// 見積もると半角文字混じりのセリフで箱が実際の描画幅より広くなり、余白(隙間)が生まれる。
// 文字ごとに半角/全角を判定して幅を積み上げることで、実際の見た目に近い箱幅にする。
const HALF_WIDTH_CHAR_RE = /[ -ÿ｡-ￜ￨-￮]/;
const WIDE_PUNCTUATION_RE = /[?!！？、。，．,.・…「」『』（）()[\]【】]/;

function charDisplayWidth(fontSize: number, ch: string): number {
  if (ch === "…") return fontSize * 1.02;
  if (/[?!！？]/.test(ch)) return fontSize * 0.9;
  if (WIDE_PUNCTUATION_RE.test(ch)) return fontSize * 0.74;
  return fontSize * (HALF_WIDTH_CHAR_RE.test(ch) ? 0.58 : 1.03);
}

function lineDisplayWidth(fontSize: number, line: string): number {
  let w = 0;
  for (const ch of line) w += charDisplayWidth(fontSize, ch);
  return w;
}

// maxTextWidth(パディング抜きの文字表示幅)に収まるよう、1つの行を複数行へ折り返す。
// 読点等での賢い改行はせず、幅超過の直前で単純に区切る（実装/検証コストを抑えるため）。
function wrapLineToWidth(line: string, fontSize: number, maxTextWidth: number): string[] {
  const out: string[] = [];
  let current = "";
  let currentWidth = 0;
  for (const ch of line) {
    const chWidth = charDisplayWidth(fontSize, ch);
    if (current && currentWidth + chWidth > maxTextWidth) {
      out.push(current);
      current = ch;
      currentWidth = chWidth;
    } else {
      current += ch;
      currentWidth += chWidth;
    }
  }
  out.push(current);
  return out;
}

// 吹き出しの幅・折り返し済みテキストを見積もる。
// 既存の改行(\n)は本来この関数に届く前に別バブルへ分割されている想定だが、
// disableAutoBubbleSplit等ですり抜けてきた場合に備え、\n は行区切りとして尊重する。
// 各行がmaxWidth(パディング66px込み)に収まらない場合のみ、その行だけ複数行へ折り返す。
function bubbleMetrics(
  turn: StoryTurn,
  text: string,
  stacked: boolean,
  maxWidth: number,
  bubbleSettings: ReturnType<typeof resolveDisplaySettings>["bubble"]
) {
  const fontSize = bubbleFontSize(text, stacked, bubbleSettings.fontSize);
  const paragraphs = String(text || "").split("\n").map((p) => p.replace(/\s+/g, ""));
  let budget = Math.max(40, maxWidth - 66);
  const charLimit = bubbleWrapCharLimit(turn, bubbleSettings.maxChars);
  if (charLimit) {
    // turn.bubbleMaxChars は「警告」だけでなく、このターンの表示折り返し幅の目安としても使う。
    // 半角混じりでも過度に狭くなりすぎないよう、見た目幅ベースの概算へ変換して上限としてかける。
    const charBudget = Math.max(40, charDisplayWidth(fontSize, "あ") * charLimit);
    budget = Math.min(budget, charBudget);
  }
  const lines: string[] = [];
  let maxLineWidth = 0;
  for (const para of paragraphs) {
    const paraWidth = lineDisplayWidth(fontSize, para);
    if (paraWidth <= budget) {
      lines.push(para);
      maxLineWidth = Math.max(maxLineWidth, paraWidth);
      continue;
    }
    for (const wrapped of wrapLineToWidth(para, fontSize, budget)) {
      lines.push(wrapped);
      maxLineWidth = Math.max(maxLineWidth, lineDisplayWidth(fontSize, wrapped));
    }
  }
  // 幅見積もりの誤差マージン。実フォント差で右端が窮屈になる分だけ吸収する。
  const width = Math.max(120, maxLineWidth + 66 + 20);
  return { fontSize, width, text: lines.join("\n") };
}

// 仮想カメラの目標（s=ズーム / cx,cy=注視点・ステージ正規化座標）。
type Cam = { s: number; cx: number; cy: number };

// 立ち絵ボックス内での「顔の中心」のおおよその高さ比（上端=0）。
// ズームの縦注視点はシーン固定値ではなく、この比率から実際の描画位置を逆算して顔を狙う。
const FACE_RATIO = { bust: 0.3, full: 0.12 } as const;

// キャラの顔中心のy（ステージ正規化座標）。
// 立ち絵はボックス中心=アンカーに translate(-50%,-50%) で置かれ avScale 倍されるため、
// 顔y = アンカーy − (0.5 − 顔比率) × ボックス高 × avScale ÷ ステージ高(1080)。
// figure(bust/full)・キャラ別の全身キャンバス比・シーンのscaleを全て織り込む＝WYSIWYG。
function faceCyOf(
  charId: string,
  anchorOf: Record<string, string>,
  sceneDef: SceneDef,
  seg: Segment,
  tb: number
): number {
  const anchor = resolveCharXY(charId, anchorOf, sceneDef, seg, tb);
  const isFull = (sceneDef.figure ?? "bust") === "full";
  const avatar = CHARACTERS[charId]?.avatar ?? charId;
  const boxH = isFull ? fullBoxSize(avatar).h : AVATAR_BOX;
  const avScale = sceneDef.scale ?? 1.9;
  const ratio = isFull ? FACE_RATIO.full : FACE_RATIO.bust;
  return clamp(anchor.y - (0.5 - ratio) * ((boxH * avScale) / 1080), 0, 1);
}

function resolveTurnCameraFrame(
  turn: StoryTurn | undefined,
  present: string[],
  anchorOf: Record<string, string>,
  sceneDef: SceneDef
): ReturnType<typeof normalizeCameraFrame> {
  if (turn?.manualCameraFrame) {
    return normalizeCameraFrame(turn.manualCameraFrame);
  }
  if (turn?.focusSpeaker && !isNarrationTurn(turn) && present.includes(turn.speaker)) {
    const anchorName = anchorOf[turn.speaker];
    if (anchorName === "left") return sceneCameraFrame(sceneDef, "leftFocus");
    if (anchorName === "right") return sceneCameraFrame(sceneDef, "rightFocus");
  }
  return sceneCameraFrame(sceneDef, "default");
}

function targetCameraFrame(
  turn: StoryTurn | undefined,
  present: string[],
  anchorOf: Record<string, string>,
  sceneDef: SceneDef
): Cam & { width: number } {
  const frame = resolveTurnCameraFrame(turn, present, anchorOf, sceneDef);
  return { s: 1.0 / frame.width, cx: frame.cx, cy: frame.cy, width: frame.width };
}

// ─── BGMレイヤー ────────────────────────────────────────────
// BGM。story.bgm(時間ベース区間)があればそれを再生（隙間=無音）。無ければシーン連動。
const BgmLayer: React.FC<{
  bgmRegions?: BgmRegion[];
  fps: number;
}> = ({ bgmRegions, fps }) => {
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

  // 時間ベース（タイムライン編集）のみが唯一の真実。隙間=無音。
  const validSegs: BgmSegment[] = (bgmRegions || [])
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
      {validSegs.map((seg, i) => {
        const startFrame = Math.round(seg.startSec * fps);
        const durFrames = Math.max(1, Math.round((seg.endSec - seg.startSec) * fps));
        const fadeInFrames = Math.round(seg.fadeIn * fps);
        const fadeOutFrames = Math.round(seg.fadeOut * fps);
        const vol = seg.volume;

        const volumeFn = (f: number): number => {
          // フェードイン: 0..fadeInFrames で 0→1。
          const inK = fadeInFrames > 0 ? Math.min(f / fadeInFrames, 1) : 1;
          // フェードアウト: (dur-fadeOutFrames)..dur で 1→0。
          const outK =
            fadeOutFrames > 0
              ? Math.min((durFrames - f) / fadeOutFrames, 1)
              : 1;
          return vol * Math.max(0, Math.min(inK, outK));
        };

        const audioKey = `${seg.file}-${i}-${seg.startSec}-${seg.endSec}-${seg.volume}-${seg.fadeIn}-${seg.fadeOut}`;
        return (
          <Sequence key={audioKey} from={startFrame} durationInFrames={durFrames}>
            <Audio
              src={staticFile(seg.file)}
              loop
              volume={volumeFn}
            />
          </Sequence>
        );
      })}
    </>
  );
};

// ─── SEレイヤー ─────────────────────────────────────────────
// ワンショット SE をターン単位で収集してフレームに配置する。
const SeLayer: React.FC<{
  script: StoryTurn[];
  seMap?: SeMap;
  fps: number;
}> = ({ script, seMap, fps }) => {
  if (!seMap) return null;

  type SeEvent = { t: number; file: string; volume: number };
  const events: SeEvent[] = [];
  const isFiniteNumber = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);

  const tryAdd = (
    t: number,
    entry: SeMapEntry | undefined
  ) => {
    if (!isFiniteNumber(t)) return;
    if (!entry) return;
    if (!entry.enabled) return;
    if (!entry.file) return;
    events.push({ t, file: entry.file, volume: entry.volume });
  };

  for (let i = 0; i < script.length; i++) {
    const turn = script[i];

    // 表情 SE
    if (turn.expression) {
      tryAdd(turn.start, seMap.expression?.[turn.expression]);
    }

    // エフェクト SE
    if (turn.shake) {
      tryAdd(turn.start, seMap.effect?.["shake"]);
    }
    if (turn.flashback) {
      tryAdd(turn.start, seMap.effect?.["flashback"]);
    }
    if (turn.emphasis) {
      tryAdd(turn.start, seMap.effect?.["emphasis"]);
    }

    // インサート SE
    if (turn.insert) {
      tryAdd(turn.start, seMap.insert?.[turn.insert.kind]);
    }

    // 手動ワンショット SE
    for (const s of turn.se ?? []) {
      if (!s.file) continue;
      if (!isFiniteNumber(turn.start)) continue;
      const at = s.at ?? 0;
      if (!isFiniteNumber(at)) continue;
      events.push({
        t: turn.start + at,
        file: s.file,
        volume: s.volume ?? 0.7,
      });
    }
  }

  return (
    <>
      {events.map((ev, i) => {
        const startFrame = Math.round(ev.t * fps);
        // SE の最大尺。Audio は自分のファイル長で勝手に止まるが、Sequence をこの長さに
        // 区切ることで鳴り終わったら Audio がアンマウントされ、同時マウント数が増え続けない。
        // (durationInFrames=1 だと1フレームで打ち切られ声に消える / 無指定だと終端まで載りっぱなし)
        const SE_MAX_FRAMES = Math.round(6 * fps);
        return (
          <Sequence key={i} from={startFrame} durationInFrames={SE_MAX_FRAMES}>
            <Audio src={staticFile(ev.file)} volume={ev.volume} />
          </Sequence>
        );
      })}
    </>
  );
};

export const StoryVideo: React.FC<StoryVideoProps> = ({
  story,
  scenes,
  manifest,
  audio,
  expressions,
  poses,
  seMap,
  mobs,
}) => {
  MOBS = mobs && Object.keys(mobs).length > 0 ? mobs : DEFAULT_MOBS;
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;
  const audioSrc = audio ?? "story-01.wav";
  const audioQueryIndex = audioSrc.indexOf("?");
  const audioBase = audioQueryIndex >= 0 ? audioSrc.slice(0, audioQueryIndex) : audioSrc;
  const audioSuffix = audioQueryIndex >= 0 ? audioSrc.slice(audioQueryIndex) : "";
  // 口パク解析は再生用 audio とは別ソースを優先する。
  // モバイルでは同じ圧縮音声(mp3等)を「再生」と「解析」で同時に扱うと
  // プレイヤー側の音声が頭に戻る不安定さが出ることがあるため、同名wavがあればそちらを使う。
  const analysisAudio = !/\.wav$/i.test(audioBase)
    ? `${audioBase.replace(/\.[^.]+$/i, ".wav")}${audioSuffix}`
    : audioSrc;
  // ※ windowInSeconds は動的変更不可（固定値）。フックは無条件呼び出し。
  const { audioData, dataOffsetInSeconds } = useWindowedAudioData({
    src: mediaStaticSrc(analysisAudio),
    frame,
    fps,
    windowInSeconds: 1,
  });

  const script = story.script;
  const displaySettings = resolveDisplaySettings(story);
  const segments = buildSegments(script);
  const active = activeTurnAt(script, t);
  const activeIdx = script.findIndex((x) => x === active);
  const activeKey = active.id ?? `${active.start}-${active.speaker}-${active.text}`;
  // セリフ無しターン(SE/演出だけの間)は音声尺が0で active.end===active.start になりうる。
  // その場合、activeTurnAtは次ターンの開始まで居座り続ける(=画面には数秒映る)のに、
  // ここを active.end-active.start で割ると即座に progress=1 まで到達してしまい、
  // typingFlood等の演出フェードが1フレームで終わって実質見えなくなっていた。
  // インサートの実効終端(dispEnd)と同じ考え方で、次ターンの開始を実効終端として使う。
  const nextTurnForProgress = activeIdx >= 0 && activeIdx < script.length - 1 ? script[activeIdx + 1] : null;
  const activeEffectiveEnd =
    active.end > active.start ? active.end : (nextTurnForProgress?.start ?? active.end);
  const activeProgress = clamp((t - active.start) / Math.max(activeEffectiveEnd - active.start, 0.001), 0, 1);
  const seg =
    segments.find((s) => s.turns.includes(active)) ??
    segments[0];
  const segIndex = segments.findIndex((s) => s === seg);
  const prevSeg = segIndex > 0 ? segments[segIndex - 1] : null;
  const nextSeg = segments[segIndex + 1];
  const sceneDef = scenes.scenes[active.scene];
  const activeInsert = activeIdx >= 0 ? effectiveInsertAt(script, activeIdx) : null;
  const whiteboardPopTargets = activeInsert?.kind === "whiteboard_explain"
    ? (() => {
      const prevInsert = activeIdx > 0 ? effectiveInsertAt(script, activeIdx - 1) : null;
      const prevWhiteboard = prevInsert?.kind === "whiteboard_explain" ? prevInsert : null;
      const currentSections = activeInsert.visibleSections ?? ([true, true, true] as [boolean, boolean, boolean]);
      const previousSections = prevWhiteboard?.visibleSections ?? ([false, false, false] as [boolean, boolean, boolean]);
      const currentArrows = activeInsert.visibleArrows ?? ([true, true] as [boolean, boolean]);
      const previousArrows = prevWhiteboard?.visibleArrows ?? ([false, false] as [boolean, boolean]);
      const currentConclusion = activeInsert.showConclusion ?? true;
      const previousConclusion = prevWhiteboard?.showConclusion ?? false;
      return {
        sections: currentSections.map((visible, index) => visible && !previousSections[index]) as [boolean, boolean, boolean],
        arrows: currentArrows.map((visible, index) => visible && !previousArrows[index]) as [boolean, boolean],
        conclusion: currentConclusion && !previousConclusion,
      };
    })()
    : undefined;

  // ── シーン未設定/未登録のフォールバック ──
  // scene 空("") = 暗転(真っ黒)。新規ターンの既定。音声/BGM/SE は継続させる。
  // scene 非空なのに未登録(タイプミス等)は分かるよう placeholder を出す。
  if (!sceneDef) {
    return (
      <AbsoluteFill
        style={{
          background: active.scene ? "#1b1b1f" : "#000",
          color: "#fff",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 48,
          fontFamily: "sans-serif",
        }}
      >
        {audio ? <Audio src={mediaStaticSrc(audioSrc)} /> : null}
        <BgmLayer bgmRegions={story.bgm} fps={fps} />
        <SeLayer script={script} seMap={seMap} fps={fps} />
        {active.scene ? <span>未登録シーン: {active.scene}</span> : null}
      </AbsoluteFill>
    );
  }

  const avScale = sceneDef.scale ?? 1.9;
  const flashbackBoundaryStarts = new Set<number>();
  for (let i = 1; i < script.length; i++) {
    if (!!script[i - 1].flashback !== !!script[i].flashback) {
      flashbackBoundaryStarts.add(script[i].start);
    }
  }
  const isFlashbackBoundaryStart = (at: number | undefined) =>
    at !== undefined && flashbackBoundaryStarts.has(at);

  // 登場/退場スライドにかける秒数（演出共通設定 > entrance.duration で調整可能）。
  const slideDur = resolveEffectSettings(story.effectSettings).entrance.duration;

  // ── 立ち位置は区間中ずっと固定（後から登場する人ぶんも最初から確保） ──
  const roster = segmentRoster(seg);
  const anchorOfAt = (tb: number) => resolveAnchorMapAt(seg, roster, sceneDef, tb);
  const anchorOf = anchorOfAt(t);
  const entrance = entranceTimesFor(seg, isKnownChar);
  const enterDirs = enterDirsFor(seg, isKnownChar);
  const exit = exitTimesFor(seg, isKnownChar);
  const exitDir = exitDirsFor(seg, isKnownChar);
  const effectiveExitAt = (charId: string) => {
    const leaving = exit[charId];
    if (leaving === undefined) return undefined;
    return leaving;
  };
  // 表示中＝登場済み かつ（退場していない or 退場スライド中）。
  const presentNow = roster.filter(
    (c) =>
      entrance[c] <= t + 1e-6 &&
      (
        effectiveExitAt(c) === undefined ||
        ((exitDir[c] === "instant" || isFlashbackBoundaryStart(effectiveExitAt(c)))
          ? t <= effectiveExitAt(c)! + 1e-6
          : t < effectiveExitAt(c)! + slideDur)
      )
  );

  // モブ側も同じ「登場〜退場」の考え方・同じスライド演出で表示区間を判定する
  // （アバターと違い立ち位置はモブ固有のanchor固定なのでレイアウト/カメラのrosterには混ぜない）。
  // これにより、退場を明示しない限り同じモブの発言が連続してもスライドインし直さない。
  const mobEntrance = entranceTimesFor(seg, isMob);
  const mobEnterDirs = enterDirsFor(seg, isMob);
  const mobExit = exitTimesFor(seg, isMob);
  const mobExitDir = exitDirsFor(seg, isMob);
  const mobEffectiveExitAt = (mobId: string) => {
    const leaving = mobExit[mobId];
    if (leaving === undefined) return undefined;
    return leaving;
  };
  const presentMobs = Object.keys(mobEntrance).filter(
    (id) =>
      mobEntrance[id] <= t + 1e-6 &&
      (
        mobEffectiveExitAt(id) === undefined ||
        (mobExitDir[id] === "instant"
          ? t <= mobEffectiveExitAt(id)! + 1e-6
          : t < mobEffectiveExitAt(id)! + slideDur)
      )
  );

  // ── カメラ枠：構図を先に確定し、その上にカメラ演出を重ねる ──
  const TRANS = 0.8; // 遷移にかける秒数
  // 境界時刻＝ターン開始＋登場/退場。manualCameraFrame/focusSpeaker はそのターンだけで評価し、
  // 未指定の次ターンでは default / focusSpeaker 判定へ戻す。
  const times = [
    ...new Set([
      ...seg.turns.map((turn) => turn.start),
      ...roster.map((c) => entrance[c]),
      ...roster.map((c) => effectiveExitAt(c)).filter((v): v is number => typeof v === "number"),
    ]),
  ].sort((a, b) => a - b);
  let idx = 0;
  for (let i = 0; i < times.length; i++) if (times[i] <= t + 1e-6) idx = i;
  // tb 時点で「画面にいる」キャラ＝登場済み かつ 退場時刻前。
  const presentAt = (tb: number) =>
    roster.filter(
      (c) => entrance[c] <= tb + 1e-6 && (effectiveExitAt(c) === undefined || tb <= effectiveExitAt(c)! + 1e-6)
    );
  const turnAt = (tb: number) => activeTurnAt(seg.turns, tb);
  const boundaryTurn = turnAt(times[idx]);
  const upcomingTurn =
    activeIdx >= 0 &&
    activeIdx + 1 < script.length &&
    t >= active.end - 1e-6 &&
    t < script[activeIdx + 1].start &&
    script[activeIdx + 1].cameraTransition !== "cut"
      ? script[activeIdx + 1]
      : null;
  const cameraCurTime = upcomingTurn ? upcomingTurn.start : times[idx];
  const cameraPrevTime = upcomingTurn
    ? active.end
    : (() => {
      const boundaryIdx = script.indexOf(boundaryTurn);
      const prevTurn = boundaryIdx > 0 ? script[boundaryIdx - 1] : null;
      return (
        prevTurn &&
        Math.abs(boundaryTurn.start - times[idx]) < 1e-6 &&
        prevTurn.end < boundaryTurn.start &&
        boundaryTurn.cameraTransition !== "cut"
      )
        ? prevTurn.end
        : (idx > 0 ? times[idx - 1] : times[idx]);
    })();
  const cameraCurTurn = upcomingTurn ?? boundaryTurn;
  const cameraPrevTurn = upcomingTurn ? active : turnAt(cameraPrevTime);
  const Tcur = targetCameraFrame(cameraCurTurn, presentAt(cameraCurTime), anchorOfAt(cameraCurTime), sceneDef);
  const Tprev = targetCameraFrame(cameraPrevTurn, presentAt(cameraPrevTime), anchorOfAt(cameraPrevTime), sceneDef);
  const transitionMode = cameraCurTurn?.cameraTransition;
  const transitionEnd = cameraCurTime + TRANS;
  const k = cameraCurTime !== cameraPrevTime && transitionMode !== "cut"
    ? easeInOutCubic(clamp((t - cameraPrevTime) / Math.max(transitionEnd - cameraPrevTime, 0.001), 0, 1))
    : 1;
  // cam(s,cx,cy) → クランプ済みステージ変換(tx,ty,s)。
  // ★「補間してからクランプ」ではなく「クランプ済み変換同士を補間」する。
  //   こうしないと、ズーム率が低い間は pan が clamp で中央に固定され、ズームが進むにつれ
  //   pan が解放されて目標へ動く＝「一度中央に寄ってからズーム」の二段モーションになる。
  //   端点はどちらも有効枠で、その線形補間も有効枠内に収まる（黒縁は出ない）。
  const toTf = (c: Cam) => {
    const s = c.s;
    return {
      tx: clamp(width / 2 - c.cx * width * s, width * (1 - s), 0),
      ty: clamp(height / 2 - c.cy * height * s, height * (1 - s), 0),
      s,
    };
  };

  // 1. 常時スロードリフト（slow-zoom）: sceneDef.camera !== "static" のとき区間中ずっと微速プッシュイン。
  // ★ドリフトはステージ全体に毎フレーム違う scale を掛ける＝全画面再ラスタライズが毎フレーム走る。
  //   Studioプレビューだとこれが重く再生が詰まる（音切れ/リップシンク停止の主因）。
  //   最終renderでだけ効かせ、プレビューでは無効にして軽くする。
  const camCfg = scenes.camera || {};

  const sceneStartTransformFor = (targetSeg: Segment, targetSceneDef: SceneDef) => {
    const targetTurn = targetSeg.turns[0];
    const targetRoster = segmentRoster(targetSeg);
    const targetAnchorOf = resolveAnchorMapAt(targetSeg, targetRoster, targetSceneDef, targetSeg.start);
    const targetEntrance = entranceTimesFor(targetSeg, isKnownChar);
    const targetPresent = targetRoster.filter((c) => targetEntrance[c] <= targetSeg.start + 1e-6);
    const baseCam = targetCameraFrame(targetTurn, targetPresent, targetAnchorOf, targetSceneDef);
    let localTf = toTf(baseCam);
    let localRotateDeg = 0;

    if (targetTurn.emphasis === true && !isNarrationTurn(targetTurn) && isKnownChar(targetTurn.speaker)) {
      const speakerAnchor = resolveCharXY(targetTurn.speaker, targetAnchorOf, targetSceneDef, targetSeg, targetSeg.start);
      const autoFocusCy = clamp(
        faceCyOf(targetTurn.speaker, targetAnchorOf, targetSceneDef, targetSeg, targetSeg.start) + (targetSceneDef.focusDy ?? 0.12),
        0,
        1
      );
      const manualFocus =
        explicitZoomTarget(targetTurn) ??
        zoomTargetValueAt(targetSeg, targetSeg.start) ??
        resolveZoomTargetAt(targetSeg, targetSeg.start, () => ({ x: speakerAnchor.x, y: autoFocusCy }));
      const emphasisCfg = resolveEffectSettings(
        mergeEffectSettings(story.effectSettings, targetTurn.effectSettings)
      ).emphasis;
      const emphasisScaleOverridden =
        story.effectSettings?.emphasis?.scale != null || targetTurn.effectSettings?.emphasis?.scale != null;
      const emphasisScale = emphasisScaleOverridden ? emphasisCfg.scale : (targetSceneDef.focusZoom ?? emphasisCfg.scale);
      localTf = toTf({
        s: localTf.s + emphasisScale,
        cx: manualFocus?.x ?? speakerAnchor.x,
        cy: manualFocus?.y ?? autoFocusCy,
      });
    }

    const targetEffects = normalizedCameraEffects(targetTurn);
    if (targetEffects.zoom) {
      const defaultFocus = (() => {
        if (!isNarrationTurn(targetTurn) && isKnownChar(targetTurn.speaker)) {
          const speakerAnchor = resolveCharXY(targetTurn.speaker, targetAnchorOf, targetSceneDef, targetSeg, targetSeg.start);
          return {
            x: speakerAnchor.x,
            y: clamp(faceCyOf(targetTurn.speaker, targetAnchorOf, targetSceneDef, targetSeg, targetSeg.start) + (targetSceneDef.focusDy ?? 0.12), 0, 1),
          };
        }
        return { x: baseCam.cx, y: baseCam.cy };
      })();
      const zoomFocus = explicitZoomTarget(targetTurn) ?? defaultFocus;
      const zoomAmount = cameraEffectTurnValue(targetTurn, camCfg, "zoom", "amount");
      localTf = toTf({
        s: targetEffects.zoom === "in" ? localTf.s + zoomAmount : Math.max(1, localTf.s - zoomAmount),
        cx: zoomFocus.x,
        cy: zoomFocus.y,
      });
    }
    if (targetEffects.pan) {
      const dir = targetEffects.pan === "left" ? -1 : 1;
      const panRoom = Math.max(0, -width * (1 - localTf.s));
      const panAmount = Math.min(panRoom, width * cameraEffectTurnValue(targetTurn, camCfg, "pan", "amount")) * dir;
      localTf = {
        tx: clamp(localTf.tx + panAmount, width * (1 - localTf.s), 0),
        ty: localTf.ty,
        s: localTf.s,
      };
    }
    if (targetEffects.tilt) {
      const dir = targetEffects.tilt === "left" ? -1 : 1;
      localRotateDeg = cameraEffectTurnValue(targetTurn, camCfg, "tilt", "angle") * dir;
      localTf = {
        tx: localTf.tx,
        ty: localTf.ty,
        s: Math.max(localTf.s, 1.04),
      };
    }

    return `translate(${localTf.tx}px, ${localTf.ty}px) scale(${localTf.s}) rotate(${localRotateDeg}deg)`;
  };

  let driftS = 1.0;
  if (sceneDef.camera !== "static" && getRemotionEnvironment().isRendering) {
    const segDur = Math.max(seg.end - seg.start, 0.001);
    const p = clamp((t - seg.start) / segDur, 0, 1);
    driftS = 1 + (camCfg.slowZoomDrift ?? 0.05) * p;
  }

  // 人数変化の寄り↔引き：キーフレーム(Tprev/Tcur)それぞれをクランプ済み変換にしてから補間。
  const tfPrev = toTf({ ...Tprev, s: Tprev.s * driftS });
  const tfCur = toTf({ ...Tcur, s: Tcur.s * driftS });
  let tf = {
    tx: lerp(tfPrev.tx, tfCur.tx, k),
    ty: lerp(tfPrev.ty, tfCur.ty, k),
    s: lerp(tfPrev.s, tfCur.s, k),
  };

  // 2. 話者プッシュイン（emphasis を起点に、話者が変わるまで維持）。
  // focus のクランプ済み変換へ「変換ごと」補間する＝まっすぐ寄る。
  const focusStart = activeIdx >= 0 ? resolveSpeakerFocusStart(script, activeIdx) : null;
  let focusBubbleK = 0;
  const withinActiveTurnSpeech = t < active.end - 1e-6;
  const focusTfFor = (turn: StoryTurn, baseTf: typeof tf) => {
    const speakerAnchor = resolveCharXY(turn.speaker, anchorOf, sceneDef, seg, t);
    const autoFocusCx = speakerAnchor.x;
    const autoFocusCy = clamp(faceCyOf(turn.speaker, anchorOf, sceneDef, seg, t) + (sceneDef.focusDy ?? 0.12), 0, 1);
    const manualFocus =
      explicitZoomTarget(turn) ??
      zoomTargetValueAt(seg, t) ??
      resolveZoomTargetAt(seg, t, () => ({ x: autoFocusCx, y: autoFocusCy }));
    const emphasisCfg = resolveEffectSettings(
      mergeEffectSettings(story.effectSettings, turn.effectSettings)
    ).emphasis;
    const emphasisScaleOverridden =
      story.effectSettings?.emphasis?.scale != null || turn.effectSettings?.emphasis?.scale != null;
    const emphasisScale = emphasisScaleOverridden ? emphasisCfg.scale : (sceneDef.focusZoom ?? emphasisCfg.scale);
    return {
      tf: toTf({
        s: baseTf.s + emphasisScale,
        cx: manualFocus?.x ?? autoFocusCx,
        cy: manualFocus?.y ?? autoFocusCy,
      }),
      cfg: emphasisCfg,
    };
  };
  if (focusStart != null) {
    const { tf: focusTf, cfg: emphasisCfg } = focusTfFor(active, tf);
    // emphasis を立てた時点からemphasis.duration秒でイーズインし、その後は話者交代まで維持する。
    let focusK = easeInOutCubic(clamp((t - focusStart) / Math.max(emphasisCfg.duration, 0.05), 0, 1));
    if (upcomingTurn && upcomingTurn.speaker !== active.speaker) {
      const releaseK = easeInOutCubic(clamp((t - active.end) / Math.max(upcomingTurn.start + TRANS - active.end, 0.001), 0, 1));
      focusK *= 1 - releaseK;
    }
    focusBubbleK = focusK;
    tf = {
      tx: lerp(tf.tx, focusTf.tx, focusK),
      ty: lerp(tf.ty, focusTf.ty, focusK),
      s: lerp(tf.s, focusTf.s, focusK),
    };
  } else if (activeIdx > 0 && transitionMode !== "cut" && script[activeIdx - 1]?.scene === active.scene) {
    const prevTurn = script[activeIdx - 1];
    const prevFocusStart = resolveSpeakerFocusStart(script, activeIdx - 1);
    if (prevFocusStart != null && prevTurn.speaker !== active.speaker) {
      const releaseK = easeInOutCubic(clamp((t - prevTurn.end) / Math.max(active.start + TRANS - prevTurn.end, 0.001), 0, 1));
      if (releaseK < 1) {
        const { tf: prevFocusTf } = focusTfFor(prevTurn, tf);
        const keepK = 1 - releaseK;
        focusBubbleK = keepK;
        tf = {
          tx: lerp(tf.tx, prevFocusTf.tx, keepK),
          ty: lerp(tf.ty, prevFocusTf.ty, keepK),
          s: lerp(tf.s, prevFocusTf.s, keepK),
        };
      }
    }
  }

  // 3. 単発カメラ効果（その行だけ付ける軽いズーム/パン/傾き）。
  let stageRotateDeg = 0;
  const zoomEffectRange = activeIdx >= 0 ? resolveCameraEffectRange(script, activeIdx, "zoom") : null;
  const prevZoomEffectRange = activeIdx > 0 ? resolveCameraEffectRange(script, activeIdx - 1, "zoom") : null;
  if (
    !zoomEffectRange &&
    prevZoomEffectRange &&
    activeIdx > 0 &&
    transitionMode !== "cut" &&
    script[activeIdx - 1]?.scene === active.scene
  ) {
    const prevTurn = script[activeIdx - 1];
    const releaseK = easeInOutCubic(clamp((t - prevTurn.end) / Math.max(active.start + TRANS - prevTurn.end, 0.001), 0, 1));
    if (releaseK < 1) {
      const prevBase = targetCameraFrame(prevTurn, presentAt(active.start), anchorOfAt(active.start), sceneDef);
      const prevBaseTf = toTf({ ...prevBase, s: prevBase.s * driftS });
      const defaultFocus = (() => {
        if (!isNarrationTurn(prevTurn) && isKnownChar(prevTurn.speaker)) {
          const speakerAnchor = resolveCharXY(prevTurn.speaker, anchorOf, sceneDef, seg, t);
          return {
            x: speakerAnchor.x,
            y: clamp(faceCyOf(prevTurn.speaker, anchorOf, sceneDef, seg, t) + (sceneDef.focusDy ?? 0.12), 0, 1),
          };
        }
        return { x: prevBase.cx, y: prevBase.cy };
      })();
      const zoomFocus = explicitZoomTarget(prevTurn) ?? defaultFocus;
      const zoomAmount = cameraEffectTurnValue(prevTurn, camCfg, "zoom", "amount");
      const targetScale = prevZoomEffectRange.effect === "in"
        ? prevBaseTf.s + zoomAmount
        : Math.max(1, prevBaseTf.s - zoomAmount);
      const prevEffectTf = toTf({
        s: targetScale,
        cx: zoomFocus.x,
        cy: zoomFocus.y,
      });
      tf = {
        tx: lerp(prevEffectTf.tx, tf.tx, releaseK),
        ty: lerp(prevEffectTf.ty, tf.ty, releaseK),
        s: lerp(prevEffectTf.s, tf.s, releaseK),
      };
    }
  }
  if (zoomEffectRange && activeIdx >= 0) {
    const effect = normalizedCameraEffects(active).zoom;
    const effectDur = Math.max(cameraEffectTurnValue(active, camCfg, "zoom", "duration"), 0.001);
    const isContinuingZoom =
      !!effect &&
      !!prevZoomEffectRange &&
      prevZoomEffectRange.effect === effect &&
      script[activeIdx - 1]?.scene === active.scene;
    const effectK = isContinuingZoom
      ? 1
      : easeInOutCubic(clamp((t - zoomEffectRange.start) / effectDur, 0, 1));
    const defaultFocus = (() => {
      if (!isNarrationTurn(active) && isKnownChar(active.speaker)) {
        const speakerAnchor = resolveCharXY(active.speaker, anchorOf, sceneDef, seg, t);
        return {
          x: speakerAnchor.x,
          y: clamp(faceCyOf(active.speaker, anchorOf, sceneDef, seg, t) + (sceneDef.focusDy ?? 0.12), 0, 1),
        };
      }
      return { x: Tcur.cx, y: Tcur.cy };
    })();
    const zoomFocus = explicitZoomTarget(active) ?? defaultFocus;
    const zoomAmount = cameraEffectTurnValue(active, camCfg, "zoom", "amount");
    const targetScale = effect === "in"
      ? tf.s + zoomAmount
      : Math.max(1, tf.s - zoomAmount);
    const effectCam = toTf({
      s: targetScale,
      cx: zoomFocus.x,
      cy: zoomFocus.y,
    });
    tf = {
      tx: lerp(tf.tx, effectCam.tx, effectK),
      ty: lerp(tf.ty, effectCam.ty, effectK),
      s: lerp(tf.s, effectCam.s, effectK),
    };
  }
  const panEffectRange = activeIdx >= 0 ? resolveCameraEffectRange(script, activeIdx, "pan") : null;
  const prevPanEffectRange = activeIdx > 0 ? resolveCameraEffectRange(script, activeIdx - 1, "pan") : null;
  if (
    !panEffectRange &&
    prevPanEffectRange &&
    activeIdx > 0 &&
    transitionMode !== "cut" &&
    script[activeIdx - 1]?.scene === active.scene
  ) {
    const prevTurn = script[activeIdx - 1];
    const releaseK = easeInOutCubic(clamp((t - prevTurn.end) / Math.max(active.start + TRANS - prevTurn.end, 0.001), 0, 1));
    if (releaseK < 1) {
      const baseTf = tf;
      const dir = prevPanEffectRange.effect === "left" ? -1 : 1;
      const panRoom = Math.max(0, -width * (1 - baseTf.s));
      const panAmount = Math.min(panRoom, width * cameraEffectTurnValue(prevTurn, camCfg, "pan", "amount")) * dir;
      const prevTx = clamp(baseTf.tx + panAmount, width * (1 - baseTf.s), 0);
      tf = {
        tx: lerp(prevTx, baseTf.tx, releaseK),
        ty: baseTf.ty,
        s: baseTf.s,
      };
    }
  }
  if (panEffectRange) {
    const effectDur = Math.max(cameraEffectTurnValue(active, camCfg, "pan", "duration"), 0.001);
    const effectK = easeInOutCubic(clamp((t - panEffectRange.start) / effectDur, 0, 1));
    const baseTf = tf;
    const dir = panEffectRange.effect === "left" ? -1 : 1;
    const panRoom = Math.max(0, -width * (1 - baseTf.s));
    const panAmount = Math.min(panRoom, width * cameraEffectTurnValue(active, camCfg, "pan", "amount")) * dir;
    const targetTx = clamp(baseTf.tx + panAmount, width * (1 - baseTf.s), 0);
    tf = {
      tx: lerp(baseTf.tx, targetTx, effectK),
      ty: baseTf.ty,
      s: baseTf.s,
    };
  }
  const tiltEffectRange = activeIdx >= 0 ? resolveCameraEffectRange(script, activeIdx, "tilt") : null;
  const prevTiltEffectRange = activeIdx > 0 ? resolveCameraEffectRange(script, activeIdx - 1, "tilt") : null;
  if (
    !tiltEffectRange &&
    prevTiltEffectRange &&
    activeIdx > 0 &&
    transitionMode !== "cut" &&
    script[activeIdx - 1]?.scene === active.scene
  ) {
    const prevTurn = script[activeIdx - 1];
    const releaseK = easeInOutCubic(clamp((t - prevTurn.end) / Math.max(active.start + TRANS - prevTurn.end, 0.001), 0, 1));
    if (releaseK < 1) {
      const dir = prevTiltEffectRange.effect === "left" ? -1 : 1;
      stageRotateDeg = cameraEffectTurnValue(prevTurn, camCfg, "tilt", "angle") * dir * (1 - releaseK);
      tf = {
        tx: tf.tx,
        ty: tf.ty,
        s: lerp(Math.max(tf.s, 1.04), tf.s, releaseK),
      };
    }
  }
  if (tiltEffectRange) {
    const effectDur = Math.max(cameraEffectTurnValue(active, camCfg, "tilt", "duration"), 0.001);
    const effectK = easeInOutCubic(clamp((t - tiltEffectRange.start) / effectDur, 0, 1));
    const baseTf = tf;
    const dir = tiltEffectRange.effect === "left" ? -1 : 1;
    stageRotateDeg = cameraEffectTurnValue(active, camCfg, "tilt", "angle") * dir * effectK;
    const targetS = Math.max(baseTf.s, 1.04);
    tf = {
      tx: baseTf.tx,
      ty: baseTf.ty,
      s: lerp(baseTf.s, targetS, effectK),
    };
  }

  // 4. カメラシェイク（shake===true のターン中、減衰振動オフセットを translate に加算）。
  // s=1.0 など余裕ゼロのシーンでも振幅が出るよう、shake 中はスケールを最低 1.02 に嵩上げ。
  let stageS = tf.s;
  let stageTx = tf.tx;
  let stageTy = tf.ty;
  let shakeX = 0;
  let shakeY = 0;
  if (withinActiveTurnSpeech && (active.shake || normalizedCameraEffects(active).shake)) {
    const shakeS = Math.max(tf.s, 1.02);
    const turnDur = Math.max(active.end - active.start, 0.001);
    const elapsed = t - active.start;
    const decayRaw = 1 - clamp(elapsed / turnDur, 0, 1);
    const availX = (shakeS - 1) * width * 0.5;
    const availY = (shakeS - 1) * height * 0.5;
    const maxAmp = Math.min(7, availX, availY);
    const amp = maxAmp * decayRaw;
    shakeX = amp * Math.sin(t * 2 * Math.PI * 16);
    shakeY = amp * Math.sin(t * 2 * Math.PI * 16 * 1.3 + 1);
    // 嵩上げ後の s に合わせて translate を再クランプ（中心維持・範囲が広がるだけ）。
    stageS = shakeS;
    stageTx = clamp(tf.tx, width * (1 - shakeS), 0);
    stageTy = clamp(tf.ty, height * (1 - shakeS), 0);
  }

  // 5. ズームパンチ（zoomPunch===true のターン開始直後、一瞬だけステージ全体を追加でパンチイン）。
  // 以前はステージと連動しない透明レイヤーの拡大＋薄い縁取りのみで体感できなかったため、
  // 実際にステージ(背景・キャラ)を一瞬拡大する演出に変更した。
  if (withinActiveTurnSpeech && active.zoomPunch) {
    const zoomPunchCfg = resolveEffectSettings(
      mergeEffectSettings(story.effectSettings, active.effectSettings)
    ).zoomPunch;
    // duration は「秒」指定。ターン開始からの経過秒数(t - active.start)で比較する
    // （activeProgressのようなターン内0-1割合のまま割ると、ターンの長さで体感速度が変わってしまう）。
    const local = clamp((t - active.start) / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
    const punch = Math.sin(local * Math.PI);
    const punchScale = 1 + Math.max(0, zoomPunchCfg.scale - 1) * punch;
    stageS = stageS * punchScale;
  }

  // shakeX/Y を加算した最終値をスケール clamp 内に収める（黒縁が出ないよう）。
  const sfx = clamp(stageTx + shakeX, width * (1 - stageS), 0);
  const sfy = clamp(stageTy + shakeY, height * (1 - stageS), 0);

  const stageTransform = `translate(${sfx}px, ${sfy}px) scale(${stageS}) rotate(${stageRotateDeg}deg)`;
  const insertShakeTransform = active.shake || normalizedCameraEffects(active).shake
    ? `translate(${shakeX}px, ${shakeY}px) scale(1.02)`
    : undefined;

  // ── 場面切り替え演出 ──────────────────────────────────────
  // 片側の秒数（総遷移 = 2×FADE）。種類による差は付けず統一。
  const TRANSITION_FADE = 0.9;
  const FADE_BY_TRANSITION: Record<string, number> = {
    "fade-black": TRANSITION_FADE,
    "fade-white": TRANSITION_FADE,
    "wipe-left": TRANSITION_FADE,
    "wipe-right": TRANSITION_FADE,
    "slide-left": TRANSITION_FADE,
    "slide-right": TRANSITION_FADE,
  };
  const DEFAULT_FADE = TRANSITION_FADE;
  const entryFade = FADE_BY_TRANSITION[seg.transition] ?? DEFAULT_FADE;
  const exitFade = nextSeg
    ? FADE_BY_TRANSITION[nextSeg.transition] ?? DEFAULT_FADE
    : DEFAULT_FADE;
  const entryProgress = easeInOutCubic(
    seg.start > 1e-6 ? clamp((t - seg.start) / entryFade, 0, 1) : 1
  );
  const exitProgress = nextSeg
    ? easeInOutCubic(clamp((t - (nextSeg.start - exitFade)) / exitFade, 0, 1))
    : 0;
  const inEntryWindow = seg.start > 1e-6 && t < seg.start + entryFade;
  const inExitWindow = !!nextSeg && t > nextSeg.start - exitFade;
  const entrySceneDef = prevSeg ? scenes.scenes[prevSeg.scene] : null;
  const nextSceneDef = nextSeg ? scenes.scenes[nextSeg.scene] : null;

  let currentStageClipPath: string | undefined;
  let currentStageShiftX = 0;
  let transitionCoverColor: string | null = null;
  let transitionCoverOpacity = 0;
  let incomingPlate: {
    sceneDef: SceneDef;
    clipPath?: string;
    shiftX?: number;
    stageTransform?: string;
    key: string;
  } | null = null;

  if (inEntryWindow) {
    switch (seg.transition) {
      case "fade-black":
        transitionCoverColor = "#000";
        transitionCoverOpacity = Math.max(transitionCoverOpacity, 1 - entryProgress);
        break;
      case "fade-white":
        transitionCoverColor = "#fff";
        transitionCoverOpacity = Math.max(transitionCoverOpacity, 1 - entryProgress);
        break;
    }
  } else if (inExitWindow && nextSeg && nextSceneDef) {
    switch (nextSeg.transition) {
      case "fade-black":
        transitionCoverColor = "#000";
        transitionCoverOpacity = Math.max(transitionCoverOpacity, exitProgress);
        break;
      case "fade-white":
        transitionCoverColor = "#fff";
        transitionCoverOpacity = Math.max(transitionCoverOpacity, exitProgress);
        break;
      case "wipe-left":
        currentStageClipPath = `inset(0 0 0 ${exitProgress * 100}%)`;
        incomingPlate = {
          sceneDef: nextSceneDef,
          clipPath: `inset(0 ${(1 - exitProgress) * 100}% 0 0)`,
          stageTransform: sceneStartTransformFor(nextSeg, nextSceneDef),
          key: `next-${nextSeg.start}`,
        };
        break;
      case "wipe-right":
        currentStageClipPath = `inset(0 ${exitProgress * 100}% 0 0)`;
        incomingPlate = {
          sceneDef: nextSceneDef,
          clipPath: `inset(0 0 0 ${(1 - exitProgress) * 100}%)`,
          stageTransform: sceneStartTransformFor(nextSeg, nextSceneDef),
          key: `next-${nextSeg.start}`,
        };
        break;
      case "slide-left":
        currentStageShiftX = width * exitProgress;
        incomingPlate = {
          sceneDef: nextSceneDef,
          shiftX: -width * (1 - exitProgress),
          stageTransform: sceneStartTransformFor(nextSeg, nextSceneDef),
          key: `next-slide-${nextSeg.start}`,
        };
        break;
      case "slide-right":
        currentStageShiftX = -width * exitProgress;
        incomingPlate = {
          sceneDef: nextSceneDef,
          shiftX: width * (1 - exitProgress),
          stageTransform: sceneStartTransformFor(nextSeg, nextSceneDef),
          key: `next-slide-${nextSeg.start}`,
        };
        break;
    }
  }

  // ── 話者の音量（実音声の波形RMS）→ リップシンク。 ──
  // useAudioData は音声全体（166秒≒16MBのPCM）をブラウザで丸ごと展開するため、
  // Studioプレビューで読込中に null を返し続けリップシンクが止まる/重い。
  // useWindowedAudioData は現フレーム周辺の窓だけ読むので軽く安定する。
  let speakerAmp = 0;
  if (audio && audioData) {
    const wave = audioData.channelWaveforms[0];
    const sr = audioData.sampleRate;
    // 窓の先頭が dataOffsetInSeconds。現在時刻 t をその窓内のサンプル位置へ変換。
    const center = Math.floor((t - dataOffsetInSeconds) * sr);
    const win = Math.floor(sr / fps); // 1フレーム分の窓
    let sum = 0;
    let n = 0;
    for (let i = center - win / 2; i < center + win / 2; i++) {
      if (i >= 0 && i < wave.length) {
        sum += wave[i] * wave[i];
        n++;
      }
    }
    const rms = Math.sqrt(sum / Math.max(1, n));
    speakerAmp = Math.min(1, rms * LIPSYNC_GAIN);
  }

  const renderAvatar = (charId: string) => {
    const cdef = CHARACTERS[charId];
    if (!cdef) return null;
    const anchor = resolveCharXY(charId, anchorOf, sceneDef, seg, t, { x: 0.5, y: 1.02 });
    const isSpeaker = !isNarrationTurn(active) && charId === active.speaker;
    const lipsyncEnabled = isSpeaker && !active.noLipSync;
    // 向き: 台本の face 指定 > x座標からの自動（中央を向く）。
    // 立ち絵素材は「画面左向き」が素なので、右を向かせるときだけ反転する。
    // 画面左半分(x<0.5)のキャラは右＝中央向き、右半分は左＝中央向き。x を動かせば向きも自動追従。
    const explicitFace = normalizeFaceDirectionForChar(charId, active.face?.[charId]);
    const resolvedFace = explicitFace ?? heldFaceOf(script, charId, t);
    const angleSrc = angleFaceSrc(charId, sceneDef, resolvedFace);
    const angleScale = angleFaceScale(charId, resolvedFace);
    const flip = angleSrc ? false : facingFlipFor(resolvedFace, anchor.x);
    // 表情未指定は直前の自分の表情を引き継ぐ（lastExpressionOf）。それも無ければ normal。
    // 未知の表情キーは "normal" にフォールバック（組み込み5種のモーションを維持）。
    const resolvedExpr =
      active.expression ?? lastExpressionOf(script, charId, t) ?? "normal";
    const emotion = EXPRESSION_TO_EMOTION[resolvedExpr] ?? EXPRESSION_TO_EMOTION["normal"];

    // expressions.json が渡されていれば該当表情の ExpressionCfg を解決する。
    // 未知キーは "normal" にフォールバック（クラッシュ防止）。
    const exprKey = resolvedExpr;
    const charKey = cdef.avatar; // "zundamon" / "metan"
    const charExprs = expressions?.[charKey];
    const baseExpressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
    const poseCfg = active.pose ? poses?.[charKey]?.[active.pose] ?? null : null;
    const expressionCfg =
      baseExpressionCfg && active.pose
        ? { ...baseExpressionCfg, pose: active.pose }
        : baseExpressionCfg;

    // 非話者(聞き役)の表情。story.idleFace==="hold" のとき「現時刻以前で自分が最後に
    // 話したターンの表情」を保持する。引き継ぎ先は表情ごとの holdAs 設定
    // (無ければ surprise/panic→normal、他はそのまま)で決まる。
    // 既定(normal/未指定)は常に normal(真顔)。
    let idleExprKey = "normal";
    if (story.idleFace === "hold") {
      idleExprKey = lastExpressionOf(script, charId, t) ?? "normal";
      idleExprKey = holdExpressionKey(idleExprKey, charExprs);
    }
    const idleCfg = charExprs?.[idleExprKey] ?? charExprs?.["normal"] ?? null;
    const idleEmotion = EXPRESSION_TO_EMOTION[idleExprKey] ?? EXPRESSION_TO_EMOTION["normal"];

    // 全身 or バスト の判定。figure="full" のとき全身パーツを使う。
    const isFull = (sceneDef.figure ?? "bust") === "full";
    const avatarDir = isFull ? `${cdef.avatar}/full` : cdef.avatar;
    const manifestKey = isFull ? `${cdef.avatar}_full` : cdef.avatar;
    const avatarManifest = manifest?.[manifestKey];
    const box = isFull ? fullBoxSize(cdef.avatar) : { w: AVATAR_BOX, h: AVATAR_BOX };

    // 途中で登場するキャラ（区間の頭からいる人ではない）は、自分の側からスライドイン。
    // 区間の頭からいる場合でも、enterDir が明示指定されていればその方向からスライドインさせる
    // （指定が無ければ従来通り最初から画面にいる扱い）。
    const entered = entrance[charId] ?? seg.start;
    const isInitial = entered <= seg.start + 1e-6;
    const enterDir = enterDirs[charId];
    const entersInstantly = enterDir === "instant" || isFlashbackBoundaryStart(entered);
    let slideOffsetPx = 0;
    if ((!isInitial || enterDir !== undefined) && !entersInstantly) {
      const sp = clamp((t - entered) / slideDur, 0, 1); // slideDur秒で着地
      const e = easeOutCubic(sp);
      // 登場方向：明示指定があればそちら、無ければ自分の居る側（近い画面端）から。
      const fromXNorm =
        enterDir === "right" ? 1.35 : enterDir === "left" ? -0.35 : anchor.x < 0.5 ? -0.35 : 1.35;
      slideOffsetPx = (1 - e) * (fromXNorm - anchor.x) * width;
    }
    // 退場：exit 時刻になったら自分の側へスライドアウト（slideDur秒で画面外へ）。
    const leaving = effectiveExitAt(charId);
    const exitsInstantly = exitDir[charId] === "instant" || isFlashbackBoundaryStart(leaving);
    if (leaving !== undefined && t >= leaving && !exitsInstantly) {
      const sp = clamp((t - leaving) / slideDur, 0, 1);
      const e = easeInOutCubic(sp);
      // 退場方向：明示指定があればそちら、無ければ自分の居る側（近い端）へ。
      const dir = exitDir[charId];
      const toXNorm =
        dir === "right" ? 1.35 : dir === "left" ? -0.35 : anchor.x < 0.5 ? -0.35 : 1.35;
      slideOffsetPx = e * (toXNorm - anchor.x) * width;
    }

    return (
      <div
        key={charId}
        style={{
          position: "absolute",
          left: anchor.x * width + slideOffsetPx,
          top: anchor.y * height,
          // anchor.y は立ち絵の「中央(体の中心)」位置（足元ではない）。
          // 全身は背が高く足元基準だと初期表示で画面外に出て配置しづらいため中央基準。
          transform: "translate(-50%, -50%)",
        }}
      >
        <div style={{ transform: `scale(${avScale})`, transformOrigin: "center" }}>
          <Avatar
            dir={avatarDir}
            manifest={avatarManifest}
            singleSrc={angleSrc}
            singleScale={angleScale}
            fallbackGender={cdef.gender}
            active={isSpeaker}
            activatedAtFrame={Math.round(active.start * fps)}
            amplitude={lipsyncEnabled ? speakerAmp : 0}
            emotion={isSpeaker ? emotion : idleEmotion}
            emotionAtFrame={Math.round(active.start * fps)}
            expressive={!!cdef.expressive}
            flip={flip}
            popScale={false}
            boxWidth={box.w}
            boxHeight={box.h}
            expressionCfg={isSpeaker ? expressionCfg : idleCfg}
            poseName={isSpeaker ? active.pose : undefined}
            poseArmStem={isSpeaker ? poseCfg?.arm ?? null : null}
            poseSpeed={isSpeaker ? poseCfg?.speed ?? null : null}
            poseStrength={isSpeaker ? poseCfg?.strength ?? null : null}
          />
        </div>
      </div>
    );
  };

  // whiteboard_explain インサートの立ち絵。通常ターンと同じ表情(expressions.json)・
  // ポーズ(poses.json)を使い、独自の画像パス方式(public/characters/...)は使わない。
  const renderWhiteboardCharacterSlot = (insert: Extract<StoryInsert, { kind: "whiteboard_explain" }>) => {
    const charKey = insert.character?.name && CHARACTERS[insert.character.name] ? insert.character.name : "metan";
    const cdef = CHARACTERS[charKey];
    if (!cdef) return null;
    const exprKey = insert.character?.expression || "normal";
    const emotion = EXPRESSION_TO_EMOTION[exprKey] ?? EXPRESSION_TO_EMOTION["normal"];
    const charExprs = expressions?.[charKey];
    const expressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
    const poseNameRaw = insert.character?.pose || undefined;
    const poseCfg = poseNameRaw ? poses?.[charKey]?.[poseNameRaw] ?? null : null;
    const wbLayout = getWhiteboardExplainLayout(width, height, insert.layout === "compact" ? "compact" : "default");
    const scale = wbLayout.character.width / AVATAR_BOX;
    // このインサートのキャラが実際のターン話者と一致する時だけ、通常のセリフ同様にリップシンクする。
    const isSpeaker = charKey === active.speaker;
    const lipsyncEnabled = isSpeaker && !active.noLipSync;
    return (
      <div
        style={{
          position: "absolute",
          left: "50%",
          bottom: 0,
          transform: `translateX(-50%) scale(${scale})`,
          transformOrigin: "bottom center",
        }}
      >
        <Avatar
          dir={cdef.avatar}
          manifest={manifest?.[cdef.avatar]}
          fallbackGender={cdef.gender}
          active={isSpeaker}
          activatedAtFrame={Math.round(active.start * fps)}
          amplitude={lipsyncEnabled ? speakerAmp : 0}
          emotion={emotion}
          emotionAtFrame={Math.round(active.start * fps)}
          expressive={!!cdef.expressive}
          flip={false}
          popScale={false}
          expressionCfg={expressionCfg}
          poseName={poseNameRaw as ExpressionCfg["pose"]}
          poseArmStem={poseCfg?.arm ?? null}
          poseSpeed={poseCfg?.speed ?? null}
          poseStrength={poseCfg?.strength ?? null}
        />
      </div>
    );
  };

  // モブ（1枚絵）描画：「登場〜退場」の区間だけ立たせる（登場でフェードイン、退場でフェードアウト）。
  // 同じモブの発言が連続しても、明示的に退場していない限りフェードインし直さない。
  // 発話中（このモブが現在の話者の間）だけ speakerAmp（実音声RMS）に応じて口パクする。
  // 画像が無ければ onError で非表示にし、render を壊さない（素材未配置でも安全）。
  const renderMob = (mobId: string) => {
    const m = MOBS[mobId];
    if (!m) return null;
    // 配置はturn.manualPos(手動配置)優先 → シーンデータ(scene.mobs[mob]) → MobDef既定 → シーン既定。
    const place = sceneDef.mobs?.[mobId];
    if (place?.hidden) return null; // 立ち絵を非表示（チャット/音声のみ登場）
    const a = resolveMobXY(mobId, seg, t, sceneDef);
    const sc = place?.scale ?? m.scale ?? 1;
    const h = (sceneDef.mobHeight ?? 760) * sc;
    const isSpeakingNow = !isNarrationTurn(active) && active.speaker === mobId;
    const entranceAt = mobEntrance[mobId] ?? active.start;
    const exitAt = mobExit[mobId];
    // アバターと同じ「登場〜退場」のスライド演出（フェードではなく画面端からの出入り）。
    const enterDir = mobEnterDirs[mobId];
    const exitDirVal = mobExitDir[mobId];
    const isInitial = entranceAt <= seg.start + 1e-6;
    const entersInstantly = enterDir === "instant";
    let slideOffsetPx = 0;
    if ((!isInitial || enterDir !== undefined) && !entersInstantly) {
      const sp = clamp((t - entranceAt) / slideDur, 0, 1);
      const e = easeOutCubic(sp);
      const fromXNorm = enterDir === "right" ? 1.35 : enterDir === "left" ? -0.35 : a.x < 0.5 ? -0.35 : 1.35;
      slideOffsetPx = (1 - e) * (fromXNorm - a.x) * width;
    }
    const exitsInstantly = exitDirVal === "instant";
    if (exitAt !== undefined && t >= exitAt && !exitsInstantly) {
      const sp = clamp((t - exitAt) / slideDur, 0, 1);
      const e = easeInOutCubic(sp);
      const toXNorm = exitDirVal === "right" ? 1.35 : exitDirVal === "left" ? -0.35 : a.x < 0.5 ? -0.35 : 1.35;
      slideOffsetPx = e * (toXNorm - a.x) * width;
    }
    const mouthOpen = isSpeakingNow && !active.noLipSync && speakerAmp >= MOUTH_HALF;
    return (
      <div
        key={`mob-${mobId}`}
        style={{
          position: "absolute",
          left: a.x * width + slideOffsetPx,
          top: a.y * height,
          transform: `translate(-50%, -100%) scaleX(${m.flip ? -1 : 1})`,
          transformOrigin: "bottom center",
        }}
      >
        <img
          src={staticFile(mobImage(
            mobId,
            (isSpeakingNow ? active.expression : undefined) ?? lastExpressionOf(script, mobId, t),
            mouthOpen
          ))}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
          style={{ display: "block", height: h, width: "auto" }}
        />
      </div>
    );
  };

  // 吹き出しは基本は安定表示を優先して最終カメラ基準。
  // ただし emphasis 中だけは少し現在カメラへ追従させ、寄りの違和感を減らす。
  const stx = clamp(
    width / 2 - Tcur.cx * width * Tcur.s,
    width * (1 - Tcur.s),
    0
  );
  // 1つの吹き出しを描く（話者の足元・話者色）。
  // 下端を固定(translateY -100%)して上に伸ばす＝行が増えても画面下にはみ出ない。
  const bubbleBaseMaxWidth = width * 0.72;
  const zoomBubbleK = clamp((tf.s - 1) / 0.6, 0, 1);
  const followK = focusBubbleK * 0.45;
  const bubbleBoxStyle = (
    color: string,
    text: string,
    stacked: boolean,
    align: "left" | "right",
    widthPx: number,
    fontSize: number
  ): React.CSSProperties => ({
    display: "inline-block",
    width: widthPx,
    boxSizing: "border-box",
    background: displaySettings.bubble.bgColor,
    color: displaySettings.bubble.textColor,
    padding: "14px 28px",
    borderRadius: displaySettings.bubble.radius,
    border: `${displaySettings.bubble.borderWidth}px solid ${color}`,
    fontSize,
    lineHeight: 1.3,
    fontWeight: 700,
    fontFamily: displaySettings.bubble.fontFamily,
    boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
    textAlign: align,
    // 改行位置は bubbleMetrics が決める。pre-wrap にすると実フォント幅との差で
    // ブラウザが追加改行し、同じ台詞でも意図しない段数に変わってしまう。
    whiteSpace: "pre",
  });
  const bubbleGroupPlacement = (speaker: string, groupWidth: number) => {
    const a = isMob(speaker)
      ? resolveMobXY(speaker, seg, t, sceneDef, { x: 0.5, y: 1.02 })
      : resolveCharXY(speaker, anchorOf, sceneDef, seg, t, { x: 0.5, y: 1.02 });
    const finalSx = stx + a.x * width * Tcur.s;
    const currentSx = tf.tx + a.x * width * tf.s;
    const baseSx = lerp(finalSx, currentSx, followK);
    const side = bubbleSide(baseSx, width);
    const groupCenterX = clamp(
      baseSx,
      groupWidth / 2 + 20,
      width - groupWidth / 2 - 20
    );
    const baseTop = height * lerp(0.95, 0.9, zoomBubbleK);
    const currentTop = height * lerp(0.95, 0.87, zoomBubbleK);
    return { side, groupCenterX, top: lerp(baseTop, currentTop, followK) };
  };
  const renderBubble = (turn: StoryTurn, key: string, bottomOffset = 0, stacked = false) => {
    const text = bubbleTextAt(turn, t);
    const bubbleMaxWidth = bubbleMaxWidthForTurn(turn, width, stacked, bubbleBaseMaxWidth, displaySettings.bubble);
    const metrics = bubbleMetrics(turn, text, stacked, bubbleMaxWidth, displaySettings.bubble);
    const { side, groupCenterX, top } = bubbleGroupPlacement(turn.speaker, metrics.width);
    const sx = side === "right"
      ? groupCenterX + metrics.width / 2
      : groupCenterX - metrics.width / 2;
    const color = speakerBubbleColor(turn.speaker, displaySettings);
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: sx,
          top: top - bottomOffset,
          transform: side === "right" ? "translate(-100%, -100%)" : "translate(0, -100%)",
          ...bubbleBoxStyle(color, text, stacked, side, metrics.width, metrics.fontSize),
        }}
      >
        {metrics.text}
      </div>
    );
  };
  const renderBubbleGroup = (
    turn: StoryTurn,
    speaker: string,
    texts: string[],
    visibleCount: number,
    key: string
  ) => {
    const bubbleMaxWidth = bubbleMaxWidthForTurn(turn, width, true, bubbleBaseMaxWidth, displaySettings.bubble);
    const metrics = texts.map((text) => bubbleMetrics(turn, text, true, bubbleMaxWidth, displaySettings.bubble));
    const groupWidth = metrics.reduce((max, item) => Math.max(max, item.width), 120);
    const { side, groupCenterX, top } = bubbleGroupPlacement(speaker, groupWidth);
    const color = speakerBubbleColor(speaker, displaySettings);
    const bubbleStepX = side === "right" ? -18 : 18;
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: groupCenterX,
          top,
          transform: "translate(-50%, -100%)",
          display: "flex",
          flexDirection: "column",
          alignItems: side === "right" ? "flex-end" : "flex-start",
          gap: 6,
          width: groupWidth,
          pointerEvents: "none",
        }}
        >
          {texts.map((text, idx) => (
          <div
            key={`${key}-${idx}`}
            style={{
              ...bubbleBoxStyle(color, text, true, side, metrics[idx].width, metrics[idx].fontSize),
              transform: `translateX(${idx * bubbleStepX}px)`,
              visibility: idx < visibleCount ? "visible" : "hidden",
            }}
          >
            {metrics[idx].text}
          </div>
        ))}
      </div>
    );
  };
  const subtitleStyleOf = (turn: StoryTurn): Required<SubtitleStyle> => {
    const raw = turn.subtitleStyle || {};
    const fontSizeNum = Number(raw.fontSize);
    const fontSize = Number.isFinite(fontSizeNum)
      ? clamp(Math.round(fontSizeNum), 24, 96)
      : displaySettings.subtitle.fontSize;
    const textColor = /^#([0-9a-fA-F]{6})$/.test(String(raw.textColor || ""))
      ? String(raw.textColor)
      : displaySettings.subtitle.textColor;
    const boxBorderColor = /^#([0-9a-fA-F]{6})$/.test(String(raw.boxBorderColor || ""))
      ? String(raw.boxBorderColor)
      : displaySettings.subtitle.borderColor;
    const boxBorderWidthNum = Number(raw.boxBorderWidth);
    return {
      fontSize,
      textColor,
      boxBorder: raw.boxBorder != null ? raw.boxBorder !== false : displaySettings.subtitle.border,
      boxBorderColor,
      boxBorderWidth: Number.isFinite(boxBorderWidthNum)
        ? clamp(boxBorderWidthNum, 0.5, 6)
        : displaySettings.subtitle.borderWidth,
    };
  };
  const renderSubtitle = (texts: string[], key: string) => {
    const visibleTexts = texts.filter((text) => !!String(text || "").trim());
    if (visibleTexts.length === 0) return null;
    const subtitleStyle = subtitleStyleOf(active);
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: "50%",
          bottom: displaySettings.subtitle.bottom,
          transform: "translateX(-50%)",
          width: Math.min(width * displaySettings.subtitle.width, 1360),
          padding: "16px 28px 18px",
          borderRadius: 18,
          background: hexToRgba(displaySettings.subtitle.bgColor, displaySettings.subtitle.bgOpacity),
          border: subtitleStyle.boxBorder ? `${subtitleStyle.boxBorderWidth}px solid ${subtitleStyle.boxBorderColor}` : "none",
          boxShadow: "0 14px 34px rgba(0,0,0,0.4)",
          color: subtitleStyle.textColor,
          fontSize: subtitleStyle.fontSize,
          lineHeight: 1.45,
          fontWeight: 700,
          fontFamily: displaySettings.subtitle.fontFamily,
          textAlign: "center",
          whiteSpace: "pre-wrap",
          textShadow: "0 2px 6px rgba(0,0,0,0.4)",
          pointerEvents: "none",
        }}
      >
        {visibleTexts.join("\n")}
      </div>
    );
  };

  const groupRange = activeIdx >= 0 ? continueBubbleGroupRange(script, activeIdx) : null;
  const bubbleGroup = groupRange
    ? script.slice(groupRange.start, groupRange.end + 1)
    : [active];
  const insertAtIdx = (idx: number) => (idx >= 0 ? effectiveInsertAt(script, idx) : null);
  const activeInsertLine = isInsertLineKind(activeInsert);
  const visibleGroupCount = groupRange ? activeIdx - groupRange.start + 1 : 1;
  const shouldShowBubbleGroup =
    !activeInsertLine &&
    bubbleGroup.length > 1 &&
    bubbleGroup.slice(0, visibleGroupCount).every((turn) => {
      const idx = script.indexOf(turn);
      return !isInsertLineKind(insertAtIdx(idx));
    });
  const autoBubbleTexts = bubbleSentenceTexts(active);
  const shouldShowAutoBubbleGroup =
    !shouldShowBubbleGroup &&
    !activeInsertLine &&
    autoBubbleTexts.length > 1;
  const autoBubbleVisibleCount = bubbleSentenceVisibleCount(active, t);
  const hideBubble = active.hideBubble === true;
  const shouldShowSubtitle = !hideBubble && !isNarrationTurn(active) && hasBubbleText(active) && isSubtitleTurn(active) && !activeInsertLine;
  const subtitleTexts = shouldShowSubtitle
    ? (bubbleGroup.length > 1
      ? bubbleGroup.slice(0, visibleGroupCount).map((turn) => subtitleProgressiveText(turn, t))
      : [subtitleProgressiveText(active, t)])
    : [];

  // ── 回想（flashback）演出 ────────────────────────────────────
  const isFlashback = !!active.flashback;

  // flashback が切り替わる境界時刻を全ターンから列挙する。
  // 境界 = 前のターンと flashback 状態が違う最初のターンの start。
  type FbBoundary = { at: number; entering: boolean; telop?: string };
  const fbBoundaries: FbBoundary[] = [];
  for (let i = 1; i < script.length; i++) {
    const prev = script[i - 1];
    const cur = script[i];
    if (!!prev.flashback !== !!cur.flashback) {
      fbBoundaries.push({
        at: cur.start,
        entering: !!cur.flashback,
        // 回想に入るターンと戻るターンに telop を付ける。
        telop: cur.telop,
      });
    }
  }

  // 回想から戻る境界のテロップ表示に使う。白ディゾルブは自動では出さない。
  const nearestBoundary = fbBoundaries.reduce<FbBoundary | null>((best, b) => {
    if (best === null) return b;
    return Math.abs(t - b.at) < Math.abs(t - best.at) ? b : best;
  }, null);

  // ZunMeet タイル用ライブフィード（カメラ映像風の合成）。
  // 全タイル共通の描画方式: bgStyle 背景（renderTile側）＋ここで立ち絵を下端中央に合成する。
  // 立ち絵の表示幅はタイル幅の一定割合＝フォーカス切替では「同じ映像のサイズと場所」だけが変わる。
  const renderVideoCallFeed = (
    participant: VideoCallParticipant,
    opts: { large: boolean; tileW: number }
  ): React.ReactNode => {
    const speaker = participant.speaker;
    const isSpeaking = !isNarrationTurn(active) && active.speaker === speaker;
    const feedX = clamp(participant.feedX ?? 0.5, -0.5, 1.5);
    const feedY = clamp(participant.feedY ?? 1, -0.5, 1.5);
    const feedScale = clamp(participant.feedScale ?? 1, 0.1, 3);
    const dispW = opts.tileW * VC_FEED_AVATAR_FRAC * feedScale;
    if (isKnownChar(speaker)) {
      const cdef = CHARACTERS[speaker];
      // 話者は現在ターンの表情/ポーズ/リップシンク。表情未指定・非話者とも
      // 「自分が最後に話したときの表情」を引き継ぐ（メイン画面の会話と同じ挙動）。
      // 非話者は表情ごとの holdAs(引き継ぎ先)設定を適用する。
      const charExprs = expressions?.[cdef.avatar];
      const lastExpr = lastExpressionOf(script, speaker, t) ?? "normal";
      const exprKey = isSpeaking
        ? active.expression ?? lastExpr
        : holdExpressionKey(lastExpr, charExprs);
      const emotion = EXPRESSION_TO_EMOTION[exprKey] ?? EXPRESSION_TO_EMOTION["normal"];
      const expressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
      const poseCfg = isSpeaking && active.pose ? poses?.[cdef.avatar]?.[active.pose] ?? null : null;
      const k = dispW / AVATAR_BOX;
      return (
        <div
          style={{
            position: "absolute",
            left: `${feedX * 100}%`,
            // 下へはみ出させて胸元をクロップ＝顔に寄せる（タイル側 overflow:hidden）。
            bottom: `calc(${(1 - feedY) * 100}% - ${dispW * VC_FEED_BOTTOM_SHIFT}px)`,
            width: dispW,
            height: dispW,
            transform: "translateX(-50%)",
            filter: "drop-shadow(0 12px 20px rgba(0,0,0,0.34))",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: 0,
              bottom: 0,
              width: AVATAR_BOX,
              height: AVATAR_BOX,
              transform: `scale(${k})`,
              transformOrigin: "bottom left",
            }}
          >
            <Avatar
              dir={cdef.avatar}
              manifest={manifest?.[cdef.avatar]}
              fallbackGender={cdef.gender}
              active={isSpeaking}
              activatedAtFrame={Math.round(active.start * fps)}
              amplitude={isSpeaking && !active.noLipSync ? speakerAmp : 0}
              emotion={emotion}
              emotionAtFrame={Math.round(active.start * fps)}
              expressive={!!cdef.expressive}
              flip={false}
              popScale={false}
              boxWidth={AVATAR_BOX}
              boxHeight={AVATAR_BOX}
              expressionCfg={expressionCfg}
              poseName={isSpeaking ? active.pose : undefined}
              poseArmStem={isSpeaking ? poseCfg?.arm ?? null : null}
              poseSpeed={isSpeaking ? poseCfg?.speed ?? null : null}
              poseStrength={isSpeaking ? poseCfg?.strength ?? null : null}
            />
          </div>
        </div>
      );
    }
    if (isMob(speaker)) {
      const mouthOpen = isSpeaking && !active.noLipSync && speakerAmp >= MOUTH_HALF;
      return (
        <img
          src={staticFile(mobImage(
            speaker,
            (isSpeaking ? active.expression : undefined) ?? lastExpressionOf(script, speaker, t),
            mouthOpen
          ))}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
          style={{
            position: "absolute",
            left: `${feedX * 100}%`,
            bottom: `${(1 - feedY) * 100}%`,
            width: opts.tileW * VC_FEED_MOB_FRAC * feedScale,
            maxHeight: "88%",
            transform: "translateX(-50%)",
            objectFit: "contain",
            objectPosition: "bottom center",
            filter: "drop-shadow(0 12px 20px rgba(0,0,0,0.34))",
          }}
        />
      );
    }
    return null; // 未知話者は renderTile 側のフォールバック（頭文字）に任せる
  };

  // ワイプ/スライドで先出しする次シーンの「開始直後の静止フレーム」のキャラ。
  // 次シーン冒頭からいる面子だけを、アニメーション無しの静止ポーズで描く
  // （途中登場のキャラは従来通りシーン開始後に自分でスライドインする＝対象外）。
  // これが無いと、ワイプ中は背景だけ見えてキャラは次シーンが始まった瞬間に
  // 突然ポップインしてしまう。
  const renderNextSceneAvatarsGhost = () => {
    if (!nextSeg || !nextSceneDef) return null;
    const nextActive = nextSeg.turns[0];
    if (!nextActive) return null;
    const nextRoster = segmentRoster(nextSeg);
    const nextAnchorOf = resolveAnchorMapAt(nextSeg, nextRoster, nextSceneDef, nextSeg.start);
    const nextEntrance = entranceTimesFor(nextSeg, isKnownChar);
    const nextPresent = nextRoster.filter(
      (c) => (nextEntrance[c] ?? nextSeg.start) <= nextSeg.start + 1e-6
    );
    const isFull = (nextSceneDef.figure ?? "bust") === "full";
    return nextPresent.map((charId) => {
      const cdef = CHARACTERS[charId];
      if (!cdef) return null;
      const anchor = resolveCharXY(charId, nextAnchorOf, nextSceneDef!, nextSeg!, nextSeg!.start, { x: 0.5, y: 1.02 });
      const isSpeaker = !isNarrationTurn(nextActive) && charId === nextActive.speaker;
      const explicitFace = normalizeFaceDirectionForChar(charId, nextActive.face?.[charId]);
      const resolvedFace = explicitFace ?? heldFaceOf(script, charId, nextSeg!.start);
      const angleSrc = angleFaceSrc(charId, nextSceneDef!, resolvedFace);
      const angleScale = angleFaceScale(charId, resolvedFace);
      const flip = angleSrc ? false : facingFlipFor(resolvedFace, anchor.x);
      // 次ターンの入場プレビューも表情未指定なら直前の自分の表情を引き継ぐ。
      const exprKey =
        nextActive.expression ??
        lastExpressionOf(script, charId, nextActive.start ?? t) ??
        "normal";
      const charKey = cdef.avatar;
      const charExprs = expressions?.[charKey];
      const baseExpressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
      const poseCfg = nextActive.pose ? poses?.[charKey]?.[nextActive.pose] ?? null : null;
      const expressionCfg =
        baseExpressionCfg && nextActive.pose
          ? { ...baseExpressionCfg, pose: nextActive.pose }
          : baseExpressionCfg;
      const emotion = EXPRESSION_TO_EMOTION[exprKey] ?? EXPRESSION_TO_EMOTION["normal"];
      const avatarDir = isFull ? `${cdef.avatar}/full` : cdef.avatar;
      const manifestKey = isFull ? `${cdef.avatar}_full` : cdef.avatar;
      const avatarManifest = manifest?.[manifestKey];
      const box = isFull ? fullBoxSize(cdef.avatar) : { w: AVATAR_BOX, h: AVATAR_BOX };
      return (
        <div
          key={`ghost-${charId}`}
          style={{
            position: "absolute",
            left: anchor.x * width,
            top: anchor.y * height,
            transform: "translate(-50%, -50%)",
          }}
        >
          <div style={{ transform: `scale(${nextSceneDef!.scale ?? 1.9})`, transformOrigin: "center" }}>
            <Avatar
              dir={avatarDir}
              manifest={avatarManifest}
              singleSrc={angleSrc}
              singleScale={angleScale}
              fallbackGender={cdef.gender}
              active={isSpeaker}
              activatedAtFrame={Math.round(nextSeg!.start * fps)}
              amplitude={0}
              emotion={emotion}
              emotionAtFrame={Math.round(nextSeg!.start * fps)}
              expressive={!!cdef.expressive}
              flip={flip}
              popScale={false}
              boxWidth={box.w}
              boxHeight={box.h}
              expressionCfg={expressionCfg}
              poseName={isSpeaker ? nextActive.pose : undefined}
              poseArmStem={isSpeaker ? poseCfg?.arm ?? null : null}
              poseSpeed={isSpeaker ? poseCfg?.speed ?? null : null}
              poseStrength={isSpeaker ? poseCfg?.strength ?? null : null}
            />
          </div>
        </div>
      );
    });
  };

  const renderSceneBackdrop = (plateSceneDef: SceneDef) => {
    const plateBlur = Math.max(0, plateSceneDef.bgBlur ?? 0);
    const plateScale = plateBlur > 0 ? 1 + Math.min(plateBlur, 32) / 180 : 1;
    const mediaStyle: React.CSSProperties = {
      position: "absolute",
      inset: 0,
      width: "100%",
      height: "100%",
      objectFit: "cover",
      filter: plateBlur > 0 ? `blur(${plateBlur}px)` : undefined,
      transform: plateScale > 1 ? `scale(${plateScale})` : undefined,
      transformOrigin: "center center",
    };
    return (
      <>
        {plateSceneDef.bgVideo ? (
          <Video
            src={staticFile(plateSceneDef.bgVideo)}
            muted
            loop={plateSceneDef.bgVideoLoop === true}
            style={mediaStyle}
          />
        ) : (
          <Img src={staticFile(plateSceneDef.bg)} style={mediaStyle} />
        )}
      </>
    );
  };

  const renderScenePlate = (
    plateSceneDef: SceneDef,
    key: string,
    opts?: { clipPath?: string; shiftX?: number; filter?: string; stageTransform?: string; children?: React.ReactNode }
  ) => {
    const plateTransform = opts?.shiftX
      ? `translateX(${opts.shiftX}px)`
      : undefined;
    return (
      <AbsoluteFill
        key={key}
        style={{
          transform: plateTransform,
          clipPath: opts?.clipPath,
          filter: opts?.filter,
          overflow: "hidden",
        }}
      >
        <AbsoluteFill
          style={{
            transform: opts?.stageTransform,
            transformOrigin: "0 0",
            overflow: "hidden",
          }}
        >
          {renderSceneBackdrop(plateSceneDef)}
          {opts?.children}
          {plateSceneDef.front ? (
            <Img
              src={staticFile(plateSceneDef.front)}
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
      </AbsoluteFill>
    );
  };

  // グレインのシードをフレームごとに変えてちらつきを出す（軽量: 数px の位置オフセット）。
  const grainOffsetX = (frame * 7) % 64;
  const grainOffsetY = (frame * 13) % 64;

  // テロップ表示: 境界から FB_TELOP_SEC の間、フェードイン/アウトして出す。
  // 回想に入る境界: その境界の telop を使う。
  // 「現在」へ戻る境界: 戻り先のターンの telop。
  // 回想中は左上に「前日」ラベルを固定表示（大きめ・回想の間ずっと）。入った所でフェードイン。
  // 「現在」へ戻る境界など回想以外の telop は、従来どおり短時間だけ出す。
  let telopText: string | null = null;
  let telopOpacity = 0;
  let telopTurn: StoryTurn | null = null;
  const firstTurn = script[0] ?? null;
  const introTelopDt = firstTurn ? t - firstTurn.start : Number.POSITIVE_INFINITY;
  const shouldShowIntroTelop =
    !!firstTurn?.telop &&
    introTelopDt >= -FB_TELOP_FADE &&
    introTelopDt < FB_TELOP_SEC;
  if (isFlashback) {
    const entered = [...fbBoundaries]
      .filter((b) => b.entering && b.at <= t + 1e-6)
      .pop();
    if (entered?.telop) {
      telopText = entered.telop;
      telopTurn = script.find((turn) => turn.start === entered.at) ?? null;
      telopOpacity = clamp((t - entered.at) / FB_TELOP_FADE, 0, 1);
    } else if (firstTurn?.flashback && shouldShowIntroTelop) {
      telopText = firstTurn.telop ?? null;
      telopTurn = firstTurn;
      telopOpacity = clamp((t - firstTurn.start) / FB_TELOP_FADE, 0, 1);
    }
  } else if (shouldShowIntroTelop && !firstTurn?.flashback) {
    telopText = firstTurn.telop ?? null;
    telopTurn = firstTurn;
    if (introTelopDt < FB_TELOP_FADE) {
      telopOpacity = clamp((introTelopDt + FB_TELOP_FADE) / FB_TELOP_FADE, 0, 1);
    } else if (introTelopDt >= FB_TELOP_SEC - FB_TELOP_FADE) {
      telopOpacity = clamp((FB_TELOP_SEC - introTelopDt) / FB_TELOP_FADE, 0, 1);
    } else {
      telopOpacity = 1;
    }
  } else if (nearestBoundary?.telop && !nearestBoundary.entering) {
    const dt = t - nearestBoundary.at;
    if (dt >= -FB_TELOP_FADE && dt < FB_TELOP_SEC) {
      telopText = nearestBoundary.telop;
      telopTurn = script.find((turn) => turn.start === nearestBoundary.at) ?? null;
      if (dt < FB_TELOP_FADE) {
        telopOpacity = clamp((dt + FB_TELOP_FADE) / FB_TELOP_FADE, 0, 1);
      } else if (dt >= FB_TELOP_SEC - FB_TELOP_FADE) {
        telopOpacity = clamp((FB_TELOP_SEC - dt) / FB_TELOP_FADE, 0, 1);
      } else {
        telopOpacity = 1;
      }
    }
  }
  if (!telopText && active.telop) {
    let telopBlockStartIdx = activeIdx;
    while (telopBlockStartIdx > 0 && !!script[telopBlockStartIdx - 1]?.telop) {
      telopBlockStartIdx--;
    }
    let telopBlockEndIdx = activeIdx;
    while (telopBlockEndIdx + 1 < script.length && !!script[telopBlockEndIdx + 1]?.telop) {
      telopBlockEndIdx++;
    }
    const telopBlockStart = script[telopBlockStartIdx]?.start ?? active.start;
    const telopBlockLast = script[telopBlockEndIdx] ?? active;
    const telopBlockEnd = Math.max(telopBlockLast.end, script[telopBlockEndIdx + 1]?.start ?? telopBlockLast.end);
    const dt = t - telopBlockStart;
    const outStart = Math.max(telopBlockStart, telopBlockEnd - FB_TELOP_FADE);
    if (dt >= -FB_TELOP_FADE && t < telopBlockEnd) {
      telopText = active.telop;
      telopTurn = active;
      if (dt < FB_TELOP_FADE) {
        telopOpacity = clamp((dt + FB_TELOP_FADE) / FB_TELOP_FADE, 0, 1);
      } else if (t >= outStart) {
        telopOpacity = clamp((telopBlockEnd - t) / FB_TELOP_FADE, 0, 1);
      } else {
        telopOpacity = 1;
      }
    }
  }
  const telopX = typeof telopTurn?.telopX === "number" ? telopTurn.telopX : displaySettings.telop.x;
  const telopY = typeof telopTurn?.telopY === "number" ? telopTurn.telopY : displaySettings.telop.y;
  const telopSize = typeof telopTurn?.telopSize === "number" ? telopTurn.telopSize : displaySettings.telop.size;

  // 回想中はステージに彩度ダウン＋輝度微加の CSS filter を掛ける。
  const stageFilter = isFlashback
    ? `saturate(${FB_SATURATE}) brightness(${FB_BRIGHTNESS})`
    : undefined;

  // ── PC画面インサート フェード計算 ─────────────────────────
  // INSERT_FADE: フェードイン/アウトの片側秒数。
  const INSERT_FADE = 0.2;
  const activeOverlays = activeOverlaysAt(script, story.overlays, t);
  const hideCharacters = active.hideCharacters === true;
  const nextTurn2 = activeIdx < script.length - 1 ? script[activeIdx + 1] : null;
  // 隣のターンがインサートを持つか（種別問わず）。インサート同士の間は通常画面を出さない。
  const prevHasInsert = activeIdx > 0 && !!effectiveInsertAt(script, activeIdx - 1);
  const nextHasInsert = !!(nextTurn2 && effectiveInsertAt(script, activeIdx + 1));
  let insertOpacity = 0;     // パネル本体（in/out両方フェード）
  let insertBgOpacity = 0;   // 背景＝シーン隠し
  // このインサートが画面に出ている終端＝次ターンの開始(無ければ自分のend)。
  // 空セリフのインサート(end==start)でも次ターンまで表示されるよう実効終端を使う。
  const dispEnd = nextTurn2 ? nextTurn2.start : active.end;
  if (activeInsert) {
    // フェードイン: 直前がインサート/冒頭なら即1。背景はそもそもフェードインしない(即カバー)。
    const fadeIn = (prevHasInsert || activeIdx === 0)
      ? 1
      : clamp((t - active.start) / INSERT_FADE, 0, 1);
    // フェードアウト: 次がインサートなら消さない(隙間で通常画面を出さない)。
    // 次が非インサートの時だけ、実効終端へ向けてフェードアウトし次シーンを滑らかに見せる。
    const fadeOut = nextHasInsert
      ? 1
      : clamp((dispEnd - t) / INSERT_FADE, 0, 1);
    insertOpacity = Math.min(fadeIn, fadeOut);
    insertBgOpacity = fadeOut;
  }

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {audio ? <Audio src={mediaStaticSrc(audioSrc)} /> : null}
      <BgmLayer
        bgmRegions={story.bgm}
        fps={fps}
      />
      <SeLayer
        script={script}
        seMap={seMap}
        fps={fps}
      />
      {incomingPlate
        ? renderScenePlate(incomingPlate.sceneDef, incomingPlate.key, {
          clipPath: incomingPlate.clipPath,
          shiftX: incomingPlate.shiftX,
          filter: stageFilter,
          stageTransform: incomingPlate.stageTransform,
          children: renderNextSceneAvatarsGhost(),
        })
        : null}
      {/* ステージ（背景＋キャラ＋前景を1枚として仮想カメラで撮る）。
          ワイプ/スライドはカメラ後の表示プレートに掛ける。 */}
      <AbsoluteFill
        style={{
          clipPath: currentStageClipPath,
          overflow: "hidden",
          transform: currentStageShiftX !== 0 ? `translateX(${currentStageShiftX}px)` : undefined,
        }}
      >
        <AbsoluteFill
          style={{
            transform: stageTransform,
            transformOrigin: "0 0",
            filter: stageFilter,
            overflow: "hidden",
          }}
        >
          {/* 背景（back） */}
          {renderSceneBackdrop(sceneDef)}

          {/* キャラ（back と front の間） */}
          {hideCharacters ? null : presentNow.map(renderAvatar)}

          {/* モブ（登場〜退場の区間だけ1枚絵を立たせる。素材未配置なら自動で非表示） */}
          {hideCharacters ? null : presentMobs.map(renderMob)}

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
      </AbsoluteFill>

      {/* 回想中グレイン（タイル状ノイズを低opacity＋毎フレームずれ）。回想中のみ表示。 */}
      {isFlashback ? (
        <AbsoluteFill
          style={{
            backgroundImage: `url(${staticFile("noise.png")})`,
            backgroundRepeat: "repeat",
            backgroundPosition: `${grainOffsetX}px ${grainOffsetY}px`,
            backgroundSize: "64px 64px",
            opacity: FB_GRAIN_OPACITY,
            pointerEvents: "none",
            mixBlendMode: "luminosity",
          }}
        />
      ) : null}

      {/* PC画面インサート（ステージより前面・吹き出しより後面）。
          z順: ステージ → インサート → 吹き出し */}
      {activeInsert && insertBgOpacity > 0 ? (
        <InsertOverlay
          insert={activeInsert}
          bgOpacity={insertBgOpacity}
          opacity={insertOpacity}
          transform={insertShakeTransform}
          activeSpeaker={active.speaker}
          durationInFrames={Math.round((dispEnd - active.start) * fps)}
          localFrame={Math.max(0, Math.round((t - active.start) * fps))}
          whiteboardPopTargets={whiteboardPopTargets}
          whiteboardCharacterSlot={
            activeInsert.kind === "whiteboard_explain" ? renderWhiteboardCharacterSlot(activeInsert) : undefined
          }
          renderVideoCallFeed={renderVideoCallFeed}
        />
      ) : null}

      {/* 補助画像オーバーレイ。ホワイトボード等のインサート演出にも重ねられるよう、
          インサートより前面、字幕/吹き出しより後面で描画する。 */}
      {activeOverlays.length > 0 ? <StoryOverlayLayer overlays={activeOverlays} /> : null}

      <ExtraEffectsLayer
        active={active}
        progress={activeProgress}
        width={width}
        height={height}
        settings={story.effectSettings}
        rawElapsedSinceStart={t - active.start}
        availableWindow={
          (script[activeIdx + 1]?.start ?? active.end + (active.pause ?? 0)) - active.start
        }
        onlyImpactLines
      />

      {shouldShowSubtitle ? renderSubtitle(subtitleTexts, `subtitle-${activeKey}`) : null}

      {/* 吹き出し。continueBubble の連続区間は1グループとして積み、
          先の段数ぶんも最初から予約して位置を固定する。インサートより前面。 */}
      {/* セリフがチャット/AIチャット画面に出ているターンは吹き出しを出さない。
          前後ターンも各自の insert 種別で判定し、遷移時のチラ見え漏れを防ぐ。 */}
      {!hideBubble && !shouldShowSubtitle && !isNarrationTurn(active) && hasBubbleText(active) && shouldShowBubbleGroup
        ? renderBubbleGroup(
          active,
          bubbleGroup[0]?.speaker ?? active.speaker,
          bubbleGroup.map((turn) => bubbleTextAt(turn, t)),
          visibleGroupCount,
          `bubble-group-${bubbleGroup[0]?.id ?? activeKey}`
        )
        : !hideBubble && !shouldShowSubtitle && !isNarrationTurn(active) && hasBubbleText(active) && shouldShowAutoBubbleGroup
          ? renderBubbleGroup(
            active,
            active.speaker,
            autoBubbleTexts,
            autoBubbleVisibleCount,
            `bubble-auto-group-${activeKey}`
          )
        : (!hideBubble && !shouldShowSubtitle && !isNarrationTurn(active) && !activeInsertLine && hasBubbleText(active)
          ? renderBubble(active, "bubble-active", bubbleBottomOffset(active, false), !!active.continueBubble)
          : null)}

      <ExtraEffectsLayer
        active={active}
        progress={activeProgress}
        width={width}
        height={height}
        settings={story.effectSettings}
        rawElapsedSinceStart={t - active.start}
        availableWindow={
          (script[activeIdx + 1]?.start ?? active.end + (active.pause ?? 0)) - active.start
        }
        hideImpactLines
      />

      {/* テロップ（回想境界付近：「― 前日 ―」「― 現在 ―」等）。ローワーサード風の帯。 */}
      {telopText && telopOpacity > 0 ? (
        <AbsoluteFill
          style={{
            pointerEvents: "none",
          }}
        >
          <div
            style={{
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
              opacity: telopOpacity,
              transform: "translate(0, 0)",
            }}
          >
            {telopText}
          </div>
        </AbsoluteFill>
      ) : null}

      {/* 場面切り替えのフェード被せ。回想の自動白ディゾルブは使わず、手動トランジションで制御する。 */}
      {transitionCoverColor && transitionCoverOpacity > 0 ? (
        <AbsoluteFill
          style={{ background: transitionCoverColor, opacity: transitionCoverOpacity, pointerEvents: "none" }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
