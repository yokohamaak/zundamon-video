"""
src.manual_cuts 純関数の単体テスト（FS一時dir使用・network/Pillow不要）。

実行: python3 test_manual_cuts.py
build_manual_cuts（既存画像検出/プレースホルダ）と write_manifest を検証する。
"""
import json
import os
import tempfile

from src import manual_cuts as mc


def test_build_manual_cuts_placeholder_and_user():
    with tempfile.TemporaryDirectory() as d:
        # 2番目だけ画像を置いておく（user用意済み）
        open(os.path.join(d, "manual_02.jpg"), "wb").close()
        specs = [
            {"label": "赤い空", "prompt": "二重星の浜辺", "image_prompt": "alien beach, binary stars, 16:9"},
            {"label": "氷の海", "prompt": "凍った衛星"},
        ]
        out = mc.build_manual_cuts(specs, d)
        assert out[0]["target"] == "manual_01.png" and out[0]["file"] is None
        assert out[0]["status"] == "placeholder"
        assert out[0]["image_prompt"] == "alien beach, binary stars, 16:9", "image_promptを保持"
        assert out[1]["target"] == "manual_02.png"
        assert out[1]["file"] == "manual_02.jpg", "拡張子違いでも既存を検出"
        assert out[1]["status"] == "user"
        print("  build_manual_cuts: 未用意=placeholder/既存=user OK")


def test_write_manifest():
    with tempfile.TemporaryDirectory() as d:
        manuals = [
            {"label": "a", "prompt": "p1", "image_prompt": "ip1", "target": "manual_01.png", "file": None, "status": "placeholder"},
            {"label": "b", "prompt": "p2", "target": "manual_02.png", "file": "manual_02.png", "status": "user"},
        ]
        n = mc.write_manifest(d, manuals)
        assert n == 1, "未用意件数を返す"
        data = json.load(open(os.path.join(d, "manifest.json"), encoding="utf-8"))
        assert len(data["missing"]) == 1 and data["missing"][0]["target"] == "manual_01.png"
        assert data["missing"][0]["prompt"] == "p1"
        assert data["missing"][0]["image_prompt"] == "ip1", "画像AI用プロンプトをmanifestに出力"
        assert len(data["ready"]) == 1 and data["ready"][0]["target"] == "manual_02.png"
        print("  write_manifest: missing/ready分類+prompt保持 OK")


if __name__ == "__main__":
    print("test_manual_cuts:")
    test_build_manual_cuts_placeholder_and_user()
    test_write_manifest()
    print("ALL PASS")
