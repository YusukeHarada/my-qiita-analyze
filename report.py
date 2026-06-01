"""
SQLiteに蓄積したスナップショットを読み込み、HTMLレポートを生成する。

使い方:
  python report.py                         # デフォルト（直近2年）
  python report.py --start 2024-01-01      # 開始日を指定
  python report.py --start 2024-01-01 --end 2025-01-01
"""

import argparse
import os
import sqlite3
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "qiita.db")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "report")
REPORT_PATH = os.path.join(REPORT_DIR, "index.html")
TOP_N = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qiita記事分析レポートを生成する")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="集計開始日（デフォルト: 直近2年）")
    parser.add_argument("--end", metavar="YYYY-MM-DD", help="集計終了日（デフォルト: 最新データ日）")
    return parser.parse_args()


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


def resolve_date_range(df: pd.DataFrame, start: str | None, end: str | None) -> tuple[str, str]:
    latest = df["snapshot_date"].max()
    end_date = end or latest
    start_date = start or (
        date.fromisoformat(end_date) - timedelta(days=365 * 2)
    ).isoformat()
    return start_date, end_date


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


def _range_selector_xaxis(start_date: str, end_date: str) -> dict:
    return dict(
        type="date",
        range=[start_date, end_date],
        rangeselector=dict(
            buttons=[
                dict(count=1,  label="1ヶ月", step="month", stepmode="backward"),
                dict(count=3,  label="3ヶ月", step="month", stepmode="backward"),
                dict(count=6,  label="6ヶ月", step="month", stepmode="backward"),
                dict(count=1,  label="1年",   step="year",  stepmode="backward"),
                dict(count=2,  label="2年",   step="year",  stepmode="backward"),
                dict(step="all", label="全期間"),
            ],
            bgcolor="#f0f0f0",
            activecolor="#55C500",
        ),
        rangeslider=dict(visible=True, thickness=0.05),
    )


def build_total_pv_chart(df: pd.DataFrame, start_date: str, end_date: str) -> str:
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
        xaxis=_range_selector_xaxis(start_date, end_date),
        yaxis_title="累計PV数",
        hovermode="x unified",
        template="plotly_white",
        height=450,
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False)


def build_per_article_chart(df: pd.DataFrame, latest: pd.DataFrame, start_date: str, end_date: str) -> str:
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
        full_title = top_titles.get(article_id, article_id)
        short_title = (full_title[:30] + "…") if len(full_title) > 30 else full_title

        fig.add_trace(go.Scatter(
            x=article_df["snapshot_date"],
            y=article_df["page_views"],
            mode="lines+markers",
            name=short_title,
            line=dict(color=colors[i % len(colors)], width=2),
            marker=dict(size=5),
            hovertemplate=f"{full_title}<br>%{{x}}<br>PV: %{{y:,}}<extra></extra>",
        ))

    fig.update_layout(
        title=f"上位{TOP_N}記事のPV推移",
        xaxis=_range_selector_xaxis(start_date, end_date),
        yaxis_title="累計PV数",
        hovermode="x unified",
        template="plotly_white",
        height=550,
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
    start_date: str,
    end_date: str,
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
    .meta-note {{ color: #aaa; font-size: 0.8rem; }}
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
    <p class="meta">最終更新: {latest_date}　|　表示期間: {start_date} 〜 {end_date}　<span class="meta-note">（グラフ右上のボタンやスライダーで変更可）</span></p>

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
    args = parse_args()
    df = load_data()

    if df.empty:
        print("データが空です。collect.py を先に実行してください。")
        return

    latest_date = df["snapshot_date"].max()
    start_date, end_date = resolve_date_range(df, args.start, args.end)

    # 期間内のデータに絞る（グラフ用）
    df_filtered = df[(df["snapshot_date"] >= start_date) & (df["snapshot_date"] <= end_date)]

    # ランキングは期間内の最新スナップショットを使う
    latest_in_range = df_filtered[df_filtered["snapshot_date"] == df_filtered["snapshot_date"].max()].copy()

    if latest_in_range.empty:
        print(f"指定期間 {start_date} 〜 {end_date} にデータがありません。")
        return

    total_pv = int(latest_in_range["page_views"].sum())
    article_count = len(latest_in_range)
    has_history = df_filtered["snapshot_date"].nunique() > 1

    no_history_msg = "<p style='color:#888;padding:16px 0'>時系列グラフはデータが2日分以上蓄積されると表示されます。</p>"

    ranking_table = build_ranking_table(latest_in_range)
    total_chart = build_total_pv_chart(df_filtered, start_date, end_date) if has_history else no_history_msg
    per_article_chart = build_per_article_chart(df_filtered, latest_in_range, start_date, end_date) if has_history else no_history_msg

    html = generate_html(
        ranking_table,
        total_chart,
        per_article_chart,
        latest_date,
        total_pv,
        article_count,
        start_date,
        end_date,
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"レポートを生成しました: {REPORT_PATH}  （期間: {start_date} 〜 {end_date}）")


if __name__ == "__main__":
    main()
