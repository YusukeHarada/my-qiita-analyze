"""
SQLiteに蓄積したスナップショットを読み込み、HTMLレポートを生成する。

使い方:
  python report.py                         # デフォルト（直近2年）
  python report.py --start 2024-01-01      # 開始日を指定
  python report.py --start 2024-01-01 --end 2025-01-01
"""

import argparse
import json
import os
import sqlite3
import statistics
from datetime import date, timedelta
from html import escape as html_escape

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "qiita.db")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "docs")
REPORT_PATH = os.path.join(REPORT_DIR, "index.html")
TOP_N = 10
CHART_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
BRAND_COLOR = "#55C500"
BRAND_COLOR_BG = "#f0f0f0"
BRAND_COLOR_BG_DARK = "#2b2f35"
NEGATIVE_COLOR = "#d62728"
TITLE_MAX_LEN = 30
TAG_CHANGE_RATE_WINDOW_DAYS = 7
ARTICLE_COMPARE_MAX = 5
ROW_CLICK_HINT = "クリックで記事別PV推移を表示（Shift/Ctrl+クリックで比較に追加）"


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


def compute_article_pv_increases(
    latest: pd.DataFrame, df_filtered: pd.DataFrame, window_days: int
) -> dict[str, int | None]:
    """記事ごとのwindow_days分の増減PVを計算する。

    window_days分の履歴がない場合のフォールバックとして、公開間もない記事は
    公開時点(=0)からの増加量とみなしPV数をそのまま使い、データ収集期間が
    足りないだけの古い記事は最初に記録されたスナップショットからの増加量を使う。
    """
    rates_by_id = _compute_article_change_rates(df_filtered, window_days)
    first_pv_by_id = df_filtered.sort_values("snapshot_date").groupby("id")["page_views"].first()

    increases: dict[str, int | None] = {}
    for _, row in latest.iterrows():
        rate = rates_by_id.get(row["id"])
        if rate is not None and rate["prev_pv"] is not None:
            pv_increase = rate["pv_increase"]
        else:
            article_age_days = (
                date.fromisoformat(row["snapshot_date"]) - date.fromisoformat(row["created_at"])
            ).days
            if article_age_days < window_days:
                pv_increase = int(row["page_views"])
            else:
                first_pv = first_pv_by_id.get(row["id"])
                pv_increase = None if first_pv is None else int(row["page_views"]) - int(first_pv)
        increases[row["id"]] = pv_increase
    return increases


def build_ranking_table(
    latest: pd.DataFrame, df_filtered: pd.DataFrame, window_days: int = TAG_CHANGE_RATE_WINDOW_DAYS
) -> str:
    ranked = latest.copy()
    ranked[["page_views", "likes", "stocks"]] = ranked[["page_views", "likes", "stocks"]].fillna(0)
    ranked = ranked.sort_values("page_views", ascending=False).reset_index(drop=True)
    ranked.index += 1

    pv_increases = compute_article_pv_increases(ranked, df_filtered, window_days)

    rows = ""
    for rank, row in ranked.iterrows():
        title_link = f'<a href="{row["url"]}" target="_blank">{row["title"]}</a>'
        pv_increase = pv_increases.get(row["id"])
        if pv_increase is None:
            increase_display = "−"
        else:
            increase_display = f"{'+' if pv_increase >= 0 else ''}{pv_increase:,}"
        rows += (
            f"<tr data-id=\"{html_escape(str(row['id']))}\" title=\"{ROW_CLICK_HINT}\">"
            f"<td>{rank}</td>"
            f"<td class='title'>{title_link}</td>"
            f"<td>{row['created_at']}</td>"
            f"<td class='num'>{int(row['page_views']):,}</td>"
            f"<td class='num'>{increase_display}</td>"
            f"<td class='num'>{int(row['likes']):,}</td>"
            f"<td class='num'>{int(row['stocks']):,}</td>"
            f"</tr>"
        )

    return f"""
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th class="sortable" data-key="title">タイトル<span class="sort-arrow"></span></th>
          <th class="sortable" data-key="created_at">投稿日<span class="sort-arrow"></span></th>
          <th class="sortable" data-key="page_views">PV数<span class="sort-arrow"></span></th>
          <th class="sortable" data-key="pv_increase">増減PV（{window_days}日）<span class="sort-arrow"></span></th>
          <th class="sortable" data-key="likes">いいね<span class="sort-arrow"></span></th>
          <th class="sortable" data-key="stocks">ストック<span class="sort-arrow"></span></th>
        </tr>
      </thead>
      <tbody id="ranking-tbody">{rows}</tbody>
    </table>
    """


def _range_selector_xaxis(start_date: str, end_date: str, default_all: bool = False) -> dict:
    layout = dict(
        type="date",
        rangeselector=dict(
            buttons=[
                dict(count=1,  label="1ヶ月", step="month", stepmode="backward"),
                dict(count=3,  label="3ヶ月", step="month", stepmode="backward"),
                dict(count=6,  label="6ヶ月", step="month", stepmode="backward"),
                dict(count=1,  label="1年",   step="year",  stepmode="backward"),
                dict(count=2,  label="2年",   step="year",  stepmode="backward"),
                dict(step="all", label="全期間"),
            ],
            bgcolor=BRAND_COLOR_BG,
            activecolor=BRAND_COLOR,
        ),
        rangeslider=dict(visible=True, thickness=0.05),
    )
    if not default_all:
        layout["range"] = [start_date, end_date]
    return layout


def build_total_pv_chart(df: pd.DataFrame, start_date: str, end_date: str) -> str:
    daily_total = df.groupby("snapshot_date")["page_views"].sum().reset_index()
    daily_total.columns = ["date", "total_pv"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily_total["date"],
        y=daily_total["total_pv"],
        mode="lines+markers",
        name="合計PV",
        line=dict(color=BRAND_COLOR, width=2),
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
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id="total-pv-chart")


def build_per_article_chart(df: pd.DataFrame, latest: pd.DataFrame, start_date: str, end_date: str) -> str:
    top_ids = (
        latest.sort_values("page_views", ascending=False)
        .head(TOP_N)["id"]
        .tolist()
    )
    top_titles = latest.set_index("id")["title"].to_dict()

    def _fmt_diff(v):
        if pd.isna(v):
            return "−"
        v = int(v)
        return f"+{v:,}" if v >= 0 else f"{v:,}"

    fig = go.Figure()
    for i, article_id in enumerate(top_ids):
        article_df = df[df["id"] == article_id].sort_values("snapshot_date")
        full_title = top_titles.get(article_id, article_id)
        short_title = (full_title[:TITLE_MAX_LEN] + "…") if len(full_title) > TITLE_MAX_LEN else full_title
        diffs = article_df["page_views"].diff().map(_fmt_diff).tolist()

        fig.add_trace(go.Scatter(
            x=article_df["snapshot_date"],
            y=article_df["page_views"],
            customdata=diffs,
            mode="lines+markers",
            name=short_title,
            line=dict(color=CHART_COLORS[i % len(CHART_COLORS)], width=2),
            marker=dict(size=5),
            hovertemplate=f"{full_title}<br>%{{x}}<br>PV: %{{y:,}}<br>前日比: %{{customdata}}<extra></extra>",
        ))

    fig.update_layout(
        title=f"上位{TOP_N}記事のPV推移",
        xaxis=_range_selector_xaxis(start_date, end_date),
        yaxis_title="累計PV数",
        hovermode="closest",
        template="plotly_white",
        height=550,
        margin=dict(t=60, r=160),
        legend=dict(orientation="v", x=1.02, y=1),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id="per-article-chart")


def select_top_pv_increase_ids(pv_increases: dict[str, int | None], top_n: int = TOP_N) -> list[str]:
    ranked = sorted(
        ((aid, v) for aid, v in pv_increases.items() if v is not None),
        key=lambda kv: kv[1], reverse=True,
    )
    return [aid for aid, _ in ranked[:top_n]]


def build_pv_increase_bar_chart(
    latest: pd.DataFrame, pv_increases: dict[str, int | None], top_ids: list[str], window_days: int
) -> str:
    if not top_ids:
        return ""

    titles = latest.set_index("id")["title"].to_dict()
    # 横棒グラフは上から降順に見せたいので、描画順を逆にしておく
    ordered_ids = list(reversed(top_ids))
    full_titles = [titles.get(aid, aid) for aid in ordered_ids]
    short_titles = [t if len(t) <= TITLE_MAX_LEN else t[:TITLE_MAX_LEN] + "…" for t in full_titles]
    values = [pv_increases[aid] for aid in ordered_ids]
    colors = [NEGATIVE_COLOR if v < 0 else BRAND_COLOR for v in values]

    fig = go.Figure(go.Bar(
        x=values,
        y=short_titles,
        orientation="h",
        marker=dict(color=colors),
        text=[f"{'+' if v >= 0 else ''}{v:,}" for v in values],
        textposition="outside",
        customdata=full_titles,
        hovertemplate="%{customdata}<br>増減PV: %{x:,}<extra></extra>",
    ))
    fig.update_layout(
        title=f"増減PV上位{len(top_ids)}記事（直近{window_days}日）",
        xaxis_title="増減PV",
        yaxis=dict(automargin=True),
        template="plotly_white",
        height=max(320, 40 * len(top_ids) + 100),
        margin=dict(t=60, r=60),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id="pv-increase-bar-chart")


