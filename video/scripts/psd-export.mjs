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
import { readFileSync, writeFileSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";

initializeCanvas(createCanvas);
const root = resolve(import.meta.dirname, "..");

// ─── スロット定義（眉/顔色の独立パーツ化。Stage1） ──────────────
// id=ASCII スラッグ、layer=PSD パス配列。
// expressions.json の id と一致させること。
const SLOTS = {
  zundamon: {
    edamame: {
      normal: [["!枝豆", "*枝豆通常"]],
      wilt:   [["!枝豆", "*枝豆萎え"]],
    },
    cheek: {
      hoppe:     [["!顔色", "*ほっぺ"]],
      hoppe2:    [["!顔色", "*ほっぺ2"]],
      hoppe_red: [["!顔色", "*ほっぺ赤め"]],
      pale:      [["!顔色", "*青ざめ"]],
    },
    // タスクA: かげり(影)を cheek から独立スロット化
    shadow: {
      kageri: [["!顔色", "かげり"]],
    },
    brow: {
      normal:    [["!眉", "*普通眉"]],
      worry1:    [["!眉", "*困り眉1"]],
      worry2:    [["!眉", "*困り眉2"]],
      up:        [["!眉", "*上がり眉"]],
      angry:     [["!眉", "*怒り眉"]],
    },
    eye: {
      open:         [["!目", "*目セット", "*普通白目"], ["!目", "*目セット", "!黒目", "*普通目"]],
      close:        [["!目", "*UU"]],
      surprise:     [["!目", "*〇〇"]],
      nikkori:      [["!目", "*にっこり"]],
      nikkori2:     [["!目", "*にっこり2"]],
      batsu:        [["!目", "*><"]],
      guruguru:     [["!目", "*ぐるぐる"]],
      nagomi:       [["!目", "*なごみ目"]],
      jito:         [["!目", "*ジト目"]],
      hosome:       [["!目", "*細め目"]],
      hosome_heart: [["!目", "*細め目ハート"]],
      uwamuki:      [["!目", "*上向き"]],
    },
    mouth: {
      close: [["!口", "*むー"]],
      half:  [["!口", "*ほあ"]],
      open:  [["!口", "*ほあー"]],
      mufu:  [["!口", "*むふ"]],
      tri:   [["!口", "*△"]],
      ho:    [["!口", "*ほー"]],
      naa:   [["!口", "*んあー"]],
      nhe:   [["!口", "*んへー"]],
      nn:    [["!口", "*んー"]],
      hahee: [["!口", "*はへえ"]],
      ohoo:  [["!口", "*おほお"]],
      o:     [["!口", "*お"]],
      yu:    [["!口", "*ゆ"]],
    },
    fx: {
      sweat1: [["記号など", "汗1"]],
      sweat2: [["記号など", "汗2"]],
      sweat3: [["記号など", "汗3"]],
      tears:  [["記号など", "涙"]],
    },
  },
  metan: {
    hair_back: {
      twin_drill: [["ツインドリル右"], ["ツインドリル左"]],
      ponytail: [["回転移動でポニテにするなど"]],
    },
    head_dress: {
      normal: [["頭部アクセサリ", "ヘッドドレス"]],
    },
    hair_clip: {
      heart: [["頭部アクセサリ", "髪留めハート"]],
      frill: [["頭部アクセサリ", "髪留めフリル"]],
    },
    cheek: {
      normal:   [["!顔色", "*普通"]],
      normal2:  [["!顔色", "*普通2"]],
      blush:    [["!顔色", "*赤面"]],
      pale:     [["!顔色", "*青ざめ"]],
    },
    // タスクA: かげり(影)を cheek から独立スロット化
    shadow: {
      kageri: [["!顔色", "かげり"]],
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
      tojime2:  [["!目", "*目閉じ2"]],
      batsu:    [["!目", "*><"]],
      guruguru: [["!目", "*ぐるぐる"]],
      miage:    [["!目", "*見上げ"]],
    },
    mouth: {
      close:    [["!口", "*ほほえみ"]],
      half:     [["!口", "*お"]],
      open:     [["!口", "*わあー"]],
      niyari:   [["!口", "*にやり"]],
      perori:   [["!口", "*ぺろり"]],
      tri:      [["!口", "*△"]],
      tri_down: [["!口", "*▽"]],
      momu:     [["!口", "*もむー"]],
      nn:       [["!口", "*んー"]],
      uee:      [["!口", "*うえー"]],
      ii:       [["!口", "*いー"]],
      mu:       [["!口", "*む"]],
      yu:       [["!口", "*ゆ"]],
    },
    fx: {
      sweat: [["記号など", "汗"]],
      tears: [["記号など", "涙"]],
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
      // 注: 以前の body には ["!顔色","*ほっぺ"] と ["!眉","*普通眉"] が含まれていたが
      // Stage1 以降は cheek/brow を SLOTS から独立書き出しするため除去。
      // 注: 枝豆は edamame スロットとして独立。未指定時は描画側で normal を重ねる。
    ],
    // arm: body とは別に書き出す腕パーツ。
    arm: {
      arm_normal: [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*基本"]],
      arm_raise:  [["*服装1", "!左腕", "*手を挙げる"], ["*服装1", "!右腕", "*手を挙げる"]],
    },
    // 候補棚卸し用。現行buildでは使わず、candidates出力のみに使う。
    armCandidates: {
      arm_normal:    [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*基本"]],
      arm_raise:     [["*服装1", "!左腕", "*手を挙げる"], ["*服装1", "!右腕", "*手を挙げる"]],
      arm_mouth:     [["*服装1", "!左腕", "*口元"], ["*服装1", "!右腕", "*口元"]],
      arm_suffering: [["*服装1", "!左腕", "*苦しむ"], ["*服装1", "!右腕", "*苦しむ"]],
      arm_waist:     [["*服装1", "!左腕", "*腰"], ["*服装1", "!右腕", "*腰"]],
      arm_whisper:   [["*服装1", "!左腕", "*ひそひそ"], ["*服装1", "!右腕", "*基本"]],
      arm_think:     [["*服装1", "!左腕", "*考える"], ["*服装1", "!右腕", "*基本"]],
      arm_point:     [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*指差し"]],
      arm_mic:       [["*服装1", "!左腕", "*基本"], ["*服装1", "!右腕", "*マイク"]],
    },
    // タスクB: zunda は bangs なし
    extra: {},
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
    // 先生役: 腕をbodyから外し、arm_* として差し替え可能にする。
    // 眉(!眉)と顔色(!顔色)は SLOTS から独立書き出しするため body から除去。
    // タスクB: 前髪もみあげ を body から除去し extra.bangs として独立パーツ化。
    //          PSD z順: 顔色(5) < 口(6) < 目(7) < 眉(8) < アクセサリ(9) < 前髪もみあげ(10)
    //          → 顔色は前髪の下になるので bangs を最前面レイヤーで重ねる必要がある。
    body: [
      ["*白ロリ服", "!体"],
      // 注: 以前は ["!顔色","*普通2"] と ["!眉","*太眉ごきげん"] を body に含んでいたが除去。
      // 注: ["!前髪もみあげ"] は extra.bangs として独立（タスクB）。
      // 注: ツインドリル/ポニーテール/ヘッドドレス/髪留めは表情スロットとして独立。
    ],
    arm: {
      arm_normal:   [["*白ロリ服", "!左腕", "*普通"], ["*白ロリ服", "!右腕", "*普通"]],
    },
    // 候補棚卸し兼 build 出力用。
    armCandidates: {
      arm_normal:   [["*白ロリ服", "!左腕", "*普通"], ["*白ロリ服", "!右腕", "*普通"]],
      arm_hush:     [["*白ロリ服", "!左腕", "*ひそひそ"], ["*白ロリ服", "!右腕", "*普通"]],
      arm_mouth:    [["*白ロリ服", "!左腕", "*口元に指"], ["*白ロリ服", "!右腕", "*普通"]],
      arm_hold:     [["*白ロリ服", "!左腕", "*抱える"], ["*白ロリ服", "!右腕", "*普通"]],
      arm_point:    [["*白ロリ服", "!左腕", "*普通"], ["*白ロリ服", "!右腕", "*指差す"]],
      arm_present:  [["*白ロリ服", "!左腕", "*普通"], ["*白ロリ服", "!右腕", "*手をかざす"]],
      arm_mic:      [["*白ロリ服", "!左腕", "*マイク"], ["*白ロリ服", "!右腕", "*普通"]],
      arm_manju:    [
        ["*白ロリ服", "!左腕", "*普通"],
        ["*白ロリ服", "!右腕", "*普通"],
        ["*白ロリ服", "!左腕", "まんじゅう袋"],
        ["*白ロリ服", "!右腕", "まんじゅう"],
      ],
    },
    // タスクB: 常時パーツ(bangs)。build/build-full で arm と同様に書き出す。
    extra: {
      bangs: [["!前髪もみあげ"]],
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
const PART_SLOTS = ["edamame", "hair_back", "head_dress", "hair_clip", "cheek", "shadow", "brow", "eye", "mouth", "fx"];

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
    if (charKey === "zundamon") {
      if (!used.edamame) used.edamame = new Set();
      used.edamame.add("normal");
      used.edamame.add("wilt");
      if (exprCfg.edamame) used.edamame.add(exprCfg.edamame);
    }
    if (charKey === "metan") {
      if (!used.hair_back) used.hair_back = new Set();
      used.hair_back.add("twin_drill");
      used.hair_back.add("ponytail");
      if (exprCfg.hair_back) used.hair_back.add(exprCfg.hair_back);
      if (!used.head_dress) used.head_dress = new Set();
      used.head_dress.add("normal");
      if (exprCfg.head_dress) used.head_dress.add(exprCfg.head_dress);
      if (!used.hair_clip) used.hair_clip = new Set();
      used.hair_clip.add("heart");
      used.hair_clip.add("frill");
      if (exprCfg.hair_clip) used.hair_clip.add(exprCfg.hair_clip);
    }
    if (exprCfg.brow) {
      if (!used.brow) used.brow = new Set();
      used.brow.add(exprCfg.brow);
    }
    if (exprCfg.cheek) {
      if (!used.cheek) used.cheek = new Set();
      used.cheek.add(exprCfg.cheek);
    }
    // タスクA: shadow スロット
    if (exprCfg.shadow) {
      if (!used.shadow) used.shadow = new Set();
      used.shadow.add(exprCfg.shadow);
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
      const fxIds = Array.isArray(exprCfg.fx) ? exprCfg.fx : [exprCfg.fx];
      for (const fxId of fxIds) if (fxId) used.fx.add(fxId);
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
  const armParts = { ...(cfg.arm || {}), ...(cfg.armCandidates || {}) };

  // base（眉/顔色なし）
  writeFileSync(resolve(dir, "base.png"), compose(BODY));
  console.log(`[build] ${charName}/base.png`);

  // arm（zundaのみ）
  for (const [stem, paths] of Object.entries(armParts)) {
    writeFileSync(resolve(dir, `${stem}.png`), compose(paths));
    console.log(`[build] ${charName}/${stem}.png`);
  }

  // タスクB: extra（常時パーツ: metan の bangs 等）
  for (const [partName, paths] of Object.entries(cfg.extra || {})) {
    writeFileSync(resolve(dir, `${partName}.png`), compose(paths));
    console.log(`[build] ${charName}/${partName}.png`);
  }

  // 各スロット：使用 id のみ書き出す（タスクA: shadow スロット追加）
  for (const slot of PART_SLOTS) {
    const slotDef = slots[slot];
    if (!slotDef) continue;
    const usedSet = usedIds[slot] ?? new Set();
    for (const id of usedSet) {
      const layerPaths = slotDef[id];
      if (!layerPaths) {
        console.warn(`[build] WARN: ${charName} ${slot}/${id} が SLOTS に未定義`);
        continue;
      }
      const stem = `${slot}_${id}`;
      writeFileSync(resolve(dir, `${stem}.png`), compose(layerPaths));
      console.log(`[build] ${charName}/${stem}.png`);
    }
  }

  const total = 1 + Object.keys(armParts).length + Object.keys(cfg.extra || {}).length +
    PART_SLOTS.reduce((n, slot) => {
      return n + (usedIds[slot]?.size ?? 0);
    }, 0);
  console.log(`[build] done: ${total}部位 → ${dir}`);
}

if (mode === "build-full") {
  const slots = SLOTS[charName];
  const usedIds = collectUsedIds(charName);
  const armParts = { ...(cfg.arm || {}), ...(cfg.armCandidates || {}) };

  // 全パーツを収集（bbox算出のため）
  // base + arm + extra + 各スロットの使用パーツ
  const allParts = [];
  allParts.push({ stem: "base", paths: BODY });
  for (const [stem, paths] of Object.entries(armParts)) {
    allParts.push({ stem, paths });
  }
  // タスクB: extra(bangs等)も union bbox に含める
  for (const [partName, paths] of Object.entries(cfg.extra || {})) {
    allParts.push({ stem: partName, paths });
  }
  for (const slot of PART_SLOTS) {
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
  // プレビュー用途。base.png はバスト用 assets/avatars/<char>/ から流用。
  const dir = resolve(root, `assets/avatars/${charName}/candidates`);
  mkdirSync(dir, { recursive: true });
  // 旧候補(削除/改名されたid)が残らないよう、既存のpngを一旦掃除してから書き出す。
  for (const f of readdirSync(dir)) {
    if (f.toLowerCase().endsWith(".png")) rmSync(resolve(dir, f));
  }
  const slots = SLOTS[charName];
  let count = 0;

  for (const [stem, layerPaths] of Object.entries(cfg.armCandidates || {})) {
    const buf = compose(layerPaths);
    writeFileSync(resolve(dir, `${stem}.png`), buf);
    console.log(`[candidates] ${charName}/candidates/${stem}.png`);
    count++;
  }

  for (const slot of PART_SLOTS) {
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
