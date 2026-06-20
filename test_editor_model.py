"""editor_model（人間向け編集モデルへの移行・Phase 1）の単体テスト。

実行: python3 test_editor_model.py
turn ID 付与 / assets / imageCues / visualSegments への冪等変換と、保存・再読込での不変性を検証する。
"""
import json
import os
import tempfile

from src import editor_model as em


def _turn(speaker="A", text="t", chapter=0, **kw):
    d = {"speaker": speaker, "text": text, "chapter": chapter}
    d.update(kw)
    return d


# ---- turn ID ----

def test_assign_ids_to_all_turns():
    script = [_turn() for _ in range(3)]
    em.assign_turn_ids(script)
    ids = [t["id"] for t in script]
    assert ids == ["turn-0001", "turn-0002", "turn-0003"], ids
    assert len(set(ids)) == 3, "全ID一意"
    print("  全セリフへ安定turn ID付与: OK")


def test_keep_existing_ids():
    script = [_turn(id="turn-0005"), _turn(), _turn(id="custom-x")]
    em.assign_turn_ids(script)
    assert script[0]["id"] == "turn-0005", "既存ID維持"
    assert script[2]["id"] == "custom-x", "非標準形式の既存IDも維持"
    # 既存最大(5)の続きから採番＝再利用しない
    assert script[1]["id"] == "turn-0006", script[1]["id"]
    print("  既存turn ID維持＋続き採番: OK")


def test_split_turn_id_rule():
    # 分割: 前半は元IDを維持、後半は衝突しない新ID。
    script = [_turn(id="turn-0001"), _turn(id="turn-0002")]
    existing = {t["id"] for t in script}
    new_id = em.next_turn_id(existing)
    assert new_id == "turn-0003", new_id
    # 後半IDを足してもう一度 → さらに次（再利用なし）
    existing.add(new_id)
    assert em.next_turn_id(existing) == "turn-0004"
    print("  セリフ分割時のID規則: OK")


def test_idempotent_id_assign():
    script = [_turn(), _turn()]
    em.assign_turn_ids(script)
    first = [t["id"] for t in script]
    em.assign_turn_ids(script)
    assert [t["id"] for t in script] == first, "再付与で番号が動かない"
    print("  turn ID付与の冪等: OK")


# ---- assets ----

def test_assets_no_collision_same_cut_across_chapters():
    chapters = [
        {"image_cuts": [{"image_query": "a"}, {"image_query": "b"}]},
        {"image_cuts": [{"image_query": "c"}, {"image_query": "d"}]},
    ]
    assets, index = em.build_assets(chapters)
    ids = [a["id"] for a in assets]
    assert len(set(ids)) == 4, "章をまたいでも asset ID 一意"
    assert index[(0, 0)] != index[(1, 0)], "章ごとに同じcut番号でも別asset"
    print("  章ごとに同じcut番号でもassetが衝突しない: OK")


def test_assets_migrate_fields():
    chapters = [{"image_cuts": [{"image_query": "Yokohama", "image_query_ja": "横浜",
                                 "image_kind": "subject"}]}]
    review = {"cuts": [{"ch": 0, "ci": 0, "image": "ch_00_00.jpg",
                        "attribution": "Foo / Pexels"}]}
    assets, _ = em.build_assets(chapters, review)
    a = assets[0]
    assert a["file"] == "ch_00_00.jpg", "file移行"
    assert a["query"] == "Yokohama" and a["queryJa"] == "横浜", "query移行"
    assert a["kind"] == "subject", "kind移行"
    assert a["attribution"] == "Foo / Pexels", "attribution移行"
    assert a["sourceChapter"] == 0
    print("  attribution/query/kind/fileをassetへ移行: OK")


def test_assets_empty_and_unfetched():
    # 空query・画像なし(review未登録)でも asset 枠は作る（cueが参照できるよう）。
    chapters = [{"image_cuts": [{"image_query": "", "image_kind": "ambient"}]}]
    assets, index = em.build_assets(chapters)
    assert len(assets) == 1
    assert assets[0]["file"] is None, "未取得は file None"
    assert assets[0]["query"] is None, "空queryはNoneに畳む"
    print("  未取得画像・画像なしのasset: OK")


# ---- imageCues ----

