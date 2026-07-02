import pytest
import pandas as pd
from datetime import date, timedelta


class TestResolveDataRange:
    def test_both_none_defaults_to_2_years(self):
        from report import resolve_date_range
        df = pd.DataFrame({"snapshot_date": ["2026-06-01"]})
        start, end = resolve_date_range(df, None, None)
        assert end == "2026-06-01"
        assert start == (date(2026, 6, 1) - timedelta(days=730)).isoformat()

    def test_start_specified(self):
        from report import resolve_date_range
        df = pd.DataFrame({"snapshot_date": ["2026-06-01"]})
        start, end = resolve_date_range(df, "2025-01-01", None)
        assert start == "2025-01-01"

    def test_end_specified(self):
        from report import resolve_date_range
        df = pd.DataFrame({"snapshot_date": ["2026-06-01"]})
        start, end = resolve_date_range(df, None, "2025-12-31")
        assert end == "2025-12-31"

    def test_both_specified(self):
        from report import resolve_date_range
        df = pd.DataFrame({"snapshot_date": ["2026-06-01"]})
        start, end = resolve_date_range(df, "2025-01-01", "2025-12-31")
        assert start == "2025-01-01"
        assert end == "2025-12-31"

    def test_start_none_uses_2_years_before_end(self):
        from report import resolve_date_range
        df = pd.DataFrame({"snapshot_date": ["2026-06-01"]})
        end_str = "2025-06-01"
        start, end = resolve_date_range(df, None, end_str)
        assert end == end_str
        assert start == (date.fromisoformat(end_str) - timedelta(days=730)).isoformat()


class TestBuildRankingTable:
    def test_returns_html_table(self, sample_latest_df):
        from report import build_ranking_table
        html = build_ranking_table(sample_latest_df)
        assert "<table>" in html
        assert "<thead>" in html
        assert 'id="ranking-tbody"' in html

    def test_sorted_by_page_views_desc(self, sample_latest_df):
        from report import build_ranking_table
        html = build_ranking_table(sample_latest_df)
        # 記事A（1000PV）が記事B（500PV）より前に来る
        assert html.index("記事A") < html.index("記事B")

    def test_url_is_linked(self, sample_latest_df):
        from report import build_ranking_table
        html = build_ranking_table(sample_latest_df)
        assert 'href="https://qiita.com/art001"' in html

    def test_numbers_formatted_with_comma(self):
        from report import build_ranking_table
        df = pd.DataFrame([{
            "id": "art001", "title": "記事A", "url": "https://q.com/a",
            "created_at": "2024-01-01", "snapshot_date": "2026-06-01",
            "page_views": 1000, "likes": 0, "stocks": 0,
        }])
        assert "1,000" in build_ranking_table(df)

    def test_rank_starts_at_1(self, sample_latest_df):
        from report import build_ranking_table
        html = build_ranking_table(sample_latest_df)
        tbody_start = html.index('<tbody id="ranking-tbody">')
        first_td = html.index("<td>", tbody_start)
        first_td_end = html.index("</td>", first_td)
        assert html[first_td + len("<td>"):first_td_end] == "1"


class TestBuildTotalPvChart:
    def test_returns_html_string(self, sample_df):
        from report import build_total_pv_chart
        html = build_total_pv_chart(sample_df, "2026-05-30", "2026-05-31")
        assert "<div" in html

    def test_empty_df_returns_gracefully(self):
        from report import build_total_pv_chart
        df = pd.DataFrame(columns=["snapshot_date", "page_views"])
        html = build_total_pv_chart(df, "2024-01-01", "2026-06-01")
        assert isinstance(html, str) and len(html) > 0


