"""
Qiita記事のPV・いいね・ストック数を取得してSQLiteに保存する。
毎日cronで実行することで時系列データを蓄積できる。
"""

import os
import sqlite3
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

QIITA_TOKEN = os.getenv("QIITA_TOKEN")
API_BASE = "https://qiita.com/api/v2"
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "qiita.db")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id          TEXT NOT NULL,
            title       TEXT NOT NULL,
            url         TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            page_views  INTEGER,
            likes       INTEGER,
            stocks      INTEGER,
            PRIMARY KEY (id, snapshot_date)
        )
    """)
    conn.commit()


def fetch_all_items() -> list[dict]:
    if not QIITA_TOKEN:
        raise RuntimeError("QIITA_TOKEN が設定されていません。.env ファイルを確認してください。")

    headers = {"Authorization": f"Bearer {QIITA_TOKEN}"}
    items = []
    page = 1

    while True:
        resp = requests.get(
            f"{API_BASE}/authenticated_user/items",
            headers=headers,
            params={"page": page, "per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return items


def save_snapshot(conn: sqlite3.Connection, items: list[dict], today: str) -> int:
    saved = 0
    for item in items:
        conn.execute(
            """
            INSERT OR REPLACE INTO snapshots
                (id, title, url, created_at, snapshot_date, page_views, likes, stocks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["title"],
                item["url"],
                item["created_at"][:10],
                today,
                item.get("page_views_count"),
                item.get("likes_count", 0),
                item.get("stocks_count", 0),
            ),
        )
        saved += 1
    conn.commit()
    return saved


def main() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    today = date.today().isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        print("Qiita APIから記事データを取得中...")
        items = fetch_all_items()
        count = save_snapshot(conn, items, today)
        print(f"{today}: {count} 件のスナップショットを保存しました。")


if __name__ == "__main__":
    main()
