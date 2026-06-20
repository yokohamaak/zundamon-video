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
# セリフ（turn）に乗る大演出の進行フラグ。keyframe へ正規化する。
_FLAG_BOOL = ("reveal", "viz_start", "viz_end")
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
    """全セリフへ安定 turn ID を付与（in-place）。既存 ID は維持し、未付与だけ採番する。

    - 既存 ID（turn-NNNN 形式以外も含む）はそのまま尊重する。
    - 連番は既存の最大値の続きから振る＝再付与しても番号が動かない＝冪等。
    """
    existing = {t.get("id") for t in script if t.get("id")}
    mx = 0
    for tid in existing:
        n = _id_num(tid)
        if n is not None and n > mx:
            mx = n
    for turn in script:
        if not turn.get("id"):
            mx += 1
            turn["id"] = _format_turn_id(mx)
            existing.add(turn["id"])
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

def _effective_cut(turn, carry):
    """そのセリフの実効 cut（明示があれば採用・無ければ直前を継続）。

    main_story._cut_groups と同じ「欠落は直前のcutを継続」規則に合わせる。
    """
    c = turn.get("cut")
    if isinstance(c, int) and not isinstance(c, bool) and c >= 0:
        return c
    return carry


def build_image_cues(script, asset_index, review_data=None):
    """script順に (chapter, cut) を走査し、切り替わり位置へ imageCue を作る（6.3）。

    - 先頭と、直前から (chapter, cut) 組が変わったセリフへキューを作る。
    - 章境界では carry を 0 に戻す（cut は章内ローカル番号のため）。
    - crop/fit/filter/pad/bg/hide は review からコピーし、cue 側に持たせる
      （同じ素材を別クロップで複数箇所に置けるよう、表示調整は asset でなく cue）。
    - assetId は (chapter, cut) の asset。範囲外（asset 無し）なら None。
    """
    rev = _review_index(review_data)
    cues = []
    prev_key = None
    prev_chapter = None
    carry = 0
    n = 0
    for turn in script:
        ch = turn.get("chapter", 0)
        if not isinstance(ch, int) or isinstance(ch, bool):
            ch = 0
        if ch != prev_chapter:
            carry = 0
        cut = _effective_cut(turn, carry)
        carry = cut
        prev_chapter = ch
        key = (ch, cut)
        if key == prev_key:
            continue
        prev_key = key
        n += 1
        rcut = rev.get(key, {})
        pad = rcut.get("pad")
        cues.append({
            "id": f"image-cue-{n:04d}",
            "turnId": turn.get("id"),
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

def _keyframes_for_turns(member_turns):
    """所属セリフ群のフラグと vizPoints を keyframe 列へ正規化する。

    - turn 直下のフラグ（reveal/viz_start/viz_end/panel_event/panel_item/...）はそのセリフ全体に効く点。
    - vizPoints はセリフ内の文字位置(pos)を持つ点。pos を保持する。
    順序は member_turns の順＝script順。id は seg 内連番（kf-N）。
    """
    kfs = []
    for turn in member_turns:
        tid = turn.get("id")
        for flag in _FLAG_BOOL:
            if turn.get(flag):
                kfs.append({"turnId": tid, "type": flag})
        if turn.get("panel_event") == "shrink":
            kfs.append({"turnId": tid, "type": "panel_event", "value": "shrink"})
        for flag in _FLAG_VALUED:
            if flag in turn:
                v = turn[flag]
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, list)):
                    kfs.append({"turnId": tid, "type": flag, "value": v})
        for p in turn.get("vizPoints") or []:
            if not isinstance(p, dict) or "type" not in p:
                continue
            kf = {"turnId": tid, "type": p["type"], "pos": p.get("pos")}
            if "value" in p:
                kf["value"] = p["value"]
            kfs.append(kf)
    for i, kf in enumerate(kfs, 1):
        kf["id"] = f"kf-{i:03d}"
        # id を先頭に揃える（可読性のみ・順序は意味を持たない）。
    return [{"id": kf.pop("id"), **kf} for kf in kfs]


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
    - 所属が無いエントリは章の先頭/末尾セリフへアンカー（データを落とさない）。
    - フラグと vizPoints を keyframes へ正規化。
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
                if not members:
                    members = ch_turns  # アンカー不能なら章全体へ寄せる（消失防止）
                if not members:
                    continue
                vtype = _entry_type(entry)
                segs.append({
                    "id": f"visual-{ch:02d}-{sid}",
                    "type": vtype,
                    "mode": _TYPE_TO_MODE.get(vtype, "replace"),
                    "startTurnId": members[0].get("id"),
                    "endTurnId": members[-1].get("id"),
                    "config": _entry_config(entry, vtype),
                    "keyframes": _keyframes_for_turns(members),
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
            "startTurnId": start_turn.get("id"),
            "endTurnId": end_turn.get("id"),
            "config": dict(legacy),
            "keyframes": _keyframes_for_turns(members),
            "sourceChapter": ch,
        })
    return segs


# ---- 変換本体（冪等） ----

def is_migrated(data):
    """編集モデルへ変換済みか（schemaVersion と3キーが揃っているか）。"""
    return (isinstance(data, dict)
            and data.get("schemaVersion") == SCHEMA_VERSION
            and all(k in data for k in ("assets", "imageCues", "visualSegments")))


def migrate(script_data, review_data=None):
    """script.json の dict（＋任意の review.json）を編集モデルへ変換して新 dict を返す。

    冪等：変換済み入力には turn ID の補完のみ行い、assets/cues/segments は作り直さない
    （＝2回適用しても ID・asset・cue・segment が増えない）。入力は破壊しない（deepcopy）。
    既存フィールドは残す＝旧経路（meta生成・UI）は引き続きそのまま読める。
    """
    if not isinstance(script_data, dict):
        return script_data
    data = copy.deepcopy(script_data)
    script = data.get("script")
    if not isinstance(script, list):
        return data
    assign_turn_ids(script)              # 既存IDは維持・未付与のみ採番（常に実行＝冪等）
    if is_migrated(data):
        return data                      # 変換済みは再構築しない（冪等の核）
    chapters = data.get("chapters") or []
    assets, index = build_assets(chapters, review_data)
    data["schemaVersion"] = SCHEMA_VERSION
    data["assets"] = assets
    data["imageCues"] = build_image_cues(script, index, review_data)
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
    if persist and not is_migrated(script_data):
        bdir = _backup_dir(backups_root)
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(sp, os.path.join(bdir, "script.json"))
        if os.path.exists(rp):
            shutil.copy2(rp, os.path.join(bdir, "review.json"))
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(migrated, f, ensure_ascii=False, indent=2)
    return migrated