def build_pv_increase_trend_chart(
    df_filtered: pd.DataFrame, top_ids: list[str], start_date: str, end_date: str
) -> str:
    if not top_ids:
        return ""

    titles = df_filtered.drop_duplicates("id").set_index("id")["title"].to_dict()

    fig = go.Figure()
    for i, article_id in enumerate(top_ids):
        article_df = df_filtered[df_filtered["id"] == article_id].sort_values("snapshot_date")
        diffs = article_df["page_views"].diff()
        plot_df = article_df.assign(pv_diff=diffs).dropna(subset=["pv_diff"])
        if plot_df.empty:
            continue
        full_title = titles.get(article_id, article_id)
        short_title = full_title if len(full_title) <= TITLE_MAX_LEN else full_title[:TITLE_MAX_LEN] + "…"
        fig.add_trace(go.Scatter(
            x=plot_df["snapshot_date"],
            y=plot_df["pv_diff"],
            mode="lines+markers",
            name=short_title,
            line=dict(color=CHART_COLORS[i % len(CHART_COLORS)], width=2),
            marker=dict(size=5),
            hovertemplate=f"{full_title}<br>%{{x}}<br>増減PV（前日比）: %{{y:,}}<extra></extra>",
        ))

    if not fig.data:
        return ""

    fig.update_layout(
        title="増減PV推移（前日比・増減PV上位記事）",
        xaxis=_range_selector_xaxis(start_date, end_date, default_all=True),
        yaxis_title="増減PV（前日比）",
        hovermode="closest",
        template="plotly_white",
        height=450,
        margin=dict(t=60, r=160),
        legend=dict(orientation="v", x=1.02, y=1),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id="pv-increase-trend-chart")


def explode_tags(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["id", "title", "snapshot_date", "page_views", "group"]
    if df.empty or "tags" not in df.columns:
        return pd.DataFrame(columns=cols)

    working = df[df["tags"].notna() & (df["tags"] != "")].copy()
    if working.empty:
        return pd.DataFrame(columns=cols)

    working["group"] = working["tags"].str.split(",")
    exploded = working.explode("group")
    exploded["group"] = exploded["group"].str.strip()
    exploded = exploded[exploded["group"] != ""]
    return exploded[cols].reset_index(drop=True)


def _compute_group_change_rates(daily_df: pd.DataFrame, groups: list[str], window_days: int) -> list[dict]:
    results = []
    for group in groups:
        group_df = daily_df[daily_df["group"] == group].sort_values("snapshot_date")
        if group_df.empty:
            continue

        latest_date_str = group_df["snapshot_date"].max()
        latest_pv = int(group_df.loc[group_df["snapshot_date"] == latest_date_str, "page_views"].iloc[0])

        target_date = (date.fromisoformat(latest_date_str) - timedelta(days=window_days)).isoformat()
        prev_candidates = group_df[group_df["snapshot_date"] <= target_date]
        prev_pv = int(prev_candidates.iloc[-1]["page_views"]) if not prev_candidates.empty else None

        if prev_pv is None:
            rate_value, rate_display, pv_increase = float("-inf"), "データ不足", float("-inf")
        else:
            pv_increase = latest_pv - prev_pv
            if prev_pv == 0:
                rate_value = float("inf") if latest_pv > 0 else 0.0
                rate_display = "新規" if latest_pv > 0 else "0.0%"
            else:
                rate_value = pv_increase / prev_pv * 100
                rate_display = f"{'+' if rate_value >= 0 else ''}{rate_value:.1f}%"

        results.append(dict(
            group=group, latest_pv=latest_pv, prev_pv=prev_pv,
            rate_value=rate_value, rate_display=rate_display,
            pv_increase=pv_increase,
        ))
    return results


def _compute_article_change_rates(df_filtered: pd.DataFrame, window_days: int) -> dict[str, dict]:
    article_daily = df_filtered[["id", "snapshot_date", "page_views"]].rename(columns={"id": "group"})
    article_ids = df_filtered["id"].unique().tolist()
    return {r["group"]: r for r in _compute_group_change_rates(article_daily, article_ids, window_days)}


def median_pv_increase_by_group(long_df: pd.DataFrame, article_rates_by_id: dict[str, dict]) -> dict[str, float]:
    if long_df.empty:
        return {}
    medians = {}
    for group, ids in long_df.groupby("group")["id"].unique().items():
        increases = [
            article_rates_by_id[i]["pv_increase"]
            for i in ids
            if i in article_rates_by_id and article_rates_by_id[i]["prev_pv"] is not None
        ]
        if increases:
            medians[group] = statistics.median(increases)
    return medians


def select_top_groups_by_median_pv_increase(group_medians: dict[str, float], n: int = TOP_N) -> list[str]:
    # 記事数の多いタグが単に母数の多さで上位に来るのを避けるため、グループの合計増加数ではなく
    # 「そのグループに属する各記事の増減PVの中央値」でランキングする。中央値は平均と違い、
    # 1記事だけの突発的なバズ（外れ値）に引っ張られにくい。
    ranked = sorted(group_medians.items(), key=lambda kv: kv[1], reverse=True)
    return [group for group, _ in ranked[:n]]


def explode_keywords(df: pd.DataFrame, keywords: list[str]) -> pd.DataFrame:
    cols = ["id", "title", "snapshot_date", "page_views", "group"]
    if df.empty or not keywords:
        return pd.DataFrame(columns=cols)

    frames = []
    for keyword in keywords:
        matched = df[df["title"].str.contains(keyword, case=False, na=False, regex=False)].copy()
        if matched.empty:
            continue
        matched["group"] = keyword
        frames.append(matched[cols])

    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)


def aggregate_group_daily_pv(long_df: pd.DataFrame) -> pd.DataFrame:
    cols = ["group", "snapshot_date", "page_views"]
    if long_df.empty:
        return pd.DataFrame(columns=cols)
    return long_df.groupby(["group", "snapshot_date"])["page_views"].sum().reset_index()


def build_group_pv_chart(
    daily_df: pd.DataFrame, groups: list[str], start_date: str, end_date: str, title: str, div_id: str
) -> str:
    fig = go.Figure()
    for i, group in enumerate(groups):
        group_df = daily_df[daily_df["group"] == group].sort_values("snapshot_date")
        fig.add_trace(go.Scatter(
            x=group_df["snapshot_date"],
            y=group_df["page_views"],
            mode="lines+markers",
            name=group,
            line=dict(color=CHART_COLORS[i % len(CHART_COLORS)], width=2),
            marker=dict(size=5),
            hovertemplate=f"{group}<br>%{{x}}<br>PV: %{{y:,}}<extra></extra>",
        ))

    fig.update_layout(
        title=title,
        xaxis=_range_selector_xaxis(start_date, end_date, default_all=True),
        yaxis_title="合計PV数",
        hovermode="closest",
        template="plotly_white",
        height=450,
        margin=dict(t=60, r=160),
        legend=dict(orientation="v", x=1.02, y=1),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id=div_id)