class TestBuildPerArticleChart:
    def test_returns_html_string(self, sample_df, sample_latest_df):
        from report import build_per_article_chart
        html = build_per_article_chart(sample_df, sample_latest_df, "2026-05-30", "2026-05-31")
        assert "<div" in html

    def test_limits_to_top_n(self):
        from report import build_per_article_chart, TOP_N
        n = TOP_N + 5  # 15記事
        latest = pd.DataFrame([
            {"id": f"art{i:02d}", "title": f"Article{i:02d}", "url": f"https://q.com/{i}",
             "created_at": "2024-01-01", "snapshot_date": "2026-06-01",
             "page_views": 1000 - i * 50, "likes": 10, "stocks": 5}
            for i in range(n)
        ])
        html = build_per_article_chart(latest.copy(), latest, "2026-01-01", "2026-06-01")
        # art09（PV=550, rank 10）はトップ10に含まれる
        assert "Article09" in html
        # art10（PV=500, rank 11）はトップ10に含まれない
        assert "Article10" not in html

    def test_title_truncated_at_30_chars(self):
        from report import build_per_article_chart
        long_title = "A" * 35
        latest = pd.DataFrame([{
            "id": "art001", "title": long_title, "url": "https://q.com/a",
            "created_at": "2024-01-01", "snapshot_date": "2026-06-01",
            "page_views": 1000, "likes": 10, "stocks": 5,
        }])
        html = build_per_article_chart(latest.copy(), latest, "2026-01-01", "2026-06-01")
        # トレース名に省略記号が含まれる（リテラルまたはJSONエスケープ）
        assert "\\u2026" in html or "…" in html


class TestBuildChartDataJson:
    def test_returns_json_string(self, sample_df):
        from report import build_chart_data_json
        result = build_chart_data_json(sample_df)
        import json
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_contains_required_columns(self, sample_df):
        from report import build_chart_data_json
        import json
        data = json.loads(build_chart_data_json(sample_df))
        for col in ["id", "title", "url", "created_at", "snapshot_date", "page_views", "likes", "stocks"]:
            assert col in data[0]

    def test_null_numeric_values_filled_with_zero(self):
        from report import build_chart_data_json
        import json
        import numpy as np
        df = pd.DataFrame([{
            "id": "art001", "title": "記事A", "url": "https://q.com/a",
            "created_at": "2024-01-01", "snapshot_date": "2026-06-01",
            "page_views": None, "likes": np.nan, "stocks": None,
        }])
        data = json.loads(build_chart_data_json(df))
        assert data[0]["page_views"] == 0
        assert data[0]["likes"] == 0
        assert data[0]["stocks"] == 0


class TestGenerateHtml:
    def _call(self, **kwargs):
        from report import generate_html
        defaults = dict(
            ranking_table="<table></table>",
            total_chart="<div>chart</div>",
            per_article_chart="<div>chart</div>",
            latest_date="2026-06-01",
            total_pv=12345,
            article_count=42,
            start_date="2024-06-01",
            end_date="2026-06-01",
            chart_data_json="[]",
        )
        defaults.update(kwargs)
        return generate_html(**defaults)

    def test_contains_doctype(self):
        assert self._call().startswith("<!DOCTYPE html>")

    def test_total_pv_formatted(self):
        assert "12,345" in self._call(total_pv=12345)

    def test_article_count_in_output(self):
        assert 'id="stat-article-count"' in self._call()
        assert ">42<" in self._call(article_count=42)

    def test_plotly_cdn_included(self):
        assert "cdn.plot.ly" in self._call()

    def test_filter_ui_elements_present(self):
        html = self._call()
        assert 'id="articleStart"' in html
        assert 'id="articleEnd"' in html
        assert 'id="clearFilter"' in html

    def test_quick_filter_buttons_present(self):
        html = self._call()
        assert 'data-months="1"' in html
        assert 'data-months="3"' in html
        assert 'data-months="6"' in html
        assert 'data-months="12"' in html
        assert '1ヶ月以内' in html
        assert '3ヶ月以内' in html
        assert '半年以内' in html
        assert '1年以内' in html

    def test_chart_data_embedded(self):
        html = self._call(chart_data_json='[{"id":"art001"}]')
        assert 'ALL_DATA' in html
        assert '"id":"art001"' in html or 'id":"art001"' in html


class TestExplodeTags:
    def test_splits_comma_separated_tags_into_rows(self, sample_df_with_tags):
        from report import explode_tags
        long_df = explode_tags(sample_df_with_tags)
        art001_groups = set(long_df[long_df["id"] == "art001"]["group"])
        assert art001_groups == {"Python", "Docker"}

    def test_excludes_rows_without_tags(self, sample_df_with_tags):
        from report import explode_tags
        df = sample_df_with_tags.copy()
        df.loc[df["id"] == "art002", "tags"] = None
        long_df = explode_tags(df)
        assert "art002" not in set(long_df["id"])

    def test_missing_tags_column_returns_empty(self, sample_df):
        from report import explode_tags
        long_df = explode_tags(sample_df)
        assert long_df.empty

    def test_empty_df_returns_empty(self):
        from report import explode_tags
        import pandas as pd
        assert explode_tags(pd.DataFrame()).empty


