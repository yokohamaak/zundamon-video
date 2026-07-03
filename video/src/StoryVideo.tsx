import {
  AbsoluteFill,
  Audio,
  getRemotionEnvironment,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useWindowedAudioData } from "@remotion/media-utils";
import { Avatar } from "./Avatar";
import type { ExpressionCfg } from "./Avatar";
import type { Emotion, Gender } from "./types";

// ─── PC画面インサート型定義 ──────────────────────────────────
export type StoryInsert =
  | { kind: "warning"; width?: number; title?: string; text: string }
  | { kind: "chat"; width?: number; user: string; ai: string[]; highlight?: number }
  | { kind: "ok"; width?: number; text?: string }
  | { kind: "teamchat"; width?: number; channel?: string; messages: { from: string; text: string; highlight?: boolean }[] }
  | { kind: "mailer"; width?: number; from?: string; fromAddr?: string; subject: string; body: string; time?: string }
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
      cameraOff?: boolean;
      muted?: boolean;
    }>;
  };

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。DialogueVideo と同値。
const LIPSYNC_GAIN = 5;

// ─── 回想（flashback）演出の定数 ────────────────────────────
// 後で調整しやすいよう1箇所にまとめる。
const FB_SATURATE = 0.4;        // 回想中の彩度（1.0=元のまま・低いほど色が薄い）
const FB_BRIGHTNESS = 1.02;     // 回想中の輝度（微加）
const FB_GRAIN_OPACITY = 0.06;  // グレインの不透明度（0.0=なし・0.1で見えてくる）
const FB_DISSOLVE_SEC = 0.3;    // 白ディゾルブ片側の秒数（合計 2×FB_DISSOLVE_SEC）
const FB_TELOP_SEC = 1.2;       // テロップの表示秒数
const FB_TELOP_FADE = 0.25;     // テロップのフェードイン/アウト秒数
const CAMERA_EFFECT_SETTLE_SEC = 0.4; // パン/傾き/引きが到達するまでの固定秒数

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
    | "step_in"
    | "step_back"
    | "listening"
    | "sneak"
    | "wobble";
  enter?: string[];
  enterMode?: "instant";
  speakerAnchor?: string;
  // キャラの向き（画面のどちらを向くか）の明示指定。省略時は立ち位置から自動（中央向き）。
  // 例: { "zundamon": "left", "metan": "right" }
  face?: Record<string, "left" | "right">;
  // 話者プッシュイン演出の開始トリガー。
  // emphasis=true のターンで寄りを開始し、同じ話者が続く間は維持する。
  emphasis?: boolean;
  // その行だけ付ける追加カメラ効果。
  cameraEffect?: "pull-out" | "pan-left" | "pan-right" | "tilt-left" | "tilt-right";
  // カメラシェイク演出（shake=true のターン中、ターン開始からの減衰振動オフセットを加算）。
  shake?: boolean;
  // 回想フラグ（true のターンが回想区間）。
  flashback?: boolean;
  // テロップ（境界付近で短時間表示する時代テキスト。例「― 前日 ―」）。
  telop?: string;
  telopSize?: number;
  telopX?: number;
  telopY?: number;
  // 追加の単発演出。主に行頭〜行末の短い強調演出として使う。
  impactText?: string;
  zoomPunch?: boolean;
  quoteFreeze?: boolean;
  stampRain?: string;
  typingFlood?: boolean;
  sparkleBurst?: boolean;
  irisOut?: boolean;
  effectSettings?: StoryEffectSettings;
  // 台詞後の無音秒（音声生成で使用。描画では参照しない）。
  pause?: number;
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
  disableAutoBubbleSplit?: boolean;
  noLipSync?: boolean;
  sentences?: StorySentence[];
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
  zoomPunch?: { scale?: number; duration?: number; borderStrength?: number };
  quoteFreeze?: { fadeIn?: number; fadeOutStart?: number; fadeOutDuration?: number; backdropOpacity?: number };
  stampRain?: { count?: number; fallDuration?: number; stagger?: number; spread?: number };
  typingFlood?: { rows?: number; flowDuration?: number; stagger?: number };
  sparkleBurst?: { count?: number; spread?: number; duration?: number };
  irisOut?: { start?: number; duration?: number; startRadius?: number };
};

type ResolvedEffectSettings = {
  zoomPunch: { scale: number; duration: number; borderStrength: number };
  quoteFreeze: { fadeIn: number; fadeOutStart: number; fadeOutDuration: number; backdropOpacity: number };
  stampRain: { count: number; fallDuration: number; stagger: number; spread: number };
  typingFlood: { rows: number; flowDuration: number; stagger: number };
  sparkleBurst: { count: number; spread: number; duration: number };
  irisOut: { start: number; duration: number; startRadius: number };
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
  effectSettings?: StoryEffectSettings;
  overlays?: StoryOverlay[];
  script: StoryTurn[];
};

type Anchor = { x: number; y: number };

export type SceneDef = {
  label?: string;
  bg: string;
  // 背景(back)だけに掛ける被写界深度風のブラー量(px)。
  bgBlur?: number;
  front?: string | null;
  shot?: "solo" | "duo" | "split";
  camera?: "static" | "slow-zoom";
  soloZoom?: boolean;
  soloZoomScale?: number;
  soloZoomCy?: number;
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
  // BGM（このシーンに設定するBGMファイル・例 "bgm/bgm.mp3"）。
  bgm?: string;
  bgmVolume?: number;
};

export type SceneLibrary = { scenes: Record<string, SceneDef> };

export type CharDef = {
  avatar: string; // パーツ立ち絵フォルダ名
  gender: Gender; // フォールバック用
  expressive?: boolean;
  bubbleColor?: string; // 吹き出し枠の色（話者で色分け）
};

// モブ／未定義キャラの既定枠色。
const DEFAULT_BUBBLE_COLOR = "#9aa0a6"; // グレー

// 主役キャラ定義（Phase 1 はインライン。将来はライブラリ化）。
const CHARACTERS: Record<string, CharDef> = {
  zundamon: { avatar: "zundamon", gender: "male", expressive: true, bubbleColor: "#5fb84f" }, // 緑系
  metan: { avatar: "metan", gender: "female", expressive: false, bubbleColor: "#e87bb0" }, // ピンク系
};

