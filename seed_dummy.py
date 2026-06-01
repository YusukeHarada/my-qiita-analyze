"""
時系列グラフの見た目確認用ダミーデータ挿入スクリプト。
今日の実データをベースに、過去 N 日分のスナップショットを生成して DB に挿入する。

確認後は以下で削除できる:
  python seed_dummy.py --delete
"""

import argparse
import random
import sqlite3
from datetime import date, timedelta

DB_PATH = "data/qiita.db"
DAYS = 30  # 何日分遡るか
SEED = 42


def load_latest(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots)"
    ).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM snapshots LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def insert_dummy(conn: sqlite3.Connection, articles: list[dict], days: int) -> int:
    rng = random.Random(SEED)
    today = date.fromisoformat(articles[0]["snapshot_date"])
    inserted = 0

    for article in articles:
        pv_today = article["page_views"] or 0
        likes_today = article["likes"] or 0
        stocks_today = article["stocks"] or 0

        # 記事の投稿日からの経過日数（PV成長率の基準に使う）
        article_age = max((today - date.fromisoformat(article["created_at"])).days, 1)
        daily_pv_rate = pv_today / article_age

        for d in range(1, days + 1):
            target_date = (today - timedelta(days=d)).isoformat()

            # d 日前の累計 PV を推定（日次増加 + ランダムノイズ）
            noise = rng.uniform(0.85, 1.05)
            pv = max(0, int(pv_today - daily_pv_rate * d * noise))
            likes = max(0, int(likes_today * (pv / pv_today))) if pv_today > 0 else 0
            stocks = max(0, int(stocks_today * (pv / pv_today))) if pv_today > 0 else 0

            conn.execute(
                """
                INSERT OR IGNORE INTO snapshots
                    (id, title, url, created_at, snapshot_date, page_views, likes, stocks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["id"],
                    article["title"],
                    article["url"],
                    article["created_at"],
                    target_date,
                    pv,
                    likes,
                    stocks,
                ),
            )
            inserted += conn.execute("SELECT changes()").fetchone()[0]

    conn.commit()
    return inserted


def delete_dummy(conn: sqlite3.Connection, real_date: str) -> int:
    conn.execute("DELETE FROM snapshots WHERE snapshot_date != ?", (real_date,))
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true", help="ダミーデータを削除して実データだけ残す")
    args = parser.parse_args()

    with sqlite3.connect(DB_PATH) as conn:
        articles = load_latest(conn)
        if not articles:
            print("実データがありません。先に collect.py を実行してください。")
            return

        real_date = articles[0]["snapshot_date"]

        if args.delete:
            n = delete_dummy(conn, real_date)
            print(f"ダミーデータを {n} 件削除しました。実データ（{real_date}）のみ残っています。")
        else:
            n = insert_dummy(conn, articles, DAYS)
            print(f"過去 {DAYS} 日分のダミーデータを {n} 件挿入しました。")


if __name__ == "__main__":
    main()
