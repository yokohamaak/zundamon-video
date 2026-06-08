// 立ち絵PSDからパーツPNGを書き出す（ずんだもん/四国めたん）。
// 全部位を同一キャンバスに配置するので重ねれば位置が合う。
// 使い方:
//   node scripts/psd-export.mjs preview <char>   … base+口/目候補をout/psd_preview_<char>/へ
//   node scripts/psd-export.mjs build   <char>   … 最終パーツをassets/avatars/<char>/へ
//   <char> = zundamon | metan
import { readPsd, initializeCanvas } from "ag-psd";
import { createCanvas } from "@napi-rs/canvas";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

initializeCanvas(createCanvas);
const root = resolve(import.meta.dirname, "..");

// キャラごとの設定。BODY=腕/口/目を含まない土台。build=確定マッピング。
const CHARS = {
  zundamon: {
    psd: "assets/avatars/zundamon/source/zunda_2.3.psd",
    // バストアップのクロップ（全部位共通＝重ねれば位置が合う）。手挙げ時の手も収まる範囲。
    crop: { x: 140, y: 90, w: 820, h: 780 },
    body: [
      ["尻尾的なアレ"],
      ["*服装1", "*いつもの服"],
      ["!枝豆", "*枝豆通常"],
      ["!顔色", "*ほっぺ"],
      ["!眉", "*普通眉"],
    ],
    build: {
      base: "BODY",
      arm_normal: [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*基本"]],
      arm_raise: [["*服装1", "!左腕", "*手を挙げる"], ["*服装1", "!右腕", "*手を挙げる"]],
      mouth_close: [["!口", "*むー"]],
      mouth_half: [["!口", "*ほあ"]],
      mouth_open: [["!口", "*ほあー"]],
      eye_open: [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      eye_close: [["!目", "*UU"]],
      eye_surprise: [["!目", "*〇〇"]],
      eye_smile: [["!目", "*にっこり"]],
      fx_surprise: [["記号など", "汗1"]],
    },
    previewMouths: ["*むー", "*んー", "*△", "*ほあ", "*ほー", "*ほあー", "*お", "*おほお"],
    previewEyes: {
      "目セット普通": [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      "UU": [["!目", "*UU"]],
      "〇〇": [["!目", "*〇〇"]],
      "にっこり": [["!目", "*にっこり"]],
    },
  },
  metan: {
    psd: "assets/avatars/metan/source/metan_2.1.psd",
    crop: { x: 110, y: 30, w: 840, h: 800 },
    // 先生役: 腕差分なし。腕(普通)を土台に焼き込む。
    body: [
      ["ツインドリル右"],
      ["ツインドリル左"],
      ["*白ロリ服", "!体"],
      ["*白ロリ服", "!右腕", "*普通"],
      ["*白ロリ服", "!左腕", "*普通"],
      ["!顔色", "*普通2"],
      ["!眉", "*太眉ごきげん"],
      ["!前髪もみあげ"],
      ["頭部アクセサリ", "ヘッドドレス"],
      ["頭部アクセサリ", "髪留めハート"],
    ],
    build: {
      base: "BODY",
      // 閉じ口は発話中の語間/待機の両方で使う。への字を避け常に口角UP(ほほえみ)。
      mouth_close: [["!口", "*ほほえみ"]],
      mouth_half: [["!口", "*お"]],
      mouth_open: [["!口", "*わあー"]],
      eye_open: [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      eye_close: [["!目", "*目閉じ"]],
      eye_surprise: [["!目", "*○○"]],
      eye_smile: [["!目", "*目閉じ2"]],
      fx_surprise: [["記号など", "汗"]],
    },
    previewMouths: ["*む", "*んー", "*△", "*ほほえみ", "*お", "*わあー", "*▽", "*いー"],
    previewEyes: {
      "目セット普通": [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      "目閉じ": [["!目", "*目閉じ"]],
      "目閉じ2": [["!目", "*目閉じ2"]],
      "○○": [["!目", "*○○"]],
      "見開き": [["!目", "*目セット", "*見開き白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
    },
  },
};

const mode = process.argv[2] || "preview";
const charName = process.argv[3] || "zundamon";
const cfg = CHARS[charName];
if (!cfg) throw new Error("未知のキャラ: " + charName + " (zundamon|metan)");

const psd = readPsd(readFileSync(resolve(root, cfg.psd)), {
  skipCompositeImageData: true,
  skipThumbnail: true,
});
const W = psd.width, H = psd.height;

function find(path, layers = psd.children) {
  const [head, ...rest] = path;
  const node = (layers || []).find((l) => l.name === head);
  if (!node) throw new Error("レイヤーが見つからない: " + path.join(" / "));
  return rest.length ? find(rest, node.children) : node;
}

function compose(paths) {
  const full = createCanvas(W, H);
  const ctx = full.getContext("2d");
  for (const p of paths) {
    const l = find(p);
    if (l.canvas) ctx.drawImage(l.canvas, l.left || 0, l.top || 0);
  }
  // バストアップ等のクロップ（指定があれば）。全部位同一cropなので重ね位置は保たれる。
  let out = full;
  if (cfg.crop) {
    const { x, y, w, h } = cfg.crop;
    out = createCanvas(w, h);
    out.getContext("2d").drawImage(full, -x, -y);
  }
  return out.encodeSync ? out.encodeSync("png") : out.toBuffer("image/png");
}

const BODY = cfg.body;

if (mode === "preview") {
  const dir = resolve(root, `out/psd_preview_${charName}`);
  mkdirSync(dir, { recursive: true });
  const w = (n, b) => writeFileSync(resolve(dir, n), b);
  w("base.png", compose(BODY));
  for (const m of cfg.previewMouths)
    w(`mouth_${m.replace(/\*/g, "")}.png`, compose([...BODY, ["!口", m]]));
  for (const [n, sel] of Object.entries(cfg.previewEyes))
    w(`eye_${n}.png`, compose([...BODY, ...sel]));
  console.log("preview written to", dir);
}

if (mode === "build") {
  const dir = resolve(root, `assets/avatars/${charName}`);
  for (const [stem, sel] of Object.entries(cfg.build)) {
    const paths = sel === "BODY" ? BODY : sel;
    writeFileSync(resolve(dir, `${stem}.png`), compose(paths));
    console.log(`[build] ${charName}/${stem}.png`);
  }
  console.log(`[build] ${Object.keys(cfg.build).length}部位を書き出し: ${dir}`);
}
