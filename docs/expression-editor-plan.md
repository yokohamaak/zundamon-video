# 表情エディタ＋表情データ駆動化 設計書

目的: 立ち絵の表情(目/口に加え **眉・顔色** も)を可変にし、scene_editorと同様の
ローカルGUIで各表情のパーツを組み替え・プレビュー・保存できるようにする。
外部サービス・従量課金なし。Opus設計→Sonnet実装。

## 全体像（3ステージ）

- Stage1: 描画のデータ駆動化（眉/顔色をパーツ化＋expressions.jsonで表情→パーツを定義）。
  挙動は現状維持（パリティ）を確認できる状態まで。
- Stage2: 候補書き出し＋表情エディタ(expression_editor.py/.html)。
- Stage3: エディタでtrouble/panic等に眉/顔色を割り当て（ユーザー作業）。

---

## スロット（重ねる部位）と重ね順

下から上の順:
`base → cheek(顔色) → arm → brow(眉) → eye → mouth → fx`

- base: 体・服・髪・アクセサリ等（眉と顔色を **含まない**）。zundaは腕も含まない、metanは腕含む（現状踏襲）。
- cheek: 顔色（ほっぺ/青ざめ/かげり等）。1枚。
- arm: zundaのみ（arm_normal/arm_raise）。metanはbaseに内包＝なし。
- brow: 眉。1枚。
- eye / mouth / fx: 現状どおり。

## パーツID（ASCIIスラッグ）と元PSDレイヤー対応

psd-export.mjs に `SLOTS` 定義を新設。id=ASCII、layer=PSDパス。

### zundamon
- cheek: hoppe=[!顔色,*ほっぺ], hoppe2=[!顔色,*ほっぺ2], hoppe_red=[!顔色,*ほっぺ赤め], pale=[!顔色,*青ざめ], shadow=[!顔色,かげり]
- brow: normal=[!眉,*普通眉], worry1=[!眉,*困り眉1], worry2=[!眉,*困り眉2], up=[!眉,*上がり眉], angry=[!眉,*怒り眉]
- eye: open=[目セット普通(白目+黒目)], close=[*UU], surprise=[*〇〇], smile=[*にっこり], happy=[*にっこり], grin=[*><]
  （openは2枚: [!目,*目セット,*普通白目]+[!目,*目セット,!黒目,*普通目]）
- mouth: close=[!口,*むー], half=[!口,*ほあ], open=[!口,*ほあー], smile_close=[!口,*むふ], smile_open=[!口,*ほあー], tri=[!口,*△]
- fx: sweat1=[記号など,汗1], sweat2=[記号など,汗2], sweat3=[記号など,汗3]

### metan
- cheek: normal=[!顔色,*普通], normal2=[!顔色,*普通2], blush=[!顔色,*赤面], pale=[!顔色,*青ざめ], shadow=[!顔色,かげり]
- brow: gokigen=[!眉,*ごきげん], komari=[!眉,*こまり], oko=[!眉,*おこ], yayaoko=[!眉,*ややおこ],
        futo_gokigen=[!眉,*太眉ごきげん], futo_komari=[!眉,*太眉こまり], futo_oko=[!眉,*太眉おこ]
- eye: open, close=[*目閉じ], surprise=[*○○], smile=[*目閉じ2], happy=[*目閉じ], grin=[*><]
- mouth: close=[!口,*ほほえみ], half=[!口,*お], open=[!口,*わあー], smile_close=[!口,*ほほえみ], smile_open=[!口,*わあー]
- fx: sweat=[記号など,汗]

※ baseはbodyから眉・顔色を除いたもの。

## expressions.json（描画が読む唯一の真実）

