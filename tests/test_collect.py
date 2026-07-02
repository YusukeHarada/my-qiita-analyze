import pytest
import requests


class TestInitDb:
    def test_creates_snapshots_table(self, mem_conn):
        from collect import init_db
        init_db(mem_conn)
        result = mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
        ).fetchone()
        assert result is not None

    def test_idempotent(self, mem_conn):
        from collect import init_db
        init_db(mem_conn)
        init_db(mem_conn)

    def test_primary_key_columns(self, mem_conn):
        from collect import init_db
        init_db(mem_conn)
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        cols = mem_conn.execute("PRAGMA table_info(snapshots)").fetchall()
        pk_cols = {col[1] for col in cols if col[5] > 0}
        assert pk_cols == {"id", "snapshot_date"}

    def test_adds_tags_column(self, mem_conn):
        from collect import init_db
        init_db(mem_conn)
        cols = {row[1] for row in mem_conn.execute("PRAGMA table_info(snapshots)")}
        assert "tags" in cols

    def test_migrates_existing_table_without_tags_column(self, mem_conn):
        from collect import init_db
        mem_conn.execute("""
            CREATE TABLE snapshots (
                id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                page_views INTEGER,
                likes INTEGER,
                stocks INTEGER,
                PRIMARY KEY (id, snapshot_date)
            )
        """)
        mem_conn.commit()
        init_db(mem_conn)
        cols = {row[1] for row in mem_conn.execute("PRAGMA table_info(snapshots)")}
        assert "tags" in cols


class TestFetchAllItems:
    def test_single_page(self, requests_mock, qiita_token_env):
        from collect import fetch_all_items
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
        result = fetch_all_items()
        assert len(result) == 3
        assert result[0]["id"] == "art1"

    def test_multi_page(self, requests_mock, qiita_token_env):
        from collect import fetch_all_items
        page1 = [
            {"id": f"a{i:03d}", "title": f"Article{i}", "url": f"https://q.com/{i}",
             "created_at": "2024-01-01T00:00:00+09:00",
             "page_views_count": i, "likes_count": 0, "stocks_count": 0}
            for i in range(100)
        ]
        page2 = [
            {"id": f"b{i:03d}", "title": f"ArticleB{i}", "url": f"https://q.com/b{i}",
             "created_at": "2024-01-01T00:00:00+09:00",
             "page_views_count": i, "likes_count": 0, "stocks_count": 0}
            for i in range(3)
        ]
        requests_mock.get(
            "https://qiita.com/api/v2/authenticated_user/items",
            response_list=[
                {"json": page1, "status_code": 200},
                {"json": page2, "status_code": 200},
            ],
        )
        result = fetch_all_items()
        assert len(result) == 103

    def test_empty_response_on_first_page(self, requests_mock, qiita_token_env):
        from collect import fetch_all_items
        requests_mock.get(
            "https://qiita.com/api/v2/authenticated_user/items",
            json=[],
        )
        result = fetch_all_items()
        assert result == []

    def test_raises_without_token(self, monkeypatch):
        import collect
        monkeypatch.setattr(collect, "QIITA_TOKEN", None)
        with pytest.raises(RuntimeError, match="QIITA_TOKEN"):
            collect.fetch_all_items()

    def test_raises_on_http_error(self, requests_mock, qiita_token_env):
        from collect import fetch_all_items
        requests_mock.get(
            "https://qiita.com/api/v2/authenticated_user/items",
            status_code=401,
        )
        with pytest.raises(requests.HTTPError):
            fetch_all_items()

    def test_authorization_header_sent(self, requests_mock, qiita_token_env):
        from collect import fetch_all_items
        requests_mock.get(
            "https://qiita.com/api/v2/authenticated_user/items",
            json=[],
        )
        fetch_all_items()
        assert requests_mock.last_request.headers["Authorization"] == "Bearer test-token-12345"


