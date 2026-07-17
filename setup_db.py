"""post_studio のスキーマを 1コマンドで作るブートストラップ。

実行:
    .venv/bin/python3 setup_db.py
    .venv/bin/python3 setup_db.py --check   # 既存テーブルだけ確認、何も作らない

作るもの:
  - pgvector 拡張
  - my_content         （RAG コーパス：過去ポスト / レターテンプレ等）
  - buzz_posts         （X バズ収集の蓄積）
  - post_studio_history（生成履歴）
  - 必要なインデックス
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# .env を server/_env.py 経由で読みたいので、import パスを通す
sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))

from _env import ENV_PATH  # noqa: E402

import psycopg  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL", "")


SCHEMA_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector;

-- ===== my_content =========================================================
CREATE TABLE IF NOT EXISTS my_content (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,             -- 'x_post' / 'note_letter_template' 等
    source_file TEXT,                      -- 元ファイル名 / 識別子
    chunk_index INT NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    embedding   vector(1536),              -- Gemini text-embedding-001 / 1536次元
    brand       TEXT,                      -- 'sheep' / 'umai' / NULL（共通）
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_file, chunk_index)
);

CREATE INDEX IF NOT EXISTS my_content_embedding_idx
    ON my_content USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS my_content_source_type_idx
    ON my_content (source_type);
CREATE INDEX IF NOT EXISTS my_content_brand_idx
    ON my_content (brand) WHERE brand IS NOT NULL;

-- ===== buzz_posts =========================================================
CREATE TABLE IF NOT EXISTS buzz_posts (
    id                SERIAL PRIMARY KEY,
    tweet_id          TEXT UNIQUE NOT NULL,
    text              TEXT NOT NULL,
    likes             INT,
    retweets          INT,
    replies           INT,
    impressions       INT,
    engagement_rate   FLOAT,
    author_username   TEXT,
    author_followers  INT,
    keyword           TEXT,
    url               TEXT,
    embedding         vector(1536),
    created_at        TIMESTAMP DEFAULT NOW(),
    collected_at      TIMESTAMP,
    posted_at         TIMESTAMP,
    brand             TEXT,
    conversation_id   TEXT,
    post_type         TEXT,                -- 'tweet' / 'long_tweet' / 'article' / 'thread_reply'
    article_title     TEXT
);

CREATE INDEX IF NOT EXISTS buzz_posts_embedding_idx
    ON buzz_posts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS buzz_posts_posted_at_idx
    ON buzz_posts (posted_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS buzz_posts_impressions_idx
    ON buzz_posts (impressions DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS buzz_posts_post_type_idx
    ON buzz_posts (post_type);

-- ===== post_studio_history ===============================================
CREATE TABLE IF NOT EXISTS post_studio_history (
    id          SERIAL PRIMARY KEY,
    brand       TEXT NOT NULL,
    type        TEXT NOT NULL,
    theme       TEXT NOT NULL,
    extra       TEXT DEFAULT '',
    output      TEXT NOT NULL,
    use_rag     BOOLEAN DEFAULT TRUE,
    rag_refs    INT DEFAULT 0,
    cover_path  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_psh_created_at ON post_studio_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_psh_brand      ON post_studio_history(brand);
CREATE INDEX IF NOT EXISTS idx_psh_type       ON post_studio_history(type);
"""


def show_tables(cur) -> None:
    cur.execute("""
        SELECT t.table_name,
               (SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = t.table_name) AS cols
        FROM information_schema.tables t
        WHERE table_schema = 'public'
          AND table_name IN ('my_content', 'buzz_posts', 'post_studio_history')
        ORDER BY t.table_name;
    """)
    rows = cur.fetchall()
    if not rows:
        print("  （該当テーブルなし）")
    for name, cols in rows:
        cur.execute(f"SELECT COUNT(*) FROM {name};")
        n = cur.fetchone()[0]
        print(f"  {name:<22} {cols:>2} 列 / {n:>6,} 行")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="既存テーブルを表示するだけで作成はしない")
    args = ap.parse_args()

    if not DATABASE_URL:
        sys.exit(
            "ERROR: DATABASE_URL 未設定。\n"
            f"  確認した .env: {ENV_PATH}\n"
            "  .env に DATABASE_URL=postgres://... を追加してください。"
        )

    print(f"=== post_studio setup_db ===")
    print(f"env: {ENV_PATH}")
    # ホスト名だけログ（パスワードを出さない）
    safe = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "(local)"
    print(f"DB:  {safe}")

    with psycopg.connect(DATABASE_URL, connect_timeout=8) as conn:
        with conn.cursor() as cur:
            if args.check:
                print("\n[CHECK] 既存テーブル:")
                show_tables(cur)
                return
            print("\nCREATE EXTENSION + テーブル + インデックスを実行中…")
            cur.execute(SCHEMA_SQL)
            conn.commit()
            print("OK\n")
            print("現状:")
            show_tables(cur)
    print("\n完了。次のステップ: ./start.sh で起動。")


if __name__ == "__main__":
    main()
