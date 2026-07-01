import React, { useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Player, type PlayerRef } from "@remotion/player";
import { StoryVideo } from "./StoryVideo";
import type { StoryScript, SceneLibrary, ExpressionsMap, PosesMap, SeMap } from "./StoryVideo";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

type StoryPlayerApi = {
  mount: (element: HTMLElement) => Promise<void>;
  unmount: () => void;
  updateStory: (story: StoryScript) => void;
  reloadScenes: () => Promise<void>;
  reloadExpressions: () => Promise<void>;
  reloadPoses: () => Promise<void>;
  seekToFrame: (frame: number) => void;
  seekToTime: (sec: number) => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => boolean;
  isPlaying: () => boolean;
  reloadAudio: () => Promise<void>;
};

declare global {
  interface Window {
    storyPlayer?: StoryPlayerApi;
  }
}

type Props = {
  story: StoryScript;
  scenes: SceneLibrary;
  manifest?: Record<string, Record<string, string>>;
  audio?: string;
  expressions?: ExpressionsMap;
  poses?: PosesMap;
  seMap?: SeMap;
};

let root: Root | null = null;
let playerRef: PlayerRef | null = null;
let setPropsState: React.Dispatch<React.SetStateAction<Props>> | null = null;
let onPlayerReady: (() => void) | null = null;

// Remotion の staticFile は window.remotion_staticBase が設定されていると
// "staticBase/path" を返す。mount 前にこの値を /preview-assets に設定することで
// StoryVideo.tsx 内の全 staticFile() 呼び出しが /preview-assets/ を指すようになる。
// これにより video/public/ 配下のファイルを /preview-assets/ 経由で配信するだけで
// 背景・立ち絵・モブ・音声・noise.png の全アセットが解決される。
function patchStaticFilePrefix() {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).remotion_staticBase = "/preview-assets";
  } catch {
    // 失敗しても続行（studio など別環境で使う場合）
  }
}

const StoryPlayerComponent: React.FC<{ initialProps: Props }> = ({ initialProps }) => {
  const ref = useRef<PlayerRef>(null);
  const [props, setProps] = useState(initialProps);

  const duration = Math.max(
    1,
    Math.ceil(props.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0) * FPS) + FPS
  );

  useEffect(() => {
    playerRef = ref.current;
    setPropsState = setProps;
    onPlayerReady?.();
    onPlayerReady = null;
    // フレーム更新を window イベントで通知（エディタが再生に合わせて台本を選択する用）。
    const player = ref.current;
    const onFrame = (e: { detail: { frame: number } }) => {
      const playing = player ? player.isPlaying() : false;
      window.dispatchEvent(
        new CustomEvent("story-player-frame", {
          detail: { frame: e.detail.frame, playing },
        })
      );
    };
    player?.addEventListener("frameupdate", onFrame);
    return () => {
      player?.removeEventListener("frameupdate", onFrame);
      playerRef = null;
      setPropsState = null;
    };
  }, []);

  return (
    <Player
      ref={ref}
      component={StoryVideo}
      inputProps={props}
      durationInFrames={duration}
      compositionWidth={WIDTH}
      compositionHeight={HEIGHT}
      fps={FPS}
      controls
      showPlaybackRateControl={[0.5, 0.75, 1, 1.25, 1.5, 2]}
      initiallyMuted={false}
      // モバイルでは共有 audio tag を増やしすぎると、主音声が頭に戻る不安定さが出やすい。
      // このプレビューで実際に同時再生されるのは voice + BGM(最大2) + 数個のSE なので 8 で足りる。
      numberOfSharedAudioTags={8}
      acknowledgeRemotionLicense
      style={{ width: "100%", height: "100%", background: "#080a0f" }}
      renderLoading={() => (
        <div style={{ color: "#8693a5", fontFamily: "sans-serif", fontSize: 24 }}>
          読み込み中...
        </div>
      )}
      errorFallback={({ error }) => (
        <div style={{ padding: 24, color: "#ff9b9b", fontFamily: "sans-serif", fontSize: 22 }}>
          プレビューを表示できません: {error.message}
        </div>
      )}
    />
  );
};

