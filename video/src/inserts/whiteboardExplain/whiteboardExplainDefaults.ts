import type { NormalizedWhiteboardExplainInsertConfig, WhiteboardExplainInsertConfig, WhiteboardExplainSection } from './whiteboardExplainTypes';

export const WHITEBOARD_EXPLAIN_DEFAULTS = {
  type: 'whiteboard_explain' as const,
  title: 'めたんの解説コーナー',
  theme: '今回の問題点と対策',
  conclusion: 'つまり：見える形にするのが大事！',
  sections: [
    { heading: '原因', bullets: ['状況を整理', '見えにくい点', '残らない仕事'], icon: 'cause' as const, iconY: 200 },
    { heading: '問題点', bullets: ['評価されにくい', '伝わりにくい', '成果にならない'], icon: 'problem' as const, iconX: 345, iconY: 200 },
    { heading: '対策', bullets: ['メモ化', '一覧化', '正しく共有する'], icon: 'solution' as const, iconY: 200 },
  ],
  character: {
    name: 'metan',
    pose: 'point',
    expression: 'happy',
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
    titleFontSize: 90,
    themeFontSize: 65,
    sectionHeadingFontSize: 50,
    sectionBodyFontSize: 42,
    conclusionFontSize: 70,
    conclusionBoxX: 245,
    conclusionBoxY: 700,
    conclusionBoxWidth: 1150,
    conclusionBoxHeight: 200,
    arrow0X: 540,
    arrow0Y: 450,
    arrow0Width: 56,
    arrow0Height: 54,
    arrow0StrokeWidth: 9,
    arrow1X: 975,
    arrow1Y: 450,
    arrow1Width: 60,
    arrow1Height: 54,
    arrow1StrokeWidth: 9,
    boardColor: '#fffef8',
    boardFrameColor: '#72777d',
    backgroundColor: '#f1eadf',
  },
  animation: {
    mode: 'all' as const,
    sectionPop: true,
    arrowPop: true,
    conclusionPop: true,
    underlineDraw: true,
    conclusionImpact: true,
  },
  showConclusionArrow: false,
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
    visibleArrows: config.visibleArrows ?? [true, true],
    showConclusion: config.showConclusion !== false,
    showConclusionArrow: config.showConclusionArrow === true,
    activeSection: typeof config.activeSection === 'number' ? Math.max(0, Math.min(2, Math.floor(config.activeSection))) : undefined,
    highlightSections: config.highlightSections,
    sections: sections.map((section, index) => {
      const defaultSection = WHITEBOARD_EXPLAIN_DEFAULTS.sections[index];
      const defaultSectionTyped = defaultSection as WhiteboardExplainSection;
      const defaultIconX = typeof defaultSectionTyped.iconX === 'number' ? defaultSectionTyped.iconX : undefined;
      const defaultIconY = typeof defaultSectionTyped.iconY === 'number' ? defaultSectionTyped.iconY : undefined;
      const defaultIconSize = typeof defaultSectionTyped.iconSize === 'number' ? defaultSectionTyped.iconSize : undefined;
      return {
        heading: section?.heading || defaultSection.heading,
        bullets: (section?.bullets ?? defaultSection.bullets)
          .filter(Boolean)
          .slice(0, WHITEBOARD_EXPLAIN_LIMITS.bulletsPerSection),
        icon: section?.icon ?? defaultSection.icon,
        iconX: typeof section?.iconX === 'number' ? section.iconX : defaultIconX,
        iconY: typeof section?.iconY === 'number' ? section.iconY : defaultIconY,
        iconSize: typeof section?.iconSize === 'number' ? section.iconSize : defaultIconSize,
      };
    }),
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
