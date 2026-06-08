// プロトタイプ用のサンプル画像（テーマ別）をローカル生成する。外部サービス不使用。
// 本番では各セグメントの画像をここに差し替える（将来Bの動的生成に置換予定）。
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const outDir = resolve(import.meta.dirname, "..", "public", "samples");
mkdirSync(outDir, { recursive: true });

const themes = [
  { id: "opening", title: "オープニング", c1: "#3b5bdb", c2: "#1c2e7a" },
  { id: "domestic", title: "国内のうごき", c1: "#2f9e44", c2: "#1b5e2a" },
  { id: "world", title: "世界・経済", c1: "#1098ad", c2: "#0a5566" },
  { id: "vibe", title: "バイブコーディング", c1: "#7048e8", c2: "#3d2585" },
  { id: "closing", title: "エンディング", c1: "#e8590c", c2: "#8a3206" },
];

for (const t of themes) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${t.c1}"/>
      <stop offset="1" stop-color="${t.c2}"/>
    </linearGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#g)"/>
  <circle cx="1050" cy="170" r="220" fill="rgba(255,255,255,0.08)"/>
  <circle cx="230" cy="560" r="160" fill="rgba(255,255,255,0.06)"/>
  <text x="640" y="380" font-family="'Noto Sans JP', sans-serif" font-size="90" font-weight="800"
        fill="#ffffff" text-anchor="middle">${t.title}</text>
  <text x="640" y="470" font-family="'Noto Sans JP', sans-serif" font-size="34"
        fill="rgba(255,255,255,0.55)" text-anchor="middle" letter-spacing="6">SAMPLE</text>
</svg>`;
  writeFileSync(resolve(outDir, `${t.id}.svg`), svg);
}

console.log(`[make-sample-images] ${themes.length}枚生成: ${outDir}`);
