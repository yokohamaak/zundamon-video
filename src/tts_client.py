"""
Gemini TTS連携モジュール
スクリプトから男女掛け合い音声を生成する
"""
import logging
import os
import re
import time
import wave
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2  # 16-bit PCM


def wave_file(filename, pcm, channels=1, rate=SAMPLE_RATE, sample_width=SAMPLE_WIDTH):
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def generate_audio(script: list, config: dict, output_path: str) -> list:
    """
    スクリプトリストから掛け合い音声を生成してmp3として保存する。
    Multi-speaker TTSで1リクエスト生成し、文字数比でタイムスタンプを近似する。
    Returns: [{"start": 0.0, "end": 5.2}, ...]
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    host = config["characters"]["host"]
    guest = config["characters"]["guest"]
    host_voice = config["tts"]["host_voice"]
    guest_voice = config["tts"]["guest_voice"]
    tts_model = config.get("models", {}).get("tts", "gemini-2.5-flash-preview-tts")

    prompt = "\n".join(f'{turn["speaker"]}: {turn["text"]}' for turn in script)

    tts_config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker=host["name"],
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=host_voice),
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker=guest["name"],
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=guest_voice),
                        ),
                    ),
                ]
            )
        ),
    )

    logger.info(f"Gemini TTSで音声生成中（{len(script)}ターン・1リクエスト）...")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=tts_model,
                contents=prompt,
                config=tts_config,
            )
            break
        except Exception as e:
            if attempt == max_attempts:
                raise
            err_str = str(e)
            match = re.search(r'retry[^\d]*(\d+(?:\.\d+)?)\s*s', err_str, re.IGNORECASE)
            wait = max(int(float(match.group(1))) + 10, 65) if match else 65
            logger.warning(f"TTS失敗（試行{attempt}/{max_attempts}）、{wait}秒後にリトライ: {e}")
            time.sleep(wait)

    parts = response.candidates[0].content.parts
    pcm = b"".join(p.inline_data.data for p in parts if p.inline_data)
    total_duration = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)
    logger.info(f"音声生成完了（合計{total_duration:.1f}秒）")

    import pykakasi
    kks = pykakasi.kakasi()

    def _turn_weight(text):
        w = 0.0
        for item in kks.convert(text):
            hira = item["hira"] or item["orig"]
            for ch in hira:
                if ch in "。！？\n":
                    w += 3.0
                elif ch in "、，「」『』…":
                    w += 1.8
                else:
                    w += 1.0
        return w + 2.0  # 話者切り替えオーバーヘッド

    total_weight = sum(_turn_weight(turn["text"]) for turn in script)
    timestamps = []
    current_time = 0.0
    for turn in script:
        duration = total_duration * _turn_weight(turn["text"]) / total_weight
        timestamps.append({"start": round(current_time, 3), "end": round(current_time + duration, 3)})
        current_time += duration

    wav_path = output_path.replace(".mp3", ".wav")
    wave_file(wav_path, pcm)

    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", output_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg変換失敗: {result.stderr}")

    os.remove(wav_path)
    logger.info(f"MP3保存完了: {output_path}")

    return timestamps
