# エージェント引き継ぎメモ（Fable → Sonnet/Opus/Haiku）

CLAUDE.md（共通ルール）と skills（手順）に載せきれなかった、実セッションで学んだ具体ノウハウ。
ルールではなく「知っていると事故らない知識」を置く。

## モデル別の使い分けの目安

- **Opus**: 原因調査・設計判断・「案出して」への回答。StoryVideo.tsx の構造変更
  （登場退場ロジックの共通化のような横断変更）はOpusで方針を固めてから実装する
- **Sonnet**: 通常の実装・修正。`/safe-implement` → 実装 → `/fable-review` の流れを守れば十分戦える
- **Haiku**: 文言修正・設定値変更・ドキュメント更新まで。**story_editor.html（7000行超）と
  StoryVideo.tsx（4000行超）の複数箇所編集は任せない**。ミラー実装の対応漏れに気づけない

## ユーザーの仕事の進め方（重要）

- ユーザー自身が story_editor をポート **8771** で常時起動して作業していることが多い。
  だから検証サーバは別ポートで起動する（skills参照）。8771が使用中でもプロセスをkillしない
- 「よろしく」「そうして」= 直前に提示した方針で実装からコミット・pushまで進めてよい合図
- 「〜しといて」= 完了まで任された。完了報告は結論から1〜3行で十分
- バグ報告は現象ベースで来る（「セリフが化ける」「エラーで起動確認しろって出る」）。
  再現条件を聞き返す前に、まずコードから原因候補を特定して提示すると話が速い
- 修正のついでに見つけた別の問題は、勝手に直さず報告する。ユーザーは「それはロードマップに」
  「プロンプト側の問題では?」のように自分で切り分けたい人

## 検証レシピ（コピペ用）

### remotion still で映像確認
```bash
# 必ず video/ ディレクトリで実行（ルートで npx tsc すると偽tscパッケージが入る事故あり）
cd video && npx remotion still src/index.ts StoryVideo /tmp/scratch/out.png --frame=N
```
- frame = 秒 × 30（fps=30、`video/src/Root.tsx` の FPS）
- 毎回バンドルで20〜30秒かかる。確認フレームは1〜2枚に絞る
- 特定演出の確認で story-01.json に仮データが要るときは必ず退避→編集→レンダ→復元:
  ```bash
  cp video/public/story-01.json $SCRATCH/backup.json
  # (python3で仮フィールドを注入 → レンダ → 確認)
  cp $SCRATCH/backup.json video/public/story-01.json && git status --short
  ```

### story_editor.html のJS構文チェック
```bash
python3 -c "
import re
html = open('story_editor.html').read()
for s in re.findall(r'<script(?: [^>]*)?>(.*?)</script>', html, re.S):
    if len(s) > 1000: open('/tmp/_check.js','w').write(s)
" && node --check /tmp/_check.js
```

### エディタのブラウザ検証
1. `.claude/launch.json` の story-editor のポートを空きポート（例:8792）へ変更
2. preview_start → preview_eval で内部関数を直接呼んで検証するのが速い
   （例: `showImportReport(2, {...})` を直接叩いてDOM確認）
3. **終了後は必ずポートを8771へ戻す**（fable-reviewのチェック項目）

### Pythonの内部関数を単体で叩く
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
import story_editor
story_editor.STORY_JSON = '/tmp/scratch/test.json'  # 実ファイルへ書かせない
ok, msg, info = story_editor._import_script_text(raw)
"
```
`_import_script_text` は `_save_story` 経由で **STORY_JSON に書き込む**。
モンキーパッチせずに呼ぶと実データを壊す（一度やらかした）。

## 過去にやらかした事故（同じ轍を踏まない）

1. **git stash で比較検証** → ユーザーの未コミット台本編集ごと巻き込み、別の古い台本が
   レンダされて混乱した。復旧はできたが、以後は cp 退避方式に統一
2. **`_import_script_text` のテストで実 story-01.json を上書き** → STORY_JSON を
   モンキーパッチしてから呼ぶこと（上のレシピ）
3. **ルートで `npx tsc`** → npm の偽 `tsc` パッケージがインストールされる。`cd video` 必須
4. **シェルの grep が0件を返す** → ユーザー環境の grep はugrepラッパーで不安定。
   `/usr/bin/grep`、日本語検索でバイナリ扱いされたら `LC_ALL=C /usr/bin/grep -a`
5. **エラーメッセージの決め打ち** → 「VOICEVOX起動を確認」のような固定文言は、別原因
   （話者プロファイル不足）を隠して調査を遠回りさせた。失敗時は実際の例外文言を出す

## 設計上の暗黙知

- **セグメント**が描画の基本単位: `buildSegments()` が同一sceneの連続ターンをまとめる。
  登場退場・アンカー・カメラはすべてセグメント単位で解決され、シーンを跨いで持ち越さない
- 座標系: アンカーは画面比率0-1。キャラは「体の中央」基準、手動配置(manualPos)は「顔位置」基準で
  `charFaceOffset` が変換する。ズームの注視点も顔位置逆算（faceCyOf）
- `effectSettings` は「動画全体（story直下）→ ターン上書き（turn直下）」の2層マージ。
  既定値と同じ値は保存時に削除される（デフォルト値プルーニング）
- 話者の種類は3系統: 立ち絵キャラ(zundamon/metan・ハードコード) / モブ(mobs.json・1枚絵) /
  声のみ(troublemaker系・音声なし・棒読み)。UIの選択肢は `_current_speakers_and_icons()` が組む
- AI台本生成は「プロンプトをローカル生成 → ユーザーが外部AIに貼る → 出力JSONを取り込み」方式。
  外部API呼び出しは一切しない（課金禁止ルールのため）。プロンプト文面は story_editor.py の
  `_build_script_prompt` にあり、**機能を足したらここへの追記を忘れがち**