def test_cues_merge_consecutive_same_cut():
    script = [_turn(chapter=0, cut=0), _turn(chapter=0, cut=0), _turn(chapter=0, cut=0)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}]}]
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert len(cues) == 1, "連続する同じchapter+cutは1キュー"
    assert cues[0]["turnId"] == "turn-0001"
    print("  連続する同じchapter+cutを1つのimageCueへ: OK")


def test_cues_add_at_cut_change():
    script = [_turn(chapter=0, cut=0), _turn(chapter=0, cut=1), _turn(chapter=0, cut=1),
              _turn(chapter=1, cut=0)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}, {}]}, {"image_cuts": [{}]}]
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert [c["turnId"] for c in cues] == ["turn-0001", "turn-0002", "turn-0004"], \
        [c["turnId"] for c in cues]
    assert cues[0]["assetId"] == "asset-00-00"
    assert cues[1]["assetId"] == "asset-00-01"
    assert cues[2]["assetId"] == "asset-01-00", "章境界で新キュー＋章のasset"
    print("  cut変更位置にimageCueを追加: OK")


def test_cues_carry_missing_cut():
    # cut欠落は直前を継続（_cut_groups と同じ）＝新キューを作らない。
    script = [_turn(chapter=0, cut=2), _turn(chapter=0), _turn(chapter=0)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}, {}, {}]}]
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert len(cues) == 1 and cues[0]["assetId"] == "asset-00-02"
    print("  cut欠落は直前を継続: OK")


def test_cues_out_of_range_cut_fallback():
    # 範囲外cut(=5 だが image_cuts は2枚)は無効値＝直前cutへフォールバック（旧_cut_groupsと一致）。
    script = [_turn(chapter=0, cut=1), _turn(chapter=0, cut=5), _turn(chapter=0, cut=5)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}, {}]}]
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert len(cues) == 1 and cues[0]["assetId"] == "asset-00-01", \
        [(c["assetId"]) for c in cues]
    print("  範囲外cutは直前へフォールバック: OK")


def test_cues_no_anchor_even_split():
    # cutアンカーが一つも無い＋複数画像 → 旧metaどおり発言数を均等割りしてキュー化する。
    script = [_turn(chapter=0) for _ in range(4)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}, {}]}]   # 2枚 → 4発言を2分割
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert [c["assetId"] for c in cues] == ["asset-00-00", "asset-00-01"], \
        [c["assetId"] for c in cues]
    # 0..2 / 2..4 の境界＝turn-0001 と turn-0003 が開始
    assert [c["turnId"] for c in cues] == ["turn-0001", "turn-0003"], \
        [c["turnId"] for c in cues]
    print("  cutアンカーなし複数画像の均等割り: OK")


def test_cue_display_settings_from_review():
    script = [_turn(chapter=0, cut=0)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}]}]
    review = {"cuts": [{"ch": 0, "ci": 0, "fit": "contain", "crop": {"x": 1},
                        "filter": "mono", "pad": 12, "bg": "#000", "hide": True}]}
    _, index = em.build_assets(chapters, review)
    cues = em.build_image_cues(script, chapters, index, review)
    c = cues[0]
    assert c["fit"] == "contain" and c["crop"] == {"x": 1} and c["filter"] == "mono"
    assert c["pad"] == 12 and c["bg"] == "#000" and c["hide"] is True
    print("  review crop/fit/filter/pad/bg/hide を cue へ移行: OK")


def test_cue_settings_per_cue_not_asset():
    # 同じassetを複数cueで共有しても、表示調整はcueごとに独立。
    script = [_turn(chapter=0, cut=0), _turn(chapter=0, cut=1), _turn(chapter=0, cut=0)]
    em.assign_turn_ids(script)
    chapters = [{"image_cuts": [{}, {}]}]
    _, index = em.build_assets(chapters)
    cues = em.build_image_cues(script, chapters, index)
    assert cues[0]["assetId"] == cues[2]["assetId"] == "asset-00-00", "同asset共有"
    assert cues[0]["id"] != cues[2]["id"], "cueは別物"
    print("  同asset共有・cropはcueごと独立: OK")


# ---- visualSegments ----

