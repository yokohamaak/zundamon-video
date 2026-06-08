"""
APOD画像プラン（クロップ）生成モジュール

APOD1枚画像を gemini-2.5-flash（マルチモーダル・無料枠）に見せ、「複数カット風」に
使える注目クロップ領域を座標で出させ、実際に切り出して動画の中央ビジュアルに使う。
単調さ解消（APOD1枚はNG）の自動化の中核。APODは公開画像なので送信OK（CLAUDE.md非抵触）。

設計（apod_script.py に倣う）:
- build_prompt / parse_crops / box_to_pixels / assign_cut_times は純関数でテスト可能。
- google.genai・Pillow は plan_crops / render_crops 内で遅延import（テストに依存を持ち込まない）。

bbox形式: Geminiが学習している [ymin, xmin, ymax, xmax]（0-1000正規化）を採用。
注意（実証2026-06-07）: box座標は信頼できるが、label/reason等のproseは方向違い・小天体の
ハルシネーションがある。**proseは字幕/テロップに流用しない（座標のみ採用）**。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_COUNT = 5
MIN_CROP_PX = 50      # これ未満の辺はスキップ
DEFAULT_MAX_SIDE = 1920  # 書き出し長辺の上限（1080p＋Ken Burnsズーム余裕）
MIN_FOCUS = 0.06     # focus方式：正規化での最小枠サイズ（これ未満の辺の枠はスキップ）

PROMPT = """\
あなたは宇宙系YouTube動画の映像編集者です。
この1枚のNASA天文写真から、動画で「複数カット風」に見せるための注目クロップ領域を {n} 個提案してください。

題材情報（参考）:
タイトル: {title}
解説: {explanation}

要件:
- 各クロップは画像内の見ごたえのある被写体・構造（銀河の中心、星雲の濃い部分、特徴的な天体、面白い隅 など）を切り取ること。
- ズームの度合いに変化をつける（全体を広く＝1つ、寄りのクロップ＝複数）。互いになるべく重ならない多様な領域にする。
- 各クロップは縦長すぎ・横長すぎを避け、おおむね横:縦が 1:1〜16:9 の範囲に収める。
- 領域は box_2d = [ymin, xmin, ymax, xmax] で、画像全体を 0〜1000 に正規化した整数で表す。

出力はマークダウンのコードブロックを使わず、次のJSONだけ:
{{
  "crops": [
    {{"label": "短い日本語ラベル(8字以内)", "box_2d": [ymin, xmin, ymax, xmax], "reason": "なぜ見どころか(日本語1行)"}}
  ]
}}
"""


def build_prompt(title, explanation, n=DEFAULT_COUNT):
    """画像プラン生成プロンプト（純関数）。"""
    return PROMPT.format(n=n, title=title or "(不明)", explanation=(explanation or "")[:1500])


def parse_crops(text):
    """Geminiの応答からクロップ配列を取り出す（純関数）。box_2dが4要素のものだけ返す。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.index("{")
    data, _ = json.JSONDecoder().raw_decode(text, start)
    crops = data.get("crops")
    if not isinstance(crops, list) or not crops:
        raise ValueError("応答に有効な crops がありません")
    valid = []
    for c in crops:
        box = c.get("box_2d")
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            valid.append(c)
        else:
            logger.warning(f"box_2d不正のためスキップ: {c}")
    if not valid:
        raise ValueError("有効な box_2d を持つクロップがありません")
    return valid


def box_to_pixels(box_2d, w, h):
    """[ymin,xmin,ymax,xmax](0-1000) を画素 (left, top, right, bottom) へ（範囲クランプ済）。"""
    ymin, xmin, ymax, xmax = box_2d
    left = round(xmin / 1000 * w)
    top = round(ymin / 1000 * h)
    right = round(xmax / 1000 * w)
    bottom = round(ymax / 1000 * h)
    left, right = sorted((max(0, left), min(w, right)))
    top, bottom = sorted((max(0, top), min(h, bottom)))
    return left, top, right, bottom


def box_to_focus(box_2d):
    """[ymin,xmin,ymax,xmax](0-1000) を正規化フォーカス枠 {l,t,r,b}(0-1) へ（クランプ＋整列）。

    focus方式（切り出さず元画像に枠を重ねる）用。動画側が l/t/r/b を画像左上原点の
    割合として解釈し、枠描画とズーム中心に使う。
    """
    ymin, xmin, ymax, xmax = box_2d
    clamp = lambda v: min(max(v / 1000.0, 0.0), 1.0)  # noqa: E731
    l, r = sorted((clamp(xmin), clamp(xmax)))
    t, b = sorted((clamp(ymin), clamp(ymax)))
    return {"l": round(l, 4), "t": round(t, 4), "r": round(r, 4), "b": round(b, 4)}


