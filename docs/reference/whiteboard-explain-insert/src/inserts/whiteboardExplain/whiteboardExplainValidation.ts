import { WHITEBOARD_EXPLAIN_LIMITS } from './whiteboardExplainDefaults';
import type { NormalizedWhiteboardExplainInsertConfig } from './whiteboardExplainTypes';

export type WhiteboardExplainValidationWarning = {
  path: string;
  message: string;
};

const countChars = (value: string) => [...value].length;

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
  if (countChars(config.conclusion) > WHITEBOARD_EXPLAIN_LIMITS.conclusion) {
    warnings.push({ path: 'conclusion', message: `結論は${WHITEBOARD_EXPLAIN_LIMITS.conclusion}文字以内推奨です。` });
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
