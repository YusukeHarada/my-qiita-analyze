import sqlite3
import pytest
from datetime import date


@pytest.fixture
def single_day_conn():
    """1日分のデータのみを持つインメモリSQLite（insert_dummy用）。"""
    from collect import init_db, save_snapshot
    with sqlite3.connect(":memory:") as conn:
        init_db(conn)
        articles = [
            {
                "id": "art001",
                "title": "記事A",
                "url": "https://qiita.com/art001",
                "created_at": "2024-01-01T00:00:00+09:00",
                "page_views_count": 1000,
                "likes_count": 10,
                "stocks_count": 5,
            },
            {
                "id": "art002",
                "title": "記事B",
                "url": "https://qiita.com/art002",
                "created_at": "2024-02-01T00:00:00+09:00",
                "page_views_count": 500,
                "likes_count": 5,
                "stocks_count": 2,
            },
        ]
        save_snapshot(conn, articles, "2026-05-31")
        yield conn


@pytest.fixture
def seeded_conn_with_dummy(single_day_conn):
    from seed_dummy import load_latest, insert_dummy
    articles = load_latest(single_day_conn)
    insert_dummy(single_day_conn, articles, days=5)
    return single_day_conn


class TestLoadLatest:
    def test_returns_latest_snapshot(self, seeded_conn):
        from seed_dummy import load_latest
        articles = load_latest(seeded_conn)
        assert all(a["snapshot_date"] == "2026-05-31" for a in articles)

    def test_returns_empty_on_no_data(self, mem_conn_with_schema):
        from seed_dummy import load_latest
        assert load_latest(mem_conn_with_schema) == []

    def test_returns_dicts(self, seeded_conn):
        from seed_dummy import load_latest
        articles = load_latest(seeded_conn)
        assert all(isinstance(a, dict) for a in articles)


class TestInsertDummy:
    def test_inserts_correct_count(self, single_day_conn):
        from seed_dummy import load_latest, insert_dummy
        articles = load_latest(single_day_conn)
        count = insert_dummy(single_day_conn, articles, days=3)
        assert count == 6  # 2記事 × 3日

    def test_dates_are_past(self, single_day_conn):
        from seed_dummy import load_latest, insert_dummy
        articles = load_latest(single_day_conn)
        insert_dummy(single_day_conn, articles, days=5)
        rows = single_day_conn.execute(
            "SELECT snapshot_date FROM snapshots WHERE snapshot_date != '2026-05-31'"
        ).fetchall()
        today = date.today().isoformat()
        assert all(r[0] < today for r in rows)

    def test_ignore_duplicate(self, single_day_conn):
        from seed_dummy import load_latest, insert_dummy
        articles = load_latest(single_day_conn)
        count1 = insert_dummy(single_day_conn, articles, days=3)
        count2 = insert_dummy(single_day_conn, articles, days=3)
        assert count1 == 6
        assert count2 == 0  # INSERT OR IGNORE、すべて重複


class TestDeleteDummy:
    def test_deletes_non_real_dates(self, seeded_conn_with_dummy):
        from seed_dummy import delete_dummy
        delete_dummy(seeded_conn_with_dummy, "2026-05-31")
        dates = seeded_conn_with_dummy.execute(
            "SELECT DISTINCT snapshot_date FROM snapshots"
        ).fetchall()
        assert len(dates) == 1
        assert dates[0][0] == "2026-05-31"

    def test_real_date_preserved(self, seeded_conn_with_dummy):
        from seed_dummy import delete_dummy
        before = seeded_conn_with_dummy.execute(
            "SELECT COUNT(*) FROM snapshots WHERE snapshot_date = '2026-05-31'"
        ).fetchone()[0]
        delete_dummy(seeded_conn_with_dummy, "2026-05-31")
        after = seeded_conn_with_dummy.execute(
            "SELECT COUNT(*) FROM snapshots WHERE snapshot_date = '2026-05-31'"
        ).fetchone()[0]
        assert before == after

    def test_returns_deleted_count(self, seeded_conn_with_dummy):
        from seed_dummy import delete_dummy
        count = delete_dummy(seeded_conn_with_dummy, "2026-05-31")
        assert count == 10  # 2記事 × 5日のダミー
