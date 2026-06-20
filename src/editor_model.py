"""人間向け編集モデルへの段階移行（Phase 1：スキーマと変換のみ）。

docs/editor-model-refactor-plan.md の方針に沿い、Gemini生成形式（章所有の image_cuts /
turn.cut / vizList / turn.vizSeg / review.json）を、セリフへアンカーされた編集モデル
（assets / imageCues / visualSegments＋安定 turn ID）へ変換する純ロジックを集約する。

Phase 1 の不変条件:
- 既存フィールド（chapters[].image_cuts, turn.cut, vizList, turn.vizSeg, 各フラグ,
  vizPoints, review.json）は破壊せずそのまま残す＝後方互換の読み込みを維持する。
- 追加するのはトップレベルの schemaVersion / assets / imageCues / visualSegments と
  各 turn の id だけ。UI 表示と meta 出力（main_story の旧経路）は一切変えない。
- 変換は冪等。同じ入力に2回適用しても ID・asset・cue・segment は増えない。

検証・ID発行・移行処理はこのモジュールへ集約し、UI経路ごとに重複実装しない。
"""

import copy
import datetime
import json
import os
import shutil

SCHEMA_VERSION = 2

# vizList の type → 編集モデルの mode（描画優先順位の区分。Phase 1 では保存のみで描画には未使用）。
# replace=全面置換(比較/全面カード) / layout=現画像を素材にレイアウト(解説パネル) / overlay=重ね(数字/注釈/クイズ)。
_TYPE_TO_MODE = {
    "panel": "layout",
    "compare": "replace",
    "quiz": "overlay",
    "stat": "overlay",
    "callouts": "overlay",
    "timeline": "replace",
    "lowerthird": "overlay",
}
# vizList エントリの種類別ペイロードを持つキー（config へ退避する対象）。
_VIZ_PAYLOAD_KEYS = ("panel", "quiz", "compare", "stat", "callouts", "calloutStyle")
# セリフ（turn）に乗る大演出の進行フラグ（整数 value つき）。keyframe へ展開する。
# reveal / panel_event は別途個別に扱い、viz_start/viz_end は範囲情報なので keyframe にしない。
_FLAG_VALUED = ("panel_item", "callout_item", "compare_item")


def _clean_str(v):
    """空文字・空白のみは None に畳む（query/kind/attribution の正規化用）。"""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


# ---- turn ID（6.1） ----

def _id_num(turn_id):
    """'turn-0007' → 7。形式外は None。"""
    if isinstance(turn_id, str) and turn_id.startswith("turn-"):
        tail = turn_id[len("turn-"):]
        if tail.isdigit():
            return int(tail)
    return None


def _format_turn_id(n):
    return f"turn-{n:04d}"


def next_turn_id(existing_ids):
    """既存 ID 集合と衝突しない次の turn ID を返す（分割の後半・移行の採番に共用）。

    既存の最大連番+1。ID再利用を避けるため、抜けた番号は埋め戻さない。
    """
    mx = 0
    for tid in existing_ids:
        n = _id_num(tid)
        if n is not None and n > mx:
            mx = n
    return _format_turn_id(mx + 1)


def assign_turn_ids(script):
    """全セリフへ安定 turn ID を付与（in-place）。既存 ID は維持し、未付与・重複だけ採番し直す。

    - 既存 ID（turn-NNNN 形式以外も含む）はそのまま尊重する。
    - 同じ ID が複数あるとアンカーが曖昧になるため、後から出現した重複へ新 ID を振る
      （先に出たものを正とする）。
    - 連番は全 turn の最大番号の続きから振る＝既存番号と衝突しない＝再付与で番号が動かない＝冪等。
    """
    mx = 0
    for turn in script:
        n = _id_num(turn.get("id"))
        if n is not None and n > mx:
            mx = n
    seen = set()
    for turn in script:
        tid = turn.get("id")
        if not tid or tid in seen:
            mx += 1
            tid = _format_turn_id(mx)   # mx は全体最大の続き＝既存IDと必ず非衝突
            turn["id"] = tid
        seen.add(tid)
    return script


# ---- assets（6.2） ----

def _review_index(review_data):
    """review.json の cuts を (ch, ci) → cut 辞書に索引化する。"""
    idx = {}
    for c in (review_data or {}).get("cuts", []) or []:
        try:
            key = (int(c.get("ch")), int(c.get("ci")))
        except (TypeError, ValueError):
            continue
        idx[key] = c
    return idx


