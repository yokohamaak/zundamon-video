# news-digest-tts

毎朝・毎晩、RSSから自動収集したニュースをGemini TTSで男女掛け合い音声に変換し、Cloudflare Pagesで再生するシステム。

## セットアップ

### 1. Cloudflare Pagesプロジェクトを作成
1. [Cloudflare Dashboard](https://dash.cloudflare.com/) にログイン
2. Workers & Pages → Create → Pages → Direct Upload
3. プロジェクト名を `news-digest-tts` にして作成（ファイルアップロードは不要、作成だけでOK）

### 2. Cloudflare APIトークンを取得
1. Cloudflare Dashboard → My Profile → API Tokens → Create Token
2. **Edit Cloudflare Workers** テンプレートを選択して作成
3. 表示されたトークンをコピー（一度しか表示されない）
4. アカウントIDはDashboardのURLまたは右サイドバーで確認

### 3. GitHub Secretsに登録
Settings → Secrets and variables → Actions → New repository secret

| Secret名 | 内容 |
|---|---|
| `GEMINI_API_KEY` | Google AI StudioのAPIキー |
| `CLOUDFLARE_API_TOKEN` | 手順2で取得したトークン |
| `CLOUDFLARE_ACCOUNT_ID` | CloudflareのアカウントID |

### 4. ワークフロー権限を設定
Settings → Actions → General → Workflow permissions
- **Read and write permissions** に変更

### 5. 初回手動実行
Actions タブ → News Digest TTS Generator → Run workflow

完了後、`https://news-digest-tts.pages.dev` でアクセス可能になります。

---

## config/config.yaml の編集
- RSSフィード追加：`sources.rss` にURLを追記
- キャラクター変更：`characters` の name / style を編集
- 音声変更：`tts` の host_voice / guest_voice を変更

## 利用可能なTTS音声
| 音声名 | 性別 |
|---|---|
| Leda | female |
| Aoede | female |
| Kore | female |
| Zephyr | female |
| Puck | male |
| Charon | male |
| Fenrir | male |
| Orus | male |

## 実行タイミング
- 朝版：毎日 6:00 JST
- 夜版：毎日 18:00 JST
- 手動：Actions タブ → Run workflow

## ファイル構成
```
├── main.py                  # メイン処理
├── src/
│   ├── collector.py         # RSS収集・キャッシュ管理
│   ├── gemini_client.py     # Gemini Flashスクリプト生成
│   └── tts_client.py        # Gemini TTS音声生成
├── config/
│   └── config.yaml          # 設定ファイル
├── docs/
│   ├── index.html           # WebプレーヤーUI
│   └── digest.mp3           # 最新音声（自動更新）
└── articles_cache.json      # 記事キャッシュ（自動更新）
```