def build_group_change_rate_table(
    daily_df: pd.DataFrame, groups: list[str], window_days: int, group_medians: dict[str, float]
) -> str:
    if daily_df.empty or not groups:
        return "<p>データがありません。</p>"

    results = _compute_group_change_rates(daily_df, groups, window_days)
    for r in results:
        r["median_pv_increase"] = group_medians.get(r["group"])
    # 選定基準（1記事あたり増減PVの中央値）と表示順を一致させる
    results.sort(
        key=lambda r: r["median_pv_increase"] if r["median_pv_increase"] is not None else float("-inf"),
        reverse=True,
    )

    rows = ""
    for r in results:
        prev_pv_display = f"{r['prev_pv']:,}" if r["prev_pv"] is not None else "−"
        if r["prev_pv"] is None:
            increase_display = "−"
        else:
            increase_display = f"{'+' if r['pv_increase'] >= 0 else ''}{r['pv_increase']:,}"
        median = r["median_pv_increase"]
        median_display = "−" if median is None else f"{'+' if median >= 0 else ''}{median:,.0f}"
        rows += (
            f"<tr>"
            f"<td>{r['group']}</td>"
            f"<td class='num'>{r['latest_pv']:,}</td>"
            f"<td class='num'>{prev_pv_display}</td>"
            f"<td class='num'>{increase_display}</td>"
            f"<td class='num'>{median_display}</td>"
            f"<td class='num'>{r['rate_display']}</td>"
            f"</tr>"
        )

    return f"""
    <table>
      <thead>
        <tr>
          <th>グループ</th><th>直近PV</th><th>{window_days}日前PV</th><th>増減PV</th>
          <th>1記事あたり増減PV（中央値）</th><th>変化率</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def build_chart_data_json(df: pd.DataFrame) -> str:
    cols = ["id", "title", "url", "created_at", "snapshot_date", "page_views", "likes", "stocks"]
    data = df[cols].copy()
    for col in ["page_views", "likes", "stocks"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    return data.to_json(orient="records", force_ascii=False)


def _build_style_block() -> str:
    return f"""<style>
    :root {{
      --color-brand: {BRAND_COLOR};
      --color-brand-bg: {BRAND_COLOR_BG};
      --color-bg: #f5f5f5;
      --color-surface: #ffffff;
      --color-text: #333333;
      --color-heading: #333333;
      --color-text-muted: #777777;
      --color-text-faint: #aaaaaa;
      --color-border: #e2e2e2;
      --color-border-strong: #dddddd;
      --color-row-divider: #eeeeee;
      --color-row-hover: #fafafa;
      --color-row-alt: #fafbfa;
      --color-link: #0066cc;
      --color-input-border: #cccccc;
      --color-btn-bg: {BRAND_COLOR_BG};
      --color-btn-bg-hover: #e0e0e0;
      --color-th-hover: #e6e6e6;
      --shadow-card: 0 2px 8px rgba(0,0,0,.06);
      --radius-card: 10px;
      --radius-btn: 4px;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --color-bg: #14161a;
        --color-surface: #1d2025;
        --color-text: #e4e6eb;
        --color-heading: #f2f3f5;
        --color-text-muted: #9aa0a6;
        --color-text-faint: #767b82;
        --color-border: #2c3036;
        --color-border-strong: #33383f;
        --color-row-divider: #2a2e34;
        --color-row-hover: #23262b;
        --color-row-alt: #202226;
        --color-link: #6cb6ff;
        --color-input-border: #3a3f46;
        --color-btn-bg: #22252a;
        --color-btn-bg-hover: #2b2f35;
        --color-th-hover: #2b2f35;
        --shadow-card: 0 2px 10px rgba(0,0,0,.35);
      }}
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--color-bg); color: var(--color-text); }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 4px; color: var(--color-brand); }}
    .meta {{ color: var(--color-text-muted); font-size: 0.9rem; margin-bottom: 24px; }}
    .meta-note {{ color: var(--color-text-faint); font-size: 0.8rem; }}
    .stats {{ display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }}
    .stat-card {{ background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-card); padding: 20px 28px; box-shadow: var(--shadow-card); }}
    .stat-card .label {{ font-size: 0.8rem; color: var(--color-text-muted); margin-bottom: 4px; }}
    .stat-card .value {{ font-size: 2rem; font-weight: 700; color: var(--color-brand); }}
    .card {{ background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-card); padding: 24px; margin-bottom: 24px; box-shadow: var(--shadow-card); }}
    h2 {{ font-size: 1.2rem; margin-bottom: 16px; color: var(--color-heading); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th {{ background: var(--color-btn-bg); padding: 10px 12px; text-align: left; font-weight: 600; border-bottom: 2px solid var(--color-border-strong); color: var(--color-heading); }}
    th.sortable {{ cursor: pointer; user-select: none; white-space: nowrap; }}
    th.sortable:hover {{ background: var(--color-th-hover); }}
    th.sortable .sort-arrow {{ display: inline-block; width: 1em; color: var(--color-brand); }}
    td {{ padding: 10px 12px; border-bottom: 1px solid var(--color-row-divider); vertical-align: top; }}
    tbody tr:nth-child(even) td {{ background: var(--color-row-alt); }}
    tr:hover td {{ background: var(--color-row-hover); }}
    td.title {{ max-width: 400px; }}
    td.title a {{ color: var(--color-link); text-decoration: none; word-break: break-word; }}
    td.title a:hover {{ text-decoration: underline; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .date-filter {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .date-filter label {{ display: flex; align-items: center; gap: 6px; font-size: 0.9rem; color: var(--color-text); }}
    .date-filter input[type="date"] {{ padding: 6px 10px; border: 1px solid var(--color-input-border); border-radius: var(--radius-btn); font-size: 0.9rem; background: var(--color-surface); color: var(--color-text); }}
    .date-filter button {{ padding: 6px 14px; background: var(--color-btn-bg); border: 1px solid var(--color-input-border); border-radius: var(--radius-btn); cursor: pointer; font-size: 0.9rem; color: var(--color-text); }}
    .date-filter button:hover {{ background: var(--color-btn-bg-hover); }}
    .quick-filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }}
    .quick-filter-btn {{ padding: 6px 14px; background: var(--color-btn-bg); border: 1px solid var(--color-input-border); border-radius: var(--radius-btn); cursor: pointer; font-size: 0.9rem; color: var(--color-text); }}
    .quick-filter-btn:hover {{ background: var(--color-btn-bg-hover); }}
    .quick-filter-btn.active {{ background: var(--color-brand); color: #fff; border-color: var(--color-brand); }}
    .table-wrapper {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}

    .section {{ margin-bottom: 48px; }}
    .section:last-child {{ margin-bottom: 0; }}
    .section-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }}
    .section-title {{ font-size: 1.15rem; font-weight: 600; color: var(--color-heading); }}
    .subsection-title {{ font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: var(--color-heading); }}
    .section-badge {{ font-size: 0.72rem; font-weight: 600; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }}
    .section-badge--live {{ background: rgba(85, 197, 0, .15); color: var(--color-brand); }}
    .section-badge--fixed {{ background: var(--color-btn-bg); color: var(--color-text-muted); border: 1px solid var(--color-border); }}

    details.section {{ margin-bottom: 48px; }}
    details.section:last-child {{ margin-bottom: 0; }}
    details.section > summary {{ list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }}
    details.section > summary::-webkit-details-marker {{ display: none; }}
    details.section > summary::before {{ content: "▾"; font-size: 0.7em; color: var(--color-text-muted); transition: transform .15s ease; }}
    details.section:not([open]) > summary::before {{ transform: rotate(-90deg); }}
    details.section:not([open]) > summary {{ margin-bottom: 0; }}
    .section-body > .card:last-child {{ margin-bottom: 0; }}

    #ranking-tbody tr[data-id] {{ cursor: pointer; }}
    #ranking-tbody tr.is-selected td {{ background: rgba(85, 197, 0, .12); }}
    #ranking-tbody tr.is-selected td:first-child {{ box-shadow: inset 3px 0 0 var(--color-brand); }}
    .article-picker {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .article-picker input {{ flex: 1 1 320px; min-width: 0; padding: 8px 12px; border: 1px solid var(--color-input-border); border-radius: var(--radius-btn); font-size: 0.9rem; background: var(--color-surface); color: var(--color-text); }}
    .article-picker button, .chip-remove, .seg-btn {{ padding: 6px 14px; background: var(--color-btn-bg); border: 1px solid var(--color-input-border); border-radius: var(--radius-btn); cursor: pointer; font-size: 0.9rem; color: var(--color-text); }}
    .article-picker button:hover, .seg-btn:hover {{ background: var(--color-btn-bg-hover); }}
    .article-picker button:disabled {{ opacity: .5; cursor: not-allowed; }}
    .article-hint {{ font-size: 0.8rem; color: var(--color-text-muted); margin: 8px 0 12px; }}
    .article-hint.is-warning {{ color: {NEGATIVE_COLOR}; }}
    .article-chips {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .article-chip {{ display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 4px 4px 4px 10px; border: 1px solid var(--color-border); border-radius: 999px; font-size: 0.82rem; background: var(--color-surface); }}
    .article-chip .chip-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 280px; }}
    .chip-remove {{ padding: 0 7px; border-radius: 999px; line-height: 1.5; font-size: 0.85rem; }}
    .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }}
    .article-controls {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
    .seg {{ display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 0.85rem; color: var(--color-text-muted); }}
    .seg-btn {{ padding: 5px 12px; font-size: 0.85rem; }}
    .seg-btn.active {{ background: var(--color-brand); color: #fff; border-color: var(--color-brand); }}
    .seg[hidden] {{ display: none; }}
    .article-empty {{ padding: 48px 12px; text-align: center; color: var(--color-text-muted); font-size: 0.9rem; }}
    #article-detail-summary {{ margin-top: 12px; }}
    #article-detail-summary td.title a {{ color: var(--color-link); }}
    #article-detail-summary td.title {{ min-width: 200px; }}

    @media (max-width: 768px) {{
      .container {{ padding: 12px; }}
      h1 {{ font-size: 1.4rem; }}
      .meta {{ line-height: 1.7; }}
      .meta-note {{ display: block; margin-top: 2px; }}
      .stats {{ gap: 10px; margin-bottom: 20px; }}
      .stat-card {{ padding: 14px 18px; flex: 1; min-width: 130px; }}
      .stat-card .value {{ font-size: 1.6rem; }}
      .card {{ padding: 16px; margin-bottom: 16px; }}
      h2 {{ font-size: 1.05rem; margin-bottom: 12px; }}
      table {{ font-size: 0.8rem; }}
      th, td {{ padding: 8px 6px; }}
      td.title {{ max-width: 180px; }}
      .article-chip .chip-label {{ max-width: 180px; }}
      .article-controls {{ gap: 10px; }}
      #article-detail-summary .col-opt {{ display: none; }}
      #article-detail-summary td.title {{ min-width: 150px; }}
      .date-filter {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
      .date-filter input[type="date"] {{ width: 160px; }}
      .section, details.section {{ margin-bottom: 28px; }}
      .section-title {{ font-size: 1rem; }}
    }}
    @media (max-width: 480px) {{
      .container {{ padding: 8px; }}
      h1 {{ font-size: 1.2rem; }}
      .stat-card .value {{ font-size: 1.3rem; }}
      .quick-filter-btn, .date-filter button {{ padding: 8px 10px; }}
    }}
  </style>"""


def _build_data_script(chart_data_json: str) -> str:
    return (
        f'<script>\n'
        f'const ALL_DATA = {chart_data_json};\n'
        f'const CHART_COLORS = {json.dumps(CHART_COLORS)};\n'
        f'const TOP_N = {TOP_N};\n'
        f'const TITLE_MAX_LEN = {TITLE_MAX_LEN};\n'
        f'const PV_INCREASE_WINDOW_DAYS = {TAG_CHANGE_RATE_WINDOW_DAYS};\n'
        f'const BRAND_COLOR = {json.dumps(BRAND_COLOR)};\n'
        f'const BRAND_COLOR_BG = {json.dumps(BRAND_COLOR_BG)};\n'
        f'const BRAND_COLOR_BG_DARK = {json.dumps(BRAND_COLOR_BG_DARK)};\n'
        f'const ARTICLE_COMPARE_MAX = {ARTICLE_COMPARE_MAX};\n'
        f'const ROW_CLICK_HINT = {json.dumps(ROW_CLICK_HINT, ensure_ascii=False)};\n'
        f'</script>'
    )


def _build_filter_script() -> str:
    return """<script>
(function () {
  var rsBtns = [
    {count: 1,  label: "1ヶ月", step: "month", stepmode: "backward"},
    {count: 3,  label: "3ヶ月", step: "month", stepmode: "backward"},
    {count: 6,  label: "6ヶ月", step: "month", stepmode: "backward"},
    {count: 1,  label: "1年",   step: "year",  stepmode: "backward"},
    {count: 2,  label: "2年",   step: "year",  stepmode: "backward"},
    {step: "all", label: "全期間"}
  ];

  function isDarkMode() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function themeLayout() {
    var dark = isDarkMode();
    return {
      template: dark ? "plotly_dark" : "plotly_white",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: {color: dark ? "#e4e6eb" : "#333333"}
    };
  }

  function makeXaxis(dates) {
    var dark = isDarkMode();
    return {
      type: "date",
      range: dates.length ? [dates[0], dates[dates.length - 1]] : undefined,
      rangeselector: {
        buttons: rsBtns,
        bgcolor: dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG,
        activecolor: BRAND_COLOR,
        font: {color: dark ? "#e4e6eb" : "#333333"}
      },
      rangeslider: {visible: true, thickness: 0.05, bgcolor: dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG}
    };
  }

  function filtered() {
    var start = document.getElementById("articleStart").value;
    var end   = document.getElementById("articleEnd").value;
    return ALL_DATA.filter(function (r) {
      return (!start || r.created_at >= start) && (!end || r.created_at <= end);
    });
  }

  function latestDate(data) {
    return data.reduce(function (m, r) { return r.snapshot_date > m ? r.snapshot_date : m; }, "");
  }

  function updateTotalPvChart(data) {
    var dateMap = {};
    data.forEach(function (r) {
      dateMap[r.snapshot_date] = (dateMap[r.snapshot_date] || 0) + (r.page_views || 0);
    });
    var dates = Object.keys(dateMap).sort();
    var trace = {
      x: dates,
      y: dates.map(function (d) { return dateMap[d]; }),
      mode: "lines+markers",
      name: "合計PV",
      line: {color: BRAND_COLOR, width: 2},
      marker: {size: 6},
      hovertemplate: "%{x}<br>合計PV: %{y:,}<extra></extra>"
    };
    var isMobile = window.innerWidth <= 768;
    var layout = themeLayout();
    layout.title = "全記事の合計PV推移";
    layout.xaxis = makeXaxis(dates);
    layout.yaxis = {title: {text: "累計PV数"}};
    layout.hovermode = "x unified";
    layout.height = isMobile ? 300 : 450;
    Plotly.react("total-pv-chart", [trace], layout);
  }

  function updatePerArticleChart(data) {
    var ld = latestDate(data);
    var latest = data.filter(function (r) { return r.snapshot_date === ld; });
    latest.sort(function (a, b) { return (b.page_views || 0) - (a.page_views || 0); });
    var topIds = latest.slice(0, TOP_N).map(function (r) { return r.id; });

    var byId = {};
    data.forEach(function (r) {
      if (!byId[r.id]) { byId[r.id] = {title: r.title, rows: []}; }
      byId[r.id].rows.push(r);
    });

    var allDates = [];
    data.forEach(function (r) { if (allDates.indexOf(r.snapshot_date) < 0) { allDates.push(r.snapshot_date); } });
    allDates.sort();

    var traces = topIds.map(function (id, i) {
      var entry = byId[id] || {title: id, rows: []};
      var rows = entry.rows.slice().sort(function (a, b) { return a.snapshot_date < b.snapshot_date ? -1 : 1; });
      var full = entry.title;
      var short = full.length > TITLE_MAX_LEN ? full.slice(0, TITLE_MAX_LEN) + "…" : full;
      var pvs = rows.map(function (r) { return r.page_views; });
      var customdata = pvs.map(function (pv, idx) {
        if (idx === 0) return "−";
        var diff = pv - pvs[idx - 1];
        return (diff >= 0 ? "+" : "") + diff.toLocaleString("ja-JP");
      });
      return {
        x: rows.map(function (r) { return r.snapshot_date; }),
        y: pvs,
        customdata: customdata,
        mode: "lines+markers",
        name: short,
        line: {color: CHART_COLORS[i % CHART_COLORS.length], width: 2},
        marker: {size: 5},
        hovertemplate: full + "<br>%{x}<br>PV: %{y:,}<br>前日比: %{customdata}<extra></extra>"
      };
    });

    var isMobile = window.innerWidth <= 768;
    var layout = themeLayout();
    layout.title = "上位" + TOP_N + "記事のPV推移";
    layout.xaxis = makeXaxis(allDates);
    layout.yaxis = {title: {text: "累計PV数"}};
    layout.hovermode = "closest";
    layout.hoverlabel = {namelength: -1};
    layout.height = isMobile ? 380 : 550;
    layout.margin = {t: 60, r: isMobile ? 20 : 160};
    layout.showlegend = !isMobile;
    layout.legend = {orientation: "v", x: 1.02, y: 1};
    Plotly.react("per-article-chart", traces, layout);
  }

  function updateStats(data) {
    var ld = latestDate(data);
    var latest = data.filter(function (r) { return r.snapshot_date === ld; });
    var ids = {};
    latest.forEach(function (r) { ids[r.id] = true; });
    var totalPv = latest.reduce(function (s, r) { return s + (r.page_views || 0); }, 0);
    document.getElementById("stat-article-count").textContent = Object.keys(ids).length;
    document.getElementById("stat-total-pv").textContent = totalPv.toLocaleString("ja-JP");
  }

  function pvIncreaseValue(data, id, latestDateStr) {
    var rows = data.filter(function (r) { return r.id === id; })
      .sort(function (a, b) { return a.snapshot_date < b.snapshot_date ? -1 : 1; });
    if (rows.length === 0) { return null; }
    var latestRow = rows.filter(function (r) { return r.snapshot_date === latestDateStr; })[0];
    var latestPv = latestRow ? (latestRow.page_views || 0) : 0;
    var targetDate = new Date(latestDateStr + "T00:00:00Z");
    targetDate.setUTCDate(targetDate.getUTCDate() - PV_INCREASE_WINDOW_DAYS);
    var targetDateStr = targetDate.toISOString().slice(0, 10);
    var priorRows = rows.filter(function (r) { return r.snapshot_date <= targetDateStr; });
    if (priorRows.length > 0) {
      return latestPv - (priorRows[priorRows.length - 1].page_views || 0);
    }
    // window日分の履歴がない場合のフォールバック（公開間もない記事は公開時点=0からの
    // 増加量とみなしPV数をそのまま使い、データ収集期間が足りないだけの古い記事は
    // 最初に記録されたスナップショットからの増加量を使う）
    var createdAt = (latestRow || rows[0]).created_at;
    var ageDays = Math.round(
      (new Date(latestDateStr + "T00:00:00Z") - new Date(createdAt + "T00:00:00Z")) / 86400000
    );
    if (ageDays < PV_INCREASE_WINDOW_DAYS) {
      return latestPv;
    }
    return latestPv - (rows[0].page_views || 0);
  }

  function pvIncreaseDisplay(diff) {
    if (diff === null) { return "−"; }
    return (diff >= 0 ? "+" : "") + diff.toLocaleString("ja-JP");
  }

  var rankingSort = {key: "page_views", dir: "desc"};

  function rankingSortValue(row, key, data, ld) {
    switch (key) {
      case "title": return row.title || "";
      case "created_at": return row.created_at || "";
      case "pv_increase":
        var diff = pvIncreaseValue(data, row.id, ld);
        return diff === null ? -Infinity : diff;
      case "likes": return row.likes || 0;
      case "stocks": return row.stocks || 0;
      case "page_views":
      default: return row.page_views || 0;
    }
  }

  function updateSortIndicators() {
    document.querySelectorAll("th.sortable").forEach(function (th) {
      var arrow = th.querySelector(".sort-arrow");
      if (th.getAttribute("data-key") === rankingSort.key) {
        arrow.textContent = rankingSort.dir === "asc" ? "▲" : "▼";
      } else {
        arrow.textContent = "";
      }
    });
  }

  function updateRankingTable(data) {
    var ld = latestDate(data);
    var latest = data.filter(function (r) { return r.snapshot_date === ld; });
    var key = rankingSort.key;
    var dir = rankingSort.dir === "asc" ? 1 : -1;
    latest.sort(function (a, b) {
      var va = rankingSortValue(a, key, data, ld);
      var vb = rankingSortValue(b, key, data, ld);
      if (va < vb) { return -1 * dir; }
      if (va > vb) { return 1 * dir; }
      return 0;
    });
    updateSortIndicators();
    document.getElementById("ranking-tbody").innerHTML = latest.map(function (r, i) {
      return "<tr data-id='" + String(r.id).replace(/[&<>"']/g, "") + "' title='" + ROW_CLICK_HINT + "'>" +
        "<td>" + (i + 1) + "</td>" +
        "<td class='title'><a href='" + r.url + "' target='_blank'>" + r.title + "</a></td>" +
        "<td>" + r.created_at + "</td>" +
        "<td class='num'>" + (r.page_views || 0).toLocaleString("ja-JP") + "</td>" +
        "<td class='num'>" + pvIncreaseDisplay(pvIncreaseValue(data, r.id, ld)) + "</td>" +
        "<td class='num'>" + (r.likes || 0).toLocaleString("ja-JP") + "</td>" +
        "<td class='num'>" + (r.stocks || 0).toLocaleString("ja-JP") + "</td>" +
        "</tr>";
    }).join("");
  }

  function applyFilter() {
    var data = filtered();
    updateTotalPvChart(data);
    updatePerArticleChart(data);
    updateStats(data);
    updateRankingTable(data);
  }

  var STATIC_CHART_IDS = ["pv-increase-bar-chart", "pv-increase-trend-chart", "tag-pv-chart", "keyword-pv-chart"];
  // 日付レンジセレクター（右上ボタン）を持つのは時系列チャートのみ（棒グラフには存在しない）
  var STATIC_CHART_IDS_WITH_RANGESELECTOR = ["pv-increase-trend-chart", "tag-pv-chart", "keyword-pv-chart"];

  function applyStaticChartTheme() {
    var dark = isDarkMode();
    var fontColor = dark ? "#e4e6eb" : "#333333";
    STATIC_CHART_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || typeof Plotly === "undefined") { return; }
      Plotly.relayout(id, {
        template: dark ? "plotly_dark" : "plotly_white",
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        "font.color": fontColor
      });
    });
    STATIC_CHART_IDS_WITH_RANGESELECTOR.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || typeof Plotly === "undefined") { return; }
      Plotly.relayout(id, {
        "xaxis.rangeselector.bgcolor": dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG,
        "xaxis.rangeselector.font.color": fontColor,
        "xaxis.rangeslider.bgcolor": dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG
      });
    });
  }

  function applyMobileCollapse() {
    var isMobile = window.innerWidth <= 768;
    document.querySelectorAll("details.section--collapsible").forEach(function (d) {
      if (isMobile) { d.removeAttribute("open"); }
      else { d.setAttribute("open", ""); }
    });
  }

  document.getElementById("articleStart").addEventListener("change", function () {
    document.querySelectorAll(".quick-filter-btn").forEach(function (b) { b.classList.remove("active"); });
    applyFilter();
  });
  document.getElementById("articleEnd").addEventListener("change", function () {
    document.querySelectorAll(".quick-filter-btn").forEach(function (b) { b.classList.remove("active"); });
    applyFilter();
  });
  document.getElementById("clearFilter").addEventListener("click", function () {
    document.getElementById("articleStart").value = "";
    document.getElementById("articleEnd").value = "";
    document.querySelectorAll(".quick-filter-btn").forEach(function (b) { b.classList.remove("active"); });
    applyFilter();
  });
  document.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.getAttribute("data-key");
      if (rankingSort.key === key) {
        rankingSort.dir = rankingSort.dir === "asc" ? "desc" : "asc";
      } else {
        rankingSort.key = key;
        rankingSort.dir = (key === "title" || key === "created_at") ? "asc" : "desc";
      }
      updateRankingTable(filtered());
    });
  });
  document.querySelectorAll(".quick-filter-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var months = parseInt(btn.getAttribute("data-months"), 10);
      var today = new Date();
      var start = new Date(today);
      start.setMonth(start.getMonth() - months);
      document.getElementById("articleStart").value = start.toISOString().slice(0, 10);
      document.getElementById("articleEnd").value = today.toISOString().slice(0, 10);
      document.querySelectorAll(".quick-filter-btn").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      applyFilter();
    });
  });

  applyFilter();
  applyStaticChartTheme();
  applyMobileCollapse();

  if (window.matchMedia) {
    var darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    var handleSchemeChange = function () {
      applyFilter();
      applyStaticChartTheme();
    };
    if (darkModeQuery.addEventListener) {
      darkModeQuery.addEventListener("change", handleSchemeChange);
    } else if (darkModeQuery.addListener) {
      darkModeQuery.addListener(handleSchemeChange);
    }
  }

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(applyFilter, 200);
  });
}());
</script>"""


def _build_pv_trend_section(total_chart: str, per_article_chart: str) -> str:
    return f"""
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">PV推移</h2>
        <span class="section-badge section-badge--live">期間フィルター連動</span>
      </div>
      <div class="card">
        {total_chart}
      </div>
      <div class="card">
        {per_article_chart}
      </div>
    </section>