def build_assets(chapters, review_data=None):
    """chapters[].image_cuts と review.json から素材ライブラリ assets を作る。

    Returns: (assets_list, index) — index は (chapter, cut) → asset_id。
    - ID は章・cut から決定的に生成（asset-CC-II）＝章ごとに同じ cut 番号でも衝突しない。
    - ファイル名は移行で変更しない（review の image をそのまま採用）。
    - sourceChapter はフィルター用タグであり所有権ではない（他章でも参照可）。
    """
    rev = _review_index(review_data)
    assets, index = [], {}
    for ch, chapter in enumerate(chapters or []):
        for ci, cut in enumerate(chapter.get("image_cuts", []) or []):
            cut = cut if isinstance(cut, dict) else {}
            rcut = rev.get((ch, ci), {})
            asset_id = f"asset-{ch:02d}-{ci:02d}"
            assets.append({
                "id": asset_id,
                "file": rcut.get("image") or None,
                "query": _clean_str(cut.get("image_query")) or _clean_str(rcut.get("query")),
                "queryJa": _clean_str(cut.get("image_query_ja")),
                "kind": _clean_str(cut.get("image_kind")) or _clean_str(rcut.get("kind")),
                "attribution": _clean_str(rcut.get("attribution")),
                "sourceChapter": ch,
            })
            index[(ch, ci)] = asset_id
    return assets, index


# ---- imageCues（6.3） ----

def _chapter_segments(script):
    """script を chapter の連続塊に分ける [(ch, [turn_index,...]), ...]（出現順）。

    main_story.assign_sections_to_turns と同じ「連続塊で切る」規則。非連続再登場は別塊。
    画像の章内cut割当は塊ごとに独立（旧 build_chapter_topics と一致させる）。
    """
    segs = []
    for i, t in enumerate(script):
        ch = t.get("chapter", 0)
        if not isinstance(ch, int) or isinstance(ch, bool):
            ch = 0
        if segs and segs[-1][0] == ch:
            segs[-1][1].append(i)
        else:
            segs.append((ch, [i]))
    return segs


def _cut_groups(idxs, script, ncuts):
    """章塊のターン列を cut アンカーでグループ化（main_story._cut_groups の完全移植）。

    - 範囲外/不正/欠落の cut は無効値として直前の cut を継続する。
    - アンカーが1つも無ければ None（呼び出し側が均等割りへフォールバック）。
    Returns: [(cut_index, lo, hi)]（idxs 内の位置・hi排他・連続被覆）または None
    """
    if ncuts <= 0:
        return None
    vals, cur, any_anchor = [], 0, False
    for j in idxs:
        c = script[j].get("cut")
        if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < ncuts:
            any_anchor = True
        else:
            c = cur
        vals.append(c)
        cur = c
    if not any_anchor:
        return None
    groups, pos, n = [], 0, len(vals)
    while pos < n:
        ci, start = vals[pos], pos
        while pos < n and vals[pos] == ci:
            pos += 1
        groups.append((ci, start, pos))
    return groups


