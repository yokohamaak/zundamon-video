/**
 * expression-catalog.mjs
 * 各キャラの表情カタログ画像を生成する
 * 実行: cd /workspace/video && node scripts/expression-catalog.mjs
 */

import { createCanvas, loadImage, GlobalFonts } from '@napi-rs/canvas';
import { existsSync, mkdirSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..');
const ASSETS = join(REPO_ROOT, 'assets', 'avatars');
const FONTS = join(REPO_ROOT, 'assets', 'fonts');
const OUT = join(REPO_ROOT, 'out');

// フォント登録
const fontPath400 = join(FONTS, 'japanese-400-normal.woff2');
const fontPath700 = join(FONTS, 'japanese-700-normal.woff2');
let fontRegistered = false;
if (existsSync(fontPath400)) {
  GlobalFonts.registerFromPath(fontPath400, 'JP');
  fontRegistered = true;
  console.log('Font registered: japanese-400-normal.woff2');
}
if (existsSync(fontPath700)) {
  GlobalFonts.registerFromPath(fontPath700, 'JP-Bold');
  console.log('Font registered: japanese-700-normal.woff2');
}
if (!fontRegistered) {
  console.warn('WARNING: No JP font registered. Japanese text may not render correctly.');
}

// 出力ディレクトリ
if (!existsSync(OUT)) mkdirSync(OUT, { recursive: true });

// ---- 元PSDレイヤー名マップ ----
// タスクC: 旧stem(eye_happy/eye_smile/mouth_smile_*)を新id(nikkori/mufu等)に更新
const LAYER_NAMES = {
  zundamon: {
    eye_open:     '目セット普通',
    eye_nikkori:  'にっこり',
    eye_surprise: '〇〇',
    eye_close:    'UU',
    mouth_close:  'むー',
    mouth_half:   'ほあ',
    mouth_open:   'ほあー',
    mouth_mufu:   'むふ',
    fx_sweat1:    '汗1',
    fx_sweat2:    '汗2',
    fx_sweat3:    '汗3',
    arm_normal:   '腕(通常)',
    edamame_normal: '枝豆通常',
    edamame_wilt:   '枝豆萎え',
  },
  metan: {
    hair_back_twin_drill: 'ツインドリル',
    hair_back_ponytail:   'ポニーテール',
    head_dress_normal:    'ヘッドドレス',
    hair_clip_heart:      '髪留めハート',
    hair_clip_frill:      '髪留めフリル',
    eye_open:     '目セット普通',
    eye_close:    '目閉じ',
    eye_surprise: '○○',
    mouth_close:  'ほほえみ',
    mouth_half:   'お',
    mouth_open:   'わあー',
    fx_sweat:     '汗',
  },
};

// ---- 表情定義 ----
// タスクC: 旧stem(eye_happy/eye_smile/mouth_smile_*) → 新id(eye_nikkori/mouth_mufu等)。
// expressions.json の実値を参照して解決するため、カタログはstemの存在確認のみ。
const EXPRESSIONS = [
  {
    name: 'normal',
    label: '通常',
    eye: ['eye_open'],
    mouth_close: ['mouth_close'],
    mouth_open: ['mouth_open'],
    fx: [],
  },
  {
    name: 'happy',
    label: 'うれしい',
    // zunda: eye_nikkori / metan: eye_close
    eye: ['eye_nikkori', 'eye_close'],
    // zunda: mouth_mufu / metan: mouth_close
    mouth_close: ['mouth_mufu', 'mouth_close'],
    mouth_open: ['mouth_open'],
    fx: [],
  },
  {
    name: 'surprise',
    label: 'おどろき',
    eye: ['eye_surprise'],
    mouth_close: ['mouth_close'],
    mouth_open: ['mouth_open'],
    // zunda: fx_sweat1 / metan: fx_sweat (フォールバックなし)
    fx: ['fx_sweat1', 'fx_sweat'],
  },
  {
    name: 'trouble',
    label: 'こまった',
    eye: ['eye_close', 'eye_open'],      // metan: eye_close / zunda: eye_open
    mouth_close: ['mouth_close'],
    mouth_open: ['mouth_open'],
    fx: ['fx_sweat', 'fx_sweat2'],       // metan: fx_sweat / zunda: fx_sweat2
  },
  {
    name: 'panic',
    label: 'パニック',
    eye: ['eye_surprise'],
    mouth_close: ['mouth_close'],
    mouth_open: ['mouth_open'],
    fx: ['fx_sweat2', 'fx_sweat'],       // zunda: fx_sweat2 / metan: fx_sweat
  },
];

// ---- ユーティリティ ----

/** stemリストのうち最初に存在するファイルを返す。{path, stem} or null */
function resolveFirst(dir, stems) {
  for (const stem of stems) {
    const p = join(dir, stem + '.png');
    if (existsSync(p)) return { path: p, stem };
  }
  return null;
}

/** 画像をロード（キャッシュ付き） */
const imageCache = new Map();
async function loadCached(path) {
  if (imageCache.has(path)) return imageCache.get(path);
  const img = await loadImage(path);
  imageCache.set(path, img);
  return img;
}

/**
 * 表情ごとのパーツ解決結果
 * @returns {Promise<{expressionName, closed: layers[], open: layers[], usedStems, fallbacks}>}
 */
async function resolveExpression(charDir, expr) {
  const fallbacks = [];

  // arm_normal (あれば)
  const armPath = join(charDir, 'arm_normal.png');
  const hasArm = existsSync(armPath);
  const edamamePath = join(charDir, 'edamame_normal.png');
  const hasEdamame = existsSync(edamamePath);
  const hairBackPath = join(charDir, 'hair_back_twin_drill.png');
  const hasHairBack = existsSync(hairBackPath);
  const headDressPath = join(charDir, 'head_dress_normal.png');
  const hasHeadDress = existsSync(headDressPath);
  const hairClipPath = join(charDir, 'hair_clip_heart.png');
  const hasHairClip = existsSync(hairClipPath);

  // eye
  const eyeResult = resolveFirst(charDir, expr.eye);
  if (!eyeResult) throw new Error(`Eye not found for ${expr.name} in ${charDir}`);
  if (expr.eye.length > 1 && eyeResult.stem !== expr.eye[0]) {
    fallbacks.push(`eye: ${expr.eye[0]} -> ${eyeResult.stem}`);
  }

  // fx
  let fxResult = null;
  if (expr.fx.length > 0) {
    fxResult = resolveFirst(charDir, expr.fx);
    if (fxResult && expr.fx.length > 1 && fxResult.stem !== expr.fx[0]) {
      fallbacks.push(`fx: ${expr.fx[0]} -> ${fxResult.stem}`);
    }
  }

  // 口閉じ / 口開き の共通ベース
  const basePath = join(charDir, 'base.png');

  // bangs (前髪もみあげ: metan のみ)
  const bangsPath = join(charDir, 'bangs.png');
  const hasBangs = existsSync(bangsPath);

  const buildLayers = async (mouthStems) => {
    const mouthResult = resolveFirst(charDir, mouthStems);
    if (!mouthResult) throw new Error(`Mouth not found for ${expr.name}`);

    // 重ね順: base → cheek → shadow → arm → brow → eye → mouth → bangs → fx
    // catalog は簡易版のため cheek/shadow/brow は省略（目口fxのみ確認）
    const layers = [];
    if (hasHairBack) layers.push({ path: hairBackPath, stem: 'hair_back_twin_drill' });
    layers.push({ path: basePath, stem: 'base' });
    if (hasEdamame) layers.push({ path: edamamePath, stem: 'edamame_normal' });
    if (hasHeadDress) layers.push({ path: headDressPath, stem: 'head_dress_normal' });
    if (hasHairClip) layers.push({ path: hairClipPath, stem: 'hair_clip_heart' });
    if (hasArm) layers.push({ path: armPath, stem: 'arm_normal' });
    layers.push({ path: eyeResult.path, stem: eyeResult.stem });
    layers.push({ path: mouthResult.path, stem: mouthResult.stem });
    if (hasBangs) layers.push({ path: bangsPath, stem: 'bangs' });
    if (fxResult) layers.push({ path: fxResult.path, stem: fxResult.stem });

    return layers;
  };

  const closedLayers = await buildLayers(expr.mouth_close);
  const openLayers   = await buildLayers(expr.mouth_open);

  return {
    expressionName: expr.name,
    label: expr.label,
    closed: closedLayers,
    open: openLayers,
    eyeStem: eyeResult.stem,
    mouthClosedStem: resolveFirst(charDir, expr.mouth_close)?.stem,
    mouthOpenStem: resolveFirst(charDir, expr.mouth_open)?.stem,
    fxStem: fxResult?.stem || null,
    fallbacks,
  };
}

// ---- 描画定数 ----
const FACE_TARGET_H = 360; // 顔タイルの縦px
const LABEL_COL_W  = 370; // テキスト欄幅
const FACE_PAD     = 12;  // 顔タイル内padding
const ROW_PAD      = 20;  // 行間
const HEADER_H     = 80;  // タイトル行の高さ
const CANVAS_PAD   = 24;  // 全体余白
const TILE_BG      = '#f4f4f8';
const TILE_BORDER  = '#ccccdd';
const TEXT_COLOR   = '#111111';
const SUB_COLOR    = '#555577';
const BG_COLOR     = '#ffffff';

// フォントファミリ文字列
const F = fontRegistered ? 'JP' : 'sans-serif';
const FB = fontRegistered ? 'JP-Bold' : 'sans-serif';

/** テキストを指定幅で折り返す（@napi-rs/canvas は measureText をサポート） */
function wrapText(ctx, text, maxWidth) {
  const lines = [];
  const words = Array.from(text); // 日本語は文字単位
  let line = '';
  for (const ch of words) {
    const test = line + ch;
    if (ctx.measureText(test).width > maxWidth && line !== '') {
      lines.push(line);
      line = ch;
    } else {
      line = test;
    }
  }
  if (line) lines.push(line);
  return lines;
}

/**
 * 顔タイル(合成済み)を canvas に描画する
 * returns 実際に描いた幅
 */
async function drawFaceTile(ctx, layers, x, y, tileW, tileH, label) {
  // 背景
  ctx.fillStyle = TILE_BG;
  ctx.strokeStyle = TILE_BORDER;
  ctx.lineWidth = 1;
  ctx.fillRect(x, y, tileW, tileH);
  ctx.strokeRect(x + 0.5, y + 0.5, tileW - 1, tileH - 1);

  // ラベル（上部）
  const labelH = 22;
  ctx.fillStyle = '#e0e0ee';
  ctx.fillRect(x, y, tileW, labelH);
  ctx.fillStyle = SUB_COLOR;
  ctx.font = `13px "${F}"`;
  ctx.textAlign = 'center';
  ctx.fillText(label, x + tileW / 2, y + labelH - 5);

  // 合成描画エリア
  const drawY = y + labelH + FACE_PAD;
  const drawH = tileH - labelH - FACE_PAD * 2;
  const drawX = x + FACE_PAD;
  const drawW = tileW - FACE_PAD * 2;

  // 全パーツを重ね描き（base の width/height を基準にスケール）
  for (const layer of layers) {
    const img = await loadCached(layer.path);
    // 描画サイズ: drawH に合わせてアスペクト維持
    const scale = drawH / img.height;
    const scaledW = img.width * scale;
    const cx = drawX + (drawW - scaledW) / 2;
    ctx.drawImage(img, cx, drawY, scaledW, drawH);
  }
}

/** 1キャラ分のカタログPNGを生成 */
async function generateCatalog(charKey, charLabel, charDir) {
  console.log(`\n=== Generating catalog for ${charKey} ===`);

  // 全表情を解決
  const resolvedExprs = [];
  const allFallbacks = [];
  for (const expr of EXPRESSIONS) {
    const resolved = await resolveExpression(charDir, expr);
    resolvedExprs.push(resolved);
    if (resolved.fallbacks.length > 0) {
      allFallbacks.push({ expr: expr.name, fallbacks: resolved.fallbacks });
    }
    console.log(`  ${expr.name}: eye=${resolved.eyeStem}, mouth_close=${resolved.mouthClosedStem}, mouth_open=${resolved.mouthOpenStem}, fx=${resolved.fxStem || 'none'}`);
    if (resolved.fallbacks.length > 0) {
      console.log(`    Fallbacks: ${resolved.fallbacks.join(', ')}`);
    }
  }

  // サイズ計算
  // base のサイズを取得
  const baseImg = await loadCached(join(charDir, 'base.png'));
  const faceAspect = baseImg.width / baseImg.height;
  const tileH = FACE_TARGET_H;
  const tileW = Math.round(faceAspect * tileH) + FACE_PAD * 2;
  const rowH  = tileH + 22 + ROW_PAD; // ラベル22px + bottom pad

  const totalW = CANVAS_PAD * 2 + LABEL_COL_W + tileW * 2 + 20; // 20=中間ギャップ
  const totalH = CANVAS_PAD * 2 + HEADER_H + (rowH + ROW_PAD) * resolvedExprs.length;

  console.log(`  Canvas size: ${totalW} x ${totalH}`);

  const canvas = createCanvas(totalW, totalH);
  const ctx = canvas.getContext('2d');

  // 全体背景
  ctx.fillStyle = BG_COLOR;
  ctx.fillRect(0, 0, totalW, totalH);

  // ---- ヘッダー ----
  ctx.fillStyle = '#2a2a4a';
  ctx.fillRect(0, 0, totalW, HEADER_H);
  ctx.fillStyle = '#ffffff';
  ctx.font = `bold 28px "${FB}"`;
  ctx.textAlign = 'left';
  ctx.fillText(`${charLabel}  表情カタログ`, CANVAS_PAD, HEADER_H - 22);
  ctx.font = `14px "${F}"`;
  ctx.fillStyle = '#aaaacc';
  ctx.fillText('口閉じ / 口開き  各パーツstem名付き', CANVAS_PAD, HEADER_H - 6);

  // ---- 各行 ----
  const layersX = CANVAS_PAD + LABEL_COL_W;
  const closedX = layersX;
  const openX   = layersX + tileW + 10;
  const layerNames = LAYER_NAMES[charKey] || {};

  for (let i = 0; i < resolvedExprs.length; i++) {
    const r = resolvedExprs[i];
    const rowY = CANVAS_PAD + HEADER_H + ROW_PAD / 2 + i * (tileH + 22 + ROW_PAD * 2);

    // 行区切り線
    if (i > 0) {
      ctx.strokeStyle = '#ddddee';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(CANVAS_PAD, rowY - ROW_PAD);
      ctx.lineTo(totalW - CANVAS_PAD, rowY - ROW_PAD);
      ctx.stroke();
    }

    // ---- テキスト欄 ----
    const txtX = CANVAS_PAD + 8;
    let txtY = rowY + 14;

    // 表情名（大見出し）
    ctx.fillStyle = '#2a2a4a';
    ctx.font = `bold 18px "${FB}"`;
    ctx.textAlign = 'left';
    ctx.fillText(`${r.expressionName}`, txtX, txtY);
    ctx.fillStyle = SUB_COLOR;
    ctx.font = `14px "${F}"`;
    ctx.fillText(`  ${r.label}`, txtX + ctx.measureText(r.expressionName).width + 6, txtY);
    txtY += 26;

    // eye
    const eyeLayerName = layerNames[r.eyeStem] || '';
    ctx.fillStyle = '#444466';
    ctx.font = `13px "${F}"`;
    ctx.fillText(`eye: ${r.eyeStem}`, txtX, txtY);
    if (eyeLayerName) {
      ctx.fillStyle = '#888899';
      ctx.fillText(`  "${eyeLayerName}"`, txtX + ctx.measureText(`eye: ${r.eyeStem}`).width + 2, txtY);
    }
    txtY += 20;

    // mouth_close
    const mcLayerName = layerNames[r.mouthClosedStem] || '';
    ctx.fillStyle = '#444466';
    ctx.font = `13px "${F}"`;
    ctx.fillText(`口閉: ${r.mouthClosedStem}`, txtX, txtY);
    if (mcLayerName) {
      ctx.fillStyle = '#888899';
      ctx.fillText(`  "${mcLayerName}"`, txtX + ctx.measureText(`口閉: ${r.mouthClosedStem}`).width + 2, txtY);
    }
    txtY += 20;

    // mouth_open
    const moLayerName = layerNames[r.mouthOpenStem] || '';
    ctx.fillStyle = '#444466';
    ctx.font = `13px "${F}"`;
    ctx.fillText(`口開: ${r.mouthOpenStem}`, txtX, txtY);
    if (moLayerName) {
      ctx.fillStyle = '#888899';
      ctx.fillText(`  "${moLayerName}"`, txtX + ctx.measureText(`口開: ${r.mouthOpenStem}`).width + 2, txtY);
    }
    txtY += 20;

    // fx（あれば）
    if (r.fxStem) {
      const fxLayerName = layerNames[r.fxStem] || '';
      ctx.fillStyle = '#664444';
      ctx.font = `13px "${F}"`;
      ctx.fillText(`fx:  ${r.fxStem}`, txtX, txtY);
      if (fxLayerName) {
        ctx.fillStyle = '#998888';
        ctx.fillText(`  "${fxLayerName}"`, txtX + ctx.measureText(`fx:  ${r.fxStem}`).width + 2, txtY);
      }
      txtY += 20;
    }

    // フォールバック表示（あれば）
    if (r.fallbacks.length > 0) {
      ctx.fillStyle = '#cc6600';
      ctx.font = `11px "${F}"`;
      for (const fb of r.fallbacks) {
        ctx.fillText(`fallback: ${fb}`, txtX, txtY);
        txtY += 16;
      }
    }

    // ---- 顔タイル（口閉じ） ----
    await drawFaceTile(ctx, r.closed, closedX, rowY, tileW, tileH + 22, '口閉じ');

    // ---- 顔タイル（口開き） ----
    await drawFaceTile(ctx, r.open, openX, rowY, tileW, tileH + 22, '口開き');
  }

  // 出力
  const outPath = join(OUT, `expression-catalog-${charKey}.png`);
  const buf = canvas.toBuffer('image/png');
  writeFileSync(outPath, buf);
  console.log(`  Saved: ${outPath} (${totalW}x${totalH}, ${(buf.length / 1024).toFixed(0)} KB)`);

  return { path: outPath, width: totalW, height: totalH, fallbacks: allFallbacks };
}

// ---- メイン ----
async function main() {
  const chars = [
    { key: 'zundamon', label: 'ずんだもん', dir: join(ASSETS, 'zundamon') },
    { key: 'metan',    label: '四国めたん', dir: join(ASSETS, 'metan') },
  ];

  const results = [];
  for (const c of chars) {
    const res = await generateCatalog(c.key, c.label, c.dir);
    results.push({ charKey: c.key, ...res });
  }

  console.log('\n=== DONE ===');
  for (const r of results) {
    console.log(`${r.charKey}: ${r.path} (${r.width}x${r.height})`);
    if (r.fallbacks.length > 0) {
      for (const fb of r.fallbacks) {
        console.log(`  fallback in ${fb.expr}: ${fb.fallbacks.join(', ')}`);
      }
    }
  }
  console.log(`Font registered: ${fontRegistered}`);
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
