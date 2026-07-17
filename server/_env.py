"""post_studio で使う .env を一元的に読み込むためのヘルパ。

リポジトリ単体で配布できるように、
以下の優先順位で .env を探す:

  1. post_studio/.env           （リポジトリ内に置く新形式）
  2. <post_studio の親>/.env    （旧形式・後方互換）

最初に見つかったものを `load_dotenv` で読み込み、そのパスを返す。
どれも無ければ何もせず、想定パスだけ返す。
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_SERVER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SERVER_DIR.parent  # post_studio/

_CANDIDATES = (
    _REPO_ROOT / ".env",
    _REPO_ROOT.parent / ".env",
)


def load_app_env() -> Path:
    for path in _CANDIDATES:
        if path.exists():
            load_dotenv(path, override=False)
            return path
    # 未発見でも例外にはしない（healthz が値の有無で示す）
    return _CANDIDATES[0]


# import 時に一度だけ実行する利便ラッパ
ENV_PATH = load_app_env()
