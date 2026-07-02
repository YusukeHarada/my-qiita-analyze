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
from datetime import date, timedelta

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
TITLE_MAX_LEN = 30
TAG_CHANGE_RATE_WINDOW_DAYS = 7


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
    ranked = latest.copy()
    ranked[["page_views", "likes", "stocks"]] = ranked[["page_views", "likes", "stocks"]].fillna(0)
    ranked = ranked.sort_values("page_views", ascending=False).reset_index(drop=True)
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
      <tbody id="ranking-tbody">{rows}</tbody>
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


def top_frequent_tags(long_df: pd.DataFrame, n: int = TOP_N) -> list[str]:
    if long_df.empty:
        return []
    latest_date = long_df["snapshot_date"].max()
    latest = long_df[long_df["snapshot_date"] == latest_date]
    counts = latest.groupby("group")["id"].nunique().sort_values(ascending=False)
    return counts.head(n).index.tolist()


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
        xaxis=_range_selector_xaxis(start_date, end_date),
        yaxis_title="合計PV数",
        hovermode="closest",
        template="plotly_white",
        height=450,
        margin=dict(t=60, r=160),
        legend=dict(orientation="v", x=1.02, y=1),
    )
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id=div_id)


def build_group_change_rate_table(daily_df: pd.DataFrame, groups: list[str], window_days: int = 7) -> str:
    if daily_df.empty or not groups:
        return "<p>データがありません。</p>"

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
            rate_value, rate_display = float("-inf"), "データ不足"
        elif prev_pv == 0:
            rate_value = float("inf") if latest_pv > 0 else 0.0
            rate_display = "新規" if latest_pv > 0 else "0.0%"
        else:
            rate_value = (latest_pv - prev_pv) / prev_pv * 100
            rate_display = f"{'+' if rate_value >= 0 else ''}{rate_value:.1f}%"

        results.append(dict(
            group=group, latest_pv=latest_pv, prev_pv=prev_pv,
            rate_value=rate_value, rate_display=rate_display,
        ))

    results.sort(key=lambda r: r["rate_value"], reverse=True)

    rows = ""
    for r in results:
        prev_pv_display = f"{r['prev_pv']:,}" if r["prev_pv"] is not None else "−"
        rows += (
            f"<tr>"
            f"<td>{r['group']}</td>"
            f"<td class='num'>{r['latest_pv']:,}</td>"
            f"<td class='num'>{prev_pv_display}</td>"
            f"<td class='num'>{r['rate_display']}</td>"
            f"</tr>"
        )

    return f"""
    <table>
      <thead>
        <tr>
          <th>グループ</th><th>直近PV</th><th>{window_days}日前PV</th><th>変化率</th>
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
) -> str:
    extra_sections = ""
    if tag_chart and tag_table:
        extra_sections += f"""
    <div class="card">
      {tag_chart}
    </div>

    <div class="card">
      <h2>タグ別PV変化率（直近{TAG_CHANGE_RATE_WINDOW_DAYS}日）</h2>
      <div class="table-wrapper">
        {tag_table}
      </div>
    </div>
"""
    if keyword_chart and keyword_table:
        extra_sections += f"""
    <div class="card">
      {keyword_chart}
    </div>

    <div class="card">
      <h2>キーワード別PV変化率（タイトル内・直近{TAG_CHANGE_RATE_WINDOW_DAYS}日）</h2>
      <div class="table-wrapper">
        {keyword_table}
      </div>
    </div>
"""

    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    data_script = (
        f'<script>\n'
        f'const ALL_DATA = {chart_data_json};\n'
        f'const CHART_COLORS = {json.dumps(CHART_COLORS)};\n'
        f'const TOP_N = {TOP_N};\n'
        f'const TITLE_MAX_LEN = {TITLE_MAX_LEN};\n'
        f'</script>'
    )
    filter_script = """<script>
