"""Claude Code CLI を subprocess 経由で呼び出すラッパー。

claude.ai サブスクリプション認証（Max）を使うことで、Anthropic API の従量課金を回避する。
ANTHROPIC_API_KEY を子プロセス env から外すことで強制的にサブスク認証を使わせる。

公開関数:
  stream(system, user, model="opus") -> async generator[str]    # /generate 用
  complete(system, user, model="sonnet") -> str                  # cover.py 用
"""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import AsyncGenerator


class RateLimitError(RuntimeError):
    """Claude Code サブスク認証側でレート上限に当たった時に投げる。
    main.py 側でこれを catch して API フォールバックする。"""
    def __init__(self, message: str = "", retry_after_seconds: int = 3600):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


_RATE_LIMIT_PATTERNS = (
    "usage limit",
    "usage_limit",
    "rate limit",
    "rate_limit",
    "limit exceeded",
    "too many requests",
    "quota exceeded",
    "5-hour limit",
    "weekly limit",
)


def _is_rate_limit_text(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(p in t for p in _RATE_LIMIT_PATTERNS)


def _parse_retry_after(text: str) -> int:
    """エラーメッセージから「何秒後／何分後／何時間後にリセット」を抽出。デフォルト3600秒。"""
    if not text:
        return 3600
    m = re.search(r"(\d+)\s*(?:hour|hr)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 3600
    m = re.search(r"(\d+)\s*(?:minute|min)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*(?:second|sec)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 3600

# CLAUDE.md 自動読み込みを避けるため、プロジェクト外の隔離ディレクトリを cwd に使う
WORK_DIR = Path.home() / ".post_studio_claude_workdir"
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Max 加入なので Opus をデフォにする。短い抽出系（cover）は sonnet 指定で十分。
DEFAULT_MODEL = os.environ.get("POST_STUDIO_MODEL", "opus")


def _env_for_subscription() -> dict:
    """サブスク認証を強制するため、ANTHROPIC_API_KEY を子プロセス env から外す。

    Claude Code は ANTHROPIC_API_KEY が設定されていると API キー認証を優先する。
    削っておけば keychain の OAuth トークン（claude login の結果）にフォールバックする。
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _build_cmd(system: str, user: str, model: str, stream: bool, effort: str = "low") -> list[str]:
    # 注: --effort は Claude Code 2.1.x 以降の専用フラグ。2.0.x 系では unknown option エラーになる。
    # 互換性のため、現状はコマンドに含めない（thinking スキップによる高速化は得られないが動作優先）。
    cmd = [
        "claude",
        "--print",
        "--model", model,
        "--tools", "",                    # ツール全部禁止（純粋テキスト生成）
        "--system-prompt", system,        # ブランドプロンプトで Claude Code の default をオーバーライド
        "--no-session-persistence",       # セッションを保存しない（履歴汚さない）
        "--permission-mode", "bypassPermissions",  # ツール無しなので実害なし、確認ダイアログ抑止
        "--disable-slash-commands",       # スキル一覧の自動ロードを止める（TTFT を 3 倍速くする最大の効きどころ）
    ]
    if stream:
        cmd += [
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",                  # stream-json は verbose 必須
        ]
    cmd.append(user)
    return cmd


def _extract_text_delta(event: dict) -> str:
    """stream-json の partial message イベントから text delta を取り出す。"""
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event", {})
    if inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta", {})
    if delta.get("type") != "text_delta":
        return ""
    return delta.get("text", "")


async def stream(
    system: str, user: str, model: str = DEFAULT_MODEL,
    usage_out: dict | None = None,
) -> AsyncGenerator[str, None]:
    """非同期ストリーミング生成。Claude Code subprocess の stdout を逐次パースして text を yield。
    レート上限を検出した場合は RateLimitError を投げる（main.py 側で API フォールバックする）。
    usage_out に dict を渡すと、result イベントのトークン使用量を書き込んで返す。
    """
    cmd = _build_cmd(system, user, model, stream=True)
    # asyncio の StreamReader のデフォルト上限は 64KB。Claude Code の stream-json は
    # `result` イベントなどで生成全文 + usage を1行に詰めて出力するため、長文ポストや
    # X記事だと容易に64KBを超えて ValueError("Separator is found, but chunk is longer than limit")
    # になる。10MB あれば現実的な生成では足りる。
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_env_for_subscription(),
        cwd=str(WORK_DIR),
        limit=10 * 1024 * 1024,
    )
    assert proc.stdout is not None

    stderr_chunks: list[str] = []
    rate_limit_msg: str | None = None
    any_text_yielded = False

    async def _drain_stderr():
        if proc.stderr is None:
            return
        async for line in proc.stderr:
            stderr_chunks.append(line.decode("utf-8", errors="ignore"))

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # stream-json の error 形式：result/subtype=error など
            etype = event.get("type")
            if etype == "result":
                if event.get("subtype") in ("error", "error_during_execution"):
                    err = event.get("result") or event.get("error") or ""
                    if isinstance(err, dict):
                        err = err.get("message", "") or json.dumps(err)
                    if _is_rate_limit_text(err):
                        rate_limit_msg = err
                        break
                elif usage_out is not None:
                    u = event.get("usage") or {}
                    usage_out["input_tokens"] = u.get("input_tokens", 0) or 0
                    usage_out["output_tokens"] = u.get("output_tokens", 0) or 0
                    usage_out["cache_read_input_tokens"] = u.get("cache_read_input_tokens", 0) or 0
                    usage_out["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens", 0) or 0

            text = _extract_text_delta(event)
            if text:
                any_text_yielded = True
                yield text
    finally:
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        stderr_task.cancel()
        try:
            await stderr_task
        except (asyncio.CancelledError, Exception):
            pass

    stderr_text = "".join(stderr_chunks)[:2000]
    # subprocess が非ゼロ終了した場合、stderr もチェック
    if rate_limit_msg is None and proc.returncode not in (0, None):
        if _is_rate_limit_text(stderr_text):
            rate_limit_msg = stderr_text
        else:
            # ストリーム途中で死んだ／何も text を返してこなかった場合の汎用エラー
            if not any_text_yielded:
                raise RuntimeError(
                    f"claude subprocess exited {proc.returncode} without output. stderr={stderr_text[:500]}"
                )
            print(f"[claude_cli.stream] exit={proc.returncode} stderr={stderr_text[:500]}")

    if rate_limit_msg is not None:
        retry = _parse_retry_after(rate_limit_msg)
        # 何も text を返していなければ純粋なレート上限。フォールバック可能
        if not any_text_yielded:
            raise RateLimitError(rate_limit_msg, retry_after_seconds=retry)
        # 途中まで返した後にレート上限に当たった稀ケースはログだけ
        print(f"[claude_cli.stream] rate limit mid-stream after partial output: {rate_limit_msg[:300]}")


def complete(system: str, user: str, model: str = "sonnet", timeout: int = 180) -> str:
    """同期 one-shot 生成。stdout 全文を返す。レート上限時は RateLimitError、その他失敗時は空文字。"""
    cmd = _build_cmd(system, user, model, stream=False)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_env_for_subscription(),
            cwd=str(WORK_DIR),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[claude_cli.complete] timeout after {timeout}s")
        return ""
    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_rate_limit_text(stderr) or _is_rate_limit_text(result.stdout or ""):
            raise RateLimitError(stderr or result.stdout, retry_after_seconds=_parse_retry_after(stderr or result.stdout))
        print(f"[claude_cli.complete] exit={result.returncode} stderr={stderr[:500]}")
        return ""
    return result.stdout.strip()
