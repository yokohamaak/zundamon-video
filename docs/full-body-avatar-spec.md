# 全身アバター対応 実装仕様（Sonnet実装用）

## 目的
ストーリー動画で、**office以外のシーン（server_room / rooftop / home）のキャラを全身立ち絵にする**。
ただし口パク（リップシンク）と表情（normal/happy/surprise/trouble/panic）は維持する。
→ 1枚絵の `full_body.png` ではなく、**全身の画角で書き出したパーツ立ち絵**を作り、既存 `Avatar.tsx` の仕組みで動かす。

office は従来どおりバスト立ち絵のまま（机=front で脚は隠れる）。**office以外だけ全身に切り替える。**

## 最重要の制約（これを外すと破綻する）
1. **全パーツは共通キャンバスで書き出す。** 既存バスト素材は全部 820×780 の同一サイズで、重ねるとピクセル整列している。全身パーツも同様に、**1キャラ分の全パーツ（base/eye_*/mouth_*/arm_*/fx_*）を完全に同一サイズのキャンバス**で書き出すこと。パーツごとに `getbbox()` で個別トリミングすると整列が壊れる → **全パーツ共通の単一bbox（全レイヤーの和集合）を1回だけ算出し、全パーツに同じcropを適用**する。
2. **`Avatar.tsx` は `DialogueVideo.tsx`（既存の本編ツール）でも使われている。** 変更は後方互換にすること。新しい寸法指定は**任意プロパティにして、未指定時は現在の 445×445 のまま**にする。既存挙動を一切変えない。
3. ユーザーの未コミットファイルを変更・削除・コミットしない。git commit はしない。

## 素材の書き出し（psd-tools・既に導入済み）
PSD: ずんだもん=`docs/reference/ずんだもん立ち絵素材V3.2/ずんだもん立ち絵素材V3.2_基本版.psd`、めたん=`docs/reference/四国めたん立ち絵素材2-2.1/四国めたん立ち絵素材2.1.psd`。

全身の画角（=`full_body.png` と同じく脚まで入る範囲。共通bboxは全使用レイヤーの和集合で決める）で、各キャラ次のパーツを書き出す。表情は**眉＋目（＋頬/かげり）を1枚に合成して `eye_*` として書き出す**方式（既存バストと同じ思想）。口はリップシンク用に分離維持。

必要パーツ（stem名＝manifestキー）:
- `base` … 体・腕・髪・服・顔の土台。**目と口は含めない**（差し替えるため）。腕は通常ポーズを base に含めてよい。
- `mouth_close` / `mouth_half` / `mouth_open` … 口の開き3段（PSDの口：閉じ/半開き/開き に対応）。
- `eye_open` … 基本眉＋基本目。
- `eye_close` … 基本眉＋閉じ目（まばたき用）。
- `eye_smile` … 笑い眉＋笑い目（happy）。
- `eye_surprise` … 驚き/上がり眉＋見開き目（surprise）。
- `eye_trouble` … 困り眉＋困り目（＋かげり等）（trouble/panic）。←既存バストには無い。全身では追加する。
- `fx_surprise` … 汗/びっくり記号（surprise一瞬）。
- `fx_sweat` … 焦りの汗（panic持続）。無ければ `fx_surprise` 流用で可。
- `arm_raise`（ずんだもんのみ・任意）… 驚き時に手を上げる差分。base と同一キャンバス。metanは腕をbaseに含め省略可。

書き出し先: `video/assets/avatars/<char>/full/` と `video/public/avatars/<char>/full/`（publicはgitignoreだが描画に必要なので両方に置く）。
manifest: `video/public/avatars/manifest.json` に**全身用エントリを追加**する。既存のバスト用（`zundamon`/`metan`）は壊さず、全身用を別キー（例 `zundamon_full` / `metan_full`、ファイルは `<char>/full/` 配下）で追加。`Avatar` の `dir` には `"<char>/full"` を渡せるようにする。

## Avatar.tsx の変更（後方互換厳守）
- 現在ラップは固定 `width:445, height:445`。**任意プロパティ `boxWidth?` `boxHeight?` を追加**し、未指定なら 445×445（現状維持）。全身では全身キャンバスのアスペクト比に合わせた寸法を渡す（例：全身canvasが W×H なら boxHeight = boxWidth × H/W）。`objectFit:"contain"`・`transformOrigin:"bottom center"` は維持（足元基準で整列）。
- **`emotion === "trouble"` の分岐を追加**：`eye_trouble` があれば困り目にする（現状 trouble は通常目になっている＝バグ的挙動。横展開で直す）。panic は既存どおり eye_trouble→無ければ eye_surprise。
- それ以外のロジック（口パク閾値・まばたき・オーバーアクション・panic汗の SWEAT_EXTRA 等）は流用。SWEAT_EXTRA はキャンバス比%なので全身でもそのまま効くはず（位置がずれたら全身用に係数を見直し、報告に明記）。

## StoryVideo.tsx の変更
- シーン定義に任意フィールド **`figure?: "bust" | "full"`** を追加（型 `SceneDef`）。
- `story-scenes.json`：office は `"figure": "bust"`、server_room / rooftop / home は `"figure": "full"`。未指定時の既定は `"bust"`（office互換）。
- `renderAvatar` で、シーンの figure が `"full"` のとき：
  - `dir` に `"<char>/full"`、`manifest` に全身用エントリを渡す。
  - Avatar に全身用の `boxWidth/boxHeight` を渡す。
  - 全身は背景に対して大きいので、`avScale`（scene.scale）と anchor.y（足元位置）で収まるよう既定値を調整。最終の位置/サイズ合わせは scene_editor で人手調整する前提なので、**破綻しない初期値**にしておけばよい。
- バスト（office）の経路は一切変えない。

## 受け入れ確認（Sonnetがやること）
- still レンダ（この環境はMac向けnode_modules同期のため、描画時のみ linux-arm64 バインディングを `--no-save` で追加して確認。package.json は変更しない）で、server_room と rooftop に**全身キャラが立ち、口パク用パーツが整列している**こと（base/eye/mouth がズレず重なる）を静止画で確認。
- office（バスト）が従来どおりであること、DialogueVideo に影響が無いこと（Avatar の既定寸法が 445×445 のまま）を確認。
- 確認用の still 画像は残してよい（パスを報告）。一時スクリプトは残さない。

## 報告（事実のみ）
- 書き出したパーツ一覧（キャラ別・stem別）と共通キャンバスサイズ、選んだPSDレイヤー名（特に eye_trouble に使った眉/目）。
- 変更ファイル（Avatar.tsx / StoryVideo.tsx / manifest.json / story-scenes.json / 素材）。
- still 確認の結果（整列OKか、office・DialogueVideoへの非影響）。
- パーツ整列やSWEAT位置で気づいた問題と対処。
- 実行していない確認を「OK」と書かない。
