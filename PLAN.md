# IT技術史ストーリー動画パイプライン（APOD if動画からの作り替え）

> このファイルは新repo `zundamon-video` の設計・実装計画書。
> APOD版は別repo digest-to-video / branch feat/voicevox-tts（commit 7723f1b）に保存済み＝このrepoの作業は影響しない。
> 旧「ランキング動画」案からさらにピボット。経緯は下記Context参照。

## Context（なぜ作り替えるか）

現行「NASA APOD 1枚 → if台本 → VOICEVOX → Remotion」は、**APOD1枚をズーム/パンするだけで構造的に単調**（focus注目アノテーションでもクロップでも同じ1枚由来で単調さが残る、と実機確認で判明）。原因は素材数でなく**番組の型**にある。

一度「ずんだもん達の掛け合いランキング動画」案へピボットしたが、ランキングのビジュアルをComfyUI生成画像で賄う構成だった。**最終的にユーザー判断で題材を「IT技術史ストーリー」に変更**：「IT技術の誕生物語」「なぜ〇〇が歴史を塗り替えたのか」系。理由＝**フリー素材が豊富な分野**で、実在の人物・製品・歴史的瞬間が主役になるため、生成画像（ComfyUI）では絵が嘘になりやすい。題材変更に伴い**画像ソースもフリー素材中心**へ。

残す土台＝「ずんだもん達の2キャラ掛け合い／自動生成・定期投稿／無料・ローカル完結」。

採用コンセプト＝**ずんだもん達の掛け合いでIT技術史を語る動画**（1本=1テーマ深掘り）。狙い：
- **単調さを型で構造的に解く**＝カウントダウンの代わりに**章立て時系列**で推進力を作り、章ごとに被写体（画像）が必ず変わる。
- **ビジュアルはフリー素材**＝実在の人物/製品を正確に出せる。著作権リスクはライセンス選別で管理、課金ゼロ。
- 既存資産（VOICEVOX・Remotion・credits・プレースホルダ機構）をほぼ無改修で流用。

### 番組フォーマット
1本=1テーマ（例「なぜGitは世界を変えたのか」「Unixはどう生まれたか」）。章立て時系列：
`intro(hook) → background(なぜ必要だった) → turning_point(誕生/ブレイクスルー) → impact(歴史を塗り替えた点) → outro(まとめ)`
掛け合い＝ずんだもん（聞き手/初心者）× 四国めたん（解説役）。章ごとに画像が切り替わる。

### 画像ソース：ハイブリッド（用途で出し分け）
台本が各章に持つ `image_kind` でプロバイダを振り分ける。
- **`subject`（実在の人物/製品/歴史的瞬間）→ Wikimedia Commons**
  - 課金ゼロ・無認証。`extmetadata.LicenseShortName` で **PD/CC0/CC-BY のみ通す。CC-BY-SA/ND/不明は機械的に除外**。
  - 帰属：`Artist`/`LicenseShortName`/`LicenseUrl`/ファイル名から自動クレジット生成。
  - ⚠️ **教訓踏襲**（apod_crop「構造のみ採用、proseは信頼しない」）：ライセンス判定は構造フィールド値のみで行う。`Artist` はHTML混在で信頼性低いため、サニタイズ（タグ除去・空なら "Wikimedia Commons" にフォールバック）した上でクレジット列挙のみに使い、判定には使わない。
- **`ambient`（抽象/雰囲気/つなぎ：コード画面・サーバー室・ネットワーク等）→ Pexels / Pixabay**
  - 課金ゼロ（無料枠制・従量課金ではない）。APIキー無料登録（`.env` 管理）。
  - **帰属表示不要・商用可** → クレジット自動生成の手間なし。
- 取得失敗・該当なしは既存プレースホルダ機構へフォールバック（無改修流用）。

