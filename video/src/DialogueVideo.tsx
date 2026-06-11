import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useAudioData } from "@remotion/media-utils";
import { Avatar } from "./Avatar";
import { FONT_FAMILY } from "./fonts";
import type { Emotion, Gender, Meta, Topic, Turn } from "./types";

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。素材/音声に合わせて調整。
const LIPSYNC_GAIN = 5;

// 中央ビジュアル枠の配置(px・1920x1080基準)。背景 bg.png の黒板の内縁に合わせた目測値。
// 黒板の前に画像/立ち絵が乗る構図。背景を差し替えたら npm run dev で見ながらここを微調整する。
const BOARD = { left: 252, right: 252, top: 70, bottom: 347 };

// 「実は」バッジのラベル。ネタ番号を丸数字で（1〜10は①②…、超えたら数字）。
function triviaLabel(idx?: number): string {
  if (!idx) return "実は";
  const circled = "①②③④⑤⑥⑦⑧⑨⑩";
  return "実は " + (idx >= 1 && idx <= 10 ? circled[idx - 1] : String(idx));
}

// 話者→性別を解決。名前には依存しない（キャラ名が変わっても壊れない）。
// ①meta.speakers の gender を優先 ②無ければ登場順（0番目=female, それ以外=male）
function resolveGender(meta: Meta, name: string, index: number): Gender {
  const fromMeta = meta.speakers?.find((s) => s.name === name)?.gender;
  if (fromMeta) return fromMeta;
  return index === 0 ? "female" : "male";
}

// 話者→立ち絵フォルダ名を解決。
// ①speaker.avatar を優先 ②既知キャラは名前から既定(ずんだもん/めたん) ③無ければnull(単一画像へ)
function resolveAvatarDir(meta: Meta, name: string): string | null {
  const sp = meta.speakers?.find((s) => s.name === name);
  if (sp?.avatar) return sp.avatar;
  if (name.includes("ずんだ")) return "zundamon";
  if (name.includes("めたん") || name.includes("メタン")) return "metan";
  return null;
}

// 話者がオーバーアクション持ちか。①speaker.expressive ②既定でずんだもん
function resolveExpressive(meta: Meta, name: string): boolean {
  const sp = meta.speakers?.find((s) => s.name === name);
  if (typeof sp?.expressive === "boolean") return sp.expressive;
  return name.includes("ずんだ");
}

// 立ち絵の左右反転。①speaker.flip ②立ち絵素材は画面左向きが素なので、
// 左配置のときは反転して中央(右)を向かせる。右配置は素のまま（中央=左を向く）。
function resolveFlip(meta: Meta, name: string, side: "left" | "right"): boolean {
  const sp = meta.speakers?.find((s) => s.name === name);
  if (typeof sp?.flip === "boolean") return sp.flip;
  return side === "left";
}

// 話者カラー。立ち絵dir優先（ずんだもん=緑/めたん=ピンク）、無ければ性別で割当。
// 字幕の名前タグ＝「誰が話しているか」の色分けに使う。
function speakerColor(dir: string | null, gender: Gender): string {
  if (dir === "zundamon") return "#3fa34d";
  if (dir === "metan") return "#d85a9c";
  return gender === "female" ? "#d85a9c" : "#3fa34d";
}

// 字幕背景：ほぼ白（話者ごとにごく薄く色味を足す）。発話者は枠色で示す＝色は強すぎない。
// 濃い文字色前提（白地に読める）。
function speakerCaptionBg(dir: string | null, gender: Gender): string {
  const isMetan = dir === "metan" || (dir == null && gender === "female");
  return isMetan ? "rgba(252,247,250,0.96)" : "rgba(247,251,248,0.96)";
}