class TestSelectTopGroupsByChangeRate:
    def test_sorted_by_absolute_pv_increase_not_percentage(self):
        from report import select_top_groups_by_change_rate
        import pandas as pd
        daily = pd.DataFrame([
            # A: 10 -> 15 (+5, +50%)
            {"group": "A", "snapshot_date": "2026-05-30", "page_views": 10},
            {"group": "A", "snapshot_date": "2026-05-31", "page_views": 15},
            # B: 1000 -> 1100 (+100, +10%)
            {"group": "B", "snapshot_date": "2026-05-30", "page_views": 1000},
            {"group": "B", "snapshot_date": "2026-05-31", "page_views": 1100},
        ])
        groups = select_top_groups_by_change_rate(daily, window_days=1, n=2)
        # Bの方が%は低いが絶対増加数(+100)がAの絶対増加数(+5)より大きいため上位に来る
        assert groups == ["B", "A"]

    def test_excludes_groups_without_prior_baseline(self):
        from report import select_top_groups_by_change_rate
        import pandas as pd
        daily = pd.DataFrame([
            {"group": "OnlyToday", "snapshot_date": "2026-05-31", "page_views": 1000},
            {"group": "HasHistory", "snapshot_date": "2026-05-30", "page_views": 100},
            {"group": "HasHistory", "snapshot_date": "2026-05-31", "page_views": 110},
        ])
        groups = select_top_groups_by_change_rate(daily, window_days=1, n=5)
        assert groups == ["HasHistory"]

    def test_new_group_ranked_by_current_volume(self):
        from report import select_top_groups_by_change_rate
        import pandas as pd
        daily = pd.DataFrame([
            # Steady: 100 -> 105 (+5)
            {"group": "Steady", "snapshot_date": "2026-05-30", "page_views": 100},
            {"group": "Steady", "snapshot_date": "2026-05-31", "page_views": 105},
            # New: 0 -> 50 (+50、新規記事などで前期間PVが0だったケース)
            {"group": "New", "snapshot_date": "2026-05-30", "page_views": 0},
            {"group": "New", "snapshot_date": "2026-05-31", "page_views": 50},
        ])
        groups = select_top_groups_by_change_rate(daily, window_days=1, n=2)
        assert groups[0] == "New"

    def test_limits_to_n(self, sample_df_with_tags):
        from report import explode_tags, aggregate_group_daily_pv, select_top_groups_by_change_rate
        long_df = explode_tags(sample_df_with_tags)
        daily = aggregate_group_daily_pv(long_df)
        groups = select_top_groups_by_change_rate(daily, window_days=1, n=1)
        assert len(groups) == 1

    def test_empty_input_returns_empty_list(self):
        from report import select_top_groups_by_change_rate
        import pandas as pd
        assert select_top_groups_by_change_rate(pd.DataFrame(columns=["group", "snapshot_date", "page_views"])) == []


class TestExplodeKeywords:
    def test_matches_title_case_insensitively(self, sample_df_with_tags):
        from report import explode_keywords
        df = sample_df_with_tags.copy()
        df.loc[df["id"] == "art001", "title"] = "python入門"
        long_df = explode_keywords(df, ["Python"])
        assert "art001" in set(long_df[long_df["group"] == "Python"]["id"])

    def test_no_match_excludes_group(self, sample_df_with_tags):
        from report import explode_keywords
        long_df = explode_keywords(sample_df_with_tags, ["Rust"])
        assert long_df.empty

    def test_empty_keywords_returns_empty(self, sample_df_with_tags):
        from report import explode_keywords
        assert explode_keywords(sample_df_with_tags, []).empty


class TestAggregateGroupDailyPv:
    def test_sums_page_views_per_group_and_date(self, sample_df_with_tags):
        from report import explode_tags, aggregate_group_daily_pv
        long_df = explode_tags(sample_df_with_tags)
        daily = aggregate_group_daily_pv(long_df)
        python_latest = daily[(daily["group"] == "Python") & (daily["snapshot_date"] == "2026-05-31")]
        # art001(1000) + art002(500)
        assert python_latest["page_views"].iloc[0] == 1500

    def test_empty_input_returns_empty(self):
        from report import aggregate_group_daily_pv
        import pandas as pd
        assert aggregate_group_daily_pv(pd.DataFrame(columns=["group", "snapshot_date", "page_views"])).empty


