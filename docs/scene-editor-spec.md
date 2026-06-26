# シーンエディタ 実装仕様（Sonnet 実装用）

> このドキュメントは実装担当（Sonnet）向け。設計は Opus 済み。
> **実装が一通り終わったら `/model opus` に戻す**こと（レビュー・次の方針決めは Opus で行う）。
> 関連: `docs/next-animation-editor-spec.md`（全体仕様）/ 記憶 `story-video-editor-plan`。

## 0. ゴール

`video/public/story-scenes.json`（シーンライブラリ）を**ブラウザでビジュアル編集**するローカルツール。
特に「**キャラの大きさ(scale)・立ち位置(anchor)の調整**」をドラッグ/スライダーで行えるようにする（今は手JSONで辛い）。

役割分担：
- **編集（配置・サイズ）＝このツール（軽量HTMLプレビュー）**
- **動きを含む最終確認＝既存の `npm run dev:story` / `render:story`**（このツールでは動き・カメラ・口パクは扱わない）

## 1. 形態（既存 review_server.py に倣う）

- **標準ライブラリのみ**（`http.server`）のローカルWebアプリ。追加パッケージ禁止。
- 静的フロント（HTML）＋ JSON API。相対URLで `/api/*`。
- ローカル完結・無料枠のみ・従量課金/情報漏洩リスクのある実装は禁止（CLAUDE.md 厳守）。
- 起動例：`python scene_editor.py`（既定ポート 8770、`--port` で変更可）。

## 2. 作るファイル

- `scene_editor.py`（リポジトリ直下。review_server.py と同階層）
- `scene_editor.html`（同階層。`_load_page` 相当でサーバが読み込んで配信。review_story_page.html を参考に）
- README 追記は不要（このspecが仕様）。

## 3. 編集対象データ（SceneDef）

`video/public/story-scenes.json` の構造（既存）。型は `video/src/StoryVideo.tsx` の `SceneDef`/`SceneLibrary` が正。

```jsonc
{
  "scenes": {
    "office": {
      "label": "オフィス",
      "bg": "background/office.png",        // 必須。public配下の相対パス
      "front": "background/office_front.png", // 任意(null可)。手前レイヤー(透過PNG)
      "shot": "duo",                         // "solo"|"duo"|"split"（編集UIでは選択肢として持つ程度）
      "camera": "static",                    // "static"|"slow-zoom"
      "scale": 1.4,                          // 立ち絵の拡大率（既定1.9）
      "transition": "fade-black",            // "fade-black"|"cut"（任意・既定fade-black）
      "cast": { "zundamon": "right", "metan": "left" }, // charId→アンカー名（任意）
      "anchors": {
        "center": { "x": 0.5, "y": 0.82 },   // 正規化座標(0..1)。yは立ち絵の「足元(下端)」
        "left":   { "x": 0.42, "y": 0.7 },
        "right":  { "x": 0.72, "y": 0.75 }
      }
    }
  }
}
```

- **anchor 座標系**：x,y とも 0..1（画面比）。x=左右、**y=立ち絵の下端（足元）位置**。
- **保存時は既存の他フィールドを壊さない**（読み込んだJSONを土台に、編集分だけ上書きして書き戻す）。

## 4. プレビューの配置計算（★renderと一致させる）

`StoryVideo.tsx` の描画と同じ計算でプレビューする（これを外すとWYSIWYGにならない）。
仮想ステージは **1920×1080**。プレビュー枠にはCSS transformで縮小表示する（枠幅に合わせ scale）。カメラ(ズーム)は**かけない**＝ステージ全体を等倍で見せる。

各キャラ立ち絵の配置（StoryVideo の renderAvatar と同一）：
- 立ち絵の箱サイズ＝ **445 × 445**（定数 `AVATAR_BOX`）。中に立ち絵画像を `object-fit: contain` で収める。
- 箱を `scale(sceneScale)` 拡大（`transform-origin: bottom center`）。`sceneScale` = scene.scale（既定1.9）。
- 箱の**下端中央**をアンカー位置 `(anchor.x*1920, anchor.y*1080)` に合わせる
  （CSS: `left: anchor.x*1920; top: anchor.y*1080; transform: translate(-50%,-100%)` のラッパに、内側で `scale(sceneScale)`）。
- **向き(flip)**：`anchor.x < 0.5` なら右向き＝**左右反転(scaleX(-1))**、それ以外は素のまま（立ち絵素材は画面左向きが素）。
- 描画順：**bg（cover）→ 各キャラ → front（cover, あれば）**。front はキャラの手前（机に隠れる確認用）。

