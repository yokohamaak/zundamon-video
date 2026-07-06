export type WhiteboardExplainInsertType = 'whiteboard_explain';

export type WhiteboardExplainIcon =
  | 'none'
  | 'confused'
  | 'scribble'
  | 'checklist'
  | 'memo'
  | 'conversation'
  | 'warning'
  | 'idea'
  | 'table';

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

export type WhiteboardExplainSection = {
  heading: string;
  bullets: string[];
  icon?: WhiteboardExplainIcon;
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
  boardColor?: string;
  boardFrameColor?: string;
  backgroundColor?: string;
};

export type WhiteboardExplainAnimationConfig = {
  mode?: WhiteboardExplainAnimationMode;
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
  character?: WhiteboardExplainCharacterConfig;
  style?: WhiteboardExplainStyleConfig;
  animation?: WhiteboardExplainAnimationConfig;
  assets?: WhiteboardExplainAssetsConfig;
};

export type NormalizedWhiteboardExplainInsertConfig = Required<
  Pick<WhiteboardExplainInsertConfig, 'title' | 'theme' | 'sections' | 'conclusion'>
> & {
  type: WhiteboardExplainInsertType;
  character: Required<WhiteboardExplainCharacterConfig>;
  style: Required<WhiteboardExplainStyleConfig>;
  animation: Required<WhiteboardExplainAnimationConfig>;
  assets: Required<WhiteboardExplainAssetsConfig>;
};

export type WhiteboardExplainInsertProps = {
  config: WhiteboardExplainInsertConfig;
  /** Insert duration in frames. If omitted, Remotion composition duration is used. */
  durationInFrames?: number;
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
};