// キーワード強調色＝黄色。話者色（ピンク/緑）より目を引く。
// ※字幕箱はほぼ白地なので黄色のみだと埋もれる→黒の太い縁取り(全方向の影)で
//   黄色を縁取り、白地でもはっきり浮かせる（ゆっくり系の縁取り文字と同じ手法）。
const KEYWORD_HIGHLIGHT = "#ffd000";
const KEYWORD_OUTLINE =
  "1px 1px 0 #000, -1px 1px 0 #000, 1px -1px 0 #000, -1px -1px 0 #000," +
  " 2px 0 0 #000, -2px 0 0 #000, 0 2px 0 #000, 0 -2px 0 #000," +
  " 0 2px 6px rgba(0,0,0,0.45)";

// 字幕テキスト：『』「」で囲まれた語を黄色＋縁取りで強調。それ以外は地の文色。
function renderCaption(text: string): React.ReactNode {
  const parts = text.split(/(『[^』]*』|「[^」]*」)/g);
  return parts.map((p, i) =>
    /^[『「]/.test(p) ? (
      <span
        key={i}
        style={{ color: KEYWORD_HIGHLIGHT, fontWeight: 900, textShadow: KEYWORD_OUTLINE }}
      >
        {p}
      </span>
    ) : (
      <span key={i}>{p}</span>
    )
  );
}

// テキストからの簡易感情推定（meta側にemotion指定が無いときのフォールバック）。
function inferEmotion(text: string): Emotion {
  if (/[！!？?]{1}/.test(text) && /(えっ|えー|まさか|なんと|本当|ほんと|びっくり|すご|わお|マジ|！？|\?！)/.test(text))
    return "surprise";
  if (/(えっ|まさか|なんと|びっくり|！？|\?！)/.test(text)) return "surprise";
  if (/(楽し|嬉し|うれし|いいね|最高|わくわく|笑)/.test(text)) return "happy";
  return "normal";
}

// start <= t を満たす最後の要素を返す（index.htmlの字幕選択ロジックと同じ）
function pickActive<T extends { start: number }>(units: T[], t: number): T | null {
  let active: T | null = null;
  for (const u of units) {
    if ((u.start ?? 0) <= t) active = u;
    else break;
  }
  return active;
}

// Ken Burns（カットごとのゆっくりズーム/パン）。静止1枚の単調さを解消する中核。
// カットindexで動きのプリセットを選び、カット内の進捗pで scale/translate を補間。
// 常に scale>=1.06 でクロップ済み画像をさらに少し寄せ、パンしても端が見えないようにする。
// objectFit:cover の Img に transform を当てる。コンテナは overflow:hidden 前提。
type KB = { s0: number; s1: number; x0: number; x1: number; y0: number; y1: number };
// 端が見えないよう scale は常に >=1.10（パン余白5%>translate最大3%を確保）。
const KEN_BURNS: KB[] = [
  { s0: 1.1, s1: 1.22, x0: -3, x1: 3, y0: 0, y1: 0 }, // 左→右にパンしつつ寄る
  { s0: 1.22, s1: 1.1, x0: 0, x1: 0, y0: -3, y1: 3 }, // 上→下に引く
  { s0: 1.12, s1: 1.24, x0: 3, x1: -3, y0: 3, y1: -2 }, // 右下→左上に寄る
  { s0: 1.24, s1: 1.12, x0: -3, x1: 2, y0: -3, y1: 1 }, // 左上→中央に引く
  { s0: 1.1, s1: 1.2, x0: 0, x1: 0, y0: 3, y1: -3 }, // 下→上に寄る
  { s0: 1.2, s1: 1.1, x0: 3, x1: -2, y0: 0, y1: 0 }, // 右→左に引く
];

function kenBurnsTransform(index: number, p: number): string {
  const k = KEN_BURNS[((index % KEN_BURNS.length) + KEN_BURNS.length) % KEN_BURNS.length];
  const lerp = (a: number, b: number) => a + (b - a) * p;
  const scale = lerp(k.s0, k.s1);
  const x = lerp(k.x0, k.x1);
  const y = lerp(k.y0, k.y1);
  return `scale(${scale.toFixed(4)}) translate(${x.toFixed(3)}%, ${y.toFixed(3)}%)`;
}

