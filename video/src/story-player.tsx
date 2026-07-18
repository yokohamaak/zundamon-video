import React, { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { Player, type PlayerRef } from "@remotion/player";
import { StoryVideoRouter } from "./StoryVideoRouter";
import type { ExpressionsMap, PosesMap, SeMap, MobsMap } from "./StoryVideo";
import type { StoryVideoRouterProps } from "./StoryVideoRouter";
import "./fonts"; // プレビューでも書き出しと同じ同梱フォントを使う（Yusei Magic等の豆腐防止）

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

function isLikelyMobilePreview() {
  if (typeof window === "undefined") return false;
  if (window.matchMedia?.("(max-width: 900px), (pointer: coarse)").matches) return true;
  return /iPhone|iPad|iPod|Android/i.test(window.navigator.userAgent);
}

type StoryPlayerApi = {
  mount: (element: HTMLElement) => Promise<void>;
  unmount: () => void;
  updateStory: (story: StoryVideoRouterProps["story"]) => void;
  reloadScenes: () => Promise<void>;
  reloadExpressions: () => Promise<void>;
  reloadPoses: () => Promise<void>;
  reloadMobs: () => Promise<void>;
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
    storyDisplayPreviewPlayer?: StoryPlayerApi;
  }
}

type Props = StoryVideoRouterProps;

function withAudioCacheBust(audio: string | undefined, token: number | string = Date.now()) {
  if (!audio) return undefined;
  return `${audio}${audio.includes("?") ? "&" : "?"}v=${token}`;
}

function bgmSignature(story: StoryVideoRouterProps["story"] | undefined) {
  const regions = story && Array.isArray(story.bgm) ? story.bgm : [];
  return regions
    .map((region) => [
      region.file ?? "",
      region.start ?? "",
      region.end ?? "",
      region.volume ?? "",
      region.fadeIn ?? "",
      region.fadeOut ?? "",
    ].join(":"))
    .join("|");
}

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

const StoryPlayerComponent: React.FC<{
  initialProps: Props;
  controls?: boolean;
  emitFrameEvents?: boolean;
  onReady?: (ref: PlayerRef | null, setProps: React.Dispatch<React.SetStateAction<Props>>) => void;
}> = ({ initialProps, controls = true, emitFrameEvents = true, onReady }) => {
  const ref = useRef<PlayerRef>(null);
  const [props, setProps] = useState(initialProps);

  const lastTurn = props.story.script[props.story.script.length - 1];
  const tailPause = lastTurn?.pause ?? 0;
  const duration = Math.max(
    1,
    Math.ceil((props.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0) + tailPause) * FPS) + FPS
  );
  const sharedAudioTags = isLikelyMobilePreview() ? 6 : 12;

  useEffect(() => {
    onReady?.(ref.current, setProps);
    // フレーム更新を window イベントで通知（エディタが再生に合わせて台本を選択する用）。
    const player = ref.current;
    const onFrame = (e: { detail: { frame: number } }) => {
      if (!emitFrameEvents) return;
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
    };
  }, [emitFrameEvents, onReady]);

  return (
    <Player
      ref={ref}
      component={StoryVideoRouter}
      inputProps={props}
      durationInFrames={duration}
      compositionWidth={WIDTH}
      compositionHeight={HEIGHT}
      fps={FPS}
      controls={controls}
      showPlaybackRateControl={[0.5, 0.75, 1, 1.25, 1.5, 2]}
      initiallyMuted={false}
      // モバイルでは共有 audio tag が多いと主音声が巻き戻ることがあるため、
      // 編集プレビューではスマホだけ少なめにする。
      numberOfSharedAudioTags={sharedAudioTags}
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

  const story = await storyRes.json() as StoryVideoRouterProps["story"];
  const scenes = await scenesRes.json() as StoryVideoRouterProps["scenes"];

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

  let mobs: MobsMap | undefined;
  try {
    const mobsRes = await fetch("/preview-assets/mobs.json", { cache: "no-store" });
    if (mobsRes.ok) mobs = await mobsRes.json();
  } catch {
    // mobs.json 未配置時は組み込みの既定モブ定義にフォールバック
  }

  const audio = withAudioCacheBust(story.audio);
  return { story, scenes, manifest, audio, expressions, poses, seMap, mobs };
}

