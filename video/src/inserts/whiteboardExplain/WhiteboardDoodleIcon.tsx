import React from 'react';
import type { WhiteboardExplainIcon } from './whiteboardExplainTypes';

type Props = {
  icon?: WhiteboardExplainIcon;
  color?: string;
  accentColor?: string;
  size?: number;
};

// Path data taken from Tabler Icons (MIT license, https://github.com/tabler/tabler-icons),
// outline set, 24x24 viewBox. Kept as inline path strings instead of a package dependency
// since only ~20 fixed icons are needed.
const ICON_PATHS: Partial<Record<WhiteboardExplainIcon, string[]>> = {
  cause: ['M3 10a7 7 0 1 0 14 0a7 7 0 1 0 -14 0', 'M21 21l-6 -6'], // search
  problem: ['M3 5a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14', 'M9 9l6 6m0 -6l-6 6'], // square-x
  solution: ['M7 10h3v-3l-3.5 -3.5a6 6 0 0 1 8 8l6 6a2 2 0 0 1 -3 3l-6 -6a6 6 0 0 1 -8 -8l3.5 3.5'], // tool
  process: ['M3 19a2 2 0 1 0 4 0a2 2 0 0 0 -4 0', 'M19 7a2 2 0 1 0 0 -4a2 2 0 0 0 0 4', 'M11 19h5.5a3.5 3.5 0 0 0 0 -7h-8a3.5 3.5 0 0 1 0 -7h4.5'], // route
  priority: ['M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873l-6.158 -3.245'], // star
  deadline: ['M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M12 12h-3.5', 'M12 7v5'], // clock-hour-9
  evidence: ['M14 3v4a1 1 0 0 0 1 1h4', 'M12 21h-5a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v4.5', 'M14 17.5a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0', 'M18.5 19.5l2.5 2.5'], // file-search
  data: ['M3 13a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -6', 'M15 9a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -10', 'M9 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -14', 'M4 20h14'], // chart-bar
  share: ['M8 9h-1a2 2 0 0 0 -2 2v8a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-8a2 2 0 0 0 -2 -2h-1', 'M12 14v-11', 'M9 6l3 -3l3 3'], // share-2
  rule: ['M3.5 5.5l1.5 1.5l2.5 -2.5', 'M3.5 11.5l1.5 1.5l2.5 -2.5', 'M3.5 17.5l1.5 1.5l2.5 -2.5', 'M11 6l9 0', 'M11 12l9 0', 'M11 18l9 0'], // list-check
  risk: ['M15.04 19.745c-.942 .551 -1.964 .976 -3.04 1.255a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3a12 12 0 0 0 8.5 3a12 12 0 0 1 .195 6.015', 'M19 16v3', 'M19 22v.01'], // shield-exclamation
  improvement: ['M3 17l6 -6l4 4l8 -8', 'M14 7l7 0l0 7'], // trending-up
  confused: ['M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M9 10l.01 0', 'M15 10l.01 0', 'M9.5 16a10 10 0 0 1 6 -1.5'], // mood-confuzed
  scribble: ['M10 12.057a1.9 1.9 0 0 0 .614 .743c1.06 .713 2.472 .112 3.043 -.919c.839 -1.513 -.022 -3.368 -1.525 -4.08c-2 -.95 -4.371 .154 -5.24 2.086c-1.095 2.432 .29 5.248 2.71 6.246c2.931 1.208 6.283 -.418 7.438 -3.255c1.36 -3.343 -.557 -7.134 -3.896 -8.41c-3.855 -1.474 -8.2 .68 -9.636 4.422c-1.63 4.253 .823 9.024 5.082 10.576c4.778 1.74 10.118 -.941 11.833 -5.59a9.354 9.354 0 0 0 .577 -2.813'], // spiral
  checklist: ['M9 5h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2h-2', 'M9 5a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2', 'M9 14l2 2l4 -4'], // clipboard-check
  memo: ['M5 5a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2l0 -14', 'M9 7l6 0', 'M9 11l6 0', 'M9 15l4 0'], // notes
  conversation: ['M21 14l-3 -3h-7a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1h9a1 1 0 0 1 1 1v10', 'M14 15v2a1 1 0 0 1 -1 1h-7l-3 3v-10a1 1 0 0 1 1 -1h2'], // messages
  warning: ['M12 9v4', 'M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0', 'M12 16h.01'], // alert-triangle
  idea: ['M3 12h1m8 -9v1m8 8h1m-15.4 -6.4l.7 .7m12.1 -.7l-.7 .7', 'M9 16a5 5 0 1 1 6 0a3.5 3.5 0 0 0 -1 3a2 2 0 0 1 -4 0a3.5 3.5 0 0 0 -1 -3', 'M9.7 17l4.6 0'], // bulb
  table: ['M3 5a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14', 'M3 10h18', 'M10 3v18'], // table
};

export const WhiteboardDoodleIcon: React.FC<Props> = ({
  icon = 'none',
  color = '#111111',
  size = 120,
}) => {
  const paths = icon !== 'none' ? ICON_PATHS[icon] : undefined;
  if (!paths) return null;

  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      {paths.map((d, index) => (
        <path
          key={index}
          d={d}
          stroke={color}
          strokeWidth={1.75}
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  );
};