def test_segments_no_collision_same_vizseg_across_chapters():
    script = [_turn(chapter=0, vizSeg="s1"), _turn(chapter=1, vizSeg="s1")]
    em.assign_turn_ids(script)
    chapters = [
        {"vizList": [{"id": "s1", "type": "quiz", "quiz": {"question": "q0"}}]},
        {"vizList": [{"id": "s1", "type": "panel", "panel": {"items": []}}]},
    ]
    segs = em.build_visual_segments(script, chapters)
    ids = [s["id"] for s in segs]
    assert len(set(ids)) == 2, "章をまたいでも segment ID 一意"
    assert ids == ["visual-00-s1", "visual-01-s1"], ids
    print("  章ごとに同じvizSeg IDでもvisualSegmentが衝突しない: OK")


def test_segments_anchor_and_config():
    script = [_turn(chapter=0, vizSeg="s1"), _turn(chapter=0, vizSeg="s1", reveal=True),
              _turn(chapter=0, vizSeg="s2")]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [
        {"id": "s1", "type": "stat", "stat": {"value": "6"}},
        {"id": "s2", "type": "panel", "panel": {"items": [{"text": "x"}]}},
    ]}]
    segs = em.build_visual_segments(script, chapters)
    s1 = segs[0]
    assert s1["startTurnId"] == "turn-0001" and s1["endTurnId"] == "turn-0002", "端点アンカー"
    assert s1["type"] == "stat" and s1["mode"] == "overlay"
    assert s1["config"]["stat"] == {"value": "6"}, "configへ退避"
    assert any(k["type"] == "reveal" and k["turnId"] == "turn-0002" for k in s1["keyframes"]), \
        "フラグがkeyframeへ"
    assert segs[1]["mode"] == "layout", "panel→layout"
    print("  vizList→visualSegment＋端点/config/keyframe: OK")


def test_vizpoints_to_keyframes_pos0():
    # pos:0 と未指定を区別（pos:0 を保持する）。
    script = [_turn(chapter=0, vizSeg="s1",
                    vizPoints=[{"id": "vp1", "type": "panel_item", "pos": 0, "value": 1}])]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [{"id": "s1", "type": "panel", "panel": {"items": []}}]}]
    segs = em.build_visual_segments(script, chapters)
    kfs = segs[0]["keyframes"]
    kf = next(k for k in kfs if k["type"] == "panel_item")
    assert kf["pos"] == 0, "pos:0 を保持"
    assert kf["value"] == 1
    print("  vizPoints→keyframe（pos:0保持）: OK")


def test_keyframe_dedup_flag_and_vizpoint():
    # 同じ発言に reveal フラグと reveal vizPoint。vizPoint 優先＝reveal は1つだけ（重複しない）。
    script = [_turn(chapter=0, vizSeg="s1", reveal=True,
                    vizPoints=[{"id": "vp1", "type": "reveal", "pos": 4}])]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [{"id": "s1", "type": "quiz", "quiz": {"question": "q"}}]}]
    segs = em.build_visual_segments(script, chapters)
    reveals = [k for k in segs[0]["keyframes"] if k["type"] == "reveal"]
    assert len(reveals) == 1, reveals
    assert reveals[0].get("pos") == 4, "vizPoint(文字位置)を採用"
    print("  flag＋vizPointの重複排除: OK")


def test_keyframe_excludes_viz_start_end():
    # viz_start/viz_end は範囲フラグ＝keyframe に入れない。
    script = [_turn(chapter=0, vizSeg="s1", viz_start=True),
              _turn(chapter=0, vizSeg="s1", viz_end=True)]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [{"id": "s1", "type": "quiz", "quiz": {"question": "q"}}]}]
    segs = em.build_visual_segments(script, chapters)
    types = {k["type"] for k in segs[0]["keyframes"]}
    assert "viz_start" not in types and "viz_end" not in types, types
    print("  viz_start/end が keyframe へ入らない: OK")


def test_keyframe_panel_item_array_expand():
    # panel_item 配列は項目ごとに keyframe を展開する。
    script = [_turn(chapter=0, vizSeg="s1", panel_item=[0, 2, 3])]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [{"id": "s1", "type": "panel", "panel": {"items": []}}]}]
    segs = em.build_visual_segments(script, chapters)
    vals = sorted(k["value"] for k in segs[0]["keyframes"] if k["type"] == "panel_item")
    assert vals == [0, 2, 3], vals
    print("  panel_item配列の展開: OK")


