import type { NormalizedWhiteboardExplainInsertConfig, WhiteboardExplainInsertConfig } from './whiteboardExplainTypes';

export const WHITEBOARD_EXPLAIN_DEFAULTS = {
  type: 'whiteboard_explain' as const,
  title: 'めたんの解説コーナー',
  theme: 'ここで一回整理するわね',
  conclusion: 'つまり：見える形にするのが大事！',
  sections: [
    { heading: '原因', bullets: ['状況を整理', '見えにくい点', '残らない仕事'], icon: 'confused' as const },
    { heading: '問題', bullets: ['評価されにくい', '伝わりにくい', '成果にならない'], icon: 'scribble' as const },
    { heading: '解決', bullets: ['メモ化', '一覧化', '共有する'], icon: 'checklist' as const },
  ],
  character: {
    name: 'metan',
    pose: 'pointing',
    expression: 'smile',
    image: '',
  },
  style: {
    fontFamily: 'Yusei Magic, "Yu Gothic", "Hiragino Sans", sans-serif',
    titleColor: '#111111',
    themeColor: '#111111',
    headingColor: '#10358c',
    bodyColor: '#111111',
    accentColor: '#f04d93',
    secondaryAccentColor: '#10358c',
    markerColor: '#f6db45',
    boardColor: '#fffef8',
    boardFrameColor: '#72777d',
    backgroundColor: '#f1eadf',
  },
  animation: {
    mode: 'step' as const,
  },
  assets: {
    backgroundImage: '',
    whiteboardImage: '',
    iconImages: {},
  },
};

export const WHITEBOARD_EXPLAIN_LIMITS = {
  title: 18,
  theme: 30,
  heading: 14,
  bullet: 16,
  conclusion: 40,
  sections: 3,
  bulletsPerSection: 3,
};

export const normalizeWhiteboardExplainConfig = (
  config: WhiteboardExplainInsertConfig,
): NormalizedWhiteboardExplainInsertConfig => {
  const sections = [...(config.sections ?? [])].slice(0, WHITEBOARD_EXPLAIN_LIMITS.sections);
  while (sections.length < WHITEBOARD_EXPLAIN_LIMITS.sections) {
    sections.push(WHITEBOARD_EXPLAIN_DEFAULTS.sections[sections.length]);
  }

  return {
    type: 'whiteboard_explain',
    title: config.title || WHITEBOARD_EXPLAIN_DEFAULTS.title,
    theme: config.theme || WHITEBOARD_EXPLAIN_DEFAULTS.theme,
    conclusion: config.conclusion || WHITEBOARD_EXPLAIN_DEFAULTS.conclusion,
    sections: sections.map((section, index) => ({
      heading: section?.heading || WHITEBOARD_EXPLAIN_DEFAULTS.sections[index].heading,
      bullets: (section?.bullets ?? WHITEBOARD_EXPLAIN_DEFAULTS.sections[index].bullets)
        .filter(Boolean)
        .slice(0, WHITEBOARD_EXPLAIN_LIMITS.bulletsPerSection),
      icon: section?.icon ?? WHITEBOARD_EXPLAIN_DEFAULTS.sections[index].icon,
    })),
    character: {
      ...WHITEBOARD_EXPLAIN_DEFAULTS.character,
      ...(config.character ?? {}),
    },
    style: {
      ...WHITEBOARD_EXPLAIN_DEFAULTS.style,
      ...(config.style ?? {}),
    },
    animation: {
      ...WHITEBOARD_EXPLAIN_DEFAULTS.animation,
      ...(config.animation ?? {}),
    },
    assets: {
      ...WHITEBOARD_EXPLAIN_DEFAULTS.assets,
      ...(config.assets ?? {}),
      iconImages: {
        ...WHITEBOARD_EXPLAIN_DEFAULTS.assets.iconImages,
        ...(config.assets?.iconImages ?? {}),
      },
    },
  };
};
