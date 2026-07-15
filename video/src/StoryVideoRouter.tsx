import React from "react";
import {StageVideoV2} from "./StageVideoV2";
import {StoryVideo} from "./StoryVideo";
import type {StoryVideoProps} from "./StoryVideo";
import type {SceneLibraryV2, StoryV2} from "./stage-v2";

export type StoryVideoRouterProps = Omit<StoryVideoProps, "story" | "scenes"> & {
  story: StoryVideoProps["story"] | StoryV2;
  scenes: StoryVideoProps["scenes"] | SceneLibraryV2;
};

function isStoryV2(story: StoryVideoRouterProps["story"]): story is StoryV2 {
  return (story as Partial<StoryV2>).schemaVersion === 2;
}

/** 新形式だけをv2 rendererへ渡し、編集中の旧台本は既存rendererをそのまま使う。 */
export const StoryVideoRouter: React.FC<StoryVideoRouterProps> = (props) => {
  if (isStoryV2(props.story)) {
    return <StageVideoV2 {...props} story={props.story} scenes={props.scenes as SceneLibraryV2} />;
  }
  return <StoryVideo {...props} story={props.story} scenes={props.scenes as StoryVideoProps["scenes"]} />;
};
