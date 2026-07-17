"""ブランド別の類似ポスト検索（過去ポストの voice を参考としてプロンプトに注入するため）。

sheep: own_post_sheep ソースのしーぷ自身ポストから類似 top-k（voice 参考）
       + buzz_posts テーブルから類似 top-k（型・フック参考）
umai:  brand IS NULL の馬井自身コンテンツ + brand='umai' の peer posts から類似 top-k
"""

import os
import time
from pathlib import Path

from google import genai
from google.genai import types
import psycopg
from pgvector.psycopg import register_vector

from _env import ENV_PATH  # noqa: F401 (import 副作用で .env をロード)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 1536

_gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def embed_query(query: str) -> list[float] | None:
    if not _gemini:
        return None
    for attempt in range(3):
        try:
            r = _gemini.models.embed_content(
                model=EMBED_MODEL,
                contents=query,
                config=types.EmbedContentConfig(
                    output_dimensionality=EMBED_DIM,
                    task_type="RETRIEVAL_QUERY",
                ),
            )
            return r.embeddings[0].values
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)
    return None


def search_similar(brand: str, theme: str, k: int = 4) -> list[dict]:
    """類似ポストを取得。失敗時は空配列を返す（生成は止めない）。"""
    if not (DATABASE_URL and GEMINI_API_KEY):
        return []
    qv = embed_query(theme)
    if qv is None:
        return []

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                if brand == "sheep":
                    cur.execute("""
                        SELECT source_type, source_file, content, metadata,
                               1 - (embedding <=> %s::vector) AS sim
                        FROM my_content
                        WHERE brand = 'sheep' AND source_type = 'own_post_sheep'
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s;
                    """, (qv, qv, k))
                elif brand == "taro":
                    cur.execute("""
                        SELECT source_type, source_file, content, metadata,
                               1 - (embedding <=> %s::vector) AS sim
                        FROM my_content
                        WHERE brand = 'taro' AND source_type = 'own_post_taro'
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s;
                    """, (qv, qv, k))
                elif brand == "umai":
                    # 馬井自身のコンテンツ（brand IS NULL）優先
                    cur.execute("""
                        SELECT source_type, source_file, content, metadata,
                               1 - (embedding <=> %s::vector) AS sim
                        FROM my_content
                        WHERE brand IS NULL
                          AND source_type IN ('x_post', 'my_content', 'youtube_script')
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s;
                    """, (qv, qv, k))
                else:
                    return []
                rows = cur.fetchall()
                return [
                    {
                        "source_type": r[0],
                        "source_file": r[1],
                        "content": r[2],
                        "metadata": r[3],
                        "similarity": float(r[4]),
                    }
                    for r in rows
                ]
    except Exception as e:
        print(f"[rag.search_similar] error: {type(e).__name__}: {e}")
        return []


def search_buzz_posts(theme: str, k: int = 4) -> list[dict]:
    """buzz_posts テーブルから類似 top-k を取得（型・フック参考用）。失敗時は空配列。"""
    if not (DATABASE_URL and GEMINI_API_KEY):
        return []
    qv = embed_query(theme)
    if qv is None:
        return []
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT author_username, author_followers, text, likes, impressions, url,
                           1 - (embedding <=> %s::vector) AS sim
                    FROM buzz_posts
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (qv, qv, k))
                rows = cur.fetchall()
                return [
                    {
                        "author_username": r[0],
                        "author_followers": r[1] or 0,
                        "content": r[2],
                        "likes": r[3] or 0,
                        "impressions": r[4] or 0,
                        "url": r[5],
                        "similarity": float(r[6]),
                    }
                    for r in rows
                ]
    except Exception as e:
        print(f"[rag.search_buzz_posts] error: {type(e).__name__}: {e}")
        return []


