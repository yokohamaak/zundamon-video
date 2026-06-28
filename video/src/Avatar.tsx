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
// 重ね順: base → shadow → cheek → arm → eye → mouth → bangs(前髪) → brow(眉・最前面寄り) → fx
//
// 必要パーツ（assets/avatars/<キャラ>/、stem名で参照）:
//   base                               … 口・目・眉・顔色を除いた土台（必須）
//   cheek_<id>                         … 顔色（Stage1から追加）
//   shadow_<id>                        … かげり（独立スロット・TaskA）。null/未設定なら描かない。
//   arm_normal / arm_raise             … 腕（zundaのみ）
//   brow_<id>                          … 眉（Stage1から追加）
//   eye_open / eye_close / eye_<id>    … 目の開閉（まばたき）
//   mouth_close / mouth_half / mouth_open … 口の開き3段（リップシンク）
//   bangs                              … 前髪もみあげ（metanのみ・常時最前面・TaskB）
//   fx_<id>                            … 任意。効果オーバーレイ
// パーツが無いキャラは従来の単一画像(gender_open/close)へ自動フォールバック。

type Manifest = Record<string, string>;

// 表情設定（expressions.json の 1エントリ）。
export type ExpressionCfg = {
  brow: string | null;
  cheek: string | null;
  // タスクA: かげり独立スロット。null/未定義なら描かない。
  shadow?: string | null;
  eye: string;
  mouth_close: string;
  mouth_half: string;
  mouth_open: string;
  fx: string | null;
};

