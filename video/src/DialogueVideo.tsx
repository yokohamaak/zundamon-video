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
import { ParallaxImage } from "./ParallaxImage";
import { FONT_FAMILY } from "./fonts";
import type {
  Callout,
  Compare,
  CompareSide,
  Emotion,
  Gender,
  Meta,
  Panel,
  Quiz,
  Stat,
  Topic,
  Turn,
} from "./types";

// リップシンクの音量ゲイン（波形RMS→amplitude 0..1）。素材/音声に合わせて調整。
const LIPSYNC_GAIN = 5;

// 中央ビジュアル枠の配置(px)。背景 bg.png の黒板の内縁に合わせた目測値。
// 黒板の前に画像/立ち絵が乗る構図。背景を差し替えたら npm run dev で見ながらここを微調整する。
// 横(1920x1080)＝黒板内縁。縦(1080x1920・ショート)＝上部に横幅いっぱいの16:9枠を置く。
const BOARD_LANDSCAPE = { left: 252, right: 252, top: 70, bottom: 347 };
// 縦ショート：画像を主役に大きく（16:9縛りなし・写真はcoverで埋める）。キャラなし。
// 下部はYouTube ShortsのUI(タイトル/説明/ボタン)に被るので字幕は上げ、画像下端も被り境界まで。
const BOARD_PORTRAIT = { left: 24, right: 24, top: 410, bottom: 690 };

// 縦/横で変わるレイアウト値（立ち絵位置・字幕箱）。portrait時は縦積み構図に。
function layoutFor(portrait: boolean) {
  return portrait
    ? {
        avatarL: { left: -50, bottom: -30 } as const,  // (顔バブル時は未使用)
        avatarR: { right: -40, bottom: -30 } as const,
        avatarScale: 1.25,
        // 肩から上：今の立ち絵をそのままの大きさで、肩から下だけ見えない高さに収める（枠・縁なし＝透明）。
        faceW: 500, faceH: 400,  // 表示領域(px・肩から上が収まる。下端で胴を隠す)
        faceScale: 0.9,   // 立ち絵(445px)は等倍（クロップ＝ズームしない）
        faceTop: 10,     // 立ち絵の上端オフセット(px・髪が切れない程度に微調整)
        faceGap: 30, faceLeft: 10, faceRight: 10,  // 字幕下端からの隙間・左右位置
        capLeft: 40,
        capRight: 40,
        capBottom: 550,  // 字幕を上げる（画像直下）。下は顔バブル＋YouTube UI領域に空ける
        capFont: 48,     // 固定見出しより一段弱く（視線を見出しへ誘導）
        capPad: "20px 30px",
        badgeLeft: 24,
        badgeTop: 96,    // 画像(BOARD_PORTRAIT.top=150)より上に置き、被らせない
        // キャラは出さない（画像主役・離脱率対策）。型整合のためダミー値。
        soloAvatar: { right: -10, bottom: 300 } as const,
        soloScale: 1,
      }
    : {
        avatarL: { left: -70, bottom: -70 } as const,
        avatarR: { right: -50, bottom: -70 } as const,
        avatarScale: 1,
        capLeft: 330,
        capRight: 320,
        capBottom: 40,
        capFont: 50,
        capPad: "22px 44px",
        badgeLeft: BOARD_LANDSCAPE.left + 1,
        badgeTop: BOARD_LANDSCAPE.top - 6,
        soloAvatar: { right: -10, bottom: 300 } as const, // 横では未使用（型整合）
        soloScale: 1,
        faceW: 0, faceH: 0, faceScale: 1, faceTop: 0, faceGap: 0, faceLeft: 0, faceRight: 0, // 横では未使用
      };
}

// 深度マップ（2.5Dパララックス用）。画像 ch_xx.jpg に対し ch_xx.depth.png を対応付ける。
function depthPath(image: string): string {
  return image.replace(/\.[^.]+$/, "") + ".depth.png";
}
function hasDepth(meta: Meta, image?: string): boolean {
  return !!image && !!meta.depthMaps && meta.depthMaps.includes(image);
}

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
// ※字幕箱はほぼ白地なので黄色のみだと埋もれる→太い黒縁(text-stroke)で輪郭を締め、
//   白地でもはっきり浮かせる（ゆっくり系の縁取り文字と同じ手法）。
const KEYWORD_HIGHLIGHT = "#ffd000";
const KEYWORD_STROKE = "10px #000"; // 黄色文字の太い黒縁(輪郭をくっきり締める)
const KEYWORD_OUTLINE = "0 2px 5px rgba(0,0,0,0.4)"; // 黒縁の下に僅かな影で浮かせる

