import type { WhiteboardExplainLayoutVariant } from './whiteboardExplainTypes';

export type Rect = { x: number; y: number; width: number; height: number };
export type Point = { x: number; y: number };

export type WhiteboardExplainLayout = {
  scene: Rect;
  board: Rect;
  character: Rect;
  title: Rect;
  theme: Rect;
  sections: Rect[];
  conclusion: Rect;
};

const scaleRect = (rect: Rect, sx: number, sy: number): Rect => ({
  x: rect.x * sx,
  y: rect.y * sy,
  width: rect.width * sx,
  height: rect.height * sy,
});

const BASE_LAYOUTS: Record<WhiteboardExplainLayoutVariant, WhiteboardExplainLayout> = {
  default: {
    scene: { x: 0, y: 0, width: 1920, height: 1080 },
    board: { x: 80, y: 38, width: 1450, height: 985 },
    // 右下の解説役。v3で調整済みの位置を維持する。
    // 髪が少し画面外にはみ出てもよいが、v4では追加移動しない。
    character: { x: 1410, y: 205, width: 720, height: 870 },
    title: { x: 180, y: 80, width: 1030, height: 105 },
    theme: { x: 300, y: 205, width: 980, height: 72 },
    sections: [
      // 3項目の間隔を均等にし、ホワイトボード幅をより広く使う。
      { x: 165, y: 330, width: 430, height: 360 },
      { x: 600, y: 330, width: 430, height: 360 },
      { x: 1035, y: 330, width: 430, height: 360 },
    ],
    // プレイヤーUIに被りにくいよう少し上げる。右端はキャラに被らない幅に抑える。
    conclusion: { x: 245, y: 700, width: 1150, height: 200 },
  },
  // ホワイトボードを広く使いたい時用。キャラを小さくして右下に寄せ、その分ボードと項目を広げる。
  compact: {
    scene: { x: 0, y: 0, width: 1920, height: 1080 },
    board: { x: 50, y: 34, width: 1620, height: 990 },
    character: { x: 1600, y: 560, width: 380, height: 480 },
    title: { x: 180, y: 80, width: 1030, height: 105 },
    theme: { x: 300, y: 205, width: 980, height: 72 },
    sections: [
      { x: 135, y: 330, width: 480, height: 360 },
      { x: 630, y: 330, width: 480, height: 360 },
      { x: 1125, y: 330, width: 480, height: 360 },
    ],
    conclusion: { x: 160, y: 700, width: 1350, height: 200 },
  },
};

export const getWhiteboardExplainLayout = (
  width = 1920,
  height = 1080,
  variant: WhiteboardExplainLayoutVariant = 'default',
): WhiteboardExplainLayout => {
  const base = BASE_LAYOUTS[variant] ?? BASE_LAYOUTS.default;

  const sx = width / 1920;
  const sy = height / 1080;
  return {
    scene: { x: 0, y: 0, width, height },
    board: scaleRect(base.board, sx, sy),
    character: scaleRect(base.character, sx, sy),
    title: scaleRect(base.title, sx, sy),
    theme: scaleRect(base.theme, sx, sy),
    sections: base.sections.map((section) => scaleRect(section, sx, sy)),
    conclusion: scaleRect(base.conclusion, sx, sy),
  };
};

export const getStepFrameRanges = (durationInFrames: number) => {
  const minDuration = Math.max(durationInFrames, 1);
  const unit = Math.max(8, Math.floor(minDuration / 14));
  return {
    background: { start: 0, end: unit },
    character: { start: unit, end: unit * 2 },
    title: { start: unit * 2, end: unit * 3 },
    theme: { start: unit * 3, end: unit * 4 },
    section0: { start: unit * 4, end: unit * 6 },
    section1: { start: unit * 6, end: unit * 8 },
    section2: { start: unit * 8, end: unit * 10 },
    conclusion: { start: unit * 10, end: unit * 12 },
  };
};
