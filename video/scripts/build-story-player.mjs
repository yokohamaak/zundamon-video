import { build } from "esbuild";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

await build({
  entryPoints: [resolve(root, "src", "story-player.tsx")],
  outfile: resolve(root, "public", "story-player.js"),
  bundle: true,
  minify: true,
  sourcemap: false,
  platform: "browser",
  format: "iife",
  target: ["chrome110"],
  define: {
    "process.env.NODE_ENV": '"production"',
  },
});

console.log("[story-player] built public/story-player.js");
