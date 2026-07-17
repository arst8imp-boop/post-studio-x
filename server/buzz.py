"""buzz_posts テーブル参照ロジック。post_studio の『バズピックアップ』UIから読まれる。

収集は別系統（１/research_buzz_posts.py, ingest_manual_urls.py 等）の責務。
ここは読み取り専用で、最近の高エンゲージメントポストを返すだけ。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import psycopg

from _env import ENV_PATH  # noqa: F401

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def list_recent(
    hours: int = 4320,
    limit: int = 40,
    min_impressions: int = 10000,
    exclude_replies: bool = True,
    keyword: Optional[str] = None,
    post_type: str = "article_first",  # 'all' | 'article_first' | 'article_only' | 'tweet_only'
) -> list[dict]:
    """指定時間内のバズを **インプレッション数降順** で返す。

    - hours: 直近何時間以内（posted_at 基準）
    - limit: 最大件数
    - min_impressions: インプ下限（バズの定義）
    - exclude_replies: post_type='thread_reply' を除外（引用には向かない）
    - keyword: 本文 ILIKE '%kw%' フィルタ（任意）
    - post_type:
        * 'article_first': X記事(=article) を先に並べ、その下に通常を続ける（既定）
        * 'article_only' : X記事だけ
        * 'tweet_only'   : X記事以外（通常 + 長文）
        * 'all'          : 全部、ただ imp 降順
    """
    if not DATABASE_URL:
        return []

    where = ["posted_at IS NOT NULL",
             "posted_at >= NOW() - %s::interval",
             "impressions IS NOT NULL",
             "impressions >= %s"]
    params: list = [f"{int(hours)} hours", int(min_impressions)]
    if exclude_replies:
        where.append("(post_type IS NULL OR post_type != 'thread_reply')")
    if keyword:
        where.append("text ILIKE %s")
        params.append(f"%{keyword}%")
    if post_type == "article_only":
        where.append("post_type = 'article'")
    elif post_type == "tweet_only":
        where.append("(post_type IS NULL OR post_type != 'article')")
    # article_first / all は WHERE に追加なし

    # ソート: article_first だけ「X記事を先頭にブースト」
    if post_type == "article_first":
        order = ("CASE WHEN post_type = 'article' THEN 0 ELSE 1 END, "
                 "impressions DESC NULLS LAST, engagement_rate DESC NULLS LAST")
    else:
        order = "impressions DESC NULLS LAST, engagement_rate DESC NULLS LAST"

    sql = f"""
        SELECT tweet_id, text, likes, retweets, replies, impressions,
               engagement_rate, author_username, author_followers,
               url, posted_at, post_type, article_title
        FROM buzz_posts
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT %s
    """
    params.append(int(limit))

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [
                    {
                        "tweet_id": r[0],
                        "text": r[1] or "",
                        "likes": r[2] or 0,
                        "retweets": r[3] or 0,
                        "replies": r[4] or 0,
                        "impressions": r[5] or 0,
                        "engagement_rate": float(r[6]) if r[6] is not None else None,
                        "author_username": r[7] or "",
                        "author_followers": r[8] or 0,
                        "url": r[9] or "",
                        "posted_at": r[10].isoformat() if r[10] else None,
                        "post_type": r[11],
                        "article_title": r[12],
                    }
                    for r in rows
                ]
    except Exception as e:
        print(f"[buzz.list_recent] {type(e).__name__}: {e}")
        return []


def stats() -> dict:
    """UI上部に出す簡易サマリ用。期間ごとの件数とDB上の最新posted_atなど。"""
    if not DATABASE_URL:
        return {}
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                      COUNT(*),
                      COUNT(*) FILTER (WHERE posted_at >= NOW() - INTERVAL '24 hours'),
                      COUNT(*) FILTER (WHERE posted_at >= NOW() - INTERVAL '7 days'),
                      MAX(posted_at)
                    FROM buzz_posts
                """)
                total, last_24h, last_7d, latest = cur.fetchone()
                return {
                    "total": total or 0,
                    "last_24h": last_24h or 0,
                    "last_7d": last_7d or 0,
                    "latest_posted_at": latest.isoformat() if latest else None,
                }
    except Exception as e:
        print(f"[buzz.stats] {type(e).__name__}: {e}")
        return {}
