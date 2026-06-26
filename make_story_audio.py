#!/usr/bin/env python3
"""ストーリー台本(story-sample.json) → VOICEVOXで音声(story.wav)を生成し、
各ターンの start/end と sentences(字幕単位) を JSON に書き戻す。

前提: VOICEVOXエンジンを起動しておく（ローカル・無料・課金なし）。
      接続先は環境変数 VOICEVOX_URL か既定 http://localhost:50021。
使い方: python make_story_audio.py
標準ライブラリのみ＋ src/tts_voicevox（既存の合成ロジックを流用）。
"""
import json
import os
import sys
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
from tts_voicevox import synthesize_dialogue  # noqa: E402

STORY = os.path.join(ROOT, "video", "public", "story-sample.json")
OUT_WAV = os.path.join(ROOT, "video", "public", "story.wav")

# 話者→VOICEVOX話者ID（ずんだもん=3 / 四国めたん=2）。声の調整は voice_params で。
CONFIG = {
    "tts_voicevox": {
        "speakers": {"zundamon": 3, "metan": 2},
        "speed": 1.0,
        "pitch": 0.0,
        "intonation": 1.0,
        "inter_turn_pause": 0.35,  # ターン間の無音（テンポ）
        "caption_max_chars": 24,
    }
}


def main():
    data = json.load(open(STORY, encoding="utf-8"))
    script = [{"speaker": t["speaker"], "text": t["text"]} for t in data["script"]]

    pcm, turns, (channels, width, rate) = synthesize_dialogue(script, CONFIG)

    with wave.open(OUT_WAV, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

    # 実音声の尺で start/end/sentences を上書き（手書きの仮タイミングを置換）。
    for turn, info in zip(data["script"], turns):
        turn["start"] = info["start"]
        turn["end"] = info["end"]
        turn["sentences"] = info["sentences"]
    data["audio"] = "story.wav"

    json.dump(data, open(STORY, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    total = turns[-1]["end"] if turns else 0
    print(f"OK: {OUT_WAV} 生成 / 尺 {total:.1f}s / timings を {STORY} に書き戻しました")


if __name__ == "__main__":
    main()
