import React, { useState } from 'react';
import { Img, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { normalizeWhiteboardExplainConfig } from './whiteboardExplainDefaults';
import { getStepFrameRanges, getWhiteboardExplainLayout, Rect } from './whiteboardExplainLayout';
import { parseConclusionSegments } from './whiteboardExplainValidation';
import { resolveCharacterImage, resolveIconImage, toStaticFile } from './whiteboardExplainAssets';
import { WhiteboardDoodleIcon } from './WhiteboardDoodleIcon';
import type { WhiteboardExplainInsertProps, WhiteboardExplainSection } from './whiteboardExplainTypes';

const fade = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, end], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

const pop = (frame: number, start: number, end: number) => {
  // A small, marker-board friendly pop: appears a little smaller,
  // overshoots once, then settles at the normal scale.
  // Keep the inputRange strictly increasing even when the insert/step is short.
  const duration = end - start;
  if (duration <= 0) return 1;
  const p1 = start + duration * 0.35;
  const p2 = start + duration * 0.7;
  return interpolate(
    frame,
    [start, p1, p2, end],
    [0.92, 1.06, 0.98, 1],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
};

const quickPop = (frame: number, start = 0, duration = 18, strength = 1) => {
  const overshoot = 1 + 0.07 * strength;
  const undershoot = 0.98;
  const initial = 1 - 0.08 * strength;
  return interpolate(
    frame,
    [start, start + duration * 0.35, start + duration * 0.7, start + duration],
    [initial, overshoot, undershoot, 1],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
};

const quickFade = (frame: number, start = 0, duration = 10) =>
  interpolate(frame, [start, start + duration], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

const handwritingLine = (props: {
  x: number;
  y: number;
  width: number;
  color: string;
  strokeWidth?: number;
  progress?: number;
  variant?: number;
}) => {
  const { x, y, width, color, strokeWidth = 7, progress = 1, variant = 0 } = props;
  const clippedWidth = Math.max(0, Math.min(1, progress)) * width;
  const variants = [
    `M4 11 C ${width * 0.16} 8.6, ${width * 0.30} 13.9, ${width * 0.44} 11.2 S ${width * 0.70} 8.8, ${width - 4} 10.8`,
    `M4 11 C ${width * 0.18} 12.8, ${width * 0.31} 8.9, ${width * 0.50} 11.1 S ${width * 0.79} 13.1, ${width - 4} 10.9`,
    `M4 10.8 C ${width * 0.14} 9.2, ${width * 0.25} 12.9, ${width * 0.41} 11.0 S ${width * 0.61} 8.6, ${width * 0.77} 10.9 S ${width * 0.92} 12.2, ${width - 4} 10.7`,
    `M4 11.3 C ${width * 0.13} 13.0, ${width * 0.29} 8.5, ${width * 0.46} 11.2 S ${width * 0.66} 13.0, ${width * 0.82} 10.7 S ${width * 0.93} 9.9, ${width - 4} 11.1`,
    `M4 11 C ${width * 0.15} 8.9, ${width * 0.36} 12.5, ${width * 0.53} 11.0 S ${width * 0.77} 8.8, ${width - 4} 11.2`,
  ];
  const pathD = variants[((variant % variants.length) + variants.length) % variants.length];
  const ghostVariants = [
    `M8 12.3 C ${width * 0.22} 11.5, ${width * 0.43} 10.5, ${width * 0.69} 11.5 S ${width * 0.89} 12.2, ${width - 10} 11.5`,
    `M7 12.2 C ${width * 0.20} 10.9, ${width * 0.40} 12.3, ${width * 0.67} 11.2 S ${width * 0.89} 11.8, ${width - 9} 11.1`,
    `M8 12.4 C ${width * 0.18} 12.0, ${width * 0.42} 10.6, ${width * 0.66} 11.8 S ${width * 0.88} 12.6, ${width - 11} 11.8`,
    `M8 12.0 C ${width * 0.16} 11.1, ${width * 0.39} 11.8, ${width * 0.63} 11.0 S ${width * 0.87} 12.3, ${width - 10} 11.4`,
    `M8 12.1 C ${width * 0.24} 11.0, ${width * 0.44} 11.8, ${width * 0.67} 11.3 S ${width * 0.89} 12.0, ${width - 10} 11.4`,
  ];
  const ghostD = ghostVariants[((variant % ghostVariants.length) + ghostVariants.length) % ghostVariants.length];
  return (
    <div style={{ position: 'absolute', left: x, top: y, width: clippedWidth, height: 22, overflow: 'hidden' }}>
      <svg
        style={{ position: 'absolute', left: 0, top: 0, width, height: 22, overflow: 'visible' }}
        viewBox={`0 0 ${width} 22`}
      >
        <path
          d={pathD}
          stroke={color}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
        />
        <path
          d={ghostD}
          stroke={color}
          strokeWidth={Math.max(1, strokeWidth * 0.33)}
          fill="none"
          strokeLinecap="round"
          opacity={0.18}
        />
      </svg>
    </div>
  );
};

const drawProgress = (frame: number, enabled: boolean, start = 4, duration = 16) => {
  if (!enabled) return 1;
  return interpolate(frame, [start, start + Math.max(1, duration)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
};

const estimateWrappedLineCount = (text: string, fontSize: number, maxWidth: number) => {
  const chars = [...String(text || '')];
  if (chars.length === 0) return 1;
  const charWidth = fontSize * 0.92;
  const charsPerLine = Math.max(1, Math.floor(maxWidth / Math.max(1, charWidth)));
  return Math.max(1, Math.ceil(chars.length / charsPerLine));
};

const Arrow: React.FC<{ x: number; y: number; width: number; height: number; strokeWidth: number; color: string }> = ({ x, y, width, height, strokeWidth, color }) => {
  const midY = height / 2;
  const headX = Math.max(18, width - 8);
  const headBackX = Math.max(10, width - 32);
  const headOffsetY = Math.max(9, height * 0.25);
  return (
    <svg style={{ position: 'absolute', left: x, top: y, width, height, overflow: 'visible' }} viewBox={`0 0 ${width} ${height}`}>
      <path
        d={`M8 ${midY + height * 0.02} C ${width * 0.28} ${midY - height * 0.10}, ${width * 0.50} ${midY + height * 0.08}, ${width * 0.72} ${midY - height * 0.03} S ${width - 34} ${midY + height * 0.06}, ${width - 24} ${midY + height * 0.01}`}
        stroke={color}
        strokeWidth={strokeWidth}
        fill="none"
        strokeLinecap="round"
      />
      <path
        d={`M${headBackX} ${midY - headOffsetY} Q ${headX - 7} ${midY - headOffsetY * 0.12}, ${headX} ${midY}`}
        stroke={color}
        strokeWidth={strokeWidth}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={`M${headBackX + 1} ${midY + headOffsetY} Q ${headX - 8} ${midY + headOffsetY * 0.18}, ${headX} ${midY}`}
        stroke={color}
        strokeWidth={strokeWidth}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const ConclusionArrow: React.FC<{ width: number; height: number; strokeWidth: number; color: string }> = ({ width, height, strokeWidth, color }) => {
  const headX = width - 8;
  const headBackX = width - 34;
  const headY = height * 0.82;
  const headOffsetY = Math.max(12, height * 0.17);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ overflow: 'visible' }}>
      <path
        d={`M ${width * 0.12} ${height * 0.20} C ${width * 0.03} ${height * 0.69}, ${width * 0.47} ${height * 0.98}, ${width * 0.77} ${height * 0.86} S ${width * 0.92} ${height * 0.78}, ${headX} ${headY}`}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      <path
        d={`M ${headBackX} ${headY - headOffsetY} Q ${headX - 8} ${headY - headOffsetY * 0.12}, ${headX} ${headY}`}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={`M ${headBackX} ${headY + headOffsetY} Q ${headX - 8} ${headY + headOffsetY * 0.12}, ${headX} ${headY}`}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const BoardText: React.FC<{
  children: React.ReactNode;
  rect: Rect;
  fontSize: number;
  color: string;
  fontFamily: string;
  align?: React.CSSProperties['textAlign'];
  lineHeight?: number;
  weight?: React.CSSProperties['fontWeight'];
}> = ({ children, rect, fontSize, color, fontFamily, align = 'left', lineHeight = 1.2, weight = 400 }) => (
  <div
    style={{
      position: 'absolute',
      left: rect.x,
      top: rect.y,
      width: rect.width,
      height: rect.height,
      fontFamily,
      fontSize,
      lineHeight,
      fontWeight: weight,
      color,
      textAlign: align,
      whiteSpace: 'pre-wrap',
      letterSpacing: '0.02em',
    }}
  >
    {children}
  </div>
);

const SectionBlock: React.FC<{
  section: WhiteboardExplainSection;
  index: number;
  rect: Rect;
  fontFamily: string;
  headingColor: string;
  bodyColor: string;
  accentColor: string;
  iconImage?: string;
  widthScale: number;
  heightScale: number;
  sectionHeadingFontSize?: number;
  sectionBodyFontSize?: number;
  underlineProgress?: number;
  highlighted?: boolean;
  markerColor?: string;
}> = ({ section, index, rect, fontFamily, headingColor, bodyColor, accentColor, iconImage, widthScale, heightScale, sectionHeadingFontSize, sectionBodyFontSize, underlineProgress = 1, highlighted = false, markerColor = '#f6db45' }) => {
  const [iconFailed, setIconFailed] = useState(false);
  const heading = section.heading;
  const bullets = section.bullets.slice(0, 3);
  const headingFont = Math.max(22, sectionHeadingFontSize ?? rect.width * 0.075);
  const bodyFont = Math.max(20, sectionBodyFontSize ?? rect.width * 0.066);
  const iconSize = Math.max(24, (section.iconSize ?? 120) * widthScale);
  const iconLeft = (typeof section.iconX === 'number' ? section.iconX : rect.width / widthScale - 128) * widthScale;
  const iconTop = (typeof section.iconY === 'number' ? section.iconY : 155) * heightScale;
  const headingTextWidth = Math.max(1, rect.width - headingFont * 1.15 - 12);
  const headingLines = estimateWrappedLineCount(heading, headingFont, headingTextWidth);
  const headingHeight = Math.max(headingFont * 1.15, headingLines * headingFont);
  const bodyTextWidth = Math.max(1, rect.width - 16 - bodyFont);
  const bodyLineHeight = bodyFont * 1.45;
  const bodyLines = bullets.reduce((sum, bullet) => sum + estimateWrappedLineCount(bullet, bodyFont, bodyTextWidth), 0);
  const highlightHeight = Math.min(
    rect.height + 28,
    Math.max(headingHeight + 50, 92 + bodyLines * bodyLineHeight + 28),
  );

  return (
    <div style={{ position: 'absolute', left: rect.x, top: rect.y, width: rect.width, height: rect.height }}>
      {highlighted && (
        <div
          style={{
            position: 'absolute',
            left: -18,
            top: -18,
            width: rect.width + 24,
            height: highlightHeight,
            borderRadius: 24,
            background: markerColor,
            opacity: 0.18,
            transform: 'rotate(-1deg)',
            boxShadow: `0 0 0 4px ${markerColor}22`,
          }}
        />
      )}
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          fontFamily,
          fontSize: headingFont,
          color: headingColor,
          lineHeight: 1,
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: headingFont * 1.15,
            height: headingFont * 1.15,
            border: `4px solid ${headingColor}`,
            borderRadius: 999,
          }}
        >
          {index + 1}
        </span>
        <span style={{ whiteSpace: 'normal', overflowWrap: 'break-word', lineHeight: 1.05 }}>{heading}</span>
      </div>
      {handwritingLine({ x: 54, y: headingFont + 13, width: rect.width - 72, color: headingColor, strokeWidth: 5, progress: underlineProgress, variant: index + 2 })}

      <div
        style={{
          position: 'absolute',
          left: 8,
          top: 92,
          fontFamily,
          fontSize: bodyFont,
          lineHeight: 1.45,
          color: bodyColor,
        }}
      >
        {bullets.map((bullet, bulletIndex) => (
          <div key={bulletIndex} style={{ paddingLeft: '1em', textIndent: '-1em' }}>・{bullet}</div>
        ))}
      </div>

      {section.icon && section.icon !== 'none' && (() => {
        const showBadge = section.iconBadge !== false;
        const iconColor = section.iconColor || bodyColor;
        const badgeColor = section.iconBadgeColor || accentColor;
        const badgeSize = iconSize * 1.34;
        const badgeOffset = (badgeSize - iconSize) / 2;
        const ringWidth = Math.max(3, iconSize * 0.032);
        return (
          <div style={{ position: 'absolute', left: iconLeft - badgeOffset, top: iconTop - badgeOffset, width: badgeSize, height: badgeSize }}>
            {showBadge && (
              <>
                <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: badgeColor, opacity: 0.1 }} />
                <div
                  style={{
                    position: 'absolute',
                    inset: badgeSize * 0.05,
                    borderRadius: '50%',
                    border: `${ringWidth}px solid ${badgeColor}`,
                    opacity: 0.55,
                    transform: 'rotate(-2deg)',
                  }}
                />
              </>
            )}
            <div style={{ position: 'absolute', left: badgeOffset, top: badgeOffset, width: iconSize, height: iconSize, opacity: 0.94 }}>
              {iconImage && !iconFailed ? (
                <Img
                  src={iconImage}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                  onError={() => setIconFailed(true)}
                />
              ) : (
                <WhiteboardDoodleIcon icon={section.icon} color={iconColor} accentColor={badgeColor} size={iconSize} />
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
};

const VectorWhiteboard: React.FC<{ rect: Rect; boardColor: string; frameColor: string }> = ({ rect, boardColor, frameColor }) => (
  <div
    style={{
      position: 'absolute',
      left: rect.x,
      top: rect.y,
      width: rect.width,
      height: rect.height,
      background: boardColor,
      border: `${Math.max(8, rect.width * 0.008)}px solid ${frameColor}`,
      borderRadius: 14,
      boxShadow: '0 16px 40px rgba(0,0,0,0.18)',
    }}
  >
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: -28,
        height: 34,
        background: frameColor,
        borderRadius: 6,
      }}
    />
    <div style={{ position: 'absolute', bottom: -20, left: 130, width: 120, height: 22, borderRadius: 8, background: '#222' }} />
    <div style={{ position: 'absolute', bottom: -18, left: 520, width: 160, height: 16, borderRadius: 8, background: '#f7f7f7', border: '3px solid #333' }} />
    <div style={{ position: 'absolute', bottom: -18, left: 690, width: 160, height: 16, borderRadius: 8, background: '#2d74d4', border: '3px solid #333' }} />
    <div style={{ position: 'absolute', bottom: -18, left: 870, width: 160, height: 16, borderRadius: 8, background: '#db3434', border: '3px solid #333' }} />
    <div style={{ position: 'absolute', bottom: -22, left: 1070, width: 150, height: 34, borderRadius: 8, background: '#2a54a2' }} />
  </div>
);

export const WhiteboardExplainInsert: React.FC<WhiteboardExplainInsertProps> = ({
  config,
  durationInFrames,
  width,
  height,
  characterSlot,
  visibleSections,
  visibleArrows,
  showConclusion,
  popTargets,
  localFrame,
}) => {
  const [bgImageFailed, setBgImageFailed] = useState(false);
  const [boardImageFailed, setBoardImageFailed] = useState(false);
  const [characterImageFailed, setCharacterImageFailed] = useState(false);
  const absoluteFrame = useCurrentFrame();
  const videoConfig = useVideoConfig();
  const actualWidth = width ?? videoConfig.width ?? 1920;
  const actualHeight = height ?? videoConfig.height ?? 1080;
  const actualDuration = durationInFrames ?? videoConfig.durationInFrames ?? 420;
  // useCurrentFrame() is composition-absolute in run-story, so per-turn pop animations
  // need the frame relative to the current insert/turn start. StoryVideo passes localFrame.
  const frame = typeof localFrame === 'number' ? localFrame : absoluteFrame;
  const normalized = normalizeWhiteboardExplainConfig(config);
  const layout = getWhiteboardExplainLayout(actualWidth, actualHeight, normalized.layout);
  const ranges = getStepFrameRanges(actualDuration);
  const mode = normalized.animation.mode;

  const showAt = (key: keyof ReturnType<typeof getStepFrameRanges>) => {
    if (mode === 'none' || mode === 'all') return { opacity: 1, scale: 1 };
    const range = ranges[key];
    return { opacity: fade(frame, range.start, range.end), scale: pop(frame, range.start, range.end) };
  };

  const popOnTurnStart = (enabled: boolean | undefined, strength = 1) => {
    if (mode === 'none' || enabled === false) return { opacity: 1, scale: 1 };
    return { opacity: quickFade(frame, 0, 8), scale: quickPop(frame, 0, 18, strength) };
  };

  const bgImage = toStaticFile(normalized.assets.backgroundImage);
  const boardImage = toStaticFile(normalized.assets.whiteboardImage);
  const characterImage = resolveCharacterImage(normalized);
  const fontFamily = normalized.style.fontFamily;
  const widthScale = actualWidth / 1920;
  const titleFontSize = normalized.style.titleFontSize * widthScale;
  const themeFontSize = normalized.style.themeFontSize * widthScale;
  const sectionHeadingFontSize = normalized.style.sectionHeadingFontSize * widthScale;
  const sectionBodyFontSize = normalized.style.sectionBodyFontSize * widthScale;
  const conclusionFontSize = normalized.style.conclusionFontSize * widthScale;
  const conclusionRect = {
    x: normalized.style.conclusionBoxX * widthScale,
    y: normalized.style.conclusionBoxY * (actualHeight / 1080),
    width: normalized.style.conclusionBoxWidth * widthScale,
    height: normalized.style.conclusionBoxHeight * (actualHeight / 1080),
  };
  const effectiveVisibleSections = visibleSections ?? ([true, true, true] as [boolean, boolean, boolean]);
  const latestVisibleSectionIndex = effectiveVisibleSections.reduce((latest, isVisible, index) => isVisible ? index : latest, -1);
  const effectiveHighlightSections: [boolean, boolean, boolean] = normalized.highlightSections
    ?? [normalized.activeSection === 0, normalized.activeSection === 1, normalized.activeSection === 2];
  const effectiveVisibleArrows = visibleArrows ?? normalized.visibleArrows;
  const latestVisibleArrowIndex = effectiveVisibleArrows.reduce((latest, isVisible, index) => isVisible ? index : latest, -1);
  const effectiveShowConclusion = showConclusion ?? normalized.showConclusion;
  const effectiveShowConclusionArrow = normalized.showConclusionArrow && effectiveShowConclusion;

  const boardInner = {
    x: layout.board.x,
    y: layout.board.y,
    width: layout.board.width,
    height: layout.board.height,
  };

  const bgVisibility = showAt('background');
  const charVisibility = showAt('character');
  const titleVisibility = showAt('title');
  const themeVisibility = showAt('theme');
  const underlineDrawEnabled = normalized.animation.underlineDraw && mode !== 'none';
  const titleLineProgress = mode === 'step' ? fade(frame, ranges.title.start + 4, ranges.title.end + 12) : 1;
  const themeLineProgress = mode === 'step' ? fade(frame, ranges.theme.start + 4, ranges.theme.end + 12) : 1;
  const baseConclusionVisibility = showAt('conclusion');
  const shouldPopConclusion = normalized.animation.conclusionPop && mode !== 'none'
    && (popTargets ? popTargets.conclusion === true : effectiveShowConclusion);
  const turnStartConclusionPop = shouldPopConclusion
    ? popOnTurnStart(true, 1.6)
    : { opacity: 1, scale: 1 };
  const conclusionVisibility = {
    opacity: baseConclusionVisibility.opacity * turnStartConclusionPop.opacity,
    scale: baseConclusionVisibility.scale * turnStartConclusionPop.scale,
  };
  const conclusionImpactOpacity = shouldPopConclusion && normalized.animation.conclusionImpact
    ? interpolate(frame, [0, 6, 18], [0, 0.28, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })
    : 0;
  const conclusionMarkerProgress = underlineDrawEnabled && shouldPopConclusion ? drawProgress(frame, true, 8, 18) : 1;
  const conclusionArrowVisibility = shouldPopConclusion ? popOnTurnStart(true, 0.95) : { opacity: 1, scale: 1 };
  const conclusionArrowRect = {
    left: conclusionRect.x - 126 * widthScale,
    top: conclusionRect.y - 14 * (actualHeight / 1080),
    width: 168 * widthScale,
    height: 128 * (actualHeight / 1080),
  };

  return (
    <div style={{ position: 'relative', width: actualWidth, height: actualHeight, overflow: 'hidden', background: normalized.style.backgroundColor }}>
      {bgImage && !bgImageFailed ? (
        <Img
          src={bgImage}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: bgVisibility.opacity }}
          onError={() => setBgImageFailed(true)}
        />
      ) : (
        <>
          <div style={{ position: 'absolute', inset: 0, background: normalized.style.backgroundColor, opacity: bgVisibility.opacity }} />
          <div style={{ position: 'absolute', left: 0, top: 0, width: actualWidth * 0.16, height: actualHeight, background: 'rgba(255,255,255,0.35)' }} />
        </>
      )}

      {boardImage && !boardImageFailed ? (
        <Img
          src={boardImage}
          style={{ position: 'absolute', left: boardInner.x, top: boardInner.y, width: boardInner.width, height: boardInner.height, objectFit: 'fill' }}
          onError={() => setBoardImageFailed(true)}
        />
      ) : (
        <VectorWhiteboard rect={layout.board} boardColor={normalized.style.boardColor} frameColor={normalized.style.boardFrameColor} />
      )}

      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: titleVisibility.opacity,
          transform: `scale(${titleVisibility.scale})`,
          transformOrigin: `${layout.title.x}px ${layout.title.y}px`,
        }}
      >
        <BoardText rect={layout.title} fontSize={titleFontSize} color={normalized.style.titleColor} fontFamily={fontFamily} align="center" weight={700}>
          {normalized.title}
        </BoardText>
        {handwritingLine({ x: layout.title.x + 90, y: layout.title.y + 92 * (actualHeight / 1080), width: layout.title.width - 180, color: normalized.style.accentColor, progress: underlineDrawEnabled ? titleLineProgress : 1, variant: 0 })}
      </div>

      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: themeVisibility.opacity,
          transform: `scale(${themeVisibility.scale})`,
          transformOrigin: `${layout.theme.x}px ${layout.theme.y}px`,
        }}
      >
        <BoardText rect={layout.theme} fontSize={themeFontSize} color={normalized.style.themeColor} fontFamily={fontFamily} align="center" weight={600}>
          {normalized.theme}
        </BoardText>
        {handwritingLine({ x: layout.theme.x - 25, y: layout.theme.y + 67 * (actualHeight / 1080), width: layout.theme.width + 50, color: normalized.style.accentColor, strokeWidth: 6, progress: underlineDrawEnabled ? themeLineProgress : 1, variant: 1 })}
      </div>

      {normalized.sections.map((section, index) => {
        const key = `section${index}` as 'section0' | 'section1' | 'section2';
        const baseVisibility = showAt(key);
        const rect = layout.sections[index];
        const iconImage = resolveIconImage(normalized, section.icon);
        const sectionHidden = effectiveVisibleSections[index] === false;
        const shouldPopThisSection = normalized.animation.sectionPop && mode !== 'none'
          && (popTargets ? popTargets.sections?.[index] === true : (visibleSections ? index === latestVisibleSectionIndex : mode === 'all'));
        const turnStartSectionPop = shouldPopThisSection ? popOnTurnStart(true, 1) : { opacity: 1, scale: 1 };
        const visibility = {
          opacity: baseVisibility.opacity * turnStartSectionPop.opacity,
          scale: baseVisibility.scale * turnStartSectionPop.scale,
        };
        const sectionLineProgress = underlineDrawEnabled
          ? (shouldPopThisSection ? drawProgress(frame, true, 5, 14) : (mode === 'step' ? fade(frame, ranges[key].start + 4, ranges[key].end + 10) : 1))
          : 1;
        return (
          <div
            key={index}
            style={{
              position: 'absolute',
              inset: 0,
              opacity: sectionHidden ? 0 : visibility.opacity,
              transform: `scale(${visibility.scale})`,
              transformOrigin: `${rect.x}px ${rect.y}px`,
            }}
          >
            <SectionBlock
              section={section}
              index={index}
              rect={rect}
              fontFamily={fontFamily}
              headingColor={normalized.style.headingColor}
              bodyColor={normalized.style.bodyColor}
              accentColor={normalized.style.accentColor}
              iconImage={iconImage}
              widthScale={widthScale}
              heightScale={actualHeight / 1080}
              sectionHeadingFontSize={sectionHeadingFontSize}
              sectionBodyFontSize={sectionBodyFontSize}
              underlineProgress={sectionLineProgress}
              highlighted={effectiveHighlightSections[index] === true}
              markerColor={normalized.style.markerColor}
            />
            {index < 2 && effectiveVisibleArrows[index] !== false && (() => {
              const arrowX = index === 0 ? normalized.style.arrow0X : normalized.style.arrow1X;
              const arrowY = index === 0 ? normalized.style.arrow0Y : normalized.style.arrow1Y;
              const arrowWidth = index === 0 ? normalized.style.arrow0Width : normalized.style.arrow1Width;
              const arrowHeight = index === 0 ? normalized.style.arrow0Height : normalized.style.arrow1Height;
              const arrowStrokeWidth = index === 0 ? normalized.style.arrow0StrokeWidth : normalized.style.arrow1StrokeWidth;
              const shouldPopThisArrow = normalized.animation.arrowPop && mode !== 'none'
                && (popTargets ? popTargets.arrows?.[index] === true : (visibleArrows ? index === latestVisibleArrowIndex : mode === 'all'));
              const arrowVisibility = shouldPopThisArrow ? popOnTurnStart(true, 0.85) : { opacity: 1, scale: 1 };
              const scaledX = arrowX * widthScale;
              const scaledY = arrowY * (actualHeight / 1080);
              const scaledWidth = arrowWidth * widthScale;
              const scaledHeight = arrowHeight * (actualHeight / 1080);
              return (
                <div
                  style={{
                    position: 'absolute',
                    left: scaledX,
                    top: scaledY,
                    width: scaledWidth,
                    height: scaledHeight,
                    opacity: arrowVisibility.opacity,
                    transform: `scale(${arrowVisibility.scale})`,
                    transformOrigin: 'center center',
                  }}
                >
                  <Arrow
                    x={0}
                    y={0}
                    width={scaledWidth}
                    height={scaledHeight}
                    strokeWidth={arrowStrokeWidth * widthScale}
                    color={normalized.style.bodyColor}
                  />
                </div>
              );
            })()}
          </div>
        );
      })}

      {effectiveShowConclusionArrow && (
        <div
          style={{
            position: 'absolute',
            left: conclusionArrowRect.left,
            top: conclusionArrowRect.top,
            width: conclusionArrowRect.width,
            height: conclusionArrowRect.height,
            opacity: conclusionArrowVisibility.opacity,
            transform: `scale(${conclusionArrowVisibility.scale})`,
            transformOrigin: 'left top',
            pointerEvents: 'none',
          }}
        >
          <ConclusionArrow
            width={conclusionArrowRect.width}
            height={conclusionArrowRect.height}
            strokeWidth={Math.max(13, 16 * widthScale)}
            color={normalized.style.accentColor}
          />
        </div>
      )}

      {effectiveShowConclusion && (
      <div
        style={{
          position: 'absolute',
          left: conclusionRect.x,
          top: conclusionRect.y,
          width: conclusionRect.width,
          height: conclusionRect.height,
          opacity: conclusionVisibility.opacity,
          transform: `scale(${conclusionVisibility.scale})`,
          transformOrigin: 'center center',
          borderRadius: 24 * (actualWidth / 1920),
          background: 'rgba(255,255,255,0.35)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 20,
          boxSizing: 'border-box',
        }}
      >
        <svg
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible', pointerEvents: 'none' }}
          viewBox={`0 0 ${conclusionRect.width} ${conclusionRect.height}`}
          preserveAspectRatio="none"
        >
          <path
            d={`M 24 22 C ${conclusionRect.width * 0.30} 18, ${conclusionRect.width * 0.67} 26, ${conclusionRect.width - 24} 20 C ${conclusionRect.width - 16} 20, ${conclusionRect.width - 10} ${conclusionRect.height * 0.28}, ${conclusionRect.width - 18} ${conclusionRect.height - 24} C ${conclusionRect.width * 0.68} ${conclusionRect.height - 18}, ${conclusionRect.width * 0.30} ${conclusionRect.height - 26}, 22 ${conclusionRect.height - 18} C 15 ${conclusionRect.height - 18}, 11 ${conclusionRect.height * 0.69}, 16 24 Z`}
            fill="none"
            stroke={normalized.style.accentColor}
            strokeWidth={Math.max(5, 6 * widthScale)}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {conclusionImpactOpacity > 0 && (
          <div
            style={{
              position: 'absolute',
              inset: -10,
              borderRadius: 30 * (actualWidth / 1920),
              background: 'rgba(255,255,255,0.85)',
              opacity: conclusionImpactOpacity,
              pointerEvents: 'none',
            }}
          />
        )}
        <div
          style={{
            fontFamily,
            fontSize: conclusionFontSize,
            lineHeight: 1.5,
            color: normalized.style.bodyColor,
            textAlign: 'center',
            whiteSpace: 'pre-wrap',
          }}
        >
          {parseConclusionSegments(normalized.conclusion).map((segment, segmentIndex) =>
            segment.highlighted ? (
              <span
                key={segmentIndex}
                style={{
                  background: `linear-gradient(to top, ${normalized.style.markerColor} 40%, transparent 40%)`,
                  boxDecorationBreak: 'clone',
                  WebkitBoxDecorationBreak: 'clone',
                  padding: '0 0.06em',
                  opacity: interpolate(conclusionMarkerProgress, [0, 1], [0.35, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
                }}
              >
                {segment.text}
              </span>
            ) : (
              <React.Fragment key={segmentIndex}>{segment.text}</React.Fragment>
            ),
          )}
        </div>
      </div>
      )}

      {characterSlot ? (
        <div
          style={{
            position: 'absolute',
            left: layout.character.x,
            top: layout.character.y,
            width: layout.character.width,
            height: layout.character.height,
            opacity: charVisibility.opacity,
            transform: `translateY(${(1 - charVisibility.opacity) * 18}px) scale(${charVisibility.scale})`,
            transformOrigin: 'bottom center',
          }}
        >
          {characterSlot}
        </div>
      ) : characterImage && !characterImageFailed ? (
        <Img
          src={characterImage}
          style={{
            position: 'absolute',
            left: layout.character.x,
            top: layout.character.y,
            width: layout.character.width,
            height: layout.character.height,
            objectFit: 'contain',
            opacity: charVisibility.opacity,
            transform: `translateY(${(1 - charVisibility.opacity) * 18}px) scale(${charVisibility.scale})`,
            transformOrigin: 'bottom center',
            filter: 'drop-shadow(0 12px 12px rgba(0,0,0,0.18))',
          }}
          onError={() => setCharacterImageFailed(true)}
        />
      ) : (
        <div
          style={{
            position: 'absolute',
            left: layout.character.x + layout.character.width * 0.18,
            top: layout.character.y + layout.character.height * 0.06,
            width: layout.character.width * 0.56,
            height: layout.character.height * 0.82,
            opacity: charVisibility.opacity,
            transform: `translateY(${(1 - charVisibility.opacity) * 18}px) scale(${charVisibility.scale})`,
            transformOrigin: 'bottom center',
            borderRadius: '45% 45% 35% 35%',
            background: 'linear-gradient(#f5a1c8, #ffffff 35%, #f7d4e8 100%)',
            border: '6px solid rgba(120,60,100,0.45)',
            boxShadow: '0 12px 18px rgba(0,0,0,0.18)',
          }}
        >
          <div style={{ position: 'absolute', top: 78, left: 88, fontFamily, fontSize: 48, color: '#6a2459' }}>めたん</div>
          <div style={{ position: 'absolute', top: 155, left: 75, fontFamily, fontSize: 36, color: '#6a2459' }}>立ち絵を配置</div>
        </div>
      )}
    </div>
  );
};