`video/public/expressions.json`（git追跡・public直置き＝scene等と同運用）。
```
{
  "zundamon": {
    "normal":   { "brow":"normal", "cheek":"hoppe",     "eye":"open",     "mouth_close":"close",       "mouth_half":"half", "mouth_open":"open",       "fx":null },
    "happy":    { "brow":"normal", "cheek":"hoppe_red",  "eye":"happy",    "mouth_close":"smile_close", "mouth_half":"smile_open","mouth_open":"smile_open","fx":null },
    "surprise": { "brow":"up",     "cheek":"hoppe",      "eye":"surprise", "mouth_close":"close",       "mouth_half":"half", "mouth_open":"open",       "fx":"sweat1" },
    "trouble":  { "brow":"worry1", "cheek":"hoppe",      "eye":"open",     "mouth_close":"close",       "mouth_half":"half", "mouth_open":"open",       "fx":null },
    "panic":    { "brow":"worry2", "cheek":"pale",       "eye":"surprise", "mouth_close":"close",       "mouth_half":"half", "mouth_open":"open",       "fx":"sweat1" }
  },
  "metan": { ... 同形 ... }
}
```
- Stage1では「現状の見た目を再現する値」で初期化（眉=normal/gokigen、顔色=現行、目/口=現行マッピング）。
  ※ happyだけは先日確定値（zunda:にっこり+むふ/ほあー、metan:目閉じ+ほほえみ/わあー）。
- Stage3でtrouble/panic等を改善（上表は改善後の例。Stage1初期値はあくまで現状再現を優先）。

## 書き出し（node: psd-export.mjs build/build-full）

- buildは `expressions.json` で実際に使われている id を slot ごとに集計し、
  使用パーツのみを `<slot>_<id>.png` として書き出す（例: `brow_worry1.png`, `cheek_pale.png`）。
  既存の `eye_open.png` 等の命名も `eye_<id>` に統一（open/close/...）。
- full(全身)も同様。union bbox算出は従来どおり全使用パーツで。
- manifest.json は従来どおり stem→ファイル名。
- 後方互換は不要（個人開発・一括移行）。ただし StoryVideo.tsx 側の旧stem参照
  （eye_open 等）が壊れないよう、命名は極力据え置き、新規は brow_/cheek_ のみ増える。

## Avatar.tsx 改修

- props に `expressions`(=expressions.jsonの該当キャラ分) と `charKey` を渡す。
  もしくは manifest と同様に親(StoryVideo)が解決して各stemを渡す。実装簡単な方を選択。
- emotion(normal/happy/surprise/sad/panic) ではなく **StoryExpression(normal/happy/surprise/trouble/panic)** を
  直接キーにして expressions.json を引く（EXPRESSION_TO_EMOTION は廃止 or 内部用に縮小）。
- リップシンク: amplitude→ close/half/open を選び、その表情の mouth_close/half/open id を使う。
- 重ね順は上記スロット順。fx は id があれば表示（panicの汗増しは現状の SWEAT_EXTRA を流用）。
- 立ち絵の「オーバーアクション」(happy hop/surprise跳ね)はemotion由来の演出として維持。
- フォールバック: id に対応するstemが manifest に無ければ近いものへ（base/eye_open等）。

## StoryVideo.tsx 改修

- expressions.json を public から読み込み props で渡す（story-player.tsx の loadInitialProps にも追加）。
- 既存の expression 指定(台本の expression フィールド)はそのまま使える。

## Stage2: 表情エディタ

### expression_editor.py（:8772 想定。scene_editor を雛形に）
- GET `/` HTML
- GET `/api/catalog` … 各キャラ・各スロットの候補 [{id, label(元レイヤー名), file}] を返す。
- GET `/api/expressions` / POST `/api/expressions` … expressions.json 読み書き。
- GET `/img/candidates/<char>/<slot>_<id>.png` … 候補PNG配信（public/avatars/candidates/）。
- POST `/api/export` … node で psd-export build/build-full + prep-story + build-story-player を実行（ストリーム進捗）。
  ※従量課金なし・ローカルのみ。

### 候補PNG書き出し（node: psd-export.mjs candidates <char>）
- SLOTS の全候補を、base合成なしの **単体透過PNG**（同一クロップ）で
  `assets/avatars/<char>/candidates/<slot>_<id>.png` に書き出し → prep で public へ。
