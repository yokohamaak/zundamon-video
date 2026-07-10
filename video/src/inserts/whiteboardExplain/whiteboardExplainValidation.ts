import { WHITEBOARD_EXPLAIN_LIMITS } from './whiteboardExplainDefaults';
import type { NormalizedWhiteboardExplainInsertConfig } from './whiteboardExplainTypes';

export type WhiteboardExplainValidationWarning = {
  path: string;
  message: string;
};

const countChars = (value: string) => [...value].length;

/** 結論文中の `**強調したい部分**` をマーカー付き区間として抜き出す。 */
export type ConclusionSegment = { text: string; highlighted: boolean };

const CONCLUSION_MARKER = /\*\*(.+?)\*\*/g;

export const parseConclusionSegments = (value: string): ConclusionSegment[] => {
  const segments: ConclusionSegment[] = [];
  let lastIndex = 0;
  CONCLUSION_MARKER.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = CONCLUSION_MARKER.exec(value))) {
    if (match.index > lastIndex) segments.push({ text: value.slice(lastIndex, match.index), highlighted: false });
    segments.push({ text: match[1], highlighted: true });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < value.length) segments.push({ text: value.slice(lastIndex), highlighted: false });
  return segments;
};

const conclusionPlainLength = (value: string) =>
  countChars(parseConclusionSegments(value).map((segment) => segment.text).join(''));

/** 結論文をマーカー区間ごとに保ったまま maxChars（表示上の文字数）で切り詰める。 */
export const fitConclusionSegments = (value: string, maxChars: number): ConclusionSegment[] => {
  const segments = parseConclusionSegments(value);
  let remaining = maxChars;
  const out: ConclusionSegment[] = [];
  for (const segment of segments) {
    if (remaining <= 0) break;
    const chars = [...segment.text];
    if (chars.length <= remaining) {
      out.push(segment);
      remaining -= chars.length;
    } else {
      out.push({ text: `${chars.slice(0, Math.max(0, remaining - 1)).join('')}…`, highlighted: segment.highlighted });
      remaining = 0;
    }
  }
  return out;
};

export const validateWhiteboardExplainConfig = (
  config: NormalizedWhiteboardExplainInsertConfig,
): WhiteboardExplainValidationWarning[] => {
  const warnings: WhiteboardExplainValidationWarning[] = [];

  if (countChars(config.title) > WHITEBOARD_EXPLAIN_LIMITS.title) {
    warnings.push({ path: 'title', message: `タイトルは${WHITEBOARD_EXPLAIN_LIMITS.title}文字以内推奨です。` });
  }
  if (countChars(config.theme) > WHITEBOARD_EXPLAIN_LIMITS.theme) {
    warnings.push({ path: 'theme', message: `テーマは${WHITEBOARD_EXPLAIN_LIMITS.theme}文字以内推奨です。` });
  }
  if (conclusionPlainLength(config.conclusion) > WHITEBOARD_EXPLAIN_LIMITS.conclusion) {
    warnings.push({ path: 'conclusion', message: `結論は${WHITEBOARD_EXPLAIN_LIMITS.conclusion}文字以内推奨です（**で囲んだ強調記号は含めません）。` });
  }

  config.sections.forEach((section, sectionIndex) => {
    if (countChars(section.heading) > WHITEBOARD_EXPLAIN_LIMITS.heading) {
      warnings.push({
        path: `sections.${sectionIndex}.heading`,
        message: `項目見出しは${WHITEBOARD_EXPLAIN_LIMITS.heading}文字以内推奨です。`,
      });
    }
    section.bullets.forEach((bullet, bulletIndex) => {
      if (countChars(bullet) > WHITEBOARD_EXPLAIN_LIMITS.bullet) {
        warnings.push({
          path: `sections.${sectionIndex}.bullets.${bulletIndex}`,
          message: `箇条書きは${WHITEBOARD_EXPLAIN_LIMITS.bullet}文字以内推奨です。`,
        });
      }
    });
  });

  return warnings;
};

export const fitText = (value: string, maxChars: number): string => {
  if (countChars(value) <= maxChars) return value;
  return `${[...value].slice(0, Math.max(0, maxChars - 1)).join('')}…`;
};
