# video — 掛け合い動画生成（Remotion / ローカル描画）

`meta.json`（字幕タイミング）と `digest.mp3`（音声）から、2人掛け合いの動画(MP4)を生成する。
ニュース非依存：`meta.json` 形式さえ守れば別コンテンツにも流用可能。

## 構成

- `src/Root.tsx` — コンポジション登録。尺はアライメント末尾と音声実尺の長い方で自動算出（末尾ブツ切り防止）
  - `DialogueVideo`（横16:9・1920x1080・本編全尺）/ `DialogueVideoShort`（縦9:16・1080x1920・1ネタ切り抜き）の2つ
- `src/DialogueVideo.tsx` — 本体。話者で明暗切替・文単位字幕・音声。`portrait`/`clipStartSec` propsで縦＆章切り抜きに対応（横は従来どおり）
- `src/Avatar.tsx` — 仮SVGアバター（本番アートは後で画像差し替え）
- `scripts/prep.mjs` — 描画の唯一の準備ステップ。入力を `public/` にコピー＋topics確定（dev/renderが必ず通すので古いデータで焼く事故が起きない）。入力元は既定 `../docs/story`（`SRC_DIR` で変更可）

## 前提（初回のみ）

Node 22。headless Chrome用のシステムライブラリとCJKフォントが必要：

```sh
apt-get install -y \
  libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libxcb1 libx11-6 \
  fonts-noto-cjk
npm install
```

## 使い方

```sh
# プレビュー（見た目の調整。ブラウザでStudioが開く）
npm run dev

# フル描画（横16:9）→ out/video.mp4
npm run render

# ショート描画（縦9:16・1ネタ切り抜き）→ out/short.mp4
npm run render:short
# 切り抜く章を指定（既定=最初のtrivia章）
npx remotion render DialogueVideoShort out/short.mp4 --props='{"clipChapter":2}'

# 別コンテンツの入力元を指定（既定は ../docs/story）
SRC_DIR=/path/to/dir npm run render   # その配下の meta.json / digest.mp3 を使う
```

縦レイアウトの数値調整は `src/DialogueVideo.tsx` の `BOARD_PORTRAIT`（中央ビジュアル枠）と `layoutFor()` の portrait 側（立ち絵位置・字幕サイズ）。保存すれば dev のStudioに即反映。

## メモ

- 描画はローカル前提（privateリポのActions無料枠を割るため）。完成後にCI/移行を再検討。
- 将来このディレクトリごと別リポジトリへ切り出す想定。結合点は `prep.mjs` の入力パスだけ。
