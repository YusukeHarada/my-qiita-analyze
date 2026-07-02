import sqlite3
import pytest
import pandas as pd


@pytest.fixture
def mem_conn():
    with sqlite3.connect(":memory:") as conn:
        yield conn


@pytest.fixture
def mem_conn_with_schema():
    from collect import init_db
    with sqlite3.connect(":memory:") as conn:
        init_db(conn)
        yield conn


@pytest.fixture
def seeded_conn():
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
                "tags": [{"name": "Python"}, {"name": "Docker"}],
            },
            {
                "id": "art002",
                "title": "記事B",
                "url": "https://qiita.com/art002",
                "created_at": "2024-02-01T00:00:00+09:00",
                "page_views_count": 500,
                "likes_count": 5,
                "stocks_count": 2,
                "tags": [{"name": "Python"}],
            },
        ]
        save_snapshot(conn, articles, "2026-05-30")
        save_snapshot(conn, articles, "2026-05-31")
        yield conn


@pytest.fixture
def sample_latest_df():
    return pd.DataFrame([
        {
            "id": "art001",
            "title": "記事A",
            "url": "https://qiita.com/art001",
            "created_at": "2024-01-01",
            "snapshot_date": "2026-05-31",
            "page_views": 1000,
            "likes": 10,
            "stocks": 5,
        },
        {
            "id": "art002",
            "title": "記事B",
            "url": "https://qiita.com/art002",
            "created_at": "2024-02-01",
            "snapshot_date": "2026-05-31",
            "page_views": 500,
            "likes": 5,
            "stocks": 2,
        },
    ])


@pytest.fixture
def sample_df(sample_latest_df):
    day0 = sample_latest_df.copy()
    day0["snapshot_date"] = "2026-05-30"
    day0["page_views"] = [900, 450]
    return pd.concat([day0, sample_latest_df], ignore_index=True)


@pytest.fixture
def sample_df_with_tags(sample_df):
    df = sample_df.copy()
    df["tags"] = df["id"].map({"art001": "Python,Docker", "art002": "Python"})
    return df


@pytest.fixture
def qiita_token_env(monkeypatch):
    monkeypatch.setenv("QIITA_TOKEN", "test-token-12345")
    import collect
    monkeypatch.setattr(collect, "QIITA_TOKEN", "test-token-12345")
    yield "test-token-12345"
