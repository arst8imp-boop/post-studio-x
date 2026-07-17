"""ポストスタジオ X版 FastAPI バックエンド（X専用・note/テーマ設定/テーマ診断なし）。

エンドポイント:
  GET  /                      → web/index.html
  GET  /static/*              → web/ 静的配信（covers/ 含む）
  POST /generate              → ストリーミング生成（履歴に自動保存）
  GET  /history               → 履歴一覧
  GET  /history/{id}          → 履歴詳細
  DELETE /history/{id}        → 履歴削除
  POST /generate_cover        → X記事カバー画像生成
  GET  /healthz               → 動作確認
"""

import asyncio
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from _env import ENV_PATH  # post_studio/.env または親 .env をロード（import 副作用）  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

from prompts import build_system_prompt, build_user_prompt  # noqa: E402
from rag import build_rag_context  # noqa: E402
import history  # noqa: E402
import brand_settings  # noqa: E402
import post_ideas  # noqa: E402
import buzz  # noqa: E402
import cover  # noqa: E402
import claude_cli  # noqa: E402

# 通常はサブスク経由（claude_cli）。レート上限に当たったら API キーにフォールバック。
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
try:
    from anthropic import AsyncAnthropic
    _api_client: Optional[AsyncAnthropic] = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
except ImportError:
    _api_client = None

# 用途別モデル。速度優先で全タイプ Sonnet に統一（Opus は使わない）。
MODEL_BY_TYPE = {
    "x_article": "sonnet",
    "long_post": "sonnet",
    "short_post": "sonnet",
    "quote_post": "sonnet",
    "reply_post": "sonnet",
    "thread_reply": "sonnet",
}

# RAG 件数を用途別に最適化（少ないほど高速）。リプは2件で十分、長文は3件まで。
RAG_K_BY_TYPE = {
    "reply_post": 2,
    "thread_reply": 2,
    "short_post": 3,
    "quote_post": 3,
    "long_post": 3,
    "x_article": 3,
}

# Claude Code の簡易モデル名 → Anthropic API のフルモデル名
API_MODEL_MAP = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

# API フォールバック時の max_tokens
MAX_TOKENS_BY_TYPE = {
    "x_article": 16000,
    "long_post": 4000,
    "short_post": 1500,
    "quote_post": 1500,
    "reply_post": 1000,
    "thread_reply": 1200,
}

# API フォールバック時のコスト算出用単価（USD / 100万トークン、claude-sonnet-4-6）。
# モデルを変えたらここも更新すること。
API_PRICING_PER_MTOK = {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_write": 3.75,
}
USD_JPY_RATE = history.USD_JPY_RATE  # 円換算の概算表示用（定義は history.py）

# サーバー起動からの累計使用量（メモリ上。再起動でリセット）
_usage_totals = {
    "sub_count": 0, "sub_input_tokens": 0, "sub_output_tokens": 0,
    "api_count": 0, "api_input_tokens": 0, "api_output_tokens": 0,
    "api_cost_usd": 0.0,
}


def _estimate_api_cost_usd(u: dict) -> float:
    return (
        u.get("input_tokens", 0) * API_PRICING_PER_MTOK["input"]
        + u.get("output_tokens", 0) * API_PRICING_PER_MTOK["output"]
        + u.get("cache_read_input_tokens", 0) * API_PRICING_PER_MTOK["cache_read"]
        + u.get("cache_creation_input_tokens", 0) * API_PRICING_PER_MTOK["cache_write"]
    ) / 1_000_000


# サブスクが詰まったら何秒間 API に倒すかを記録するメモリ上のフラグ。
# 単一プロセス前提。再起動するとリセットされる（その時はまたサブスクから試行）。
_subscription_blocked_until: float = 0.0


def _subscription_available() -> bool:
    return time.time() >= _subscription_blocked_until


def _mark_subscription_blocked(retry_after_seconds: int) -> None:
    global _subscription_blocked_until
    _subscription_blocked_until = time.time() + max(60, retry_after_seconds)
    reset_at = time.strftime("%H:%M:%S", time.localtime(_subscription_blocked_until))
    print(f"[fallback] subscription rate-limited. API モードに切替（{reset_at} まで）")

