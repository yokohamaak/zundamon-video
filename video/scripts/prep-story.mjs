// ストーリー調エディタ（新ツール）用の準備ステップ。
// 既存 prep.mjs と違い、本編の meta.json / digest.mp3 を要求しない。
// StoryVideo が必要とする静的アセットだけを assets/ → public/ にコピーし、
// 立ち絵 manifest.json を生成する。
//   - avatars（パーツ立ち絵）＋ manifest.json
//   - background（シーン背景）
//   - effects（固定の演出用画像）
//   - fonts（日本語フォント）
// story-scenes.json は public/ に直接置く運用（git追跡済み）。
import { copyFileSync, mkdirSync, existsSync, readdirSync, statSync, writeFileSync, unlinkSync } from "node:fs";
import { resolve, parse as parsePath } from "node:path";

const root = resolve(import.meta.dirname, "..");
const pubDir = resolve(root, "public");
mkdirSync(pubDir, { recursive: true });

// 単純なディレクトリ単位コピー（拡張子フィルタ）。
// mirror=true の種類は、assetsに無いファイルをpublic側からも削除する
// （プルダウン一覧はpublicを見るため、assets側で削除したのにpublicへ残り続けて
// 「消したはずのBGM/SEが選択肢に残る」事故になっていた）。
// fontsは配布物を直接publicに置く運用もあるため対象外。
for (const [sub, exts, label, mirror] of [
  ["fonts", [".woff2", ".woff", ".ttf", ".otf"], "fonts", false],
  ["background", [".png", ".jpg", ".jpeg", ".webp"], "background image", true],
  ["effects", [".png", ".jpg", ".jpeg", ".webp", ".svg"], "effect image", true],
  ["mobs", [".png", ".webp"], "mob image", true],
  ["bgm", [".mp3", ".wav", ".m4a"], "BGM", true],
  ["se", [".mp3", ".wav", ".m4a"], "SE", true],
]) {
  const s = resolve(root, "assets", sub);
  const d = resolve(pubDir, sub);
  if (!existsSync(s)) {
    console.warn(`[prep-story] ${sub} が見つかりません: ${s}`);
    continue;
  }
  mkdirSync(d, { recursive: true });
  const files = readdirSync(s).filter((f) => exts.some((e) => f.toLowerCase().endsWith(e)));
  for (const f of files) copyFileSync(resolve(s, f), resolve(d, f));
  console.log(`[prep-story] copied ${files.length} ${label}`);
  if (mirror) {
    const keep = new Set(files);
    const stale = readdirSync(d).filter((f) => exts.some((e) => f.toLowerCase().endsWith(e)) && !keep.has(f));
    for (const f of stale) unlinkSync(resolve(d, f));
    if (stale.length) console.log(`[prep-story] removed ${stale.length} stale ${label} (assetsに存在しないため)`);
  }
}

// StoryVideo の回想グレイン用ノイズ画像（assets直下 → public直下）。
{
  const ns = resolve(root, "assets", "noise.png");
  if (existsSync(ns)) {
    copyFileSync(ns, resolve(pubDir, "noise.png"));
    console.log("[prep-story] copied noise.png");
  }
}