"""


def _build_article_detail_section() -> str:
    return f"""
    <section class="section" id="article-detail">
      <div class="section-header">
        <h2 class="section-title">記事別PV推移</h2>
        <span class="section-badge section-badge--fixed">最大{ARTICLE_COMPARE_MAX}記事まで比較</span>
      </div>
      <div class="card">
        <div class="article-picker">
          <input type="search" id="article-search" list="article-options"
                 placeholder="記事タイトルで検索して追加…" autocomplete="off" aria-label="記事タイトルで検索">
          <datalist id="article-options"></datalist>
          <button type="button" id="article-add">追加</button>
          <button type="button" id="article-clear">クリア</button>
        </div>
        <p class="article-hint" id="article-hint">ランキング表の行をクリックしても選べます（Shift/Ctrl+クリックで比較に追加）。</p>
        <div class="article-chips" id="article-chips"></div>
        <div class="article-controls">
          <div class="seg" data-control="metric">指標:
            <button type="button" class="seg-btn" data-value="page_views">PV</button>
            <button type="button" class="seg-btn" data-value="likes">いいね</button>
            <button type="button" class="seg-btn" data-value="stocks">ストック</button>
          </div>
          <div class="seg" data-control="xaxis">横軸:
            <button type="button" class="seg-btn" data-value="date">日付</button>
            <button type="button" class="seg-btn" data-value="elapsed">投稿からの日数</button>
          </div>
          <div class="seg" data-control="value" id="seg-value">表示:
            <button type="button" class="seg-btn" data-value="cumulative">累計</button>
            <button type="button" class="seg-btn" data-value="daily">日別増加</button>
          </div>
        </div>
        <div id="article-detail-empty" class="article-empty">記事を選ぶと推移を表示します。</div>
        <div id="article-detail-chart"></div>
        <div class="table-wrapper">
          <table id="article-detail-summary" hidden>
            <thead>
              <tr>
                <th></th>
                <th>タイトル</th>
                <th class="col-opt">投稿日</th>
                <th id="summary-metric-head">累計PV</th>
                <th>直近7日</th>
                <th>直近30日</th>
                <th class="col-opt">いいね</th>
                <th class="col-opt">ストック</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="article-detail-summary-body"></tbody>
          </table>
        </div>
      </div>
    </section>