### fork戦略（プロジェクト分離）
現行APOD版（repo digest-to-video / branch feat/voicevox-tts）は**そのまま保存**し、新案は**新しいGitHubリポジトリへ分離**して story 専用に最適化する（apod系は新repoでは削除＝下記Phase S）。共通資産（`video/`・`src/tts_voicevox.py`・`src/manual_cuts.py`・`video/scripts/prep.mjs`・フォント/立ち絵）は引き継ぐ。

## 全体フロー

```
story_script.generate_story_script()   ← Gemini（テーマ＋章立て掛け合い台本＋各章の image_query/image_kind）
  → image_fetch.fetch_images()         ← image_kind で Wikimedia / Pexels / Pixabay に振り分け→ ch_NN.png ／失敗はプレースホルダ
  → tts_voicevox.generate_audio()      ← 流用（無改修）
  → build_meta()（story版）             ← topics に section/chapter テロップ＋出典クレジット用フィールド付与
  → docs/story/{meta.json, digest.mp3, ch_NN.png}
  → prep.mjs（流用）→ Remotion render（DialogueVideo に章テロップ＋出典表示追加）
```

新repoでは `apod_*` 系（`main_apod.py`/`apod_client.py`/`apod_crop.py`/`nasa_images.py`/`config.apod.yaml`/`test_apod_*.py`/`docs/apod*`）を**削除して story 専用に最適化**する（APOD版は元repoに保存済み）。`src/apod_script.py`→`src/story_script.py`、`main_apod.py`→`main_story.py` は中身を作り替えるため元をコピーして改名・改修。

## データスキーマ（story_script の出力）

```jsonc
{
  "theme": "なぜGitは世界を変えたのか",   // 日本語テーマ（テロップ/meta.title）
  "chapters": [   // 時系列順。image_query は英語の検索語、image_kind でプロバイダ振り分け
    {"section":"intro",         "title":"分散管理という発明", "image_query":"version control concept", "image_kind":"ambient"},
    {"section":"turning_point", "title":"Linusの2週間",     "image_query":"Linus Torvalds",          "image_kind":"subject"}
  ],
  "script": [  // 既存と同形の掛け合いターン列 ＋ chapter / section フィールド追加
    {"speaker":"四国めたん","text":"...","emotion":"normal","section":"intro","chapter":0,"effect":"kenburns"},
    {"speaker":"ずんだもん","text":"なんで分散が必要だったの？","emotion":"surprise","section":"background","chapter":1,"effect":"flash"}
  ]
}
```

**topicとターンの紐付け＝`script[].chapter`（章index）を一次情報**にし、`chapters[].turn_index` はGemini出力を信用せず**parse後に再計算**（apod_cropの教訓「構造のみ採用、proseは信頼しない」）。chapterの連続塊→章トピックへ時間割当する純関数を持つ。`image_kind` は `subject|ambient` 以外を `ambient` に正規化。

## 実装（新規・改修・流用）

### 新規モジュール
- **`src/story_script.py`**（`src/apod_script.py` を作り替え元に）
  - `build_prompt(config)`：テーマ自動選定 or `config.story.theme`、章立て構成（intro→background→turning_point→impact→outro）、両キャラ役割、各発言に `chapter` 付与指示、各chapterに英語 `image_query` ＋ `image_kind(subject/ambient)` 指示。
  - `parse_script_json(text)`：`apod_script.parse_script_json` 流用＋`_clean_chapters`（section正規化・image_query trim・image_kind正規化）。
  - `normalize_turns`：`chapter` の int 正規化（range外→近接clamp）、`section` の enum正規化を追加。
  - `assign_sections_to_turns(script)`：chapter連続塊→区間化する**純関数**（テストの中核）。`apod_crop._segments_by_phase` と同型。
  - Gemini呼び出し/リトライは `apod_script._generate_with_retry` を流用（初期は複製で可・過剰抽象化回避）。
- **`src/wikimedia_client.py`**（`src/nasa_images.py` のurllib/リトライ/Pillow保存パターン流用）
  - 純関数：`pick_license(extmetadata)`（PD/CC0/CC-BY→OK、それ以外→reject。**構造値のみで判定**）、`build_attribution(extmetadata,filename)`（Artistサニタイズ＋ライセンス名＋URL）、`build_search_url`/`build_imageinfo_url`。
  - I/O：`search`（Commons API `list=search`）、`imageinfo`（`prop=imageinfo&iiprop=url|extmetadata`）、`_save_image`（`nasa_images._save_image`流用）。
