#!/bin/bash
# Post Studio X（X専用版）起動スクリプト
# 使い方: ./start.sh
# 既定ポートは 7879（元版 post_studio の 7878 と同時に立ち上げられます）。
#
# .venv / .env の場所は次の順で自動判定：
#   1) このフォルダ内（post_studio_x/.venv, post_studio_x/.env）  ← 単体で配布した人向け
#   2) 親フォルダ（元 post_studio と共用の構成）                   ← 開発マシンの既存構成

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"
PORT="${POST_STUDIO_PORT:-7879}"

# venv: フォルダ内優先、なければ親
if [ -d "$ROOT/.venv" ]; then VENV="$ROOT/.venv"; else VENV="$PARENT/.venv"; fi
# .env: フォルダ内優先、なければ親（server/_env.py と同じ探索順）
if [ -f "$ROOT/.env" ]; then ENVFILE="$ROOT/.env"; else ENVFILE="$PARENT/.env"; fi

if [ ! -d "$VENV" ]; then
  echo "❌ venv が見つかりません（探した場所: $ROOT/.venv, $PARENT/.venv）"
  echo "   先に作成してください:"
  echo "     python3.12 -m venv \"$ROOT/.venv\""
  echo "     \"$ROOT/.venv/bin/pip\" install -r \"$ROOT/server/requirements.txt\""
  exit 1
fi

# 依存があるか軽くチェック
if ! "$VENV/bin/python3" -c "import anthropic, fastapi, uvicorn" 2>/dev/null; then
  echo "📦 依存を再インストールします…"
  "$VENV/bin/pip" install -q -r "$ROOT/server/requirements.txt"
fi

# .env チェック（DATABASE_URL は必須。API キーは任意）
if [ ! -f "$ENVFILE" ]; then
  echo "⚠️  .env が見つかりません（$ROOT/.env か $PARENT/.env に置いてください）。"
  echo "   最低限 DATABASE_URL=（Neon の接続文字列）が必要です。"
elif ! grep -q "^DATABASE_URL=" "$ENVFILE" 2>/dev/null; then
  echo "⚠️  .env に DATABASE_URL が未設定です（Neon の接続文字列を入れてください）。"
fi

echo "🚀 Post Studio X 起動: http://localhost:$PORT"
echo "   停止: Ctrl+C"

# 起動して、起動直後にブラウザを開く
(sleep 1.5 && open "http://localhost:$PORT") &

cd "$ROOT/server"
exec "$VENV/bin/python3" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" --reload
