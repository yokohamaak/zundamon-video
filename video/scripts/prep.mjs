// docs/<dir>/ から meta.json と digest.mp3 を public/ にコピーする。
// Remotionは public/ 配下のファイルしか staticFile() で参照できないため。
// SRC_DIR で入力元を差し替え可能（ニュース以外のコンテンツに流用するため）。
//
// 描画の唯一の準備ステップ。dev/render は必ずこれを通すので、
// 「コピー忘れ／topics注入忘れ」による先祖返り（古いデータで焼く事故）を構造的に防ぐ。
import { copyFileSync, mkdirSync, existsSync, readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { resolve, parse as parsePath } from "node:path";

const root = resolve(import.meta.dirname, "..");
const srcDir = process.env.SRC_DIR
  ? resolve(process.env.SRC_DIR)
  : resolve(root, "..", "docs", "main");
const pubDir = resolve(root, "public");

mkdirSync(pubDir, { recursive: true });

for (const f of ["meta.json", "digest.mp3"]) {
  const s = resolve(srcDir, f);
  if (!existsSync(s)) {
    console.error(`[prep] 入力が見つかりません: ${s}`);
    process.exit(1);
  }
  copyFileSync(s, resolve(pubDir, f));
  console.log(`[prep] copied ${f}  (from ${srcDir})`);
}

// 中央ビジュアル画像（章カットの画像）を srcDir → public/ にコピー。
// meta.json の topics[].image がこれらを staticFile で参照する（例: "ch_01_00.jpg"）。
// 画像が無い場合は no-op（全プレースホルダ）。
const IMG_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".gif"];
const imgs = readdirSync(srcDir).filter((f) => IMG_EXTS.some((e) => f.toLowerCase().endsWith(e)));
for (const f of imgs) copyFileSync(resolve(srcDir, f), resolve(pubDir, f));
if (imgs.length) console.log(`[prep] copied ${imgs.length} topic image(s)  (from ${srcDir})`);

// 静的アセット（アバター立ち絵・フォント）を assets/ → public/ にコピー。
// public/ は生成物扱い(gitignore)なので、ソースは assets/ で管理する。
// フォントを同梱することで描画環境のOSフォント有無に依存せず日本語を描ける。
for (const [sub, exts, label] of [
  ["avatars", [".png"], "avatar images"],
  ["fonts", [".woff2", ".woff", ".ttf", ".otf"], "fonts"],
  ["background", [".png", ".jpg", ".jpeg", ".webp"], "background image"],
  ["bgm", [".mp3", ".wav", ".m4a", ".ogg"], "bgm"],
  ["se", [".mp3", ".wav", ".m4a", ".ogg"], "se"],
]) {
  const s = resolve(root, "assets", sub);
  const d = resolve(pubDir, sub);
  if (!existsSync(s)) {
    console.warn(`[prep] ${sub} が見つかりません: ${s}`);
    continue;
  }
  mkdirSync(d, { recursive: true });
  const files = readdirSync(s).filter((f) => exts.some((e) => f.endsWith(e)));
  for (const f of files) copyFileSync(resolve(s, f), resolve(d, f));
  console.log(`[prep] copied ${files.length} ${label}`);
}

// パーツ分け立ち絵（assets/avatars/<キャラ>/ 配下の部位別画像）を public/ へコピーし、
// manifest.json（キャラ→{部位stem: ファイル名}）を生成する。
// Avatar.tsx は manifest を見て存在する部位だけ重ねる。未配置キャラは単一画像へフォールバック。
{
  const PART_EXTS = [".png", ".webp", ".svg"];
  const srcAv = resolve(root, "assets", "avatars");
  const dstAv = resolve(pubDir, "avatars");
  const manifest = {};
  if (existsSync(srcAv)) {
    const subdirs = readdirSync(srcAv).filter((f) =>
      statSync(resolve(srcAv, f)).isDirectory()
    );
    for (const dir of subdirs) {
      const sdir = resolve(srcAv, dir);
      const ddir = resolve(dstAv, dir);
      mkdirSync(ddir, { recursive: true });
      const parts = {};
      const files = readdirSync(sdir).filter((f) =>
        PART_EXTS.some((e) => f.toLowerCase().endsWith(e))
      );
      for (const f of files) {
        copyFileSync(resolve(sdir, f), resolve(ddir, f));
        parts[parsePath(f).name] = f; // stem（拡張子なし）→ ファイル名
      }
      if (files.length) {
        manifest[dir] = parts;
        console.log(`[prep] avatar parts: ${dir} (${files.length}枚)`);
      }
    }
  }
  mkdirSync(dstAv, { recursive: true });
  writeFileSync(resolve(dstAv, "manifest.json"), JSON.stringify(manifest, null, 2));
  console.log(`[prep] avatar manifest: ${Object.keys(manifest).length}キャラ`);
}