(function () {
  var rsBtns = [
    {count: 1,  label: "1ヶ月", step: "month", stepmode: "backward"},
    {count: 3,  label: "3ヶ月", step: "month", stepmode: "backward"},
    {count: 6,  label: "6ヶ月", step: "month", stepmode: "backward"},
    {count: 1,  label: "1年",   step: "year",  stepmode: "backward"},
    {count: 2,  label: "2年",   step: "year",  stepmode: "backward"},
    {step: "all", label: "全期間"}
  ];

  function makeXaxis(dates) {
    return {
      type: "date",
      range: dates.length ? [dates[0], dates[dates.length - 1]] : undefined,
      rangeselector: {buttons: rsBtns, bgcolor: "#f0f0f0", activecolor: "#55C500"},
      rangeslider: {visible: true, thickness: 0.05}
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
      line: {color: "#55C500", width: 2},
      marker: {size: 6},
      hovertemplate: "%{x}<br>合計PV: %{y:,}<extra></extra>"
    };
    var isMobile = window.innerWidth <= 768;
    Plotly.react("total-pv-chart", [trace], {
      title: "全記事の合計PV推移",
      xaxis: makeXaxis(dates),
      yaxis: {title: {text: "累計PV数"}},
      hovermode: "x unified",
      template: "plotly_white",
      height: isMobile ? 300 : 450
    });
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
    Plotly.react("per-article-chart", traces, {
      title: "上位" + TOP_N + "記事のPV推移",
      xaxis: makeXaxis(allDates),
      yaxis: {title: {text: "累計PV数"}},
      hovermode: "closest",
      hoverlabel: {namelength: -1},
      template: "plotly_white",
      height: isMobile ? 380 : 550,
      margin: {t: 60, r: isMobile ? 20 : 160},
      showlegend: !isMobile,
      legend: {orientation: "v", x: 1.02, y: 1}
    });
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

  function updateRankingTable(data) {
    var ld = latestDate(data);
    var latest = data.filter(function (r) { return r.snapshot_date === ld; });
    latest.sort(function (a, b) { return (b.page_views || 0) - (a.page_views || 0); });
    document.getElementById("ranking-tbody").innerHTML = latest.map(function (r, i) {
      return "<tr>" +
        "<td>" + (i + 1) + "</td>" +
        "<td class='title'><a href='" + r.url + "' target='_blank'>" + r.title + "</a></td>" +
        "<td>" + r.created_at + "</td>" +
        "<td class='num'>" + (r.page_views || 0).toLocaleString("ja-JP") + "</td>" +
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

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(applyFilter, 200);
  });
}());
</script>"""

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
    .date-filter {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .date-filter label {{ display: flex; align-items: center; gap: 6px; font-size: 0.9rem; color: #555; }}
    .date-filter input[type="date"] {{ padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9rem; }}
    .date-filter button {{ padding: 6px 14px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 0.9rem; }}
    .date-filter button:hover {{ background: #e0e0e0; }}
    .quick-filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }}
    .quick-filter-btn {{ padding: 6px 14px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 0.9rem; }}
    .quick-filter-btn:hover {{ background: #e0e0e0; }}
    .quick-filter-btn.active {{ background: #55C500; color: #fff; border-color: #55C500; }}
    .table-wrapper {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
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
      .date-filter {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
      .date-filter input[type="date"] {{ width: 160px; }}
    }}
    @media (max-width: 480px) {{
      .container {{ padding: 8px; }}
      h1 {{ font-size: 1.2rem; }}
      .stat-card .value {{ font-size: 1.3rem; }}
      .quick-filter-btn, .date-filter button {{ padding: 8px 10px; }}
    }}
  </style>
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

    <div class="card">
      {total_chart}
    </div>

    <div class="card">
      {per_article_chart}
    </div>
    {extra_sections}
  </div>
  {data_script}
  {filter_script}
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

    ranking_table = build_ranking_table(latest_in_range)
    total_chart = build_total_pv_chart(df_filtered, start_date, end_date)
    per_article_chart = build_per_article_chart(df_filtered, latest_in_range, start_date, end_date)
    chart_data_json = build_chart_data_json(df_filtered)

    tag_chart = tag_table = keyword_chart = keyword_table = None
    tag_long_df = explode_tags(df_filtered)
    if not tag_long_df.empty:
        tag_groups = top_frequent_tags(tag_long_df, TOP_N)
        tag_daily = aggregate_group_daily_pv(tag_long_df)
        tag_chart = build_group_pv_chart(
            tag_daily, tag_groups, start_date, end_date, "タグ別PV推移", "tag-pv-chart"
        )
        tag_table = build_group_change_rate_table(tag_daily, tag_groups, TAG_CHANGE_RATE_WINDOW_DAYS)

        keyword_long_df = explode_keywords(df_filtered, tag_groups)
        keyword_daily = aggregate_group_daily_pv(keyword_long_df)
        keyword_chart = build_group_pv_chart(
            keyword_daily, tag_groups, start_date, end_date,
            "キーワード別PV推移（タイトル内）", "keyword-pv-chart",
        )
        keyword_table = build_group_change_rate_table(keyword_daily, tag_groups, TAG_CHANGE_RATE_WINDOW_DAYS)

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
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"レポートを生成しました: {REPORT_PATH}  （期間: {start_date} 〜 {end_date}）")


if __name__ == "__main__":
    main()
