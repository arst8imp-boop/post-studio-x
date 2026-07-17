"""ブランド（Xアカウント）ごとのテーマ設定の保存・取得（Neon Postgres）。

UI の「🎯 テーマ設定」から編集され、生成時にシステムプロンプトへ注入される。
空欄の項目は注入されない（prompts.py の voice 既定値のまま動く）。
"""

import os
import re
import time
from typing import Optional

import psycopg

from _env import ENV_PATH  # noqa: F401

DATABASE_URL = os.environ.get("DATABASE_URL", "")

FIELDS = ("theme_areas", "target_audience", "extra_rules")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS brand_settings (
  brand TEXT PRIMARY KEY,
  theme_areas TEXT DEFAULT '',
  target_audience TEXT DEFAULT '',
  extra_rules TEXT DEFAULT '',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- 動的ブランド（新規アカウント）用の後付けカラム
ALTER TABLE brand_settings ADD COLUMN IF NOT EXISTS display_name TEXT DEFAULT '';
ALTER TABLE brand_settings ADD COLUMN IF NOT EXISTS is_custom BOOLEAN DEFAULT FALSE;
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
        print(f"[brand_settings.ensure_schema] {type(e).__name__}: {e}")


def get(brand: str) -> dict:
    empty = {f: "" for f in FIELDS}
    if not DATABASE_URL:
        return empty
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT theme_areas, target_audience, extra_rules "
                    "FROM brand_settings WHERE brand=%s",
                    (brand,),
                )
                r = cur.fetchone()
                if not r:
                    return empty
                return dict(zip(FIELDS, (v or "" for v in r)))
    except Exception as e:
        print(f"[brand_settings.get] {type(e).__name__}: {e}")
        return empty


def save(brand: str, theme_areas: str, target_audience: str, extra_rules: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO brand_settings (brand, theme_areas, target_audience, extra_rules, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (brand) DO UPDATE SET
                      theme_areas = EXCLUDED.theme_areas,
                      target_audience = EXCLUDED.target_audience,
                      extra_rules = EXCLUDED.extra_rules,
                      updated_at = NOW();
                """, (brand, theme_areas.strip(), target_audience.strip(), extra_rules.strip()))
            conn.commit()
            return True
    except Exception as e:
        print(f"[brand_settings.save] {type(e).__name__}: {e}")
        return False


def get_meta(brand: str) -> Optional[dict]:
    """ブランドのメタ情報。存在しなければ None。"""
    if not DATABASE_URL:
        return None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT display_name, is_custom FROM brand_settings WHERE brand=%s",
                    (brand,),
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {"display_name": r[0] or "", "is_custom": bool(r[1])}
    except Exception as e:
        print(f"[brand_settings.get_meta] {type(e).__name__}: {e}")
        return None


def list_custom() -> list[dict]:
    """新規作成されたカスタムブランドの一覧。"""
    if not DATABASE_URL:
        return []
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT brand, display_name FROM brand_settings "
                    "WHERE is_custom ORDER BY updated_at ASC"
                )
                return [{"brand": r[0], "display_name": r[1] or r[0]} for r in cur.fetchall()]
    except Exception as e:
        print(f"[brand_settings.list_custom] {type(e).__name__}: {e}")
        return []


def create_brand(display_name: str) -> Optional[str]:
    """新規カスタムブランドを作成してキーを返す。"""
    if not DATABASE_URL:
        return None
    display_name = display_name.strip()
    if not display_name:
        return None
    # キーは英数字のみ（URL・プロンプトで扱いやすくする）
    slug = re.sub(r"[^a-z0-9]", "", display_name.lower())[:12]
    key = f"c{int(time.time())}{('_' + slug) if slug else ''}"
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO brand_settings (brand, display_name, is_custom, updated_at)
                    VALUES (%s, %s, TRUE, NOW())
                """, (key, display_name))
            conn.commit()
            return key
    except Exception as e:
        print(f"[brand_settings.create_brand] {type(e).__name__}: {e}")
        return None


def rename(brand: str, display_name: str) -> bool:
    """カスタムブランドの表示名を変更。組み込みブランドや未知のキーは False。"""
    display_name = display_name.strip()
    if not DATABASE_URL or not display_name:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE brand_settings SET display_name=%s, updated_at=NOW() "
                    "WHERE brand=%s AND is_custom",
                    (display_name, brand),
                )
                ok = cur.rowcount > 0
            conn.commit()
            return ok
    except Exception as e:
        print(f"[brand_settings.rename] {type(e).__name__}: {e}")
        return False


def delete(brand: str) -> bool:
    """カスタムブランドを削除。組み込み（is_custom=false）や未知のキーは False。"""
    if not DATABASE_URL:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM brand_settings WHERE brand=%s AND is_custom",
                    (brand,),
                )
                ok = cur.rowcount > 0
            conn.commit()
            return ok
    except Exception as e:
        print(f"[brand_settings.delete] {type(e).__name__}: {e}")
        return False


def build_prompt_block(brand: str) -> str:
    """設定をシステムプロンプト末尾に足すブロックに整形。全項目空なら空文字。"""
    s = get(brand)
    lines: list[str] = []
    if s["theme_areas"]:
        lines.append(f"- 発信テーマ（この領域を軸に書く）: {s['theme_areas']}")
    if s["target_audience"]:
        lines.append(f"- ターゲット読者（この人に届くように書く）: {s['target_audience']}")
    if s["extra_rules"]:
        lines.append(f"- 追加の指示: {s['extra_rules']}")
    if not lines:
        return ""
    return (
        "【アカウントのテーマ設定（画面から設定・キャラ既定値より優先）】\n"
        + "\n".join(lines)
    )
