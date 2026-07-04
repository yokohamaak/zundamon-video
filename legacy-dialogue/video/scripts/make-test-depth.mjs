// 検証用の合成深度マップを生成（torch不要）。中央=近(明)・周辺=遠(暗)＋下ほど近。
// 本番の深度は make_depth.py（Mac/ローカル）で画像から推定する。これはシェーダ動作確認用。
// 使い方: node scripts/make-test-depth.mjs <出力PNGパス> [幅] [高さ]
import { deflateSync } from "node:zlib";
import { writeFileSync } from "node:fs";

const out = process.argv[2] || "public/test.depth.png";
const W = parseInt(process.argv[3] || "640");
const H = parseInt(process.argv[4] || "360");

// グレースケール各画素（0=遠,255=近）。中央放射＋下方を近く。
const raw = Buffer.alloc((W + 1) * H);
let o = 0;
for (let y = 0; y < H; y++) {
  raw[o++] = 0; // 各行先頭のフィルタバイト
  for (let x = 0; x < W; x++) {
    const nx = (x / W - 0.5) * 2;
    const ny = (y / H - 0.5) * 2;
    const r = Math.sqrt(nx * nx + ny * ny) / Math.SQRT2; // 0(中心)→1(隅)
    const radial = 1 - r; // 中心ほど近い
    const vertical = (y / H) * 0.5; // 下ほど近い
    const d = Math.max(0, Math.min(1, radial * 0.7 + vertical * 0.6));
    raw[o++] = Math.round(d * 255);
  }
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const t = Buffer.from(type, "ascii");
  const body = Buffer.concat([t, data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body) >>> 0, 0);
  return Buffer.concat([len, body, crc]);
}
function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c;
}

const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(W, 0);
ihdr.writeUInt32BE(H, 4);
ihdr[8] = 8; // bit depth
ihdr[9] = 0; // color type 0 = grayscale
const png = Buffer.concat([
  sig,
  chunk("IHDR", ihdr),
  chunk("IDAT", deflateSync(raw)),
  chunk("IEND", Buffer.alloc(0)),
]);
writeFileSync(out, png);
console.log(`wrote ${out} (${W}x${H} grayscale depth)`);
