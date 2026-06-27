import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useAudioData } from "@remotion/media-utils";
import { Avatar } from "./Avatar";
import type { Emotion, Gender } from "./types";

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。DialogueVideo と同値。
const LIPSYNC_GAIN = 5;

// ─── 回想（flashback）演出の定数 ────────────────────────────
// 後で調整しやすいよう1箇所にまとめる。
const FB_SATURATE = 0.7;        // 回想中の彩度（1.0=元のまま・低いほど色が薄い）
const FB_BRIGHTNESS = 1.02;     // 回想中の輝度（微加）
const FB_GRAIN_OPACITY = 0.06;  // グレインの不透明度（0.0=なし・0.1で見えてくる）
const FB_DISSOLVE_SEC = 0.3;    // 白ディゾルブ片側の秒数（合計 2×FB_DISSOLVE_SEC）
const FB_TELOP_SEC = 1.2;       // テロップの表示秒数
const FB_TELOP_FADE = 0.25;     // テロップのフェードイン/アウト秒数

// ───────────────────────────────────────────────────────────
// ストーリー調 会話劇動画（新ツール Phase 1）の描画。
// 既存の DialogueVideo には触れず、立ち絵 Avatar だけ流用する。
// 入力は手書きの story-sample.json / story-scenes.json（固定入力）。
// 仕様: docs/next-animation-editor-spec.md §4。
// ───────────────────────────────────────────────────────────

// 台本の表情語彙（§4.3）。立ち絵 Emotion へマップする。
// trouble/panic は専用パーツ未配置のため当面は近い既存表情へ寄せる（Phase 5 でパーツ追加）。
export type StoryExpression =
  | "normal"
  | "happy"
  | "surprise"
  | "trouble"
  | "panic";

const EXPRESSION_TO_EMOTION: Record<StoryExpression, Emotion> = {
  normal: "normal",
  happy: "happy",
  surprise: "surprise",
  trouble: "sad", // 困り：暫定（専用パーツが無ければ通常目にフォールバック）
  panic: "panic", // 焦り：汗＋見開き目＋小刻みな揺れ
};

export type StorySentence = { text: string; start: number; end: number };

export type StoryTurn = {
  id: string;
  speaker: string;
  text: string;
  scene: string;
  expression?: StoryExpression;
  enter?: string[];
  // キャラの向き（画面のどちらを向くか）の明示指定。省略時は立ち位置から自動（中央向き）。
  // 例: { "zundamon": "left", "metan": "right" }
  face?: Record<string, "left" | "right">;
  // 話者プッシュイン演出（emphasis=true のターン中、話者寄りへズームイン）。
  emphasis?: boolean;
  // カメラシェイク演出（shake=true のターン中、ターン開始からの減衰振動オフセットを加算）。
  shake?: boolean;
  // 回想フラグ（true のターンが回想区間）。
  flashback?: boolean;
  // テロップ（境界付近で短時間表示する時代テキスト。例「― 前日 ―」）。
  telop?: string;
  // 台詞後の無音秒（音声生成で使用。描画では参照しない）。
  pause?: number;
  // 退場するキャラ。このターンの終わり（end）に自分の側へスライドアウトして以後は非表示。
  exit?: string[];
  start: number;
  end: number;
  sentences?: StorySentence[];
};

export type StoryScript = { title?: string; audio?: string; script: StoryTurn[] };

type Anchor = { x: number; y: number };