// モブ定義（いらすとや風の1枚絵・口パク無し）。話している間だけ立つ。
// images: 状態キー→ファイル（normal / agitated）。表情で出し分ける。
// 画像は public/mobs/<file>（assets/mobs を prep-story が public へコピー）。
type MobDef = { images: Record<string, string>; scale?: number; flip?: boolean; anchor?: Anchor };
const MOBS: Record<string, MobDef> = {
  // 営業=ノートPC作業姿（画像下端=机／机frontで隠れる）。anchor.y を下げて机裏に。
  営業: {
    images: { normal: "mobs/mob_normal.png", agitated: "mobs/mob_panic.png" },
    scale: 0.85,
    anchor: { x: 0.5, y: 0.99 },
  },
  // 部長=バスト肖像（画像下端=胸）。anchor.y を上げて胸が机の高さに来るように。
  部長: {
    images: { normal: "mobs/manager_normal.png", agitated: "mobs/manager_angry.png" },
    scale: 0.62,
    anchor: { x: 0.5, y: 0.82 },
  },
};
const isMob = (id: string): boolean => id in MOBS;
// セリフ文字がインサートUI内に表示される種別（吹き出しを抑制する）。
// videocall はUI内にセリフが出ないため対象外＝通常の吹き出しを通話画面の手前に出す。
function isInsertLineKind(insert: StoryInsert | null | undefined): boolean {
  return !!insert && (insert.kind === "teamchat" || insert.kind === "chat");
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
function mobImage(mobId: string, expression?: StoryExpression): string {
  const m = MOBS[mobId];
  const agitated =
    expression === "panic" || expression === "surprise" || expression === "trouble";
  const key = agitated && m.images.agitated ? "agitated" : "normal";
  return m.images[key] ?? Object.values(m.images)[0];
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
};

// 立ち絵ボックスサイズ。バスト用は 445×445（Avatar の既定値と同じ）。
// 全身用はキャンバスのアスペクト比をバスト幅445に合わせた高さ。
const AVATAR_BOX = 445;
// 全身キャンバスサイズ（PSD書き出し時の共通bbox＝assets/avatars/<char>/full/_box.json と一致）。
const FULL_CANVAS = {
  zundamon: { w: 783, h: 1473 },
  metan: { w: 858, h: 1769 },
} as const;
const FULL_BOX_W = 445; // 全身Avatar表示幅（px）。scene_editor.html と同値にする（WYSIWYG）。sceneのavScaleで最終サイズが決まる。
function fullBoxSize(charId: string): { w: number; h: number } {
  const c = FULL_CANVAS[charId as keyof typeof FULL_CANVAS];
  if (!c) return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * 1.8) };
  return { w: FULL_BOX_W, h: Math.round(FULL_BOX_W * (c.h / c.w)) };
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

// scene 名が連続する範囲を 1 区間（segment）にまとめる（§4.1）。
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
    if (last && last.scene === t.scene) {
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
    if (turn.id === activeTurn.id) break;
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

// 各キャラが segment 内で初めて画面に出る時刻（秒）。
// モブ（CHARACTERS 未定義）は除外（アバターを持たないのでレイアウト計算に不要）。
function entranceTimes(seg: Segment): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !(c in e)) e[c] = turn.start;
    }
    if (!isNarrationTurn(turn) && isKnownChar(turn.speaker) && !(turn.speaker in e)) e[turn.speaker] = turn.start;
  }
  return e;
}

function instantEnterChars(seg: Segment): Record<string, true> {
  const out: Record<string, true> = {};
  for (const turn of seg.turns) {
    if (turn.enterMode !== "instant") continue;
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c)) out[c] = true;
    }
  }
  return out;
}

// 各キャラの退場時刻（秒）。turn.exit で指定されたキャラは、そのターンの end で退場する。
function exitTimes(seg: Segment): Record<string, number> {
  const e: Record<string, number> = {};
  for (const turn of seg.turns) {
    for (const c of turn.exit ?? []) {
      if (isKnownChar(c)) e[c] = turn.end;
    }
  }
  return e;
}

// 各キャラの退場方向（turn.exitDir）。省略時は undefined（＝自分の居る側へ）。
function exitDirs(seg: Segment): Record<string, "left" | "right" | "instant"> {
  const d: Record<string, "left" | "right" | "instant"> = {};
  for (const turn of seg.turns) {
    if (!turn.exitDir) continue;
    for (const c of turn.exit ?? []) {
      if (isKnownChar(c)) d[c] = turn.exitDir;
    }
  }
  return d;
}

// スライドイン/アウトにかける秒数。
const SLIDE_DUR = 0.5;

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
  zoomPunch: { scale: 1.14, duration: 0.18, borderStrength: 1 },
  quoteFreeze: { fadeIn: 0.14, fadeOutStart: 0.72, fadeOutDuration: 0.18, backdropOpacity: 0.22 },
  stampRain: { count: 8, fallDuration: 0.46, stagger: 0.05, spread: 1 },
  typingFlood: { rows: 9, flowDuration: 0.38, stagger: 0.035 },
  sparkleBurst: { count: 10, spread: 260, duration: 0.32 },
  irisOut: { start: 0.72, duration: 0.28, startRadius: 0.78 },
};

function resolveEffectSettings(settings?: StoryEffectSettings): ResolvedEffectSettings {
  return {
    zoomPunch: { ...DEFAULT_EFFECT_SETTINGS.zoomPunch, ...(settings?.zoomPunch || {}) },
    quoteFreeze: { ...DEFAULT_EFFECT_SETTINGS.quoteFreeze, ...(settings?.quoteFreeze || {}) },
    stampRain: { ...DEFAULT_EFFECT_SETTINGS.stampRain, ...(settings?.stampRain || {}) },
    typingFlood: { ...DEFAULT_EFFECT_SETTINGS.typingFlood, ...(settings?.typingFlood || {}) },
    sparkleBurst: { ...DEFAULT_EFFECT_SETTINGS.sparkleBurst, ...(settings?.sparkleBurst || {}) },
    irisOut: { ...DEFAULT_EFFECT_SETTINGS.irisOut, ...(settings?.irisOut || {}) },
  };
}

