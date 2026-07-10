export type WhiteboardExplainInsertType = 'whiteboard_explain';

export type WhiteboardExplainIcon =
  | 'none'
  // Legacy names kept for existing scripts, but redrawn to be easier to read.
  | 'confused'
  | 'scribble'
  | 'checklist'
  | 'memo'
  | 'conversation'
  | 'warning'
  | 'idea'
  | 'table'
  // Whiteboard explanation icons.
  | 'cause'
  | 'problem'
  | 'solution'
  | 'process'
  | 'priority'
  | 'deadline'
  | 'evidence'
  | 'data'
  | 'share'
  | 'rule'
  | 'risk'
  | 'improvement';

export type WhiteboardExplainPose =
  | 'explain'
  | 'pointing'
  | 'confident'
  | 'thinking';

export type WhiteboardExplainExpression =
  | 'normal'
  | 'smile'
  | 'serious'
  | 'surprised'
  | 'troubled';

export type WhiteboardExplainAnimationMode = 'step' | 'all' | 'none';

/** 'compact' はキャラを小さくしてボード・項目の表示領域を広げる版。 */
export type WhiteboardExplainLayoutVariant = 'default' | 'compact';

export type WhiteboardExplainSection = {
  heading: string;
  bullets: string[];
  icon?: WhiteboardExplainIcon;
  /** Icon X position within the section, in 1920x1080 base pixels. Omitted = default. */
  iconX?: number;
  /** Icon Y position within the section, in 1920x1080 base pixels. Omitted = default. */
  iconY?: number;
  /** Icon size in 1920x1080 base pixels. Omitted = default. */
  iconSize?: number;
  /** Show the circular badge (fill + ring) behind the icon. Omitted = true. */
  iconBadge?: boolean;
  /** Icon line color. Omitted = style.bodyColor. */
  iconColor?: string;
  /** Badge circle/ring color. Omitted = style.accentColor. */
  iconBadgeColor?: string;
};

export type WhiteboardExplainCharacterConfig = {
  name?: 'metan' | string;
  pose?: WhiteboardExplainPose | string;
  expression?: WhiteboardExplainExpression | string;
  /**
   * Optional override image path. Use Remotion staticFile compatible path, e.g.
   * /characters/metan/pointing_smile.png
   */
  image?: string;
};

export type WhiteboardExplainStyleConfig = {
  fontFamily?: string;
  titleColor?: string;
  themeColor?: string;
  headingColor?: string;
  bodyColor?: string;
  accentColor?: string;
  secondaryAccentColor?: string;
  markerColor?: string;
  /** Base pixel size at 1920px width. */
  titleFontSize?: number;
  /** Base pixel size at 1920px width. */
  themeFontSize?: number;
  /** Base pixel size at 1920px width. If omitted, layout-based default is used. */
  sectionHeadingFontSize?: number;
  /** Base pixel size at 1920px width. If omitted, layout-based default is used. */
  sectionBodyFontSize?: number;
  /** Base pixel size at 1920px width. */
  conclusionFontSize?: number;
  /** Conclusion box X in 1920x1080 base pixels. */
  conclusionBoxX?: number;
  /** Conclusion box Y in 1920x1080 base pixels. */
  conclusionBoxY?: number;
  /** Conclusion box width in 1920x1080 base pixels. */
  conclusionBoxWidth?: number;
  /** Conclusion box height in 1920x1080 base pixels. */
  conclusionBoxHeight?: number;
  /** Arrow 1 (section 1 → 2) X in 1920x1080 base pixels. */
  arrow0X?: number;
  /** Arrow 1 (section 1 → 2) Y in 1920x1080 base pixels. */
  arrow0Y?: number;
  /** Arrow 1 width in 1920x1080 base pixels. */
  arrow0Width?: number;
  /** Arrow 1 height in 1920x1080 base pixels. */
  arrow0Height?: number;
  /** Arrow 1 stroke width in 1920x1080 base pixels. */
  arrow0StrokeWidth?: number;
  /** Arrow 2 (section 2 → 3) X in 1920x1080 base pixels. */
  arrow1X?: number;
  /** Arrow 2 (section 2 → 3) Y in 1920x1080 base pixels. */
  arrow1Y?: number;
  /** Arrow 2 width in 1920x1080 base pixels. */
  arrow1Width?: number;
  /** Arrow 2 height in 1920x1080 base pixels. */
  arrow1Height?: number;
  /** Arrow 2 stroke width in 1920x1080 base pixels. */
  arrow1StrokeWidth?: number;
  boardColor?: string;
  boardFrameColor?: string;
  backgroundColor?: string;
};