// パーツ分け立ち絵 → public/avatars/ ＋ manifest.json（prep.mjs と同方式）。
// full/ サブディレクトリがある場合は <char>_full キーとして manifest に追加する。
{
  const PART_EXTS = [".png", ".webp", ".svg"];
  const srcAv = resolve(root, "assets", "avatars");
  const dstAv = resolve(pubDir, "avatars");
  const manifest = {};
  if (existsSync(srcAv)) {
    // ルート直下の単一画像フォールバック（gender_open/close）もコピー。
    for (const f of readdirSync(srcAv).filter((f) => f.toLowerCase().endsWith(".png"))) {
      mkdirSync(dstAv, { recursive: true });
      copyFileSync(resolve(srcAv, f), resolve(dstAv, f));
    }
    const subdirs = readdirSync(srcAv).filter((f) => statSync(resolve(srcAv, f)).isDirectory());
    for (const dir of subdirs) {
      const sdir = resolve(srcAv, dir);
      const ddir = resolve(dstAv, dir);
      mkdirSync(ddir, { recursive: true });
      const parts = {};
      // トップレベルのパーツファイル（バスト用）
      const files = readdirSync(sdir).filter((f) => {
        if (!PART_EXTS.some((e) => f.toLowerCase().endsWith(e))) return false;
        // ディレクトリは除外（statで確認）
        return statSync(resolve(sdir, f)).isFile();
      });
      for (const f of files) {
        copyFileSync(resolve(sdir, f), resolve(ddir, f));
        parts[parsePath(f).name] = f;
      }
      if (files.length) {
        manifest[dir] = parts;
        console.log(`[prep-story] avatar parts: ${dir} (${files.length}枚)`);
      }
      // full/ サブディレクトリがあれば <char>_full として追加
      const fullSrc = resolve(sdir, "full");
      const fullDst = resolve(ddir, "full");
      if (existsSync(fullSrc) && statSync(fullSrc).isDirectory()) {
        mkdirSync(fullDst, { recursive: true });
        const fullFiles = readdirSync(fullSrc).filter(
          (f) => PART_EXTS.some((e) => f.toLowerCase().endsWith(e)) && statSync(resolve(fullSrc, f)).isFile()
        );
        const fullParts = {};
        for (const f of fullFiles) {
          copyFileSync(resolve(fullSrc, f), resolve(fullDst, f));
          // dir が "<char>/full" として Avatar に渡されるので、値はファイル名のみ（プレフィックス不要）。
          fullParts[parsePath(f).name] = f;
        }
        if (fullFiles.length) {
          manifest[`${dir}_full`] = fullParts;
          console.log(`[prep-story] avatar parts: ${dir}_full (${fullFiles.length}枚)`);
        }
      }
      // candidates/ サブディレクトリがあれば public/avatars/<char>/candidates/ へコピー
      const candSrc = resolve(sdir, "candidates");
      const candDst = resolve(ddir, "candidates");
      if (existsSync(candSrc) && statSync(candSrc).isDirectory()) {
        mkdirSync(candDst, { recursive: true });
        const candFiles = readdirSync(candSrc).filter(
          (f) => f.toLowerCase().endsWith(".png") && statSync(resolve(candSrc, f)).isFile()
        );
        for (const f of candFiles) {
          copyFileSync(resolve(candSrc, f), resolve(candDst, f));
        }
        if (candFiles.length) {
          console.log(`[prep-story] avatar candidates: ${dir} (${candFiles.length}枚)`);
        }
      }
    }
  }
  mkdirSync(dstAv, { recursive: true });
  writeFileSync(resolve(dstAv, "manifest.json"), JSON.stringify(manifest, null, 2));
  console.log(`[prep-story] avatar manifest: ${Object.keys(manifest).length}キャラ`);
}

// expressions.json を public/ にコピー（public/ 直置き・git追跡済み）。
// すでに public/expressions.json にある場合はそのまま使う（上書きしない）。
{
  const exprSrc = resolve(pubDir, "expressions.json");
  if (existsSync(exprSrc)) {
    console.log("[prep-story] expressions.json は public/ に存在します（コピー不要）");
  } else {
    // assets/expressions.json があればコピー（将来の移動先対応）
    const exprAssets = resolve(root, "assets", "expressions.json");
    if (existsSync(exprAssets)) {
      copyFileSync(exprAssets, exprSrc);
      console.log("[prep-story] copied expressions.json from assets/");
    } else {
      console.warn("[prep-story] expressions.json が見つかりません（public/ にも assets/ にもなし）");
    }
  }
}

// poses.json を public/ にコピー（public/直置き運用）。
// すでに public/poses.json にある場合はそのまま使う（上書きしない）。
{
  const posesDst = resolve(pubDir, "poses.json");
  if (existsSync(posesDst)) {
    console.log("[prep-story] poses.json は public/ に存在します（コピー不要）");
  } else {
    const posesAssets = resolve(root, "assets", "poses.json");
    if (existsSync(posesAssets)) {
      copyFileSync(posesAssets, posesDst);
      console.log("[prep-story] copied poses.json from assets/");
    } else {
      console.warn("[prep-story] poses.json が見つかりません（public/ にも assets/ にもなし）");
    }
  }
}

// se-map.json を public/ に用意（public直置き運用）。
// assets/se-map.json があればコピー。無ければ既存 public/se-map.json を尊重（上書きしない）。
{
  const seMapDst = resolve(pubDir, "se-map.json");
  const seMapAssets = resolve(root, "assets", "se-map.json");
  if (existsSync(seMapAssets)) {
    copyFileSync(seMapAssets, seMapDst);
    console.log("[prep-story] copied se-map.json from assets/");
  } else if (existsSync(seMapDst)) {
    console.log("[prep-story] se-map.json は public/ に存在します（コピー不要）");
  } else {
    console.warn("[prep-story] se-map.json が見つかりません（public/ にも assets/ にもなし）");
  }
}

// 固定入力の存在チェック（無ければ警告のみ。描画側でも未登録fallbackする）。
for (const f of ["story-scenes.json"]) {
  if (!existsSync(resolve(pubDir, f))) {
    console.warn(`[prep-story] ${f} が public/ にありません（git追跡済みのはず）`);
  }
}
console.log("[prep-story] done");
