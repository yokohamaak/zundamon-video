# 掛け合いランキング動画パイプライン（APOD if動画からの作り替え）

> このファイルは新repo引き継ぎ用。新repoの `PLAN.md` 等にコピーして使う。
> （現repo digest-to-video には**コミットしない**＝APOD版の履歴を汚さない一時ファイル）

## Context（なぜ作り替えるか）

現行「NASA APOD 1枚 → if台本 → VOICEVOX → Remotion」は、**APOD1枚をズーム/パンするだけで構造的に単調**（focus注目アノテーションでもクロップでも同じ1枚由来で単調さが残る、と実機確認で判明）。原因は素材数でなく**番組の型**にある。

ユーザー判断で、宇宙ジャンル・APODソースの縛りを外し、**ゼロベースで番組フォーマットから作り替える**。残す土台＝「ずんだもん達の2キャラ掛け合い／自動生成・定期投稿／無料・ローカル完結」。

採用コンセプト＝**ずんだもん達の掛け合いランキング動画**（毎回テーマを変え、TOP N をカウントダウン形式で紹介）。狙い：
- **単調さを型で構造的に解く**＝順位ごとに被写体（画像）が必ず変わる＋カウントダウンの推進力。
- **ビジュアルはComfyUIローカル生成**＝著作権リスクゼロ・外部画像APIのライセンス処理/欠落対応が不要・テーマ完全自由・量産無限・完全ローカル（課金ゼロ）。
- 既存資産（VOICEVOX・Remotion・credits・topics・プレースホルダ機構）をほぼ無改修で流用。

**生成画像が内容的に嘘にならないよう、テーマ/プロンプトは概念・イメージ系に寄せる**（実在の特定物の精密再現を要求しない）。

### fork戦略（プロジェクト分離）
現行APOD版（repo digest-to-video / branch feat/voicevox-tts）は**そのまま保存**し、新案は**新しいGitHubリポジトリへ分離**して ranking 専用に最適化する（apod系は新repoでは温存せず削除＝下記Phase S）。共通資産（`video/`・`src/tts_voicevox.py`・`src/manual_cuts.py`・`video/scripts/prep.mjs`・フォント/立ち絵）は引き継ぐ。

## 全体フロー

```
ranking_script.generate_ranking_script()  ← Gemini（テーマ＋N順位の掛け合い台本＋各順位の英語image_prompt）
  → comfyui_client.generate_images()       ← ComfyUI HTTP API で rank_NN.png 生成／失敗はプレースホルダ
  → tts_voicevox.generate_audio()          ← 流用（無改修）
  → build_meta()（ランキング版）           ← topics に rank/section テロップ用フィールド付与
  → docs/ranking/{meta.json, digest.mp3, rank_NN.png}
  → prep.mjs（流用）→ Remotion render（DialogueVideo にランキング演出追加）
```

新repoでは `apod_*` 系（`main_apod.py`/`apod_client.py`/`apod_crop.py`/`nasa_images.py`/`config.apod.yaml`/`test_apod_*.py`/`docs/apod*`）を**削除して ranking 専用に最適化**する（APOD版は元repoに保存済み）。`src/apod_script.py`→`src/ranking_script.py`、`main_apod.py`→`main_ranking.py` は中身を作り替えるため元をコピーして改名・改修。

## データスキーマ（ranking_script の出力）

```jsonc
{
  "theme": "宇宙で最もワクワクする現象ランキング",  // 日本語テーマ（テロップ/meta.title）
  "count": 5,
  "items": [   // カウントダウン順（N位→1位）。image_prompt は英語16:9・概念/イメージ系
    {"rank": 5, "title": "赤色巨星", "image_prompt": "cinematic wide shot of a glowing red giant star, concept art, 16:9, ..."}
  ],
  "script": [  // 既存と同形の掛け合いターン列 ＋ rank フィールド追加
    {"speaker":"四国めたん","text":"...","emotion":"normal","phase":"intro","effect":"kenburns","rank":null},
    {"speaker":"ずんだもん","text":"第5位は？","emotion":"surprise","phase":"rank","effect":"flash","rank":5}
  ]
}
```