class TestBuildGroupPvChart:
    def test_returns_html_string(self, sample_df_with_tags):
        from report import explode_tags, aggregate_group_daily_pv, build_group_pv_chart, select_top_groups_by_change_rate
        long_df = explode_tags(sample_df_with_tags)
        daily = aggregate_group_daily_pv(long_df)
        groups = select_top_groups_by_change_rate(daily, window_days=1)
        html = build_group_pv_chart(daily, groups, "2026-05-30", "2026-05-31", "タグ別PV推移", "tag-pv-chart")
        assert "<div" in html
        assert 'id="tag-pv-chart"' in html


class TestBuildGroupChangeRateTable:
    def test_computes_percentage_change(self, sample_df_with_tags):
        from report import explode_tags, aggregate_group_daily_pv, build_group_change_rate_table
        long_df = explode_tags(sample_df_with_tags)
        daily = aggregate_group_daily_pv(long_df)
        # Python: 900+450=1350 -> 1000+500=1500, (1500-1350)/1350*100 = +11.1%
        html = build_group_change_rate_table(daily, ["Python"], window_days=1)
        assert "+11.1%" in html

    def test_shows_absolute_pv_increase_column(self, sample_df_with_tags):
        from report import explode_tags, aggregate_group_daily_pv, build_group_change_rate_table
        long_df = explode_tags(sample_df_with_tags)
        daily = aggregate_group_daily_pv(long_df)
        html = build_group_change_rate_table(daily, ["Python"], window_days=1)
        assert "増減PV" in html
        assert "+150" in html

    def test_sorted_descending_by_absolute_pv_increase_not_rate(self):
        from report import build_group_change_rate_table
        import pandas as pd
        daily = pd.DataFrame([
            # A: 10 -> 15 (+5, +50%)
            {"group": "A", "snapshot_date": "2026-05-30", "page_views": 10},
            {"group": "A", "snapshot_date": "2026-05-31", "page_views": 15},
            # B: 1000 -> 1100 (+100, +10%)
            {"group": "B", "snapshot_date": "2026-05-30", "page_views": 1000},
            {"group": "B", "snapshot_date": "2026-05-31", "page_views": 1100},
        ])
        html = build_group_change_rate_table(daily, ["A", "B"], window_days=1)
        # Aの方が変化率(%)は高いが、増減PVはBの方が大きいためBが上位に来る
        assert html.index(">B<") < html.index(">A<")

    def test_zero_previous_pv_shown_as_new(self):
        from report import build_group_change_rate_table
        import pandas as pd
        daily = pd.DataFrame([
            {"group": "A", "snapshot_date": "2026-05-30", "page_views": 0},
            {"group": "A", "snapshot_date": "2026-05-31", "page_views": 50},
        ])
        html = build_group_change_rate_table(daily, ["A"], window_days=1)
        assert "新規" in html

    def test_no_baseline_shows_dash_for_increase(self):
        from report import build_group_change_rate_table
        import pandas as pd
        daily = pd.DataFrame([
            {"group": "A", "snapshot_date": "2026-05-31", "page_views": 100},
        ])
        html = build_group_change_rate_table(daily, ["A"], window_days=7)
        assert "データ不足" in html

    def test_empty_groups_returns_placeholder(self):
        from report import build_group_change_rate_table
        import pandas as pd
        html = build_group_change_rate_table(pd.DataFrame(), [])
        assert "データがありません" in html


class TestLoadData:
    def test_raises_file_not_found(self, monkeypatch, tmp_path):
        import report
        monkeypatch.setattr(report, "DB_PATH", str(tmp_path / "nonexistent.db"))
        with pytest.raises(FileNotFoundError):
            report.load_data()

    def test_returns_dataframe(self, monkeypatch, tmp_path):
        import sqlite3
        import report
        from collect import init_db, save_snapshot

        db_path = tmp_path / "test.db"
        with sqlite3.connect(str(db_path)) as conn:
            init_db(conn)
            save_snapshot(conn, [{
                "id": "art001", "title": "記事A", "url": "https://q.com/a",
                "created_at": "2024-01-01T00:00:00+09:00",
                "page_views_count": 100, "likes_count": 5, "stocks_count": 2,
            }], "2026-06-01")

        monkeypatch.setattr(report, "DB_PATH", str(db_path))
        df = report.load_data()
        assert not df.empty
        assert "snapshot_date" in df.columns
        assert "page_views" in df.columns