app = FastAPI(title="Post Studio X")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.middleware("http")
async def no_cache_for_static(request: Request, call_next):
    """開発中はブラウザキャッシュを完全に無効化（JS/CSS変更が即反映されるように）。"""
    response = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# 起動時に履歴テーブルを ensure
history.ensure_schema()
brand_settings.ensure_schema()


class GenerateRequest(BaseModel):
    brand: str
    type: str
    theme: str
    extra: str = ""
    use_rag: bool = True


class CoverRequest(BaseModel):
    line1: str
    line2: str
    line3: str
    brand: str = "umai"
    history_id: Optional[int] = None
    filename_stem: Optional[str] = None


class CoverAutoRequest(BaseModel):
    article_text: str
    brand: str = "sheep"
    history_id: Optional[int] = None
    filename_stem: Optional[str] = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "primary_auth": "subscription",
        "fallback_auth": "api" if _api_client else None,
        "subscription_available_now": _subscription_available(),
        "subscription_blocked_until": _subscription_blocked_until,
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
        "db_set": bool(os.environ.get("DATABASE_URL")),
    }


async def _stream_via_api(
    request: Request, system: str, user: str, model: str, max_tokens: int,
    usage_out: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """API キー直叩きでストリーミング。フォールバック専用。"""
    if _api_client is None:
        raise HTTPException(500, "API キーが未設定。サブスクが詰まっていてもフォールバック不可。.env の ANTHROPIC_API_KEY を確認してください。")
    api_model = API_MODEL_MAP.get(model, "claude-sonnet-4-6")
    async with _api_client.messages.stream(
        model=api_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as response:
        async for text in response.text_stream:
            if await request.is_disconnected():
                break
            yield text
        if usage_out is not None:
            try:
                u = (await response.get_final_message()).usage
                usage_out["input_tokens"] = u.input_tokens or 0
                usage_out["output_tokens"] = u.output_tokens or 0
                usage_out["cache_read_input_tokens"] = getattr(u, "cache_read_input_tokens", 0) or 0
                usage_out["cache_creation_input_tokens"] = getattr(u, "cache_creation_input_tokens", 0) or 0
            except Exception as e:
                print(f"[_stream_via_api] usage capture failed: {type(e).__name__}: {e}")


BUILTIN_BRANDS = [
    {"key": "taro", "label": "タロ"},
    {"key": "sheep", "label": "しーぷ"},
    {"key": "umai", "label": "馬井"},
]


async def _resolve_brand(brand: str) -> Optional[str]:
    """ブランドの存在確認。カスタムブランドなら表示名を返す。組み込みなら None、未知なら例外。"""
    if brand in ("sheep", "umai", "taro"):
        return None
    meta = await asyncio.to_thread(brand_settings.get_meta, brand)
    if not meta:
        raise HTTPException(400, f"unknown brand: {brand}")
    return meta.get("display_name") or brand


@app.get("/brands")
async def list_brands() -> JSONResponse:
    customs = await asyncio.to_thread(brand_settings.list_custom)
    brands = list(BUILTIN_BRANDS) + [
        {"key": c["brand"], "label": c["display_name"], "custom": True} for c in customs
    ]
    return JSONResponse({"brands": brands})


class CreateBrandRequest(BaseModel):
    name: str


@app.post("/brands")
async def create_brand(req: CreateBrandRequest) -> JSONResponse:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "アカウント名を入力してください")
    key = await asyncio.to_thread(brand_settings.create_brand, name)
    if not key:
        raise HTTPException(500, "作成に失敗しました。DATABASE_URL を確認してください。")
    return JSONResponse({"key": key, "label": name})


# 組み込みブランドのテーマ設定が空のときに使う既定の発信ジャンル
BUILTIN_TOPICS = {
    "taro": "ビジネス論・期待値思考・凡人の戦略",
    "sheep": "note副業・SNS発信",
    "umai": "AI副業・コンテンツ販売",
}


class SuggestThemesRequest(BaseModel):
    brand: str
    count: int = 10


@app.post("/suggest_themes")
async def suggest_themes(req: SuggestThemesRequest) -> JSONResponse:
    """投稿ネタ（テーマ・指示）をAIが提案（サブスク経由のみ）。"""
    display_name = await _resolve_brand(req.brand)
    label = display_name or next(
        (b["label"] for b in BUILTIN_BRANDS if b["key"] == req.brand), req.brand
    )
    theme_block = await asyncio.to_thread(brand_settings.build_prompt_block, req.brand)
    if not theme_block and req.brand in BUILTIN_TOPICS:
        theme_block = f"【発信ジャンル】{BUILTIN_TOPICS[req.brand]}"
    recent = await asyncio.to_thread(history.list_recent, req.brand, 40)
    recent_themes = [r["theme"] for r in recent if r.get("theme")]
    count = max(3, min(20, req.count))
    result = await asyncio.to_thread(post_ideas.suggest, label, theme_block, recent_themes, count)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return JSONResponse(result)


@app.post("/brands/{brand}/rename")
async def rename_brand(brand: str, req: CreateBrandRequest) -> JSONResponse:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "アカウント名を入力してください")
    ok = await asyncio.to_thread(brand_settings.rename, brand, name)
    if not ok:
        raise HTTPException(400, "名前を変更できるのは新規作成したアカウントだけです")
    return JSONResponse({"key": brand, "label": name})