function createStoryPlayerApi(options?: {
  controls?: boolean;
  emitFrameEvents?: boolean;
  initialPropsLoader?: () => Promise<Props>;
}) {
  let root: Root | null = null;
  let playerRef: PlayerRef | null = null;
  let setPropsState: React.Dispatch<React.SetStateAction<Props>> | null = null;
  let onPlayerReady: (() => void) | null = null;
  let currentBgmSignature = "";
  const controls = options?.controls ?? true;
  const emitFrameEvents = options?.emitFrameEvents ?? true;
  const loadProps = options?.initialPropsLoader ?? loadInitialProps;

  const setPropsSync = (updater: React.SetStateAction<Props>) => {
    const setProps = setPropsState;
    if (!setProps) return;
    flushSync(() => {
      setProps(updater);
    });
  };

  const refreshAudioAtCurrentFrame = () => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const frame = playerRef?.getCurrentFrame();
        if (typeof frame === "number") playerRef?.seekTo(frame);
      });
    });
  };

  return {
    async mount(element) {
      if (root) root.unmount();
      root = null;
      playerRef = null;
      setPropsState = null;
      onPlayerReady = null;

      patchStaticFilePrefix();

      const initialProps = await loadProps();
      currentBgmSignature = bgmSignature(initialProps.story);

      root = createRoot(element);
      await new Promise<void>((resolve) => {
        onPlayerReady = resolve;
        root!.render(
          <StoryPlayerComponent
            initialProps={initialProps}
            controls={controls}
            emitFrameEvents={emitFrameEvents}
            onReady={(ref, setProps) => {
              playerRef = ref;
              setPropsState = setProps;
              onPlayerReady?.();
              onPlayerReady = null;
            }}
          />
        );
      });
    },

    unmount() {
      onPlayerReady = null;
      if (root) root.unmount();
      root = null;
      playerRef = null;
      setPropsState = null;
      currentBgmSignature = "";
    },

    updateStory(story) {
      if (!setPropsState) return;
      const nextBgmSignature = bgmSignature(story);
      const shouldRefreshAudio = nextBgmSignature !== currentBgmSignature;
      currentBgmSignature = nextBgmSignature;
      setPropsSync((prev) => {
        const duration = story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
        const prevDuration = prev.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
        const prevBase = (prev.audio || "").split("?")[0];
        const nextBase = story.audio || "";
        const audio = nextBase && nextBase !== prevBase ? withAudioCacheBust(nextBase) : prev.audio;
        return { ...prev, story, audio, _duration: Math.max(duration, prevDuration) } as Props;
      });
      if (shouldRefreshAudio) {
        refreshAudioAtCurrentFrame();
      }
    },

    async reloadScenes() {
      if (!setPropsState) return;
      try {
        const res = await fetch("/preview-assets/story-scenes.json", { cache: "no-store" });
        if (!res.ok) return;
        const scenes = await res.json();
        setPropsState((prev) => ({ ...prev, scenes } as Props));
      } catch {
      }
    },

    async reloadExpressions() {
      if (!setPropsState) return;
      try {
        const res = await fetch("/preview-assets/expressions.json", { cache: "no-store" });
        if (!res.ok) return;
        const expressions = await res.json();
        setPropsState((prev) => ({ ...prev, expressions } as Props));
      } catch {
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
      }
    },

    async reloadMobs() {
      if (!setPropsState) return;
      try {
        const res = await fetch("/preview-assets/mobs.json", { cache: "no-store" });
        if (!res.ok) return;
        const mobs = await res.json();
        setPropsState((prev) => ({ ...prev, mobs } as Props));
      } catch {
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
        let shouldRefreshAudio = false;
        setPropsSync((prev) => {
          const nextStory = story ?? prev.story;
          const nextBgmSignature = bgmSignature(nextStory);
          shouldRefreshAudio = nextBgmSignature !== currentBgmSignature;
          currentBgmSignature = nextBgmSignature;
          return {
            ...prev,
            ...(scenes ? { scenes } : {}),
            ...(story ? { story, audio: withAudioCacheBust(story.audio) ?? prev.audio } : {}),
            ...(seMap ? { seMap } : {}),
          } as Props;
        });
        if (shouldRefreshAudio) {
          refreshAudioAtCurrentFrame();
        }
      } catch {
      }
    },
  } satisfies StoryPlayerApi;
}

window.storyPlayer = createStoryPlayerApi();
window.storyDisplayPreviewPlayer = createStoryPlayerApi({ controls: false, emitFrameEvents: false });

window.dispatchEvent(new Event("story-player-ready"));