- **`src/pexels_client.py` / `src/pixabay_client.py`**（薄い検索＋DL。APIキーは `.env`）
  - 純関数：`build_search_url(query,key,...)`、`pick_first_landscape(results)`（16:9優先選択）。I/O：`search`＋`_save_image`流用。帰属生成は不要（任意でphotographer名を保持）。
- **`src/image_fetch.py`**（振り分け＝過剰抽象化回避の薄い統合層）
  - `fetch_images(chapters,out_dir,config)`：chapter毎に `image_kind` で provider選択（subject→wikimedia / ambient→pexels→pixabayフォールバック）→ `ch_NN.png` 保存＋ `image_status`/`attribution` を返す。失敗はstatus=placeholderでスキップ。
- **`main_story.py`**（`main_apod.py` を作り替え元に。APOD取得/crop/stock段を除去）
  - `build_chapter_topics(chapters_timing,turns,image_status,attributions)`：章区間→topic（画像 or プレースホルダ＋出典）。`build_topics`+`_cut_to_topic` の作り替え。
  - `build_meta`：`main_apod.build_meta` 流用（speakers性別割当・script合流はそのまま）。topics生成のみ差し替え、`meta.title=theme`、各topicに section/chapter/chapterTitle/chapterTotal/credit 付与。
  - `build_credits`：VOICEVOX規約＋**Wikimedia由来画像のCC帰属を自動列挙**（Pexels/Pixabayは帰属不要だが任意で出典記載）。
  - CLI：`--script-only` / `--from-script` / `--no-images`（全プレースホルダで通す）。

### 改修（描画側）
- **`video/src/types.ts`**：`Topic` に `section?:"intro"|"background"|"turning_point"|"impact"|"outro"` / `chapter?:number` / `chapterTitle?:string` / `chapterTotal?:number` / `credit?:string` を追加。
- **`video/src/DialogueVideo.tsx`**：既存の中央ビジュアル三分岐（image / note(プレースホルダ) / タイトルカード）は非破壊のまま、**`activeTopic.chapter` がある時だけ章オーバーレイ層を1枚重ねる**。要素＝章番号＋章タイトルのテロップ（切替時に既存`topicFade`でスケールイン）、`section==="intro"`はtheme大見出し（既存タイトルカード分岐流用）、**`credit` がある時は画面隅に出典クレジットを小表示**（Wikimedia帰属の動画内表示）。章切替の白転換は台本の`effect:"flash"`で出す（enum追加不要）。

### 流用（無改修）
- `src/tts_voicevox.py`（話者ID metan=2/zundamon=3）、`video/scripts/prep.mjs`（`ch_NN.png` はIMG_EXTSで自動コピー＋placeholder昇格機構がそのまま効く）、`Avatar.tsx`/`Root.tsx`/`fonts.ts`、`src/manual_cuts.py`（画像取得失敗時の「決め打ち名で置けば差し替わる」フォールバックに転用＝target=`ch_NN.png`）。

### 設定
- 新規 `config/config.story.yaml`（if_dialogue→story役割、`story:{theme?,chapters:5}`、`images:{wikimedia:{enable,licenses:[PD,CC0,CC-BY]},pexels:{enable,api_key_env:PEXELS_API_KEY},pixabay:{enable,api_key_env:PIXABAY_API_KEY},width:1280,height:720,timeout}`）。
- `.env`：`PEXELS_API_KEY` / `PIXABAY_API_KEY`（無料登録。`.gitignore` 済を確認）。

## 段階実装（MVP優先・画像APIなしで先に1本通す）

