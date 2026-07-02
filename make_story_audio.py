#!/usr/bin/env python3
"""ストーリー台本(story-01.json) → VOICEVOXで音声生成し timings を書き戻す。"""
import json
import os
import shutil
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
from tts_voicevox import synthesize_dialogue  # noqa: E402

VOICE_PROFILES_PATH = os.path.join(ROOT, "config", "voice_profiles.json")
VIDEO_PUBLIC_DIR = os.path.join(ROOT, "video", "public")

DEFAULT_VOICE_PROFILES = {
    "zundamon": {"engine": "voicevox", "speaker": 3, "params": {"intonationScale": 1.3}},
    "metan": {"engine": "voicevox", "speaker": 2, "params": {"speedScale": 0.92}},
    "営業": {"engine": "voicevox", "speaker": 11},
    "部長": {"engine": "voicevox", "speaker": 13},
    "AI": {"engine": "voicevox", "speaker": 8, "params": {"intonationScale": 0.18}},
    "棒読み男": {
        "engine": "voicevox",
        "speaker": 11,
        "params": {"speedScale": 0.88, "pitchScale": 0.0, "intonationScale": 0.0},
    },
    "棒読み女": {
        "engine": "voicevox",
        "speaker": 8,
        "params": {"speedScale": 0.90, "pitchScale": 0.0, "intonationScale": 0.0},
    },
    "troublemaker_male_normal": {
        "engine": "voicevox",
        "speaker": 11,
        "params": {
            "speedScale": 1.08, "pitchScale": -0.03, "intonationScale": 0.45,
            "volumeScale": 0.88, "prePhonemeLength": 0.04, "postPhonemeLength": 0.09,
        },
        "fx": {"volume": 0.86, "lowpass": 7200},
    },
    "troublemaker_male_creepy": {
        "engine": "voicevox",
        "speaker": 11,
        "params": {
            "speedScale": 0.92, "pitchScale": -0.07, "intonationScale": 0.22,
            "volumeScale": 0.84, "prePhonemeLength": 0.12, "postPhonemeLength": 0.18,
        },
        "fx": {"volume": 0.82, "lowpass": 5200},
    },
    "troublemaker_female_normal": {
        "engine": "voicevox",
        "speaker": 8,
        "params": {
            "speedScale": 1.12, "pitchScale": 0.06, "intonationScale": 0.50,
            "volumeScale": 0.86, "prePhonemeLength": 0.03, "postPhonemeLength": 0.08,
        },
        "fx": {"volume": 0.86, "lowpass": 7200},
    },
    "troublemaker_female_creepy": {
        "engine": "voicevox",
        "speaker": 8,
        "params": {
            "speedScale": 0.94, "pitchScale": 0.02, "intonationScale": 0.24,
            "volumeScale": 0.82, "prePhonemeLength": 0.11, "postPhonemeLength": 0.18,
        },
        "fx": {"volume": 0.82, "lowpass": 5200},
    },
}

BASE_TTS_CONFIG = {
    "speedScale": 1.0,
    "pitchScale": 0.0,
    "intonationScale": 1.0,
    "volumeScale": 1.0,
    "prePhonemeLength": 0.0,
    "postPhonemeLength": 0.1,
    "inter_turn_pause": 0.35,
    "caption_max_chars": 24,
    "cache_dir": os.path.join(VIDEO_PUBLIC_DIR, ".voice_cache"),
}


def load_voice_profiles(path=VOICE_PROFILES_PATH):
    profiles = json.loads(json.dumps(DEFAULT_VOICE_PROFILES, ensure_ascii=False))
    if not os.path.exists(path):
        return profiles
    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)
    for name, profile in (loaded or {}).items():
        if not isinstance(profile, dict):
            continue
        merged = dict(profiles.get(name, {}))
        merged.update(profile)
        if isinstance(profiles.get(name, {}).get("params"), dict) or isinstance(profile.get("params"), dict):
            merged["params"] = {
                **(profiles.get(name, {}).get("params") or {}),
                **(profile.get("params") or {}),
            }
        if isinstance(profiles.get(name, {}).get("fx"), dict) or isinstance(profile.get("fx"), dict):
            merged["fx"] = {
                **(profiles.get(name, {}).get("fx") or {}),
                **(profile.get("fx") or {}),
            }
        profiles[name] = merged
    return profiles


