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


def _issue_seq_id(data, list_key, prefix, counter_key):
    """prefix + 連番(4桁) を「最大連番+1」で採番（削除後も再利用しない）。

    data["idCounters"][counter_key] に発番済み最大値を永続化し、毎回+1する。カウンタが無い
    （旧データ/migrate直後）場合は既存の prefix+連番 から最大値で初期化する。万一カウンタが
    現存IDより小さくても衝突しないよう、現存最大ともmaxを取る。
    """
    counters = data.setdefault("idCounters", {})
    cur = counters.get(counter_key)
    existing_max = 0
    for x in data.get(list_key) or []:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", str(x.get("id") or ""))
        if m:
            existing_max = max(existing_max, int(m.group(1)))
    base = existing_max if cur is None else max(cur, existing_max)
    nxt = base + 1
    counters[counter_key] = nxt
    return f"{prefix}{nxt:04d}"


def _validate_asset(data, asset_id, *, allow_none):
    """assetId の妥当性を保証（不正参照を生成させない）。allow_none=Falseは実在必須。"""
    if asset_id is None:
        if allow_none:
            return
        raise ValueError("assetId が必要です")
    if find_asset(data, asset_id) is None:
        raise ValueError(f"asset {asset_id} は存在しません")


def _assert_no_start_collision(data, turn_id, *, ignore_cue_id=None):
    """同じ開始 turnId に別の cue が無いことを保証（開始位置衝突を生成させない）。"""
    other = cue_at(data, turn_id)
    if other is not None and other.get("id") != ignore_cue_id:
        raise ValueError(f"turnId {turn_id} には既に cue {other.get('id')} があります（差し替えは place_image）")


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
    """素材を1件追加して返す（ID は最大連番+1で採番＝削除後も再利用しない）。"""
    assets = data.setdefault("assets", [])
    aid = _issue_seq_id(data, "assets", "asset-", "asset")
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
    cid = _issue_seq_id(data, "imageCues", "image-cue-", "imageCue")
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
    """指定セリフを開始位置に画像 cue を追加する。

    検証（不正状態を生成させない）: turnId はスクリプトに存在必須／開始位置に既存 cue があれば拒否
    （差し替えは place_image）／assetId は None か実在素材／endTurnId 指定時は開始以降。
    """
    idx = _turn_index(data.get("script") or [])
    if turn_id not in idx:
        raise ValueError(f"turnId {turn_id} がスクリプトに存在しません")
    _assert_no_start_collision(data, turn_id)
    _validate_asset(data, asset_id, allow_none=True)
    if end_turn_id is not None:
        if end_turn_id not in idx:
            raise ValueError(f"endTurnId {end_turn_id} がスクリプトに存在しません")
        if idx[end_turn_id] < idx[turn_id]:
            raise ValueError("endTurnId が開始より前です（範囲逆転）")
    return _new_cue(data, turn_id, asset_id, end_turn_id, opts)


def place_image(data, turn_id, asset_id, **opts):
    """plan 3.4 の配置規則：開始位置に既存 cue があれば素材を差し替え、無ければ追加（add-or-replace）。

    assetId は実在必須（画像を置く操作なので未指定/不正は拒否）。
    """
    _validate_asset(data, asset_id, allow_none=False)
    existing = cue_at(data, turn_id)
    if existing is not None:
        existing["assetId"] = asset_id
        _apply_cue_opts(existing, opts)
        return existing
    return add_cue(data, turn_id, asset_id, **opts)


def replace_cue_asset(data, cue_id, asset_id):
    """cue の参照素材だけ差し替える（表示調整はそのまま＝同位置で別画像へ）。assetId は実在必須。"""
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    _validate_asset(data, asset_id, allow_none=False)
    cue["assetId"] = asset_id
    return cue


def move_cue(data, cue_id, new_turn_id):
    """cue の開始セリフを変更（タイムラインの左端ドラッグ）。

    検証: turnId 存在必須／移動先に別 cue があれば拒否（開始位置衝突）／endTurnId があれば
    移動後に範囲逆転しないこと。
    """
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    idx = _turn_index(data.get("script") or [])
    if new_turn_id not in idx:
        raise ValueError(f"turnId {new_turn_id} がスクリプトに存在しません")
    _assert_no_start_collision(data, new_turn_id, ignore_cue_id=cue_id)
    e = cue.get("endTurnId")
    if e is not None and e in idx and idx[e] < idx[new_turn_id]:
        raise ValueError("移動すると endTurnId より後になります（範囲逆転）")
    cue["turnId"] = new_turn_id
    return cue


