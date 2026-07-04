import { continueRender, delayRender, staticFile } from "remotion";

// 日本語フォントを同梱WOFF2から明示ロード（OSフォント有無に依存しない＝豆腐防止）。
// このモジュールをimportした時点でロードが走り、描画は完了まで delayRender で待機する。
export const FONT_FAMILY = "Noto Sans JP";

const fontHandle = delayRender("load-fonts");
Promise.all(
  [
    ["fonts/japanese-400-normal.woff2", "400"],
    ["fonts/japanese-700-normal.woff2", "700"],
  ].map(([path, weight]) => {
    const face = new FontFace(
      FONT_FAMILY,
      `url(${staticFile(path)}) format('woff2')`,
      { weight }
    );
    return face.load().then((f) => document.fonts.add(f));
  })
)
  .then(() => continueRender(fontHandle))
  .catch(() => continueRender(fontHandle)); // 失敗時もOSフォントへフォールバックして描画は止めない
