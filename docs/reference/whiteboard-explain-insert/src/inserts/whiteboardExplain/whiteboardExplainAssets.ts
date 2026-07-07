import { staticFile } from 'remotion';
import type { NormalizedWhiteboardExplainInsertConfig, WhiteboardExplainIcon } from './whiteboardExplainTypes';

export const toStaticFile = (path?: string): string | undefined => {
  if (!path) return undefined;
  if (/^https?:\/\//.test(path) || path.startsWith('data:')) return path;
  return staticFile(path.replace(/^\//, ''));
};

export const resolveCharacterImage = (config: NormalizedWhiteboardExplainInsertConfig): string | undefined => {
  if (config.character.image) return toStaticFile(config.character.image);

  const pose = config.character.pose || 'pointing';
  const expression = config.character.expression || 'smile';
  // Place your assets at public/characters/metan/{pose}_{expression}.png
  return toStaticFile(`/characters/metan/${pose}_${expression}.png`);
};

export const resolveIconImage = (
  config: NormalizedWhiteboardExplainInsertConfig,
  icon?: WhiteboardExplainIcon,
): string | undefined => {
  if (!icon || icon === 'none') return undefined;
  const override = config.assets.iconImages?.[icon];
  return toStaticFile(override);
};
