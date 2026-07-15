import { Composition, staticFile } from "remotion";
import { StoryVideoRouter } from "./StoryVideoRouter";
import type {
  SceneLibrary,
  PosesMap,
  SeMap,
  MobsMap,
} from "./StoryVideo";
import type { StoryVideoRouterProps } from "./StoryVideoRouter";
import "./fonts"; // import時に日本語フォントのロードを開始（豆腐防止）

// ストーリー調エディタ（新ツール Phase1）用の固定入力を読み込む。
// story-01.json を使用（50ターン・第1話「AIが大丈夫って言ったのだ」）。
async function loadStory(): Promise<StoryVideoRouterProps> {
  const story = await (await fetch(staticFile("story-01.json"))).json() as StoryVideoRouterProps["story"];
  const scenes = await (await fetch(staticFile("story-scenes.json"))).json() as SceneLibrary;
  let manifest: Record<string, Record<string, string>> | undefined;
  try {
    const mres = await fetch(staticFile("avatars/manifest.json"));
    if (mres.ok) manifest = await mres.json();
  } catch {
    // manifest 未生成でも単一画像フォールバックで描画継続
  }
  let expressions: import("./StoryVideo").ExpressionsMap | undefined;
  try {
    const eres = await fetch(staticFile("expressions.json"));
    if (eres.ok) expressions = await eres.json();
  } catch {
    // expressions.json 未配置時は旧来の emotion ベースにフォールバック
  }
  let poses: PosesMap | undefined;
  try {
    const pres = await fetch(staticFile("poses.json"));
    if (pres.ok) poses = await pres.json();
  } catch {
    // poses.json 未配置時はAvatar側の自動腕割当にフォールバック
  }
  let seMap: SeMap | undefined;
  try {
    const sres = await fetch(staticFile("se-map.json"));
    if (sres.ok) seMap = await sres.json();
  } catch {
    // se-map.json 未配置時は SE 再生なしにフォールバック
  }
  let mobs: MobsMap | undefined;
  try {
    const mobRes = await fetch(staticFile("mobs.json"));
    if (mobRes.ok) mobs = await mobRes.json();
  } catch {
    // mobs.json 未配置時は組み込みの既定モブ定義にフォールバック
  }
  return { story, scenes, manifest, audio: story.audio, expressions, poses, seMap, mobs };
}

export const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* ストーリー調 会話劇動画。入力: story-01.json / story-scenes.json。
          掛け合い雑学動画（DialogueVideo系）は凍結し legacy-dialogue/ へ移設済み。 */}
      <Composition
        id="StoryVideo"
        component={StoryVideoRouter}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          story: undefined as unknown as StoryVideoRouterProps["story"],
          scenes: undefined as unknown as StoryVideoRouterProps["scenes"],
        }}
        calculateMetadata={async ({props}) => {
          // CLI/テストがinput propsを渡した場合はそちらを優先する。
          // 指定が無い通常renderだけ story-01.json を読む。
          const resolvedProps = props?.story ? props : await loadStory();
          // 尺: 台本の最終 end（秒）× fps + 末尾マージン 30 フレーム。
          const end = resolvedProps.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
          // 最後のターンの pause は音声側にも末尾の無音として反映される（tts_voicevox.py）。
          // アイリスアウト等、末尾ターンの演出がその間を使えるよう尺にも加える。
          const lastTurn = resolvedProps.story.script[resolvedProps.story.script.length - 1];
          const tailPause = lastTurn?.pause ?? 0;
          return {
            durationInFrames: Math.max(1, Math.ceil((end + tailPause) * FPS) + 30),
            props: resolvedProps,
          };
        }}
      />
    </>
  );
};
