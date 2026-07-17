"""生成履歴の保存・取得（Neon Postgres）。"""

import json
import os
from pathlib import Path
from typing import Optional

import psycopg

from _env import ENV_PATH  # noqa: F401

DATABASE_URL = os.environ.get("DATABASE_URL", "")

USD_JPY_RATE = 150  # 円換算の概算表示用


def api_budget_jpy() -> Optional[float]:
    """API_MONTHLY_BUDGET_JPY の値。未設定なら None、設定済みなら float（0 = API課金を完全禁止）。"""
    raw = os.environ.get("API_MONTHLY_BUDGET_JPY")
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def api_budget_blocked() -> bool:
    """予算によって API 課金が禁止されている状態か。
    予算0円 → 常にブロック。予算超過 → ブロック。未設定 → ブロックしない。
    ただし画面のトグルで「API課金を許可」に切り替えている間はブロックしない。"""
    if api_charges_allowed():
        return False
    budget = api_budget_jpy()
    if budget is None:
        return False
    if budget <= 0:
        return True
    spent_jpy = usage_stats()["month"]["api"]["cost_usd"] * USD_JPY_RATE
    return spent_jpy >= budget


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """app_settings から1件取得。未設定・DBなしなら default。"""
    if not DATABASE_URL:
        return default
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,))
                r = cur.fetchone()
                return r[0] if r else default
    except Exception as e:
        print(f"[history.get_setting] {type(e).__name__}: {e}")
        return default


def set_setting(key: str, value: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW();
                """, (key, value))
            conn.commit()
            return True
    except Exception as e:
        print(f"[history.set_setting] {type(e).__name__}: {e}")
        return False


def api_charges_allowed() -> bool:
    """画面トグルで API 課金の手動許可がONか（既定OFF＝予算どおりブロック）。"""
    return get_setting("allow_api_charges", "0") == "1"


def set_api_charges_allowed(allowed: bool) -> bool:
    return set_setting("allow_api_charges", "1" if allowed else "0")


def get_hidden_builtins() -> list[str]:
    """UIから非表示にした組み込みブランドのキー一覧（このインストール固有）。"""
    raw = get_setting("hidden_builtins", "") or ""
    return [k for k in raw.split(",") if k]


def set_builtin_hidden(key: str, hidden: bool) -> bool:
    """組み込みブランドの表示/非表示を切り替え（コードは触らず表示状態だけ保存）。"""
    cur = set(get_hidden_builtins())
    if hidden:
        cur.add(key)
    else:
        cur.discard(key)
    return set_setting("hidden_builtins", ",".join(sorted(cur)))


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS post_studio_history (
  id SERIAL PRIMARY KEY,
  brand TEXT NOT NULL,
  type TEXT NOT NULL,
  theme TEXT NOT NULL,
  extra TEXT DEFAULT '',
  output TEXT NOT NULL,
  use_rag BOOLEAN DEFAULT TRUE,
  rag_refs INT DEFAULT 0,
  cover_path TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_psh_created_at ON post_studio_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_psh_brand ON post_studio_history(brand);
-- 使用量トラッキング用（後付けカラム。既存行はデフォルト値のまま）
ALTER TABLE post_studio_history ADD COLUMN IF NOT EXISTS auth TEXT DEFAULT '';
ALTER TABLE post_studio_history ADD COLUMN IF NOT EXISTS input_tokens INT DEFAULT 0;
ALTER TABLE post_studio_history ADD COLUMN IF NOT EXISTS output_tokens INT DEFAULT 0;
ALTER TABLE post_studio_history ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION DEFAULT 0;

-- 画面トグルなどの永続設定（key-value）
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT DEFAULT '',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def ensure_schema() -> None:
    if not DATABASE_URL:
        return
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()
    except Exception as e:
        print(f"[history.ensure_schema] {type(e).__name__}: {e}")


def delete_by_brand(brand: str) -> int:
    """指定ブランドの生成履歴をすべて削除。削除件数を返す。"""
    if not DATABASE_URL:
        return 0
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM post_studio_history WHERE brand=%s", (brand,))
                n = cur.rowcount
            conn.commit()
            return n
    except Exception as e:
        print(f"[history.delete_by_brand] {type(e).__name__}: {e}")
        return 0


def save(brand: str, type_: str, theme: str, extra: str,
         output: str, use_rag: bool, rag_refs: int,
         auth: str = "", input_tokens: int = 0, output_tokens: int = 0,
         cost_usd: float = 0.0) -> Optional[int]:
    if not DATABASE_URL or not output.strip():
        return None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO post_studio_history
                        (brand, type, theme, extra, output, use_rag, rag_refs,
                         auth, input_tokens, output_tokens, cost_usd)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (brand, type_, theme, extra, output, use_rag, rag_refs,
                      auth, input_tokens, output_tokens, cost_usd))
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as e:
        print(f"[history.save] {type(e).__name__}: {e}")
        return None


def usage_stats() -> dict:
    """今日・今月・全期間の使用量集計（JST基準）。auth別に本数/トークン/コストを返す。"""
    empty = {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    result = {
        "today": {"subscription": dict(empty), "api": dict(empty)},
        "month": {"subscription": dict(empty), "api": dict(empty)},
        "total": {"subscription": dict(empty), "api": dict(empty)},
    }
    if not DATABASE_URL:
        return result
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                      CASE WHEN (created_at AT TIME ZONE 'Asia/Tokyo')::date
                                = (NOW() AT TIME ZONE 'Asia/Tokyo')::date
                           THEN 1 ELSE 0 END AS is_today,
                      CASE WHEN date_trunc('month', created_at AT TIME ZONE 'Asia/Tokyo')
                                = date_trunc('month', NOW() AT TIME ZONE 'Asia/Tokyo')
                           THEN 1 ELSE 0 END AS is_month,
                      CASE WHEN auth = 'api' THEN 'api' ELSE 'subscription' END AS auth_kind,
                      COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
                    FROM post_studio_history
                    GROUP BY 1, 2, 3
                """)
                for is_today, is_month, auth_kind, n, tin, tout, cost in cur.fetchall():
                    row = {
                        "count": int(n or 0),
                        "input_tokens": int(tin or 0),
                        "output_tokens": int(tout or 0),
                        "cost_usd": float(cost or 0.0),
                    }
                    for period, flag in (("today", is_today), ("month", is_month), ("total", 1)):
                        if flag:
                            agg = result[period][auth_kind]
                            agg["count"] += row["count"]
                            agg["input_tokens"] += row["input_tokens"]
                            agg["output_tokens"] += row["output_tokens"]
                            agg["cost_usd"] += row["cost_usd"]
    except Exception as e:
        print(f"[history.usage_stats] {type(e).__name__}: {e}")
    return result