"""


def _build_article_detail_script() -> str:
    return """<script>
(function () {
  var METRIC_LABELS = {page_views: "PV", likes: "いいね", stocks: "ストック"};
  var VALID = {
    metric: ["page_views", "likes", "stocks"],
    xaxis: ["date", "elapsed"],
    value: ["cumulative", "daily"]
  };
  var DAY_MS = 86400000;
  // 投稿日からこの日数以内に最初のスナップショットがある記事は、投稿日を 0 起点とみなす
  var NEW_ARTICLE_BASELINE_DAYS = 7;
  var state = {ids: [], metric: "page_views", xaxis: "date", value: "cumulative"};
  var userTouched = false;

  var chartEl = document.getElementById("article-detail-chart");
  var emptyEl = document.getElementById("article-detail-empty");
  var searchEl = document.getElementById("article-search");
  var hintEl = document.getElementById("article-hint");
  var defaultHint = hintEl.textContent;
  if (!chartEl) { return; }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function truncate(s) {
    return s.length > TITLE_MAX_LEN ? s.slice(0, TITLE_MAX_LEN) + "…" : s;
  }
  function dayDiff(a, b) {
    return Math.round((Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10))) / DAY_MS);
  }
  function addDays(d, n) {
    return new Date(Date.parse(d) + n * DAY_MS).toISOString().slice(0, 10);
  }
  function fmt(n) { return Math.round(n).toLocaleString("ja-JP"); }
  function fmtSigned(n) {
    if (n === null || n === undefined) { return "−"; }
    return (n >= 0 ? "+" : "") + fmt(n);
  }

  // ---- 記事インデックス ----
  var articles = {};
  var globalMinDate = "";
  ALL_DATA.forEach(function (r) {
    var a = articles[r.id];
    if (!a) { a = articles[r.id] = {id: r.id, created_at: r.created_at, rows: []}; }
    a.rows.push(r);
    if (!globalMinDate || r.snapshot_date < globalMinDate) { globalMinDate = r.snapshot_date; }
  });
  var articleList = Object.keys(articles).map(function (id) {
    var a = articles[id];
    a.rows.sort(function (x, y) { return x.snapshot_date < y.snapshot_date ? -1 : 1; });
    a.latest = a.rows[a.rows.length - 1];
    a.title = a.latest.title;
    a.url = a.latest.url;
    return a;
  });
  articleList.sort(function (x, y) { return (y.latest.page_views || 0) - (x.latest.page_views || 0); });

  var labelToId = {};
  var datalist = document.getElementById("article-options");
  datalist.innerHTML = articleList.map(function (a) {
    var label = a.title + "（" + a.created_at + "）";
    labelToId[label] = a.id;
    return "<option value=\\"" + escapeHtml(label) + "\\"></option>";
  }).join("");

  function resolveQuery(q) {
    q = q.trim();
    if (!q) { return {}; }
    if (labelToId[q]) { return {id: labelToId[q]}; }
    var lower = q.toLowerCase();
    var hits = articleList.filter(function (a) { return a.title.toLowerCase().indexOf(lower) >= 0; });
    if (hits.length === 1) { return {id: hits[0].id}; }
    return {error: hits.length ? hits.length + "件が該当します。候補から1つ選んでください。" : "該当する記事がありません。"};
  }

  // ---- 系列計算 ----
  function computeSeries(a, metric) {
    var s = {dates: [], elapsed: [], cum: [], inc: [], incHover: []};
    var prev = null;
    a.rows.forEach(function (r, i) {
      var v = r[metric] || 0;
      var d = r.snapshot_date;
      var inc = null, hover = "";
      var baseDate = null, baseVal = null;
      if (prev) {
        baseDate = prev.snapshot_date; baseVal = prev[metric] || 0;
      } else if (dayDiff(a.created_at, d) <= NEW_ARTICLE_BASELINE_DAYS) {
        baseDate = a.created_at; baseVal = 0;
      }
      if (baseDate !== null) {
        var days = Math.max(dayDiff(baseDate, d), 1);
        var diff = v - baseVal;
        inc = Math.round(diff / days * 10) / 10;
        hover = days > 1
          ? "前回取得から" + days + "日分: " + fmtSigned(diff) + "（1日平均 " + inc.toLocaleString("ja-JP") + "）"
          : "前日比: " + fmtSigned(diff);
      }
      s.dates.push(d);
      s.elapsed.push(dayDiff(a.created_at, d));
      s.cum.push(v);
      s.inc.push(inc);
      s.incHover.push(hover);
      prev = r;
    });
    return s;
  }

  // 直近 N 日の増加量（ランキング表の増減PVと同じ考え方）
  function recentIncrease(a, metric, windowDays) {
    var latest = a.latest;
    var target = addDays(latest.snapshot_date, -windowDays);
    var prior = null;
    a.rows.forEach(function (r) { if (r.snapshot_date <= target) { prior = r; } });
    if (prior) { return (latest[metric] || 0) - (prior[metric] || 0); }
    if (dayDiff(a.created_at, latest.snapshot_date) < windowDays) { return latest[metric] || 0; }
    return null;
  }

  // ---- 状態とURL ----
  function colorOf(id) {
    var i = state.ids.indexOf(id);
    return CHART_COLORS[(i < 0 ? 0 : i) % CHART_COLORS.length];
  }

  function readHash() {
    var h = location.hash.replace(/^#/, "");
    if (!h) { return false; }
    var p = new URLSearchParams(h);
    var ids = (p.get("article") || "").split(",").filter(function (id) { return articles[id]; });
    if (!ids.length) { return false; }
    state.ids = ids.slice(0, ARTICLE_COMPARE_MAX);
    ["metric", "xaxis", "value"].forEach(function (k) {
      var v = p.get(k === "xaxis" ? "x" : k);
      if (VALID[k].indexOf(v) >= 0) { state[k] = v; }
    });
    return true;
  }

  function writeHash() {
    if (!userTouched || !window.history || !history.replaceState) { return; }
    var base = location.pathname + location.search;
    if (!state.ids.length) { history.replaceState(null, "", base); return; }
    var p = new URLSearchParams();
    p.set("article", state.ids.join(","));
    if (state.metric !== "page_views") { p.set("metric", state.metric); }
    if (state.xaxis !== "date") { p.set("x", state.xaxis); }
    if (state.value !== "cumulative") { p.set("value", state.value); }
    history.replaceState(null, "", base + "#" + p.toString().replace(/%2C/g, ","));
  }

  function setHint(msg, warn) {
    hintEl.textContent = msg || defaultHint;
    hintEl.classList.toggle("is-warning", !!warn);
  }

  function addArticle(id) {
    if (state.ids.indexOf(id) >= 0) { return true; }
    if (state.ids.length >= ARTICLE_COMPARE_MAX) {
      setHint("比較できるのは最大" + ARTICLE_COMPARE_MAX + "記事までです。どれかを外してから追加してください。", true);
      return false;
    }
    state.ids.push(id);
    return true;
  }

  // ---- 描画 ----
  function isDarkMode() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function renderControls() {
    document.querySelectorAll("#article-detail .seg").forEach(function (seg) {
      var key = seg.getAttribute("data-control");
      seg.querySelectorAll(".seg-btn").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-value") === state[key]);
      });
    });
    // 1記事のときは累計と日別増加を同時に表示するので「表示」切替は不要
    document.getElementById("seg-value").hidden = state.ids.length < 2;
    document.getElementById("article-add").disabled = state.ids.length >= ARTICLE_COMPARE_MAX;
    document.getElementById("article-clear").disabled = !state.ids.length;
  }

  function renderChips() {
    document.getElementById("article-chips").innerHTML = state.ids.map(function (id) {
      var a = articles[id];
      return "<span class=\\"article-chip\\">" +
        "<span class=\\"swatch\\" style=\\"background:" + colorOf(id) + "\\"></span>" +
        "<span class=\\"chip-label\\" title=\\"" + escapeHtml(a.title) + "\\">" + escapeHtml(a.title) + "</span>" +
        "<button type=\\"button\\" class=\\"chip-remove\\" data-id=\\"" + escapeHtml(id) + "\\" aria-label=\\"外す\\">×</button>" +
        "</span>";
    }).join("");
  }

  function renderSummary() {
    var table = document.getElementById("article-detail-summary");
    table.hidden = !state.ids.length;
    var m = state.metric;
    document.getElementById("summary-metric-head").textContent = "累計" + METRIC_LABELS[m];
    document.getElementById("article-detail-summary-body").innerHTML = state.ids.map(function (id) {
      var a = articles[id];
      var l = a.latest;
      return "<tr>" +
        "<td><span class=\\"swatch\\" style=\\"background:" + colorOf(id) + "\\"></span></td>" +
        "<td class=\\"title\\"><a href=\\"" + escapeHtml(a.url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">" + escapeHtml(a.title) + "</a></td>" +
        "<td class=\\"col-opt\\">" + escapeHtml(a.created_at) + "</td>" +
        "<td class=\\"num\\">" + fmt(l[m] || 0) + "</td>" +
        "<td class=\\"num\\">" + fmtSigned(recentIncrease(a, m, 7)) + "</td>" +
        "<td class=\\"num\\">" + fmtSigned(recentIncrease(a, m, 30)) + "</td>" +
        "<td class=\\"num col-opt\\">" + fmt(l.likes || 0) + "</td>" +
        "<td class=\\"num col-opt\\">" + fmt(l.stocks || 0) + "</td>" +
        "<td><button type=\\"button\\" class=\\"chip-remove\\" data-id=\\"" + escapeHtml(id) + "\\" aria-label=\\"外す\\">×</button></td>" +
        "</tr>";
    }).join("");
  }

  function highlightRows() {
    document.querySelectorAll("#ranking-tbody tr[data-id]").forEach(function (tr) {
      tr.classList.toggle("is-selected", state.ids.indexOf(tr.getAttribute("data-id")) >= 0);
    });
  }

  function renderChart() {
    var sel = state.ids.map(function (id) { return articles[id]; });
    if (!sel.length || typeof Plotly === "undefined") {
      if (typeof Plotly !== "undefined") { Plotly.purge(chartEl); }
      chartEl.style.display = "none";
      emptyEl.style.display = "";
      return;
    }
    chartEl.style.display = "";
    emptyEl.style.display = "none";

    var dark = isDarkMode();
    var fontColor = dark ? "#e4e6eb" : "#333333";
    var label = METRIC_LABELS[state.metric];
    var elapsed = state.xaxis === "elapsed";
    var single = sel.length === 1;
    var mobile = window.innerWidth <= 768;
    var xHover = elapsed ? "投稿から%{x}日目" : "%{x}";
    var traces = [];

    sel.forEach(function (a) {
      var s = computeSeries(a, state.metric);
      var x = elapsed ? s.elapsed : s.dates;
      var color = colorOf(a.id);
      if (single) {
        traces.push({
          type: "bar", x: x, y: s.inc, yaxis: "y2", name: "日別増加（1日あたり）",
          marker: {color: color, opacity: 0.35}, customdata: s.incHover,
          hovertemplate: xHover + "<br>%{customdata}<extra></extra>"
        });
        traces.push({
          type: "scatter", mode: "lines+markers", x: x, y: s.cum, name: "累計" + label,
          line: {color: color, width: 2.5}, marker: {size: 5},
          hovertemplate: xHover + "<br>累計" + label + ": %{y:,}<extra></extra>"
        });
      } else {
        var daily = state.value === "daily";
        traces.push({
          type: "scatter", mode: "lines+markers", x: x, y: daily ? s.inc : s.cum,
          name: truncate(a.title), line: {color: color, width: 2}, marker: {size: 4},
          connectgaps: false, customdata: daily ? s.incHover : null,
          hovertemplate: escapeHtml(truncate(a.title)) + "<br>" + xHover + "<br>" +
            (daily ? "%{customdata}" : "累計" + label + ": %{y:,}") + "<extra></extra>"
        });
      }
    });

    var xaxis;
    if (elapsed) {
      xaxis = {title: {text: mobile ? "" : "投稿からの経過日数"}, rangeslider: {visible: true, thickness: 0.05,
        bgcolor: dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG}};
    } else {
      xaxis = {
        type: "date",
        rangeselector: {
          buttons: [
            {count: 1, label: "1ヶ月", step: "month", stepmode: "backward"},
            {count: 3, label: "3ヶ月", step: "month", stepmode: "backward"},
            {count: 6, label: "6ヶ月", step: "month", stepmode: "backward"},
            {step: "all", label: "全期間"}
          ],
          bgcolor: dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG,
          activecolor: BRAND_COLOR, font: {color: fontColor}
        },
        rangeslider: {visible: true, thickness: 0.05, bgcolor: dark ? BRAND_COLOR_BG_DARK : BRAND_COLOR_BG}
      };
    }

    // 投稿日の縦線（日付軸かつデータ収集期間内に投稿された記事のみ）
    var shapes = [], annotations = [];
    if (!elapsed) {
      sel.forEach(function (a) {
        if (a.created_at < globalMinDate) { return; }
        shapes.push({type: "line", xref: "x", yref: "paper", x0: a.created_at, x1: a.created_at, y0: 0, y1: 1,
          line: {color: colorOf(a.id), width: 1.5, dash: "dot"}});
        annotations.push({xref: "x", yref: "paper", x: a.created_at, y: 1, yanchor: "bottom", showarrow: false,
          text: "投稿", font: {size: 11, color: colorOf(a.id)}});
      });
    }

    var yTitle = single || state.value === "cumulative" ? "累計" + label : "日別増加（1日あたり）";
    var layout = {
      title: {text: single ? escapeHtml(truncate(sel[0].title)) : "記事別" + label + "推移（比較）", font: {size: 15}},
      template: dark ? "plotly_dark" : "plotly_white",
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: {color: fontColor},
      xaxis: xaxis,
      yaxis: {title: {text: mobile ? "" : yTitle}, rangemode: "tozero"},
      hovermode: single ? "x unified" : "closest",
      height: mobile ? 420 : 480,
      margin: {t: 70, r: single ? 60 : 20, l: mobile ? 40 : 70, b: 40},
      legend: {orientation: "h", x: 0, y: elapsed && !mobile ? -0.36 : -0.28, yanchor: "top"},
      shapes: shapes, annotations: annotations,
      bargap: 0.1
    };
    if (single) {
      layout.yaxis2 = {title: {text: mobile ? "" : "日別増加"}, overlaying: "y", side: "right",
        showgrid: false, rangemode: "tozero"};
    }
    Plotly.react(chartEl, traces, layout, {responsive: true, displaylogo: false});
  }

  function render() {
    setHint(null);
    renderAll();
  }
  function renderAll() {
    renderControls();
    renderChips();
    renderSummary();
    highlightRows();
    renderChart();
    writeHash();
  }

  // ---- イベント ----
  function submitSearch() {
    var r = resolveQuery(searchEl.value);
    if (r.error) { setHint(r.error, true); return; }
    if (!r.id) { return; }
    userTouched = true;
    setHint(null);
    if (addArticle(r.id)) { searchEl.value = ""; }
    renderAll();
  }
  document.getElementById("article-add").addEventListener("click", submitSearch);
  searchEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); submitSearch(); }
  });
  // 候補リストから選んだ時点で追加する
  searchEl.addEventListener("input", function () {
    if (labelToId[searchEl.value]) { submitSearch(); }
  });
  document.getElementById("article-clear").addEventListener("click", function () {
    userTouched = true;
    state.ids = [];
    render();
  });
  document.getElementById("article-detail").addEventListener("click", function (e) {
    var rm = e.target.closest(".chip-remove");
    if (rm) {
      userTouched = true;
      state.ids = state.ids.filter(function (id) { return id !== rm.getAttribute("data-id"); });
      render();
      return;
    }
    var seg = e.target.closest(".seg-btn");
    if (seg) {
      userTouched = true;
      state[seg.parentNode.getAttribute("data-control")] = seg.getAttribute("data-value");
      render();
    }
  });

  var tbody = document.getElementById("ranking-tbody");
  if (tbody) {
    tbody.addEventListener("click", function (e) {
      if (e.target.closest("a")) { return; }
      var tr = e.target.closest("tr[data-id]");
      if (!tr || !articles[tr.getAttribute("data-id")]) { return; }
      var id = tr.getAttribute("data-id");
      userTouched = true;
      setHint(null);
      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        if (state.ids.indexOf(id) >= 0) {
          state.ids = state.ids.filter(function (x) { return x !== id; });
        } else {
          addArticle(id);
        }
      } else {
        state.ids = [id];
      }
      renderAll();
      var rect = document.getElementById("article-detail").getBoundingClientRect();
      if (rect.top < 0 || rect.top > window.innerHeight * 0.6) {
        document.getElementById("article-detail").scrollIntoView({behavior: "smooth", block: "start"});
      }
    });
    // ランキング表はソートや発行日フィルターで再描画されるので、そのたびに選択行を付け直す
    if (window.MutationObserver) {
      new MutationObserver(highlightRows).observe(tbody, {childList: true});
    }
  }

  window.addEventListener("hashchange", function () {
    if (readHash()) { userTouched = true; render(); }
  });
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) { mq.addEventListener("change", renderChart); }
    else if (mq.addListener) { mq.addListener(renderChart); }
  }
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderChart, 200);
  });

  // ---- 初期表示 ----
  if (readHash()) {
    userTouched = true;
  } else if (articleList.length) {
    state.ids = [articleList[0].id];  // 未指定時は累計PVトップの記事を表示
  }
  render();
}());
</script>"""


def _build_rising_articles_section(
    pv_increase_bar_chart: str | None, pv_increase_trend_chart: str | None
) -> str:
    if not pv_increase_bar_chart and not pv_increase_trend_chart:
        return ""
    cards = ""
    if pv_increase_bar_chart:
        cards += f"""
      <div class="card">
        {pv_increase_bar_chart}
      </div>
