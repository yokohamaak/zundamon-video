# 立ち絵アセット

## フォールバック用 単一画像（旧方式）
- `male_open.png` / `male_close.png` / `female_open.png` / `female_close.png`
- パーツ分け立ち絵が未配置の話者はこれを使う（口開け/閉じの2枚だけ、簡易リップシンク）。

## パーツ分け立ち絵（推奨・本方式）
`assets/avatars/<キャラ>/` に部位別画像を置く。`prep.mjs` が走査して
`public/avatars/manifest.json` を生成し、`Avatar.tsx` が存在する部位だけを重ねる。

- フォルダ名 = `meta.json` の `speakers[].avatar`（未指定なら名前から既定: ずんだもん→`zundamon` / 四国めたん→`metan`）
- **全部位を同一キャンバスサイズ・同一位置で書き出す**こと（重ねたときに位置が合う）。
- 背景は透過。ファイル名の stem（拡張子なし）で部位を識別する。拡張子は png/webp/svg 可。

### 部位一覧（stem名）
| stem | 必須 | 用途 |
|------|------|------|
| `base` | ✅ | 口・目を除いた土台（体＋頭＋髪） |
| `mouth_close` | ✅ | 口閉じ（無音/待機） |
| `mouth_half` | 推奨 | 口半開き（小音量） |
| `mouth_open` | 推奨 | 口開き（大音量） |
| `eye_open` | 推奨 | 目開き（通常） |
| `eye_close` | 推奨 | 目閉じ（まばたき） |
| `eye_surprise` | 任意 | 驚き目（surprise時） |
| `eye_smile` | 任意 | 笑顔目（happy時） |
| `fx_surprise` | 任意 | びっくりマーク等の効果オーバーレイ |

リップシンクは音量で `mouth_close → mouth_half → mouth_open` を切替。
まばたきは `eye_open`/`eye_close` を周期切替。`mouth_half`/`eye_close` が無ければ自動で近い部位に縮退。

### 動作確認用プレースホルダー
`npm run parts`（= `node scripts/make-avatar-parts.mjs`）で `zundamon`/`metan` の
簡易SVG部位を生成できる。**本物PNGを入れたら、同stemのSVGは必ず削除する**
（同stemが二重に存在すると意図しない方が使われる）。

### speakers の指定例（meta.json）
```json
"speakers": [
  { "name": "四国めたん", "gender": "female", "avatar": "metan" },
  { "name": "ずんだもん", "gender": "male", "avatar": "zundamon", "expressive": true }
]
```
`expressive: true` の話者は surprise/happy 時にオーバーアクション（ジャンプ・伸縮・効果）をする。
`avatar`/`expressive` 省略時も名前から既定解決される（ずんだもんは自動でexpressive）。