def assign_cut_times(crops, turns):
    """
    クロップ列を台本ターンに沿って時間割り当てする（純関数）。
    各カットは連続するターンの塊を担当し、切替はターン境界で起きる（文の途中で切れない）。
    [0, total] を隙間なく覆い、重ならない。

    Args:
        crops: [{label, box_2d, file?, ...}]（順序維持）
        turns: [{start, end}, ...]（TTS後のターン時刻）
    Returns:
        crops に start/end を付与した新リスト（カット数は min(len(crops), len(turns)) に丸める）
    """
    n = len(crops)
    if not turns or n == 0:
        return []
    n = min(n, len(turns))
    total = turns[-1]["end"]
    out = []
    for i in range(n):
        lo = i * len(turns) // n
        hi = (i + 1) * len(turns) // n  # exclusive
        start = 0.0 if i == 0 else turns[lo]["start"]
        end = total if i == n - 1 else turns[hi]["start"]
        out.append({**crops[i], "start": round(float(start), 3), "end": round(float(end), 3)})
    return out


def _segments_by_phase(turns):
    """ターン列を phase の連続塊へ。Returns: [(phase, [turn_index,...]), ...]。phase欠落はfact扱い。"""
    segs = []
    for i, t in enumerate(turns):
        ph = t.get("phase") or "fact"
        if segs and segs[-1][0] == ph:
            segs[-1][1].append(i)
        else:
            segs.append((ph, [i]))
    return segs


def assign_cuts_by_phase(turns, crops, stocks, manuals=None):
    """
    台本ターンを phase の連続塊（セグメント）に分け、各セグメントへ画像を割り当てる（純関数）。
    - fact セグメント → stock実写を優先
    - if   セグメント → manual想像イラストを優先
    - intro/outro セグメント → APODクロップ
    優先プールが空ならcrop、cropも空なら残る非空プールへフォールバック。
    セグメント内は均等割り。[0,total] を隙間なく覆い、重ならない。

    Args:
        turns: [{start, end, phase}]（phase欠落は fact 扱い）
        crops: [{file, label?, ...}]  APODクロップ
        stocks: [{file, title?, ...}] images.nasa.gov 実写
        manuals: [{file|None, label, prompt, target, ...}] 想像イラスト（fileがNoneでもプレースホルダ枠として配置）
    Returns:
        [{..., start, end}]（時刻順）。全プール空なら []（呼び出し側がAPOD1枚へフォールバック）。
    """
    manuals = manuals or []
    if not turns or (not crops and not stocks and not manuals):
        return []
    total = turns[-1]["end"]
    pools = {"crop": crops, "stock": stocks, "manual": manuals}
    ptr = {"crop": 0, "stock": 0, "manual": 0}
    # phase→優先プール種別
    pref = {"fact": "stock", "if": "manual"}
    out = []
    for phase, idxs in _segments_by_phase(turns):
        kind = pref.get(phase, "crop")
        if not pools[kind]:
            kind = "crop" if crops else ("stock" if stocks else "manual")
        pool = pools[kind]
        if not pool:
            continue
        m = min(len(idxs), len(pool))
        for b in range(m):
            lo = b * len(idxs) // m
            img = pool[(ptr[kind] + b) % len(pool)]
            out.append({**img, "start": round(float(turns[idxs[lo]]["start"]), 3), "end": 0.0})
        ptr[kind] += m
    # 連結なので各カットの end を次カットの start に合わせ、先頭=0・末尾=total で隙間/重なりを除去。
    out[0]["start"] = 0.0
    for i in range(len(out) - 1):
        out[i]["end"] = out[i + 1]["start"]
    out[-1]["end"] = round(float(total), 3)
    return out


def plan_crops(image_path, title, explanation, n=DEFAULT_COUNT, model="gemini-2.5-flash"):
    """Geminiに画像を見せてクロップ領域案を得る。Returns: [{label, box_2d, reason}]。"""
    from google import genai  # 遅延import（新SDK）
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    logger.info(f"画像プランを生成（model={model}・画像{len(image_bytes)}バイト・目安{n}カット）")
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            build_prompt(title, explanation, n),
        ],
    )
    crops = parse_crops(resp.text)
    logger.info(f"画像プラン取得: {len(crops)}カット")
    return crops