**topicとターンの紐付け＝`script[].rank` を一次情報**にし、`items[].turn_index` はGemini出力を信用せず**parse後に再計算**（apod_cropの教訓「構造のみ採用、proseは信頼しない」）。rankの連続塊→順位トピックへ時間割当する純関数を持つ。

## 実装（新規・改修・流用）

### 新規モジュール
- **`src/ranking_script.py`**（`src/apod_script.py` を作り替え元に）
  - `build_prompt(config)`：テーマ自動選定 or `config.ranking.theme`、TOP N、両キャラ役割、各発言に `rank` 付与指示、各itemに英語image_prompt(概念系)指示。
  - `parse_script_json(text)`：`apod_script.parse_script_json` 流用＋`_clean_items`（rank整数化・range外除去・image_prompt trim）。
  - `normalize_turns`：`rank` の int|null 正規化（1..N外→null）を追加。
  - `assign_ranks_to_turns(script)`：rank連続塊→区間（intro/rank/outro）化する**純関数**（テストの中核）。`apod_crop._segments_by_phase` と同型。
  - Gemini呼び出し/リトライは `apod_script._generate_with_retry` を流用（初期は複製で可・過剰抽象化回避）。
- **`src/comfyui_client.py`**（`src/nasa_images.py` のurllib/リトライ/Pillow保存パターン流用）
  - 純関数：`build_workflow(template,node_map,prompt,seed,w,h,ckpt)`（テンプレJSONをdeepcopyして値差し込み）、`find_image_in_history(history,prompt_id)`、`build_view_url(...)`。
  - I/O：`queue_prompt`（POST /prompt）、`poll_history`（GET /history/{id} をHTTPポーリング・ws不使用＝依存追加なし）、`_save_image`（`nasa_images._save_image`流用）、`generate_images(items,out_dir,config)`（item毎に生成→失敗はstatus=placeholderでスキップ）。
  - 接続先 env `COMFYUI_URL`（既定 http://localhost:8188・コンテナから host.docker.internal:8188）＝VOICEVOXと同じ解決順。
- **`main_ranking.py`**（`main_apod.py` を作り替え元に。APOD取得/crop/stock段を除去）
  - `build_rank_topics(items_timing,turns,image_status)`：rank区間→topic（画像 or プレースホルダ）。`build_topics`+`_cut_to_topic` の作り替え。
  - `build_meta`：`main_apod.build_meta` 流用（speakers性別割当・script合流はそのまま）。topics生成のみ差し替え、`meta.title=theme`、各topicに rank/rankTotal/section 付与。
  - `build_credits`：VOICEVOX規約＋「画像: ComfyUIローカル生成」。
  - CLI：`--script-only` / `--from-script` / `--no-comfyui`（全プレースホルダで通す）。

### 改修（描画側）
- **`video/src/types.ts`**：`Topic` に `rank?:number` / `rankTotal?:number` / `section?:"intro"|"rank"|"outro"` を追加。
- **`video/src/DialogueVideo.tsx`**：既存の中央ビジュアル三分岐（image / note(プレースホルダ) / タイトルカード）は非破壊のまま、**`activeTopic.rank` がある時だけ順位オーバーレイ層を1枚重ねる**。要素＝大番号テロップ「第N位」（切替時に既存`topicFade`でスケールイン）、1位は金色強調、`section==="intro"`はtheme大見出し（既存タイトルカード分岐流用）。順位切替の白転換は台本の`effect:"flash"`で出す（enum追加不要）。

### 流用（無改修）
- `src/tts_voicevox.py`（話者ID metan=2/zundamon=3）、`video/scripts/prep.mjs`（`rank_NN.png` はIMG_EXTSで自動コピー＋placeholder昇格機構がそのまま効く）、`Avatar.tsx`/`Root.tsx`/`fonts.ts`、`src/manual_cuts.py`（ComfyUI失敗時の「決め打ち名で置けば差し替わる」フォールバックに転用＝target=`rank_NN.png`）。

