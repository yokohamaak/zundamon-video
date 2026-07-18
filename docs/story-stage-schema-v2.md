# Story Stage Schema v2

人物・配置・カメラ・対応する表示種別を扱う新しい台本形式。旧 `speakerAnchor`、`manualPos`、`focusSpeaker`、`manualCameraFrame` は v2 では使用しない。旧JSONは実行時に互換解釈せず、必要な時だけ変換してvalidatorへ通す。

## 基本形

```json
{
  "schemaVersion": 2,
  "instances": {
    "zundamon": { "characterId": "zundamon", "voiceId": "zundamon" },
    "metan": { "characterId": "metan", "voiceId": "metan" },
    "narrator": { "voiceId": "棒読み男", "role": "voiceOnly" }
  },
  "script": [
    {
      "id": "turn-0001",
      "speaker": "zundamon",
      "text": "始めるのだ。",
      "scene": "office",
      "displayMode": { "kind": "standard" },
      "stage": {
        "enter": [
          { "instanceId": "zundamon", "placement": { "mode": "slot", "slotId": "speakerLeft" } }
        ],
        "framing": { "mode": "speaker" }
      }
    }
  ]
}
```

## 人物の定義と個体

- `instances` のキーは台本内で使う **個体ID**。`speaker`、`stage.enter`、`stage.exit`、`stage.update` はこのIDを参照する。
- `characterId` は立ち絵・表情・ポーズを引く素材定義ID。未指定の `voiceOnly` 個体は画面へ出せない。
- `voiceId` は音声プロファイルを引くID。同じ素材を使う別個体でも別の声を指定できる。
- 同一素材の複数個体は、異なる個体IDに同じ `characterId` を指定する。

## ターンの舞台イベント

`stage` はそのターン開始時に適用する差分イベントである。scene変更時には前sceneの舞台状態を破棄してから、そのターンのイベントを適用する。

```ts
type StageEvent = {
  enter?: Array<{ instanceId: string; placement: Placement }>;
  exit?: string[];
  update?: Record<string, InstancePatch>;
  reset?: string[];
  framing?: Framing;
  cameraMotion?: CameraMotion;
};

type Placement =
  | { mode: "slot"; slotId: string; offset?: { x: number; y: number } }
  | { mode: "manual"; origin: { x: number; y: number }; scale?: number; zIndex?: number };

type InstancePatch = {
  placement?: Placement;
  expression?: string;
  pose?: string;
  face?: "left" | "right";
  flip?: boolean;
  zIndex?: number;
};
```

- 登場・退場は必ず `enter` / `exit` で明示する。話者指定は登場させない。
- `update` は在席中の個体だけを更新する。`reset` はその個体の表情・ポーズ・向き・反転・重なり順を既定値へ戻し、配置は維持する。現在の `face` は左右の向きだけを表す。後ろ姿などの素材切替は、素材能力モデルを追加するまで指定しない。
- `present` は `enter` / `exit` からresolverが導く。在席中でも特殊表示中は `visible=false` になり得る。両者を台本に重複保存しない。

## 場面転換

`transition` はそのターンに入る境界で使う場面転換である。V2で許可する値は `cut` / `fade-black` / `fade-white` / `wipe-left` / `wipe-right` / `slide-left` / `slide-right`。未指定時は通常の即時切替として扱う。

- `cut`: 即時切替。
- `fade-black`: 切替先ターン開始時に黒カバーを抜く。
- `fade-white`: 切替先ターン開始時に白カバーを抜く。
- `wipe-left`: 前ターンの映像プレート上に、切替先ターンの映像プレートを左からワイプ表示する。
- `wipe-right`: 前ターンの映像プレート上に、切替先ターンの映像プレートを右からワイプ表示する。
- `slide-left`: 前ターンの映像プレート上に、切替先ターンの映像プレートを左からスライド表示する。
- `slide-right`: 前ターンの映像プレート上に、切替先ターンの映像プレートを右からスライド表示する。

V2のwipe/slideでは、前ターン側のプレートは背景・前景・人物・表示種別の静的な映像だけを持つ。前ターン側の吹き出し・字幕・テロップ・effectsは残さず、切替先ターン側だけに表示する。音声/BGM/SEも二重再生しない。

## 表示種別

```ts
type DisplayMode =
  | { kind: "standard" }
  | {
      kind: "framedStage";
      framedStage: {
        background: string;
        frame: { x: number; y: number; width: number };
        frameTransition?: "fixed" | "smooth";
      };
    }
  | {
      kind: "whiteboard";
      presenterId?: string;
      whiteboard: { title: string; theme: string; sections: Section[]; conclusion: string };
    }
  | {
      kind: "zunMeet";
      zunMeet: {
        room?: string;
        layout?: "focus" | "grid";
        activeSpeakerId?: string;
        participants: Array<{ instanceId: string; name?: string; cameraOff?: boolean; muted?: boolean }>;
      };
    };
```

`standard`、`framedStage`、`whiteboard`、`zunMeet` を描画する。`framedStage` はターンごとに `background/` 配下の外側背景を選び、`frame` の左上座標 `x/y` と幅 `width` で指定する16:9領域に通常stage（シーン・人物・吹き出し・字幕・テロップ・カメラ）を縮小してクリップ描画する。`x/y/width` はそれぞれ画面幅・画面高に対する比率で、外側も16:9のため枠の高さ比率は `width` と同じになる。枠は画面内に収め、`width` は `0.1〜1` とする。`frameTransition: "smooth"` は、前ターンと外側背景が同じ時に枠を0.9秒で移動・拡縮する。ターンの `transition`（フェード／ワイプ／スライド）は枠の内側だけに適用され、外側背景は固定する。ホワイトボードの`presenterId`は在席中のstage個体だけを指定でき、通常stageの座標やカメラを流用しない。ZunMeetは1〜4人の参加者を専用タイルへ描画し、`focus`では`activeSpeakerId`（なければ話者）を大きくする。特殊表示中にも `stage` イベントは適用され、通常表示へ戻った時点で解決済みの状態へ復帰する。ニュース・再現VTRは、それぞれの専用データ契約を追加してから対応する。

