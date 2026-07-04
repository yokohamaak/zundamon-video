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

# 3. 締めCTA/ユニゾン等のパイプライン固定ターンは比較から除外（手編集しても再生成で上書き＝永久audio-staleを防ぐ）
#    meta側=固定CTA、script側=手編集（高評価を含む別文）でも audio-stale にしない。
meta_close = base + [{"speaker": "A", "text": "次のテーマはコメントで。高評価も嬉しいわ。", "chapter": 0, "section": "outro", "closing": True}]
edit_close = base + [{"speaker": "A", "text": "この話どう思った？高評価とコメントで教えてね。", "chapter": 0, "section": "outro", "closing": True}]
t("締めターンの差は音声影響にしない(False)", rs.audio_affecting_changed(meta_close, edit_close) is False)
t("締めターンを除けば本編一致でFalse", rs.audio_affecting_changed(meta_close, base + [{"speaker": "B", "text": "ばいばい", "chapter": 0, "chorus": True}]) is False)
t("本編の差は締め除外後も検出(True)", rs.audio_affecting_changed(meta_close, [base[0], {**base[1], "text": "ちがう"}, edit_close[2]]) is True)
from main_story import is_managed_closing
t("is_managed_closing: closingフラグ", is_managed_closing({"closing": True}) is True)
t("is_managed_closing: outroの高評価CTA", is_managed_closing({"section": "outro", "text": "高評価よろしく"}) is True)
t("is_managed_closing: 通常本編はFalse", is_managed_closing({"section": "trivia", "text": "高評価"}) is False)

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

# 4. compute_preview_state（再読込時の初期状態・mtime方式）
def _m(p, v):
    os.utime(p, (v, v))

d2 = tempfile.mkdtemp()
mp = os.path.join(d2, "meta.json"); sp = os.path.join(d2, "script.json")
rj = os.path.join(d2, "review.json"); img = os.path.join(d2, "ch_00_00.jpg")
json.dump({"script": [{"speaker": "A", "text": "あ", "chapter": 0, "start": 0, "end": 1, "sentences": []}]}, open(mp, "w"))
json.dump({"script": [{"speaker": "A", "text": "あ", "chapter": 0}]}, open(sp, "w"))
_m(mp, 2000)

_m(sp, 1000)
t("全てmetaより古い→synced", rs.compute_preview_state(d2) == "synced")
_m(sp, 3000)
t("script.json更新(演出/chapters.vizList等)→visual-stale", rs.compute_preview_state(d2) == "visual-stale")
_m(sp, 1000); json.dump({"cuts": []}, open(rj, "w")); _m(rj, 3000)
t("review.json更新(クロップ/画像設定)→visual-stale", rs.compute_preview_state(d2) == "visual-stale")
_m(rj, 1000); open(img, "w").write("x"); _m(img, 3000)
t("画像差し替え→visual-stale", rs.compute_preview_state(d2) == "visual-stale")
_m(img, 1000)
t("全て再びmetaより古い→synced", rs.compute_preview_state(d2) == "synced")
# 音声差は mtime より優先（古くても audio-stale）
json.dump({"script": [{"speaker": "A", "text": "ちがう", "chapter": 0}]}, open(sp, "w")); _m(sp, 1000)
t("音声差→audio-stale(mtime優先)", rs.compute_preview_state(d2) == "audio-stale")
t("meta無し→synced", rs.compute_preview_state(tempfile.mkdtemp()) == "synced")

print("ALL PASS (%d)" % passed)