- **Phase S（セットアップ・Mac作業）**：GitHubで新repo作成 → 現コードをコピーして新dir用意 → apod系削除 → init/remote/push → 新dirでClaude起動。Pexels/Pixabay APIキー無料登録＆`.env`配置もここで。
- **Phase 0**：`story_script.py`＋`test_story_script.py`、`config.story.yaml`、`main_story.py --script-only` で台本JSON目視。
- **Phase 1（MVP）**：`build_chapter_topics`/`build_meta`＋テスト → `tts_voicevox`で音声+meta（`--no-images`）→ `types.ts`/`DialogueVideo`に章オーバーレイ最小版 → **Mac `cd video && SRC_DIR=../docs/story npm run render` でプレースホルダ章動画が1本完成**（MVP完了点）。
- **Phase 2**：`wikimedia_client.py`＋`pexels_client.py`＋`pixabay_client.py`＋`image_fetch.py`＋各テスト → `main_story`に取得フェーズ（失敗はプレースホルダへ）→ 実画像＋出典で再render。**APIキー（Pexels/Pixabay）はここで必要**。Wikimediaは無認証。
- **Phase 3（任意）**：演出強化（章境界flash/章タイトルのスケールイン/BGM）、テーマ自動選定の重複回避（過去theme履歴）、subject画像の人物判定精度向上。

## 前提・未確定（Phase0-1はブロックしない）
- **画像API**：Phase 2で必要。Pexels/Pixabayは**無料枠のレート制限を実装前にWeb確認**（1日数本生成が制限内か）。Wikimediaは無認証だがUser-Agent必須・礼儀的レート配慮。未導入でもPhase 0-1（プレースホルダ動画）は進む。
- **ライセンス運用**：CC-BYまで許容（帰属自動生成）、CC-BY-SA/ND/不明は除外。Pexels/Pixabayは帰属不要。**YouTube収益化時も商用可ライセンスのみ通す方針を厳守**。
- **クレジット表示**：動画内（隅に小表示）＋概要欄テキスト（credits）の二段。詳細はPhase1で確定。
- **テーマ運用**：MVPは固定テーマ（config指定）で検証→後で自動選定。章数既定=5（尺7分前後）。
- **描画/確認の運用**：反復は `npm run dev`(Remotion Studio/HMR)、最終確認だけ `render`。dev起動中はmeta/画像変更でStudio再起動。描画はMac固定（コンテナはnode_modules食い合いでNG）。音声/台本/画像取得はコンテナ可。

## 検証
- **純関数の単体テスト**（重い依存はモック）：
  - `test_story_script.py`：build_prompt（テーマ/章立て/両キャラ/chapter付与/image_query・image_kind指示）、parse（素/フェンス/異常系）、`_clean_chapters`、`normalize_turns`のchapter/section正規化、`assign_sections_to_turns`の区間化。
  - `test_image_clients.py`：`pick_license`（PD/CC0/CC-BY→OK、SA/ND/不明→reject）、`build_attribution`（Artist HTMLサニタイズ・空フォールバック）、各`build_*_url`、`pick_first_landscape`、`fetch_images`（search/imageinfo/saveをモンキーパッチし成功/timeout/失敗の分岐でstatus・provider振り分け検証＝ネットワーク/API/Pillow不要）。
  - `test_story_meta.py`：`build_chapter_topics`（[0,total]隙間なく被覆・section/chapter/credit付与）、`build_meta`（speakers/script合流）。
- **end-to-end**：Phase1で `--no-images` → render でプレースホルダ動画。Phase2で各API設定 → 実画像＋出典で render。

## Critical Files（作り替え元）
- `src/apod_script.py`（→ `src/story_script.py`）
- `main_apod.py`（→ `main_story.py`：build_meta/build_topics/_cut_to_topic/speakers割当）
- `src/nasa_images.py`（→ `src/wikimedia_client.py` / `pexels_client.py` / `pixabay_client.py` のurllib/リトライ/_save_image流用元）
- `video/src/DialogueVideo.tsx`（章オーバーレイ＋出典表示追加）
- `video/src/types.ts`（Topic に section/chapter/chapterTitle/chapterTotal/credit 追加）
