"""
SQLiteに蓄積したスナップショットを読み込み、HTMLレポートを生成する。
"""

import os
import sqlite3
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "qiita.db")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "report")
REPORT_PATH = os.path.join(REPORT_DIR, "index.html")
TOP_N = 10


def load_data() -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"データベースが見つかりません: {DB_PATH}\n"
            "まず collect.py を実行してデータを収集してください。"
        )
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM snapshots ORDER BY snapshot_date, page_views DESC",
            conn,
        )
    return df


def build_ranking_table(latest: pd.DataFrame) -> str:
    ranked = latest.sort_values("page_views", ascending=False).reset_index(drop=True)
    ranked.index += 1

    rows = ""
    for rank, row in ranked.iterrows():
        title_link = f'<a href="{row["url"]}" target="_blank">{row["title"]}</a>'
        rows += (
            f"<tr>"
            f"<td>{rank}</td>"
            f"<td class='title'>{title_link}</td>"
            f"<td>{row['created_at']}</td>"
            f"<td class='num'>{int(row['page_views']):,}</td>"
            f"<td class='num'>{int(row['likes']):,}</td>"
            f"<td class='num'>{int(row['stocks']):,}</td>"
            f"</tr>"
        )

    return f"""
    <table>
      <thead>
        <tr>
          <th>#</th><th>タイトル</th><th>投稿日</th>
          <th>PV数</th><th>いいね</th><th>ストック</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def build_total_pv_chart(df: pd.DataFrame) -> str:
    daily_total = df.groupby("snapshot_date")["page_views"].sum().reset_index()
    daily_total.columns = ["date", "total_pv"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily_total["date"],
        y=daily_total["total_pv"],
        mode="lines+markers",
        name="合計PV",
        line=dict(color="#55C500", width=2),
        marker=dict(size=6),
        hovertemplate="%{x}<br>合計PV: %{y:,}<extra></extra>",
    ))
    fig.update_layout(
        title="全記事の合計PV推移",
        xaxis_title="日付",
        yaxis_title="累計PV数",
        hovermode="x unified",
        template="plotly_white",
        height=400,
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_per_article_chart(df: pd.DataFrame, latest: pd.DataFrame) -> str:
    top_ids = (
        latest.sort_values("page_views", ascending=False)
        .head(TOP_N)["id"]
        .tolist()
    )
    top_titles = latest.set_index("id")["title"].to_dict()

    fig = go.Figure()
    colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    ]

    for i, article_id in enumerate(top_ids):
        article_df = df[df["id"] == article_id].sort_values("snapshot_date")
        short_title = top_titles.get(article_id, article_id)
        if len(short_title) > 30:
            short_title = short_title[:30] + "…"

        fig.add_trace(go.Scatter(
            x=article_df["snapshot_date"],
            y=article_df["page_views"],
            mode="lines+markers",
            name=short_title,
            line=dict(color=colors[i % len(colors)], width=2),
            marker=dict(size=5),
            hovertemplate=f"{top_titles.get(article_id, ''[:40])}<br>%{{x}}<br>PV: %{{y:,}}<extra></extra>",
        ))

    fig.update_layout(
        title=f"上位{TOP_N}記事のPV推移",
        xaxis_title="日付",
        yaxis_title="累計PV数",
        hovermode="x unified",
        template="plotly_white",
        height=500,
        legend=dict(orientation="v", x=1.02, y=1),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def generate_html(
    ranking_table: str,
    total_chart: str,
    per_article_chart: str,
    latest_date: str,
    total_pv: int,
    article_count: int,
) -> str:
    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Qiita 記事分析レポート</title>
  {plotly_cdn}
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; color: #333; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 4px; color: #55C500; }}
    .meta {{ color: #777; font-size: 0.9rem; margin-bottom: 24px; }}
    .stats {{ display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }}
    .stat-card {{ background: #fff; border-radius: 8px; padding: 20px 28px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    .stat-card .label {{ font-size: 0.8rem; color: #888; margin-bottom: 4px; }}
    .stat-card .value {{ font-size: 2rem; font-weight: 700; color: #55C500; }}
    .card {{ background: #fff; border-radius: 8px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    h2 {{ font-size: 1.2rem; margin-bottom: 16px; color: #444; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th {{ background: #f0f0f0; padding: 10px 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #ddd; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #eee; vertical-align: top; }}
    tr:hover td {{ background: #fafafa; }}
    td.title {{ max-width: 400px; }}
    td.title a {{ color: #0066cc; text-decoration: none; word-break: break-word; }}
    td.title a:hover {{ text-decoration: underline; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Qiita 記事分析レポート</h1>
    <p class="meta">最終更新: {latest_date}</p>

    <div class="stats">
      <div class="stat-card">
        <div class="label">総記事数</div>
        <div class="value">{article_count}</div>
      </div>
      <div class="stat-card">
        <div class="label">総PV数</div>
        <div class="value">{total_pv:,}</div>
      </div>
    </div>

    <div class="card">
      <h2>PVランキング</h2>
      {ranking_table}
    </div>

    <div class="card">
      {total_chart}
    </div>

    <div class="card">
      {per_article_chart}
    </div>
  </div>
</body>
</html>"""


def main() -> None:
    df = load_data()

    if df.empty:
        print("データが空です。collect.py を先に実行してください。")
        return

    latest_date = df["snapshot_date"].max()
    latest = df[df["snapshot_date"] == latest_date].copy()

    total_pv = int(latest["page_views"].sum())
    article_count = len(latest)

    has_history = df["snapshot_date"].nunique() > 1

    ranking_table = build_ranking_table(latest)
    total_chart = build_total_pv_chart(df) if has_history else "<p>時系列グラフはデータが2日分以上蓄積されると表示されます。</p>"
    per_article_chart = build_per_article_chart(df, latest) if has_history else "<p>時系列グラフはデータが2日分以上蓄積されると表示されます。</p>"

    html = generate_html(
        ranking_table,
        total_chart,
        per_article_chart,
        latest_date,
        total_pv,
        article_count,
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"レポートを生成しました: {REPORT_PATH}")


if __name__ == "__main__":
    main()