def set_cue_range(data, cue_id, *, start_turn_id=None, end_turn_id="__keep__"):
    """cue の開始/終了セリフを調整。end_turn_id=None で「次のcueまで継続」へ戻す。

    start_turn_id 省略時は変更しない。end_turn_id は明示時のみ変更（"__keep__"=据え置き）。
    検証: 各 turnId 存在必須／開始移動先の衝突拒否／最終的に開始<=終了（範囲逆転を生成させない）。
    """
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    idx = _turn_index(data.get("script") or [])
    new_start = cue.get("turnId")
    if start_turn_id is not None:
        if start_turn_id not in idx:
            raise ValueError(f"turnId {start_turn_id} がスクリプトに存在しません")
        _assert_no_start_collision(data, start_turn_id, ignore_cue_id=cue_id)
        new_start = start_turn_id
    new_end = cue.get("endTurnId") if end_turn_id == "__keep__" else end_turn_id
    if new_end is not None:
        if new_end not in idx:
            raise ValueError(f"endTurnId {new_end} がスクリプトに存在しません")
        if idx[new_end] < idx.get(new_start, 0):
            raise ValueError("endTurnId が開始より前です（範囲逆転）")
    # 検証通過後にのみ適用（部分適用しない）。
    cue["turnId"] = new_start
    if new_end is None:
        cue.pop("endTurnId", None)
    else:
        cue["endTurnId"] = new_end
    return cue


def set_cue_opts(data, cue_id, **opts):
    """cue の表示調整(fit/crop/filter/pad/bg/hide)を更新する。0/None/空を区別して保持。

    指定キーのみ上書き（fit=None でクリア、pad=0 で0を保持、hide は bool 化）。
    """
    cue = find_cue(data, cue_id)
    if cue is None:
        raise ValueError(f"cue {cue_id} がありません")
    _apply_cue_opts(cue, opts)
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


def editor_attributions(assets, image_cues):
    """使用中(cueに参照されている)assetのattributionを {assetId: 出典} で返す（出現順・assetは一意）。

    未使用assetは含めない。build_credits/write_credits_txt が .values() で重複除去するので、
    同一出典が複数assetにあっても最終的なクレジットは1つになる。
    """
    asset_by = {a.get("id"): a for a in assets or []}
    out = {}
    for c in image_cues or []:
        aid = c.get("assetId")
        a = asset_by.get(aid)
        if a and a.get("attribution") and aid not in out:
            out[aid] = a["attribution"]
    return out


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


# ============================================================================
# Phase 3: 大演出（visualSegments / keyframes）の共通操作・正規化・meta解決アダプタ
# ----------------------------------------------------------------------------
# 方針:
#   - editor権威時、大演出の「正」は top-level visualSegments（章所有 vizList ではない）。
#   - meta解決は「visualSegments → legacy(vizList+turnフラグ) へ復元 → 既存 _resolve_viz_segments」
#     のアダプタで行う＝歴戦の resolver を再利用し等価性を構造的に保証する（二重実装しない）。
#   - keyframe の type/value/pos:0 を保持する（0 と未指定を区別）。orphaned は復元しない（突然有効化しない）。
# ============================================================================

# segment の type 別ペイロード（config に入る本体キー）。
_SEG_PAYLOAD_KEYS = ("panel", "quiz", "compare", "stat", "callouts")


def _seg_local_id(seg_id):
    """visual-CC-<sid> から元のローカル vizSeg id を取り出す（復元時に turn.vizSeg と一致させる）。"""
    m = re.match(r"visual-\d+-(.+)$", str(seg_id or ""))
    return m.group(1) if m else (seg_id or "")


def find_segment(data, seg_id):
    return next((s for s in data.get("visualSegments") or [] if s.get("id") == seg_id), None)