// topics（中央ビジュアルの切替）の確定。
// 本番ではコンテンツ側(main_story.py)が meta.json に topics を出力する想定。
// まだ出力していない間だけ、デモ用に均等割りで注入する（出力済みなら触らない）。
const metaPath = resolve(pubDir, "meta.json");
const meta = JSON.parse(readFileSync(metaPath, "utf-8"));
let metaChanged = false;

// manual想像イラストのプレースホルダ昇格：
// 差し替え先(placeholder=manual_NN.png)の画像が置かれていれば image へ昇格する。
// これで「画像を置く→renderするだけ」で差し替わる（main_story再実行は不要）。
for (const t of meta.topics ?? []) {
  if (t.placeholder && existsSync(resolve(pubDir, t.placeholder))) {
    t.image = t.placeholder;
    delete t.placeholder;
    delete t.note;
    metaChanged = true;
    console.log(`[prep] manual昇格: ${t.image} を中央ビジュアルに使用`);
  }
}

if (!meta.topics || meta.topics.length === 0) {
  const total = meta.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
  const segs = [
    { title: "オープニング", image: "samples/opening.svg" },
    { title: "国内のうごき", image: "samples/domestic.svg" },
    { title: "世界・経済", image: "samples/world.svg" },
    { title: "バイブコーディング", image: "samples/vibe.svg" },
    { title: "エンディング", image: "samples/closing.svg" },
  ];
  const N = segs.length;
  meta.topics = segs.map((s, i) => ({
    ...s,
    start: (total * i) / N,
    end: (total * (i + 1)) / N,
  }));
  metaChanged = true; // meta.topics は上で注入済み。下で一括書き出し。
  console.log(`[prep] topics未設定のためデモtopicsを${N}件注入（total=${total.toFixed(1)}s）`);
} else {
  console.log(`[prep] topicsはmeta.json由来のものを使用（${meta.topics.length}件）`);
}

// 音声(BGM/SE)：public に実ファイルが在るものだけ meta.audio に残す（無ければ無音でスキップ）。
// 「音源を置く→renderするだけ」で鳴り、未配置でも壊れない（画像プレースホルダと同じ思想）。
if (meta.audio) {
  const present = (sub, file) => file && existsSync(resolve(pubDir, sub, file));
  if (meta.audio.bgm && !present("bgm", meta.audio.bgm.file)) {
    meta.audio.bgm = null;
    metaChanged = true;
    console.log("[prep] BGM未配置のためスキップ");
  }
  const se = meta.audio.se ?? {};
  for (const k of Object.keys(se)) {
    if (!present("se", se[k])) { delete se[k]; metaChanged = true; }
  }
  const before = (meta.audio.events ?? []).length;
  meta.audio.events = (meta.audio.events ?? []).filter((e) => se[e.se]);
  if (meta.audio.events.length !== before) metaChanged = true;
  const seKinds = Object.keys(se);
  console.log(`[prep] audio: BGM=${meta.audio.bgm ? "on" : "off"} / SE種=${seKinds.length ? seKinds.join(",") : "なし"} / イベント${meta.audio.events.length}件`);
}

if (metaChanged) writeFileSync(metaPath, JSON.stringify(meta, null, 2));