- base.png（眉/顔色なしの土台）も candidates として用意（プレビュー土台）。

### expression_editor.html（3ペイン）
- 左: 表情リスト(normal/happy/surprise/trouble/panic) × キャラ(zunda/metan)タブ。
- 中央: 合成プレビュー。base + 選択中の cheek/arm/brow/eye/mouth/fx を
  `<img position:absolute>` で重ねて表示（候補PNGは同一クロップなので重なる）。
  口は close/half/open の3枚をトグルで確認できると良い（任意）。
- 右: スロットごとの候補ピッカー（cheek/brow/eye/mouth_close/mouth_half/mouth_open/fx）。
  クリックで割当→中央プレビュー即時更新→expressions.json(メモリ)更新。
- 保存ボタン→POST /api/expressions。書き出しボタン→POST /api/export（進捗表示）。
- フラットでミニマルなUI（既存ツールと統一）。

## 検証（ローカル・従量課金なし）
- Stage1: `node scripts/psd-export.mjs build/build-full` → prep → player-build →
  `npx remotion still` で normal/happy/surprise/trouble/panic の静止画を出し、
  現状(Stage1初期値)と一致することを目視。expression-catalog.mjs を更新して一覧確認。
- Stage2: エディタで割当→書き出し→stillで反映確認。

## Stage1 パリティ仕様（重要）

Avatarの **時間的ロジックは全て維持** し、「どのidを使うか」だけを expressions.json から引く。
維持する挙動: まばたき / リップシンク3段(close/half/open) / オーバーアクション(happy hop, surprise跳ね) /
fxの出方(surprise=反応中だけ一瞬, panic=継続+SWEAT_EXTRA増し) / 腕差替(surprise→arm_raise) /
pose_surprise差替 / happyの目は区間中保持。

eye選択ルール（パリティ）:
- eyeId = cfg.eye を基本に、`cfg.eye === "open"` の時だけ まばたきで "close" に差し替え。
  （normal/trouble は eye=open でまばたき継続。happy/surprise/panic は閉じ/見開きでまばたきしない＝現状どおり）

mouth選択: level(close/half/open)を amplitude で決め、`cfg["mouth_"+level]` のidを使う。!activeはclose(idle相当)。

fx選択: 表示タイミングは現状のまま expression で分岐。表示する画像stemは `cfg.fx` (例 sweat1/sweat)。

### Stage1 初期 expressions.json（現状再現値）
zundamon: 全表情 brow=normal, cheek=hoppe。
- normal:   eye=open,     mouth=close/half/open,             fx=null
- happy:    eye=happy,    mouth=smile_close/smile_open/smile_open, fx=null
- surprise: eye=surprise, mouth=close/half/open,             fx=sweat1
- trouble:  eye=open,     mouth=close/half/open,             fx=null
- panic:    eye=surprise, mouth=close/half/open,             fx=sweat1

metan: 全表情 brow=futo_gokigen, cheek=normal2。
- normal:   eye=open,     mouth=close/half/open,             fx=null
- happy:    eye=happy,    mouth=smile_close/smile_open/smile_open, fx=null
- surprise: eye=surprise, mouth=close/half/open,             fx=sweat
- trouble:  eye=open,     mouth=close/half/open,             fx=null
- panic:    eye=surprise, mouth=close/half/open,             fx=sweat

※この初期値で `npx remotion still` の各表情が現状と一致すれば Stage1 成功。
　眉/顔色は今と同じ(normal/hoppe, futo_gokigen/normal2)なので見た目は不変のはず。
　Stage3でtrouble=worry1+hoppe, panic=worry2+pale 等に振り替える。

## 注意
- Python は全角クォート禁止（ASCIIのみ）。JSの文字列中の日本語はOK。
- public/ は生成物(gitignore)。expressions.json は scene/story 同様 force-add で追跡。
- PSDは再配布NG（公開時は履歴ごと除去）。candidates/ も公開対象外で良い。
