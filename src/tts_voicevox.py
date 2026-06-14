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


# 句末の区切り（！？。、と閉じ括弧/引用符）。感嘆処理の境界判定に使う。
_CLAUSE_END = r"[！!？?。、」』）)]"
# 語末の促音「っ/ッ」（句末の直前）。VOICEVOXは声を切る“囁き”に読むので落とす（例「えぇーっ！？」）。
_TRAILING_SOKUON_RE = re.compile(rf"[っッ](?={_CLAUSE_END}|$)")
# 感嘆の「へぇ」系（へぇ/へえ/へぇー/へえー…）。小さい「ぇ」は弱く読まれ平板になるので、
# 音声では正規形に揃える。_HEE_CANON は耳で調整できるよう定数化（例「へえ〜」「へー」）。
_HEE_CANON = "へえ〜"
_INTERJECTION_HEE_RE = re.compile(rf"へ[ぇえ][ーぇえ〜]*(?={_CLAUSE_END}|$)")
# 驚きの「ええ」系（ええー/えぇー/えええー…）。語尾無声化＝囁き化するので正規形に揃える。
# 短い「え」「えー」(相槌/フィラー)や「ええ」(肯定)は触らず、伸ばした驚き形だけが対象。
_EE_CANON = "ええ〜"
_INTERJECTION_EE_RE = re.compile(rf"え[ぇえ][ーぇえ〜]+(?={_CLAUSE_END}|$)")


# 英字→カタカナ読み辞書（IT用語前提）。台本に（読み）が無い英字を音声で正しく読ませる。
# 字幕は英字のまま（_spoken_text＝音声専用）。config/readings.json で上書き/追記できる。
_DEFAULT_READINGS = {
    "hi-fi": "ハイファイ", "hifi": "ハイファイ", "wi-fi": "ワイファイ", "wifi": "ワイファイ",
    "fidelity": "フィデリティ",
    "wireless": "ワイヤレス", "bluetooth": "ブルートゥース", "captcha": "キャプチャ",
    "jpeg": "ジェイペグ", "mpeg": "エムペグ", "gif": "ジフ", "png": "ピング",
    "http": "エイチティーティーピー", "https": "エイチティーティーピーエス",
    "url": "ユーアールエル", "api": "エーピーアイ", "html": "エイチティーエムエル",
    "css": "シーエスエス", "sql": "エスキューエル", "usb": "ユーエスビー",
    "cpu": "シーピーユー", "gpu": "ジーピーユー", "ram": "ラム", "rom": "ロム",
    "ssd": "エスエスディー", "hdd": "エイチディーディー", "os": "オーエス",
    "ai": "エーアイ", "iot": "アイオーティー", "ok": "オーケー",
}