def _seg_bounds(idx, seg):
    """segment の [startIdx, endIdx]（script index）。解決不能や orphaned は None を返す。"""
    if seg.get("status") == "orphaned":
        return None
    s, e = idx.get(seg.get("startTurnId")), idx.get(seg.get("endTurnId"))
    if s is None or e is None or e < s:
        return None
    return (s, e)


# ---- visualSegments 共通操作 ----

def _range_chapter(data, s_idx, e_idx):
    """[s,e] 内の全 turn が同一章ならその章番号、章を跨ぐなら None（章跨ぎ禁止判定）。"""
    script = data.get("script") or []
    ch = script[s_idx].get("chapter", 0)
    for i in range(s_idx, e_idx + 1):
        if script[i].get("chapter", 0) != ch:
            return None
    return ch


def _active_overlap(data, s_idx, e_idx, ignore_id):
    """[s,e] が他の active セグメント範囲と重なるなら、その segment ID を返す（重複禁止判定）。"""
    idx = _turn_index(data.get("script") or [])
    for seg in data.get("visualSegments") or []:
        if seg.get("id") == ignore_id:
            continue
        b = _seg_bounds(idx, seg)
        if b is not None and e_idx >= b[0] and s_idx <= b[1]:
            return seg.get("id")
    return None


def _kf_dupe_key(kf_type, value):
    """keyframe の同一性キー (type, value)。値なし種別は value=None。"""
    return (kf_type, value if (isinstance(value, int) and not isinstance(value, bool)) else None)


def add_visual_segment(data, *, seg_type, start_turn_id, end_turn_id=None, config=None, mode=None):
    """大演出セグメントを追加。turnId は実在必須、開始<=終了、章跨ぎ・active重複は拒否。

    sourceChapter は開始セリフの章で確定（章をまたぐ範囲は不可＝単一章）。ID は最大連番+1で再利用しない。
    """
    idx = _turn_index(data.get("script") or [])
    if start_turn_id not in idx:
        raise ValueError(f"startTurnId {start_turn_id} がスクリプトに存在しません")
    end_turn_id = end_turn_id or start_turn_id
    if end_turn_id not in idx:
        raise ValueError(f"endTurnId {end_turn_id} がスクリプトに存在しません")
    si, ei = idx[start_turn_id], idx[end_turn_id]
    if ei < si:
        raise ValueError("終了が開始より前です（範囲逆転）")
    ch = _range_chapter(data, si, ei)
    if ch is None:
        raise ValueError("大演出の範囲が章を跨いでいます（単一章に収めてください）")
    ov = _active_overlap(data, si, ei, None)
    if ov is not None:
        raise ValueError(f"他の大演出({ov})と範囲が重複します")
    segs = data.setdefault("visualSegments", [])
    sid = _issue_seq_id(data, "visualSegments", "visual-", "visualSegment")
    seg = {"id": sid, "type": seg_type, "mode": mode or _TYPE_TO_MODE.get(seg_type, "replace"),
           "status": "active", "startTurnId": start_turn_id, "endTurnId": end_turn_id,
           "config": dict(config or {}), "keyframes": [], "sourceChapter": ch}
    segs.append(seg)
    return seg


def set_segment_range(data, seg_id, *, start_turn_id=None, end_turn_id=None):
    """セグメントの開始/終了セリフを変更。章跨ぎ・active重複・範囲逆転は拒否。検証通過後のみ適用。

    sourceChapter は新しい開始セリフの章へ更新（単一章に収まる前提）。
    """
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    idx = _turn_index(data.get("script") or [])
    ns = start_turn_id if start_turn_id is not None else seg.get("startTurnId")
    ne = end_turn_id if end_turn_id is not None else seg.get("endTurnId")
    if ns not in idx:
        raise ValueError(f"startTurnId {ns} がスクリプトに存在しません")
    if ne not in idx:
        raise ValueError(f"endTurnId {ne} がスクリプトに存在しません")
    si, ei = idx[ns], idx[ne]
    if ei < si:
        raise ValueError("終了が開始より前です（範囲逆転）")
    ch = _range_chapter(data, si, ei)
    if ch is None:
        raise ValueError("大演出の範囲が章を跨いでいます（単一章に収めてください）")
    ov = _active_overlap(data, si, ei, seg_id)
    if ov is not None:
        raise ValueError(f"他の大演出({ov})と範囲が重複します")
    seg["startTurnId"], seg["endTurnId"] = ns, ne
    seg["status"] = "active"                  # 有効範囲へ直したので active へ復帰
    seg.pop("orphanReason", None)
    seg["sourceChapter"] = ch
    # 範囲外へ出た keyframe は捨てる（端を縮めたら外側の変化点は無効）。範囲内は残す（修復後も保持）。
    seg["keyframes"] = [k for k in seg.get("keyframes") or []
                        if idx.get(k.get("turnId")) is not None and si <= idx[k["turnId"]] <= ei]
    return seg


