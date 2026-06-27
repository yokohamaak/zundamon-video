import { Composition, staticFile } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { DialogueVideo } from "./DialogueVideo";
import { StoryVideo } from "./StoryVideo";
import type {
  StoryScript,
  SceneLibrary,
  StoryVideoProps,
} from "./StoryVideo";
import "./fonts"; // import時に日本語フォントのロードを開始（豆腐防止）
import type { Meta, Turn } from "./types";

// ストーリー調エディタ（新ツール Phase1）用の固定入力を読み込む。
// story-01.json を使用（50ターン・第1話「AIが大丈夫って言ったのだ」）。
async function loadStory(): Promise<StoryVideoProps> {
  const story: StoryScript = await (await fetch(staticFile("story-01.json"))).json();
  const scenes: SceneLibrary = await (await fetch(staticFile("story-scenes.json"))).json();
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
  return { story, scenes, manifest, audio: story.audio, expressions };
}

export const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

// ショート（縦9:16）。中身は同じ DialogueVideo を portrait レイアウトで描く。
const SHORT_WIDTH = 1080;
const SHORT_HEIGHT = 1920;

// 末尾の余韻（秒）
const TAIL_SEC = 0.8;

// dev <章> で渡す章番号（横・1章プレビュー用）。REMOTION_CHAPTER 環境変数→既定0。
// Studio起動時にバンドルへ埋め込まれる。章を変えるなら REMOTION_CHAPTER を変えて再起動するか、
// Studioのprops欄で clipChapter を直接編集（calculateMetadataが再クリップ）。
const PREVIEW_CHAPTER = Number(process.env.REMOTION_CHAPTER ?? "0") || 0;

// meta.json と立ち絵manifestを読み込む（横/縦の両Compositionで共用）。
async function loadMeta(): Promise<Meta> {
  const res = await fetch(staticFile("meta.json"));
  const meta: Meta = await res.json();
  // 立ち絵パーツ一覧を注入（prep.mjsが生成）。失敗時はパーツ無し扱いで単一画像にフォールバック。
  try {
    const mres = await fetch(staticFile("avatars/manifest.json"));
    if (mres.ok) meta.avatarManifest = await mres.json();
  } catch {
    // manifest未生成でも描画は継続
  }
  try {
    const dres = await fetch(staticFile("depth-manifest.json"));
    if (dres.ok) meta.depthMaps = await dres.json();
  } catch {
    // 深度マップ未生成でも描画は継続（通常のKen Burnsにフォールバック）
  }
  return meta;
}

// 末尾の余白フレーム数（最後のセリフ後にBGMをフェードしきる時間）。
function tailFrames(meta: Meta): number {
  const bgmFade = meta.audio?.bgm?.fade ?? 0;
  const tail = Math.max(TAIL_SEC, bgmFade + 0.4);
  return Math.ceil(tail * FPS);
}

// 章(chapter)単位の時間窓を算出。clipChapter未指定なら最初のtrivia章を採用。
// 1ネタ切り抜き用：返り値 [start,end] 秒。該当章が無ければ null（=全編）。
function computeClip(meta: Meta, clipChapter?: number): { start: number; end: number; ch: number } | null {
  const turns: Turn[] = meta.script ?? [];
  let ch = clipChapter;
  if (ch == null) {
    const triviaChs = turns
      .filter((t) => t.section === "trivia" && typeof t.chapter === "number")
      .map((t) => t.chapter as number);
    ch = triviaChs.length ? Math.min(...triviaChs) : undefined;
  }
  if (ch == null) return null;
  const inCh = turns.filter((t) => t.chapter === ch);
  if (!inCh.length) return null;
  const start = Math.min(...inCh.map((t) => t.start ?? 0));
  const end = Math.max(...inCh.map((t) => t.end ?? 0));
  return { start, end, ch };
}

// ショート冒頭に重ねるフック文。①その章の topic.hook（Gemini生成）②無ければタイトルから仮生成。
function hookForChapter(meta: Meta, ch: number | undefined): string {
  const tops = meta.topics ?? [];
  const inCh = tops.filter((t) => t.chapter === ch);
  const authored = inCh.map((t) => t.hook).find((h) => h && h.trim());
  if (authored) return authored.trim();
  const title = inCh.find((t) => t.title)?.title;
  return title ? `${title}って、知ってる？` : "";
}

// ショート尺の上限（秒）。長い章でもショート要件に収める安全策。
const SHORT_MAX_SEC = 60;