def build_tts_config(voice_profiles, on_progress=None):
    speakers = {}
    voice_params = {}
    for name, profile in voice_profiles.items():
        if profile.get("engine") != "voicevox":
            continue
        speakers[name] = profile["speaker"]
        if profile.get("params"):
            voice_params[name] = dict(profile["params"])
    cfg = {"tts_voicevox": dict(BASE_TTS_CONFIG)}
    cfg["tts_voicevox"]["speakers"] = speakers
    cfg["tts_voicevox"]["voice_params"] = voice_params
    if callable(on_progress):
        cfg["tts_voicevox"]["on_progress"] = on_progress
    return cfg


def build_script_turns(data, voice_profiles):
    insert_hold = 2.5
    script = []
    for t in data["script"]:
        pause = t.get("pause")
        if t.get("insert") and not (t.get("text") or "").strip() and not pause:
            pause = insert_hold
        narration_voice = t.get("narrationVoice")
        speaker = narration_voice or t["speaker"]
        if speaker not in voice_profiles:
            raise KeyError(f"話者プロファイルがありません: {speaker}")
        item = {"speaker": speaker, "text": t["text"]}
        if pause:
            item["pause"] = pause
        if isinstance(t.get("voice"), dict) and t["voice"]:
            item["voice"] = dict(t["voice"])
        fx = voice_profiles[speaker].get("fx")
        if isinstance(fx, dict) and fx:
            item["audioFx"] = dict(fx)
        script.append(item)
    return script


def render_story_audio(basename, voice_profiles_path=VOICE_PROFILES_PATH):
    story_path = os.path.join(VIDEO_PUBLIC_DIR, f"{basename}.json")
    out_wav = os.path.join(VIDEO_PUBLIC_DIR, f"{basename}.wav")
    out_mp3 = os.path.join(VIDEO_PUBLIC_DIR, f"{basename}.mp3")

    with open(story_path, encoding="utf-8") as f:
        data = json.load(f)
    voice_profiles = load_voice_profiles(voice_profiles_path)
    script = build_script_turns(data, voice_profiles)
    total = len(script)
    url = os.environ.get("VOICEVOX_URL") or "http://localhost:50021"
    print(f"VOICEVOX合成開始: {total}ターン (接続先 {url})", flush=True)

    def on_progress(idx, n, turn):
        head = turn["text"][:18].replace("\n", " ")
        print(f"[{idx + 1:>3}/{n}] {turn['speaker']}: {head}…", flush=True)

    config = build_tts_config(voice_profiles, on_progress=on_progress)
    cache_dir = ((config.get("tts_voicevox") or {}).get("cache_dir"))
    if os.environ.get("STORY_AUDIO_FORCE_REBUILD") == "1" and cache_dir:
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
            print(f"音声キャッシュ削除: {cache_dir}", flush=True)
    pcm, turns, (channels, width, rate) = synthesize_dialogue(script, config)
    print(f"合成完了 → WAV書き出し: {out_wav}", flush=True)

    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

    audio_name = f"{basename}.wav"
    if shutil.which("ffmpeg"):
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", out_wav,
                 "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0",
                 "-f", "mp3", out_mp3],
                check=True,
            )
            audio_name = f"{basename}.mp3"
            print(f"MP3書き出し: {out_mp3}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"※ MP3変換に失敗（WAVを使用）: {e}", flush=True)
    else:
        print("※ ffmpeg が無いため WAV を使用（プレビューが重い場合あり）", flush=True)

    for turn, info in zip(data["script"], turns):
        turn["start"] = info["start"]
        turn["end"] = info["end"]
        turn["sentences"] = info["sentences"]
    data["audio"] = audio_name

    with open(story_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total_sec = turns[-1]["end"] if turns else 0
    print(f"OK: {out_wav} 生成 / 尺 {total_sec:.1f}s / timings を {story_path} に書き戻しました")


def main():
    basename = sys.argv[1] if len(sys.argv) > 1 else "story-01"
    render_story_audio(basename)


if __name__ == "__main__":
    main()
