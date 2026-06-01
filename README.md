# my-qiita-analyze

Qiita の記事ごとのページビュー・いいね・ストック数を毎日自動収集し、インタラクティブな HTML レポートを生成するツールです。

## 機能

- **毎日自動収集** — GitHub Actions で Qiita API を叩き、スナップショットを SQLite に蓄積
- **PV ランキング表** — 全記事をページビュー順に一覧表示（いいね・ストック数付き）
- **合計 PV 推移グラフ** — 全記事の日別累計 PV 推移
- **上位 10 記事 PV 推移グラフ** — 記事ごとの推移を重ねて比較
- **期間フィルター** — CLI 引数またはグラフ上のボタン・スライダーで期間を絞り込み（デフォルト: 直近 2 年）

## セットアップ

### 1. Qiita アクセストークンを取得

[https://qiita.com/settings/tokens/new](https://qiita.com/settings/tokens/new) にアクセスし、スコープ `read_qiita` で発行する。

### 2. `.env` を作成

```bash
cp .env.example .env
# .env を開いて QIITA_TOKEN=<発行したトークン> を記入
```

### 3. 依存パッケージをインストール

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. GitHub Actions の Secret を設定

リポジトリの **Settings → Secrets and variables → Actions** で `QIITA_TOKEN` を登録する。

## 使い方

### データ収集

```bash
python collect.py
```

GitHub Actions により毎日 JST 07:00 に自動実行される。  
手動実行は Actions タブ → **Run workflow** から可能。

### レポート生成

```bash
# デフォルト（直近2年）
python report.py

# 開始日を指定
python report.py --start 2024-01-01

# 期間を両端指定
python report.py --start 2024-01-01 --end 2025-12-31
```

生成された `report/index.html` をブラウザで開くとレポートを閲覧できる。  
グラフ右上のボタン（1ヶ月 / 3ヶ月 / 6ヶ月 / 1年 / 2年 / 全期間）やスライダーでブラウザ上からも期間を変更できる。

## ファイル構成

```
my-qiita-analyze/
├── .github/
│   └── workflows/
│       └── collect.yml   # GitHub Actions ワークフロー
├── data/
│   └── qiita.db          # SQLite（スナップショット蓄積）
├── report/
│   └── index.html        # 生成された HTML レポート
├── collect.py            # データ収集スクリプト
├── report.py             # レポート生成スクリプト
├── requirements.txt
├── .env.example
└── .gitignore
```

## 動作環境

- Python 3.12+
- GitHub Actions (ubuntu-latest)

## ライセンス

[MIT](LICENSE)