def _load_readings():
    """組み込み辞書に config/readings.json をマージ（あれば上書き/追記）。キーは小文字化。"""
    merged = {k.lower(): v for k, v in _DEFAULT_READINGS.items()}
    path = os.path.join("config", "readings.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for k, v in (json.load(f) or {}).items():
                    if isinstance(k, str) and not k.startswith("_") and isinstance(v, str) and v:
                        merged[k.lower()] = v
        except (OSError, ValueError) as e:
            logger.warning(f"config/readings.json 読込失敗（組み込み辞書のみ使用）: {e}")
    return merged


_READINGS = _load_readings()
# 長いキー優先（httpsをhttpより先に）。語境界つきで英字語のみ置換。
_READINGS_RE = (
    re.compile(
        r"(?<![A-Za-z0-9])(" +
        "|".join(re.escape(k) for k in sorted(_READINGS, key=len, reverse=True)) +
        r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    if _READINGS else None
)


def _apply_readings(text):
    """英字のIT用語をカタカナ読みへ（音声専用）。辞書に無い語はそのまま。"""
    if not _READINGS_RE:
        return text
    return _READINGS_RE.sub(lambda m: _READINGS[m.group(1).lower()], text)


def _spoken_text(text):
    """音声合成用にテキストを整える（字幕には使わない）。

    感嘆詞がVOICEVOXで弱く/平板に読まれるのを補正し、英字を辞書でカタカナ読みにする:
    - 「英字（かな）」の読み仮名はかなだけ残す＝二重読み防止。
    - 残った英字IT用語は辞書でカタカナ読みへ（例 Hi-Fi→ハイファイ。台本に読みが無くても救う）。
    - 語末の促音「っ」（！？。」等の直前）を落とす＝“囁き”化を防ぐ。
    - 感嘆の「へぇ/ええ」系は正規形(へえ〜/ええ〜)に＝平板/無声化を防ぐ。
    """
    t = _READING_GLOSS_RE.sub(lambda m: m.group(1), text)  # 台本の（かな）読みを優先採用
    t = _apply_readings(t)                          # 残った英字IT用語を辞書でカタカナ化
    t = _TRAILING_SOKUON_RE.sub("", t)            # 語末促音を落とす
    t = _INTERJECTION_HEE_RE.sub(_HEE_CANON, t)   # 「へぇ」系を正規形へ
    t = _INTERJECTION_EE_RE.sub(_EE_CANON, t)     # 「ええ」系(驚き)を正規形へ
    return t


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


def revoice_if_all_unvoiced(query, pitch=None, pitch_provider=None, fallback=5.8):
    """全母音が無声化されている query を有声化する（in-place）。

    「ええ」「ふふ」等の短い相づち/笑いは VOICEVOX が全モーラを無声化(pitch=0)し、
    囁き声になってしまう。utterance 全体が無声のときだけ有声ピッチを与えて戻す。
    通常の文は有声モーラを含むので対象外＝自然な無声化（「です」等）はそのまま保つ。

    pitch=固定値 / pitch_provider=遅延取得の関数（全無声と判明した時だけ呼ぶ＝
    話者の自然な高さを実測する用。通常発話では呼ばれず無駄な問い合わせをしない）。
    Returns: 有声化した場合 True。
    """
    moras = []
    for ap in query.get("accent_phrases", []):
        moras.extend(ap.get("moras") or [])
    vowels = [m for m in moras if m.get("vowel") and m.get("vowel") != "pau"]
    if not vowels:
        return False
    if any((m.get("pitch") or 0) > 0 for m in vowels):
        return False  # 有声モーラがある＝通常発話。無声化は自然なので触らない
    if pitch is None:
        pitch = pitch_provider() if pitch_provider else fallback
    for m in vowels:
        m["pitch"] = pitch
        v = m.get("vowel")
        if isinstance(v, str):  # 無声母音は大文字表記の場合がある→小文字へ戻し有声扱いに
            m["vowel"] = v.lower()
    return True


def replace_interjection(text, mapping):
    """文が丸ごと相づち/笑い（mappingのキー）に一致する時だけ別語へ置換（純関数）。

    「ふふ」「ええ」等は VOICEVOX が全無声化して囁きになるため、有声で読まれる語に差し替える。
    文末の記号(。！？〜ー…等)は保持し、核が完全一致した時のみ置換＝文中の語は触らない（誤爆防止）。
    """
    if not mapping or not text:
        return text
    m = re.match(r"^(.*?)([。、，,．.！!？?〜～ー…・\s]*)$", text)
    core, tail = m.group(1), m.group(2)
    if core in mapping:
        return mapping[core] + tail
    return text


def _reference_pitch(base_url, speaker, cache, fallback=5.8):
    """話者の自然な声の高さ（有声モーラの平均 pitch）を1度だけ実測しキャッシュ。

    全無声の相づちを有声化する基準に使う＝話者ごとに自動で高さが合う（固定値の推測を避ける）。
    """
    if speaker in cache:
        return cache[speaker]
    val = fallback
    try:
        q = audio_query(base_url, "アー", speaker)  # 確実に有声な参照音
        ps = [m["pitch"] for ap in q.get("accent_phrases", [])
              for m in (ap.get("moras") or []) if (m.get("pitch") or 0) > 0]
        if ps:
            val = sum(ps) / len(ps)
    except Exception:  # noqa: BLE001 - 取得失敗時は fallback
        pass
    cache[speaker] = val
    return val


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


def _mix_pcm(frames_list, width):
    """複数の16bit PCM（モノ）を重ねて1本に混ぜる（ユニゾン＝二人同時発話用）。

    長さは最長に合わせ（短い方は無音で埋め）、加算後に16bit範囲へクランプ。
    16bit以外（width≠2）や1本のみのときは先頭をそのまま返す（安全側）。
    """
    frames_list = [f for f in frames_list if f]
    if not frames_list:
        return b""
    if width != 2 or len(frames_list) == 1:
        return frames_list[0]
    import array

    arrs = []
    for f in frames_list:
        a = array.array("h")
        a.frombytes(f)
        arrs.append(a)
    n = max(len(a) for a in arrs)
    out = array.array("h", bytes(2 * n))  # 無音(0)で初期化
    for a in arrs:
        for i in range(len(a)):
            s = out[i] + a[i]
            out[i] = 32767 if s > 32767 else -32768 if s < -32768 else s
    return out.tobytes()


def _resolve_speaker_id(turn_speaker, speakers_map):
    if turn_speaker not in speakers_map:
        raise KeyError(
            f"話者 '{turn_speaker}' のVOICEVOX話者IDが config.tts_voicevox.speakers にありません "
            f"（既知: {list(speakers_map)}）"
        )
    return speakers_map[turn_speaker]


def _resolve_voice_params(vc, speaker):
    """全体デフォルト＋話者別上書きで (speed, pitch, intonation, volume) を決める。"""
    params = {
        "speed": float(vc.get("speed", 1.0)),
        "pitch": float(vc.get("pitch", 0.0)),
        "intonation": float(vc.get("intonation", 1.0)),
        "volume": float(vc.get("volume", 1.0)),
    }
    override = (vc.get("voice_params") or {}).get(speaker, {})
    for k in params:
        if k in override:
            params[k] = float(override[k])
    return params


def _effective_voice(vp, turn):
    """話者の声パラメータに、台詞ごとの turn["voice"] 上書きを重ねる（per-turnの声演技）。"""
    ev = dict(vp)
    over = turn.get("voice")
    if isinstance(over, dict):
        for k in ev:
            if k in over and over[k] is not None:
                ev[k] = float(over[k])
    return ev


def _split_sentences(text):
    """テキストを文末記号で文に分割（記号は残す・空要素除去）。"""
    return [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]


def _split_chorus_chunks(text):
    """ユニゾンの再同期点で分割（読点/カンマの直後で切る）。各チャンクで二人の頭を揃え直す。

    区切り文字は前側のチャンクに残す（、の自然な間を保つ）。区切りが無ければ全体を1チャンク。
    例「それじゃあ、また見てね」→ ["それじゃあ、", "また見てね"]（2チャンクで2回同期）。
    """
    parts = [p for p in (s.strip() for s in re.split(r"(?<=[、，,])", text)) if p]
    return parts or [text]


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
    # ネタ(章)が切り替わる境目に足す追加の間。0.3前後でネタの区切りが立ち、テンポの単調さが和らぐ。
    chapter_gap = float(vc.get("chapter_gap_pause", 0.0))
    max_chars = int(vc.get("caption_max_chars", DEFAULT_CAPTION_MAX_CHARS))
    # 文ごとに個別合成するため、文の境目に VOICEVOX の前後無音(pre+post)が毎回入る。
    # pre を 0 にして「文の先頭の無音」を消す＝複数文ターン(長い説明)のカクつきを抑える。
    # 値は config で耳調整可（pre=文先頭の無音 / post=文末の余韻＝文間の間）。
    pre_phoneme = float(vc.get("pre_phoneme_length", 0.0))
    post_phoneme = float(vc.get("post_phoneme_length", 0.1))
    # 動画開始から声が始まるまでの無音（先頭リードイン）。digest.mp3 の頭に足し、
    # 全発話/字幕の時刻もこの分ずらす。最初の画像は0秒から出る（topics[0].startは0のまま）。
    lead_in = float(vc.get("lead_in_silence", 0.0))
    # 全無声化で囁きになる相づち/笑いを有声な語へ置換（config.tts_voicevox.interjection_replace）。
    interjection_replace = vc.get("interjection_replace") or {}

    if not script:
        raise ValueError("空の台本です")

    pcm_chunks = []
    turns_out = []
    current = lead_in   # 先頭リードイン分だけ全発話を後ろへずらす
    ref_params = None
    ref_pitch_cache = {}  # 話者→自然な声の高さ（全無声の相づちを有声化する基準・実測キャッシュ）

    chorus_names = list(speakers_map.keys())  # ユニゾン時に声を重ねる話者（設定の全キャラ）

    for idx, turn in enumerate(script):
        speaker = turn["speaker"]
        speaker_id = _resolve_speaker_id(speaker, speakers_map)
        vp = _effective_voice(_resolve_voice_params(vc, speaker), turn)  # 話者＋台詞ごとの声上書き
        # chorus=True のターンは設定の全話者で同じ文を合成して重ねる（二人同時発話＝締めの挨拶等）。
        # ユニゾンが揃って聞こえるよう、話者ごとに違う speed/intonation を共通値に統一する
        # （声の個性は pitch で残す）。speed が違うとテンポがズレて二人がバラバラに聞こえるため。
        if turn.get("chorus"):
            uni_speed = float(vc.get("chorus_speed", vc.get("speed", 1.0)))
            uni_into = float(vc.get("chorus_intonation", vc.get("intonation", 1.0)))
            voices = []
            for n in chorus_names:
                cvp = _effective_voice(_resolve_voice_params(vc, n), turn)
                cvp = {**cvp, "speed": uni_speed, "intonation": uni_into}  # テンポ/抑揚を揃える
                voices.append((_resolve_speaker_id(n, speakers_map), cvp))
        else:
            voices = [(speaker_id, vp)]
        turn_start = current
        captions = []

        for sentence in _split_sentences(turn["text"]):
            # 囁きになる相づち/笑いは有声な語へ差し替え（字幕＝この置換後テキストに揃う）。
            sentence = replace_interjection(sentence, interjection_replace)
            # ユニゾンは読点(、)でさらに細かく分け、チャンクごとに二人の頭を揃え直す
            # （1フレーズ丸ごと混ぜると途中でズレが蓄積するため・字幕は文単位のまま）。
            chunks = (_split_chorus_chunks(sentence)
                      if turn.get("chorus") and len(voices) > 1 else [sentence])
            sent_frames = []
            duration = 0.0
            for chunk in chunks:
                # 音声に渡すのは読み仮名を畳んだテキスト（字幕＝sentenceは原文のまま）。
                spoken = _spoken_text(chunk)
                outs = []
                for sid, vparams in voices:
                    query = audio_query(base_url, spoken, sid)
                    # 「ええ」「ふふ」等が全無声＝囁きになるのを防ぐ。基準は話者の実測高さ（全無声時のみ取得）。
                    revoice_if_all_unvoiced(
                        query, pitch_provider=lambda: _reference_pitch(base_url, sid, ref_pitch_cache))
                    query["speedScale"] = vparams["speed"]
                    query["pitchScale"] = vparams["pitch"]
                    query["intonationScale"] = vparams["intonation"]
                    query["volumeScale"] = vparams["volume"]
                    query["prePhonemeLength"] = pre_phoneme    # 文先頭の無音（境目のカクつき低減）
                    query["postPhonemeLength"] = post_phoneme   # 文末の余韻（文間の自然な間）
                    wav_bytes = synthesis(base_url, query, sid)
                    outs.append(_wav_params_and_frames(wav_bytes))

                params = outs[0][0]
                if ref_params is None:
                    ref_params = params
                elif params != ref_params:
                    raise ValueError(f"WAV形式が不一致: {params} != {ref_params}（話者で出力形式が違う？）")
                # 単独はそのまま、chorusは重ねて混ぜる（チャンクごとに最長へ合わせ＝頭を揃える）。
                sent_frames.append(_mix_pcm([o[1] for o in outs], params[1]))
                duration += max(o[2] for o in outs)
            frames = b"".join(sent_frames)
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

        # ターン間の無音＝全体設定(inter_turn_pause)＋この台詞の pause（任意の間）。最後の後には付けない。
        sil = pause + float(turn.get("pause", 0) or 0)
        # 次のターンが別の章（＝次のネタ）に変わるなら、境目に追加の間を足してネタの区切りを立てる。
        if idx < len(script) - 1 and script[idx + 1].get("chapter") != turn.get("chapter"):
            sil += chapter_gap
        if sil > 0 and idx < len(script) - 1:
            channels, width, rate = ref_params
            pcm_chunks.append(b"\x00" * (int(sil * rate) * width * channels))
            current += sil

    audio = b"".join(pcm_chunks)
    # 先頭リードインの無音を digest の頭に付ける（turns_out は current=lead_in 起点で既にずれている）。
    if lead_in > 0 and ref_params:
        channels, width, rate = ref_params
        audio = b"\x00" * (int(lead_in * rate) * width * channels) + audio
    return audio, turns_out, ref_params


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