def test_segment_orphaned_not_activated():
    # vizSeg を持つ発言が無いエントリは旧 meta が描画しない＝activeにしない（orphaned保持）。
    script = [_turn(chapter=0), _turn(chapter=0)]
    em.assign_turn_ids(script)
    chapters = [{"vizList": [{"id": "s9", "type": "quiz", "quiz": {"question": "q"}}]}]
    segs = em.build_visual_segments(script, chapters)
    assert len(segs) == 1, "データは保持する"
    s = segs[0]
    assert s["status"] == "orphaned", s["status"]
    assert s["startTurnId"] is None and s["endTurnId"] is None, "アンカー無し＝有効化しない"
    assert s["keyframes"] == []
    print("  所属なし演出が有効化されない: OK")


def test_legacy_single_viz_form():
    script = [_turn(chapter=0, viz_start=True), _turn(chapter=0, viz_end=True)]
    em.assign_turn_ids(script)
    chapters = [{"quiz": {"question": "legacy?"}}]   # 章直下に直接（旧単一形式）
    segs = em.build_visual_segments(script, chapters)
    assert len(segs) == 1 and segs[0]["id"] == "visual-00-legacy"
    assert segs[0]["config"]["quiz"] == {"question": "legacy?"}
    print("  旧単一形式の演出も移行: OK")


def test_no_viz_chapter():
    script = [_turn(chapter=0)]
    em.assign_turn_ids(script)
    segs = em.build_visual_segments(script, [{"image_cuts": [{}]}])
    assert segs == [], "演出なし章は空"
    print("  演出なし章: OK")


# ---- migrate（統合・冪等） ----

def _sample():
    return {
        "theme": "T",
        "chapters": [
            {"image_cuts": [{"image_query": "q0"}, {"image_query": "q1"}],
             "vizList": [{"id": "s1", "type": "quiz", "quiz": {"question": "q"}}]},
            {"image_cuts": [{"image_query": "q2"}]},
        ],
        "script": [
            _turn(chapter=0, cut=0, vizSeg="s1", reveal=True),
            _turn(chapter=0, cut=1),
            _turn(chapter=1, cut=0),
        ],
    }


def test_migrate_adds_schema():
    out = em.migrate(_sample(), {"cuts": [{"ch": 0, "ci": 0, "image": "ch_00_00.jpg"}]})
    assert out["schemaVersion"] == em.SCHEMA_VERSION
    assert len(out["assets"]) == 3 and len(out["imageCues"]) == 3
    assert len(out["visualSegments"]) == 1
    assert all(t.get("id") for t in out["script"]), "全turnにID"
    # 旧フィールドは残る（後方互換）。
    assert out["chapters"][0]["image_cuts"][0]["image_query"] == "q0"
    assert out["chapters"][0]["vizList"][0]["id"] == "s1"
    print("  migrate: スキーマ追加＋旧フィールド維持: OK")


def test_migrate_does_not_mutate_input():
    src = _sample()
    em.migrate(src)
    assert "schemaVersion" not in src and "id" not in src["script"][0], "入力を破壊しない"
    print("  migrate: 入力非破壊: OK")


def test_migrate_idempotent():
    src = _sample()
    once = em.migrate(src)
    twice = em.migrate(once)
    assert once == twice, "2回適用で不変"
    # 件数が増えていない
    assert len(twice["assets"]) == len(once["assets"])
    assert len(twice["imageCues"]) == len(once["imageCues"])
    assert len(twice["visualSegments"]) == len(once["visualSegments"])
    print("  変換を2回実行して結果不変: OK")


def test_migrate_empty_and_null():
    assert em.migrate(None) is None
    assert em.migrate({"script": []})["imageCues"] == []
    # script が無い/不正でも落ちない
    assert "assets" not in em.migrate({"foo": 1})
    print("  null・空・script不正でも安全: OK")


def test_duplicate_turn_id_repaired():
    script = [_turn(id="turn-0001"), _turn(id="turn-0001"), _turn(id="turn-0002")]
    em.assign_turn_ids(script)
    ids = [t["id"] for t in script]
    assert ids[0] == "turn-0001" and ids[2] == "turn-0002", "先に出たIDは維持"
    assert ids[1] not in ("turn-0001", "turn-0002"), ids
    assert len(set(ids)) == 3, ids
    print("  重複turn IDの修復: OK")


