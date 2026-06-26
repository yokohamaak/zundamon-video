import {
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { Emotion, Gender } from "./types";

// パーツ分け立ち絵のアバター。
// 同一キャンバスサイズの部位別PNGを重ねて描画し、
// ・口形状を音量(amplitude)で切替＝音量ベースのリップシンク
// ・目を周期的に閉じる＝まばたき
// ・発話時の弾み/揺れ、待機時の微呼吸
// ・expressive(ずんだもん)は驚き等でオーバーアクション
// を行う。
//
// 必要パーツ（assets/avatars/<キャラ>/、stem名で参照）:
//   base                         … 口・目を除いた土台（必須）
//   mouth_close / mouth_half / mouth_open … 口の開き3段（リップシンク）
//   eye_open / eye_close         … 目の開閉（まばたき）
//   eye_surprise / eye_smile     … 任意。驚き/笑顔の目差分
//   fx_surprise / fx_sweat       … 任意。びっくりマーク等の効果オーバーレイ
// パーツが無いキャラは従来の単一画像(gender_open/close)へ自動フォールバック。

type Manifest = Record<string, string>;

// 口の開き判定しきい値（amplitude=波形RMS×LIPSYNC_GAIN, 0..1）。
// 発話中の実測レンジ(≈0.10〜0.33)に合わせる。無音/語間は閉じ。
const MOUTH_HALF = 0.06;
const MOUTH_OPEN = 0.18;

// まばたき：周期(フレーム)の末尾BLINK_DURだけ目を閉じる。
const BLINK_CYCLE = 110; // ≒3.7s @30fps
const BLINK_DUR = 6;

// 文字列→安定したハッシュ（まばたき位相のキャラ別オフセット用）
function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export const Avatar: React.FC<{
  // パーツ立ち絵のフォルダ名。nullなら従来の単一画像フォールバック。
  dir: string | null;
  manifest?: Manifest;
  fallbackGender: Gender;
  active: boolean;
  activatedAtFrame: number;
  // 音量(0..1)。発話者のみ意味を持つ。リップシンクに使用。
  amplitude: number;
  emotion: Emotion;
  // 現在の感情が始まったフレーム（オーバーアクションのトリガ）
  emotionAtFrame: number;
  // trueで驚き等のオーバーアクションを有効化（ずんだもん）
  expressive: boolean;
  // 立ち絵を左右反転（向きが逆の素材用）
  flip?: boolean;
}> = ({
  dir,
  manifest,
  fallbackGender,
  active,
  activatedAtFrame,
  amplitude,
  emotion,
  emotionAtFrame,
  expressive,
  flip,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const time = frame / fps;

  const hasParts = !!(dir && manifest && manifest.base);

  // 発話開始の弾み（spring）
  const bounce = active
    ? spring({
        frame: frame - activatedAtFrame,
        fps,
        config: { damping: 12, stiffness: 120 },
        durationInFrames: 18,
      })
    : 0;

  // 待機時の微呼吸（常時、ごく弱く）
  const breath = Math.sin((time * Math.PI * 2) / 3) * 3;
  // 発話中の体の揺れ（ゆっくりめ）
  const sway = active ? Math.sin(time * Math.PI * 2 * 0.7) * 1.2 : 0;
  // 聞き手のうなずき（相槌）：時々ゆっくり頷く＝発話者の話を聞いている気配。静止による単調さを和らげる。
  // 約0.4Hz(≈2.5秒周期)で、下向き(+Y)に軽く一拍。max(0,sin)^3 で間隔を空けて自然に。
  const nodPhase = Math.sin(time * Math.PI * 2 * 0.4 + (hash(dir ?? "x") % 7));
  const nod = !active ? Math.max(0, nodPhase) ** 3 * 5 : 0;

  let translateY = breath - bounce * 8 + nod;
  // 発話者は少し手前にポップ（拡大）。非発話者は等倍（透明化・減光はしない）。
  let scale = active ? 1.04 + interpolate(bounce, [0, 1], [0, 0.04]) : 1.0;
  let rotate = sway;

  // オーバーアクション（expressive＋発話者）。surprise: 跳ねて伸縮＋減衰ウォブル。happy: 小さくホップ。
  // ※大きすぎたので控えめに調整。
  const reactT = frame - emotionAtFrame;
  const REACT_DUR = Math.round(fps * 0.8);
  if (active && expressive && reactT >= 0 && reactT < REACT_DUR) {
    const p = reactT / REACT_DUR; // 0..1
    const pulse = Math.sin(Math.min(p / 0.45, 1) * Math.PI); // 立上り早→緩やかに戻る
    const decay = 1 - p;
    if (emotion === "surprise") {
      translateY -= pulse * 42;
      scale *= 1 + pulse * 0.05;
      rotate += Math.sin(reactT * 1.8) * 3.5 * decay;
    } else if (emotion === "happy") {
      translateY -= pulse * 18;
      rotate += Math.sin(reactT * 2.2) * 2 * decay;
    }
  }

  // 焦り(panic)：小刻みに震える（発話有無に関わらず持続）。
  if (emotion === "panic") {
    rotate += Math.sin(time * Math.PI * 2 * 3.5) * 0.9;
  }

  // 発話者だけ柔らかい影で手前に浮かせる（区別は透明化ではなくポップ＋影で表現）。
  const filter = active
    ? "drop-shadow(0 8px 16px rgba(0,0,0,0.45))"
    : "none";

  // 立ち絵はバストアップ（ほぼ正方形クロップ）。大きめ・下端揃えで左右コーナーに配置。
  const wrap = (children: React.ReactNode) => (
    <div
      style={{
        width: 445,
        height: 445,
        position: "relative",
        filter,
        transform: `translateY(${translateY}px) scale(${scale}) rotate(${rotate}deg) scaleX(${flip ? -1 : 1})`,
        transformOrigin: "bottom center",
      }}
    >
      {children}
    </div>
  );

  const layer = (src: string, key: string, extra?: React.CSSProperties) => (
    <Img
      key={key}
      src={src}
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        objectFit: "contain",
        objectPosition: "center bottom",
        ...extra,
      }}
    />
  );

  // ── フォールバック（パーツ未配置）：従来の口開け/閉じ2枚を音量で切替 ──
  if (!hasParts) {
    const g = fallbackGender;
    const open = active && amplitude >= MOUTH_HALF;
    return wrap(
      <>
        {layer(staticFile(`avatars/${g}_close.png`), "fb-close")}
        {layer(staticFile(`avatars/${g}_open.png`), "fb-open", {
          opacity: open ? 1 : 0,
        })}
      </>
    );
  }

  // ── パーツ立ち絵 ──
  const part = (stem: string): string | null => {
    const fn = manifest![stem];
    return fn ? staticFile(`avatars/${dir}/${fn}`) : null;
  };

  // 口：発話者は音量で開き具合を決定。非発話者は待機口(mouth_idle、無ければmouth_close)。
  let mouthSrc: string | null;
  if (!active) {
    mouthSrc = part("mouth_idle") || part("mouth_close");
  } else {
    let mouthStem = "mouth_close";
    if (amplitude >= MOUTH_OPEN) mouthStem = "mouth_open";
    else if (amplitude >= MOUTH_HALF) mouthStem = "mouth_half";
    mouthSrc = part(mouthStem) || part("mouth_close") || part("mouth_open");
  }

  // 目：感情差分 > まばたき > 通常。
  const phase = hash(dir!) % BLINK_CYCLE;
  const blinking = (frame + phase) % BLINK_CYCLE >= BLINK_CYCLE - BLINK_DUR;
  // 笑顔の目(eye_smile)は閉じ目素材なので、happyが続く間ずっと出すと目を閉じっぱなしに見える。
  // → 感情発生から短い間だけ表示し、過ぎたら通常の開き目＋まばたきへ戻す。
  // 驚き目(eye_surprise)は開いた表情なので固定でも問題なし＝従来どおり。
  const EYE_SMILE_DUR = Math.round(fps * 1.0);
  const smileFresh = reactT >= 0 && reactT < EYE_SMILE_DUR;
  let eyeStem = "eye_open";
  if (emotion === "surprise" && part("eye_surprise")) eyeStem = "eye_surprise";
  // 焦り(panic): 専用の困り目が無ければ見開き目で慌てた表情に寄せる。
  else if (emotion === "panic" && (part("eye_trouble") || part("eye_surprise")))
    eyeStem = part("eye_trouble") ? "eye_trouble" : "eye_surprise";
  else if (emotion === "happy" && smileFresh && part("eye_smile")) eyeStem = "eye_smile";
  else if (blinking && part("eye_close")) eyeStem = "eye_close";
  const eyeSrc = part(eyeStem) || part("eye_open");

  // 効果オーバーレイ。驚き=一瞬だけ / 焦り(panic)=汗を出し続ける。
  // panic は専用 fx_sweat があれば使い、無ければ fx_surprise(=汗ドロップ)を流用。
  const showFxSurprise =
    emotion === "surprise" && reactT >= 0 && reactT < REACT_DUR;
  const fxSrc = showFxSurprise
    ? part("fx_surprise")
    : emotion === "panic"
    ? part("fx_sweat") || part("fx_surprise")
    : null;

  const surprised = emotion === "surprise";

  // ① 全身差し替えポーズ（pose_surprise があれば最優先）。
  //    完成1枚絵に丸ごと差し替える方式（口パクは止まる＝息を呑む表現）。驚き中ずっと表示。
  const poseSrc = surprised ? part("pose_surprise") : null;
  if (poseSrc) {
    return wrap(
      <>
        {layer(poseSrc, "pose")}
        {fxSrc ? layer(fxSrc, "fx") : null}
      </>
    );
  }

  // ② 腕レイヤー差し替え（arm_normal/arm_raise があれば）。
  //    驚き中は arm_raise（手を上げる）。口パク・まばたきは継続したまま腕だけ替わる。
  //    腕レイヤーが無いキャラ(例:めたん)は base に腕が含まれる前提で何もしない。
  let armStem = "arm_normal";
  if (surprised && part("arm_raise")) armStem = "arm_raise";
  const armSrc = part(armStem);

  return wrap(
    <>
      {layer(part("base")!, "base")}
      {armSrc ? layer(armSrc, "arm") : null}
      {eyeSrc ? layer(eyeSrc, "eye") : null}
      {mouthSrc ? layer(mouthSrc, "mouth") : null}
      {fxSrc ? layer(fxSrc, "fx") : null}
      {/* 焦り(panic)：汗を増やす。同じ汗ドロップを位置/サイズを変えて複数重ねる。 */}
      {emotion === "panic" && fxSrc ? (
        <>
          {layer(fxSrc, "fx-b", { transform: "translate(7%, -4%) scale(0.95)" })}
          {layer(fxSrc, "fx-c", { transform: "translate(-9%, 4%) scale(0.8)" })}
          {layer(fxSrc, "fx-d", { transform: "translate(13%, 6%) scale(0.7)" })}
        </>
      ) : null}
    </>
  );
};
