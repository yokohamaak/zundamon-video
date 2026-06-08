FROM node:22-slim

# git/curl/python3: 台本生成〜TTS〜動画描画パイプライン用
# ffmpeg: VOICEVOXのWAV→MP3変換（tts_voicevox.py がsubprocessで使用）
# 以降のlib群: Remotion描画のheadless Chrome実行に必要
# fonts-noto-cjk: 動画内の日本語表示（無いと豆腐化）
RUN apt-get update && apt-get install -y \
    git curl python3 python3-pip ffmpeg \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libxcb1 libx11-6 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace

ENTRYPOINT ["claude"]