立ち絵画像（プレビュー用・public配下）：
- パーツを重ねて顔つきにする：`avatars/<dir>/base.png` ＋（あれば）`eye_open.png` ＋ `mouth_close.png` を**同じ箱に重ねる**（base だけだと目鼻なしになる）。
- `<dir>` は cast のキャラID（例 zundamon/metan）。利用可能キャラは `avatars/manifest.json` のキー。

どのキャラをどのアンカーに置くか：
- `scene.cast`（charId→アンカー名）があればそれに従う。
- 無ければ簡易に「manifestの先頭2キャラを left/right」程度でよい（MVP）。

## 5. 編集操作（MVP）

プレビュー枠（16:9）上で：
1. **アンカーをドラッグ** → そのアンカーの x,y を更新（px→正規化に変換）。cast でそのアンカーに割当たったキャラ立ち絵が動く。
2. **scale スライダー**（例 0.8〜2.5、刻み0.05）→ scene.scale を更新、立ち絵サイズ即反映。
3. **front の重ね表示 ON/OFF** トグル（隠れ具合を確認）。
4. サイドパネルで編集：シーン選択ドロップダウン、bg/front 画像選択（`/api/list-assets` の一覧から）、transition 選択、cast 割当（charId→left/center/right）、label。
5. **保存ボタン** → `POST /api/scenes` で story-scenes.json 全体を書き戻す。
6. （任意）シーン追加・複製。

> MVP では「位置ドラッグ＋scale＋front重ね＋各フィールド編集＋保存」が最優先。下記は後回し（やらない）：アンカーの新規追加UI、複数キャラ3人以上、アニメーション/カメラのプレビュー。

## 6. API エンドポイント

- `GET /` → scene_editor.html
- `GET /api/scenes` → story-scenes.json をそのまま返す（JSON）
- `POST /api/scenes` → リクエストbody(JSON全体)を `video/public/story-scenes.json` へ保存（整形して indent=2, ensure_ascii=False相当）。保存前に最低限の検証（scenes が dict / 各 bg が文字列）。
- `GET /api/list-assets` → `{ "backgrounds": ["background/xxx.png", ...], "characters": ["zundamon","metan",...] }`
  （backgrounds は `video/public/background/` の画像一覧、characters は `avatars/manifest.json` のキー）
- `GET /img/<path>` → `video/public/<path>` の画像を配信（背景・front用。パスは許可ディレクトリ内に限定＝traversal防止）
- `GET /avatars/<dir>/<file>` → `video/public/avatars/<dir>/<file>` を配信（立ち絵パーツ）

パス基準：`scene_editor.py` の場所からの相対で `video/public/`。images は public 配下のみ許可（`..` 拒否）。

## 7. 制約・注意

- **標準ライブラリのみ**（http.server, json, os, urllib 等）。npm/pip 追加なし。
- `story-scenes.json` は `video/public/`（gitignore対象）。**保存先はここ**。
- 既存フィールドを保持（読込→部分更新→書き戻し）。コメント用 `_note` 等があっても壊さない。
- UIはフラット・ミニマル・モダン（CLAUDE.md UI方針）。ダーク基調で可。
- 画像が無い/未prepでも落ちない（プレースホルダ表示・警告）。`npm run prep:story` 済み前提だが、未コピーでも500で落とさない。

## 8. 検証（実装担当がこの環境でやること）

ブラウザUIの最終確認はユーザーがMacで行う。実装担当(Sonnet)はこの環境で**ロジック検証**まで：
1. `python scene_editor.py --port 8770` をバックグラウンド起動。
2. `curl -s localhost:8770/api/scenes` が現JSONを返す。
3. `curl -s localhost:8770/api/list-assets` が backgrounds/characters を返す。
4. `POST /api/scenes` に取得JSONをそのまま投げ→200、`video/public/story-scenes.json` が壊れず（`python -c "import json;json.load(...)"`）。
5. 値を1つ変えてPOST→ファイルに反映される。
6. `GET /img/background/office.png` と `GET /avatars/zundamon/base.png` が画像を返す（200・Content-Type画像）。
7. 既存への非干渉確認：`cd video && npx tsc --noEmit`（StoryVideo等が壊れていない）。
8. HTML を Read して、配置計算（445箱・scale・bottom-center・flip・描画順 bg→char→front）が §4 と一致しているか自己点検。

## 9. 完了後

- 上記検証が通ったらコミット（`feat: シーンライブラリ編集ツール（配置/サイズのビジュアル編集）` 等、内容を端的に）。
- **`/model opus` に戻す**。Opus 側でレビュー＆次工程（ユーザーが台本を書く→JSON化→1本通し作成、追加で必要なツール機能の洗い出し）へ進む。