def render_crops(image_path, crops, out_dir, prefix="cut", max_side=DEFAULT_MAX_SIDE):
    """
    クロップ領域を実際に切り出して out_dir 直下へ JPG 保存する（prep.mjsが拾える階層）。
    長辺 max_side に収まるよう縮小。古い <prefix>_*.jpg は事前に掃除する。
    Returns: 有効カットに file/pixels/size/aspect を付与したリスト。
    """
    from PIL import Image  # 遅延import

    os.makedirs(out_dir, exist_ok=True)
    # 古いカットを掃除（カット数が減ったときのゴミ残りを防ぐ）
    for f in os.listdir(out_dir):
        if f.startswith(f"{prefix}_") and f.lower().endswith((".jpg", ".jpeg", ".png")):
            os.remove(os.path.join(out_dir, f))

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    rendered = []
    idx = 0
    for c in crops:
        left, top, right, bottom = box_to_pixels(c["box_2d"], w, h)
        cw, ch = right - left, bottom - top
        if cw < MIN_CROP_PX or ch < MIN_CROP_PX:
            logger.warning(f"領域が小さすぎスキップ: {cw}x{ch} {c.get('label')}")
            continue
        idx += 1
        crop = img.crop((left, top, right, bottom))
        scale = min(1.0, max_side / max(cw, ch))
        if scale < 1.0:
            crop = crop.resize((round(cw * scale), round(ch * scale)), Image.LANCZOS)
        fname = f"{prefix}_{idx:02d}.jpg"
        crop.save(os.path.join(out_dir, fname), quality=88)
        aspect = round(cw / ch, 3) if ch else 0
        logger.info(f"カット#{idx} {c.get('label')}: px=({left},{top},{right},{bottom}) {cw}x{ch} a={aspect} -> {fname}")
        rendered.append({
            "label": c.get("label"),
            "reason": c.get("reason"),
            "box_2d": c["box_2d"],
            "pixels": [left, top, right, bottom],
            "size": [cw, ch],
            "aspect": aspect,
            "file": fname,
        })
    return rendered


def build_image_plan(image_path, apod, out_dir, count=DEFAULT_COUNT, model="gemini-2.5-flash"):
    """plan_crops→render_crops の一括。失敗時は例外。Returns: render済みカットリスト。"""
    crops = plan_crops(image_path, apod.get("title"), apod.get("explanation"), count, model)
    rendered = render_crops(image_path, crops, out_dir)
    # 記録（人間が見る用。動画化には不要）
    try:
        with open(os.path.join(out_dir, "crops.json"), "w", encoding="utf-8") as f:
            json.dump({"image": os.path.basename(image_path), "crops": rendered},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crops.json保存に失敗（無視）: {e}")
    return rendered


def build_focus_plan(image_path, apod, out_dir, count=DEFAULT_COUNT, model="gemini-2.5-flash"):
    """plan_crops で得た領域を「切り出さず元画像＋focus枠」のカット列にする（アノテーション方式）。

    切り出し版(build_image_plan)と違い、画像は元のAPOD1枚を共有し、各カットは正規化focus枠と
    画像アスペクト(w/h)だけを持つ。動画側がその領域に枠を重ね、そこへゆっくり寄る。
    題材が必ず一致し（同じ画像）、全体の文脈も保てる＝stockの誤誘導問題が構造的に起きない。

    Returns: [{label, reason, box_2d, focus, file(=元画像名), image_aspect}]。有効カット0件なら例外。
    """
    crops = plan_crops(image_path, apod.get("title"), apod.get("explanation"), count, model)

    from PIL import Image  # 遅延import（アスペクト取得のみ）
    with Image.open(image_path) as img:
        w, h = img.size
    aspect = round(w / h, 4) if h else 1.0
    base = os.path.basename(image_path)

    # 切り出し版の残骸(cut_*.jpg)が残っていれば掃除（focus版では使わず、prepの誤コピーを防ぐ）。
    for f in os.listdir(out_dir):
        if f.startswith("cut_") and f.lower().endswith((".jpg", ".jpeg", ".png")):
            os.remove(os.path.join(out_dir, f))

    cuts = []
    for c in crops:
        focus = box_to_focus(c["box_2d"])
        if (focus["r"] - focus["l"]) < MIN_FOCUS or (focus["b"] - focus["t"]) < MIN_FOCUS:
            logger.warning(f"focus枠が小さすぎスキップ: {focus} {c.get('label')}")
            continue
        cuts.append({
            "label": c.get("label"),
            "reason": c.get("reason"),
            "box_2d": c["box_2d"],
            "focus": focus,
            "file": base,
            "image_aspect": aspect,
        })
        logger.info(f"focusカット#{len(cuts)} {c.get('label')}: {focus}")
    if not cuts:
        raise ValueError("有効な focus カットがありません")
    # 記録（人間が見る用）。
    try:
        with open(os.path.join(out_dir, "crops.json"), "w", encoding="utf-8") as fh:
            json.dump({"image": base, "mode": "focus", "aspect": aspect, "cuts": cuts},
                      fh, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crops.json保存に失敗（無視）: {e}")
    return cuts