@app.delete("/brands/{brand}")
async def delete_brand(brand: str, with_history: bool = False) -> JSONResponse:
    """新規作成したアカウントを削除。組み込み3ブランドは削除不可。
    with_history=true のときは、そのアカウントの生成履歴も一緒に削除する（既定は残す）。"""
    if brand in ("taro", "sheep", "umai"):
        raise HTTPException(400, "組み込みアカウント（タロ／しーぷ／馬井）は削除できません")
    ok = await asyncio.to_thread(brand_settings.delete, brand)
    if not ok:
        raise HTTPException(400, "削除できるのは新規作成したアカウントだけです")
    removed_history = 0
    if with_history:
        removed_history = await asyncio.to_thread(history.delete_by_brand, brand)
    return JSONResponse({"deleted": True, "removed_history": removed_history})


@app.post("/generate")
async def generate(req: GenerateRequest, request: Request) -> StreamingResponse:
    display_name = await _resolve_brand(req.brand)

    try:
        system_prompt = build_system_prompt(req.brand, req.type, display_name=display_name)
    except ValueError as e:
        raise HTTPException(400, str(e))

    rag_used = 0
    if req.use_rag:
        k = RAG_K_BY_TYPE.get(req.type, 3)
        # build_rag_context は同期＆DB接続込みで時間がかかるので to_thread でイベントループを解放する。
        # こうしないと並列リクエストで全体がブロックされ「RAG検索中…」のまま固まる。
        try:
            ctx = await asyncio.wait_for(
                asyncio.to_thread(build_rag_context, req.brand, req.theme, k),
                timeout=8.0,  # connect_timeout=4 + クエリ余裕分。これ超えたらRAGなしで続行
            )
        except asyncio.TimeoutError:
            print(f"[/generate] RAG timeout (>8s), continuing without RAG")
            ctx = ""
        if ctx:
            system_prompt = f"{system_prompt}\n\n{ctx}"
            rag_used = ctx.count("--- 参考")

    user_prompt = build_user_prompt(req.theme, req.extra)
    model = MODEL_BY_TYPE.get(req.type, "sonnet")
    max_tokens = MAX_TOKENS_BY_TYPE.get(req.type, 4000)
    use_sub = _subscription_available()
    auth_mode_initial = "subscription" if use_sub else "api"
    print(f"[/generate] brand={req.brand} type={req.type} model={model} auth={auth_mode_initial} rag_ref={rag_used}件 theme={req.theme[:50]}")

    async def stream() -> AsyncGenerator[bytes, None]:
        accumulated: list[str] = []
        auth_used = auth_mode_initial
        fell_back = False
        usage: dict = {}

        # 1) サブスクで試す（_subscription_available のときだけ）
        if use_sub:
            try:
                async for text in claude_cli.stream(system_prompt, user_prompt, model=model, usage_out=usage):
                    if await request.is_disconnected():
                        break
                    accumulated.append(text)
                    yield text.encode("utf-8")
            except claude_cli.RateLimitError as e:
                # サブスク側でレート上限。フラグ立てて API に切替
                _mark_subscription_blocked(e.retry_after_seconds)
                fell_back = True
                if accumulated:
                    # 途中までサブスクで返してたケース：そのままにして API リトライはしない
                    err = f"\n\n[NOTE] サブスク上限到達。次回から自動で API モードに切替。"
                    accumulated.append(err)
                    yield err.encode("utf-8")
            except Exception as e:
                err = f"\n\n[ERROR] {type(e).__name__}: {e}"
                accumulated.append(err)
                yield err.encode("utf-8")

        # 2) サブスクが使えない／途中で詰まって何も返してない場合は API へ
        need_api_fallback = (not use_sub) or (fell_back and not accumulated)
        if need_api_fallback and await asyncio.to_thread(history.api_budget_blocked):
            # 予算設定（0円 or 超過）により API 課金を禁止 → 課金せず停止
            err = (
                "\n\n[NOTE] サブスクが上限中ですが、予算設定によりAPI課金をブロックしました。"
                "サブスク枠の復帰までお待ちください（使用量パネルで状態を確認できます）。"
            )
            accumulated.append(err)
            yield err.encode("utf-8")
            need_api_fallback = False
        if need_api_fallback:
            auth_used = "api"
            try:
                async for text in _stream_via_api(request, system_prompt, user_prompt, model, max_tokens, usage_out=usage):
                    accumulated.append(text)
                    yield text.encode("utf-8")
            except HTTPException:
                raise
            except Exception as e:
                err = f"\n\n[ERROR] API フォールバック失敗 {type(e).__name__}: {e}"
                accumulated.append(err)
                yield err.encode("utf-8")

        full = "".join(accumulated)

        # トークン使用量とコストを集計（サブスク経由は追加課金なし = 0円）
        tokens_in = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        tokens_out = usage.get("output_tokens", 0)
        cost_usd = _estimate_api_cost_usd(usage) if auth_used == "api" else 0.0

        history_id: Optional[int] = await asyncio.to_thread(
            history.save, req.brand, req.type, req.theme, req.extra,
            full, req.use_rag, rag_used,
            auth_used, tokens_in, tokens_out, cost_usd,
        )
        if auth_used == "api":
            _usage_totals["api_count"] += 1
            _usage_totals["api_input_tokens"] += tokens_in
            _usage_totals["api_output_tokens"] += tokens_out
            _usage_totals["api_cost_usd"] += cost_usd
        else:
            _usage_totals["sub_count"] += 1
            _usage_totals["sub_input_tokens"] += tokens_in
            _usage_totals["sub_output_tokens"] += tokens_out
        print(f"[/generate] done auth={auth_used} in={tokens_in} out={tokens_out} cost=${cost_usd:.4f}")

        if history_id is not None:
            trailer = (
                f"\n<!--POST_STUDIO_META id={history_id} rag_refs={rag_used} auth={auth_used}"
                f" in={tokens_in} out={tokens_out} cost_usd={cost_usd:.6f}-->"
            )
            yield trailer.encode("utf-8")

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


