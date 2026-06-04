import sqlite3
import pytest


@pytest.mark.integration
class TestCollectToReport:
    def test_collect_then_report_full_flow(self, tmp_path, monkeypatch, requests_mock):
        import collect
        import report

        db_path = tmp_path / "data" / "qiita.db"
        report_dir = tmp_path / "report"
        report_path = report_dir / "index.html"
        (tmp_path / "data").mkdir()
        report_dir.mkdir()

        monkeypatch.setattr(collect, "DB_PATH", str(db_path))
        monkeypatch.setattr(collect, "QIITA_TOKEN", "test-token")
        monkeypatch.setattr(report, "DB_PATH", str(db_path))
        monkeypatch.setattr(report, "REPORT_DIR", str(report_dir))
        monkeypatch.setattr(report, "REPORT_PATH", str(report_path))

        items = [
            {"id": f"art{i}", "title": f"Article{i}", "url": f"https://q.com/{i}",
             "created_at": "2024-01-01T00:00:00+09:00",
             "page_views_count": 100 * i, "likes_count": i, "stocks_count": i}
            for i in range(1, 4)
        ]
        requests_mock.get(
            "https://qiita.com/api/v2/authenticated_user/items",
            json=items,
        )

        with sqlite3.connect(str(db_path)) as conn:
            collect.init_db(conn)
            fetched = collect.fetch_all_items()
            count = collect.save_snapshot(conn, fetched, "2026-06-01")
        assert count == 3

        df = report.load_data()
        assert len(df) == 3

        latest_date = df["snapshot_date"].max()
        start_date, end_date = report.resolve_date_range(df, None, None)
        latest = df[df["snapshot_date"] == latest_date].copy()

        html = report.generate_html(
            report.build_ranking_table(latest),
            "<p>No history</p>",
            "<p>No history</p>",
            latest_date,
            int(latest["page_views"].sum()),
            len(latest),
            start_date,
            end_date,
            report.build_chart_data_json(df),
        )

        report_path.write_text(html, encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "Article1" in html

    def test_seed_and_report_with_history(self, tmp_path, monkeypatch):
        import report
        from collect import init_db, save_snapshot
        from seed_dummy import load_latest, insert_dummy

        db_path = tmp_path / "data" / "qiita.db"
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(report, "DB_PATH", str(db_path))

        with sqlite3.connect(str(db_path)) as conn:
            init_db(conn)
            save_snapshot(conn, [{
                "id": "art001", "title": "Article A", "url": "https://q.com/a",
                "created_at": "2024-01-01T00:00:00+09:00",
                "page_views_count": 1000, "likes_count": 10, "stocks_count": 5,
            }], "2026-06-01")
            articles = load_latest(conn)
            insert_dummy(conn, articles, days=10)

        df = report.load_data()
        assert df["snapshot_date"].nunique() > 1

        latest_date = df["snapshot_date"].max()
        start_date, end_date = report.resolve_date_range(df, None, None)
        df_filtered = df[(df["snapshot_date"] >= start_date) & (df["snapshot_date"] <= end_date)]
        latest = df_filtered[df_filtered["snapshot_date"] == latest_date].copy()

        total_chart = report.build_total_pv_chart(df_filtered, start_date, end_date)
        per_article_chart = report.build_per_article_chart(df_filtered, latest, start_date, end_date)

        assert "<div" in total_chart
        assert "<div" in per_article_chart