class TestSaveSnapshot:
    def _item(self, **kwargs):
        base = {
            "id": "art001",
            "title": "記事A",
            "url": "https://q.com/a",
            "created_at": "2024-01-01T00:00:00+09:00",
            "page_views_count": 100,
            "likes_count": 5,
            "stocks_count": 2,
        }
        base.update(kwargs)
        return base

    def test_returns_saved_count(self, mem_conn_with_schema):
        from collect import save_snapshot
        items = [self._item(id=f"art{i:03d}") for i in range(3)]
        assert save_snapshot(mem_conn_with_schema, items, "2026-06-01") == 3

    def test_data_is_persisted(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(mem_conn_with_schema, [self._item()], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT id, page_views FROM snapshots").fetchone()
        assert row == ("art001", 100)

    def test_insert_or_replace(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(mem_conn_with_schema, [self._item(page_views_count=100)], "2026-06-01")
        save_snapshot(mem_conn_with_schema, [self._item(page_views_count=200)], "2026-06-01")
        rows = mem_conn_with_schema.execute("SELECT COUNT(*), page_views FROM snapshots").fetchone()
        assert rows == (1, 200)

    def test_created_at_truncated_to_date(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(mem_conn_with_schema, [self._item(created_at="2024-01-15T09:00:00+09:00")], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT created_at FROM snapshots").fetchone()
        assert row[0] == "2024-01-15"

    def test_page_views_none(self, mem_conn_with_schema):
        from collect import save_snapshot
        item = {"id": "art001", "title": "記事A", "url": "https://q.com/a",
                "created_at": "2024-01-01T00:00:00+09:00",
                "likes_count": 5, "stocks_count": 2}
        save_snapshot(mem_conn_with_schema, [item], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT page_views FROM snapshots").fetchone()
        assert row[0] is None

    def test_likes_defaults_to_zero(self, mem_conn_with_schema):
        from collect import save_snapshot
        item = {"id": "art001", "title": "記事A", "url": "https://q.com/a",
                "created_at": "2024-01-01T00:00:00+09:00",
                "page_views_count": 100, "stocks_count": 2}
        save_snapshot(mem_conn_with_schema, [item], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT likes FROM snapshots").fetchone()
        assert row[0] == 0

    def test_tags_saved_as_comma_joined_string(self, mem_conn_with_schema):
        from collect import save_snapshot
        item = self._item(tags=[{"name": "Python"}, {"name": "Docker"}])
        save_snapshot(mem_conn_with_schema, [item], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT tags FROM snapshots").fetchone()
        assert row[0] == "Python,Docker"

    def test_tags_empty_when_absent(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(mem_conn_with_schema, [self._item()], "2026-06-01")
        row = mem_conn_with_schema.execute("SELECT tags FROM snapshots").fetchone()
        assert row[0] == ""

    def test_tags_backfilled_to_past_snapshots_without_tags(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(mem_conn_with_schema, [self._item()], "2026-05-30")
        save_snapshot(
            mem_conn_with_schema,
            [self._item(tags=[{"name": "Python"}])],
            "2026-06-01",
        )
        rows = mem_conn_with_schema.execute(
            "SELECT snapshot_date, tags FROM snapshots ORDER BY snapshot_date"
        ).fetchall()
        assert rows == [
            ("2026-05-30", "Python"),
            ("2026-06-01", "Python"),
        ]

    def test_tags_backfill_does_not_overwrite_existing_tags(self, mem_conn_with_schema):
        from collect import save_snapshot
        save_snapshot(
            mem_conn_with_schema,
            [self._item(tags=[{"name": "Ruby"}])],
            "2026-05-30",
        )
        save_snapshot(
            mem_conn_with_schema,
            [self._item(tags=[{"name": "Python"}])],
            "2026-06-01",
        )
        row = mem_conn_with_schema.execute(
            "SELECT tags FROM snapshots WHERE snapshot_date = '2026-05-30'"
        ).fetchone()
        assert row[0] == "Ruby"
