// パーツ分け立ち絵の「プレースホルダー」をローカル生成する（外部サービス不使用）。
// 本物のずんだもん/四国めたんの立ち絵が手元に無くても、重ね合わせ・リップシンク・
// まばたき・オーバーアクションの動作を確認できるようにするための簡易SVG部位画像。
//
// ★本番では assets/avatars/<キャラ>/ に同じstem名（base, mouth_open …）の
//   本物PNGを置き、ここで生成したSVGプレースホルダーは削除すること。
//   prep.mjsが拡張子問わずstem名でmanifest化するため、PNGを入れたらSVGは消す
//   （同stemが二重に存在すると意図しない方が使われる）。
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const W = 500;
const H = 650;

// 部位の基準座標（全部位で共通。重ねたときに位置が合う）
const EYE_Y = 215;
const EYE_LX = 205;
const EYE_RX = 295;
const MOUTH_X = 250;
const MOUTH_Y = 300;

const svg = (body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${body}</svg>`;

// キャラ定義（プレースホルダー用の色）
const chars = {
  zundamon: {
    skin: "#fce4c8",
    hair: "#79c14a",
    accent: "#3f7a23",
    cloth: "#eaeaea",
    eye: "#3f6a2a",
    // 頭上の枝豆スプラウト
    crown: `<path d="M250 70 q-40 -55 -8 -70 q22 24 8 70 z" fill="#8fd45a" stroke="#3f7a23" stroke-width="3"/>
            <path d="M250 70 q40 -55 8 -70 q-22 24 -8 70 z" fill="#8fd45a" stroke="#3f7a23" stroke-width="3"/>`,
  },
  metan: {
    skin: "#fce4c8",
    hair: "#2e2440",
    accent: "#e86aa0",
    cloth: "#3a3550",
    eye: "#7a3a6a",
    crown: `<path d="M250 95 q-70 -10 -120 30 q70 -8 120 6 z" fill="#2e2440"/>
            <path d="M250 95 q70 -10 120 30 q-70 -8 -120 6 z" fill="#2e2440"/>`,
  },
};

// 土台（体＋頭＋髪。目と口は別レイヤなので描かない）
const base = (c) =>
  svg(`
    <!-- 体 -->
    <path d="M120 650 L120 470 Q120 410 180 400 L320 400 Q380 410 380 470 L380 650 Z" fill="${c.cloth}" stroke="${c.accent}" stroke-width="4"/>
    <!-- 首 -->
    <rect x="225" y="350" width="50" height="70" fill="${c.skin}"/>
    <!-- 顔 -->
    <circle cx="250" cy="210" r="130" fill="${c.skin}"/>
    <!-- 髪（後ろ＋前髪） -->
    <path d="M120 220 Q120 80 250 80 Q380 80 380 220 L380 150 Q300 100 250 120 Q200 100 120 150 Z" fill="${c.hair}"/>
    <path d="M130 200 Q150 120 250 120 Q350 120 370 200 Q300 150 250 165 Q200 150 130 200 Z" fill="${c.hair}"/>
    ${c.crown}
    <!-- ほお -->
    <circle cx="${EYE_LX - 25}" cy="255" r="16" fill="${c.accent}" opacity="0.25"/>
    <circle cx="${EYE_RX + 25}" cy="255" r="16" fill="${c.accent}" opacity="0.25"/>
  `);

const eyePair = (inner) =>
  svg(`
    <g transform="translate(${EYE_LX} ${EYE_Y})">${inner}</g>
    <g transform="translate(${EYE_RX} ${EYE_Y})">${inner}</g>
  `);

const eye_open = (c) =>
  eyePair(`<ellipse cx="0" cy="0" rx="22" ry="28" fill="#fff" stroke="#333" stroke-width="3"/>
           <circle cx="0" cy="2" r="13" fill="${c.eye}"/><circle cx="-4" cy="-3" r="4" fill="#fff"/>`);

const eye_close = (c) =>
  eyePair(`<path d="M-22 0 Q0 16 22 0" fill="none" stroke="#333" stroke-width="4" stroke-linecap="round"/>`);

const eye_surprise = (c) =>
  eyePair(`<ellipse cx="0" cy="0" rx="28" ry="34" fill="#fff" stroke="#333" stroke-width="3"/>
           <circle cx="0" cy="2" r="10" fill="${c.eye}"/><circle cx="-3" cy="-2" r="3" fill="#fff"/>`);

const eye_smile = (c) =>
  eyePair(`<path d="M-22 4 Q0 -18 22 4" fill="none" stroke="#333" stroke-width="5" stroke-linecap="round"/>`);

const mouth = (d) => svg(`<g transform="translate(${MOUTH_X} ${MOUTH_Y})">${d}</g>`);
const mouth_close = () =>
  mouth(`<path d="M-30 0 Q0 10 30 0" fill="none" stroke="#a14a3a" stroke-width="5" stroke-linecap="round"/>`);
const mouth_half = () =>
  mouth(`<ellipse cx="0" cy="3" rx="26" ry="14" fill="#7a2a2a"/><path d="M-22 0 Q0 -6 22 0" fill="#fff"/>`);
const mouth_open = () =>
  mouth(`<ellipse cx="0" cy="6" rx="30" ry="26" fill="#7a2a2a"/><ellipse cx="0" cy="18" rx="16" ry="9" fill="#e06a6a"/>`);

// びっくりマーク（驚き効果オーバーレイ）
const fx_surprise = (c) =>
  svg(`
    <g transform="translate(395 95) rotate(12)" fill="#ffd23f" stroke="#c08a00" stroke-width="3">
      <rect x="-7" y="-40" width="14" height="40" rx="6"/><circle cx="0" cy="12" r="9"/>
    </g>
    <g transform="translate(430 140) rotate(20)" fill="#ffd23f" stroke="#c08a00" stroke-width="2">
      <rect x="-4" y="-22" width="9" height="22" rx="4"/><circle cx="0" cy="8" r="5"/>
    </g>
  `);

let n = 0;
for (const [name, c] of Object.entries(chars)) {
  const dir = resolve(root, "assets", "avatars", name);
  mkdirSync(dir, { recursive: true });
  const parts = {
    base: base(c),
    eye_open: eye_open(c),
    eye_close: eye_close(c),
    eye_surprise: eye_surprise(c),
    eye_smile: eye_smile(c),
    mouth_close: mouth_close(),
    mouth_half: mouth_half(),
    mouth_open: mouth_open(),
    fx_surprise: fx_surprise(c),
  };
  // ずんだもんのみ腕レイヤー（通常/上げ）。めたんは腕をbaseに含める想定で用意しない。
  if (name === "zundamon") {
    parts.arm_normal = svg(`
      <g fill="${c.skin}" stroke="${c.accent}" stroke-width="3">
        <rect x="104" y="430" width="46" height="160" rx="23"/>
        <rect x="350" y="430" width="46" height="160" rx="23"/>
      </g>`);
    parts.arm_raise = svg(`
      <g fill="${c.skin}" stroke="${c.accent}" stroke-width="3">
        <rect x="70" y="150" width="42" height="160" rx="21" transform="rotate(-20 91 230)"/>
        <rect x="388" y="150" width="42" height="160" rx="21" transform="rotate(20 409 230)"/>
        <circle cx="74" cy="150" r="24"/>
        <circle cx="426" cy="150" r="24"/>
      </g>`);
  }

  for (const [stem, data] of Object.entries(parts)) {
    writeFileSync(resolve(dir, `${stem}.svg`), data);
    n++;
  }
  console.log(`[make-avatar-parts] ${name}: ${Object.keys(parts).length}部位`);
}
console.log(`[make-avatar-parts] 合計${n}枚生成（assets/avatars/<キャラ>/*.svg）`);