### 設定
- 新規 `config/config.ranking.yaml`（if_dialogue→ranking役割、`ranking:{theme?,count:5}`、`comfyui:{enable,url,workflow,nodes,checkpoint,width:1280,height:720,timeout,poll_interval}`）。
- 新規 `config/comfyui/txt2img.api.json`（ComfyUIの「Save(API Format)」で書き出したテンプレ。ノードIDはconfig.comfyui.nodesでマッピングし環境差を吸収）。

## 段階実装（MVP優先・ComfyUI無しで先に1本通す）

- **Phase S（セットアップ・Mac作業）**：GitHubで新repo作成 → 現コードをコピーして新dir用意 → apod系削除 → init/remote/push → 新dirでClaude起動。
- **Phase 0**：`ranking_script.py`＋`test_ranking_script.py`、`config.ranking.yaml`、`main_ranking.py --script-only` で台本JSON目視。
- **Phase 1（MVP）**：`build_rank_topics`/`build_meta`＋テスト → `tts_voicevox`で音声+meta（`--no-comfyui`）→ `types.ts`/`DialogueVideo`に順位オーバーレイ最小版 → **Mac `cd video && SRC_DIR=../docs/ranking npm run render` でプレースホルダ順位動画が1本完成**（MVP完了点）。
- **Phase 2**：`comfyui_client.py`＋`txt2img.api.json`＋テスト → `main_ranking`に生成フェーズ（失敗はプレースホルダへ）→ 生成画像で再render。**ComfyUI環境（GPUホスト=Mac側で起動・モデル選定）はここで必要**。
- **Phase 3（任意）**：カウントダウン演出強化（スケールイン/1位金色/section境界flash/BGM）、テーマ自動選定の重複回避（過去theme履歴）。

## 前提・未確定（Phase0-1はブロックしない）
- **ComfyUI環境**：Phase 2で必要。導入済みか/モデル(checkpoint)/接続方式が未確定。未導入でもPhase 0-1（プレースホルダ動画）は進む。
- **テーマ運用**：MVPは固定テーマ（config指定）で検証→後で自動選定。N既定=5（尺7分前後）。
- **描画/確認の運用**：反復は `npm run dev`(Remotion Studio/HMR)、最終確認だけ `render`。dev起動中はmeta/画像変更でStudio再起動。描画はMac固定（コンテナはnode_modules食い合いでNG）。音声/台本/ComfyUI呼び出しはコンテナ可。

## 検証
- **純関数の単体テスト**（重い依存はモック）：
  - `test_ranking_script.py`：build_prompt（テーマ/TOP N/両キャラ/rank付与/image_prompt指示）、parse（素/フェンス/異常系）、`_clean_items`、`normalize_turns`のrank正規化、`assign_ranks_to_turns`の区間化。
  - `test_comfyui_client.py`：`build_workflow`（差し込み位置・deepcopy非破壊）、`find_image_in_history`、`build_view_url`、`generate_images`（queue/poll/saveをモンキーパッチし成功/timeout/失敗の分岐でstatus検証＝ネットワーク/ComfyUI/Pillow不要）。
  - `test_ranking_meta.py`：`build_rank_topics`（[0,total]隙間なく被覆・rank/section付与）、`build_meta`（speakers/script合流）。
- **end-to-end**：Phase1で `--no-comfyui` → render でプレースホルダ動画。Phase2でComfyUI起動 → 生成画像で render。

## Critical Files（作り替え元）
- `src/apod_script.py`（→ `src/ranking_script.py`）
- `main_apod.py`（→ `main_ranking.py`：build_meta/build_topics/_cut_to_topic/speakers割当）
- `src/nasa_images.py`（→ `src/comfyui_client.py` のurllib/リトライ/_save_image流用元）
- `video/src/DialogueVideo.tsx`（順位オーバーレイ追加）
- `video/src/types.ts`（Topic に rank/rankTotal/section 追加）
