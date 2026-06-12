import { Composition, staticFile } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { DialogueVideo } from "./DialogueVideo";
import "./fonts"; // import時に日本語フォントのロードを開始（豆腐防止）
import type { Meta, Turn } from "./types";

export const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

// ショート（縦9:16）。中身は同じ DialogueVideo を portrait レイアウトで描く。
const SHORT_WIDTH = 1080;
const SHORT_HEIGHT = 1920;

// 末尾の余韻（秒）
const TAIL_SEC = 0.8;

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
function computeClip(meta: Meta, clipChapter?: number): { start: number; end: number } | null {
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
  return { start, end };
}

export const RemotionRoot: React.FC = () => {
  return (
    <>
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
          clipChapter: undefined as number | undefined,
        }}
        calculateMetadata={async ({ props }) => {
          const meta = await loadMeta();
          const clip = computeClip(meta, props.clipChapter);
          const scriptEnd = meta.script.reduce((max, t) => Math.max(max, t.end ?? 0), 0);
          const start = clip?.start ?? 0;
          const end = clip?.end ?? scriptEnd;
          return {
            durationInFrames: Math.max(1, Math.ceil((end - start) * FPS) + tailFrames(meta)),
            props: { meta, portrait: true, clipStartSec: start, clipChapter: props.clipChapter },
          };
        }}
      />
    </>
  );
};
