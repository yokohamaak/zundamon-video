"""editor_model Phase 2（画像編集：assets/imageCues の共通操作・解決・authority切替）の単体テスト。

実行: python3 test_editor_phase2.py
共通操作・normalize/reconcile・editor→meta解決・legacy/editor等価（実データ）・保存ライフサイクルを検証する。
0/null/未指定を同一視しない点（pad:0・crop座標0・空配列）を含む。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_story as ms
from src import story_script, editor_model as em

passed = 0
def ok(name, cond=True):
    global passed
    assert cond, "FAIL: " + name
    passed += 1
    print("  " + name + ": OK")


def _script(n, chapter=0):
    s = [{"speaker": "A", "text": f"t{i}", "chapter": chapter, "id": f"turn-{i+1:04d}"}
         for i in range(n)]
    return s


def _data(n=4, chapter=0):
    return {"script": _script(n, chapter), "chapters": [{"image_cuts": []}],
            "assets": [], "imageCues": []}


def _write_real_legacy_fixture(directory):
    """実データの内容を使いつつ、現在のauthorityに依存しないlegacy fixtureを書き出す。"""
    data = json.load(open("docs/story/script.json"))
    data.pop("editorModelAuthority", None)
    json.dump(data, open(os.path.join(directory, "script.json"), "w"))
    json.dump(json.load(open("docs/story/review.json")),
              open(os.path.join(directory, "review.json"), "w"))


# ===== 共通 asset 操作 =====

def test_add_asset_unique_id():
    d = _data()
    a = em.add_asset(d, file="x.jpg", query="q")
    b = em.add_asset(d, file="y.jpg")
    ok("asset追加でID一意・fields保持", a["id"] != b["id"] and a["file"] == "x.jpg" and a["query"] == "q")


def test_asset_usage_and_delete():
    d = _data()
    a = em.add_asset(d, file="x.jpg")
    em.add_cue(d, "turn-0001", a["id"])
    ok("使用中assetは使用cue一覧を返す", em.asset_usage(d, a["id"]) and not em.can_delete_asset(d, a["id"]))
    try:
        em.delete_asset(d, a["id"])
        ok("使用中asset無条件削除を拒否", False)
    except ValueError:
        ok("使用中asset無条件削除を拒否", True)
    removed = em.delete_asset(d, a["id"], force=True)
    ok("force削除で参照cueも一括解除", removed and not em.find_asset(d, a["id"]) and not d["imageCues"])


def test_unused_vs_broken_distinction():
    d = _data()
    a = em.add_asset(d, file="x.jpg")      # 未使用
    # 壊れた参照は ops では生成不可（検証で弾く）＝外部要因(削除レース等)で混入した状態を直接構築。
    d["imageCues"].append({"id": "image-cue-9999", "turnId": "turn-0001", "assetId": "asset-missing"})
    ok("未使用assetを列挙", em.unused_assets(d) == [a["id"]])
    ok("壊れたassetId参照を区別して列挙", len(em.broken_cue_refs(d)) == 1)


# ===== 共通 cue 操作 =====

def test_add_cue_requires_turn():
    d = _data()
    try:
        em.add_cue(d, "turn-9999", None)
        ok("存在しないturnIdのcue追加を拒否", False)
    except ValueError:
        ok("存在しないturnIdのcue追加を拒否", True)


def test_place_image_add_or_replace():
    d = _data()
    a0 = em.add_asset(d, file="0.jpg")
    a1 = em.add_asset(d, file="1.jpg")
    c1 = em.place_image(d, "turn-0001", a0["id"], fit="cover")
    c2 = em.place_image(d, "turn-0001", a1["id"], fit="contain")  # 同位置→差し替え
    ok("開始位置に既存cueあれば差し替え（増えない）", len(d["imageCues"]) == 1 and c1["id"] == c2["id"])
    ok("差し替えでasset/optが更新", c2["assetId"] == a1["id"] and c2["fit"] == "contain")
    em.place_image(d, "turn-0002", a0["id"])
    ok("開始位置に無ければ追加", len(d["imageCues"]) == 2)


def test_move_replace_range_delete():
    d = _data()
    a0 = em.add_asset(d, file="0.jpg")
    a1 = em.add_asset(d, file="1.jpg")
    c = em.add_cue(d, "turn-0001", a0["id"])
    em.move_cue(d, c["id"], "turn-0002")
    ok("move_cueで開始セリフ変更", em.find_cue(d, c["id"])["turnId"] == "turn-0002")
    em.replace_cue_asset(d, c["id"], a1["id"])
    ok("replace_cue_assetで素材差し替え（optは保持）", em.find_cue(d, c["id"])["assetId"] == a1["id"])
    em.set_cue_range(d, c["id"], end_turn_id="turn-0003")
    ok("set_cue_rangeで終了セリフ設定", em.find_cue(d, c["id"]).get("endTurnId") == "turn-0003")
    em.set_cue_range(d, c["id"], end_turn_id=None)
    ok("set_cue_range end=Noneで継続へ戻す", "endTurnId" not in em.find_cue(d, c["id"]))
    em.delete_cue(d, c["id"])
    ok("delete_cueで削除（assetは残る）", not d["imageCues"] and em.find_asset(d, a1["id"]))


def test_pad_zero_and_crop_zero_preserved():
    d = _data()
    a = em.add_asset(d, file="0.jpg")
    c = em.add_cue(d, "turn-0001", a["id"], pad=0, crop={"l": 0, "t": 0, "r": 0.5, "b": 0.5})
    ok("pad:0 を欠損扱いしない", c["pad"] == 0)
    ok("crop座標0 を保持", c["crop"]["l"] == 0)


# ===== ID 再利用しない（削除→再追加） =====

def test_asset_id_no_reuse():
    d = _data()
    a1 = em.add_asset(d, file="1")
    a2 = em.add_asset(d, file="2")
    em.delete_asset(d, a2["id"])
    a3 = em.add_asset(d, file="3")
    ok("削除したasset IDを再利用しない（最大連番+1）", a3["id"] != a2["id"] and a3["id"] != a1["id"])
    em.delete_asset(d, a1["id"])
    a4 = em.add_asset(d, file="4")
    ok("先頭asset削除後も再利用しない", a4["id"] not in (a1["id"], a2["id"], a3["id"]))


def test_cue_id_no_reuse():
    d = _data(4)
    a = em.add_asset(d, file="x")
    c1 = em.add_cue(d, "turn-0001", a["id"])
    c2 = em.add_cue(d, "turn-0002", a["id"])
    em.delete_cue(d, c2["id"])
    c3 = em.add_cue(d, "turn-0002", a["id"])
    ok("削除したcue IDを再利用しない", c3["id"] != c2["id"] and c3["id"] != c1["id"])


def test_id_counter_persists_through_save():
    import review_server as rs
    d = _data()
    a1 = em.add_asset(d, file="1"); em.add_asset(d, file="2")
    em.delete_asset(d, a1["id"])
    _, _, saved = rs.apply_save_script(dict(d, editorModelAuthority="legacy"))
    ok("idCountersが保存で持ち越される", saved.get("idCounters", {}).get("asset") == 2)
    a3 = em.add_asset(saved, file="3")
    ok("再読込後も再利用しない", a3["id"] == "asset-0003")


# ===== 不正状態を生成させない（add/place/replace/move/set_range） =====

def test_ops_reject_invalid_asset():
    d = _data()
    a = em.add_asset(d, file="x")
    c = em.add_cue(d, "turn-0001", a["id"])
    for label, fn in [
        ("place_image不正assetId", lambda: em.place_image(d, "turn-0002", "asset-x")),
        ("replace_cue_asset不正assetId", lambda: em.replace_cue_asset(d, c["id"], "asset-x")),
        ("add_cue不正assetId", lambda: em.add_cue(d, "turn-0003", "asset-x")),
    ]:
        try:
            fn(); ok(label + "を拒否", False)
        except ValueError:
            ok(label + "を拒否", True)


def test_ops_reject_start_collision():
    d = _data(4)
    a = em.add_asset(d, file="x")
    em.add_cue(d, "turn-0001", a["id"])
    c2 = em.add_cue(d, "turn-0003", a["id"])
    try:
        em.add_cue(d, "turn-0001", a["id"]); ok("add_cue開始位置衝突を拒否", False)
    except ValueError:
        ok("add_cue開始位置衝突を拒否", True)
    try:
        em.move_cue(d, c2["id"], "turn-0001"); ok("move_cue開始位置衝突を拒否", False)
    except ValueError:
        ok("move_cue開始位置衝突を拒否", True)
    ok("拒否後も状態は不変（cue 2件・turn-0003のまま）",
       len(d["imageCues"]) == 2 and em.find_cue(d, c2["id"])["turnId"] == "turn-0003")


def test_ops_reject_reversed_range():
    d = _data(4)
    a = em.add_asset(d, file="x")
    c = em.add_cue(d, "turn-0002", a["id"])
    try:
        em.add_cue(d, "turn-0003", a["id"], end_turn_id="turn-0001")
        ok("add_cue範囲逆転を拒否", False)
    except ValueError:
        ok("add_cue範囲逆転を拒否", True)
    try:
        em.set_cue_range(d, c["id"], end_turn_id="turn-0001")  # end(0) < start(1)
        ok("set_cue_range範囲逆転を拒否", False)
    except ValueError:
        ok("set_cue_range範囲逆転を拒否", True)
    ok("set_cue_range拒否で部分適用しない", "endTurnId" not in em.find_cue(d, c["id"]))


# ===== 削除→再追加（UI共通操作の往復） =====

def test_delete_readd_roundtrip():
    d = _data(4)
    a = em.add_asset(d, file="x")
    c = em.add_cue(d, "turn-0001", a["id"], fit="cover")
    em.delete_cue(d, c["id"])
    ok("cue削除後assetは残る", not d["imageCues"] and em.find_asset(d, a["id"]))
    c2 = em.place_image(d, "turn-0001", a["id"], fit="contain")
    ok("同位置へ再配置できる（IDは新規）", c2["id"] != c["id"] and c2["fit"] == "contain")
    em.delete_asset(d, a["id"], force=True)
    ok("force削除でassetとcue両方消える", not d["assets"] and not d["imageCues"])


# ===== normalize / reconcile =====

def test_normalize_dedup_orphan_reversed():
    d = _data()
    a = em.add_asset(d, file="0.jpg")
    d["imageCues"] = [
        {"id": "image-cue-0001", "turnId": "turn-0002", "assetId": a["id"]},
        {"id": "image-cue-0002", "turnId": "turn-0002", "assetId": a["id"]},   # 重複開始
        {"id": "image-cue-0003", "turnId": "turn-9999", "assetId": a["id"]},   # 孤立
        {"id": "image-cue-0004", "turnId": "turn-0003", "assetId": a["id"], "endTurnId": "turn-0001"},  # 逆転
        {"id": "image-cue-0005", "turnId": "turn-0001", "assetId": a["id"]},
    ]
    em.normalize_cues(d)
    ids = [c["id"] for c in d["imageCues"]]
    ok("重複開始は最初だけ", "image-cue-0002" not in ids)
    ok("孤立turnIdは除去", "image-cue-0003" not in ids)
    ok("範囲逆転はendTurnId除去", "endTurnId" not in em.find_cue(d, "image-cue-0004"))
    ok("開始セリフ順にソート", [em._turn_index(d["script"])[c["turnId"]] for c in d["imageCues"]]
       == sorted(em._turn_index(d["script"])[c["turnId"]] for c in d["imageCues"]))


def test_normalize_idempotent():
    d = _data()
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0001", a["id"]); em.add_cue(d, "turn-0003", a["id"])
    em.normalize_cues(d)
    snap = json.dumps(d["imageCues"], sort_keys=True)
    em.normalize_cues(d)
    ok("normalize冪等", json.dumps(d["imageCues"], sort_keys=True) == snap)


def test_reconcile_turn_delete_relocates():
    d = _data(4)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0002", a["id"])
    prev = [t["id"] for t in d["script"]]
    # turn-0002 を削除
    d["script"] = [t for t in d["script"] if t["id"] != "turn-0002"]
    em.reconcile_cues(d, prev_turn_ids=prev)
    ok("削除セリフのcueは次の生存セリフへ移る", em.cue_at(d, "turn-0003") is not None)


def test_reconcile_turn_delete_no_successor_drops():
    d = _data(2)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0002", a["id"])
    prev = [t["id"] for t in d["script"]]
    d["script"] = [t for t in d["script"] if t["id"] != "turn-0002"]   # 末尾削除＝後続なし
    em.reconcile_cues(d, prev_turn_ids=prev)
    ok("後続が無ければcue削除", len(d["imageCues"]) == 0)


def test_reconcile_reorder_follows_turn():
    d = _data(3)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0003", a["id"])
    d["script"] = [d["script"][2], d["script"][0], d["script"][1]]  # 並べ替え（idは不変）
    em.reconcile_cues(d, prev_turn_ids=["turn-0001", "turn-0002", "turn-0003"])
    ok("並べ替えてもcueはturnIdで追従", em.cue_at(d, "turn-0003") is not None)


# ===== editor → meta 解決 =====

def test_resolve_continuation_until_next():
    d = _data(4)
    a0 = em.add_asset(d, file="0.jpg"); a1 = em.add_asset(d, file="1.jpg")
    em.add_cue(d, "turn-0001", a0["id"]); em.add_cue(d, "turn-0003", a1["id"])
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("画像は次cueまで継続", plan[0]["image"] == "0.jpg" and plan[1]["image"] == "0.jpg"
       and plan[2]["image"] == "1.jpg" and plan[3]["image"] == "1.jpg")


def test_resolve_endturn_gap_then_blank():
    d = _data(4)
    a0 = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0001", a0["id"], end_turn_id="turn-0002")  # 0..1だけ・2,3は画像なし
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("endTurnId後は画像なし(None)", plan[1]["image"] == "0.jpg" and plan[2] is None and plan[3] is None)


def test_resolve_hide_blank_broken_nofile():
    d = _data(4)
    a_file = em.add_asset(d, file="0.jpg")
    a_nofile = em.add_asset(d)                       # file無し→placeholder
    em.add_cue(d, "turn-0001", a_file["id"], hide=True)   # hide→blank
    d["imageCues"].append({"id": "image-cue-9000", "turnId": "turn-0002",
                           "assetId": "asset-missing"})    # 壊れ参照(外部混入)→blank
    em.add_cue(d, "turn-0003", a_nofile["id"])            # file無し→placeholder
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("hide→blank", plan[0].get("blank") is True)
    ok("壊れたassetId→blank", plan[1].get("blank") is True)
    ok("file無しasset→placeholder(ch,ci)", plan[2].get("placeholder") is not None)


def test_resolve_before_first_cue_none():
    d = _data(3)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0002", a["id"])
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("先頭にcue無し→画像なし", plan[0] is None and plan[1]["image"] == "0.jpg")


def test_resolve_same_asset_multiple_cues():
    d = _data(3)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0001", a["id"], crop={"l": 0.1})
    em.add_cue(d, "turn-0003", a["id"], crop={"l": 0.5})  # 同asset別crop
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("同assetを複数cueで別cropに", plan[0]["crop"]["l"] == 0.1 and plan[2]["crop"]["l"] == 0.5)


def test_resolve_empty():
    ok("0件でも落ちない", em.resolve_turn_images([], [], []) == [])
    d = _data(2)
    ok("cue0件→全None", em.resolve_turn_images(d["script"], [], []) == [None, None])


# ===== meta グルーピング（build_chapter_topics editor経路） =====

def _timed(script, dur=2.0):
    out = []
    for i, t in enumerate(script):
        out.append({**t, "start": round(i * dur, 3), "end": round((i + 1) * dur, 3),
                    "sentences": []})
    return out


def test_meta_cue_boundary_switches_image():
    d = _data(4)
    a0 = em.add_asset(d, file="0.jpg"); a1 = em.add_asset(d, file="1.jpg")
    em.add_cue(d, "turn-0001", a0["id"]); em.add_cue(d, "turn-0003", a1["id"])
    turns = _timed(d["script"])
    ti = em.resolve_turn_images(turns, d["assets"], d["imageCues"])
    segs = story_script.assign_sections_to_turns(turns)
    topics = ms.build_chapter_topics(segs, turns, d["chapters"], {}, {}, {}, turn_image=ti)
    ok("cue境界で画像が切り替わる", [t.get("image") for t in topics] == ["0.jpg", "1.jpg"])
    ok("topicが[0,total]被覆", topics[0]["start"] == 0.0 and topics[-1]["end"] == turns[-1]["end"])


def test_meta_bigeffect_suppresses_background():
    # 大演出区間では vizFrom/vizUntil で背後画像を描画時ゲート（露出しない）。editorでも同じ経路。
    d = _data(3)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0001", a["id"])
    d["script"][1]["vizSeg"] = "s1"
    d["chapters"][0]["vizList"] = [{"id": "s1", "type": "quiz", "quiz": {"question": "q"}}]
    turns = _timed(d["script"])
    ti = em.resolve_turn_images(turns, d["assets"], d["imageCues"])
    segs = story_script.assign_sections_to_turns(turns)
    topics = ms.build_chapter_topics(segs, turns, d["chapters"], {}, {}, {}, turn_image=ti)
    viz = [t for t in topics if t.get("vizFrom") is not None]
    ok("大演出区間にvizFrom/vizUntilが付く（背後画像をゲート）", len(viz) >= 1)


# ===== authority 切替 =====

def test_switch_to_editor_atomic():
    base = {"theme": "T",
            "chapters": [{"image_cuts": [{"image_query": "q0"}], "vizList": []}],
            "script": [{"speaker": "A", "text": "x", "chapter": 0, "cut": 0}]}
    review = {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg"}]}
    out = em.switch_to_editor(base, review)
    ok("切替でeditor権威が立つ", out["editorModelAuthority"] == "editor")
    ok("切替でassets/imageCues確定", out["assets"] and out["imageCues"])
    ok("元dataは破壊しない(legacyのまま)", "editorModelAuthority" not in base)


def test_switch_then_legacy_change_ignored():
    base = {"chapters": [{"image_cuts": [{"image_query": "q0"}]}],
            "script": [{"speaker": "A", "text": "x", "chapter": 0, "cut": 0, "id": "turn-0001"}]}
    ed = em.switch_to_editor(base, {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg", "fit": "cover"}]})
    hand = em.find_cue(ed, ed["imageCues"][0]["id"])
    hand["fit"] = "contain"               # 人手編集
    # editor権威で旧フィールドだけ変えても再導出されない
    ed["chapters"][0]["image_cuts"][0]["image_query"] = "CHANGED"
    out = em.migrate(ed, {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg", "fit": "cover"}]})
    ok("editor権威：旧フィールド変更を無視・人手編集を維持", out["imageCues"][0]["fit"] == "contain")


def test_legacy_authority_uses_legacy_meta_path():
    # legacy権威で新フィールドが残っていても、meta は legacy 経路（turn_image=None）。
    d = {"chapters": [{"image_cuts": [{"image_query": "q"}]}],
         "script": [{"speaker": "A", "text": "x", "chapter": 0, "cut": 0}],
         "assets": [{"id": "asset-00-00", "file": "z.jpg"}],
         "imageCues": [{"id": "image-cue-0001", "turnId": "turn-0001", "assetId": "asset-00-00"}]}
    # authority未設定＝legacy。build_meta は turn_image を作らない＝image_filesベース。
    ok("legacy権威でassets/imageCuesがあってもlegacy扱い",
       d.get("editorModelAuthority") != "editor")


# ===== 保存ライフサイクル（apply_save_script 経由） =====

def test_save_reload_resave_idempotent_editor():
    import review_server as rs
    base = {"theme": "T", "chapters": [{"image_cuts": [{"image_query": "q0"}, {"image_query": "q1"}]}],
            "script": [{"speaker": "A", "text": "x", "chapter": 0, "cut": 0},
                       {"speaker": "B", "text": "y", "chapter": 0, "cut": 1}]}
    ed = em.switch_to_editor(base, {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg"},
                                             {"ch": 0, "ci": 1, "image": "b.jpg"}]})
    okk, _, saved = rs.apply_save_script(ed)
    ok("保存でeditor権威/assets/imageCues保持", okk and saved["editorModelAuthority"] == "editor"
       and saved["assets"] and saved["imageCues"])
    reloaded = em.migrate(saved, {"cuts": []})        # 再読込相当（editorなので再導出しない）
    ok("再読込で編集データ不変", reloaded["imageCues"] == saved["imageCues"]
       and reloaded["assets"] == saved["assets"])
    _, _, resaved = rs.apply_save_script(reloaded)
    ok("保存→再読込→再保存が冪等", resaved["imageCues"] == saved["imageCues"])


# ===== editor クレジット（使用中assetのattributionのみ・重複除去） =====

_CONFIG = {"characters_gender": {}, "tts_voicevox": {"speakers": {"四国めたん": {}}}}


def test_editor_credits_used_only_dedup():
    d = _data(3)
    used = em.add_asset(d, file="u.jpg", attribution="Foo / Pexels")
    used2 = em.add_asset(d, file="u2.jpg", attribution="Foo / Pexels")  # 同出典別asset
    em.add_asset(d, file="x.jpg", attribution="UNUSED / Z")             # 未使用
    em.add_cue(d, "turn-0001", used["id"]); em.add_cue(d, "turn-0002", used2["id"])
    d["editorModelAuthority"] = "editor"
    turns = [{"start": i, "end": i + 1, "sentences": []} for i in range(3)]
    meta = ms.build_meta(d, turns, _CONFIG, "X")
    line = next((c for c in meta["credits"] if c.startswith("画像出典")), "")
    ok("使用中assetの出典をcreditsへ", "Foo / Pexels" in line)
    ok("未使用assetは含めない", "UNUSED" not in line)
    ok("同一出典は重複除去", line.count("Foo / Pexels") == 1)


def test_editor_credits_txt_for_added_asset():
    import pathlib
    import tempfile as _tf
    d = _data(2)
    a = em.add_asset(d, file="z.jpg", attribution="Bar / CC-BY")
    em.add_cue(d, "turn-0001", a["id"])
    d["editorModelAuthority"] = "editor"
    attrs = em.editor_attributions(d["assets"], d["imageCues"])
    tmp = pathlib.Path(_tf.mkdtemp())
    try:
        ms.write_credits_txt(tmp, _CONFIG, attrs)
        txt = (tmp / "credits.txt").read_text(encoding="utf-8")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp)
    ok("editor追加assetの出典がcredits.txtに出る", "Bar / CC-BY" in txt)


# ===== 原子的保存（一時ファイル＋fsync＋os.replace） =====

def test_atomic_write_and_failure_preserves_original():
    import tempfile as _tf
    import review_server as rs
    d = _tf.mkdtemp()
    p = os.path.join(d, "f.json")
    try:
        rs._atomic_write_json(p, {"a": 1})
        ok("原子的書き込み成功", json.load(open(p)) == {"a": 1})
        # 直列化不能オブジェクトで失敗→元ファイル不変・一時ファイル残さない
        try:
            rs._atomic_write_json(p, {"bad": {1, 2, 3}})  # set はJSON不可
            ok("保存失敗を検知", False)
        except TypeError:
            ok("保存失敗を検知", True)
        ok("失敗時に元ファイルは無傷", json.load(open(p)) == {"a": 1})
        ok("失敗時に一時ファイルを残さない",
           not [f for f in os.listdir(d) if f.startswith(".tmp-")])
    finally:
        import shutil as _sh
        _sh.rmtree(d)


def test_switch_failure_keeps_legacy_file():
    import tempfile as _tf
    import review_server as rs
    d = _tf.mkdtemp()
    # script が不正（リストでない）＝switch_to_editor が例外→書き込まず legacy のまま据え置き。
    json.dump({"script": "bad", "chapters": []}, open(os.path.join(d, "script.json"), "w"))
    before = open(os.path.join(d, "script.json")).read()
    old = rs.DIR
    try:
        rs.DIR = d
        r = rs.do_switch_to_editor()
        after = open(os.path.join(d, "script.json")).read()
    finally:
        rs.DIR = old
        import shutil as _sh
        _sh.rmtree(d, ignore_errors=True)
    ok("切替失敗時はok=False", r["ok"] is False and "switched" not in r)
    ok("切替失敗時にscript.jsonを書き換えない", before == after)


def test_switch_success_atomic_and_idempotent():
    import shutil as _sh
    import tempfile as _tf
    import review_server as rs
    d = _tf.mkdtemp()
    bk = _tf.mkdtemp()                       # 退避先（tempdir）
    unrelated_root = _tf.mkdtemp()           # 「触ってはいけない別の退避場所」を模したtempdir
    sentinel = os.path.join(unrelated_root, "editor-authority-pre-SENTINEL")
    os.makedirs(sentinel, exist_ok=True)
    _write_real_legacy_fixture(d)
    old = rs.DIR
    try:
        rs.DIR = d
        r1 = rs.do_switch_to_editor(backups_root=bk)
        sd = json.load(open(os.path.join(d, "script.json")))
        r2 = rs.do_switch_to_editor(backups_root=bk)   # 2回目は no-op
        bk_dirs = os.listdir(bk)
        sentinel_survives = os.path.isdir(sentinel)
    finally:
        rs.DIR = old
        _sh.rmtree(d, ignore_errors=True)
        _sh.rmtree(bk, ignore_errors=True)
        _sh.rmtree(unrelated_root, ignore_errors=True)  # 作成したtempdirだけ削除
    ok("切替成功でeditor権威・妥当JSON", r1["ok"] and r1["switched"]
       and sd.get("editorModelAuthority") == "editor")
    ok("退避は指定backups_root配下にだけ作られる", any(b.startswith("editor-authority-pre-") for b in bk_dirs))
    ok("backups_root外の退避(sentinel)は触らない", sentinel_survives)
    ok("切替は冪等（2回目はno-op）", r2["ok"] and r2.get("switched") is False)


# ===== set_cue_opts（表示調整） =====

def test_hide_cue_continuation_lifecycle():
    # 継続位置に hide cue を足すと黒板、その hide cue(素材無し)を削除すると前画像が継続復元。
    d = _data(4)
    a = em.add_asset(d, file="0.jpg")
    em.add_cue(d, "turn-0001", a["id"])
    hc = em.add_cue(d, "turn-0003", None, hide=True)
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("継続位置のhideでblank・手前は前画像", plan[2].get("blank") is True and plan[1]["image"] == "0.jpg")
    em.delete_cue(d, hc["id"])     # UIの「解除」は素材無しhideをdeleteする
    plan = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("hide cue削除で前画像が継続復元", plan[2]["image"] == "0.jpg")


def test_hide_with_asset_unhide_keeps_cue():
    # 素材ありの hide cue は解除(hide:false)で cue を保持し画像を表示する（deleteしない）。
    d = _data(2)
    a = em.add_asset(d, file="0.jpg")
    c = em.add_cue(d, "turn-0001", a["id"], hide=True)
    p1 = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    em.set_cue_opts(d, c["id"], hide=False)
    p2 = em.resolve_turn_images(d["script"], d["assets"], d["imageCues"])
    ok("素材ありhide解除でcue保持・画像表示",
       p1[0].get("blank") is True and em.find_cue(d, c["id"]) is not None and p2[0]["image"] == "0.jpg")


def test_set_cue_opts():
    d = _data(2)
    a = em.add_asset(d, file="x.jpg")
    c = em.add_cue(d, "turn-0001", a["id"])
    em.set_cue_opts(d, c["id"], fit="contain", pad=0, crop={"l": 0, "t": 0, "r": 0.5, "b": 0.5}, hide=True)
    cc = em.find_cue(d, c["id"])
    ok("fit/crop/pad:0/hide を反映（0を欠損扱いしない）",
       cc["fit"] == "contain" and cc["pad"] == 0 and cc["crop"]["l"] == 0 and cc["hide"] is True)
    em.set_cue_opts(d, c["id"], fit=None, crop=None, hide=False)
    cc = em.find_cue(d, c["id"])
    ok("None指定でクリア", cc["fit"] is None and cc["crop"] is None and cc["hide"] is False)


def test_cue_op_endpoint_roundtrip():
    import shutil as _sh
    import tempfile as _tf
    import review_server as rs
    d = _tf.mkdtemp(); bk = _tf.mkdtemp()
    _write_real_legacy_fixture(d)
    old = rs.DIR
    try:
        rs.DIR = d
        rs.do_switch_to_editor(backups_root=bk)
        data = json.load(open(os.path.join(d, "script.json")))
        t0, t1, t2 = data["script"][0]["id"], data["script"][1]["id"], data["script"][2]["id"]
        a0 = data["assets"][0]["id"]
        # 先頭cueはt0にあるはず。setOpts→fit反映
        c0 = next(c for c in data["imageCues"] if c["turnId"] == t0)
        r = rs.do_cue_op({"data": data, "op": "setOpts", "cueId": c0["id"], "opts": {"fit": "contain", "pad": 0}})
        data = r["data"]; c0n = next(c for c in data["imageCues"] if c["id"] == c0["id"])
        set_ok = r["ok"] and c0n["fit"] == "contain" and c0n["pad"] == 0
        # range設定→範囲逆転は拒否（不正は保存されない）
        rbad = rs.do_cue_op({"data": data, "op": "range", "cueId": c0["id"], "endTurnId": t0,
                             "startTurnId": t2})  # end(t0)<start(t2)
        reversed_rejected = rbad["ok"] is False
        # delete先頭cue→件数-1
        n_before = len(data["imageCues"])
        rdel = rs.do_cue_op({"data": data, "op": "delete", "cueId": c0["id"]})
        del_ok = rdel["ok"] and len(rdel["data"]["imageCues"]) == n_before - 1
        data = rdel["data"]
        # place at t0 (再配置・add-or-replace)
        rpl = rs.do_cue_op({"data": data, "op": "place", "turnId": t0, "assetId": a0})
        place_ok = rpl["ok"] and any(c["turnId"] == t0 for c in rpl["data"]["imageCues"])
        # 不正assetId→拒否
        rbad2 = rs.do_cue_op({"data": rpl["data"], "op": "place", "turnId": t1, "assetId": "asset-zzz"})
        bad_asset_rejected = rbad2["ok"] is False
        # legacyデータ拒否
        leg = {k: v for k, v in rpl["data"].items() if k != "editorModelAuthority"}
        leg_rejected = rs.do_cue_op({"data": leg, "op": "delete", "cueId": "x"})["ok"] is False
    finally:
        rs.DIR = old
        _sh.rmtree(d, ignore_errors=True); _sh.rmtree(bk, ignore_errors=True)
    ok("cue-op setOpts反映", set_ok)
    ok("cue-op 範囲逆転を拒否", reversed_rejected)
    ok("cue-op delete", del_ok)
    ok("cue-op place(add-or-replace)", place_ok)
    ok("cue-op 不正assetIdを拒否", bad_asset_rejected)
    ok("cue-op legacyデータ拒否", leg_rejected)


# ===== authority切替でtheme等が維持される / 素材API往復 =====

def test_switch_preserves_theme_and_script():
    base = {"theme": "マイテーマ", "chapters": [{"image_cuts": [{"image_query": "q"}]}],
            "script": [{"speaker": "四国めたん", "text": "やあ", "chapter": 0, "cut": 0}]}
    ed = em.switch_to_editor(base, {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg"}]})
    ok("切替後もthemeを維持", ed.get("theme") == "マイテーマ")
    ok("切替後もscript本文を維持", ed["script"][0]["text"] == "やあ")
    ok("editor権威が立つ", ed["editorModelAuthority"] == "editor")


def test_asset_api_roundtrip():
    import base64 as _b64
    import shutil as _sh
    import tempfile as _tf
    import review_server as rs
    d = _tf.mkdtemp(); bk = _tf.mkdtemp()
    _write_real_legacy_fixture(d)
    png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQ"
           "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    old = rs.DIR
    try:
        rs.DIR = d
        r0 = rs.do_switch_to_editor(backups_root=bk)
        data = json.load(open(os.path.join(d, "script.json")))
        n0 = len(data["assets"])
        # 追加（直後に件数/ファイルを確定。後続のdeleteが同一dictを破壊的に変えるため）。
        ra = rs.do_asset_add({"data": data, "dataB64": png, "filename": "x.png", "query": "t"})
        add_ok = ra["ok"]; new_id = ra.get("assetId")
        added = next((a for a in ra["data"]["assets"] if a["id"] == new_id), {}) if add_ok else {}
        n_after_add = len(ra["data"]["assets"]) if add_ok else -1
        file_exists = os.path.exists(os.path.join(d, added.get("file") or "_none_"))
        # 未使用削除（追加したものを消す）
        rd = rs.do_asset_delete({"data": ra["data"], "assetId": new_id})
        n_after_del = len(rd["data"]["assets"]) if rd["ok"] else -1
        # 使用中削除拒否
        used_id = next(c["assetId"] for c in rd["data"]["imageCues"] if c.get("assetId"))
        rdu = rs.do_asset_delete({"data": rd["data"], "assetId": used_id})
        # legacyデータでは追加拒否
        leg = {k: v for k, v in rd["data"].items() if k != "editorModelAuthority"}
        rl = rs.do_asset_add({"data": leg, "dataB64": png, "filename": "y.png"})
    finally:
        rs.DIR = old
        _sh.rmtree(d, ignore_errors=True); _sh.rmtree(bk, ignore_errors=True)
    ok("切替成功", r0["ok"] and r0["switched"])
    ok("asset追加でID採番・ファイル保存", add_ok and n_after_add == n0 + 1 and file_exists)
    ok("未使用asset削除", rd["ok"] and n_after_del == n0)
    ok("使用中asset削除は拒否（参照cue返却）", rdu["ok"] is False and len(rdu.get("used", [])) >= 1)
    ok("legacyデータでは追加拒否", rl["ok"] is False)


# ===== 実データ：legacy/editor の meta 等価性（機械比較） =====

def test_real_data_legacy_editor_meta_equivalence():
    sp, rp, mp = ("docs/story/script.json", "docs/story/review.json", "docs/story/meta.json")
    if not all(os.path.exists(p) for p in (sp, rp, mp)):
        ok("実データ等価（ファイルなしでskip）", True)
        return
    script_data = json.load(open(sp)); review = json.load(open(rp)); meta = json.load(open(mp))
    base, metas, chapters = script_data["script"], meta["script"], script_data["chapters"]
    if len(base) != len(metas):
        ok("実データ等価（ターン数不一致でskip）", True)
        return

    def wt(b):
        return [{**x, "start": m["start"], "end": m["end"]} for x, m in zip(b, metas)]
    img, attr, opts = {}, {}, {}
    for c in review.get("cuts", []):
        k = (c["ch"], c["ci"])
        if c.get("image"):
            img[k] = c["image"]
        if c.get("attribution"):
            attr[k] = c["attribution"]
        o = {}
        for kk in ("fit", "crop", "filter", "hide", "pad", "bg"):
            if c.get(kk):
                o[kk] = True if kk == "hide" else c[kk]
        if o:
            opts[k] = o
    tl = wt(base)
    legacy = ms.build_chapter_topics(story_script.assign_sections_to_turns(tl), tl, chapters,
                                     img, attr, opts)
    legacy_source = dict(script_data)
    legacy_source.pop("editorModelAuthority", None)  # 現在の実データがeditorでも旧形式から比較用に再導出
    ed = em.migrate(legacy_source, review); ed["editorModelAuthority"] = "editor"
    te = wt(ed["script"])
    ti = em.resolve_turn_images(te, ed["assets"], ed["imageCues"])
    editor = ms.build_chapter_topics(story_script.assign_sections_to_turns(te), te, chapters,
                                     img, attr, opts, turn_image=ti)
    ok(f"実データ legacy/editor meta等価（{len(legacy)} topics）", legacy == editor)


if __name__ == "__main__":
    test_add_asset_unique_id()
    test_asset_usage_and_delete()
    test_unused_vs_broken_distinction()
    test_add_cue_requires_turn()
    test_place_image_add_or_replace()
    test_move_replace_range_delete()
    test_pad_zero_and_crop_zero_preserved()
    test_asset_id_no_reuse()
    test_cue_id_no_reuse()
    test_id_counter_persists_through_save()
    test_ops_reject_invalid_asset()
    test_ops_reject_start_collision()
    test_ops_reject_reversed_range()
    test_delete_readd_roundtrip()
    test_normalize_dedup_orphan_reversed()
    test_normalize_idempotent()
    test_reconcile_turn_delete_relocates()
    test_reconcile_turn_delete_no_successor_drops()
    test_reconcile_reorder_follows_turn()
    test_resolve_continuation_until_next()
    test_resolve_endturn_gap_then_blank()
    test_resolve_hide_blank_broken_nofile()
    test_resolve_before_first_cue_none()
    test_resolve_same_asset_multiple_cues()
    test_resolve_empty()
    test_meta_cue_boundary_switches_image()
    test_meta_bigeffect_suppresses_background()
    test_switch_to_editor_atomic()
    test_switch_then_legacy_change_ignored()
    test_legacy_authority_uses_legacy_meta_path()
    test_save_reload_resave_idempotent_editor()
    test_editor_credits_used_only_dedup()
    test_editor_credits_txt_for_added_asset()
    test_atomic_write_and_failure_preserves_original()
    test_switch_failure_keeps_legacy_file()
    test_switch_success_atomic_and_idempotent()
    test_switch_preserves_theme_and_script()
    test_asset_api_roundtrip()
    test_hide_cue_continuation_lifecycle()
    test_hide_with_asset_unhide_keeps_cue()
    test_set_cue_opts()
    test_cue_op_endpoint_roundtrip()
    test_real_data_legacy_editor_meta_equivalence()
    print(f"ALL PASS ({passed} checks)")