@app.get("/usage")
async def usage_totals() -> JSONResponse:
    """使用量の詳細。DB集計（今日/今月/全期間）+ サブスク状態 + 予算残り。"""
    stats = await asyncio.to_thread(history.usage_stats)

    budget_jpy = history.api_budget_jpy()  # None = 未設定, 0 = API課金を完全禁止
    month_api_cost_usd = stats["month"]["api"]["cost_usd"]
    month_api_cost_jpy = month_api_cost_usd * USD_JPY_RATE

    t = dict(_usage_totals)  # 起動からのメモリ集計（後方互換用に残す）
    t["api_cost_jpy"] = round(t["api_cost_usd"] * USD_JPY_RATE, 2)
    t["usd_jpy_rate"] = USD_JPY_RATE
    t["stats"] = stats
    t["subscription"] = {
        "available_now": _subscription_available(),
        "blocked_until": _subscription_blocked_until if not _subscription_available() else 0,
    }
    charges_allowed = await asyncio.to_thread(history.api_charges_allowed)
    t["budget"] = {
        "monthly_jpy": budget_jpy,
        "spent_month_jpy": round(month_api_cost_jpy, 2),
        "remaining_jpy": round(budget_jpy - month_api_cost_jpy, 2) if (budget_jpy or 0) > 0 else None,
        # 予算的にはブロック対象か（トグルとは独立に、予算だけで見た状態）
        "budget_would_block": (budget_jpy is not None) and (budget_jpy <= 0 or month_api_cost_jpy >= budget_jpy),
        # 手動トグルで課金を許可しているか
        "charges_allowed": charges_allowed,
        # 実際に今ブロックされているか（トグルを加味した最終状態）
        "api_blocked": await asyncio.to_thread(history.api_budget_blocked),
    }
    return JSONResponse(t)


