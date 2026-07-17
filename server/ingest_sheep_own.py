"""ポスト（しーぷ）/ 配下の .txt をしーぷ自身のRAGデータとしてDBに投入。

- source_type='own_post_sheep'
- brand='sheep'
- 既存 tweet_id 相当の管理: filename を一意キーに

実行:
    python3 ingest_sheep_own.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from pgvector.psycopg import register_vector
from google import genai
from google.genai import types

ROOT = Path("/Applications/AI/X記事作成")
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 1536

gemini = genai.Client(api_key=GEMINI_API_KEY)


def embed(text: str) -> list[float]:
    for attempt in range(3):
        try:
            r = gemini.models.embed_content(
                model=EMBED_MODEL,
                contents=text,
                config=types.EmbedContentConfig(
                    output_dimensionality=EMBED_DIM,
                    task_type="RETRIEVAL_DOCUMENT",
                ),
            )
            return r.embeddings[0].values
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def classify_post_type(filename: str) -> str:
    """ファイル名から post_type を推測。"""
    for kw in ("短文", "長文", "引用", "リプ"):
        if kw in filename:
            return kw
    return "unknown"


def main() -> None:
    targets: list[Path] = []
    for sub in ("ポスト（しーぷ）", "X記事（しーぷ）"):
        d = ROOT / sub
        if d.exists():
            targets.extend(sorted(d.glob("*.txt")))

    if not targets:
        sys.exit("対象なし")

    print(f"対象 {len(targets)} ファイル")

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        # 既存チェック（source_file ベース）
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source_file FROM my_content
                WHERE source_type='own_post_sheep'
            """)
            existing = {r[0] for r in cur.fetchall()}

        new_count = skip_count = 0
        for p in targets:
            source_file = f"own_post_sheep:{p.name}"
            if source_file in existing:
                skip_count += 1
                continue
            text = p.read_text(encoding="utf-8").strip()
            if not text:
                continue
            try:
                emb = embed(text)
            except Exception as e:
                print(f"  ❌ embed失敗 {p.name}: {e}")
                continue

            metadata = {
                "filename": p.name,
                "post_type": classify_post_type(p.name),
                "char_count": len(text),
                "source_folder": p.parent.name,
            }
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO my_content
                        (source_type, source_file, chunk_index, content, embedding, metadata, brand)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_file, chunk_index) DO UPDATE SET
                        content=EXCLUDED.content,
                        embedding=EXCLUDED.embedding,
                        metadata=EXCLUDED.metadata,
                        brand=EXCLUDED.brand;
                """, ("own_post_sheep", source_file, 0, text, emb,
                      json.dumps(metadata, ensure_ascii=False), "sheep"))
            conn.commit()
            new_count += 1
            print(f"  ✅ {p.name}")
            time.sleep(0.3)

        print(f"\n📊 新規 {new_count} / スキップ {skip_count}")


if __name__ == "__main__":
    main()