// 注目アノテーション（image_plan mode=focus）。
// クロップで切り出す代わりに、元画像を contain で全体表示し、該当領域(focus)に枠を重ねて
// その中心へゆっくり寄る（題材一致＋文脈保持）。画像アスペクトで枠を実被写体に正確に合わせる。
const FocusVisual: React.FC<{
  image: string;
  focus: { l: number; t: number; r: number; b: number };
  imageAspect: number;
  boxW: number;
  boxH: number;
  p: number; // カット内進捗 0→1
  fxTransform: string; // zoom_punch/shake 合成分
}> = ({ image, focus, imageAspect, boxW, boxH, p, fxTransform }) => {
  // contain フィット：箱に画像全体が収まる表示サイズ（切り抜きしない＝枠が被写体に一致）。
  const fitByWidth = imageAspect >= boxW / boxH;
  const dispW = fitByWidth ? boxW : boxH * imageAspect;
  const dispH = fitByWidth ? boxW / imageAspect : boxH;
  const cx = ((focus.l + focus.r) / 2) * 100;
  const cy = ((focus.t + focus.b) / 2) * 100;
  const zoom = 1 + 0.5 * p; // 1.0→1.5 ゆっくり寄る（焦点中心を基点に）
  const frameOpacity = interpolate(p, [0, 0.12, 0.75, 1], [0, 1, 1, 0.4], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          position: "relative",
          width: dispW,
          height: dispH,
          transformOrigin: `${cx}% ${cy}%`,
          transform: `scale(${zoom.toFixed(4)})${fxTransform}`,
          willChange: "transform",
        }}
      >
        <Img
          src={staticFile(image)}
          style={{ width: "100%", height: "100%", objectFit: "fill", display: "block", borderRadius: 6 }}
        />
        <div
          style={{
            position: "absolute",
            left: `${focus.l * 100}%`,
            top: `${focus.t * 100}%`,
            width: `${(focus.r - focus.l) * 100}%`,
            height: `${(focus.b - focus.t) * 100}%`,
            border: "4px solid rgba(130,205,255,0.95)",
            borderRadius: 8,
            boxShadow: "0 0 0 2px rgba(0,0,0,0.45), 0 0 26px rgba(130,205,255,0.55)",
            opacity: frameOpacity,
            pointerEvents: "none",
          }}
        />
      </div>
    </div>
  );
};

// 重ねエフェクト（台本 turn.effect）。kenburns は基準（追加なし）。
// zoom_punch/shake は中央画像 transform に合成、flash/glow_pulse は重ねレイヤーで表現。
// ターン開始(turn.start)からの経過で発火・減衰させ、決定論的に（sin/cosで）算出する。
type FX = { punchScale: number; shakeX: number; shakeY: number; flash: number; glow: number };
function effectState(
  turn: Turn | null,
  t: number,
  frame: number
): FX {
  const fx: FX = { punchScale: 0, shakeX: 0, shakeY: 0, flash: 0, glow: 0 };
  if (!turn) return fx;
  const since = t - (turn.start ?? 0); // ターン開始からの秒
  const clamp = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };
  switch (turn.effect) {
    case "zoom_punch":
      // 開始0.5sで+8%→0へ減衰（グッと寄ってから落ち着く）
      fx.punchScale = interpolate(since, [0, 0.5], [0.08, 0], clamp);
      break;
    case "shake": {
      // 開始0.8sで減衰する揺れ。sin/cosで決定論的に振動。
      const env = interpolate(since, [0, 0.8], [1, 0], clamp);
      fx.shakeX = Math.sin(frame * 1.7) * 1.2 * env;
      fx.shakeY = Math.cos(frame * 2.3) * 1.2 * env;
      break;
    }
    case "flash":
      // 開始0.35sの白フラッシュ（0.7→0）
      fx.flash = interpolate(since, [0, 0.35], [0.7, 0], clamp);
      break;
    case "glow_pulse":
      // 持続する発光脈動（0.12〜0.44を往復）
      fx.glow = 0.28 + 0.16 * Math.sin(t * Math.PI * 1.2);
      break;
    default:
      break; // kenburns / 未知値は追加なし
  }
  return fx;
}