def test_legacy_authority_reflects_review_change():
    # Phase 1（authority=legacy）: 新フィールド保存後でも、旧形式(review.json)変更を再導出で反映する。
    base = em.migrate(_sample(), {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg", "fit": "cover"}]})
    assert base["imageCues"][0]["fit"] == "cover"
    assert em.AUTHORITY_FIELD not in base, "Phase 1 は legacy（明示フラグを立てない）"
    out = em.migrate(base, {"cuts": [{"ch": 0, "ci": 0, "image": "a.jpg", "fit": "contain"}]})
    assert out["imageCues"][0]["fit"] == "contain", "移行済み状態でも review 変更が反映される"
    print("  移行済み状態でreview変更が反映: OK")


def test_editor_authority_freezes_rederive():
    # authority=editor（Phase 2以降）になったら編集モデル側を正とし、再導出で上書きしない。
    base = em.migrate(_sample())
    base[em.AUTHORITY_FIELD] = "editor"
    base["imageCues"] = [{"id": "hand-edited"}]
    out = em.migrate(base, {"cuts": [{"ch": 0, "ci": 0, "fit": "contain"}]})
    assert out["imageCues"] == [{"id": "hand-edited"}], "editorが正＝再導出しない"
    print("  editor権威では再導出しない: OK")


def test_save_reload_invariant():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "script.json"), "w", encoding="utf-8") as f:
            json.dump(_sample(), f, ensure_ascii=False)
        with open(os.path.join(d, "review.json"), "w", encoding="utf-8") as f:
            json.dump({"cuts": [{"ch": 0, "ci": 0, "image": "ch_00_00.jpg", "fit": "cover"}]}, f)
        # 1回目: 変換してディスクへ保存（バックアップ付き）
        first = em.migrate_dir(d, persist=True, backups_root=os.path.join(d, ".backups"))
        # バックアップが作られている
        assert os.path.isdir(os.path.join(d, ".backups")), "変換前バックアップ"
        # 再読込→再変換しても不変
        with open(os.path.join(d, "script.json"), encoding="utf-8") as f:
            reloaded = json.load(f)
        again = em.migrate(reloaded, {"cuts": [{"ch": 0, "ci": 0, "image": "ch_00_00.jpg",
                                                "fit": "cover"}]})
        assert again == first, "保存・再読込後も結果不変"
        # 2回目persist: 変換済みなので新たなバックアップ書き戻しをしない＝冪等
        before = sorted(os.listdir(os.path.join(d, ".backups")))
        em.migrate_dir(d, persist=True, backups_root=os.path.join(d, ".backups"))
        after = sorted(os.listdir(os.path.join(d, ".backups")))
        assert before == after, "変換済みは再バックアップしない"
    print("  保存・再読込後も結果不変＋バックアップ: OK")


if __name__ == "__main__":
    test_assign_ids_to_all_turns()
    test_keep_existing_ids()
    test_split_turn_id_rule()
    test_idempotent_id_assign()
    test_assets_no_collision_same_cut_across_chapters()
    test_assets_migrate_fields()
    test_assets_empty_and_unfetched()
    test_cues_merge_consecutive_same_cut()
    test_cues_add_at_cut_change()
    test_cues_carry_missing_cut()
    test_cues_out_of_range_cut_fallback()
    test_cues_no_anchor_even_split()
    test_cue_display_settings_from_review()
    test_cue_settings_per_cue_not_asset()
    test_segments_no_collision_same_vizseg_across_chapters()
    test_segments_anchor_and_config()
    test_vizpoints_to_keyframes_pos0()
    test_keyframe_dedup_flag_and_vizpoint()
    test_keyframe_excludes_viz_start_end()
    test_keyframe_panel_item_array_expand()
    test_segment_orphaned_not_activated()
    test_legacy_single_viz_form()
    test_no_viz_chapter()
    test_migrate_adds_schema()
    test_migrate_does_not_mutate_input()
    test_migrate_idempotent()
    test_migrate_empty_and_null()
    test_duplicate_turn_id_repaired()
    test_legacy_authority_reflects_review_change()
    test_editor_authority_freezes_rederive()
    test_save_reload_invariant()
    print("ALL PASS")