## シーンのレイアウト

シーンは `bg`（静止画）または `bgVideo`（動画）のどちらかを必ず持つ。両方がある場合は `bgVideo` を描画し、`bgVideoLoop: true` で動画をループする。`front` は人物レイヤーの前に描画する前景画像である。

```json
{
  "layouts": {
    "standard": {
      "slots": {
        "speakerLeft": {
          "origin": { "x": 0.28, "y": 0.96 },
          "scale": 1.9,
          "zIndex": 20,
          "cameraPresetId": "left",
          "previewCharacterId": "metan"
        },
        "speakerRight": {
          "origin": { "x": 0.72, "y": 0.96 },
          "scale": 1.9,
          "zIndex": 20,
          "cameraPresetId": "right"
        },
        "backgroundLeft": {
          "origin": { "x": 0.12, "y": 0.98 },
          "allowOverlap": true,
          "zIndex": 5
        }
      }
    }
  },
  "cameraPresets": {
    "default": { "cx": 0.5, "cy": 0.5, "width": 1 },
    "left": { "cx": 0.3, "cy": 0.5, "width": 0.7 }
  }
}
```

slotは画面上の場所であり、話者役やanchor名の意味を持たない。`origin` は人物の論理接地点（現実装では足元）であり、編集時のドラッグの掴み点ではない。v2エディタは素材の身体中央を掴み点に使い、ドラッグ量を足元基準の`origin`へ変換して保存する。素材別の `stageOrigin` / `faceOrigin` メタデータは、座り姿・後ろ姿などを扱う時に追加する未実装範囲である。

## カメラ

```ts
type Framing =
  | { mode: "sceneDefault" }
  | { mode: "speaker" }
  | { mode: "slot"; slotId: string }
  | { mode: "manual"; frame: { cx: number; cy: number; width: number } };

type CameraMotion = {
  inherit?: boolean;
  zoom?: number;
  pan?: { x: number; y: number };
  tilt?: number;
  shake?: { strength: number; duration: number };
};
```

`framing` は構図の唯一の入口である。`speaker` は「話者のslotに結び付いたcamera presetを選ぶ」だけで、話者・在席・人物配置を変更しない。`cameraMotion` は解決済み構図に一時的に重ねる演出であり、配置の補正には使わない。既定では指定したターンだけに効くが、`inherit: true` を指定したターンは前ターンの `zoom` / `pan` / `tilt` を継続し、そのターンの指定値だけを上書きする。`shake` は常にターン限定で、シーン変更時はすべてリセットする。

`previewCharacterId` はシーンエディタだけで使う確認用立ち絵であり、台本でそのslotへ配置できる個体・実際に配置された個体を制限しない。未指定ならシーンエディタは人物を描かない。

## エディタでの操作

- シーンエディタで、背景ごとの `layouts.standard.slots` と `cameraPresets` を事前設定する。ここは台本のターン状態を編集する画面ではない。
- 台本エディタで、instanceの追加、明示的な登場／退場、slot選択、offset、表情、pose、左右向き、手動配置、scale、z-index、framingを操作する。話者を選んでも自動登場はしない。
- 手動配置のドラッグは身体中央を掴むが、保存値は足元originである。手動カメラはステージ全体図でカメラ枠をドラッグ・リサイズして `framing: manual` として保存する。`cameraMotion` はこの構図とは別の一時演出である。
- `framedStage` は基本タブで外側背景を選び、背景マップ上の16:9枠をドラッグ・リサイズして `frame` を保存する。枠の内側だけに通常stageを描画する。
- `whiteboard` は在席instanceをpresenterに選び、`zunMeet` は1〜4人のinstanceを参加者に選ぶ。特殊表示中もstage eventは進み、通常表示に戻るとその状態に復帰する。
- v2編集のUndo/Redoはセッション内で最大80操作を保持する。JSONに履歴は保存しないため、読込・インポート後は新しい履歴として始まる。

## 現在の対応範囲

実装済みの表示種別は `standard`、`framedStage`、`whiteboard`、`zunMeet` と各種インサート表示である。`face` は `left` / `right` のみで、後ろ姿・座り姿・素材ごとの対応表は未実装である。登場・退場は状態イベントとして即時に反映され、時間・補間を持つ登場／退場アニメーションはまだ定義しない。

## 保存時の不変条件

1. `schemaVersion` は `2`、`instances` と `script` は必須。
2. `speaker`、`enter`、`exit`、`update`、`reset` は実在する個体IDだけを参照する。
3. stageに出る個体は `characterId` を持つ。`voiceOnly` 個体は登場・配置・フォーカスできない。
4. `standard` の同一slotには1個体だけ置ける。`allowOverlap=true` のslotだけは複数可で、各個体に明示 `zIndex` が必要。
5. slot指定・slot framingは、そのsceneの `layouts.standard.slots` に存在するslotだけを参照する。
6. `framing: speaker` は、話者が画面上の在席個体である時だけ使える。
7. `manual` の座標・カメラ枠・scaleは有限数値かつ画面操作で扱える範囲に収める。
8. 各sceneは空でない `bg` または `bgVideo` を持つ。`bgVideoLoop` は指定時にbooleanである。

モブに表情を指定する時は、モブ定義に口閉じ／口開き画像の両方が存在する表情だけを使う。UIとvalidatorの両方で確認し、描画時に別表情へ暗黙フォールバックしない。
