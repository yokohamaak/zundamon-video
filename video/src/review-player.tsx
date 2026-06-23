import React, {useEffect, useRef, useState} from "react";
import {createRoot, type Root} from "react-dom/client";
import {Player, type PlayerRef, type CallbackListener} from "@remotion/player";
import {DialogueVideo} from "./DialogueVideo";
import type {Meta} from "./types";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

type EditorPlayerApi = {
  mount: (element: HTMLElement) => Promise<void>;
  unmount: () => void;
  seekToTurn: (turnIndex: number) => void;
  seekToFrame: (frame: number) => void;
  getCurrentFrame: () => number | null;
  updateTextEffects: (turnIndex: number, effects: Meta["script"][number]["textEffects"]) => void;
  syncTextEffects: (effects: Array<Meta["script"][number]["textEffects"]>) => void;
};

declare global {
  interface Window {
    remotionEditorPlayer?: EditorPlayerApi;
  }
}

let root: Root | null = null;
let playerRef: PlayerRef | null = null;
let loadedMeta: Meta | null = null;
let setMetaState: React.Dispatch<React.SetStateAction<Meta>> | null = null;

async function loadMeta(): Promise<Meta> {
  const response = await fetch("/preview-assets/meta.json", {cache: "no-store"});
  if (!response.ok) throw new Error("meta.json is not available");
  const meta = (await response.json()) as Meta;
  try {
    const manifest = await fetch("/preview-assets/avatars/manifest.json", {cache: "no-store"});
    if (manifest.ok) meta.avatarManifest = await manifest.json();
  } catch {
    // A single-image avatar remains available when the parts manifest is missing.
  }
  try {
    const depth = await fetch("/preview-assets/depth-manifest.json", {cache: "no-store"});
    if (depth.ok) meta.depthMaps = await depth.json();
  } catch {
    // Normal images remain available when depth maps are missing.
  }
  return meta;
}

const ReviewPlayer: React.FC<{meta: Meta}> = ({meta: initialMeta}) => {
  const ref = useRef<PlayerRef>(null);
  const lastTurnRef = useRef(-1);
  const [meta, setMeta] = useState(initialMeta);
  const [ready, setReady] = useState(false);
  const duration = Math.max(
    1,
    Math.ceil(meta.script.reduce((max, turn) => Math.max(max, turn.end ?? 0), 0) * FPS) + FPS,
  );

  useEffect(() => {
    playerRef = ref.current;
    setMetaState = setMeta;
    setReady(true);
    // 逆連携: 再生/シークの現在フレーム→セリフindexを逆算し editor 側へ通知（追従ハイライト用）。
    const player = ref.current;
    const onFrame: CallbackListener<"frameupdate"> = (e) => {
      // 再生中のみ追従。mount時のフレーム0や editor→Player のシークechoで選択行/タイムラインが
      // 先頭へ飛ぶのを防ぐ（保存→更新の再mountで先頭に戻る問題の原因）。
      if (!player || !player.isPlaying()) return;
      const script = loadedMeta?.script;
      if (!script || !script.length) return;
      const t = (e.detail.frame ?? 0) / FPS;
      let turnIndex = 0;
      for (let i = script.length - 1; i >= 0; i--) {
        if ((script[i].start ?? 0) <= t + 1e-6) { turnIndex = i; break; }
      }
      if (turnIndex !== lastTurnRef.current) {
        lastTurnRef.current = turnIndex;
        window.dispatchEvent(new CustomEvent("remotion-turn-change", {detail: {turnIndex}}));
      }
    };
    player?.addEventListener("frameupdate", onFrame);
    return () => {
      player?.removeEventListener("frameupdate", onFrame);
      playerRef = null;
      setMetaState = null;
    };
  }, []);

  return (
    <Player
      ref={ref}
      component={DialogueVideo}
      inputProps={{meta}}
      durationInFrames={duration}
      compositionWidth={WIDTH}
      compositionHeight={HEIGHT}
      fps={FPS}
      controls
      showPlaybackRateControl={[0.5, 0.75, 1, 1.25, 1.5, 2]}
      initiallyMuted={false}
      acknowledgeRemotionLicense
      style={{width: "100%", height: "100%", background: "#080a0f"}}
      renderLoading={() => (
        <div style={{color: "#8693a5", fontFamily: "sans-serif", fontSize: 24}}>読み込み中...</div>
      )}
      errorFallback={({error}) => (
        <div style={{padding: 24, color: "#ff9b9b", fontFamily: "sans-serif", fontSize: 22}}>
          プレビューを表示できません: {error.message}
        </div>
      )}
      className={ready ? "review-remotion-player ready" : "review-remotion-player"}
    />
  );
};

window.remotionEditorPlayer = {
  async mount(element) {
    if (root) root.unmount();
    root = null;
    playerRef = null;
    loadedMeta = await loadMeta();
    root = createRoot(element);
    root.render(<ReviewPlayer meta={loadedMeta} />);
  },
  unmount() {
    if (root) root.unmount();
    root = null;
    playerRef = null;
    loadedMeta = null;
    setMetaState = null;
  },
  seekToTurn(turnIndex) {
    const turn = loadedMeta?.script?.[turnIndex];
    if (!turn || !playerRef) return;
    // 開始時刻以上の最初のフレーム（＝そのセリフの先頭フレーム）へ。
    // round だと丸め方向次第で1フレーム手前に乗り、pickActive が前のセリフを表示してしまう。
    playerRef.seekTo(Math.max(0, Math.ceil((turn.start ?? 0) * FPS)));
  },
  seekToFrame(frame) {
    if (!playerRef) return;
    playerRef.seekTo(Math.max(0, frame));
  },
  getCurrentFrame() {
    return playerRef?.getCurrentFrame() ?? null;
  },
  updateTextEffects(turnIndex, effects) {
    if (!loadedMeta?.script?.[turnIndex]) return;
    loadedMeta = {...loadedMeta, script: loadedMeta.script.map((turn, index) =>
      index === turnIndex ? {...turn, textEffects: effects ?? []} : turn)};
    setMetaState?.(loadedMeta);
  },
  syncTextEffects(effects) {
    if (!loadedMeta) return;
    loadedMeta = {...loadedMeta, script: loadedMeta.script.map((turn, index) =>
      ({...turn, textEffects: effects[index] ?? []}))};
    setMetaState?.(loadedMeta);
  },
};

window.dispatchEvent(new Event("remotion-player-ready"));
