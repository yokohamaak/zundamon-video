#!/usr/bin/env bash
# Mac等（Docker非使用）でのローカル実行環境を用意する。
# - .venv を作成し requirements.txt を導入
# - 音声生成には別途 VOICEVOX アプリ/エンジンの起動（:50021）が必要
#
# 使い方: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。先に Python をインストールしてください。" >&2
  exit 1
fi

echo "[setup] .venv を作成..."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
echo "[setup] 依存をインストール (requirements.txt)..."
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[setup] .env を作成しました。GEMINI_API_KEY を記入してください。"
fi

cat <<'MSG'

[setup] 完了。

実行例:
  source .venv/bin/activate
  python main_story.py --script-only    # 台本JSONのみ（VOICEVOX不要・GEMINI_API_KEY必須）
  python main_story.py --no-images      # 音声+meta（VOICEVOXアプリ起動が必要）

  # 動画化（render）:
  cd video && npm install && SRC_DIR=../docs/story npm run render

テスト:
  python test_story_script.py && python test_story_meta.py && python test_editor_model.py && python test_editor_phase2.py
MSG
