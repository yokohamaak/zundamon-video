import React from 'react';
import { Img, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { normalizeWhiteboardExplainConfig } from './whiteboardExplainDefaults';
import { getStepFrameRanges, getWhiteboardExplainLayout, Rect } from './whiteboardExplainLayout';
import { fitText } from './whiteboardExplainValidation';
import { resolveCharacterImage, resolveIconImage, toStaticFile } from './whiteboardExplainAssets';
import { WhiteboardDoodleIcon } from './WhiteboardDoodleIcon';
import type { WhiteboardExplainInsertProps, WhiteboardExplainSection } from './whiteboardExplainTypes';

const fade = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, end], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

const pop = (frame: number, start: number, end: number) => {
  const p = fade(frame, start, end);
  return 0.96 + p * 0.04;
};

const handwritingLine = (props: {
  x: number;
  y: number;
  width: number;
  color: string;
  strokeWidth?: number;
}) => {
  const { x, y, width, color, strokeWidth = 7 } = props;
  return (
    <svg
      style={{ position: 'absolute', left: x, top: y, width, height: 22, overflow: 'visible' }}
      viewBox={`0 0 ${width} 22`}
    >
      <path
        d={`M4 10 C ${width * 0.18} 18, ${width * 0.34} 2, ${width * 0.5} 10 S ${width * 0.82} 18, ${width - 4} 10`}
        stroke={color}
        strokeWidth={strokeWidth}
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
};

const Arrow: React.FC<{ x: number; y: number; width: number; color: string }> = ({ x, y, width, color }) => (
  <svg style={{ position: 'absolute', left: x, top: y, width, height: 54, overflow: 'visible' }} viewBox={`0 0 ${width} 54`}>
    <path d={`M8 26 C ${width * 0.35} 22, ${width * 0.6} 30, ${width - 25} 26`} stroke={color} strokeWidth="9" fill="none" strokeLinecap="round" />
    <path d={`M${width - 35} 10 L${width - 8} 26 L${width - 35} 44`} stroke={color} strokeWidth="9" fill="none" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

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
}> = ({ section, index, rect, fontFamily, headingColor, bodyColor, accentColor, iconImage }) => {
  const heading = fitText(section.heading, 14);
  const bullets = section.bullets.slice(0, 3).map((bullet) => fitText(bullet, 16));
  const headingFont = Math.max(28, rect.width * 0.075);
  const bodyFont = Math.max(26, rect.width * 0.066);

  return (
    <div style={{ position: 'absolute', left: rect.x, top: rect.y, width: rect.width, height: rect.height }}>
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
          alignItems: 'center',
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
        <span>{heading}</span>
      </div>
      {handwritingLine({ x: 54, y: headingFont + 13, width: rect.width - 72, color: headingColor, strokeWidth: 5 })}

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
          <div key={bulletIndex}>・{bullet}</div>
        ))}
      </div>

      <div style={{ position: 'absolute', right: 10, bottom: 8, width: 130, height: 130, opacity: 0.92 }}>
        {iconImage ? (
          <Img src={iconImage} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
        ) : (
          <WhiteboardDoodleIcon icon={section.icon} color={bodyColor} accentColor={accentColor} size={130} />
        )}
      </div>
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
}) => {
  const frame = useCurrentFrame();
  const videoConfig = useVideoConfig();
  const actualWidth = width ?? videoConfig.width ?? 1920;
  const actualHeight = height ?? videoConfig.height ?? 1080;
  const actualDuration = durationInFrames ?? videoConfig.durationInFrames ?? 420;
  const normalized = normalizeWhiteboardExplainConfig(config);
  const layout = getWhiteboardExplainLayout(actualWidth, actualHeight);
  const ranges = getStepFrameRanges(actualDuration);
  const mode = normalized.animation.mode;

  const showAt = (key: keyof ReturnType<typeof getStepFrameRanges>) => {
    if (mode === 'none' || mode === 'all') return { opacity: 1, scale: 1 };
    const range = ranges[key];
    return { opacity: fade(frame, range.start, range.end), scale: pop(frame, range.start, range.end) };
  };

  const bgImage = toStaticFile(normalized.assets.backgroundImage);
  const boardImage = toStaticFile(normalized.assets.whiteboardImage);
  const characterImage = resolveCharacterImage(normalized);
  const fontFamily = normalized.style.fontFamily;

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
  const conclusionVisibility = showAt('conclusion');

  return (
    <div style={{ position: 'relative', width: actualWidth, height: actualHeight, overflow: 'hidden', background: normalized.style.backgroundColor }}>
      {bgImage ? (
        <Img src={bgImage} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: bgVisibility.opacity }} />
      ) : (
        <>
          <div style={{ position: 'absolute', inset: 0, background: normalized.style.backgroundColor, opacity: bgVisibility.opacity }} />
          <div style={{ position: 'absolute', left: 0, top: 0, width: actualWidth * 0.16, height: actualHeight, background: 'rgba(255,255,255,0.35)' }} />
          <div style={{ position: 'absolute', right: actualWidth * 0.04, top: actualHeight * 0.14, width: actualWidth * 0.13, height: actualHeight * 0.7, background: 'rgba(120,90,60,0.12)', borderRadius: 12 }} />
        </>
      )}

      {boardImage ? (
        <Img src={boardImage} style={{ position: 'absolute', left: boardInner.x, top: boardInner.y, width: boardInner.width, height: boardInner.height, objectFit: 'fill' }} />
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
        <BoardText rect={layout.title} fontSize={72 * (actualWidth / 1920)} color={normalized.style.titleColor} fontFamily={fontFamily} align="center" weight={700}>
          {fitText(normalized.title, 18)}
        </BoardText>
        {handwritingLine({ x: layout.title.x + 90, y: layout.title.y + 92 * (actualHeight / 1080), width: layout.title.width - 180, color: normalized.style.accentColor })}
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
        <BoardText rect={layout.theme} fontSize={45 * (actualWidth / 1920)} color={normalized.style.themeColor} fontFamily={fontFamily} align="center" weight={600}>
          {fitText(normalized.theme, 30)}
        </BoardText>
        {handwritingLine({ x: layout.theme.x - 25, y: layout.theme.y + 67 * (actualHeight / 1080), width: layout.theme.width + 50, color: normalized.style.accentColor, strokeWidth: 6 })}
      </div>

      {normalized.sections.map((section, index) => {
        const key = `section${index}` as 'section0' | 'section1' | 'section2';
        const visibility = showAt(key);
        const rect = layout.sections[index];
        const iconImage = resolveIconImage(normalized, section.icon);
        return (
          <div
            key={index}
            style={{
              position: 'absolute',
              inset: 0,
              opacity: visibility.opacity,
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
            />
            {index < 2 && (
              <Arrow
                x={rect.x + rect.width - 22}
                y={rect.y + 120 * (actualHeight / 1080)}
                width={96 * (actualWidth / 1920)}
                color={normalized.style.bodyColor}
              />
            )}
          </div>
        );
      })}

      <div
        style={{
          position: 'absolute',
          left: layout.conclusion.x,
          top: layout.conclusion.y,
          width: layout.conclusion.width,
          height: layout.conclusion.height,
          opacity: conclusionVisibility.opacity,
          transform: `scale(${conclusionVisibility.scale})`,
          transformOrigin: 'center center',
          border: `${6 * (actualWidth / 1920)}px solid ${normalized.style.accentColor}`,
          borderRadius: 24 * (actualWidth / 1920),
          background: 'rgba(255,255,255,0.35)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 20,
          boxSizing: 'border-box',
        }}
      >
        <div
          style={{
            fontFamily,
            fontSize: 48 * (actualWidth / 1920),
            lineHeight: 1.25,
            color: normalized.style.bodyColor,
            textAlign: 'center',
            whiteSpace: 'pre-wrap',
          }}
        >
          {fitText(normalized.conclusion, 40)}
        </div>
        <div style={{ position: 'absolute', left: layout.conclusion.width * 0.25, bottom: 28, width: layout.conclusion.width * 0.48, height: 18, background: normalized.style.markerColor, opacity: 0.75, borderRadius: 20, zIndex: -1 }} />
      </div>

      {characterImage ? (
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