class AllowApiRequest(BaseModel):
    allowed: bool


@app.post("/settings/allow_api_charges")
async def set_allow_api_charges(req: AllowApiRequest) -> JSONResponse:
    """予算ブロックの手動解除トグル。allowed=True で予算0円でもAPI課金を許可。"""
    ok = await asyncio.to_thread(history.set_api_charges_allowed, req.allowed)
    if not ok:
        raise HTTPException(500, "設定の保存に失敗しました。DATABASE_URL を確認してください。")
    return JSONResponse({"allowed": req.allowed})


@app.get("/history")
async def list_history(brand: Optional[str] = None, limit: int = 30) -> JSONResponse:
    items = await asyncio.to_thread(history.list_recent, brand, limit)
    return JSONResponse({"items": items})


@app.get("/history/stats/today")
async def history_stats_today(type: str = "reply_post") -> JSONResponse:
    n = await asyncio.to_thread(history.count_today_by_type, type)
    return JSONResponse({"type": type, "count": n})


@app.get("/buzz/recent")
async def buzz_recent(
    hours: int = 4320,
    limit: int = 40,
    min_impressions: int = 10000,
    exclude_replies: bool = True,
    keyword: Optional[str] = None,
    post_type: str = "article_first",
) -> JSONResponse:
    items = await asyncio.to_thread(
        buzz.list_recent, hours, limit, min_impressions, exclude_replies, keyword, post_type
    )
    s = await asyncio.to_thread(buzz.stats)
    return JSONResponse({"items": items, "stats": s})


@app.get("/history/{history_id}")
async def get_history(history_id: int) -> JSONResponse:
    item = await asyncio.to_thread(history.get_one, history_id)
    if not item:
        raise HTTPException(404, "not found")
    return JSONResponse(item)


@app.delete("/history/{history_id}")
async def delete_history(history_id: int) -> JSONResponse:
    ok = await asyncio.to_thread(history.delete, history_id)
    if not ok:
        raise HTTPException(404, "not found or delete failed")
    return JSONResponse({"deleted": True})


@app.post("/generate_cover")
async def generate_cover(req: CoverRequest) -> JSONResponse:
    """3行を手動指定して画像のみ生成。"""
    if not (req.line1 and req.line2 and req.line3):
        raise HTTPException(400, "line1/line2/line3 すべて必須")

    stem = req.filename_stem
    if not stem and req.history_id is not None:
        stem = f"cover_history_{req.history_id}"

    path = await asyncio.to_thread(
        cover.generate_cover, req.line1, req.line2, req.line3, stem, req.brand
    )
    if not path:
        raise HTTPException(500, "画像生成に失敗。GEMINI_API_KEYかAPI残高を確認してください。")

    rel = Path(path).name
    url = f"/static/covers/{rel}"

    if req.history_id is not None:
        await asyncio.to_thread(history.set_cover_path, req.history_id, path)

    return JSONResponse({"path": path, "url": url})


@app.post("/generate_cover_auto")
async def generate_cover_auto(req: CoverAutoRequest) -> JSONResponse:
    """記事本文から自動で3行抽出→画像生成。"""
    if not req.article_text.strip():
        raise HTTPException(400, "article_text が空です")

    lines = await asyncio.to_thread(cover.extract_cover_lines, req.article_text, req.brand)
    if not lines:
        raise HTTPException(500, "3行抽出に失敗。ANTHROPIC_API_KEY や記事本文を確認してください。")

    stem = req.filename_stem
    if not stem and req.history_id is not None:
        stem = f"cover_history_{req.history_id}"

    path = await asyncio.to_thread(
        cover.generate_cover, lines["line1"], lines["line2"], lines["line3"], stem, req.brand
    )
    if not path:
        raise HTTPException(500, "画像生成に失敗。GEMINI_API_KEY や API残高を確認してください。")

    rel = Path(path).name
    url = f"/static/covers/{rel}"

    if req.history_id is not None:
        await asyncio.to_thread(history.set_cover_path, req.history_id, path)

    print(f"[/generate_cover_auto] lines={lines} → {rel}")
    return JSONResponse({"path": path, "url": url, "lines": lines})
