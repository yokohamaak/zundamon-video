#!/usr/bin/env python3
"""ストーリー台本(story-01.json) → VOICEVOXで音声(story-01.wav)を生成し、
各ターンの start/end と sentences(字幕単位) を JSON に書き戻す。

前提: VOICEVOXエンジンを起動しておく（ローカル・無料・課金なし）。
      接続先は環境変数 VOICEVOX_URL か既定 http://localhost:50021。
使い方:
  python make_story_audio.py              # 既定: story-01.json / story-01.wav
  python make_story_audio.py story-02    # 任意ファイル名（拡張子なし）を第1引数で指定
標準ライブラリのみ＋ src/tts_voicevox（既存の合成ロジックを流用）。
"""
import json
import os
import shutil
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
from tts_voicevox import synthesize_dialogue  # noqa: E402

# 第1引数でファイル名ベース（拡張子なし）を受け付ける。省略時は story-01。
_basename = sys.argv[1] if len(sys.argv) > 1 else "story-01"
STORY = os.path.join(ROOT, "video", "public", f"{_basename}.json")
OUT_WAV = os.path.join(ROOT, "video", "public", f"{_basename}.wav")
OUT_MP3 = os.path.join(ROOT, "video", "public", f"{_basename}.mp3")

# 話者→VOICEVOX話者ID。
# 主役: ずんだもん=3 / 四国めたん=2
# モブ(姿なし・声+吹き出しのみ):
#   営業=11（玄野武宏・男性・要調整可）
#   部長=13（青山龍星・低め男性・要調整可）
#   AI=8（春日部つむぎ・中性的・要調整可）
# TODO: 営業は「棒読み」が理想だが synthesize_dialogue が話者別パラメータ非対応のため未実装。
#       話者別 speed/intonation を実装する場合は tts_voicevox.synthesize_dialogue を拡張すること。
CONFIG = {
    "tts_voicevox": {
        "speakers": {
            "zundamon": 3,
            "metan": 2,
            "営業": 11,
            "部長": 13,
            "AI": 8,
        },
        "speed": 1.0,
        "pitch": 0.0,
        "intonation": 1.0,
        "inter_turn_pause": 0.35,  # ターン間の無音（テンポ）
        "caption_max_chars": 24,
    }
}

NARRATION_VOICE_DEFAULTS = {
    "棒読み男": {"speed": 0.88, "pitch": 0.0, "intonation": 0.0},
    "棒読み女": {"speed": 0.90, "pitch": 0.0, "intonation": 0.0},
}


def main():
    data = json.load(open(STORY, encoding="utf-8"))
    # per-turn の pause(台詞後の無音秒)があれば転送する。回想境界の「一拍の間」に使う。
    # インサート専用ターン(セリフ空)は音声尺がゼロだと画面に一瞬も出ないため、
    # 最低表示秒(INSERT_HOLD)の無音をpauseとして確保する。
    INSERT_HOLD = 2.5
    script = []
    for t in data["script"]:
        pause = t.get("pause")
        if t.get("insert") and not (t.get("text") or "").strip() and not pause:
            pause = INSERT_HOLD
        narration_voice = t.get("narrationVoice")
        speaker = narration_voice or t["speaker"]
        item = {"speaker": speaker, "text": t["text"]}
        if pause:
            item["pause"] = pause
        voice = None
        if isinstance(t.get("voice"), dict) and t["voice"]:
            voice = dict(t["voice"])
        elif narration_voice in NARRATION_VOICE_DEFAULTS:
            voice = dict(NARRATION_VOICE_DEFAULTS[narration_voice])
        if voice:
            item["voice"] = voice
        script.append(item)

    total = len(script)
    url = os.environ.get("VOICEVOX_URL") or "http://localhost:50021"
    print(f"VOICEVOX合成開始: {total}ターン (接続先 {url})", flush=True)

    def on_progress(idx, n, turn):
        head = turn["text"][:18].replace("\n", " ")
        print(f"[{idx + 1:>3}/{n}] {turn['speaker']}: {head}…", flush=True)

    CONFIG["tts_voicevox"]["on_progress"] = on_progress
    CONFIG["tts_voicevox"]["speakers"]["棒読み男"] = 11
    CONFIG["tts_voicevox"]["speakers"]["棒読み女"] = 8

    pcm, turns, (channels, width, rate) = synthesize_dialogue(script, CONFIG)
    print(f"合成完了 → WAV書き出し: {OUT_WAV}", flush=True)

    with wave.open(OUT_WAV, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

    # 非圧縮WAVはStudioプレビューで重く不安定（useAudioData/再生が途切れる）。
    # ffmpeg があれば MP3 に圧縮し、動画はそちらを参照する（プレビュー安定・約1/10）。
    audio_name = f"{_basename}.wav"
    if shutil.which("ffmpeg"):
        try:
            # CBR(128k)＋Xingヘッダ無し。VBR/Xingだとブラウザ<audio>のシークが不正確になり、
            # シーク後に音声が冒頭だけ鳴って後はズレる（review_serverと同じ対策）。
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", OUT_WAV,
                 "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0",
                 "-f", "mp3", OUT_MP3],
                check=True,
            )
            audio_name = f"{_basename}.mp3"
            print(f"MP3書き出し: {OUT_MP3}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"※ MP3変換に失敗（WAVを使用）: {e}", flush=True)
    else:
        print("※ ffmpeg が無いため WAV を使用（プレビューが重い場合あり）", flush=True)

    # 実音声の尺で start/end/sentences を上書き（手書きの仮タイミングを置換）。
    for turn, info in zip(data["script"], turns):
        turn["start"] = info["start"]
        turn["end"] = info["end"]
        turn["sentences"] = info["sentences"]
    # audio フィールドはファイル名のみ（public/ 直下への相対パス）。
    data["audio"] = audio_name

    json.dump(data, open(STORY, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    total = turns[-1]["end"] if turns else 0
    print(f"OK: {OUT_WAV} 生成 / 尺 {total:.1f}s / timings を {STORY} に書き戻しました")


if __name__ == "__main__":
    main()
