"""v2ステージ確認に必要な経路だけを残す短縮台本を生成する。"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "video/public/story-01.json"


def slot(instance_id: str, slot_id: str) -> dict:
    return {"instanceId": instance_id, "placement": {"mode": "slot", "slotId": slot_id}}


def manual(instance_id: str, x: float, y: float, scale: float) -> dict:
    return {
        "instanceId": instance_id,
        "placement": {
            "mode": "manual",
            "origin": {"x": x, "y": y},
            "scale": scale,
            "zIndex": 15,
        },
    }


def turn(turn_id: str, speaker: str, text: str, scene: str, stage: dict, start: float) -> dict:
    return {
        "id": turn_id,
        "speaker": speaker,
        "text": text,
        "scene": scene,
        "displayMode": {"kind": "standard"},
        "stage": stage,
        "start": start,
        "end": start + 2.5,
        "sentences": [],
    }


def main() -> None:
    script = [
        turn("verify-01", "zundamon", "通常表示と右スロットを確認するのだ。", "office", {
            "enter": [slot("zundamon", "speakerRight")],
            "update": {"zundamon": {"expression": "happy", "pose": "proud"}},
            "framing": {"mode": "speaker"},
        }, 0),
        turn("verify-02", "metan", "左スロットと話者フォーカスを確認するわ。", "office", {
            "enter": [slot("metan", "speakerLeft")],
            "update": {"metan": {"expression": "normal", "pose": "point"}},
            "framing": {"mode": "speaker"},
        }, 2.5),
        turn("verify-03", "zundamon", "婚活シーンへ移動するのだ。", "konkatsu", {
            "enter": [slot("zundamon", "speakerRight"), manual("女性", 0.388, 0.817, 0.65)],
            "update": {"zundamon": {"expression": "happy", "pose": "step_in"}},
            "framing": {"mode": "sceneDefault"},
        }, 5),
        turn("verify-04", "女性", "モブの表示と発話を確認します。", "konkatsu", {
            "update": {"女性": {"expression": "normal"}},
            "framing": {"mode": "sceneDefault"},
        }, 7.5),
        turn("verify-05", "zundamon", "手動カメラでも位置関係を保つのだ。", "konkatsu", {
            "update": {"zundamon": {"expression": "trouble", "pose": "flustered"}},
            "framing": {"mode": "manual", "frame": {"cx": 0.6671408765467864, "cy": 0.4618075673550469, "width": 0.47003424657534226}},
            "cameraMotion": {"zoom": 0.1, "tilt": -2},
        }, 10),
        turn("verify-06", "zundamon", "モブの退場を確認するのだ。", "konkatsu", {
            "exit": ["女性"],
            "update": {"zundamon": {"expression": "trouble", "pose": "droop"}},
            "framing": {"mode": "speaker"},
            "cameraMotion": {"zoom": 0.1},
        }, 12.5),
        turn("verify-07", "zundamon", "次は公園で別のモブを確認するのだ。", "park_day", {
            "enter": [slot("zundamon", "speakerRight"), manual("おじいさん", 0.347, 0.767, 0.85)],
            "update": {"zundamon": {"expression": "happy", "pose": "cheer"}},
            "framing": {"mode": "sceneDefault"},
        }, 15),
        turn("verify-08", "おじいさん", "別のモブも正しく表示されているかな。", "park_day", {
            "update": {"おじいさん": {"expression": "normal"}},
            "framing": {"mode": "sceneDefault"},
        }, 17.5),
        turn("verify-09", "metan", "ホワイトボード表示へ切り替えるわ。", "office", {
            "enter": [slot("zundamon", "speakerRight"), slot("metan", "speakerLeft")],
            "update": {"metan": {"expression": "normal", "pose": "proud"}},
            "framing": {"mode": "sceneDefault"},
        }, 20),
        {
            **turn("verify-10", "metan", "配置とカメラの確認ポイントを整理するわ。", "office", {
                "update": {"metan": {"expression": "normal", "pose": "proud"}},
                "framing": {"mode": "sceneDefault"},
            }, 22.5),
            "displayMode": {
                "kind": "whiteboard",
                "presenterId": "metan",
                "whiteboard": {
                    "title": "v2ステージ確認",
                    "theme": "確認項目",
                    "sections": [
                        {"heading": "配置", "bullets": ["スロット", "手動配置"]},
                        {"heading": "人物", "bullets": ["メイン", "モブ"]},
                        {"heading": "画面", "bullets": ["カメラ", "表示種別"]},
                    ],
                    "conclusion": "問題があればシーン側で調整する",
                    "layout": "compact",
                },
            },
        },
        turn("verify-11", "AI", "画面外ナレーションも確認します。", "home-night", {
            "framing": {"mode": "sceneDefault"},
        }, 25),
    ]
    story = {
        "schemaVersion": 2,
        "title": "v2ステージ確認用・短縮台本",
        "instances": {
            "AI": {"voiceId": "AI", "role": "voiceOnly"},
            "metan": {"characterId": "metan", "voiceId": "metan"},
            "zundamon": {"characterId": "zundamon", "voiceId": "zundamon"},
            "おじいさん": {"characterId": "おじいさん", "voiceId": "おじいさん"},
            "女性": {"characterId": "女性", "voiceId": "女性"},
        },
        "script": script,
    }
    DESTINATION.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