// 焦り(panic)時に追加する汗ドロップの位置（キャラ別・キャンバス比%）。
// 元の汗の位置を基準に、目に被らない側へずらす。
const SWEAT_EXTRA: Record<string, { dx: number; dy: number; s: number }[]> = {
  // ずんだもん：元の汗は右こめかみ(0.60,0.34)。目の下〜口の高さの右頬に収める。
  // （あご下=y0.55超に行くと空中に浮くので dy は控えめに）
  zundamon: [
    { dx: -5, dy: 12, s: 0.85 }, // 右頬・上（≈0.59,0.48）
    { dx: -6, dy: 15, s: 0.75 }, // 右頬（≈0.55,0.50）
  ],
  // めたん：元の汗は左頬(0.40,0.50)。左頬に小さく散らす（目y0.41・口y0.47の下側）。
  metan: [
    { dx: -7, dy: -2, s: 0.8 }, // ≈0.43,0.51
    { dx: -4, dy: -4, s: 0.75 }, // ≈0.36,0.53
  ],
};

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
  // 発話時に少し拡大する（既存の挙動）。falseで拡大しない。既定true。
  popScale?: boolean;
  // 立ち絵ボックスのサイズ（任意）。未指定時は 445×445（バスト用・後方互換）。
  // 全身立ち絵ではキャンバスのアスペクト比に合わせた値を渡す。
  boxWidth?: number;
  boxHeight?: number;
  // 表情設定（expressions.json の該当エントリ）。
  // 指定がなければ旧来の emotion ベースのフォールバック挙動。
  expressionCfg?: ExpressionCfg | null;
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
  popScale = true,
  boxWidth = 445,
  boxHeight = 445,
  expressionCfg,
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
  let scale = active && popScale ? 1.04 + interpolate(bounce, [0, 1], [0, 0.04]) : 1.0;
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

  // 立ち絵ボックス。boxWidth/boxHeightが指定されると全身サイズにもなる（後方互換: 既定445×445）。
  const wrap = (children: React.ReactNode) => (
    <div
      style={{
        width: boxWidth,
        height: boxHeight,
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

  // ── expressionCfg が指定されているか否かで分岐 ──
  const hasCfg = expressionCfg != null;

  // ── 口の選択 ──
  let mouthSrc: string | null;
  if (hasCfg) {
    const cfg = expressionCfg!;
    // amplitude → level → cfg.mouth_<level> id → stem
    let level: "close" | "half" | "open" = "close";
    if (active) {
      if (amplitude >= MOUTH_OPEN) level = "open";
      else if (amplitude >= MOUTH_HALF) level = "half";
    }
    const mouthId = cfg[`mouth_${level}` as "mouth_close" | "mouth_half" | "mouth_open"];
    // stem 名: mouth_<id>（例: mouth_close, mouth_smile_close 等）
    mouthSrc = part(`mouth_${mouthId}`) || part("mouth_close") || null;
  } else {
    // 旧来の emotion ベース（後方互換）
    const isHappy = emotion === "happy";
    if (!active) {
      mouthSrc =
        (isHappy ? part("mouth_smile_close") : null) ||
        part("mouth_idle") ||
        part("mouth_close");
    } else {
      let mouthStem = "mouth_close";
      if (amplitude >= MOUTH_OPEN) mouthStem = "mouth_open";
      else if (amplitude >= MOUTH_HALF) mouthStem = "mouth_half";
      if (isHappy) {
        const happyStem =
          mouthStem === "mouth_close" ? "mouth_smile_close" : "mouth_smile_open";
        mouthSrc =
          part(happyStem) || part(mouthStem) || part("mouth_close") || part("mouth_open");
      } else {
        mouthSrc = part(mouthStem) || part("mouth_close") || part("mouth_open");
      }
    }
  }

  // ── 目の選択 ──
  const phase = hash(dir!) % BLINK_CYCLE;
  const blinking = (frame + phase) % BLINK_CYCLE >= BLINK_CYCLE - BLINK_DUR;
  const EYE_SMILE_DUR = Math.round(fps * 1.0);
  const smileFresh = reactT >= 0 && reactT < EYE_SMILE_DUR;

  let eyeStem: string;
  if (hasCfg) {
    const cfg = expressionCfg!;
    // cfg.eye を基本に、eye==="open" の時だけまばたきで "close" に差し替え。
    const baseEyeId = cfg.eye;
    if (baseEyeId === "open" && blinking) {
      eyeStem = "eye_close";
    } else {
      eyeStem = `eye_${baseEyeId}`;
    }
  } else {
    // 旧来の emotion ベース（後方互換）
    eyeStem = "eye_open";
    if (emotion === "surprise" && part("eye_surprise")) eyeStem = "eye_surprise";
    else if (emotion === "sad" && part("eye_trouble")) eyeStem = "eye_trouble";
    else if (emotion === "panic" && (part("eye_trouble") || part("eye_surprise")))
      eyeStem = part("eye_trouble") ? "eye_trouble" : "eye_surprise";
    else if (emotion === "happy" && part("eye_happy")) eyeStem = "eye_happy";
    else if (emotion === "happy" && smileFresh && part("eye_smile")) eyeStem = "eye_smile";
    else if (blinking && part("eye_close")) eyeStem = "eye_close";
  }
  const eyeSrc = part(eyeStem) || part("eye_open");

  // ── fx（効果オーバーレイ）の選択 ──
  // タイミングロジックは旧来どおり emotion で分岐。表示する画像は cfg.fx から引く。
  const showFxSurprise =
    emotion === "surprise" && reactT >= 0 && reactT < REACT_DUR;

  let fxSrc: string | null;
  if (hasCfg) {
    const cfg = expressionCfg!;
    if (showFxSurprise) {
      // surprise 反応中のみ fx を出す（cfg.fx が null でも出さない）
      fxSrc = cfg.fx ? part(`fx_${cfg.fx}`) : null;
    } else if (emotion === "panic") {
      // panic は継続して fx を出す
      fxSrc = cfg.fx ? part(`fx_${cfg.fx}`) : null;
    } else {
      fxSrc = null;
    }
  } else {
    // 旧来の emotion ベース（後方互換）
    fxSrc = showFxSurprise
      ? part("fx_surprise")
      : emotion === "panic"
      ? part("fx_sweat") || part("fx_surprise")
      : null;
  }

  // ── cheek（顔色）の解決 ──
  const cheekSrc = hasCfg && expressionCfg!.cheek
    ? part(`cheek_${expressionCfg!.cheek}`) || null
    : null;

  // ── shadow（かげり）の解決（タスクA: 独立スロット） ──
  // null/未定義なら描かない。cheekの直後に重ねる。
  const shadowSrc = hasCfg && expressionCfg!.shadow
    ? part(`shadow_${expressionCfg!.shadow}`) || null
    : null;

  // ── brow（眉）の解決 ──
  const browSrc = hasCfg && expressionCfg!.brow
    ? part(`brow_${expressionCfg!.brow}`) || null
    : null;

  // ── bangs（前髪もみあげ）の解決（タスクB: metanのみ常時パーツ） ──
  // manifest に "bangs" があれば重ねる（zundaは無いのでスキップ）。
  const bangsSrc = part("bangs") || null;

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

  // ③ 重ね順: base → shadow → cheek → arm → eye → mouth → bangs → brow → fx（眉は髪より前面）
  //    （!顔色グループ内は下→上が かげり→青ざめ。shadow(かげり)を cheek より下に）
  //    （タスクB: bangsをmouthの後・fxの前に追加。metanのみ、zundaはnull）
  return wrap(
    <>
      {layer(part("base")!, "base")}
      {/* PSDの!顔色グループ内は下→上が かげり→青ざめ→…。よって shadow(かげり)を
          cheek(青ざめ/ほっぺ等)より下に描く。 */}
      {shadowSrc ? layer(shadowSrc, "shadow") : null}
      {cheekSrc ? layer(cheekSrc, "cheek") : null}
      {armSrc ? layer(armSrc, "arm") : null}
      {eyeSrc ? layer(eyeSrc, "eye") : null}
      {mouthSrc ? layer(mouthSrc, "mouth") : null}
      {bangsSrc ? layer(bangsSrc, "bangs") : null}
      {/* 眉は髪/前髪より前面に描く（前髪に隠れず必ず見えるように）。 */}
      {browSrc ? layer(browSrc, "brow") : null}
      {fxSrc ? layer(fxSrc, "fx") : null}
      {/* 焦り(panic)：汗を増やす。同じ汗ドロップを位置/サイズ違いで複数重ねる。
          位置はキャラごと（目に被らないよう調整）。dx/dyはキャンバス比%。 */}
      {emotion === "panic" && fxSrc
        ? (SWEAT_EXTRA[dir ?? ""] ?? []).map((o, i) =>
            layer(fxSrc!, `fx-${i}`, {
              transform: `translate(${o.dx}%, ${o.dy}%) scale(${o.s})`,
            })
          )
        : null}
    </>
  );
};
