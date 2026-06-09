"""
VOICEVOX TTS連携モジュール

台本（話者ごとのテキスト）から掛け合い音声を生成する。
- 文単位で個別合成し、WAVを連結する（文ごとに正確な実時間が取れる）
- 各WAVの実長から「文／字幕単位」の厳密なタイムスタンプを算出する（Whisper不要）
- 話者ごとに声パラメータ（速さ・高さ・抑揚）を上書きできる

戻り値は動画(video/)が読む形に合わせ、ターンごとに sentences(字幕単位) を持つ。

config（例）:
    tts_voicevox:
      base_url: http://localhost:50021   # 環境変数 VOICEVOX_URL で上書き可
      speakers:                          # 台本のspeaker名 → VOICEVOX話者ID
        ずんだもん: 3
        四国めたん: 2
      speed: 1.0                         # 全体デフォルト(speedScale)
      pitch: 0.0                         # 全体デフォルト(pitchScale)
      intonation: 1.0                    # 全体デフォルト(intonationScale)
      voice_params:                      # 話者ごとの上書き（任意）
        四国めたん: { speed: 0.92 }       #   テンポを少し遅く
        ずんだもん: { intonation: 1.3 }    #   抑揚を強く
      caption_max_chars: 22              # 字幕1枚の最大文字数（超えたら分割）
      inter_turn_pause: 0.0              # ターン間の無音秒（任意）

標準ライブラリのみ。VOICEVOXエンジンのHTTP APIを叩く（自己ホスト・無料・課金なし）。
"""
import io
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import wave

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:50021"
DEFAULT_CAPTION_MAX_CHARS = 22

# 文末（。！？）で区切る。連続する終止符（！？等）はまとめて1文に含める。
# 終止符の直後の閉じ括弧/引用符（」』）等）も同じ文に含める（「へぇ！」の「」」が次の字幕に
# こぼれるのを防ぐ）。
_SENTENCE_RE = re.compile(r"[^。！？!?]*[。！？!?]+[」』）)】〕》〉”’\"']*|[^。！？!?]+")
_MIN_CAPTION_CHARS = 6  # これ未満の字幕断片は隣に併合する

# 英字の直後に「（かな）」が続く読み仮名（例: HIFI（ハイファイ））。VOICEVOXが英字と
# かなを二重に読むのを防ぐため、合成テキストではかなだけにする（字幕は原文のまま）。
# 括弧内が純粋なかな（ひらがな/カタカナ/長音・中黒）かつ直前が英字のときだけ発動＝
# （笑）（諸説あり）等は対象外。全角/半角の括弧に対応。
# 括弧内のかなは語間に空白を含むことがある（例「ワイヤレス フィデリティ」）ので許可する。
_READING_GLOSS_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9.\-'’&\s]*[（(]([ぁ-んゔァ-ヴヷ-ヺー・ 　]+)[）)]"
)


def _spoken_text(text):
    """音声合成用にテキストを整える（字幕には使わない）。

    「英字（かな）」の読み仮名はかなだけ残す（英字＋括弧を落とす）＝二重読み防止。
    """
    return _READING_GLOSS_RE.sub(lambda m: m.group(1), text)