def build_image_cues(script, chapters, asset_index, review_data=None):
    """章塊ごとに cut グループ（旧 meta と同じ割当）を求め、各グループ先頭へ imageCue を作る（6.3）。

    旧 build_chapter_topics と同じ規則で変換し、無編集移行で描画が一致するようにする:
    - cut アンカーがあれば _cut_groups で章内を区切る。
    - アンカーが無ければ均等割り（len(image_cuts or [{}]) と発言数で分割）。
    - 章境界では cut を引き継がない（cut は章内ローカル番号のため）。
    crop/fit/filter/pad/bg/hide は review からコピーし cue 側に持たせる（表示調整は asset でなく cue）。
    assetId は (chapter, cut) の asset。範囲外（asset 無し）なら None。
    """
    rev = _review_index(review_data)
    cues = []
    n = 0
    for ch, idxs in _chapter_segments(script):
        chapter = chapters[ch] if 0 <= ch < len(chapters) else {}
        ncuts = len(chapter.get("image_cuts") or [{}])   # 旧 meta と同じ（空でも最低1）
        groups = _cut_groups(idxs, script, ncuts)
        if groups is None:
            ncut = max(1, min(ncuts, len(idxs)))
            groups = [(ci, ci * len(idxs) // ncut, (ci + 1) * len(idxs) // ncut)
                      for ci in range(ncut)]
        for ci, lo, _hi in groups:
            anchor = script[idxs[lo]]
            key = (ch, ci)
            rcut = rev.get(key, {})
            pad = rcut.get("pad")
            n += 1
            cues.append({
                "id": f"image-cue-{n:04d}",
                "turnId": anchor.get("id"),
                "assetId": asset_index.get(key),
                "fit": rcut.get("fit"),
                "crop": rcut.get("crop"),
                "filter": rcut.get("filter"),
                "pad": pad if isinstance(pad, (int, float)) and not isinstance(pad, bool) else 0,
                "bg": rcut.get("bg"),
                "hide": bool(rcut.get("hide")),
            })
    return cues


# ---- visualSegments（6.4） ----

def _kf_key(vtype, value):
    """keyframe の同一性キー (type, value)。値なし種別は value=None（meta の _viz_point_times と同形）。"""
    v = value if (isinstance(value, int) and not isinstance(value, bool)) else None
    return (vtype, v)


def _keyframes_for_turns(member_turns):
    """所属セリフ群の vizPoints と進行フラグを keyframe 列へ正規化する。

    旧 meta の優先規則（_viz_point_times / _resolve_viz）に合わせる:
    - vizPoints を最優先。対応点（同じ type+value）が無いフラグだけをフォールバックで追加する。
    - viz_start / viz_end は「範囲」を表すフラグ＝keyframe には入れない（startTurnId/endTurnId で表現）。
    - panel_item / callout_item / compare_item の配列は項目ごとに展開する。
    pos は vizPoints のみ持つ（pos:0 と未指定を区別＝0 をそのまま保持）。id は seg 内連番。
    """
    covered = set()      # vizPoints が押さえている (type, value)＝同じフラグは抑制する
    for turn in member_turns:
        for p in turn.get("vizPoints") or []:
            if isinstance(p, dict) and p.get("type"):
                covered.add(_kf_key(p["type"], p.get("value")))
    kfs = []
    for turn in member_turns:
        tid = turn.get("id")
        # 1) vizPoints（文字位置つき・最優先）
        for p in turn.get("vizPoints") or []:
            if not isinstance(p, dict) or not p.get("type"):
                continue
            kf = {"turnId": tid, "type": p["type"], "pos": p.get("pos")}
            if isinstance(p.get("value"), int) and not isinstance(p.get("value"), bool):
                kf["value"] = p["value"]
            kfs.append(kf)
        # 2) フラグ（vizPoints に対応点が無いものだけ）
        if turn.get("reveal") and _kf_key("reveal", None) not in covered:
            kfs.append({"turnId": tid, "type": "reveal"})
        if turn.get("panel_event") == "shrink" and _kf_key("panel_event", None) not in covered:
            kfs.append({"turnId": tid, "type": "panel_event", "value": "shrink"})
        for flag in _FLAG_VALUED:
            if flag not in turn:
                continue
            v = turn[flag]
            if isinstance(v, bool):
                continue
            items = v if isinstance(v, list) else [v]
            for item in items:
                if not isinstance(item, int) or isinstance(item, bool):
                    continue
                if _kf_key(flag, item) in covered:
                    continue
                kfs.append({"turnId": tid, "type": flag, "value": item})
    return [{"id": f"kf-{i:03d}", **kf} for i, kf in enumerate(kfs, 1)]


def _entry_type(entry):
    """vizList エントリの種類を決める（type 明示＞ペイロードキー）。"""
    t = entry.get("type")
    if t:
        return t
    for k in _VIZ_PAYLOAD_KEYS:
        if k in entry and k != "calloutStyle":
            return k
    return None


def _entry_config(entry, vtype):
    """エントリの種類別ペイロードを config へまとめる（情報を落とさない）。"""
    cfg = {}
    for k in _VIZ_PAYLOAD_KEYS:
        if entry.get(k) is not None:
            cfg[k] = entry[k]
    if vtype and entry.get(vtype) is not None and vtype not in cfg:
        cfg[vtype] = entry[vtype]
    return cfg


def build_visual_segments(script, chapters):
    """chapters[].vizList と旧単一形式を、動画全体の visualSegments へ移す（6.4）。

    - id は chapter + vizSeg からグローバル一意（visual-CC-<sid>）＝章ごとに同じ ID でも衝突しない。
    - 所属セリフ＝同章で turn.vizSeg==id。その先頭/末尾を startTurnId/endTurnId へ。
    - 所属が無いエントリは旧 meta が描画しない（_resolve_viz_segments がスキップ）。突然表示されない
      よう status="orphaned"・アンカー None で「保持するが無効」にする（移行で再アンカー可能）。
    - vizPoints/フラグを keyframes へ正規化。
    - 旧単一形式（章直下の panel/quiz/...）は viz_start/viz_end か章全体を範囲に1セグメント。
    """
    segs = []
    for ch, chapter in enumerate(chapters or []):
        ch_turns = [t for t in script if (t.get("chapter", 0) == ch)]
        vl = chapter.get("vizList")
        if isinstance(vl, list) and vl:
            for entry in vl:
                if not isinstance(entry, dict):
                    continue
                sid = entry.get("id")
                members = [t for t in ch_turns if t.get("vizSeg") == sid]
                vtype = _entry_type(entry)
                orphaned = not members
                segs.append({
                    "id": f"visual-{ch:02d}-{sid}",
                    "type": vtype,
                    "mode": _TYPE_TO_MODE.get(vtype, "replace"),
                    "status": "orphaned" if orphaned else "active",
                    "startTurnId": None if orphaned else members[0].get("id"),
                    "endTurnId": None if orphaned else members[-1].get("id"),
                    "config": _entry_config(entry, vtype),
                    "keyframes": [] if orphaned else _keyframes_for_turns(members),
                    "sourceChapter": ch,
                })
            continue
        # 旧・単一形式（章直下に panel/quiz/... を直接持つ）。
        legacy = {k: chapter[k] for k in _VIZ_PAYLOAD_KEYS if chapter.get(k) is not None}
        if not legacy or not ch_turns:
            continue
        starts = [t for t in ch_turns if t.get("viz_start")]
        ends = [t for t in ch_turns if t.get("viz_end")]
        start_turn = starts[0] if starts else ch_turns[0]
        end_turn = ends[-1] if ends else ch_turns[-1]
        members = ch_turns
        vtype = next((k for k in _VIZ_PAYLOAD_KEYS if k in legacy and k != "calloutStyle"), None)
        segs.append({
            "id": f"visual-{ch:02d}-legacy",
            "type": vtype,
            "mode": _TYPE_TO_MODE.get(vtype, "replace"),
            "status": "active",
            "startTurnId": start_turn.get("id"),
            "endTurnId": end_turn.get("id"),
            "config": dict(legacy),
            "keyframes": _keyframes_for_turns(members),
            "sourceChapter": ch,
        })
    return segs


# ---- 変換本体（冪等） ----

# 編集モデルの「正」がどちらか。Phase 1 では常に legacy（毎回旧形式から再導出する）。
# Phase 2 で UI が編集を imageCues 等へ直接書くようになったら "editor" を立てて切り替える。
AUTHORITY_FIELD = "editorModelAuthority"


def is_migrated(data):
    """編集モデルの3キーと schemaVersion が揃っているか（保存・前方互換の判定用）。"""
    return (isinstance(data, dict)
            and data.get("schemaVersion") == SCHEMA_VERSION
            and all(k in data for k in ("assets", "imageCues", "visualSegments")))


def migrate(script_data, review_data=None):
    """script.json の dict（＋任意の review.json）を編集モデルへ変換して新 dict を返す。

    Phase 1（authority=legacy）: assets/imageCues/visualSegments は毎回 *旧形式から* 再導出する。
    こうしないと、新フィールド保存後に旧UIが review.json や vizList を変えても反映されない
    （正が曖昧になる）。旧形式が真である限り再導出は決定的＝冪等（2回適用で不変）。
    authority=="editor"（Phase 2以降）になったら編集モデル側を正とし、再導出しない。

    turn ID は唯一その場で確定して持ち越す情報。既存は維持し、未付与・重複のみ採番し直す。
    入力は破壊しない（deepcopy）。旧フィールドは残す＝旧経路（meta生成・UI）はそのまま読める。
    """
    if not isinstance(script_data, dict):
        return script_data
    data = copy.deepcopy(script_data)
    script = data.get("script")
    if not isinstance(script, list):
        return data
    assign_turn_ids(script)              # 既存IDは維持・未付与/重複のみ採番（常に実行＝冪等）
    if data.get(AUTHORITY_FIELD) == "editor":
        return data                      # 編集モデルが正＝再導出しない（Phase 2以降）
    chapters = data.get("chapters") or []
    assets, index = build_assets(chapters, review_data)
    data["schemaVersion"] = SCHEMA_VERSION
    data["assets"] = assets
    data["imageCues"] = build_image_cues(script, chapters, index, review_data)
    data["visualSegments"] = build_visual_segments(script, chapters)
    return data


# ---- ディスク移行（バックアップ付き・任意の明示実行） ----

def _backup_dir(root=".backups", label="editor-model-pre"):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(root, f"{label}-{stamp}")


def migrate_dir(dir_path, persist=False, backups_root=".backups"):
    """ディレクトリの script.json を読み、review.json と突き合わせて編集モデルへ変換する。

    persist=True のときだけ、変換前に script.json / review.json を .backups へ退避してから
    変換結果を script.json へ書き戻す（ユーザーデータを上書き破壊しない）。
    Returns: 変換後の dict（ファイルが無ければ None）。
    """
    sp = os.path.join(dir_path, "script.json")
    if not os.path.exists(sp):
        return None
    with open(sp, encoding="utf-8") as f:
        script_data = json.load(f)
    review_data = None
    rp = os.path.join(dir_path, "review.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            review_data = json.load(f)
    migrated = migrate(script_data, review_data)
    # 内容が実際に変わるときだけ退避＋書き戻し（再導出で同一なら何もしない＝再バックアップしない）。
    if persist and migrated != script_data:
        bdir = _backup_dir(backups_root)
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(sp, os.path.join(bdir, "script.json"))
        if os.path.exists(rp):
            shutil.copy2(rp, os.path.join(bdir, "review.json"))
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(migrated, f, ensure_ascii=False, indent=2)
    return migrated
