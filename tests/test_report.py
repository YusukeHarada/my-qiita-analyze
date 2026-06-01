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
        assert "<tbody>" in html

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
        tbody_start = html.index("<tbody>")
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
        )
        defaults.update(kwargs)
        return generate_html(**defaults)

    def test_contains_doctype(self):
        assert self._call().startswith("<!DOCTYPE html>")

    def test_total_pv_formatted(self):
        assert "12,345" in self._call(total_pv=12345)

    def test_article_count_in_output(self):
        assert '<div class="value">42</div>' in self._call(article_count=42)

    def test_plotly_cdn_included(self):
        assert "cdn.plot.ly" in self._call()


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
