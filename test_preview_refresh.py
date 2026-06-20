"""preview-refresh の音声影響判定と meta-only 失敗時復元のテスト（VOICEVOX/main_story不要）。"""
import json
import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_server as rs

passed = 0
def t(name, cond):
    global passed
    assert cond, "FAIL: " + name
    passed += 1
    print("  " + name + " OK")

base = [{"speaker": "A", "text": "あいう", "chapter": 0},
        {"speaker": "B", "text": "かきく", "chapter": 0}]

# 1. 演出/vizPoints/textEffectsだけの変更は更新可能（False）
viz = [{"speaker": "A", "text": "あいう", "chapter": 0, "vizPoints": [{"type": "reveal", "pos": 1}],
        "textEffects": [{"type": "emphasis", "start": 0, "end": 1}]},
       {"speaker": "B", "text": "かきく", "chapter": 0, "panel_item": 0}]
t("演出だけの変更は更新可能(False)", rs.audio_affecting_changed(base, viz) is False)

# 2. 音声に影響する変更は True
t("text変更→再生成(True)", rs.audio_affecting_changed(base, [{"speaker": "A", "text": "ちがう", "chapter": 0}, base[1]]) is True)
t("speaker変更→再生成(True)", rs.audio_affecting_changed(base, [{"speaker": "X", "text": "あいう", "chapter": 0}, base[1]]) is True)
t("voice変更→再生成(True)", rs.audio_affecting_changed(base, [{**base[0], "voice": {"speed": 1.2}}, base[1]]) is True)
t("pause変更→再生成(True)", rs.audio_affecting_changed(base, [{**base[0], "pause": 1.0}, base[1]]) is True)
t("chapter変更→再生成(True)", rs.audio_affecting_changed(base, [{**base[0], "chapter": 1}, base[1]]) is True)
t("chorus変更→再生成(True)", rs.audio_affecting_changed(base, [{**base[0], "chorus": True}, base[1]]) is True)
t("ターン数変更→再生成(True)", rs.audio_affecting_changed(base, [base[0]]) is True)

# 3. meta-only失敗時に旧meta.jsonが維持される（subprocessをモックして失敗させる）
d = tempfile.mkdtemp()
old_dir = rs.DIR
try:
    rs.DIR = d
    meta = {"title": "t", "script": [{"speaker": "A", "text": "あ", "chapter": 0, "start": 0, "end": 1, "sentences": []}]}
    meta_text = json.dumps(meta, ensure_ascii=False, indent=2)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        f.write(meta_text)
    # 同じ音声内容（演出だけ追加）＝meta-onlyへ進む。subprocessを失敗させる。
    body = {"script": [{"speaker": "A", "text": "あ", "chapter": 0, "vizPoints": [{"type": "reveal", "pos": 0}]}]}
    with mock.patch.object(rs.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="boom", stdout="")):
        r = rs.do_preview_refresh(body)
    t("meta-only失敗でok=False", r.get("ok") is False)
    t("失敗時meta.jsonが元のまま維持", json.load(open(os.path.join(d, "meta.json"), encoding="utf-8")) == meta)
    t("失敗時もscript.jsonは保存される", os.path.exists(os.path.join(d, "script.json")))
finally:
    rs.DIR = old_dir

print("ALL PASS (%d)" % passed)
