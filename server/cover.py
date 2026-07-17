"""X記事のカバー画像を Gemini Image API で生成する。

馬井／しーぷ ともに同じ Note風カードデザイン（クリーム背景＋オレンジ星＋3行）。
"""

import io
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from PIL import Image
from google import genai
from google.genai import types

from _env import ENV_PATH  # noqa: F401

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview")
TARGET_SIZE = (1200, 630)
COVERS_DIR = Path(__file__).resolve().parent.parent / "web" / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

EXTRACT_SYSTEM = """あなたは Note 風カバー画像用の3行コピーを記事本文から抽出するエディターです。

【出力形式】
必ず以下のJSONのみを返してください（前置き・後置き・コードブロック禁止）:
{"line1": "...", "line2": "...", "line3": "..."}

【3行の役割】
- line1: 小さく濃グレーで表示される「煽り／前提」（8〜16字）
  例: 「Claude Code持ってるなら」「note書き始めて3週間」「ChatGPTだけで戦う人」
- line2: 中サイズ黒太字の「つなぎ／提示」（7〜14字）
  例: 「3週間で取れます」「半年でこうなった」「全員これ知りません」
- line3: 大サイズ濃赤太字の「主役の数字や結論」（4〜10字）
  例: 「月50万円」「初の100円」「note副業」「3つの罠」

【設計ルール】
- 記事本文から最も刺さるエッセンスを抽出。原文をそのままコピペせず、見出しコピーとして再構成してOK
- 3行で1つの完結したフックになるよう接続を意識
- 数字（月◯万、◯日、◯選 等）があれば line3 に持ってくる
- 「センスじゃなく◯◯」のような対比型は line2/line3 にバラす
- ベイト表現（リプください等）は禁止
"""


def _extract_lines_via_api(article_text: str) -> str:
    """API キー直叩きで3行コピー抽出（サブスクが詰まった時のフォールバック）。"""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        import history
        if history.api_budget_blocked():
            print("[cover._extract_lines_via_api] 予算設定によりAPI課金をブロック")
            return ""
    except Exception:
        pass
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=EXTRACT_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "以下の記事本文から、カバー画像用の3行コピーを抽出してJSONで返してください。\n\n"
                    f"--- 記事本文 ---\n{article_text[:6000]}"
                ),
            }],
        )
        return "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"[cover._extract_lines_via_api] {type(e).__name__}: {e}")
        return ""


def extract_cover_lines(article_text: str, brand: str = "sheep") -> Optional[dict]:
    """記事本文から3行コピーを抽出。サブスク優先・レート上限なら API にフォールバック。失敗時 None。"""
    import claude_cli  # 遅延 import（cover.py 単体実行でも壊れないように）
    user_msg = (
        "以下の記事本文から、カバー画像用の3行コピーを抽出してJSONで返してください。\n\n"
        f"--- 記事本文 ---\n{article_text[:6000]}"
    )

    text = ""
    try:
        text = claude_cli.complete(EXTRACT_SYSTEM, user_msg, model="sonnet", timeout=120)
    except claude_cli.RateLimitError as e:
        print(f"[cover.extract_lines] subscription rate-limited, falling back to API: {e}")
        text = _extract_lines_via_api(article_text)
    if not text:
        # サブスクが空応答なら API も試す
        text = _extract_lines_via_api(article_text)

    if not text:
        print("[cover.extract_lines] empty response from both subscription and API")
        return None

    m = re.search(r'\{[^{}]*"line1"[^{}]*\}', text, re.DOTALL)
    if not m:
        print(f"[cover.extract_lines] JSON not found: {text[:200]}")
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f"[cover.extract_lines] JSON decode error: {e} text={text[:200]}")
        return None
    if not all(k in data and data[k].strip() for k in ("line1", "line2", "line3")):
        return None
    return {"line1": data["line1"].strip(), "line2": data["line2"].strip(), "line3": data["line3"].strip()}

# 馬井（umai）: 既存の暖色テイスト
NOTE_STYLE_UMAI = (
    "Note.com style article thumbnail card, clean minimalist editorial design, "
    "horizontal landscape banner displayed at 1200x630 pixels. "
    "Warm soft cream off-white background (color similar to #FBF7EE). "
    "At the top center: a small orange-red eight-pointed sparkle / asterisk icon "
    "(about 7% of image height, color similar to #E8553D). "
    "Generous white space and margins. Premium book-cover quality, "
    "trustworthy long-form content vibe. Crisp clean Japanese typography. "
    "Centered composition. No other decorations, no shadows, no gradients, "
    "no extra text or letters beyond what is specified below."
)

