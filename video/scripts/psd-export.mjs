// 立ち絵PSDからパーツPNGを書き出す（ずんだもん/四国めたん）。
// 全部位を同一キャンバスに配置するので重ねれば位置が合う。
// 使い方:
//   node scripts/psd-export.mjs preview    <char>   … base+口/目候補をout/psd_preview_<char>/へ
//   node scripts/psd-export.mjs build      <char>   … 最終パーツをassets/avatars/<char>/へ
//   node scripts/psd-export.mjs build-full <char>   … 全身クロップをassets/avatars/<char>/full/へ
//   node scripts/psd-export.mjs candidates <char>   … 全候補PNGをassets/avatars/<char>/candidates/へ
//   <char> = zundamon | metan
import { readPsd, initializeCanvas } from "ag-psd";
import { createCanvas } from "@napi-rs/canvas";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

initializeCanvas(createCanvas);
const root = resolve(import.meta.dirname, "..");

// ─── スロット定義（眉/顔色の独立パーツ化。Stage1） ──────────────
// id=ASCII スラッグ、layer=PSD パス配列。
// expressions.json の id と一致させること。
const SLOTS = {
  zundamon: {
    cheek: {
      hoppe:     [["!顔色", "*ほっぺ"]],
      hoppe2:    [["!顔色", "*ほっぺ2"]],
      hoppe_red: [["!顔色", "*ほっぺ赤め"]],
      pale:      [["!顔色", "*青ざめ"]],
      shadow:    [["!顔色", "かげり"]],
    },
    brow: {
      normal:    [["!眉", "*普通眉"]],
      worry1:    [["!眉", "*困り眉1"]],
      worry2:    [["!眉", "*困り眉2"]],
      up:        [["!眉", "*上がり眉"]],
      angry:     [["!眉", "*怒り眉"]],
    },
    eye: {
      open:     [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      close:    [["!目", "*UU"]],
      surprise: [["!目", "*〇〇"]],
      smile:    [["!目", "*にっこり"]],
      happy:    [["!目", "*にっこり"]],
    },
    mouth: {
      close:       [["!口", "*むー"]],
      half:        [["!口", "*ほあ"]],
      open:        [["!口", "*ほあー"]],
      smile_close: [["!口", "*むふ"]],
      smile_open:  [["!口", "*ほあー"]],
    },
    fx: {
      sweat1: [["記号など", "汗1"]],
      sweat2: [["記号など", "汗2"]],
      sweat3: [["記号など", "汗3"]],
    },
  },
  metan: {
    cheek: {
      normal:   [["!顔色", "*普通"]],
      normal2:  [["!顔色", "*普通2"]],
      blush:    [["!顔色", "*赤面"]],
      pale:     [["!顔色", "*青ざめ"]],
      shadow:   [["!顔色", "かげり"]],
    },
    brow: {
      gokigen:      [["!眉", "*ごきげん"]],
      komari:       [["!眉", "*こまり"]],
      oko:          [["!眉", "*おこ"]],
      yayaoko:      [["!眉", "*ややおこ"]],
      futo_gokigen: [["!眉", "*太眉ごきげん"]],
      futo_komari:  [["!眉", "*太眉こまり"]],
      futo_oko:     [["!眉", "*太眉おこ"]],
    },
    eye: {
      open:     [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      close:    [["!目", "*目閉じ"]],
      surprise: [["!目", "*○○"]],
      smile:    [["!目", "*目閉じ2"]],
      happy:    [["!目", "*目閉じ"]],
    },
    mouth: {
      close:       [["!口", "*ほほえみ"]],
      half:        [["!口", "*お"]],
      open:        [["!口", "*わあー"]],
      smile_close: [["!口", "*ほほえみ"]],
      smile_open:  [["!口", "*わあー"]],
    },
    fx: {
      sweat: [["記号など", "汗"]],
    },
  },
};

// キャラごとの設定。body=眉・顔色を含まない土台レイヤー群。
const CHARS = {
  zundamon: {
    psd: "assets/avatars/zundamon/source/zunda_2.3.psd",
    // バストアップのクロップ（全部位共通＝重ねれば位置が合う）。手挙げ時の手も収まる範囲。
    crop: { x: 140, y: 90, w: 820, h: 780 },
    // body: 眉(!眉)と顔色(!顔色)を除いた土台。cheek/brow は SLOTS から書き出す。
    body: [
      ["尻尾的なアレ"],
      ["*服装1", "*いつもの服"],
      ["!枝豆", "*枝豆通常"],
      // 注: 以前の body には ["!顔色","*ほっぺ"] と ["!眉","*普通眉"] が含まれていたが
      // Stage1 以降は cheek/brow を SLOTS から独立書き出しするため除去。
    ],
    // arm: body とは別に書き出す腕パーツ（zundaのみ）。
    arm: {
      arm_normal: [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*基本"]],
      arm_raise:  [["*服装1", "!左腕", "*手を挙げる"], ["*服装1", "!右腕", "*手を挙げる"]],
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
    // 眉(!眉)と顔色(!顔色)は SLOTS から独立書き出しするため body から除去。
    body: [
      ["ツインドリル右"],
      ["ツインドリル左"],
      ["*白ロリ服", "!体"],
      ["*白ロリ服", "!右腕", "*普通"],
      ["*白ロリ服", "!左腕", "*普通"],
      // 注: 以前は ["!顔色","*普通2"] と ["!眉","*太眉ごきげん"] を body に含んでいたが除去。
      ["!前髪もみあげ"],
      ["頭部アクセサリ", "ヘッドドレス"],
      ["頭部アクセサリ", "髪留めハート"],
    ],
    arm: {}, // metan は腕なし（body に内包）
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

// バスト用: cfg.cropを使って書き出す
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

// 全身用: 任意のcropを受け取る（build-fullで使う）
function composeWithCrop(paths, crop) {
  const full = createCanvas(W, H);
  const ctx = full.getContext("2d");
  for (const p of paths) {
    const l = find(p);
    if (l.canvas) ctx.drawImage(l.canvas, l.left || 0, l.top || 0);
  }
  if (crop) {
    const { x, y, w, h } = crop;
    const out = createCanvas(w, h);
    out.getContext("2d").drawImage(full, -x, -y);
    return out.encodeSync ? out.encodeSync("png") : out.toBuffer("image/png");
  }
  return full.encodeSync ? full.encodeSync("png") : full.toBuffer("image/png");
}

// キャンバスの不透明ピクセルbboxを取得する。
function opaqueBbox(canvas) {
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  const imgData = ctx.getImageData(0, 0, width, height);
  const data = imgData.data;
  let minX = width, minY = height, maxX = 0, maxY = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const a = data[(y * width + x) * 4 + 3];
      if (a > 0) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (minX > maxX || minY > maxY) return null; // 全透明
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
}

// expressions.json から指定キャラの全表情で実際に使われている id を slot ごとに集計する。
function collectUsedIds(charKey) {
  const exprsPath = resolve(root, "public", "expressions.json");
  const exprs = JSON.parse(readFileSync(exprsPath, "utf8"));
  const charExprs = exprs[charKey];
  if (!charExprs) return {};
  const used = {}; // { slot: Set<id> }
  for (const exprCfg of Object.values(charExprs)) {
    if (exprCfg.brow) {
      if (!used.brow) used.brow = new Set();
      used.brow.add(exprCfg.brow);
    }
    if (exprCfg.cheek) {
      if (!used.cheek) used.cheek = new Set();
      used.cheek.add(exprCfg.cheek);
    }
    if (exprCfg.eye) {
      if (!used.eye) used.eye = new Set();
      used.eye.add(exprCfg.eye);
      // まばたきで "open" → "close" に差し替えるため、eye=open の場合は close も必要
      if (exprCfg.eye === "open") used.eye.add("close");
    }
    // mouth_close / mouth_half / mouth_open
    for (const level of ["mouth_close", "mouth_half", "mouth_open"]) {
      const id = exprCfg[level];
      if (id) {
        if (!used.mouth) used.mouth = new Set();
        used.mouth.add(id);
      }
    }
    if (exprCfg.fx) {
      if (!used.fx) used.fx = new Set();
      used.fx.add(exprCfg.fx);
    }
  }
  return used;
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
  const slots = SLOTS[charName];
  const usedIds = collectUsedIds(charName);

  // base（眉/顔色なし）
  writeFileSync(resolve(dir, "base.png"), compose(BODY));
  console.log(`[build] ${charName}/base.png`);

  // arm（zundaのみ）
  for (const [stem, paths] of Object.entries(cfg.arm || {})) {
    writeFileSync(resolve(dir, `${stem}.png`), compose(paths));
    console.log(`[build] ${charName}/${stem}.png`);
  }

  // 各スロット：使用 id のみ書き出す
  for (const slot of ["cheek", "brow", "eye", "mouth", "fx"]) {
    const slotDef = slots[slot];
    if (!slotDef) continue;
    const usedSet = usedIds[slot] ?? new Set();
    for (const id of usedSet) {
      const layerPaths = slotDef[id];
      if (!layerPaths) {
        console.warn(`[build] WARN: ${charName} ${slot}/${id} が SLOTS に未定義`);
        continue;
      }
      // mouth_* は既存 stem 名互換: mouth_<id>.png
      // eye_* / brow_* / cheek_* / fx_* も同様
      const stem = `${slot}_${id}`;
      writeFileSync(resolve(dir, `${stem}.png`), compose(layerPaths));
      console.log(`[build] ${charName}/${stem}.png`);
    }
  }

  const total = 1 + Object.keys(cfg.arm || {}).length +
    ["cheek", "brow", "eye", "mouth", "fx"].reduce((n, slot) => {
      return n + (usedIds[slot]?.size ?? 0);
    }, 0);
  console.log(`[build] done: ${total}部位 → ${dir}`);
}

if (mode === "build-full") {
  const slots = SLOTS[charName];
  const usedIds = collectUsedIds(charName);

  // 全パーツを収集（bbox算出のため）
  // base + arm + 各スロットの使用パーツ
  const allParts = [];
  allParts.push({ stem: "base", paths: BODY });
  for (const [stem, paths] of Object.entries(cfg.arm || {})) {
    allParts.push({ stem, paths });
  }
  for (const slot of ["cheek", "brow", "eye", "mouth", "fx"]) {
    const slotDef = slots[slot];
    if (!slotDef) continue;
    const usedSet = usedIds[slot] ?? new Set();
    for (const id of usedSet) {
      const layerPaths = slotDef[id];
      if (!layerPaths) continue;
      allParts.push({ stem: `${slot}_${id}`, paths: layerPaths });
    }
  }

  // step1: 全パーツの不透明領域 union bbox を算出（base 合成込みで計算）
  console.log(`[build-full] ${charName}: 全パーツbbox算出中...`);
  let uMinX = W, uMinY = H, uMaxX = 0, uMaxY = 0;
  for (const { stem, paths } of allParts) {
    const compPaths = paths === BODY ? BODY : [...BODY, ...paths];
    const canvas = createCanvas(W, H);
    const ctx = canvas.getContext("2d");
    for (const p of compPaths) {
      const l = find(p);
      if (l.canvas) ctx.drawImage(l.canvas, l.left || 0, l.top || 0);
    }
    const bb = opaqueBbox(canvas);
    if (bb) {
      if (bb.x < uMinX) uMinX = bb.x;
      if (bb.y < uMinY) uMinY = bb.y;
      if (bb.x + bb.w - 1 > uMaxX) uMaxX = bb.x + bb.w - 1;
      if (bb.y + bb.h - 1 > uMaxY) uMaxY = bb.y + bb.h - 1;
      console.log(`[build-full]   ${stem}: bbox x=${bb.x} y=${bb.y} w=${bb.w} h=${bb.h}`);
    } else {
      console.log(`[build-full]   ${stem}: 不透明ピクセルなし(スキップ)`);
    }
  }
  const fullCrop = { x: uMinX, y: uMinY, w: uMaxX - uMinX + 1, h: uMaxY - uMinY + 1 };
  console.log(`[build-full] union bbox: x=${fullCrop.x} y=${fullCrop.y} w=${fullCrop.w} h=${fullCrop.h}`);

  // step2: 全パーツを同一cropで書き出す（パーツ単体で書き出す＝重ね合わせ前提）。
  const dir = resolve(root, `assets/avatars/${charName}/full`);
  mkdirSync(dir, { recursive: true });
  const stems = [];
  for (const { stem, paths } of allParts) {
    const writePaths = paths === BODY ? BODY : paths;
    const buf = composeWithCrop(writePaths, fullCrop);
    writeFileSync(resolve(dir, `${stem}.png`), buf);
    stems.push(stem);
    console.log(`[build-full] ${charName}/full/${stem}.png`);
  }

  // step3: _box.json
  const box = { w: fullCrop.w, h: fullCrop.h };
  writeFileSync(resolve(dir, "_box.json"), JSON.stringify(box, null, 2));
  console.log(`[build-full] _box.json: ${JSON.stringify(box)}`);
  console.log(`[build-full] done: ${stems.length}部位 → ${dir}`);
}

if (mode === "candidates") {
  // 全スロット・全候補を単体透過PNG（base合成なし）で書き出す。
  // 同一クロップ(cfg.crop)で書き出すので重ねれば位置が合う。
  // プレビュー用途。base.png と arm_*.png はバスト用 assets/avatars/<char>/ から流用。
  const dir = resolve(root, `assets/avatars/${charName}/candidates`);
  mkdirSync(dir, { recursive: true });
  const slots = SLOTS[charName];
  let count = 0;

  for (const slot of ["cheek", "brow", "eye", "mouth", "fx"]) {
    const slotDef = slots[slot];
    if (!slotDef) continue;
    for (const [id, layerPaths] of Object.entries(slotDef)) {
      const stem = `${slot}_${id}`;
      const buf = compose(layerPaths);
      writeFileSync(resolve(dir, `${stem}.png`), buf);
      console.log(`[candidates] ${charName}/candidates/${stem}.png`);
      count++;
    }
  }

  console.log(`[candidates] done: ${count}枚 → ${dir}`);
}