export const DialogueVideo: React.FC<{ meta: Meta }> = ({ meta }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = frame / fps;

  // 左右の割当: meta.speakers の並び順を優先（[0]=左 / [1]=右）。
  // 無ければ台本の登場順にフォールバック（先に話す方が左）。
  let order: string[];
  if (meta.speakers && meta.speakers.length > 0) {
    order = meta.speakers.map((s) => s.name);
  } else {
    order = [];
    for (const turn of meta.script) {
      if (!order.includes(turn.speaker)) order.push(turn.speaker);
    }
  }
  const leftSpeaker = order[0] ?? "";
  const rightSpeaker = order[1] ?? order[0] ?? "";
  const leftGender = resolveGender(meta, leftSpeaker, 0);
  const rightGender = resolveGender(meta, rightSpeaker, 1);
  const leftDir = resolveAvatarDir(meta, leftSpeaker);
  const rightDir = resolveAvatarDir(meta, rightSpeaker);
  const leftExpressive = resolveExpressive(meta, leftSpeaker);
  const rightExpressive = resolveExpressive(meta, rightSpeaker);
  const leftFlip = resolveFlip(meta, leftSpeaker, "left");
  const rightFlip = resolveFlip(meta, rightSpeaker, "right");
  const manifest = meta.avatarManifest ?? {};

  // 音量ベースのリップシンク：digest.mp3 の現フレーム付近の波形RMS（実効音量）を口の開きにする。
  // visualizeAudio(スペクトル)は値が小さすぎて口が開かないため、波形RMSを直接使う。
  const audioData = useAudioData(staticFile("digest.mp3"));
  let amplitude = 0;
  if (audioData) {
    const wave = audioData.channelWaveforms[0];
    const sr = audioData.sampleRate;
    const center = Math.floor((frame / fps) * sr);
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
    amplitude = Math.min(1, rms * LIPSYNC_GAIN);
  }

  const activeTurn = pickActive(meta.script, t) as Turn | null;
  const activeSpeaker = activeTurn?.speaker ?? "";
  const activatedAtFrame = Math.round((activeTurn?.start ?? 0) * fps);

  // 字幕の名前タグ用：話者の配置側・立ち絵・色を解決（話している側にタグを出す）。
  const activeIsLeft = !!activeSpeaker && activeSpeaker === leftSpeaker;
  const activeIsRight = !!activeSpeaker && activeSpeaker === rightSpeaker && !activeIsLeft;
  const activeDir = activeIsLeft ? leftDir : activeIsRight ? rightDir : null;
  const activeGender = activeIsLeft ? leftGender : rightGender;
  const nameColor = speakerColor(activeDir, activeGender);

  // 現在のトピック（中央ビジュアル）。切替時にフェードイン。
  const topics = meta.topics ?? [];
  let activeTopic: Topic | null = null;
  let activeTopicIndex = -1;
  for (let i = 0; i < topics.length; i++) {
    if ((topics[i].start ?? 0) <= t) {
      activeTopic = topics[i];
      activeTopicIndex = i;
    } else break;
  }
  const topicFade = activeTopic
    ? interpolate(t, [activeTopic.start, activeTopic.start + 0.4], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 1;
  // Ken Burns: カット内の進捗(0→1)。カットのstart→endで線形。endが無ければ動かさない。
  const kbProgress = activeTopic
    ? interpolate(t, [activeTopic.start, activeTopic.end ?? activeTopic.start], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  let caption = "";
  // 現在発話中の文（unit）から感情とその開始時刻を解決。
  // 優先: unit.emotion > turn.emotion > テキスト推定。開始時刻はoverアクションのトリガに使う。
  let emotion: Emotion = "normal";
  let emotionAtFrame = activatedAtFrame;
  if (activeTurn) {
    const units =
      activeTurn.sentences && activeTurn.sentences.length > 0
        ? activeTurn.sentences
        : [{ text: activeTurn.text, start: activeTurn.start, end: activeTurn.end }];
    const cur = pickActive(units, t);
    caption = cur?.text ?? activeTurn.text;
    emotion =
      cur?.emotion ?? activeTurn.emotion ?? inferEmotion(caption || activeTurn.text);
    emotionAtFrame = Math.round((cur?.start ?? activeTurn.start ?? 0) * fps);
  }
  // 解説役（expressive=false、例:めたん先生）は驚き/焦り顔を出さず落ち着かせる。
  // surprise→normal に落とす（happyの笑顔は許容）。
  const calm = (e: Emotion, expressive: boolean): Emotion =>
    !expressive && e === "surprise" ? "normal" : e;
  const leftEmotion =
    activeSpeaker === leftSpeaker ? calm(emotion, leftExpressive) : "normal";
  const rightEmotion =
    activeSpeaker === rightSpeaker ? calm(emotion, rightExpressive) : "normal";

  // 重ねエフェクト（台本 turn.effect）。ifパートの見せ場で発火する。
  const fx = effectState(activeTurn, t, frame);
  // zoom_punch/shake の合成分（先頭スペース付き）。Ken Burns版とfocus版で共用する。
  const fxTransform = ` scale(${(1 + fx.punchScale).toFixed(4)}) translate(${fx.shakeX.toFixed(3)}%, ${fx.shakeY.toFixed(3)}%)`;
  // Ken Burns に zoom_punch/shake を合成（CSS transformは左→右に合成される）。
  const imgTransform = kenBurnsTransform(activeTopicIndex, kbProgress) + fxTransform;
  // contain（ロゴ等の全体表示）はパンすると余白が露出するため、中央ゆっくりズームのみ。
  const isContain = activeTopic?.fit === "contain";
  const containTransform = `scale(${(1 + 0.04 * kbProgress).toFixed(4)})` + fxTransform;
  // 補正フィルタ（画像レビュー指定）→ CSS filter 文字列。
  const flt = activeTopic?.filter;
  const imgFilter = flt
    ? `brightness(${flt.brightness ?? 1}) contrast(${flt.contrast ?? 1}) grayscale(${flt.grayscale ?? 0})`
    : undefined;
  // 手動クロップ（画像レビュー指定）。元画像の[l..r]×[t..b]だけを枠に出す。
  const crop = activeTopic?.crop;
  // contain時の余白(px・全方向)と余白背景色（画像レビュー指定）。
  const containPad = activeTopic?.pad ?? 0;
  const containBg = activeTopic?.bg;
  // 中央ビジュアル枠の実寸（focusアノテーションの contain フィット計算用。styleのleft/right/top/bottomと一致）。
  // 黒板内縁(BOARD)に収まる最大の16:9枠を中央配置する（画像素材が16:9＝間延び/切れを防ぐ）。
  const boardW = 1920 - BOARD.left - BOARD.right;
  const boardH = 1080 - BOARD.top - BOARD.bottom;
  const VISUAL_AR = 16 / 9;
  const wide = boardW / boardH > VISUAL_AR; // 黒板が16:9より横長なら高さ基準
  const visualBoxW = wide ? boardH * VISUAL_AR : boardW;
  const visualBoxH = wide ? boardH : boardW / VISUAL_AR;
  const visualLeft = BOARD.left + (boardW - visualBoxW) / 2;
  const visualTop = BOARD.top + (boardH - visualBoxH) / 2;

  return (
    <AbsoluteFill
      style={{
        // 背景画像(assets/background/bg.png)があれば敷く。無ければCSSが404を無視しグラデのみ表示。
        background: `url(${staticFile("background/bg.png")}) center / cover no-repeat, linear-gradient(160deg, #1e2433 0%, #2b3447 60%, #1a1f2b 100%)`,
        fontFamily: `'${FONT_FAMILY}', 'Hiragino Sans', 'Yu Gothic', sans-serif`,
      }}
    >
      <Audio src={staticFile("digest.mp3")} />

      {/* BGM（全体ループ・薄く・冒頭/末尾フェード）。prep が未配置なら meta.audio.bgm=null で無音。 */}
      {meta.audio?.bgm ? (
        <Audio
          src={staticFile(`bgm/${meta.audio.bgm.file}`)}
          loop
          volume={(f) => {
            const v = meta.audio?.bgm?.volume ?? 0.07;
            const fadeF = Math.round((meta.audio?.bgm?.fade ?? 0) * fps);
            if (fadeF <= 0) return v;
            const inV = interpolate(f, [0, fadeF], [0, v], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const outV = interpolate(f, [durationInFrames - fadeF, durationInFrames], [v, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            return Math.min(inV, outV);
          }}
        />
      ) : null}

      {/* SE（効果音）：各イベント時刻に1発鳴らす。prep が実ファイルの在るイベントだけ残す。 */}
      {(meta.audio?.events ?? []).map((ev, i) => {
        const file = meta.audio?.se?.[ev.se];
        if (!file) return null;
        return (
          <Sequence
            key={`se-${i}`}
            from={Math.round(ev.t * fps)}
            durationInFrames={Math.round(4 * fps)}
            name={`se:${ev.se}`}
          >
            <Audio src={staticFile(`se/${file}`)} volume={meta.audio?.se_volume ?? 0.5} />
          </Sequence>
        );
      })}

      {/* タイトル(meta.title)は非表示。章バッジ(第N章+章タイトル)が上部に出るため冗長＝中央ビジュアルを上へ広げる。 */}

      {/* 中央ビジュアル（背面）。立ち絵より後ろに描く。枠線/影は無し（空枠が浮くのを避ける）。
          角丸＋overflow:hiddenは画像の角取りのため残す。 */}
      <div
        style={{
          position: "absolute",
          left: visualLeft,
          top: visualTop,
          width: visualBoxW,
          height: visualBoxH,
          borderRadius: 18,
          overflow: "hidden",
        }}
      >
        {activeTopic && activeTopic.blank ? null : activeTopic ? (
          <div
            // トピック切替でフェードをやり直すためkeyを付与
            key={activeTopicIndex}
            style={{
              position: "absolute",
              inset: 0,
              opacity: topicFade,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxSizing: "border-box",
              // contain余白の背景は既定では付けない（黒板が透ける）。bg指定時だけその色を敷く。
              background:
                isContain && activeTopic.image && containBg ? containBg : undefined,
              // contain時のみ内側余白（ロゴが枠いっぱいの素材に呼吸を持たせる）。
              padding: isContain ? containPad : 0,
            }}
          >
            {activeTopic.image ? (
              activeTopic.focus ? (
                // 注目アノテーション（切り出さず元画像＋枠＋焦点へ寄る）。
                <FocusVisual
                  image={activeTopic.image}
                  focus={activeTopic.focus}
                  imageAspect={activeTopic.image_aspect ?? visualBoxW / visualBoxH}
                  boxW={visualBoxW}
                  boxH={visualBoxH}
                  p={kbProgress}
                  fxTransform={fxTransform}
                />
              ) : crop ? (
                // 手動クロップ：元画像の[l..r]×[t..b]だけを枠に出す（はみ出しはoverflow:hiddenで切る）。
                // 画像を 1/(r-l)×1/(b-t) に拡大し -l,-t へずらして該当矩形を枠に合わせる。Ken Burnsも乗せる。
                <Img
                  src={staticFile(activeTopic.image)}
                  style={{
                    position: "absolute",
                    width: `${(100 / (crop.r - crop.l)).toFixed(3)}%`,
                    height: `${(100 / (crop.b - crop.t)).toFixed(3)}%`,
                    left: `${(-crop.l * 100 / (crop.r - crop.l)).toFixed(3)}%`,
                    top: `${(-crop.t * 100 / (crop.b - crop.t)).toFixed(3)}%`,
                    objectFit: "fill",
                    transform: imgTransform,
                    filter: imgFilter,
                    willChange: "transform",
                  }}
                />
              ) : (
                <Img
                  src={staticFile(activeTopic.image)}
                  style={{
                    width: "100%",
                    height: "100%",
                    // ロゴ/アイコンは contain で全体表示（端切れ防止）。写真は cover で枠を埋める。
                    objectFit: isContain ? "contain" : "cover",
                    // contain は余白露出を避け中央ズームのみ。cover は Ken Burns＋effect。
                    transform: isContain ? containTransform : imgTransform,
                    filter: imgFilter,
                    willChange: "transform",
                  }}
                />
              )
            ) : activeTopic.note || activeTopic.placeholder ? (
              // manual想像イラストのプレースホルダ（未用意）。情景プロンプトと差し替え先を表示。
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  background:
                    "linear-gradient(135deg, #3a2f52 0%, #241d34 100%)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 24,
                  padding: "0 80px",
                  boxSizing: "border-box",
                }}
              >
                <div
                  style={{
                    color: "rgba(255,255,255,0.55)",
                    fontSize: 26,
                    letterSpacing: 3,
                  }}
                >
                  ✎ 画像（準備中）
                </div>
                {activeTopic.note ? (
                  <div
                    style={{
                      color: "rgba(255,255,255,0.8)",
                      fontSize: 32,
                      lineHeight: 1.5,
                      textAlign: "center",
                      maxWidth: 1100,
                    }}
                  >
                    {activeTopic.note}
                  </div>
                ) : null}
                {activeTopic.placeholder ? (
                  <div
                    style={{
                      color: "rgba(255,255,255,0.4)",
                      fontSize: 22,
                      letterSpacing: 1,
                    }}
                  >
                    {activeTopic.placeholder} を置くと差し替わります
                  </div>
                ) : null}
              </div>
            ) : (
              // タイトルカード（画像なしの安全な既定）
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  background:
                    "linear-gradient(135deg, #324a5f 0%, #25323f 100%)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 20,
                }}
              >
                <div
                  style={{
                    color: "rgba(255,255,255,0.5)",
                    fontSize: 28,
                    letterSpacing: 4,
                  }}
                >
                  {activeTopicIndex + 1} / {topics.length}
                </div>
                <div
                  style={{
                    color: "#fff",
                    fontSize: 60,
                    fontWeight: 800,
                    textAlign: "center",
                    padding: "0 40px",
                    textShadow: "0 2px 8px rgba(0,0,0,0.4)",
                  }}
                >
                  {activeTopic.title ?? ""}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "rgba(255,255,255,0.06)",
              border: "2px dashed rgba(255,255,255,0.25)",
              borderRadius: 20,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "rgba(255,255,255,0.4)",
              fontSize: 34,
            }}
          >
            中央ビジュアル（topics未設定）
          </div>
        )}

        {/* クレジットは動画内に出さない（帰属は概要欄の credits.txt に集約＝CC-BY要件はそこで満たす）。 */}

        {/* glow_pulse: 中央ビジュアルの内側に脈動する発光リング（神秘的な強調） */}
        {fx.glow > 0 ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: 18,
              pointerEvents: "none",
              boxShadow: `inset 0 0 ${Math.round(40 + fx.glow * 120)}px rgba(150,200,255,${fx.glow.toFixed(3)})`,
            }}
          />
        ) : null}

        {/* flash: 白フラッシュ転換（ネタが切り替わる瞬間など） */}
        {fx.flash > 0 ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              pointerEvents: "none",
              background: "#ffffff",
              opacity: fx.flash,
            }}
          />
        ) : null}
      </div>

      {/* 章見出し（trivia章のみ）。画像枠の外＝画像の上に、フラットな見出しバーで「実は＋タイトル」。
          画像に重ねない。切替でフェード＋わずかに下から出す。 */}
      {activeTopic && activeTopic.section === "trivia" ? (
        <div
          key={`chap-${activeTopicIndex}`}
          style={{
            position: "absolute",
            left: BOARD.left + 1,
            top: BOARD.top - 6,
            maxWidth: boardW - 24,
            display: "flex",
            alignItems: "stretch",
            pointerEvents: "none",
            opacity: topicFade,
            transform: `translateY(${((1 - topicFade) * 8).toFixed(1)}px)`,
            borderRadius: 6,
            overflow: "hidden",
            boxShadow: "0 3px 14px rgba(0,0,0,0.5)",
            zIndex: 8,
          }}
        >
          {/* 左アクセント帯（フラット） */}
          <div style={{ width: 8, background: "#ffd84d" }} />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              background: "rgba(18,30,58,0.88)",
              padding: "7px 22px",
            }}
          >
            <span style={{ color: "#ffd84d", fontSize: 26, fontWeight: 800, letterSpacing: 1 }}>
              {triviaLabel(activeTopic.triviaIndex)}
            </span>
            {activeTopic.title ? (
              <span
                style={{
                  color: "#fff",
                  fontSize: 30,
                  fontWeight: 800,
                  textShadow: "0 1px 4px rgba(0,0,0,0.6)",
                }}
              >
                {activeTopic.title}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* 左立ち絵（中央画像より前面・下端の左右コーナーに大きく配置。最下部が胸あたりになるよう下げる。字幕に被らない位置まで左へ） */}
      <div style={{ position: "absolute", left: -70, bottom: -70 }}>
        <Avatar
          dir={leftDir}
          manifest={leftDir ? manifest[leftDir] : undefined}
          fallbackGender={leftGender}
          active={activeSpeaker === leftSpeaker}
          activatedAtFrame={activatedAtFrame}
          amplitude={activeSpeaker === leftSpeaker ? amplitude : 0}
          emotion={leftEmotion}
          emotionAtFrame={emotionAtFrame}
          expressive={leftExpressive}
          flip={leftFlip}
        />
      </div>

      {/* 右立ち絵（右に寄せる＝右見切れ許容） */}
      <div style={{ position: "absolute", right: -50, bottom: -70 }}>
        <Avatar
          dir={rightDir}
          manifest={rightDir ? manifest[rightDir] : undefined}
          fallbackGender={rightGender}
          active={activeSpeaker === rightSpeaker}
          activatedAtFrame={activatedAtFrame}
          amplitude={activeSpeaker === rightSpeaker ? amplitude : 0}
          emotion={rightEmotion}
          emotionAtFrame={emotionAtFrame}
          expressive={rightExpressive}
          flip={rightFlip}
        />
      </div>

      {/* 字幕（最前面）：ほぼ白の角丸ボックス＋話者色の枠（誰が話しているか）＋濃色太字。キーワードは黄色強調。 */}
      {caption ? (
        <div
          style={{
            position: "absolute",
            left: 320,
            right: 320,
            bottom: 40,
            background: speakerCaptionBg(activeDir, activeGender),
            backdropFilter: "blur(10px)",
            borderRadius: 24,
            border: `5px solid ${nameColor}`,
            padding: "22px 44px",
            boxSizing: "border-box",
            boxShadow: "0 6px 22px rgba(0,0,0,0.4)",
          }}
        >
          <div
            style={{
              fontSize: 50,
              fontWeight: 800,
              lineHeight: 1.34,
              textAlign: "center",
              color: "#1b2330",
            }}
          >
            {renderCaption(caption)}
          </div>
        </div>
      ) : null}

      {/* クレジットは動画内に出さない（帰属は概要欄の credits.txt に集約）。meta.credits はデータとして保持。 */}
    </AbsoluteFill>
  );
};