def _http_post(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _with_retry(fn, what, attempts=3, base_wait=2.0):
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - ネットワーク全般をリトライ対象にする
            if i == attempts:
                raise
            wait = base_wait * i
            logger.warning(f"{what} 失敗（試行{i}/{attempts}）、{wait:.0f}秒後に再試行: {e}")
            time.sleep(wait)


def audio_query(base_url, text, speaker, timeout=60):
    """VOICEVOX /audio_query。合成パラメータ（モーラ長等を含む）を取得する。"""
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    raw = _with_retry(
        lambda: _http_post(f"{base_url}/audio_query?{q}", timeout=timeout),
        "audio_query",
    )
    return json.loads(raw)


def synthesis(base_url, query, speaker, timeout=120):
    """VOICEVOX /synthesis。queryからWAV（24kHz/16bit/mono既定）を生成する。"""
    q = urllib.parse.urlencode({"speaker": speaker})
    body = json.dumps(query).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
    return _with_retry(
        lambda: _http_post(f"{base_url}/synthesis?{q}", data=body, headers=headers, timeout=timeout),
        "synthesis",
    )


def _wav_params_and_frames(wav_bytes):
    """WAVバイト列から (channels, width, rate)・PCMフレーム・実秒数を取り出す。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
        nframes = wf.getnframes()
        frames = wf.readframes(nframes)
        duration = nframes / wf.getframerate()
    return params, frames, duration


def _resolve_speaker_id(turn_speaker, speakers_map):
    if turn_speaker not in speakers_map:
        raise KeyError(
            f"話者 '{turn_speaker}' のVOICEVOX話者IDが config.tts_voicevox.speakers にありません "
            f"（既知: {list(speakers_map)}）"
        )
    return speakers_map[turn_speaker]


def _resolve_voice_params(vc, speaker):
    """全体デフォルト＋話者別上書きで (speed, pitch, intonation) を決める。"""
    params = {
        "speed": float(vc.get("speed", 1.0)),
        "pitch": float(vc.get("pitch", 0.0)),
        "intonation": float(vc.get("intonation", 1.0)),
    }
    override = (vc.get("voice_params") or {}).get(speaker, {})
    for k in params:
        if k in override:
            params[k] = float(override[k])
    return params


def _split_sentences(text):
    """テキストを文末記号で文に分割（記号は残す・空要素除去）。"""
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


def _split_caption_units(sentence, max_chars):
    """
    1文を字幕表示単位に分割。max_chars以下ならそのまま。
    長い場合は読点（、）境界でまとめる。語の途中では切らない（読点が無い長い塊は1枚のまま許容）。
    短すぎる断片（_MIN_CAPTION_CHARS未満）は隣に併合してチラつきを防ぐ。
    """
    if len(sentence) <= max_chars:
        return [sentence]

    # 読点で分割し、「、」は直前のトークンに付ける（行頭に読点が来ないように）。
    tokens = []
    for tok in re.split(r"(、)", sentence):
        if not tok:
            continue
        if tok == "、" and tokens:
            tokens[-1] += tok
        else:
            tokens.append(tok)

    # max_charsを目安に貪欲に詰める（途中切断はしない）。
    units = []
    buf = ""
    for tok in tokens:
        if not buf:
            buf = tok
        elif len(buf) + len(tok) <= max_chars:
            buf += tok
        else:
            units.append(buf)
            buf = tok
    if buf:
        units.append(buf)

    # 短すぎる断片を隣へ併合（末尾→前、先頭→次）。
    merged = []
    for u in units:
        if merged and len(u) < _MIN_CAPTION_CHARS:
            merged[-1] += u
        else:
            merged.append(u)
    if len(merged) >= 2 and len(merged[0]) < _MIN_CAPTION_CHARS:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


def synthesize_dialogue(script, config):
    """
    台本 → (連結PCM, ターン情報, WAVパラメータ)。
    ffmpeg非依存。audio_query/synthesis はモジュール関数なのでテストで差し替え可能。

    ターン情報: [{"start","end","sentences":[{"text","start","end"}...]}...]
    （sentences＝字幕単位。文ごとに個別合成して実尺から算出、長文は文字数比で細分）
    """
    vc = config.get("tts_voicevox", {})
    # 接続先は 環境変数VOICEVOX_URL > config.base_url > 既定 の順。
    base_url = os.environ.get("VOICEVOX_URL") or vc.get("base_url") or DEFAULT_BASE_URL
    speakers_map = vc.get("speakers", {})
    pause = float(vc.get("inter_turn_pause", 0.0))
    max_chars = int(vc.get("caption_max_chars", DEFAULT_CAPTION_MAX_CHARS))

    if not script:
        raise ValueError("空の台本です")

    pcm_chunks = []
    turns_out = []
    current = 0.0
    ref_params = None

    for idx, turn in enumerate(script):
        speaker = turn["speaker"]
        speaker_id = _resolve_speaker_id(speaker, speakers_map)
        vp = _resolve_voice_params(vc, speaker)
        turn_start = current
        captions = []

        for sentence in _split_sentences(turn["text"]):
            # 音声に渡すのは読み仮名を畳んだテキスト（字幕＝sentenceは原文のまま）。
            spoken = _spoken_text(sentence)
            query = audio_query(base_url, spoken, speaker_id)
            query["speedScale"] = vp["speed"]
            query["pitchScale"] = vp["pitch"]
            query["intonationScale"] = vp["intonation"]
            wav_bytes = synthesis(base_url, query, speaker_id)
            params, frames, duration = _wav_params_and_frames(wav_bytes)

            if ref_params is None:
                ref_params = params
            elif params != ref_params:
                raise ValueError(f"WAV形式が不一致: {params} != {ref_params}（話者で出力形式が違う？）")
            pcm_chunks.append(frames)

            # この文の実尺を字幕単位へ文字数比で配分（端数は最後で吸収）。
            units = _split_caption_units(sentence, max_chars)
            total_chars = sum(len(u) for u in units) or 1
            u_start = current
            for j, u in enumerate(units):
                if j == len(units) - 1:
                    u_end = current + duration
                else:
                    u_end = u_start + duration * (len(u) / total_chars)
                captions.append({"text": u, "start": round(u_start, 3), "end": round(u_end, 3)})
                u_start = u_end
            current += duration

        turns_out.append({
            "start": round(turn_start, 3),
            "end": round(current, 3),
            "sentences": captions,
        })

        # ターン間の無音（任意）。最後のターン後には付けない。
        if pause > 0 and idx < len(script) - 1:
            channels, width, rate = ref_params
            pcm_chunks.append(b"\x00" * (int(pause * rate) * width * channels))
            current += pause

    return b"".join(pcm_chunks), turns_out, ref_params


def generate_audio(script, config, output_path):
    """
    掛け合い音声をmp3として保存し、ターン情報（start/end/sentences）を返す。
    Returns: [{"start","end","sentences":[{"text","start","end"}...]}...]
    """
    logger.info(f"VOICEVOXで音声生成中（{len(script)}ターン）...")
    pcm, turns, params = synthesize_dialogue(script, config)
    channels, width, rate = params

    wav_path = output_path.replace(".mp3", ".wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm)

    import subprocess

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", output_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg変換失敗: {result.stderr}")

    os.remove(wav_path)
    total = turns[-1]["end"] if turns else 0.0
    logger.info(f"MP3保存完了: {output_path}（合計{total:.1f}秒・{len(script)}ターン）")
    return turns
