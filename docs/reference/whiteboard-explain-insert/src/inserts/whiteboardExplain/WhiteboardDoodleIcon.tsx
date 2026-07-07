import React from 'react';
import type { WhiteboardExplainIcon } from './whiteboardExplainTypes';

type Props = {
  icon?: WhiteboardExplainIcon;
  color?: string;
  accentColor?: string;
  size?: number;
};

export const WhiteboardDoodleIcon: React.FC<Props> = ({
  icon = 'none',
  color = '#111111',
  accentColor = '#f04d93',
  size = 120,
}) => {
  if (icon === 'none') return null;

  const common = {
    stroke: color,
    strokeWidth: 6,
    fill: 'none',
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  const svgProps = { width: size, height: size, viewBox: '0 0 120 120' };

  if (icon === 'confused') {
    return (
      <svg {...svgProps}>
        <circle cx="60" cy="62" r="22" {...common} />
        <path d="M35 45 C25 30, 35 22, 45 34" {...common} />
        <path d="M85 45 C95 30, 85 22, 75 34" {...common} />
        <path d="M51 59 L51 59" {...common} />
        <path d="M69 59 L69 59" {...common} />
        <path d="M50 76 Q60 68 70 76" {...common} />
        <path d="M25 25 L18 18" {...common} />
        <path d="M95 25 L103 18" {...common} />
        <path d="M35 93 C45 105,75 105,86 93" {...common} />
      </svg>
    );
  }

  if (icon === 'scribble') {
    return (
      <svg {...svgProps}>
        <path d="M25 68 C18 40,55 30,66 48 C80 70,35 88,30 62 C25 35,80 28,92 54 C105 82,58 94,42 75" {...common} />
        <circle cx="44" cy="98" r="8" {...common} />
        <circle cx="72" cy="98" r="8" {...common} />
        <path d="M38 28 L30 16" {...common} />
        <path d="M85 32 L97 20" {...common} />
      </svg>
    );
  }

  if (icon === 'checklist') {
    return (
      <svg {...svgProps}>
        <rect x="28" y="18" width="68" height="86" rx="5" {...common} />
        <path d="M45 42 L52 49 L65 34" stroke="#18a957" strokeWidth="6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M72 42 L86 42" {...common} />
        <path d="M45 66 L52 73 L65 58" stroke="#18a957" strokeWidth="6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M72 66 L86 66" {...common} />
        <path d="M45 88 L52 95 L65 80" stroke="#18a957" strokeWidth="6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M72 88 L86 88" {...common} />
      </svg>
    );
  }

  if (icon === 'memo') {
    return (
      <svg {...svgProps}>
        <path d="M30 20 H82 L96 34 V100 H30 Z" {...common} />
        <path d="M82 20 V36 H96" {...common} />
        <path d="M45 48 H80" {...common} />
        <path d="M45 64 H86" {...common} />
        <path d="M45 80 H73" {...common} />
        <path d="M28 105 C45 96,67 112,92 100" stroke={accentColor} strokeWidth="6" fill="none" strokeLinecap="round" />
      </svg>
    );
  }

  if (icon === 'conversation') {
    return (
      <svg {...svgProps}>
        <circle cx="42" cy="62" r="15" {...common} />
        <circle cx="82" cy="62" r="15" {...common} />
        <path d="M25 100 C28 80,56 80,60 100" {...common} />
        <path d="M65 100 C68 80,96 80,100 100" {...common} />
        <path d="M39 29 C50 16,75 16,86 30 C77 28,68 32,58 39" {...common} />
        <path d="M30 35 L20 28" stroke={accentColor} strokeWidth="6" fill="none" strokeLinecap="round" />
      </svg>
    );
  }

  if (icon === 'warning') {
    return (
      <svg {...svgProps}>
        <path d="M60 17 L105 98 H15 Z" {...common} />
        <path d="M60 44 V70" stroke={accentColor} strokeWidth="8" fill="none" strokeLinecap="round" />
        <circle cx="60" cy="84" r="4" fill={accentColor} />
      </svg>
    );
  }

  if (icon === 'idea') {
    return (
      <svg {...svgProps}>
        <path d="M60 18 V8" {...common} />
        <path d="M31 30 L22 21" {...common} />
        <path d="M89 30 L98 21" {...common} />
        <path d="M41 63 C26 43,40 24,60 24 C80 24,94 43,79 63 C72 72,72 76,72 80 H48 C48 76,48 72,41 63 Z" {...common} />
        <path d="M49 92 H71" {...common} />
        <path d="M53 104 H67" {...common} />
      </svg>
    );
  }

  if (icon === 'table') {
    return (
      <svg {...svgProps}>
        <rect x="18" y="28" width="84" height="66" rx="3" {...common} />
        <path d="M18 47 H102" stroke="#2e8edb" strokeWidth="7" fill="none" strokeLinecap="round" />
        <path d="M46 28 V94" {...common} />
        <path d="M74 28 V94" {...common} />
        <path d="M18 70 H102" {...common} />
      </svg>
    );
  }

  return null;
};
