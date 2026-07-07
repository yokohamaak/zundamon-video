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
  const accent = {
    stroke: accentColor,
    strokeWidth: 7,
    fill: 'none',
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  const blue = {
    stroke: '#2e8edb',
    strokeWidth: 7,
    fill: 'none',
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  const green = {
    stroke: '#18a957',
    strokeWidth: 7,
    fill: 'none',
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  const svgProps = { width: size, height: size, viewBox: '0 0 120 120' };

  // Legacy icon name, redrawn: bigger worried face with connected shoulders + question mark.
  if (icon === 'confused') {
    return (
      <svg {...svgProps}>
        <circle cx="50" cy="46" r="24" {...common} />
        <path d="M37 43 L37 43" {...common} />
        <path d="M63 43 L63 43" {...common} />
        <path d="M40 58 Q50 52 60 58" {...common} />
        <path d="M28 88 Q40 74 50 74 Q60 74 72 88" {...common} />
        <path d="M32 100 Q50 110 68 100" {...common} />
        <path d="M84 27 C88 17,106 20,105 34 C104 45,92 46,92 55" {...accent} />
        <circle cx="92" cy="72" r="4" fill={accentColor} />
      </svg>
    );
  }

  // Legacy icon name, redrawn: same worried face but with a swirling confusion mark.
  if (icon === 'scribble') {
    return (
      <svg {...svgProps}>
        <circle cx="50" cy="46" r="24" {...common} />
        <path d="M37 43 L37 43" {...common} />
        <path d="M63 43 L63 43" {...common} />
        <path d="M40 58 Q50 52 60 58" {...common} />
        <path d="M28 88 Q40 74 50 74 Q60 74 72 88" {...common} />
        <path d="M32 100 Q50 110 68 100" {...common} />
        <path d="M82 31 C87 21,103 23,101 35 C99 46,84 45,83 54 C82 62,90 67,96 62 C102 56,99 47,92 46" {...accent} />
        <path d="M89 78 C86 74,86 69,90 66 C95 62,101 63,104 68 C107 73,104 80,98 81 C94 82,90 81,88 78" {...accent} />
      </svg>
    );
  }

  if (icon === 'cause') {
    return (
      <svg {...svgProps}>
        <circle cx="50" cy="50" r="27" {...common} />
        <path d="M70 70 L96 96" {...common} />
        <path d="M41 50 H59" {...accent} />
        <path d="M50 41 V59" {...accent} />
        <path d="M26 94 C44 84,70 110,96 92" stroke="#2e8edb" strokeWidth="6" fill="none" strokeLinecap="round" />
      </svg>
    );
  }

  if (icon === 'problem') {
    return (
      <svg {...svgProps}>
        <rect x="25" y="23" width="70" height="74" rx="10" {...common} />
        <path d="M43 43 L77 77" {...accent} />
        <path d="M77 43 L43 77" {...accent} />
        <path d="M38 101 H84" {...common} />
      </svg>
    );
  }

  if (icon === 'solution') {
    return (
      <svg {...svgProps}>
        <path d="M82 20 L100 38 L80 58 L69 47 Z" {...common} />
        <path d="M72 55 L35 92 C28 99,18 89,25 82 L62 45" {...common} />
        <path d="M27 27 L45 45" {...accent} />
        <path d="M45 27 L27 45" {...accent} />
        <path d="M60 91 H100" {...green} />
      </svg>
    );
  }

  if (icon === 'process') {
    return (
      <svg {...svgProps}>
        <circle cx="28" cy="60" r="12" {...common} />
        <circle cx="60" cy="60" r="12" {...common} />
        <circle cx="92" cy="60" r="12" {...common} />
        <path d="M40 60 H48" {...common} />
        <path d="M72 60 H80" {...common} />
        <path d="M23 92 H97" {...accent} />
      </svg>
    );
  }

  if (icon === 'priority') {
    return (
      <svg {...svgProps}>
        <path d="M60 18 L70 44 L98 46 L76 63 L84 91 L60 75 L36 91 L44 63 L22 46 L50 44 Z" {...common} />
        <path d="M25 101 H95" {...accent} />
        <path d="M60 36 V62" {...blue} />
      </svg>
    );
  }

  if (icon === 'deadline') {
    return (
      <svg {...svgProps}>
        <rect x="22" y="28" width="76" height="70" rx="7" {...common} />
        <path d="M22 47 H98" {...blue} />
        <path d="M41 18 V35" {...common} />
        <path d="M79 18 V35" {...common} />
        <circle cx="62" cy="73" r="17" {...common} />
        <path d="M62 63 V74 L72 80" {...accent} />
      </svg>
    );
  }

  if (icon === 'evidence') {
    return (
      <svg {...svgProps}>
        <path d="M25 18 H75 L90 33 V96 H25 Z" {...common} />
        <path d="M75 18 V34 H90" {...common} />
        <path d="M39 46 H72" {...common} />
        <path d="M39 61 H65" {...common} />
        <circle cx="77" cy="76" r="14" {...blue} />
        <path d="M87 86 L100 99" {...blue} />
      </svg>
    );
  }

  if (icon === 'data') {
    return (
      <svg {...svgProps}>
        <path d="M24 96 H100" {...common} />
        <path d="M28 96 V25" {...common} />
        <rect x="40" y="65" width="12" height="31" rx="2" stroke="#2e8edb" strokeWidth="6" fill="none" />
        <rect x="60" y="49" width="12" height="47" rx="2" stroke="#2e8edb" strokeWidth="6" fill="none" />
        <rect x="80" y="34" width="12" height="62" rx="2" stroke="#2e8edb" strokeWidth="6" fill="none" />
        <path d="M38 35 C54 43,66 25,92 25" {...accent} />
      </svg>
    );
  }

  if (icon === 'share') {
    return (
      <svg {...svgProps}>
        <circle cx="35" cy="60" r="13" {...common} />
        <circle cx="84" cy="34" r="13" {...common} />
        <circle cx="84" cy="86" r="13" {...common} />
        <path d="M47 54 L72 40" {...blue} />
        <path d="M47 66 L72 80" {...blue} />
        <path d="M25 100 C33 87,53 88,61 100" {...common} />
      </svg>
    );
  }

  if (icon === 'rule') {
    return (
      <svg {...svgProps}>
        <rect x="25" y="18" width="70" height="86" rx="6" {...common} />
        <path d="M42 44 L50 52 L64 36" {...green} />
        <path d="M70 45 H84" {...common} />
        <path d="M42 70 L50 78 L64 62" {...green} />
        <path d="M70 71 H84" {...common} />
        <path d="M36 28 H84" {...accent} />
      </svg>
    );
  }

  if (icon === 'risk') {
    return (
      <svg {...svgProps}>
        <path d="M60 17 L105 98 H15 Z" {...common} />
        <path d="M60 44 V70" {...accent} />
        <circle cx="60" cy="84" r="4" fill={accentColor} />
        <path d="M20 18 L10 8" {...common} />
        <path d="M100 18 L110 8" {...common} />
      </svg>
    );
  }

  if (icon === 'improvement') {
    return (
      <svg {...svgProps}>
        <path d="M25 88 C42 78,51 67,65 58 C77 50,84 39,95 26" {...green} />
        <path d="M75 27 H96 V48" {...green} />
        <path d="M24 98 H100" {...common} />
        <path d="M28 98 V30" {...common} />
        <circle cx="46" cy="73" r="5" fill={accentColor} />
        <circle cx="65" cy="58" r="5" fill={accentColor} />
      </svg>
    );
  }

  if (icon === 'checklist') {
    return (
      <svg {...svgProps}>
        <rect x="28" y="18" width="68" height="86" rx="5" {...common} />
        <path d="M45 42 L52 49 L65 34" {...green} />
        <path d="M72 42 L86 42" {...common} />
        <path d="M45 66 L52 73 L65 58" {...green} />
        <path d="M72 66 L86 66" {...common} />
        <path d="M45 88 L52 95 L65 80" {...green} />
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
        <path d="M60 44 V70" {...accent} />
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
        <path d="M18 47 H102" {...blue} />
        <path d="M46 28 V94" {...common} />
        <path d="M74 28 V94" {...common} />
        <path d="M18 70 H102" {...common} />
      </svg>
    );
  }

  return null;
};
