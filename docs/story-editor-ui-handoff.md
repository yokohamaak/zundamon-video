# story_editor UI 引き継ぎメモ（2026-07-14）

次の担当（別エージェント想定）向け。**この文書だけで作業を始められる**ように書いてある。
背景の詳しい調査結果は [display-type-ui-analysis.md](display-type-ui-analysis.md) に全部ある。先にそれを読むこと。

---

## 1. ここまでに終わっていること

ブランチ `refactor/display-type-ui`（`main` から分岐。基準HEAD `f71598aa`）。

| コミット | 内容 |
|---|---|
| `9eebec47` | ユーザーの作業中変更（今回の作業とは無関係。触らない） |
| `18618fe6` | 分析資料 `docs/display-type-ui-analysis.md` |
| `f7c816a2` | 基本タブ最上部に「表示種別」プルダウンを追加。インサートタブを廃止し中身を基本タブへ統合 |
| `45fddf4d` | 表示種別で無視される項目を保存時に削除 |
| `6bc7d8a6` | 基本タブのUI整理（セクション分け・説明文の畳み・向き3行の折りたたみ・ラベル日本語化） |
| `e1281b7a` | 詳細ペインを縦積みレイアウトへ戻す（横並びは幅360pxで折り返して破綻したため） |

未コミット: `video/public/story-01.json`（**ユーザーの編集中データ。絶対にコミットしない**）、`legacy-dialogue/` 配下（凍結領域。触らない）。

**JSONスキーマと Remotion 側（`video/src/StoryVideo.tsx`）は一切変更していない。** 既存台本のレンダリング結果は変わらない。以降の作業でもこの前提を崩さないこと。

---

## 2. 残っている課題（次にやること）

**「設定が探しづらい」**。項目名も入力欄も全部同じ見た目で縦に並んでいるため、目的の設定を見つけるには上から全部読むしかない。右ペインは幅360px・項目は40個近くある。

ユーザーとの会話で出た候補（どれをやるかは未確定。ユーザーに確認すること）:

1. **検索ボックスで絞り込み** — 右ペイン上部に入力欄を置き、「テロップ」「口パク」等と打つと該当行だけに絞る。探すコストが直接下がる本命。
2. **セクションの折りたたみ** — `セリフ / 吹き出し・字幕 / キャラクター / 演出 / 音声 / SE` をクリックで開閉。閉じれば見出しだけになり全体を見渡せる。開閉状態は localStorage に記憶。
3. **設定済み項目に印** — その行で値が入っている項目にアクセントの点を付け、セクション見出しに「2件設定中」のような件数を出す。折りたたんでいても中身が分かる。
4. **セクション見出しの強調** — 今の小さいグレー見出しを、アイコン＋大きめの文字＋背景帯にし、スクロール中も上に貼り付く（sticky）ようにする。

**検討して見送った案**: 「基本＝よく使う項目 / 詳細＝全項目」の2タブ化。項目が整理されて数が減ったため、ユーザー判断でいったん不要となった。蒸し返さないこと。

---

## 3. 触る場所

すべて `story_editor.html`（素のJS・約12,000行・単一ファイル）に閉じている。

| 対象 | 場所 |
|---|---|
| 右ペインのCSS（`.field-row` / `.field-help` / `.section-divider` / `.fx-grid`） | 620行〜745行あたり |
| 3ペイン幅（`--left-w: 320px` / `--right-w: 360px`。1500px以上で440px） | 20行あたり＋その直後のメディアクエリ |
| 基本タブのHTML（`#tabBasic`） | 1471行〜1836行 |
| 配置タブのHTML（`#tabPlacement`） | 1838行〜 |
| `renderDetail()` — ターン→フォームへ値を流し込む | 7200行あたり |
| `renderDisplayTypeUi()` / `applyDisplayTypeVisibility()` / `syncFaceRows()` — 表示種別による出し分け | `renderDetail()` の直後 |
| `collectBasic()` / `collectInsert()` — フォーム→ターンへ書き戻す | 8700行以降 |
| `DISPLAY_TYPES` / `effectiveDisplayTypeAt()` / `pruneTurnForDisplayType()` | 10600行あたり |
| `saveStory()` | 9500行あたり |

行番号は目安。`/usr/bin/grep -n` で関数名を引くこと（シェルの `grep` が壊れていることがある）。

---

## 4. 壊しやすい落とし穴

- **表示種別ごとの項目の出し分けは `applyDisplayTypeVisibility()` が `style.display` で行っている。** 検索での絞り込みや折りたたみを別の仕組みで `display` を触ると競合する。「表示種別による非表示」と「検索・折りたたみによる非表示」は別レイヤーとして扱うこと（例: 前者は `hidden` 属性、後者はクラスで分けるなど）。
- **`#tabBasic .field-help:not(.warn) { display: none; }`** で常時表示の説明文を畳んでいる。行の `title` にホバー用テキストを入れているのは `setupEvents` 内の初期化ループ。説明文を検索対象にするならここを見る。
- **ZunMeet（videocall）は継承する。** 自分の `insert` を持たないターンも、同シーン内の直前から通話画面を引き継ぐ（`effectiveDisplayTypeAt()`）。ターンの表示種別を「`turn.insert` があるか」だけで判定しないこと。
- **スマホ幅（768px以下）は別レイアウト。** メディアクエリで縦積み・説明文ありに戻している。デスクトップ側だけ直したつもりが崩れることがある。
- **`story-01.json` はユーザーの作業中データ。** 検証で書き換えるならスクラッチパッドへ `cp` で退避してから。`git stash` は使わない（過去に事故）。エディタの「保存」ボタンを押すとファイルが上書きされるので、ブラウザ検証では押さないこと。

---

## 5. 検証

```bash
# JS構文チェック（story_editor.html の <script> 部を抜いて node --check）
python3 - <<'EOF'
import re
s = open('story_editor.html', encoding='utf-8').read()
open('/tmp/editor.js', 'w', encoding='utf-8').write(
    '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', s, re.S)))
EOF
node --check /tmp/editor.js

# Python回帰テスト（VOICEVOX不要・数秒）
python3 test_story_editor.py

# エディタ起動（既定ポート8771。変えたら必ず戻す）
python3 story_editor.py
```

ブラウザでの確認は、`selectTurn(i, false)` を直接叩いてターンを選び、`document.getElementById(id).style.display` を見るのが速い。ホワイトボードのターンは `storyData.script.findIndex(t => (t.insert||{}).kind === "whiteboard_explain")` で探せる。

---

## 6. 戻し方

```bash
# 今回のUI作業だけ全部戻す（ユーザーの変更 9eebec47 は残る）
git revert 18618fe6..e1281b7a
```

`git reset --hard` / `git clean -fd` は使わない。