"""
    if pv_increase_trend_chart:
        cards += f"""
      <div class="card">
        {pv_increase_trend_chart}
      </div>
"""
    return f"""
    <details class="section section--collapsible" open>
      <summary class="section-title">
        急上昇記事
        <span class="section-badge section-badge--fixed">直近{TAG_CHANGE_RATE_WINDOW_DAYS}日固定</span>
      </summary>
      <div class="section-body">
        {cards}
      </div>
    </details>
"""


def _build_tag_keyword_section(
    tag_chart: str | None, tag_table: str | None,
    keyword_chart: str | None, keyword_table: str | None,
) -> str:
    has_tag = bool(tag_chart and tag_table)
    has_keyword = bool(keyword_chart and keyword_table)
    if not has_tag and not has_keyword:
        return ""
    cards = ""
    if has_tag:
        cards += f"""
      <div class="card">
        {tag_chart}
      </div>

      <div class="card">
        <h3 class="subsection-title">タグ別PV変化率（直近{TAG_CHANGE_RATE_WINDOW_DAYS}日）</h3>
        <div class="table-wrapper">
          {tag_table}
        </div>
      </div>
"""
    if has_keyword:
        cards += f"""
      <div class="card">
        {keyword_chart}
      </div>

      <div class="card">
        <h3 class="subsection-title">キーワード別PV変化率（タイトル内・直近{TAG_CHANGE_RATE_WINDOW_DAYS}日）</h3>
        <div class="table-wrapper">
          {keyword_table}
        </div>
      </div>
