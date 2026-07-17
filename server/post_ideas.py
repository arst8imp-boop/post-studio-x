"""投稿ネタ（テーマ・指示）の自動生成。

UI の「💡 ネタ出し」から呼ばれる。アカウントのテーマ設定と過去の生成履歴を渡し、
重複しない投稿ネタを提案する。生成は claude_cli（サブスク経由）のみで API 課金はしない。
"""

import json
import re

import claude_cli

IDEAS_SYSTEM = """あなたはX（Twitter）運用の敏腕ネタ出し編集者です。
アカウント情報をもとに、投稿生成ツールの「テーマ・指示」欄にそのまま貼れる投稿ネタを提案します。

【ネタの条件】
- 1本ごとに「テーマ + 切り口の指示」をセットにした1〜2文にする
  例:「『初めて単価3万円の案件が決まった日』を実体験ベースで。金額を上げる前に何を変えたかを軸に、保存したくなる構造で。」
- 1行目のフックが作りやすい具体的なネタにする（数字・実体験・逆張り・あるある・失敗談）
- アカウントの発信テーマ・ターゲット読者から外れない
- 「過去に使ったテーマ」と内容が重複しないこと（言い換えただけの類似もNG）
- ネタの種類をバランスよく混ぜる: 実体験 / ノウハウ / 逆張り主張 / あるある共感 / 失敗談 / チェックリスト

【出力形式】
以下のJSONのみを出力する。前置き・説明・コードブロック記号は不要:
{
  "ideas": [
    {"kind": "実体験", "theme": "テーマ・指示の全文", "hook": "想定する1行目の例"}
  ]
}"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def suggest(brand_label: str, theme_block: str, recent_themes: list[str], count: int = 10) -> dict:
    """ネタを count 本生成。失敗時は {"error": ...}。"""
    parts = [f"【アカウント】{brand_label}"]
    if theme_block:
        parts.append(theme_block)
    else:
        parts.append("（テーマ設定は未設定。アカウント名から発信ジャンルを推測すること）")
    if recent_themes:
        themes_list = "\n".join(f"- {t[:80]}" for t in recent_themes[:40])
        parts.append(f"【過去に使ったテーマ（重複禁止）】\n{themes_list}")
    parts.append(f"投稿ネタを{count}本提案してください。")
    user_prompt = "\n\n".join(parts)

    try:
        text = claude_cli.complete(IDEAS_SYSTEM, user_prompt, model="sonnet", timeout=180)
    except claude_cli.RateLimitError:
        return {"error": "サブスクが上限中です。復帰までお待ちください（API課金はしません）。"}

    if not text:
        return {"error": "ネタの生成に失敗しました。もう一度試してください。"}

    data = _extract_json(text)
    if not data or not isinstance(data.get("ideas"), list) or not data["ideas"]:
        return {"error": "生成結果の解析に失敗しました。もう一度試してください。"}
    return data