def set_cover_path(history_id: int, path: str) -> None:
    if not DATABASE_URL:
        return
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE post_studio_history SET cover_path=%s WHERE id=%s",
                    (path, history_id),
                )
            conn.commit()
    except Exception as e:
        print(f"[history.set_cover_path] {type(e).__name__}: {e}")


def list_recent(brand: Optional[str] = None, limit: int = 30) -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                if brand:
                    cur.execute("""
                        SELECT id, brand, type, theme, extra, use_rag, rag_refs,
                               cover_path, created_at, LEFT(output, 120) AS preview
                        FROM post_studio_history
                        WHERE brand=%s
                        ORDER BY created_at DESC LIMIT %s
                    """, (brand, limit))
                else:
                    cur.execute("""
                        SELECT id, brand, type, theme, extra, use_rag, rag_refs,
                               cover_path, created_at, LEFT(output, 120) AS preview
                        FROM post_studio_history
                        ORDER BY created_at DESC LIMIT %s
                    """, (limit,))
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0], "brand": r[1], "type": r[2],
                        "theme": r[3], "extra": r[4] or "",
                        "use_rag": r[5], "rag_refs": r[6],
                        "cover_path": r[7],
                        "created_at": r[8].isoformat() if r[8] else None,
                        "preview": r[9] or "",
                    }
                    for r in rows
                ]
    except Exception as e:
        print(f"[history.list_recent] {type(e).__name__}: {e}")
        return []


def count_today_by_type(type_: str) -> int:
    """JST基準で『今日（00:00〜23:59）』の指定タイプ生成件数を返す。
    type_ はカンマ区切りで複数指定可（例: 'reply_post,thread_reply'）。"""
    if not DATABASE_URL:
        return 0
    types = [t.strip() for t in type_.split(",") if t.strip()]
    if not types:
        return 0
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM post_studio_history
                    WHERE type = ANY(%s)
                      AND (created_at AT TIME ZONE 'Asia/Tokyo')::date
                          = (NOW() AT TIME ZONE 'Asia/Tokyo')::date
                """, (types,))
                r = cur.fetchone()
                return int(r[0]) if r else 0
    except Exception as e:
        print(f"[history.count_today_by_type] {type(e).__name__}: {e}")
        return 0


def get_one(history_id: int) -> Optional[dict]:
    if not DATABASE_URL:
        return None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, brand, type, theme, extra, output,
                           use_rag, rag_refs, cover_path, created_at
                    FROM post_studio_history WHERE id=%s
                """, (history_id,))
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    "id": r[0], "brand": r[1], "type": r[2],
                    "theme": r[3], "extra": r[4] or "",
                    "output": r[5],
                    "use_rag": r[6], "rag_refs": r[7],
                    "cover_path": r[8],
                    "created_at": r[9].isoformat() if r[9] else None,
                }
    except Exception as e:
        print(f"[history.get_one] {type(e).__name__}: {e}")
        return None


def delete(history_id: int) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM post_studio_history WHERE id=%s", (history_id,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    except Exception as e:
        print(f"[history.delete] {type(e).__name__}: {e}")
        return False
