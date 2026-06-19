import {build} from "esbuild";
import {resolve} from "node:path";

const root = resolve(import.meta.dirname, "..");

await build({
  entryPoints: [resolve(root, "src", "review-player.tsx")],
  outfile: resolve(root, "public", "review-player.js"),
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

console.log("[review-player] built public/review-player.js");