export type SceneDef = {
  label?: string;
  bg: string;
  front?: string | null;
  shot?: "solo" | "duo" | "split";
  camera?: "static" | "slow-zoom";
  scale?: number; // 立ち絵の拡大率（既定 1.9）
  anchors: Record<string, Anchor>;
  // どのキャラをどのアンカーに置くか（charId→アンカー名）。
  // 省略時は登場順で left/right を自動割当。
  cast?: Record<string, string>;
  // この場面に入るときの切り替え方式（省略時 fade-black）。今後 crossfade 等を追加予定。
  transition?: "fade-black" | "cut";
  // 立ち絵の画角。"bust"=バストアップ（既定・office）、"full"=全身（server_room/rooftop/home 等）。
  figure?: "bust" | "full";
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

type Manifest = Record<string, Record<string, string>>;

export type StoryVideoProps = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Manifest;
  audio?: string; // 音声ファイル（public配下・任意）
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

// scene 名が連続する範囲を 1 区間（segment）にまとめる（§4.1）。
type Segment = { scene: string; turns: StoryTurn[]; start: number; end: number };

function buildSegments(script: StoryTurn[]): Segment[] {
  const segs: Segment[] = [];
  for (const t of script) {
    const last = segs[segs.length - 1];
    if (last && last.scene === t.scene) {
      last.turns.push(t);
      last.end = Math.max(last.end, t.end);
    } else {
      segs.push({ scene: t.scene, turns: [t], start: t.start, end: t.end });
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

// segment 全体で登場する全キャラ（登場順）。立ち位置を区間中ずっと固定するため最終集合で決める。
// モブ（CHARACTERS 未定義）はレイアウトから除外する。
function segmentRoster(seg: Segment): string[] {
  const order: string[] = [];
  for (const turn of seg.turns) {
    for (const c of turn.enter ?? []) {
      if (isKnownChar(c) && !order.includes(c)) order.push(c);
    }
    if (isKnownChar(turn.speaker) && !order.includes(turn.speaker)) {
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
    if (isKnownChar(turn.speaker) && !(turn.speaker in e)) e[turn.speaker] = turn.start;
  }
  return e;
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

// スライドイン/アウトにかける秒数。
const SLIDE_DUR = 0.5;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const easeInOutCubic = (x: number) =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);

// その時刻に吹き出しへ出す文字列（sentences があれば文単位で小出し・§4.4）。
function bubbleTextAt(turn: StoryTurn, t: number): string {
  if (turn.sentences && turn.sentences.length) {
    const s =
      turn.sentences.find((x) => x.start <= t && t < x.end) ??
      turn.sentences[turn.sentences.length - 1];
    return s.text;
  }
  return turn.text;
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
    // 単独：その人に寄る（背景もアップ）。cy を下げ気味にして胴まで見せ、
    // 顔を画面上側に置く＝足元の吹き出しスペースを確保する。
    return { s: 1.4, cx: chars[0] ? ax(chars[0]) : 0.5, cy: 0.58 };
  }
  // 複数：全員が収まる引き。
  const xs = chars.map(ax);
  return { s: 1.0, cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: 0.5 };
}

export const StoryVideo: React.FC<StoryVideoProps> = ({
  story,
  scenes,
  manifest,
  audio,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;

  const script = story.script;
  const segments = buildSegments(script);
  const active = activeTurnAt(script, t);
  const seg =
    segments.find((s) => s.turns.some((x) => x.id === active.id)) ??
    segments[0];
  const sceneDef = scenes.scenes[active.scene];

  // ── 未登録シーンのフォールバック（§4.1：エラーで止めず分かるように出す） ──
  if (!sceneDef) {
    return (
      <AbsoluteFill
        style={{
          background: "#1b1b1f",
          color: "#fff",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 48,
          fontFamily: "sans-serif",
        }}
      >
        未登録シーン: {active.scene}
      </AbsoluteFill>
    );
  }

  const avScale = sceneDef.scale ?? 1.9;

  // ── 立ち位置は区間中ずっと固定（後から登場する人ぶんも最初から確保） ──
  const roster = segmentRoster(seg);
  // 登場順の自動割当を土台に、シーンの cast（charId→アンカー名）で上書き。
  const anchorOf = { ...assignAnchors(roster), ...(sceneDef.cast ?? {}) };
  const entrance = entranceTimes(seg);
  const exit = exitTimes(seg);
  // 表示中＝登場済み かつ（退場していない or 退場スライド中）。
  const presentNow = roster.filter(
    (c) =>
      entrance[c] <= t + 1e-6 &&
      (exit[c] === undefined || t < exit[c] + SLIDE_DUR)
  );

  // ── 仮想カメラ：登場/退場のたびに「寄り↔引き」を滑らかに遷移（カットしない） ──
  const TRANS = 0.8; // 遷移にかける秒数
  // 境界時刻＝登場時刻＋退場時刻（退場で人数が減ればカメラも寄りへ遷移する）。
  const times = [
    ...new Set([
      ...roster.map((c) => entrance[c]),
      ...Object.values(exit),
    ]),
  ].sort((a, b) => a - b);
  let idx = 0;
  for (let i = 0; i < times.length; i++) if (times[i] <= t + 1e-6) idx = i;
  // tb 時点で「画面にいる」キャラ＝登場済み かつ 退場時刻前。
  const presentAt = (tb: number) =>
    roster.filter(
      (c) => entrance[c] <= tb + 1e-6 && (exit[c] === undefined || tb + 1e-6 < exit[c])
    );
  const Tcur = targetCam(presentAt(times[idx]), anchorOf, sceneDef);
  const Tprev = idx > 0 ? targetCam(presentAt(times[idx - 1]), anchorOf, sceneDef) : Tcur;
  const k = idx > 0 ? easeInOutCubic(clamp((t - times[idx]) / TRANS, 0, 1)) : 1;
  const cam: Cam = {
    s: lerp(Tprev.s, Tcur.s, k),
    cx: lerp(Tprev.cx, Tcur.cx, k),
    cy: lerp(Tprev.cy, Tcur.cy, k),
  };
  // ── カメラ追加演出 ─────────────────────────────────────────
  // 1. 常時スロードリフト（slow-zoom）: sceneDef.camera !== "static" のとき区間中ずっと微速プッシュイン。
  let driftS = 1.0;
  if (sceneDef.camera !== "static") {
    const segDur = Math.max(seg.end - seg.start, 0.001);
    const p = clamp((t - seg.start) / segDur, 0, 1);
    driftS = 1 + 0.05 * p;
  }
  cam.s *= driftS;

  // 2 & 3. 話者プッシュイン（emphasis）＋リアクション寄り（surprise/panic）。
  // 既知キャラが話者で、emphasis===true または expression が surprise/panic のとき focus。
  const isFocusTurn =
    isKnownChar(active.speaker) &&
    (active.emphasis === true ||
      active.expression === "surprise" ||
      active.expression === "panic");
  if (isFocusTurn) {
    const anchorName = anchorOf[active.speaker] ?? "center";
    const speakerAnchor = sceneDef.anchors[anchorName] ?? { x: 0.5 };
    const focusTarget: Cam = {
      s: cam.s + 0.3,
      cx: speakerAnchor.x,
      cy: 0.46,
    };
    // ターン開始から 0.5s でイーズイン、ターン終了 0.5s 前からイーズアウト（台形）。
    const turnDur = active.end - active.start;
    const elapsed = t - active.start;
    const fadeInDur = Math.min(0.5, turnDur * 0.5);
    const fadeOutDur = Math.min(0.5, turnDur * 0.5);
    const inK = clamp(elapsed / fadeInDur, 0, 1);
    const outK = clamp((active.end - t) / fadeOutDur, 0, 1);
    const focusK = easeInOutCubic(Math.min(inK, outK));
    cam.s = lerp(cam.s, focusTarget.s, focusK);
    cam.cx = lerp(cam.cx, focusTarget.cx, focusK);
    cam.cy = lerp(cam.cy, focusTarget.cy, focusK);
  }

  // ステージ変換（端が見切れて黒が出ないようクランプ）。
  const tx = clamp(width / 2 - cam.cx * width * cam.s, width * (1 - cam.s), 0);
  const ty = clamp(height / 2 - cam.cy * height * cam.s, height * (1 - cam.s), 0);

  // 4. カメラシェイク（shake===true のターン中、減衰振動オフセットを translate に加算）。
  // 振幅 ~7px、周波数 ~16Hz、ターン開始からの経過で減衰（1→0）。
  // s=1.0 など余裕ゼロのシーンでも振幅が出るよう、shake アクティブ時はスケールを最低 1.02 に嵩上げ。
  // 黒縁が出ないよう、嵩上げ後の translate clamp 余裕内に振幅を制限する。
  let shakeX = 0;
  let shakeY = 0;
  if (active.shake) {
    // shake 中は最低 1.02 スケール確保（嵩上げ分でシェイク余裕を作る）。
    const shakeS = Math.max(cam.s, 1.02);
    const turnDur = Math.max(active.end - active.start, 0.001);
    const elapsed = t - active.start;
    const decayRaw = 1 - clamp(elapsed / turnDur, 0, 1);
    // スケール超過分がシェイク余裕（ステージが画面より shakeS-1 の割合だけ大きい）。
    // 各軸の余裕 = (shakeS - 1) * 画面サイズ / 2 で clamp 前に均等に使える。
    const availX = (shakeS - 1) * width * 0.5;
    const availY = (shakeS - 1) * height * 0.5;
    const maxAmp = Math.min(7, availX, availY);
    const amp = maxAmp * decayRaw;
    shakeX = amp * Math.sin(t * 2 * Math.PI * 16);
    shakeY = amp * Math.sin(t * 2 * Math.PI * 16 * 1.3 + 1);
    // 嵩上げ後のスケール・translate で描画する。
    cam.s = shakeS;
  }

  // shake も含めた最終 translate（shake 時は嵩上げ後 cam.s で再計算）。
  const finalTx = active.shake
    ? clamp(width / 2 - cam.cx * width * cam.s, width * (1 - cam.s), 0)
    : tx;
  const finalTy = active.shake
    ? clamp(height / 2 - cam.cy * height * cam.s, height * (1 - cam.s), 0)
    : ty;

  // shakeX/Y を加算した最終値をスケール clamp 内に収める（端がクランプ済みの場合に超過しないよう）。
  const sfx = clamp(finalTx + shakeX, width * (1 - cam.s), 0);
  const sfy = clamp(finalTy + shakeY, height * (1 - cam.s), 0);

  const stageTransform = `translate(${sfx}px, ${sfy}px) scale(${cam.s})`;

  // ── 場面切り替え演出（今は fade-black＝一瞬暗くする。後で方式を追加可能） ──
  // 区間境界＝場面切替なので、各区間の頭で暗→明、次区間の直前で明→暗にして黒で隠す。
  const FADE = 0.3; // 片側の秒数（総遷移 = 2×FADE）
  const segIndex = segments.findIndex((s) => s === seg);
  const nextSeg = segments[segIndex + 1];
  let fadeOpacity = 0;
  if ((sceneDef.transition ?? "fade-black") !== "cut" && seg.start > 1e-6) {
    // この場面に入った直後：黒→明
    fadeOpacity = Math.max(fadeOpacity, 1 - (t - seg.start) / FADE);
  }
  if (nextSeg) {
    const nextTrans = scenes.scenes[nextSeg.scene]?.transition ?? "fade-black";
    if (nextTrans !== "cut") {
      // 次の場面の直前：明→黒
      fadeOpacity = Math.max(fadeOpacity, 1 - (nextSeg.start - t) / FADE);
    }
  }
  fadeOpacity = clamp(fadeOpacity, 0, 1);

  // ── 話者の音量（実音声の波形RMS）→ リップシンク。DialogueVideo と同方式。 ──
  // 音声(story-01.wav)の現フレーム付近のRMSを口の開きに使う。発話者のみに適用。
  // useAudioData は string 必須のため、audio 未指定時もダミーで呼ぶ（フックは無条件呼び出し）。
  // 実際のリップシンクは audio がある時だけ行う。
  const audioData = useAudioData(staticFile(audio ?? "story-01.wav"));
  let speakerAmp = 0;
  if (audio && audioData) {
    const wave = audioData.channelWaveforms[0];
    const sr = audioData.sampleRate;
    const center = Math.floor(t * sr);
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
    const isSpeaker = charId === active.speaker;
    // 向き: 台本の face 指定 > x座標からの自動（中央を向く）。
    // 立ち絵素材は「画面左向き」が素なので、右を向かせるときだけ反転する。
    // 画面左半分(x<0.5)のキャラは右＝中央向き、右半分は左＝中央向き。x を動かせば向きも自動追従。
    const want: "left" | "right" =
      active.face?.[charId] ?? (anchor.x < 0.5 ? "right" : "left");
    const flip = want === "right";
    const emotion = EXPRESSION_TO_EMOTION[active.expression ?? "normal"];

    // 全身 or バスト の判定。figure="full" のとき全身パーツを使う。
    const isFull = (sceneDef.figure ?? "bust") === "full";
    const avatarDir = isFull ? `${cdef.avatar}/full` : cdef.avatar;
    const manifestKey = isFull ? `${cdef.avatar}_full` : cdef.avatar;
    const avatarManifest = manifest?.[manifestKey];
    const box = isFull ? fullBoxSize(cdef.avatar) : { w: AVATAR_BOX, h: AVATAR_BOX };

    // 途中で登場するキャラ（区間の頭からいる人ではない）は、自分の側からスライドイン。
    const entered = entrance[charId] ?? seg.start;
    const isInitial = entered <= seg.start + 1e-6;
    let slideOffsetPx = 0;
    if (!isInitial) {
      const sp = clamp((t - entered) / SLIDE_DUR, 0, 1); // 0.5秒で着地
      const e = easeOutCubic(sp);
      const fromXNorm = anchor.x < 0.5 ? -0.35 : 1.35; // 画面外（自分側）から
      slideOffsetPx = (1 - e) * (fromXNorm - anchor.x) * width;
    }
    // 退場：exit 時刻になったら自分の側へスライドアウト（0.5秒で画面外へ）。
    const leaving = exit[charId];
    if (leaving !== undefined && t >= leaving) {
      const sp = clamp((t - leaving) / SLIDE_DUR, 0, 1);
      const e = easeInOutCubic(sp);
      const toXNorm = anchor.x < 0.5 ? -0.35 : 1.35; // 画面外（自分側）へ
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
            amplitude={isSpeaker ? speakerAmp : 0}
            emotion={isSpeaker ? emotion : "normal"}
            emotionAtFrame={Math.round(active.start * fps)}
            expressive={!!cdef.expressive}
            flip={flip}
            popScale={false}
            boxWidth={box.w}
            boxHeight={box.h}
          />
        </div>
      </div>
    );
  };

  // 吹き出しは「移動後の最終カメラ(Tcur)」基準で位置を決め、移動中も固定表示する。
  // → 移動中に位置が動かない＝変形・ガタつき無し・途中で消えない。
  const stx = clamp(
    width / 2 - Tcur.cx * width * Tcur.s,
    width * (1 - Tcur.s),
    0
  );
  // 1つの吹き出しを描く（話者の足元・話者色）。
  // 下端を固定(translateY -100%)して上に伸ばす＝行が増えても画面下にはみ出ない。
  const renderBubble = (turn: StoryTurn, key: string) => {
    const aName = anchorOf[turn.speaker] ?? "center";
    const a = sceneDef.anchors[aName] ?? { x: 0.5, y: 1.02 };
    // 横幅が広いので、左右端で見切れないよう中心xを内側にクランプする。
    const sx = clamp(stx + a.x * width * Tcur.s, width * 0.31, width * 0.69);
    const color = CHARACTERS[turn.speaker]?.bubbleColor ?? DEFAULT_BUBBLE_COLOR;
    return (
      <div
        key={key}
        style={{
          position: "absolute",
          left: sx,
          top: height * 0.95, // 吹き出しの「下端」をこの位置に置き、上方向に伸ばす
          transform: "translate(-50%, -100%)",
          maxWidth: width * 0.56, // 横幅広め＝2行に収まりやすく
          background: "#ffffff",
          color: "#1b1b1f",
          padding: "18px 30px",
          borderRadius: 16,
          border: `5px solid ${color}`,
          fontSize: 42,
          lineHeight: 1.35,
          fontWeight: 700,
          fontFamily: "sans-serif",
          boxShadow: "0 6px 18px rgba(0,0,0,0.35)",
          textAlign: "center",
        }}
      >
        {bubbleTextAt(turn, t)}
      </div>
    );
  };

  // 直前のセリフ（別話者・同一シーン）は、相手が喋り出してから OVERLAP 秒だけ残して消す。
  const OVERLAP = 0.6;
  const activeIdx = script.findIndex((x) => x.id === active.id);
  const prevTurn = activeIdx > 0 ? script[activeIdx - 1] : null;
  const showPrev =
    !!prevTurn &&
    prevTurn.scene === active.scene &&
    prevTurn.speaker !== active.speaker &&
    t - active.start < OVERLAP;

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

  // グレインのシードをフレームごとに変えてちらつきを出す（軽量: 数px の位置オフセット）。
  const grainOffsetX = (frame * 7) % 64;
  const grainOffsetY = (frame * 13) % 64;

  // テロップ表示: 境界から FB_TELOP_SEC の間、フェードイン/アウトして出す。
  // 回想に入る境界: その境界の telop を使う。
  // 「現在」へ戻る境界: 戻り先のターンの telop。
  let telopText: string | null = null;
  let telopOpacity = 0;
  if (nearestBoundary?.telop) {
    const dt = t - nearestBoundary.at;
    if (dt >= -FB_TELOP_FADE && dt < FB_TELOP_SEC) {
      telopText = nearestBoundary.telop;
      if (dt < FB_TELOP_FADE) {
        // フェードイン
        telopOpacity = clamp((dt + FB_TELOP_FADE) / FB_TELOP_FADE, 0, 1);
      } else if (dt >= FB_TELOP_SEC - FB_TELOP_FADE) {
        // フェードアウト
        telopOpacity = clamp((FB_TELOP_SEC - dt) / FB_TELOP_FADE, 0, 1);
      } else {
        telopOpacity = 1;
      }
    }
  }

  // 回想中はステージに彩度ダウン＋輝度微加の CSS filter を掛ける。
  const stageFilter = isFlashback
    ? `saturate(${FB_SATURATE}) brightness(${FB_BRIGHTNESS})`
    : undefined;

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {audio ? <Audio src={staticFile(audio)} /> : null}
      {/* ステージ（背景＋キャラ＋前景を1枚として仮想カメラで撮る） */}
      <AbsoluteFill
        style={{
          transform: stageTransform,
          transformOrigin: "0 0",
          filter: stageFilter,
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
          }}
        />

        {/* キャラ（back と front の間） */}
        {presentNow.map(renderAvatar)}

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

      {/* 吹き出し。基本は話者の1つ。話者交代の直後だけ直前のセリフを少し残す（一瞬2つ）。
          位置は最終カメラ基準で固定＝移動中も消えず動かない。 */}
      {showPrev ? renderBubble(prevTurn as StoryTurn, "bubble-prev") : null}
      {renderBubble(active, "bubble-active")}

      {/* テロップ（回想境界付近：「― 前日 ―」「― 現在 ―」等）。ローワーサード風の帯。 */}
      {telopText && telopOpacity > 0 ? (
        <AbsoluteFill
          style={{
            alignItems: "flex-start",
            justifyContent: "center",
            paddingTop: height * 0.12,
            pointerEvents: "none",
          }}
        >
          <div
            style={{
              background: "rgba(10, 10, 10, 0.52)",
              color: "#f0ece4",
              fontSize: 44,
              fontWeight: 400,
              fontFamily: "sans-serif",
              letterSpacing: "0.18em",
              padding: "14px 48px",
              borderRadius: 4,
              opacity: telopOpacity,
            }}
          >
            {telopText}
          </div>
        </AbsoluteFill>
      ) : null}

      {/* 場面切り替えの暗転（fade-black）。白ディゾルブ中は抑制。 */}
      {fadeOpacity > 0 && !suppressBlackFade ? (
        <AbsoluteFill
          style={{ background: "#000", opacity: fadeOpacity, pointerEvents: "none" }}
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