"""
    return f"""
    <details class="section section--collapsible" open>
      <summary class="section-title">
        タグ・キーワード分析
        <span class="section-badge section-badge--fixed">直近{TAG_CHANGE_RATE_WINDOW_DAYS}日固定</span>
      </summary>
      <div class="section-body">
        {cards}
      </div>
    </details>
"""


def generate_html(
    ranking_table: str,
    total_chart: str,
    per_article_chart: str,
    latest_date: str,
    total_pv: int,
    article_count: int,
    start_date: str,
    end_date: str,
    chart_data_json: str,
    tag_chart: str | None = None,
    tag_table: str | None = None,
    keyword_chart: str | None = None,
    keyword_table: str | None = None,
    pv_increase_bar_chart: str | None = None,
    pv_increase_trend_chart: str | None = None,
) -> str:
    pv_trend_section = _build_pv_trend_section(total_chart, per_article_chart)
    rising_articles_section = _build_rising_articles_section(pv_increase_bar_chart, pv_increase_trend_chart)
    tag_keyword_section = _build_tag_keyword_section(tag_chart, tag_table, keyword_chart, keyword_table)

    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    style_block = _build_style_block()
    data_script = _build_data_script(chart_data_json)
    filter_script = _build_filter_script()
    article_detail_section = _build_article_detail_section()
    article_detail_script = _build_article_detail_script()

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Qiita 記事分析レポート</title>
  {plotly_cdn}
  {style_block}
</head>
<body>
  <div class="container">
    <h1>Qiita 記事分析レポート</h1>
    <p class="meta">最終更新: {latest_date}　|　表示期間: {start_date} 〜 {end_date}　<span class="meta-note">（グラフ右上のボタンやスライダーで変更可）</span></p>

    <div class="stats">
      <div class="stat-card">
        <div class="label">総記事数</div>
        <div class="value" id="stat-article-count">{article_count}</div>
      </div>
      <div class="stat-card">
        <div class="label">総PV数</div>
        <div class="value" id="stat-total-pv">{total_pv:,}</div>
      </div>
    </div>

    <div class="card">
      <h2>発行日フィルター</h2>
      <div class="quick-filters">
        <button class="quick-filter-btn" data-months="1">1ヶ月以内</button>
        <button class="quick-filter-btn" data-months="3">3ヶ月以内</button>
        <button class="quick-filter-btn" data-months="6">半年以内</button>
        <button class="quick-filter-btn" data-months="12">1年以内</button>
      </div>
      <div class="date-filter">
        <label>発行日：<input type="date" id="articleStart"></label>
        〜
        <label><input type="date" id="articleEnd"></label>
        <button id="clearFilter">クリア</button>
      </div>
    </div>

    <div class="card">
      <h2>PVランキング</h2>
      <div class="table-wrapper">
        {ranking_table}
      </div>
    </div>

    {article_detail_section}
    {pv_trend_section}
    {rising_articles_section}
    {tag_keyword_section}
  </div>
  {data_script}
  {filter_script}
  {article_detail_script}
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

    ranking_table = build_ranking_table(latest_in_range, df_filtered, TAG_CHANGE_RATE_WINDOW_DAYS)
    total_chart = build_total_pv_chart(df_filtered, start_date, end_date)
    per_article_chart = build_per_article_chart(df_filtered, latest_in_range, start_date, end_date)
    chart_data_json = build_chart_data_json(df_filtered)

    pv_increases = compute_article_pv_increases(latest_in_range, df_filtered, TAG_CHANGE_RATE_WINDOW_DAYS)
    top_increase_ids = select_top_pv_increase_ids(pv_increases, TOP_N)
    pv_increase_bar_chart = build_pv_increase_bar_chart(
        latest_in_range, pv_increases, top_increase_ids, TAG_CHANGE_RATE_WINDOW_DAYS
    )
    pv_increase_trend_chart = build_pv_increase_trend_chart(
        df_filtered, top_increase_ids, start_date, end_date
    )

    tag_chart = tag_table = keyword_chart = keyword_table = None
    article_rates_by_id = _compute_article_change_rates(df_filtered, TAG_CHANGE_RATE_WINDOW_DAYS)

    tag_long_df = explode_tags(df_filtered)
    all_tag_names = tag_long_df["group"].unique().tolist()

    if all_tag_names:
        tag_medians = median_pv_increase_by_group(tag_long_df, article_rates_by_id)
        tag_groups = select_top_groups_by_median_pv_increase(tag_medians, TOP_N)
        if tag_groups:
            tag_daily = aggregate_group_daily_pv(tag_long_df)
            tag_chart = build_group_pv_chart(
                tag_daily, tag_groups, start_date, end_date, "タグ別PV推移", "tag-pv-chart"
            )
            tag_table = build_group_change_rate_table(
                tag_daily, tag_groups, TAG_CHANGE_RATE_WINDOW_DAYS, tag_medians
            )

        # キーワードはタグ別ランキングの当落に関わらず、これまでに使われた全タグ名を
        # タイトル内検索の対象にして、独自に上位N件を選ぶ（タグ名がタイトルに含まれない
        # ケースが多いため、上位タグに限定すると表示件数が減ってしまう）
        keyword_long_df = explode_keywords(df_filtered, all_tag_names)
        keyword_medians = median_pv_increase_by_group(keyword_long_df, article_rates_by_id)
        keyword_groups = select_top_groups_by_median_pv_increase(keyword_medians, TOP_N)
        if keyword_groups:
            keyword_daily = aggregate_group_daily_pv(keyword_long_df)
            keyword_chart = build_group_pv_chart(
                keyword_daily, keyword_groups, start_date, end_date,
                "キーワード別PV推移（タイトル内）", "keyword-pv-chart",
            )
            keyword_table = build_group_change_rate_table(
                keyword_daily, keyword_groups, TAG_CHANGE_RATE_WINDOW_DAYS, keyword_medians
            )

    html = generate_html(
        ranking_table,
        total_chart,
        per_article_chart,
        latest_date,
        total_pv,
        article_count,
        start_date,
        end_date,
        chart_data_json,
        tag_chart,
        tag_table,
        keyword_chart,
        keyword_table,
        pv_increase_bar_chart,
        pv_increase_trend_chart,
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"レポートを生成しました: {REPORT_PATH}  （期間: {start_date} 〜 {end_date}）")


if __name__ == "__main__":
    main()