function mergeEffectSettings(
  base?: StoryEffectSettings,
  override?: StoryEffectSettings
): StoryEffectSettings | undefined {
  if (!base && !override) return undefined;
  return {
    zoomPunch: { ...(base?.zoomPunch || {}), ...(override?.zoomPunch || {}) },
    quoteFreeze: { ...(base?.quoteFreeze || {}), ...(override?.quoteFreeze || {}) },
    stampRain: { ...(base?.stampRain || {}), ...(override?.stampRain || {}) },
    typingFlood: { ...(base?.typingFlood || {}), ...(override?.typingFlood || {}) },
    sparkleBurst: { ...(base?.sparkleBurst || {}), ...(override?.sparkleBurst || {}) },
    irisOut: { ...(base?.irisOut || {}), ...(override?.irisOut || {}) },
  };
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
}> = ({ active, progress, width, height, settings }) => {
  const layers: React.ReactNode[] = [];
  const effectSettings = resolveEffectSettings(mergeEffectSettings(settings, active.effectSettings));
  const zoomPunchCfg = effectSettings.zoomPunch;
  const quoteFreezeCfg = effectSettings.quoteFreeze;
  const stampRainCfg = effectSettings.stampRain;
  const typingFloodCfg = effectSettings.typingFlood;
  const sparkleBurstCfg = effectSettings.sparkleBurst;
  const irisOutCfg = effectSettings.irisOut;
  const dur = Math.max(active.end - active.start, 0.001);
  const burstIn = clamp(progress / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
  const burstOut = 1 - clamp((progress - 0.58) / 0.22, 0, 1);

  if (active.zoomPunch) {
    const local = clamp(progress / Math.max(zoomPunchCfg.duration, 0.001), 0, 1);
    const punch = Math.sin(local * Math.PI);
    const scale = 1 + Math.max(0, zoomPunchCfg.scale - 1) * punch;
    layers.push(
      <AbsoluteFill
        key="zoomPunch"
        style={{
          pointerEvents: "none",
          boxShadow: `inset 0 0 0 ${Math.round(18 * punch * zoomPunchCfg.borderStrength)}px rgba(255,255,255,${0.11 * punch * zoomPunchCfg.borderStrength})`,
          transform: `scale(${scale})`,
          transformOrigin: "center center",
        }}
      />
    );
  }

  if (active.impactText) {
    const scale = lerp(1.7, 1, easeOutBack(burstIn));
    const opacity = clamp(Math.min(burstIn * 1.1, burstOut), 0, 1);
    layers.push(
      <AbsoluteFill
        key="impactText"
        style={{ pointerEvents: "none", alignItems: "center", justifyContent: "center" }}
      >
        <div
          style={{
            transform: `translateY(${-height * 0.02}px) scale(${scale}) rotate(-2deg)`,
            opacity,
            padding: "20px 42px",
            borderRadius: 22,
            color: "#fff6cf",
            background: "linear-gradient(135deg, rgba(176,31,31,0.92), rgba(245,142,42,0.92))",
            border: "5px solid rgba(255,245,202,0.96)",
            boxShadow: "0 18px 44px rgba(0,0,0,0.42)",
            fontFamily: EXTRA_EFFECT_FONT,
            fontWeight: 900,
            fontSize: Math.round(Math.min(width * 0.07, 110)),
            lineHeight: 1.1,
            letterSpacing: "0.08em",
            textAlign: "center",
            whiteSpace: "pre-wrap",
          }}
        >
          {active.impactText}
        </div>
      </AbsoluteFill>
    );
  }

  if (active.quoteFreeze) {
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

  if (active.stampRain) {
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

  if (active.typingFlood) {
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

  if (active.sparkleBurst) {
    const opacity = clamp(Math.min(progress / 0.14, 1 - Math.max(0, progress - 0.68) / 0.2), 0, 1);
    const sparkCount = Math.max(4, Math.min(20, Math.round(sparkleBurstCfg.count)));
    const sparks = Array.from({ length: sparkCount }, (_, i) => {
      const angle = (Math.PI * 2 * i) / sparkCount;
      const dist = lerp(40, sparkleBurstCfg.spread, easeOutCubic(clamp(progress / Math.max(sparkleBurstCfg.duration, 0.05), 0, 1)));
      const x = width * 0.5 + Math.cos(angle) * dist;
      const y = height * 0.45 + Math.sin(angle) * dist * 0.72;
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

  if (active.irisOut) {
    const p = clamp((progress - irisOutCfg.start) / Math.max(irisOutCfg.duration, 0.05), 0, 1);
    if (p > 0) {
      const radius = lerp(Math.max(width, height) * irisOutCfg.startRadius, 0, easeInOutCubic(p));
      layers.push(
        <AbsoluteFill
          key="irisOut"
          style={{
            pointerEvents: "none",
            background: "#000",
            maskImage: `radial-gradient(circle ${radius}px at 50% 50%, transparent 0, transparent ${Math.max(
              radius - 1,
              0
            )}px, #000 ${radius}px, #000 100%)`,
            WebkitMaskImage: `radial-gradient(circle ${radius}px at 50% 50%, transparent 0, transparent ${Math.max(
              radius - 1,
              0
            )}px, #000 ${radius}px, #000 100%)`,
          }}
        />
      );
    }
  }

  return layers.length ? <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden" }}>{layers}</AbsoluteFill> : null;
};

// ─── PC画面インサートコンポーネント ─────────────────────────

const INSERT_BG = "#11151c";
const DEFAULT_INSERT_WIDTH = 1;

function insertWidthScale(insert: { width?: number } | null | undefined): number {
  return clamp(insert?.width ?? DEFAULT_INSERT_WIDTH, 0.6, 1.4);
}

/** 警告ダイアログ */
const InsertWarning: React.FC<{ insert: Extract<StoryInsert, { kind: "warning" }> }> = ({ insert }) => {
  const title = insert.title ?? "警告";
  const panelWidth = Math.round(860 * insertWidthScale(insert));
  return (
    <div
      style={{
        background: "#1a1d24",
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
          fontSize: 26,
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
        <span style={{ fontSize: 56, lineHeight: 1, color: "#e87b3a" }}>⚠</span>
        <span
          style={{
            fontSize: 44,
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
          fontSize: 48,
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
  return (
    <div
      style={{
        background: "#181c24",
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
            fontSize: 34,
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
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <div
            style={{
              background: "#4a9e44",
              color: "#fff",
              borderRadius: "18px 4px 18px 18px",
              padding: "16px 24px",
              fontSize: 38,
              fontFamily: "sans-serif",
              lineHeight: 1.5,
              maxWidth: "76%",
              fontWeight: 500,
              whiteSpace: "pre-wrap",
            }}
          >
            {insert.user}
          </div>
        </div>
        {/* AI返答（左寄せ）。複数行。highlight 番号の吹き出しを強調。 */}
        {insert.ai.map((msg, i) => {
          const isHighlighted = typeof insert.highlight === "number" && insert.highlight === i;
          return (
            <div key={i} style={{ display: "flex", justifyContent: "flex-start" }}>
              <div
                style={{
                  background: isHighlighted ? "#2c1a08" : "#232a38",
                  color: isHighlighted ? "#f0e0c0" : "#c8d0e0",
                  borderRadius: "4px 18px 18px 18px",
                  padding: "16px 24px",
                  fontSize: 38,
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
  return (
    <div
      style={{
        background: "#141e18",
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
          fontSize: 26,
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
          fontSize: 96,
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
          fontSize: 56,
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
      imgSrc: staticFile(m.images.normal),
      name: from,
      nameColor: DEFAULT_BUBBLE_COLOR,
    };
  }
  return { imgSrc: null, name: from, nameColor: DEFAULT_BUBBLE_COLOR };
}

/** 社内チャット（ZunChat・Slack風） */
const InsertTeamChat: React.FC<{ insert: Extract<StoryInsert, { kind: "teamchat" }> }> = ({
  insert,
}) => {
  const channel = insert.channel ?? "general";
  const panelWidth = Math.round(1480 * insertWidthScale(insert));
  return (
    <div
      style={{
        background: "#f6f8fc",
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
            fontSize: 48,
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
        <span style={{ fontSize: 28, color: "#3f6a2a", fontFamily: "sans-serif", fontWeight: 600 }}>
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
                  flexShrink: 0,
                  background: imgSrc ? "transparent" : nameColor,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {imgSrc ? (
                  <img
                    src={imgSrc}
                    onError={(e) => {
                      const el = e.currentTarget as HTMLImageElement;
                      el.style.display = "none";
                      // 親の div を nameColor で塗りつぶす（onError 後は img 非表示）
                      if (el.parentElement) {
                        el.parentElement.style.background = nameColor;
                      }
                    }}
                    style={{
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
                    fontSize: 40,
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
                    fontSize: 54,
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
  // アバター色は headletter のコードポイントから決定論的に選ぶ（固定色・ランダムでない）
  const AVATAR_COLORS = ["#1a73e8", "#0f9d58", "#f4511e", "#8430ce", "#188038", "#d50000"];
  const avatarColor = AVATAR_COLORS[fromName.charCodeAt(0) % AVATAR_COLORS.length];

  return (
    <div
      style={{
        background: "#f6f8fc",
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
        <span style={{ fontSize: 34, color: "#5fb84f", lineHeight: 1 }}>✉</span>
        <span
          style={{
            fontSize: 32,
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
            fontSize: 56,
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
              fontSize: 34,
              fontWeight: 700,
            }}
          >
            {initial}
          </div>
          {/* 差出人名・アドレス */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span style={{ fontSize: 38, fontWeight: 600, color: "#202124" }}>
                {fromName}
              </span>
              {insert.fromAddr ? (
                <span style={{ fontSize: 30, color: "#80868b" }}>
                  {"<"}{insert.fromAddr}{">"}
                </span>
              ) : null}
            </div>
          </div>
          {/* 時刻（右端） */}
          {insert.time ? (
            <div style={{ fontSize: 32, color: "#80868b", flexShrink: 0 }}>
              {insert.time}
            </div>
          ) : null}
        </div>

        {/* 区切り線 */}
        <div style={{ height: 1, background: "#e0e0e0" }} />

        {/* 本文 */}
        <div
          style={{
            fontSize: 42,
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
                fontSize: 28,
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
      src: staticFile(m.images.normal),
      label: speaker,
      accent: "#d7e56d",
    };
  }
  return { kind: "unknown", label: speaker, accent: "#9ed957" };
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
    const bg = videoBgStyle(participant.bgStyle || (participant.speaker === "AI" ? "ai" : "office"));
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
const InsertOverlay: React.FC<{
  insert: StoryInsert;
  bgOpacity: number;
  opacity: number;
  transform?: string;
  activeSpeaker?: string;
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
  renderVideoCallFeed,
}) => {
  // mailer だけライトテーマ（白背景）。それ以外はダーク背景。
  const isLight = insert.kind === "mailer";
  return (
    <AbsoluteFill
      style={{
        background: isLight ? "#e8eaf0" : INSERT_BG,
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
      <AbsoluteFill
        style={{ opacity, alignItems: "center", justifyContent: "center" }}
      >
        {insert.kind === "warning" && <InsertWarning insert={insert} />}
        {insert.kind === "chat" && <InsertChat insert={insert} />}
        {insert.kind === "ok" && <InsertOk insert={insert} />}
        {insert.kind === "teamchat" && <InsertTeamChat insert={insert} />}
        {insert.kind === "mailer" && <InsertMailer insert={insert} />}
        {insert.kind === "videocall" && <InsertVideoCall insert={insert} activeSpeaker={activeSpeaker} renderFeed={renderVideoCallFeed} />}
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

function manualBubbleSplitTexts(text: string | null | undefined): string[] {
  return String(text ?? "")
    .split(/\r?\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function bubbleSentenceTexts(turn: StoryTurn): string[] {
  if (turn.disableAutoBubbleSplit) return [turn.text];
  const manualSplits = manualBubbleSplitTexts(turn.text);
  if (manualSplits.length > 1) return manualSplits;
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
  const manualSplits = manualBubbleSplitTexts(turn.text);
  if (manualSplits.length > 1) return manualSplits.length;
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
    if (turn.emphasis === true) {
      focusStart = turn.start;
    }
  }
  return focusStart;
}

function resolveCameraEffectRange(
  script: StoryTurn[],
  activeIdx: number,
): { effect: NonNullable<StoryTurn["cameraEffect"]>; start: number; end: number; settleEnd: number } | null {
  const activeTurn = script[activeIdx];
  const effect = activeTurn?.cameraEffect;
  if (!activeTurn || !effect || isNarrationTurn(activeTurn)) return null;
  let startIdx = activeIdx;
  let endIdx = activeIdx;
  while (startIdx > 0) {
    const prevTurn = script[startIdx - 1];
    if (!prevTurn || isNarrationTurn(prevTurn) || prevTurn.cameraEffect !== effect) break;
    startIdx -= 1;
  }
  while (endIdx < script.length - 1) {
    const nextTurn = script[endIdx + 1];
    if (!nextTurn || isNarrationTurn(nextTurn) || nextTurn.cameraEffect !== effect) break;
    endIdx += 1;
  }
  return {
    effect,
    start: script[startIdx].start,
    end: script[endIdx].end,
    settleEnd: Math.min(script[startIdx].end, script[startIdx].start + CAMERA_EFFECT_SETTLE_SEC),
  };
}

function bubbleBottomOffset(turn: StoryTurn, hasNextContinue: boolean): number {
  if (hasNextContinue) return 112;
  return turn.continueBubble ? 12 : 36;
}

function bubbleFontSize(text: string, stacked: boolean): number {
  return stacked ? 52 : 54;
}

function bubbleSide(x: number, width: number): "left" | "right" {
  if (x >= width * 0.52) return "right";
  return "left";
}

function bubbleMetrics(text: string, stacked: boolean, maxWidth: number) {
  const fontSize = bubbleFontSize(text, stacked);
  const chars = String(text || "").replace(/\s+/g, "").length;
  const estTextWidth = chars * fontSize * 0.98;
  const width = Math.max(120, estTextWidth + 66);
  return { fontSize, width };
}

// 仮想カメラの目標（s=ズーム / cx,cy=注視点・ステージ正規化座標）。
type Cam = { s: number; cx: number; cy: number };

function targetCam(
  chars: string[],
  anchorOf: Record<string, string>,
  sceneDef: SceneDef
): Cam {
  const ax = (c: string) =>
    (sceneDef.anchors[anchorOf[c] ?? "center"] ?? { x: 0.5 }).x;
  if (chars.length <= 1) {
    if (sceneDef.soloZoom === false) {
      return { s: 1.0, cx: chars[0] ? ax(chars[0]) : 0.5, cy: 0.5 };
    }
    const soloZoomScale = sceneDef.soloZoomScale ?? 1.4;
    const soloZoomCy = sceneDef.soloZoomCy ?? 0.58;
    // 単独：その人に寄る（背景もアップ）。cy を下げ気味にして胴まで見せ、
    // 顔を画面上側に置く＝足元の吹き出しスペースを確保する。
    return { s: soloZoomScale, cx: chars[0] ? ax(chars[0]) : 0.5, cy: soloZoomCy };
  }
  // 複数：全員が収まる引き。
  const xs = chars.map(ax);
  return { s: 1.0, cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: 0.5 };
}

// ─── BGMレイヤー ────────────────────────────────────────────
// BGM。story.bgm(時間ベース区間)があればそれを再生（隙間=無音）。無ければシーン連動。
const BgmLayer: React.FC<{
  script: StoryTurn[];
  scenes: SceneLibrary;
  bgmRegions?: BgmRegion[];
  fps: number;
}> = ({ script, scenes, bgmRegions, fps }) => {
  type BgmInfo = { file: string; volume: number; fadeIn: number; fadeOut: number };
  const BGM_DEFAULT_VOL = 0.25;
  const BGM_DEFAULT_FADE = 0.6;
  const isFiniteNumber = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);

  // フォールバック用: シーン連動BGMの解決。
  const turnBgm = (_turnIdx: number, turn: StoryTurn): BgmInfo | null => {
    const sceneDef = scenes.scenes[turn.scene];
    if (!sceneDef?.bgm) return null;
    return {
      file: sceneDef.bgm,
      volume: sceneDef.bgmVolume ?? BGM_DEFAULT_VOL,
      fadeIn: BGM_DEFAULT_FADE,
      fadeOut: BGM_DEFAULT_FADE,
    };
  };

  type BgmSegment = {
    file: string;
    volume: number;
    fadeIn: number;
    fadeOut: number;
    startSec: number;
    endSec: number;
  };

  let validSegs: BgmSegment[];
  if (bgmRegions && bgmRegions.length > 0) {
    // ── 時間ベース（タイムライン編集）── これが唯一の真実。隙間=無音。
    validSegs = bgmRegions
      .filter((r) => r.file && isFiniteNumber(r.start) && isFiniteNumber(r.end) && r.end > r.start)
      .map((r) => ({
        file: r.file,
        volume: r.volume ?? BGM_DEFAULT_VOL,
        fadeIn: r.fadeIn ?? BGM_DEFAULT_FADE,
        fadeOut: r.fadeOut ?? BGM_DEFAULT_FADE,
        startSec: r.start,
        endSec: r.end,
      }));
  } else {
    // ── フォールバック: シーン連動BGMを per-turn で区間化 ──
    const segments: BgmSegment[] = [];
    for (let i = 0; i < script.length; i++) {
      const turn = script[i];
      const info = turnBgm(i, turn);
      if (!info) {
        segments.push({ file: "", volume: 0, fadeIn: 0, fadeOut: 0, startSec: 0, endSec: 0 });
        continue;
      }
      if (!isFiniteNumber(turn.start) || !isFiniteNumber(turn.end)) continue;
      const last = segments[segments.length - 1];
      if (last && last.file === info.file) {
        last.endSec = turn.end;
        last.fadeOut = info.fadeOut;
      } else {
        segments.push({
          file: info.file,
          volume: info.volume,
          fadeIn: info.fadeIn,
          fadeOut: info.fadeOut,
          startSec: turn.start,
          endSec: turn.end,
        });
      }
    }
    validSegs = segments.filter((s) => !!s.file);
  }

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

        return (
          <Sequence key={i} from={startFrame} durationInFrames={durFrames}>
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
}) => {
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
  const segments = buildSegments(script);
  const active = activeTurnAt(script, t);
  const activeProgress = clamp((t - active.start) / Math.max(active.end - active.start, 0.001), 0, 1);
  const activeIdx = script.findIndex((x) => x.id === active.id);
  const seg =
    segments.find((s) => s.turns.some((x) => x.id === active.id)) ??
    segments[0];
  const segIndex = segments.findIndex((s) => s === seg);
  const prevSeg = segIndex > 0 ? segments[segIndex - 1] : null;
  const nextSeg = segments[segIndex + 1];
  const sceneDef = scenes.scenes[active.scene];
  const activeInsert = activeIdx >= 0 ? effectiveInsertAt(script, activeIdx) : null;

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
        <BgmLayer script={script} scenes={scenes} bgmRegions={story.bgm} fps={fps} />
        <SeLayer script={script} seMap={seMap} fps={fps} />
        {active.scene ? <span>未登録シーン: {active.scene}</span> : null}
      </AbsoluteFill>
    );
  }

  const avScale = sceneDef.scale ?? 1.9;
  const bgBlur = Math.max(0, sceneDef.bgBlur ?? 0);
  const bgScale = bgBlur > 0 ? 1 + Math.min(bgBlur, 32) / 180 : 1;
  const flashbackBoundaryStarts = new Set<number>();
  for (let i = 1; i < script.length; i++) {
    if (!!script[i - 1].flashback !== !!script[i].flashback) {
      flashbackBoundaryStarts.add(script[i].start);
    }
  }
  const isFlashbackBoundaryStart = (at: number | undefined) =>
    at !== undefined && flashbackBoundaryStarts.has(at);

  // ── 立ち位置は区間中ずっと固定（後から登場する人ぶんも最初から確保） ──
  const roster = segmentRoster(seg);
  const anchorOfAt = (tb: number) => resolveAnchorMapAt(seg, roster, sceneDef, tb);
  const anchorOf = anchorOfAt(t);
  const entrance = entranceTimes(seg);
  const instantEnter = instantEnterChars(seg);
  const exit = exitTimes(seg);
  const exitDir = exitDirs(seg);
  const effectiveExitAt = (charId: string) => {
    const leaving = exit[charId];
    if (leaving === undefined) return undefined;
    if (exitDir[charId] === "instant" && nextSeg) {
      return Math.max(leaving, nextSeg.start);
    }
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
          : t < effectiveExitAt(c)! + SLIDE_DUR)
      )
  );

  // ── 仮想カメラ：登場/退場のたびに「寄り↔引き」を滑らかに遷移（カットしない） ──
  const TRANS = 0.8; // 遷移にかける秒数
  // 境界時刻＝登場時刻＋退場時刻（退場で人数が減ればカメラも寄りへ遷移する）。
  const times = [
    ...new Set([
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
  const Tcur = targetCam(presentAt(times[idx]), anchorOfAt(times[idx]), sceneDef);
  const Tprev = idx > 0 ? targetCam(presentAt(times[idx - 1]), anchorOfAt(times[idx - 1]), sceneDef) : Tcur;
  const k = idx > 0 ? easeInOutCubic(clamp((t - times[idx]) / TRANS, 0, 1)) : 1;
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
  let driftS = 1.0;
  if (sceneDef.camera !== "static" && getRemotionEnvironment().isRendering) {
    const segDur = Math.max(seg.end - seg.start, 0.001);
    const p = clamp((t - seg.start) / segDur, 0, 1);
    driftS = 1 + 0.05 * p;
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
  if (focusStart != null) {
    const anchorName = anchorOf[active.speaker] ?? "center";
    const speakerAnchor = sceneDef.anchors[anchorName] ?? { x: 0.5 };
    const focusTf = toTf({ s: tf.s + 0.3, cx: speakerAnchor.x, cy: 0.46 });
    // emphasis を立てた時点から 0.5s でイーズインし、その後は話者交代まで維持する。
    const focusK = easeInOutCubic(clamp((t - focusStart) / 0.5, 0, 1));
    focusBubbleK = focusK;
    tf = {
      tx: lerp(tf.tx, focusTf.tx, focusK),
      ty: lerp(tf.ty, focusTf.ty, focusK),
      s: lerp(tf.s, focusTf.s, focusK),
    };
  }

  // 3. 単発カメラ効果（その行だけ付ける軽い引き/パン）。
  let stageRotateDeg = 0;
  const cameraEffectRange = activeIdx >= 0 ? resolveCameraEffectRange(script, activeIdx) : null;
  if (cameraEffectRange) {
    const effectDur = Math.max(cameraEffectRange.settleEnd - cameraEffectRange.start, 0.001);
    const effectK = easeInOutCubic(clamp((t - cameraEffectRange.start) / effectDur, 0, 1));
    const baseTf = tf;
    if (cameraEffectRange.effect === "pull-out") {
      const effectCam = toTf({
        s: Math.max(1, baseTf.s - 0.12),
        cx: Tcur.cx,
        cy: Tcur.cy,
      });
      tf = {
        tx: lerp(baseTf.tx, effectCam.tx, effectK),
        ty: lerp(baseTf.ty, effectCam.ty, effectK),
        s: lerp(baseTf.s, effectCam.s, effectK),
      };
    } else if (cameraEffectRange.effect === "pan-left" || cameraEffectRange.effect === "pan-right") {
      const dir = cameraEffectRange.effect === "pan-left" ? -1 : 1;
      const panRoom = Math.max(0, -width * (1 - baseTf.s));
      const panAmount = Math.min(panRoom * 0.22, width * 0.08) * dir;
      const targetTx = clamp(baseTf.tx + panAmount, width * (1 - baseTf.s), 0);
      tf = {
        tx: lerp(baseTf.tx, targetTx, effectK),
        ty: baseTf.ty,
        s: baseTf.s,
      };
    } else if (cameraEffectRange.effect === "tilt-left" || cameraEffectRange.effect === "tilt-right") {
      const dir = cameraEffectRange.effect === "tilt-left" ? -1 : 1;
      stageRotateDeg = 2.4 * dir * effectK;
      const targetS = Math.max(baseTf.s, 1.04);
      tf = {
        tx: baseTf.tx,
        ty: baseTf.ty,
        s: lerp(baseTf.s, targetS, effectK),
      };
    }
  }

  // 4. カメラシェイク（shake===true のターン中、減衰振動オフセットを translate に加算）。
  // s=1.0 など余裕ゼロのシーンでも振幅が出るよう、shake 中はスケールを最低 1.02 に嵩上げ。
  let stageS = tf.s;
  let stageTx = tf.tx;
  let stageTy = tf.ty;
  let shakeX = 0;
  let shakeY = 0;
  if (active.shake) {
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

  // shakeX/Y を加算した最終値をスケール clamp 内に収める（黒縁が出ないよう）。
  const sfx = clamp(stageTx + shakeX, width * (1 - stageS), 0);
  const sfy = clamp(stageTy + shakeY, height * (1 - stageS), 0);

  const stageTransform = `translate(${sfx}px, ${sfy}px) scale(${stageS}) rotate(${stageRotateDeg}deg)`;
  const insertShakeTransform = active.shake
    ? `translate(${shakeX}px, ${shakeY}px) scale(1.02)`
    : undefined;

  // ── 場面切り替え演出 ──────────────────────────────────────
  // 片側の秒数（総遷移 = 2×FADE）。フェードはゆったり、ワイプ/スライドは機敏に。
  const FADE_BY_TRANSITION: Record<string, number> = {
    "fade-black": 0.38,
    "fade-white": 0.38,
    "wipe-left": 0.22,
    "wipe-right": 0.22,
    "slide-left": 0.22,
    "slide-right": 0.22,
  };
  const DEFAULT_FADE = 0.3;
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
          key: `next-${nextSeg.start}`,
        };
        break;
      case "wipe-right":
        currentStageClipPath = `inset(0 ${exitProgress * 100}% 0 0)`;
        incomingPlate = {
          sceneDef: nextSceneDef,
          clipPath: `inset(0 0 0 ${(1 - exitProgress) * 100}%)`,
          key: `next-${nextSeg.start}`,
        };
        break;
      case "slide-left":
        currentStageShiftX = width * exitProgress;
        incomingPlate = {
          sceneDef: nextSceneDef,
          shiftX: -width * (1 - exitProgress),
          key: `next-slide-${nextSeg.start}`,
        };
        break;
      case "slide-right":
        currentStageShiftX = -width * exitProgress;
        incomingPlate = {
          sceneDef: nextSceneDef,
          shiftX: width * (1 - exitProgress),
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
    const anchorName = anchorOf[charId] ?? "center";
    const anchor = sceneDef.anchors[anchorName] ?? { x: 0.5, y: 1.02 };
    const isSpeaker = !isNarrationTurn(active) && charId === active.speaker;
    const lipsyncEnabled = isSpeaker && !active.noLipSync;
    // 向き: 台本の face 指定 > x座標からの自動（中央を向く）。
    // 立ち絵素材は「画面左向き」が素なので、右を向かせるときだけ反転する。
    // 画面左半分(x<0.5)のキャラは右＝中央向き、右半分は左＝中央向き。x を動かせば向きも自動追従。
    const want: "left" | "right" =
      active.face?.[charId] ?? (anchor.x < 0.5 ? "right" : "left");
    const flip = want === "right";
    // 未知の表情キーは "normal" にフォールバック（組み込み5種のモーションを維持）。
    const resolvedExpr = active.expression ?? "normal";
    const emotion = EXPRESSION_TO_EMOTION[resolvedExpr] ?? EXPRESSION_TO_EMOTION["normal"];

    // expressions.json が渡されていれば該当表情の ExpressionCfg を解決する。
    // 未知キーは "normal" にフォールバック（クラッシュ防止）。
    const exprKey = active.expression ?? "normal";
    const charKey = cdef.avatar; // "zundamon" / "metan"
    const charExprs = expressions?.[charKey];
    const baseExpressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
    const poseCfg = active.pose ? poses?.[charKey]?.[active.pose] ?? null : null;
    const expressionCfg =
      baseExpressionCfg && active.pose
        ? { ...baseExpressionCfg, pose: active.pose }
        : baseExpressionCfg;

    // 非話者(聞き役)の表情。story.idleFace==="hold" のとき「現時刻以前で自分が最後に
    // 話したターンの表情」を保持する（surprise/panic は一瞬の反応なので normal へ）。
    // 既定(normal/未指定)は常に normal(真顔)。
    let idleExprKey = "normal";
    if (story.idleFace === "hold") {
      for (const tn of script) {
        if (tn.start > t) break;
        if (!isNarrationTurn(tn) && tn.speaker === charId && tn.expression) idleExprKey = tn.expression;
      }
      if (idleExprKey === "surprise" || idleExprKey === "panic") idleExprKey = "normal";
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
    const entered = entrance[charId] ?? seg.start;
    const isInitial = entered <= seg.start + 1e-6;
    const entersInstantly = !!instantEnter[charId] || isFlashbackBoundaryStart(entered);
    let slideOffsetPx = 0;
    if (!isInitial && !entersInstantly) {
      const sp = clamp((t - entered) / SLIDE_DUR, 0, 1); // 0.5秒で着地
      const e = easeOutCubic(sp);
      const fromXNorm = anchor.x < 0.5 ? -0.35 : 1.35; // 画面外（自分側）から
      slideOffsetPx = (1 - e) * (fromXNorm - anchor.x) * width;
    }
    // 退場：exit 時刻になったら自分の側へスライドアウト（0.5秒で画面外へ）。
    const leaving = effectiveExitAt(charId);
    const exitsInstantly = exitDir[charId] === "instant" || isFlashbackBoundaryStart(leaving);
    if (leaving !== undefined && t >= leaving && !exitsInstantly) {
      const sp = clamp((t - leaving) / SLIDE_DUR, 0, 1);
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

  // モブ（1枚絵）描画：話者がモブのとき、その間だけ立たせる（フェードイン）。
  // 画像が無ければ onError で非表示にし、render を壊さない（素材未配置でも安全）。
  const renderMob = (mobId: string) => {
    const m = MOBS[mobId];
    if (!m) return null;
    // 配置はシーンデータ(scene.mobs[mob])優先 → MobDef既定 → シーン既定。
    const place = sceneDef.mobs?.[mobId];
    if (place?.hidden) return null; // 立ち絵を非表示（チャット/音声のみ登場）
    const a = place ?? m.anchor ?? sceneDef.mobAnchor ?? { x: 0.5, y: 1.0 };
    const sc = place?.scale ?? m.scale ?? 1;
    const h = (sceneDef.mobHeight ?? 760) * sc;
    const inP = clamp((t - active.start) / 0.3, 0, 1); // 話し始めでフェードイン
    return (
      <div
        key={`mob-${mobId}`}
        style={{
          position: "absolute",
          left: a.x * width,
          top: a.y * height,
          transform: `translate(-50%, -100%) scaleX(${m.flip ? -1 : 1})`,
          transformOrigin: "bottom center",
          opacity: inP,
        }}
      >
        <img
          src={staticFile(mobImage(mobId, active.expression))}
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
  const bubbleMaxWidth = width * 0.72;
  const zoomBubbleK = clamp((tf.s - 1) / 0.6, 0, 1);
  const followK = focusBubbleK * 0.45;
  const bubbleBoxStyle = (
    color: string,
    text: string,
    stacked: boolean,
    align: "left" | "right",
    widthPx: number
  ): React.CSSProperties => ({
    display: "inline-block",
    width: widthPx,
    boxSizing: "border-box",
    background: "#ffffff",
    color: "#1b1b1f",
    padding: "14px 28px",
    borderRadius: 18,
    border: `5px solid ${color}`,
    fontSize: bubbleMetrics(text, stacked, bubbleMaxWidth).fontSize,
    lineHeight: 1.3,
    fontWeight: 700,
    fontFamily: "sans-serif",
    boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
    textAlign: align,
    whiteSpace: "nowrap",
  });
  const bubbleGroupPlacement = (speaker: string, groupWidth: number) => {
    const aName = anchorOf[speaker] ?? "center";
    const a = sceneDef.anchors[aName] ?? { x: 0.5, y: 1.02 };
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
    const metrics = bubbleMetrics(text, stacked, bubbleMaxWidth);
    const { side, groupCenterX, top } = bubbleGroupPlacement(turn.speaker, metrics.width);
    const sx = side === "right"
      ? groupCenterX + metrics.width / 2
      : groupCenterX - metrics.width / 2;
    const color = CHARACTERS[turn.speaker]?.bubbleColor ?? DEFAULT_BUBBLE_COLOR;
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: sx,
          top: top - bottomOffset,
          transform: side === "right" ? "translate(-100%, -100%)" : "translate(0, -100%)",
          ...bubbleBoxStyle(color, text, stacked, side, metrics.width),
        }}
      >
        {text}
      </div>
    );
  };
  const renderBubbleGroup = (
    speaker: string,
    texts: string[],
    visibleCount: number,
    key: string
  ) => {
    const metrics = texts.map((text) => bubbleMetrics(text, true, bubbleMaxWidth));
    const groupWidth = metrics.reduce((max, item) => Math.max(max, item.width), 120);
    const { side, groupCenterX, top } = bubbleGroupPlacement(speaker, groupWidth);
    const color = CHARACTERS[speaker]?.bubbleColor ?? DEFAULT_BUBBLE_COLOR;
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
              ...bubbleBoxStyle(color, text, true, side, metrics[idx].width),
              transform: `translateX(${idx * bubbleStepX}px)`,
              visibility: idx < visibleCount ? "visible" : "hidden",
            }}
          >
            {text}
          </div>
        ))}
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
      const idx = script.findIndex((x) => x.id === turn.id);
      return !isInsertLineKind(insertAtIdx(idx));
    });
  const autoBubbleTexts = bubbleSentenceTexts(active);
  const shouldShowAutoBubbleGroup =
    !shouldShowBubbleGroup &&
    !activeInsertLine &&
    autoBubbleTexts.length > 1;
  const autoBubbleVisibleCount = bubbleSentenceVisibleCount(active, t);

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

  // 現在時刻から最も近い境界を探す（白ディゾルブの基準）。
  const nearestBoundary = fbBoundaries.reduce<FbBoundary | null>((best, b) => {
    if (best === null) return b;
    return Math.abs(t - b.at) < Math.abs(t - best.at) ? b : best;
  }, null);

  // 白ディゾルブのオーバーレイ opacity（三角波: 境界中心で1、±FB_DISSOLVE_SEC で 0）。
  let whiteFadeOpacity = 0;
  if (nearestBoundary !== null) {
    const dt = Math.abs(t - nearestBoundary.at);
    if (dt < FB_DISSOLVE_SEC) {
      whiteFadeOpacity = clamp(1 - dt / FB_DISSOLVE_SEC, 0, 1);
    }
  }
  // 白ディゾルブが出る間は黒フェードを抑制する（両立させると汚くなるため）。
  const suppressBlackFade = whiteFadeOpacity > 0;
  const stageTransformWithShift = currentStageShiftX !== 0
    ? `translateX(${currentStageShiftX}px) ${stageTransform}`
    : stageTransform;
  // ZunMeet タイル用ライブフィード（カメラ映像風の合成）。
  // 全タイル共通の描画方式: bgStyle 背景（renderTile側）＋ここで立ち絵を下端中央に合成する。
  // 立ち絵の表示幅はタイル幅の一定割合＝フォーカス切替では「同じ映像のサイズと場所」だけが変わる。
  const renderVideoCallFeed = (
    participant: VideoCallParticipant,
    opts: { large: boolean; tileW: number }
  ): React.ReactNode => {
    const speaker = participant.speaker;
    const isSpeaking = !isNarrationTurn(active) && active.speaker === speaker;
    const dispW = opts.tileW * VC_FEED_AVATAR_FRAC;
    if (isKnownChar(speaker)) {
      const cdef = CHARACTERS[speaker];
      // 話者は現在ターンの表情/ポーズ/リップシンク、非話者は normal で待機。
      const exprKey = isSpeaking ? active.expression ?? "normal" : "normal";
      const emotion = EXPRESSION_TO_EMOTION[exprKey] ?? EXPRESSION_TO_EMOTION["normal"];
      const charExprs = expressions?.[cdef.avatar];
      const expressionCfg = charExprs?.[exprKey] ?? charExprs?.["normal"] ?? null;
      const poseCfg = isSpeaking && active.pose ? poses?.[cdef.avatar]?.[active.pose] ?? null : null;
      const k = dispW / AVATAR_BOX;
      return (
        <div
          style={{
            position: "absolute",
            left: "50%",
            // 下へはみ出させて胸元をクロップ＝顔に寄せる（タイル側 overflow:hidden）。
            bottom: -dispW * VC_FEED_BOTTOM_SHIFT,
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
      return (
        <img
          src={staticFile(mobImage(speaker, isSpeaking ? active.expression : undefined))}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
          style={{
            position: "absolute",
            left: "50%",
            bottom: 0,
            width: opts.tileW * VC_FEED_MOB_FRAC,
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

  const renderScenePlate = (
    plateSceneDef: SceneDef,
    key: string,
    opts?: { clipPath?: string; shiftX?: number; filter?: string }
  ) => {
    const plateBlur = Math.max(0, plateSceneDef.bgBlur ?? 0);
    const plateScale = plateBlur > 0 ? 1 + Math.min(plateBlur, 32) / 180 : 1;
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
        <Img
          src={staticFile(plateSceneDef.bg)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: plateBlur > 0 ? `blur(${plateBlur}px)` : undefined,
            transform: plateScale > 1 ? `scale(${plateScale})` : undefined,
            transformOrigin: "center center",
          }}
        />
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
  if (isFlashback) {
    const entered = [...fbBoundaries]
      .filter((b) => b.entering && b.at <= t + 1e-6)
      .pop();
    if (entered?.telop) {
      telopText = entered.telop;
      telopTurn = script.find((turn) => turn.start === entered.at) ?? null;
      telopOpacity = clamp((t - entered.at) / FB_TELOP_FADE, 0, 1);
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
  const telopX = typeof telopTurn?.telopX === "number" ? telopTurn.telopX : 0.045;
  const telopY = typeof telopTurn?.telopY === "number" ? telopTurn.telopY : 0.06;
  const telopSize = typeof telopTurn?.telopSize === "number" ? telopTurn.telopSize : 1;

  // 回想中はステージに彩度ダウン＋輝度微加の CSS filter を掛ける。
  const stageFilter = isFlashback
    ? `saturate(${FB_SATURATE}) brightness(${FB_BRIGHTNESS})`
    : undefined;

  // ── PC画面インサート フェード計算 ─────────────────────────
  // INSERT_FADE: フェードイン/アウトの片側秒数。
  const INSERT_FADE = 0.2;
  const activeOverlays = activeOverlaysAt(script, story.overlays, t);
  const normalOverlays = activeOverlays.filter((overlay) => !isOverInsertOverlay(overlay));
  const overInsertOverlays = activeOverlays.filter((overlay) => isOverInsertOverlay(overlay));
  const nextTurn2 = activeIdx < script.length - 1 ? script[activeIdx + 1] : null;
  // 隣のターンがインサートを持つか（種別問わず）。インサート同士の間は通常画面を出さない。
  const prevHasInsert = activeIdx > 0 && !!effectiveInsertAt(script, activeIdx - 1);
  const nextHasInsert = !!(nextTurn2 && effectiveInsertAt(script, activeIdx + 1));
  let insertOpacity = 0;     // パネル本体（in/out両方フェード）
  let insertBgOpacity = 0;   // 背景＝シーン隠し
  if (activeInsert) {
    // このインサートが画面に出ている終端＝次ターンの開始(無ければ自分のend)。
    // 空セリフのインサート(end==start)でも次ターンまで表示されるよう実効終端を使う。
    const dispEnd = nextTurn2 ? nextTurn2.start : active.end;
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
        script={script}
        scenes={scenes}
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
        })
        : null}
      {/* ステージ（背景＋キャラ＋前景を1枚として仮想カメラで撮る） */}
      <AbsoluteFill
        style={{
          transform: stageTransformWithShift,
          transformOrigin: "0 0",
          filter: stageFilter,
          clipPath: currentStageClipPath,
          overflow: "hidden",
        }}
      >
        {/* 背景（back） */}
        <Img
          src={staticFile(sceneDef.bg)}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: bgBlur > 0 ? `blur(${bgBlur}px)` : undefined,
            transform: bgScale > 1 ? `scale(${bgScale})` : undefined,
            transformOrigin: "center center",
          }}
        />

        {/* キャラ（back と front の間） */}
        {presentNow.map(renderAvatar)}

        {/* モブ（話者がモブのとき1枚絵を立たせる。素材未配置なら自動で非表示） */}
        {!isNarrationTurn(active) && isMob(active.speaker) ? renderMob(active.speaker) : null}

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

      {/* 補助画像オーバーレイ。screen基準なのでステージ変形の外で描画する。 */}
      {normalOverlays.length > 0 ? <StoryOverlayLayer overlays={normalOverlays} /> : null}

      {/* PC画面インサート（ステージより前面・吹き出しより後面）。
          z順: ステージ → インサート → 吹き出し */}
      {activeInsert && insertBgOpacity > 0 ? (
        <InsertOverlay
          insert={activeInsert}
          bgOpacity={insertBgOpacity}
          opacity={insertOpacity}
          transform={insertShakeTransform}
          activeSpeaker={active.speaker}
          renderVideoCallFeed={renderVideoCallFeed}
        />
      ) : null}

      {overInsertOverlays.length > 0 ? <StoryOverlayLayer overlays={overInsertOverlays} /> : null}

      {/* 吹き出し。continueBubble の連続区間は1グループとして積み、
          先の段数ぶんも最初から予約して位置を固定する。インサートより前面。 */}
      {/* セリフがチャット/AIチャット画面に出ているターンは吹き出しを出さない。
          前後ターンも各自の insert 種別で判定し、遷移時のチラ見え漏れを防ぐ。 */}
      {!isNarrationTurn(active) && shouldShowBubbleGroup
        ? renderBubbleGroup(
          bubbleGroup[0]?.speaker ?? active.speaker,
          bubbleGroup.map((turn) => bubbleTextAt(turn, t)),
          visibleGroupCount,
          `bubble-group-${bubbleGroup[0]?.id ?? active.id}`
        )
        : !isNarrationTurn(active) && shouldShowAutoBubbleGroup
          ? renderBubbleGroup(
            active.speaker,
            autoBubbleTexts,
            autoBubbleVisibleCount,
            `bubble-auto-group-${active.id}`
          )
        : (!isNarrationTurn(active) && !activeInsertLine
          ? renderBubble(active, "bubble-active", bubbleBottomOffset(active, false), !!active.continueBubble)
          : null)}

      <ExtraEffectsLayer
        active={active}
        progress={activeProgress}
        width={width}
        height={height}
        settings={story.effectSettings}
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

      {/* 場面切り替えのフェード被せ。flashbackの白ディゾルブ中は黒被せを抑制。 */}
      {transitionCoverColor && transitionCoverOpacity > 0 && !(transitionCoverColor === "#000" && suppressBlackFade) ? (
        <AbsoluteFill
          style={{ background: transitionCoverColor, opacity: transitionCoverOpacity, pointerEvents: "none" }}
        />
      ) : null}

      {/* 白ディゾルブ（flashback境界の出入り）。黒fadeより手前に重ねる。 */}
      {whiteFadeOpacity > 0 ? (
        <AbsoluteFill
          style={{ background: "#fff", opacity: whiteFadeOpacity, pointerEvents: "none" }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