async function loadInitialProps(): Promise<Props> {
  // story は /api/story（エディタ側）から取得する。
  // scenes / manifest / audio / expressions は /preview-assets/ から取得する。
  const [storyRes, scenesRes] = await Promise.all([
    fetch("/api/story", { cache: "no-store" }),
    fetch("/preview-assets/story-scenes.json", { cache: "no-store" }),
  ]);
  if (!storyRes.ok) throw new Error("story-01.json が取得できません");
  if (!scenesRes.ok) throw new Error("story-scenes.json が取得できません");

  const story: StoryScript = await storyRes.json();
  const scenes: SceneLibrary = await scenesRes.json();

  let manifest: Record<string, Record<string, string>> | undefined;
  try {
    const mRes = await fetch("/preview-assets/avatars/manifest.json", { cache: "no-store" });
    if (mRes.ok) manifest = await mRes.json();
  } catch {
    // manifest 未生成時は単一画像フォールバック
  }

  let expressions: ExpressionsMap | undefined;
  try {
    const eRes = await fetch("/preview-assets/expressions.json", { cache: "no-store" });
    if (eRes.ok) expressions = await eRes.json();
  } catch {
    // expressions.json 未配置時は旧来の emotion ベースにフォールバック
  }

  let poses: PosesMap | undefined;
  try {
    const pRes = await fetch("/preview-assets/poses.json", { cache: "no-store" });
    if (pRes.ok) poses = await pRes.json();
  } catch {
    // poses.json 未配置時はAvatar側の自動腕割当にフォールバック
  }

  let seMap: SeMap | undefined;
  try {
    const sRes = await fetch("/preview-assets/se-map.json", { cache: "no-store" });
    if (sRes.ok) seMap = await sRes.json();
  } catch {
    // se-map.json 未配置時は SE 再生なしにフォールバック
  }

  const audio = story.audio ?? undefined;
  return { story, scenes, manifest, audio, expressions, poses, seMap };
}

window.storyPlayer = {
  async mount(element) {
    if (root) root.unmount();
    root = null;
    playerRef = null;
    onPlayerReady = null;

    patchStaticFilePrefix();

    const initialProps = await loadInitialProps();

    root = createRoot(element);
    await new Promise<void>((resolve) => {
      onPlayerReady = resolve;
      root!.render(<StoryPlayerComponent initialProps={initialProps} />);
    });
  },

  unmount() {
    onPlayerReady = null;
    if (root) root.unmount();
    root = null;
    playerRef = null;
    setPropsState = null;
  },

  updateStory(story) {
    if (!setPropsState) return;
    setPropsState((prev) => {
      const duration = story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
      const prevDuration = prev.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
      // audio は story.audio を優先（変更されていれば更新）
      const audio = story.audio ?? prev.audio;
      return { ...prev, story, audio, _duration: Math.max(duration, prevDuration) } as Props;
    });
  },

  async reloadScenes() {
    // シーンタブで編集・保存された story-scenes.json を読み直してプレビューへ反映。
    if (!setPropsState) return;
    try {
      const res = await fetch("/preview-assets/story-scenes.json", { cache: "no-store" });
      if (!res.ok) return;
      const scenes = await res.json();
      setPropsState((prev) => ({ ...prev, scenes } as Props));
    } catch {
      // 失敗時は据え置き
    }
  },

  async reloadExpressions() {
    // 表情タブ(expression_editor)で編集・書き出しされた expressions.json を読み直す。
    if (!setPropsState) return;
    try {
      const res = await fetch("/preview-assets/expressions.json", { cache: "no-store" });
      if (!res.ok) return;
      const expressions = await res.json();
      setPropsState((prev) => ({ ...prev, expressions } as Props));
    } catch {
      // 失敗時は据え置き
    }
  },

  async reloadPoses() {
    if (!setPropsState) return;
    try {
      const res = await fetch("/preview-assets/poses.json", { cache: "no-store" });
      if (!res.ok) return;
      const poses = await res.json();
      setPropsState((prev) => ({ ...prev, poses } as Props));
    } catch {
      // 失敗時は据え置き
    }
  },

  seekToFrame(frame) {
    if (!playerRef) return;
    playerRef.seekTo(Math.max(0, frame));
  },

  seekToTime(sec) {
    if (!playerRef) return;
    playerRef.seekTo(Math.max(0, Math.ceil(sec * FPS)));
  },

  play() {
    playerRef?.play();
  },

  pause() {
    playerRef?.pause();
  },

  togglePlay() {
    if (!playerRef) return false;
    if (playerRef.isPlaying()) {
      playerRef.pause();
      return false;
    }
    playerRef.play();
    return true;
  },

  isPlaying() {
    return playerRef ? playerRef.isPlaying() : false;
  },

  async reloadAudio() {
    // 音タブで保存された BGM(scenes/story) と SE(se-map) をプレビューへ反映。
    if (!setPropsState) return;
    try {
      const [scenesRes, storyRes, seRes] = await Promise.all([
        fetch("/preview-assets/story-scenes.json", { cache: "no-store" }),
        fetch("/api/story", { cache: "no-store" }),
        fetch("/preview-assets/se-map.json", { cache: "no-store" }),
      ]);
      const scenes = scenesRes.ok ? await scenesRes.json() : undefined;
      const story = storyRes.ok ? await storyRes.json() : undefined;
      const seMap = seRes.ok ? await seRes.json() : undefined;
      setPropsState((prev) => ({
        ...prev,
        ...(scenes ? { scenes } : {}),
        ...(story ? { story, audio: story.audio ?? prev.audio } : {}),
        ...(seMap ? { seMap } : {}),
      } as Props));
    } catch {
      // 失敗時は据え置き
    }
  },
};

window.dispatchEvent(new Event("story-player-ready"));
