import { Composition, staticFile } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { DialogueVideo } from "./DialogueVideo";
import "./fonts"; // import時に日本語フォントのロードを開始（豆腐防止）
import type { Meta } from "./types";

export const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

// 末尾の余韻（秒）
const TAIL_SEC = 0.8;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="DialogueVideo"
      component={DialogueVideo}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      // 尺はmeta.jsonの最終ターンendから算出（preview/render両対応）
      defaultProps={{ meta: undefined as unknown as Meta }}
      calculateMetadata={async () => {
        const res = await fetch(staticFile("meta.json"));
        const meta: Meta = await res.json();

        // 立ち絵パーツ一覧を注入（prep.mjsが生成）。失敗時はパーツ無し扱いで単一画像にフォールバック。
        try {
          const mres = await fetch(staticFile("avatars/manifest.json"));
          if (mres.ok) meta.avatarManifest = await mres.json();
        } catch {
          // manifest未生成でも描画は継続
        }
        const scriptEnd = meta.script.reduce(
          (max, t) => Math.max(max, t.end ?? 0),
          0
        );
        // 尺はアライメント末尾と音声実尺の長い方を採用。
        // whisperが末尾を取りこぼしてもブツ切りにならないようにする。
        let audioDur = 0;
        try {
          audioDur = await getAudioDurationInSeconds(staticFile("digest.mp3"));
        } catch {
          // 取得失敗時はアライメント末尾にフォールバック
        }
        const end = Math.max(scriptEnd, audioDur);
        // 末尾の余白：最後のセリフが終わってからBGMをフェードアウトしきる時間を確保する
        // （BGMフェードは末尾 fade 秒に当たるので、声の後に fade ぶん残す）。
        const bgmFade = meta.audio?.bgm?.fade ?? 0;
        const tail = Math.max(TAIL_SEC, bgmFade + 0.4);
        return {
          durationInFrames: Math.max(1, Math.ceil((end + tail) * FPS)),
          props: { meta },
        };
      }}
    />
  );
};