// ショート終盤CTA（既定）。--props で上書き可。空文字なら出さない。
const DEFAULT_SHORT_CTA = "続きは本編でも解説！\nチャンネル登録もよろしく";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* ストーリー調 会話劇動画（新ツール Phase1）。入力: story-01.json / story-scenes.json。 */}
      <Composition
        id="StoryVideo"
        component={StoryVideo}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          story: undefined as unknown as StoryScript,
          scenes: undefined as unknown as SceneLibrary,
        }}
        calculateMetadata={async () => {
          const props = await loadStory();
          // 尺: 台本の最終 end（秒）× fps + 末尾マージン 30 フレーム。
          const end = props.story.script.reduce((m, t) => Math.max(m, t.end ?? 0), 0);
          return {
            durationInFrames: Math.max(1, Math.ceil(end * FPS) + 30),
            props,
          };
        }}
      />

      <Composition
        id="DialogueVideo"
        component={DialogueVideo}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        // 尺はmeta.jsonの最終ターンendから算出（preview/render両対応）
        defaultProps={{ meta: undefined as unknown as Meta }}
        calculateMetadata={async () => {
          const meta = await loadMeta();
          const scriptEnd = meta.script.reduce((max, t) => Math.max(max, t.end ?? 0), 0);
          // 尺はアライメント末尾と音声実尺の長い方を採用。
          // whisperが末尾を取りこぼしてもブツ切りにならないようにする。
          let audioDur = 0;
          try {
            audioDur = await getAudioDurationInSeconds(staticFile("digest.mp3"));
          } catch {
            // 取得失敗時はアライメント末尾にフォールバック
          }
          const end = Math.max(scriptEnd, audioDur);
          return {
            durationInFrames: Math.max(1, Math.ceil(end * FPS) + tailFrames(meta)),
            props: { meta },
          };
        }}
      />

      {/* 横・1章だけのライブプレビュー（dev <章>用）。中身は本編と同じ DialogueVideo を
          clipChapter で1章にクリップ＝ショートのフック/CTAや縦レイアウトは付けない。
          章は REMOTION_CHAPTER（dev <章>が設定）or Studioのprops欄 clipChapter で指定。 */}
      <Composition
        id="DialogueChapter"
        component={DialogueVideo}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        defaultProps={{
          meta: undefined as unknown as Meta,
          clipStartSec: 0,
          clipEndSec: 0,
          clipChapter: PREVIEW_CHAPTER as number | undefined,
        }}
        calculateMetadata={async ({ props }) => {
          const meta = await loadMeta();
          const clip = computeClip(meta, props.clipChapter);
          const scriptEnd = meta.script.reduce((max, t) => Math.max(max, t.end ?? 0), 0);
          const start = clip?.start ?? 0;
          const end = clip?.end ?? scriptEnd;
          return {
            durationInFrames: Math.max(1, Math.ceil((end - start) * FPS) + tailFrames(meta)),
            props: { meta, clipStartSec: start, clipEndSec: end, clipChapter: props.clipChapter },
          };
        }}
      />

      {/* ショート（縦9:16・1ネタ切り抜き）。clipChapter で切り抜く章を指定（未指定=最初のtrivia章）。
          例: remotion render DialogueVideoShort out/short.mp4 --props='{"clipChapter":2}' */}
      <Composition
        id="DialogueVideoShort"
        component={DialogueVideo}
        fps={FPS}
        width={SHORT_WIDTH}
        height={SHORT_HEIGHT}
        defaultProps={{
          meta: undefined as unknown as Meta,
          portrait: true,
          clipStartSec: 0,
          clipEndSec: 0,
          clipChapter: undefined as number | undefined,
          hookText: "",
          ctaText: DEFAULT_SHORT_CTA,
        }}
        calculateMetadata={async ({ props }) => {
          const meta = await loadMeta();
          const clip = computeClip(meta, props.clipChapter);
          const scriptEnd = meta.script.reduce((max, t) => Math.max(max, t.end ?? 0), 0);
          const start = clip?.start ?? 0;
          // 60秒上限ガード：章が長くてもショート要件に収める（末尾を切る）。
          const end = Math.min(clip?.end ?? scriptEnd, start + SHORT_MAX_SEC);
          const hookText = hookForChapter(meta, clip?.ch);
          return {
            durationInFrames: Math.max(1, Math.ceil((end - start) * FPS) + tailFrames(meta)),
            props: {
              meta,
              portrait: true,
              clipStartSec: start,
              clipEndSec: end,
              clipChapter: props.clipChapter,
              hookText,
              ctaText: props.ctaText ?? DEFAULT_SHORT_CTA,
            },
          };
        }}
      />
    </>
  );
};