def delete_visual_segment(data, seg_id):
    """セグメントを削除。"""
    before = data.get("visualSegments") or []
    data["visualSegments"] = [s for s in before if s.get("id") != seg_id]
    return len(data["visualSegments"]) != len(before)


def set_segment_config(data, seg_id, config):
    """セグメントの種類別設定(config)を差し替える（パネル項目・クイズ文面など）。"""
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    seg["config"] = dict(config or {})
    return seg


def delete_segment_item(data, seg_id, index):
    """panel/callouts の index 番目の項目を削除し、対応 keyframe の value を整合（in-place・原子的）。

    同 index の keyframe(panel_item/callout_item)を削除、index より大きい value を -1 する
    （項目削除で番号がずれて別項目を指すのを防ぐ）。config と keyframes を同時に更新。
    """
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    t = seg.get("type")
    cfg = seg.setdefault("config", {})
    if t == "panel":
        items = (cfg.setdefault("panel", {})).setdefault("items", [])
        kf_type = "panel_item"
    elif t == "callouts":
        items = cfg.setdefault("callouts", [])
        kf_type = "callout_item"
    else:
        raise ValueError("項目を持たないセグメントです（panel/callouts のみ）")
    if not (0 <= index < len(items)):
        raise ValueError("index が範囲外です")
    items.pop(index)
    out = []
    for k in seg.get("keyframes") or []:
        v = k.get("value")
        if k.get("type") == kf_type and isinstance(v, int) and not isinstance(v, bool):
            if v == index:
                continue                       # 削除した項目の keyframe は除去
            if v > index:
                k = {**k, "value": v - 1}       # 後ろの項目番号を繰り下げ
        out.append(k)
    seg["keyframes"] = out
    return seg


# ---- keyframe 共通操作（type/value/pos:0 を保持） ----