def search_letter_templates(theme: str, k: int = 3) -> list[dict]:
    """note記事の無料部分（レター）テンプレを類似検索。
    source_type='note_letter_template' の chunk を上位 k 件返す。失敗時は空配列。
    """
    if not (DATABASE_URL and GEMINI_API_KEY):
        return []
    qv = embed_query(theme)
    if qv is None:
        return []
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=4) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT source_file, content, metadata,
                           1 - (embedding <=> %s::vector) AS sim
                    FROM my_content
                    WHERE source_type = 'note_letter_template'
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (qv, qv, k))
                rows = cur.fetchall()
                return [
                    {
                        "source_file": r[0],
                        "content": r[1],
                        "metadata": r[2],
                        "similarity": float(r[3]),
                    }
                    for r in rows
                ]
    except Exception as e:
        print(f"[rag.search_letter_templates] error: {type(e).__name__}: {e}")
        return []


def build_letter_context(theme: str, k: int = 3, max_chars_each: int = 1200) -> str:
    """有料note の『無料部分（レター）』生成用 RAG コンテキスト。
    ゆるやま / なまいき などのセールスレターの構成を土台にする。
    """
    rows = search_letter_templates(theme, k=k)
    if not rows:
        return ""
    parts = [
        "【参考レター：有料noteの無料部分（=セールスレター）の構成土台】",
        "以下は『有料商品の前段に置かれた無料部分（レター）』の実例。",
        "**構成・流れ・章立て・煽り方・煽りの落とし方・締め方** はこれをほぼ同じ枠組みで踏襲してよい。",
        "ただし以下は禁止：",
        "- 数字や実績の借用（売上額・順位・販売額ランキング・累計など）",
        "- 人物背景や属性の借用",
        "- 言い回しの丸コピー（似たら voice に書き換える）",
        "踏襲して良いのは『構成の骨組み』だけ。具体は今回のテーマと自分の素材で埋める。",
    ]
    for i, r in enumerate(rows, 1):
        snippet = r["content"][:max_chars_each]
        src = r.get("source_file") or "?"
        parts.append(f"\n--- 参考レター{i}（出典: {src} / 類似度{r['similarity']:.2f}）---\n{snippet}")
    return "\n".join(parts)


def build_rag_context(brand: str, theme: str, k: int = 4, max_chars_each: int = 600) -> str:
    """類似ポストを「参考スタイル」テキストとして整形して返す。0件なら空文字。

    sheep: 自分の過去ポスト (voice 参考) + buzz_posts (型・フック参考) の2セット
    umai:  自分の過去ポスト のみ
    """
    parts: list[str] = []

    own_results = search_similar(brand, theme, k=k)
    if own_results:
        parts.append("【参考A：あなた自身の過去ポスト（voice・口調・リズムの参考）】")
        parts.append("以下はあなたが過去に書いたポスト。voice・言い回し・改行リズム・絵文字配置を参考に、テーマに合わせて新規に書くこと。コピペは禁止。")
        for i, r in enumerate(own_results, 1):
            snippet = r["content"][:max_chars_each]
            parts.append(f"\n--- 参考A{i}（類似度{r['similarity']:.2f}）---\n{snippet}")

    # シープのみ、bazz_posts も型・フック参考として注入
    if brand == "sheep":
        buzz_results = search_buzz_posts(theme, k=k)
        if buzz_results:
            if parts:
                parts.append("")  # セクション間の空行
            parts.append("【参考B：他者のバズポスト（型・フック・構成の参考のみ）】")
            parts.append("以下は他人のバズポスト。**型・フック・構成・1行目の作り方**を参考にする。")
            parts.append("**禁止事項**：")
            parts.append("- 文章のコピペ（言い回しが似すぎたら自分の voice に書き換える）")
            parts.append("- 人物背景（年齢・職業・家族構成・地域・〇〇歳）の借用 → 自分のプロフィールNG言及に違反する")
            parts.append("- しーぷの数字正典（3週間0円→100円→月15万）からの逸脱")
            parts.append("使い方：『9割が〜』『ヶ月前の僕も〜』『〇〇じゃなく△△』のような型・構造のみ参照。")
            for i, r in enumerate(buzz_results, 1):
                snippet = r["content"][:max_chars_each]
                meta = f"@{r['author_username']} いいね{r['likes']} インプ{r['impressions']:,}"
                parts.append(f"\n--- 参考B{i}（{meta} / 類似度{r['similarity']:.2f}）---\n{snippet}")

    if not parts:
        return ""
    return "\n".join(parts)