// 字幕テキスト：『』「」で囲まれた語を黄色＋太黒縁で強調。それ以外は地の文色。
function renderCaption(text: string): React.ReactNode {
  const parts = text.split(/(『[^』]*』|「[^」]*」)/g);
  return parts.map((p, i) =>
    /^[『「]/.test(p) ? (
      <span
        key={i}
        style={{
          color: KEYWORD_HIGHLIGHT,
          fontWeight: 900,
          WebkitTextStroke: KEYWORD_STROKE,
          paintOrder: "stroke fill", // 黒縁を先に描き黄色塗りを上に→黄色は満タン・縁は外側だけ
          textShadow: KEYWORD_OUTLINE,
        }}
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

// portrait=true（ショート）は動きを強める：cover画像は横に大きくはみ出すのでパン余地が広い。
// 寄り基準(+0.10)で余白を確保しつつ、パン量を増幅して縦のスクロール上でも“動いてる”印象を出す。
function kenBurnsTransform(index: number, p: number, portrait = false): string {
  const k = KEN_BURNS[((index % KEN_BURNS.length) + KEN_BURNS.length) % KEN_BURNS.length];
  const lerp = (a: number, b: number) => a + (b - a) * p;
  if (portrait) {
    // 平均まわりにズーム振幅を1.6倍、寄り基準を底上げ。パンは横2.4倍/縦1.6倍（縦は余地が狭い）。
    const sMean = (k.s0 + k.s1) / 2;
    const scale = sMean + (lerp(k.s0, k.s1) - sMean) * 1.6 + 0.1;
    const x = lerp(k.x0, k.x1) * 2.4;
    const y = lerp(k.y0, k.y1) * 1.6;
    return `scale(${scale.toFixed(4)}) translate(${x.toFixed(3)}%, ${y.toFixed(3)}%)`;
  }
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

// 解説パネル（案A）：画像を縮小して定位置へ寄せ、空いた領域に要点テキストを段階表示する。
// ゆっくり解説系の土台レイアウト。横=画像を左へ縮小しテキストを右へ / 縦=画像を上・テキストを下。
// 出現時刻(panel.shrinkAt / item.at)は build_chapter_topics が発言timingから解決済（描画は時刻参照のみ）。
const DialoguePanel: React.FC<{
  panel: Panel;
  t: number; // 現在時刻(秒・clip補正後)
  boxW: number;
  boxH: number;
  portrait: boolean;
}> = ({ panel, t, boxW, boxH, portrait }) => {
  const items = panel.items ?? [];
  const shrinkAt = panel.shrinkAt ?? 0;
  // 縮小進捗 0(全体表示)→1(縮小・テキスト領域オープン)。0.5秒でイージング。
  const sp = interpolate(t, [shrinkAt, shrinkAt + 0.5], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const GAP = portrait ? 24 : 36;
  // 縮小後の画像比率（横=幅46% / 縦=高さ50%）。残りがテキスト領域。
  const imgFrac = portrait ? 0.5 : 0.46;
  const imgW = portrait ? boxW : boxW * (1 - (1 - imgFrac) * sp);
  const imgH = portrait ? boxH * (1 - (1 - imgFrac) * sp) : boxH;
  const textLeft = portrait ? 0 : imgW + GAP;
  const textTop = portrait ? imgH + GAP : 0;
  const textW = portrait ? boxW : boxW - imgW - GAP;
  const textH = portrait ? boxH - imgH - GAP : boxH;
  const fontSize = portrait ? 40 : 46;
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      {/* 縮小画像（panel.image・無ければ淡い枠だけ）。cover で枠を埋める。 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: imgW,
          height: imgH,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 14,
          overflow: "hidden",
          background: "rgba(255,255,255,0.04)",
        }}
      >
        {panel.image ? (
          <Img
            src={staticFile(panel.image)}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : null}
      </div>
      {/* テキスト領域（縮小で開いた側）。items を at 時刻で順に出現させ、出たものは残す。 */}
      <div
        style={{
          position: "absolute",
          left: textLeft,
          top: textTop,
          width: textW,
          height: textH,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: portrait ? 8 : 14,
          opacity: sp,
          padding: portrait ? "0 24px" : "0 18px",
          boxSizing: "border-box",
          // テキスト領域の背景色（任意）。無指定なら透過（黒板が見える）。
          background: panel.bg || undefined,
          borderRadius: panel.bg ? 12 : 0,
        }}
      >
        {items.map((it, i) => {
          const at = it.at ?? shrinkAt;
          const appear = interpolate(t, [at, at + 0.35], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          if (appear <= 0) return null; // 到達前は表示しない
          return (
            <div
              key={i}
              style={{ opacity: appear, transform: `translateY(${((1 - appear) * 14).toFixed(2)}px)` }}
            >
              {it.arrow_from_prev ? (
                <div
                  style={{
                    color: "rgba(255,255,255,0.5)",
                    fontSize: Math.round(fontSize * 0.7),
                    lineHeight: 1,
                    margin: portrait ? "1px 0" : "3px 0",
                  }}
                >
                  ▼
                </div>
              ) : null}
              <div
                style={{
                  display: "inline-block",
                  background: "rgba(20,26,38,0.85)",
                  color: "#fff",
                  fontWeight: 800,
                  fontSize,
                  padding: portrait ? "8px 16px" : "10px 22px",
                  borderRadius: 12,
                  lineHeight: 1.3,
                  boxShadow: "0 2px 10px rgba(0,0,0,0.35)",
                }}
              >
                {it.text}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// クイズ・リビール：「？」＋問いで溜め、revealAt で答え（＋画像）へ切り替える。
const QuizVisual: React.FC<{
  quiz: Quiz;
  t: number;
  portrait: boolean;
}> = ({ quiz, t, portrait }) => {
  const revealAt = quiz.revealAt ?? 0;
  // 「考え中の画面」→「答えの画面」をクロスフェード。答えは少し遅れてせり上がる。
  const rev = interpolate(t, [revealAt, revealAt + 0.35], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ansPop = interpolate(t, [revealAt + 0.12, revealAt + 0.5], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const barPad = portrait ? "10px 18px" : "12px 26px";
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "linear-gradient(135deg, #243049 0%, #1a2333 100%)",
      }}
    >
      {/* 画像：答えと同時に全面クリア表示（中央が主役・上下のバーはこの上に重なる）。 */}
      {quiz.image ? (
        <Img
          src={staticFile(quiz.image)}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", opacity: rev }}
        />
      ) : null}

      {/* リビール前：？＋問題を中央に大きく（画像があれば暗幕で可読性確保）。答えと入れ替わりで消える。 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 14,
          opacity: 1 - rev,
          background: quiz.image ? "rgba(15,20,30,0.55)" : "transparent",
        }}
      >
        <div style={{ fontSize: portrait ? 140 : 200, fontWeight: 900, color: "#ffd84d", lineHeight: 1, textShadow: "0 4px 16px rgba(0,0,0,0.5)" }}>？</div>
        <div style={{ fontSize: portrait ? 44 : 56, fontWeight: 800, color: "#fff", textAlign: "center", padding: "0 40px", textShadow: "0 2px 8px rgba(0,0,0,0.6)" }}>{quiz.question}</div>
      </div>

      {/* リビール後・上：問題を細バーに縮小して残す。横は章バッジ枠へ移すのでportraitのみ
          （ショートには章バッジ枠が無いため、ここで問題を保持する）。 */}
      {portrait ? (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            opacity: rev,
            background: "rgba(15,20,30,0.82)",
            padding: barPad,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <span style={{ color: "#ffd84d", fontWeight: 900, fontSize: 24 }}>Q.</span>
          <span style={{ color: "#fff", fontWeight: 700, fontSize: 24, textAlign: "center", lineHeight: 1.25 }}>{quiz.question}</span>
        </div>
      ) : null}

      {/* リビール後・下：答えを大きく（黄バナー・せり上がる）。 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: portrait ? 40 : 36,
          display: "flex",
          justifyContent: "center",
          padding: "0 24px",
          opacity: ansPop,
          transform: `translateY(${((1 - ansPop) * 20).toFixed(1)}px)`,
        }}
      >
        <div style={{ background: "rgba(255,216,77,0.96)", color: "#1a1f2b", fontWeight: 900, fontSize: portrait ? 50 : 66, padding: portrait ? "10px 26px" : "12px 34px", borderRadius: 14, boxShadow: "0 6px 20px rgba(0,0,0,0.45)", textAlign: "center", lineHeight: 1.2 }}>{quiz.answer}</div>
      </div>
    </div>
  );
};

// 比較（2分割）：横=左右 / 縦=上下。セリフに合わせて分割タイミングを制御する。
// at0=左が出る（無指定は章頭）/ at1=右が出る＝分割する（at0と同じなら最初から2分割）。
// at0<at1 のときは「左が1枚フル表示 → at1で左が縮み右がスライドイン」になる。
const CompareVisual: React.FC<{
  compare: Compare;
  t: number;
  portrait: boolean;
}> = ({ compare, t, portrait }) => {
  const at0 = compare.at0 ?? compare.showAt ?? 0;
  const at1 = compare.at1 ?? at0;
  const lAppear = interpolate(t, [at0, at0 + 0.4], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const split = interpolate(t, [at1, at1 + 0.45], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }); // 0=左フル / 1=2分割
  const firstPct = (100 - 50 * split).toFixed(2); // 1つ目が占める割合：100→50
  const secondPct = (50 * split).toFixed(2);      // 2つ目：0→50
  const inner = (s: CompareSide) => (
    <>
      {s.image ? (
        <Img src={staticFile(s.image)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <div style={{ width: "100%", height: "100%", background: "linear-gradient(135deg, #324a5f 0%, #25323f 100%)" }} />
      )}
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, background: "rgba(20,26,38,0.82)", color: "#fff", fontWeight: 800, fontSize: portrait ? 34 : 40, textAlign: "center", padding: portrait ? "8px 6px" : "10px 8px" }}>{s.label}</div>
    </>
  );
  // 1つ目=左(横)/上(縦)。フル幅から半分へ縮む。2つ目=右/下。0から半分へ広がる。
  const firstStyle: React.CSSProperties = portrait
    ? { position: "absolute", left: 0, right: 0, top: 0, height: `${firstPct}%`, overflow: "hidden", opacity: lAppear }
    : { position: "absolute", top: 0, bottom: 0, left: 0, width: `${firstPct}%`, overflow: "hidden", opacity: lAppear };
  const secondStyle: React.CSSProperties = portrait
    ? { position: "absolute", left: 0, right: 0, bottom: 0, height: `${secondPct}%`, overflow: "hidden", opacity: split }
    : { position: "absolute", top: 0, bottom: 0, right: 0, width: `${secondPct}%`, overflow: "hidden", opacity: split };
  // 分割線（境目）。split で現れる。
  const dividerStyle: React.CSSProperties = portrait
    ? { position: "absolute", left: 0, right: 0, top: `${firstPct}%`, height: 3, transform: "translateY(-1.5px)", background: "rgba(255,255,255,0.85)", opacity: split }
    : { position: "absolute", top: 0, bottom: 0, left: `${firstPct}%`, width: 3, transform: "translateX(-1.5px)", background: "rgba(255,255,255,0.85)", opacity: split };
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div style={firstStyle}>{inner(compare.left)}</div>
      <div style={secondStyle}>{inner(compare.right)}</div>
      <div style={dividerStyle} />
    </div>
  );
};

// 数字強調（重ね層）：大きな数字＋単位を画像中央に出す。countTo があればカウントアップ。
const StatOverlay: React.FC<{
  stat: Stat;
  t: number;
  portrait: boolean;
}> = ({ stat, t, portrait }) => {
  const showAt = stat.showAt ?? 0;
  const p = interpolate(t, [showAt, showAt + 0.4], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  if (p <= 0) return null;
  let valueText = stat.value;
  if (stat.countTo != null) {
    const cu = interpolate(t, [showAt, showAt + 0.8], [0, stat.countTo], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    valueText = Math.round(cu).toLocaleString();
  }
  const scale = 0.7 + 0.3 * Math.min(1, p * 1.2);
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none", opacity: p }}>
      <div style={{ background: "rgba(15,20,30,0.5)", borderRadius: 20, padding: portrait ? "14px 28px" : "18px 40px", display: "flex", flexDirection: "column", alignItems: "center", transform: `scale(${scale.toFixed(3)})` }}>
        {stat.label ? <div style={{ color: "rgba(255,255,255,0.85)", fontSize: portrait ? 28 : 34, fontWeight: 700, marginBottom: 4 }}>{stat.label}</div> : null}
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, color: "#ffd84d", fontWeight: 900, lineHeight: 1, textShadow: "0 4px 14px rgba(0,0,0,0.55)" }}>
          <span style={{ fontSize: portrait ? 96 : 140 }}>{valueText}</span>
          {stat.unit ? <span style={{ fontSize: portrait ? 44 : 60, color: "#fff" }}>{stat.unit}</span> : null}
        </div>
      </div>
    </div>
  );
};

// 注釈・吹き出し（重ね層）：画像上の点(0..1)を指してラベルを出す。
const CalloutOverlay: React.FC<{
  callouts: Callout[];
  t: number;
  portrait: boolean;
}> = ({ callouts, t, portrait }) => {
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {callouts.map((c, i) => {
        const at = c.at ?? 0;
        const ap = interpolate(t, [at, at + 0.3], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        if (ap <= 0) return null;
        const below = c.y < 0.25; // 点が上端寄りならラベルを下に出す
        const labelStyle: React.CSSProperties = {
          position: "absolute",
          left: "50%",
          transform: "translateX(-50%)",
          whiteSpace: "nowrap",
          background: "rgba(20,26,38,0.9)",
          color: "#fff",
          fontWeight: 800,
          fontSize: portrait ? 26 : 32,
          padding: "6px 14px",
          borderRadius: 10,
          boxShadow: "0 2px 10px rgba(0,0,0,0.4)",
        };
        if (below) labelStyle.top = 20; else labelStyle.bottom = 20;
        return (
          <div key={i} style={{ position: "absolute", left: `${c.x * 100}%`, top: `${c.y * 100}%`, opacity: ap }}>
            <div style={{ position: "absolute", left: -9, top: -9, width: 18, height: 18, borderRadius: "50%", background: "#ff5a6a", border: "3px solid #fff", boxShadow: "0 0 0 2px rgba(0,0,0,0.4)" }} />
            <div style={labelStyle}>{c.text}</div>
          </div>
        );
      })}
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

export const DialogueVideo: React.FC<{
  meta: Meta;
  portrait?: boolean;
  clipStartSec?: number;
  // 章末尾の実時刻（秒）。ショートは末尾の余韻で映像を次章へ進めず最後のカットで固定する用。
  clipEndSec?: number;
  // 切り抜く章番号（入力props用。実際の窓計算は Root の calculateMetadata 側。描画では未使用）。
  clipChapter?: number;
  // ショート冒頭に重ねるフック（掴み）テロップ。縦のみ・冒頭数秒だけ表示。
  hookText?: string;
  // ショート終盤に出すCTA（続きは本編で／登録誘導）。縦のみ・末尾数秒。空なら出さない。
  ctaText?: string;
}> = ({ meta, portrait = false, clipStartSec = 0, clipEndSec = 0, hookText = "", ctaText = "" }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();
  // 切り抜き時：フレームは章先頭=0だが、台本/音声の時刻は実タイムライン基準。
  // tに clipStartSec を足して実時刻へ写像（横動画は clipStartSec=0 で従来どおり）。
  // ショートの末尾余韻（音声フェード中）は映像を次章へ進めず章末で固定（ブツ切り回避）。
  // ※音声は別<Audio>で実再生のためここでの clamp の影響を受けない。
  let t = frame / fps + clipStartSec;
  if (portrait && clipEndSec) t = Math.min(t, clipEndSec - 0.05);
  const BOARD = portrait ? BOARD_PORTRAIT : BOARD_LANDSCAPE;
  const L = layoutFor(portrait);

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
  // 縦ショートは「顔だけ素材」を優先：assets/avatars/<dir>_face/ があればそれを使う（無ければ通常）。
  // 立ち絵の identity/色は base の dir のまま（speakerColor は leftDir/rightDir を使う）。
  const faceDir = (d: string | null) =>
    portrait && d && manifest[`${d}_face`] ? `${d}_face` : d;
  const leftAvatarDir = faceDir(leftDir);
  const rightAvatarDir = faceDir(rightDir);

  // 音量ベースのリップシンク：digest.mp3 の現フレーム付近の波形RMS（実効音量）を口の開きにする。
  // visualizeAudio(スペクトル)は値が小さすぎて口が開かないため、波形RMSを直接使う。
  const audioData = useAudioData(staticFile("digest.mp3"));
  let amplitude = 0;
  if (audioData) {
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
    amplitude = Math.min(1, rms * LIPSYNC_GAIN);
  }

  const activeTurn = pickActive(meta.script, t) as Turn | null;
  const activeSpeaker = activeTurn?.speaker ?? "";
  const isChorus = !!activeTurn?.chorus; // ユニゾン時は両方の立ち絵をactive（両方の口を動かす）
  const activatedAtFrame = Math.round(((activeTurn?.start ?? 0) - clipStartSec) * fps);

  // 字幕の名前タグ用：話者の配置側・立ち絵・色を解決（話している側にタグを出す）。
  const activeIsLeft = !!activeSpeaker && activeSpeaker === leftSpeaker;
  const activeIsRight = !!activeSpeaker && activeSpeaker === rightSpeaker && !activeIsLeft;
  const activeDir = activeIsLeft ? leftDir : activeIsRight ? rightDir : null;
  const activeGender = activeIsLeft ? leftGender : rightGender;
  const nameColor = speakerColor(activeDir, activeGender);
  // ユニゾン(二人同時)字幕用：両話者の色（枠を二色グラデにして「二人」を示す）。
  const leftColor = speakerColor(leftDir, leftGender);
  const rightColor = speakerColor(rightDir, rightGender);

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
  // 章切替のページめくり（CSS 3D flip）。前章の最後の画像を「ページ」として左ヒンジで回転させ、
  // 下から新章の画像を現す。章内のカット切替は従来のフェードのまま（＝章が変わる時だけめくる）。
  const FLIP_DUR = 0.55; // めくり時間（秒）
  const FLIP_HOLD = 0.15; // めくり前に前章画像を保持＝章終わりの余白（config.audio.se_lead と揃える）
  const prevTopic = activeTopicIndex > 0 ? topics[activeTopicIndex - 1] : null;
  const isChapterFlip =
    !!activeTopic && !!prevTopic && !!prevTopic.image &&
    prevTopic.chapter !== activeTopic.chapter;
  // 章のtopic.startは「前章末＝章間の無音の始まり」（build_chapter_topics）。
  // よって [start, start+HOLD] は前章画像を保持（章終わりの余白）、その後 FLIP_DUR でめくり、
  // 残りの無音で新章画像を見せてから声が始まる。
  const flipStart = activeTopic?.start ?? 0;
  const flipP = isChapterFlip
    ? interpolate(t, [flipStart + FLIP_HOLD, flipStart + FLIP_HOLD + FLIP_DUR], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 1;
  const flipping = isChapterFlip && t < flipStart + FLIP_HOLD + FLIP_DUR;
  // めくり中は新画像を即・全表示（前章ページが上を覆うのでフェード不要）。
  const effectiveTopicFade = flipping ? 1 : topicFade;
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
    emotionAtFrame = Math.round(((cur?.start ?? activeTurn.start ?? 0) - clipStartSec) * fps);
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
  // 注釈(callouts)がある章は画像を静止させる。マーカーは画像枠座標(0..1)に固定で置くため、
  // Ken Burnsでパン/ズームすると指し示す位置が被写体からズレる。整合のため動きを止める。
  const freezeImage = !!activeTopic?.callouts;
  // Ken Burns に zoom_punch/shake を合成（CSS transformは左→右に合成される）。縦は動きを強める。
  const imgTransform = freezeImage ? "none" : kenBurnsTransform(activeTopicIndex, kbProgress, portrait) + fxTransform;
  // contain（ロゴ等の全体表示）はパンすると余白が露出するため、中央ゆっくりズームのみ。
  // 縦ショートはズーム幅を少し広げて単調さを軽減（余白露出しない範囲）。
  const isContain = activeTopic?.fit === "contain";
  const containZoom = portrait ? 0.08 : 0.04;
  const containTransform = freezeImage ? "none" : `scale(${(1 + containZoom * kbProgress).toFixed(4)})` + fxTransform;
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
  const boardW = width - BOARD.left - BOARD.right;
  const boardH = height - BOARD.top - BOARD.bottom;
  const VISUAL_AR = 16 / 9;
  // 横：黒板内縁に収まる最大の16:9枠を中央配置（横長素材を間延び/切れなく）。
  // 縦ショート：16:9に縛らず枠いっぱい（写真は cover で大きく埋める／ロゴ等 contain は全体表示にフォールバック）。
  let visualBoxW: number, visualBoxH: number, visualLeft: number, visualTop: number;
  if (portrait) {
    visualBoxW = boardW;
    visualBoxH = boardH;
    visualLeft = BOARD.left;
    visualTop = BOARD.top;
  } else {
    const wide = boardW / boardH > VISUAL_AR; // 黒板が16:9より横長なら高さ基準
    visualBoxW = wide ? boardH * VISUAL_AR : boardW;
    visualBoxH = wide ? boardH : boardW / VISUAL_AR;
    visualLeft = BOARD.left + (boardW - visualBoxW) / 2;
    visualTop = BOARD.top + (boardH - visualBoxH) / 2;
  }

  return (
    <AbsoluteFill
      style={{
        // 背景画像(assets/background/bg.png)があれば敷く。無ければCSSが404を無視しグラデのみ表示。
        background: `url(${staticFile("background/bg.png")}) center / cover no-repeat, linear-gradient(160deg, #1e2433 0%, #2b3447 60%, #1a1f2b 100%)`,
        fontFamily: `'${FONT_FAMILY}', 'Hiragino Sans', 'Yu Gothic', sans-serif`,
      }}
    >
      {/* メインボイス。切り抜き時は trimBefore で章先頭へ頭出し（横は clipStartSec=0）。
          ショートは末尾でフェードアウト（次章へ食い込んだ音声のブツ切りを防ぐ）。 */}
      <Audio
        src={staticFile("digest.mp3")}
        trimBefore={Math.round(clipStartSec * fps)}
        volume={
          portrait
            ? (f) =>
                interpolate(
                  f,
                  [durationInFrames - Math.round(0.8 * fps), durationInFrames],
                  [1, 0],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                )
            : undefined
        }
      />

      {/* BGM（全体ループ・薄く）。フェードインは無し（冒頭から定常音量）・末尾のみフェードアウト。
          prep が未配置なら meta.audio.bgm=null で無音。 */}
      {meta.audio?.bgm ? (
        <Audio
          src={staticFile(`bgm/${meta.audio.bgm.file}`)}
          loop
          volume={(f) => {
            const v = meta.audio?.bgm?.volume ?? 0.07;
            const fadeF = Math.round((meta.audio?.bgm?.fade ?? 0) * fps);
            if (fadeF <= 0) return v;
            // フェードインはせず冒頭から v。末尾だけ fade 秒かけて 0 へ。
            return interpolate(f, [durationInFrames - fadeF, durationInFrames], [v, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
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
            from={Math.round((ev.t - clipStartSec) * fps)}
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
              opacity: effectiveTopicFade,
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
            {activeTopic.panel ? (
              // 解説パネル（任意・案A）：画像を縮小し、空いた領域に要点テキストを段階表示。
              <DialoguePanel
                panel={activeTopic.panel}
                t={t}
                boxW={visualBoxW}
                boxH={visualBoxH}
                portrait={portrait}
              />
            ) : activeTopic.quiz ? (
              // クイズ・リビール：？で溜めて答えを出す。
              <QuizVisual quiz={activeTopic.quiz} t={t} portrait={portrait} />
            ) : activeTopic.compare ? (
              // 比較（2分割）：A対Bを並べる。
              <CompareVisual compare={activeTopic.compare} t={t} portrait={portrait} />
            ) : activeTopic.image ? (
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
              ) : portrait && !isContain && hasDepth(meta, activeTopic.image) ? (
                // 縦ショート：深度マップがある写真は 2.5Dパララックスで「動画らしく」動かす。
                <ParallaxImage
                  image={activeTopic.image}
                  depth={depthPath(activeTopic.image)}
                  progress={kbProgress}
                  boxW={visualBoxW}
                  boxH={visualBoxH}
                  filter={imgFilter}
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

        {/* 重ね層（任意）：主モード(画像/パネル/クイズ/比較)の上に数字強調・注釈を出す。 */}
        {activeTopic && !activeTopic.blank && activeTopic.stat ? (
          <StatOverlay stat={activeTopic.stat} t={t} portrait={portrait} />
        ) : null}
        {activeTopic && !activeTopic.blank && activeTopic.callouts ? (
          <CalloutOverlay callouts={activeTopic.callouts} t={t} portrait={portrait} />
        ) : null}

        {/* クレジットは動画内に出さない（帰属は概要欄の credits.txt に集約＝CC-BY要件はそこで満たす）。 */}

        {/* 章切替のページめくり：前章の画像を左ヒンジで回転させ、下の新章画像を現す。
            ※回転の向き/ヒンジ位置は transformOrigin と rotateY の符号で調整可（renderで微調整）。 */}
        {flipping && prevTopic?.image ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              zIndex: 6,
              transformOrigin: "left center",
              transform: `perspective(1800px) rotateY(${(-105 * flipP).toFixed(2)}deg)`,
              backfaceVisibility: "hidden",
              boxShadow: `${Math.round(28 * (1 - flipP))}px 0 ${Math.round(
                48 * (1 - flipP)
              )}px rgba(0,0,0,${(0.45 * (1 - flipP)).toFixed(3)})`,
            }}
          >
            <Img
              src={staticFile(prevTopic.image)}
              style={{
                width: "100%",
                height: "100%",
                objectFit: prevTopic.fit === "contain" ? "contain" : "cover",
                background:
                  prevTopic.fit === "contain" ? prevTopic.bg ?? "#1a2230" : undefined,
              }}
            />
            {/* めくれる面の陰影（ヒンジ側→先端へ濃く＝紙が立ち上がる立体感） */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                pointerEvents: "none",
                background: `linear-gradient(to right, rgba(0,0,0,0) 55%, rgba(0,0,0,${(
                  0.4 * flipP
                ).toFixed(3)}) 100%)`,
              }}
            />
          </div>
        ) : null}

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

        {/* 白フラッシュ転換は廃止（章切替はページめくりで表現）。 */}
      </div>

      {/* 章見出し（trivia章のみ）。画像枠の外＝画像の上に、フラットな見出しバーで「実は＋タイトル」。
          画像に重ねない。切替でフェード＋わずかに下から出す。 */}
      {!portrait && activeTopic && activeTopic.section === "trivia"
        ? (() => {
            // quiz章はタイトルがネタバレ＆クイズ表示と被るので、通常バッジを出さない。
            // 代わりに「実は番号＋問題」をリビール後にバッジ枠へフェードインさせる（問題が見出しへ昇格）。
            const isQuiz = !!activeTopic.quiz;
            const qRevealAt = activeTopic.quiz?.revealAt ?? 0;
            const badgeText = isQuiz ? activeTopic.quiz?.question : activeTopic.title;
            const badgeOpacity = isQuiz
              ? interpolate(t, [qRevealAt, qRevealAt + 0.4], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                })
              : topicFade;
            return (
              <div
                key={`chap-${activeTopicIndex}`}
                style={{
                  position: "absolute",
                  left: L.badgeLeft,
                  top: L.badgeTop,
                  maxWidth: boardW - 24,
                  display: "flex",
                  alignItems: "stretch",
                  pointerEvents: "none",
                  opacity: badgeOpacity,
                  transform: `translateY(${((1 - badgeOpacity) * 8).toFixed(1)}px)`,
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
                  {badgeText ? (
                    <span
                      style={{
                        color: "#fff",
                        fontSize: 30,
                        fontWeight: 800,
                        textShadow: "0 1px 4px rgba(0,0,0,0.6)",
                      }}
                    >
                      {badgeText}
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })()
        : null}

      {portrait ? (
        // 縦ショート：今の立ち絵を四角く切り抜いた「肩から上」を字幕のすぐ下に小さめで2人。
        // 同じパーツなので口パク・まばたきもそのまま効く（新素材不要）。
        (() => {
          const topPos = height - L.capBottom + L.faceGap; // 字幕箱の下端のすぐ下
          const bust = (
            side: "left" | "right",
            dir: string | null,
            gender: Gender,
            isActive: boolean,
            emotion: Emotion,
            expressive: boolean,
            flip: boolean
          ) => (
            // 枠/縁/背景なしの透明クリップ。下端で胴を隠す＝肩から上だけ自然に見せる。
            <div
              style={{
                position: "absolute",
                top: topPos,
                ...(side === "left" ? { left: L.faceLeft } : { right: L.faceRight }),
                width: L.faceW,
                height: L.faceH,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: L.faceTop,
                  left: "50%",
                  transform: `translateX(-50%) scale(${L.faceScale})`,
                  transformOrigin: "top center",
                }}
              >
                <Avatar
                  dir={dir}
                  manifest={dir ? manifest[dir] : undefined}
                  fallbackGender={gender}
                  active={isActive}
                  activatedAtFrame={activatedAtFrame}
                  amplitude={isActive ? amplitude : 0}
                  emotion={emotion}
                  emotionAtFrame={emotionAtFrame}
                  expressive={expressive}
                  flip={flip}
                />
              </div>
            </div>
          );
          return (
            <>
              {bust("left", leftAvatarDir, leftGender,
                activeSpeaker === leftSpeaker || isChorus, leftEmotion, leftExpressive, leftFlip)}
              {bust("right", rightAvatarDir, rightGender,
                activeSpeaker === rightSpeaker || isChorus, rightEmotion, rightExpressive, rightFlip)}
            </>
          );
        })()
      ) : (
        <>
          {/* 左立ち絵（中央画像より前面・下端の左右コーナーに大きく配置。最下部が胸あたりになるよう下げる。字幕に被らない位置まで左へ） */}
          <div
            style={{
              position: "absolute",
              ...L.avatarL,
              transform: `scale(${L.avatarScale})`,
              transformOrigin: "left bottom",
            }}
          >
            <Avatar
              dir={leftAvatarDir}
              manifest={leftAvatarDir ? manifest[leftAvatarDir] : undefined}
              fallbackGender={leftGender}
              active={activeSpeaker === leftSpeaker || isChorus}
              activatedAtFrame={activatedAtFrame}
              amplitude={activeSpeaker === leftSpeaker || isChorus ? amplitude : 0}
              emotion={leftEmotion}
              emotionAtFrame={emotionAtFrame}
              expressive={leftExpressive}
              flip={leftFlip}
            />
          </div>

          {/* 右立ち絵（右に寄せる＝右見切れ許容） */}
          <div
            style={{
              position: "absolute",
              ...L.avatarR,
              transform: `scale(${L.avatarScale})`,
              transformOrigin: "right bottom",
            }}
          >
            <Avatar
              dir={rightAvatarDir}
              manifest={rightAvatarDir ? manifest[rightAvatarDir] : undefined}
              fallbackGender={rightGender}
              active={activeSpeaker === rightSpeaker || isChorus}
              activatedAtFrame={activatedAtFrame}
              amplitude={activeSpeaker === rightSpeaker || isChorus ? amplitude : 0}
              emotion={rightEmotion}
              emotionAtFrame={emotionAtFrame}
              expressive={rightExpressive}
              flip={rightFlip}
            />
          </div>
        </>
      )}

      {/* 字幕（最前面）：ほぼ白の角丸ボックス＋枠＋濃色太字。キーワードは黄色強調。
          枠の色は通常＝話者色（誰が話しているか）。ユニゾン時＝両話者色のグラデ（二人を示す・別色）。 */}
      {caption ? (
        <div
          style={{
            position: "absolute",
            left: L.capLeft,   // 横:左端を右へ（髪と被り回避）/ 縦:ほぼ全幅
            right: L.capRight,
            bottom: L.capBottom,
            backdropFilter: "blur(10px)",
            borderRadius: 24,
            padding: L.capPad,
            boxSizing: "border-box",
            boxShadow: "0 6px 22px rgba(0,0,0,0.4)",
            // ユニゾン：角丸を保ったまま二色グラデ枠（padding-box/border-box の二重背景）。
            ...(isChorus
              ? {
                  border: "5px solid transparent",
                  background: `linear-gradient(rgba(250,248,252,0.96), rgba(250,248,252,0.96)) padding-box, linear-gradient(90deg, ${leftColor}, ${rightColor}) border-box`,
                }
              : {
                  border: `5px solid ${nameColor}`,
                  background: speakerCaptionBg(activeDir, activeGender),
                }),
          }}
        >
          <div
            style={{
              fontSize: L.capFont,
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

      {/* ショート用の固定見出し（サマリ/フック）：縦のみ・最初から最後まで上部に出し続ける。
          「上に固定タイトル＋下にライブ字幕」のShorts王道。無音視聴でも内容が伝わりクリック率/離脱率に効く。
          冒頭だけ軽くポップさせて注目を作る（以降は固定）。 */}
      {portrait && hookText ? (
        (() => {
          // 0秒から最後まで完全固定（フェードイン/ポップ/パルスなし）。
          return (
            <div
              style={{
                position: "absolute",
                left: 14,
                right: 14,
                top: 140,  // iPhoneのDynamic Island/ステータスバーに被らない位置まで下げる
                display: "flex",
                justifyContent: "center",
                pointerEvents: "none",
                zIndex: 20,
              }}
            >
              <div
                style={{
                  position: "relative",
                  background: "rgba(8,12,24,0.96)",
                  borderRadius: 22,
                  border: "5px solid #ffd400",   // 全周の太い黄枠で字幕より圧倒的に目立たせる
                  padding: "26px 30px",
                  boxShadow: "0 12px 34px rgba(0,0,0,0.6), 0 0 0 3px rgba(0,0,0,0.35)",
                  textAlign: "center",
                  fontSize: 72,                  // 字幕(48)の1.5倍。視線を最優先で奪う
                  fontWeight: 900,
                  lineHeight: 1.22,
                  letterSpacing: 0.5,
                  color: "#ffe24d",              // 黄文字＝最も目を引く色
                  // 太い黒縁で画像の上でも沈まない（ゆっくり系の縁取り）。
                  WebkitTextStroke: "2px #000",
                  paintOrder: "stroke fill",
                  textShadow:
                    "3px 3px 0 #000, -3px 3px 0 #000, 3px -3px 0 #000, -3px -3px 0 #000, 0 4px 14px rgba(0,0,0,0.7)",
                }}
              >
                {hookText}
              </div>
            </div>
          );
        })()
      ) : null}

      {/* ショート終盤CTA：縦のみ・末尾約3.5秒にセーフゾーン内へ。離脱抑制＋本編/登録への送客。 */}
      {portrait && ctaText ? (
        (() => {
          const start = durationInFrames - 3.5 * fps;
          const op = interpolate(frame, [start, start + 0.4 * fps], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          if (op <= 0) return null;
          const pop = interpolate(frame, [start, start + 0.4 * fps], [0.86, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          return (
            <div
              style={{
                position: "absolute",
                left: 30,
                right: 30,
                top: visualTop,              // 中央ビジュアルの領域に重ねて
                height: visualBoxH,          // その「ど真ん中」に置く
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                pointerEvents: "none",
                opacity: op,
                transform: `scale(${pop.toFixed(3)})`,
                transformOrigin: "center center",
                zIndex: 25,
              }}
            >
              <div
                style={{
                  background: "linear-gradient(135deg, #ff3b6b, #ff8a3d)",
                  borderRadius: 24,
                  border: "5px solid #fff",
                  padding: "32px 40px",
                  boxShadow: "0 14px 40px rgba(0,0,0,0.6)",
                  textAlign: "center",
                  fontSize: 72,
                  fontWeight: 900,
                  lineHeight: 1.3,
                  color: "#fff",
                  textShadow: "2px 2px 0 rgba(0,0,0,0.45), 0 3px 10px rgba(0,0,0,0.5)",
                  whiteSpace: "pre-line",
                }}
              >
                {ctaText}
              </div>
            </div>
          );
        })()
      ) : null}

      {/* クレジットは動画内に出さない（帰属は概要欄の credits.txt に集約）。meta.credits はデータとして保持。 */}
    </AbsoluteFill>
  );
};
