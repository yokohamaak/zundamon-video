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
import re
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


# ============================================================================
# Phase 2: 画像編集（assets / imageCues）の共通操作・正規化・meta解決・authority切替
# ----------------------------------------------------------------------------
# 設計方針（dev-thoroughness-checklist / Phase 2指示書）:
#   - 検証/ID発行/整合(normalize/reconcile)はここへ集約し、全UI経路から呼ぶ前提の純関数にする。
#   - 配列を直接書き換えず、必ずこの関数群を通す（追加/差し替え/移動/範囲変更/削除/並べ替え）。
#   - 0 / null / 未指定 を同一視しない（pad:0・crop座標0・空配列を欠損扱いしない）。
#   - imageCue は turnId(開始) を必須、endTurnId(終了) は任意。endTurnId が無ければ「次のcueまで継続」。
#     これにより Phase 1 の移行出力（endTurnId 無し＝継続）と完全に等価のまま、UI で範囲も持てる。
# ============================================================================

# 表示調整キー（asset でなく cue 側に持つ。同素材を別クロップで複数配置できるようにするため）。
CUE_OPT_KEYS = ("fit", "crop", "filter", "pad", "bg")


def _turn_index(script):
    """turnId → script内index（最初の出現を採用。重複は assign_turn_ids 側で解消済みの前提）。"""
    idx = {}
    for i, t in enumerate(script or []):
        tid = t.get("id")
        if tid is not None and tid not in idx:
            idx[tid] = i
    return idx


def _next_seq_id(existing, prefix):
    """prefix + 連番(4桁) で existing と衝突しない ID を返す（再利用しない）。"""
    n = 1
    while True:
        cand = f"{prefix}{n:04d}"
        if cand not in existing:
            return cand
        n += 1


