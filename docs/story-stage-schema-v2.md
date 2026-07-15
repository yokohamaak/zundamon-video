# Story Stage Schema v2

通常表示の人物・配置・カメラを扱う新しい台本形式。旧 `speakerAnchor`、`manualPos`、`focusSpeaker`、`manualCameraFrame` は v2 では使用しない。

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

## 表示種別

```ts
type DisplayMode =
  | { kind: "standard" }
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

`standard`、`whiteboard`、`zunMeet` を描画する。ホワイトボードの`presenterId`は在席中のstage個体だけを指定でき、通常stageの座標やカメラを流用しない。ZunMeetは1〜4人の参加者を専用タイルへ描画し、`focus`では`activeSpeakerId`（なければ話者）を大きくする。特殊表示中にも `stage` イベントは適用され、通常表示へ戻った時点で解決済みの状態へ復帰する。ニュース・再現VTRは、それぞれの専用データ契約を追加してから対応する。

## シーンのレイアウト

```json
{
  "layouts": {
    "standard": {
      "slots": {
        "speakerLeft": {
          "origin": { "x": 0.28, "y": 0.96 },
          "scale": 1.9,
          "zIndex": 20,
          "cameraPresetId": "left"
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

slotは画面上の場所であり、話者役やanchor名の意味を持たない。`origin` は人物の論理接地点（通常は足元）であり、編集時のドラッグの掴み点ではない。v2エディタは素材の身体中央を掴み点に使い、ドラッグ量を足元基準の`origin`へ変換して保存する。素材は必要に応じて `stageOrigin` と `faceOrigin` を持つ。

## カメラ

```ts
type Framing =
  | { mode: "sceneDefault" }
  | { mode: "speaker" }
  | { mode: "slot"; slotId: string }
  | { mode: "manual"; frame: { cx: number; cy: number; width: number } };

type CameraMotion = {
  zoom?: number;
  pan?: { x: number; y: number };
  tilt?: number;
  shake?: { strength: number; duration: number };
};
```

`framing` は構図の唯一の入口である。`speaker` は「話者のslotに結び付いたcamera presetを選ぶ」だけで、話者・在席・人物配置を変更しない。`cameraMotion` は解決済み構図に一時的に重ねる演出であり、配置の補正には使わない。`cameraMotion` は指定したターンだけに効き、次ターンへ継承しない。

## 保存時の不変条件

1. `schemaVersion` は `2`、`instances` と `script` は必須。
2. `speaker`、`enter`、`exit`、`update`、`reset` は実在する個体IDだけを参照する。
3. stageに出る個体は `characterId` を持つ。`voiceOnly` 個体は登場・配置・フォーカスできない。
4. `standard` の同一slotには1個体だけ置ける。`allowOverlap=true` のslotだけは複数可で、各個体に明示 `zIndex` が必要。
5. slot指定・slot framingは、そのsceneの対応する `layoutProfile` に存在するslotだけを参照する。
6. `framing: speaker` は、話者が画面上の在席個体である時だけ使える。
7. `manual` の座標・カメラ枠・scaleは有限数値かつ画面操作で扱える範囲に収める。

旧JSONは実行時に解釈しない。必要な時だけ変換して、上記の検証を通過したものを保存・レンダリングする。