def _kf_next_id(seg):
    mx = 0
    for k in seg.get("keyframes") or []:
        m = re.fullmatch(r"kf-(\d+)", str(k.get("id") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"kf-{mx + 1:03d}"


def add_keyframe(data, seg_id, *, turn_id, kf_type, value=None, pos=None):
    """変化点を追加。turnId はセグメント範囲内必須。pos:0 と未指定(None)を区別して保持。"""
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    idx = _turn_index(data.get("script") or [])
    b = _seg_bounds(idx, seg)
    if b is None:
        raise ValueError("セグメントの範囲が無効です")
    if turn_id not in idx or not (b[0] <= idx[turn_id] <= b[1]):
        raise ValueError("turnId がセグメント範囲外です")
    dk = _kf_dupe_key(kf_type, value)
    if any(_kf_dupe_key(k.get("type"), k.get("value")) == dk for k in seg.get("keyframes") or []):
        raise ValueError(f"同じ変化点({kf_type},{value}) が既にあります")
    kf = {"id": _kf_next_id(seg), "turnId": turn_id, "type": kf_type}
    if value is not None:
        kf["value"] = value
    if pos is not None:                       # pos:0 を欠損扱いしない
        kf["pos"] = pos
    seg.setdefault("keyframes", []).append(kf)
    return kf


def move_keyframe(data, seg_id, kf_id, *, turn_id=None, pos="__keep__"):
    """変化点を別セリフ／文字位置へ移す。範囲外 turnId は拒否。pos="__keep__"=据え置き。"""
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    kf = next((k for k in seg.get("keyframes") or [] if k.get("id") == kf_id), None)
    if kf is None:
        raise ValueError(f"keyframe {kf_id} がありません")
    idx = _turn_index(data.get("script") or [])
    b = _seg_bounds(idx, seg)
    if turn_id is not None:
        if b is None or turn_id not in idx or not (b[0] <= idx[turn_id] <= b[1]):
            raise ValueError("turnId がセグメント範囲外です")
        kf["turnId"] = turn_id
    if pos != "__keep__":
        if pos is None:
            kf.pop("pos", None)
        else:
            kf["pos"] = pos
    return kf


def delete_keyframe(data, seg_id, kf_id):
    seg = find_segment(data, seg_id)
    if seg is None:
        raise ValueError(f"segment {seg_id} がありません")
    before = seg.get("keyframes") or []
    seg["keyframes"] = [k for k in before if k.get("id") != kf_id]
    return len(seg["keyframes"]) != len(before)


# ---- normalize / reconcile ----

def _orphan_segment(seg, reason):
    """無効化＝status と orphanReason だけ変更。端点・keyframes・config は保持（修復可能に）。"""
    seg["status"] = "orphaned"
    seg["orphanReason"] = reason


def normalize_visual_segments(data):
    """visualSegments を正規化（in-place）。冪等・決定的。

    - 範囲解決不能（turnId 欠落・逆転）／章跨ぎ → orphaned（status/orphanReason のみ変更、端点・
      keyframes・config は保持＝後で set_segment_range で修復可能）。突然有効化はしない。
    - 既に orphaned のものは触らない（自動再活性化しない／データ保持）。
    - active の keyframe: turnId 欠落 or 範囲外は除去、同一(type,value)は最小turn indexの1つへ（決定的）。
    - active 同士の範囲重複は「開始が早い→ID昇順」を残し、重なる後続を orphaned（決定的に競合解消）。
    - 並びは active を開始セリフ順、orphaned は末尾。
    """
    idx = _turn_index(data.get("script") or [])
    segs = data.get("visualSegments") or []
    # 1) 既存orphanedは保持。activeは妥当性検査→無効ならorphaned、有効ならkeyframeをトリム＋重複除去。
    for seg in segs:
        if seg.get("status") == "orphaned":
            continue
        s, e = idx.get(seg.get("startTurnId")), idx.get(seg.get("endTurnId"))
        if s is None or e is None or e < s:
            _orphan_segment(seg, "unresolved")
            continue
        if _range_chapter(data, s, e) is None:
            _orphan_segment(seg, "cross-chapter")
            continue
        seg["status"] = "active"
        seg.pop("orphanReason", None)
        seg["sourceChapter"] = _range_chapter(data, s, e)
        kfs = sorted((k for k in seg.get("keyframes") or []
                      if idx.get(k.get("turnId")) is not None and s <= idx[k["turnId"]] <= e),
                     key=lambda k: idx[k["turnId"]])
        seen, out = set(), []
        for k in kfs:
            dk = _kf_dupe_key(k.get("type"), k.get("value"))
            if dk in seen:
                continue
            seen.add(dk)
            out.append(k)
        seg["keyframes"] = out
    # 2) active 重複の決定的解消（開始が早い→ID昇順を残し、重なる後続を orphaned）。
    accepted = []
    for seg in sorted([s for s in segs if s.get("status") == "active"],
                      key=lambda sg: (idx[sg["startTurnId"]], str(sg.get("id") or ""))):
        b = (idx[seg["startTurnId"]], idx[seg["endTurnId"]])
        if any(b[0] <= ab[1] and ab[0] <= b[1] for ab in accepted):
            _orphan_segment(seg, "overlap")
        else:
            accepted.append(b)
    # active を開始順・orphaned は末尾（端点は保持しているので start index で安定ソート）。
    data["visualSegments"] = sorted(
        segs, key=lambda sg: (sg.get("status") == "orphaned", idx.get(sg.get("startTurnId"), 1 << 30)))
    return data


def reconcile_visual_segments(data, prev_turn_ids=None):
    """turn 削除・並べ替えに追従してセグメント／keyframe を整合（in-place）。冪等。

    prev_turn_ids があれば、消えた端点・keyframe を「元の並びで次に残るセリフ」へ寄せる。
    端点が消えて寄せ先も無ければ orphaned 化（突然有効化も消失もしない＝保持）。
    """
    idx = _turn_index(data.get("script") or [])
    if prev_turn_ids:
        succ = _successor_map(prev_turn_ids, set(idx))
        for seg in data.get("visualSegments") or []:
            for key in ("startTurnId", "endTurnId"):
                if seg.get(key) is not None and seg.get(key) not in idx:
                    seg[key] = succ.get(seg[key])
            for k in seg.get("keyframes") or []:
                if k.get("turnId") not in idx:
                    k["turnId"] = succ.get(k.get("turnId"))
    return normalize_visual_segments(data)


# ---- editor → legacy(vizList + turnフラグ) 復元アダプタ（meta解決用・等価保証） ----

def reconstruct_legacy_viz(turns, chapters, visual_segments):
    """visualSegments から legacy の vizList(章) と turnフラグ/vizPoints を復元する（in-place）。

    editor権威の meta 生成で、既存 _resolve_viz_segments をそのまま使うためのアダプタ。
    visualSegments は移行で vizList+フラグから導出されたため、その逆変換は元を再現し meta 等価になる。
    orphaned セグメントは復元しない（突然有効化しない）。turns/chapters は呼び出し側のコピーを渡すこと。
    """
    idx = {t.get("id"): i for i, t in enumerate(turns) if t.get("id") is not None}
    VIZ_FIELDS = ("vizSeg", "reveal", "viz_start", "viz_end", "panel_event",
                  "panel_item", "callout_item", "compare_item", "vizPoints")
    for t in turns:
        for f in VIZ_FIELDS:
            t.pop(f, None)
    for ch in chapters:
        for k in (*_SEG_PAYLOAD_KEYS, "calloutStyle", "vizList"):
            ch.pop(k, None)
    panel_items = {}   # turn index -> [panel_item値,...]（配列復元用）
    for seg in visual_segments or []:
        b = _seg_bounds(idx, seg)
        if b is None or not (0 <= (seg.get("sourceChapter") or 0) < len(chapters)):
            continue
        ch = chapters[seg["sourceChapter"]]
        sid = _seg_local_id(seg.get("id"))
        vtype = seg.get("type")
        cfg = seg.get("config") or {}
        if sid == "legacy":
            # 旧・単一形式：章直下へ payload を戻す（vizSeg なし＝resolver の else 分岐）。
            for k in (*_SEG_PAYLOAD_KEYS, "calloutStyle"):
                if k in cfg:
                    ch[k] = cfg[k]
        else:
            entry = {"id": sid, "type": vtype}
            for k in (*_SEG_PAYLOAD_KEYS, "calloutStyle"):
                if k in cfg:
                    entry[k] = cfg[k]
            ch.setdefault("vizList", []).append(entry)
            for i in range(b[0], b[1] + 1):           # 連続メンバーシップを復元
                if turns[i].get("chapter", 0) == seg["sourceChapter"]:
                    turns[i]["vizSeg"] = sid
        for kf in seg.get("keyframes") or []:
            ti = idx.get(kf.get("turnId"))
            if ti is None or not (b[0] <= ti <= b[1]):
                continue
            t = turns[ti]
            ktype, kval, kpos = kf.get("type"), kf.get("value"), kf.get("pos")
            if kpos is not None:                       # vizPoint（文字位置つき）
                vp = {"type": ktype, "pos": kpos}
                if isinstance(kval, int) and not isinstance(kval, bool):
                    vp["value"] = kval
                t.setdefault("vizPoints", []).append(vp)
            elif ktype == "reveal":
                t["reveal"] = True
            elif ktype == "panel_event":
                t["panel_event"] = "shrink"
            elif ktype == "panel_item" and isinstance(kval, int) and not isinstance(kval, bool):
                panel_items.setdefault(ti, []).append(kval)
            elif ktype in ("callout_item", "compare_item") and isinstance(kval, int) and not isinstance(kval, bool):
                t[ktype] = kval
    for ti, vals in panel_items.items():               # panel_item は単数 int / 複数は配列
        vals = sorted(set(vals))
        turns[ti]["panel_item"] = vals[0] if len(vals) == 1 else vals
    return turns, chapters