# しーぷ（sheep）: 高級書籍ジャケット風（明るい背景＋強コントラスト文字＋ゴールド差し色）
NOTE_STYLE_SHEEP = (
    "Luxury hardcover book jacket design, ultra-premium editorial aesthetic, "
    "horizontal landscape format 1200x630 pixels. "

    "Background: bright airy gradient — warm ivory cream #F8F4E8 covering most of the canvas, "
    "with a very gentle pale sage tint #ECF1E5 appearing only softly in the bottom corners. "
    "Keep the background LIGHT overall so heading text stands out with maximum contrast. "
    "Very subtle handmade paper grain texture overlay (barely visible). "
    "Do NOT use dark moss / dark forest colors anywhere in the background. "

    "Refined champagne gold (#C9A461) accent: a thin elegant horizontal art-deco "
    "flourish line about 14% page width, centered, positioned just above the heading "
    "text block — looks like a minimal foil-stamped divider. "

    "At the top center: a small refined botanical sprig icon (a single curved leaf "
    "with delicate stem and one tiny bud), about 6% of image height, drawn with "
    "elegant hand-illustrated linework in deep forest green #1F5C42 with very subtle "
    "champagne gold #C9A461 highlight along edges (foil stamp feel). Keep icon SMALL "
    "so the heading text has room to dominate. "

    "Crisp clean Japanese typography with generous letter spacing and airy margins. "
    "Centered composition. Heading text occupies a generous portion of the canvas, "
    "with bold strong presence — the text is the hero. "
    "Hardcover novel quality, the look of a $50 art book cover. "
    "No harsh drop shadows, no busy ornamentation, no borders/frames, "
    "no dark watercolor washes obscuring the text area, "
    "no extra text or letters beyond what is specified below."
)

# 後方互換: 旧名 NOTE_STYLE_BASE
NOTE_STYLE_BASE = NOTE_STYLE_UMAI

# 各ブランドの line3 強調色
LINE3_COLOR_BY_BRAND = {
    "umai": "deep red color similar to #C0392B",
    "sheep": "deep forest green color similar to #0F5A3A",
}


def _resize_to_target(img_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    tw, th = TARGET_SIZE
    target_aspect = tw / th
    sw, sh = img.size
    src_aspect = sw / sh
    if src_aspect > target_aspect:
        new_w = int(sh * target_aspect)
        left = (sw - new_w) // 2
        img = img.crop((left, 0, left + new_w, sh))
    else:
        new_h = int(sw / target_aspect)
        top = (sh - new_h) // 2
        img = img.crop((0, top, sw, top + new_h))
    img = img.resize((tw, th), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_prompt(line1: str, line2: str, line3: str, brand: str = "umai") -> str:
    if brand == "sheep":
        return _build_prompt_sheep(line1, line2, line3)
    return _build_prompt_umai(line1, line2, line3)


def _build_prompt_umai(line1: str, line2: str, line3: str) -> str:
    """馬井: シンプル・ポップな3行（従来通り）"""
    return (
        NOTE_STYLE_UMAI
        + " Below the spark icon, three stacked bold Japanese lines, perfectly centered. "
        + f"First line (smaller, about 50% of the third line, dark gray): {line1} . "
        + f"Second line (medium, about 65% of the third line, black extra-bold gothic): {line2} . "
        + f"Third line (very large, main visual focus, deep red color similar to #C0392B, extra-bold gothic): {line3} . "
        + "No subtitle, no other text anywhere."
    )


def _build_prompt_sheep(line1: str, line2: str, line3: str) -> str:
    """しーぷ: 高級書籍ジャケット風（文字を大きく・濃く・主役に）"""
    return (
        NOTE_STYLE_SHEEP
        + " Below the botanical leaf icon and the gold flourish divider, "
        + "three stacked Japanese text lines, perfectly centered with STRONG, BOLD typography hierarchy. "
        + "Text must dominate the composition with maximum legibility — the heading is the HERO. "

        + f" First line (small caption, refined deep teal-green #2A8B6E, "
        + "clean medium-weight gothic with elegant letter-spacing, "
        + "about 35% of the third line's height, "
        + "with a thin champagne gold #C9A461 horizontal hairline underline beneath it): "
        + f"{line1} . "

        + f" Second line (medium-large, JET BLACK #0E0E0E, "
        + "heavy bold modern gothic for strong impact, "
        + "about 60% of the third line's height): "
        + f"{line2} . "

        + f" Third line (HUGE, the main visual hero, occupying ~45% of canvas width, "
        + "rendered in deep forest green #0E5A38, "
        + "ultra-bold modern gothic with thick confident strokes, "
        + "strong solid color for maximum legibility (no faded effects), "
        + "framed above and below by a thin elegant champagne gold #C9A461 horizontal "
        + "double-line ornament (each line about 40% of the text width, centered), "
        + "giving the hero text a luxe nameplate / foil-stamped book title feel): "
        + f"{line3} . "

        + " Critical: text must be clearly readable, high-contrast against the light background. "
        + " No subtitle, no other text anywhere beyond these three lines."
    )


def generate_cover(line1: str, line2: str, line3: str,
                   filename_stem: Optional[str] = None,
                   brand: str = "umai") -> Optional[str]:
    """生成成功時は web/covers/<file>.png のフルパスを返す。失敗時は None。
    フロントへの URL は /static/covers/<file>.png でアクセス可能。
    brand: 'umai' (暖色基調) or 'sheep' (緑基調)
    """
    if not _client:
        print("[cover] GEMINI_API_KEY 未設定")
        return None

    prompt = build_prompt(line1.strip(), line2.strip(), line3.strip(), brand=brand)
    stem = (filename_stem or f"cover_{int(time.time())}").replace("/", "_")
    out_path = COVERS_DIR / f"{stem}.png"

    try:
        response = _client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="16:9"),
            ),
        )
    except Exception as e:
        print(f"[cover.generate] {type(e).__name__}: {e}")
        return None

    for part in response.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            resized = _resize_to_target(inline.data)
            out_path.write_bytes(resized)
            return str(out_path)

    print("[cover] no image data returned")
    return None