export type WhiteboardExplainAnimationConfig = {
  mode?: WhiteboardExplainAnimationMode;
  /** Pop each section when it appears. Works with step mode and per-turn visibleSections. */
  sectionPop?: boolean;
  /** Pop arrows when they appear. */
  arrowPop?: boolean;
  /** Pop the conclusion box when it appears. */
  conclusionPop?: boolean;
  /** Draw handwritten underlines from left to right when newly shown. */
  underlineDraw?: boolean;
  /** Add a subtle emphasis flash when the conclusion appears. */
  conclusionImpact?: boolean;
};

export type WhiteboardExplainAssetsConfig = {
  /** Optional background image for the whole scene. */
  backgroundImage?: string;
  /** Optional whiteboard image. If omitted, a vector board is drawn. */
  whiteboardImage?: string;
  /** Optional map for icon image overrides. If omitted, SVG doodles are drawn. */
  iconImages?: Partial<Record<WhiteboardExplainIcon, string>>;
};

export type WhiteboardExplainInsertConfig = {
  type?: WhiteboardExplainInsertType;
  title: string;
  theme: string;
  sections: WhiteboardExplainSection[];
  conclusion: string;
  /** Per-turn toggle of arrows between sections. [0] is section1→2, [1] is section2→3. Omitted = visible. */
  visibleArrows?: [boolean, boolean];
  /** Per-turn toggle for the conclusion box. Omitted = visible. */
  showConclusion?: boolean;
  /** Optional decorative curved arrow pointing to the conclusion box. */
  showConclusionArrow?: boolean;
  /** Highlight the section currently being explained. 0..2. Omitted = no active highlight. */
  activeSection?: number;
  /** Optional explicit section highlight flags. Overrides activeSection when provided. */
  highlightSections?: [boolean, boolean, boolean];
  character?: WhiteboardExplainCharacterConfig;
  style?: WhiteboardExplainStyleConfig;
  animation?: WhiteboardExplainAnimationConfig;
  assets?: WhiteboardExplainAssetsConfig;
  /** ボード領域を広げてキャラを小さくする版を選ぶ。省略時は 'default'。 */
  layout?: WhiteboardExplainLayoutVariant;
};

export type NormalizedWhiteboardExplainInsertConfig = Required<
  Pick<WhiteboardExplainInsertConfig, 'title' | 'theme' | 'sections' | 'conclusion'>
> & {
  type: WhiteboardExplainInsertType;
  visibleArrows: [boolean, boolean];
  showConclusion: boolean;
  showConclusionArrow: boolean;
  activeSection?: number;
  highlightSections?: [boolean, boolean, boolean];
  character: Required<WhiteboardExplainCharacterConfig>;
  style: Required<WhiteboardExplainStyleConfig>;
  animation: Required<WhiteboardExplainAnimationConfig>;
  assets: Required<WhiteboardExplainAssetsConfig>;
  layout: WhiteboardExplainLayoutVariant;
};

export type WhiteboardExplainPopTargets = {
  sections?: [boolean, boolean, boolean];
  arrows?: [boolean, boolean];
  conclusion?: boolean;
};

export type WhiteboardExplainInsertProps = {
  config: WhiteboardExplainInsertConfig;
  /** Insert duration in frames. If omitted, Remotion composition duration is used. */
  durationInFrames?: number;
  /** Local frame from the start of the insert/turn. Required for per-turn pop animation in run-story. */
  localFrame?: number;
  /** Width. Defaults to 1920. */
  width?: number;
  /** Height. Defaults to 1080. */
  height?: number;
  /**
   * Optional override for the character visual, positioned/animated at layout.character.
   * When provided, this is rendered instead of the built-in image/placeholder
   * (used by run-story to render the project's own Avatar component here).
   */
  characterSlot?: import('react').ReactNode;
  /**
   * Per-turn override of which of the 3 sections are shown (index 0..2).
   * Omitted/undefined index defaults to visible. Lets a multi-turn narration
   * reveal one section per turn instead of all 3 within a single turn.
   */
  visibleSections?: [boolean, boolean, boolean];
  /** Optional prop override for arrow visibility. Omitted = config/default. */
  visibleArrows?: [boolean, boolean];
  /** Optional prop override for conclusion visibility. Omitted = config/default. */
  showConclusion?: boolean;
  /**
   * Which elements should pop at this turn start.
   * Used by run-story to pop only false→true changes across consecutive whiteboard turns.
   */
  popTargets?: WhiteboardExplainPopTargets;
};