def _asset_origin(asset):
    """asset の生成元 (chapter, cut)。移行ID 'asset-CC-II' から決定的に復元（未取得時のplaceholder名用）。"""
    m = re.match(r"asset-(\d+)-(\d+)$", str(asset.get("id") or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    sc = asset.get("sourceChapter")
    return (sc if isinstance(sc, int) else 0), 0


# ---- assets 操作 ----

def find_asset(data, asset_id):
    return next((a for a in data.get("assets") or [] if a.get("id") == asset_id), None)


def add_asset(data, *, file=None, query=None, queryJa=None, kind=None,
              attribution=None, sourceChapter=None):
    """素材を1件追加して返す（ID は既存と衝突しない asset-NNNN を採番）。"""
    assets = data.setdefault("assets", [])
    aid = _next_seq_id({a.get("id") for a in assets}, "asset-")
    asset = {"id": aid, "file": file, "query": _clean_str(query), "queryJa": _clean_str(queryJa),
             "kind": _clean_str(kind), "attribution": _clean_str(attribution),
             "sourceChapter": sourceChapter}
    assets.append(asset)
    return asset


def asset_usage(data, asset_id):
    """その asset を参照している imageCue の ID 一覧（使用中判定・削除可否・共有判定に使う）。"""
    return [c.get("id") for c in data.get("imageCues") or [] if c.get("assetId") == asset_id]


def can_delete_asset(data, asset_id):
    """参照 cue が無ければ削除可。使用中の素材は無条件削除しない（plan 5.1）。"""
    return not asset_usage(data, asset_id)


def delete_asset(data, asset_id, *, force=False):
    """素材を削除。参照 cue があれば force 必須（明示的な一括解除）。

    force のとき参照 cue も削除する（ファイル本体は消さない＝呼び出し側の責務）。
    Returns: 削除した cue ID 一覧。参照ありで force=False なら ValueError。
    """
    used = asset_usage(data, asset_id)
    if used and not force:
        raise ValueError(f"asset {asset_id} は {len(used)} 件の cue から参照中（force 指定で一括解除）")
    removed = []
    if force and used:
        keep = [c for c in data.get("imageCues") or [] if c.get("assetId") != asset_id]
        removed = used
        data["imageCues"] = keep
    data["assets"] = [a for a in data.get("assets") or [] if a.get("id") != asset_id]
    return removed


def unused_assets(data):
    """どの cue からも参照されていない asset の ID 一覧（「未使用」フィルタ用）。"""
    used = {c.get("assetId") for c in data.get("imageCues") or []}
    return [a.get("id") for a in data.get("assets") or [] if a.get("id") not in used]


def broken_cue_refs(data):
    """assetId が存在しない asset を指す cue の ID 一覧（「壊れた参照」＝未使用assetとは区別）。"""
    ids = {a.get("id") for a in data.get("assets") or []}
    return [c.get("id") for c in data.get("imageCues") or []
            if c.get("assetId") is not None and c.get("assetId") not in ids]


# ---- imageCues 操作 ----

def _apply_cue_opts(cue, opts):
    """cue へ表示調整値を適用。0/空文字/None を区別して保持（pad:0 を落とさない）。hide は bool 化。"""
    for k in CUE_OPT_KEYS:
        if k in opts:
            cue[k] = opts[k]
    if "hide" in opts:
        cue["hide"] = bool(opts["hide"])
    return cue


def _new_cue(data, turn_id, asset_id, end_turn_id, opts):
    cues = data.setdefault("imageCues", [])
    cid = _next_seq_id({c.get("id") for c in cues}, "image-cue-")
    cue = {"id": cid, "turnId": turn_id, "assetId": asset_id,
           "fit": None, "crop": None, "filter": None, "pad": 0, "bg": None, "hide": False}
    if end_turn_id is not None:
        cue["endTurnId"] = end_turn_id
    _apply_cue_opts(cue, opts)
    cues.append(cue)
    return cue


def find_cue(data, cue_id):
    return next((c for c in data.get("imageCues") or [] if c.get("id") == cue_id), None)


def cue_at(data, turn_id):
    """その turnId を開始位置に持つ cue（差し替え/追加の判定用）。無ければ None。"""
    return next((c for c in data.get("imageCues") or [] if c.get("turnId") == turn_id), None)


def add_cue(data, turn_id, asset_id=None, *, end_turn_id=None, **opts):
    """指定セリフを開始位置に画像 cue を追加する。turnId はスクリプトに存在必須。"""
    if turn_id not in _turn_index(data.get("script") or []):
        raise ValueError(f"turnId {turn_id} がスクリプトに存在しません")
    return _new_cue(data, turn_id, asset_id, end_turn_id, opts)


def place_image(data, turn_id, asset_id, **opts):
    """plan 3.4 の配置規則：開始位置に既存 cue があれば素材を差し替え、無ければ追加（add-or-replace）。"""
    existing = cue_at(data, turn_id)
    if existing is not None:
        existing["assetId"] = asset_id
        _apply_cue_opts(existing, opts)
        return existing
    return add_cue(data, turn_id, asset_id, **opts)


def replace_cue_asset(data, cue_id, asset_id):
    """cue の参照素材だけ差し替える（表示調整はそのまま＝同位置で別画像へ）。"""
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    cue["assetId"] = asset_id
    return cue


def move_cue(data, cue_id, new_turn_id):
    """cue の開始セリフを変更（タイムラインの左端ドラッグ）。turnId は存在必須。"""
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    if new_turn_id not in _turn_index(data.get("script") or []):
        raise ValueError(f"turnId {new_turn_id} がスクリプトに存在しません")
    cue["turnId"] = new_turn_id
    return cue


def set_cue_range(data, cue_id, *, start_turn_id=None, end_turn_id="__keep__"):
    """cue の開始/終了セリフを調整。end_turn_id=None で「次のcueまで継続」へ戻す。

    start_turn_id 省略時は変更しない。end_turn_id は明示時のみ変更（"__keep__"=据え置き）。
    """
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    idx = _turn_index(data.get("script") or [])
    if start_turn_id is not None:
        if start_turn_id not in idx:
            raise ValueError(f"turnId {start_turn_id} がスクリプトに存在しません")
        cue["turnId"] = start_turn_id
    if end_turn_id != "__keep__":
        if end_turn_id is None:
            cue.pop("endTurnId", None)
        else:
            if end_turn_id not in idx:
                raise ValueError(f"turnId {end_turn_id} がスクリプトに存在しません")
            cue["endTurnId"] = end_turn_id
    return cue


def delete_cue(data, cue_id):
    """cue を削除（素材ファイルは消さない＝直前の画像が継続）。"""
    before = data.get("imageCues") or []
    data["imageCues"] = [c for c in before if c.get("id") != cue_id]
    return len(data["imageCues"]) != len(before)


# ---- normalize / reconcile ----

def normalize_cues(data):
    """imageCues の構造を正規化（in-place）。冪等。

    - 孤立参照: turnId がスクリプトに無い cue は除去。
    - 範囲逆転: endTurnId の index が turnId より前なら endTurnId を外す（＝次のcueまで継続へ）。
    - 重複: 同じ開始 turnId の cue は最初の1つだけ残す。
    - 並び: 開始セリフの出現順にソート（描画は順序前提）。pad:0/crop:0 等は保持（落とさない）。
    壊れた assetId（存在しないasset参照）は区別して残す（broken_cue_refs で検出）。
    """
    idx = _turn_index(data.get("script") or [])
    out, seen_start = [], set()
    for c in sorted((data.get("imageCues") or []),
                    key=lambda c: idx.get(c.get("turnId"), 1 << 30)):
        tid = c.get("turnId")
        if tid not in idx:
            continue                       # 孤立 cue は除去
        if tid in seen_start:
            continue                       # 同一開始の重複は最初だけ
        seen_start.add(tid)
        e = c.get("endTurnId")
        if e is not None and (e not in idx or idx[e] < idx[tid]):
            c.pop("endTurnId", None)       # 範囲逆転/無効終了は継続扱いへ
        out.append(c)
    data["imageCues"] = out
    return data


def reconcile_cues(data, prev_turn_ids=None):
    """turn の削除・並べ替えに追従して cue を整合（in-place）。冪等。

    prev_turn_ids（変更前の turnId 順）があれば、消えた開始 turnId を「元の並びで次に残る
    セリフ」へ寄せる（plan 7: キュー位置のセリフ削除→次へ移す・無ければ削除）。
    endTurnId が消えた場合は継続扱いへ戻す。最後に normalize_cues で重複/逆転/並びを整える。
    """
    idx = _turn_index(data.get("script") or [])
    if prev_turn_ids:
        succ = _successor_map(prev_turn_ids, set(idx))
        for c in data.get("imageCues") or []:
            if c.get("turnId") not in idx:
                c["turnId"] = succ.get(c.get("turnId"))   # 次の生存セリフ（無ければ None→normalizeで除去）
            e = c.get("endTurnId")
            if e is not None and e not in idx:
                c["endTurnId"] = succ.get(e)
                if c["endTurnId"] is None:
                    c.pop("endTurnId", None)
    return normalize_cues(data)


def _successor_map(prev_ids, surviving):
    """変更前の順序 prev_ids について、各IDの「以降で最初に生存するID」を返す（自分が生存なら自分）。"""
    succ, nxt = {}, None
    for tid in reversed(prev_ids):
        if tid in surviving:
            nxt = tid
        succ[tid] = tid if tid in surviving else nxt
    return succ


# ---- editor → 既存meta形式への解決（純関数） ----

def _resolve_cue(cue, asset_by):
    """1つの cue を「topicに載せる画像情報」へ解決する（legacy build_chapter_topics と等価になるよう）。

    Returns: {cueId, blank?|image+fit/crop/filter/pad/bg/credit | note+placeholder(ch,ci)}。
    """
    res = {"cueId": cue.get("id")}
    if cue.get("hide"):
        res["blank"] = True
        return res
    asset = asset_by.get(cue.get("assetId"))
    if asset and asset.get("file"):
        res["image"] = asset["file"]
        if cue.get("fit"):
            res["fit"] = cue["fit"]
        elif asset.get("kind") == "subject":
            res["fit"] = "contain"           # subjectは全体表示（legacyと同じ既定）
        for k in ("crop", "filter", "pad", "bg"):
            if cue.get(k):                    # 0/None/空は載せない（legacyの `if opt.get(k)` と一致）
                res[k] = cue[k]
        if asset.get("attribution"):
            res["credit"] = asset["attribution"]
    elif asset is not None:
        res["note"] = asset.get("query")     # build側で None→章タイトルへフォールバック
        res["placeholder"] = _asset_origin(asset)
    else:
        res["blank"] = True                  # 壊れた/未指定の assetId は画像なし
    return res


def resolve_turn_images(script, assets, image_cues):
    """各セリフ(index)で有効な画像解決を返す（[plan 4] 画像は次のcueまで継続）。

    Returns: len(script) の配列。各要素は _resolve_cue の dict、または None（cue無し＝画像なし）。
    - cue は開始セリフ index 順。endTurnId があればそこまで、無ければ次cueの直前まで継続。
    - 同一開始の重複は最初を採用、孤立 turnId の cue は無視（normalize 相当を解決時にも保証）。
    """
    idx = _turn_index(script)
    asset_by = {a.get("id"): a for a in (assets or [])}
    cues = []
    for c in image_cues or []:
        si = idx.get(c.get("turnId"))
        if si is None:
            continue
        ei = idx.get(c.get("endTurnId")) if c.get("endTurnId") else None
        cues.append((si, ei, c))
    cues.sort(key=lambda x: x[0])
    seen, uniq = set(), []
    for si, ei, c in cues:
        if si in seen:
            continue
        seen.add(si)
        uniq.append((si, ei, c))
    n = len(script)
    plan = [None] * n
    for k, (si, ei, c) in enumerate(uniq):
        next_start = uniq[k + 1][0] if k + 1 < len(uniq) else n
        end = min(ei, next_start - 1) if (ei is not None and ei >= si) else next_start - 1
        res = _resolve_cue(c, asset_by)
        for i in range(si, min(end, n - 1) + 1):
            plan[i] = res
    return plan


# ---- legacy → editor authority 切替（明示・原子的。自動では呼ばない） ----

def switch_to_editor(script_data, review_data=None):
    """編集モデルを「正」へ切り替える。変換・整合が成功したときだけ editor を立てる（原子的）。

    旧形式（image_cuts/cut/review.json）から assets/imageCues を構築し、normalize/reconcile を通し、
    全 cue の turnId がスクリプトに存在することを検証してから editorModelAuthority="editor" にする。
    途中で不整合が出れば例外を投げ、legacy のまま返さない（中途半端な混在を作らない）。
    Returns: editor 化した新 dict。
    """
    data = migrate(script_data, review_data)          # legacy→新フィールド導出（非破壊・冪等）
    if not isinstance(data.get("script"), list):
        raise ValueError("script がありません")
    reconcile_cues(data)                              # 孤立/重複/逆転を解消
    idx = _turn_index(data["script"])
    for c in data.get("imageCues") or []:
        if c.get("turnId") not in idx:
            raise ValueError(f"cue {c.get('id')} の turnId が解決できません")
    # 全 cue 検証通過後にのみ権威を切替（原子的）。
    data[AUTHORITY_FIELD] = "editor"
    return data
